import streamlit as st
import os
import json
import time
import requests
import random
import shutil
import math
import PyPDF2
import uuid
import textwrap
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import edge_tts
import asyncio

# --- 核心修正：云端必须从 moviepy.editor 导入 ---
from moviepy.editor import (
    ImageClip, 
    AudioFileClip, 
    VideoFileClip, # 新增：读取视频文件
    concatenate_videoclips, 
    CompositeAudioClip,
    concatenate_audioclips
)

# --- 0. 全局配置 ---
st.set_page_config(page_title="AI 视频工坊 (硬盘缓存版)", page_icon="💾", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(BASE_DIR, "audio_files")
IMAGE_DIR = os.path.join(BASE_DIR, "image_files")
TEMP_DIR = os.path.join(BASE_DIR, "temp_clips") # 临时视频存放区
BGM_DIR = os.path.join(BASE_DIR, "bgm_assets")
OUTPUT_VIDEO = os.path.join(BASE_DIR, "final_output.mp4")
FONT_FILENAME = "font.ttf"
FONT_PATH = os.path.join(BASE_DIR, FONT_FILENAME)

# 暴力清理重建目录
if os.path.exists(TEMP_DIR): shutil.rmtree(TEMP_DIR)
for d in [AUDIO_DIR, IMAGE_DIR, BGM_DIR, TEMP_DIR]:
    if not os.path.exists(d): os.makedirs(d)

# --- 1. 资源初始化 ---
def download_file(url, filepath):
    if os.path.exists(filepath): return True
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=headers, stream=True, timeout=30)
        if resp.status_code == 200:
            with open(filepath, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192): f.write(chunk)
            return True
    except: pass
    return False

def init_resources():
    font_url = "https://raw.githubusercontent.com/StellarCN/scp_zh/master/fonts/SimHei.ttf"
    if not os.path.exists(FONT_PATH):
        download_file(font_url, FONT_PATH)

    bgm_list = {
        "tech.mp3": "https://cdn.pixabay.com/download/audio/2022/03/15/audio_c8c8a73467.mp3",
        "epic.mp3": "https://cdn.pixabay.com/download/audio/2022/03/10/audio_c8c8a73467.mp3"
    }
    for name, url in bgm_list.items():
        path = os.path.join(BGM_DIR, name)
        if not os.path.exists(path): download_file(url, path)

init_resources()

# --- 2. 基础功能 ---
def read_pdf(file):
    try:
        reader = PyPDF2.PdfReader(file)
        text = ""
        for page in reader.pages[:20]: text += page.extract_text()
        return text
    except: return None

# --- 3. AI 逻辑 ---
def generate_script(text, api_key, lang_mode):
    url = "https://api.siliconflow.cn/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    
    lang_rule = "Simplified Chinese" if lang_mode == "中文" else "English"

    system_prompt = f"""
    Video Director Mode.
    1. Narration: {lang_rule}. Concise.
    2. Image: English, photorealistic, 8k. NO TEXT.
    3. JSON Only.
    
    {{
        "bgm": "tech",
        "scenes": [
            {{"narration": "...", "image_prompt": "..."}}
        ]
    }}
    """
    
    try:
        resp = requests.post(url, json={
            "model": "deepseek-ai/DeepSeek-V3",
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": text[:8000]}],
            "response_format": {"type": "json_object"}
        }, headers=headers, timeout=120)
        return json.loads(resp.json()['choices'][0]['message']['content'])
    except: return None

async def _gen_assets(script, voice, key, run_id):
    for i, scene in enumerate(script):
        out = os.path.join(AUDIO_DIR, f"{i}_{run_id}.mp3")
        try:
            communicate = edge_tts.Communicate(scene['narration'], voice)
            await communicate.save(out)
        except: pass
        
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for i, scene in enumerate(script):
        out = os.path.join(IMAGE_DIR, f"{i}_{run_id}.jpg")
        try:
            resp = requests.post(
                "https://api.siliconflow.cn/v1/images/generations",
                json={"model": "black-forest-labs/FLUX.1-schnell", "prompt": scene['image_prompt'] + " (no text)", "image_size": "1024x576", "num_inference_steps": 4, "seed": random.randint(0, 1e9)},
                headers=headers, timeout=30
            )
            if resp.status_code == 200:
                with open(out, 'wb') as f: f.write(requests.get(resp.json()['images'][0]['url']).content)
            time.sleep(0.5)
        except: pass

def gen_assets_sync(script, voice, key, run_id):
    v = "zh-CN-YunxiNeural" if voice == "中文" else "en-US-GuyNeural"
    asyncio.run(_gen_assets(script, v, key, run_id))

