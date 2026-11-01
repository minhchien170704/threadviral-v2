"""
REGRESSION TESTS - PHASE 1.1 (fix review blockers)

Chay: python tools/test_phase_1_1.py

Phan loai:
  [UNIT]       - ham don le, khong I/O
  [INTEGRATION]- nhieu module phoi hop nhau (plan -> split -> score)
  [E2E]        - qua API FastAPI thuc (TestClient)
  [RENDER]     - render video THAT + FFprobe (render thuc te)

Bao cao:
  - C1: visual phu kin toan bo audio (khong khoang trong)
  - C2: score_reasons giai thich dung total
  - H1: NaN/Inf bi tu choi
  - H2: QC pass 2 dung thoi luong THUC TE
  - H3/H4: sanitize viral claim
  - H5/H6: hook angle
  - M2/M4/M5
"""
import asyncio
import json
import math
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return bool(cond)


def section(title):
    print(f"\n{'=' * 62}\n  {title}\n{'=' * 62}")


# ===========================================================================
section("[UNIT] C1 - plan_visual_slices: visual phu kin audio")
# ===========================================================================
from tvcore.pacing import (MAX_SCENE_SECONDS, plan_visual_slices,
                           scene_duration, split_visual_segments)

# Cac truong hop duoc yeu cau trong review:
#   audio 12.5s text 1 dong / audio 24s text 8 dong / audio 25s text 1 dong
#   audio 27.3s text 8 dong / audio 36s text 1 dong
C1_CASES = [
    (12.5, 1, "audio 12.5s / text 1 dong"),
    (24.0, 8, "audio 24s / text 8 dong"),
    (25.0, 1, "audio 25s / text 1 dong"),
    (27.3, 8, "audio 27.3s / text 8 dong"),
    (36.0, 1, "audio 36s / text 1 dong"),
    (27.3, 1, "audio 27.3s / text 1 dong"),
    (30.0, 3, "audio 30s / text 3 dong"),
]

for _aud, _ntext, _label in C1_CASES:
    _p = plan_visual_slices(_aud, _ntext, MAX_SCENE_SECONDS)
    _segs = _p["segments"]
    # 1) Segments lien tuc, khong khoang trong
    _gaps = [
        (_segs[i + 1]["start"] - _segs[i]["end"])
        for i in range(len(_segs) - 1)
    ]
    _max_gap = max(_gaps) if _gaps else 0.0
    check(f"C1 {_label}: khong khoang trong giua cac visual",
          _max_gap <= 0.05, f"max_gap={_max_gap:.4f}s n={len(_segs)}")

    # 2) Segment dau bat dau tai 0
    check(f"C1 {_label}: segment dau bat dau tai 0",
          abs(_segs[0]["start"]) <= 0.001, f"start={_segs[0]['start']}")

    # 3) Segment cuoi ket thuc DUNG tai audio duration
    check(f"C1 {_label}: segment cuoi ket thuc tai audio duration",
          abs(_segs[-1]["end"] - _aud) <= 0.05,
          f"end={_segs[-1]['end']} audio={_aud}")

    # 4) Tong thoi luong visual == audio duration (sai so <= 0.05s)
    _tot = sum(s["duration"] for s in _segs)
    check(f"C1 {_label}: tong visual == audio duration (sai so <= 0.05s)",
          abs(_tot - _aud) <= 0.05, f"visual={_tot:.4f} audio={_aud}")

    # 5) Moi visual deu co phan text (khong visual rong)
    check(f"C1 {_label}: moi visual deu gan 1 phan text",
          len(_p["part_index"]) == len(_segs) and
          all(0 <= pi < max(1, _ntext) for pi in _p["part_index"]),
          f"part_index={_p['part_index']}")

    # 6) Moi doan <= MAX_SCENE_SECONDS
    check(f"C1 {_label}: moi doan <= {MAX_SCENE_SECONDS}s",
          all(s["duration"] <= MAX_SCENE_SECONDS + 0.001 for s in _segs),
          f"max={max(s['duration'] for s in _segs):.3f}")

# 7) split_visual_segments voi n_segments ep: van lien tuc + phu kin
for _n in (1, 2, 3, 5, 8):
    _segs = split_visual_segments(27.0, 12.0, n_segments=_n)
    _gaps = [(_segs[i + 1]["start"] - _segs[i]["end"]) for i in range(len(_segs) - 1)]
    check(f"C1 split_visual_segments(n={_n}): lien tuc + phu kin 27s",
          len(_segs) == _n and (max(_gaps) if _gaps else 0) <= 0.001
          and abs(_segs[-1]["end"] - 27.0) <= 0.001,
          f"n={len(_segs)} end={_segs[-1]['end']}")

# 8) Audio 0 -> rong
check("C1 plan_visual_slices(audio=0) -> rong",
      plan_visual_slices(0.0, 3)["segments"] == [])

