"""
Launcher của Ghiền Đọc Comment Studio.

Nhiệm vụ:
  - Chuẩn bị môi trường (PATH cho ffmpeg, biến Playwright, thư mục làm việc)
  - Sao chép tài nguyên mặc định nếu người dùng chưa có
  - Mở trình duyệt tự động và chạy server
"""
import os
import shutil
import sys
import threading
import time
import webbrowser

# ---------------------------------------------------------------------------
# Xác định thư mục bundle (hỗ trợ chạy dev + chạy đóng gói EXE)
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    bundle_dir = sys._MEIPASS
    work_dir = os.getcwd()
else:
    bundle_dir = os.path.abspath(os.path.dirname(__file__))
    work_dir = bundle_dir

sys.path.insert(0, bundle_dir)

# ---------------------------------------------------------------------------
# Chuẩn bị môi trường
# ---------------------------------------------------------------------------
if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    ms_playwright = os.path.join(local_app_data, "ms-playwright")
    if os.path.exists(ms_playwright):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = ms_playwright

# Đưa ffmpeg/ffprobe vào PATH
for b in (os.path.join(bundle_dir, "bin"), bundle_dir, os.path.abspath("bin")):
    if os.path.exists(b) and b not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = b + os.pathsep + os.environ.get("PATH", "")

# Tạo thư mục làm việc
for folder in ("outputs", "temp", "memes", "bgm", "backgrounds"):
    os.makedirs(os.path.join(work_dir, folder), exist_ok=True)


# ---------------------------------------------------------------------------
# Sao chép tài nguyên mặc định
# ---------------------------------------------------------------------------
def extract_defaults():
    """Copy asset mặc định sang thư mục làm việc nếu người dùng chưa có."""
    try:
        idx_src = os.path.join(bundle_dir, "index.html")
        if os.path.exists(idx_src) and not os.path.exists(os.path.join(work_dir, "index.html")):
            shutil.copyfile(idx_src, os.path.join(work_dir, "index.html"))

    # Video nen: lay tu thu muc `backgrounds/` (da duoc Git LFS luu chung).
    # File `background.mp4` o goc da duoc bo - trung hash voi
    # backgrounds/gameplay_60s_part1.mp4.

        bgm_dir = os.path.join(bundle_dir, "bgm")
        if os.path.isdir(bgm_dir):
            dest_dir = os.path.join(work_dir, "bgm")
            for f in os.listdir(bgm_dir):
                s = os.path.join(bgm_dir, f)
                d = os.path.join(dest_dir, f)
                if os.path.isfile(s) and not os.path.exists(d):
                    shutil.copyfile(s, d)

        bg_dir = os.path.join(bundle_dir, "backgrounds")
        if os.path.isdir(bg_dir):
            dest_dir = os.path.join(work_dir, "backgrounds")
            for f in os.listdir(bg_dir):
                s = os.path.join(bg_dir, f)
                d = os.path.join(dest_dir, f)
                if os.path.isfile(s) and not os.path.exists(d):
                    shutil.copyfile(s, d)

        # Icon Threads (assets/icons) - dung chung cho dev va ban dong goi
        assets_src = os.path.join(bundle_dir, "assets")
        if os.path.isdir(assets_src):
            shutil.copytree(assets_src, os.path.join(work_dir, "assets"),
                            dirs_exist_ok=True)
    except Exception as e:
        print(f"[Warning] Khong the sao chep asset mac dinh: {e}")


extract_defaults()

# ---------------------------------------------------------------------------
# Chạy server
# ---------------------------------------------------------------------------
from server import app  # noqa: E402
import uvicorn  # noqa: E402

HOST = "127.0.0.1"
PORT = 8000


def open_browser():
    """Mở trình duyệt sau 1.5 giây (chờ server khởi động)."""
    time.sleep(1.5)
    url = f"http://{HOST}:{PORT}"
    print(f"\n>>> Dang tu dong mo trinh duyet tai: {url} ...")
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"[Warning] Khong the tu dong mo trinh duyet: {e}")


def print_banner():
    print(f"""
======================================================================
    GHIEN DOC COMMENT - AI VIDEO MAKER v2.1
    FastAPI + Direct FFmpeg Render Engine (Ultra Fast ~5s)
======================================================================
 * Server dang chay tai dia chi : http://{HOST}:{PORT}
 * Thu muc video xuat ra       : outputs/
 * Thu muc Meme                : memes/
 * Thu muc Nhac nen            : bgm/
 * Video nen                   : backgrounds/
 * De thoat ung dung           : Nhan [Ctrl + C] hoac dong cua so nay
======================================================================
""")


if __name__ == "__main__":
    import asyncio

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    print_banner()
    threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
