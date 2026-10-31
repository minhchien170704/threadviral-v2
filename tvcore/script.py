"""
MODULE AI PHÂN TÍCH & VIẾT KỊCH BẢN (Giai đoạn 2).

Cung cấp:
  - 3-5 câu hook theo nhiều "góc tiếp cận" (tò mò / tranh luận / cảm xúc / hài)
  - Cấu trúc kịch bản đầy đủ:
        hook -> lời dẫn -> nội dung -> chuyển sang bình luận -> nhận xét biên tập -> CTA
  - Nhận xét biên tập (editorial) về bài viết
  - Tóm tắt nội dung bài viết
  - Tầng 1: template nội bộ (offline). Tầng 2: Gemini (nếu có API key).

Ràng buộc nội dung:
  - Trung thành bài đăng gốc, KHÔNG bịa tình tiết.
  - KHÔNG thay đổi ý nghĩa bình luận gốc.
"""
import asyncio
import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional

from .content import analyze_post, classify_topic, count_words, strip_emoji

# ---------------------------------------------------------------------------
# Hook theo góc tiếp cận (tầng 1 - offline)
# ---------------------------------------------------------------------------
HOOK_ANGLES = ["curiosity", "debate", "emotion", "humor", "surprise"]

_ANGLE_LABELS = {
    "curiosity": "Gây tò mò",
    "debate": "Chất thảo luận",
    "emotion": "Chạm cảm xúc",
    "humor": "Hài hước",
    "surprise": "Bất ngờ",
}

# Tên góc mà Gemini có thể trả về -> chuẩn hoá về góc nội bộ.
# Gemini dùng "conflict"/"relatability" -> map sang "debate"/"emotion".
_ANGLE_ALIASES = {
    "curiosity": "curiosity",
    "tò mò": "curiosity",
    "debate": "debate",
    "conflict": "debate",
    "thanh luan": "debate",
    "tranh luan": "debate",
    "emotion": "emotion",
    "relatability": "emotion",
    "dong cam": "emotion",
    "cam xuc": "emotion",
    "humor": "humor",
    "hai": "humor",
    "comedy": "humor",
    "surprise": "surprise",
    "bat ngo": "surprise",
    "unexpected": "surprise",
}

# ---------------------------------------------------------------------------
# ĐỢT 1: TỪ KHOÁ BỊA VIRAL - KHÔNG ĐƯỢC DÙNG KHI KHÔNG CÓ DỮ LIỆU
# ---------------------------------------------------------------------------
# Các cụm từ khẳng định mức độ lan truyền mà KHÔNG có căn cứ từ dữ liệu.
# Spec §3.2 yêu cầu loại bỏ các câu kiểu:
#   "Cả Threads đang phát sốt", "Hàng nghìn người tranh cãi",
#   "Cả mạng xã hội chia thành hai phe", "Bài viết gây bão", "Ai cũng phải bật cười"
# Khi không có dữ liệu chứng minh -> dùng cách diễn đạt trung tính.
_VIRAL_CLAIM_PATTERNS = [
    r"cả\s+threads\s+đang",
    r"cả\s+threads\s+xôn\s+xao",
    r"cả\s+threads\s+cười",
    r"threads\s+đang\s+phát\s+sốt",
    r"hàng\s+nghìn\s+người",
    r"hàng\s+ngàn\s+người",
    r"chia\s+threads\s+thành\s+hai\s+phe",
    r"chia\s+mạng\s+xã\s+hội\s+thành\s+hai\s+phe",
    r"mạng\s+xã\s+hội\s+chia\s+thành\s+hai\s+phe",
    r"toàn\s+bộ\s+threads",
    r"gây\s+bão",
    r"làm\s+mưa",
    r"đang\s+nóng\s+lơn",
    r"tranh\s+luận\s+không\s+hồi\s+kết",
    r"ai\s+cũng\s+phải\s+bật\s+cười",
    r"ai\s+cũng\s+đang\s+nói",
    r"mọi\s+người\s+đều\s+đang",
    r"đang\s+gây\s+tranh\s+cãi\s+dữ\s+dội",
    r"viral\s+lắm",
    r"lên\s+top",
    r"phá\s+kỷ\s+lục",
]
_VIRAL_CLAIM_RE = re.compile("|".join(_VIRAL_CLAIM_PATTERNS), re.IGNORECASE)


