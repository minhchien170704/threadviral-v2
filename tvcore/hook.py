"""
Module tự động sinh câu Hook (đầu video) và CTA (cuối video).

2 tầng:
  1. Template nội bộ  - luôn chạy, không cần API key, offline.
  2. Gemini (tuỳ chọn) - nếu có API key thì viết câu hay hơn theo nội dung thật.
     Nếu Gemini lỗi -> tự động fallback về tầng 1.

Cấu trúc output luôn gồm 2 trường: hook_text, cta_text.
"""
import asyncio
import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Từ khoá dùng để dựng câu hook theo mẫu
# ---------------------------------------------------------------------------
# ĐỢT 1: các mẫu dưới đây KHÔNG còn khẳng định mức độ lan truyền
# ("cả Threads chia thành hai phe", "đang làm mưa Threads"...) vì dữ liệu
# đầu vào không chứng minh được. Cách diễn đạt nay trung tính nhưng vẫn hấp dẫn.
_HOOK_PATTERNS = [
    # (từ khoá trong nội dung, mẫu hook)
    (("vote", "bầu", "cử", "election", "bầu cử"),
     "Câu chuyện này có một câu hỏi khiến người đọc phải chọn một phe."),
    (("drama", "cãi", "mâu thuẫn", "lục đục", "scandal", "vụ"),
     "Có một vụ drama ngoài đời được kể lại qua vài dòng trong bài này."),
    (("vợ", "chồng", "ex", "nyc", "người yêu", "cặp đôi", "ly hôn"),
     "Một câu chuyện tình cũ mà mỗi người đọc sẽ có một cách nhìn khác nhau."),
    (("công việc", "công ty", "sếp", "boss", "xin việc", "lương", "phỏng vấn"),
     "Câu chuyện này sẽ khiến bạn nghĩ lại chỗ làm của mình."),
    (("tiền", "lương", "đồng", "tiết kiệm", "nhà", "xe", "đầu tư"),
     "Một câu chuyện về tiền mà nhiều người sẽ tranh luận."),
    (("học", "thi", "exam", "điểm", "trường", "university", "deadline"),
     "Câu chuyện này chắc chắn sẽ khiến bạn nhớ lại thời còn đi học."),
    (("ăn", "food", "món", "quán", "review", "nhà hàng", "mẹo"),
     "Có một món ăn trong bài này mà ít người để ý tới."),
    (("mẹ", "bố", "ba mẹ", "family", "gia đình", "cha", "mẹ"),
     "Câu chuyện gia đình này đọc xong không ai ngủ được ngay."),
    (("sức mạnh", "gym", "tập", "running", "chạy", "yoga", "sức khoẻ"),
     "Câu chuyện này sẽ thay đổi cách bạn tập luyện mãi mãi."),
    (("check", "checkin", "ra mắt", "mở bán", "launch"),
     "Mọi người đang bàn tán về một món mới vừa xuất hiện."),
]

_CTA_TEMPLATES = [
    "Bạn nghĩ sao? Comment ý kiến bên dưới nhé! 👇",
    "Bạn đứng về phía nào? Cho tôi biết trong comment nhé! 👇",
    "Còn bạn thì sao? Kể tôi nghe ở comment bên dưới! 👇",
    "Theo bạn thì sao? Để lại bình luận của bạn nhé! 👇",
    "Bạn có đồng ý không? Vote ngay trong comment! 👇",
    "Thẳng thắn đi bạn! Comment xem bạn nghĩ gì nhé! 👇",
    "Chuyện này bạn thấy sao? Comment bên dưới đi! 👇",
    "Ai cũng có quan điểm riêng. Bạn thuộc phe nào? 👇",
    "Nếu là bạn, bạn sẽ chọn gì? Comment nhé! 👇",
    "Bình luận bên dưới để xem mọi người nghĩ gì nha! 👇",
    "Bạn có trải qua chuyện tương tự chưa? Kể lại đi! 👇",
    "Sự thật là gì theo bạn? Comment để cùng biết! 👇",
]

# Câu hook dựng từ nội dung topic (fallback khi không khớp mẫu nào)
# ĐỢT 1: bỏ các claim bịa viral ("Threads tranh luận không hồi kết",
# "gây tranh cãi dữ dội", "xôn xao") -> nay trung tính, dựa trên nội dung.
_GENERIC_HOOKS = [
    "Câu chuyện này được kể lại qua vài dòng nhưng để lại một câu hỏi.",
    "Đọc xong bài này, bạn sẽ không khỏi bình luận.",
    "Một câu chuyện trên Threads kết thúc bằng một câu hỏi mở.",
    "Bài viết này có một chi tiết mà mình đọc xong phải dừng lại.",
]

# ---------------------------------------------------------------------------
# Tiện ích
# ---------------------------------------------------------------------------
def _strip_emoji(text: str) -> str:
    if not text:
        return ""
    return re.sub(
        r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿☀-➿]",
        "", text,
    ).strip()


