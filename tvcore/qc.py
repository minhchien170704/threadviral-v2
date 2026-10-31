"""
MODULE KIỂM TRA CHẤT LƯỢNG TRƯỚC KHI XUẤT (Giai đoạn 4 - QC).

Kiểm tra kịch bản/trước render và hậu render:
  - Chữ bị cắt / bị tràn khung
  - Bình luận trùng lặp
  - Cảnh trống quá lâu / thời lượng quá ngắn để đọc
  - Lệch audio - hình
  - Khung hình đen/trắng bất thường
  - Nội dung nhạy cảm / không liên quan
  - Tổng thời lượng video có hợp lý không

Trả về danh sách lỗi + cảnh báo kèm thời điểm xảy ra (scene index).
"""
import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from .content import (
    is_duplicate, normalize_text, spam_reasons, count_words,
)
from .pacing import (
    MIN_READ_SECONDS, estimate_speech_seconds, min_read_seconds, scene_duration,
)

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _issue(severity: str, code: str, message: str,
           scene: Optional[int] = None, time_sec: Optional[float] = None,
           fix: str = "") -> Dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "scene": scene,
        "time_sec": round(time_sec, 2) if time_sec is not None else None,
        "fix": fix,
    }


# ---------------------------------------------------------------------------
# 1. Kiểm tra kịch bản (trước render)
# ---------------------------------------------------------------------------
def check_script(scenes: List[Dict[str, Any]], speed: str = "+20%",
                 audio_durations: Optional[List[float]] = None,
                 scene_durations: Optional[List[float]] = None) -> Dict[str, Any]:
    """
    Kiểm tra kịch bản trước khi render.

    `audio_durations`: (tuỳ chọn) thời lượng TTS thực tế theo thứ tự cảnh.
        Khi có, QC dùng số này thay vì ước lượng -> phát hiện chính xác
        tình trạng audio dài hơn thời lượng cảnh (bug Đợt 1).

    `scene_durations`: (tuỳ chọn) thời lượng cảnh ĐÃ DỰNG theo thứ tự cảnh.
        Khi có, QC so sánh trực tiếp với audio -> báo lỗi nếu cảnh ngắn
        hơn audio (tức là giọng đọc sẽ bị cắt mất).

    Trả về {"issues": [...], "summary": {...}, "ok": bool}
    """
    issues: List[Dict[str, Any]] = []
    if not scenes:
        issues.append(_issue("error", "empty_script", "Kịch bản trống!"))
        return {"issues": issues, "summary": {}, "ok": False}

    seen: List[Tuple[int, str]] = []
    total_words = 0

    for i, sc in enumerate(scenes):
        if not isinstance(sc, dict):
            sc = getattr(sc, "model_dump", lambda: dict(sc))()
        author = sc.get("author") or ""
        text = (sc.get("display_text") or "").strip()
        tts = (sc.get("tts_text") or "").strip()
        if author in ("__hook__", "__cta__"):
            continue

        # --- Chữ bị cắt / quá dài ---
        if len(text) > 2000:
            issues.append(_issue(
                "error", "text_too_long",
                f"Cảnh {i + 1}: nội dung dài {len(text)} ký tự, có thể bị cắt.",
                scene=i, fix="Bật 'Tự động chia cảnh' hoặc rút gọn bình luận.",
            ))
        elif len(text) > 600:
            issues.append(_issue(
                "warning", "text_very_long",
                f"Cảnh {i + 1}: nội dung {len(text)} ký tự, khó đọc trên màn hình điện thoại.",
                scene=i, fix="Chia nhỏ bình luận thành 2 cảnh.",
            ))

        # --- Nội dung rỗng ---
        if not text:
            issues.append(_issue(
                "error", "empty_scene",
                f"Cảnh {i + 1} không có nội dung.", scene=i,
                fix="Bỏ cảnh rỗng.",
            ))
            continue

        # --- Lời đọc lệch nội dung hiển thị ---
        if tts and tts != text:
            nt, ntt = normalize_text(text), normalize_text(tts)
            if nt and ntt and nt not in ntt and ntt not in nt:
                # Không cảnh báo nếu chỉ khác emoji
                a = re.sub(r"\s+", "", nt)
                b = re.sub(r"\s+", "", ntt)
                if a != b:
                    issues.append(_issue(
                        "warning", "tts_mismatch",
                        f"Cảnh {i + 1}: lời đọc không khớp với chữ hiển thị.",
                        scene=i, fix="Bấm 'Chuẩn hoá giọng đọc' lại.",
                    ))

        # --- Spam / quảng cáo ---
        hard_spam = [r for r in spam_reasons(text) if r not in ("quá dài", "quá ngắn")]
        if hard_spam:
            issues.append(_issue(
                "warning", "spam_comment",
                f"Cảnh {i + 1}: bình luận có dấu hiệu {', '.join(hard_spam)}.",
                scene=i, fix="Loại bình luận này khỏi kịch bản.",
            ))

        # --- Bình luận trùng lặp ---
        for j, prev_text in seen:
            if is_duplicate(text, prev_text):
                issues.append(_issue(
                    "error", "duplicate_comment",
                    f"Cảnh {i + 1} trùng nội dung với cảnh {j + 1}.",
                    scene=i, fix="Loại bỏ bình luận trùng.",
                ))
                break
        seen.append((i, text))

        # --- Thời lượng đọc ---
        n_words = count_words(text)
        total_words += n_words
        need = min_read_seconds(text)
        if n_words >= 3 and need < MIN_READ_SECONDS:
            issues.append(_issue(
                "warning", "too_short", f"Cảnh {i + 1} quá ngắn.",
                scene=i, fix="Gộp với cảnh khác.",
            ))

        # --- ĐỢT 1: AUDIO DÀI HƠN THỜI LƯỠNG CẢNH ---
        # Nếu biết thời lượng audio thật và thời lượng cảnh ngắn hơn ->
        # giọng đọc sẽ bị cắt mất. Đây là lỗi nghiêm trọng (mất lời đọc).
        if audio_durations and i < len(audio_durations):
            _aud = audio_durations[i]
            if _aud and _aud > 0:
                if scene_durations and i < len(scene_durations):
                    _scene_dur = float(scene_durations[i])
                else:
                    _scene_dur = scene_duration(text, _aud, speed)
                if _scene_dur + 0.01 < _aud:
                    issues.append(_issue(
                        "error", "audio_longer_than_scene",
                        f"Cảnh {i + 1}: audio dài {_aud:.1f}s nhưng cảnh chỉ "
                        f"{_scene_dur:.1f}s - giọng đọc sẽ bị cắt mất.",
                        scene=i,
                        fix="Bật 'Tự động chia cảnh' hoặc rút gọn nội dung.",
                    ))

    # --- Tổng thời lượng ước tính ---
    # Nếu đã có thời lượng audio thật -> dùng số đó (chính xác hơn ước lượng)
    if audio_durations:
        est_total = sum(float(d or 0) for d in audio_durations)
    else:
        est_total = sum(estimate_speech_seconds(
            (sc.get("display_text") or "") if isinstance(sc, dict) else "", speed
        ) for sc in scenes)
    est_total += 0.15 * len(scenes)

    if est_total < 15:
        issues.append(_issue(
            "warning", "video_too_short",
            f"Video ước tính chỉ {est_total:.0f}s - quá ngắn cho TikTok/Reels.",
            fix="Thêm bình luận hoặc dài lời dẫn.",
        ))
    elif est_total > 75:
        issues.append(_issue(
            "warning", "video_too_long",
            f"Video ước tính {est_total:.0f}s - có thể bị giảm tỷ lệ xem hết.",
            fix="Giảm số bình luận.",
        ))

    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]

    return {
        "issues": issues,
        "ok": len(errors) == 0,
        "summary": {
            "scenes": len(scenes),
            "words": total_words,
            "estimated_duration": round(est_total, 1),
            "errors": len(errors),
            "warnings": len(warnings),
            "duplicates": len([i for i in issues if i["code"] == "duplicate_comment"]),
            "spam": len([i for i in issues if i["code"] == "spam_comment"]),
        },
    }


