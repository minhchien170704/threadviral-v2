"""
Kiểm tra file upload an toàn.

Bản cũ chỉ dựa vào extension -> dễ đẩy file .exe/.html độc hại vào thư mục public.
Bản này kiểm tra thêm:
  - Kích thước tối đa
  - Magic bytes (sniff) khớp với extension
  - Tên file chuẩn hoá, chống path traversal
"""
import os
import uuid
from typing import Optional, Tuple

from .config import (
    AUDIO_EXTS, BG_EXTS, IMAGE_EXTS, MAX_UPLOAD_BYTES, MEME_EXTS, VIDEO_EXTS,
)
from .utils import sanitize_filename, sniff_mime

# Ánh xạ extension -> các mime được chấp nhận
_EXT_TO_MIME = {
    ".png": {"png"},
    ".jpg": {"jpg"}, ".jpeg": {"jpg"},
    ".gif": {"gif"},
    ".webp": {"webp"},
    ".bmp": {"png", "jpg"},          # BMP hiếm, chấp nhận nhẹ
    ".jfif": {"jpg"},
    ".mp3": {"mp3", "wav"},
    ".wav": {"wav", "webp"},
    ".m4a": {"mp4", "mp3"},
    ".aac": {"mp4", "mp3"},
    ".ogg": {"ogg"},
    ".flac": {"flac"},
    ".mp4": {"mp4", "webm"},
    ".mov": {"mp4"},
    ".webm": {"webm"},
    ".mkv": {"mp4", "webm"},
    ".avi": {"mp4"},
    ".flv": {"mp4"},
}


class UploadError(ValueError):
    """Lỗi khi file upload không hợp lệ."""


def validate_upload(filename: str, head: bytes, size: int, allowed_exts: Tuple[str, ...]) -> str:
    """
    Kiểm tra file upload hợp lệ.
    Trả về extension hợp lệ (kèm dấu chấm). Raise UploadError nếu không hợp lệ.
    """
    if not filename:
        raise UploadError("Thiếu tên file!")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed_exts:
        raise UploadError(
            f"Định dạng {ext or '(không có đuôi)'} không được hỗ trợ! "
            f"Chỉ chấp nhận: {', '.join(allowed_exts)}"
        )

    if size <= 0:
        raise UploadError("File rỗng hoặc không hợp lệ!")

    if size > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES / (1024 * 1024)
        raise UploadError(f"File quá lớn! Tối đa {mb:.0f} MB.")

    # Kiểm tra magic bytes
    detected = sniff_mime(head)
    if detected is None:
        raise UploadError("Không nhận diện được định dạng file (file có thể hỏng hoặc độc hại).")

    allowed_mimes = _EXT_TO_MIME.get(ext)
    if allowed_mimes and detected not in allowed_mimes:
        raise UploadError(
            f"Nội dung file không khớp với đuôi {ext} "
            f"(phát hiện: {detected}). File có thể không phải media hợp lệ."
        )

    return ext


def build_dest(folder: str, filename: str, ext: str, unique: bool = True) -> str:
    """Tạo đường dẫn đích an toàn trong folder."""
    stem = sanitize_filename(os.path.splitext(filename)[0], fallback="file")
    if unique:
        stem = f"{stem}_{uuid.uuid4().hex[:6]}"
    return os.path.join(folder, f"{stem}{ext}")
