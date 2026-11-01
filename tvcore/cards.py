"""
Module vẽ thẻ giao diện Threads (Pillow).

Tách riêng khỏi server.py, dùng chung hàm wrap_text_to_lines nên không còn
3 bản copy logic ngắt dòng như bản cũ.
"""
import os
import uuid
from io import BytesIO
from typing import List, Optional

import requests
from PIL import Image, ImageDraw, ImageFont

from .config import (
    SAFE_ZONE_BOTTOM,
    SAFE_ZONE_LEFT,
    SAFE_ZONE_RIGHT,
    SAFE_ZONE_TOP,
    TEMP_DIR,
)
from .icons import paste_icon, render_icon
from .utils import wrap_text_to_lines

# Vá lỗi MoviePy 1.0.3 không tương thích Pillow >= 10 (giữ để tương thích)
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS


# ---------------------------------------------------------------------------
# Font
# ---------------------------------------------------------------------------
def get_font(size: int, bold: bool = False):
    """Lấy font theo kích thước, ưu tiên Arial/Segoe UI trên Windows."""
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Scale theo độ phân giải
# ---------------------------------------------------------------------------
# Mọi kích thước trong module này được thiết kế cho khung hình 1080x1920.
# Khi render ở 720x1280 (hay độ phân giải khác) phải nhân lại tỉ lệ
# để card, chữ, avatar... giữ đúng tỉ lệ như bản 1080p.
BASE_VID_W = 1080
BASE_VID_H = 1920


def scale_for(vid_w: int, vid_h: int) -> float:
    """Hệ số nhân kích thước so với khung hình chuẩn 1080x1920."""
    try:
        return max(0.3, min(2.0, float(vid_w) / BASE_VID_W))
    except (TypeError, ValueError):
        return 1.0


def safe_card_width(vid_w: int, scale: float = 1.0) -> int:
    """
    Bề rộng card tối đa để nằm trong vùng an toàn của TikTok/Reels.

    Vùng an toàn = khung hình trừ đi UI mép (cột nút phải, caption dưới,
    thanh trên). Card được canh giữa theo trục ngang trong vùng an toàn
    -> không bị UI che, vẫn cân đối.
    """
    s = max(0.4, min(2.0, float(scale or 1.0)))

    def sc(v: float) -> int:
        return max(1, int(round(v * s)))

    # Vùng an toán ngang = bề rộng khung - UI hai bên
    safe_w = int(vid_w) - sc(SAFE_ZONE_RIGHT) - sc(SAFE_ZONE_LEFT)
    # Card trước đây rộng 980px (91% khung). Bỏ bớt chút cho thoáng,
    # nhưng vẫn giữ phần lớn diện tích màn hình.
    card_w = min(sc(980), safe_w)
    return max(sc(320), card_w)

def safe_card_height(vid_h: int, scale: float = 1.0) -> int:
    """
    Chiều cao card tối đa để không bị UI TikTok/Reels che từ dưới lên.

    Vùng an toàn dọc = chiều cao khung trừ UI trên + UI dưới.
    """
    s = max(0.4, min(2.0, float(scale or 1.0)))

    def sc(v: float) -> int:
        return max(1, int(round(v * s)))

    safe_h = int(vid_h) - sc(SAFE_ZONE_TOP) - sc(SAFE_ZONE_BOTTOM)
    return max(sc(300), safe_h)


def safe_overlay_x(vid_w: int, card_w: int, scale: float = 1.0) -> int:
    """
    Toạ độ X để canh card vào GIỮA VÙNG AN TOÀN (không phải giữa khung).

    Vùng an toàn ngang chạy từ SAFE_ZONE_LEFT tới (W - SAFE_ZONE_RIGHT).
    Canh giữa trong vùng này đảm bảo card không lọt vào cột nút bên phải
    của TikTok (vùng UI phủ) dù nó có lệch khỏi chính giữa khung hình.
    """
    s = max(0.4, min(2.0, float(scale or 1.0)))

    def sc(v: float) -> int:
        return max(1, int(round(v * s)))

    left = sc(SAFE_ZONE_LEFT)
    right = sc(SAFE_ZONE_RIGHT)
    area_w = int(vid_w) - left - right
    x = left + (area_w - int(card_w)) // 2
    return max(0, x)


