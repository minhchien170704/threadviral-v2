"""
Icon Threads that - tai asset PNG da render san (assets/icons/*.png).

Viet bang PIL de doi mau (recolor) + resize -> khong can dependency moi.

VI SAO DUNG PNG THAY VI EMOJI:
- Emoji Unicode (♥ ♡ 💬 ↻ ➢) render tuy thuoc font, may khac ra khac
  hinh -> khong giong Threads va khong kiem soat duoc.
- Icon SVG that trich tu https://www.threads.net -> giong het app that.

Cach tao asset: chay `python tools/build_icons.py` (chi 1 lan, dung Chrome).
"""
import os
from typing import Dict, Optional

from PIL import Image

from .config import ASSETS_DIR, TEMP_DIR

ICON_DIR = str(ASSETS_DIR / "icons")

# Icon Threads that: heart | heart_filled | comment | repost | share
#                    more | reply | verified | threads
AVAILABLE_ICONS = (
    "heart", "heart_filled", "comment", "repost", "share",
    "more", "reply", "verified", "threads",
)

_cache: Dict[str, Image.Image] = {}


def _icon_path(name: str) -> Optional[str]:
    p = os.path.join(ICON_DIR, f"{name}.png")
    return p if os.path.exists(p) else None


def has_icon(name: str) -> bool:
    """Icon co san trong asset khong."""
    return _icon_path(name) is not None


def load_icon(name: str) -> Optional[Image.Image]:
    """Tai icon goc (den, alpha) - da cache."""
    if name in _cache:
        return _cache[name]
    p = _icon_path(name)
    if not p:
        return None
    try:
        im = Image.open(p).convert("RGBA")
    except Exception:
        return None
    _cache[name] = im
    return im


def recolor(img: Image.Image, color) -> Image.Image:
    """
    Doi mau icon goc (den) thanh mau tuy y.
    Giu nguyen alpha -> hinh dang giong nguyen ban.
    """
    if isinstance(color, tuple) and len(color) == 4:
        r, g, b, a = color
    elif isinstance(color, tuple) and len(color) == 3:
        r, g, b, a = color[0], color[1], color[2], 255
    else:
        r, g, b, a = 0, 0, 0, 255
    # Alpha goc la cua shape -> dung no lam mask
    alpha = img.split()[3]
    solid = Image.new("RGBA", img.size, (r, g, b, 255))
    solid.putalpha(alpha)
    return solid


def render_icon(name: str, size: int, color=(0, 0, 0, 255)) -> Optional[Image.Image]:
    """
    Tra ve icon RGBA kich thuoc size x size, da doi mau.
    Tra None neu asset khong co (de caller fallback ve emoji cu).
    """
    src = load_icon(name)
    if src is None:
        return None
    # Resize bang LANCZOS cho canh moi
    scaled = src.resize((int(size), int(size)), Image.Resampling.LANCZOS)
    return recolor(scaled, color)


def paste_icon(dst: Image.Image, name: str, x: int, y: int, size: int,
               color=(0, 0, 0, 255)) -> bool:
    """
    Dan icon vao image `dst` tai toa do (x, y) (canh tren-trai).
    Tra True neu dan thanh cong, False neu khong co icon.
    """
    ic = render_icon(name, size, color)
    if ic is None:
        return False
    dst.alpha_composite(ic, (int(x), int(y)))
    return True


def icon_size_for(scale: float = 1.0, base: int = 34) -> int:
    """Kich thuoc icon nhat quy theo scale video."""
    s = max(0.4, min(2.0, float(scale or 1.0)))
    return max(10, int(round(base * s)))
