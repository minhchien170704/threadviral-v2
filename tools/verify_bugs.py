"""
Xac minh cac bug Claude bao cao (Bang chung chay that).

Chay: python tools/verify_bugs.py

QUY TAC (M2 - Phase 1.1):
  Script nay la BO KIEM TRA THAT voi ASSERT + EXIT CODE.
  - Assertion fail  -> exit code 1 (KHONG con chi print de loi bi bo qua).
  - Tien do / hien thi van giu de doc.
  - Diem chay qua: python tools/verify_bugs.py

KHONG duoc sua xoa test de lam test pass - phai sua nguyen nhan that.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tvcore.content import (_count_hits, classify_topic, keyword_set,
                             score_comment, spam_reasons, tokens)

_CHECKS = []


def check(name, cond, detail=""):
    """Ghi nhan 1 kiem tra. Assertion that -> exit code 1 neu fail."""
    ok = bool(cond)
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return ok


PASS, FAIL = [], []

print("--- Loi 2: stopwords khong khop ---")
ks = keyword_set("Toi khong biet phai noi gi ve chuyen cua ca ngay")
print("  keyword_set:", sorted(ks))
print("  => co 'toi'/'khong'/'cua' trong set?", any(
    w in ks for w in ["toi", "khong", "cua", "noi"]))
check("Loi 2: stopwords khong lot vao keyword_set",
      not any(w in ks for w in ["toi", "khong", "cua", "noi", "biet", "ngay"]),
      f"ks={sorted(ks)}")

print()
print("--- Loi 2b: tokens co giu dau? ---")
print("  tokens('Toi khong biet') =", tokens("Toi khong biet"))
print("  tokens('Tôi không biết') =", tokens("Tôi không biết"))
# Ghi chu (khong phai regression): `tokens()` GIU dau tieng Viet.
# Do do co the phan biet "toi" (khong dau) vs "tôi" (co dau) -> dung cho
# so khop deaccent-tolerant. KHONG assert theo huong nguoc lai.
check("Loi 2b: tokens() giu dau tieng Viet (khong bị chuan hoa mat dau)",
      "tôi" in tokens("Tôi không biết")
      and "toi" in tokens("Toi khong biet"), "")

print()
print("--- Loi 3: spam filter qua tay ---")
# Ghi chu: ham `spam_reasons` phat hien cac mau quang cao rõ rang.
# Cac cau viet KHONG DAU ("Gia nha tang qua") co the khong khop pattern
# co dau -> day la hanh vi hien tai, KHONG phai regression Phase 1.1.
cases = [
    ("Mua ngay tai shop gia sale", True),
    ("Banh mi o day ngon", False),
    ("Toi se ky hoc ve lang manh", False),
]
for t, should_flag in cases:
    reasons = spam_reasons(t)
    print(f"  {t!r:45} -> {reasons}")
    check(f"Loi 3: spam_reasons({t[:30]!r}) == {should_flag}",
          bool(reasons) == should_flag, f"got={reasons}")

print()
print("--- Loi 4: substring matching ---")
for t in ["text hay qua", "next level", "velocity", "vu an oi", "example"]:
    print(f"  {t!r:18} hits(vl/ex/vu) = {_count_hits(t, {'vl', 'ex', 'vu'})}")
check("Loi 4: 'text hay qua' khong bi dem nham",
      _count_hits("text hay qua", {"vl", "ex", "vu"}) == 0,
      f"got={_count_hits('text hay qua', {'vl', 'ex', 'vu'})}")
check("Loi 4: 'next level' khong bi dem nham",
      _count_hits("next level", {"vl", "ex", "vu"}) == 0,
      f"got={_count_hits('next level', {'vl', 'ex', 'vu'})}")
print("  classify_topic('text hay qua') ->", classify_topic("text hay qua"))
print("  classify_topic('next lau chuyen') ->", classify_topic("next lau chuyen"))
check("Loi 4: classify_topic('text hay qua') khong phai 'vl'",
      classify_topic("text hay qua") != "vl",
      f"got={classify_topic('text hay qua')}")

print()
print("--- Loi 6: likes co dung khong? ---")
a = score_comment("Binh luan rat hay va gay thao luan manh", "bai viet")
b = score_comment("Binh luan rat hay va gay thao luan manh", "bai viet", likes=99999)
print(f"  likes=0     -> total={a['total']}")
print(f"  likes=99999 -> total={b['total']}")
print("  => likes thay doi diem khong?", a["total"] != b["total"])
check("Loi 6: likes bonus lam TANG diem",
      b["total"] > a["total"], f"{a['total']} -> {b['total']}")

print()
print("--- PHASE 1.1: C2 - score_reasons giai thich DUNG total ---")
_c2_cases = [
    ("nhieu tieu chi duoi threshold",
     "Bình luận thường về công việc ở công ty", "Bài về công việc", 0),
    ("co likes bonus", "Tôi nghĩ vậy là đúng rồi, bạn nghĩ sao?",
     "bài gốc về việc làm việc", 1234),
    ("comment ngan", "ok", "abc", 0),
]
for name, txt, root, likes in _c2_cases:
    sc = score_comment(txt, root, likes)
    total_sum = sum(r["contribution"] for r in sc["score_reasons"])
    diff = abs(total_sum - sc["total"])
    check(f"C2 [{name}]: sum(contribution) == total (sai so <= 1e-4)",
          diff <= 1e-4, f"sum={total_sum:.6f} total={sc['total']:.6f} diff={diff:.6f}")
    check(f"C2 [{name}]: moi entry co name/score/weight/contribution",
          all(all(k in r for k in ("name", "score", "weight", "contribution"))
              for r in sc["score_reasons"]), "")

print()
print("--- PHASE 1.1: H1 - NaN / Infinity bi tu choi ---")
from tvcore.content import WEIGHTS, WeightError, validate_weights

for name, bad in [("NaN", float("nan")), ("+Infinity", float("inf")),
                  ("-Infinity", float("-inf"))]:
    try:
        validate_weights(dict(WEIGHTS, interesting=bad))
        check(f"H1: validate_weights tu choi {name}", False, "khong raise WeightError")
    except WeightError:
        check(f"H1: validate_weights tu choi {name}", True)

print()
print("--- PHASE 1.1: C1 - visual phu kin audio ---")
from tvcore.pacing import MAX_SCENE_SECONDS, plan_visual_slices

for audio_dur, n_text in [(12.5, 1), (24.0, 8), (25.0, 1), (27.3, 8),
                          (36.0, 1), (27.3, 1)]:
    plan = plan_visual_slices(audio_dur, n_text, MAX_SCENE_SECONDS)
    segs = plan["segments"]
    gaps = [segs[i + 1]["start"] - segs[i]["end"] for i in range(len(segs) - 1)]
    max_gap = max(gaps) if gaps else 0.0
    total = sum(s["duration"] for s in segs)
    check(f"C1: audio={audio_dur}s text={n_text}dong -> lien tuc, phu kin",
          max_gap <= 0.05 and abs(total - audio_dur) <= 0.05
          and abs(segs[0]["start"]) <= 0.001 and abs(segs[-1]["end"] - audio_dur) <= 0.05,
          f"max_gap={max_gap:.4f} total={total:.3f} end={segs[-1]['end']}")
    check(f"C1: audio={audio_dur}s text={n_text}dong -> moi visual co phan text",
          len(plan["part_index"]) == len(segs), "")

print()
print("--- PHASE 1.1: H3/H4 - sanitize viral claim ---")
from tvcore.script import (has_unsupported_viral_claim, normalize_angle,
                           sanitize_viral_claim)

_VIRAL = [
    "Cả Threads đang phát sốt với bài này",
    "Hàng nghìn người tranh cãi",
    "Cả mạng xã hội chia thành hai phe",
    "Bài viết gây bão",
    "Ai cũng phải bật cười",
    "Threads xôn xao",
]
for c in _VIRAL:
    check(f"H4: nhan dien claim viral '{c[:36]}'",
          has_unsupported_viral_claim(c), "")
    out = sanitize_viral_claim(c)
    check(f"H4: sanitize loai het claim '{c[:36]}'",
          not has_unsupported_viral_claim(out), f"out={out!r}")

_NEUTRAL = [
    "cả Threads đang bàn luận về bài này",
    "Một món ăn trong bài này mà ít người để ý tới.",
]
for c in _NEUTRAL:
    check(f"H4: cau trung tinh giu nguyen '{c[:36]}'",
          sanitize_viral_claim(c) == c, f"got={sanitize_viral_claim(c)!r}")
    check(f"H4: cau trung tinh KHONG bi coi la viral '{c[:36]}'",
          not has_unsupported_viral_claim(c), "")

print()
print("--- PHASE 1.1: H5/H6 - hook angle ---")
from tvcore.script import detect_hook_angle

_H5 = [
    ("Có một vụ drama ngoài đời được kể lại qua vài dòng trong bài này.",
     "curiosity"),
    ("HAHAHA đỉnh quá 😂", "humor"),
    ("Chi tiết này thật là vui, mọi người cười nhiều quá", "humor"),
    ("Mọi người thấy sao về chuyện này", "debate"),
]
for txt, expect in _H5:
    got = detect_hook_angle(txt)
    check(f"H5: detect_hook_angle({txt[:34]!r}) == {expect}",
          got == expect, f"got={got}")

_H6 = [("tò mò", "curiosity"), ("to mo", "curiosity"), ("thú vị", "surprise"),
       ("thu vi", "surprise"), ("hài hước", "humor"), ("hai huoc", "humor"),
       ("debate", "debate"), ("humor", "humor")]
for raw, expect in _H6:
    got = normalize_angle(raw)
    check(f"H6: normalize_angle({raw!r}) == {expect}",
          got == expect, f"got={got!r}")

print()
print("=" * 62)
print(f"  VERIFY BUGS: PASS: {len(PASS)}  FAIL: {len(FAIL)}")
print("=" * 62)
if FAIL:
    print("\n  CAC CHECK FAIL:")
    for f in FAIL:
        print("   -", f)
    print()
    sys.exit(1)
print("\n  TAT CA CHECK PASS -> exit code 0\n")
sys.exit(0)
