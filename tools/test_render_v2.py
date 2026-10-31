"""Test render end-to-end voi cac tinh nang Giai doan 1-4."""
import json
import time
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
    {"author": "author_a", "time_ago": "1 ngày",
     "display_text": "Mình mới chia tay bạn trai sau 3 năm, không biết nói gì nữa. Bạn nghĩ sao?",
     "tts_text": "Mình mới chia tay bạn trai sau 3 năm, không biết nói gì nữa. Bạn nghĩ sao?",
     "likes": "2,1K", "comments": "571", "reposts": "438", "shares": "195"},
    {"author": "user_b", "time_ago": "3 giờ",
     "display_text": "Câu chuyện này hay quá, tôi cũng từng trải qua chuyện tương tự",
     "tts_text": "Câu chuyện này hay quá, tôi cũng từng trải qua chuyện tương tự",
     "likes": "120", "comments": "8", "reposts": "3", "shares": "1"},
    {"author": "user_c", "time_ago": "5 giờ",
     "display_text": "Câu chuyện này hay quá, tôi cũng từng trải qua chuyện tương tự",
     "tts_text": "Câu chuyện này hay quá, tôi cũng từng trải qua chuyện tương tự",
     "likes": "95", "comments": "4", "reposts": "1", "shares": "0"},
    {"author": "user_d", "time_ago": "2 giờ",
     "display_text": "mua ngay tai https://shopee.vn/xyz gia re luon nha",
     "tts_text": "mua ngay",
     "likes": "10", "comments": "1", "reposts": "0", "shares": "0"},
    {"author": "user_e", "time_ago": "6 giờ",
     "display_text": "Bạn nghĩ sao? Tôi thấy vẫn nên chờ thêm một chút",
     "tts_text": "Bạn nghĩ sao? Tôi thấy vẫn nên chờ thêm một chút",
     "likes": "340", "comments": "27", "reposts": "9", "shares": "4"},
    {"author": "user_f", "time_ago": "8 giờ",
     "display_text": "Luật đá phạt mới khiến mọi người tranh luận gay gắt",
     "tts_text": "Luật đá phạt mới khiến mọi người tranh luận gay gắt",
     "likes": "210", "comments": "12", "reposts": "5", "shares": "2"},
]

print("=== Render voi smart_filter + transition + editorial ===")
r = post("/api/render", {
    "scenes": SCENES,
    "voice": "vi-VN-HoaiMyNeural",
    "speed": "+20%",
    "watermark_text": "Fanpage: Ghiền đọc comment",
    "bg_video_path": "random",
    "hook_text": "Câu chuyện này đang chia Threads thành hai phe.",
    "cta_text": "Bạn thuộc phe nào? Comment bên dưới nhé! 👇",
    "transition_text": "Và đây là phần mọi người bàn luận nhiều nhất:",
    "editorial_text": "Điều đáng chú ý nhất là câu hỏi ở cuối bài.",
    "smart_filter": True,
    "skip_qc": False,
})
print(f"Job: {r.get('job_id')}")

# Cho
for _ in range(240):
    time.sleep(1.5)
    with urllib.request.urlopen(f"{BASE}/api/render/{r['job_id']}/status", timeout=30) as rr:
        job = json.loads(rr.read().decode("utf-8"))
    if job["status"] in ("done", "error"):
        break
    print(f"  {job['progress']*100:.0f}% {job['message']}")

if job["status"] == "error":
    print("ERROR:", job["error"])
    raise SystemExit(1)

res = job["result"]
print(f"Video: {res['filename']} | {res['duration']}s")
qc = res.get("qc") or {}
print(f"QC hậu render: {qc.get('summary')}")
for i in (qc.get("issues") or []):
    print(f"  [{i['severity']}] {i['message']}")
pre = res.get("qc_precheck") or {}
print(f"QC trước render: ok={pre.get('ok')} {pre.get('summary')}")
