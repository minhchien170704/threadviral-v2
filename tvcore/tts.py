"""
Module TTS (Edge-TTS) + chuẩn hoá văn bản viết tắt tiếng Việt.

Tách riêng khỏi server.py, bổ sung:
  - Cache đĩa theo hash(text+voice+speed) để không gọi lại API cho nội dung cũ.
  - Bóc emoji, làm sạch ký tự lạ trước khi gửi TTS.
"""
import asyncio
import hashlib
import os
import random
import re
import time
from typing import List, Optional

import edge_tts

from .config import TTS_CACHE_DIR
from .utils import make_silence_aac, normalize_audio_to_aac, strip_emoji


# ---------------------------------------------------------------------------
# Giới hạn số request TTS đồng thời
# ---------------------------------------------------------------------------
# edge-tts (dịch vụ Speech của Microsoft) trả về "NoAudioReceived" (không
# phải lỗi file) khi bị throttle. Tạo hàng chục câu song song sẽ khiến
# nhiều câu hỏng -> giọng đọc "loạn", có đoạn im lặng giữa video.
#
# => Chạy TTS TUẦN TỰ với khoảng nghỉ tối thiểu giữa 2 request, kèm retry
#    lũy thừa và đổi voice dự phòng khi vẫn thất bại.
TTS_CONCURRENCY = 1
TTS_MIN_GAP = 1.1          # giây nghỉ tối thiểu giữa 2 request TTS
TTS_MAX_RETRIES = 10
TTS_RETRY_BASE_DELAY = 2.0  # backoff lũy thừa: 2s, 4s, 6s...
TTS_RETRY_MAX_DELAY = 30.0
TTS_RETRY_JITTER = 1.2     # nhiễu ngẫu nhiên để không đâm vào nhịp throttle

_tts_semaphore: Optional[asyncio.Semaphore] = None
_tts_semaphore_loop = None
_tts_last_call: Optional[float] = None
_tts_gap_lock: Optional[asyncio.Lock] = None
_tts_gap_lock_loop = None
# Đếm số lần thất bại liên tiếp -> tăng thêm khoảng nghỉ giữa các request.
# edge-tts throttle theo BURST (chặn cả một loạt request gần nhau), nên cần
# biết "đang trong vùng bị throttle" để giãn cách request ra.
_tts_burst_count = 0
_tts_burst_lock: Optional[asyncio.Lock] = None
_tts_burst_lock_loop = None

# Voice du phong: khi edge-tts bi rate-limit voi voice chinh (NoAudioReceived),
# doi sang voice khac de cau do van co giong doc.
# Chi dung 2 giong co san cua Microsoft (kiem tra qua edge_tts.list_voices).
FALLBACK_VOICES = [
    "vi-VN-HoaiMyNeural",
    "vi-VN-NamMinhNeural",
]

# Danh sach giong doc duoc phep su dung trong tool.
AVAILABLE_VOICES = [
    {
        "id": "vi-VN-HoaiMyNeural",
        "name": "Hoài My (Nữ)",
        "gender": "female",
        "description": "Giọng nữ nhẹ nhàng, phù hợp kể chuyện",
    },
    {
        "id": "vi-VN-NamMinhNeural",
        "name": "Nam Minh (Nam)",
        "gender": "male",
        "description": "Giọng nam trầm ấm, phù hợp hài hước",
    },
]


def _get_semaphore() -> asyncio.Semaphore:
    """
    Semaphore dùng chung cho toàn tiến trình.

    Phải tạo lại khi event loop đổi (asyncio.Semaphore gắn với loop tại thời
    điểm tạo -> dùng lại ở loop khác sẽ raise).
    """
    global _tts_semaphore, _tts_semaphore_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _tts_semaphore is None or _tts_semaphore_loop is not loop:
        _tts_semaphore = asyncio.Semaphore(TTS_CONCURRENCY)
        _tts_semaphore_loop = loop
    return _tts_semaphore


async def _wait_tts_turn() -> None:
    """
    Chờ tới lượt gọi TTS: bảo đảm giữa 2 request liên tiếp có ít nhất
    TTS_MIN_GAP giây nghỉ, để edge-tts không throttle.

    Nếu đang trong "vùng bị throttle" (các lần gọi gần đây đều fail),
    khoảng nghỉ được nhân lên để thoát khỏi burst block.
    """
    global _tts_last_call, _tts_gap_lock, _tts_gap_lock_loop
    global _tts_burst_count, _tts_burst_lock, _tts_burst_lock_loop

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if _tts_gap_lock is None or _tts_gap_lock_loop is not loop:
        _tts_gap_lock = asyncio.Lock()
        _tts_gap_lock_loop = loop
    if _tts_burst_lock is None or _tts_burst_lock_loop is not loop:
        _tts_burst_lock = asyncio.Lock()
        _tts_burst_lock_loop = loop

    async with _tts_burst_lock:
        burst = _tts_burst_count

    async with _tts_gap_lock:
        if _tts_last_call is not None:
            elapsed = time.monotonic() - _tts_last_call
            # Khoảng nghỉ tăng dần theo số lần fail liên tiếp
            gap = TTS_MIN_GAP * (1.0 + 0.8 * burst)
            wait = gap - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
        _tts_last_call = time.monotonic()


