"""
MODULE PHÂN TÍCH NỘI DUNG & LỌC BÌNH LUẬN THÔNG MINH.

Giải quyết các lỗi trong đề xuất nâng cấp:
  - Lỗi A: bình luận trùng lặp (trùng tuyệt đối + gần giống).
  - Lỗi B: không có cơ chế chấm điểm bình luận.
  - Nhóm 1 (AI phân tích): nhận diện chủ đề, phân loại, đánh giá hấp dẫn.

Nguyên tắc:
  - KHÔNG bịa thêm nội dung, KHÔNG sửa ý nghĩa bình luận gốc.
  - Chỉ LỌC / CHẤM ĐIỂM / SẮP XẾP, người dùng vẫn có thể chọn tay.
  - Không phụ thuộc API key -> chạy offline hoàn toàn.

Trọng số chấm điểm (có thể chỉnh qua WEIGHTS):
    độ thú vị 25% | gây thảo luận 25% | hài/bất ngờ 20% | liên quan 20% | ngắn gọn 10%
"""
import re
import math
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Trọng số chấm điểm bình luận
# ---------------------------------------------------------------------------
WEIGHTS: Dict[str, float] = {
    "interesting": 0.25,   # độ thú vị
    "discussion": 0.25,    # khả năng gây thảo luận
    "humor": 0.20,         # hài hước / bất ngờ
    "relevance": 0.20,     # liên quan bài đăng
    "concise": 0.10,       # dễ hiểu, ngắn gọn
}

# Tên các trọng số hợp lệ (dùng để validate input từ API)
WEIGHT_KEYS = tuple(WEIGHTS.keys())


class WeightError(ValueError):
    """Trọng số không hợp lệ."""


def validate_weights(weights: Optional[Dict[str, float]]) -> Dict[str, float]:
    """
    Kiểm tra trọng số chấm điểm do người dùng truyền vào.

    Quy tắc:
      - Không truyền gì -> trả về WEIGHTS mặc định (giữ nguyên hành vi cũ).
      - Phải có ĐÚNG các khoá hợp lệ (không thiếu, không thừa).
      - Mỗi giá trị phải là số thực trong [0, 1].
      - Không được âm.
      - Tổng phải > 0 (không được tất cả bằng 0).
      - Tổng được chuẩn hoá về 1.0 để điểm luôn nằm trong [0, 1].

    Raise WeightError nếu không hợp lệ.
    """
    if weights is None:
        return dict(WEIGHTS)

    if not isinstance(weights, dict):
        raise WeightError("Trọng số phải là object (dict).")

    missing = [k for k in WEIGHT_KEYS if k not in weights]
    extra = [k for k in weights if k not in WEIGHT_KEYS]
    if missing:
        raise WeightError(f"Thiếu trọng số: {', '.join(missing)}.")
    if extra:
        raise WeightError(f"Trọng số không hợp lệ: {', '.join(extra)}.")

    clean: Dict[str, float] = {}
    for k in WEIGHT_KEYS:
        v = weights[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise WeightError(f"Trọng số '{k}' phải là số.")
        fv = float(v)
        # ĐỢT 1.1 (fix H1): NaN / +Inf / -Inf phải bị từ chối.
        # - `fv < 0` và `fv > 1` đều False với NaN -> NaN lọt qua và lan
        #   vào điểm chấm -> API trả JSON `NaN` (không hợp lệ RFC 8259).
        if not math.isfinite(fv):
            raise WeightError(
                f"Trọng số '{k}' phải là số hữu hạn (không được NaN/Infinity)."
            )
        if fv < 0:
            raise WeightError(f"Trọng số '{k}' không được âm.")
        if fv > 1:
            raise WeightError(f"Trọng số '{k}' không được vượt quá 1.")
        clean[k] = fv

    total = sum(clean.values())
    if total <= 0:
        raise WeightError("Tổng trọng số phải lớn hơn 0.")

    # Chuẩn hoá về tổng 1.0 -> điểm luôn trong [0, 1]
    return {k: round(v / total, 6) for k, v in clean.items()}

# Ngưỡng coi là trùng (Jaccard trên tập từ khóa)
DUP_JACCARD = 0.82
# Ngưỡng coi là gần giống (độ tương đồng chuỗi)
NEAR_DUP_RATIO = 0.86

MAX_COMMENT_CHARS = 420
MIN_COMMENT_CHARS = 12


# ---------------------------------------------------------------------------
# Chuẩn hoá văn bản
# ---------------------------------------------------------------------------
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF←-⇿☀-➿]"
)
_URL_RE = re.compile(r"(https?://\S+|www\.\S+|\S+\.(com|net|vn|shop|shopify|link)\S*)")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s.\-]{7,}\d)")
_TAG_RE = re.compile(r"[@#][\w.]+")
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def strip_emoji(text: str) -> str:
    """Bỏ emoji (không bỏ dấu tiếng Việt)."""
    if not text:
        return ""
    return _EMOJI_RE.sub(" ", text)