# ---------------------------------------------------------------------------
# 2. Kiểm tra video đã render (hậu render)
# ---------------------------------------------------------------------------
def _probe_black_frames(video_path: str, duration: float) -> Tuple[int, List[float]]:
    """
    Dò khung hình đen/trắng bất thường bằng ffmpeg blackdetect.
    Trả về (số lần, danh sách giây bắt đầu).
    """
    from .utils import ffmpeg_bin

    if not os.path.exists(video_path):
        return 0, []
    try:
        proc = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-i", video_path,
             "-vf", "blackdetect=d=0.6:pix_th=0.10", "-an", "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=180, check=False,
        )
        out = proc.stdout.decode("utf-8", "ignore")
    except Exception:
        return 0, []

    times: List[float] = []
    for m in re.finditer(r"black_start:([0-9.]+)", out):
        try:
            times.append(float(m.group(1)))
        except ValueError:
            pass
    return len(times), times


def _probe_freeze(video_path: str) -> List[float]:
    """Dò đoạn hình đứng yên (freeze) > 2s bằng freezedetect."""
    from .utils import ffmpeg_bin

    if not os.path.exists(video_path):
        return []
    try:
        proc = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-i", video_path,
             "-vf", "freezedetect=n=0.003:d=2.0", "-an", "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=180, check=False,
        )
        out = proc.stdout.decode("utf-8", "ignore")
    except Exception:
        return []

    times: List[float] = []
    for m in re.finditer(r"freeze_start:\s*([0-9.]+)", out):
        try:
            times.append(float(m.group(1)))
        except ValueError:
            pass
    return times


