"""Tiện ích dùng chung: bọc text, đo thời lượng media, kiểm tra file an toàn."""
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from .config import bin_dir, IMAGE_EXTS


# ---------------------------------------------------------------------------
# Bọc chữ (word wrap) - dùng chung cho mọi hàm vẽ card
# ---------------------------------------------------------------------------
def wrap_text_to_lines(text: str, font=None, max_width: int = 900, draw=None) -> list:
    """
    Ngắt dòng theo pixel width (nếu có font+draw) hoặc theo số ký tự.
    Trả về danh sách dòng đã ngắt.
    """
    text = text or ""
    lines = []

    if font is not None and draw is not None:
        for para in text.split("\n"):
            para = para.strip()
            if not para:
                continue
            cur = ""
            for w in para.split():
                test = f"{cur} {w}".strip()
                bbox = draw.textbbox((0, 0), test, font=font)
                if (bbox[2] - bbox[0]) <= max_width:
                    cur = test
                else:
                    if cur:
                        lines.append(cur)
                    cur = w
            if cur:
                lines.append(cur)
        return lines

    # Fallback: ngắn theo số ký tự
    chars_per_line = max(10, int(max_width / 10))
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        cur = ""
        for w in para.split():
            test = f"{cur} {w}".strip()
            if len(test) <= chars_per_line:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
    return lines


# ---------------------------------------------------------------------------
# Media helpers
# ---------------------------------------------------------------------------
def _tool(name: str) -> str:
    """Đường dẫn tới ffmpeg/ffprobe kèm theo."""
    return str(Path(bin_dir()) / name)


def ffmpeg_bin() -> str:
    return _tool("ffmpeg.exe" if os.name == "nt" else "ffmpeg")


def ffprobe_bin() -> str:
    return _tool("ffprobe.exe" if os.name == "nt" else "ffprobe")


def get_media_duration(file_path: str, default: float = 3.0) -> float:
    """
    Đo thời lượng file audio/video.

    CẢNH BÁO: `ffprobe -show_entries format=duration` trên file AAC (ADTS) do
    FFmpeg tự encode báo SAI - thường gấp đôi thời lượng thật (vd: file 3.04s
    thật nhưng báo 6.73s). Sai số này tích luỹ theo số cảnh -> giọng đọc và
    hình ảnh lệch nhau (ảnh đổi muộn hơn giọng nói).

    => Dùng cách đếm số mẫu thật (decode to null) làm chính, ffprobe chỉ là
       fallback khi decode lỗi.
    """
    # Cách 1 (chính xác): decode to null, đọc time= cuối cùng
    try:
        proc = subprocess.run(
            [ffmpeg_bin(), "-i", file_path, "-map", "0:a:0", "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120,
        )
        err = (proc.stderr or b"").decode("utf-8", errors="ignore")
        m = re.findall(r"time=(\d+):(\d+):(\d+\.?\d*)", err)
        if m:
            h, mi, s = m[-1]
            dur = int(h) * 3600 + int(mi) * 60 + float(s)
            if dur > 0.05:
                return max(0.5, dur)
    except Exception:
        pass

    # Cách 2: ffprobe stream duration
    try:
        cmd = [
            ffprobe_bin(),
            "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ]
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=30).strip()
        if out and out != "N/A":
            return max(0.5, float(out))
    except Exception:
        pass

    # Cách 3: format duration (fallback cuối)
    try:
        cmd = [
            ffprobe_bin(),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ]
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=30).strip()
        if out and out != "N/A":
            return max(0.5, float(out))
    except Exception:
        pass

    return default


def has_audio_stream(file_path: str) -> bool:
    """Kiểm tra file có luồng âm thanh hay không."""
    try:
        cmd = [
            ffprobe_bin(),
            "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ]
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=30).strip()
        return bool(out)
    except Exception:
        return False