# --- 4. 渲染 (硬盘缓存 + 内存释放) ---
def draw_subtitle(img_path, text):
    if not os.path.exists(img_path): return None
    try:
        img = Image.open(img_path).convert("RGBA")
        # 🔴 强制 480P (854x480)，这是云端不卡死的黄金分辨率
        img = img.resize((854, 480), Image.LANCZOS)
        draw = ImageDraw.Draw(img)
        
        try: font = ImageFont.truetype(FONT_FILENAME, 25) 
        except: font = ImageFont.load_default()
        
        w_limit = 28 if any('\u4e00' <= c <= '\u9fff' for c in text) else 45
        text = textwrap.fill(text, width=w_limit)
        
        bbox = draw.multiline_textbbox((0,0), text, font=font, align="center")
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        x, y = (854-tw)//2, 480-th-30
        
        draw.rectangle([x-10, y-10, x+tw+10, y+th+10], fill=(0,0,0,160))
        draw.multiline_text((x, y), text, font=font, fill='white', align="center")
        
        return np.array(img.convert("RGB"))
    except: return None

def render(script, bgm_file, run_id):
    temp_files = [] # 存储临时小视频路径
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(script)
    
    # 1. 逐个生成临时视频文件 (每做一个清理一个内存)
    for i, scene in enumerate(script):
        status_text.text(f"🔨 正在硬盘生成片段: {i+1}/{total} (内存已释放)...")
        
        aud = os.path.join(AUDIO_DIR, f"{i}_{run_id}.mp3")
        img = os.path.join(IMAGE_DIR, f"{i}_{run_id}.jpg")
        temp_out = os.path.join(TEMP_DIR, f"clip_{i}_{run_id}.mp4")
        
        if os.path.exists(aud) and os.path.exists(img):
            try:
                # 生成单个 Clip
                audio = AudioFileClip(aud)
                img_arr = draw_subtitle(img, scene['narration'])
                
                if img_arr is not None:
                    clip = ImageClip(img_arr).set_duration(audio.duration + 0.2).set_audio(audio)
                    
                    # 🔴 立即写入硬盘！不要留在内存里！
                    # fps=8 极低帧率保证速度
                    clip.write_videofile(temp_out, fps=8, codec="libx264", audio_codec="aac", preset="ultrafast", verbose=False, logger=None)
                    
                    # 显式关闭，释放内存句柄
                    clip.close()
                    audio.close()
                    del clip
                    del audio
                    
                    temp_files.append(temp_out)
            except Exception as e:
                print(f"片段 {i} 失败: {e}")
                
        progress_bar.progress((i+1)/total * 0.8)
    
    if not temp_files: return None
    
    # 2. 最终缝合 (Chain Mode 极速模式)
    status_text.text("🔗 正在缝合硬盘文件...")
    
    # 读取所有临时文件
    clips = [VideoFileClip(f) for f in temp_files]
    final = concatenate_videoclips(clips, method="chain")
    
    # BGM 简单混音
    if bgm_file and os.path.exists(bgm_file):
        try:
            bgm = AudioFileClip(bgm_file)
            if bgm.duration < final.duration:
                loops = math.ceil(final.duration/bgm.duration)+1
                bgm = concatenate_audioclips([bgm] * loops)
            bgm = bgm.set_duration(final.duration).volumex(0.15)
            final.audio = CompositeAudioClip([final.audio, bgm])
        except: pass
    
    status_text.text("💾 最终导出...")
    final.write_videofile(
        OUTPUT_VIDEO, 
        fps=8, 
        codec="libx264", 
        audio_codec="aac", 
        preset="ultrafast",
        threads=1
    )
    
    # 清理所有句柄
    final.close()
    for c in clips: c.close()
    # 删除临时文件
    try: shutil.rmtree(TEMP_DIR)
    except: pass
    
    progress_bar.progress(1.0)
    status_text.empty()
    return OUTPUT_VIDEO

# --- 5. UI ---
with st.sidebar:
    st.header("配置")
    key = st.text_input("SiliconFlow Key", type="password")
    lang = st.radio("目标语言", ["中文", "英文"])
    voice = st.selectbox("配音", ["中文男声", "中文女声"] if lang=="中文" else ["English Male", "English Female"])
    bgm_up = st.file_uploader("BGM", type="mp3")

st.title("💾 AI 视频工坊 (硬盘缓存版)")

tab1, tab2 = st.tabs(["PDF", "文本"])
raw = ""
with tab1:
    f = st.file_uploader("文件", type="pdf")
    if f: raw = read_pdf(f)
with tab2:
    t = st.text_area("文本", height=200)
    if t: raw = t

if st.button("🚀 立即生成", type="primary"):
    if not key or not raw:
        st.error("缺内容")
    else:
        run_id = str(uuid.uuid4())[:6]
        
        with st.spinner("1/3 写剧本..."):
            data = generate_script(raw, key, lang)
        
        if not data: st.stop()
        
        with st.spinner("2/3 做素材..."):
            gen_assets_sync(data['scenes'], voice, key, run_id)
            
        bgm = None
        if bgm_up:
            bgm = "temp.mp3"
            with open(bgm, "wb") as f: f.write(bgm_up.getbuffer())
        else:
            p = os.path.join(BGM_DIR, "tech.mp3")
            if os.path.exists(p): bgm = p
            
        v = render(data['scenes'], bgm, run_id)
        if v:
            st.success("✅ 完成！")
            st.video(v)