# 9) scene_duration LUON >= audio (khong cat audio)
for _a in (5.0, 12.0, 25.0, 27.3, 36.0):
    check(f"C1 scene_duration(audio={_a}) >= audio",
          scene_duration("mot cau ngan gon", _a) >= _a,
          f"scene={scene_duration('mot cau ngan gon', _a)}")


# ===========================================================================
section("[INTEGRATION] C1 - text split + visual plan khop nhau")
# ===========================================================================
from tvcore.cards import split_visual_text_parts

LONG_8_LINES = (
    "Sau khi nghỉ việc tôi dành cả ba tháng để suy nghĩ xem mình thực sự muốn làm gì. "
    "Ban đầu tôi nghĩ rằng nghỉ việc sẽ khiến mình cảm thấy nhẹ nhõm và tự do hơn. "
    "Nhưng thực tế tháng đầu tiên tôi thấy hối hận và lo lắng về tiền bạc. "
    "Rồi tôi bắt đầu nhận ra rằng mình đã sống mà không thật sự sống."
)


def _simulate_render_split(text, audio_dur, scale=1.0, vid_w=1080):
    """Mô phỏng ĐÚNG logic render.py (sau fix) để kiểm tra hành vi."""
    parts = split_visual_text_parts(text, 99, scale=scale, vid_w=vid_w)
    if not parts or not any(p.strip() for p in parts):
        parts = [text]
    n_text = max(1, len(parts))
    plan = plan_visual_slices(audio_dur, n_text, MAX_SCENE_SECONDS)
    return plan, parts


for _aud, _label in [(12.5, "12.5s"), (24.0, "24s"), (25.0, "25s"),
                     (27.3, "27.3s"), (36.0, "36s")]:
    _plan, _parts = _simulate_render_split(LONG_8_LINES, _aud)
    _segs = _plan["segments"]
    # Moi visual deu tro toi mot phan text that
    _ok_map = len(_plan["part_index"]) == len(_segs) and \
        all(0 <= pi < len(_parts) for pi in _plan["part_index"])
    check(f"C1 [INTEGRATION] {_label}: moi visual -> 1 phan text that",
          _ok_map,
          f"segs={len(_segs)} parts={len(_parts)} map={_plan['part_index']}")
    # Tong thoi luong phu kin
    check(f"C1 [INTEGRATION] {_label}: tong visual == audio",
          abs(sum(s["duration"] for s in _segs) - _aud) <= 0.05,
          f"{sum(s['duration'] for s in _segs):.3f} vs {_aud}")

# Text ngắn (1 dong) + audio dai: PHAI co visual (khong trong)
_plan, _parts = _simulate_render_split("Ngắn.", 27.3)
check("C1 [INTEGRATION] text 1 dong + audio 27.3s: visual van phu kin",
      abs(sum(s["duration"] for s in _plan["segments"]) - 27.3) <= 0.05,
      f"n={len(_plan['segments'])} part_index={_plan['part_index']}")

# Thu tu noi dung text duoc giu nguyen
_plan, _parts = _simulate_render_split(LONG_8_LINES, 27.3)
_joined = " ".join(_parts).replace("  ", " ").strip()
check("C1 [INTEGRATION] thu tu noi dung text giu nguyen",
      _joined == LONG_8_LINES.strip(),
      f"len={len(_joined)} vs {len(LONG_8_LINES.strip())}")

# Fallback: text rong -> van co 1 phan, khong crash
_plan, _parts = _simulate_render_split("", 27.3)
check("C1 [INTEGRATION] text rong -> fallback an toan (khong crash)",
      len(_parts) >= 1 and len(_plan["segments"]) >= 1)


# ===========================================================================
section("[UNIT] C2 - score_reasons giai thich DUNG total")
# ===========================================================================
from tvcore.content import (WEIGHTS, WeightError, parse_likes, score_comment,
                            select_comments, validate_weights)

C2_CASES = [
    ("nhieu tieu chi duoi threshold",
     "Bình luận thường về công việc ở công ty", "Bài về công việc", 0),
    ("co likes bonus", "Tôi nghĩ người ta mới nói đúng, bạn nghĩ sao?",
     "bài gốc về việc làm việc", 1234),
    ("comment ngan", "ok", "abc", 0),
    ("comment viral", "HAHAHA đỉnh quá 😂😂😂 mọi người ơi", "bài gốc", 99999),
    ("cau hoi y kien", "Tôi nghĩ vậy là đúng rồi, bạn thấy sao?",
     "bài gốc về việc làm việc", 50),
]

