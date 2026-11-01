"""
MODULE ĐIỀU CHỈNH THỜI LƯỜNG ĐỘNG (DYNAMIC PACING).

Giải quyết Lỗi C: thời gian hiển thị phải khớp với:
  - số từ / số ký tự
  - thời lượng giọng đọc thực tế (TTS)
  - thời gian người xem cần để đọc
  - khoảng nghỉ giữa các câu

Nguyên tắc: thời lượng cảnh = max(thời gian đọc, thời gian nghe TTS, thời gian đọc tối thiểu).
Không bao giờ cắt ngắn hơn thời gian TTS -> tránh mất đầu/cuối câu.

BUG ĐÃ SỬA (Đợt 1): trước đây `scene_duration()` clamp cứng ở MAX_SCENE_SECONDS=12.0.
=> Một đoạn TTS dài 27s bị cắt còn 12s -> mất 15s giọng đọc.
   Nay MAX_SCENE_SECONDS chỉ là NGUỒNG KHUYẾN NGHỊ chia visual, KHÔNG cắt audio.

NGUỒN TIMELINE CHÍNH THỨC (Đợt 1.1 - M4):
  Module này là nguồn DUY NHẤT tính thời lượng / chia visual.
  - `scene_duration()`      -> thời lượng 1 cảnh (>= audio, không cắt audio)
  - `plan_timeline()`       -> kế hoạch thời lượng cho nhiều cảnh (API /api/timeline)
  - `plan_visual_slices()`  -> nguồn DUY NHẤT chia visual cho 1 cảnh có audio dài
  - `split_visual_segments()` -> helper chia đều, CHỈ được gọi từ plan_visual_slices()

  `render.py` KHÔNG tự tính lại timeline: nó dùng `plan_visual_slices()` và
  đo lại thời lượng thực tế từ file audio để truyền cho QC pass 2.
"""
from typing import Any, Dict, List, Optional
import math

# Bảng tham khảo theo đề xuất (giây) cho từng khoảng số từ
WORD_BANDS = [
    (10, 2.0, 3.0),
    (20, 3.0, 4.0),
    (35, 4.0, 6.0),
    (50, 6.0, 8.0),
]

# Tốc độ đọc trung bình (từ/giây) theo giọng + tốc độ TTS
BASE_WPS = 3.4          # ~3.4 từ/giây khi đọc bình thường
MIN_READ_SECONDS = 1.6  # ngắn nhất cho 1 cảnh
PAUSE_BETWEEN_SCENES = 0.15

# NGUỒNG KHUYẾN NGHỊ: một visual nên ngắn hơn mức này để người xem
# không bị "đứng hình" nhìn một thẻ quá lâu.
# TUYỆT ĐỐI KHÔNG dùng giá trị này để CẮT audio.
MAX_SCENE_SECONDS = 12.0

# Khoảng đệm thêm sau mỗi cảnh (giây) - giữ nguyên thiết kế cũ
SCENE_PAD_SECONDS = 0.15

# Khoảng nghỉ thêm sau dấu câu (tính vào giọng đọc)
PUNCT_PAUSE = {
    ".": 0.22, "!": 0.22, "?": 0.26, ",": 0.10,
    ";": 0.14, ":": 0.12, "…": 0.28, "-": 0.08,
}


def count_words(text: str) -> int:
    if not text:
        return 0
    import re
    t = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return len([w for w in t.split() if w])