def deaccent(text: str) -> str:
    """
    Bỏ dấu tiếng Việt: "tôi" -> "toi", "không" -> "khong".

    Dùng để so khớp 2 chiều (dấu / không dấu) mà không cần viết 2 bản
    danh sách từ dừng.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFD", text)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", t).lower()


def normalize_text(text: str) -> str:
    """
    Chuẩn hoá để so khớp: bỏ emoji, hạ chữ thường, bỏ dấu câu,
    gộp khoảng trắng, bỏ URL/hashtag.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    t = strip_emoji(t)
    t = _URL_RE.sub(" ", t)
    t = t.lower()
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


def tokens(text: str) -> List[str]:
    """Danh sách từ sau khi chuẩn hoá (giữ nguyên dấu câu để dùng cho đếm từ)."""
    t = strip_emoji(text or "").lower()
    t = _URL_RE.sub(" ", t)
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return [w for w in t.split() if w]


def keyword_set(text: str) -> set:
    """
    Tập từ khóa dùng cho đo độ tương đồng.

    Từ dừng được so khớp ở dạng KHÔNG DẤU, nên không vấn đề bài viết
    dùng "tôi" hay "toi" đều bị loại đúng.
    """
    out = set()
    for w in tokens(text):
        if len(w) <= 1:
            continue
        if deaccent(w) in STOPWORDS:
            continue
        out.add(w)
    return out


def count_words(text: str) -> int:
    return len(tokens(text))


# ---------------------------------------------------------------------------
# ĐỢT 1: Parse lượt thích từ dữ liệu thô
# ---------------------------------------------------------------------------
_LIKES_SUFFIX_RE = re.compile(
    r"^([0-9]+(?:[.,][0-9]+)?)\s*([KMBkmb])?", re.UNICODE
)


def parse_likes(raw: Any) -> int:
    """
    Chuyển giá trị likes thô thành số nguyên.

    Hỗ trợ: 12, "12", "1.2K", "1,2K", "3.3M", "87 lượt thích", "1.2K likes".

    QUY TẮC (Đợt 1):
      - Nguồn KHÔNG cung cấp likes -> 0 (KHÔNG tự tạo số).
      - Giá trị không parse được -> 0 (an toàn cho pipeline).
      - Số âm -> 0.
    """
    if raw is None:
        return 0
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, (int, float)):
        try:
            return max(0, int(raw))
        except (TypeError, ValueError, OverflowError):
            return 0

    s = str(raw).strip()
    if not s:
        return 0
    m = _LIKES_SUFFIX_RE.match(s)
    if not m:
        return 0
    try:
        num = float(m.group(1).replace(",", "."))
    except (TypeError, ValueError):
        return 0
    suffix = (m.group(2) or "").upper()
    mult = {"K": 1000.0, "M": 1000000.0, "B": 1000000000.0}.get(suffix, 1.0)
    try:
        return max(0, int(num * mult))
    except (TypeError, ValueError, OverflowError):
        return 0


# ---------------------------------------------------------------------------
# Từ dừng tiếng Việt (KHÔNG DẤU - so khớp qua deaccent())
# ---------------------------------------------------------------------------
STOPWORDS = {
    # đại từ & liên từ
    "toi", "ta", "ban", "anh", "chi", "em", "chung", "no", "minh", "con",
    "nguoi", "nguoi", "ai", "gi", "gi", "do", "day", "kia", "nay", "luc",
    # trợ động từ
    "va", "la", "co", "khong", "mot", "nhung", "cho", "de", "cua", "voi",
    "cung", "thi", "roi", "bay", "gia", "nua", "hay", "bi", "deu", "ngay",
    "tu", "den", "trong", "tren", "duoi", "ra", "vao", "boi", "vi", "ma",
    "nen", "neu", "the", "nhu", "ve", "tai", "di", "them", "con", "khi",
    "moi", "rat", "biet", "se", "cac", "sau", "truoc", "sao", "nao", "ba",
    "gio", "thoi", "loi", "viec", "nam", "khac", "nua", "roi", "nen",
    # động từ thường dùng
    "bi", "co", "khong", "muon", "can", "phai", "nen", "hay", "se",
    "noi", "ca", "lam", "thay", "nen", "chua", "roi", "thi", "kinh",
    "nghe", "thay", "nhin", "thich", "ghet", "yêu", "yêu",
    # số từ & lượng từ
    "mot", "hai", "ba", "bon", "nam", "muoi", "nhieu", "it", "nhieu hon",
    # dấu câu đứng riêng (do tokenize tách ra)
    "a", "o", "u", "i", "y", "e",
}