def check_video(video_path: str, expected_duration: Optional[float] = None) -> Dict[str, Any]:
    """
    Kiểm tra file video sau khi render.
    """
    from .utils import ffprobe_bin, get_media_duration

    issues: List[Dict[str, Any]] = []

    if not os.path.exists(video_path):
        issues.append(_issue("error", "file_missing", "Không tìm thấy file video."))
        return {"issues": issues, "ok": False, "summary": {}}

    try:
        size = os.path.getsize(video_path)
    except OSError:
        size = 0
    if size < 50_000:
        issues.append(_issue(
            "error", "file_too_small",
            f"File video quá nhỏ ({size} bytes) - có thể render lỗi.",
        ))

    dur = get_media_duration(video_path, 0.0)
    if dur <= 0:
        issues.append(_issue("error", "no_duration", "Không đọc được thời lượng video."))
    elif expected_duration and abs(dur - expected_duration) > max(1.5, expected_duration * 0.08):
        issues.append(_issue(
            "warning", "duration_mismatch",
            f"Thời lượng video ({dur:.1f}s) lệch so với dự kiến ({expected_duration:.1f}s).",
        ))

    # Kiểm tra audio stream
    try:
        proc = subprocess.run(
            [ffprobe_bin(), "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", video_path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=False,
        )
        has_audio = bool(proc.stdout.decode("utf-8", "ignore").strip())
    except Exception:
        has_audio = True
    if not has_audio:
        issues.append(_issue("warning", "no_audio", "Video không có tiếng."))

    # Khung hình đen
    n_black, black_times = _probe_black_frames(video_path, dur)
    if n_black:
        issues.append(_issue(
            "warning", "black_frames",
            f"Phát hiện {n_black} đoạn khung hình đen bất thường "
            f"(tại {_fmt_times(black_times)}).",
            time_sec=black_times[0] if black_times else None,
            fix="Kiểm tra lại nền video hoặc cảnh chuyển tiếp.",
        ))

    # Freeze
    freezes = _probe_freeze(video_path)
    if freezes:
        issues.append(_issue(
            "info", "freeze_frames",
            f"Phát hiện {len(freezes)} đoạn hình đứng yên (tại {_fmt_times(freezes)}).",
            time_sec=freezes[0] if freezes else None,
        ))

    errors = [i for i in issues if i["severity"] == "error"]
    return {
        "issues": issues,
        "ok": len(errors) == 0,
        "summary": {
            "duration": round(dur, 2),
            "size_mb": round(size / (1024 * 1024), 2),
            "black_frames": n_black,
            "freeze_frames": len(freezes),
            "errors": len(errors),
            "warnings": len([i for i in issues if i["severity"] == "warning"]),
        },
    }


def _fmt_times(times: List[float], limit: int = 4) -> str:
    if not times:
        return "-"
    parts = [f"{t:.0f}s" for t in times[:limit]]
    if len(times) > limit:
        parts.append("...")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# 3. Kiểm tra quyền sử dụng / watermark
# ---------------------------------------------------------------------------
def check_rights(watermark_text: str, bg_source: str = "",
                 is_user_upload: bool = True) -> Dict[str, Any]:
    """
    Kiểm tra watermark và nguồn nền.
    """
    issues: List[Dict[str, Any]] = []
    if not watermark_text.strip():
        issues.append(_issue(
            "warning", "no_watermark",
            "Video không có watermark - nên thêm tên kênh để tránh bị đạo.",
            fix="Điền tên fanpage ở ô watermark.",
        ))
    if bg_source and not is_user_upload:
        issues.append(_issue(
            "info", "bg_external",
            "Nền lấy từ nguồn bên ngoài - hãy chắc chắn bạn có quyền sử dụng.",
        ))
    return {"issues": issues, "ok": True, "summary": {"watermark": bool(watermark_text.strip())}}