def estimate_speech_seconds(text: str, speed: str = "+20%") -> float:
    """
    Ước lượng thời lượng đọc của 1 đoạn chữ (giây).

    Dựa trên: số từ / tốc độ đọc + khoảng nghỉ theo dấu câu.
    """
    text = (text or "").strip()
    if not text:
        return 0.0

    n_words = count_words(text)
    if n_words == 0:
        return 0.0

    # Điều chỉnh theo tốc độ TTS ("+20%" -> nhanh hơn 20%)
    mult = 1.0
    try:
        raw = (speed or "+0%").replace("%", "").replace("+", "").strip()
        pct = int(raw or 0)
        mult = 1.0 / (1.0 + pct / 100.0)
    except (TypeError, ValueError):
        mult = 1.0

    wps = BASE_WPS * mult
    speech = n_words / max(0.8, wps)

    # Khoảng nghỉ theo dấu câu
    pauses = 0.0
    for ch, sec in PUNCT_PAUSE.items():
        pauses += text.count(ch) * sec

    return round(speech + pauses, 3)


def min_read_seconds(text: str) -> float:
    """
    Thời gian tối thiểu để người xem ĐỌC được nội dung trên màn hình.
    Dùng bảng tham khảo theo số từ.
    """
    n = count_words(text)
    if n <= 0:
        return MIN_READ_SECONDS
    if n <= WORD_BANDS[0][0]:
        lo, hi = WORD_BANDS[0][1], WORD_BANDS[0][2]
    elif n <= WORD_BANDS[1][0]:
        lo, hi = WORD_BANDS[1][1], WORD_BANDS[1][2]
    elif n <= WORD_BANDS[2][0]:
        lo, hi = WORD_BANDS[2][1], WORD_BANDS[2][2]
    elif n <= WORD_BANDS[3][0]:
        lo, hi = WORD_BANDS[3][1], WORD_BANDS[3][2]
    else:
        # Trên 50 từ -> chia nhiều cảnh, mỗi cảnh ~8s
        lo, hi = 8.0, 9.5
    # Nội dung có câu hỏi / nhiều dấu câu -> cần thêm thời gian suy nghĩ
    t = text or ""
    if "?" in t:
        hi += 0.3
    return round(min(max(lo, MIN_READ_SECONDS), hi), 3)


def scene_duration(
    text: str,
    audio_duration: Optional[float] = None,
    speed: str = "+20%",
    is_hook: bool = False,
    is_cta: bool = False,
) -> float:
    """
    Tính thời lượng 1 cảnh (giây).

    - Hook: luôn >= 2.5s để tạo được ấn tượng đầu video.
    - CTA : luôn >= 2.0s.
    - Bình thường: max(đọc tối thiểu, TTS thực tế) + đệm nhỏ.

    BUG ĐÃ SỬA (Đợt 1): KHÔNG còn clamp `min(..., MAX_SCENE_SECONDS)`.
    Trước đây một đoạn TTS 27s bị cắt còn 12s -> mất 15s giọng đọc.
    Nay hàm luôn trả về >= audio_duration (khi có audio thật).

    MAX_SCENE_SECONDS giờ chỉ dùng làm ngưỡng khuyến nghị trong
    `split_visual_segments()` (chia visual, KHÔNG cắt audio).
    """
    t = (text or "").strip()
    est = estimate_speech_seconds(t, speed)
    read = min_read_seconds(t)

    if audio_duration and audio_duration > 0:
        base = max(est, float(audio_duration), read)
    else:
        base = max(est, read)

    if is_hook:
        base = max(base, 2.5)
    if is_cta:
        base = max(base, 2.0)

    # KHÔNG clamp ở MAX_SCENE_SECONDS - audio phải phát đủ.
    return round(base + SCENE_PAD_SECONDS, 3)


