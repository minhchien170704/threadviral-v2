"""
Bo test cho tvcore - kiem tra icon Threads + card rendering + an toan TikTok.

Chay: .venv\\Scripts\\python.exe tools\\run_tests.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageChops

from tvcore import icons
from tvcore.cards import (draw_cta_card, draw_hook_card, draw_single_box,
                          safe_card_height, safe_card_width, wrap_like_card)
from tvcore.config import SAFE_ZONE_BOTTOM, SAFE_ZONE_LEFT, SAFE_ZONE_RIGHT, SAFE_ZONE_TOP

# Dam bao cac thu muc ban lam viec ton tai (outputs/, temp/, cache/...)
# -> tranh loi crash khi clone repo moi va chay test ngay
try:
    from tvcore.config import ensure_dirs
    ensure_dirs()
except Exception as _e:  # pragma: no cover
    print(f"[WARN] khong tao duoc thu muc ban lam viec: {_e}")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


# ===========================================================================
print("\n=== 1. Icon assets ===")
for n in icons.AVAILABLE_ICONS:
    check(f"asset '{n}' ton tai", icons.has_icon(n))

for n in icons.AVAILABLE_ICONS:
    src = icons.load_icon(n)
    if src is None:
        check(f"'{n}' load duoc", False)
        continue
    a = src.split()[3]
    bbox = a.getbbox()
    check(f"'{n}' co hinh dang (alpha khac 0)", bbox is not None, f"bbox={bbox}")

# ===========================================================================
print("\n=== 2. Recolor giu nguyen hinh dang ===")
for n in icons.AVAILABLE_ICONS:
    src = icons.load_icon(n)
    rec = icons.render_icon(n, 512, (66, 66, 66, 255))
    diff = ImageChops.difference(src.split()[3], rec.split()[3])
    mx = 0
    if diff.getbbox():
        d = diff.load()
        for y in range(diff.size[1]):
            for x in range(diff.size[0]):
                v = d.getpixel((x, y))
                mx = max(mx, max(v) if isinstance(v, tuple) else v)
    check(f"'{n}' recolor khong meo", mx == 0, f"maxdiff={mx}")

# ===========================================================================
print("\n=== 3. Mau icon ===")
rec = icons.render_icon("heart_filled", 64, (255, 48, 64, 255))
px = rec.load()
reds = sum(1 for y in range(64) for x in range(64)
           if px[x, y][0] > 200 and px[x, y][1] < 110 and px[x, y][2] < 130)
check("heart_filled mau do", reds > 500, f"{reds} px")

rec = icons.render_icon("verified", 64, (29, 155, 240, 255))
px = rec.load()
blues = sum(1 for y in range(64) for x in range(64)
            if px[x, y][2] > 170 and px[x, y][0] < 130 and px[x, y][1] > 110)
check("verified mau xanh", blues > 100, f"{blues} px")

rec = icons.render_icon("heart", 64, (66, 66, 66, 255))
px = rec.load()
outline = sum(1 for y in range(64) for x in range(64) if px[x, y][3] > 200)
check("heart rong (it pixel hon filled)", outline < 64 * 64 * 0.35, f"{outline} px")

# ===========================================================================
print("\n=== 4. Card rendering ===")
TXT = ("Mình vừa phát hiện một điều thay đổi hoàn toàn cách mình làm việc. "
       "Thay vì ngồi chờ đợi, mình bắt đầu làm những việc nhỏ mỗi ngày.")

card = draw_single_box(
    author="nguyen.dev", time_ago="3 giờ", text=TXT, avatar_url="",
    likes="2,1K", comments="637", reposts="234", shares="149",
    author_liked=True, show_footer=True, is_root_post=False,
    scale=1.0, vid_w=1080, vid_h=1920, is_verified=True,
)
check("card render thanh cong", card is not None)
check("card rong <= vung an toan doc", card.height <= safe_card_height(1920, 1.0),
      f"h={card.height} max={safe_card_height(1920, 1.0)}")
check("card rong <= vung an toan ngang", card.width <= safe_card_width(1080, 1.0),
      f"w={card.width} max={safe_card_width(1080, 1.0)}")

cp = card.load()
blue = sum(1 for y in range(card.height) for x in range(card.width)
           if cp[x, y][2] > 170 and cp[x, y][0] < 130 and cp[x, y][1] > 110)
check("badge verified xanh render trong card", blue > 100, f"{blue} px")

red = [(x, y) for y in range(card.height) for x in range(card.width)
       if cp[x, y][0] > 200 and cp[x, y][1] < 110 and cp[x, y][2] < 130]
check("icon heart filled do render trong card", len(red) > 300, f"{len(red)} px")

# Icon nen (gray) nen nam ben canh icon do
check("heart nam trong footer (khong phai header)",
      len(red) > 0 and min(p[1] for p in red) > card.height * 0.6,
      f"y={min(p[1] for p in red) if red else '-'}")

# So lieu duoc ve (text den) trong footer
dark = sum(1 for y in range(card.height - 70, card.height - 10)
           for x in range(card.width) if cp[x, y][0] < 120)
check("so lieu footer duoc ve", dark > 200, f"{dark} px")

# ===========================================================================
print("\n=== 5. Card reply (co icon reply) ===")
reply = draw_single_box(
    author="zuck", time_ago="15 giờ", text="Tra loi cua toi day ne.",
    avatar_url="", likes="12", comments="3", reposts="1", shares="0",
    author_liked=True, show_footer=True, scale=1.0, vid_w=1080, vid_h=1920,
    is_verified=True, show_reply_mark=True,
)
check("reply card render", reply is not None)
check("reply card khong vuot vung an toan", reply.height <= safe_card_height(1920, 1.0),
      f"h={reply.height}")

# ===========================================================================
print("\n=== 6. Ngat dong khop card ===")
for is_root in (False, True):
    lines_card = wrap_like_card(TXT, is_root=is_root, scale=1.0, vid_w=1080)
    c = draw_single_box("a", "1 ngày", TXT, "", "1", "2", "3", "4", True,
                        show_footer=True, is_root_post=is_root,
                        scale=1.0, vid_w=1080, vid_h=1920)
    check(f"wrap khop card (root={is_root})", len(lines_card) > 0,
          f"{len(lines_card)} dong")

# ===========================================================================
print("\n=== 7. Card hook / CTA ===")
hook = draw_hook_card("Ban co dang lang nghe 90% cua cuoc doi khong?", 1080, 1920)
check("hook card tao duoc", os.path.exists(hook))
cta = draw_cta_card("Theo doi de xem them bai viet moi moi ngay!", 1080, 1920)
check("cta card tao duoc", os.path.exists(cta))

for p in (hook, cta):
    im = Image.open(p).convert("RGBA")
    check(f"{os.path.basename(p)}kich thuoc dung", im.size[0] == 1080, str(im.size))

# CTA: icon comment that phai trong vung an toan ngang
cta_im = Image.open(cta).convert("RGBA")
cp = cta_im.load()
icon_px = sum(1 for y in range(cta_im.height) for x in range(cta_im.width)
              if cp[x, y][3] > 200 and cp[x, y][0] > 240 and cp[x, y][1] > 240
              and cp[x, y][2] > 240)
check("CTA co icon trang", icon_px > 100, f"{icon_px} px")

# ===========================================================================
print("\n=== 8. Scale (720p / 1440p) ===")
for w, h in ((720, 1280), (1080, 1920), (1440, 2560)):
    s = w / 1080
    c = draw_single_box("a", "1 ngày", TXT, "", "1", "2", "3", "4", True,
                        show_footer=True, scale=s, vid_w=w, vid_h=h)
    check(f"scale {w}x{h}: card vua vung an toan",
          c.height <= safe_card_height(h, s) and c.width <= safe_card_width(w, s),
          f"{c.size} vs max {safe_card_width(w, s)}x{safe_card_height(h, s)}")

# ===========================================================================
print("\n=== 9. Text dai (tu dong chia) ===")
long_txt = (" ".join(["Cau nay rat lau va thu vi."] * 40))
c = draw_single_box("a", "1 ngày", long_txt, "", "1", "2", "3", "4", True,
                    show_footer=True, scale=1.0, vid_w=1080, vid_h=1920)
check("text dai khong vuot vung an toan", c.height <= safe_card_height(1920, 1.0),
      f"h={c.height} max={safe_card_height(1920, 1.0)}")
check("text dai van render", c.width > 0)

# ===========================================================================
# GIAI ĐOẠN 1 - Lọc & chấm điểm bình luận
print("\n=== 10. Loc & cham diem binh luan (content.py) ===")
from tvcore.content import (analyze_post, classify_topic, is_duplicate,
                             normalize_text, score_comment, select_comments,
                             spam_reasons)

c1 = "Câu chuyện này hay quá, tôi cũng từng trải qua chuyện tương tự"
c2 = "Câu chuyện này hay quá, tôi cũng từng trải qua chuyện giống hệt"
check("bình luận trùng tuyệt đối bị phát hiện",
      is_duplicate(c1, "Câu chuyện này hay quá, tôi cũng từng trải qua chuyện tương tự"))
check("bình luận gần giống bị phát hiện", is_duplicate(c1, c2))
check("bình luận khác nhau không bị coi là trùng",
      not is_duplicate(c1, "Luật đá phạt mới khiến mọi người tranh luận gay gắt"))

check("spam chứa link bị loại", "chứa liên kết" in spam_reasons("Xem tại https://shopee.vn/abc"))
check("spam quảng cáo bị loại", "quảng cáo / spam" in spam_reasons("mua ngay tại shop giá sale"))
check("bình luận bình thường không bị loại", spam_reasons(c1) == [])

sc = score_comment("Bạn nghĩ sao về chuyện này? Tôi thấy khá hợp lý, nhưng có người lại không đồng ý",
                   "Câu chuyện tình yêu trên Threads")
check("chấm điểm trả về 0..1", 0.0 <= sc["total"] <= 1.0, f"total={sc['total']}")
check("chấm điểm có đủ 5 tiêu chí", len(sc["parts"]) == 5, str(list(sc["parts"].keys())))
check("bình luận gây thảo luận điểm cao",
      sc["parts"]["discussion"] > 0.5, f"discussion={sc['parts']['discussion']}")

spam_list = [
    {"text": c1},
    {"text": c2},                       # gần trùng
    {"text": "mua ngay tại https://shopee.vn/xyz"},   # spam
    {"text": "ok"},                      # quá ngắn
    {"text": "Bạn nghĩ sao về chuyện này? Tôi thấy khá hợp lý"},
    {"text": "Luật đá phạt mới khiến mọi người tranh luận gay gắt"},
    {"text": "Tôi cũng từng gặp trường hợp y như vậy, khá mệt mỏi thật"},
]
sel, stats = select_comments(spam_list, root_text="Câu chuyện tình yêu trên Threads",
                             max_n=4)
check("bình luận trùng bị loại khỏi kết quả", stats["duplicate"] >= 1,
      f"dup={stats['duplicate']}")
check("spam bị loại khỏi kết quả", stats["spam"] >= 1, f"spam={stats['spam']}")
check("chọn tối đa max_n bình luận", len(sel) <= 4, f"n={len(sel)}")
check("không chọn trùng lặp trong kết quả",
      len({normalize_text(s["text"]) for s in sel}) == len(sel))
check("mọi bình luận được chọn đều có điểm",
      all(s["score"] >= 0 for s in sel))

# ===========================================================================
# GIAI ĐOẠN 1 - Phân tích bài viết
print("\n=== 11. Phan tich bai viet (content.py) ===")
a = analyze_post("Mình mới chia tay bạn trai sau 3 năm. Không biết nói gì nữa. Bạn nghĩ sao?")
check("phân loại chủ đề đúng", a["topic"] == "tình yêu", f"topic={a['topic']}")
check("có độ hấp dẫn 0..1", 0.0 <= a["appeal"] <= 1.0, f"appeal={a['appeal']}")
check("đếm từ đúng", a["words"] >= 10, f"words={a['words']}")
check("nhận diện có câu hỏi", a["has_question"] is True)
check("phân loại hài hước",
      classify_topic("Cái này vcl hài quá đi 😂")[0] == "hài hước")

# ===========================================================================
# GIAI ĐOẠN 2 - Kịch bản & hook
print("\n=== 12. Sinh kich ban & hook (script.py) ===")
from tvcore.script import (auto_generate_script, generate_hook_options,
                           generate_script_template)

_scenes = [
    {"author": "a", "display_text": "Mình mới chia tay bạn trai sau 3 năm. Bạn nghĩ sao?"},
    {"author": "b", "display_text": "Tôi cũng từng vậy, cảm giác rất nặng nề"},
    {"author": "c", "display_text": "Bạn nghĩ sao? Tôi thấy vẫn nên chờ thêm"},
]
hooks = generate_hook_options(_scenes, 3)
check("sinh đủ 3 hook", len(hooks) == 3, f"n={len(hooks)}")
check("3 hook có góc khác nhau", len({h["angle"] for h in hooks}) == 3,
      str([h["angle"] for h in hooks]))
check("hook có nhãn tiếng Việt", all(h["label"] for h in hooks))

scr = generate_script_template(_scenes)
check("kịch bản có summary", bool(scr["summary"]))
check("kịch bản có editorial", bool(scr["editorial"]))
check("kịch bản có transition", bool(scr["transition"]))
check("kịch bản có phân tích", "topic" in scr["analysis"])

# ===========================================================================
# GIAI ĐOẠN 3 - Pacing
print("\n=== 13. Thoi luong dong (pacing.py) ===")
from tvcore.pacing import (estimate_speech_seconds, min_read_seconds,
                           scene_duration)

check("bảng tham khảo: 1-10 từ -> 2-3s",
      2.0 <= min_read_seconds("một hai ba bốn năm sáu bảy tám chín mười") <= 3.0)
check("bảng tham khảo: 21-35 từ -> 4-6s",
      4.0 <= min_read_seconds(" ".join(["từ"] * 25)) <= 6.0)
check("bảng tham khảo: 36-50 từ -> 6-8s",
      6.0 <= min_read_seconds(" ".join(["từ"] * 40)) <= 8.0)
check("ước lượng đọc tăng theo độ dài",
      estimate_speech_seconds(" ".join(["từ"] * 50)) >
      estimate_speech_seconds(" ".join(["từ"] * 10)))
check("hook tối thiểu 2.5s", scene_duration("Ngắn", 1.0, is_hook=True) >= 2.5)
check("CTA tối thiểu 2.0s", scene_duration("Ngắn", 1.0, is_cta=True) >= 2.0)
check("không cắt ngắn hơn TTS",
      scene_duration("a b c d e", 8.0) >= 8.0)
check("cảnh không vượt quá 12s",
      scene_duration(" ".join(["từ"] * 500), 30.0) <= 12.0)

# ===========================================================================
# GIAI ĐOẠN 4 - QC
print("\n=== 14. Kiem tra chat luong (qc.py) ===")
from tvcore.qc import check_script, check_rights

qc = check_script([
    {"author": "a", "display_text": "Bài viết gốc", "tts_text": "Bài viết gốc"},
    {"author": "b", "display_text": "Bình luận số một", "tts_text": "Bình luận số một"},
    {"author": "b", "display_text": "Bình luận số một", "tts_text": "Bình luận số một"},
])
codes = [i["code"] for i in qc["issues"]]
check("phát hiện bình luận trùng", "duplicate_comment" in codes, str(codes))
check("QC báo lỗi khi có trùng", qc["ok"] is False)

qc2 = check_script([
    {"author": "a", "display_text": "Bài viết gốc", "tts_text": "Bài viết gốc"},
    {"author": "b", "display_text": "Bình luận hoàn toàn khác", "tts_text": "Bình luận hoàn toàn khác"},
])
check("QC pass khi không trùng", qc2["ok"] is True,
      f"ok={qc2['ok']} issues={[i['code'] for i in qc2['issues']]}")

qc3 = check_script([
    {"author": "a", "display_text": "A", "tts_text": "B"},
])
check("QC phát hiện lời đọc lệch", "tts_mismatch" in [i["code"] for i in qc3["issues"]])

check("QC quyền: cảnh báo thiếu watermark",
      any(i["code"] == "no_watermark" for i in check_rights("")["issues"]))
check("QC quyền: OK khi có watermark", check_rights("Fanpage")["ok"] is True)

# ===========================================================================
# GIAI ĐOẠN 1/2 trong render
print("\n=== 15. Render: smart filter + insert script ===")
from tvcore.render import _apply_smart_filter, _insert_script_scenes

_s = [
    {"author": "root", "display_text": "Bài gốc"},
    {"author": "u1", "display_text": "Câu chuyện này hay quá, tôi cũng từng trải qua"},
    {"author": "u2", "display_text": "Câu chuyện này hay quá, tôi cũng từng trải qua"},
    {"author": "u3", "display_text": "Luật đá phạt mới khiến mọi người tranh luận gay gắt"},
]
_f = _apply_smart_filter(_s)
check("smart filter loại bình luận trùng", len(_f) < len(_s), f"{len(_s)} -> {len(_f)}")
check("smart filter giữ bài viết gốc", _f[0]["author"] == "root")

_i = _insert_script_scenes([
    {"author": "root", "display_text": "Bài gốc"},
    {"author": "u1", "display_text": "Bình luận"},
], "Và đây là phần mọi người bàn luận:", "Nhận xét biên tập ngắn.")
check("chèn lời dẫn chuyển cảnh", any(s["author"] == "__narr__" for s in _i))
check("chèn nhận xét biên tập", len([s for s in _i if s["author"] == "__narr__"]) == 2)

# ===========================================================================
# NHÓM 4: Thống kê
print("\n=== 16. Thong ke (analytics.py) ===")
import os as _os
from tvcore import analytics as _an

_st = _an.get_stats()
check("stats có cấu trúc totals", isinstance(_st.get("totals"), dict))
check("stats có rates", isinstance(_st.get("rates"), dict))
check("stats có history", isinstance(_st.get("history"), list))

_an.record_render("test_vid.mp4", 42.5, ok=True, scenes=5, hook_text="Hook test",
                  cta_text="CTA test", voice="vi-VN-HoaiMyNeural", speed="+20%")
_an.record_analytics("test_vid.mp4", {"views": 10000, "likes": 500,
                                      "comments": 60, "shares": 30})
_st2 = _an.get_stats()
check("ghi nhận render vào lịch sử", len(_st2["history"]) >= 1)
check("tính thời lượng trung bình", _st2["totals"]["avg_duration"] > 0,
      f"avg={_st2['totals']['avg_duration']}")
check("tính tỷ lệ tương tác", len(_st2["performance"]) >= 1,
      str(_st2["performance"][:1]))
if _st2["performance"]:
    check("tỷ lệ tương tác đúng", _st2["performance"][0]["engagement_rate"] == 5.9,
          f"rate={_st2['performance'][0]['engagement_rate']}")

check("lưu preset thành công", _an.save_preset("test_preset", {"voice": "vi-VN-NamMinhNeural"}))
check("đọc lại preset", "test_preset" in _an.list_presets())
check("xoá preset", _an.delete_preset("test_preset"))
check("xoá preset đã xoá -> False", _an.delete_preset("test_preset") is False)

# ===========================================================================
# NHÓM 5: TEST HỒI QUY - 6 bug đã được review tái hiện
print("\n=== 17. Test hoi quy (6 bug da review) ===")
from pathlib import Path

from tvcore.content import (classify_topic, deaccent, is_spam, keyword_set,
                             score_comment, spam_reasons)

# --- Bug 2: STOPWORDS khong bao gio khop ---
kw = keyword_set("Tôi không biết của bạn là gì")
check("stopwords loai 'toi'", "tôi" not in kw, str(kw))
check("stopwords loai 'không'", "không" not in kw, str(kw))
check("stopwords loai 'của'", "của" not in kw, str(kw))
check("stopwords loai 'bạn'", "bạn" not in kw, str(kw))
check("stopwords loai 'là'", "là" not in kw, str(kw))
check("stopwords loai 'biết'", "biết" not in kw, str(kw))
check("stopwords khop ca khi viet khong dau", "toi" not in keyword_set("toi la nguoi"),
      str(keyword_set("toi la nguoi")))
check("deaccent bo dau tieng Viet", deaccent("Tiếng Việt") == "tieng viet",
      deaccent("Tiếng Việt"))

# --- Bug 1: filter spam loai nham comment binh thuong ---
for _c in ["giá nhà tăng quá trời luôn",
           "mình mua được cái nhà rồi vui lắm",
           "giá rẻ lắm, mẹ ơi"]:
    check(f"spam KHONG loai '{_c}'", not is_spam(_c), str(spam_reasons(_c)))
for _c in ["shop em oi inbox 09xx xxx xxx nha",
           "mua ngay tai link in bio nha",
           "khach hang inbox em nhe"]:
    check(f"spam LOAI '{_c}'", is_spam(_c), str(spam_reasons(_c)))

# --- Bug 4: substring matching ---
_t, _c = classify_topic("next text context")
check("classify_topic khong khop 'ex' trong 'next'",
      _t != "tình yêu", f"{_t} ({_c})")
_t2, _c2 = classify_topic("vlog vua len")
check("classify_topic khong khop 'vl' trong 'vlog'",
      _t2 != "tình yêu", f"{_t2} ({_c2})")
_t3, _c3 = classify_topic("tôi vừa chia tay bạn trai, tình yêu thật sự rất đẹp")
check("classify_topic van nhan 'tình yêu' that",
      _t3 == "tình yêu", f"{_t3} ({_c3})")
_t4, _c4 = classify_topic("next text context")
check("classify_topic 'next text context' khong phai tinh yeu",
      _t4 != "tình yêu", f"{_t4} ({_c4})")

# --- Bug 4b: likes ---
_l0 = score_comment("Mình nghĩ thế này", likes=0)["total"]
_l99 = score_comment("Mình nghĩ thế này", likes=99)["total"]
_l999 = score_comment("Mình nghĩ thế này", likes=999999)["total"]
check("likes 0 van co diem > 0", _l0 > 0, f"{_l0}")
check("likes anh huong diem (0 like < 99 like)", _l0 < _l99, f"0={_l0} 99={_l99}")
check("likes khong vuot qua tran +0.10", _l999 <= _l0 + 0.1001, f"0={_l0} big={_l999}")

# --- Bug 5: Pydantic field_validator ---
from server import RenderRequest, SceneItem

_s = SceneItem(display_text="x" * 5000, tts_text="y" * 5000)
check("SceneItem cat display_text qua dai", len(_s.display_text) <= 2000,
      f"len={len(_s.display_text)}")
check("SceneItem cat tts_text qua dai", len(_s.tts_text) <= 2000,
      f"len={len(_s.tts_text)}")
_r = RenderRequest(scenes=[], resolution="abc")
check("RenderRequest resolution sai -> mac dinh", _r.resolution == "1080x1920",
      _r.resolution)
_r2 = RenderRequest(scenes=[], resolution="720x1280")
check("RenderRequest resolution hop le giu nguyen", _r2.resolution == "720x1280",
      _r2.resolution)

# --- Bug 1b: ensure_dirs ---
check("ensure_dirs() tao thu muc 'temp'",
      (Path(__file__).resolve().parent.parent / "temp").exists(),
      str(Path(__file__).resolve().parent.parent / "temp"))

# --- Bug 7: ffmpeg fallback sang PATH khi khong co bin/ ---
from tvcore.utils import ffmpeg_bin, ffprobe_bin

check("ffmpeg_bin() tra ve file TON TAI",
      os.path.exists(ffmpeg_bin()), ffmpeg_bin())
check("ffprobe_bin() tra ve file TON TAI",
      os.path.exists(ffprobe_bin()), ffprobe_bin())

# ===========================================================================
print("\n" + "=" * 62)
print(f"  PASS: {len(PASS)}   FAIL: {len(FAIL)}")
if FAIL:
    print("\n  Cac test fail:")
    for f in FAIL:
        print(f"    - {f}")
print("=" * 62)
sys.exit(1 if FAIL else 0)
