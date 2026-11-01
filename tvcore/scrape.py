"""
Module cào dữ liệu Threads bằng Playwright.

Tách riêng khỏi server.py, bổ sung:
  - Retry + fallback nhiều selector (Threads hay đổi DOM).
  - Cache kết quả scrape trong bộ nhớ (tránh cào lại cùng 1 link).
"""
import re
from typing import Any, Dict, List

from playwright.async_api import async_playwright

from .content import select_comments
from .images import dedupe_images, is_allowed_image_url, pick_best_srcset
from .tts import local_slang_to_tts

# ---------------------------------------------------------------------------
# Hằng số parse
# ---------------------------------------------------------------------------
IGNORED_HEADER_WORDS = {
    "đã ghim", "tác giả", "theo dõi", "follow", "following", "pinned", "author",
    "·", "•", "được tài trợ", "sponsored",
    "đang trả lời", "replying to", "trả lời", "xem thêm", "see more", "more",
    "dịch", "translate", "giao diện", "báo cáo", "report",
}
STAT_REGEX = re.compile(r"^[0-9]+(?:[.,][0-9]+)?\s*[KMBkmb]?$")
# "87K lượt" / "1,2K lượt thích" / "3 lượt"
STAT_WITH_SUFFIX_REGEX = re.compile(
    r"^[0-9]+(?:[.,][0-9]+)?\s*[KMBkmb]?\s*(?:lượt|like|likes|thích|replies|trả lời)?$",
    re.IGNORECASE,
)
# Link chia sẻ mạng xã hội cần loại bỏ
SHARE_URL_REGEX = re.compile(
    r"(?:https?://)?(?:www\.)?(?:facebook\.com|fb\.me|instagram\.com|t\.me|twitter\.com|x\.com)/\S+",
    re.IGNORECASE,
)
TIME_REGEX = re.compile(
    r"^\d+\s*(?:giờ|ngày|phút|giây|h|d|m|s|tuần|tháng|năm)(?:\s*trước)?$",
    re.IGNORECASE,
)
# Ngày tháng dạng 19/10/2026 hoặc 19-10-2026
DATE_REGEX = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")
# "Đang trả lời @user" / "Replying to @user"
REPLYING_REGEX = re.compile(
    r"^(?:đang\s+trả\s+lời|replying\s+to|trả\s+lời)\s+@?[\w.]+",
    re.IGNORECASE,
)

# Selector dự phòng (Threads hay đổi DOM -> thử lần lượt)
_CONTAINER_SELECTORS = [
    'div[data-pressable-container="true"]',
    "div[data-pressable-container]",
    'div[role="article"]',
]

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


# ---------------------------------------------------------------------------
# Parse helper
# ---------------------------------------------------------------------------
def parse_stat_number(s: str) -> float:
    """
    '5K' -> 5000, '1.2K' -> 1200, '2M' -> 2000000.
    Threads hay ghi kèm hậu tố: '87K lượt', '1,2K likes', '3.3K lượt thích'.
    """
    try:
        s = (s or "").strip()
        # Cắt mọi hậu tố chữ (lượt, lượt thích, likes, replies...)
        s = re.split(r"[\s,]+", s)[0]
        s = s.replace(",", ".")
        if s.endswith(("K", "k")):
            return float(s[:-1]) * 1000
        if s.endswith(("M", "m")):
            return float(s[:-1]) * 1_000_000
        if s.endswith(("B", "b")):
            return float(s[:-1]) * 1_000_000_000
        return float(s)
    except Exception:
        return 0.0


