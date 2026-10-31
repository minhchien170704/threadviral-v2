"""
MODULE THỐNG KÊ & LỊCH SỬ (Nhóm 4).

Lưu lại lịch sử các lần xuất video + thống kê:
  - Tổng số bài đã xử lý / số video đã xuất
  - Thời lượng trung bình
  - Số bình luận đã lọc / tỷ lệ bình luận trùng
  - Số video bị lỗi render
  - Lưu cấu hình dựng video hiệu quả (preset)
  - Nhập số liệu TikTok Analytics để so sánh hiệu quả

Lưu trữ: JSON file trong thư mục temp/ (không cần database).
"""
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from .config import TEMP_DIR

STATS_FILE = TEMP_DIR / "stats.json"
PRESETS_FILE = TEMP_DIR / "presets.json"


def _load(path) -> Dict[str, Any]:
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save(path, data: Dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def _default_state() -> Dict[str, Any]:
    return {
        "renders": [],       # lịch sử xuất video
        "analytics": {},     # số liệu TikTok theo tên video
        "totals": {
            "scraped_posts": 0,
            "renders_total": 0,
            "renders_ok": 0,
            "renders_failed": 0,
            "comments_seen": 0,
            "comments_filtered": 0,
            "comments_duplicate": 0,
            "comments_spam": 0,
        },
    }


# ---------------------------------------------------------------------------
# Ghi lịch sử
# ---------------------------------------------------------------------------
def record_scrape(post_count: int = 0, comments_seen: int = 0,
                 comments_filtered: int = 0, comments_duplicate: int = 0,
                 comments_spam: int = 0) -> None:
    """Ghi nhận 1 lần cào dữ liệu Threads."""
    st = _load(STATS_FILE) or _default_state()
    for k in st.get("totals", {}):
        st.setdefault("totals", {}).setdefault(k, 0)
    t = st["totals"]
    t["scraped_posts"] += 1
    t["comments_seen"] += comments_seen
    t["comments_filtered"] += comments_filtered
    t["comments_duplicate"] += comments_duplicate
    t["comments_spam"] += comments_spam
    _save(STATS_FILE, st)


def record_render(filename: str, duration: float, ok: bool = True,
                  error: str = "", scenes: int = 0,
                  hook_text: str = "", cta_text: str = "",
                  voice: str = "", speed: str = "",
                  qc_errors: int = 0) -> None:
    """Ghi nhận 1 lần xuất video."""
    st = _load(STATS_FILE) or _default_state()
    st.setdefault("totals", {})
    t = st["totals"]
    t["renders_total"] = t.get("renders_total", 0) + 1
    if ok:
        t["renders_ok"] = t.get("renders_ok", 0) + 1
    else:
        t["renders_failed"] = t.get("renders_failed", 0) + 1

    st.setdefault("renders", []).insert(0, {
        "id": uuid.uuid4().hex[:8],
        "filename": filename,
        "duration": round(duration, 2),
        "ok": ok,
        "error": error,
        "scenes": scenes,
        "hook": hook_text[:200],
        "cta": cta_text[:200],
        "voice": voice,
        "speed": speed,
        "qc_errors": qc_errors,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    # Giữ tối đa 200 lịch sử
    st["renders"] = st["renders"][:200]
    _save(STATS_FILE, st)


def record_analytics(filename: str, data: Dict[str, Any]) -> bool:
    """
    Nhập số liệu TikTok Analytics cho 1 video.

    `data` ví dụ: {"views": 12000, "likes": 850, "comments": 120,
                    "shares": 45, "watch_time_avg": 18.4}
    """
    st = _load(STATS_FILE) or _default_state()
    st.setdefault("analytics", {})
    entry = dict(data or {})
    entry["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    st["analytics"][filename] = entry
    _save(STATS_FILE, st)
    return True


# ---------------------------------------------------------------------------
# Đọc thống kê
# ---------------------------------------------------------------------------
def get_stats() -> Dict[str, Any]:
    """Trả về toàn bộ thống kê + phân tích."""
    st = _load(STATS_FILE) or _default_state()
    totals = st.get("totals", {})
    renders = st.get("renders", [])
    analytics = st.get("analytics", {})

    ok_renders = [r for r in renders if r.get("ok")]
    durations = [r["duration"] for r in ok_renders if r.get("duration")]
    avg_dur = round(sum(durations) / len(durations), 1) if durations else 0.0

    seen = totals.get("comments_seen", 0)
    filtered = totals.get("comments_filtered", 0)
    dup = totals.get("comments_duplicate", 0)

    # Phân tích hiệu quả: kết hợp analytics nếu có
    perf = []
    for r in ok_renders:
        fn = r.get("filename")
        a = analytics.get(fn)
        if not a:
            continue
        views = float(a.get("views") or 0)
        eng = (float(a.get("likes") or 0) + float(a.get("comments") or 0)
               + float(a.get("shares") or 0))
        perf.append({
            "filename": fn,
            "duration": r.get("duration"),
            "hook": r.get("hook"),
            "views": int(views),
            "engagement": int(eng),
            "engagement_rate": round(eng / views * 100, 2) if views else 0,
            "avg_watch_time": a.get("watch_time_avg"),
            "hook_len": len(r.get("hook") or ""),
        })
    perf.sort(key=lambda x: x["engagement_rate"], reverse=True)

    return {
        "totals": {
            **totals,
            "avg_duration": avg_dur,
            "duration_total": round(sum(durations), 1),
        },
        "rates": {
            "duplicate_rate": round(dup / seen * 100, 1) if seen else 0.0,
            "filtered_rate": round(filtered / seen * 100, 1) if seen else 0.0,
            "success_rate": (
                round(totals.get("renders_ok", 0) / totals.get("renders_total", 1) * 100, 1)
                if totals.get("renders_total") else 0.0
            ),
        },
        "history": renders[:30],
        "performance": perf[:20],
    }


# ---------------------------------------------------------------------------
# Preset cấu hình dựng video
# ---------------------------------------------------------------------------
def save_preset(name: str, config: Dict[str, Any]) -> bool:
    """Lưu 1 preset cấu hình dựng video."""
    if not name.strip():
        return False
    presets = _load(PRESETS_FILE) or {}
    presets[name.strip()] = {
        "config": config,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return _save(PRESETS_FILE, presets)


def list_presets() -> Dict[str, Any]:
    return _load(PRESETS_FILE) or {}


def delete_preset(name: str) -> bool:
    presets = _load(PRESETS_FILE) or {}
    if name not in presets:
        return False
    del presets[name]
    return _save(PRESETS_FILE, presets)


def get_preset(name: str) -> Optional[Dict[str, Any]]:
    return (list_presets() or {}).get(name)
