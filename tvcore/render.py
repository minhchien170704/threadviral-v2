"""
Engine render video: dựng timeline -> FFmpeg filter_complex trực tiếp.

Tách riêng khỏi server.py. Bổ sung:
  - Callback progress (0.0 -> 1.0) để UI hiển thị tiến trình.
  - Chạy FFmpeg ở thread pool (không chặn event loop).
  - Kiểm tra giới hạn thời lượng & số cảnh.
"""
import asyncio
import os
import random
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, List, Optional

from .config import (
    AUDIO_EXTS, BG_EXTS, MAX_DISPLAY_CHARS, MAX_RENDER_SECONDS, MAX_SCENES,
    OUTPUTS_DIR, RESOLUTIONS, SAFE_ZONE_LEFT, SAFE_ZONE_RIGHT, TEMP_DIR, VIDEO_EXTS,
)
from PIL import Image

from .cards import (
    create_watermark_image, draw_cta_card, draw_hook_card, draw_narrator_card,
    draw_single_box, safe_card_height, safe_card_width, safe_overlay_x,
    safe_overlay_y, scale_for,
)
from .content import select_comments
from .pacing import scene_duration
from .qc import check_script, check_video
from .tts import local_slang_to_tts, make_tts_audio
from .utils import (
    ffmpeg_bin, ffprobe_bin, get_media_duration, has_audio_stream, is_image_file,
    make_silence_aac, normalize_audio_to_aac, pad_audio, scene_to_dict,
)

ProgressFn = Optional[Callable[[float, str], Any]]


# ---------------------------------------------------------------------------
# Giai đoạn 1: lọc & chọn bình luận thông minh
# ---------------------------------------------------------------------------
def _apply_smart_filter(scenes: List[dict], min_score: float = 0.18) -> List[dict]:
    """
    Loại bình luận trùng lặp / spam / dưới ngưỡng điểm, chọn bình luận đa dạng.

    Bảo toàn cảnh gốc (bài viết) và các cảnh hook/CTA.
    """
    if not scenes:
        return scenes

    head: List[dict] = []
    body: List[dict] = []
    tail: List[dict] = []
    seen_body = False
    for sc in scenes:
        author = sc.get("author") or ""
        if author in ("__hook__", "__cta__"):
            (head if author == "__hook__" else tail).append(sc)
        elif not seen_body:
            head.append(sc)      # bài viết gốc
            seen_body = True
        else:
            body.append(sc)

    if len(body) < 2:
        return scenes

    root_text = (head[-1].get("display_text") if head else "") or ""
    raw = [
        {
            "text": (sc.get("display_text") or "").strip(),
            "likes": sc.get("likes") or "0",
            "reply_display_text": sc.get("reply_display_text"),
            "reply_author": sc.get("reply_author"),
        }
        for sc in body
    ]

    selected, _stats = select_comments(
        raw, root_text=root_text, max_n=max(len(body), 1), min_score=min_score,
    )
    keep_idx = {s["index"] for s in selected}
    if not keep_idx:
        return scenes

    new_body = [body[i] for i in sorted(keep_idx)]
    return head + new_body + tail


# ---------------------------------------------------------------------------
# Giai đoạn 2: chèn lời dẫn chuyển cảnh & nhận xét biên tập
# ---------------------------------------------------------------------------
def _insert_script_scenes(
    scenes: List[dict],
    transition_text: Optional[str],
    editorial_text: Optional[str],
) -> List[dict]:
    """
    Chèn thêm:
      - "lời dẫn chuyển sang bình luận" sau bài viết gốc
      - "nhận xét biên tập" sau cảnh bình luận cuối (trước CTA)
    """
    if not scenes:
        return scenes

    def _narr(text: str) -> dict:
        return {
            "author": "__narr__", "time_ago": "", "display_text": text,
            "tts_text": text, "avatar_url": None, "likes": "", "comments": "",
            "reposts": "", "shares": "", "author_liked": False, "meme_file": "",
            "reply_display_text": None, "reply_tts_text": None, "reply_author": None,
        }

    trans = (transition_text or "").strip()
    edi = (editorial_text or "").strip()

    # Tìm vị trí bài viết gốc (sau hook nếu có)
    root_idx = 0
    for i, sc in enumerate(scenes):
        if (sc.get("author") or "") not in ("__hook__",):
            root_idx = i
            break

    if trans:
        scenes = scenes[:root_idx + 1] + [_narr(trans)] + scenes[root_idx + 1:]

    if edi:
        # Chèn trước CTA (nếu có), ngay sau cảnh bình luận cuối
        insert_at = len(scenes)
        for i in range(len(scenes) - 1, -1, -1):
            if (scenes[i].get("author") or "") == "__cta__":
                insert_at = i
                break
        scenes = scenes[:insert_at] + [_narr(edi)] + scenes[insert_at:]

    return scenes