def parse_threads_container(lines: List[str], default_author: str = "",
                           default_time: str = "1 ngày") -> Dict[str, Any]:
    """Phân tích các dòng văn bản thô từ container Threads thành thông tin chuẩn."""
    if not lines:
        return {"author": default_author or "user", "time_ago": default_time, "text": "",
                "likes": "0", "comments": "0", "reposts": "0", "shares": "0"}

    stats = []
    idx = len(lines) - 1
    while idx >= 0 and STAT_WITH_SUFFIX_REGEX.match(lines[idx].strip()):
        stats.insert(0, lines[idx].strip())
        idx -= 1

    content_lines = list(lines[:idx + 1])

    def _is_header(line: str) -> bool:
        """Dòng này là header/metadata (không phải nội dung bình luận)?"""
        low = line.lower().strip()
        if low in IGNORED_HEADER_WORDS:
            return True
        if DATE_REGEX.match(line.strip()):
            return True
        if REPLYING_REGEX.match(line.strip()):
            return True
        return False

    while content_lines and _is_header(content_lines[0]):
        content_lines.pop(0)

    author = default_author
    if content_lines:
        first = content_lines[0].strip()
        if not TIME_REGEX.match(first) and not DATE_REGEX.match(first) \
                and len(first.split()) == 1:
            author = content_lines.pop(0)

    time_ago = default_time
    while content_lines and (TIME_REGEX.match(content_lines[0].strip())
                             or DATE_REGEX.match(content_lines[0].strip())):
        w = content_lines.pop(0).strip()
        if TIME_REGEX.match(w) and time_ago == default_time:
            time_ago = w

    while content_lines and _is_header(content_lines[0]):
        content_lines.pop(0)

    # Loại bỏ mọi dòng metadata còn sót ở giữa nội dung
    content_lines = [l for l in content_lines if not _is_header(l)]

    # Dọn tiền tố metadata dính ở đầu dòng: "18/10/2026 Đang trả lời @x Nội dung..."
    cleaned = []
    for line in content_lines:
        s = line.strip()
        # Bỏ link chia sẻ mạng xã hội (facebook.com/share…, instagram…)
        s = SHARE_URL_REGEX.sub("", s).strip()
        changed = True
        while changed:
            changed = False
            s2 = DATE_REGEX.sub("", s).strip()
            s3 = REPLYING_REGEX.sub("", s2).strip()
            if s3 != s:
                s = s3
                changed = True
        if s:
            cleaned.append(s)

    text = "\n".join(cleaned).strip()

    # Chuẩn hoá số liệu: bỏ hậu tố chữ ("87K lượt" -> "87K")
    def _clean_stat(v: str) -> str:
        return re.split(r"[\s]+", (v or "0").strip())[0] or "0"

    return {
        "author": author or default_author or "user",
        "time_ago": time_ago,
        "text": text,
        "likes": _clean_stat(stats[0]) if len(stats) > 0 else "0",
        "comments": _clean_stat(stats[1]) if len(stats) > 1 else "0",
        "reposts": _clean_stat(stats[2]) if len(stats) > 2 else "0",
        "shares": _clean_stat(stats[3]) if len(stats) > 3 else "0",
    }


# ---------------------------------------------------------------------------
# PHASE 1.2: Trích xuất ảnh bài viết
# ---------------------------------------------------------------------------
def _extract_image_urls(raw_images: List[Dict[str, Any]]) -> List[str]:
    """
    Chuyển danh sách ảnh thô từ DOM thành list URL ảnh hợp lệ.

    - Ưu tiên biến thể LỚN NHẤT trong `srcset` (render 9:16 không vỡ nét).
    - Chỉ giữ URL từ host được allowlist (chống SSRF / nguồn lạ).
    - Loại trùng nhưng GIỮ NGUYÊN THỨ TỰ gốc của bài viết.
    - KHÔNG tự tạo/đoán URL.
    """
    out: List[str] = []
    for item in raw_images or []:
        if not isinstance(item, dict):
            continue
        url = pick_best_srcset(item.get("srcset"))
        if not url:
            src = (item.get("src") or "").strip()
            url = src if is_allowed_image_url(src) else ""
        if not url:
            continue
        out.append(url)
    return dedupe_images(out)


# ---------------------------------------------------------------------------
# Cache trong bộ nhớ
# ---------------------------------------------------------------------------
_SCRAPE_CACHE: Dict[str, Any] = {}


def clear_scrape_cache() -> int:
    global _SCRAPE_CACHE
    n = len(_SCRAPE_CACHE)
    _SCRAPE_CACHE = {}
    return n


