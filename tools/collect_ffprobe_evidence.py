"""
Sinh bang chung FFprobe + timeline visual cho video THAT (Phase 1.1 report).

Chay: python tools/collect_ffprobe_evidence.py
Output: docs/phase_1_1_ffprobe_evidence.txt
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tvcore.render as R
from server import RenderRequest, SceneItem
import asyncio

OUT = []
FWD = "assets"  # thư mục output


def _p(*a):
    OUT.append(" ".join(str(x) for x in a))


def _probe(path):
    out = subprocess.run(
        [R.ffprobe_bin(), "-v", "error", "-show_streams", "-show_format",
         "-of", "json", path], capture_output=True, text=True)
    return json.loads(out.stdout or "{}")


LONG = (
    "Sau khi nghỉ việc tôi dành cả ba tháng để suy nghĩ xem mình thực sự muốn làm gì. "
    "Ban đầu tôi nghĩ rằng nghỉ việc sẽ khiến mình cảm thấy nhẹ nhõm và tự do hơn. "
    "Nhưng thực tế tháng đầu tiên tôi thấy hối hận và lo lắng về tiền bạc. "
    "Rồi tôi bắt đầu nhận ra rằng mình đã sống mà không thật sự sống. "
    "Bây giờ tôi bắt đầu từng ngày một chậm lại trước khi bắt tay vào việc gì. "
    "Mọi người nghĩ sao về quyết định nghỉ việc của tôi?"
)

CASES = [
    ("A. nen tat / text dai", LONG, LONG, None),
    ("B. nen tat / display 1 dong + audio dai", "Một câu ngắn.", LONG, None),
    ("C. nen gameplay BAT / display 1 dong + audio dai", "Một câu ngắn.", LONG, "random"),
    ("D. co reply / text dai", LONG, LONG, None),
]


def render_and_evidence(label, display, tts, bg):
    captured = []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, cmd, *a, **kw):
            if isinstance(cmd, list):
                captured.append([str(c) for c in cmd])
            super().__init__(cmd, *a, **kw)

    R.subprocess.Popen = Spy
    try:
        req = RenderRequest(
            scenes=[SceneItem(author="tacgia", time_ago="2 giờ trước",
                              display_text=display, tts_text=tts,
                              likes="1.2K", comments="48")],
            voice="vi-VN-HoaiMyNeural", speed="+20%",
            watermark_text="", resolution="1080x1920",
            bg_video_path=bg, auto_split=False,
        )
        res = asyncio.run(R.render_video(req, progress=lambda p, m: None))
    finally:
        R.subprocess.Popen = real

    _p("=" * 74)
    _p(f"TRUONG HOP: {label}")
    _p("=" * 74)
    if res.get("status") != "success":
        _p(f"  RENDER FAIL: {res}")
        return

    vid = os.path.join("outputs", res.get("filename", ""))
    info = _probe(vid)
    vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    as_ = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    vdur = float(vs[0].get("duration", 0) or 0) if vs else 0.0
    adur = float(as_[0].get("duration", 0) or 0) if as_ else 0.0
    _p(f"  File            : {vid}")
    _p(f"  Container       : {info.get('format', {}).get('format_name')}")
    _p(f"  Video stream    : codec={vs[0].get('codec_name')} "
       f"{vs[0].get('width')}x{vs[0].get('height')} "
       f"fps={vs[0].get('r_frame_rate')} dur={vdur:.3f}s")
    _p(f"  Audio stream    : codec={as_[0].get('codec_name')} "
       f"dur={adur:.3f}s")
    _p(f"  Format duration : {info.get('format', {}).get('duration')}")
    _p(f"  AUDIO KHONG CAT : {adur:.3f}s audio <= {vdur:.3f}s video "
       f"-> {'OK' if vdur >= adur - 0.3 else 'FAIL'}")
    _p(f"  AUDIO KHONG LAP : audio < video -> "
       f"{'OK' if adur < vdur else 'FAIL'}")

    # Timeline visual tu lenh FFmpeg that
    spans = []
    for c in captured:
        joined = " ".join(c)
        for m in re.finditer(
                r"overlay=[^:]+:[^:]+:enable='between\(t,([-\d.]+),([-\d.]+)\)'",
                joined):
            spans.append((float(m.group(1)), float(m.group(2))))
    spans = sorted(set(spans))
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    total = res.get("duration", 0) or 0
    covered = sum(b - a for a, b in merged)
    _p(f"  Timeline visual ({len(spans)} overlay spans):")
    for a, b in spans:
        _p(f"    {a:8.3f} -> {b:8.3f}   ({b - a:6.3f}s)")
    _p(f"  Tong thoi luong visual  : {covered:.3f}s / video {total:.3f}s")
    _p(f"  THIEU (khoang trong)    : {total - covered:.3f}s "
       f"-> {'OK (khong co khoang trong)' if total - covered <= 0.05 else 'FAIL'}")
    gaps = []
    cur = merged[0][0] if merged else 0.0
    for a, b in merged:
        if a - cur > 0.01:
            gaps.append((cur, a))
        cur = max(cur, b)
    if total - cur > 0.01:
        gaps.append((cur, total))
    _p(f"  Khoang trong giua cac visual: {gaps if gaps else 'KHONG CO'}")

    # Silence detect
    out3 = subprocess.run(
        [R.ffmpeg_bin(), "-hide_banner", "-i", vid, "-af",
         "silencedetect=noise=-45dB:d=1.5", "-f", "null", "-"],
        capture_output=True, text=True, errors="ignore")
    sil = [l for l in (out3.stderr or "").splitlines() if "silence_duration" in l]
    _p(f"  Khoang im lang > 1.5s : {len(sil)} -> {'OK' if len(sil) <= 2 else 'FAIL'}")
    _p("")


for _lbl, _disp, _tts, _bg in CASES:
    render_and_evidence(_lbl, _disp, _tts, _bg)

os.makedirs("docs", exist_ok=True)
with open("docs/phase_1_1_ffprobe_evidence.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(OUT))
print("\n".join(OUT))
print(f"\n>> Da luu docs/phase_1_1_ffprobe_evidence.txt")
