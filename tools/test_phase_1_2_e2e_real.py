"""
E2E THAT: Scraper Threads -> Scene -> Render video -> trich frame kiem tra anh.

Chay: python tools/test_phase_1_2_e2e_real.py [url_threads]
"""
import asyncio
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image

from tvcore.scrape import scrape_threads_post
from tvcore import images as im_mod
import tvcore.render as R
from server import RenderRequest, SceneItem

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.threads.net/@zuck"
OUT = os.path.join("temp", "probe")
os.makedirs(OUT, exist_ok=True)


def probe(video):
    o = subprocess.run([R.ffprobe_bin(), "-v", "error", "-print_format", "json",
                        "-show_streams", "-show_format", video],
                       capture_output=True, text=True, errors="ignore")
    try:
        return json.loads(o.stdout or "{}")
    except Exception:
        return {}


def durations(info):
    v = a = 0.0
    vc = ac = ""
    for s in (info.get("streams") or []):
        if s.get("codec_type") == "video":
            v = float(s.get("duration") or 0)
            vc = s.get("codec_name") or ""
        elif s.get("codec_type") == "audio":
            a = float(s.get("duration") or 0)
            ac = s.get("codec_name") or ""
    return v, a, vc, ac


def frame(video, t, out):
    subprocess.run([R.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
                    "-i", video, "-ss", f"{t:.3f}", "-frames:v", "1", out],
                   capture_output=True, errors="ignore")
    return os.path.isfile(out) and os.path.getsize(out) > 500


def colorfulness(png):
    """Do do sat trung binh -> bang chung anh mau (khong phai nen xam)."""
    with Image.open(png).convert("RGB") as im:
        px = im.load()
        w, h = im.size
        n = 0
        tot = 0
        for y in range(0, h, 4):
            for x in range(0, w, 4):
                r, g, b = px[x, y]
                tot += 1
                if max(r, g, b) - min(r, g, b) >= 90:
                    n += 1
    return (100.0 * n / tot) if tot else 0.0


def main():
    print("== SCRAPE ==", URL)
    scenes = asyncio.run(scrape_threads_post(URL, 5))
    print(f"scenes={len(scenes)}")
    root = scenes[0]
    imgs = root.get("images") or []
    print(f"root images = {len(imgs)}")
    for i, u in enumerate(imgs[:8]):
        print(f"  [{i}] {u[:120]}")
    with open(os.path.join(OUT, "real_scenes.json"), "w", encoding="utf-8") as f:
        json.dump(scenes, f, ensure_ascii=False, indent=1)

    if not imgs:
        print("!! Bai nay khong co anh -> dung bai khac de kiem tra render.")
        return 1

    # Tai anh that
    dest_dir = os.path.join(OUT, "dl")
    os.makedirs(dest_dir, exist_ok=True)
    paths = asyncio.run(im_mod.prepare_images_async(imgs, "real", dest_dir))
    print(f"downloaded {len(paths)}/{len(imgs)}")
    if not paths:
        print("!! Khong tai duoc anh nao")
        return 1
    for p in paths:
        with Image.open(p) as i:
            print(f"  {os.path.basename(p)} {i.size}")

    # Render
    sc_items = []
    for s in scenes[:3]:
        sc_items.append({
            "author": s.get("author") or "u",
            "time_ago": s.get("time_ago") or "1 ngày",
            "display_text": s.get("display_text") or "",
            "tts_text": s.get("tts_text") or s.get("display_text") or "",
            "avatar_url": s.get("avatar_url"),
            "is_verified": bool(s.get("is_verified")),
            "likes": s.get("likes") or "", "comments": s.get("comments") or "",
            "reposts": s.get("reposts") or "", "shares": s.get("shares") or "",
            "author_liked": True, "meme_file": "",
            "images": s.get("images") or [],
        })

    req = RenderRequest(
        scenes=[SceneItem(**s) for s in sc_items],
        voice="vi-VN-HoaiMyNeural", speed="+20%", watermark_text="",
        resolution="1080x1920", bg_video_path="random", auto_split=True,
        skip_qc=True,
    )
    res = asyncio.run(R.render_video(req, progress=lambda p, m: print(f"  [{p:.0%}] {m}")))
    print("render:", res)
    vid = os.path.join("outputs", res.get("filename", ""))
    info = probe(vid)
    v, a, vc, ac = durations(info)
    print(f"VIDEO: dur={v:.2f}s audio={a:.2f}s vcodec={vc} acodec={ac} size={os.path.getsize(vid)}")

    # Trich frame
    fr_dir = os.path.join(OUT, "frames")
    os.makedirs(fr_dir, exist_ok=True)
    n = 8
    rows = []
    for i in range(n):
        t = v * (i + 0.5) / n
        fp = os.path.join(fr_dir, f"real_{i}.png")
        if frame(vid, t, fp):
            cf = colorfulness(fp)
            rows.append((t, cf, fp))
            print(f"  t={t:6.2f} sat={cf:5.2f}%  {fp}")

    # Luu bang chung
    with open(os.path.join(OUT, "real_render_report.json"), "w", encoding="utf-8") as f:
        json.dump({"video": vid, "duration": v, "audio": a, "vcodec": vc,
                   "acodec": ac, "images": imgs, "downloaded": paths,
                   "frames": [{"t": t, "sat": cf, "path": p} for t, cf, p in rows]},
                  f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