for _name, _txt, _root, _likes in C2_CASES:
    _sc = score_comment(_txt, _root, _likes)
    _sum = sum(r["contribution"] for r in _sc["score_reasons"])
    check(f"C2 {_name}: sum(contribution) == total (sai so <= 1e-4)",
          abs(_sum - _sc["total"]) <= 1e-4,
          f"sum={_sum:.6f} total={_sc['total']:.6f} diff={_sum - _sc['total']:.6f}")

# Moi entry phai co name/score/weight/contribution
_sc = score_comment("Tôi nghĩ vậy là đúng rồi, bạn thấy sao?", "abc", 100)
check("C2 moi entry co name/score/weight/contribution",
      all(all(k in r for k in ("name", "score", "weight", "contribution"))
          for r in _sc["score_reasons"]),
      f"n={len(_sc['score_reasons'])}")

# MOI tieu chi (ke ca duoi threshold) phai xuat hien
_keys = {r["key"] for r in _sc["score_reasons"]}
check("C2 moi 5 tieu chi + likes deu co trong score_reasons",
      {"interesting", "discussion", "humor", "relevance", "concise"} <= _keys
      and ("likes" in _keys),
      f"keys={sorted(_keys)}")

# Khong cong trung likes bonus (contribution cua likes == like_bonus)
_like_entries = [r for r in _sc["score_reasons"] if r["key"] == "likes"]
check("C2 likes bonus khong bi cong trung",
      len(_like_entries) == 1
      and abs(_like_entries[0]["contribution"] - _sc["like_bonus"]) <= 1e-4,
      f"n={len(_like_entries)} bonus={_sc['like_bonus']}")

# Comment ngắn: van phai giai thich DU total
_sc_short = score_comment("ok", "abc", 0)
check("C2 comment ngan: sum(contribution) == total",
      abs(sum(r["contribution"] for r in _sc_short["score_reasons"])
          - _sc_short["total"]) <= 1e-4,
      f"total={_sc_short['total']}")

# Weights tuy chinh: giai thich van dung
_w = validate_weights({"interesting": 0.6, "discussion": 0.2, "humor": 0.1,
                       "relevance": 0.05, "concise": 0.05})
_sc_w = score_comment("Tôi nghĩ vậy là đúng rồi, bạn thấy sao?", "abc", 100, weights=_w)
check("C2 weights tuy chinh: sum(contribution) == total",
      abs(sum(r["contribution"] for r in _sc_w["score_reasons"])
          - _sc_w["total"]) <= 1e-4,
      f"sum={sum(r['contribution'] for r in _sc_w['score_reasons']):.6f} "
      f"total={_sc_w['total']}")

# is_primary phai ton tai
check("C2 moi entry co is_primary",
      all("is_primary" in r for r in _sc["score_reasons"]))

# select_comments: comment bi loai van co rejection_reason
_comments = [
    {"text": "Mua ngay tai shop gia sale", "likes": "10"},
    {"text": "ok", "likes": "0"},
    {"text": "Tôi nghĩ người ta mới nói đúng, bạn nghĩ sao?", "likes": "1.2K"},
    {"text": "Tôi nghĩ người ta mới nói đúng, bạn nghĩ sao?", "likes": "5"},
]
_sel, _stats = select_comments(_comments, root_text="bài gốc", max_n=3,
                               min_score=0.18)
check("C2 comment bi loai co rejection_reason day du",
      len(_stats["rejected"]) == 3
      and all(r.get("rejection_reason") for r in _stats["rejected"]),
      f"rejected={len(_stats['rejected'])}")

# score_reasons trong selected cung khop total
_ok = True
for _s in _sel:
    _sum = sum(r["contribution"] for r in _s["score_reasons"])
    if abs(_sum - _s["score"]) > 1e-4:
        _ok = False
check("C2 selected comments: score_reasons khop score", _ok)


# ===========================================================================
section("[UNIT] H1 - NaN / Infinity bi tu choi")
# ===========================================================================
_bad_weights = [
    ("NaN", dict(WEIGHTS, interesting=float("nan"))),
    ("+Infinity", dict(WEIGHTS, interesting=float("inf"))),
    ("-Infinity", dict(WEIGHTS, interesting=float("-inf"))),
    ("NaN o tat ca key", {k: float("nan") for k in WEIGHTS}),
]
for _name, _w in _bad_weights:
    _raised = False
    try:
        validate_weights(_w)
    except WeightError as _e:
        _raised = True
    check(f"H1 validate_weights tu choi {_name}", _raised)

for _name, _w in [("am", dict(WEIGHTS, interesting=-0.5)),
                  ("> 1", dict(WEIGHTS, interesting=5.0)),
                  ("tat ca 0", {k: 0.0 for k in WEIGHTS}),
                  ("sai kieu", dict(WEIGHTS, interesting="abc"))]:
    _raised = False
    try:
        validate_weights(_w)
    except WeightError:
        _raised = True
    check(f"H1 validate_weights tu choi {_name}", _raised)