# ---------------------------------------------------------------------------
# Spam / quảng cáo / vô nghĩa
#
# QUY TẮC: chỉ loại khi có DẤU HIỆU RÕ (cụm quảng cáo + ngữ cảnh liên hệ
# / thanh toán), KHÔNG loại từ đơn. Nếu không, bình luận bình thường như
# "giá nhà tăng quá" hay "mình mua được cái nhà rồi vui lắm" sẽ bị mất oan.
# ---------------------------------------------------------------------------
# Cụm quảng cáo - đủ dài để không trùng từ thường
_SPAM_PHRASES = [
    # mua bán trực tiếp + liên hệ / đặt hàng
    r"(mua|ban|order|dat hang|ship)\s+(o|shop|hang|ngay|luon|ve|re|qua)\b.{0,20}"
    r"\b(shop|inbox|ib|khach hang|so dt|number|contact|lien he|hotline|zalo)\b",
    r"\b(shopee|lazada|tiktok shop|temu|amazon|aliexpress|shein)\b",
    r"\b(discount|discount code|ma giam gia|code giam|free ship|ship free)\b",
    r"\b(khuyen mai|chay hang|flash sale|black friday|dang giam gia)\b",
    # lời rao quảng cáo rõ ràng
    r"\b(shop em|shop moi|shop day|shop online|like +follow|follow +like|lk +fl)\b",
    r"\b(link (in bio|trong bio)|in bio|them zalo|add zalo|nhan so dien thoai)\b",
    # liên hệ / inbox trực tiếp -> dấu hiệu quảng cáo rõ
    r"\b(khach hang|nguoi mua|nguoi can|ban mua)\b.{0,25}\b(inbox|ib|zalo|lien he|hotline|sdt)\b",
    r"\b(inbox|add zalo|them zalo)\s+(em|toi|anh|chi|ban|minh)\b",
    # lừa đảo / đầu tư
    r"\b(kiem tien online|da cap|mlm|crypto|forex|bot trading|signal trading)\b",
    r"\b(rut tien|nap tien|dau tu tien|huynh dong quang cao)\b",
    # tuyển dụng quảng cáo
    r"\b(tuyen dung|tuyển dụng|recruit|ung vien|apply ngay)\b.{0,30}\b(luon|ngay|luong)\b",
]

# Chỉ là từ đơn -> KHÔNG đủ để loại, dùng để cộng điểm nghi ngờ
_SPAM_SOFT_WORDS = {
    "shop", "mua", "ban", "sale", "inbox", "zalo", "lienhe", "hang",
    "giamgia", "khuyenmai", "dat_hang", "ship",
}

_SPAM_RE = re.compile("|".join(_SPAM_PHRASES), re.IGNORECASE)
_SPAM_SOFT_RE = re.compile(
    r"\b(" + "|".join(_SPAM_SOFT_WORDS) + r")\b", re.IGNORECASE
)

_NONSENSE_RE = re.compile(r"^[\W\d_]+$")  # chỉ ký tự đặc biệt / số


def spam_reasons(text: str) -> List[str]:
    """Trả về danh sách lý do bị loại (rỗng = OK)."""
    reasons: List[str] = []
    t = text or ""
    low = t.lower()

    if _URL_RE.search(t):
        reasons.append("chứa liên kết")
    if _PHONE_RE.search(t):
        reasons.append("chứa số điện thoại")
    if _SPAM_RE.search(low):
        reasons.append("quảng cáo / spam")
    if _TAG_RE.search(t) and count_words(t) <= 4:
        reasons.append("chỉ có hashtag/mention")
    if _NONSENSE_RE.match(t.strip()):
        reasons.append("không có nội dung đọc được")
    if len(t.strip()) < MIN_COMMENT_CHARS:
        reasons.append("quá ngắn")
    if len(t) > MAX_COMMENT_CHARS:
        reasons.append("quá dài")
    # Quá nhiều chữ in hoa liên tiếp -> shout
    letters = [c for c in t if c.isalpha()]
    if len(letters) >= 12:
        caps_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        if caps_ratio > 0.7:
            reasons.append("toàn chữ in hoa")
    return reasons


