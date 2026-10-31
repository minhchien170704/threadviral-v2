"""
Test end-to-end: scrape Threads that -> TTS -> render video -> kiem tra ket qua.

Chay: .venv\\Scripts\\python.exe tools\\test_e2e.py
"""
import asyncio
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from server import RenderRequest, SceneItem
from tvcore.render import render_video
from tvcore.scrape import scrape_threads_post

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.threads.net/@zuck"
OUT = "temp/e2e"
os.makedirs(OUT, exist_ok=True)

FFMPEG = os.path.join("bin", "ffmpeg.exe")
if not os.path.exists(FFMPEG):
    FFMPEG = "ffmpeg"

# Dung dung utils de co fallback PATH (bug: duong dan cung khong ton tai
# o ban clone sach tu GitHub -> FileNotFoundError)
try:
    from tvcore.utils import ffmpeg_bin, ffprobe_bin
    FFMPEG = ffmpeg_bin()
    FFPROBE = ffprobe_bin()
except Exception:
    FFPROBE = os.path.join("bin", "ffprobe.exe")
    if not os.path.exists(FFPROBE):
        FFPROBE = "ffprobe"


def ok(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return cond


results = []


async def main():
    print("=" * 62)
    print(f"  E2E: {URL}")
    print("=" *62)

    # ---- 1. Scrape ----
    print("\n=== 1. Scrape Threads ===")
    scenes = await scrape_threads_post(URL, max_comments=3)
    results.append(ok("scrape thanh cong", len(scenes) > 0, f"{len(scenes)} scenes"))

    s0 = scenes[0]
    print(f"    author={s0.get('author')} | verified={s0.get('is_verified')} "
          f"| likes={s0.get('likes')} | avatar={'co' if s0.get('avatar_url') else 'KHONG'}")
    results.append(ok("co avatar_url", bool(s0.get("avatar_url"))))
    results.append(ok("co likes", bool(s0.get("likes"))))
    results.append(ok("co display_text", bool(s0.get("display_text"))))
    results.append(ok("co badge verified", s0.get("is_verified") is not None,
                      f"is_verified={s0.get('is_verified')}"))

    # ---- 2. Render ----
    print("\n=== 2. Render video (TTS + FFmpeg) ===")
    items = []
    for s in scenes[:2]:
        items.append(SceneItem(
            author=s.get("author", ""), time_ago="3 giờ trước",
            display_text=s.get("display_text", "")[:200],
            tts_text=s.get("tts_text", ""),
            avatar_url=s.get("avatar_url"),
            is_verified=s.get("is_verified", False),
            likes=s.get("likes", ""), comments=s.get("comments", ""),
            reposts=s.get("reposts", ""), shares=s.get("shares", ""),
            author_liked=True,
        ))

    req = RenderRequest(
        scenes=items, voice="vi-VN-HoaiMyNeural", speed="+20%",
        watermark_text="Fanpage: Ghiền đọc comment",
        resolution="1080x1920", bg_video_path="random",
    )

    def prog(pct, msg):
        if pct >= 100 or pct % 25 == 0:
            print(f"    [{pct:.0f}%] {msg}")

    res = await render_video(req, progress=prog)
    results.append(ok("render thanh cong", res.get("status") == "success", str(res)[:90]))

    vid = os.path.join("outputs", res.get("filename", ""))
    results.append(ok("file video ton tai", os.path.exists(vid),
                      f"{os.path.getsize(vid)//1024 if os.path.exists(vid) else 0} KB"))
    results.append(ok("duration > 0", res.get("duration", 0) > 0,
                      f"{res.get('duration')}s"))

    # ---- 3. Probe video ----
    print("\n=== 3. Thong so video ===")
    ffprobe = FFPROBE
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height,r_frame_rate,codec_name,pix_fmt",
           "-show_entries", "format=duration", "-of", "json", vid]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    info = json.loads(out.stdout or "{}")
    vs = (info.get("streams") or [{}])[0]
    dur = float((info.get("format") or {}).get("duration", 0))

    print(f"    {vs.get('width')}x{vs.get('height')} | {vs.get('codec_name')} "
          f"| {vs.get('pix_fmt')} | {vs.get('r_frame_rate')} | {dur:.2f}s")

    results.append(ok("1080x1920 (9:16)", vs.get("width") == 1080 and vs.get("height") == 1920))
    results.append(ok("codec h264", vs.get("codec_name") == "h264"))
    results.append(ok("pix_fmt yuv420p (compat)", vs.get("pix_fmt") == "yuv420p"))
    # fps phu thuoc nen nen: co video nen 30fps, khong co thi lavfi 24fps.
    # -> chap nhan ca hai, chi can la fps hop le (18..60).
    _fps_raw = str(vs.get("r_frame_rate") or "")
    try:
        _num, _den = (_fps_raw.split("/") + ["1"])[:2]
        _fps = float(_num) / float(_den or 1)
    except Exception:
        _fps = 0.0
    _has_bg = os.path.isdir("backgrounds") and any(
        f.lower().endswith((".mp4", ".mov", ".webm")) for f in os.listdir("backgrounds")
    ) if os.path.isdir("backgrounds") else False
    if _has_bg:
        results.append(ok("30fps (co video nen)", _fps_raw in ("30/1", "30000/1001"),
                          f"{_fps_raw}"))
    else:
        results.append(ok("fps hop le (18..60, khong co video nen)",
                          18.0 <= _fps <= 60.0, f"{_fps_raw}"))

    # ---- 4. Loudness ----
    print("\n=== 4. Do giong (EBU R128) ===")
    cmd2 = [FFMPEG, "-hide_banner", "-i", vid, "-af",
            "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
            "-f", "null", "-"]
    o2 = subprocess.run(cmd2, capture_output=True, text=True, encoding="utf-8",
                        errors="ignore")
    txt = o2.stderr or ""
    i = txt.rfind("{")
    lufs = None
    if i >= 0:
        try:
            j = json.loads(txt[i:txt.rfind("}") + 1])
            lufs = float(j.get("input_i", 0))
        except Exception:
            pass
    if lufs is not None:
        print(f"    input_i = {lufs:.1f} LUFS")
        results.append(ok("giong trong khoang -16..-12 LUFS", -16 <= lufs <= -12,
                          f"{lufs:.1f} LUFS"))
    else:
        print("    (khong doc duoc - co the video khong co audio)")

    # ---- 5. Card trong video ----
    print("\n=== 5. Kiem tra card trong video ===")
    frames = []
    for i, t in enumerate((3.0, 8.0, 14.0, 20.0)):
        fp = os.path.join(OUT, f"frame_{i}.png")
        subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", str(t), "-i", vid, "-frames:v", "1", "-update", "1", fp],
                       capture_output=True)
        if os.path.exists(fp):
            frames.append((t, fp))

    results.append(ok("trich duoc frame", len(frames) > 0, f"{len(frames)} frame"))

    from tvcore.config import (SAFE_ZONE_BOTTOM, SAFE_ZONE_LEFT,
                               SAFE_ZONE_RIGHT, SAFE_ZONE_TOP)
    for t, fp in frames:
        im = Image.open(fp).convert("RGB")
        W, H = im.size
        px = im.load()
        # Tim vung card trang lien mach (nhieu cot trang canh nhau).
        # Diem pixel trang o nen gameplay khong lien mach -> bi loai.
        rows = []
        for y in range(H):
            cnt = sum(1 for x in range(0, W, 4) if px[x, y][0] > 247 and px[x, y][1] > 247)
            if cnt > 150:
                rows.append(y)
        if not rows:
            print(f"    t={t}s: khong thay card trang")
            continue
        y0, y1 = min(rows), max(rows)
        # Vung an toan ngang dung de do
        sl, sr = SAFE_ZONE_LEFT, W - SAFE_ZONE_RIGHT
        st, sb = SAFE_ZONE_TOP, H - SAFE_ZONE_BOTTOM
        # Do card theo hang ngang: dong nao co it nhat 30% pixel trang
        # trong vung an toan -> do duong bien card that.
        xs = []
        for y in range(y0, y1 + 1):
            row_white = sum(1 for x in range(sl, sr, 2)
                            if px[x, y][0] > 247 and px[x, y][1] > 247)
            if row_white > (sr - sl) * 0.30:
                xs.extend(x for x in range(sl, sr, 2)
                          if px[x, y][0] > 247 and px[x, y][1] > 247)
        if not xs:
            print(f"    t={t}s: khong xac dinh duoc bien card")
            continue
        x0, x1 = min(xs), max(xs)
        okx = x0 >= sl and x1 <= sr
        oky = y0 >= st and y1 <= sb
        print(f"    t={t}s: card x={x0}..{x1} y={y0}..{y1} | "
              f"an toan x={sl}..{sr} y={st}..{sb} -> "
              f"{'OK' if okx and oky else 'LOI'}")
        results.append(ok(f"t={t}s card trong vung an toan", okx and oky))

    print("\n" + "=" * 62)
    npass = sum(1 for r in results if r)
    print(f"  PASS: {npass}   FAIL: {len(results) - npass}")
    print("=" * 62)
    print(f"  Video: {vid}")
    return 0 if all(results) else 1


sys.exit(asyncio.run(main()))