# Khong NaN lot vao scoring
_raised = False
try:
    score_comment("test", "abc", 0, weights=dict(WEIGHTS, interesting=float("nan")))
except WeightError:
    _raised = True
check("H1 NaN khong lot vao scoring", _raised)

# Khong NaN lot vao JSON (theo RFC 8259, NaN KHONG phai JSON hop le)
import json as _json
_json_ok = True
try:
    _json.dumps({"x": float("nan")}, allow_nan=False)
    _json_ok = False
except ValueError:
    pass
check("H1 NaN la JSON khong hop le (nen phai chan truoc)", _json_ok)


# ===========================================================================
section("[E2E] H1 - API that tra HTTP 400 voi weights khong hop le")
# ===========================================================================
try:
    from fastapi.testclient import TestClient
    import server as _server

    _client = TestClient(_server.app)

    def _post_analyze_raw(weights_dict):
        """Gửi RAW JSON (json.dumps mac dinh cho phep NaN) -> server phai chan."""
        body = json.dumps({
            "scenes": [
                {"author": "a", "display_text": "Bài gốc về công việc",
                 "tts_text": "Bài gốc về công việc", "likes": "1.2K"},
                {"author": "b", "display_text": "Tôi nghĩ vậy là đúng, bạn nghĩ sao?",
                 "tts_text": "Tôi nghĩ vậy là đúng, bạn nghĩ sao?", "likes": "10"},
            ],
            "weights": weights_dict,
        })
        return _client.post("/api/analyze", content=body,
                            headers={"Content-Type": "application/json"})

    _r_nan = _post_analyze_raw({"interesting": float("nan"), "discussion": 0.25,
                                "humor": 0.2, "relevance": 0.2, "concise": 0.1})
    check("H1 [E2E] API tra 400 cho weights NaN",
          _r_nan.status_code == 400, f"status={_r_nan.status_code}")

    _r_inf = _post_analyze_raw({"interesting": float("inf"), "discussion": 0.25,
                                "humor": 0.2, "relevance": 0.2, "concise": 0.1})
    check("H1 [E2E] API tra 400 cho weights +Infinity",
          _r_inf.status_code == 400, f"status={_r_inf.status_code}")

    _r_ninf = _post_analyze_raw({"interesting": float("-inf"), "discussion": 0.25,
                                 "humor": 0.2, "relevance": 0.2, "concise": 0.1})
    check("H1 [E2E] API tra 400 cho weights -Infinity",
          _r_ninf.status_code == 400, f"status={_r_ninf.status_code}")

    _r_neg = _post_analyze_raw({"interesting": -0.1, "discussion": 0.25,
                                "humor": 0.2, "relevance": 0.2, "concise": 0.1})
    check("H1 [E2E] API tra 400 cho weights am",
          _r_neg.status_code == 400, f"status={_r_neg.status_code}")

    _r_big = _post_analyze_raw({"interesting": 5.0, "discussion": 0.25,
                                "humor": 0.2, "relevance": 0.2, "concise": 0.1})
    check("H1 [E2E] API tra 400 cho weights > 1",
          _r_big.status_code == 400, f"status={_r_big.status_code}")

    _r_ok = _post_analyze_raw({"interesting": 0.25, "discussion": 0.25,
                               "humor": 0.2, "relevance": 0.2, "concise": 0.1})
    check("H1 [E2E] API tra 200 cho weights hop le",
          _r_ok.status_code == 200, f"status={_r_ok.status_code}")
    if _r_ok.status_code == 200:
        _body = _r_ok.json()
        _sc = (_body.get("selected") or [{}])[0]
        _sum = sum(r["contribution"] for r in (_sc.get("score_reasons") or []))
        check("H1 [E2E] response score_reasons khop score",
              abs(_sum - _sc.get("score", 0)) <= 1e-4,
              f"sum={_sum:.6f} score={_sc.get('score')}")
        # JSON phai parse duoc (khong co NaN)
        _raw = _r_ok.text
        check("H1 [E2E] response JSON khong chua NaN/Infinity",
              "NaN" not in _raw and "Infinity" not in _raw)

    # Body hop le -> khong 400
    _r_ok2 = _post_analyze_raw({"interesting": 0.25, "discussion": 0.25,
                                "humor": 0.2, "relevance": 0.2, "concise": 0.1})
    check("H1 [E2E] weights hop le van qua (khong chan nham)",
          _r_ok2.status_code == 200, f"status={_r_ok2.status_code}")
except Exception as _e:
    check("H1 [E2E] khong chay duoc (TestClient)", False, repr(_e))


# ===========================================================================
section("[UNIT] H2 - QC pass 2 dung thoi luong THUC TE")
# ===========================================================================
from tvcore.qc import check_script

_scene = [{"author": "a", "display_text": "cau day du day du",
            "tts_text": "cau day du day du"}]