async def _mark_tts_result(ok: bool) -> None:
    """Ghi nhận kết quả 1 lần gọi TTS để điều chỉnh khoảng nghỉ cho request sau."""
    global _tts_burst_count, _tts_burst_lock, _tts_burst_lock_loop

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _tts_burst_lock is None or _tts_burst_lock_loop is not loop:
        _tts_burst_lock = asyncio.Lock()
        _tts_burst_lock_loop = loop

    async with _tts_burst_lock:
        if ok:
            _tts_burst_count = max(0, _tts_burst_count - 1)
        else:
            _tts_burst_count += 1


# ---------------------------------------------------------------------------
# Từ điển viết tắt -> lời đọc tự nhiên
# ---------------------------------------------------------------------------
# Thứ tự quan trọng: cụm dài phải được thay TRƯỚC từ đơn lẻ.
SLANG_TTS_MAP = [
    (r"\bvãi cả l\b", "vãi cả lờ"),
    (r"\bvãi l\b", "vãi lờ"),
    (r"\bvcl\b", "vãi cả lờ"),
    (r"\bvl\b", "vãi lờ"),
    (r"\bvđ\b", "vãi đạn"),
    (r"\bhãm lồn\b", "hãm lờ"),
    (r"\bhãm l\b", "hãm lờ"),
    (r"\blồn\b", "lờ"),
    (r"\bđéo\b", "éo"),
    (r"\bđm\b", "đệt mẹ"),
    (r"\bđme\b", "đệt mẹ"),
    (r"\bđịt mẹ\b", "đệt mẹ"),
    (r"\bcứt\b", "cức"),
    (r"\bchết\b", "chớt"),
    (r"\btrc\b", "trước"),
    (r"\bngta\b", "người ta"),
    (r"\bnta\b", "người ta"),
    (r"\bvk\b", "vợ"),
    (r"\bck\b", "chồng"),
    (r"\bnv\b", "như vậy"),
    (r"\bcx\b", "cũng"),
    (r"\bko\b", "không"),
    (r"\bk\b", "không"),
    (r"\bđc\b", "được"),
    (r"\bdc\b", "được"),
    (r"\bmn\b", "mọi người"),
    (r"\bmng\b", "mọi người"),
    (r"\bnyc\b", "người yêu cũ"),
    (r"\bny\b", "người yêu"),
    (r"\bbn\b", "bạn"),
    (r"\bb\b", "bạn"),
    (r"\blp\b", "lớp"),
    (r"\bmít\b", "méc"),
    (r"\btn\b", "tin nhắn"),
    (r"\brep\b", "rép"),
    (r"\bmmc\b", "mờ mờ cờ"),
    (r"\bmí thg\b", "mấy thằng"),
    (r"\bthg\b", "thằng"),
    (r"\bnma\b", "nhưng mà"),
    (r"\bvs\b", "với"),
    (r"\bv\b", "vậy"),
    (r"\br\b", "rồi"),
    (r"\bt\b", "tao"),
    (r"\bm\b", "mày"),
]


def local_slang_to_tts(text: str) -> str:
    """Chuyển đổi câu chứa từ viết tắt & nhạy cảm sang câu đọc tự nhiên."""
    if not text:
        return ""
    res = text

    # Giờ/phút: 30p -> 30 phút, 5h -> 5 giờ
    res = re.sub(r"(\d+)\s*p\b", r"\1 phút", res, flags=re.IGNORECASE)
    res = re.sub(r"(\d+)\s*h\b", r"\1 giờ", res, flags=re.IGNORECASE)
    # Ký hiệu cười "=))", ":))", ":("
    res = re.sub(r"[:=]+[\)\(\-D]+", "", res)
    res = re.sub(r"blah\s*blah", "bờ la bờ la", res, flags=re.IGNORECASE)

    for pattern, repl in SLANG_TTS_MAP:
        res = re.sub(pattern, repl, res, flags=re.IGNORECASE)

    res = re.sub(r"\.{2,}", ", ", res)
    return re.sub(r"\s+", " ", res).strip()


