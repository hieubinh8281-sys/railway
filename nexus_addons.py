import os
import sys
import re
import time
import json
import math
import socket
import shutil
import subprocess
from threading import Thread, Lock
from flask import Blueprint, request, jsonify, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
import numpy as np
from PIL import Image

addons_bp = Blueprint("nexus_addons", __name__)

# Lưu trữ trạng thái cấu hình Anti-Reup (DVD Watermark + Logo) và SmartBlur
ADDON_SETTINGS = {
    "smartblur_scan_minutes": 4.0,
    "smartblur_pad_y": 14,
    "dvd_text_enabled": False,
    "dvd_text": "NEXUS STUDIO - DO NOT REUP",
    "dvd_font_size": 24,
    "dvd_color": "#FFFFFF",
    "dvd_opacity": 35,          # 0% (vô hình) -> 100% (đậm đặc)
    "dvd_speed": 120,           # Tốc độ di chuyển (pixel / giây)
    "logo_enabled": False,
    "logo_mode": "top_right",   # 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right' | 'dvd_bounce'
    "logo_scale": 110,          # Chiều rộng logo (px)
    "logo_opacity": 75          # Độ đậm của logo (%)
}

ADDON_CONTEXT = {
    "base_dir": os.path.dirname(os.path.abspath(__file__)),
    "temp_dir": None,
    "update_progress": None,
    "tunnel_info": None
}

# ==================== 1. FIX TRUY CẬP TỪ MÁY NGOÀI & TƯƠNG THÍCH RAILWAY 24/7 ====================
def open_windows_firewall_port(port=5000):
    """Tự động mở cổng Firewall trên Windows để các máy khác trong mạng LAN/Wi-Fi truy cập không bị chặn"""
    if os.name != "nt":
        return
    try:
        rule_name = f"NexusStudio_Port_{port}"
        check_cmd = ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"]
        res = subprocess.run(check_cmd, capture_output=True, text=True, errors="ignore")
        if rule_name not in (res.stdout or ""):
            add_cmd = [
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={rule_name}", "dir=in", "action=allow", "protocol=TCP", f"localport={port}"
            ]
            subprocess.run(add_cmd, capture_output=True, text=True, errors="ignore")
    except Exception:
        pass

def start_stable_cloudflare_tunnel(port=5000, tunnel_info_dict=None):
    """
    Khởi chạy Cloudflare Tunnel với cờ --protocol http2 --edge-ip-version 4
    giúp khắc phục triệt để lỗi máy ngoài truy cập bị văng 502 hoặc đứt kết nối khi upload video.
    Nếu đang chạy trên Railway 24/7 thì tự động lấy tên miền Railway.
    """
    # 1. Kiểm tra nếu đang chạy trên máy chủ Railway 24/7
    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or os.environ.get("RAILWAY_STATIC_URL")
    if railway_domain:
        pub_url = railway_domain if railway_domain.startswith("http") else f"https://{railway_domain}"
        if tunnel_info_dict is not None:
            tunnel_info_dict["public_url"] = pub_url.rstrip("/")
            tunnel_info_dict["lan_url"] = pub_url.rstrip("/")
            tunnel_info_dict["status"] = "Railway 24/7 Online"
        print(f"\n[🌐 RAILWAY 24/7 DOMAIN]: {pub_url}\n", flush=True)
        return

    open_windows_firewall_port(port)

    base_dir = ADDON_CONTEXT["base_dir"]
    bin_dir = os.path.join(base_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)

    if os.name == "nt":
        cf_path = os.path.join(bin_dir, "cloudflared.exe")
        dl_url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    else:
        cf_path = os.path.join(bin_dir, "cloudflared")
        dl_url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"

    if shutil.which("cloudflared"):
        cf_path = shutil.which("cloudflared")
    elif not os.path.exists(cf_path):
        try:
            import requests
            print("[*] Đang tải 'cloudflared' chuẩn HTTP/2...")
            resp = requests.get(dl_url, stream=True, timeout=60)
            if resp.status_code == 200:
                with open(cf_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                if os.name != "nt":
                    os.chmod(cf_path, 0o755)
        except Exception as e:
            if tunnel_info_dict is not None:
                tunnel_info_dict["status"] = f"Lỗi tải Tunnel: {e}"
            return

    def _tunnel_worker():
        try:
            # Tắt进程 cloudflared cũ bị treo (nếu có) để tránh xung đột cổng
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # Cờ --protocol http2 và --edge-ip-version 4 giúp mọi máy ngoài truy cập mượt 100%
            cmd = [
                cf_path, "tunnel",
                "--url", f"http://127.0.0.1:{port}",
                "--protocol", "http2",
                "--edge-ip-version", "4",
                "--no-autoupdate"
            ]
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="ignore"
            )
            url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
            for line in proc.stdout:
                m = url_pattern.search(line)
                if m:
                    pub_url = m.group(0)
                    if tunnel_info_dict is not None:
                        tunnel_info_dict["public_url"] = pub_url
                        tunnel_info_dict["status"] = "Online Toàn Cầu (HTTP/2)"
                    print("\n" + "=" * 74)
                    print(f"  [🌐 LINK TRUY CẬP TỪ MỌI MÁY NGOÀI]: {pub_url}")
                    print("=" * 74 + "\n", flush=True)
        except Exception as e:
            if tunnel_info_dict is not None:
                tunnel_info_dict["status"] = f"Lỗi Tunnel: {e}"

    Thread(target=_tunnel_worker, daemon=True).start()