def is_spam(text: str) -> bool:
    return bool(spam_reasons(text))


# ---------------------------------------------------------------------------
# Đo độ tương đồng
# ---------------------------------------------------------------------------
def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / float(len(a | b))


def similarity(a: str, b: str) -> float:
    """
    Độ tương đồng 0..1 giữa 2 chuỗi bình luận.
    Kết hợp Jaccard (từ khóa) + SequenceMatcher (thứ tự từ).
    """
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ja = jaccard(keyword_set(na), keyword_set(nb))
    seq = SequenceMatcher(None, na, nb).ratio()
    return max(ja, seq * 0.95)


def is_duplicate(a: str, b: str) -> bool:
    """Trùng tuyệt đối hoặc gần giống."""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if jaccard(keyword_set(na), keyword_set(nb)) >= DUP_JACCARD:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= NEAR_DUP_RATIO


# ---------------------------------------------------------------------------
# Từ điểm tín hiệu cho chấm điểm
# ---------------------------------------------------------------------------
_HUMOR_WORDS = {
    "haha", "lol", "vcl", "vl", "vãi", "kms", "thề", "té", "hjx", "kkk",
    "ahaha", "hehe", "😆", "😂", "🤣", "😭", "😹", "vui", "cười", "hài",
    "lối", "fail", " cringe", "cringe", "meme", "lolz",
}
_SURPRISE_WORDS = {
    "không ngờ", "thật sự", "hóa ra", "hoá ra", "gì vậy", "trời ơi", "wtf",
    "bất ngờ", "không thể tin", "plot twist", "hóa ra là", "thật à",
}
_OPINION_WORDS = {
    "tôi nghĩ", "tao nghĩ", "theo tôi", "theo mình", "quan điểm", "ý kiến",
    "cá nhân tôi", "tôi thấy", "mình thấy", "theo bạn", "tôi từng", "chuyện của tôi",
}
_DISCUSSION_WORDS = {
    "bạn nghĩ sao", "bạn thấy sao", "có ai", "ai cũng", "suy nghĩ", "tranh luận",
    "đồng ý", "không đồng ý", "phe", "bên nào", "còn bạn", "mọi người", "chứ không phải",
    "vậy thì", "tại sao", "vì sao", "còn tôi", "theo tôi thì", "nói thật",
}
_STORY_WORDS = {
    "tôi", "mình", "chuyện", "hôm", "năm ngoái", "năm ngoái", "hồi", "lúc",
    "khi tôi", "khi mình", "chuyện của", "mới xảy ra", "bạn biết không",
}


def _count_hits(text: str, word_set: set) -> int:
    """
    Đếm số cụm từ trong `word_set` xuất hiện trong `text`.

    QUAN TRỌNG: so khớp theo RANH GIỚI TỪ, không dùng `w in low`.
    Nếu dùng `in`, chuỗi "ex" sẽ khớp trong "next"/"text"/"context",
    "vl" sẽ khớp trong "vlog"... -> phân loại chủ đề sai hoàn toàn.
    """
    low = (text or "").lower()
    if not low:
        return 0
    hits = 0
    for w in word_set:
        if not w:
            continue
        pattern = r"(?<!\w)" + re.escape(w.lower()) + r"(?!\w)"
        if re.search(pattern, low):
            hits += 1
    return hits


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