# ---------------------------------------------------------------------------
# Browser launch
# ---------------------------------------------------------------------------
async def _launch_browser(p):
    """Thử lần lượt Edge -> Chrome -> Chromium mặc định."""
    errors = []
    for opt in (
        {"channel": "msedge", "headless": True,
         "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]},
        {"channel": "chrome", "headless": True,
         "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]},
        {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]},
    ):
        try:
            return await p.chromium.launch(**opt), None
        except Exception as e:
            errors.append(str(e))
    return None, (errors[-1] if errors else "Không tìm thấy Chromium hoặc Edge")


# ---------------------------------------------------------------------------
# Scrape bài viết
# ---------------------------------------------------------------------------
async def scrape_threads_post(url: str, max_comments: int = 7) -> List[dict]:
    """Cào bài viết gốc + bình luận từ Threads. Trả về list dict scene."""
    clean_url = url.strip()
    if not clean_url.startswith("http"):
        clean_url = "https://" + clean_url
    clean_url = clean_url.split("?")[0]

    if "threads.net" not in clean_url and "threads.com" not in clean_url:
        raise ValueError("URL không phải liên kết Threads hợp lệ "
                         "(cần chứa threads.net hoặc threads.com).")

    # Cache
    cache_key = f"{clean_url}|{max_comments}"
    cached = _SCRAPE_CACHE.get(cache_key)
    if cached:
        return [dict(s) for s in cached["scenes"]]

    async with async_playwright() as p:
        browser, err = await _launch_browser(p)
        if browser is None:
            raise RuntimeError(f"Lỗi khởi chạy trình duyệt: {err}")

        try:
            context = await browser.new_context(
                user_agent=_UA,
                viewport={"width": 1280, "height": 900},
                locale="vi-VN",
            )
            page = await context.new_page()
            # Chặn media nặng để tăng tốc tải trang
            await page.route(
                "**/*.{mp4,webm,ogg,wav,mp3,gif,woff,woff2,otf,ttf}",
                lambda r: r.abort(),
            )

            await page.goto(clean_url, wait_until="domcontentloaded", timeout=30000)

            # Thử lần lượt các selector dự phòng
            for sel in _CONTAINER_SELECTORS:
                try:
                    await page.wait_for_selector(sel, timeout=4000)
                    break
                except Exception:
                    continue

            # Cuộn để kích hoạt tải thêm bình luận
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 900)")
                await page.wait_for_timeout(800)

            extracted = await page.evaluate('''() => {
                const sels = [
                    'div[data-pressable-container="true"]',
                    "div[data-pressable-container]",
                    'div[role="article"]'
                ];
                let containers = [];
                for (const s of sels) {
                    containers = Array.from(document.querySelectorAll(s));
                    if (containers.length) break;
                }

                // Threads nest reply chain: container cha chua nhieu container con.
                // innerText cua container cha se gop text cua TAT CA cac reply,
                // gay ra hien tuong "ten user + noi dung cua user khac" bi tron.
                // => Lay innerText that cua container cha, roi cat bo phan
                //    thuoc ve cac container con (xoa tu sau len truoc).
                const isTopLevel = (c) => !containers.some(
                    other => other !== c && other.contains(c)
                );
                const leaves = containers.filter(isTopLevel);

                const ownText = (c) => {
                    let text = c.innerText || '';
                    // Cac container con truc tiep (theo cap)
                    const kids = Array.from(c.querySelectorAll(
                        'div[data-pressable-container="true"], div[data-pressable-container], div[role="article"]'
                    )).filter(k => !k.querySelector(
                        'div[data-pressable-container="true"], div[data-pressable-container], div[role="article"]'
                    ));
                    // Xoa tu sau len truoc de text con da duoc lam sach khi cat khoi cha
                    kids.sort((a, b) => b.innerText.length - a.innerText.length);
                    for (const k of kids) {
                        const kt = (k.innerText || '').trim();
                        if (kt) text = text.replace(kt, '');
                    }
                    return text.split('\\n').map(s => s.trim()).filter(Boolean);
                };

                return leaves.map((c, idx) => {
                    const authorLink = c.querySelector('a[href*="/@"]');
                    let author = "";
                    if (authorLink) {
                        const href = authorLink.getAttribute('href') || "";
                        const m = href.match(/@([a-zA-Z0-9._]+)/);
                        author = m ? m[1] : authorLink.innerText.trim();
                    }
                    let avatarUrl = "";
                    for (const img of c.querySelectorAll('img')) {
                        const src = img.getAttribute('src') || "";
                        if (src.includes('fbcdn.net') || src.includes('cdninstagram.com')) {
                            avatarUrl = src; break;
                        }
                    }
                    // --- PHASE 1.2: ANH BAI VIET ---
                    // Anh bai viet cua Threads nam trong the <picture>.
                    // KHONG nam trong <picture>: avatar (alt="...profile picture"),
                    // poster/thumbnail cua video, anh bia nho.
                    // Thu thu ANH THEO THU TU DOM (carousel ngang -> dung thu tu
                    // goc cua bai viet).
                    const images = [];
                    const seenSrc = new Set();
                    for (const pic of c.querySelectorAll('picture')) {
                        const im = pic.querySelector('img');
                        if (!im) continue;
                        const alt = (im.getAttribute('alt') || '').toLowerCase();
                        if (alt.includes('profile picture')) continue;
                        const raw = im.getAttribute('src') || '';
                        const srcset = im.getAttribute('srcset') || '';
                        const key = (im.currentSrc || raw || '').split('?')[0];
                        if (!key || seenSrc.has(key)) continue;
                        seenSrc.add(key);
                        images.push({
                            src: raw,
                            srcset: srcset,
                            w: im.naturalWidth || 0,
                            h: im.naturalHeight || 0,
                        });
                    }
                    const timeEl = c.querySelector('time');
                    const timeAgo = timeEl ? (timeEl.innerText.trim() || timeEl.getAttribute('datetime')) : "";
                    // Badge "Da xac minh" (tick xanh) cua Threads.
                    // Luu y: badge inline 12x12 dung attribute title="Đã xác minh",
                    // con badge 25x25 dung aria-label="Đã xác minh" -> canh ca 2.
                    let isVerified = false;
                    for (const s of c.querySelectorAll('svg')) {
                        const tEl = s.querySelector('title');
                        const lb = (s.getAttribute('aria-label')
                            || s.getAttribute('title')
                            || (tEl ? (tEl.textContent || '') : '')) || '';
                        if (/xác minh|verified/i.test(lb)) { isVerified = true; break; }
                    }
                    const lines = ownText(c);
                    return { idx, author, avatarUrl, timeAgo, isVerified, lines, images };
                });
            }''')

            if not extracted:
                raise ValueError(
                    "Không tìm thấy dữ liệu bài viết trên trang Threads này "
                    "(có thể bài bị xoá hoặc để chế độ riêng tư)."
                )

            parsed_items = []
            for item in extracted:
                p_item = parse_threads_container(
                    item["lines"],
                    default_author=item["author"],
                    default_time=item["timeAgo"] or "1 ngày",
                )
                if not p_item["text"]:
                    continue
                p_item["avatar_url"] = item["avatarUrl"]
                p_item["is_verified"] = bool(item.get("isVerified"))
                p_item["raw_lines"] = item["lines"]
                # PHASE 1.2: ảnh bài viết (giữ thứ tự gốc)
                p_item["images"] = _extract_image_urls(item.get("images") or [])
                parsed_items.append(p_item)

            if not parsed_items:
                raise ValueError("Không trích xuất được nội dung bài viết từ trang Threads.")

            root_post = parsed_items[0]
            root_author = root_post["author"]

            scenes: List[dict] = [{
                "author": root_post["author"],
                "time_ago": root_post["time_ago"],
                "display_text": root_post["text"],
                "tts_text": local_slang_to_tts(root_post["text"]),
                "avatar_url": root_post["avatar_url"],
                "is_verified": root_post.get("is_verified", False),
                "likes": root_post["likes"],
                "comments": root_post["comments"],
                "reposts": root_post["reposts"],
                "shares": root_post["shares"],
                "author_liked": True,
                "meme_file": "",
                "reply_display_text": None,
                "reply_tts_text": None,
                "reply_author": None,
                # PHASE 1.2: ảnh bài viết (list URL, thứ tự gốc)
                "images": list(root_post.get("images") or []),
            }]

            # ------------------------------------------------------------------
            # Lọc & chấm điểm bình luận (module content.py)
            #   - Loại spam / quảng cáo / vô nghĩa
            #   - Loại bình luận trùng tuyệt đối & gần giống
            #   - Chấm điểm 5 tiêu chí (thú vị / thảo luận / hài / liên quan / ngắn gọn)
            #   - Chọn N bình luận ĐA DẠNG (MMR) - không lấy cứ 5 bình luận đầu
            # ------------------------------------------------------------------
            root_text = root_post.get("text") or ""

            regular: List[Dict[str, Any]] = []
            author_replies: List[Dict[str, Any]] = []
            for pos, it in enumerate(parsed_items[1:]):
                lines_lower = [l.lower() for l in it.get("raw_lines", [])]
                is_author_reply = ("tác giả" in lines_lower) or (it["author"] == root_author)
                text = (it.get("text") or "").strip()
                if not text:
                    continue

                rec = {
                    "pos": pos,
                    "item": it,
                    "text": text,
                    "likes_num": parse_stat_number(it.get("likes") or "0"),
                    "is_author_reply": is_author_reply,
                }
                if is_author_reply:
                    author_replies.append(rec)
                else:
                    regular.append(rec)

            # Chấm điểm & chọn bình luận đa dạng
            selected, cstats = select_comments(
                regular, root_text=root_text, max_n=max_comments,
            )
            selected_positions = {s["item"]["pos"] for s in selected}

            # Ghép reply của tác giả vào bình luận ngay trước nó
            # (reply luôn đi kèm bình luận mà nó trả lời).
            reply_by_pos: Dict[int, Dict[str, Any]] = {}
            for rep in author_replies:
                reply_by_pos[rep["pos"]] = rep

            # Sắp xếp lại theo thứ tự gốc trên Threads (tự nhiên, không nhảy)
            kept_positions = sorted(selected_positions)

            for pos in kept_positions:
                it = parsed_items[1 + pos]
                scenes.append({
                    "author": it["author"],
                    "time_ago": it["time_ago"],
                    "display_text": it["text"],
                    "tts_text": local_slang_to_tts(it["text"]),
                    "avatar_url": it["avatar_url"],
                    "is_verified": it.get("is_verified", False),
                    "likes": it["likes"],
                    "comments": it["comments"],
                    "reposts": it["reposts"],
                    "shares": it["shares"],
                    "author_liked": True,
                    "meme_file": "",
                    "reply_display_text": None,
                    "reply_tts_text": None,
                    "reply_author": None,
                    # PHASE 1.2: ảnh bài viết (list URL, thứ tự gốc)
                    "images": list(it.get("images") or []),
                })

                # Gắn reply của tác giả (nếu có) vào cảnh này
                rep = reply_by_pos.get(pos + 1)
                if rep and rep["text"].strip():
                    scenes[-1]["reply_author"] = rep["item"]["author"]
                    scenes[-1]["reply_display_text"] = rep["text"]
                    scenes[-1]["reply_tts_text"] = local_slang_to_tts(rep["text"])

            _SCRAPE_CACHE[cache_key] = {"scenes": scenes}
            return [dict(s) for s in scenes]
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Discover bài viết hot
# ---------------------------------------------------------------------------
DEFAULT_KEYWORDS = [
    "drama", "tâm sự", "cuộc sống", "chia sẻ", "bóc phốt", "hẹn hò", "việt nam",
    "câu chuyện", "tranh luận", "review", "kinh nghiệm", "cảm xúc", "hài hước",
    "sự thật", "góc nhìn",
]

