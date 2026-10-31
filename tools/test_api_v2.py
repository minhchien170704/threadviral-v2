"""Test các API mới của Giai đoạn 1-4."""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"


def post(path, data):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


SCENES = [
    {"author": "a",
     "display_text": "Mình mới chia tay bạn trai sau 3 năm, không biết nói gì nữa. Bạn nghĩ sao?",
     "tts_text": "Mình mới chia tay bạn trai sau 3 năm, không biết nói gì nữa. Bạn nghĩ sao?"},
    {"author": "b",
     "display_text": "Câu chuyện này hay quá, tôi cũng từng trải qua chuyện tương tự",
     "tts_text": "Câu chuyện này hay quá"},
    {"author": "c",
     "display_text": "Câu chuyện này hay quá, tôi cũng từng trải qua chuyện tương tự",
     "tts_text": "Câu chuyện này hay quá"},
    {"author": "d",
     "display_text": "mua ngay tai https://shopee.vn/xyz gia re luon",
     "tts_text": "mua ngay"},
    {"author": "e",
     "display_text": "Luật đá phạt mới khiến mọi người tranh luận gay gắt",
     "tts_text": "Luật đá phạt mới"},
    {"author": "f",
     "display_text": "Bạn nghĩ sao? Tôi thấy vẫn nên chờ thêm một chút",
     "tts_text": "Bạn nghĩ sao"},
]

print("=" * 62)
print("=== /api/analyze ===")
r = post("/api/analyze", {"scenes": SCENES, "max_comments": 4})
a = r["analysis"]
print(f"Chu de: {a['topic']} ({a['topic_confidence']})")
print(f"Hap dan: {a['appeal_label']} ({a['appeal']})")
print(f"Do dai: {a['length_label']} ({a['words']} tu) | Co cau hoi: {a['has_question']}")
s = r["stats"]
print(f"Stats: total={s['total']} scored={s['scored']} dup={s['duplicate']} "
      f"spam={s['spam']} invalid={s['invalid']} selected={s['selected']}")
print("--- Binh luan duoc chon ---")
for x in r["selected"]:
    print(f"  {x['score']*100:5.1f}  {x['text'][:65]}  {x['reasons']}")

print()
print("=" * 62)
print("=== /api/script ===")
r = post("/api/script", {"scenes": SCENES, "n_hooks": 3})
print(f"Source: {r['source']}")
print(f"Summary: {r['summary']}")
print(f"Transition: {r['transition']}")
print(f"Editorial: {r['editorial']}")
print(f"CTA: {r['cta_text']}")
print("--- 3 hook ---")
for i, h in enumerate(r["hooks"]):
    print(f"  [{h['label']}] {h['text']}")

print()
print("=" * 62)
print("=== /api/timeline ===")
r = post("/api/timeline", {"scenes": SCENES, "speed": "+20%"})
print(f"Tong thoi luong: {r['total_duration']}s")
for i, p in enumerate(r["plan"]):
    print(f"  Canh {i+1}: {p['duration']}s ({p['words']} tu, uoc tinh={p['estimated']})")

print()
print("=" * 62)
print("=== /api/qc ===")
r = post("/api/qc", {"scenes": SCENES, "watermark_text": "Fanpage: Ghiền đọc comment"})
print(f"OK: {r['ok']} | summary: {r['summary']}")
for i in r["issues"]:
    print(f"  [{i['severity']}] {i['message']}")