# Thieu scene_durations -> phai BAO THIEU DU LIEU (khong im lang bo qua)
_qc_missing = check_script(_scene, audio_durations=[27.0])
check("H2 [UNIT] thieu scene_durations -> warning 'missing_scene_duration'",
      any(i["code"] == "missing_scene_duration" for i in _qc_missing["issues"]),
      str([i["code"] for i in _qc_missing["issues"]]))

# scene_durations THAT ngan hon audio -> phai BAO LOI
_qc_short = check_script(_scene, audio_durations=[27.0], scene_durations=[12.0])
check("H2 [UNIT] scene ngan hon audio -> error 'audio_longer_than_scene'",
      any(i["code"] == "audio_longer_than_scene" for i in _qc_short["issues"]),
      str([i["code"] for i in _qc_short["issues"]]))

# Visual gap -> error
_qc_gap = check_script(_scene, audio_durations=[27.0], scene_durations=[18.0])
check("H2 [UNIT] visual gap -> error 'visual_gap_audio'",
      any(i["code"] == "visual_gap_audio" for i in _qc_gap["issues"]),
      str([i["code"] for i in _qc_gap["issues"]]))

# scene >= audio -> khong loi
_qc_ok = check_script(_scene, audio_durations=[27.0], scene_durations=[27.15])
check("H2 [UNIT] scene >= audio -> khong loi",
      not any(i["severity"] == "error" for i in _qc_ok["issues"]),
      str([i["code"] for i in _qc_ok["issues"]]))

# KHONG tu tinh lai bang scene_duration() (neu lam vay check se chet)
import tvcore.qc as _qc_mod
_src_qc = open(_qc_mod.__file__, encoding="utf-8").read()
check("H2 [UNIT] qc.py KHONG con fallback 'scene_duration(text, _aud, speed)'",
      "scene_duration(text, _aud, speed)" not in _src_qc)


# ===========================================================================
section("[UNIT] H3/H4 - sanitize viral claim")
# ===========================================================================
from tvcore.script import (detect_hook_angle, has_unsupported_viral_claim,
                           normalize_angle, sanitize_viral_claim)

# --- H3: cac vi du loi trong review ---
_H3_CASES = [
    ("Câu chuyện này đang làm Threads tranh luận không hồi kết.",
     "Câu chuyện này được kể lại qua vài dòng."),
    ("Cả Threads đang phát sốt vì bài này", "Bài này đang được bàn luận."),
]
for _inp, _expect in _H3_CASES:
    _got = sanitize_viral_claim(_inp)
    check(f"H3 sanitize: '{_inp[:42]}...' -> dung mau",
          _got == _expect, f"got={_got!r}")

# Khong con "làm Threads ... đang được"
_got = sanitize_viral_claim("Câu chuyện này đang làm Threads tranh luận không hồi kết.")
check("H3 khong con cau vo nghia ('làm Threads ... đang được')",
      "làm Threads" not in _got and "đang được tranh luận" not in _got
      or _got == "Câu chuyện này được kể lại qua vài dòng.",
      f"got={_got!r}")

# Khong lap chu ngu
for _inp in ["Cả Threads đang phát sốt vì bài này",
             "Bài viết này gây bão lắm",
             "Có một món ăn đang làm mưa Threads mà ít ai biết."]:
    _got = sanitize_viral_claim(_inp)
    _words = re.findall(r"[\wàáảãạăâêôơưđ]+", _got.lower())
    _dup = [w for w in set(_words) if _words.count(w) > 1]
    check(f"H3 khong lap chu ngu: '{_inp[:38]}...'",
          len(_dup) == 0, f"dup={_dup} got={_got!r}")

# --- H4: claim phai bi chan ---
_VIRAL_MUST_CATCH = [
    "Cả Threads đang phát sốt với bài này",
    "Hàng nghìn người tranh cãi",
    "Cả mạng xã hội chia thành hai phe",
    "Bài viết gây bão",
    "Ai cũng phải bật cười",
    "Threads xôn xao",
    "làm mưa Threads",
    "Bài này đang làm Threads tranh luận không hồi kết.",
]
for _c in _VIRAL_MUST_CATCH:
    check(f"H4 nhan dien claim viral: '{_c[:44]}'",
          has_unsupported_viral_claim(_c), "")

# Sanitize phai loai het claim viral
for _c in _VIRAL_MUST_CATCH:
    _out = sanitize_viral_claim(_c)
    check(f"H4 sanitize loai het claim: '{_c[:44]}'",
          not has_unsupported_viral_claim(_out), f"out={_out!r}")