async def _noop(pct: float, msg: str) -> None:
    pass


# ---------------------------------------------------------------------------
# Chọn nền / nhạc nền
# ---------------------------------------------------------------------------
def _resolve_media(requested: str, folder: str, exts: tuple) -> Optional[str]:
    """
    Chọn file nền/nhạc: ưu tiên yêu cầu, hỗ trợ 'random', tìm theo basename.

    BẢO MẬT: chỉ được dùng file nằm trong `folder` (hoặc thư mục con).
    Nếu nhận đường dẫn tuyệt đối/traversal (vd "..\\..\\secret.mp4") sẽ bị
    từ chối -> tránh đọc file ngoài phạm vi tool.
    """
    req = (requested or "").strip()
    candidates = []
    if os.path.isdir(folder):
        candidates = [
            os.path.join(folder, f) for f in os.listdir(folder)
            if f.lower().endswith(exts)
        ]

    if req in ("random", ""):
        return random.choice(candidates) if candidates else None

    # Chuẩn hoá đường dẫn tuyệt đối của thư mục được phép
    base = os.path.abspath(folder)
    try:
        cand = os.path.abspath(os.path.join(base, os.path.basename(req)))
        # Đường dẫn yêu cầu (nếu là tương đối) phải nằm trong `folder`
        req_abs = os.path.abspath(req)
        inside_req = (
            req_abs == base
            or req_abs.startswith(base + os.sep)
            or (not os.path.isabs(req) and req_abs.startswith(base + os.sep))
        )
        # Nếu yêu cầu là đường dẫn tuyệt đối -> chỉ chấp nhận nếu nằm trong base
        if os.path.isabs(req):
            inside_req = req_abs.startswith(base + os.sep)
    except (ValueError, OSError):
        return random.choice(candidates) if candidates else None

    if inside_req and os.path.isfile(req_abs) \
            and os.path.getsize(req_abs) > 1024 \
            and req_abs.lower().endswith(exts):
        return req_abs

    # Tìm theo basename trong thư mục được phép
    cand = os.path.join(base, os.path.basename(req))
    if os.path.isfile(cand) and os.path.getsize(cand) > 1024 \
            and cand.lower().endswith(exts):
        return os.path.abspath(cand)

    return random.choice(candidates) if candidates else None


