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

# Template theo chủ đề -> hook góc "gây tò mò"
_TOPIC_CURIOSITY = {
    "hài hước": "Có một chi tiết trong bài này mà cả Threads đang cười vì tới giờ chưa ai nhận ra.",
    "tình yêu": "Câu chuyện tình yêu này đang khiến cả Threads hỏi lại chuyện của chính mình.",
    "drama": "Một vụ drama ngoài đời đang được bàn tán ầm ĩ trên Threads.",
    "tâm sự": "Có những câu chuyện đọc xong thì im lặng mất mấy giây.",
    "tranh luận": "Câu hỏi này đang chia Threads thành hai phe không hồi kết.",
    "đời sống": "Một câu chuyện đời sống nhỏ thôi nhưng chạm đúng điều ai cũng gặp.",
    "không rõ": "Bài viết này đang làm Threads xôn xao, và lý do thú vị hơn bạn nghĩ.",
}

_ANGLE_TEMPLATES = {
    "curiosity": [
        "Có một thứ trong bài này mà bạn đọc xong sẽ phải dừng lại một giây.",
        "Đọc bài này xong, bạn sẽ không khỏi tự hỏi vì sao.",
        "Chi tiết cuối bài này mới là thứ đáng chú ý nhất.",
    ],
    "debate": [
        "Câu hỏi trong bài này chắc chắn sẽ chia người xem thành hai phe.",
        "Có người sẽ đồng ý, có người sẽ phản bác. Còn bạn?",
        "Bài này đang mở ra một cuộc tranh luận không hồi kết.",
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
        "Có một chi tiết ở đây khiến cả Threads phải ngạc nhiên.",
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


# ---------------------------------------------------------------------------
# Tầng 1: hook nhiều lựa chọn (offline)
# ---------------------------------------------------------------------------
def generate_hook_options(
    scenes: List[Dict[str, Any]],
    n: int = 3,
) -> List[Dict[str, str]]:
    """
    Sinh N câu hook theo các góc tiếp cận khác nhau.

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
        angle = ordered[i % len(ordered)]
        tpl_list = _ANGLE_TEMPLATES[angle]
        tpl = tpl_list[(seed + i) % len(tpl_list)]

        if angle == "curiosity":
            tpl = _TOPIC_CURIOSITY.get(label, tpl)
            if snippet and i == 0:
                tpl = f"“{snippet}…” {tpl}"

        options.append({
            "text": tpl.strip(),
            "angle": angle,
            "label": _ANGLE_LABELS.get(angle, angle),
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
2. "hooks": MỘT DANH SÁCH 3 câu hook, mỗi câu 12-26 từ. Mỗi câu theo một góc tiếp cận KHÁC NHAU: một câu gây tò mò, một câu chất thảo luận, một câu chạm cảm xúc. Bắt đầu mỗi câu bằng thông tin cụ thể từ bài viết.
3. "intro": lời dẫn giới thiệu bài viết (1-2 câu, tối đa 30 từ).
4. "transition": lời dẫn chuyển sang phần bình luận (1 câu, tối đa 15 từ).
5. "editorial": nhận xét biên tập ngắn về bài viết (1-2 câu, tối đa 30 từ) - nêu điều đáng chú ý nhất.
6. "cta": câu kết khuyến khích thảo luận (1 câu, 8-18 từ, CÓ emoji, KHÔNG quá 2 emoji).

QUY TẮC BẮT BUỘC:
- Trung thành bài đăng gốc. KHÔNG bịa thêm sự kiện, con số, nhân vật hay trích dẫn.
- KHÔNG thay đổi ý nghĩa của bình luận.
- Tiếng Việt tự nhiên, đúng chính tả. KHÔNG dùng Markdown. KHÔNG ghi nhãn "hook:" hay "cta:" trong giá trị.
- Nếu nội dung gốc là tiếng Trung Quốc hoặc tiếng Anh, hãy viết lại bằng tiếng Việt đúng nghĩa.
- Không nội dung nhạy cảm, không kỳ thị, không nhạo đùa công kích cá nhân.
- Nếu nội dung thiếu ngữ cảnh, hãy giữ hook ngắn gọn, không bịa.

Trả về JSON đúng format:
{"summary": "...", "hooks": ["...", "...", "..."], "intro": "...", "transition": "...", "editorial": "...", "cta": "..."}"""

_GEMINI_MODEL = "gemini-2.5-flash"


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
    resp = await asyncio.to_thread(
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
                    "intro": {"type": "string"},
                    "transition": {"type": "string"},
                    "editorial": {"type": "string"},
                    "cta": {"type": "string"},
                },
                "required": ["summary", "hooks", "intro", "transition", "editorial", "cta"],
            },
        ),
    )

    data = json.loads(resp.text)

    hooks_raw = data.get("hooks") or []
    if isinstance(hooks_raw, str):
        hooks_raw = [hooks_raw]
    hooks = []
    for i, h in enumerate(hooks_raw[:5]):
        ht = (h or "").strip()
        if not ht:
            continue
        angle = HOOK_ANGLES[i % len(HOOK_ANGLES)]
        hooks.append({
            "text": ht,
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