VIET_RE = re.compile(r"[\u00C0-\u024F\u1E00-\u1EFF\u0110\u0111]", re.IGNORECASE)
MIN_LIKES = 1000
MIN_COMMENTS = 50

JS_EXTRACT = """() => {
    const containers = Array.from(document.querySelectorAll('div[data-pressable-container="true"]'));
    const posts = [];
    const statRe = /^[0-9,.]+[KMBkmb]?$/;

    // Chi giu container cap cao (khong chua container con) de tranh gop reply chain
    const isTopLevel = (c) => !containers.some(other => other !== c && other.contains(c));

    const ownText = (c) => {
        let text = c.innerText || '';
        const kids = Array.from(c.querySelectorAll('div[data-pressable-container="true"]'))
            .filter(k => !k.querySelector('div[data-pressable-container="true"]'));
        kids.sort((a, b) => b.innerText.length - a.innerText.length);
        for (const k of kids) {
            const kt = (k.innerText || '').trim();
            if (kt) text = text.replace(kt, '');
        }
        return text.split('\\n').map(s => s.trim()).filter(Boolean);
    };

    for (const c of containers.filter(isTopLevel)) {
        const postLink = c.querySelector('a[href*="/post/"]');
        if (!postLink) continue;
        const href = postLink.getAttribute('href') || '';
        if (!href.includes('/post/')) continue;
        const url = href.startsWith('http') ? href : 'https://www.threads.net' + href;

        const authorLink = c.querySelector('a[href*="/@"]');
        let author = '';
        if (authorLink) {
            const ahref = authorLink.getAttribute('href') || '';
            const m = ahref.match(/@([a-zA-Z0-9._]+)/);
            author = m ? m[1] : authorLink.innerText.trim();
        }

        let avatarUrl = '';
        for (const img of c.querySelectorAll('img')) {
            const src = img.getAttribute('src') || '';
            if (src.includes('fbcdn.net') || src.includes('cdninstagram.com')) {
                avatarUrl = src; break;
            }
        }

        const timeEl = c.querySelector('time');
        const timeAgo = timeEl ? (timeEl.innerText.trim() || '') : '';

        const lines = ownText(c);
        const stats = [];
        for (let i = lines.length - 1; i >= 0 && statRe.test(lines[i]); i--) {
            stats.unshift(lines[i]);
        }
        const contentLines = lines.slice(0, lines.length - stats.length);
        const preview = contentLines.join(' ').trim().substring(0, 220);

        posts.push({ url, author, avatarUrl, timeAgo, preview,
                     likes: stats[0] || '0', comments: stats[1] || '0' });
    }
    return posts;
}"""


