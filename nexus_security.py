import os
import sys
import json
import time
import uuid
import hmac
import hashlib
import platform
import subprocess
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LICENSE_DB_PATH = os.path.join(BASE_DIR, "license_db.json")
LOCAL_LICENSE_PATH = os.path.join(BASE_DIR, "local_license.json")
SECRET_KEY_PATH = os.path.join(BASE_DIR, "secret_hmac.key")

# Tạo hoặc đọc server secret HMAC
if not os.path.exists(SECRET_KEY_PATH):
    with open(SECRET_KEY_PATH, "w", encoding="utf-8") as f:
        f.write(uuid.uuid4().hex + uuid.uuid4().hex)

with open(SECRET_KEY_PATH, "r", encoding="utf-8") as f:
    SERVER_HMAC_SECRET = f.read().strip()

# ==================== 1. HWID & ANTI-DEBUGGER PHẦN CỨNG MÁY TÍNH ====================
def get_machine_hwid():
    """Tạo Hardware ID (HWID) duy nhất cho máy tính Windows"""
    hw_parts = []
    if os.name == "nt":
        try:
            cmd = ["reg", "query", "HKLM\\SOFTWARE\\Microsoft\\Cryptography", "/v", "MachineGuid"]
            out = subprocess.check_output(cmd, text=True, errors="ignore")
            for line in out.splitlines():
                if "MachineGuid" in line:
                    hw_parts.append(line.split()[-1].strip())
        except Exception:
            pass

        try:
            out = subprocess.check_output(["wmic", "csproduct", "get", "uuid"], text=True, errors="ignore")
            for line in out.splitlines():
                clean = line.strip()
                if clean and "UUID" not in clean:
                    hw_parts.append(clean)
                    break
        except Exception:
            pass

    hw_parts.append(platform.processor() or "CPU")
    hw_parts.append(os.environ.get("COMPUTERNAME", "PC"))

    raw_id = "|".join(hw_parts)
    hash_hex = hashlib.sha256(raw_id.encode("utf-8")).hexdigest().upper()
    return f"HWID-{hash_hex[:4]}-{hash_hex[4:8]}-{hash_hex[8:12]}-{hash_hex[12:16]}"

# Danh sách công cụ dịch ngược / hack / can thiệp tiến trình bị cấm
BLACK_LISTED_PROCESSES = {
    "processhacker.exe", "processhacker", "processhacker2.exe", "processhacker3.exe",
    "dnspy.exe", "dnspy", "dnspy-x86.exe",
    "x64dbg.exe", "x32dbg.exe", "x96dbg.exe",
    "ida.exe", "ida64.exe", "idag.exe", "idag64.exe",
    "ghidra", "ghidra.exe",
    "cheatengine.exe", "cheatengine-x86_64.exe", "cheatengine-i386.exe",
    "fiddler.exe", "fiddler", "charles.exe", "httpdebuggerui.exe", "httpdebugger.exe",
    "wireshark.exe", "scylla.exe", "pestudio.exe", "de4dot.exe", "ilspy.exe"
}

def scan_running_processes_fast():
    if os.name != "nt":
        return set()
    procs = []
    try:
        TH32CS_SNAPPROCESS = 0x00000002
        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260)
            ]
        hSnap = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if hSnap == -1:
            return set()
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if ctypes.windll.kernel32.Process32First(hSnap, ctypes.byref(pe)):
            while True:
                name = pe.szExeFile.decode("utf-8", errors="ignore").lower()
                procs.append(name)
                if not ctypes.windll.kernel32.Process32Next(hSnap, ctypes.byref(pe)):
                    break
        ctypes.windll.kernel32.CloseHandle(hSnap)
    except Exception:
        pass
    return set(procs)

def start_anti_debugger_guard(interval=2.5):
    """Khởi chạy luồng giám sát ngầm phát hiện Process Hacker, dnSpy, Debugger"""
    import threading
    def _guard():
        while True:
            time.sleep(interval)
            try:
                running = scan_running_processes_fast()
                detected = BLACK_LISTED_PROCESSES.intersection(running)
                if detected:
                    tool_name = list(detected)[0]
                    print("\n" + "="*70)
                    print(f"  [⚠️ CẢNH BÁO BẢO MẬT] Phát hiện công cụ dịch ngược/can thiệp: {tool_name}")
                    print("  Hệ thống bảo vệ Nexus tự động ngắt tiến trình và đóng ứng dụng ngay lập tức.")
                    print("="*70 + "\n", flush=True)
                    os._exit(1)
            except Exception:
                pass
    t = threading.Thread(target=_guard, daemon=True)
    t.start()

# ==================== 2. QUẢN LÝ LICENSE KEY (DATABASE & ADMIN) ====================
# Mật khẩu quản trị bảo mật cao, mã hóa SHA-256 (Nexus@Adm!n_2026#VipKey)
DEFAULT_ADMIN_PWD_HASH = "5f762f5fa2fada5ea58c9020f83c47a9770c2064acac427ff5d19edcd0d74d9e"