def has_unsupported_viral_claim(text: str) -> bool:
    """
    Kiểm tra câu có khẳng định mức độ lan truyền KHÔNG có căn cứ không.

    Trả về True nếu câu chứa một trong các cụm bịa viral.
    """
    t = (text or "").strip()
    if not t:
        return False
    return bool(_VIRAL_CLAIM_RE.search(t))


# Bảng thay thế cụm bịa -> cách diễu đạt trung tính
_VIRAL_REPLACEMENTS = [
    (r"cả\s+threads\s+đang\s+phát\s+sốt", "Bài này đang được bàn luận"),
    (r"cả\s+threads\s+xôn\s+xao", "Bài này đang được bàn luận"),
    (r"cả\s+threads\s+cười", "Nhiều người đã thấy hài"),
    (r"chia\s+threads\s+thành\s+hai\s+phe", "chia thành hai hướng"),
    (r"chia\s+mạng\s+xã\s+hội\s+thành\s+hai\s+phe", "chia thành hai hướng"),
    (r"mạng\s+xã\s+hội\s+chia\s+thành\s+hai\s+phe", "chia thành hai hướng"),
    (r"hàng\s+nghìn\s+người", "rất nhiều người"),
    (r"hàng\s+ngàn\s+người", "rất nhiều người"),
    (r"toàn\s+bộ\s+threads", "nhiều người"),
    (r"tranh\s+luận\s+không\s+hồi\s+kết", "đang được tranh luận"),
    (r"đang\s+gây\s+tranh\s+cãi\s+dữ\s+dội", "đang được tranh luận"),
    (r"đang\s+nóng\s+lơn", "đang được quan tâm"),
    (r"làm\s+mưa(\s+threads)?", "được quan tâm"),
    (r"gây\s+bão", "được quan tâm"),
    (r"ai\s+cũng\s+phải\s+bật\s+cười", "nhiều người sẽ bật cười"),
    (r"ai\s+cũng\s+đang\s+nói", "nhiều người đang nói"),
    (r"mọi\s+người\s+đều\s+đang", "nhiều người đang"),
    (r"viral\s+lắm", "được quan tâm"),
    (r"lên\s+top", "được chú ý"),
    (r"phá\s+kỷ\s+lục", "đạt mức ấn tượng"),
]


def sanitize_viral_claim(text: str) -> str:
    """
    Thay các cụm từ khẳng định mức độ lan truyền KHÔNG có căn cứ
    bằng cách diễn đạt trung tính tương đương.

    ĐỢT 1 - spec §3.2: hook phải dựa trên nội dung bài viết, comment thật,
    số lượng comment / lượt tương tác NẾU CÓ dữ liệu. Không có dữ liệu
    -> dùng cách diễn đạt trung tính.
    """
    t = (text or "").strip()
    if not t:
        return t
    out = t
    for pat, rep in _VIRAL_REPLACEMENTS:
        out = re.sub(pat, rep, out, flags=re.IGNORECASE)
    return out


# Template theo chủ đề -> hook góc "gây tò mò".
# ĐỢT 1: bỏ toàn bộ claim bịa viral ("cả Threads đang...", "chia thành hai phe"...).
# Nay dùng cách diễn đạt trung tính nhưng vẫn gợi tò mò.
_TOPIC_CURIOSITY = {
    "hài hước": "Có một chi tiết trong bài này mà đọc xong mới nhận ra.",
    "tình yêu": "Câu chuyện tình yêu này chạm đúng chỗ nhiều người đang trải qua.",
    "drama": "Một vụ drama ngoài đời được kể lại qua vài dòng trong bài này.",
    "tâm sự": "Có những câu chuyện đọc xong thì im lặng mất mấy giây.",
    "tranh luận": "Bài này đặt ra một câu hỏi khiến người đọc phải tự trả lời.",
    "đời sống": "Một câu chuyện đời sống nhỏ thôi nhưng chạm đúng điều ai cũng gặp.",
    "không rõ": "Bài viết này có một chi tiết đáng để dừng lại và đọc kỹ hơn.",
}

