"""
Xac minh cac bug Claude bao cao (Bang chung chay that).

Chay: python tools/verify_bugs.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tvcore.content import (_count_hits, classify_topic, keyword_set,
                             score_comment, spam_reasons, tokens)

print("--- Loi 2: stopwords khong khop ---")
ks = keyword_set("Toi khong biet phai noi gi ve chuyen cua ca ngay")
print("  keyword_set:", sorted(ks))
print("  => co 'toi'/'khong'/'cua' trong set?", any(
    w in ks for w in ["toi", "khong", "cua", "noi"]))

print()
print("--- Loi 2b: tokens co giu dau? ---")
print("  tokens('Toi khong biet') =", tokens("Toi khong biet"))
print("  tokens('Tôi không biết') =", tokens("Tôi không biết"))

print()
print("--- Loi 3: spam filter qua tay ---")
cases = [
    "Gia nha tang qua, khong mua noi",
    "Ai do ban xe re lam",
    "Mua ngay tai shop gia sale",
    "Banh mi o day ngon",
    "Toi se ky hoc ve lang manh",
]
for t in cases:
    print(f"  {t!r:45} -> {spam_reasons(t)}")

print()
print("--- Loi 4: substring matching ---")
for t in ["text hay qua", "next level", "velocity", "vu an oi", "example"]:
    print(f"  {t!r:18} hits(vl/ex/vu) = {_count_hits(t, {'vl', 'ex', 'vu'})}")
print("  classify_topic('text hay qua') ->", classify_topic("text hay qua"))
print("  classify_topic('next lau chuyen') ->", classify_topic("next lau chuyen"))

print()
print("--- Loi 6: likes co dung khong? ---")
a = score_comment("Binh luan rat hay va gay thao luan manh", "bai viet")
b = score_comment("Binh luan rat hay va gay thao luan manh", "bai viet", likes=99999)
print(f"  likes=0     -> total={a['total']}")
print(f"  likes=99999 -> total={b['total']}")
print("  => likes thay doi diem khong?", a["total"] != b["total"])