async def discover_posts(keyword: str = "", max_posts: int = 10) -> dict:
    """Tự động tìm bài viết tiếng Việt hot / tranh luận nhiều trên Threads."""
    import random as _rand
    import urllib.parse

    keyword = (keyword or "").strip()
    max_posts = max(1, min(max_posts, 30))

    if not keyword:
        sample_kws = _rand.sample(DEFAULT_KEYWORDS, min(3, len(DEFAULT_KEYWORDS)))
    else:
        sample_kws = [keyword]

    search_urls = [
        f"https://www.threads.net/search?q={urllib.parse.quote(kw)}&serp_type=default"
        for kw in sample_kws
    ]
    label_keyword = sample_kws[0]

    extracted_all = []
    async with async_playwright() as p:
        browser, _err = await _launch_browser(p)
        if browser is None:
            raise RuntimeError("Không thể khởi động trình duyệt để tìm bài viết.")

        try:
            context = await browser.new_context(
                user_agent=_UA,
                viewport={"width": 1280, "height": 900},
                locale="vi-VN",
                extra_http_headers={"Accept-Language": "vi-VN,vi;q=0.9,en;q=0.5"},
            )

            for s_url in search_urls:
                page = await context.new_page()
                await page.route(
                    "**/*.{mp4,webm,ogg,wav,mp3,gif,woff,woff2,otf,ttf}",
                    lambda r: r.abort(),
                )
                try:
                    await page.goto(s_url, wait_until="domcontentloaded", timeout=30000)
                    for sel in _CONTAINER_SELECTORS:
                        try:
                            await page.wait_for_selector(sel, timeout=6000)
                            break
                        except Exception:
                            continue
                    for _ in range(5):
                        await page.evaluate("window.scrollBy(0, 900)")
                        await page.wait_for_timeout(500)
                    extracted_all.extend(await page.evaluate(JS_EXTRACT))
                except Exception:
                    pass
                finally:
                    await page.close()
        finally:
            await browser.close()

    seen_urls = set()
    candidates = []
    for item in extracted_all:
        url = (item.get("url") or "").split("?")[0].strip()
        preview = (item.get("preview") or "").strip()
        if not url or not preview or url in seen_urls:
            continue
        if len(preview) < 10:
            continue
        if not VIET_RE.search(preview):
            continue

        likes_val = parse_stat_number(item.get("likes") or "0")
        comments_val = parse_stat_number(item.get("comments") or "0")

        if likes_val < MIN_LIKES and comments_val < MIN_COMMENTS:
            continue

        hot_score = likes_val + comments_val * 10
        seen_urls.add(url)
        candidates.append({
            "url": url,
            "author": item.get("author") or "unknown",
            "avatar_url": item.get("avatarUrl") or "",
            "time_ago": item.get("timeAgo") or "",
            "preview": preview,
            "likes": item.get("likes") or "0",
            "comments": item.get("comments") or "0",
            "likes_num": int(likes_val),
            "comments_num": int(comments_val),
            "hot_score": int(hot_score),
        })

    candidates.sort(key=lambda x: (x.get("comments_num", 0), x.get("likes_num", 0)), reverse=True)
    results = candidates[:max_posts]

    return {
        "status": "success",
        "keyword": label_keyword,
        "keywords_searched": sample_kws,
        "total": len(results),
        "posts": results,
    }