_ANGLE_TEMPLATES = {
    "curiosity": [
        "Có một thứ trong bài này mà bạn đọc xong sẽ phải dừng lại một giây.",
        "Đọc bài này xong, bạn sẽ không khỏi tự hỏi vì sao.",
        "Chi tiết cuối bài này mới là thứ đáng chú ý nhất.",
    ],
    "debate": [
        "Câu hỏi trong bài này dễ khiến người đọc phải nghiêng về hai hướng.",
        "Có người sẽ đồng ý, có người sẽ phản bác. Còn bạn?",
        "Bài này mở ra một câu hỏi mà mỗi người sẽ trả lời khác nhau.",
    ],
    "emotion": [
        "Có những câu chuyện nghe xong chỉ muốn nhắn tin cho người đã kể.",
        "Bài này không vui, nhưng sẽ khiến bạn nhớ về một ai đó.",
        "Có những điều chỉ cần một câu để chạm đúng chỗ đau.",
    ],
    "humor": [
        "Bài này hài hơn bạn tưởng, mà cũng tự trào hơn bạn tưởng nữa.",
        "Cuối bài là một cú lật mà chắc bạn không đoán được.",
        "Đọc xong bạn sẽ cười, rồi lại thấy hơi xót.",
    ],
    "surprise": [
        "Không ai ngờ chi tiết này lại ở đây.",
        "Điều bất ngờ nằm ngay giữa bài, không phải cuối bài.",
        "Có một chi tiết ở đây khiến người đọc phải ngạc nhiên.",
    ],
}

_EMOTION_WORDS = ("yêu", "buồn", "cô đơn", "nhớ", "mệt", "khóc", "cảm ơn", "xin lỗi")
_HUMOR_WORDS = ("haha", "vcl", "vl", "vãi", "kms", "hài", "meme", "lối", "cringe")


def _first_sentences(text: str, max_chars: int = 140) -> str:
    """Lấy phần đầu nội dung, cắt gọn ở dấu câu."""
    t = strip_emoji((text or "").replace("\n", " ").strip())
    if not t:
        return ""
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    for sep in (". ", "! ", "? ", ", ", "… "):
        idx = cut.rfind(sep)
        if idx > max_chars * 0.4:
            return cut[:idx].strip()
    return cut.strip()


def _topic_text(scenes: List[Dict[str, Any]]) -> str:
    if not scenes:
        return ""
    s = scenes[0]
    if not isinstance(s, dict):
        s = getattr(s, "model_dump", lambda: dict(s))()
    return (s.get("display_text") or "").strip()


def _author_text(scenes: List[Dict[str, Any]]) -> str:
    if not scenes:
        return ""
    s = scenes[0]
    if not isinstance(s, dict):
        s = getattr(s, "model_dump", lambda: dict(s))()
    return (s.get("author") or "").strip()


def _comment_texts(scenes: List[Dict[str, Any]], limit: int = 8) -> List[str]:
    out = []
    for s in scenes[1:1 + limit]:
        if not isinstance(s, dict):
            s = getattr(s, "model_dump", lambda: dict(s))()
        txt = (s.get("display_text") or "").strip()
        if txt:
            out.append(txt)
    return out