def _first_sentences(text: str, max_chars: int = 140) -> str:
    """Lấy phần đầu nội dung, cắt gọn ở câu/dấu phẩy để dùng làm hook."""
    t = _strip_emoji((text or "").replace("\n", " ").strip())
    if not t:
        return ""
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    # Cắt ở dấu câu gần nhất trong 40 ký tự cuối
    for sep in (". ", "! ", "? ", ", ", "… "):
        idx = cut.rfind(sep)
        if idx > max_chars * 0.4:
            return cut[:idx].strip()
    return cut.strip()


def _topic_from_scenes(scenes: List[Dict[str, Any]]) -> str:
    """Lấy nội dung bài gốc (scene đầu tiên)."""
    if not scenes:
        return ""
    s = scenes[0]
    if not isinstance(s, dict):
        s = getattr(s, "model_dump", lambda: dict(s))()
    return (s.get("display_text") or "").strip()


def _author_from_scenes(scenes: List[Dict[str, Any]]) -> str:
    if not scenes:
        return ""
    s = scenes[0]
    if not isinstance(s, dict):
        s = getattr(s, "model_dump", lambda: dict(s))()
    return (s.get("author") or "").strip()


def _comment_count(scenes: List[Dict[str, Any]]) -> int:
    return max(0, len(scenes) - 1)