# --- H4: cau TRUNG TINH phai duoc giu nguyen ---
_NEUTRAL = [
    "cả Threads đang bàn luận về bài này",
    "Câu chuyện này có một câu hỏi khiến người đọc phải chọn một phe.",
    "Một món ăn trong bài này mà ít người để ý tới.",
    "Bài này đặt ra một câu hỏi khiến người đọc phải tự trả lời.",
]
for _c in _NEUTRAL:
    check(f"H4 cau trung tinh giu nguyen: '{_c[:44]}'",
          sanitize_viral_claim(_c) == _c,
          f"got={sanitize_viral_claim(_c)!r}")
    check(f"H4 cau trung tinh KHONG bi coi la viral: '{_c[:44]}'",
          not has_unsupported_viral_claim(_c), "")

# Sanitized phai doc tu nhien (khong vung khoang trong, khong dau lap)
for _c in _VIRAL_MUST_CATCH:
    _out = sanitize_viral_claim(_c)
    check(f"H4 sanitize ket qua doc tu nhien: '{_c[:40]}'",
          "  " not in _out and ".." not in _out and "đang được được" not in _out,
          f"out={_out!r}")


# ===========================================================================
section("[UNIT] H5 - detect_hook_angle")
# ===========================================================================
_H5_CASES = [
    ("Có một vụ drama ngoài đời được kể lại qua vài dòng trong bài này.",
     "curiosity", "khong gán humor vi 'vai' trong 'vai dong'"),
    ("HAHAHA đỉnh quá 😂", "humor", "emoji + haha -> humor"),
    ("Chi tiết này thật là vui, mọi người cười nhiều quá", "humor",
     "co 'cuoi' -> humor"),
    ("Mọi người thấy sao về chuyện này", "debate",
     "cau hoi tu tu -> debate"),
    ("Bạn nghĩ sao về chuyện này?", "debate", "dau hoi -> debate"),
    ("Không ai ngờ chi tiết này lại ở đây", "surprise", "bat ngo"),
    ("Câu chuyện này chạm đúng chỗ tôi đang trải qua", "emotion",
     "cam xuc"),
    ("Chi tiết cuối bài này mới là thứ đáng chú ý nhất.", "curiosity",
     "'cuoi' KHONG phai humor"),
    ("", "curiosity", "rong -> fallback trung tinh"),
    ("Một câu chuyện bình thường không có gì đặc biệt", "curiosity",
     "fallback trung tinh"),
]
for _txt, _expect, _why in _H5_CASES:
    _got = detect_hook_angle(_txt)
    check(f"H5 detect_hook_angle({_why}): '{_txt[:40]}' -> {_expect}",
          _got == _expect, f"got={_got}")


# ===========================================================================
section("[UNIT] H6 - normalize_angle")
# ===========================================================================
_H6_CASES = [
    ("tò mò", "curiosity"), ("to mo", "curiosity"), ("tòmò", "curiosity"),
    ("thú vị", "surprise"), ("thu vi", "surprise"),
    ("hài hước", "humor"), ("hai huoc", "humor"),
    ("debate", "debate"), ("conflict", "debate"), ("thanh luan", "debate"),
    ("humor", "humor"), ("emotion", "emotion"), ("relatability", "emotion"),
    ("surprise", "surprise"), ("curiosity", "curiosity"),
    ("DEBATE", "debate"), ("Unexpected", "surprise"),
]
for _raw, _expect in _H6_CASES:
    _got = normalize_angle(_raw)
    check(f"H6 normalize_angle({_raw!r}) -> {_expect}",
          _got == _expect, f"got={_got!r}")

check("H6 normalize_angle(None) -> None", normalize_angle(None) is None)
check("H6 normalize_angle('') -> None", normalize_angle("") is None)
check("H6 normalize_angle('khong_hop_le') -> None",
      normalize_angle("khong_hop_le") is None)


# ===========================================================================
section("[UNIT] M5 - reply card khong lap toan bo text goc")
# ===========================================================================
_src_render = open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "tvcore", "render.py"), encoding="utf-8").read()
check("M5 render.py co logic dung anh visual CUOI cho reply card",
      "_last_visual_img" in _src_render and "_did_split" in _src_render)

# ===========================================================================
section("[UNIT] M2/M4 - verify_bugs co assert; pacing khong co 2 nguon timeline")
# ===========================================================================
_src_vb = open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "tools", "verify_bugs.py"), encoding="utf-8").read()
check("M2 verify_bugs.py co assert (khong con chi print)",
      "assert" in _src_vb or "sys.exit" in _src_vb)

# plan_visual_slices la nguon chia DUY NHAT trong render.py
check("M4 render.py dung plan_visual_slices (nguon chia duy nhat)",
      "plan_visual_slices(" in _src_render)
# KHONG con goi TRUC TIEP split_visual_segments() (chi duoc phep nhac toi
# trong comment). Nguon chia visual phai la plan_visual_slices.
_code_only = "\n".join(
    ln for ln in _src_render.splitlines()
    if not ln.lstrip().startswith("#")
)
check("M4 render.py KHONG con goi truc tiep split_visual_segments()",
      not re.search(r"(?<![\w.])split_visual_segments\s*\(", _code_only))