# ---------------------------------------------------------------------------
# Làm sạch văn bản trước khi đưa vào TTS
# ---------------------------------------------------------------------------
_KEEP_CHARS = r"àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ" \
             r"ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ"


def clean_for_tts(text: str) -> str:
    """Loại bỏ emoji, ký tự lạ, gọn khoảng trắng."""
    if not text:
        return ""
    clean = strip_emoji(text)
    clean = re.sub(r"[:=]+[\)\(\-D]+", "", clean)
    clean = re.sub(r"\.{2,}", ", ", clean)
    # Giữ lại chữ, số và dấu câu cơ bản (bao gồm tiếng Việt)
    clean = re.sub(rf"[^\w\s\d,?.!\-À-ỹ{_KEEP_CHARS}]", " ", clean)
    return re.sub(r"\s+", " ", clean).strip()


# ---------------------------------------------------------------------------
# Cache TTS trên đĩa
# ---------------------------------------------------------------------------
def _cache_key(text: str, voice: str, speed: str) -> str:
    raw = f"{voice}|{speed}|{text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _cache_path(text: str, voice: str, speed: str) -> str:
    return str(TTS_CACHE_DIR / f"{_cache_key(text, voice, speed)}.aac")


def clear_tts_cache() -> int:
    """Xoá toàn bộ cache TTS. Trả về số file đã xoá."""
    removed = 0
    if TTS_CACHE_DIR.exists():
        for f in TTS_CACHE_DIR.glob("*.aac"):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


# ---------------------------------------------------------------------------
# Sinh audio TTS
# ---------------------------------------------------------------------------
async def make_tts_audio(text: str, voice: str, speed: str, out_path: str) -> str:
    """
    Tạo file AAC từ văn bản. Có cache: nếu đã sinh trước đó thì copy lại.
    Trả về đường dẫn file đầu ra.
    """
    clean = clean_for_tts(text)

    # Câu rỗng hoặc không phát âm được -> audio im lặng
    if not clean or not re.search(r"[\w\d]", clean):
        make_silence_aac(out_path, 0.6)
        return out_path

    cached = _cache_path(clean, voice, speed)
    # File AAC hop le >= 1KB. File nho hon la ket qua TTS that bai -> bo qua
    if os.path.exists(cached) and os.path.getsize(cached) > 1024:
        try:
            with open(cached, "rb") as src, open(out_path, "wb") as dst:
                dst.write(src.read())
            return out_path
        except OSError:
            pass
    elif os.path.exists(cached):
        # Cache hong -> xoa de khong dung lai
        try:
            os.remove(cached)
        except OSError:
            pass

    raw_mp3 = out_path + ".raw.mp3"
    last_err: Exception | None = None

    # Cac voice du phong: khi voice chinh bi rate-limit (NoAudioReceived),
    # doi sang voice khac de cau do van co giong doc.
    voices_to_try = [voice] + [v for v in FALLBACK_VOICES if v != voice]

    # Giu so request TTS dong thoi o muc thap de tranh bi rate-limit
    async with _get_semaphore():
        for attempt in range(TTS_MAX_RETRIES):
            # Doi voice xen ke: cau bi throttle o voice nao se thu voice khac
            cur_voice = voices_to_try[attempt % len(voices_to_try)]
            try:
                await _wait_tts_turn()
                if os.path.exists(raw_mp3):
                    os.remove(raw_mp3)
                comm = edge_tts.Communicate(clean, cur_voice, rate=speed)
                await comm.save(raw_mp3)

                # File MP3 rong/nho => TTS that bai (edge_tts khong loi nhung
                # tao file rong). Phai validate truoc khi dung.
                mp3_sz = os.path.getsize(raw_mp3) if os.path.exists(raw_mp3) else 0
                if mp3_sz < 1024:
                    raise RuntimeError(f"TTS tra ve audio rong ({mp3_sz} bytes)")

                if not normalize_audio_to_aac(raw_mp3, out_path):
                    raise RuntimeError("Khong chuyen doi duoc sang AAC")

                # AAC hop le >= 1KB. Neu nho hon -> audio hong/im lang.
                if os.path.getsize(out_path) < 1024:
                    raise RuntimeError(
                        f"AAC nho khong hop le ({os.path.getsize(out_path)} bytes)"
                    )

                # Luu vao cache de lan sau dung lai
                try:
                    with open(out_path, "rb") as src, open(cached, "wb") as dst:
                        dst.write(src.read())
                except OSError:
                    pass

                if os.path.exists(raw_mp3):
                    try:
                        os.remove(raw_mp3)
                    except OSError:
                        pass
                await _mark_tts_result(True)
                return out_path
            except Exception as e:
                last_err = e
                await _mark_tts_result(False)
                if os.path.exists(raw_mp3):
                    try:
                        os.remove(raw_mp3)
                    except OSError:
                        pass
                if attempt < TTS_MAX_RETRIES - 1:
                    # Backoff luy thua co jitter de thoat khoi "burst throttle"
                    # cua edge-tts (loi NoAudioReceived xuat hien ngau nhien).
                    delay = min(
                        TTS_RETRY_MAX_DELAY,
                        TTS_RETRY_BASE_DELAY * (attempt + 1),
                    )
                    delay += random.uniform(0, TTS_RETRY_JITTER)
                    await asyncio.sleep(delay)

    # Het so lan thu -> fallback an toan: im lang
    make_silence_aac(out_path, 1.0)
    if os.path.exists(raw_mp3):
        try:
            os.remove(raw_mp3)
        except OSError:
            pass
    print(f"[TTS] that bai sau nhieu lan thu cho '{clean[:40]}': {last_err}")
    return out_path