def load_license_db():
    if not os.path.exists(LICENSE_DB_PATH):
        db = {
            "admin_pwd_hash": DEFAULT_ADMIN_PWD_HASH,
            "keys": {
                "NEXUS-FREE-DEMO-2USES": {
                    "key": "NEXUS-FREE-DEMO-2USES",
                    "customer": "Dùng Thử Miễn Phí (2 Lượt CPU)",
                    "tier": "basic",
                    "quota_limit": 2,
                    "quota_used": 0,
                    "duration_days": 7,
                    "created_at": datetime.now().isoformat(),
                    "expires_at": (datetime.now() + timedelta(days=7)).isoformat(),
                    "status": "active",
                    "bound_device_id": None,
                    "bound_ip": None,
                    "bound_user_agent": None,
                    "last_active": None
                },
                "NEXUS-VIP-PRO-DEMO": {
                    "key": "NEXUS-VIP-PRO-DEMO",
                    "customer": "Bản VIP Quản Trị (PRO GPU)",
                    "tier": "pro",
                    "quota_limit": 100,
                    "quota_used": 0,
                    "duration_days": 30,
                    "created_at": datetime.now().isoformat(),
                    "expires_at": (datetime.now() + timedelta(days=30)).isoformat(),
                    "status": "active",
                    "bound_device_id": None,
                    "bound_ip": None,
                    "bound_user_agent": None,
                    "last_active": None
                }
            }
        }
        save_license_db(db)
        return db
    try:
        with open(LICENSE_DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Tự động đồng bộ các trường tier và quota nếu thiếu
            for k, rec in data.get("keys", {}).items():
                rec.setdefault("tier", "pro")
                rec.setdefault("quota_limit", 100)
                rec.setdefault("quota_used", 0)
            return data
    except Exception:
        return {"admin_pwd_hash": DEFAULT_ADMIN_PWD_HASH, "keys": {}}

def save_license_db(db):
    with open(LICENSE_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

def verify_admin_password(password):
    db = load_license_db()
    pwd_hash = hashlib.sha256((password or "").encode("utf-8")).hexdigest()
    env_pwd = os.environ.get("ADMIN_PASSWORD")
    if env_pwd and password == env_pwd:
        return True
    return pwd_hash == db.get("admin_pwd_hash", DEFAULT_ADMIN_PWD_HASH)

def generate_random_key():
    p1 = uuid.uuid4().hex[:4].upper()
    p2 = uuid.uuid4().hex[4:8].upper()
    p3 = uuid.uuid4().hex[8:12].upper()
    return f"NEXUS-{p1}-{p2}-{p3}"

def create_license_key(customer="Khách hàng", duration_days=30, tier="pro", quota_limit=50):
    db = load_license_db()
    key_str = generate_random_key()
    now = datetime.now()
    if duration_days >= 9999:
        expires_at = "2099-12-31T23:59:59"
    else:
        expires_at = (now + timedelta(days=duration_days)).isoformat()

    tier_clean = "basic" if str(tier).lower() == "basic" else "pro"
    key_record = {
        "key": key_str,
        "customer": customer or "Khách hàng",
        "tier": tier_clean,
        "quota_limit": int(quota_limit),
        "quota_used": 0,
        "duration_days": duration_days,
        "created_at": now.isoformat(),
        "expires_at": expires_at,
        "status": "active",
        "bound_device_id": None,
        "bound_ip": None,
        "bound_user_agent": None,
        "last_active": None
    }
    db["keys"][key_str] = key_record
    save_license_db(db)
    return key_record

def add_license_quota(key_str, extra_quota=10):
    db = load_license_db()
    if key_str in db["keys"]:
        k = db["keys"][key_str]
        cur = k.get("quota_limit", 50)
        if cur != -1:
            k["quota_limit"] = cur + extra_quota
        save_license_db(db)
        return True, k.get("quota_limit", 50)
    return False, "Không tìm thấy key"

def consume_license_quota(key_str, amount=1, device_id=None):
    db = load_license_db()
    if key_str in db["keys"]:
        k = db["keys"][key_str]
        if key_str == "NEXUS-FREE-DEMO-2USES" and device_id:
            dev_map = k.setdefault("device_usage", {})
            d_used = dev_map.get(device_id, 0)
            dev_map[device_id] = d_used + amount
            save_license_db(db)
            return True, dev_map[device_id]
        limit = k.get("quota_limit", 50)
        used = k.get("quota_used", 0)
        if limit != -1 and used >= limit:
            return False, f"Hạn mức sử dụng của Key đã hết ({used}/{limit} lượt)! Vui lòng liên hệ Admin để nạp thêm lượt."
        k["quota_used"] = used + amount
        save_license_db(db)
        return True, k["quota_used"]
    return False, "Key không tồn tại"

def revoke_license_key(key_str):
    db = load_license_db()
    if key_str in db["keys"]:
        db["keys"][key_str]["status"] = "revoked"
        save_license_db(db)
        return True
    return False

def reset_device_binding(key_str):
    db = load_license_db()
    if key_str in db["keys"]:
        db["keys"][key_str]["bound_device_id"] = None
        db["keys"][key_str]["bound_ip"] = None
        db["keys"][key_str]["bound_user_agent"] = None
        save_license_db(db)
        return True
    return False

def extend_license_key(key_str, extra_days=30):
    db = load_license_db()
    if key_str in db["keys"]:
        k = db["keys"][key_str]
        try:
            cur_exp = datetime.fromisoformat(k["expires_at"])
            new_exp = max(datetime.now(), cur_exp) + timedelta(days=extra_days)
            k["expires_at"] = new_exp.isoformat()
            k["status"] = "active"
            save_license_db(db)
            return True, k["expires_at"]
        except Exception as e:
            return False, str(e)
    return False, "Không tìm thấy key"

def delete_license_key(key_str):
    db = load_license_db()
    if key_str in db["keys"]:
        del db["keys"][key_str]
        save_license_db(db)
        return True
    return False

# ==================== 3. XÁC THỰC BẢN QUYỀN & CHỐNG CHIA SẺ COOKIE ====================
def validate_license(key_str, device_id, client_ip="127.0.0.1", user_agent=""):
    """
    Xác thực License Key:
    - Kiểm tra tồn tại
    - Kiểm tra trạng thái revoked
    - Kiểm tra hết hạn
    - Khóa chặt theo duy nhất 1 thiết bị (bound_device_id)
    """
    if not key_str:
        return False, "Vui lòng nhập License Key!", None

    db = load_license_db()
    key_str = key_str.strip().upper()
    if key_str not in db["keys"]:
        return False, "License Key không tồn tại trong hệ thống!", None

    record = db["keys"][key_str]

    if record.get("status") == "revoked":
        return False, "License Key này đã bị Admin thu hồi hoặc vô hiệu hóa!", None

    # Kiểm tra hạn dùng
    try:
        exp_dt = datetime.fromisoformat(record["expires_at"])
        if datetime.now() > exp_dt:
            record["status"] = "expired"
            save_license_db(db)
            return False, f"License Key đã hết hạn vào ngày {exp_dt.strftime('%d/%m/%Y')}!", None
    except Exception:
        pass

    # KHÓA CHẶT THIẾT BỊ (DEVICE LOCKING)
    if key_str == "NEXUS-FREE-DEMO-2USES":
        dev_map = record.setdefault("device_usage", {})
        used = dev_map.get(device_id, 0)
        if used >= 2:
            return False, "Thiết bị này đã sử dụng hết 2 lượt dùng thử miễn phí! Vui lòng liên hệ Admin để nhận Key chính thức.", None
        token = create_session_token(key_str, device_id, user_agent)
        return True, "Kích hoạt bản dùng thử miễn phí thành công (2 lượt CPU)!", token

    bound_id = record.get("bound_device_id")
    if not bound_id:
        record["bound_device_id"] = device_id
        record["bound_ip"] = client_ip
        record["bound_user_agent"] = user_agent
        record["last_active"] = datetime.now().isoformat()
        save_license_db(db)
    else:
        if bound_id != device_id:
            return False, "Key này đã được kích hoạt trên một thiết bị khác! Mỗi key chỉ dùng cho duy nhất 1 máy.", None
        record["last_active"] = datetime.now().isoformat()
        record["bound_ip"] = client_ip
        save_license_db(db)

    token = create_session_token(key_str, device_id, user_agent)
    return True, "Kích hoạt bản quyền thành công!", token

def create_session_token(key_str, device_id, user_agent):
    exp = int(time.time()) + 86400 * 7 # 7 ngày
    ua_hash = hashlib.md5((user_agent or "").encode("utf-8")).hexdigest()[:8]
    payload = f"{key_str}|{device_id}|{ua_hash}|{exp}"
    sig = hmac.new(SERVER_HMAC_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"{payload}|{sig}"

def validate_session_token(token, current_device_id, current_ua):
    if not token or "|" not in token:
        return False, "Thiếu token phiên làm việc", None
    parts = token.split("|")
    if len(parts) != 5:
        return False, "Token không hợp lệ", None

    key_str, token_dev_id, token_ua_hash, exp_str, sig = parts
    try:
        if int(exp_str) < int(time.time()):
            return False, "Phiên đăng nhập đã hết hạn", None
    except ValueError:
        return False, "Token hết hạn không hợp lệ", None

    payload = f"{key_str}|{token_dev_id}|{token_ua_hash}|{exp_str}"
    expected_sig = hmac.new(SERVER_HMAC_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    if sig != expected_sig:
        return False, "Chữ ký bảo mật không khớp", None

    # CHỐNG CHIA SẺ COOKIE (ANTI-COOKIE TRANSFER)
    if token_dev_id != current_device_id:
        return False, "Phát hiện cookie bị sao chép sang máy khác! Phiên đăng nhập đã bị hủy.", None

    cur_ua_hash = hashlib.md5((current_ua or "").encode("utf-8")).hexdigest()[:8]
    if token_ua_hash != cur_ua_hash:
        return False, "Trình duyệt truy cập không trùng khớp với thiết bị đã đăng ký!", None

    db = load_license_db()
    if key_str not in db["keys"]:
        return False, "Key không tồn tại", None
    rec = db["keys"][key_str]
    if rec.get("status") != "active":
        return False, "Key đã bị vô hiệu hóa", None

    try:
        if datetime.now() > datetime.fromisoformat(rec["expires_at"]):
            return False, "Key đã hết hạn sử dụng", None
    except Exception:
        pass

    return True, "OK", key_str

# ==================== 4. GIAO DIỆN ADMIN DASHBOARD HTML ====================
ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nexus License Manager // Admin Portal</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css"/>
    <style>
        body { background-color: #030712; color: #f3f4f6; font-family: system-ui, -apple-system, sans-serif; }
        .admin-card { background: #0b0f19; border: 1px solid #1f293d; }
    </style>
</head>
<body class="p-4 md:p-8 min-h-screen">
    <!-- KHUNG ĐĂNG NHẬP ADMIN NẾU CHƯA ĐĂNG NHẬP -->
    <div id="adminLoginBox" class="max-w-md mx-auto mt-20 admin-card rounded-2xl p-6 space-y-4 border border-purple-500/30 shadow-2xl">
        <div class="text-center space-y-1">
            <div class="w-12 h-12 mx-auto rounded-xl bg-purple-600/20 text-purple-400 flex items-center justify-center text-xl font-bold border border-purple-500/40">
                <i class="fa-solid fa-shield-halved"></i>
            </div>
            <h2 class="text-lg font-bold text-white tracking-wide">CỔNG QUẢN TRỊ ADMIN</h2>
            <p class="text-xs text-slate-400">Quản lý bán Key, Khóa máy & Giám sát bản quyền Nexus Studio</p>
        </div>
        <div>
            <label class="text-xs text-slate-400">Mật khẩu Admin:</label>
            <input type="password" id="adminPwdInput" placeholder="Nhập mật khẩu quản trị..." class="w-full mt-1 bg-slate-950 border border-slate-700 rounded-xl p-2.5 text-sm text-white outline-none focus:border-purple-500"/>
        </div>
        <button onclick="loginAdmin()" class="w-full py-2.5 rounded-xl bg-gradient-to-r from-purple-600 to-indigo-600 hover:brightness-110 text-white text-xs font-bold transition">
            Đăng Nhập Quản Trị
        </button>
        <p class="text-[11px] text-center text-slate-500 flex items-center justify-center gap-1.5">
            <i class="fa-solid fa-lock text-[10px] text-purple-400"></i> Cổng bảo mật Quản trị viên (Chỉ người nắm giữ khóa mới có quyền truy cập)
        </p>
    </div>

    <!-- NỘI DUNG ADMIN SAU KHI ĐĂNG NHẬP -->
    <div id="adminPanel" class="hidden max-w-6xl mx-auto space-y-6">
        <div class="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-4">
            <div>
                <h1 class="text-xl font-black tracking-wide text-transparent bg-clip-text bg-gradient-to-r from-purple-400 via-pink-400 to-sky-400 flex items-center gap-2">
                    <i class="fa-solid fa-crown text-amber-400"></i> NEXUS STUDIO // LICENSE ADMIN
                </h1>
                <p class="text-xs text-slate-400 mt-0.5">Bảng điều khiển cấp phát Key, khóa thiết bị & quản lý khách hàng</p>
            </div>
            <div class="flex items-center gap-2">
                <button onclick="openCreateModal()" class="px-4 py-2 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-600 hover:brightness-110 text-white text-xs font-bold flex items-center gap-1.5 shadow-lg shadow-emerald-500/20 transition">
                    <i class="fa-solid fa-plus"></i> Tạo Key Mới
                </button>
                <a href="/" target="_blank" class="px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold flex items-center gap-1.5 transition">
                    <i class="fa-solid fa-arrow-up-right-from-square"></i> Vào Tool
                </a>
                <button onclick="logoutAdmin()" class="px-3 py-2 rounded-xl bg-rose-500/20 hover:bg-rose-500 text-rose-300 hover:text-white text-xs font-bold transition">
                    <i class="fa-solid fa-power-off"></i>
                </button>
            </div>
        </div>

        <!-- THỐNG KÊ NHANH -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div class="admin-card rounded-xl p-4">
                <div class="text-xs text-slate-400">Tổng Số Key Đã Bán</div>
                <div id="statTotal" class="text-2xl font-black text-white mt-1">0</div>
            </div>
            <div class="admin-card rounded-xl p-4">
                <div class="text-xs text-slate-400">Key Đang Hoạt Động</div>
                <div id="statActive" class="text-2xl font-black text-emerald-400 mt-1">0</div>
            </div>
            <div class="admin-card rounded-xl p-4">
                <div class="text-xs text-slate-400">Key Đã Bị Khóa/Thu Hồi</div>
                <div id="statRevoked" class="text-2xl font-black text-rose-400 mt-1">0</div>
            </div>
            <div class="admin-card rounded-xl p-4">
                <div class="text-xs text-slate-400">Key Đã Hết Hạn</div>
                <div id="statExpired" class="text-2xl font-black text-amber-400 mt-1">0</div>
            </div>
        </div>

        <!-- BẢNG DANH SÁCH KEY -->
        <div class="admin-card rounded-2xl overflow-hidden border border-slate-800">
            <div class="p-4 border-b border-slate-800 flex items-center justify-between">
                <h3 class="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center gap-2">
                    <i class="fa-solid fa-list-check text-purple-400"></i> Danh Sách Key Đã Phát Hành
                </h3>
                <button onclick="loadKeysList()" class="text-xs text-sky-400 hover:underline flex items-center gap-1">
                    <i class="fa-solid fa-rotate"></i> Làm mới
                </button>
            </div>
            <div class="overflow-x-auto">
                <table class="w-full text-left text-xs">
                    <thead class="bg-slate-950 text-slate-400 border-b border-slate-800">
                        <tr>
                            <th class="py-3 px-3">Mã License Key</th>
                            <th class="py-3 px-3">Khách Hàng</th>
                            <th class="py-3 px-3 text-center">Gói (Tier)</th>
                            <th class="py-3 px-3 text-center">Hạn Mức (Quota)</th>
                            <th class="py-3 px-3">Thời Hạn</th>
                            <th class="py-3 px-3">Khóa Máy (HWID)</th>
                            <th class="py-3 px-3">Trạng Thái</th>
                            <th class="py-3 px-3 text-center">Hành Động</th>
                        </tr>
                    </thead>
                    <tbody id="keysTableBody" class="divide-y divide-slate-800/60">
                        <tr><td colspan="8" class="py-8 text-center text-slate-500">Đang tải danh sách key...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- THẺ ĐẶC BIỆT DÀNH CHO ADMIN: ĐÓNG GÓI & DEPLOY RAILWAY 24/7 -->
        <div class="admin-card rounded-2xl p-5 border border-sky-500/30 space-y-3 bg-gradient-to-br from-sky-950/40 to-indigo-950/30">
            <div class="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h3 class="text-sm font-bold text-sky-400 flex items-center gap-2">
                        <i class="fa-solid fa-cloud-arrow-up"></i> ĐÓNG GÓI & TRIỂN KHAI LÊN RAILWAY 24/7
                    </h3>
                    <p class="text-xs text-slate-400 mt-0.5">Tạo bộ file Dockerfile, railway.json, requirements.txt, .dockerignore để đưa tool lên Railway chạy online 24/7</p>
                </div>
                <button onclick="generateRailwayFiles()" id="btnGenerateRailway" class="px-4 py-2.5 rounded-xl bg-gradient-to-r from-sky-500 to-indigo-600 hover:brightness-110 text-white text-xs font-bold flex items-center gap-2 shadow-lg shadow-sky-500/20 transition">
                    <i class="fa-solid fa-file-export"></i> Tạo Bộ File Deploy Railway
                </button>
            </div>
            <div id="railwayResultBox" class="hidden text-xs bg-slate-950/90 border border-sky-500/40 rounded-xl p-3.5 space-y-2 text-slate-300">
                <div class="text-emerald-400 font-bold flex items-center gap-1.5"><i class="fa-solid fa-circle-check"></i> <span id="railwayMsgText"></span></div>
                <div class="text-[11px] text-slate-400 leading-relaxed space-y-1">
                    <p><b>Hướng dẫn 3 bước đưa lên Railway:</b></p>
                    <ol class="list-decimal list-inside space-y-0.5 text-slate-300">
                        <li>Bộ file <code>Dockerfile</code>, <code>railway.json</code>, <code>requirements.txt</code> đã được tạo sẵn trong thư mục dự án.</li>
                        <li>Đẩy (Push / Commit) toàn bộ thư mục này lên GitHub repository của bạn.</li>
                        <li>Mở <a href="https://railway.app" target="_blank" class="text-sky-400 underline font-bold">Railway.app</a>, tạo New Project ➔ <b>Deploy from GitHub repo</b>. Railway sẽ tự động dựng web và cấp tên miền chạy 24/7!</li>
                    </ol>
                </div>
            </div>
        </div>
    </div>

    <!-- MODAL TẠO KEY MỚI -->
    <div id="createKeyModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 hidden items-center justify-center p-4">
        <div class="admin-card rounded-2xl max-w-md w-full p-5 space-y-4 border border-purple-500/40">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3">
                <h3 class="text-sm font-bold text-white flex items-center gap-2">
                    <i class="fa-solid fa-key text-emerald-400"></i> Tạo License Key Mới
                </h3>
                <button onclick="closeCreateModal()" class="text-slate-400 hover:text-white"><i class="fa-solid fa-xmark"></i></button>
            </div>
            <div class="space-y-3">
                <div>
                    <label class="text-xs text-slate-400">Tên khách hàng / Ghi chú đơn hàng:</label>
                    <input type="text" id="newCustomerInput" placeholder="VD: Anh Nam - Khách Bilibili..." class="w-full mt-1 bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-white outline-none"/>
                </div>
                <div class="grid grid-cols-2 gap-2">
                    <div>
                        <label class="text-xs text-slate-400">Phân loại Gói (Tier):</label>
                        <select id="newTierSelect" class="w-full mt-1 bg-slate-950 border border-purple-500/50 rounded-lg p-2 text-xs text-amber-300 font-bold outline-none">
                            <option value="pro" selected>👑 PRO (Mở khóa GPU + CPU)</option>
                            <option value="basic">⚡ BASIC (Chỉ chạy CPU)</option>
                        </select>
                    </div>
                    <div>
                        <label class="text-xs text-slate-400">Hạn mức Render (Quota):</label>
                        <input type="number" id="newQuotaInput" value="50" min="-1" placeholder="50 (-1 = Vô hạn)" class="w-full mt-1 bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs font-mono font-bold text-sky-400 outline-none"/>
                    </div>
                </div>
                <div>
                    <label class="text-xs text-slate-400">Gói thời hạn sử dụng:</label>
                    <select id="newDurationSelect" class="w-full mt-1 bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-emerald-300 font-bold outline-none">
                        <option value="1">1 Ngày (Dùng thử)</option>
                        <option value="3">3 Ngày</option>
                        <option value="7">7 Ngày (1 Tuần)</option>
                        <option value="30" selected>30 Ngày (1 Tháng)</option>
                        <option value="90">90 Ngày (3 Tháng)</option>
                        <option value="365">365 Ngày (1 Năm)</option>
                        <option value="9999">Vĩnh Viễn (Lifetime)</option>
                    </select>
                </div>
            </div>
            <div class="flex justify-end gap-2 pt-2">
                <button onclick="closeCreateModal()" class="px-3 py-1.5 rounded-lg bg-slate-800 text-xs text-slate-300">Hủy</button>
                <button onclick="submitCreateKey()" class="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-xs font-bold text-white">Tạo & Xuất Key Ngay</button>
            </div>
        </div>
    </div>

    <script>
        let adminToken = sessionStorage.getItem('nexus_admin_token') || '';

        window.addEventListener('DOMContentLoaded', () => {
            if (adminToken) {
                showAdminPanel();
            }
        });

        async function loginAdmin() {
            const pwd = document.getElementById('adminPwdInput').value;
            if (!pwd) return alert("Vui lòng nhập mật khẩu admin!");
            try {
                const res = await fetch('/api/admin/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password: pwd })
                });
                const data = await res.json();
                if (data.token) {
                    adminToken = data.token;
                    sessionStorage.setItem('nexus_admin_token', adminToken);
                    showAdminPanel();
                } else {
                    alert(data.error || "Mật khẩu quản trị không đúng!");
                }
            } catch (e) {
                alert("Lỗi đăng nhập: " + e);
            }
        }

        function showAdminPanel() {
            document.getElementById('adminLoginBox').classList.add('hidden');
            document.getElementById('adminPanel').classList.remove('hidden');
            loadKeysList();
        }

        function logoutAdmin() {
            adminToken = '';
            sessionStorage.removeItem('nexus_admin_token');
            location.reload();
        }

        async function loadKeysList() {
            try {
                const res = await fetch('/api/admin/keys', {
                    headers: { 'Authorization': 'Bearer ' + adminToken }
                });
                if (res.status === 401) {
                    logoutAdmin();
                    return;
                }
                const data = await res.json();
                renderKeys(data.keys || []);
            } catch (e) {
                console.error(e);
            }
        }

        function renderKeys(keys) {
            const tbody = document.getElementById('keysTableBody');
            let active = 0, revoked = 0, expired = 0;
            const now = new Date();

            keys.forEach(k => {
                const exp = new Date(k.expires_at);
                if (k.status === 'revoked') revoked++;
                else if (now > exp) expired++;
                else active++;
            });

            document.getElementById('statTotal').innerText = keys.length;
            document.getElementById('statActive').innerText = active;
            document.getElementById('statRevoked').innerText = revoked;
            document.getElementById('statExpired').innerText = expired;

            if (!keys.length) {
                tbody.innerHTML = `<tr><td colspan="6" class="py-8 text-center text-slate-500">Chưa có License Key nào được tạo.</td></tr>`;
                return;
            }

            tbody.innerHTML = keys.map(k => {
                const exp = new Date(k.expires_at);
                const isExp = now > exp;
                let statusBadge = '';
                if (k.status === 'revoked') {
                    statusBadge = '<span class="px-2 py-0.5 rounded bg-rose-500/20 text-rose-400 font-bold">Đã Khóa</span>';
                } else if (isExp) {
                    statusBadge = '<span class="px-2 py-0.5 rounded bg-amber-500/20 text-amber-400 font-bold">Hết Hạn</span>';
                } else {
                    const daysLeft = Math.ceil((exp - now) / (1000 * 3600 * 24));
                    statusBadge = `<span class="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 font-bold">Còn ${daysLeft} ngày</span>`;
                }

                const boundDev = k.bound_device_id
                    ? `<span class="text-sky-300 font-mono text-[11px]"><i class="fa-solid fa-lock mr-1"></i>${k.bound_device_id}</span>`
                    : `<span class="text-slate-500 italic">Chưa kích hoạt trên máy nào</span>`;

                const tierBadge = k.tier === 'basic'
                    ? '<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-slate-800 text-slate-300 border border-slate-700">BASIC CPU</span>'
                    : '<span class="px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-wider bg-gradient-to-r from-amber-500/20 to-purple-500/20 text-amber-300 border border-amber-500/30">PRO GPU</span>';

                const quotaBadge = k.quota_limit === -1
                    ? '<span class="mono font-bold text-emerald-400">Vô Hạn</span>'
                    : `<span class="mono font-bold text-sky-300">${k.quota_used || 0} / ${k.quota_limit} lượt</span>`;

                return `
                <tr class="hover:bg-slate-900/60 transition">
                    <td class="py-3 px-3 font-mono font-bold text-amber-300 select-all cursor-pointer" onclick="copyText('${k.key}')" title="Click để sao chép key">
                        <i class="fa-regular fa-copy mr-1 text-slate-500"></i>${k.key}
                    </td>
                    <td class="py-3 px-3 font-semibold text-white">${k.customer || 'Khách hàng'}</td>
                    <td class="py-3 px-3 text-center">${tierBadge}</td>
                    <td class="py-3 px-3 text-center">${quotaBadge}</td>
                    <td class="py-3 px-3 text-slate-400">${exp.toLocaleDateString('vi-VN')} (${k.duration_days >= 9999 ? 'Vĩnh viễn' : k.duration_days + 'N'})</td>
                    <td class="py-3 px-3">${boundDev}</td>
                    <td class="py-3 px-3">${statusBadge}</td>
                    <td class="py-3 px-3 text-center whitespace-nowrap">
                        <button onclick="addQuota('${k.key}', 10)" title="Nạp thêm 10 lượt render" class="px-2 py-1 rounded bg-indigo-500/20 hover:bg-indigo-500/40 text-indigo-300 text-[11px] font-bold mr-1 transition">
                            +10 Quota
                        </button>
                        <button onclick="resetDevice('${k.key}')" title="Mở khóa thiết bị (cho khách đổi máy mới)" class="px-2 py-1 rounded bg-sky-500/15 hover:bg-sky-500/30 text-sky-300 text-[11px] font-semibold mr-1 transition">
                            <i class="fa-solid fa-arrow-rotate-left mr-0.5"></i> Đổi máy
                        </button>
                        <button onclick="extendKey('${k.key}', 30)" title="Gia hạn thêm 30 ngày" class="px-2 py-1 rounded bg-emerald-500/15 hover:bg-emerald-500/30 text-emerald-300 text-[11px] font-semibold mr-1 transition">
                            +30N
                        </button>
                        ${k.status === 'revoked'
                            ? `<button onclick="unrevokeKey('${k.key}')" title="Mở khóa lại key này" class="px-2 py-1 rounded bg-amber-500/15 hover:bg-amber-500/30 text-amber-300 text-[11px] font-semibold mr-1 transition">Mở lại</button>`
                            : `<button onclick="revokeKey('${k.key}')" title="Khóa/Thu hồi key này lập tức" class="px-2 py-1 rounded bg-rose-500/15 hover:bg-rose-500/30 text-rose-300 text-[11px] font-semibold mr-1 transition">Khóa</button>`
                        }
                        <button onclick="deleteKey('${k.key}')" title="Xóa vĩnh viễn" class="px-2 py-1 rounded hover:bg-rose-900 text-slate-500 hover:text-white transition">
                            <i class="fa-solid fa-trash"></i>
                        </button>
                    </td>
                </tr>`;
            }).join('');
        }

        function copyText(txt) {
            navigator.clipboard.writeText(txt);
            alert("Đã sao chép License Key vào clipboard: " + txt);
        }

        function openCreateModal() {
            document.getElementById('createKeyModal').classList.remove('hidden');
            document.getElementById('createKeyModal').classList.add('flex');
        }

        function closeCreateModal() {
            document.getElementById('createKeyModal').classList.add('hidden');
            document.getElementById('createKeyModal').classList.remove('flex');
        }

        async function submitCreateKey() {
            const customer = document.getElementById('newCustomerInput').value.trim() || "Khách hàng mới";
            const days = parseInt(document.getElementById('newDurationSelect').value || 30);
            const tier = document.getElementById('newTierSelect').value || "pro";
            const quota = parseInt(document.getElementById('newQuotaInput').value || 50);
            try {
                const res = await fetch('/api/admin/create_key', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + adminToken },
                    body: JSON.stringify({ customer, duration_days: days, tier: tier, quota_limit: quota })
                });
                const data = await res.json();
                if (data.key) {
                    closeCreateModal();
                    alert(`🎉 ĐÃ TẠO KEY THÀNH CÔNG!\n\nKey: ${data.key}\nGói: ${(data.tier || 'pro').toUpperCase()}\nHạn mức: ${data.quota_limit === -1 ? 'Vô hạn' : data.quota_limit + ' lượt'}\nKhách: ${data.customer}\nHạn dùng: ${data.duration_days} ngày\n\n(Key đã được copy vào bộ nhớ tạm)`);
                    navigator.clipboard.writeText(data.key);
                    loadKeysList();
                } else {
                    alert(data.error || "Lỗi tạo key");
                }
            } catch (e) {
                alert("Lỗi: " + e);
            }
        }

        async function addQuota(k, amt) {
            try {
                const res = await fetch('/api/admin/add_quota', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + adminToken },
                    body: JSON.stringify({ key: k, amount: amt })
                });
                const d = await res.json();
                if (d.status === 'ok') {
                    alert(`Đã nạp thêm +${amt} lượt cho key ${k}! Hạn mức mới: ${d.quota_limit} lượt.`);
                    loadKeysList();
                } else {
                    alert(d.error || "Lỗi nạp quota");
                }
            } catch (e) {
                alert("Lỗi: " + e);
            }
        }

        async function revokeKey(k) {
            if (!confirm(`Bạn có chắc chắn muốn KHÓA key "${k}" không? Khách đang dùng sẽ bị ngắt kết nối ngay lập tức!`)) return;
            await fetch('/api/admin/revoke_key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + adminToken },
                body: JSON.stringify({ key: k })
            });
            loadKeysList();
        }

        async function unrevokeKey(k) {
            await fetch('/api/admin/extend_key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + adminToken },
                body: JSON.stringify({ key: k, extra_days: 0 })
            });
            loadKeysList();
        }

        async function resetDevice(k) {
            if (!confirm(`Mở khóa thiết bị cho key "${k}"? Khách sẽ có thể đăng nhập trên máy tính mới.`)) return;
            await fetch('/api/admin/reset_device', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + adminToken },
                body: JSON.stringify({ key: k })
            });
            alert("Đã mở khóa thiết bị thành công!");
            loadKeysList();
        }

        async function extendKey(k, days) {
            await fetch('/api/admin/extend_key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + adminToken },
                body: JSON.stringify({ key: k, extra_days: days })
            });
            alert(`Đã gia hạn thêm ${days} ngày cho key ${k}!`);
            loadKeysList();
        }

        async function deleteKey(k) {
            if (!confirm(`XÓA VĨNH VIỄN key "${k}" khỏi hệ thống?`)) return;
            await fetch('/api/admin/delete_key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + adminToken },
                body: JSON.stringify({ key: k })
            });
            loadKeysList();
        }

        async function generateRailwayFiles() {
            const btn = document.getElementById('btnGenerateRailway');
            btn.disabled = true;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Đang tạo file...';
            try {
                const res = await fetch('/api/admin/generate_railway', {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + adminToken }
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    document.getElementById('railwayMsgText').innerText = data.message;
                    document.getElementById('railwayResultBox').classList.remove('hidden');
                } else {
                    alert(data.error || 'Lỗi tạo file Railway');
                }
            } catch (e) {
                alert('Lỗi: ' + e);
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i class="fa-solid fa-file-export"></i> Tạo Bộ File Deploy Railway';
            }
        }
    </script>