def safe_overlay_y(vid_h: int, card_h: int, scale: float = 1.0) -> int:
    """
    Toạ độ Y để đặt card trong vùng an toàn dọc.

    Vùng an toàn dọc: từ SAFE_ZONE_TOP xuống (H - SAFE_ZONE_BOTTOM).
    Card được canh giữa TRONG vùng này -> không bị thanh tiến trình trên
    che và không bị caption/nav dưới che.
    """
    s = max(0.4, min(2.0, float(scale or 1.0)))

    def sc(v: float) -> int:
        return max(1, int(round(v * s)))

    top = sc(SAFE_ZONE_TOP)
    bottom = sc(SAFE_ZONE_BOTTOM)
    area_h = int(vid_h) - top - bottom
    y = top + (area_h - int(card_h)) // 2
    return max(0, y)


# ---------------------------------------------------------------------------
# Avatar
# ---------------------------------------------------------------------------
def fetch_avatar(url: Optional[str], size: int = 76) -> Image.Image:
    """
    Tai avatar tron (khong badge) tu URL.
    Tra ve anh RGBA size x size, bo tron, nen trong suot.
    """
    av_img = None

    if url:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Referer": "https://www.threads.net/",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            }
            r = requests.get(url, timeout=5, headers=headers)
            if r.status_code == 200:
                av_img = Image.open(BytesIO(r.content)).convert("RGBA").resize(
                    (size, size), Image.Resampling.LANCZOS
                )
        except Exception:
            av_img = None

    if av_img is None:
        # Avatar xam: hinh nguoi toi gian (nhu Threads placeholder)
        av_img = Image.new("RGBA", (size, size), (225, 228, 233, 255))
        d_av = ImageDraw.Draw(av_img)
        d_av.ellipse((size * 0.30, size * 0.18, size * 0.70, size * 0.55),
                     fill=(168, 173, 182, 255))
        d_av.pieslice((size * 0.14, size * 0.52, size * 0.86, size * 1.16),
                      180, 360, fill=(168, 173, 182, 255))

    mask = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 2, size * 2), fill=255)
    mask = mask.resize((size, size), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(av_img, (0, 0), mask)
    return out


def fetch_avatar_with_plus_badge(url: Optional[str], size: int = 76,
                                 with_badge: bool = True) -> Image.Image:
    """
    Avatar tron kem nut (+) den o goc duoi ben phai (nut Follow cua Threads).

    Threads dung nen tron DEN + dau (+) TRANG, vien trang mang ~1px.
    """
    av = fetch_avatar(url, size)
    if not with_badge:
        return av

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(av, (0, 0), av)

    badge_d = max(16, int(round(size * 0.30)))
    bx = size - badge_d + int(badge_d * 0.18)
    by = size - badge_d + int(badge_d * 0.18)
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((bx - 1, by - 1, bx + badge_d + 1, by + badge_d + 1),
                 fill=(255, 255, 255, 255))
    draw.ellipse((bx, by, bx + badge_d, by + badge_d), fill=(0, 0, 0, 255))
    cx, cy = bx + badge_d // 2, by + badge_d // 2
    arm = max(3, int(badge_d * 0.26))
    w = max(2, int(badge_d * 0.13))
    draw.line((cx - arm, cy, cx + arm, cy), fill=(255, 255, 255, 255), width=w)
    draw.line((cx, cy - arm, cx, cy + arm), fill=(255, 255, 255, 255), width=w)
    return canvas


# ---------------------------------------------------------------------------
# Thẻ bình luận
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# PHASE 1.2: Vẽ ảnh bài viết vào card
# ---------------------------------------------------------------------------
def fit_image_contain(
    img: Image.Image,
    box_w: int,
    box_h: int,
    bg: tuple = (245, 246, 248, 255),
) -> Image.Image:
    """
    Scale ảnh vào khung `box_w` x `box_h` theo kiểu CONTAIN.

    - KHÔNG kéo méo tỷ lệ (giữ nguyên aspect ratio).
    - Phần thừa được đệm bằng nền trung tính.
    - Ảnh được bo tròn nhẹ cho đẹp.
    """
    box_w = max(1, int(box_w))
    box_h = max(1, int(box_h))
    src = img.convert("RGBA")
    sw, sh = src.size
    if sw <= 0 or sh <= 0:
        return Image.new("RGBA", (box_w, box_h), bg)

    scale = min(box_w / float(sw), box_h / float(sh))
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    resized = src.resize((nw, nh), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (box_w, box_h), bg)
    ox = (box_w - nw) // 2
    oy = (box_h - nh) // 2
    canvas.paste(resized, (ox, oy), resized)
    return canvas


def _img_probe(img_path: Optional[str]) -> Optional[Image.Image]:
    """
    Kiểm tra ảnh có đọc được không (mở + giải mã).
    Trả None nếu lỗi -> caller bỏ qua ảnh (fallback an toàn).
    """
    from .images import load_local_image
    return load_local_image(img_path)


def draw_post_image(
    img_path: str,
    box_w: int,
    box_h: int,
    radius: int = 18,
) -> Optional[Image.Image]:
    """
    Vẽ 1 ảnh bài viết vào khung (contain, không méo, bo góc).

    Trả về None nếu ảnh không đọc được -> caller bỏ qua (fallback an toàn).
    """
    from .images import load_local_image

    src = load_local_image(img_path)
    if src is None:
        return None
    fitted = fit_image_contain(src, box_w, box_h)

    # Bo goc cho ảnh
    try:
        mask = Image.new("L", (box_w, box_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [(0, 0), (box_w - 1, box_h - 1)], radius=max(0, int(radius)), fill=255,
        )
        out = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        out.paste(fitted, (0, 0), mask)
        return out
    except Exception:
        return fitted


def draw_single_box(
    author: str,
    time_ago: str,
    text: str,
    avatar_url: Optional[str],
    likes: str = "",
    comments: str = "",
    reposts: str = "",
    shares: str = "",
    author_liked: bool = True,
    show_footer: bool = True,
    is_root_post: bool = False,
    scale: float = 1.0,
    vid_w: int = 1080,
    vid_h: int = 1920,
    is_verified: bool = False,
    show_reply_mark: bool = False,
    image_path: Optional[str] = None,
    image_badge: str = "",
) -> Image.Image:
    """
    Ve 1 the Threads - layout dua tren metrics do that tu app Threads that:

    - Avatar tron, nut (+) den o goc duoi ben phai (nut Follow)
    - Ten: bold, den; badge "Da xac minh" xanh lam canh ten
    - Thoi gian: xam nhat, canh phai ten
    - Icon hanh dong (like / comment / repost / share / more) DUNG SVG that
      cua Threads, khong dung emoji Unicode
    - So lieu: 13px mau xam dam (66,66,66) canh phai icon
    """
    s = max(0.4, min(2.0, float(scale or 1.0)))

    def sc(v: float) -> int:
        return max(1, int(round(v * s)))

    width = safe_card_width(vid_w, s)

    # --- Padding & kich thuoc theo chuan Threads (1080p) ---
    pad_left = sc(30)
    pad_right = sc(30)
    av_size = sc(76)
    text_x = pad_left + av_size + sc(24)
    max_text_w = width - text_x - pad_right
    max_h = safe_card_height(vid_h, s)

    body_size = sc(38) if is_root_post else sc(36)
    footer_space = sc(92) if show_footer else sc(30)

    dummy = ImageDraw.Draw(Image.new("RGBA", (10, 10)))

    # --- PHASE 1.2: KHOANG DUNG CHO ANH BAI VIET ---
    # Anh duoc dat DUOI noi dung text va TREN footer -> khong che avatar,
    # ten tai khoan hay chu. Chieu cao anh bi gioi han theo vung an toan doc.
    img_box_x = pad_left
    img_box_w = width - pad_left - pad_right
    img_box_h = 0
    if image_path:
        # Kiem tra anh co DOC DUOC khong truoc khi chua cho trong card.
        # Anh hong -> card giong het card khong anh (fallback an toan),
        # tranh khoang trong trong card.
        if _img_probe(image_path) is None:
            image_path = None
    if image_path:
        img_box_h = int(max_h * 0.42)          # toi da 42% chieu cao vung an toan
        img_box_h = max(img_box_h, sc(180))
        img_box_h = min(img_box_h, max_h)
        # Canh duoi cho footer (neu co) + vien -> anh khong tran card
        footer_space = max(footer_space, sc(78))

    # Neu card qua cao -> giam co chu cho toi khi vua vung an toan doc.
    min_body = max(sc(24), int(body_size * 0.62))
    body_lines: List[str] = []
    line_h = 0
    while True:
        f_body = get_font(body_size, bold=False)
        body_lines = wrap_text_to_lines(text, font=f_body, max_width=max_text_w,
                                        draw=dummy)
        line_h = max(sc(26), int(round(body_size * 1.42)))
        box_h = sc(128) + (len(body_lines) * line_h) + footer_space
        if img_box_h:
            box_h += img_box_h + sc(26)         # khoang cach giua text va anh
        if box_h <= max_h or body_size <= min_body:
            break
        body_size -= max(1, int(body_size * 0.08))

    # Text da o kich thuoc nho nhat ma van tran -> thu co khop ANH truoc,
    # vi anh la phan thong tin chinh va phai LUON hien thi duoc.
    if img_box_h:
        overflow = box_h - max_h
        if overflow > 0:
            new_img_h = img_box_h - overflow
            if new_img_h < sc(140):
                # Khong du cho anh -> bo anh (fallback: card chu, an toan)
                img_box_h = 0
            else:
                img_box_h = new_img_h
        box_h = sc(128) + (len(body_lines) * line_h) + footer_space
        if img_box_h:
            box_h += img_box_h + sc(26)

    f_name = get_font(sc(33), bold=True)
    f_time = get_font(sc(27), bold=False)
    f_stat = get_font(sc(29), bold=False)

    bg_color = (255, 255, 255, 255)
    img = Image.new("RGBA", (width, box_h), bg_color)
    draw = ImageDraw.Draw(img)

    # --- Avatar + nut (+) ---
    av_y = sc(28)
    av = fetch_avatar_with_plus_badge(avatar_url, size=av_size)
    img.paste(av, (pad_left, av_y), av)

    # --- Ten nguoi dung ---
    name_y = sc(34)
    name_x = text_x
    draw.text((name_x, name_y), author or "", fill=(0, 0, 0, 255), font=f_name)
    name_w = draw.textbbox((0, 0), author or "", font=f_name)[2]

    # Badge "Da xac minh" (tick xanh) - dung SVG that cua Threads
    cur_x = name_x + name_w + sc(12)
    if is_verified:
        vb_size = sc(34)
        if not paste_icon(img, "verified", cur_x, name_y + sc(2), vb_size,
                          color=(29, 155, 240, 255)):
            draw.ellipse((cur_x, name_y + sc(4), cur_x + vb_size, name_y + sc(4) + vb_size),
                         fill=(29, 155, 240, 255))
        cur_x += vb_size + sc(10)

    # Icon "reply" cua Threads (12x12 trong app that) - chi khi la reply
    if show_reply_mark:
        r_size = sc(25)
        paste_icon(img, "reply", cur_x, name_y + sc(7), r_size, color=(66, 66, 66, 255))
        cur_x += r_size + sc(10)

    # Thoi gian (xam nhat)
    draw.text((cur_x + sc(14), name_y + sc(6)), time_ago or "",
              fill=(153, 153, 153, 255), font=f_time)

    # --- Nut "..." (more) goc tren phai ---
    more_size = sc(42)
    paste_icon(img, "more", width - pad_right - more_size, name_y + sc(2),
               more_size, color=(112, 112, 112, 255))

    # --- Noi dung ---
    y_text = sc(126)
    for ln in body_lines:
        draw.text((text_x, y_text), ln, fill=(0, 0, 0, 255), font=f_body)
        y_text += line_h

    # --- PHASE 1.2: ANH BAI VIET (nam duoi text, tren footer) ---
    # Khong che avatar / ten / chu. Dung `contain` -> khong keo meo ty le.
    if img_box_h and image_path:
        _img_disp = draw_post_image(image_path, img_box_w, img_box_h,
                                    radius=sc(18))
        if _img_disp is not None:
            _img_y = y_text + sc(26)
            # Vien xam nhat quanh anh de tach khoi nen video
            draw.rounded_rectangle(
                [(img_box_x - 1, _img_y - 1),
                 (img_box_x + img_box_w, _img_y + img_box_h)],
                radius=sc(18), outline=(214, 218, 224, 255), width=max(1, sc(1)),
            )
            img.paste(_img_disp, (img_box_x, _img_y), _img_disp)

            # Nhan thu tu anh (vd "2/5") - giup nguoi xem biet anh nao dang hien
            if image_badge:
                f_badge = get_font(sc(26), bold=True)
                _bw = draw.textbbox((0, 0), image_badge, font=f_badge)[2]
                _bx = img_box_x + img_box_w - _bw - sc(28)
                _by = _img_y + sc(14)
                draw.rounded_rectangle(
                    [(_bx - sc(12), _by - sc(6)),
                     (_bx + _bw + sc(12), _by + sc(28))],
                    radius=sc(12), fill=(0, 0, 0, 165),
                )
                draw.text((_bx, _by), image_badge, fill=(255, 255, 255, 255),
                          font=f_badge)

    # --- Thanh hanh dong (footer) ---
    if show_footer:
        icon_size = sc(42)
        fy = box_h - sc(52)
        # Canh text so voi icon
        txt_dy = (icon_size - sc(29)) // 2 - sc(3)
        x_cur = text_x

        def _stat(icon_name: str, value: str, icon_color, txt_color):
            nonlocal x_cur
            if not value:
                return
            paste_icon(img, icon_name, x_cur, fy, icon_size, color=icon_color)
            tx = x_cur + icon_size + sc(10)
            draw.text((tx, fy + txt_dy), str(value), fill=txt_color, font=f_stat)
            w = draw.textbbox((0, 0), str(value), font=f_stat)[2]
            x_cur = tx + w + sc(46)

        # Threads: bai da like -> tim FULL mau do; chua like -> tim rong xam.
        if author_liked:
            _stat("heart_filled", likes, (255, 48, 64, 255), (66, 66, 66, 255))
        else:
            _stat("heart", likes, (66, 66, 66, 255), (66, 66, 66, 255))
        _stat("comment", comments, (66, 66, 66, 255), (66, 66, 66, 255))
        _stat("repost", reposts, (66, 66, 66, 255), (66, 66, 66, 255))
        _stat("share", shares, (66, 66, 66, 255), (66, 66, 66, 255))

    # Vien nhat nheo (Threads khong bo vien, nhung can tach khoi nen video)
    draw.rectangle([(0, 0), (width - 1, box_h - 1)],
                   outline=(228, 231, 236, 255), width=max(1, sc(1)))
    return img


# Card Hook
# ---------------------------------------------------------------------------
def draw_hook_card(hook_text: str, vid_w: int = 1080, vid_h: int = 1920) -> str:
    """Card Hook đầu video: nền tối gradient, chữ trắng lớn, canh giữa."""
    s = scale_for(vid_w, vid_h)

    def sc(v: float) -> int:
        return max(1, int(round(v * s)))

    card_w = int(vid_w)
    card_h = sc(520)
    img = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for y in range(card_h):
        alpha = int(200 + 40 * y / card_h)
        r = int(10 + 15 * y / card_h)
        g = int(10 + 10 * y / card_h)
        b = int(25 + 20 * y / card_h)
        draw.line([(0, y), (card_w, y)], fill=(r, g, b, min(alpha, 245)))

    draw.rectangle([(sc(60), sc(60)), (sc(68), card_h - sc(60))], fill=(220, 40, 60, 230))

    # Logo Threads (SVG that) o goc tren trai - nhan dang nguon
    logo_size = sc(52)
    paste_icon(img, "threads", sc(60), sc(56), logo_size, color=(255, 255, 255, 235))

    f_hook = get_font(sc(52), bold=True)
    # Chữ hook phải nằm trong vùng an toàn ngang (không bị cột nút bên
    # phải của TikTok che). Nền gradient vẫn phủ full-width cho đẹp.
    safe_w = safe_card_width(vid_w, s)
    left_pad = sc(90)
    right_pad = sc(70)
    max_w = max(sc(200), safe_w - left_pad - right_pad)
    dummy = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lines_h = wrap_text_to_lines(hook_text, font=f_hook, max_width=max_w, draw=dummy)

    lh = sc(70)
    y0 = (card_h - len(lines_h) * lh) // 2 - sc(10)
    for ln in lines_h:
        draw.text((left_pad + sc(2), y0 + sc(3)), ln, fill=(0, 0, 0, 180), font=f_hook)
        draw.text((left_pad, y0), ln, fill=(255, 255, 255, 255), font=f_hook)
        y0 += lh

    out_path = str(TEMP_DIR / f"hook_{uuid.uuid4().hex[:8]}.png")
    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Card CTA
# ---------------------------------------------------------------------------
def draw_cta_card(cta_text: str, vid_w: int = 1080, vid_h: int = 1920) -> str:
    """Card CTA cuối video: gradient violet, chữ trắng, icon chat."""
    s = scale_for(vid_w, vid_h)

    def sc(v: float) -> int:
        return max(1, int(round(v * s)))

    card_w = int(vid_w)
    card_h = sc(380)
    img = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for y in range(card_h):
        t = y / card_h
        r = int(80 + 40 * t)
        g = int(20 + 30 * t)
        b = int(160 + 60 * t)
        draw.line([(0, y), (card_w, y)], fill=(r, g, b, 230))

    # Đường trắng trang trí cũng giới hạn trong vùng an toàn
    _sl = sc(SAFE_ZONE_LEFT) + sc(20)
    _sr = card_w - sc(SAFE_ZONE_RIGHT) - sc(20)
    draw.rectangle([(_sl, 0), (_sr, max(1, sc(4)))], fill=(255, 255, 255, 180))

    f_icon = get_font(sc(54), bold=True)
    f_cta = get_font(sc(42), bold=True)

    # --- Vùng an toàn ngang (dùng chung với card bình luận) ---
    safe_w = safe_card_width(vid_w, s)
    safe_left = sc(SAFE_ZONE_LEFT)
    safe_right = card_w - sc(SAFE_ZONE_RIGHT)
    # Tâm vùng an toàn (dùng để canh chữ)
    safe_cx = (safe_left + safe_right) // 2

    # Icon comment (SVG that cua Threads) phai nam trong vung an toan
    paste_icon(img, "comment", safe_cx - sc(28), sc(26), sc(56),
               color=(255, 255, 255, 235))

    max_w = max(sc(200), safe_w - sc(120))
    dummy = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lines_c = wrap_text_to_lines(cta_text, font=f_cta, max_width=max_w, draw=dummy)

    lh = sc(58)
    y0 = (card_h - len(lines_c) * lh) // 2 + sc(30)
    for ln in lines_c:
        bb = dummy.textbbox((0, 0), ln, font=f_cta)
        # Canh giữa TRONG VÙNG AN TOÀN (không phải giữa khung hình)
        x0 = safe_cx - (bb[2] - bb[0]) // 2
        x0 = max(safe_left, min(x0, safe_right - (bb[2] - bb[0])))
        draw.text((x0 + sc(2), y0 + sc(2)), ln, fill=(0, 0, 0, 140), font=f_cta)
        draw.text((x0, y0), ln, fill=(255, 255, 255, 255), font=f_cta)
        y0 += lh

    out_path = str(TEMP_DIR / f"cta_{uuid.uuid4().hex[:8]}.png")
    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Card lời dẫn / nhận xét biên tập (Giai đoạn 2)
# ---------------------------------------------------------------------------
def draw_narrator_card(text: str, vid_w: int = 1080, vid_h: int = 1920,
                       variant: str = "transition") -> str:
    """
    Card cho lời dẫn chuyển cảnh & nhận xét biên tập.

    - variant="transition": nền tối, chữ trắng, gạch ngang bên trái.
    - variant="editorial"  : nền indigo mờ, chữ trắng, nhãn "BIÊN TẬP VIÊN".
    """
    s = scale_for(vid_w, vid_h)

    def sc(v: float) -> int:
        return max(1, int(round(v * s)))

    card_w = int(vid_w)
    card_h = sc(400) if variant == "transition" else sc(460)
    img = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if variant == "editorial":
        for y in range(card_h):
            t = y / card_h
            draw.line([(0, y), (card_w, y)],
                      fill=(int(22 + 18 * t), int(16 + 12 * t), int(52 + 40 * t), 238))
    else:
        for y in range(card_h):
            t = y / card_h
            draw.line([(0, y), (card_w, y)],
                      fill=(int(18 + 10 * t), int(18 + 10 * t), int(24 + 12 * t), 236))

    # Gạch nhấn bên trái (trong vùng an toàn)
    _sl = sc(SAFE_ZONE_LEFT) + sc(18)
    draw.rectangle([(_sl, sc(40)), (_sl + sc(7), card_h - sc(40))],
                   fill=(255, 90, 110, 235))

    safe_w = safe_card_width(vid_w, s)
    safe_left = sc(SAFE_ZONE_LEFT)
    safe_right = card_w - sc(SAFE_ZONE_RIGHT)
    left_pad = safe_left + sc(34)
    max_w = max(sc(220), safe_right - left_pad - sc(30))

    f_label = get_font(sc(26), bold=True)
    f_text = get_font(sc(40), bold=(variant == "editorial"))

    label = "BIÊN TẬP VIÊN" if variant == "editorial" else ""
    body_size = sc(40)
    dummy = ImageDraw.Draw(Image.new("RGBA", (10, 10)))

    # Tự thu nhỏ chữ nếu quá cao
    while True:
        f_text = get_font(body_size, bold=(variant == "editorial"))
        lines = wrap_text_to_lines(text, font=f_text, max_width=max_w, draw=dummy)
        lh = max(sc(50), int(body_size * 1.42))
        need = sc(70) + len(lines) * lh + sc(60)
        if need <= card_h or body_size <= sc(28):
            break
        body_size = max(sc(28), int(body_size * 0.92))

    y0 = sc(60)
    if label:
        draw.text((left_pad, y0), label, font=f_label, fill=(255, 160, 175, 240))
        y0 += sc(48)

    for ln in lines:
        draw.text((left_pad + sc(2), y0 + sc(2)), ln, fill=(0, 0, 0, 150), font=f_text)
        draw.text((left_pad, y0), ln, fill=(255, 255, 255, 255), font=f_text)
        y0 += lh

    out_path = str(TEMP_DIR / f"narr_{variant}_{uuid.uuid4().hex[:8]}.png")
    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------
def create_watermark_image(text: str, scale: float = 1.0) -> str:
    """Ảnh watermark chữ nghiêng trắng viền đen chống re-up."""
    if not text or not text.strip():
        # Watermark rỗng -> tạo ảnh 1x1 trong suốt (ffmpeg vẫn nhận input)
        path = str(TEMP_DIR / f"wm_{uuid.uuid4().hex[:6]}.png")
        Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(path, "PNG")
        return path

    s = max(0.4, min(2.0, float(scale or 1.0)))

    def sc(v: float) -> int:
        return max(1, int(round(v * s)))

    path = str(TEMP_DIR / f"wm_{uuid.uuid4().hex[:6]}.png")
    font = get_font(sc(30), bold=True)
    dummy = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    bbox = dummy.textbbox((0, 0), text, font=font)
    w = max(sc(120), bbox[2] - bbox[0] + sc(40))
    img = Image.new("RGBA", (w, sc(60)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((sc(15), sc(10)), text, font=font, fill=(255, 255, 255, 235),
              stroke_width=sc(3), stroke_fill=(0, 0, 0, 220))
    img.save(path, "PNG")
    return path


# ---------------------------------------------------------------------------
# Tách bài dài thành nhiều cảnh
# ---------------------------------------------------------------------------
MAX_LINES_PER_CARD = 7
CHARS_PER_LINE = 42


def wrap_like_card(text: str, is_root: bool = False, scale: float = 1.0,
                   vid_w: int = 1080) -> list:
    """
    Ngat dong GIONG HET cach draw_single_box ve (cung font, cung be rong).
    Nho vay so dong dung de tinh so canh khop chinh xac voi thu hien tren
    man hinh -> giong doc va hinh anh luon dong bo.
    """
    from .utils import wrap_text_to_lines

    s = max(0.4, min(2.0, float(scale or 1.0)))

    def sc(v: float) -> int:
        return max(1, int(round(v * s)))

    width = safe_card_width(vid_w, s)
    text_x = sc(30) + sc(76) + sc(24)
    max_text_w = width - text_x - sc(30)
    f_body = get_font(sc(38) if is_root else sc(36), bold=False)
    dummy = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    return wrap_text_to_lines(text, font=f_body, max_width=max_text_w, draw=dummy)


def split_visual_text_parts(text: str, n_parts: int, max_lines: int = MAX_LINES_PER_CARD,
                           scale: float = 1.0, vid_w: int = 1080,
                           is_root: bool = False) -> List[str]:
    """
    Chia nội dung chữ thành N phần để hiển thị nhiều visual liên tục.

    Dùng khi audio của một cảnh dài hơn ngưỡng khuyến nghị (12s):
    audio PHẢI phát liên tục, nhưng hình ảnh nên đổi để người xem không
    phải nhìn một thẻ đứng yên 27 giây.

    Ràng buộc:
      - Chia theo DÒNG (không cắt giữa từ) -> chữ luôn đọc được.
      - Mỗi phần không vượt quá `max_lines` dòng -> không tràn card.
      - Nội dung đầu vào và thứ tự được giữ nguyên (không mất từ).
      - Nếu không chia được (text quá ngắn) -> trả về [text] (1 phần).

    Trả về list các chuỗi display text (thứ tự đúng nội dung gốc).
    """
    t = (text or "").strip()
    if not t or n_parts <= 1:
        return [t] if t else [""]

    lines = wrap_like_card(t, is_root, scale, vid_w)
    if len(lines) <= max_lines:
        return [t]

    # Số phần khả thi: không vượt quá số dòng, và mỗi phần <= max_lines dòng
    n = max(1, min(int(n_parts), (len(lines) + max_lines - 1) // max_lines))

    # Chia đều theo số dòng (ceil) -> mỗi phần <= max_lines dòng
    per = (len(lines) + n - 1) // n
    parts: List[str] = []
    i = 0
    while i < len(lines):
        chunk = lines[i:i + per]
        if not chunk:
            break
        parts.append(" ".join(chunk).strip())
        i += len(chunk)

    # Gộp phần cuối nếu quá ngắn (tránh 1 dòng lẻ lủng củng)
    if len(parts) > 1 and len(parts[-1].split()) <= 2:
        parts[-2] = (parts[-2] + " " + parts[-1]).strip()
        parts.pop()

    return parts or [t]


def split_scene_if_long(scene, max_lines: int = MAX_LINES_PER_CARD, scale: float = 1.0,
                        vid_w: int = 1080) -> list:
    """
    Nếu bài viết quá nhiều dòng -> tự động chia thành nhiều scene dict.

    QUAN TRỌNG: giọng đọc được sinh LẠI từ đúng phần chữ đang hiển thị
    (local_slang_to_tts trên từng phần), chứ không cắt chuỗi TTS sẵn có theo
    tỉ lệ dòng. Cách cũ khiến chữ hiện trên màn hình và lời đọc lệch nhau
    -> người xem thấy "loạn".
    """
    from .tts import local_slang_to_tts
    from .utils import scene_to_dict

    scene = scene_to_dict(scene)

    is_root = bool(scene.get("is_root_post"))
    display_lines = wrap_like_card(scene.get("display_text") or "", is_root, scale, vid_w)
    if len(display_lines) <= max_lines:
        return [scene]

    total_parts = (len(display_lines) + max_lines - 1) // max_lines

    parts = []
    i = 0
    part_idx = 0
    while i < len(display_lines):
        chunk_display_lines = display_lines[i:i + max_lines]
        part_display = " ".join(chunk_display_lines).strip()
        part_idx += 1

        # Lời đọc = bung viết tắt của CHÍNH phần đang hiển thị -> khớp tuyệt đối
        part_tts = local_slang_to_tts(part_display)

        parts.append({
            "author": scene.get("author", ""),
            "time_ago": scene.get("time_ago", "1 ngày"),
            "display_text": part_display,
            "tts_text": part_tts,
            "avatar_url": scene.get("avatar_url"),
            "is_verified": scene.get("is_verified", False),
            "likes": scene.get("likes", "") if part_idx == 1 else "",
            "comments": scene.get("comments", "") if part_idx == 1 else "",
            "reposts": scene.get("reposts", "") if part_idx == 1 else "",
            "shares": scene.get("shares", "") if part_idx == 1 else "",
            "author_liked": scene.get("author_liked", True),
            "meme_file": scene.get("meme_file", "") if part_idx == total_parts else "",
            "reply_display_text": None,
            "reply_tts_text": None,
            "reply_author": None,
            # PHASE 1.2: giu anh bai viet khi chia scene
            "images": list(scene.get("images") or []),
        })
        i += max_lines

    return parts