async def make_tts_audio_batch(items: List, voice: str, speed: str) -> List[str]:
    """
    Sinh nhiều audio TTS.
    items: list of (text, out_path)

    Tuần tự (không song song) để tránh edge-tts throttle sinh file im lặng.
    """
    results = []
    for text, path in items:
        results.append(await make_tts_audio(text, voice, speed, path))
    return results


# ---------------------------------------------------------------------------
# Gemini: bung viết tắt bằng AI (optional)
# ---------------------------------------------------------------------------
GEMINI_TTS_INSTRUCTION = """
Bạn là biên tập viên kịch bản đọc comment Threads cho kênh "Ghiền đọc comment".
Hãy chuyển từng câu bình luận gốc sang văn bản đọc lồng tiếng (TTS) theo đúng quy tắc sau:
1. Bung toàn bộ từ viết tắt theo đúng ngữ cảnh câu chuyện: "t" -> "tao", "m" -> "mày", "vk" -> "vợ", "ck" -> "chồng", "trc" -> "trước", "ngta" -> "người ta", "cx" -> "cũng", "v" -> "vậy", "r" -> "rồi", "lp" -> "lớp", "bn/b" -> "bạn", "tn" -> "tin nhắn", "nma" -> "nhưng mà", "mí thg" -> "mấy thằng", "30p" -> "30 phút".
2. Lách các từ nhạy cảm theo đúng phong cách đọc hài hướng của kênh:
   - "vcl" -> "vãi cả lờ"
   - "vl" -> "vãi lờ"
   - "đéo" -> "éo"
   - "đm / đme / địt mẹ" -> "đệt mẹ"
   - "hãm lồn" -> "hãm lờ"
   - "chết" -> "chớt"
   - "blah blah" -> "bờ la bờ la"
3. Xóa bỏ các ký hiệu mặt cười như ":))", "=))", ":(" hoặc emoji để giọng đọc không bị khựng.
4. Trả về đúng mảng JSON các chuỗi chữ đọc tương ứng theo thứ tự đầu vào.
"""


async def convert_tts_with_gemini(scenes, custom_key: Optional[str] = None):
    """
    Chuẩn hoá tts_text bằng Gemini. Luôn chạy bước nội bộ trước;
    nếu không có key hoặc Gemini lỗi thì giữ nguyên kết quả nội bộ.

    Hỗ trợ cả dict lẫn Pydantic model (nhận về cùng kiểu với input).
    """
    import json
    from google import genai
    from google.genai import types

    is_dict = bool(scenes) and isinstance(scenes[0], dict)

    def _get(s, key, default=""):
        if is_dict:
            return s.get(key, default)
        return getattr(s, key, default)

    def _set(s, key, value):
        if is_dict:
            s[key] = value
        else:
            setattr(s, key, value)

    # Bước 1: bộ từ điển nội bộ (luôn chạy)
    for s in scenes:
        _set(s, "tts_text", local_slang_to_tts(_get(s, "display_text", "")))
        if _get(s, "reply_display_text", None):
            _set(s, "reply_tts_text", local_slang_to_tts(_get(s, "reply_display_text", "")))

    api_key = (custom_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if not api_key:
        return scenes

    try:
        client = genai.Client(api_key=api_key)
        raw_list = [_get(s, "display_text", "") for s in scenes]
        resp = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=json.dumps(raw_list, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=GEMINI_TTS_INSTRUCTION,
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=list[str],
            ),
        )
        parsed = json.loads(resp.text)
        if isinstance(parsed, list) and len(parsed) == len(scenes):
            for i, val in enumerate(parsed):
                if val and val.strip():
                    _set(scenes[i], "tts_text", val.strip())
    except Exception as e:
        try:
            print(f"[Gemini Fallback] Dung bo tu dien noi bo: {e}")
        except Exception:
            pass

    return scenes