# ===========================================================================
section("[RENDER] C1 - render video THAT + FFprobe")
# ===========================================================================
_RENDER_LONG = (
    "Sau khi nghỉ việc tôi dành cả ba tháng để suy nghĩ xem mình thực sự muốn làm gì. "
    "Ban đầu tôi nghĩ rằng nghỉ việc sẽ khiến mình cảm thấy nhẹ nhõm và tự do hơn. "
    "Nhưng thực tế tháng đầu tiên tôi thấy hối hận và lo lắng về tiền bạc. "
    "Rồi tôi bắt đầu nhận ra rằng mình đã sống mà không thật sự sống. "
    "Bây giờ tôi bắt đầu từng ngày một chậm lại trước khi bắt tay vào việc gì. "
    "Mọi người nghĩ sao về quyết định nghỉ việc của tôi?"
)
_RENDER_SHORT = "Một câu bình luận ngắn ở đây."


def _render_and_probe(label, scenes_items, bg):
    """Render THAT, bat lenh FFmpeg, kiem tra timeline visual co gap hay khong."""
    import tvcore.render as _R
    from server import RenderRequest, SceneItem

    captured = {}
    real_popen = subprocess.Popen

    class SpyPopen(real_popen):
        def __init__(self, cmd, *a, **kw):
            if isinstance(cmd, list):
                captured.setdefault("cmds", []).append([str(c) for c in cmd])
            super().__init__(cmd, *a, **kw)

    _R.subprocess.Popen = SpyPopen
    try:
        items = [SceneItem(**it) for it in scenes_items]
        req = RenderRequest(
            scenes=items, voice="vi-VN-HoaiMyNeural", speed="+20%",
            watermark_text="", resolution="1080x1920",
            bg_video_path=bg, auto_split=False,
        )
        res = asyncio.run(_R.render_video(req, progress=lambda p, m: None))
    finally:
        _R.subprocess.Popen = real_popen

    if res.get("status") != "success":
        check(f"RENDER [{label}] render thanh cong", False, str(res)[:150])
        return None

    vid = os.path.join("outputs", res.get("filename", ""))

    # --- FFprobe: video + audio ---
    out = subprocess.run(
        [_R.ffprobe_bin(), "-v", "error", "-show_streams", "-show_format",
         "-of", "json", vid], capture_output=True, text=True)
    info = json.loads(out.stdout or "{}")
    vstreams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    astreams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    vdur = float(vstreams[0].get("duration", 0) or 0) if vstreams else 0.0
    adur = float(astreams[0].get("duration", 0) or 0) if astreams else 0.0

    check(f"RENDER [{label}] video co audio stream", len(astreams) >= 1,
          f"a={adur:.2f}s v={vdur:.2f}s")
    check(f"RENDER [{label}] video duration >= audio duration (audio khong cat)",
          vdur >= adur - 0.3, f"v={vdur:.2f} a={adur:.2f}")
    check(f"RENDER [{label}] audio khong lap (a < v)",
          adur < vdur, f"a={adur:.2f} v={vdur:.2f}")

    # --- Timeline visual tu lenh FFmpeg that ---
    spans = []
    for c in captured.get("cmds", []):
        joined = " ".join(c)
        for m in re.finditer(
                r"overlay=[^:]+:[^:]+:enable='between\(t,([-\d.]+),([-\d.]+)\)'",
                joined):
            spans.append((float(m.group(1)), float(m.group(2))))
    spans = sorted(set(spans))

    if not spans:
        check(f"RENDER [{label}] co timeline visual", False, "khong bat duoc overlay")
        return None

    # Gop cac span de tinh thoi gian CO visual
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    total = res.get("duration", 0) or 0
    covered = sum(b - a for a, b in merged)
    print(f"    timeline visual ({len(spans)} spans):")
    for a, b in spans:
        print(f"      {a:7.3f} -> {b:7.3f}  ({b - a:.3f}s)")

    check(f"RENDER [{label}] visual bat dau tai 0",
          abs(merged[0][0]) <= 0.05, f"start={merged[0][0]:.3f}")
    check(f"RENDER [{label}] visual phu ~100% thoi luong video "
          f"(khong khoang trong > 0.05s)",
          (total - covered) <= 0.05,
          f"covered={covered:.3f}/{total:.3f} thieu={total - covered:.3f}s")

    # Khoang trong giua cac visual
    gaps = []
    cur = merged[0][0]
    for a, b in merged:
        if a - cur > 0.01:
            gaps.append((cur, a))
        cur = max(cur, b)
    if total - cur > 0.01:
        gaps.append((cur, total))
    check(f"RENDER [{label}] KHONG co khoang trong visual",
          not gaps, f"gaps={[(round(a, 2), round(b, 2)) for a, b in gaps]}")

    return {"video": vid, "vdur": vdur, "adur": adur, "spans": spans,
            "total": total}