</body>
</html>
"""

# ==================== 5. MODAL KHÓA TRANG WEB YÊU CẦU NHẬP KEY ====================
LICENSE_GATE_MODAL_HTML = """
<!-- MODAL KÍCH HOẠT BẢN QUYỀN (CHẶN NẾU CHƯA NHẬP KEY) -->
<div id="licenseGateModal" class="fixed inset-0 bg-black/90 backdrop-blur-md z-[100] hidden items-center justify-center p-4">
    <div class="studio-panel rounded-2xl max-w-md w-full p-6 space-y-4 border border-purple-500/40 shadow-2xl">
        <div class="text-center space-y-1.5">
            <div class="w-12 h-12 mx-auto rounded-2xl bg-gradient-to-tr from-purple-600 to-sky-500 text-white flex items-center justify-center text-xl font-bold shadow-lg shadow-purple-500/30">
                <i class="fa-solid fa-key"></i>
            </div>
            <h2 class="text-base font-extrabold text-white uppercase tracking-wider">Kích Hoạt Bản Quyền Nexus Studio</h2>
            <p class="text-xs text-slate-400">Vui lòng nhập License Key do Admin cấp để kích hoạt quyền sử dụng trên thiết bị này.</p>
        </div>

        <div class="bg-slate-950 p-2.5 rounded-xl border border-slate-800 space-y-1">
            <div class="text-[10px] text-slate-500 flex items-center justify-between">
                <span>MÃ PHẦN CỨNG THIẾT BỊ (DEVICE ID):</span>
                <span class="text-emerald-400 font-bold"><i class="fa-solid fa-shield-check"></i> Khóa 1 Máy</span>
            </div>
            <div id="displayDeviceId" class="font-mono text-xs text-sky-400 font-bold select-all truncate">Đang tạo mã máy...</div>
        </div>

        <div>
            <label class="text-xs text-slate-300 font-medium">Nhập License Key của bạn:</label>
            <input type="text" id="licenseKeyInput" placeholder="VD: NEXUS-XXXX-YYYY-ZZZZ" class="w-full mt-1 bg-slate-950 border border-purple-500/50 rounded-xl p-2.5 text-xs font-mono font-bold text-amber-300 uppercase tracking-widest outline-none focus:border-purple-400"/>
        </div>

        <button onclick="submitActivateLicense()" id="btnActivateLicense" class="w-full py-2.5 rounded-xl bg-gradient-to-r from-purple-600 via-indigo-600 to-sky-600 hover:brightness-110 text-white text-xs font-bold transition shadow-lg shadow-purple-500/25 flex items-center justify-center gap-1.5">
            <i class="fa-solid fa-bolt"></i> Kích Hoạt Thiết Bị Ngay
        </button>

        <div class="text-center pt-1 border-t border-slate-800 text-[11px] text-slate-500">
            <span>Chưa có Key? Vui lòng liên hệ Admin để nhận mã kích hoạt bản quyền</span>
        </div>
    </div>
</div>
"""