# ==================== 2. SMARTBLUR: QUÉT 3-5 PHÚT ĐẦU & CHE KÍN CẢ HÀNG NGANG ====================
def auto_detect_smartblur_fullrow(video_path, scan_minutes=4.0, pad_y=14, progress_cb=None):
    """
    Quét tự động 3-5 phút đầu của video để phát hiện vị trí dòng phụ đề gốc (Hardsub).
    Sau khi tìm được vị trí dọc (Y và Chiều cao H), tự động mở rộng chiều ngang KÍN 100% MÀN HÌNH
    (x = 0, w = chiều rộng video) để đảm bảo câu dài đến đâu cũng bị che mờ hoàn toàn.
    """
    import av
    if not os.path.exists(video_path):
        raise FileNotFoundError("Không tìm thấy video gốc để quét SmartBlur!")

    max_scan_sec = max(30.0, min(360.0, float(scan_minutes) * 60.0))

    with av.open(video_path) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        vid_w = stream.width
        vid_h = stream.height
        total_dur = float(container.duration / av.time_base) if container.duration else 180.0
        scan_limit = min(total_dur, max_scan_sec)

        # Chỉ quét nửa dưới video (từ 50% đến 96% chiều cao) nơi phụ đề thường xuất hiện
        y_top_bound = int(vid_h * 0.50)
        y_bot_bound = int(vid_h * 0.96)
        roi_h = y_bot_bound - y_top_bound

        # Lấy mẫu khoảng 120 khung hình trải đều trong 3-5 phút đầu
        sample_step = max(0.35, scan_limit / 120.0)
        next_t = 0.0
        row_energy_accum = np.zeros(roi_h, dtype=np.float64)
        active_frames = 0

        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            t = float(frame.pts * stream.time_base)
            if t < next_t:
                continue
            if t > scan_limit:
                break
            next_t = t + sample_step

            if progress_cb:
                pct = 10 + int((t / max(1.0, scan_limit)) * 80)
                progress_cb(min(92, pct), f"🔍 SmartBlur: Đang quét vị trí Sub gốc ({t:.1f}s / {scan_limit:.0f}s)...")

            img = frame.to_ndarray(format="rgb24")
            # Cắt vùng giữa theo chiều ngang (15% -> 85%) để đo mật độ nét chữ chuẩn nhất
            x_left = int(vid_w * 0.15)
            x_right = int(vid_w * 0.85)
            roi = img[y_top_bound:y_bot_bound, x_left:x_right]

            # Chuyển sang ảnh xám và tính đạo hàm ngang |I(x+1) - I(x)| (đặc trưng của nét chữ)
            gray = (0.299 * roi[..., 0] + 0.587 * roi[..., 1] + 0.114 * roi[..., 2]).astype(np.float32)
            grad_x = np.abs(np.diff(gray, axis=1))

            # Đếm tỷ lệ điểm ảnh có biên độ tương phản mạnh (> 32) trên từng hàng ngang y
            high_edge_rows = np.mean(grad_x > 32.0, axis=1)

            # Nếu khung hình này có xuất hiện chữ phụ đề
            if np.max(high_edge_rows) > 0.045:
                row_energy_accum += high_edge_rows
                active_frames += 1

    if active_frames < 2 or np.max(row_energy_accum) <= 0:
        # Dự phòng nếu video ít sub ở đầu: lấy dải phụ đề tiêu chuẩn bao trọn 74% -> 92% chiều cao video
        default_h = int(vid_h * 0.18) // 2 * 2
        default_y = int(vid_h * 0.74) // 2 * 2
        return {
            "x": 0,
            "y": default_y,
            "w": vid_w // 2 * 2,
            "h": default_h,
            "vid_w": vid_w,
            "vid_h": vid_h,
            "detected_frames": active_frames,
            "note": "Áp dụng dải che mờ toàn hàng tiêu chuẩn (74%-92%)"
        }

    # Làm mượt biểu đồ năng lượng theo trục dọc để tìm đỉnh dòng chữ
    kernel = np.ones(5) / 5.0
    smoothed = np.convolve(row_energy_accum / active_frames, kernel, mode="same")
    peak_idx = int(np.argmax(smoothed))
    peak_val = float(smoothed[peak_idx])

    # Mở rộng lên trên và xuống dưới quanh đỉnh để ôm trọn cả phụ đề 1 dòng hoặc 2 dòng
    threshold = peak_val * 0.28
    top_idx = peak_idx
    while top_idx > 0 and smoothed[top_idx] >= threshold:
        top_idx -= 1

    bot_idx = peak_idx
    while bot_idx < roi_h - 1 and smoothed[bot_idx] >= threshold:
        bot_idx += 1

    # Mở rộng dải đệm pad_y tối thiểu 22px để ôm trọn cả dấu chữ và phụ đề 2 dòng
    eff_pad = max(int(pad_y), 22)
    real_y1 = max(0, (y_top_bound + top_idx) - eff_pad)
    real_y2 = min(vid_h, (y_top_bound + bot_idx) + eff_pad)

    # Đảm bảo chiều cao tối thiểu của dải che mờ ít nhất 16% chiều cao video (đủ che trọn phụ đề to)
    min_band_h = max(50, int(vid_h * 0.16))
    if (real_y2 - real_y1) < min_band_h:
        center_y = (real_y1 + real_y2) // 2
        real_y1 = max(0, center_y - min_band_h // 2)
        real_y2 = min(vid_h, real_y1 + min_band_h)

    by = (real_y1 // 2) * 2
    bh = max(16, ((real_y2 - real_y1) // 2) * 2)
    if by + bh > vid_h:
        bh = ((vid_h - by) // 2) * 2

    if progress_cb:
        progress_cb(100, f"✅ SmartBlur: Đã khóa cả hàng ngang (X=0, Y={by}, Rộng={vid_w}, Cao={bh})!")

    return {
        "x": 0,
        "y": by,
        "w": (vid_w // 2) * 2,
        "h": bh,
        "vid_w": vid_w,
        "vid_h": vid_h,
        "detected_frames": active_frames,
        "note": f"Đã khóa dải SmartBlur (Y={by}, Cao={bh})"
    }


# ==================== 3. CHỐNG REUP: CHỮ MỜ CHẠY KIỂU DVD (.ASS) & LOGO OVERLAY ====================
def hex_to_ass_color_with_opacity(hex_color, opacity_percent=35):
    """
    Chuyển mã màu #RRGGBB và độ đậm (0-100%) sang chuẩn ASS &HAABBGGRR.
    Trong ASS: Alpha 00 = Đậm 100%, Alpha FF (255) = Trong suốt 100%.
    """
    hex_color = (hex_color or "#FFFFFF").lstrip("#")
    if len(hex_color) != 6:
        hex_color = "FFFFFF"
    r = hex_color[0:2].upper()
    g = hex_color[2:4].upper()
    b = hex_color[4:6].upper()
    alpha = int(round((100.0 - max(5.0, min(100.0, float(opacity_percent)))) * 2.55))
    alpha = max(0, min(250, alpha))
    return f"&H{alpha:02X}{b}{g}{r}"

def inject_dvd_bouncing_text_into_ass(ass_path, vid_w, vid_h, total_dur, addon_cfg=None):
    """
    Nhúng trực tiếp lớp chữ mờ chạy nảy 4 cạnh màn hình kiểu đầu đĩa DVD vào file .ASS.
    Ưu điểm: Không tốn thêm bộ lọc FFmpeg, hỗ trợ 100% tiếng Việt có dấu và chạy được cả trên Cloud.
    """
    cfg = addon_cfg or ADDON_SETTINGS
    if not cfg.get("dvd_text_enabled") or not os.path.exists(ass_path):
        return

    text = (cfg.get("dvd_text") or "").strip()
    if not text:
        return

    play_res_y = 720
    play_res_x = int(round(720 * (vid_w / max(1, vid_h))))
    font_size = int(cfg.get("dvd_font_size", 24))
    opacity_pct = float(cfg.get("dvd_opacity", 35))
    speed = max(30.0, float(cfg.get("dvd_speed", 120)))

    ass_primary = hex_to_ass_color_with_opacity(cfg.get("dvd_color", "#FFFFFF"), opacity_pct)
    ass_outline = hex_to_ass_color_with_opacity("#000000", opacity_pct * 0.7)

    with open(ass_path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    dvd_style_line = (
        f"Style: DVDWatermark,Arial,{font_size},{ass_primary},{ass_primary},"
        f"{ass_outline},&HFF000000,-1,0,0,0,100,100,1,0,1,1,0,5,10,10,10,1\n"
    )

    if "[V4+ Styles]" in content and "Style: DVDWatermark" not in content:
        content = content.replace("[Events]", dvd_style_line + "\n[Events]")

    # Tính toán quỹ đạo bật nảy kiểu DVD xung quanh 4 mép màn hình
    margin_x = max(80, min(play_res_x // 3, len(text) * (font_size // 3)))
    margin_y = max(35, font_size + 10)
    min_x, max_x = margin_x, max(margin_x + 50, play_res_x - margin_x)
    min_y, max_y = margin_y, max(margin_y + 50, play_res_y - margin_y)

    cur_x, cur_y = min_x + 40, min_y + 40
    vx = speed * 0.82
    vy = speed * 0.58

    def _fmt_ass_t(sec):
        hrs = int(sec // 3600)
        mins = int((sec % 3600) // 60)
        secs = int(sec % 60)
        cs = min(99, int(round((sec - int(sec)) * 100)))
        return f"{hrs:d}:{mins:02d}:{secs:02d}.{cs:02d}"

    cur_t = 0.0
    max_t = max(10.0, float(total_dur or 300.0))
    dialogue_lines = []

    while cur_t < max_t:
        tx = (max_x - cur_x) / vx if vx > 0 else (min_x - cur_x) / vx
        ty = (max_y - cur_y) / vy if vy > 0 else (min_y - cur_y) / vy
        dt = max(0.5, min(tx, ty))
        if cur_t + dt > max_t:
            dt = max_t - cur_t

        next_x = int(round(cur_x + vx * dt))
        next_y = int(round(cur_y + vy * dt))

        t1_str = _fmt_ass_t(cur_t)
        t2_str = _fmt_ass_t(cur_t + dt)
        clean_text = text.replace("\n", " ")
        dialogue_lines.append(
            f"Dialogue: 1,{t1_str},{t2_str},DVDWatermark,,0,0,0,,{{\\an5\\move({int(cur_x)},{int(cur_y)},{next_x},{next_y})}}{clean_text}"
        )

        cur_x, cur_y = next_x, next_y
        cur_t += dt
        if abs(tx - ty) < 0.05:
            vx = -vx
            vy = -vy
        elif tx < ty:
            vx = -vx
        else:
            vy = -vy

    with open(ass_path, "w", encoding="utf-8-sig") as f:
        f.write(content.rstrip() + "\n" + "\n".join(dialogue_lines) + "\n")

def build_filter_complex_with_logo(
    vid_w, vid_h, blur_rect, blur_sigma, blur_opacity, blur_color,
    ass_filename="sub_vi.ass", logo_path=None, addon_cfg=None, blur_areas=None
):
    """
    Tạo chuỗi -filter_complex kết hợp:
    1. Vùng làm mờ SmartBlur sâu & tự nhiên (Hỗ trợ 1 hoặc nhiều vùng: Multi-area Blur)
    2. Phụ đề chính + Chữ mờ chạy kiểu DVD (trong file .ASS)
    3. Logo chống bản quyền (Đứng yên ở góc hoặc Bay nảy kiểu DVD)
    Returns: (vf_chain_str, has_logo_input_bool)
    """
    cfg = addon_cfg or ADDON_SETTINGS
    blur_color_clean = (blur_color or "000000").lstrip("#")
    escaped_ass = (ass_filename or "sub_vi.ass").replace("\\", "/").replace(":", "\\:").replace("'", "\\'")

    # Chuẩn bị danh sách các vùng làm mờ (Multi-area blur: che sub, logo bản quyền, watermark...)
    raw_areas = []
    if blur_areas and isinstance(blur_areas, list) and len(blur_areas) > 0:
        raw_areas = list(blur_areas)
    elif blur_rect and isinstance(blur_rect, dict) and blur_rect.get("w", 0) > 0 and blur_rect.get("h", 0) > 0:
        raw_areas = [blur_rect]

    valid_areas = []
    for rect in raw_areas:
        bx = max(0, int(rect.get("x", 0)) // 2 * 2)
        by = max(0, int(rect.get("y", 0)) // 2 * 2)
        bw = max(0, int(rect.get("w", 0)) // 2 * 2)
        bh = max(0, int(rect.get("h", 0)) // 2 * 2)
        if vid_w and vid_h:
            bw = 0 if bx >= vid_w - 4 else min(bw, (vid_w - bx) // 2 * 2)
            bh = 0 if by >= vid_h - 4 else min(bh, (vid_h - by) // 2 * 2)
        if bw >= 8 and bh >= 8:
            valid_areas.append({"x": bx, "y": by, "w": bw, "h": bh})

    # Kính mờ quang học tự nhiên xóa triệt để chữ gốc không để lại bóng
    eff_sigma = max(10, blur_sigma if blur_sigma and blur_sigma > 0 else 22)

    if len(valid_areas) == 0:
        base_chain = f"[0:v]ass=\\'{escaped_ass}\\'[v_sub]"
    elif len(valid_areas) == 1:
        a = valid_areas[0]
        safe_r = max(2, min(eff_sigma, min(a['w'], a['h']) // 3))
        b_steps = [f"crop={a['w']}:{a['h']}:{a['x']}:{a['y']}"]
        b_steps.append(f"boxblur=luma_radius={safe_r}:luma_power=2:chroma_radius={safe_r}")
        if blur_opacity > 0.02:
            b_steps.append(f"drawbox=x=0:y=0:w={a['w']}:h={a['h']}:color=0x{blur_color_clean}@{blur_opacity:.2f}:t=fill")
        base_chain = (
            f"[0:v]split=2[main][cs];"
            f"[cs]{','.join(b_steps)}[bl];"
            f"[main][bl]overlay={a['x']}:{a['y']}:shortest=1,ass=\\'{escaped_ass}\\'[v_sub]"
        )
    else:
        # Đa vùng làm mờ (Multi-area blur)
        n = len(valid_areas)
        split_names = "".join([f"[cs{i}]" for i in range(n)])
        chain_parts = [f"[0:v]split={n+1}[main]{split_names}"]
        for i, a in enumerate(valid_areas):
            safe_r = max(2, min(eff_sigma, min(a['w'], a['h']) // 3))
            b_steps = [f"crop={a['w']}:{a['h']}:{a['x']}:{a['y']}"]
            b_steps.append(f"boxblur=luma_radius={safe_r}:luma_power=2:chroma_radius={safe_r}")
            if blur_opacity > 0.02:
                b_steps.append(f"drawbox=x=0:y=0:w={a['w']}:h={a['h']}:color=0x{blur_color_clean}@{blur_opacity:.2f}:t=fill")
            chain_parts.append(f"[cs{i}]{','.join(b_steps)}[bl{i}]")

        last_layer = "[main]"
        for i, a in enumerate(valid_areas):
            next_layer = f"[tmp{i}]" if i < n - 1 else "[v_bl]"
            chain_parts.append(f"{last_layer}[bl{i}]overlay={a['x']}:{a['y']}:shortest=1{next_layer}")
            last_layer = next_layer
        chain_parts.append(f"[v_bl]ass=\\'{escaped_ass}\\'[v_sub]")
        base_chain = ";".join(chain_parts)

    use_logo = bool(cfg.get("logo_enabled") and logo_path and os.path.exists(logo_path))
    if not use_logo:
        return base_chain.replace("[v_sub]", ",scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p[outv]"), False

    logo_w = max(32, min(400, int(cfg.get("logo_scale", 110))))
    logo_alpha = max(0.1, min(1.0, float(cfg.get("logo_opacity", 75)) / 100.0))
    logo_mode = cfg.get("logo_mode", "top_right")

    if logo_mode == "top_left":
        pos_expr = "x=24:y=24"
    elif logo_mode == "bottom_left":
        pos_expr = "x=24:y=H-h-24"
    elif logo_mode == "bottom_right":
        pos_expr = "x=W-w-24:y=H-h-24"
    elif logo_mode == "dvd_bounce":
        # Công thức sóng tam giác giúp Logo bay nảy 4 cạnh màn hình kiểu DVD
        pos_expr = "x='abs(mod(t*95,2*(W-w))-(W-w))':y='abs(mod(t*65,2*(H-h))-(H-h))'"
    else:
        pos_expr = "x=W-w-24:y=24"

    # Lưu ý: Input của Logo sẽ được truyền vào làm input cuối cùng của FFmpeg
    logo_chain = (
        f"{base_chain};"
        f"[LOGO_IN]scale={logo_w}:-2,format=rgba,colorchannelmixer=aa={logo_alpha:.2f}[logo_img];"
        f"[v_sub][logo_img]overlay={pos_expr}:shortest=1,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p[outv]"
    )
    return logo_chain, True


# ==================== 4. TRÌNH TẠO BỘ FILE DEPLOY LÊN RAILWAY 24/7 ====================
def generate_railway_deployment_files(base_dir=None, main_script="1.py"):
    """
    Tự động sinh ra Dockerfile, railway.json, requirements.txt và .dockerignore
    để người dùng đẩy lên Railway chạy Web 24/7 (có sẵn FFmpeg, Font đa ngôn ngữ, Piper TTS).
    """
    target_dir = base_dir or ADDON_CONTEXT["base_dir"]
    if not os.path.exists(os.path.join(target_dir, main_script)):
        if os.path.exists(os.path.join(target_dir, "vietsub_studio.py")):
            main_script = "vietsub_studio.py"

    requirements_txt = """flask>=3.0.0
werkzeug>=3.0.0
requests>=2.31.0
faster-whisper>=1.0.3
edge-tts>=6.1.12
pydub>=0.25.1
Pillow>=10.0.0
numpy>=1.24.0
av>=12.0.0
yt-dlp>=2024.0.0
audioop-lts; python_version >= '3.13'
"""

    dockerfile_content = f"""FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV NO_BROWSER=1

# Cài đặt FFmpeg và bộ Font Quốc tế / Tiếng Việt / CJK cho Railway
RUN apt-get update && apt-get install -y --no-install-recommends \\
    ffmpeg \\
    curl \\
    ca-certificates \\
    fonts-liberation \\
    fonts-noto-core \\
    fonts-noto-cjk \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

EXPOSE 5000

CMD ["python", "{main_script}"]
"""

    railway_json = {
        "$schema": "https://railway.app/railway.schema.json",
        "build": {
            "builder": "DOCKERFILE",
            "dockerfilePath": "Dockerfile"
        },
        "deploy": {
            "numReplicas": 1,
            "restartPolicyType": "ALWAYS"
        }
    }

    dockerignore_content = """temp_session/
projects/
kaggle_worker_build/
__pycache__/
*.pyc
.git/
"""

    with open(os.path.join(target_dir, "requirements.txt"), "w", encoding="utf-8") as f:
        f.write(requirements_txt)
    with open(os.path.join(target_dir, "Dockerfile"), "w", encoding="utf-8") as f:
        f.write(dockerfile_content)
    with open(os.path.join(target_dir, "railway.json"), "w", encoding="utf-8") as f:
        json.dump(railway_json, f, indent=2)
    with open(os.path.join(target_dir, ".dockerignore"), "w", encoding="utf-8") as f:
        f.write(dockerignore_content)

    return ["Dockerfile", "railway.json", "requirements.txt", ".dockerignore"]


# ==================== 5. CÁC ROUTE API & TỰ ĐỘNG GẮN GIAO DIỆN VÀO 1.PY ====================
@addons_bp.route("/api/addons/settings", methods=["GET", "POST"])
def api_addon_settings():
    if request.method == "POST":
        data = request.json or {}
        ADDON_SETTINGS.update(data)
        return jsonify({"status": "ok", "settings": ADDON_SETTINGS})
    return jsonify(ADDON_SETTINGS)

@addons_bp.route("/api/addons/smartblur", methods=["POST"])
def api_run_smartblur():
    temp_dir = ADDON_CONTEXT["temp_dir"]
    video_path = ""

    # 1. Tìm video trong workspace người dùng (nếu có hệ thống workspace đa người dùng)
    try:
        import sys
        main_mod = sys.modules.get("__main__")
        if main_mod and hasattr(main_mod, "get_workspace_dir"):
            ws_dir = main_mod.get_workspace_dir(request)
            cand = os.path.join(ws_dir, "input_video.mp4")
            if os.path.exists(cand) and os.path.getsize(cand) > 100:
                video_path = cand
    except Exception:
        pass

    # 2. Tìm video trong thư mục session chung
    if not video_path:
        cand = os.path.join(temp_dir, "input_video.mp4") if temp_dir else ""
        if cand and os.path.exists(cand) and os.path.getsize(cand) > 100:
            video_path = cand

    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Vui lòng chọn tải Video lên trước khi chạy SmartBlur!"}), 400

    data = request.json or {}
    scan_minutes = float(data.get("scan_minutes", ADDON_SETTINGS["smartblur_scan_minutes"]))
    pad_y = int(data.get("pad_y", ADDON_SETTINGS["smartblur_pad_y"]))
    ADDON_SETTINGS["smartblur_scan_minutes"] = scan_minutes
    ADDON_SETTINGS["smartblur_pad_y"] = pad_y

    try:
        res = auto_detect_smartblur_fullrow(
            video_path,
            scan_minutes=scan_minutes,
            pad_y=pad_y,
            progress_cb=ADDON_CONTEXT["update_progress"]
        )
        return jsonify({"status": "ok", **res})
    except Exception as e:
        return jsonify({"error": f"Lỗi SmartBlur: {str(e)}"}), 500

@addons_bp.route("/api/addons/upload_logo", methods=["POST"])
def api_upload_logo():
    temp_dir = ADDON_CONTEXT["temp_dir"]
    file = request.files.get("logo")
    if not file or not temp_dir:
        return jsonify({"error": "Không tìm thấy file ảnh Logo!"}), 400

    logo_path = os.path.join(temp_dir, "watermark_logo.png")
    img = Image.open(file.stream).convert("RGBA")
    img.save(logo_path, format="PNG")
    ADDON_SETTINGS["logo_enabled"] = True
    return jsonify({
        "status": "ok",
        "logo_url": "/temp_session/watermark_logo.png",
        "message": "Đã tải Logo chống bản quyền thành công!"
    })




# Đoạn HTML/JS tự động gắn thêm khung SmartBlur + DVD Watermark + Logo + Railway vào Tab 3 của giao diện
ADDON_UI_INJECTION = """
<!-- NEXUS ADDONS: SMARTBLUR FULL-ROW, DVD WATERMARK, LOGO & RAILWAY 24/7 -->
<script>
window.addEventListener('DOMContentLoaded', () => {
    const tabStyle = document.getElementById('tabStyle');
    if (!tabStyle) return;

    const addonBox = document.createElement('div');
    addonBox.className = "studio-card rounded-xl p-3.5 space-y-3 border border-sky-500/40 mt-3";
    addonBox.innerHTML = `
        <div class="flex items-center justify-between">
            <span class="text-xs font-extrabold text-sky-400 uppercase"><i class="fa-solid fa-wand-magic-sparkles mr-1.5"></i>SmartBlur (Quét 3-5p Đầu)</span>
            <span class="text-[10px] px-2 py-0.5 rounded bg-sky-500/20 text-sky-300 font-bold">Che Kín Cả Hàng</span>
        </div>
        <p class="text-[11px] text-slate-400 leading-relaxed">
            Tự động quét 3-5 phút đầu để dò vị trí dòng Sub gốc và khóa <b>kín 100% chiều ngang</b> (câu dài sát mép cũng không bị lộ).
        </p>
        <div class="grid grid-cols-2 gap-2">
            <div>
                <label class="text-[11px] text-slate-400">Thời lượng quét (Phút)</label>
                <input type="number" id="sbScanMinutes" value="4" min="1" max="10" step="0.5" class="w-full mt-1 bg-slate-950 border border-slate-700 rounded-lg px-2 py-1 text-xs text-white"/>
            </div>
            <div>
                <label class="text-[11px] text-slate-400">Đệm lề trên/dưới (px)</label>
                <input type="number" id="sbPadY" value="14" min="4" max="50" class="w-full mt-1 bg-slate-950 border border-slate-700 rounded-lg px-2 py-1 text-xs text-white"/>
            </div>
        </div>
        <button onclick="runAutoSmartBlur()" id="btnSmartBlur" class="w-full py-2 rounded-xl bg-gradient-to-r from-sky-500 to-indigo-600 hover:brightness-110 text-white text-xs font-extrabold shadow-lg transition flex items-center justify-center gap-1.5">
            <i class="fa-solid fa-crosshairs"></i> Tự Động Quét & Che Cả Hàng Sub
        </button>

        <div class="pt-3 border-t border-slate-800 space-y-2.5">
            <div class="flex items-center justify-between">
                <span class="text-xs font-extrabold text-amber-400 uppercase"><i class="fa-solid fa-compact-disc mr-1.5"></i>Chống Reup: Chữ Mờ DVD & Logo</span>
                <input type="checkbox" id="dvdTextEnabled" onchange="syncAddonSettings()" class="w-4 h-4 accent-amber-500 rounded cursor-pointer" title="Bật chữ mờ bay quanh màn hình kiểu DVD"/>
            </div>
            <input type="text" id="dvdTextContent" oninput="syncAddonSettings()" value="NEXUS STUDIO - DO NOT REUP" placeholder="Nhập tên kênh / chữ mờ chạy kiểu DVD..." class="w-full bg-slate-950 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-amber-300 outline-none"/>
            <div class="grid grid-cols-3 gap-2">
                <div>
                    <label class="text-[10px] text-slate-400">Cỡ chữ DVD</label>
                    <input type="number" id="dvdFontSize" oninput="syncAddonSettings()" value="24" min="12" max="60" class="w-full mt-0.5 bg-slate-950 border border-slate-700 rounded-lg px-2 py-1 text-xs text-white"/>
                </div>
                <div>
                    <label class="text-[10px] text-slate-400">Độ đậm (%)</label>
                    <input type="number" id="dvdOpacity" oninput="syncAddonSettings()" value="35" min="5" max="100" class="w-full mt-0.5 bg-slate-950 border border-slate-700 rounded-lg px-2 py-1 text-xs text-white"/>
                </div>
                <div>
                    <label class="text-[10px] text-slate-400">Tốc độ bay</label>
                    <input type="number" id="dvdSpeed" oninput="syncAddonSettings()" value="120" min="40" max="350" class="w-full mt-0.5 bg-slate-950 border border-slate-700 rounded-lg px-2 py-1 text-xs text-white"/>
                </div>
            </div>

            <!-- Logo Image Watermark -->
            <div class="pt-2 border-t border-slate-800/70 space-y-2">
                <div class="flex items-center justify-between">
                    <span class="text-[11px] font-bold text-emerald-400"><i class="fa-solid fa-image mr-1"></i>Chèn Logo Ảnh (.PNG/.JPG)</span>
                    <input type="checkbox" id="logoEnabled" onchange="syncAddonSettings()" class="w-4 h-4 accent-emerald-500 rounded cursor-pointer"/>
                </div>
                <input type="file" id="logoFileInput" accept="image/*" onchange="uploadWatermarkLogo(event)" class="block w-full text-[11px] text-slate-400 file:mr-2 file:py-1 file:px-2.5 file:rounded-lg file:border-0 file:bg-emerald-600 file:text-white cursor-pointer"/>
                <div class="grid grid-cols-2 gap-2">
                    <div>
                        <label class="text-[10px] text-slate-400">Kiểu hiển thị Logo</label>
                        <select id="logoMode" onchange="syncAddonSettings()" class="w-full mt-0.5 bg-slate-950 border border-slate-700 rounded-lg px-2 py-1 text-xs text-white">
                            <option value="top_right">Góc Trên Phải</option>
                            <option value="top_left">Góc Trên Trái</option>
                            <option value="bottom_right">Góc Dưới Phải</option>
                            <option value="bottom_left">Góc Dưới Trái</option>
                            <option value="dvd_bounce">💿 Bay Nảy Kiểu DVD</option>
                        </select>
                    </div>
                    <div>
                        <label class="text-[10px] text-slate-400">Kích thước Logo (px)</label>
                        <input type="number" id="logoScale" onchange="syncAddonSettings()" value="110" min="40" max="350" class="w-full mt-0.5 bg-slate-950 border border-slate-700 rounded-lg px-2 py-1 text-xs text-white"/>
                    </div>
                </div>
            </div>
        </div>


    `;
    tabStyle.appendChild(addonBox);
});

async function runAutoSmartBlur() {
    const btn = document.getElementById('btnSmartBlur');
    const origHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Đang quét 3-5p đầu...';
    }
    if (typeof startPolling === 'function') startPolling();
    try {
        const scanM = document.getElementById('sbScanMinutes') ? parseFloat(document.getElementById('sbScanMinutes').value || 4) : 4;
        const padY = document.getElementById('sbPadY') ? parseInt(document.getElementById('sbPadY').value || 14) : 14;
        const res = await fetch('/api/addons/smartblur', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scan_minutes: scanM, pad_y: padY })
        });
        const data = await res.json();
        if (typeof stopPollingAndSync === 'function') await stopPollingAndSync();
        if (data.error) {
            console.warn("Smartblur warning:", data.error);
            return false;
        }

        blurRect = { x: data.x, y: data.y, w: data.w, h: data.h };
        if (typeof blurAreas !== 'undefined') {
            blurAreas = [{ x: data.x, y: data.y, w: data.w, h: data.h }];
            if (typeof renderBlurAreasList === 'function') renderBlurAreasList();
        }
        if (typeof updateBoxLabels === 'function') updateBoxLabels();
        if (typeof drawOverlayOnCanvas === 'function') drawOverlayOnCanvas();
        if (typeof setStatus === 'function') setStatus(100, data.note || "Đã khóa SmartBlur toàn hàng ngang!");
        return true;
    } catch (e) {
        if (typeof stopPollingAndSync === 'function') await stopPollingAndSync();
        console.warn("Lỗi quét SmartBlur:", e);
        return false;
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origHtml;
        }
    }
}

async function syncAddonSettings() {
    const payload = {
        dvd_text_enabled: document.getElementById('dvdTextEnabled')?.checked || false,
        dvd_text: document.getElementById('dvdTextContent')?.value || '',
        dvd_font_size: parseInt(document.getElementById('dvdFontSize')?.value || 24),
        dvd_opacity: parseInt(document.getElementById('dvdOpacity')?.value || 35),
        dvd_speed: parseInt(document.getElementById('dvdSpeed')?.value || 120),
        logo_enabled: document.getElementById('logoEnabled')?.checked || false,
        logo_mode: document.getElementById('logoMode')?.value || 'top_right',
        logo_scale: parseInt(document.getElementById('logoScale')?.value || 110)
    };
    await fetch('/api/addons/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
}

async function uploadWatermarkLogo(e) {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('logo', file);
    const res = await fetch('/api/addons/upload_logo', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.error) return alert(data.error);
    document.getElementById('logoEnabled').checked = true;
    await syncAddonSettings();
    alert(data.message);
}


</script>
"""

def register_nexus_addons(app, temp_dir, update_progress_fn, tunnel_info_dict=None):
    """
    Hàm kết nối duy nhất cần gọi trong 1.py để kích hoạt toàn bộ tính năng nâng cấp:
    - Sửa lỗi truy cập từ máy ngoài (CORS, ProxyFix, Firewall, HTTP/2 Tunnel)
    - Tự động gắn giao diện SmartBlur + DVD Watermark + Logo + Railway
    """
    ADDON_CONTEXT["temp_dir"] = temp_dir
    ADDON_CONTEXT["update_progress"] = update_progress_fn
    ADDON_CONTEXT["tunnel_info"] = tunnel_info_dict

    # Bật ProxyFix để hoạt động chuẩn xác sau HTTPS của Railway & Cloudflare Tunnel
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.register_blueprint(addons_bp)

    @app.after_request
    def _apply_cors_and_inject_ui(response):
        # Cho phép mọi máy ngoài truy cập API & tải Video không bị chặn CORS
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, Range"
        response.headers["Access-Control-Expose-Headers"] = "Content-Range, Content-Length, Accept-Ranges"

        # Tự động cập nhật public_url nếu người dùng truy cập qua tên miền Railway hoặc Tunnel
        if tunnel_info_dict is not None and not tunnel_info_dict.get("public_url"):
            host = request.headers.get("X-Forwarded-Host") or request.host or ""
            if any(d in host for d in [".up.railway.app", ".trycloudflare.com", ".ngrok"]):
                proto = request.headers.get("X-Forwarded-Proto", "https")
                tunnel_info_dict["public_url"] = f"{proto}://{host}"

        # Tự động gắn bảng điều khiển SmartBlur + DVD Watermark + Logo vào trang chủ
        if response.content_type and "text/html" in response.content_type and request.path == "/":
            html_text = response.get_data(as_text=True)
            if "NEXUS ADDONS:" not in html_text and "</body>" in html_text:
                html_text = html_text.replace("</body>", ADDON_UI_INJECTION + "\n</body>")
                response.set_data(html_text)
        return response