# ---------------------------------------------------------------------------
# Chấm điểm bình luận
# ---------------------------------------------------------------------------
def score_comment(text: str, root_text: str = "", likes: int = 0,
                  weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """
    Chấm điểm 1 bình luận.

    `weights`: (tuỳ chọn) trọng số thay thế. Nếu None -> dùng WEIGHTS mặc định.
      Trọng số truyền vào PHẢI được validate trước (validate_weights).

    Trả về:
      {"total": 0..1, "parts": {...}, "reasons": [...], "words": int,
       "score_reasons": [...], "likes": int, "like_bonus": float}
    """
    w = validate_weights(weights) if weights is not None else dict(WEIGHTS)
    t = (text or "").strip()
    n_words = count_words(t)
    n_chars = len(t)
    low = t.lower()

    # --- 1. Độ thú vị (25%) ---
    # Tín hiệu: có câu hỏi, có ý kiến cá nhân, kể chuyện, độ dài vừa,
    #           có dấu "!" (khẩn), có emoji biểu cảm.
    s_interesting = 0.0
    if "?" in t:
        s_interesting += 0.22
    s_interesting += min(0.28, 0.08 * _count_hits(t, _OPINION_WORDS))
    s_interesting += min(0.24, 0.08 * _count_hits(t, _STORY_WORDS))
    if "!" in t:
        s_interesting += 0.08
    if 6 <= n_words <= 45:
        s_interesting += 0.16
    if re.search(r"[\U0001F300-\U0001FAFF]", t):
        s_interesting += 0.06
    s_interesting = _clamp01(s_interesting)

    # --- 2. Khả năng gây thảo luận (25%) ---
    s_discussion = 0.0
    s_discussion += min(0.45, 0.12 * _count_hits(t, _DISCUSSION_WORDS))
    if "?" in t:
        s_discussion += 0.25
    if n_words >= 8:
        s_discussion += 0.12
    # Có ý kiến trái ngược -> dễ gây tranh cãi
    if re.search(r"\b(không phải|quá đà|thật à|thiệt à|ý kiến khác)\b", low):
        s_discussion += 0.12
    s_discussion = _clamp01(s_discussion)

    # --- 3. Hài hước / bất ngờ (20%) ---
    s_humor = 0.0
    s_humor += min(0.55, 0.14 * _count_hits(t, _HUMOR_WORDS))
    s_humor += min(0.40, 0.13 * _count_hits(t, _SURPRISE_WORDS))
    if re.search(r"[\U0001F600-\U0001F64F]", t):
        s_humor += 0.14
    s_humor = _clamp01(s_humor)

    # --- 4. Liên quan bài đăng (20%) ---
    s_relevance = 0.0
    if root_text:
        ka = keyword_set(root_text)
        kb = keyword_set(t)
        rel = jaccard(ka, kb)
        # Chỉ tính phần chồng có ý nghĩa
        s_relevance = _clamp01(rel * 2.2)
        if kb and (ka & kb):
            s_relevance = max(s_relevance, 0.30)
    else:
        s_relevance = 0.5
    s_relevance = _clamp01(s_relevance)

    # --- 5. Ngắn gọn / dễ hiểu (10%) ---
    # Tối ưu 8..30 từ -> 1.0. Quá ngắn hoặc quá dài -> giảm.
    if 8 <= n_words <= 30:
        s_concise = 1.0
    elif n_words < 8:
        s_concise = max(0.15, n_words / 8.0)
    else:
        s_concise = max(0.25, 1.0 - (n_words - 30) / 60.0)
    s_concise = _clamp01(s_concise)

    parts = {
        "interesting": s_interesting,
        "discussion": s_discussion,
        "humor": s_humor,
        "relevance": s_relevance,
        "concise": s_concise,
    }
    total = sum(parts[k] * w[k] for k in w)

    # --- 6. Điểm cộng theo lượt thích (log-scale, tối đa +0.10) ---
    # 0 like -> +0.00; 9 like -> ~+0.05; 999 like -> +0.09; 99 999 -> +0.10
    # Dùng log để 1 bình luận viral không "giết" các bình luận còn lại.
    like_bonus = 0.0
    try:
        n_likes = int(likes or 0)
    except (TypeError, ValueError):
        n_likes = 0
    n_likes = max(0, n_likes)
    if n_likes > 0:
        like_bonus = min(0.10, 0.10 * (math.log10(n_likes + 1) / 4.0))

    total = total + like_bonus

    reasons: List[str] = []
    if parts["discussion"] >= 0.6:
        reasons.append("gây thảo luận")
    if parts["humor"] >= 0.5:
        reasons.append("hài / bất ngờ")
    if parts["interesting"] >= 0.6:
        reasons.append("có góc nhìn riêng")
    if parts["relevance"] >= 0.5:
        reasons.append("liên quan bài")
    if like_bonus >= 0.05:
        reasons.append("nhiều lượt thích")

    # --- ĐỢT 1: GIẢI THÍCH ĐIỂM CHI TIẾT (score_reasons) ---
    # Mỗi lý do là chuỗi mô tả dễ đọc cho UI, kèm mức độ mạnh.
    #
    # ĐỢT 1.1 (fix C2): TRƯỚC đây chỉ thêm tiêu chí VƯỢT ngưỡng -> các
    # tiêu chí dưới ngưỡng vẫn đóng góp vào `total` nhưng không có mặt
    # trong `score_reasons` -> tổng contribution lệch tới 0.14 so với total
    # (giải thích sai). Nay MỌI thành phần thực sự đóng góp đều được liệt kê.
    #
    # Ràng buộc: sum(contribution) == total (sai số <= 1e-4).
    score_reasons: List[Dict[str, Any]] = []

    # Ngưỡng "nổi bật" (dùng để gắn cờ is_primary) - KHÔNG dùng để lọc.
    _PRIMARY_THRESHOLDS = {
        "interesting": 0.5,
        "discussion": 0.5,
        "humor": 0.45,
        "relevance": 0.45,
        "concise": 0.6,
    }
    _WHY = {
        "interesting": "Có câu hỏi, ý kiến cá nhân hoặc kể chuyện riêng.",
        "discussion": "Có câu hỏi kích thích thảo luận hoặc ý kiến trái ngược.",
        "humor": "Có yếu tố hài hước hoặc bất ngờ trong cách diễn đạt.",
        "relevance": "Liên quan trực tiếp đến nội dung bài đăng gốc.",
        "concise": "Độ dài vừa phải để đọc nhanh trên màn hình điện thoại.",
    }
    _LABELS = {
        "interesting": "Độ thú vị",
        "discussion": "Gây thảo luận",
        "humor": "Hài hước / bất ngờ",
        "relevance": "Liên quan bài viết",
        "concise": "Ngắn gọn / dễ hiểu",
    }

    # Tổng contribution của 5 tiêu chí (chưa gồm likes bonus).
    _weighted_sum = sum(parts[k] * w[k] for k in _LABELS)

    for _k in _LABELS:
        _score = float(parts[_k])
        _contrib = float(_score) * float(w[_k])
        score_reasons.append({
            "name": _LABELS[_k],
            "key": _k,
            "label": _LABELS[_k],
            "score": round(_score, 4),
            "weight": round(float(w[_k]), 4),
            "contribution": round(_contrib, 4),
            "is_primary": bool(_score >= _PRIMARY_THRESHOLDS[_k]),
            "why": _WHY[_k],
        })

    if n_likes > 0:
        score_reasons.append({
            "name": "Lượt tương tác",
            "key": "likes",
            "label": "Lượt tương tác",
            "score": round(like_bonus, 4),
            "weight": 1.0,
            "contribution": round(like_bonus, 4),
            "is_primary": bool(like_bonus >= 0.05),
            "why": f"Nhận {n_likes} lượt thích trên bài gốc.",
        })

    # Cân bằng sai số làm tròn: đảm bảo sum(contribution) == total.
    # (Các contribution đã làm tròn 4 chữ số, total cũng vậy -> hiệu chỉnh
    # ở entry cuối cùng để tổng khớp tuyệt đối trong sai số 1e-4.)
    _total_r = round(total, 4)
    _sum_r = round(sum(r["contribution"] for r in score_reasons), 4)
    _delta = round(_total_r - _sum_r, 4)
    if abs(_delta) > 0 and score_reasons:
        _last = score_reasons[-1]
        _last["contribution"] = round(float(_last["contribution"]) + _delta, 6)
        # Giữ trường giải thích phản ánh đúng: total = _weighted_sum + like_bonus
        _last["score"] = round(float(_last["score"]), 4)
        _last["weight"] = round(float(_last["weight"]), 4)

    return {
        "total": _total_r,
        "parts": {k: round(v, 4) for k, v in parts.items()},
        "reasons": reasons,
        "score_reasons": score_reasons,
        "words": n_words,
        "chars": n_chars,
        "likes": n_likes,
        "like_bonus": round(like_bonus, 4),
    }


# ---------------------------------------------------------------------------
# Chọn bình luận đa dạng (MMR - Maximal Marginal Relevance)
# ---------------------------------------------------------------------------
def select_comments(
    comments: List[Dict[str, Any]],
    root_text: str = "",
    max_n: int = 5,
    min_score: float = 0.18,
    weights: Optional[Dict[str, float]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Lọc + xếp hạng + chọn bình luận đa dạng.

    `comments`: list dict {"text", "likes"(int|str), ...}
    `weights`: (tuỳ chọn) trọng số thay thế (đã validate trước).
    Trả về (selected, stats).
      selected: list dict {"text", "score", "parts", "reasons", "index",
                           "score_reasons", "likes", "rejection_reason"}
      stats: {"total", "spam", "duplicate", "short", "selected", "avg_score",
              "rejected": [{"index", "text", "rejection_reason"}]}
    """
    w = validate_weights(weights) if weights is not None else dict(WEIGHTS)
    stats = {
        "total": len(comments),
        "spam": 0,
        "duplicate": 0,
        "invalid": 0,
        "selected": 0,
        "avg_score": 0.0,
        "rejected": [],
    }
    if not comments:
        return [], stats

    kept: List[Dict[str, Any]] = []
    seen_norm: List[str] = []

    def _reject(idx_: int, text_: str, reason: str):
        stats["rejected"].append({
            "index": idx_,
            "text": (text_ or "")[:120],
            "rejection_reason": reason,
        })

    for idx, c in enumerate(comments):
        text = (c.get("text") or "").strip()
        if not text:
            stats["invalid"] += 1
            _reject(idx, text, "nội dung rỗng")
            continue

        reasons = spam_reasons(text)
        if reasons:
            # "quá dài" không phải lý do loại -> cắt thay vì bỏ
            hard = [r for r in reasons if r not in ("quá dài", "quá ngắn")]
            if hard:
                stats["spam"] += 1
                _reject(idx, text, f"spam: {', '.join(hard)}")
                continue
            if "quá ngắn" in reasons:
                stats["invalid"] += 1
                _reject(idx, text, "quá ngắn")
                continue
            text = text[:MAX_COMMENT_CHARS].rsplit(" ", 1)[0]

        norm = normalize_text(text)
        if any(is_duplicate(text, prev) for prev in seen_norm):
            stats["duplicate"] += 1
            _reject(idx, text, "trùng lặp nội dung đã chọn")
            continue

        likes = c.get("likes_num")
        if likes is None:
            likes = parse_likes(c.get("likes"))

        sc = score_comment(text, root_text, likes or 0, weights=w)
        kept.append({
            "index": idx,
            "text": text,
            "score": sc["total"],
            "parts": sc["parts"],
            "reasons": sc["reasons"],
            "score_reasons": sc["score_reasons"],
            "words": sc["words"],
            "likes": likes or 0,
            "rejection_reason": None,
            "item": c,
        })
        seen_norm.append(text)

    # Sắp xếp theo điểm giảm dần
    kept.sort(key=lambda x: (x["score"], x["likes"]), reverse=True)

    # Lọc theo ngưỡng điểm
    pool = [k for k in kept if k["score"] >= min_score] or kept

    # Những bình luận bị loại vì điểm thấp (nằm ngoài ngưỡng) -> ghi lý do
    if kept and pool is not kept:
        pool_ids = {id(k) for k in pool}
        for k in kept:
            if id(k) not in pool_ids:
                _reject(k["index"], k["text"],
                        f"chất lượng thấp (điểm {k['score']:.2f} < {min_score:.2f})")

    # Chọn đa dạng: mỗi lần chọn điểm cao nhất sau khi trừ phạt trùng lặp
    selected: List[Dict[str, Any]] = []
    remaining = list(pool)
    while remaining and len(selected) < max_n:
        best = None
        best_val = -1.0
        for cand in remaining:
            penalty = 0.0
            for s in selected:
                sim = similarity(cand["text"], s["text"])
                if sim > 0.5:
                    penalty = max(penalty, sim)
            val = cand["score"] * (1.0 - 0.75 * penalty)
            if val > best_val:
                best_val = val
                best = cand
        if best is None:
            break
        selected.append(best)
        remaining.remove(best)

    # Sắp lại theo thứ tự "tự nhiên": giữ nguyên thứ tự chọn (điểm cao trước)
    stats["selected"] = len(selected)
    if selected:
        stats["avg_score"] = round(sum(s["score"] for s in selected) / len(selected), 4)
    stats["scored"] = len(kept)
    stats["filtered"] = stats["spam"] + stats["duplicate"] + stats["invalid"]
    return selected, stats


# ---------------------------------------------------------------------------
# Nhận diện chủ đề & phân loại bài viết
# ---------------------------------------------------------------------------
TOPIC_CATEGORIES = [
    ("hài hước", ["haha", "vcl", "vl", "vãi", "kms", "hài", "cười", "lối", "meme",
                  "fail", "cringe", "funny", "😂", "😆", "🤣"]),
    ("tình yêu", ["yêu", "người yêu", "crush", "hẹn hò", "nyc", "ex", "chồng", "vợ",
                  "bạn trai", "bạn gái", "cặp đôi", "ly hôn", "cưới", "tình cảm"]),
    ("drama", ["drama", "cãi", "mâu thuẫn", "lục đục", "bóc phốt", "scandal", "lật mặt",
                "mất tình", "nổi", "vụ"]),
    ("tâm sự", ["tâm sự", "chia sẻ", "buồn", "cô đơn", "stress", "áp lực", "khó khăn",
                 "mệt", "giác", "suy nghĩ", "tự vấn"]),
    ("tranh luận", ["tranh luận", "suy luận", "bàn luận", "vote", "bầu", "ý kiến",
                    "quan điểm", "đồng ý", "không đồng ý", "debate"]),
    ("đời sống", ["công việc", "sếp", "lương", "tiền", "nhà", "xe", "học", "thi",
                  "đi làm", "ăn", "food", "sức khoẻ", "gym", "chạy", "mẹ", "bố",
                  "gia đình", "mẹo", "review", "kinh nghiệm"]),
]


def classify_topic(text: str) -> Tuple[str, float]:
    """
    Phân loại chủ đề bài viết.

    Trả về (nhãn, độ tin cậy 0..1).
    """
    low = (text or "").lower()
    if not low.strip():
        return "không rõ", 0.0

    scores = []
    for label, kws in TOPIC_CATEGORIES:
        hits = _count_hits(low, kws)
        if hits:
            scores.append((hits, label))

    if not scores:
        return "đời sống", 0.25
    scores.sort(reverse=True)
    top_hits, label = scores[0]
    total_hits = sum(h for h, _ in scores)
    confidence = min(1.0, 0.35 + 0.2 * top_hits + 0.3 * (top_hits / max(1, total_hits)))
    return label, round(confidence, 3)


def analyze_post(text: str) -> Dict[str, Any]:
    """
    Phân tích bài viết gốc: chủ đề, độ dài, mức hấp dẫn, có câu chuyện không.
    """
    t = (text or "").strip()
    n_words = count_words(t)
    n_chars = len(t)
    topic, confidence = classify_topic(t)

    # Mức hấp dẫn (heuristic 0..1)
    appeal = 0.0
    if 20 <= n_words <= 120:
        appeal += 0.35
    elif n_words > 120:
        appeal += 0.22
    elif n_words >= 8:
        appeal += 0.18
    if "?" in t:
        appeal += 0.18
    if re.search(r"\b(nhưng|mà|thật ra|hóa ra|ai ngờ|bất ngờ)\b", t.lower()):
        appeal += 0.16
    if count_hits := _count_hits(t, _SURPRISE_WORDS):
        appeal += min(0.2, 0.1 * count_hits)
    if re.search(r"\d", t):
        appeal += 0.08
    appeal = _clamp01(appeal)

    # Có phải câu chuyện nhiều diễn biến không
    markers = ["rồi", "sau đó", "rồi sao", "kết quả", "cuối cùng", "nhưng mà",
               "hóa ra", "thì", "và rồi", "từ đó", "một ngày nọ"]
    story_hits = sum(1 for m in markers if m in t.lower())
    has_story = story_hits >= 2 and n_words >= 30

    # Thiếu ngữ cảnh: quá ngắn hoặc toàn tham chiếu không rõ
    missing_context = n_words < 8

    length_label = (
        "rất ngắn" if n_words < 10 else
        "ngắn" if n_words < 25 else
        "trung bình" if n_words < 90 else
        "dài" if n_words < 220 else
        "rất dài"
    )

    return {
        "topic": topic,
        "topic_confidence": confidence,
        "words": n_words,
        "chars": n_chars,
        "length_label": length_label,
        "appeal": round(appeal, 3),
        "appeal_label": (
            "cao" if appeal >= 0.6 else
            "khá" if appeal >= 0.38 else
            "trung bình" if appeal >= 0.2 else "thấp"
        ),
        "has_story": has_story,
        "story_markers": story_hits,
        "missing_context": missing_context,
        "has_question": "?" in t,
    }
