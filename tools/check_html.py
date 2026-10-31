"""Kiem tra cau truc HTML cua index.html."""
import re
import sys

h = open("index.html", encoding="utf-8").read()

ok = True
for t in ("div", "section", "button", "label", "textarea", "select", "script", "style"):
    o = len(re.findall(r"<" + t + r"[\s>]", h))
    c = len(re.findall(r"</" + t + r">", h))
    status = "OK" if o == c else "MISMATCH"
    if o != c:
        ok = False
    print(f"{t}: open={o} close={c} {status}")

ids = re.findall(r'id="([^"]+)"', h)
dup = sorted({i for i in ids if ids.count(i) > 1})
print("duplicate ids:", dup if dup else "none")
if dup:
    ok = False

# Kiem tra cac id ma JS su dung co ton tai trong HTML
js = re.findall(r"<script>(.*?)</script>", h, re.S)[-1]
used = set(re.findall(r'\$\("([^"]+)"\)', js))
missing = sorted(used - set(ids))
print("JS references missing ids:", missing if missing else "none")
if missing:
    ok = False

sys.exit(0 if ok else 1)
