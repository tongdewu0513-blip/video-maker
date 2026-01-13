import streamlit as st
import os
import json
import time
import asyncio
import edge_tts
import requests
import random
import shutil
import math
import PyPDF2
import uuid
import platform
from PIL import Image, ImageDraw, ImageFont
import numpy as np

# 引入 MoviePy
from moviepy.editor import (
    vfx, 
    afx, 
    ImageClip, 
    AudioFileClip, 
    concatenate_videoclips, 
    CompositeAudioClip,
    concatenate_audioclips,
    CompositeVideoClip
)

# --- 0. 全局配置 ---
st.set_page_config(page_title="AI 视频工坊 (黄金稳定版)", page_icon="🏆", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(BASE_DIR, "audio_files")
IMAGE_DIR = os.path.join(BASE_DIR, "image_files")
BGM_DIR = os.path.join(BASE_DIR, "bgm_assets")
OUTPUT_VIDEO = os.path.join(BASE_DIR, "final_output.mp4")
FONT_PATH = os.path.join(BASE_DIR, "font.ttf")

for d in [AUDIO_DIR, IMAGE_DIR, BGM_DIR]:
    os.makedirs(d, exist_ok=True)

# --- 1. 资源初始化 (国内加速源) ---
def download_file(url, filepath):
    if os.path.exists(filepath): return True
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        # 30秒超时，忽略SSL证书验证以提高国内连通率
        resp = requests.get(url, headers=headers, stream=True, timeout=30, verify=False)
        if resp.status_code == 200:
            with open(filepath, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192): f.write(chunk)
            return True
    except: pass
    return False

def init_resources():
    # 字体：使用 GitMirror 加速下载思源黑体
    font_url = "https://raw.gitmirror.com/googlefonts/noto-cjk/main/Sans/OTF/Simplified/NotoSansCJKsc-Bold.otf"
    if not os.path.exists(FONT_PATH):
        download_file(font_url, FONT_PATH)

    # 音乐：使用 Pixabay 国内可访问链接
    bgm_urls = {
        "tech.mp3": "https://cdn.pixabay.com/download/audio/2022/03/15/audio_c8c8a73467.mp3",
        "epic.mp3": "https://cdn.pixabay.com/download/audio/2022/03/10/audio_c8c8a73467.mp3"
    }
    for name, url in bgm_urls.items():
        path = os.path.join(BGM_DIR, name)
        if not os.path.exists(path): download_file(url, path)

# 启动即检查资源
init_resources()

# --- 2. 基础功能 ---
def read_pdf(uploaded_file):
    try:
        reader = PyPDF2.PdfReader(uploaded_file)
        text = ""
        for page in reader.pages[:30]: text += page.extract_text()
        return text
    except: return None

# --- 3. AI 核心 (DeepSeek 直出) ---
def generate_script(text, api_key):
    if not api_key: return None
    
    url = "https://api.siliconflow.cn/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # 简化的 Prompt，降低 AI 思考负担，防止超时
    system_prompt = f"""
    你是一个科普视频导演。请直接将文本改编为视频脚本。
    【死命令】
    1. **解说词**：必须是**简体中文**！
    2. **画面**：英文描述，8k风格。
    3. **音乐**：从 [tech, epic] 中选一个。
    4. **完整性**：涵盖核心内容。
    【JSON格式】
    {{
        "bgm_style": "tech",
        "scenes": [
            {{"narration": "中文解说...", "image_prompt": "English visual..."}}
        ]
    }}
    """
    
    payload = {
        "model": "deepseek-ai/DeepSeek-V3", 
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": f"原文：\n{text[:10000]}"}],
        "max_tokens": 4096,
        "response_format": {"type": "json_object"}
    }
    
    try:
        # 设置 180秒 超时，足够 DeepSeek V3 跑完
        resp = requests.post(url, json=payload, headers=headers, timeout=180)
        return json.loads(resp.json()['choices'][0]['message']['content'])
    except Exception as e:
        st.error(f"脚本生成遇到问题: {e}")
        return None

# --- 4. 素材生成 (UUID 防串味) ---
async def _generate_assets(script, voice, silicon_key, run_id):
    # 音频
    for i, scene in enumerate(script):
        outfile = os.path.join(AUDIO_DIR, f"scene_{i+1}_{run_id}.mp3")
        try:
            communicate = edge_tts.Communicate(scene.get('narration',''), voice)
            await communicate.save(outfile)
        except: pass
            
    # 图片 (Flux)
    headers = {"Authorization": f"Bearer {silicon_key}", "Content-Type": "application/json"}
    status_bar = st.empty()
    for i, scene in enumerate(script):
        outfile = os.path.join(IMAGE_DIR, f"scene_{i+1}_{run_id}.jpg")
        status_bar.text(f"🎨 绘制画面: {i+1}/{len(script)}")
        
        try:
            resp = requests.post(
                "https://api.siliconflow.cn/v1/images/generations",
                json={"model": "black-forest-labs/FLUX.1-schnell", "prompt": f"{scene.get('image_prompt','')}, 8k, photorealistic", "image_size": "1024x576", "num_inference_steps": 4, "seed": random.randint(0,99999)},
                headers=headers, timeout=30
            )
            if resp.status_code == 200:
                with open(outfile, 'wb') as f: f.write(requests.get(resp.json()['images'][0]['url']).content)
            time.sleep(0.5)
        except: pass
    status_bar.empty()

def generate_assets_sync(script, voice, key, run_id):
    voice_map = {"男声": "zh-CN-YunxiNeural", "女声": "zh-CN-XiaoxiaoNeural"}
    asyncio.run(_generate_assets(script, voice_map.get(voice, "zh-CN-YunxiNeural"), key, run_id))

# --- 5. 渲染 (像素级字幕+动态效果) ---
def zoom_in_effect(clip, zoom_ratio=0.04):
    def effect(get_frame, t):
        img = Image.fromarray(get_frame(t))
        base_size = img.size
        new_size = [math.ceil(base_size[0] * (1 + (zoom_ratio * t))), math.ceil(base_size[1] * (1 + (zoom_ratio * t)))]
        new_size = [s + (s % 2) for s in new_size]
        img = img.resize(new_size, Image.LANCZOS)
        x = math.ceil((new_size[0] - base_size[0]) / 2)
        y = math.ceil((new_size[1] - base_size[1]) / 2)
        img = img.crop([x, y, x + base_size[0], y + base_size[1]])
        return np.array(img)
    return clip.transform(effect)

def process_image_with_subtitle(img_path, text):
    """把文字直接画在图片上，最稳的方案"""
    if not os.path.exists(img_path): return None
    img = Image.open(img_path).convert("RGBA")
    draw = ImageDraw.Draw(img)
    width, height = img.size
    
    # 字体加载保底逻辑
    try:
        font = ImageFont.truetype(FONT_PATH, 55)
    except:
        # 如果下载的字体坏了，尝试找系统字体
        try: font = ImageFont.truetype("msyh.ttc", 55)
        except: font = ImageFont.load_default()

    # 换行处理
    if len(text) > 22:
        mid = len(text) // 2
        text = text[:mid] + "\n" + text[mid:]
    
    bbox = draw.multiline_textbbox((0, 0), text, font=font, align="center")
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (width - text_w) / 2
    y = height - text_h - 100

    # 黑色粗描边 + 白色填充 = 油管风字幕
    stroke_width = 4
    draw.multiline_text((x, y), text, font=font, fill='white', stroke_width=stroke_width, stroke_fill='black', align="center")
    
    return np.array(img.convert("RGB"))

def render_video_final(script, bgm_path, run_id):
    clips = []
    bar = st.progress(0)
    
    for i, scene in enumerate(script):
        idx = i + 1
        # 读取带 ID 的文件
        aud_path = os.path.join(AUDIO_DIR, f"scene_{idx}_{run_id}.mp3")
        img_path = os.path.join(IMAGE_DIR, f"scene_{idx}_{run_id}.jpg")
        
        if os.path.exists(aud_path) and os.path.exists(img_path):
            try:
                audio = AudioFileClip(aud_path)
                duration = audio.duration + 0.4
                
                # 处理图片：加字幕 -> 变视频 -> 加特效
                img_array = process_image_with_subtitle(img_path, scene.get('narration',''))
                img_clip = ImageClip(img_array).with_duration(duration)
                img_clip = zoom_in_effect(img_clip, 0.04)
                
                final_clip = img_clip.with_audio(audio)
                try: final_clip = final_clip.with_effects([vfx.CrossFadeIn(0.3)])
                except: pass
                
                clips.append(final_clip)
            except: pass
        bar.progress((i+1)/len(script))

    if not clips: return None

    final_v = concatenate_videoclips(clips, method="compose", padding=-0.3)
    
    # BGM 逻辑
    if bgm_path and os.path.exists(bgm_path):
        try:
            bgm = AudioFileClip(bgm_path)
            if bgm.duration < final_v.duration:
                # 循环拼接
                loops = math.ceil(final_v.duration / bgm.duration) + 1
                bgm = concatenate_audioclips([bgm] * loops)
            
            bgm = bgm.with_duration(final_v.duration).with_effects([afx.MultiplyVolume(0.12)])
            final_v.audio = CompositeAudioClip([final_v.audio, bgm])
        except: pass

    final_v.write_videofile(OUTPUT_VIDEO, fps=24, codec="libx264", audio_codec="aac", preset="ultrafast", threads=1)
    return OUTPUT_VIDEO

# --- 6. 主流程 ---
def run_pipeline(input_text, force_regenerate=False):
    if not silicon_key or not input_text:
        st.warning("⚠️ 请输入 Key 和 内容")
        return

    # 🌟 ID 机制：每次生成都有唯一身份证，绝不串味
    if 'current_run_id' not in st.session_state or force_regenerate:
        st.session_state.current_run_id = str(uuid.uuid4())[:8]
    run_id = st.session_state.current_run_id
    
    # 智能判断：是新任务还是复用
    is_new_task = force_regenerate or (input_text != st.session_state.get('last_text', ''))
    
    if is_new_task:
        st.info(f"🚀 开始新任务 (ID: {run_id})")
        st.session_state.last_text = input_text
        
        status.text("🧠 DeepSeek 正在写剧本...")
        script_data = generate_script(input_text, silicon_key)
        if not script_data: return
        
        script = script_data.get("scenes", [])
        st.session_state.current_script = script
        st.session_state.current_style = script_data.get("bgm_style", "tech")

        status.text("🎨 正在绘制新素材...")
        generate_assets_sync(script, voice, silicon_key, run_id)
    else:
        st.info(f"⚡ 复用旧任务 (ID: {run_id}) 的素材，仅重新合成...")
        script = st.session_state.get('current_script', [])

    status.text("🎬 正在合成视频 (含字幕)...")
    
    # BGM 选择
    bgm_path = None
    if user_bgm:
        bgm_path = "temp_user_bgm.mp3"
        with open(bgm_path, "wb") as f: f.write(user_bgm.getbuffer())
    else:
        style = st.session_state.get('current_style', 'tech')
        path = os.path.join(BGM_DIR, f"{style}.mp3")
        if not os.path.exists(path):
             # 兜底：找文件夹里有的任意一首
            files = [f for f in os.listdir(BGM_DIR) if f.endswith(".mp3")]
            if files: path = os.path.join(BGM_DIR, files[0])
        if os.path.exists(path): bgm_path = path
    
    try:
        v_path = render_video_final(script, bgm_path, run_id)
        p_bar.progress(100)
        if v_path:
            st.success("✅ 视频制作完成！")
            st.video(v_path)
    except Exception as e:
        st.error(f"渲染出错: {e}")

# --- 7. UI ---
with st.sidebar:
    st.header("⚙️ 设置")
    silicon_key = st.text_input("SiliconFlow Key", type="password")
    voice = st.selectbox("配音", ["男声", "女声"])
    st.divider()
    user_bgm = st.file_uploader("🎵 BGM (可选)", type="mp3")

st.title("🏆 AI 视频工坊 (黄金稳定版)")

tab1, tab2 = st.tabs(["📄 PDF", "📝 文本"])
raw_text = ""
with tab1:
    f = st.file_uploader("文件", type="pdf")
    if f: raw_text = read_pdf(f)
with tab2:
    t = st.text_area("文本", height=200)
    if not raw_text and t: raw_text = t

status = st.empty()
p_bar = st.progress(0)

col1, col2 = st.columns([1, 1])
with col1:
    if st.button("🚀 立即生成", type="primary", use_container_width=True):
        run_pipeline(raw_text, force_regenerate=False)
with col2:
    if st.button("🔄 强制重做", type="secondary", use_container_width=True):
        run_pipeline(raw_text, force_regenerate=True)
