"""Cấu hình đường dẫn & hằng số dùng chung cho toàn bộ ứng dụng."""
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Thư mục gốc: hỗ trợ cả chạy dev lẫn chạy đóng gói (PyInstaller)
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys._MEIPASS)
    WORK_ROOT = Path(os.getcwd())
else:
    APP_ROOT = Path(__file__).resolve().parent.parent
    WORK_ROOT = Path(__file__).resolve().parent.parent


# Thư mục người dùng làm việc (ghi/đọc runtime)
OUTPUTS_DIR = WORK_ROOT / "outputs"
TEMP_DIR = WORK_ROOT / "temp"
MEMES_DIR = WORK_ROOT / "memes"
BGM_DIR = WORK_ROOT / "bgm"
BACKGROUNDS_DIR = WORK_ROOT / "backgrounds"
TTS_CACHE_DIR = TEMP_DIR / "tts_cache"
JOBS_DIR = TEMP_DIR / "jobs"

# Asset (icon Threads, ...) - dung chung cho dev va ban dong goi
ASSETS_DIR = APP_ROOT / "assets"

ALL_DIRS = [OUTPUTS_DIR, TEMP_DIR, MEMES_DIR, BGM_DIR, BACKGROUNDS_DIR, TTS_CACHE_DIR, JOBS_DIR]


def ensure_dirs() -> None:
    """Tạo toàn bộ thư mục làm việc nếu chưa có."""
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


def bin_dir() -> Path:
    """
    Thư mục chứa ffmpeg/ffprobe.

    CHỈ trả về thư mục thực sự TỒN TẠI và CÓ chứa file ffmpeg.
    Nếu không, trả None -> `utils._tool()` sẽ fallback sang `shutil.which()`
    (tìm ffmpeg trong PATH) thay vì trả về đường dẫn không tồn tại.
    """
    for cand in (APP_ROOT / "bin", APP_ROOT, WORK_ROOT / "bin"):
        if not cand.exists():
            continue
        try:
            names = os.listdir(cand)
        except OSError:
            continue
        # Thư mục phải thực sự chứa ffmpeg mới dùng được
        if any(n.lower().startswith("ffmpeg") for n in names):
            return cand
    return None


def ensure_ffmpeg_in_path() -> None:
    """Đảm bảo ffmpeg & ffprobe nằm trong PATH (quan trọng khi đóng gói EXE)."""
    b = bin_dir()
    if b and str(b) not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def ensure_playwright_browsers() -> None:
    """Trỏ Playwright tới thư mục browser trong LOCALAPPDATA nếu có."""
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        ms_playwright = os.path.join(local_app_data, "ms-playwright")
        if os.path.exists(ms_playwright):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = ms_playwright


# ---------------------------------------------------------------------------
# Giới hạn & hằng số nghiệp vụ
# ---------------------------------------------------------------------------
MAX_UPLOAD_BYTES = 200 * 1024 * 1024       # 200 MB mỗi file upload
MAX_SCENES = 40                            # số cảnh tối đa trong 1 video
MAX_DISPLAY_CHARS = 2000                   # độ dài tối đa của 1 câu
MAX_RENDER_SECONDS = 600                   # video tối đa 10 phút
MAX_IMAGES_PER_SCENE = 10                  # số ảnh tối đa cho 1 bài viết (Phase 1.2)

# ---------------------------------------------------------------------------
# Vùng an toàn nội dung (theo khung 1080x1920)
# ---------------------------------------------------------------------------
# TikTok / Facebook Reels phủ UI lên các mép video:
#   - cột nút bên phải (like / comment / share / âm thanh): ~170px
#   - caption + tên + thanh điều hướng dưới:               ~380px
#   - thanh tiến trình + nút tìm kiếm trên:               ~150px
# Nội dung (card chữ) đặt ngoài vùng này sẽ bị UI che -> người xem không
# đọc được chữ. Các hằng số dưới tính cho 1080x1920, khi render ở độ phân
# giải khác sẽ được nhân theo tỉ lệ (xem cards.scale_for).
SAFE_ZONE_LEFT = 40
SAFE_ZONE_RIGHT = 170
SAFE_ZONE_TOP = 150
SAFE_ZONE_BOTTOM = 380

RESOLUTIONS = {
    "1080x1920": (1080, 1920),
    "720x1280": (720, 1280),
}

AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".flv")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".jfif", ".bmp")
GIF_EXTS = (".gif",)
MEME_EXTS = VIDEO_EXTS + IMAGE_EXTS + GIF_EXTS
BG_EXTS = (".mp4", ".mov", ".webm")

# Magic bytes để kiểm tra file thật sự là media (chống upload file độc)
_MAGIC_SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpg",
    b"GIF8": "gif",
    b"RIFF": "webp_or_wav",
    b"\x1a\x45\xdf\xa3": "webm",
    b"\x00\x00\x01\xba": "mp4",
    b"\x00\x00\x01\xb3": "mp4",
    b"ftyp": "mp4",
    b"ID3": "mp3",
    b"OggS": "ogg",
    b"fLaC": "flac",
    b"RIFF": "wav",
}
