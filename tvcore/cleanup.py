"""Tien ich: don video cu trong outputs/ (gioi han so luong file ton tai)."""
import os
import time
from typing import List, Optional, Tuple

from .config import OUTPUTS_DIR

# Chi giu N video moi nhat trong outputs/. Video cu hon se bi xoa de
# tranh vie thu muc phinh to len vo han (1 video TikTok ~30-60MB).
MAX_OUTPUT_VIDEOS = 30


def cleanup_old_videos(max_keep: int = MAX_OUTPUT_VIDEOS,
                       keep_names: Optional[List[str]] = None) -> Tuple[int, int]:
    """
    Xoa cac video cu trong outputs/, chi giu `max_keep` video moi nhat.

    `keep_names`: danh sach ten file KHONG duoc xoa (vd video vua render).
    Tra về (so_file_da_xoa, so_mb_da_giai_phong).
    """
    if not OUTPUTS_DIR.exists():
        return 0, 0

    try:
        files = [f for f in OUTPUTS_DIR.iterdir()
                 if f.is_file() and f.suffix.lower() == ".mp4"]
    except OSError:
        return 0, 0

    if len(files) <= max_keep:
        return 0, 0

    # Moi nhat truoc
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    keep = set(keep_names or [])
    removed = 0
    freed = 0
    for f in files[max_keep:]:
        if f.name in keep:
            continue
        try:
            sz = f.stat().st_size
            f.unlink()
            removed += 1
            freed += sz
        except OSError:
            continue

    return removed, freed // (1024 * 1024)


def outputs_disk_usage_mb() -> int:
    """Tong dung luong (MB) trong thu muc outputs/."""
    if not OUTPUTS_DIR.exists():
        return 0
    total = 0
    try:
        for f in OUTPUTS_DIR.iterdir():
            if f.is_file():
                total += f.stat().st_size
    except OSError:
        pass
    return total // (1024 * 1024)