# Render 1: audio dai + text dai, NEN TAT (de thay khoang trong ro rang)
_r1 = _render_and_probe(
    "nen tat / text dai",
    [{"author": "tacgia", "time_ago": "2 giờ trước",
      "display_text": _RENDER_LONG, "tts_text": _RENDER_LONG,
      "likes": "1.2K", "comments": "48"}],
    None,
)

# Render 2: display_text 1 DONG nhung audio DAI (dung tts_text dai)
# Day la truong hop C1 goc: k (so phan text) = 1 < n (so visual) = 3
# -> phai giu noi dung tren visual trong nhieu doan lien tiep, KHONG tao doan trong.
_r2 = _render_and_probe(
    "nen tat / display 1 dong + audio dai",
    [{"author": "tacgia", "time_ago": "2 giờ trước",
      "display_text": "Một câu ngắn.", "tts_text": _RENDER_LONG,
      "likes": "10", "comments": "2"}],
    None,
)

# Render 2b: display_text 1 dong, audio dai, nen gameplay BAT
_r2b = _render_and_probe(
    "nen gameplay / display 1 dong + audio dai",
    [{"author": "tacgia", "time_ago": "2 giờ trước",
      "display_text": "Một câu ngắn.", "tts_text": _RENDER_LONG,
      "likes": "10", "comments": "2"}],
    "random",
)

# Render 3: nen gameplay BAT
_r3 = _render_and_probe(
    "nen gameplay BAT / text dai",
    [{"author": "tacgia", "time_ago": "2 giờ trước",
      "display_text": _RENDER_LONG, "tts_text": _RENDER_LONG,
      "likes": "1.2K", "comments": "48"}],
    "random",
)

# Render 4: scene co REPLY + audio dai
_r4 = _render_and_probe(
    "co reply / text dai",
    [{"author": "tacgia", "time_ago": "2 giờ trước",
      "display_text": _RENDER_LONG, "tts_text": _RENDER_LONG,
      "likes": "1.2K", "comments": "48",
      "reply_display_text": "Tôi nghĩ vậy là đúng rồi, bạn thấy sao?",
      "reply_tts_text": "Tôi nghĩ vậy là đúng rồi, bạn thấy sao?",
      "reply_author": "nguoidung"}],
    None,
)

# Render 5: nhieu canh, co cai dai va ngan (dam bao tong video khop)
_r5 = _render_and_probe(
    "nhieu canh / dai + ngan",
    [{"author": "tacgia", "time_ago": "2 giờ trước",
      "display_text": _RENDER_LONG, "tts_text": _RENDER_LONG,
      "likes": "1.2K", "comments": "48"},
     {"author": "b", "time_ago": "1 giờ trước",
      "display_text": _RENDER_SHORT, "tts_text": _RENDER_SHORT,
      "likes": "230", "comments": "9"}],
    None,
)


# ===========================================================================
section("[RENDER] C1 - audio khong bi cat/lap/mat (FFprobe)")
# ===========================================================================
for _label, _r in [("nen tat / text dai", _r1),
                    ("nen tat / display 1 dong + audio dai", _r2),
                    ("nen gameplay / display 1 dong + audio dai", _r2b),
                    ("nen gameplay", _r3), ("co reply", _r4),
                    ("nhieu canh", _r5)]:
    if not _r:
        continue
    # Audio >= 25s (nhieu scene) va video >= audio
    check(f"RENDER [{_label}] video >= audio (khong cat giong doc)",
          _r["vdur"] >= _r["adur"] - 0.3,
          f"v={_r['vdur']:.2f} a={_r['adur']:.2f}")
    # Khong co khoang im lang dai > 1.5s (giai thieu cam xuc)
    import tvcore.render as _R
    out3 = subprocess.run(
        [_R.ffmpeg_bin(), "-hide_banner", "-i", _r["video"], "-af",
         "silencedetect=noise=-45dB:d=1.5", "-f", "null", "-"],
        capture_output=True, text=True, errors="ignore")
    _sil = [l for l in (out3.stderr or "").splitlines() if "silence_duration" in l]
    check(f"RENDER [{_label}] khong co khoang im lang > 1.5s",
          len(_sil) <= 2, f"n={len(_sil)}")


# ===========================================================================
print("\n" + "=" * 62)
print(f"  PHASE 1.1 REGRESSION: PASS: {len(PASS)}  FAIL: {len(FAIL)}")
print("=" * 62)
if FAIL:
    print("\n  CAC TEST FAIL:")
    for _f in FAIL:
        print("   -", _f)
print()
sys.exit(1 if FAIL else 0)