def detect_hook_angle(hook_text: str) -> str:
    """
    Suy luận GÓC HOOK từ NỘI DUNG THẬT của câu hook.

    ĐỢT 1 - sửa lỗi gán nhãn theo index (`HOOK_ANGLES[i % len(...)]`).
    Nay nhãn phải phản ánh đúng nội dung câu hook:
      - Có dấu hỏi / cụm kích thích tranh luận -> "debate"
      - Có từ hài hước -> "humor"
      - Có từ cảm xúc -> "emotion"
      - Có từ bất ngờ -> "surprise"
      - Còn lại -> "curiosity"

    So khớp KHÔNG DẤU (deaccent) -> nhận diện đúng cả chữ có dấu lẫn
    không dấu (Gemini đôi khi trả về thiếu dấu).

    Trả về 1 góc trong HOOK_ANGLES.
    """
    from .content import deaccent

    raw = (hook_text or "").strip().lower()
    if not raw:
        return "curiosity"
    t = deaccent(raw)

    # 1. Câu hỏi / tranh luận
    if "?" in raw or re.search(
        r"\b(y kien|quan diem|tranh luan|tranh cai|dong y|khong dong y|"
        r"hai phe|hai luong|hai huong|ban nghi sao|ban thay sao|co nen)\b", t,
    ):
        return "debate"

    # 2. Hài hước
    if re.search(r"\b(haha|vcl|vl|vai|kms|hai|loi|cringe|meme|buon cuoi)\b", t):
        return "humor"

    # 3. Cảm xúc
    if re.search(
        r"\b(yeu|buon|co don|cham|nho|khoc|cam on|xin loi|dong cam|chan tinh)\b", t,
    ):
        return "emotion"

    # 4. Bất ngờ
    if re.search(
        r"\b(bat ngo|ngac nhien|khong ngo|ai ngo|chua ai|it ai|hoa ra|thu vi la)\b", t,
    ):
        return "surprise"

    # 5. Tò mò (mặc định)
    return "curiosity"


def normalize_angle(raw: Optional[str]) -> Optional[str]:
    """
    Chuẩn hoá góc do Gemini trả về về góc nội bộ hợp lệ.

    Trả về góc hợp lệ, hoặc None nếu raw không nhận diện được
    (khi đó caller dùng heuristic từ nội dung).
    """
    if not raw:
        return None
    key = str(raw).strip().lower().replace("-", " ").replace("_", " ")
    key = re.sub(r"\s+", " ", key).strip()
    if key in _ANGLE_ALIASES:
        return _ANGLE_ALIASES[key]
    # Bỏ dấu tiếng Việt để so khớp ("tò mò" -> "to mo")
    from .content import deaccent
    key2 = deaccent(key)
    if key2 in _ANGLE_ALIASES:
        return _ANGLE_ALIASES[key2]
    # "to mo" (từ "tò mò") - khoảng trắng đã bị thay bằng dấu gạch
    key3 = key2.replace(" ", "")
    for k, v in _ANGLE_ALIASES.items():
        if k.replace(" ", "") == key3:
            return v
    return None


# ---------------------------------------------------------------------------
# Tầng 1: hook nhiều lựa chọn (offline)
# ---------------------------------------------------------------------------
def generate_hook_options(
    scenes: List[Dict[str, Any]],
    n: int = 3,
) -> List[Dict[str, str]]:
    """
    Sinh N câu hook theo các góc tiếp cận khác nhau.

    ĐỢT 1: nhãn "angle" được gán TỪ NỘI DUNG THẬT của câu hook
    (detect_hook_angle), KHÔNG gán theo index.

    Trả về list [{"text", "angle", "label"}].
    """
    topic = _topic_text(scenes)
    low = topic.lower()
    n = max(1, min(5, int(n or 3)))

    # Chủ đề để chọn hook phù hợp
    label, _ = classify_topic(topic)

    # Ưu tiên góc theo nội dung thực tế
    angle_priority: List[str] = []
    if any(w in low for w in _HUMOR_WORDS):
        angle_priority.append("humor")
    if "?" in topic:
        angle_priority.append("debate")
    if any(w in low for w in _EMOTION_WORDS):
        angle_priority.append("emotion")
    angle_priority.append("surprise")
    angle_priority.append("curiosity")
    # bỏ trùng
    seen = set()
    ordered = []
    for a in angle_priority:
        if a not in seen:
            seen.add(a)
            ordered.append(a)

    snippet = _first_sentences(topic, 80)
    seed = int(hashlib.md5(topic.encode("utf-8")).hexdigest()[:8], 16)

    options: List[Dict[str, str]] = []
    for i in range(n):
        # Góc ĐỀ XUẤT theo ưu tiên nội dung bài viết (dùng để chọn template)
        suggested = ordered[i % len(ordered)]
        tpl_list = _ANGLE_TEMPLATES[suggested]
        tpl = tpl_list[(seed + i) % len(tpl_list)]

        if suggested == "curiosity":
            tpl = _TOPIC_CURIOSITY.get(label, tpl)
            if snippet and i == 0:
                tpl = f"“{snippet}…” {tpl}"

        text = sanitize_viral_claim(tpl.strip())

        # ĐỢT 1: góc phải phản ánh NỘI DUNG THẬT của câu hook, không gán
        # theo index. Ưu tiên góc do heuristic đọc từ chính câu hook.
        actual = detect_hook_angle(text)
        # Nếu heuristic không tìm thấy tín hiệu nào -> giữ góc đề xuất
        # (template được chọn theo ưu tiên nội dung bài viết).
        if actual == "curiosity" and suggested != "curiosity":
            actual = suggested

        options.append({
            "text": text,
            "angle": actual,
            "label": _ANGLE_LABELS.get(actual, actual),
        })

    return options