def split_visual_segments(
    audio_duration: float,
    max_segment: float = MAX_SCENE_SECONDS,
    n_segments: Optional[int] = None,
) -> List[Dict[str, float]]:
    """
    Chia một đoạn audio dài thành nhiều VISUAL segment liên tục.

    Audio KHÔNG bị cắt: mỗi segment chỉ là một khoảng thời gian trên
    cùng một file audio (dùng để đổi ảnh giữa chừng).

    Ví dụ audio 27s, max_segment 9s -> 3 segment: 0-9, 9-18, 18-27.

    `n_segments`: (tuỳ chọn) ép số phần. Nếu None -> tự tính
        `ceil(dur / max_segment)` để mỗi đoạn <= max_segment.

    Luôn đảm bảo:
      - Các đoạn LIÊN TỤC (không khoảng trống).
      - Tổng thời lượng == audio_duration (sai số <= 0.001s).

    Trả về list [{"index", "start", "end", "duration"}].
    """
    dur = float(audio_duration or 0.0)
    if dur <= 0:
        return []
    seg_max = float(max_segment or MAX_SCENE_SECONDS)
    if seg_max <= 0:
        seg_max = MAX_SCENE_SECONDS

    if n_segments is not None and int(n_segments) >= 1:
        n = int(n_segments)
    else:
        n = max(1, int(math.ceil(dur / seg_max)))
    n = max(1, n)

    # Chia đều thành n phần -> mỗi phần <= seg_max
    step = dur / n
    segments: List[Dict[str, float]] = []
    for i in range(n):
        start = round(i * step, 3)
        end = round(dur if i == n - 1 else (i + 1) * step, 3)
        segments.append({
            "index": i,
            "start": start,
            "end": end,
            "duration": round(end - start, 3),
        })
    return segments


def plan_visual_slices(
    audio_duration: float,
    text_parts: int = 1,
    max_segment: float = MAX_SCENE_SECONDS,
) -> Dict[str, Any]:
    """
    Lập kế hoạch cắt VISUAL cho một cảnh có audio dài.

    NGUYÊN TẮC (fix C1 - Đợt 1.1):
      - Visual PHẢI phủ kín toàn bộ khoảng thời gian audio.
      - Không được có khoảng trống (video trống trong khi audio vẫn phát).
      - Audio KHÔNG bị cắt, lặp hay mất.
      - Thứ tự nội dung text được giữ nguyên.

    Vì sao cần hàm này?
      `split_visual_segments()` (theo thời lượng) có thể tạo N đoạn, còn
      `split_visual_text_parts()` chỉ tạo được K < N phần text (vd text chỉ
      wrap ra 8 dòng, mỗi thẻ chứa tối đa 7 dòng -> K = 2). Nếu render lặp
      theo K thì các đoạn còn lại KHÔNG có visual -> video trống.

    Chiến lược:
      - `n_min` = số visual tối thiểu để mỗi đoạn <= `max_segment`.
      - `n_text` = số phần text thực tế (>= 1).
      - `count` = max(n_min, n_text):
          + n_text >= n_min -> mỗi phần text 1 visual, thời gian chia đều.
          + n_text <  n_min -> có nhiều visual hơn phần text -> phần text CUỐI
            được giữ hiển thị liên tiếp qua nhiều đoạn (KHÔNG tạo đoạn trống).
      - Thời gian luôn chia đều `count` phần nên mỗi đoạn <= max_segment.

    Trả về:
      {
        "count": int,
        "segments": [{"index","start","end","duration"}, ...],
        "part_index": [int, ...],   # visual i dùng phần text nào
        "total_duration": float,    # == audio_duration
      }
    """
    dur = float(audio_duration or 0.0)
    if dur <= 0:
        return {"count": 0, "segments": [], "part_index": [], "total_duration": 0.0}

    seg_max = float(max_segment or MAX_SCENE_SECONDS)
    if seg_max <= 0:
        seg_max = MAX_SCENE_SECONDS

    n_min = max(1, int(math.ceil(dur / seg_max)))
    n_text = max(1, int(text_parts or 1))

    count = max(n_min, n_text)

    # Phân bổ phần text cho từng visual.
    # Nếu n_text >= count -> mỗi visual một phần (đơn giản, giữ thứ tự).
    # Nếu n_text <  count -> các visual cuối lặp lại phần text cuối cùng,
    #   để vẫn CÓ HÌNH trong suốt khoảng thời gian audio.
    part_index: List[int] = []
    for i in range(count):
        if n_text >= count:
            part_index.append(i)
        else:
            part_index.append(min(i, n_text - 1))

    segments = split_visual_segments(dur, seg_max, n_segments=count)
    return {
        "count": count,
        "segments": segments,
        "part_index": part_index,
        "total_duration": dur,
    }