# ---------------------------------------------------------------------------
# Tầng 1: template nội bộ
# ---------------------------------------------------------------------------
def generate_hook_cta_template(scenes: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Sinh hook + CTA bằng quy tắc nội bộ (không cần mạng, không cần API key).
    """
    topic = _topic_from_scenes(scenes)
    author = _author_from_scenes(scenes)
    n_comments = _comment_count(scenes)

    low = topic.lower()

    hook = ""
    for keywords, template in _HOOK_PATTERNS:
        if any(k in low for k in keywords):
            hook = template
            break

    if not hook:
        # Thử dựng hook từ chính nội dung topic
        snippet = _first_sentences(topic, 90)
        if snippet:
            hook = f"“{snippet}…” Câu chuyện này được kể lại qua vài dòng."
        else:
            hook = _GENERIC_HOOKS[0]

    # Bổ sung ngữ cảnh số lượng bình luận (chỉ khi THẬT SỰ có dữ liệu)
    if n_comments >= 5 and "bình luận" not in hook.lower():
        hook = f"{hook} Chỉ riêng dưới bài đã có {n_comments} bình luận."

    # Chọn CTA theo hash nội dung -> đa dạng, không bị lặp giữa các video
    seed = int(hashlib.md5(topic.encode("utf-8")).hexdigest()[:8], 16)
    cta = _CTA_TEMPLATES[seed % len(_CTA_TEMPLATES)]

    # ĐỢT 1: chống claim viral không có căn cứ (lớp bảo vệ cuối)
    hook = _sanitize_viral_claim(hook, topic)

    return {
        "hook_text": hook.strip(),
        "cta_text": cta.strip(),
        "source": "template",
    }


def _sanitize_viral_claim(hook: str, topic: str = "") -> str:
    """
    Loại bỏ các cụm từ khẳng định mức độ lan truyền KHÔNG có căn cứ.

    ĐỢT 1 - spec §3.2: không được tự bịa các câu kiểu
    "Cả Threads đang phát sốt", "Hàng nghìn người tranh cãi",
    "Cả mạng xã hội chia thành hai phe", "Bài viết gây bão"...

    Cách xử lý: thay cụm bịa bằng cách diễn đạt trung tính tương đương.
    """
    from .script import sanitize_viral_claim

    return sanitize_viral_claim(hook)


# ---------------------------------------------------------------------------
# Tầng 2: Gemini
# ---------------------------------------------------------------------------
_GEMINI_PROMPT = """Bạn là copywriter chuyên viết kịch bản video ngắn cho Threads (mạng xã hội của Meta), đọc là video 9:16 dựng bằng AI, có giọng đọc TTS tiếng Việt.

NHIỆM VỤ: Dựa trên nội dung bài viết gốc và các bình luận bên dưới, hãy viết:
1. `hook`: Câu mở đầu đầu video, 1 câu duy nhất, dài 12-28 từ. Phải GÂY TÒ MÒ, có xung đột hoặc điểm đáng bàn, khiến người xem phải xem tiếp. Có thể dùng dấu "…" hoặc "—" để tạo nhịp.
2. `cta`: Câu kết thúc video, 1 câu duy nhất, dài 8-18 từ. Lời kêu gọi bình luận cụ thể (hỏi người xem nghĩ gì về một khía cạnh trong nội dung), CÓ emoji, KHÔNG dùng emoji quá 2.

QUY TẮC:
- Viết bằng tiếng Việt tự nhiên, đúng chính tả, có dấu câu.
- KHÔNG dùng Markdown, KHÔNG giải thích, KHÔNG ghi "hook:" hay "cta:" trong giá trị.
- KHÔNG bịa thêm sự kiện, con số hay tên người không có trong nội dung.
- TUYỆT ĐỐI KHÔNG khẳng định mức độ lan truyền khi dữ liệu không chứng minh được.
  KHÔNG dùng: "cả Threads đang phát sốt", "hàng nghìn người tranh cãi",
  "cả mạng xã hội chia thành hai phe", "bài viết gây bão", "ai cũng phải bật cười",
  "Threads xôn xao", "làm mưa Threads", "viral", "lên top".
  Dùng cách diễn đạt trung tính nhưng vẫn hấp dẫn, ví dụ:
  "Một câu hỏi đơn giản nhưng phần bình luận lại có hai luồng ý kiến trái ngược."
  (chỉ dùng khi dữ liệu thực sự có hai luồng ý kiến).
- Nếu nội dung là tiếng Trung Quốc, hãy viết lại câu hook/CTA bằng tiếng Việt theo đúng nghĩa.
- Giữ giọng điệu hài hước, dí dỏm kiểu mạng xã hội nhưng không quá hỗn loạn.

Trả về JSON đúng format: {"hook": "...", "cta": "..."}"""

_GEMINI_TIMEOUT_SECONDS = 60


async def generate_hook_cta_gemini(
    scenes: List[Dict[str, Any]],
    api_key: str,
) -> Dict[str, str]:
    """Sinh hook + CTA bằng Gemini. Raise nếu lỗi để caller fallback."""
    from google import genai
    from google.genai import types

    key = (api_key or "").strip()
    if not key:
        raise ValueError("Thiếu Gemini API key")

    topic = _topic_from_scenes(scenes)
    author = _author_from_scenes(scenes)

    comments = []
    for s in scenes[1:6]:
        if not isinstance(s, dict):
            s = getattr(s, "model_dump", lambda: dict(s))()
        txt = (s.get("display_text") or "").strip()
        if txt:
            comments.append(f"- @{s.get('author', '')}: {txt[:300]}")

    payload = (
        f"BÀI VIẾT GỐC (tác giả @{author}):\n{topic[:800]}\n\n"
        f"BÌNH LUẬN NỔI BẬT:\n{chr(10).join(comments) if comments else '(chưa có bình luận)'}\n"
    )

    client = genai.Client(api_key=key)
    try:
        resp = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model="gemini-2.5-flash",
                contents=payload,
                config=types.GenerateContentConfig(
                    system_instruction=_GEMINI_PROMPT,
                    temperature=0.85,
                    response_mime_type="application/json",
                    response_schema={
                        "type": "object",
                        "properties": {
                            "hook": {"type": "string"},
                            "cta": {"type": "string"},
                        },
                        "required": ["hook", "cta"],
                    },
                ),
            ),
            timeout=_GEMINI_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(f"Gemini timeout sau {_GEMINI_TIMEOUT_SECONDS}s")

    raw_text = (getattr(resp, "text", "") or "").strip()
    if not raw_text:
        raise ValueError("Gemini trả về nội dung rỗng")
    try:
        data = json.loads(raw_text)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Gemini trả về JSON không hợp lệ: {e}")
    if not isinstance(data, dict):
        raise ValueError("Gemini trả về JSON không phải object")

    hook = str(data.get("hook") or "").strip()
    cta = str(data.get("cta") or "").strip()
    if not hook or not cta:
        raise ValueError("Gemini trả về nội dung rỗng")

    return {"hook_text": hook, "cta_text": cta, "source": "gemini"}


# ---------------------------------------------------------------------------
# Hàm chính: tự động fallback 2 tầng
# ---------------------------------------------------------------------------
async def auto_generate_hook_cta(
    scenes: List[Dict[str, Any]],
    api_key: Optional[str] = None,
) -> Dict[str, str]:
    """
    Sinh hook + CTA. Ưu tiên Gemini nếu có key và không lỗi,
    nếu không thì dùng template nội bộ.
    """
    base = generate_hook_cta_template(scenes)

    key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if not key:
        return base

    try:
        result = await generate_hook_cta_gemini(scenes, key)
        # Gemini có thể trả về câu quá dài -> cắt bớt cho hợp lý
        result["hook_text"] = _first_sentences(result["hook_text"], 180) or base["hook_text"]
        result["cta_text"] = _first_sentences(result["cta_text"], 120) or base["cta_text"]
        # ĐỢT 1: chống claim viral không có căn cứ (kể cả khi do Gemini sinh)
        result["hook_text"] = _sanitize_viral_claim(result["hook_text"])
        return result
    except Exception as e:
        print(f"[AutoHook] Gemini loi, dung template noi bo: {e}")
        base["source"] = "template (gemini fallback)"
        return base