# ---------------------------------------------------------------------------
# Tầng 1: nhận xét biên tập + lời dẫn (offline)
# ---------------------------------------------------------------------------
_EDITORIAL_TEMPLATES = [
    "Điều đáng chú ý nhất trong bài này là cách tác giả nhìn vấn đề từ góc rất riêng.",
    "Bài viết ngắn nhưng đọc lại thì thấy có điều đáng suy nghĩ hơn người tưởng.",
    "Phần hay nhất của bài này là chi tiết giữa chừng mà nhiều người bỏ qua.",
    "Có một điều trong bài này mà mình đọc xong phải dừng lại vài giây.",
    "Bài này không có kết luận, nhưng lại khiến người đọc tự nghĩ tiếp.",
]

_TRANSITION_TEMPLATES = [
    "Và đây là phần mọi người bàn luận nhiều nhất:",
    "Bình luận dưới bài mới là phần thú vị:",
    "Giờ mới đến phần gây tranh luận:",
    "Và đây là những góc nhìn khác nhau dưới bài:",
]


def generate_editorial(scenes: List[Dict[str, Any]]) -> str:
    """Nhận xét biên tập ngắn gắn với bài viết (tầng 1 offline)."""
    topic = _topic_text(scenes)
    if not topic:
        return ""
    seed = int(hashlib.md5(("ed" + topic).encode("utf-8")).hexdigest()[:8], 16)
    base = _EDITORIAL_TEMPLATES[seed % len(_EDITORIAL_TEMPLATES)]

    # Bổ sung ngữ cảnh nếu bài có câu hỏi
    if "?" in topic and seed % 2 == 0:
        base += " Và câu hỏi ở cuối chính là điểm mấu chốt."
    return base


def generate_transition(scenes: List[Dict[str, Any]]) -> str:
    """Lời dẫn chuyển từ bài viết sang phần bình luận (tầng 1 offline)."""
    n = len(_comment_texts(scenes))
    if n == 0:
        return ""
    topic = _topic_text(scenes)
    seed = int(hashlib.md5(("tr" + topic).encode("utf-8")).hexdigest()[:8], 16)
    return _TRANSITION_TEMPLATES[seed % len(_TRANSITION_TEMPLATES)]


def generate_summary(scenes: List[Dict[str, Any]]) -> str:
    """Tóm tắt 1 câu bài viết (tầng 1 offline)."""
    topic = _topic_text(scenes)
    if not topic:
        return ""
    snip = _first_sentences(topic, 160)
    if not snip:
        return ""
    if len(snip) >= 150:
        snip = snip.rstrip(",.;: ") + "…"
    return snip