def plan_timeline(
    scenes: List[Dict[str, Any]],
    audio_durations: Optional[List[float]] = None,
    speed: str = "+20%",
) -> List[Dict[str, Any]]:
    """
    Lập kế hoạch thời lượng cho toàn bộ cảnh.

    `scenes`: list dict có "display_text" (và tùy chọn "author" = "__hook__"/"__cta__")
    `audio_durations`: thời lượng TTS thực tế (nếu có) theo thứ tự cảnh.

    Trả về list dict {"duration", "estimated", "words", "audio_duration",
                      "needs_visual_split", "visual_segments"}
    """
    plan: List[Dict[str, Any]] = []
    for i, sc in enumerate(scenes):
        text = (sc.get("display_text") or sc.get("tts_text") or "")
        author = sc.get("author") or ""
        aud = None
        if audio_durations and i < len(audio_durations):
            aud = audio_durations[i]
        dur = scene_duration(
            text, aud, speed,
            is_hook=(author == "__hook__"),
            is_cta=(author == "__cta__"),
        )
        # Nếu audio dài hơn ngưỡng khuyến nghị -> visual nên được chia
        # (audio vẫn phát liên tục, chỉ đổi ảnh giữa chừng).
        needs_split = bool(aud and aud > MAX_SCENE_SECONDS)
        plan.append({
            "duration": dur,
            "estimated": aud is None,
            "words": count_words(text),
            "audio_duration": round(float(aud), 3) if aud else None,
            "needs_visual_split": needs_split,
            "visual_segments": split_visual_segments(aud) if needs_split else [],
        })
    return plan


def adjust_to_target_total(
    plan: List[Dict[str, Any]],
    target_seconds: float,
    min_total: float = 20.0,
    max_total: float = 70.0,
) -> List[Dict[str, Any]]:
    """
    Cân bằng tổng thời lượng về mục tiêu (vd 45-55s) mà không làm cảnh nào
    quá ngắn để đọc (tối thiểu 70% thời lượng gốc).

    BUG ĐÃ SỬA (Đợt 1): không còn clamp ở MAX_SCENE_SECONDS khi bù phần lệch.
    Ngoài ra KHÔNG được rút ngắn cảnh xuống dưới thời lượng audio thật
    (nếu có `audio_duration` trong plan).
    """
    if not plan:
        return plan

    total = sum(p["duration"] for p in plan)
    target = max(min_total, min(max_total, target_seconds))
    if total <= 0:
        return plan

    ratio = target / total
    floor_ratio = 0.70
    if ratio < 1.0:
        ratio = max(ratio, floor_ratio)

    for p in plan:
        new_dur = max(MIN_READ_SECONDS, p["duration"] * ratio)
        # TUYỆT ĐỐI không cắt ngắn hơn audio thật
        aud = p.get("audio_duration")
        if aud:
            new_dur = max(new_dur, float(aud))
        p["duration"] = round(new_dur, 3)

    # Bù phần lệch nhỏ bằng cách phân bổ vào cảnh dài nhất
    new_total = sum(p["duration"] for p in plan)
    diff = target - new_total
    if abs(diff) > 0.05 and plan:
        idx = max(range(len(plan)), key=lambda i: plan[i]["duration"])
        # KHÔNG được rút ngắn cảnh xuống dưới thời lượng audio thật
        _floor = MIN_READ_SECONDS
        _aud = plan[idx].get("audio_duration")
        if _aud:
            _floor = max(_floor, float(_aud))
        plan[idx]["duration"] = round(
            max(_floor, plan[idx]["duration"] + diff), 3
        )

    return plan