def normalize_audio_to_aac(in_path: str, out_path: str, duration: Optional[float] = None) -> bool:
    """Chuẩn hoá mọi âm thanh về AAC 44100Hz Stereo để concat mượt mà không lỗi."""
    cmd = [ffmpeg_bin(), "-y", "-i", in_path]
    if duration is not None:
        cmd.extend(["-t", f"{duration:.3f}"])
    cmd.extend(["-vn", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k", out_path])
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=False)
        return os.path.exists(out_path)
    except Exception:
        return False


def make_silence_aac(out_path: str, duration: float) -> bool:
    """Tạo file âm thanh im lặng chuẩn AAC 44100Hz Stereo."""
    cmd = [
        ffmpeg_bin(), "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", f"{duration:.3f}", "-c:a", "aac", "-b:a", "192k", out_path,
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False)
        return os.path.exists(out_path)
    except Exception:
        return False


def pad_audio(src: str, dst: str, pad_seconds: float = 0.0) -> str:
    """
    Nối thêm khoảng im lặng vào CUỐI file audio.

    Bắt buộc cho việc khớp hình ảnh với giọng đọc: timeline video dùng
    (duration_audio + PAD) cho mỗi cảnh, nên file audio cũng phải dài
    đúng bấy nhiêu. Nếu không, mỗi cảnh lệch `pad_seconds` và tích luỹ
    dần theo số cảnh (8 cảnh x 0.15s = 1.2s lệch).
    """
    if pad_seconds <= 0.001:
        try:
            if os.path.abspath(src) != os.path.abspath(dst):
                with open(src, "rb") as s, open(dst, "wb") as d:
                    d.write(s.read())
            else:
                return src
            return dst
        except OSError:
            return src

    cmd = [
        ffmpeg_bin(), "-y", "-i", src,
        "-af", f"apad=pad_dur={pad_seconds:.3f}",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
        dst,
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False)
        if os.path.exists(dst) and os.path.getsize(dst) > 512:
            return dst
    except Exception:
        pass
    # Fallback: copy file goc
    try:
        with open(src, "rb") as s, open(dst, "wb") as d:
            d.write(s.read())
    except OSError:
        return src
    return dst


# ---------------------------------------------------------------------------
# Kiểm tra file an toàn
# ---------------------------------------------------------------------------
def sniff_mime(head_bytes: bytes) -> Optional[str]:
    """Đoán loại file từ magic bytes. Trả về None nếu không nhận diện được."""
    if not head_bytes:
        return None
    for sig, kind in (
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"\xff\xd8\xff", "jpg"),
        (b"GIF8", "gif"),
        (b"\x1a\x45\xdf\xa3", "webm"),
        (b"ID3", "mp3"),
        (b"OggS", "ogg"),
        (b"fLaC", "flac"),
    ):
        if head_bytes.startswith(sig):
            return kind
    if head_bytes[:4] == b"RIFF":
        return "webp" if head_bytes[8:12] == b"WEBP" else "wav"
    if head_bytes[4:8] == b"ftyp":
        return "mp4"
    if head_bytes[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"):
        return "mp4"
    return None


def is_image_file(file_path: str) -> bool:
    """Kiểm tra file có phải ảnh (dựa trên extension)."""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in IMAGE_EXTS


# ---------------------------------------------------------------------------
# Làm sạch chuỗi
# ---------------------------------------------------------------------------
_EMOJI_RE = re.compile(
    "[" "\U0001F000-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F1E6-\U0001F1FF" "]+",
    flags=re.UNICODE,
)


def strip_emoji(text: str) -> str:
    """Loại bỏ emoji (TTS đọc emoji sẽ bị khựng hoặc đọc sai)."""
    return _EMOJI_RE.sub(" ", text or "")


def sanitize_filename(name: str, fallback: str = "file") -> str:
    """Chuẩn hoá tên file an toàn, chống path traversal."""
    base = os.path.basename(name or "").strip()
    base = re.sub(r"[^\w\-_.]", "_", base, flags=re.ASCII).strip("._")
    return base or fallback


# ---------------------------------------------------------------------------
# Scene helpers
# ---------------------------------------------------------------------------
SCENE_FIELDS = (
    "author", "time_ago", "display_text", "tts_text",
    "reply_author", "reply_display_text", "reply_tts_text",
    "avatar_url", "likes", "comments", "reposts", "shares",
    "author_liked", "meme_file",
)


def scene_to_dict(scene) -> dict:
    """
    Chuẩn hoá 1 scene về dict.
    Chấp nhận cả Pydantic model (có .model_dump) lẫn dict sẵn.
    """
    if isinstance(scene, dict):
        base = dict(scene)
    elif hasattr(scene, "model_dump"):
        base = scene.model_dump()
    elif hasattr(scene, "dict"):
        base = scene.dict()
    else:
        base = {f: getattr(scene, f, None) for f in SCENE_FIELDS}

    out = {f: base.get(f) for f in SCENE_FIELDS}
    out.setdefault("time_ago", "1 ngày")
    out.setdefault("author_liked", True)
    if out.get("author_liked") is None:
        out["author_liked"] = True
    return out