def generate_script_template(scenes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Dựng kịch bản đầy đủ bằng template nội bộ (offline).
    """
    analysis = analyze_post(_topic_text(scenes))
    hooks = generate_hook_options(scenes, 3)
    return {
        "source": "template",
        "summary": generate_summary(scenes),
        "hooks": hooks,
        "hook_text": hooks[0]["text"] if hooks else "",
        "intro": "",
        "transition": generate_transition(scenes),
        "editorial": generate_editorial(scenes),
        "cta_text": "",
        "analysis": analysis,
    }


# ---------------------------------------------------------------------------
# Tầng 2: Gemini
# ---------------------------------------------------------------------------
_ANALYZE_PROMPT = """Bạn là biên tập viên nội dung cho kênh video ngắn về Threads (mạng xã hội của Meta). Video dọc 9:16, có giọng đọc TTS tiếng Việt.

NHIỆM VỤ: dựa trên bài viết gốc và các bình luận được chọn, hãy trả về JSON gồm:

1. "summary": tóm tắt bài viết trong 1 câu (tối đa 30 từ).
2. "hooks": MỘT DANH SÁCH 3 câu hook, mỗi câu 12-26 từ. Mỗi câu theo một góc tiếp cận KHÁC NHAU. Bắt đầu mỗi câu bằng thông tin cụ thể từ bài viết.
3. "hook_angles": DANH SÁCH các góc tiếp cận tương ứng với từng hook, theo đúng thứ tự. Mỗi góc phải là MỘT trong: "curiosity" (gây tò mò), "debate" (chất thảo luận), "emotion" (chạm cảm xúc), "humor" (hài hước), "surprise" (bất ngờ). Nhãn phải phản ánh ĐÚNG nội dung câu hook đó.
4. "intro": lời dẫn giới thiệu bài viết (1-2 câu, tối đa 30 từ).
5. "transition": lời dẫn chuyển sang phần bình luận (1 câu, tối đa 15 từ).
6. "editorial": nhận xét biên tập ngắn về bài viết (1-2 câu, tối đa 30 từ) - nêu điều đáng chú ý nhất.
7. "cta": câu kết khuyến khích thảo luận (1 câu, 8-18 từ, CÓ emoji, KHÔNG quá 2 emoji).

QUY TẮC BẮT BUỘC:
- Trung thành bài đăng gốc. KHÔNG bịa thêm sự kiện, con số, nhân vật hay trích dẫn.
- KHÔNG thay đổi ý nghĩa của bình luận.
- TUYỆT ĐỐI KHÔNG khẳng định mức độ lan truyền khi dữ liệu không chứng minh được.
  KHÔNG dùng các cụm như: "cả Threads đang phát sốt", "hàng nghìn người tranh cãi",
  "cả mạng xã hội chia thành hai phe", "bài viết gây bão", "ai cũng phải bật cười",
  "Threads xôn xao", "làm mưa Threads", "viral".
  Nếu không có số liệu, hãy dùng cách diễn đạt trung tính nhưng vẫn hấp dẫn,
  ví dụ: "Một câu hỏi đơn giản nhưng phần bình luận lại có hai luồng ý kiến trái ngược."
  (chỉ dùng ví dụ này khi dữ liệu thực sự có hai luồng ý kiến).
- Tiếng Việt tự nhiên, đúng chính tả. KHÔNG dùng Markdown. KHÔNG ghi nhãn "hook:" hay "cta:" trong giá trị.
- Nếu nội dung gốc là tiếng Trung Quốc hoặc tiếng Anh, hãy viết lại bằng tiếng Việt đúng nghĩa.
- Không nội dung nhạy cảm, không kỳ thị, không nhạo đùa công kích cá nhân.
- Nếu nội dung thiếu ngữ cảnh, hãy giữ hook ngắn gọn, không bịa.

Trả về JSON đúng format:
{"summary": "...", "hooks": ["...", "...", "..."], "hook_angles": ["...", "...", "..."], "intro": "...", "transition": "...", "editorial": "...", "cta": "..."}"""

_GEMINI_MODEL = "gemini-2.5-flash"
# Timeout gọi Gemini (giây) - để ứng dụng không treo khi API lỗi
_GEMINI_TIMEOUT_SECONDS = 60


async def generate_script_gemini(
    scenes: List[Dict[str, Any]],
    api_key: str,
) -> Dict[str, Any]:
    """Sinh kịch bản đầy đủ bằng Gemini. Raise nếu lỗi để caller fallback."""
    from google import genai
    from google.genai import types

    key = (api_key or "").strip()
    if not key:
        raise ValueError("Thiếu Gemini API key")

    topic = _topic_text(scenes)
    author = _author_text(scenes)
    comments = _comment_texts(scenes, 8)

    payload = (
        f"BÀI VIẾT GỐC (tác giả @{author}):\n{topic[:1500]}\n\n"
        f"BÌNH LUẬN ĐÃ CHỌN:\n"
        + ("\n".join(f"- {c}" for c in comments) if comments else "(chưa có)")
        + "\n"
    )

    client = genai.Client(api_key=key)
    try:
        resp = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model=_GEMINI_MODEL,
                contents=payload,
                config=types.GenerateContentConfig(
                    system_instruction=_ANALYZE_PROMPT,
                    temperature=0.9,
                    response_mime_type="application/json",
                    response_schema={
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string"},
                            "hooks": {"type": "array", "items": {"type": "string"}},
                            "hook_angles": {"type": "array", "items": {"type": "string"}},
                            "intro": {"type": "string"},
                            "transition": {"type": "string"},
                            "editorial": {"type": "string"},
                            "cta": {"type": "string"},
                        },
                        "required": ["summary", "hooks", "intro", "transition",
                                     "editorial", "cta"],
                    },
                ),
            ),
            timeout=_GEMINI_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"Gemini timeout sau {_GEMINI_TIMEOUT_SECONDS}s"
        )

    # --- Parse JSON an toàn (Gemini có thể trả về text lỗi) ---
    raw_text = (getattr(resp, "text", "") or "").strip()
    if not raw_text:
        raise ValueError("Gemini trả về nội dung rỗng")
    try:
        data = json.loads(raw_text)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Gemini trả về JSON không hợp lệ: {e}")
    if not isinstance(data, dict):
        raise ValueError("Gemini trả về JSON không phải object")

    # --- Hooks + góc ---
    hooks_raw = data.get("hooks") or []
    if isinstance(hooks_raw, str):
        hooks_raw = [hooks_raw]
    if not isinstance(hooks_raw, list):
        raise ValueError("Gemini trả về 'hooks' không hợp lệ")

    angles_raw = data.get("hook_angles") or []
    if isinstance(angles_raw, str):
        angles_raw = [angles_raw]
    if not isinstance(angles_raw, list):
        angles_raw = []

    hooks = []
    for i, h in enumerate(hooks_raw[:5]):
        if not isinstance(h, str):
            continue
        ht = h.strip()
        if not ht:
            continue
        # ĐỢT 1: KHÔNG gán góc theo index.
        #  1. Thử dùng góc Gemini trả về (đã validate).
        #  2. Nếu không có / không hợp lệ -> suy luận từ nội dung hook.
        angle = None
        if i < len(angles_raw):
            angle = normalize_angle(angles_raw[i])
        if angle is None:
            angle = detect_hook_angle(ht)
        hooks.append({
            "text": sanitize_viral_claim(ht),
            "angle": angle,
            "label": _ANGLE_LABELS.get(angle, angle),
        })
    if not hooks:
        raise ValueError("Gemini không trả về hook hợp lệ")

    analysis = analyze_post(topic)

    return {
        "source": "gemini",
        "summary": (data.get("summary") or "").strip(),
        "hooks": hooks,
        "hook_text": hooks[0]["text"],
        "intro": (data.get("intro") or "").strip(),
        "transition": (data.get("transition") or "").strip(),
        "editorial": (data.get("editorial") or "").strip(),
        "cta_text": (data.get("cta") or "").strip(),
        "analysis": analysis,
    }


# ---------------------------------------------------------------------------
# Hàm chính: fallback 2 tầng
# ---------------------------------------------------------------------------
async def auto_generate_script(
    scenes: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    n_hooks: int = 3,
) -> Dict[str, Any]:
    """
    Sinh kịch bản đầy đủ. Ưu tiên Gemini, fallback template nội bộ.
    """
    base = generate_script_template(scenes)

    key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if not key:
        return base

    try:
        result = await generate_script_gemini(scenes, key)
        # Bổ sung phần template thiếu
        for k in ("summary", "intro", "transition", "editorial"):
            if not result.get(k):
                result[k] = base.get(k, "")
        if not result.get("cta_text"):
            result["cta_text"] = base.get("cta_text", "")
        return result
    except Exception as e:
        print(f"[Script] Gemini loi, dung template noi bo: {e}")
        base["source"] = "template (gemini fallback)"
        return base
