"""
MODULE XỬ LÝ ẢNH BÀI VIẾT (Phase 1.2).

Trách nhiệm:
  - Chuẩn hoá & chọn URL ảnh tốt nhất từ `srcset` của Threads.
  - Kiểm tra an toàn URL (allowlist host + chặn IP nội bộ -> chống SSRF).
  - Tải ảnh về file cục bộ với:
      + timeout
      + giới hạn dung lượng
      + kiểm tra MIME (header) VÀ magic bytes (nội dung thật)
      + chỉ chấp nhận định dạng ảnh được hỗ trợ
  - Dọn file tạm trong `finally`.

Nguyên tắc quan trọng:
  - MỘT ảnh lỗi KHÔNG được làm hỏng cả video -> luôn trả về danh sách
    ảnh hợp lệ, bỏ qua ảnh lỗi.
  - KHÔNG đoán/bịa URL ảnh. Chỉ dùng URL lấy thật từ DOM.
  - KHÔNG truyền URL mạng vào FFmpeg -> luôn dùng file cục bộ.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from io import BytesIO
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import requests
from PIL import Image

from .config import TEMP_DIR

# ---------------------------------------------------------------------------
# Hằng số giới hạn
# ---------------------------------------------------------------------------
IMAGE_TIMEOUT_SECONDS = 15          # timeout tải 1 ảnh
IMAGE_MAX_BYTES = 12 * 1024 * 1024  # tối đa 12MB / ảnh
IMAGE_MAX_PIXELS = 40_000_000       # chống "zip bomb" (giải nén bomb)

# Chỉ chấp nhận các định dạng ảnh này (đã kiểm tra magic bytes)
ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP")

# MIME được phép (kiểm tra thêm cả header lẫn nội dung thật)
ALLOWED_MIME = (
    "image/jpeg", "image/jpg", "image/pjpeg",
    "image/png", "image/webp",
)

# Allowlist host: chỉ CDN chính thức của Meta (Threads/Instagram/Facebook).
# Tránh SSRF và tránh kéo ảnh từ nguồn lạ.
ALLOWED_HOST_SUFFIXES = (
    ".fbcdn.net",
    ".cdninstagram.com",
    ".instagram.com",
    ".threads.net",
    ".threads.com",
    ".facebook.com",
    ".fb.com",
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.threads.net/",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


# ---------------------------------------------------------------------------
# Kiểm tra URL an toàn
# ---------------------------------------------------------------------------
def is_allowed_image_url(url: Optional[str]) -> bool:
    """
    True nếu URL là URL ảnh HTTP(S) từ host được allowlist.

    Chặn:
      - scheme không phải http/https (file://, data:, javascript: ...)
      - host không nằm trong allowlist
      - host là IP nội bộ / loopback (SSRF)
    """
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    if not u:
        return False
    try:
        p = urlparse(u)
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    if not host:
        return False
    if not any(host.endswith(sfx) or host == sfx.lstrip(".") for sfx in ALLOWED_HOST_SUFFIXES):
        return False
    # Chặn IP nội bộ (host là dạng IP literal)
    try:
        ip = ipaddress.ip_address(host)
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    except ValueError:
        pass  # không phải IP literal -> OK
    return True


# ---------------------------------------------------------------------------
# Chọn URL tốt nhất từ srcset
# ---------------------------------------------------------------------------
def pick_best_srcset(srcset: Optional[str]) -> str:
    """
    Chọn URL có độ phân giải LỚN NHẤT từ thuộc tính `srcset`.

    Threads đưa ra nhiều biến thể (240w -> 2160w). Ta muốn bản lớn nhất
    để render 9:16 không bị vỡ nét. Nếu không có `w` -> so sánh độ dài URL.

    Trả về "" nếu không có ứng viên hợp lệ.
    """
    if not srcset:
        return ""
    best_url = ""
    best_w = -1
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        url = bits[0]
        width = 0
        if len(bits) > 1 and bits[-1].endswith("w"):
            try:
                width = int(bits[-1][:-1])
            except ValueError:
                width = 0
        if not is_allowed_image_url(url):
            continue
        if width > best_w or (width == best_w and len(url) > len(best_url)):
            best_w = width
            best_url = url
    return best_url


def dedupe_images(urls: Sequence[str]) -> List[str]:
    """
    Loại trùng URL nhưng GIỮ NGUYÊN THỨ TỰ (yêu cầu: giữ thứ tự ảnh gốc).
    """
    seen = set()
    out: List[str] = []
    for u in urls or []:
        if not u:
            continue
        key = u.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out


# ---------------------------------------------------------------------------
# Kiểm tra nội dung ảnh
# ---------------------------------------------------------------------------
def sniff_image_format(data: bytes) -> Optional[str]:
    """
    Xác định định dạng ảnh từ MAGIC BYTES (không tin header từ server).

    Trả về "JPEG" / "PNG" / "WEBP" hoặc None nếu không nhận dạng được.
    """
    if not data or len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def _decode_image(data: bytes) -> Optional[Image.Image]:
    """Mở & giải mã ảnh; trả None nếu lỗi / quá nhiều pixel."""
    try:
        im = Image.open(BytesIO(data))
        if im.format not in ALLOWED_FORMATS:
            return None
        if im.width * im.height > IMAGE_MAX_PIXELS:
            return None
        im.load()
        return im
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tải ảnh
# ---------------------------------------------------------------------------
def download_image(
    url: str,
    dest_path: str,
    timeout: int = IMAGE_TIMEOUT_SECONDS,
    max_bytes: int = IMAGE_MAX_BYTES,
) -> Tuple[bool, str]:
    """
    Tải 1 ảnh về `dest_path`.

    Trả về (ok, message).
    - ok=False + message mô tả lý do (timeout / 404 / quá lớn / sai định dạng ...)
    - KHÔNG ném exception -> ảnh lỗi chỉ bị bỏ qua, không làm hỏng video.
    """
    if not is_allowed_image_url(url):
        return False, "URL ảnh không được phép (host/scheme không hợp lệ)"

    tmp_path = dest_path + ".part"
    try:
        with requests.get(url, timeout=timeout, headers=_HEADERS, stream=True) as r:
            status = getattr(r, "status_code", 0)
            if status != 200:
                return False, f"HTTP {status}"
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype and ctype not in ALLOWED_MIME:
                return False, f"MIME không được phép: {ctype}"

            # Kiểm tra Content-Length (nếu có) trước khi tải hết
            clen = r.headers.get("Content-Length")
            if clen:
                try:
                    if int(clen) > max_bytes:
                        return False, f"Ảnh quá lớn ({int(clen)} bytes)"
                except ValueError:
                    pass

            chunks = []
            total = 0
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    return False, f"Ảnh quá lớn (> {max_bytes} bytes)"
                chunks.append(chunk)
            data = b"".join(chunks)

        if not data:
            return False, "Ảnh rỗng"
        if total > max_bytes:
            return False, f"Ảnh quá lớn (> {max_bytes} bytes)"

        fmt = sniff_image_format(data)
        if fmt is None:
            return False, "Không nhận dạng được định dạng ảnh (magic bytes)"
        if fmt not in ALLOWED_FORMATS:
            return False, f"Định dạng ảnh không được hỗ trợ: {fmt}"

        # Giải mã thật để chắc chắn file không hỏng
        im = _decode_image(data)
        if im is None:
            return False, "Ảnh không giải mã được / vượt giới hạn pixel"

        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        # Lưu dạng PNG đồng nhất (WebP/JPEG -> PNG) -> FFmpeg/Pillow ổn định
        im.convert("RGB").save(tmp_path, "PNG")
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError:
                pass
        os.replace(tmp_path, dest_path)
        return True, "OK"
    except requests.exceptions.Timeout:
        return False, "Timeout khi tải ảnh"
    except requests.exceptions.RequestException as e:
        return False, f"Lỗi mạng: {type(e).__name__}"
    except Exception as e:
        return False, f"Lỗi tải ảnh: {type(e).__name__}: {e}"
    finally:
        # Dọn file tạm dù thành công hay thất bại
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def prepare_images(
    urls: Sequence[str],
    prefix: str,
    dest_dir: Optional[str] = None,
    max_images: int = 10,
    timeout: int = IMAGE_TIMEOUT_SECONDS,
    max_bytes: int = IMAGE_MAX_BYTES,
) -> List[str]:
    """
    Tải nhiều ảnh, GIỮ THỨ TỰ gốc, bỏ qua ảnh lỗi.

    Trả về list đường dẫn file cục bộ (đã tải thành công).
    """
    out: List[str] = []
    ddir = dest_dir or str(TEMP_DIR / "images")
    os.makedirs(ddir, exist_ok=True)
    safe = dedupe_images([u for u in (urls or []) if u])[:max_images]
    for i, url in enumerate(safe):
        dest = os.path.join(ddir, f"{prefix}_img{i}.png")
        ok, _msg = download_image(url, dest, timeout=timeout, max_bytes=max_bytes)
        if ok and os.path.isfile(dest):
            out.append(dest)
    return out


async def prepare_images_async(
    urls: Sequence[str],
    prefix: str,
    dest_dir: Optional[str] = None,
    max_images: int = 10,
    timeout: int = IMAGE_TIMEOUT_SECONDS,
    max_bytes: int = IMAGE_MAX_BYTES,
) -> List[str]:
    """
    Bản async của `prepare_images()` - tải SONG SONG để không bị chậm.

    Giữ thứ tự gốc: kết quả được sắp xếp lại theo index của URL.
    """
    import asyncio

    ddir = dest_dir or str(TEMP_DIR / "images")
    os.makedirs(ddir, exist_ok=True)
    safe = dedupe_images([u for u in (urls or []) if u])[:max_images]
    if not safe:
        return []

    async def _one(i: int, url: str):
        dest = os.path.join(ddir, f"{prefix}_img{i}.png")
        ok, _msg = await asyncio.to_thread(
            download_image, url, dest, timeout, max_bytes,
        )
        return (i, dest if (ok and os.path.isfile(dest)) else None)

    results = await asyncio.gather(
        *[_one(i, u) for i, u in enumerate(safe)], return_exceptions=True,
    )
    out: List[str] = []
    for res in results:
        if isinstance(res, BaseException):
            continue
        _i, path = res
        if path:
            out.append(path)
    return out


def load_local_image(path: Optional[str]) -> Optional[Image.Image]:
    """Mở ảnh cục bộ (RGBA). Trả None nếu lỗi."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with Image.open(path) as im:
            return im.convert("RGBA")
    except Exception:
        return None


def resolve_socket_host(url: str) -> bool:
    """
    Kiểm tra host có resolve ra IP nội bộ không (bảo vệ SSRF sâu hơn).
    Trả False nếu host không resolve được (coi như không an toàn).
    """
    try:
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return False
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return False
        return True
    except Exception:
        return False