# ---------------------------------------------------------------------------
# Render chính
# ---------------------------------------------------------------------------
async def render_video(req, progress: ProgressFn = None) -> dict:
    """
    Render video từ RenderRequest.
    Trả về {"status": "success", "video_url": ..., "duration": ...}
    """
    p = progress or _noop

    # Chuẩn hoá scenes về dict (hỗ trợ cả Pydantic model lẫn dict)
    scenes = [scene_to_dict(sc) for sc in (req.scenes or [])]
    if not scenes:
        raise ValueError("Kịch bản trống!")
    if len(scenes) > MAX_SCENES:
        raise ValueError(f"Quá nhiều cảnh (tối đa {MAX_SCENES}).")

    # =======================================================================
    # GIAI ĐOẠN 1: LỌC & CHỌN BÌNH LUẬN THÔNG MINH (nếu bật)
    #   Chạy TRƯỚC QC để QC chỉ báo lỗi thật (những lỗi mà smart
    #   filter KHÔNG tự xử lý được).
    # =======================================================================
    if getattr(req, "smart_filter", True):
        try:
            scenes = _apply_smart_filter(scenes, getattr(req, "min_comment_score", 0.18))
        except Exception as e:
            print(f"[SmartFilter] bo qua: {e}")

    # =======================================================================
    # GIAI ĐOẠN 2: THÊM LỜI DẪN / NHẬN XÉT BIÊN TẬP (nếu có)
    # =======================================================================
    scenes = _insert_script_scenes(scenes, getattr(req, "transition_text", None),
                                  getattr(req, "editorial_text", None))

    # =======================================================================
    # GIAI ĐOẠN 4: KIỂM TRA CHẤT LƯỢNG TRƯỚC KHI RENDER
    # =======================================================================
    qc_report = None
    if not getattr(req, "skip_qc", False):
        try:
            qc_report = check_script(scenes, speed=getattr(req, "speed", "+20%"))
            if not qc_report.get("ok"):
                errors = [i for i in qc_report["issues"] if i["severity"] == "error"]
                detail = "; ".join(e["message"] for e in errors[:3])
                raise ValueError(
                    f"Kiểm tra chất lượng thất bại: {detail}. "
                    "Bấm 'Xuất bỏ qua kiểm tra' nếu muốn tiếp tục."
                )
        except ValueError:
            raise
        except Exception:
            qc_report = None

    # --- Kích thước (phải tính TRƯỚC auto-split vì split cần biết scale) ---
    res_str = getattr(req, "resolution", "1080x1920") or "1080x1920"
    vid_w, vid_h = RESOLUTIONS.get(res_str, RESOLUTIONS["1080x1920"])
    card_scale = scale_for(vid_w, vid_h)

    # --- Auto-split bài dài ---
    if getattr(req, "auto_split", True):
        from .cards import split_scene_if_long
        expanded = []
        for sc in scenes:
            expanded.extend(split_scene_if_long(sc, scale=card_scale, vid_w=vid_w))
        scenes = expanded

    # --- Hook ---
    hook_text = (getattr(req, "hook_text", None) or "").strip()
    if hook_text:
        scenes.insert(0, {
            "author": "__hook__", "time_ago": "", "display_text": hook_text,
            "tts_text": hook_text, "avatar_url": None, "likes": "", "comments": "",
            "reposts": "", "shares": "", "author_liked": False, "meme_file": "",
            "reply_display_text": None, "reply_tts_text": None, "reply_author": None,
        })

    # --- CTA ---
    cta_text = (getattr(req, "cta_text", None) or "").strip()
    if cta_text:
        scenes.append({
            "author": "__cta__", "time_ago": "", "display_text": cta_text,
            "tts_text": cta_text, "avatar_url": None, "likes": "", "comments": "",
            "reposts": "", "shares": "", "author_liked": False, "meme_file": "",
            "reply_display_text": None, "reply_tts_text": None, "reply_author": None,
        })

    # --- Kích thước ---
    job_id = uuid.uuid4().hex[:8]
    temp_files: List[str] = []

    wm_img_path = create_watermark_image(
        getattr(req, "watermark_text", "") or "", scale=card_scale
    )
    temp_files.append(wm_img_path)

    try:
        # ============ BƯỚC 1: TẠO TẤT CẢ AUDIO TTS SONG SONG ============
        await _safe_progress(p, 0.05, "Đang tạo giọng đọc TTS...")
        tts_items = []
        scenes_meta = []

        for idx, sc in enumerate(scenes):
            a1_path = str(TEMP_DIR / f"{job_id}_{idx}_a1.aac")
            c1_path = str(TEMP_DIR / f"{job_id}_{idx}_c1.png")
            temp_files.extend([a1_path, c1_path])

            has_reply = bool(sc.get("reply_display_text") and sc["reply_display_text"].strip())
            tts_items.append((sc.get("tts_text") or sc.get("display_text", ""),
                              req.voice, req.speed, a1_path))

            a2_path = c2_path = None
            if has_reply:
                a2_path = str(TEMP_DIR / f"{job_id}_{idx}_a2.aac")
                c2_path = str(TEMP_DIR / f"{job_id}_{idx}_c2.png")
                temp_files.extend([a2_path, c2_path])
                tts_items.append((
                    sc.get("reply_tts_text") or local_slang_to_tts(sc["reply_display_text"]),
                    req.voice, req.speed, a2_path,
                ))

            scenes_meta.append({
                "idx": idx, "scene": sc, "has_reply": has_reply,
                "a1_path": a1_path, "c1_path": c1_path,
                "a2_path": a2_path, "c2_path": c2_path,
            })

        # Tạo TTS TUẦN TỰ (không song song).
        # edge-tts bị throttle khi có nhiều request cùng lúc -> sinh ra file
        # im lặng -> giọng đọc "loạn" và lệch với hình ảnh.
        # make_tts_audio tự đảm bảo khoảng nghỉ giữa các request.
        total_tts = max(1, len(tts_items))
        done_tts = 0

        async def _tts_one(txt, vc, sp, path):
            nonlocal done_tts
            r = await make_tts_audio(txt, vc, sp, path)
            done_tts += 1
            await _safe_progress(p, 0.05 + 0.30 * (done_tts / total_tts),
                    f"Đang tạo giọng đọc TTS... ({done_tts}/{total_tts})")
            return r

        for item in tts_items:
            await _tts_one(*item)
        await _safe_progress(p, 0.35, "Xong giọng đọc. Đang dựng thẻ...")

        # Kiểm tra file audio có thực sự chứa tiếng nói không.
        # TTS that bai se tao file im lang -> hinh anh va giong doc lech nhau.
        silent_items = []
        for meta in scenes_meta:
            for key in ("a1_path", "a2_path"):
                ap = meta.get(key)
                if not ap:
                    continue
                try:
                    sz = os.path.getsize(ap)
                except OSError:
                    sz = 0
                if sz < 1024:
                    silent_items.append((meta["idx"], key, sz))

        if silent_items:
            detail = ", ".join(f"cảnh {i}/{k} ({s}B)" for i, k, s in silent_items[:6])
            raise ValueError(
                f"Không tạo được giọng đọc cho {len(silent_items)} đoạn ({detail}). "
                "Vui lòng thử lại hoặc đổi giọng/nhịp độ."
            )

        # ============ BƯỚC 2: TẠO ẢNH THẺ & LẬP TIMELINE ============
        timeline_segments = []
        audio_segment_paths = []
        cur_time = 0.0

        # Khoảng nghỉ giữa các cảnh. Video timeline và file audio phải
        # cùng độ dài, nếu không mỗi cảnh sẽ lệch PAD giây và tích luỹ.
        SEG_PAD = 0.15

        for meta in scenes_meta:
            sc = meta["scene"]

            # --- GIAI ĐOẠN 3: THỜI LƯỜNG ĐỘNG ---
            # Thời lượng cảnh = max(đọc tối thiểu, TTS thực tế) + đệm.
            # Nếu giọng đọc ngắn hơn thời gian người xem cần để đọc -> thêm
            # im lặng (không cắt lời đọc) để hình và tiếng luôn khớp.
            raw_dur = get_media_duration(meta["a1_path"])
            target_dur = scene_duration(
                sc.get("display_text") or sc.get("tts_text") or "",
                raw_dur,
                getattr(req, "speed", "+20%"),
                is_hook=(sc.get("author") == "__hook__"),
                is_cta=(sc.get("author") == "__cta__"),
            )
            extra = max(0.0, target_dur - raw_dur)

            # Pad audio TRƯỚC khi đo -> duration đo được khớp đúng timeline
            padded_a1 = pad_audio(meta["a1_path"], meta["a1_path"] + ".pad.aac",
                                  SEG_PAD + extra)
            meta["a1_path"] = padded_a1
            temp_files.append(padded_a1)
            dur1 = get_media_duration(padded_a1)

            if sc["author"] == "__hook__":
                hook_img_path = draw_hook_card(sc["display_text"], vid_w, vid_h)
                box1_img = Image.open(hook_img_path).convert("RGBA")
                temp_files.append(hook_img_path)
            elif sc["author"] == "__cta__":
                cta_img_path = draw_cta_card(sc["display_text"], vid_w, vid_h)
                box1_img = Image.open(cta_img_path).convert("RGBA")
                temp_files.append(cta_img_path)
            elif sc["author"] == "__narr__":
                variant = "editorial" if meta["idx"] >= len(scenes_meta) - 1 else "transition"
                narr_img_path = draw_narrator_card(
                    sc["display_text"], vid_w, vid_h, variant=variant,
                )
                box1_img = Image.open(narr_img_path).convert("RGBA")
                temp_files.append(narr_img_path)
            else:
                is_root = (meta["idx"] == 0)
                if scenes and scenes[0]["author"] == "__hook__":
                    is_root = (meta["idx"] == 1)
                box1_img = draw_single_box(
                    sc["author"], sc["time_ago"], sc["display_text"], sc.get("avatar_url"),
                    sc.get("likes", ""), sc.get("comments", ""), sc.get("reposts", ""),
                    sc.get("shares", ""), sc.get("author_liked", True),
                    show_footer=(not meta["has_reply"]), is_root_post=is_root,
                    scale=card_scale, vid_w=vid_w, vid_h=vid_h,
                    is_verified=bool(sc.get("is_verified")),
                )
            box1_img.save(meta["c1_path"], "PNG")

            timeline_segments.append({
                "type": "card", "img_path": meta["c1_path"],
                "dur": dur1, "start": cur_time, "end": cur_time + dur1,
            })
            audio_segment_paths.append(meta["a1_path"])
            cur_time += dur1
            last_card_path = meta["c1_path"]

            if meta["has_reply"]:
                padded_a2 = pad_audio(meta["a2_path"], meta["a2_path"] + ".pad.aac", SEG_PAD)
                meta["a2_path"] = padded_a2
                temp_files.append(padded_a2)
                dur2 = get_media_duration(padded_a2)
                box2_img = draw_single_box(
                    sc.get("reply_author") or sc["author"], sc["time_ago"],
                    sc["reply_display_text"], sc.get("avatar_url"),
                    sc.get("likes", ""), sc.get("comments", ""),
                    sc.get("reposts", ""), sc.get("shares", ""),
                    sc.get("author_liked", True), show_footer=True,
                    scale=card_scale, vid_w=vid_w, vid_h=vid_h,
                    is_verified=bool(sc.get("is_verified")),
                    show_reply_mark=True,
                )
                stacked_h = box1_img.height + box2_img.height + 6
                # Card xếp chồng (bình luận + reply) có thể vượt vùng an toàn
                # dọc -> co lại cho vừa khung an toàn.
                max_stack_h = safe_card_height(vid_h, card_scale)
                if stacked_h > max_stack_h:
                    ratio = max_stack_h / float(stacked_h)
                    new_w = max(1, int(box1_img.width * ratio))
                    box1_img = box1_img.resize((new_w, max(1, int(box1_img.height * ratio))),
                                              Image.LANCZOS)
                    box2_img = box2_img.resize((new_w, max(1, int(box2_img.height * ratio))),
                                              Image.LANCZOS)
                    stacked_h = box1_img.height + box2_img.height + 6
                stacked_img = Image.new("RGBA", (box1_img.width, stacked_h), (0, 0, 0, 0))
                stacked_img.paste(box1_img, (0, 0))
                stacked_img.paste(box2_img, (0, box1_img.height + 6))
                stacked_img.save(meta["c2_path"], "PNG")
                last_card_path = meta["c2_path"]

                timeline_segments.append({
                    "type": "card", "img_path": meta["c2_path"],
                    "dur": dur2, "start": cur_time, "end": cur_time + dur2,
                })
                audio_segment_paths.append(meta["a2_path"])
                cur_time += dur2

            # --- Meme reaction ---
            meme_name = (sc.get("meme_file") or "").strip()
            if meme_name:
                m_path = os.path.join("memes", os.path.basename(meme_name))
                if os.path.exists(m_path) and os.path.getsize(m_path) > 100:
                    is_img = is_image_file(m_path)
                    m_dur = 2.2 if is_img else min(max(get_media_duration(m_path), 1.2), 3.5)

                    timeline_segments.append({
                        "type": "meme", "img_path": last_card_path, "meme_path": m_path,
                        "is_image": is_img, "dur": m_dur,
                        "start": cur_time, "end": cur_time + m_dur,
                    })

                    m_audio = str(TEMP_DIR / f"{job_id}_m_{len(timeline_segments)}.aac")
                    temp_files.append(m_audio)
                    if (not is_img) and has_audio_stream(m_path):
                        normalize_audio_to_aac(m_path, m_audio, duration=m_dur)
                    else:
                        make_silence_aac(m_audio, duration=m_dur)
                    audio_segment_paths.append(m_audio)
                    cur_time += m_dur

        total_dur = cur_time
        if total_dur > MAX_RENDER_SECONDS:
            raise ValueError(f"Video quá dài ({total_dur:.0f}s, tối đa {MAX_RENDER_SECONDS}s).")

        # ============ BƯỚC 3: DỰNG LỆNH FFMPEG ============
        await _safe_progress(p, 0.55, "Đang dựng timeline FFmpeg...")

        bg_input = _resolve_media(getattr(req, "bg_video_path", None), "backgrounds", BG_EXTS)
        has_bg_video = bool(bg_input)

        bgm_input = _resolve_media(getattr(req, "bgm_path", None), "bgm", AUDIO_EXTS)
        has_bgm = bool(bgm_input)

        # Gộp audio
        concat_txt = str(TEMP_DIR / f"{job_id}_audios.txt")
        temp_files.append(concat_txt)
        with open(concat_txt, "w", encoding="utf-8") as f:
            for ap in audio_segment_paths:
                clean_p = os.path.abspath(ap).replace("\\", "/")
                f.write(f"file '{clean_p}'\n")

        voice_merged = str(TEMP_DIR / f"{job_id}_voice_full.aac")
        temp_files.append(voice_merged)
        subprocess.run(
            [ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", concat_txt,
             "-c:a", "copy", voice_merged],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300,
        )

        cmd_inputs = []
        filter_parts = []
        cur_input_idx = 0

        # Input 0: Background
        if has_bg_video:
            cmd_inputs.extend(["-stream_loop", "-1", "-i", bg_input])
            _brightness = max(0.3, min(1.5, float(getattr(req, "bg_brightness", 1.0) or 1.0)))
            _blur = max(0, min(10, int(getattr(req, "bg_blur", 0) or 0)))
            _eq_b = (_brightness - 1.0) * 0.8
            bg_vf = ""
            if abs(_eq_b) > 0.02:
                bg_vf += f",eq=brightness={_eq_b:.3f}:saturation=0.92"
            if _blur > 0:
                bg_vf += f",boxblur={_blur}:{_blur}"
            # trim ve do dai video: stream_loop -1 khong bao gio EOF,
            # gay treo ffmpeg -> file MP4 khong co moov atom (hong).
            filter_parts.append(
                f"[{cur_input_idx}:v]scale={vid_w}:{vid_h}:force_original_aspect_ratio=increase,"
                f"crop={vid_w}:{vid_h},setsar=1{bg_vf},"
                f"trim=0:{total_dur:.3f},setpts=PTS-STARTPTS[v_base];"
            )
            cur_input_idx += 1
        else:
            cmd_inputs.extend(["-f", "lavfi", "-i",
                               f"color=c=0x120c18:s={vid_w}x{vid_h}:r=24:d={total_dur:.3f}"])
            filter_parts.append(f"[{cur_input_idx}:v]setsar=1[v_base];")
            cur_input_idx += 1

        last_v = "v_base"

        # Overlay từng thẻ & meme
        for s_idx, seg in enumerate(timeline_segments):
            cmd_inputs.extend(["-loop", "1", "-i", seg["img_path"]])
            v_card = cur_input_idx
            cur_input_idx += 1

            v_next = f"v_seg_{s_idx}"
            # Canh card trong VÙNG AN TOÀN (không phải giữa khung hình)
            # để không bị UI TikTok/Reels che.
            _ox = _oy = 0
            try:
                with Image.open(seg["img_path"]) as _im:
                    _cw, _ch = _im.size
                _ox = safe_overlay_x(vid_w, _cw, card_scale)
                _oy = safe_overlay_y(vid_h, _ch, card_scale)
            except Exception:
                _ox = max(0, (vid_w - card_scale * 980) // 2)
                _oy = max(0, (vid_h - card_scale * 500) // 2)

            # --- Lớp nền tối (scrim) phía sau card ---
            # Gameplay nền thường có vùng sáng (đèn, đường ray) -> gây mất
            # tập trung và khó đọc chữ. Phủ một lớp đen mờ phía sau card
            # giúp nội dung nổi bật, đồng thời làm mềm vùng chuyển tiếp.
            _scrim_pad = max(24, int(card_scale * 36))
            _scrim_x = max(0, _ox - _scrim_pad)
            _scrim_y = max(0, _oy - _scrim_pad)
            _scrim_w = min(vid_w - _scrim_x, _cw + _scrim_pad * 2)
            _scrim_h = min(vid_h - _scrim_y, _ch + _scrim_pad * 2)
            if _scrim_w > 0 and _scrim_h > 0:
                _scrim_v = f"v_scrim_{s_idx}"
                filter_parts.append(
                    f"[{last_v}]format=rgba,"
                    f"drawbox=x={_scrim_x}:y={_scrim_y}:w={_scrim_w}:h={_scrim_h}:"
                    f"color=black@0.42:t=fill:"
                    f"enable='between(t,{seg['start']:.3f},{seg['end']:.3f})'[{_scrim_v}];"
                )
                last_v = _scrim_v

            filter_parts.append(
                f"[{last_v}][{v_card}:v]overlay={_ox}:{_oy}:"
                f"enable='between(t,{seg['start']:.3f},{seg['end']:.3f})'[{v_next}];"
            )
            last_v = v_next

            if seg["type"] == "meme":
                is_img = seg.get("is_image", False)
                if is_img:
                    cmd_inputs.extend(["-loop", "1", "-i", seg["meme_path"]])
                else:
                    cmd_inputs.extend(["-stream_loop", "-1", "-i", seg["meme_path"]])

                v_meme_in = cur_input_idx
                cur_input_idx += 1
                v_meme_scaled = f"m_scale_{s_idx}"
                v_meme_applied = f"v_m_{s_idx}"

                # Meme cũng phải nằm trong vùng an toàn ngang (tránh cột nút).
                # Meme được scale vừa khung max_w x max_h, sau đó overlay.
                # offset = (tâm vùng an toàn) - (tâm khung hình) để dịch meme
                # vào giữa vùng an toàn.
                max_w = safe_card_width(vid_w, card_scale)
                max_h = int(vid_h * 0.42)
                safe_cx = (card_scale * SAFE_ZONE_LEFT
                           + (vid_w - card_scale * SAFE_ZONE_LEFT
                              - card_scale * SAFE_ZONE_RIGHT) / 2.0)
                wm_off = int(safe_cx - vid_w / 2.0)
                filter_parts.append(
                    f"[{v_meme_in}:v]format=yuva420p,scale=w={max_w}:h={max_h}:"
                    f"force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,"
                    f"setsar=1[{v_meme_scaled}];"
                    f"[{last_v}][{v_meme_scaled}]overlay=(W-w)/2+{wm_off}:(H-h)/2:"
                    f"enable='between(t,{seg['start']:.3f},{seg['end']:.3f})'[{v_meme_applied}];"
                )
                last_v = v_meme_applied

        # Watermark
        cmd_inputs.extend(["-loop", "1", "-i", wm_img_path])
        v_wm = cur_input_idx
        cur_input_idx += 1

        # Watermark: đặt ở góc TRÁI trong vùng an toàn (tránh cột nút
        # bên phải của TikTok vốn bị UI che). Di chuyển dọc để chống re-up.
        # Vùng trên (150..250) là nơi trống nhất -> không đè lên chữ card.
        wm_x = f"{int(card_scale * 45)}"
        if getattr(req, "moving_watermark", True):
            wm_y = f"{int(card_scale * 165)}+mod(t*95,{int(card_scale * 1150)})"
        else:
            wm_y = f"{int(card_scale * 165)}"
        filter_parts.append(f"[{last_v}][{v_wm}:v]overlay='{wm_x}':'{wm_y}'[v_out]")

        # Audio
        audio_inputs = ["-i", voice_merged]
        v_voice_idx = cur_input_idx
        cur_input_idx += 1

        if has_bgm:
            audio_inputs.extend(["-stream_loop", "-1", "-i", bgm_input])
            v_bgm_idx = cur_input_idx
            cur_input_idx += 1
            vol = max(0.01, min(1.0, float(getattr(req, "bgm_volume", 0.12) or 0.12)))
            # BGM loop vo han -> phai atrim ve do dai video, neu khong
            # amix se khong bao gio ket thuc va ffmpeg treo khong ghi
            # duoc moov atom (file MP4 hong, browser khong phai duoc).
            audio_filter = (
                f"[{v_bgm_idx}:a]atrim=0:{total_dur:.3f},asetpts=N/SR/TB[bgm];"
                f"[{v_voice_idx}:a][bgm]amix=inputs=2:duration=first:"
                f"weights=1.0 {vol}[raw]"
            )
        else:
            audio_filter = (f"[{v_voice_idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:"
                            f"channel_layouts=stereo[raw]")

        # Chuẩn hoá độ lớn âm thanh theo chuẩn nền tảng (TikTok/FB/Reels
        # đều target ~-14 LUFS). Không có bước này giọng đọc bị nhỏ, người
        # xem phải vặn volume -> giảm tỉ lệ xem hết video.
        audio_filter += (
            ";[raw]loudnorm=I=-14:TP=-1.5:LRA=11[a_out]"
        )

        full_filter = "".join(filter_parts) + ";" + audio_filter

        out_name = f"ghien_comment_{job_id}.mp4"
        out_path = str(OUTPUTS_DIR / out_name)

        cmd = [
            ffmpeg_bin(), "-y",
            *cmd_inputs,
            *audio_inputs,
            "-filter_complex", full_filter,
            "-map", "[v_out]",
            "-map", "[a_out]",
            "-t", f"{total_dur:.3f}",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "fastdecode",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-threads", "0",
            "-movflags", "+faststart",
            "-progress", "pipe:1",
            "-nostats",
            out_path,
        ]

        # ============ BƯỚC 4: CHẠY FFMPEG (thread pool) ============
        await _safe_progress(p, 0.65, "Đang mã hoá video...")

        # Chạy FFmpeg và báo tiến trình theo % (đọc từ -progress pipe:1)
        loop = asyncio.get_running_loop()
        err_log = str(TEMP_DIR / f"{job_id}_ffmpeg.log")

        def _encode():
            # stderr ghi ra file (không dùng PIPE để tránh deadlock khi buffer đầy)
            with open(err_log, "wb") as errf:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=errf
                )
                total = max(total_dur, 0.1)
                out = b""
                assert proc.stdout is not None
                last_progress = time.time()
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    out += line
                    if line.startswith(b"out_time_ms="):
                        try:
                            # out_time_ms thực tế là micro-giây
                            done = int(line.split(b"=", 1)[1].strip()) / 1_000_000.0
                            pct = min(0.99, done / total)
                            last_progress = time.time()
                            # Map tiến trình encode (0-99%) vào khoảng 65%-99%
                            overall = 0.65 + 0.34 * pct
                            try:
                                asyncio.run_coroutine_threadsafe(
                                    _safe_progress(
                                        p, overall,
                                        f"Đang mã hoá video... {pct*100:.0f}%",
                                    ),
                                    loop,
                                )
                            except Exception:
                                pass
                        except Exception:
                            pass
                # stdout đã đóng nhưng ffmpeg vẫn chạy -> treo (filter loop
                # vô hạn). Kill sau khi không còn tiến tri.
                stuck = 0
                while proc.poll() is None:
                    time.sleep(0.5)
                    if time.time() - last_progress > 20:
                        stuck += 1
                        if stuck >= 3:
                            print("[FFmpeg] ffmpeg bi treo, ket thuc force")
                            proc.kill()
                            break
                    else:
                        stuck = 0
                proc.wait()
            return proc.returncode, out

        returncode, ffmpeg_out = await loop.run_in_executor(None, _encode)

        if returncode != 0:
            try:
                with open(err_log, "rb") as f:
                    err_msg = f.read().decode("utf-8", errors="ignore")[-800:]
            except Exception:
                err_msg = (ffmpeg_out or b"").decode("utf-8", errors="ignore")[-800:]
            print(f"[FFmpeg Render Error] {err_msg}")
            raise RuntimeError(f"Lỗi khi mã hóa video: {err_msg}")

        # Kiểm tra file MP4 có hợp lệ không (có moov atom).
        # File hỏng -> browser không phát được, báo lỗi rõ ràng.
        if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
            raise RuntimeError("File video không được tạo ra hoặc bị hỏng.")

        try:
            probe = subprocess.run(
                [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", out_path],
                capture_output=True, text=True, timeout=30,
            )
            vdur = float((probe.stdout or "0").strip() or 0)
            if vdur <= 0.1:
                raise RuntimeError("File video hỏng (không đọc được thời lượng).")
            # Sai lệch > 0.5s => video khong khop voi audio
            if abs(vdur - total_dur) > 0.5:
                print(f"[Render] Canh bao lech thoi luong: video={vdur:.2f} "
                      f"timeline={total_dur:.2f}")
        except RuntimeError:
            raise
        except Exception as e:
            print(f"[Render] Khong doc duoc thoi luong video: {e}")

        try:
            os.remove(err_log)
        except OSError:
            pass

        await _safe_progress(p, 1.0, "Hoàn tất!")

        # Don video cu: giu lai MAX_OUTPUT_VIDEOS video moi nhat.
        # Neu khong, thu muc outputs/ phinh to vo han (1 video ~30-60MB).
        try:
            from .cleanup import cleanup_old_videos
            removed, freed = cleanup_old_videos(keep_names=[out_name])
            if removed:
                print(f"[Render] Da don {removed} video cu, giai phong {freed} MB")
        except Exception as e:
            print(f"[Render] Khong don duoc video cu: {e}")

        # ===================================================================
        # GIAI ĐOẠN 4: KIỂM TRA CHẤT LƯỢNG SAU KHI RENDER
        # ===================================================================
        post_qc = None
        if not getattr(req, "skip_qc", False):
            try:
                post_qc = check_video(out_path, expected_duration=total_dur)
                for iss in post_qc.get("issues", []):
                    print(f"[QC] {iss['severity'].upper()}: {iss['message']}")
            except Exception as e:
                print(f"[QC] Khong kiem tra duoc video: {e}")
                post_qc = None

        return {
            "status": "success",
            "video_url": f"/outputs/{out_name}",
            "filename": out_name,
            "duration": round(total_dur, 2),
            "qc": post_qc,
            "qc_precheck": qc_report,
        }

    finally:
        for f in temp_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass


async def _safe_progress(p, pct: float, msg: str) -> None:
    """Gọi callback progress, bỏ qua lỗi (callback có thể là sync hoặc async)."""
    try:
        res = p(pct, msg)
        if asyncio.iscoroutine(res):
            await res
    except Exception:
        pass
