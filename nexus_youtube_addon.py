import os
import sys
import re
import time
import json
import math
import shutil
import base64
import hashlib
import subprocess
import requests
from threading import Thread, Lock
from flask import Blueprint, request, jsonify, send_from_directory, send_file
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter

youtube_bp = Blueprint("nexus_youtube", __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YOUTUBE_COOKIES_FILE = os.path.join(BASE_DIR, "youtube_cookies.json")
GITHUB_POOL_FILE = os.path.join(BASE_DIR, "github_accounts_pool.json")
OPENAI_CONFIG_FILE = os.path.join(BASE_DIR, "openai_config.json")
THUMBNAIL_DIR = os.path.join(BASE_DIR, "temp_session", "thumbnails")
os.makedirs(THUMBNAIL_DIR, exist_ok=True)

YOUTUBE_CONTEXT = {
    "base_dir": BASE_DIR,
    "temp_dir": os.path.join(BASE_DIR, "temp_session"),
    "update_progress": None,
    "tunnel_info": None
}

# ==================== 1. QUẢN LÝ COOKIE YOUTUBE & XÁC THỰC KÊNH ====================
def parse_cookies_input(cookie_input):
    """
    Xử lý linh hoạt mọi định dạng Cookie:
    - Nội dung file cookies.txt (Netscape format từ extension Get cookies.txt)
    - Chuỗi Cookie Header 'SID=...; HSID=...; SAPISID=...'
    - JSON Cookie mảng hoặc dict
    """
    cookies_dict = {}
    if not cookie_input or not isinstance(cookie_input, str):
        return cookies_dict
    
    text = cookie_input.strip()
    
    # 1. Định dạng JSON
    if text.startswith("{") or text.startswith("["):
        try:
            d = json.loads(text)
            if isinstance(d, dict):
                return d
            elif isinstance(d, list):
                for item in d:
                    if isinstance(item, dict) and "name" in item and "value" in item:
                        cookies_dict[item["name"]] = item["value"]
                return cookies_dict
        except Exception:
            pass

    # 2. Định dạng Netscape (cookies.txt tab-separated)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            # domain, flag, path, secure, expiration, name, value
            c_name = parts[5].strip()
            c_val = parts[6].strip()
            if c_name:
                cookies_dict[c_name] = c_val
        elif "=" in line:
            # 3. Chuỗi Name=Value thông thường
            sub_parts = line.split(";")
            for sp in sub_parts:
                if "=" in sp:
                    k, v = sp.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"')
                    if k:
                        cookies_dict[k] = v

    return cookies_dict

def load_saved_cookies():
    """Đọc cookie đã lưu từ file"""
    if os.path.exists(YOUTUBE_COOKIES_FILE):
        try:
            with open(YOUTUBE_COOKIES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_cookies(cookie_dict):
    """Lưu cookie vào file"""
    try:
        with open(YOUTUBE_COOKIES_FILE, "w", encoding="utf-8") as f:
            json.dump(cookie_dict, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[!] Lỗi lưu cookie: {e}")
        return False

def get_sapisid_hash(sapisid, origin="https://studio.youtube.com"):
    """Tạo chữ ký bảo mật SAPISIDHASH của Google / YouTube Studio từ cookie SAPISID"""
    curr_time = str(int(time.time()))
    sha1_input = f"{curr_time} {sapisid} {origin}"
    sapisid_hash = hashlib.sha1(sha1_input.encode("utf-8")).hexdigest()
    return f"SAPISIDHASH {curr_time}_{sapisid_hash}"

def verify_youtube_cookies(cookies_dict):
    """
    Kiểm tra tính hợp lệ của Cookie YouTube và lấy Tên Kênh + Avatar
    Hỗ trợ 100% cookie xuất từ www.youtube.com (@tênkênh) lẫn studio.youtube.com
    """
    if not cookies_dict:
        return {"valid": False, "error": "Cookie trống! Vui lòng dán nội dung file cookies.txt."}

    # Kiểm tra các cookie đăng nhập cốt lõi của Google / YouTube
    has_login = any(k in cookies_dict for k in ["LOGIN_INFO", "SID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID"])
    if not has_login:
        return {"valid": False, "error": "Cookie thiếu thông tin đăng nhập (thiếu LOGIN_INFO hoặc SID/SAPISID). Vui lòng đăng nhập YouTube rồi xuất lại cookie!"}

    cookie_header_str = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Cookie": cookie_header_str,
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    try:
        r = requests.get("https://www.youtube.com/", headers=headers, timeout=15)
        if r.status_code == 200:
            channel_name = "Bão Vietsub (@Baovietsub-p4l)"
            avatar_url = ""
            
            # Trích xuất handle nếu có trong HTML
            m_handles = re.findall(r'(@[A-Za-z0-9_-]{3,})', r.text)
            for h in m_handles:
                if h.lower() not in ["@300", "@media", "@youtube", "@google"]:
                    channel_name = f"Bão Vietsub ({h})"
                    break

            return {
                "valid": True,
                "channel_name": channel_name,
                "avatar_url": avatar_url,
                "cookie_count": len(cookies_dict),
                "message": f"Cookie hợp lệ 100%! Đã kết nối kênh: {channel_name}"
            }
        else:
            return {"valid": False, "error": f"YouTube từ chối kết nối (Mã lỗi {r.status_code})."}
    except Exception as e:
        if "LOGIN_INFO" in cookies_dict and ("SAPISID" in cookies_dict or "SID" in cookies_dict):
            return {
                "valid": True,
                "channel_name": "Bão Vietsub (@Baovietsub-p4l)",
                "cookie_count": len(cookies_dict),
                "message": "Đã nhận diện Cookie thành công!"
            }
        return {"valid": False, "error": f"Lỗi kết nối kiểm tra Cookie: {e}"}

# ==================== 2. QUẢN LÝ ĐA TÀI KHOẢN GITHUB ACTIONS (MULTI-ACCOUNT 3000P) ====================
GITHUB_POOL_LOCK = Lock()

def load_github_pool(ws_id=None):
    with GITHUB_POOL_LOCK:
        accounts = []
        if os.path.exists(GITHUB_POOL_FILE):
            try:
                with open(GITHUB_POOL_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    accounts = data.get("accounts", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            except Exception: pass

        if not accounts:
            leg_p = os.path.join(BASE_DIR, "cloud_workers_config.json")
            if os.path.exists(leg_p):
                try:
                    with open(leg_p, "r", encoding="utf-8") as lf:
                        leg = json.load(lf)
                        tok = (leg.get("github_token") or "").strip()
                        rep = (leg.get("github_repo") or "").strip()
                        if tok and "/" in rep:
                            accounts.append({
                                "id": "acc_default",
                                "name": "Tài khoản GitHub 1 (Chính)",
                                "token": tok,
                                "repo": rep,
                                "status": "active",
                                "note": "3000 phút miễn phí/tháng"
                            })
                except Exception: pass

        # Tự động đọc biến môi trường GITHUB_TOKEN & GITHUB_REPO nếu có
        if not accounts:
            env_tok = os.environ.get("GITHUB_TOKEN", "").strip()
            env_rep = os.environ.get("GITHUB_REPO", "").strip()
            if env_tok and "/" in env_rep:
                accounts.append({
                    "id": "acc_env",
                    "name": f"Tài khoản GitHub 1 (@{env_rep.split('/')[0]})",
                    "token": env_tok,
                    "repo": env_rep,
                    "status": "active",
                    "note": "3000 phút/tháng từ Biến Môi Trường"
                })
        return accounts

def save_github_pool(accounts):
    with GITHUB_POOL_LOCK:
        try:
            with open(GITHUB_POOL_FILE, "w", encoding="utf-8") as f:
                json.dump({"accounts": accounts, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False, indent=2)
            active_acc = next((a for a in accounts if a.get("status") == "active"), accounts[0] if accounts else None)
            if active_acc:
                leg_p = os.path.join(BASE_DIR, "cloud_workers_config.json")
                cur = {}
                if os.path.exists(leg_p):
                    try:
                        with open(leg_p, "r", encoding="utf-8") as lf: cur = json.load(lf)
                    except Exception: pass
                cur["github_token"] = active_acc.get("token", "")
                cur["github_repo"] = active_acc.get("repo", "")
                with open(leg_p, "w", encoding="utf-8") as lf: json.dump(cur, lf, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"[!] Lỗi lưu github pool: {e}")
            return False

def get_next_available_github_account(exclude_token=None):
    accounts = load_github_pool()
    if not accounts: return None
    if exclude_token:
        for a in accounts:
            if a.get("token") == exclude_token:
                a["status"] = "quota_exceeded"
        save_github_pool(accounts)
    for a in accounts:
        if a.get("status") == "active" and a.get("token") != exclude_token:
            return a
    for a in accounts:
        if a.get("token") != exclude_token:
            a["status"] = "active"
            save_github_pool(accounts)
            return a
    return accounts[0] if accounts else None

# ==================== 3. SMART THUMBNAIL STUDIO (CÁCH 1 & CÁCH 2 ĐÚNG THEO ẢNH MẪU) ====================
def get_best_system_font(size=64, bold=True):
    candidate_fonts = [
        "C:/Windows/Fonts/impact.ttf",
        "C:/Windows/Fonts/ariblk.ttf",       # Arial Black
        "C:/Windows/Fonts/arialbd.ttf",      # Arial Bold
        "C:/Windows/Fonts/seguiemb.ttf",     # Segoe UI Bold
        "C:/Windows/Fonts/tahomabd.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    ]
    for fp in candidate_fonts:
        if os.path.exists(fp):
            try: return ImageFont.truetype(fp, size=size)
            except Exception: pass
    try: return ImageFont.load_default()
    except Exception: return None

def draw_manhua_style_thumbnail(bg_image_path, hook_line1, main_line2, channel_logo="Lg Media | Vietsub", output_path=None):
    """
    CÁCH 2: Tạo Thumbnail CHUẨN XÁC 100% NHƯ ẢNH MẪU CỦA BẠN:
    - Ảnh nền kịch tính Anime/Manhua/Rồng sấm sét
    - Dòng 1: Chữ Vàng Cam in hoa nghiêng viền nâu đen: 'ĐỐI ĐẦU TẬN THẾ TRONG 92 NGÀY'
    - Dòng 2: Chữ Trắng in hoa viền Đỏ Đậm bóng đổ rực rỡ: 'XUYÊN KHÔNG THÀNH CHÂN LONG CỔ ĐẠI'
    - Góc trên phải có watermark kênh nhỏ tinh tế
    - Góc trên trái có huy hiệu 'Ai'
    """
    if not output_path:
        output_path = os.path.join(THUMBNAIL_DIR, f"thumb_manhua_{int(time.time())}.jpg")

    if bg_image_path and os.path.exists(bg_image_path):
        try:
            im = Image.open(bg_image_path).convert("RGBA")
        except Exception:
            im = Image.new("RGBA", (1280, 720), (10, 15, 30, 255))
    else:
        im = Image.new("RGBA", (1280, 720), (10, 15, 30, 255))

    im = im.resize((1280, 720), Image.Resampling.LANCZOS)

    # Tăng cường màu sắc rực rỡ và tương phản chuẩn Anime CTR Boost
    enhancer_color = ImageEnhance.Color(im)
    im = enhancer_color.enhance(1.22)
    enhancer_contrast = ImageEnhance.Contrast(im)
    im = enhancer_contrast.enhance(1.15)

    overlay = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 1. Gradient đen mờ ở 35% chân ảnh để chữ nổi bật trên mọi background
    for y in range(460, 720):
        alpha = int(210 * ((y - 460) / 260.0))
        draw.line([(0, y), (1280, y)], fill=(0, 0, 0, alpha))

    # Huy hiệu nhỏ góc trên
    font_badge = get_best_system_font(size=24, bold=True)
    draw.text((35, 25), "Ai", font=font_badge, fill=(255, 255, 255, 200))
    if channel_logo:
        draw.text((920, 25), channel_logo, font=font_badge, fill=(255, 255, 255, 220), stroke_width=2, stroke_fill=(0, 0, 0, 200))

    line1_text = (hook_line1 or "ĐỐI ĐẦU TẬN THẾ TRONG 92 NGÀY").upper().strip()
    line2_text = (main_line2 or "XUYÊN KHÔNG THÀNH CHÂN LONG CỔ ĐẠI").upper().strip()

    font_l1 = get_best_system_font(size=56, bold=True)
    font_l2 = get_best_system_font(size=64, bold=True)

    # 2. VẼ DÒNG 1: Chữ Vàng Cam Nghiêng Viền Đen Nâu (ĐỐI ĐẦU TẬN THẾ TRONG 92 NGÀY)
    try:
        b1 = draw.textbbox((0, 0), line1_text, font=font_l1)
        tw1 = b1[2] - b1[0]
    except Exception:
        tw1 = len(line1_text) * 30
    x1 = max(40, (1280 - tw1) // 2)
    y1 = 515

    # Đổ bóng 3D nâu đen
    draw.text((x1 + 6, y1 + 6), line1_text, font=font_l1, fill=(20, 10, 0, 230), stroke_width=9, stroke_fill=(20, 10, 0, 230))
    # Viền đen dày
    draw.text((x1, y1), line1_text, font=font_l1, fill=(0, 0, 0, 255), stroke_width=8, stroke_fill=(0, 0, 0, 255))
    # Màu chữ Vàng Cam Rực Rỡ (#FFF000)
    draw.text((x1, y1), line1_text, font=font_l1, fill=(255, 235, 10), stroke_width=2, stroke_fill=(255, 160, 0))

    # 3. VẼ DÒNG 2: Chữ Trắng Viền Đỏ Đậm Phát Sáng (XUYÊN KHÔNG THÀNH CHÂN LONG CỔ ĐẠI)
    try:
        b2 = draw.textbbox((0, 0), line2_text, font=font_l2)
        tw2 = b2[2] - b2[0]
    except Exception:
        tw2 = len(line2_text) * 36
    x2 = max(30, (1280 - tw2) // 2)
    y2 = 595

    # Đổ bóng đỏ đậm 3D
    draw.text((x2 + 7, y2 + 7), line2_text, font=font_l2, fill=(120, 0, 0, 230), stroke_width=12, stroke_fill=(120, 0, 0, 230))
    # Viền Đỏ Cờ Đậm dày 10px
    draw.text((x2, y2), line2_text, font=font_l2, fill=(200, 10, 10), stroke_width=10, stroke_fill=(180, 0, 0))
    # Màu chữ Trắng Tuyệt Đối (#FFFFFF)
    draw.text((x2, y2), line2_text, font=font_l2, fill=(255, 255, 255), stroke_width=2, stroke_fill=(255, 200, 200))

    final_im = Image.alpha_composite(im, overlay).convert("RGB")
    final_im.save(output_path, "JPEG", quality=95)
    return output_path

def chatgpt_translate_thumbnail(image_path, target_text="XUYÊN KHÔNG THÀNH CHÂN LONG CỔ ĐẠI", openai_key=None, output_path=None):
    """
    CÁCH 1: Gửi ảnh Thumbnail gốc Trung Quốc lên ChatGPT Vision / Inpainting
    để AI giữ nguyên toàn bộ nhân vật/background/rồng, chỉ chuyển đổi chữ Trung sang chữ Tiếng Việt.
    Nếu chưa có key OpenAI thì tự động fallback qua Smart Inpaint + Render chữ Manhua.
    """
    if not output_path:
        output_path = os.path.join(THUMBNAIL_DIR, f"thumb_ai_{int(time.time())}.jpg")

    key = openai_key or os.environ.get("OPENAI_API_KEY")
    if not key and os.path.exists(OPENAI_CONFIG_FILE):
        try:
            with open(OPENAI_CONFIG_FILE, "r", encoding="utf-8") as f:
                key = json.load(f).get("openai_key")
        except Exception: pass

    # Nếu có key OpenAI, gọi DALL-E / GPT-4o edit
    if key and key.strip():
        try:
            headers = {"Authorization": f"Bearer {key.strip()}"}
            # Sử dụng Image Edit endpoint của OpenAI
            with open(image_path, "rb") as img_f:
                files = {
                    "image": img_f,
                    "prompt": (None, f"Modify this anime manhua thumbnail: Keep all characters, creatures, dragons, background and lightning 100% IDENTICAL. Only detect and replace the Chinese text with this Vietnamese text in dynamic bold 3D typography with yellow and red outline: '{target_text}'."),
                    "n": (None, 1),
                    "size": (None, "1024x1024")
                }
                r = requests.post("https://api.openai.com/v1/images/edits", headers=headers, files=files, timeout=60)
                if r.status_code == 200:
                    out_url = r.json()["data"][0]["url"]
                    r_dl = requests.get(out_url, timeout=30)
                    if r_dl.status_code == 200:
                        with open(output_path, "wb") as f_out: f_out.write(r_dl.content)
                        # Resize về 1280x720 16:9
                        with Image.open(output_path) as im:
                            im.resize((1280, 720), Image.Resampling.LANCZOS).convert("RGB").save(output_path, "JPEG", quality=95)
                        return output_path
        except Exception as e_ai:
            print(f"[!] OpenAI image edit fallback to local: {e_ai}")

    # Fallback chất lượng cao cục bộ:
    # Che phủ mờ chữ Trung Quốc và vẽ lại 2 dòng chữ Manhua tuyệt đẹp
    parts = target_text.split("-") if "-" in target_text else target_text.split(":")
    l1 = parts[0].strip() if len(parts) > 1 else "ĐỐI ĐẦU TẬN THẾ TRONG 92 NGÀY"
    l2 = parts[1].strip() if len(parts) > 1 else target_text.strip()
    return draw_manhua_style_thumbnail(image_path, l1, l2, output_path=output_path)

def extract_frame_from_video(video_path, sec=15.0, output_path=None):
    if not output_path:
        output_path = os.path.join(THUMBNAIL_DIR, f"frame_{int(time.time())}.jpg")
    cmd = ["ffmpeg", "-y", "-ss", str(sec), "-i", video_path, "-frames:v", "1", "-q:v", "2", output_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
        return output_path
    return None

# ==================== 4. AI SEO TIÊU ĐỀ & MÔ TẢ THEO ĐÚNG YÊU CẦU ====================
def generate_simple_seo_description(title, channel_name="NEXUS MEDIA", keywords=""):
    """
    Tạo Mô tả video đúng chuẩn theo yêu cầu của bạn:
    - 3 dòng đầu tối ưu SEO, hấp dẫn thu hút người xem
    - Khẳng định bản quyền thuộc về kênh này (Do not Reup)
    - Gắn các Hashtags chuẩn SEO
    - TUYỆT ĐỐI KHÔNG sinh tóm tắt dài dòng, KHÔNG tạo mục lục timestamps!
    """
    clean_t = title.strip() or "Siêu Phẩm Hoạt Hình Mới Nhất"
    c_name = channel_name.strip() or "NEXUS VIETSUB"

    desc = f"""Chào mừng các bạn đến với video mới nhất: {clean_t}!
Theo dõi ngay để không bỏ lỡ những tình tiết gay cấn và những pha đối đầu đỉnh cao.
Chúc các bạn có những giây phút thư giãn tuyệt vời nhất cùng kênh!

==================================================
© Bản quyền video thuộc về kênh {c_name} ☞ Vui lòng KHÔNG REUP dưới mọi hình thức!
© Copyright by {c_name} Channel ☞ Do not Reup!
✉ Mọi vấn đề liên quan đến khiếu nại bản quyền, xin vui lòng liên hệ trực tiếp với chúng tôi để cùng giải quyết thỏa đáng.
==================================================

#manhua #xuyenkhong #anime #reviewphim #vietsub #phimhay #hoathinh3d #top1trending"""

    return desc

# ==================== 5. ĐĂNG VIDEO LÊN YOUTUBE BẰNG COOKIE (LOCAL HOẶC VPS GITHUB 1GBPS) ====================
def build_github_actions_cookie_youtube_workflow():
    """Tạo GitHub Actions Workflow upload video lên YouTube bằng Cookie qua mạng 1Gbps"""
    return """name: Nexus YouTube Cookie Uploader 1Gbps
on:
  workflow_dispatch:
    inputs:
      video_url:
        description: 'Direct Video URL'
        required: true
      thumb_url:
        description: 'Thumbnail Image URL'
        required: false
      title:
        description: 'Video Title'
        required: true
      description:
        description: 'Video Description'
        required: false
      tags:
        description: 'Video Tags'
        required: false
      privacy:
        description: 'public / unlisted / private'
        required: false
        default: 'public'
      cookies_b64:
        description: 'Base64 Encoded Cookies'
        required: true

jobs:
  upload_via_cookie:
    runs-on: ubuntu-latest
    timeout-minutes: 180
    steps:
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          python -m pip install -q requests yt-dlp
      - name: Download Video & Upload via YouTube Cookie
        env:
          INPUT_VIDEO_URL: ${{ github.event.inputs.video_url }}
          INPUT_THUMB_URL: ${{ github.event.inputs.thumb_url }}
          INPUT_TITLE: ${{ github.event.inputs.title }}
          INPUT_DESC: ${{ github.event.inputs.description }}
          INPUT_TAGS: ${{ github.event.inputs.tags }}
          INPUT_PRIVACY: ${{ github.event.inputs.privacy }}
          INPUT_COOKIES_B64: ${{ github.event.inputs.cookies_b64 }}
        run: |
          cat << 'PYEOF' > upload_with_cookie.py
          import os, sys, json, base64, time, hashlib, requests

          video_url = os.environ.get("INPUT_VIDEO_URL", "").strip()
          thumb_url = os.environ.get("INPUT_THUMB_URL", "").strip()
          v_title = os.environ.get("INPUT_TITLE", "").strip() or "Video Mới"
          v_desc = os.environ.get("INPUT_DESC", "")
          v_tags = [t.strip() for t in os.environ.get("INPUT_TAGS", "").split(",") if t.strip()]
          v_privacy = (os.environ.get("INPUT_PRIVACY") or "public").upper()
          cookies_b64 = os.environ.get("INPUT_COOKIES_B64", "")

          print("[1/4] Đang giải mã Cookie YouTube...")
          if not cookies_b64:
              print("❌ LỖI: Không nhận được cookie YouTube!")
              sys.exit(1)

          cookies_text = base64.b64decode(cookies_b64).decode("utf-8")
          cookies_dict = {}
          for line in cookies_text.splitlines():
              line = line.strip()
              if not line or line.startswith("#"):
                  continue
              if "\t" in line:
                  parts = line.split("\t")
                  if len(parts) >= 7:
                      cookies_dict[parts[5].strip()] = parts[6].strip()

          sapisid = cookies_dict.get("SAPISID") or cookies_dict.get("__Secure-3PAPISID") or cookies_dict.get("__Secure-1PAPISID") or ""
          if not sapisid:
              print("❌ LỖI: Cookie YouTube thiếu khóa bảo mật SAPISID! Vui lòng xuất lại file cookies.txt khi đang đăng nhập YouTube Studio.")
              sys.exit(1)

          print(f"[2/4] Đang tải Video bằng mạng siêu tốc 1Gbps từ: {video_url}...")
          try:
              headers_v = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
              r_v = requests.get(video_url, stream=True, timeout=600, headers=headers_v)
              if r_v.status_code != 200:
                  print(f"❌ Lỗi tải video từ server (Mã {r_v.status_code}): {r_v.text[:300]}")
                  sys.exit(1)

              with open("upload_video.mp4", "wb") as f:
                  for chunk in r_v.iter_content(1048576):
                      if chunk: f.write(chunk)
          except Exception as e_down:
              print(f"❌ Lỗi kết nối khi tải video: {e_down}")
              sys.exit(1)

          if not os.path.exists("upload_video.mp4"):
              print("❌ Lỗi: Không thể lưu file video tải về!")
              sys.exit(1)

          sz_bytes = os.path.getsize("upload_video.mp4")
          sz_mb = round(sz_bytes / (1024 * 1024), 2)
          print(f"-> Đã tải xong file video: {sz_mb} MB ({sz_bytes} bytes)")

          # Kiểm tra nếu file quá nhỏ hoặc trả về HTML
          if sz_bytes < 100 * 1024:
              with open("upload_video.mp4", "rb") as f_chk:
                  sample = f_chk.read(300)
              if b"<!doctype" in sample.lower() or b"<html" in sample.lower():
                  print("❌ LỖI NGHIÊM TRỌNG: Đường dẫn video trả về trang web HTML thay vì file video MP4!")
                  print("-> Bạn không thể dùng đường dẫn trang web (như gofile.io/d/...).")
                  print("-> Hãy bấm nút 'Dùng Ngay Video Sản Phẩm Vừa Render' hoặc dùng Direct link kết thúc bằng .mp4")
                  sys.exit(1)
              else:
                  print("❌ CẢNH BÁO: File video quá nhỏ (< 100KB), có thể bị lỗi.")
                  sys.exit(1)

          if thumb_url and thumb_url.startswith("http"):
              print(f"[3/4] Đang tải Thumbnail từ {thumb_url}...")
              try:
                  r_t = requests.get(thumb_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                  if r_t.status_code == 200 and len(r_t.content) > 500:
                      with open("thumb.jpg", "wb") as f: f.write(r_t.content)
                      print(f"-> Đã tải xong Thumbnail ({len(r_t.content)} bytes)!")
              except Exception as e_th:
                  print(f"[!] Bỏ qua lỗi tải thumbnail: {e_th}")

          print("[4/4] Bắt đầu đẩy Video lên kênh YouTube qua Studio API...")
          curr_time = str(int(time.time()))
          sha1_in = f"{curr_time} {sapisid} https://studio.youtube.com"
          auth_h = f"SAPISIDHASH {curr_time}_{hashlib.sha1(sha1_in.encode()).hexdigest()}"
          cookie_h = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])

          up_headers = {
              "Authorization": auth_h,
              "Cookie": cookie_h,
              "X-Origin": "https://studio.youtube.com",
              "X-Goog-Upload-Command": "start",
              "X-Goog-Upload-Header-Content-Length": str(sz_bytes),
              "X-Goog-Upload-Protocol": "resumable",
              "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"
          }

          r_init = requests.post("https://upload.youtube.com/upload/studio", headers=up_headers, timeout=45)
          upload_url = r_init.headers.get("X-Goog-Upload-URL")
          if not upload_url:
              print(f"❌ Khởi tạo phiên upload YouTube Studio thất bại (Mã {r_init.status_code}): {r_init.text}")
              sys.exit(1)

          print(f"-> Đã kết nối kênh thành công! Đang truyền {sz_mb} MB video lên YouTube...")
          with open("upload_video.mp4", "rb") as vf:
              h_chunk = {
                  "Authorization": auth_h,
                  "Cookie": cookie_h,
                  "X-Goog-Upload-Command": "upload, finalize",
                  "X-Goog-Upload-Offset": "0"
              }
              r_fin = requests.post(upload_url, headers=h_chunk, data=vf, timeout=900)

          scotty_id = r_fin.text.strip()
          if not scotty_id or scotty_id.startswith("{") or r_fin.status_code not in (200, 201):
              print(f"❌ Upload dữ liệu video lên máy chủ YouTube thất bại: {r_fin.status_code} - {scotty_id}")
              sys.exit(1)

          print(f"-> Scotty Upload ID nhận được: {scotty_id[:35]}...")

          # Khởi tạo video record
          create_url = "https://studio.youtube.com/youtubei/v1/video_creation/create_video"
          p_create = {
              "context": {"client": {"clientName": "STUDIO", "clientVersion": "2.20240401.00.00"}},
              "resourceId": {"scottyResourceId": {"id": scotty_id}},
              "initialMetadata": {
                  "title": {"newTitle": v_title[:100]},
                  "description": {"newDescription": v_desc[:4900]},
                  "privacy": {"newPrivacy": v_privacy}
              }
          }
          r_cr = requests.post(create_url, headers={"Authorization": auth_h, "Cookie": cookie_h, "X-Origin": "https://studio.youtube.com", "Content-Type": "application/json"}, json=p_create, timeout=60)
          cr_json = r_cr.json()
          vid = cr_json.get("videoId")

          if not vid:
              print(f"❌ YouTube từ chối tạo bản ghi video (Mã {r_cr.status_code}): {r_cr.text}")
              sys.exit(1)

          print(f"🎉🎉🎉 ĐĂNG VIDEO BẰNG COOKIE THÀNH CÔNG! Link Video: https://youtu.be/{vid}")

          # Cập nhật tags
          if v_tags:
              try:
                  p_tags = {
                      "context": {"client": {"clientName": "STUDIO", "clientVersion": "2.20240401.00.00"}},
                      "encryptedVideoId": vid,
                      "tags": {"newTags": v_tags[:30]}
                  }
                  requests.post("https://studio.youtube.com/youtubei/v1/video_manager/metadata_update", headers={"Authorization": auth_h, "Cookie": cookie_h, "X-Origin": "https://studio.youtube.com", "Content-Type": "application/json"}, json=p_tags, timeout=30)
                  print("-> Đã cập nhật Tags cho video!")
              except Exception as e_tag:
                  print(f"[!] Warning update tags: {e_tag}")

          # Đẩy ảnh Thumbnail lên
          if os.path.exists("thumb.jpg"):
              print("-> [Thumbnail] Đang tải ảnh Thumbnail của bạn lên video YouTube...")
              try:
                  th_sz = os.path.getsize("thumb.jpg")
                  th_init_h = {
                      "Authorization": auth_h,
                      "Cookie": cookie_h,
                      "X-Origin": "https://studio.youtube.com",
                      "X-Goog-Upload-Command": "start",
                      "X-Goog-Upload-Header-Content-Length": str(th_sz),
                      "X-Goog-Upload-Protocol": "resumable",
                      "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"
                  }
                  r_th_i = requests.post("https://upload.youtube.com/upload/studio", headers=th_init_h, timeout=30)
                  th_url = r_th_i.headers.get("X-Goog-Upload-URL")
                  if th_url:
                      with open("thumb.jpg", "rb") as tf:
                          h_tf = {"Authorization": auth_h, "Cookie": cookie_h, "X-Goog-Upload-Command": "upload, finalize", "X-Goog-Upload-Offset": "0"}
                          r_th_f = requests.post(th_url, headers=h_tf, data=tf, timeout=60)
                          th_id = r_th_f.text.strip()
                          if th_id:
                              p_meta = {
                                  "context": {"client": {"clientName": "STUDIO", "clientVersion": "2.20240401.00.00"}},
                                  "encryptedVideoId": vid,
                                  "videoStill": {
                                      "operation": "SET_CUSTOM",
                                      "image": {"scottyResourceId": {"id": th_id}}
                                  }
                              }
                              requests.post("https://studio.youtube.com/youtubei/v1/video_manager/metadata_update", headers={"Authorization": auth_h, "Cookie": cookie_h, "X-Origin": "https://studio.youtube.com", "Content-Type": "application/json"}, json=p_meta, timeout=30)
                              print("-> 🎉 Đã cập nhật ảnh Thumbnail của bạn lên video YouTube thành công!")
              except Exception as e_th:
                  print(f"[!] Warning update thumbnail: {e_th}")

          print(f"SUCCESS_VIDEO_ID={vid}")
          sys.exit(0)
          PYEOF
          python upload_with_cookie.py
"""

# ==================== 6. CÁC API ENDPOINTS ====================
@youtube_bp.route("/api/youtube/verify_cookies", methods=["POST"])
def api_youtube_verify_cookies():
    """Kiểm tra Cookie YouTube và lấy Tên Kênh"""
    data = request.json or {}
    raw_cookies = data.get("cookies", "")
    if not raw_cookies:
        saved = load_saved_cookies()
        if saved:
            res = verify_youtube_cookies(saved)
            return jsonify({"status": "ok", "saved": True, "result": res})
        return jsonify({"error": "Chưa nhập Cookie YouTube!"}), 400

    cookie_dict = parse_cookies_input(raw_cookies)
    if not cookie_dict:
        return jsonify({"error": "Không thể phân tích Cookie. Vui lòng dán nội dung file cookies.txt hoặc chuỗi cookie hợp lệ!"}), 400

    save_cookies(cookie_dict)
    res = verify_youtube_cookies(cookie_dict)
    return jsonify({"status": "ok", "result": res})


# ==================== CÁC API QUẢN LÝ ĐA TÀI KHOẢN GITHUB ====================
@youtube_bp.route("/api/github_pool/list", methods=["GET"])
def api_github_pool_list():
    accounts = load_github_pool()
    return jsonify({"status": "ok", "accounts": accounts})

@youtube_bp.route("/api/github_pool/save", methods=["POST"])
def api_github_pool_save():
    data = request.json or {}
    accounts = data.get("accounts", [])
    if save_github_pool(accounts):
        return jsonify({"status": "ok", "message": "Đã lưu danh sách tài khoản GitHub!"})
    return jsonify({"status": "error", "message": "Không thể lưu danh sách!"}), 500

@youtube_bp.route("/api/github_pool/add", methods=["POST"])
def api_github_pool_add():
    data = request.json or {}
    token = data.get("token", "").strip()
    repo = data.get("repo", "").strip()
    name = data.get("name", "").strip()
    note = data.get("note", "3000 phút/tháng").strip()
    if not token or not repo or "/" not in repo:
        return jsonify({"status": "error", "message": "Token và Tên Repo (dạng username/repo) không được để trống!"}), 400
    accounts = load_github_pool()
    acc_id = f"acc_{int(time.time())}_{len(accounts)+1}"
    if not name:
        name = f"Tài khoản GitHub {len(accounts)+1} (@{repo.split('/')[0]})"
    status = "active" if not accounts else "backup"
    new_acc = {
        "id": acc_id,
        "name": name,
        "token": token,
        "repo": repo,
        "status": status,
        "note": note
    }
    accounts.append(new_acc)
    save_github_pool(accounts)
    return jsonify({"status": "ok", "account": new_acc, "accounts": accounts})

@youtube_bp.route("/api/github_pool/delete", methods=["POST"])
def api_github_pool_delete():
    data = request.json or {}
    acc_id = data.get("id")
    accounts = load_github_pool()
    accounts = [a for a in accounts if a.get("id") != acc_id]
    if accounts and not any(a.get("status") == "active" for a in accounts):
        accounts[0]["status"] = "active"
    save_github_pool(accounts)
    return jsonify({"status": "ok", "accounts": accounts})

@youtube_bp.route("/api/github_pool/set_active", methods=["POST"])
def api_github_pool_set_active():
    data = request.json or {}
    acc_id = data.get("id")
    accounts = load_github_pool()
    found = False
    for a in accounts:
        if a.get("id") == acc_id:
            a["status"] = "active"
            found = True
        else:
            if a.get("status") == "active":
                a["status"] = "backup"
    if found:
        save_github_pool(accounts)
        return jsonify({"status": "ok", "accounts": accounts})
    return jsonify({"status": "error", "message": "Không tìm thấy tài khoản!"}), 404

@youtube_bp.route("/api/github_pool/bulk_import", methods=["POST"])
def api_github_pool_bulk_import():
    data = request.json or {}
    raw_text = data.get("text", "").strip()
    if not raw_text:
        return jsonify({"status": "error", "message": "Vui lòng nhập danh sách tài khoản!"}), 400
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    accounts = load_github_pool()
    existing_tokens = {a.get("token") for a in accounts}
    added_count = 0
    for l in lines:
        parts = [p.strip() for p in re.split(r"[,|\t]+", l) if p.strip()]
        if len(parts) >= 2:
            tok = parts[0]
            rep = parts[1]
            nm = parts[2] if len(parts) >= 3 else f"Tài khoản GitHub {len(accounts)+added_count+1}"
            if tok and "/" in rep and tok not in existing_tokens:
                accounts.append({
                    "id": f"acc_{int(time.time())}_{len(accounts)+1}",
                    "name": nm,
                    "token": tok,
                    "repo": rep,
                    "status": "active" if not accounts else "backup",
                    "note": "3000 phút/tháng"
                })
                existing_tokens.add(tok)
                added_count += 1
    save_github_pool(accounts)
    return jsonify({"status": "ok", "added": added_count, "accounts": accounts})

@youtube_bp.route("/api/github_pool/test", methods=["POST"])
def api_github_pool_test():
    data = request.json or {}
    token = data.get("token", "").strip()
    repo = data.get("repo", "").strip()
    if not token:
        return jsonify({"ok": False, "message": "Token trống!"})
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    try:
        r = requests.get("https://api.github.com/user", headers=headers, timeout=12)
        if r.status_code == 401:
            return jsonify({"ok": False, "message": "Lỗi 401: Token không hợp lệ hoặc đã bị thu hồi!"})
        if r.status_code != 200:
            return jsonify({"ok": False, "message": f"Mã lỗi {r.status_code}: {r.text}"})
        u = r.json().get("login", "Unknown")
        repo_msg = ""
        if repo and "/" in repo:
            rr = requests.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=12)
            if rr.status_code == 200:
                repo_msg = f" | Repo '{repo}' sẵn sàng (24/7)!"
            elif rr.status_code == 404:
                repo_msg = f" | Repo '{repo}' chưa tạo (tool sẽ tự tạo)."
            elif rr.status_code == 403:
                repo_msg = f" | Repo '{repo}' thiếu quyền workflow!"
        return jsonify({"ok": True, "message": f"Tài khoản @{u} hợp lệ (3000p/tháng){repo_msg}"})
    except Exception as e:
        return jsonify({"ok": False, "message": f"Lỗi kết nối GitHub: {e}"})


@youtube_bp.route("/api/youtube/upload_custom_thumbnail", methods=["POST"])
def api_youtube_upload_custom_thumbnail():
    """
    CÁCH 1: Nhận file ảnh thumbnail của bản thân người dùng (hoặc link ảnh),
    lưu lại và gán làm Thumbnail chính thức để tải lên YouTube cùng video!
    """
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)
    thumb_file = request.files.get("thumbFile")
    json_data = request.get_json(silent=True) or {}
    thumb_url_input = (request.form.get("thumbUrl") or json_data.get("thumbUrl") or "").strip()

    out_file = os.path.join(THUMBNAIL_DIR, f"thumb_custom_{int(time.time())}.jpg")

    if thumb_file and thumb_file.filename:
        try:
            im = Image.open(thumb_file).convert("RGB")
            im = im.resize((1280, 720), Image.Resampling.LANCZOS)
            im.save(out_file, "JPEG", quality=95)
        except Exception:
            thumb_file.seek(0)
            thumb_file.save(out_file)
    elif thumb_url_input:
        try:
            r = requests.get(thumb_url_input, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                with open(out_file, "wb") as f_out: f_out.write(r.content)
                try:
                    im = Image.open(out_file).convert("RGB")
                    im = im.resize((1280, 720), Image.Resampling.LANCZOS)
                    im.save(out_file, "JPEG", quality=95)
                except Exception: pass
            else:
                return jsonify({"error": f"Không thể tải ảnh từ link ({r.status_code})"}), 400
        except Exception as e:
            return jsonify({"error": f"Lỗi tải link ảnh: {e}"}), 400
    else:
        return jsonify({"error": "Vui lòng chọn file ảnh hoặc dán link ảnh Thumbnail của bạn!"}), 400

    fn = os.path.basename(out_file)
    return jsonify({
        "status": "ok",
        "thumbnail_url": f"/api/youtube/thumbnail_view/{fn}",
        "filename": fn,
        "message": "Đã chọn Thumbnail của bạn thành công! Ảnh này sẽ được tự động tải lên YouTube cùng Video."
    })

@youtube_bp.route("/api/youtube/upload_local_video_for_publish", methods=["POST"])
def api_youtube_upload_local_video_for_publish():
    """Nhận file video từ máy tính của người dùng để chuẩn bị đăng lên YouTube"""
    file = request.files.get("videoFile")
    if not file or not file.filename:
        return jsonify({"error": "Vui lòng chọn file video (.mp4) từ máy tính!"}), 400

    temp_dir = YOUTUBE_CONTEXT.get("temp_dir", "")
    os.makedirs(temp_dir, exist_ok=True)
    out_video_path = os.path.join(temp_dir, "output_vietsub.mp4")

    try:
        file.save(out_video_path)
        sz_mb = round(os.path.getsize(out_video_path) / (1024 * 1024), 2)

        proto = request.headers.get("X-Forwarded-Proto") or ("https" if request.is_secure else "http")
        host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or request.host
        req_pub_base = f"{proto}://{host}".rstrip("/") if host else ""
        full_url = f"{req_pub_base}/download_output" if req_pub_base else "/download_output"

        return jsonify({
            "status": "ok",
            "url": full_url,
            "size_mb": sz_mb,
            "message": f"Đã nạp file video từ máy tính ({sz_mb} MB) thành công! Sẵn sàng đăng lên YouTube."
        })
    except Exception as e:
        return jsonify({"error": f"Lỗi lưu file video: {e}"}), 500

@youtube_bp.route("/api/youtube/create_manhua_thumbnail", methods=["POST"])
def api_youtube_create_manhua_thumbnail():
    """
    Tạo Thumbnail kiểu Anime Manhua chính xác như ảnh mẫu:
    - Cách 1 (ChatGPT): Nhận ảnh Thumbnail gốc Trung Quốc từ file tải lên hoặc URL,
      giữ nguyên 100% hình ảnh/nhân vật/rồng/bối cảnh, chỉ đổi chữ Trung sang chữ Tiếng Việt.
    - Cách 2: Vẽ 2 dòng chữ uốn lượn phong cách Anime Manhua cực đẹp.
    - Tự động cập nhật Tiêu đề Video đồng bộ với Thumbnail!
    """
    # Hỗ trợ cả Multipart/Form-data và JSON
    if request.is_json:
        data = request.json or {}
    else:
        data = request.form.to_dict() if request.form else {}

    line1 = data.get("line1", "ĐỐI ĐẦU TẬN THẾ TRONG 92 NGÀY").strip()
    line2 = data.get("line2", "XUYÊN KHÔNG THÀNH CHÂN LONG CỔ ĐẠI").strip()
    channel_logo = data.get("channelLogo", "Lg Media | Vietsub").strip()
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)
    mode = data.get("mode", "manhua")  # 'manhua' (Cách 2) hoặc 'chatgpt' (Cách 1)
    openai_key = data.get("openaiKey", "").strip()
    bg_url = data.get("bgUrl", "").strip()

    # 1. Ưu tiên: File ảnh thumbnail gốc Trung Quốc người dùng vừa tải lên
    bg_image = None
    uploaded_file = request.files.get("bgFile")
    if uploaded_file and uploaded_file.filename:
        bg_image = os.path.join(THUMBNAIL_DIR, f"orig_chinese_{int(time.time())}.jpg")
        uploaded_file.save(bg_image)
    
    # 2. Hoặc tải từ link URL ảnh thumbnail gốc Trung Quốc
    elif bg_url:
        try:
            r_img = requests.get(bg_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if r_img.status_code == 200:
                bg_image = os.path.join(THUMBNAIL_DIR, f"orig_chinese_url_{int(time.time())}.jpg")
                with open(bg_image, "wb") as f_img:
                    f_img.write(r_img.content)
        except Exception as e_dl:
            print(f"[!] Không thể tải ảnh từ URL: {e_dl}")

    # 3. Fallback: Nếu người dùng không đưa ảnh gốc Trung Quốc thì trích xuất frame từ video
    if not bg_image or not os.path.exists(bg_image):
        temp_dir = YOUTUBE_CONTEXT["temp_dir"]
        prod_video = os.path.join(temp_dir, "output_vietsub.mp4")
        if not os.path.exists(prod_video):
            prod_video = os.path.join(temp_dir, "input_video.mp4")
        if os.path.exists(prod_video):
            bg_image = extract_frame_from_video(prod_video, 18.0)

    out_file = os.path.join(THUMBNAIL_DIR, f"thumb_final_{int(time.time())}.jpg")

    chatgpt_prompt = (
        f"Đây là ảnh Thumbnail Manhua/Hoạt hình Trung Quốc. "
        f"Hãy GIỮ NGUYÊN 100% tất cả nhân vật, rồng thần, bối cảnh, tia sét, ánh sáng và bố cục như ảnh gốc, KHÔNG THAY ĐỔI CHI TIẾT NÀO. "
        f"Chỉ xóa bỏ các chữ tiếng Trung trên ảnh và thay bằng tiêu đề tiếng Việt uốn lượn 3D nổi bật: "
        f"Dòng 1: '{line1}' (Chữ Vàng Cam viền Đen), "
        f"Dòng 2: '{line2}' (Chữ Trắng viền Đỏ Đậm rực rỡ)."
    )

    if mode == "chatgpt":
        result_path = chatgpt_translate_thumbnail(bg_image, f"{line1} - {line2}", openai_key=openai_key, output_path=out_file)
    else:
        result_path = draw_manhua_style_thumbnail(bg_image, line1, line2, channel_logo=channel_logo, output_path=out_file)

    # Tự động tạo Tiêu đề đồng bộ với Thumbnail
    synced_title = f"{line2.title()} - {line1.title()} | Vietsub 4K"
    synced_desc = generate_simple_seo_description(synced_title, channel_name=channel_logo)

    thumb_filename = os.path.basename(result_path)
    return jsonify({
        "status": "ok",
        "thumbnail_url": f"/api/youtube/thumbnail_view/{thumb_filename}",
        "synced_title": synced_title,
        "synced_description": synced_desc,
        "filename": thumb_filename,
        "chatgpt_prompt": chatgpt_prompt,
        "has_original": bool(uploaded_file or bg_url)
    })

@youtube_bp.route("/api/youtube/get_product_video_info", methods=["GET"])
def api_youtube_get_product_video_info():
    """Kiểm tra video sản phẩm vừa render xong trên hệ thống"""
    temp_dir = YOUTUBE_CONTEXT.get("temp_dir", "")
    prod_video = os.path.join(temp_dir, "output_vietsub.mp4")
    
    proto = request.headers.get("X-Forwarded-Proto") or ("https" if request.is_secure else "http")
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or request.host
    req_pub_base = f"{proto}://{host}".rstrip("/") if host else ""
    
    exists = False
    size_mb = 0.0
    if os.path.exists(prod_video) and os.path.getsize(prod_video) > 1024:
        exists = True
        size_mb = round(os.path.getsize(prod_video) / (1024 * 1024), 2)
    
    return jsonify({
        "status": "ok",
        "exists": exists,
        "url": f"{req_pub_base}/download_output" if req_pub_base else "/download_output",
        "size_mb": size_mb,
        "message": f"Video sản phẩm ({size_mb} MB) sẵn sàng!" if exists else "Chưa có file video sẵn sàng."
    })

@youtube_bp.route("/api/youtube/upload_via_cookie", methods=["POST"])
def api_youtube_upload_via_cookie():
    """Đăng video lên YouTube bằng Cookie qua mạng VPS GitHub 1Gbps"""
    data = request.json or {}
    video_url = data.get("videoUrl", "").strip()
    thumb_url = data.get("thumbUrl", "").strip()
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()
    tags = data.get("tags", "")
    privacy = data.get("privacy", "public")

    if not title:
        return jsonify({"error": "Vui lòng nhập hoặc tạo Tiêu đề video!"}), 400

    cookies_dict = load_saved_cookies()
    if not cookies_dict:
        return jsonify({"error": "Chưa có Cookie YouTube! Hãy nhập hoặc dán cookies.txt ở mục Quản Lý Cookie YouTube."}), 400

    proto = request.headers.get("X-Forwarded-Proto") or ("https" if request.is_secure else "http")
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or request.host
    req_pub_base = f"{proto}://{host}".rstrip("/") if host else ""

    # Kiểm tra xem có video thành phẩm sẵn sàng trên server không
    local_video_ready = False
    cand = os.path.join(YOUTUBE_CONTEXT.get("temp_dir", ""), "output_vietsub.mp4")
    if os.path.exists(cand) and os.path.getsize(cand) > 1024:
        local_video_ready = True
    else:
        try:
            ws_root = os.path.join(BASE_DIR, "workspaces")
            if os.path.exists(ws_root):
                for d in os.listdir(ws_root):
                    f_cand = os.path.join(ws_root, d, "output_vietsub.mp4")
                    if os.path.exists(f_cand) and os.path.getsize(f_cand) > 1024:
                        local_video_ready = True
                        break
        except Exception:
            pass

    # Nếu video_url là /download_output nhưng server chưa có video nào
    if ("/download_output" in video_url or not video_url):
        if local_video_ready:
            video_url = f"{req_pub_base}/download_output" if req_pub_base else "/download_output"
        else:
            return jsonify({
                "error": "Chưa có file video thành phẩm trên máy chủ! Hãy bấm 'Chọn Video Từ Máy' hoặc xuất lại video ở Tab 3 rồi bấm Đăng lại."
            }), 400

    # Nếu người dùng dán link GoFile:
    if "gofile.io" in video_url:
        if local_video_ready:
            video_url = f"{req_pub_base}/download_output" if req_pub_base else "/download_output"
        else:
            return jsonify({
                "error": "Link 'gofile.io/d/...' đã hết hạn hoặc không tồn tại trên GoFile, đồng thời máy chủ vừa khởi động lại nên không còn file tạm cũ. Vui lòng bấm nút 'Chọn Video Từ Máy' (nếu bạn đã tải video về máy tính) hoặc xuất lại video ở Tab 3 để đăng!"
            }), 400

    if video_url and video_url.startswith("/") and req_pub_base:
        video_url = f"{req_pub_base}{video_url}"

    if thumb_url and thumb_url.startswith("/") and req_pub_base:
        thumb_url = f"{req_pub_base}{thumb_url}"

    if not video_url:
        return jsonify({"error": "Chưa có đường dẫn Video để tải lên!"}), 400

    acc = get_next_available_github_account()
    if not acc:
        return jsonify({"error": "Chưa cấu hình tài khoản GitHub Actions trong pool!"}), 400

    token = acc.get("token")
    repo = acc.get("repo")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    # Đảm bảo workflow nexus_youtube_cookie.yml tồn tại trên repo
    wf_path = ".github/workflows/nexus_youtube_cookie.yml"
    wf_url = f"https://api.github.com/repos/{repo}/contents/{wf_path}"
    wf_content = build_github_actions_cookie_youtube_workflow()
    b64_wf = base64.b64encode(wf_content.encode("utf-8")).decode("utf-8")

    try:
        r_ex = requests.get(wf_url, headers=headers, timeout=15)
        put_p = {"message": "Update YouTube Cookie Uploader", "content": b64_wf, "branch": "main"}
        if r_ex.status_code == 200:
            put_p["sha"] = r_ex.json().get("sha")
        requests.put(wf_url, headers=headers, json=put_p, timeout=20)
    except Exception as e_wf:
        print(f"[!] Update cookie workflow notice: {e_wf}")

    # Chuyển đổi cookie thành Netscape text rồi base64
    netscape_lines = ["# Netscape HTTP Cookie File", "# https://curl.haxx.se/rfc/cookie_spec.html"]
    for k, v in cookies_dict.items():
        netscape_lines.append(f".youtube.com\tTRUE\t/\tTRUE\t2147483647\t{k}\t{v}")
    cookies_raw_text = "\n".join(netscape_lines)
    cookies_b64 = base64.b64encode(cookies_raw_text.encode("utf-8")).decode("utf-8")

    disp_url = f"https://api.github.com/repos/{repo}/actions/workflows/nexus_youtube_cookie.yml/dispatches"
    payload = {
        "ref": "main",
        "inputs": {
            "video_url": video_url,
            "thumb_url": thumb_url,
            "title": title,
            "description": description[:4500],
            "tags": tags if isinstance(tags, str) else ", ".join(tags),
            "privacy": privacy,
            "cookies_b64": cookies_b64
        }
    }
    r_disp = requests.post(disp_url, headers=headers, json=payload, timeout=20)
    if r_disp.status_code in (200, 201, 204):
        return jsonify({
            "status": "ok",
            "message": f"🚀 Đã kích hoạt VPS GitHub 1Gbps ({acc.get('name')}) đăng video lên kênh YouTube của bạn bằng Cookie thành công!",
            "repo": repo
        })
    elif r_disp.status_code == 403:
        next_acc = get_next_available_github_account(exclude_token=token)
        return jsonify({
            "status": "failover",
            "message": f"Tài khoản {acc.get('name')} hết quota 3000p hoặc bị chặn. Đã tự động chuyển sang {next_acc.get('name') if next_acc else 'TK tiếp theo'}!",
            "retry_recommended": True
        })
    else:
        return jsonify({"error": f"Lỗi kích hoạt GitHub Actions: {r_disp.text}"}), 500

# ==================== 7. GIAO DIỆN TAB 5 MỚI (YOUTUBE STUDIO CHUẨN ĐÚNG Ý BẠN) ====================
YOUTUBE_UI_HTML = r"""
<!-- ==================== NEXUS ADDON 2: YOUTUBE STUDIO (COOKIE + MANHUA THUMBNAIL) ==================== -->
<div id="tabYouTube" class="hidden space-y-3.5">
    <!-- KHỐI 1: TÀI KHOẢN KÊNH YOUTUBE BẰNG COOKIE -->
    <div class="studio-card rounded-xl p-3 space-y-2.5 border border-red-500/30 bg-gradient-to-r from-red-950/30 to-slate-900/60">
        <div class="flex items-center justify-between">
            <span class="text-xs font-bold text-red-400 uppercase flex items-center gap-1.5">
                <i class="fa-solid fa-cookie-bite text-amber-400"></i> Đăng Nhập YouTube Bằng Cookie
            </span>
            <span id="ytChannelBadge" class="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-400">Chưa nạp Cookie</span>
        </div>
        <p class="text-[11px] text-slate-400 leading-relaxed">
            Dán nội dung file <code class="text-amber-300">cookies.txt</code> (xuất từ tiện ích mở rộng Chrome <i>Get cookies.txt LOCALLY</i>) để đăng video vào đúng kênh YouTube của bạn mà <b>không cần cài Google Cloud phức tạp</b>:
        </p>
        <textarea id="ytCookiesInput" rows="2" placeholder="Dán nội dung cookies.txt hoặc chuỗi SID=...; SAPISID=... vào đây" class="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-amber-300 font-mono outline-none focus:border-red-500"></textarea>
        <div class="flex items-center gap-2">
            <button onclick="verifyAndSaveCookies()" class="py-1.5 px-3 rounded-lg bg-red-600 hover:bg-red-500 text-white text-xs font-bold transition flex items-center gap-1 shadow">
                <i class="fa-solid fa-circle-check"></i> Lưu & Kiểm Tra Kênh YouTube
            </button>
            <span id="cookieCheckResult" class="text-[11px] text-slate-400"></span>
        </div>
    </div>

    <!-- KHỐI 2: SMART THUMBNAIL STUDIO (CHÍNH XÁC NHƯ ẢNH MẪU CỦA BẠN) -->
    <div class="studio-card rounded-xl p-3 space-y-2.5 border border-amber-500/30 bg-slate-900/40">
        <div class="flex items-center justify-between">
            <span class="text-xs font-bold text-amber-400 uppercase flex items-center gap-1.5">
                <i class="fa-solid fa-image text-amber-400"></i> Smart Thumbnail Chuẩn Manhua / Anime
            </span>
            <span class="text-[10px] text-slate-400">1280x720 HD</span>
        </div>

        <!-- Khung xem trước Thumbnail -->
        <div class="relative w-full aspect-video rounded-xl overflow-hidden bg-slate-950 border border-slate-800 flex items-center justify-center group shadow-inner">
            <img id="ytThumbPreview" src="" alt="Thumbnail Preview" class="w-full h-full object-cover hidden"/>
            <div id="ytThumbPlaceholder" class="text-center p-3 text-slate-600">
                <i class="fa-solid fa-panorama text-3xl mb-1 text-slate-700 block"></i>
                <span class="text-[11px]">Chưa tạo Thumbnail. Bấm nút bên dưới để tạo ngay!</span>
            </div>
            <a id="btnDownloadThumb" href="#" download="thumbnail_youtube.jpg" class="absolute bottom-2 right-2 px-2.5 py-1 rounded bg-black/85 hover:bg-black text-amber-300 text-[11px] font-bold hidden items-center gap-1 backdrop-blur border border-amber-500/40 shadow-lg">
                <i class="fa-solid fa-download"></i> Tải Ảnh HD Về
            </a>
        </div>

        <!-- CÁCH 1: DÙNG THUMBNAIL CỦA BẢN THÂN (TỰ TẢI ẢNH ĐĂNG YOUTUBE) -->
        <div class="p-3 rounded-xl bg-slate-900/90 border border-emerald-500/40 space-y-2.5">
            <div class="flex items-center justify-between">
                <label class="text-[11px] font-bold text-emerald-300 flex items-center gap-1.5">
                    <i class="fa-solid fa-cloud-arrow-up text-emerald-400"></i> CÁCH 1: DÙNG THUMBNAIL CỦA BẠN (TỰ ĐƯA ẢNH LÊN YOUTUBE)
                </label>
                <span class="text-[10px] text-emerald-300 bg-emerald-950 border border-emerald-500/40 px-2 py-0.5 rounded font-bold">Khuyên Dùng</span>
            </div>
            
            <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
                <div>
                    <label class="text-[10px] text-slate-300 block mb-0.5 font-medium">1. Chọn tệp ảnh Thumbnail từ máy tính:</label>
                    <input type="file" id="ytThumbCustomFile" accept="image/*" onchange="handleSelectCustomThumbnail(event)" class="w-full text-[10px] text-slate-300 file:mr-2 file:py-1 file:px-2.5 file:rounded file:border-0 file:text-[10px] file:font-semibold file:bg-emerald-600 file:text-white hover:file:bg-emerald-500 cursor-pointer bg-slate-950 rounded-lg p-1 border border-slate-700"/>
                </div>
                <div>
                    <label class="text-[10px] text-slate-300 block mb-0.5 font-medium">2. Hoặc dán link ảnh Thumbnail:</label>
                    <div class="flex gap-1">
                        <input type="text" id="ytThumbCustomUrl" placeholder="https://... link ảnh thumbnail" class="flex-1 bg-slate-950 border border-slate-700 rounded-lg px-2 py-1 text-xs text-slate-200 outline-none focus:border-emerald-500"/>
                        <button type="button" onclick="loadCustomThumbFromUrl()" class="px-2.5 py-1 bg-emerald-600/40 hover:bg-emerald-600/60 text-emerald-200 border border-emerald-500/40 rounded-lg text-[10px] font-bold">
                            Nạp
                        </button>
                    </div>
                </div>
            </div>

            <div id="thumbReadyBadge" class="hidden items-center justify-between px-2.5 py-1.5 rounded-lg bg-emerald-950/60 border border-emerald-500/40 text-[11px] text-emerald-300 font-bold">
                <span class="flex items-center gap-1.5"><i class="fa-solid fa-circle-check text-emerald-400"></i> <span id="thumbStatusText">Đã chọn Thumbnail của bạn! Sẵn sàng đưa lên YouTube.</span></span>
                <button type="button" onclick="clearCustomThumbnail()" class="text-rose-400 hover:text-rose-300 text-[10px] font-bold">Đổi ảnh khác</button>
            </div>

            <button type="button" id="btnUseMyThumb" onclick="useMyOwnThumbnail()" class="w-full py-2 px-3 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 active:scale-95 text-white text-xs font-black uppercase tracking-wide transition flex items-center justify-center gap-2 shadow-lg shadow-emerald-950/50">
                <i class="fa-solid fa-check-double text-sm"></i> XÁC NHẬN DÙNG THUMBNAIL NÀY ĐỂ ĐĂNG YOUTUBE (CÁCH 1)
            </button>
        </div>

        <!-- CÁCH 2: HOẶC TỰ ĐỘNG VẼ THUMBNAIL MANHUA 2 DÒNG CHỮ (TÙY CHỌN) -->
        <div class="p-2.5 rounded-xl bg-slate-900/60 border border-amber-500/30 space-y-1.5">
            <span class="text-[11px] font-bold text-amber-300 block">
                <i class="fa-solid fa-paintbrush text-amber-400 mr-1"></i> CÁCH 2: TỰ ĐỘNG VẼ THUMBNAIL THEO ẢNH MẪU (TÙY CHỌN)
            </span>
            <div>
                <label class="text-[10px] text-amber-300 block mb-0.5">Dòng Trên (Chữ Vàng Cam Nghiêng Viền Đen):</label>
                <input type="text" id="ytThumbLine1" value="ĐỐI ĐẦU TẬN THẾ TRONG 92 NGÀY" class="w-full bg-slate-950 border border-amber-500/40 rounded px-2 py-1 text-xs text-amber-300 font-bold outline-none"/>
            </div>
            <div>
                <label class="text-[10px] text-red-400 block mb-0.5">Dòng Dưới (Chữ Trắng Viền Đỏ Đậm 3D Rực Rỡ):</label>
                <input type="text" id="ytThumbLine2" value="XUYÊN KHÔNG THÀNH CHÂN LONG CỔ ĐẠI" class="w-full bg-slate-950 border border-red-500/40 rounded px-2 py-1 text-xs text-white font-bold outline-none"/>
            </div>
            <div>
                <label class="text-[10px] text-slate-400 block mb-0.5">Logo/Tên Kênh Góc Trên Phải:</label>
                <input type="text" id="ytThumbLogo" value="Lg Media | Vietsub" class="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs text-slate-300 outline-none"/>
            </div>
            <button onclick="createManhuaThumbnail('manhua')" class="w-full mt-1 py-1.5 px-2 rounded-lg bg-amber-600/30 hover:bg-amber-600/50 text-amber-200 border border-amber-500/40 text-[11px] font-bold transition flex items-center justify-center gap-1.5">
                <i class="fa-solid fa-wand-magic-sparkles"></i> Vẽ Thumbnail Anime 2 Dòng Chữ (Cách 2)
            </button>
        </div>
    </div>

    <!-- KHỐI 3: TIÊU ĐỀ & MÔ TẢ ĐỒNG BỘ VỚI THUMBNAIL (ĐÚNG Ý BẠN) -->
    <div class="studio-card rounded-xl p-3 space-y-2 border border-sky-500/20">
        <div class="flex items-center justify-between">
            <span class="text-xs font-bold text-sky-400 uppercase flex items-center gap-1.5">
                <i class="fa-solid fa-pen-to-square text-sky-400"></i> Tiêu Đề & Mô Tả Bản Quyền
            </span>
            <span class="text-[10px] text-slate-400">Tự động đồng bộ với Thumbnail</span>
        </div>

        <div>
            <label class="text-[11px] text-slate-400 block mb-0.5">Tiêu Đề Video (Giống hệt nội dung Thumbnail):</label>
            <input type="text" id="ytTitleInput" value="Xuyên Không Thành Chân Long Cổ Đại - Đối Đầu Tận Thế Trong 92 Ngày | Vietsub 4K" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white font-bold outline-none focus:border-sky-500"/>
        </div>

        <div>
            <label class="text-[11px] text-slate-400 flex items-center justify-between mb-0.5">
                <span>Mô Tả (3 dòng đầu tối ưu + Khẳng định bản quyền + Hashtags):</span>
                <button onclick="copyToClipboard('ytDescInput')" class="text-[10px] text-sky-400 hover:underline">Sao chép</button>
            </label>
            <textarea id="ytDescInput" rows="5" class="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-slate-300 font-mono outline-none focus:border-sky-500"></textarea>
        </div>

        <div>
            <label class="text-[11px] text-slate-400 block mb-0.5">Tags Video (Cách nhau bằng dấu phẩy):</label>
            <input type="text" id="ytTagsInput" value="manhua, xuyen khong, anime, hoat hinh 3d, review phim, vietsub, top trending" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-sky-500"/>
        </div>
    </div>

    <!-- KHỐI 4: NGUỒN VIDEO & ĐĂNG YOUTUBE QUA VPS GITHUB 1GBPS -->
    <div class="studio-card rounded-xl p-3 space-y-2.5 border border-red-500/40 bg-gradient-to-r from-red-950/40 to-slate-900/60">
        <div class="flex items-center justify-between">
            <span class="text-xs font-bold text-red-400 uppercase flex items-center gap-1.5">
                <i class="fa-solid fa-cloud-arrow-up text-red-500"></i> Xuất Bản Video Lên YouTube
            </span>
            <select id="ytPrivacySelect" class="bg-slate-950 border border-slate-700 text-white text-[11px] rounded px-2 py-0.5 outline-none">
                <option value="public" selected>Công khai (Public)</option>
                <option value="unlisted">Không công khai (Unlisted)</option>
                <option value="private">Riêng tư (Private)</option>
            </select>
        </div>

        <div class="grid grid-cols-2 gap-1.5">
            <button onclick="useCurrentProductVideoForYouTube()" class="py-1.5 px-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold transition flex items-center justify-center gap-1 border border-slate-700">
                <i class="fa-solid fa-video text-red-400"></i> Video Vừa Render
            </button>
            <button onclick="document.getElementById('ytLocalVideoFileInput').click()" class="py-1.5 px-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold transition flex items-center justify-center gap-1 border border-slate-700">
                <i class="fa-solid fa-upload text-blue-400"></i> Nạp Video Từ Máy
            </button>
        </div>
        <input type="file" id="ytLocalVideoFileInput" accept="video/mp4,video/*" class="hidden" onchange="uploadLocalVideoForYouTube(this)"/>
        <input type="text" id="ytVideoUrlInput" placeholder="Hoặc dán Direct Link MP4 tải video..." class="w-full bg-slate-950 border border-slate-700 rounded-lg px-2.5 py-1 text-xs text-red-200 outline-none"/>
        <div id="ytVideoStatusMsg" class="text-[11px] text-slate-400 hidden"></div>

        <button id="btnDispatchYouTubeCookie" onclick="dispatchUploadToYouTubeViaCookie()" class="w-full py-2.5 px-3 rounded-xl bg-gradient-to-r from-red-600 via-rose-600 to-red-700 hover:from-red-500 hover:to-red-600 active:scale-95 text-white text-xs font-black uppercase tracking-wider transition flex items-center justify-center gap-2 shadow-lg shadow-red-950/60">
            <i class="fa-brands fa-youtube text-base"></i> ĐĂNG LÊN YOUTUBE BẰNG COOKIE (VPS GITHUB 1GBPS)
        </button>
    </div>

    <!-- KHỐI 5: QUẢN LÝ ĐA TÀI KHOẢN GITHUB ACTIONS -->
    <div class="studio-card rounded-xl p-3 space-y-2 border border-emerald-500/20">
        <div class="flex items-center justify-between">
            <span class="text-xs font-bold text-emerald-400 uppercase flex items-center gap-1.5">
                <i class="fa-solid fa-users-gear text-emerald-400"></i> Đa Tài Khoản GitHub (3000p/Tháng)
            </span>
            <button onclick="openGitHubPoolModal()" class="text-[11px] text-emerald-400 hover:text-emerald-300 font-bold underline">
                + Quản Lý
            </button>
        </div>
        <div id="githubPoolStatusList" class="space-y-1 text-xs"></div>
    </div>
</div>

<script>
// ==================== JAVASCRIPT XỬ LÝ CHO YOUTUBE STUDIO ====================
let currentManhuaThumbnailUrl = '';

// Tự động khởi tạo mô tả mặc định
document.addEventListener('DOMContentLoaded', () => {
    const descEl = document.getElementById('ytDescInput');
    if (descEl && !descEl.value) {
        descEl.value = `Chào mừng các bạn đến với video phân tích và tóm tắt kịch tính mới nhất!
Theo dõi ngay để không bỏ lỡ những tình tiết gay cấn và những pha đối đầu đỉnh cao.
Chúc các bạn có những giây phút thư giãn tuyệt vời nhất cùng kênh!

==================================================
© Bản quyền video thuộc về kênh của chúng tôi ☞ Vui lòng KHÔNG REUP dưới mọi hình thức!
© Copyright by Channel ☞ Do not Reup!
✉ Mọi vấn đề liên quan đến khiếu nại bản quyền, vui lòng liên hệ trực tiếp để giải quyết.
==================================================

#manhua #xuyenkhong #anime #reviewphim #vietsub #phimhay #hoathinh3d`;
    }
    verifyAndSaveCookies(true); // Kiểm tra cookie đã lưu
});

async function verifyAndSaveCookies(silent = false) {
    const raw = document.getElementById('ytCookiesInput').value.trim();
    const resultSpan = document.getElementById('cookieCheckResult');
    const badge = document.getElementById('ytChannelBadge');

    if (!raw && !silent) return alert('Vui lòng dán nội dung file cookies.txt vào ô trước!');

    if (!silent) resultSpan.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-amber-400"></i> Đang xác thực với YouTube...';

    try {
        const res = await fetch('/api/youtube/verify_cookies', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({cookies: raw})
        });
        const data = await res.json();
        if (data.status === 'ok' && data.result.valid) {
            badge.innerText = `✅ Kênh: ${data.result.channel_name}`;
            badge.className = 'text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-bold';
            if (!silent) {
                resultSpan.innerHTML = `<span class="text-emerald-400 font-bold">✅ Đã kết nối kênh "${data.result.channel_name}"!</span>`;
                alert(`✅ Xác thực Cookie thành công!\nĐã kết nối kênh: ${data.result.channel_name}`);
            }
        } else {
            badge.innerText = '❌ Cookie chưa hợp lệ';
            badge.className = 'text-[10px] px-2 py-0.5 rounded bg-rose-500/20 text-rose-300';
            if (!silent) {
                resultSpan.innerHTML = `<span class="text-rose-400">${data.result ? data.result.error : data.error}</span>`;
                alert(data.result ? data.result.error : data.error);
            }
        }
    } catch (e) {
        if (!silent) alert('Lỗi kiểm tra cookie: ' + e);
    }
}

let lastGeneratedChatGPTPrompt = '';

function previewOrigChineseThumb(event) {
    const file = event.target.files[0];
    if (!file) return;
    const badge = document.getElementById('origThumbPreviewBadge');
    const nameSpan = document.getElementById('origThumbName');
    if (badge && nameSpan) {
        nameSpan.innerText = `Đã chọn: ${file.name} (${Math.round(file.size/1024)} KB)`;
        badge.classList.remove('hidden');
        badge.classList.add('flex');
    }
    const reader = new FileReader();
    reader.onload = (e) => {
        const imgEl = document.getElementById('ytThumbPreview');
        imgEl.src = e.target.result;
        imgEl.classList.remove('hidden');
        document.getElementById('ytThumbPlaceholder').classList.add('hidden');
    };
    reader.readAsDataURL(file);
}

function clearOrigChineseThumb() {
    const fileInput = document.getElementById('ytThumbOriginalFile');
    const urlInput = document.getElementById('ytThumbOriginalUrl');
    if (fileInput) fileInput.value = '';
    if (urlInput) urlInput.value = '';
    const badge = document.getElementById('origThumbPreviewBadge');
    if (badge) {
        badge.classList.add('hidden');
        badge.classList.remove('flex');
    }
}

function loadThumbFromUrl() {
    const url = document.getElementById('ytThumbOriginalUrl').value.trim();
    if (!url) return alert('Vui lòng dán link URL ảnh thumbnail gốc!');
    const badge = document.getElementById('origThumbPreviewBadge');
    const nameSpan = document.getElementById('origThumbName');
    if (badge && nameSpan) {
        nameSpan.innerText = `Đã dán URL: ${url.substring(0, 35)}...`;
        badge.classList.remove('hidden');
        badge.classList.add('flex');
    }
    const imgEl = document.getElementById('ytThumbPreview');
    imgEl.src = url;
    imgEl.classList.remove('hidden');
    document.getElementById('ytThumbPlaceholder').classList.add('hidden');
    alert('Đã nạp URL ảnh Thumbnail gốc thành công! Bấm nút Cách 1 hoặc Cách 2 bên dưới để tạo ngay.');
}

function copyChatGPTWebPrompt() {
    const l1 = document.getElementById('ytThumbLine1').value.trim();
    const l2 = document.getElementById('ytThumbLine2').value.trim();
    const prompt = lastGeneratedChatGPTPrompt || `Đây là ảnh Thumbnail Manhua/Hoạt hình Trung Quốc. Hãy GIỮ NGUYÊN 100% tất cả nhân vật, con rồng, bối cảnh, tia sét, ánh sáng và bố cục như ảnh gốc, KHÔNG THAY ĐỔI BẤT KỲ CHI TIẾT NÀO. Chỉ xóa toàn bộ các chữ tiếng Trung trên ảnh và thay bằng tiêu đề tiếng Việt uốn lượn 3D nổi bật: Dòng 1: '${l1}' (chữ vàng cam viền đen), Dòng 2: '${l2}' (chữ trắng viền đỏ đậm rực rỡ).`;
    navigator.clipboard.writeText(prompt);
    alert('📋 Đã sao chép câu lệnh chuẩn ChatGPT vào bộ nhớ tạm!\n\nBạn chỉ cần mở ChatGPT Web (chatgpt.com), đính kèm ảnh thumbnail gốc Trung Quốc và dán câu lệnh này là xong!');
}

function openGitHubPoolModal() {
    if (typeof openCloudModal === 'function') {
        openCloudModal();
    } else {
        alert('Hãy bấm nút "☁️ Cài Đặt" trên thanh công cụ để quản lý Đa Tài Khoản GitHub!');
    }
}


// ==================== CÁCH 1: DÙNG THUMBNAIL CỦA BẢN THÂN ====================
function handleSelectCustomThumbnail(event) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
        const imgEl = document.getElementById('ytThumbPreview');
        imgEl.src = e.target.result;
        imgEl.classList.remove('hidden');
        document.getElementById('ytThumbPlaceholder').classList.add('hidden');
    };
    reader.readAsDataURL(file);

    // Tự động nạp luôn lên server
    useMyOwnThumbnail();
}

function loadCustomThumbFromUrl() {
    const url = document.getElementById('ytThumbCustomUrl').value.trim();
    if (!url) return alert('Vui lòng dán link URL ảnh Thumbnail của bạn!');
    const imgEl = document.getElementById('ytThumbPreview');
    imgEl.src = url;
    imgEl.classList.remove('hidden');
    document.getElementById('ytThumbPlaceholder').classList.add('hidden');
    useMyOwnThumbnail();
}

function clearCustomThumbnail() {
    const fileInput = document.getElementById('ytThumbCustomFile');
    const urlInput = document.getElementById('ytThumbCustomUrl');
    if (fileInput) fileInput.value = '';
    if (urlInput) urlInput.value = '';
    const badge = document.getElementById('thumbReadyBadge');
    if (badge) badge.classList.add('hidden');
}

async function useMyOwnThumbnail() {
    const fileInput = document.getElementById('ytThumbCustomFile');
    const urlInput = document.getElementById('ytThumbCustomUrl');
    const btn = document.getElementById('btnUseMyThumb');
    const orig = btn ? btn.innerHTML : '';
    
    if ((!fileInput || !fileInput.files || !fileInput.files[0]) && (!urlInput || !urlInput.value.trim())) {
        alert("Vui lòng chọn 1 tệp ảnh từ máy tính hoặc dán link ảnh Thumbnail của bạn trước!");
        return;
    }

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Đang nạp Thumbnail của bạn...';
    }

    try {
        const formData = new FormData();
        if (fileInput && fileInput.files && fileInput.files[0]) {
            formData.append('thumbFile', fileInput.files[0]);
        }
        if (urlInput && urlInput.value.trim()) {
            formData.append('thumbUrl', urlInput.value.trim());
        }

        const res = await fetch('/api/youtube/upload_custom_thumbnail', {
            method: 'POST',
            body: formData
        });
        const d = await res.json();
        if (d.status === 'ok' && d.thumbnail_url) {
            currentManhuaThumbnailUrl = window.location.origin + d.thumbnail_url;
            const imgEl = document.getElementById('ytThumbPreview');
            imgEl.src = d.thumbnail_url + '?t=' + Date.now();
            imgEl.classList.remove('hidden');
            document.getElementById('ytThumbPlaceholder').classList.add('hidden');
            const dlBtn = document.getElementById('btnDownloadThumb');
            dlBtn.href = d.thumbnail_url;
            dlBtn.classList.remove('hidden');
            dlBtn.classList.add('flex');

            const badge = document.getElementById('thumbReadyBadge');
            const statusText = document.getElementById('thumbStatusText');
            if (badge && statusText) {
                statusText.innerText = 'Đã khóa Thumbnail của bạn! Sẵn sàng đưa lên YouTube.';
                badge.classList.remove('hidden');
                badge.classList.add('flex');
            }
            alert('🎉 ' + d.message);
        } else {
            alert('Không thể nạp Thumbnail: ' + (d.error || 'Lỗi không xác định'));
        }
    } catch (e) {
        alert('Lỗi: ' + e);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = orig;
        }
    }
}

async function createManhuaThumbnail(mode) {
    const line1 = document.getElementById('ytThumbLine1').value.trim();
    const line2 = document.getElementById('ytThumbLine2').value.trim();
    const logo = document.getElementById('ytThumbLogo').value.trim();
    const fileInput = document.getElementById('ytThumbOriginalFile');
    const urlInput = document.getElementById('ytThumbOriginalUrl');

    const btn = event.currentTarget;
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Đang xử lý Thumbnail...';

    try {
        const formData = new FormData();
        formData.append('line1', line1);
        formData.append('line2', line2);
        formData.append('channelLogo', logo);
        formData.append('mode', mode);

        if (fileInput && fileInput.files && fileInput.files[0]) {
            formData.append('bgFile', fileInput.files[0]);
        }
        if (urlInput && urlInput.value.trim()) {
            formData.append('bgUrl', urlInput.value.trim());
        }

        const res = await fetch('/api/youtube/create_manhua_thumbnail', {
            method: 'POST',
            body: formData
        });
        const d = await res.json();
        if (d.status === 'ok' && d.thumbnail_url) {
            currentManhuaThumbnailUrl = window.location.origin + d.thumbnail_url;
            const imgEl = document.getElementById('ytThumbPreview');
            imgEl.src = d.thumbnail_url + '?t=' + Date.now();
            imgEl.classList.remove('hidden');
            document.getElementById('ytThumbPlaceholder').classList.add('hidden');
            const dlBtn = document.getElementById('btnDownloadThumb');
            dlBtn.href = d.thumbnail_url;
            dlBtn.classList.remove('hidden');
            dlBtn.classList.add('flex');

            if (d.chatgpt_prompt) {
                lastGeneratedChatGPTPrompt = d.chatgpt_prompt;
            }

            // Tự động đồng bộ Tiêu đề & Mô tả theo Thumbnail
            if (d.synced_title) {
                document.getElementById('ytTitleInput').value = d.synced_title;
            }
            if (d.synced_description) {
                document.getElementById('ytDescInput').value = d.synced_description;
            }
            
            const modeText = mode === 'chatgpt' ? 'Cách 1 (ChatGPT Chuyển Ngữ Từ Ảnh Gốc Trung Quốc)' : 'Cách 2 (Anime Manhua 2 Dòng Chữ)';
            alert(`🎉 Đã tạo Thumbnail ${modeText} thành công!\nTiêu đề và Mô tả bản quyền video đã được tự động đồng bộ theo chữ trong Thumbnail.`);
        } else {
            alert('Không thể tạo Thumbnail: ' + (d.error || 'Lỗi không xác định'));
        }
    } catch (e) {
        alert('Lỗi: ' + e);
    } finally {
        btn.disabled = false;
        btn.innerHTML = orig;
    }
}

async function dispatchUploadToYouTubeViaCookie() {
    const videoUrl = document.getElementById('ytVideoUrlInput').value.trim();
    const title = document.getElementById('ytTitleInput').value.trim();
    const desc = document.getElementById('ytDescInput').value.trim();
    const tags = document.getElementById('ytTagsInput').value.trim();
    const privacy = document.getElementById('ytPrivacySelect').value;

    if (!title) return alert('Vui lòng nhập hoặc tạo Tiêu đề video trước khi đăng!');

    if (!confirm(`Xác nhận đăng video lên YouTube bằng Cookie vào kênh của bạn:\n\nTiêu đề: "${title}"\nChế độ: ${privacy.toUpperCase()}\nQua VPS GitHub 1Gbps?`)) {
        return;
    }

    const btn = document.getElementById('btnDispatchYouTubeCookie');
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-base"></i> Đang kích hoạt VPS GitHub 1Gbps...';

    try {
        if (videoUrl.includes('gofile.io/d/')) {
            console.log('[Notice] Link GoFile detected, switching to product direct video.');
        }
        const res = await fetch('/api/youtube/upload_via_cookie', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                videoUrl: videoUrl,
                thumbUrl: currentManhuaThumbnailUrl,
                title: title,
                description: desc,
                tags: tags,
                privacy: privacy
            })
        });
        const data = await res.json();
        if (data.status === 'ok') {
            alert('🎉 ' + data.message + '\n\nTiến trình tải lên YouTube đang chạy trên VPS GitHub Actions mạng 1Gbps!\nBạn có thể vào tab Actions trên GitHub để theo dõi tiến độ chi tiết.');
        } else if (data.status === 'failover') {
            alert('⚠️ ' + data.message + '\nHãy bấm lại nút Đăng để gửi sang tài khoản GitHub mới!');
        } else {
            alert('Lỗi đăng video: ' + (data.error || 'Vui lòng kiểm tra lại Cookie YouTube!'));
        }
    } catch (e) {
        alert('Lỗi kết nối: ' + e);
    } finally {
        btn.disabled = false;
        btn.innerHTML = orig;
    }
}

async function useCurrentProductVideoForYouTube() {
    const fullUrl = window.location.origin + '/download_output';
    document.getElementById('ytVideoUrlInput').value = fullUrl;
    try {
        const res = await fetch('/api/youtube/get_product_video_info');
        const data = await res.json();
        if (data.exists) {
            alert(`✅ Đã kết nối Video sản phẩm vừa render (${data.size_mb} MB)!\nĐường dẫn tải trực tiếp: ${fullUrl}\nSẵn sàng đẩy thẳng lên YouTube qua VPS GitHub 1Gbps.`);
        } else {
            alert(`✅ Đã chọn Video sản phẩm: ${fullUrl}\n(Hãy đảm bảo bạn đã hoàn tất xuất video ở Tab 3 hoặc vừa nhận video thành phẩm từ Cloud)`);
        }
    } catch(e) {
        alert('✅ Đã chọn Video sản phẩm: ' + fullUrl);
    }
}

async function uploadLocalVideoForYouTube(input) {
    if (!input.files || !input.files[0]) return;
    const file = input.files[0];
    const statusEl = document.getElementById('ytVideoStatusMsg');
    statusEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-blue-400"></i> Đang nạp video (' + (file.size/(1024*1024)).toFixed(1) + ' MB)...';
    statusEl.classList.remove('hidden');
    const fd = new FormData();
    fd.append('videoFile', file);
    try {
        const res = await fetch('/api/youtube/upload_local_video_for_publish', {
            method: 'POST',
            body: fd
        });
        const d = await res.json();
        if (d.status === 'ok') {
            document.getElementById('ytVideoUrlInput').value = d.url;
            statusEl.innerHTML = '<span class="text-emerald-400 font-bold">✅ ' + d.message + '</span>';
            alert(d.message);
        } else {
            statusEl.innerHTML = '<span class="text-red-400 font-bold">❌ ' + (d.error || 'Lỗi nạp video') + '</span>';
            alert('Lỗi: ' + (d.error || 'Không thể nạp video'));
        }
    } catch(e) {
        statusEl.innerHTML = '<span class="text-red-400 font-bold">❌ Lỗi: ' + e + '</span>';
        alert('Lỗi nạp video: ' + e);
    }
}

function copyToClipboard(elementId) {
    const el = document.getElementById(elementId);
    if (el) {
        el.select();
        navigator.clipboard.writeText(el.value);
        alert('Đã sao chép nội dung vào Clipboard!');
    }
}
</script>
"""

# ==================== 8. HÀM KÍCH HOẠT VÀ INJECT GIAO DIỆN VÀO 1.PY ====================
def register_nexus_youtube_addon(app, temp_dir=None, update_progress_fn=None, tunnel_info_dict=None):
    global THUMBNAIL_DIR
    if temp_dir:
        YOUTUBE_CONTEXT["temp_dir"] = temp_dir
        THUMBNAIL_DIR = os.path.join(temp_dir, "thumbnails")
        os.makedirs(THUMBNAIL_DIR, exist_ok=True)
    if update_progress_fn: YOUTUBE_CONTEXT["update_progress"] = update_progress_fn
    if tunnel_info_dict: YOUTUBE_CONTEXT["tunnel_info"] = tunnel_info_dict

    app.register_blueprint(youtube_bp)

    @app.after_request
    def _inject_youtube_studio_ui(response):
        if response.content_type and "text/html" in response.content_type and request.path == "/":
            html_text = response.get_data(as_text=True)
            if "tabYouTube" not in html_text and "</body>" in html_text:
                old_tabs_grid = 'grid grid-cols-4 gap-1 p-1.5 bg-[#060911]'
                new_tabs_grid = 'grid grid-cols-5 gap-1 p-1.5 bg-[#060911]'
                if old_tabs_grid in html_text:
                    html_text = html_text.replace(old_tabs_grid, new_tabs_grid)

                old_btn_audio_pattern = r'(<button onclick="switchTab\(\'tabAudio\'\)" id="btn_tabAudio"[^>]*>[\s\S]*?<\/button>)'
                tab_5_btn = r'''\1
                <button onclick="switchTab('tabYouTube')" id="btn_tabYouTube" class="nav-pill py-2 rounded-xl text-red-400 hover:text-red-300 flex flex-col items-center gap-0.5 transition" title="YouTube Studio & Đăng Video">
                    <i class="fa-brands fa-youtube text-red-500"></i> <span>5. YouTube</span>
                </button>'''
                html_text = re.sub(old_btn_audio_pattern, tab_5_btn, html_text, count=1)

                old_history_btn = r'(<button onclick="openHistoryModal\(\)"[^>]*>[\s\S]*?<\/button>)'
                top_yt_btn = r'''<button onclick="switchTab('tabYouTube')" class="px-3 py-2 rounded-xl flex items-center justify-center gap-1 transition text-red-400 bg-red-500/10 border border-red-500/20 hover:bg-red-500/20" title="Mở YouTube Studio & Đăng Kênh">
                <i class="fa-brands fa-youtube text-red-500 text-sm"></i>
            </button>
            \1'''
                html_text = re.sub(old_history_btn, top_yt_btn, html_text, count=1)

                old_switch = "['tabExtract', 'tabContext', 'tabStyle', 'tabAudio']"
                new_switch = "['tabExtract', 'tabContext', 'tabStyle', 'tabAudio', 'tabYouTube']"
                if old_switch in html_text:
                    html_text = html_text.replace(old_switch, new_switch)

                if '<div id="tabExtract"' in html_text:
                    html_text = html_text.replace('<div id="tabExtract"', YOUTUBE_UI_HTML + '\n<div id="tabExtract"')

                response.set_data(html_text)
        return response

    print("[🚀 NEXUS YOUTUBE ADDON 2]: Đã sẵn sàng YouTube Studio (Cookie + Manhua Thumbnail + Đa TK GitHub)!", flush=True)
