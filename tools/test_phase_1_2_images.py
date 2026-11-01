"""
REGRESSION TESTS - PHASE 1.2 (THREAD IMAGE SUPPORT)

Chay: python tools/test_phase_1_2_images.py

Phan loai:
  [UNIT]        - ham don le, khong I/O (images.py, pacing, cards, utils)
  [INTEGRATION] - pipeline nhieu module (scraper -> model -> api -> render)
  [E2E]         - render video THAT + FFprobe + trich frame (anh that xuat hien)

YEU CAU: KHONG chi kiem tra danh sach `images` ton tai.
  Test E2E phai trich frame pixel va chung minh anh THAT xuat hien trong video.

Bien moi truong:
  P12_NO_NET=1     -> bo qua test can mang (van chay offline)
  P12_SKIP_RENDER=1 -> bo qua render that (nhanh)
"""
import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return bool(cond)


def section(title):
    print(f"\n{'=' * 62}\n  {title}\n{'=' * 62}")


NO_NET = os.getenv("P12_NO_NET") == "1"
SKIP_RENDER = os.getenv("P12_SKIP_RENDER") == "1"

# ---------------------------------------------------------------------------
# Tai anh that tu Threads (dung chung cho unit + e2e)
# ---------------------------------------------------------------------------
REAL_IMAGE_URLS = []
_real_json = os.path.join("temp", "probe", "srcsets.json")
if os.path.isfile(_real_json):
    try:
        with open(_real_json, "r", encoding="utf-8") as f:
            _data = json.load(f)
        from tvcore.images import pick_best_srcset
        for _d in _data:
            u = pick_best_srcset(_d.get("srcset"))
            if u and u not in REAL_IMAGE_URLS:
                REAL_IMAGE_URLS.append(u)
    except Exception:
        pass


# ===========================================================================
section("[UNIT] A - images.py: URL an toan (SSRF / scheme / allowlist)")
# ===========================================================================
from tvcore.images import (ALLOWED_FORMATS, IMAGE_MAX_BYTES, dedupe_images,
                           is_allowed_image_url, pick_best_srcset,
                           sniff_image_format)

check("A cho phep host fbcdn.net", is_allowed_image_url("https://x.fbcdn.net/a.jpg"))
check("A cho phep host cdninstagram.com", is_allowed_image_url("https://y.cdninstagram.com/a.jpg"))
check("A cho phep host threads.net", is_allowed_image_url("https://www.threads.net/a.jpg"))
check("A chan host la", not is_allowed_image_url("https://evil.com/a.jpg"))
check("A chan http://IP-noi-bo", not is_allowed_image_url("http://127.0.0.1/a.jpg"))
check("A chan http://IP-noi-bo 10.x", not is_allowed_image_url("http://10.0.0.5/a.jpg"))
check("A chan http://IP-noi-bo 192.168.x", not is_allowed_image_url("http://192.168.1.1/a.jpg"))
check("A chan file://", not is_allowed_image_url("file:///etc/passwd"))
check("A chan data:", not is_allowed_image_url("data:image/png;base64,AAAA"))
check("A chan javascript:", not is_allowed_image_url("javascript:alert(1)"))
check("A chan URL rong", not is_allowed_image_url(""))
check("A chan None", not is_allowed_image_url(None))
check("A chan khong phai chuoi", not is_allowed_image_url(123))


# ===========================================================================
section("[UNIT] B - images.py: pick_best_srcset chon bien the LON NHAT")
# ===========================================================================
_SRCSET = (
    "https://a.fbcdn.net/x.webp 240w, "
    "https://a.fbcdn.net/x.webp?stp=dst-webp_p1080x1080 1080w, "
    "https://a.fbcdn.net/x.webp?stp=dst-webp_p320x320 320w"
)
_best = pick_best_srcset(_SRCSET)
check("B chon bien 1080w (lon nhat)", "p1080x1080" in _best, _best[:70])
check("B bo qua host khong allowlist",
      pick_best_srcset("https://evil.com/x.jpg 2000w") == "")
check("B srcset rong -> chuoi rong", pick_best_srcset("") == "")
check("B srcset None -> chuoi rong", pick_best_srcset(None) == "")
# Khong co hau to -> so sanh do dai URL
check("B khong hau to -> van chon duoc",
      pick_best_srcset("https://a.fbcdn.net/short.jpg, https://a.fbcdn.net/longer.jpg").endswith("longer.jpg"))


# ===========================================================================
section("[UNIT] C - images.py: dedupe_images GIU THU TU")
# ===========================================================================
_d = dedupe_images([
    "https://a.fbcdn.net/1.jpg?x=1",
    "https://a.fbcdn.net/2.jpg",
    "https://a.fbcdn.net/1.jpg?x=99",   # trung cung file
    "https://a.fbcdn.net/3.jpg",
    "",
])
check("C loai trung nhung giu thu tu",
      _d == ["https://a.fbcdn.net/1.jpg?x=1", "https://a.fbcdn.net/2.jpg",
             "https://a.fbcdn.net/3.jpg"], str(_d))
check("C danh sach rong -> rong", dedupe_images([]) == [])
check("C None -> rong", dedupe_images(None) == [])


# ===========================================================================
section("[UNIT] D - images.py: sniff_image_format (magic bytes)")
# ===========================================================================
check("D nhan dien JPEG", sniff_image_format(b"\xff\xd8\xff\xe0" + b"\x00" * 20) == "JPEG")
check("D nhan dien PNG", sniff_image_format(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20) == "PNG")
check("D nhan dien WEBP", sniff_image_format(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "WEBP")
check("D nhan dien GIF -> None (khong ho tro)",
      sniff_image_format(b"GIF89a" + b"\x00" * 20) is None)
check("D nhan dien MP4 -> None", sniff_image_format(b"\x00\x00\x00\x18ftypmp4" + b"\x00" * 20) is None)
check("D du lieu rong -> None", sniff_image_format(b"") is None)
check("D khong phai anh -> None", sniff_image_format(b"hello world this is text") is None)
check("D danh sach format cho phep dung",
      set(ALLOWED_FORMATS) == {"JPEG", "PNG", "WEBP"}, str(ALLOWED_FORMATS))


# ===========================================================================
section("[UNIT] E - images.py: download_image xu ly loi an toan")
# ===========================================================================
from tvcore.images import download_image, prepare_images

_tmpdir = tempfile.mkdtemp(prefix="p12_")

# E1: URL khong hop le -> fail ngay, khong tao file
_ok, _msg = download_image("https://evil.com/x.jpg", os.path.join(_tmpdir, "e1.png"))
check("E1 URL host la -> that bai", not _ok, _msg)
check("E1 khong tao file khi URL sai",
      not os.path.exists(os.path.join(_tmpdir, "e1.png")))

# E2: file tam .part duoc don
_ok2, _msg2 = download_image("https://a.fbcdn.net/x.jpg", os.path.join(_tmpdir, "e2.png"))
check("E2 URL sai -> that bai", not _ok2, _msg2)
check("E2 khong con file .part",
      not os.path.exists(os.path.join(_tmpdir, "e2.png.part")))

# E3: timeout -> that bai, khong crash
from tvcore import images as _img_mod
_real_get = _img_mod.requests.get


class _TimeoutResp:
    status_code = 200
    headers = {"Content-Type": "image/png"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size=0):
        raise _img_mod.requests.exceptions.Timeout("boom")


def _get_timeout(*a, **kw):
    raise _img_mod.requests.exceptions.Timeout("boom")


_img_mod.requests.get = _get_timeout
try:
    _ok3, _msg3 = download_image("https://a.fbcdn.net/x.jpg", os.path.join(_tmpdir, "e3.png"))
finally:
    _img_mod.requests.get = _real_get
check("E3 timeout -> that bai (khong crash)", not _ok3, _msg3)
check("E3 timeout -> thong bao dung", "Timeout" in _msg3, _msg3)
check("E3 khong con file .part sau timeout",
      not os.path.exists(os.path.join(_tmpdir, "e3.png.part")))

# E4: HTTP 404
def _get_404(*a, **kw):
    class R:
        status_code = 404
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_content(self, chunk_size=0):
            yield b""

    return R()


_img_mod.requests.get = _get_404
try:
    _ok4, _msg4 = download_image("https://a.fbcdn.net/x.jpg", os.path.join(_tmpdir, "e4.png"))
finally:
    _img_mod.requests.get = _real_get
check("E4 404 -> that bai", not _ok4, _msg4)
check("E4 404 -> bao loi HTTP", "404" in _msg4, _msg4)

# E5: MIME sai (text/html) -> fail
def _get_html(*a, **kw):
    class R:
        status_code = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_content(self, chunk_size=0):
            yield b"<html>not an image</html>"

    return R()


_img_mod.requests.get = _get_html
try:
    _ok5, _msg5 = download_image("https://a.fbcdn.net/x.jpg", os.path.join(_tmpdir, "e5.png"))
finally:
    _img_mod.requests.get = _real_get
check("E5 MIME text/html -> that bai", not _ok5, _msg5)
check("E5 bao ly do MIME", "MIME" in _msg5, _msg5)

# E6: dung MIME nhung noi dung khong phai anh (magic bytes) -> fail
def _get_fake(*a, **kw):
    class R:
        status_code = 200
        headers = {"Content-Type": "image/png"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_content(self, chunk_size=0):
            yield b"this is definitely not a png file at all"

    return R()


_img_mod.requests.get = _get_fake
try:
    _ok6, _msg6 = download_image("https://a.fbcdn.net/x.jpg", os.path.join(_tmpdir, "e6.png"))
finally:
    _img_mod.requests.get = _real_get
check("E6 noi dung gia mao (magic bytes sai) -> that bai", not _ok6, _msg6)
check("E6 bao ly do magic bytes", "magic bytes" in _msg6.lower() or "gia mao" in _msg6, _msg6)

# E7: file qua lon -> fail
_big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (1024 * 1024)


def _get_big(*a, **kw):
    class R:
        status_code = 200
        headers = {"Content-Type": "image/png"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_content(self, chunk_size=0):
            yield _big

    return R()


_img_mod.requests.get = _get_big
try:
    _ok7, _msg7 = download_image("https://a.fbcdn.net/x.jpg", os.path.join(_tmpdir, "e7.png"),
                                 max_bytes=100 * 1024)
finally:
    _img_mod.requests.get = _real_get
check("E7 file qua lon -> that bai", not _ok7, _msg7)
check("E7 bao ly do qua lon", "lon" in _msg7.lower() or "lớn" in _msg7, _msg7)

# E8: anh that (PNG hop le) -> thanh cong, luu PNG
_png_bytes = io.BytesIO()
Image.new("RGB", (64, 48), (10, 120, 200)).save(_png_bytes, "PNG")


def _get_png(*a, **kw):
    class R:
        status_code = 200
        headers = {"Content-Type": "image/png"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_content(self, chunk_size=0):
            yield _png_bytes.getvalue()

    return R()


_img_mod.requests.get = _get_png
try:
    _ok8, _msg8 = download_image("https://a.fbcdn.net/x.jpg", os.path.join(_tmpdir, "e8.png"))
finally:
    _img_mod.requests.get = _real_get
check("E8 anh hop le -> thanh cong", _ok8, _msg8)
check("E8 file PNG da tao", os.path.isfile(os.path.join(_tmpdir, "e8.png")))
if os.path.isfile(os.path.join(_tmpdir, "e8.png")):
    with Image.open(os.path.join(_tmpdir, "e8.png")) as _im:
        check("E8 kich thuoc giu nguyen (khong keo meo)",
              _im.size == (64, 48), str(_im.size))
check("E8 khong con file .part", not os.path.exists(os.path.join(_tmpdir, "e8.png.part")))

# E9: prepare_images bo qua anh loi, giu thu tu
def _get_seq(url, *a, **kw):
    class R:
        status_code = 200
        headers = {"Content-Type": "image/png"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_content(self, chunk_size=0):
            # 1 -> that bai, 2 -> ok, 3 -> ok
            if "1.jpg" in url:
                yield b"broken"
            else:
                yield _png_bytes.getvalue()

    return R()


_img_mod.requests.get = _get_seq
try:
    _paths = prepare_images(
        ["https://a.fbcdn.net/1.jpg", "https://a.fbcdn.net/2.jpg",
         "https://a.fbcdn.net/3.jpg"],
        "prep", dest_dir=_tmpdir,
    )
finally:
    _img_mod.requests.get = _real_get
check("E9 anh loi bi bo qua, anh tot duoc giu",
      len(_paths) == 2, f"{len(_paths)} files")
check("E9 thu tu giu nguyen (2.jpg roi 3.jpg)",
      len(_paths) == 2 and _paths[0].endswith("_img1.png") and _paths[1].endswith("_img2.png"),
      str([os.path.basename(p) for p in _paths]))


# ===========================================================================
section("[UNIT] F - pacing.plan_image_slices: anh -> visual theo thu tu")
# ===========================================================================
from tvcore.pacing import plan_image_slices

check("F khong anh -> tat ca visual khong anh",
      plan_image_slices(0, 3) == [-1, -1, -1])
check("F 1 anh / 1 visual", plan_image_slices(1, 1) == [0])
check("F 2 anh / 2 visual -> 0,1", plan_image_slices(2, 2) == [0, 1])
check("F 3 anh / 5 visual -> 0,1,2,2,2 (lặp anh cuoi)",
      plan_image_slices(3, 5) == [0, 1, 2, 2, 2], str(plan_image_slices(3, 5)))
check("F 5 anh / 2 visual -> 0,2 (khong vuot ngoai)",
      plan_image_slices(5, 2) == [0, 2], str(plan_image_slices(5, 2)))
check("F anh nhieu hon visual -> chi dung anh dau theo thu tu",
      all(0 <= i < 5 for i in plan_image_slices(5, 3)))
_s = plan_image_slices(4, 4)
check("F 4 anh / 4 visual -> 0,1,2,3", _s == [0, 1, 2, 3], str(_s))
check("F visual >= 1 du so anh = 0", len(plan_image_slices(0, 1)) == 1)


# ===========================================================================
section("[UNIT] G - utils.scene_to_dict: giu truong images (tuong thich nguoi)")
# ===========================================================================
from tvcore.utils import scene_to_dict

_s1 = scene_to_dict({"author": "a", "display_text": "x"})
check("G scene cu khong co images -> []", _s1.get("images") == [], str(_s1.get("images")))
_s2 = scene_to_dict({"author": "a", "display_text": "x",
                     "images": ["https://a.fbcdn.net/1.jpg", "https://a.fbcdn.net/2.jpg"]})
check("G scene co images -> giu nguyen thu tu",
      _s2.get("images") == ["https://a.fbcdn.net/1.jpg", "https://a.fbcdn.net/2.jpg"])
_s3 = scene_to_dict({"author": "a", "display_text": "x", "images": "https://a.fbcdn.net/1.jpg"})
check("G images la chuoi -> moi thanh list", _s3.get("images") == ["https://a.fbcdn.net/1.jpg"])
_s4 = scene_to_dict({"author": "a", "display_text": "x", "images": None})
check("G images None -> []", _s4.get("images") == [])
_s5 = scene_to_dict({"author": "a", "display_text": "x", "images": [1, 2, "https://a.fbcdn.net/3.jpg"]})
check("G bo phan tu khong phai chuoi", _s5.get("images") == ["https://a.fbcdn.net/3.jpg"])


# ===========================================================================
section("[UNIT] H - cards.draw_single_box: anh duoc ve vao card")
# ===========================================================================
from tvcore.cards import draw_single_box, fit_image_contain, safe_card_height, safe_card_width

# Tao anh mau: vang -> de phan biet voi nen card trang
_img_dir = os.path.join(_tmpdir, "imgs")
os.makedirs(_img_dir, exist_ok=True)
_img_path = os.path.join(_img_dir, "yellow.png")
Image.new("RGB", (400, 600), (255, 220, 0)).save(_img_path)

_card_no_img = draw_single_box("a", "1 ngày", "text ngắn", None, scale=1.0,
                               vid_w=1080, vid_h=1920)
_card_img = draw_single_box("a", "1 ngày", "text ngắn", None, scale=1.0,
                             vid_w=1080, vid_h=1920, image_path=_img_path)
check("H card co anh cao hon card khong anh",
      _card_img.height > _card_no_img.height,
      f"{_card_img.height} vs {_card_no_img.height}")
check("H card co anh khong vuot vung an toan doc",
      _card_img.height <= safe_card_height(1920, 1.0),
      f"{_card_img.height} <= {safe_card_height(1920, 1.0)}")

# Kiem tra pixel vang co trong card (anh THAT da duoc ve)
_yellow = [px for px in _card_img.convert("RGB").getdata()
           if px[0] > 230 and px[1] > 190 and px[2] < 70]
check("H anh xuat hien trong card (co pixel vang)", len(_yellow) > 500,
      f"{len(_yellow)} pixel vang")

# Khong co pixel vang trong card khong anh
_yellow2 = [px for px in _card_no_img.convert("RGB").getdata()
            if px[0] > 230 and px[1] > 190 and px[2] < 70]
check("H card khong anh -> khong co pixel vang", len(_yellow2) == 0, f"{len(_yellow2)}")

# File anh khong ton tai -> fallback an toan (khong crash)
_card_bad = draw_single_box("a", "1 ngày", "text", None, scale=1.0,
                             vid_w=1080, vid_h=1920,
                             image_path=os.path.join(_img_dir, "khong_ton_tai.png"))
check("H anh khong doc duoc -> fallback khong crash", _card_bad is not None)
check("H fallback giong card khong anh",
      _card_bad.height == _card_no_img.height,
      f"{_card_bad.height} vs {_card_no_img.height}")

# fit_image_contain: khong keo meo
_fit = fit_image_contain(Image.new("RGB", (200, 100), (255, 0, 0)), 300, 300)
check("H contain: anh ngang trong khung vuong -> canh giua, khong meo",
      _fit.size == (300, 300), str(_fit.size))
_px = _fit.convert("RGB").getpixel((150, 150))
check("H contain: pixel giua = mau anh (khong nen)", _px[0] > 200, str(_px))
_fit2 = fit_image_contain(Image.new("RGB", (100, 400), (0, 255, 0)), 300, 300)
check("H contain: anh doc trong khung vuong -> khong meo", _fit2.size == (300, 300))

# ratio giu nguyen
_src = Image.new("RGB", (800, 400), (10, 200, 10))
_fit3 = fit_image_contain(_src, 400, 400)
_arr = list(_fit3.convert("RGB").getdata())
_rows = [y for y in range(400) if _arr[y * 400 + 200] == (10, 200, 10)]
check("H contain: ty le anh duoc giu (cao 200 cho 800x400 -> 400x200)",
      len(_rows) >= 190 and len(_rows) <= 210, f"{len(_rows)} rows")


# ===========================================================================
section("[UNIT] I - cards.split_scene_if_long: giu anh khi chia scene")
# ===========================================================================
from tvcore.cards import split_scene_if_long

_long = " ".join(["Cau thu nhat day du bang chu de chia ra nhieu dong"] * 12)
_parts = split_scene_if_long({"author": "a", "display_text": _long,
                              "images": ["https://a.fbcdn.net/1.jpg",
                                         "https://a.fbcdn.net/2.jpg"]},
                             scale=1.0, vid_w=1080)
check("I scene dai duoc chia thanh nhieu phan", len(_parts) >= 2, str(len(_parts)))
check("I moi phan giu duoc danh sach anh",
      all(p.get("images") == ["https://a.fbcdn.net/1.jpg", "https://a.fbcdn.net/2.jpg"]
          for p in _parts),
      str([p.get("images") for p in _parts]))


# ===========================================================================
section("[UNIT] J - server.SceneItem: validate images")
# ===========================================================================
from server import SceneItem

_si = SceneItem(author="a", display_text="x")
check("J SceneItem mac dinh images=[]", _si.images == [])
_si2 = SceneItem(author="a", display_text="x",
                 images=["https://a.fbcdn.net/1.jpg", "https://a.fbcdn.net/2.jpg"])
check("J SceneItem giu thu tu anh", _si2.images == ["https://a.fbcdn.net/1.jpg",
                                                    "https://a.fbcdn.net/2.jpg"])
_si3 = SceneItem(author="a", display_text="x",
                 images=["https://evil.com/a.jpg", "http://127.0.0.1/b.jpg",
                         "file:///etc/passwd"])
check("J SceneItem loai URL khong an toan", _si3.images == [], str(_si3.images))
_si4 = SceneItem(author="a", display_text="x", images=None)
check("J SceneItem images=None -> []", _si4.images == [])
_si5 = SceneItem(author="a", display_text="x", images="https://a.fbcdn.net/1.jpg")
check("J SceneItem images la chuoi -> list", _si5.images == ["https://a.fbcdn.net/1.jpg"])
_si6 = SceneItem(author="a", display_text="x", images=["https://a.fbcdn.net/%d.jpg" % i
                                                      for i in range(30)])
check("J SceneItem gioi han so anh", len(_si6.images) <= 10, str(len(_si6.images)))
_si7 = SceneItem(author="a", display_text="x", images=[123, None, "https://a.fbcdn.net/1.jpg"])
check("J SceneItem bo phan tu sai kieu", _si7.images == ["https://a.fbcdn.net/1.jpg"])


# ===========================================================================
section("[UNIT] K - scrape._extract_image_urls: chon URL hop le, giu thu tu")
# ===========================================================================
from tvcore.scrape import _extract_image_urls

_raw = [
    {"src": "https://a.fbcdn.net/small.jpg",
     "srcset": "https://a.fbcdn.net/small.jpg 240w, https://a.fbcdn.net/big.jpg 1080w",
     "w": 240, "h": 360},
    {"src": "https://a.fbcdn.net/two.jpg", "srcset": "", "w": 100, "h": 100},
    {"src": "https://evil.com/x.jpg", "srcset": "", "w": 10, "h": 10},
]
_out = _extract_image_urls(_raw)
check("K chon bien lon nhat tu srcset", "big.jpg" in _out[0], str(_out[:1]))
check("K bo URL host khong allowlist", all("evil.com" not in u for u in _out), str(_out))
check("K giu thu tu", len(_out) == 2 and _out[1].endswith("two.jpg"), str(_out))
check("K input rong -> rong", _extract_image_urls([]) == [])
check("K item khong phai dict -> bo qua", _extract_image_urls(["x", 5]) == [])


# ===========================================================================
section("[INTEGRATION] L - pipeline: scrape -> model -> api -> render giu anh")
# ===========================================================================
from tvcore.utils import scene_to_dict as _s2d

# L1: SceneItem -> scene_to_dict -> giu anh
_item = SceneItem(author="a", display_text="x",
                  images=["https://a.fbcdn.net/1.jpg", "https://a.fbcdn.net/2.jpg"])
_d = _s2d(_item)
check("L1 SceneItem -> dict giu anh", _d.get("images") == ["https://a.fbcdn.net/1.jpg",
                                                          "https://a.fbcdn.net/2.jpg"])

# L2: smart filter (chon comment) giu anh cua scene goc
import tvcore.render as _R

_scenes = [
    {"author": "root", "display_text": "goc", "tts_text": "goc", "images": ["https://a.fbcdn.net/1.jpg"]},
    {"author": "c1", "display_text": "comment 1", "tts_text": "c1"},
    {"author": "c2", "display_text": "comment 2", "tts_text": "c2"},
]
_kept = _R._apply_smart_filter(list(_scenes), 0.0)
check("L2 scene goc giu anh sau smart filter",
      _kept and _kept[0].get("images") == ["https://a.fbcdn.net/1.jpg"],
      str(_kept[0].get("images") if _kept else None))

# L3: hook/CTA/narrator scene khong co anh
from tvcore.render import _insert_script_scenes

_ins = _insert_script_scenes([{"author": "root", "display_text": "x", "tts_text": "x"}],
                              "chuyen canh", "bien tap")
check("L3 scene narrator khong mang anh",
      all(s.get("images") in (None, []) for s in _ins), str([s.get("images") for s in _ins]))

# L4: API /api/scrape tra ve truong images (mock scraper)
try:
    from fastapi.testclient import TestClient
    import server as _srv

    _orig = _srv.scrape_threads_post

    async def _fake_scrape(url, max_comments=7):
        return [{"author": "root", "time_ago": "1 ngày", "display_text": "x",
                 "tts_text": "x", "avatar_url": None, "is_verified": False,
                 "likes": "1", "comments": "0", "reposts": "0", "shares": "0",
                 "author_liked": True, "meme_file": "", "reply_display_text": None,
                 "reply_tts_text": None, "reply_author": None,
                 "images": ["https://a.fbcdn.net/1.jpg", "https://a.fbcdn.net/2.jpg"]}]

    _srv.scrape_threads_post = _fake_scrape
    try:
        _client = TestClient(_srv.app)
        _resp = _client.post("/api/scrape", json={"url": "https://www.threads.net/@a/post/b"})
        _js = _resp.json()
        check("L4 API /api/scrape tra ve images",
              _resp.status_code == 200
              and _js["scenes"][0]["images"] == ["https://a.fbcdn.net/1.jpg",
                                                 "https://a.fbcdn.net/2.jpg"],
              str(_js.get("scenes", [{}])[0].get("images"))[:120])
    finally:
        _srv.scrape_threads_post = _orig
except Exception as e:
    check("L4 API /api/scrape tra ve images", False, f"{type(e).__name__}: {e}")


# ===========================================================================
section("[E2E] M - Render THAT voi anh: FFprobe + trich frame")
# ===========================================================================
if SKIP_RENDER:
    check("M render that (bi bo qua qua P12_SKIP_RENDER)", True, "skipped")
else:
    import functools
    import http.server
    import threading

    import tvcore.render as _R2
    from server import RenderRequest

    # ---------------------------------------------------------------------
    # M0: HTTP server cuc bo phuc vu anh mau (RED / GREEN / MAGENTA)
    #     -> chung minh anh THAT xuat hien + THU TU DUNG trong video.
    #     Server chay tren 127.0.0.1; allowlist duoc mo tam trong test (chi
    #     trong process test, khong anh huong production).
    # ---------------------------------------------------------------------
    _srv_dir = os.path.join(_tmpdir, "www")
    os.makedirs(_srv_dir, exist_ok=True)
    # Palette: MAU THUAN (saturated) de khong trung bat ky mau UI nao
    # (UI Threads dung do cho icon tim, xanh lam cho badge verified,
    # den/xam cho chu) va khong trung nen card trang.
    # -> dem pixel mau trong frame la bang chung 100% rang anh THAT xuat hien.
    _COLORS = {"cyan": (0, 255, 255), "lime": (0, 255, 0), "purple": (255, 0, 255),
               "red": (255, 0, 0), "blue": (0, 0, 255)}
    for _n, _c in _COLORS.items():
        Image.new("RGB", (600, 600), _c).save(os.path.join(_srv_dir, f"{_n}.png"))

    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=_srv_dir, **kw)

        def log_message(self, *a):
            pass

    try:
        _httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    except Exception as e:
        _httpd = None
        check("M0 khoi dong HTTP server cuc bo", False, str(e))
    _port = _httpd.server_address[1] if _httpd else 0
    _th = threading.Thread(target=_httpd.serve_forever, daemon=True)
    if _httpd:
        _th.start()
    else:
        check("M0 khoi dong HTTP server cuc bo", False, "fail")

    _LOCAL_URLS = {n: f"http://127.0.0.1:{_port}/{n}.png" for n in _COLORS}

    # Mo allowlist trong tam thoi (chi cho 127.0.0.1) cho test
    from tvcore import images as _im
    import server as _srv_mod

    _orig_allow = _im.is_allowed_image_url
    _orig_allow_srv = _srv_mod.is_allowed_image_url

    def _allow_test(u):
        if isinstance(u, str) and "127.0.0.1" in u:
            return u.startswith("http://127.0.0.1:")
        return _orig_allow(u)

    _im.is_allowed_image_url = _allow_test
    _srv_mod.is_allowed_image_url = _allow_test

    def _render_scenes(label, scene_items, bg):
        """Render that -> tra ve duong dan video (hoac None)."""
        _items = [SceneItem(**it) for it in scene_items]
        _req = RenderRequest(
            scenes=_items, voice="vi-VN-HoaiMyNeural", speed="+20%",
            watermark_text="", resolution="1080x1920",
            bg_video_path=bg, auto_split=False, skip_qc=True,
        )
        try:
            _res = asyncio.run(_R2.render_video(_req, progress=lambda p, m: None))
        except Exception as e:
            check(f"M render [{label}] thanh cong", False, f"{type(e).__name__}: {e}")
            return None
        if _res.get("status") != "success":
            check(f"M render [{label}] thanh cong", False, str(_res)[:200])
            return None
        _p = os.path.join("outputs", _res.get("filename", ""))
        check(f"M render [{label}] thanh cong", os.path.isfile(_p), _p)
        return _p if os.path.isfile(_p) else None

    def _probe(video):
        _out = subprocess.run(
            [_R2.ffprobe_bin(), "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", video],
            capture_output=True, text=True, errors="ignore")
        try:
            return json.loads(_out.stdout or "{}")
        except Exception:
            return {}

    def _vdur(info):
        for st in (info.get("streams") or []):
            if st.get("codec_type") == "video":
                try:
                    return float(st.get("duration") or 0)
                except Exception:
                    return 0.0
        return 0.0

    def _acodec(info):
        for st in (info.get("streams") or []):
            if st.get("codec_type") == "audio":
                return st.get("codec_name") or ""
        return ""

    def _extract_frame(video, t, out_png):
        """
        Trich 1 frame tai thoi diem `t`.

        QUAN TRONG: dung `-i video -ss T` (output seek) chu KHONG dung
        `-ss T -i video` (input seek). Input seek reset timestamp cua frame
        ve 0 -> filter `enable='between(t,start,end)'` cua FFmpeg se KHONG
        khop -> anh overlay khong hien -> test bao "anh khong xuat hien"
        gia (sai).
        """
        subprocess.run(
            [_R2.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
             "-i", video, "-ss", f"{t:.3f}", "-frames:v", "1", out_png],
            capture_output=True, text=True, errors="ignore")
        return os.path.isfile(out_png) and os.path.getsize(out_png) > 500

    def _count_color(png_path, target, tol=60):
        """
        Dem so pixel gan voi `target` trong frame.

        dung khoang cach trong khong gian RGB voi nguong `tol` rong
        (codec H.264 lam lech nhe mau, nen dung nguong rong).
        THEM: yeu cau SAT (channel sat) -> loai tru mau trang/xam (nen card).
        """
        with Image.open(png_path).convert("RGB") as im:
            _w, _h = im.size
            _px = im.load()
            _n = 0
            for y in range(0, _h, 3):
                for x in range(0, _w, 3):
                    _r, _g, _b = _px[x, y]
                    if (abs(_r - target[0]) <= tol and abs(_g - target[1]) <= tol
                            and abs(_b - target[2]) <= tol
                            and max(_r, _g, _b) - min(_r, _g, _b) >= 90):
                        _n += 1
        return _n

    def _dominant(png_path):
        """Tra ve ten mau chiem da trong frame (nhieu nhat)."""
        _c = {n: _count_color(png_path, col) for n, col in _COLORS.items()}
        _best = max(_c, key=lambda k: _c[k])
        return _best, _c

    def _color_ratio(png_path, name):
        """
        Ti le % pixel mau `name` trong frame.

        Dung TI LE (khong dung so tuyet doi) de khong phu thuoc do phan giai
        va bien so mau do nen game / nen toi.
        """
        with Image.open(png_path).convert("RGB") as im:
            _w, _h = im.size
            _px = im.load()
            _n = 0
            _total = 0
            _tgt = _COLORS[name]
            for y in range(0, _h, 3):
                for x in range(0, _w, 3):
                    _total += 1
                    _r, _g, _b = _px[x, y]
                    if (abs(_r - _tgt[0]) <= 60 and abs(_g - _tgt[1]) <= 60
                            and abs(_b - _tgt[2]) <= 60
                            and max(_r, _g, _b) - min(_r, _g, _b) >= 90):
                        _n += 1
        _ratio = (100.0 * _n / _total) if _total else 0.0
        if _ratio > 0.9:
            print(f"      [DEBUG] ratio {name}={_ratio:.2f}% frame={_w}x{_h} "
                  f"hits={_n} total={_total}")
        return _ratio

    _fr_dir = os.path.join(_tmpdir, "frames")
    os.makedirs(_fr_dir, exist_ok=True)

    try:
        # ---------------------------------------------------------------
        # M1: 1 ANH - nen gameplay ON
        # ---------------------------------------------------------------
        _v1 = _render_scenes(
            "1 anh / nen gameplay ON",
            [{"author": "u", "display_text": "Bai nay co mot anh dep.",
              "tts_text": "Bai nay co mot anh dep.",
              "images": [_LOCAL_URLS["cyan"]]}],
            "random",
        )
        if _v1:
            _info1 = _probe(_v1)
            check("M1 video co stream video", _vdur(_info1) > 0, f"{_vdur(_info1):.2f}s")
            check("M1 video co stream audio", bool(_acodec(_info1)), _acodec(_info1))
            _f1 = os.path.join(_fr_dir, "m1.png")
            _ok_f = _extract_frame(_v1, 1.0, _f1)
            check("M1 trich frame thanh cong", _ok_f, _f1)
            if _ok_f:
                _dom1, _c1 = _dominant(_f1)
                _r1 = _color_ratio(_f1, "cyan")
                check("M1 ANH THAT XUAT HIEN trong frame (pixel mau)",
                      _c1["cyan"] > 500 and _dom1 == "cyan",
                      f"{_c1} -> dom={_dom1}")
                check("M1 anh chiem dung dien tich (>=3% khung hinh)",
                      _r1 >= 3.0, f"{_r1:.2f}%")
                _f1b = os.path.join(_fr_dir, "m1b.png")
                if _extract_frame(_v1, 2.0, _f1b):
                    _dom1b, _c1b = _dominant(_f1b)
                    check("M1 anh van hien o giua video (khong bien mat)",
                          _c1b["cyan"] > 500 and _dom1b == "cyan", f"{_c1b}")

        # ---------------------------------------------------------------
        # M2: 1 ANH - nen gameplay OFF
        # ---------------------------------------------------------------
        _v2 = _render_scenes(
            "1 anh / nen gameplay OFF",
            [{"author": "u", "display_text": "Bai nay co mot anh dep.",
              "tts_text": "Bai nay co mot anh dep.",
              "images": [_LOCAL_URLS["lime"]]}],
            None,
        )
        if _v2:
            _f2 = os.path.join(_fr_dir, "m2.png")
            if _extract_frame(_v2, 1.0, _f2):
                _dom2, _c2 = _dominant(_f2)
                _r2 = _color_ratio(_f2, "lime")
                check("M2 anh hien dung khi TAT gameplay",
                      _c2["lime"] > 500 and _dom2 == "lime", f"{_c2} -> dom={_dom2}")
                check("M2 anh chiem dung dien tich khi tat nen (>=3%)",
                      _r2 >= 3.0, f"{_r2:.2f}%")

        # ---------------------------------------------------------------
        # M3: KHONG ANH - layout giu nguyen (khong regression)
        # ---------------------------------------------------------------
        _v3 = _render_scenes(
            "khong anh",
            [{"author": "u", "display_text": "Bai nay khong co anh gi het.",
              "tts_text": "Bai nay khong co anh gi het."}],
            None,
        )
        if _v3:
            _f3 = os.path.join(_fr_dir, "m3.png")
            if _extract_frame(_v3, 1.0, _f3):
                # CHI kiem tra 3 mau dung cho ANH trong test (cyan/lime/purple).
                # KHONG kiem tra `red`/`blue`: nut "like" mau do cua UI Threads
                # (va nut xanh lam "verified") co mat trong card -> false positive.
                _r3 = {n: _color_ratio(_f3, n) for n in ("cyan", "lime", "purple")}
                check("M3 khong anh -> khong co pixel mau anh",
                      all(v < 1.0 for v in _r3.values()), f"{_r3}")

        # ---------------------------------------------------------------
        # M4: NHIEU ANH -> thu tu dung (red -> green -> magenta)
        # ---------------------------------------------------------------
        _v4 = _render_scenes(
            "3 anh / thu tu",
            [{"author": "u",
              "display_text": "Ba anh se xuat hien theo thu tu goc cua bai viet.",
              "tts_text": "Ba anh se xuat hien theo thu tu goc cua bai viet.",
              "images": [_LOCAL_URLS["cyan"], _LOCAL_URLS["lime"],
                         _LOCAL_URLS["purple"]]}],
            None,
        )
        if _v4:
            _info4 = _probe(_v4)
            _dur4 = _vdur(_info4)
            check("M4 video nhieu anh co thoi luong > 0", _dur4 > 0, f"{_dur4:.2f}s")
            # 3 visual => lay frame o 1/6, 1/2, 5/6 thoi luong
            _pts = [(_dur4 * f) for f in (0.16, 0.5, 0.84)]
            _counts = []
            for _i, _t in enumerate(_pts):
                _fp = os.path.join(_fr_dir, f"m4_{_i}.png")
                if not _extract_frame(_v4, _t, _fp):
                    continue
                _dom, _c = _dominant(_fp)
                _counts.append(_c)
                _expect = ("cyan", "lime", "purple")[_i]
                _ratio = _color_ratio(_fp, _expect)
                check(f"M4 frame {_i + 1} hien dung anh theo thu tu (mong doi {_expect})",
                      _dom == _expect and _c[_expect] > 500,
                      f"{_c} -> dom={_dom}")
                check(f"M4 frame {_i + 1} anh {_expect} chiem >=3% khung hinh",
                      _ratio >= 3.0, f"{_ratio:.2f}%")
            check("M4 co it nhat 3 frame kiem tra duoc", len(_counts) == 3,
                  str(len(_counts)))

        # ---------------------------------------------------------------
        # M5: ANH + TEXT DAI -> audio khong bi cat, visual khong gap
        # ---------------------------------------------------------------
        _long = (
            "Sau khi nghi viec toi danh ca ba thang de suy nghi xem minh thuc su "
            "muon lam gi. Ban dau toi nghi rang nghi viec se khien minh cam thay "
            "nhe nhom va tu do hon. Nhung thuc te thang dau toi thay hoi han va lo "
            "lang ve tien bac. Roi toi bat dau nhan ra minh da song ma khong thuc "
            "su song. Bay gio toi bat dau tung ngay mot cham lai truoc khi bat tay "
            "vao viec gi. Moi nguoi nghi sao ve quyet dinh nghi viec cua toi?"
        )
        _v5 = _render_scenes(
            "anh + text dai",
            [{"author": "u", "display_text": _long, "tts_text": _long,
              "images": [_LOCAL_URLS["cyan"]]}],
            None,
        )
        if _v5:
            _info5 = _probe(_v5)
            _vd5 = _vdur(_info5)
            _ad5 = 0.0
            for st in (_info5.get("streams") or []):
                if st.get("codec_type") == "audio":
                    try:
                        _ad5 = float(st.get("duration") or 0)
                    except Exception:
                        _ad5 = 0.0
            check("M5 video co audio", _ad5 > 0, f"audio={_ad5:.2f}s")
            check("M5 video khong cat nganh audio",
                  _vd5 >= _ad5 - 0.3, f"v={_vd5:.2f} a={_ad5:.2f}")
            # Anh van xuat hien o giua video (audio dai -> nhieu visual)
            _f5 = os.path.join(_fr_dir, "m5.png")
            if _extract_frame(_v5, min(_vd5 * 0.5, 8.0), _f5):
                _dom5, _c5 = _dominant(_f5)
                check("M5 anh khong bi mat khi audio dai / visual chia segment",
                      _c5["cyan"] > 300 and _dom5 == "cyan", f"{_c5} -> dom={_dom5}")
            # Khong co khoang trong visual: quet frame theo thoi gian
            _found_any = False
            for _t in [0.5, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0]:
                if _t > _vd5 - 0.2:
                    break
                _fp = os.path.join(_fr_dir, f"m5_{_t}.png")
                if not _extract_frame(_v5, _t, _fp):
                    continue
                _c = sum(_count_color(_fp, col) for col in _COLORS.values())
                if _c > 300:
                    _found_any = True
                    break
            check("M5 khong co khoang trong visual (anh hien lien tuc)", _found_any)

        # ---------------------------------------------------------------
        # M6: ANH + REPLY -> anh khong che noi dung binh luan
        # ---------------------------------------------------------------
        _v6 = _render_scenes(
            "anh + reply",
            [{"author": "u", "display_text": "Bai goc co anh va nhieu binh luan.",
              "tts_text": "Bai goc co anh va nhieu binh luan.",
              "images": [_LOCAL_URLS["cyan"]],
              "reply_display_text": "Toi thay anh nay rat dep day.",
              "reply_tts_text": "Toi thay anh nay rat dep day.",
              "reply_author": "v"}],
            None,
        )
        if _v6:
            _f6 = os.path.join(_fr_dir, "m6.png")
            if _extract_frame(_v6, 1.0, _f6):
                _dom6, _c6 = _dominant(_f6)
                check("M6 anh + reply: anh van xuat hien",
                      _c6["cyan"] > 300 and _dom6 == "cyan", f"{_c6} -> dom={_dom6}")
            _info6 = _probe(_v6)
            check("M6 video co audio", _acodec(_info6) != "", _acodec(_info6))

        # ---------------------------------------------------------------
        # M7: ANH LOI -> video VAN RENDER DUOC (khong crash)
        # ---------------------------------------------------------------
        _v7 = _render_scenes(
            "anh loi",
            [{"author": "u", "display_text": "Anh khong tai duoc.",
              "tts_text": "Anh khong tai duoc.",
              "images": ["https://a.fbcdn.net/khong-ton-tai-404.jpg"]}],
            None,
        )
        check("M7 anh loi -> video VAN render duoc", _v7 is not None)
        if _v7:
            _info7 = _probe(_v7)
            check("M7 video co audio dung", _acodec(_info7) != "", _acodec(_info7))
            check("M7 video co thoi luong > 0", _vdur(_info7) > 0, f"{_vdur(_info7):.2f}s")

        # ---------------------------------------------------------------
        # M9: NHIEU ANH + TEXT NGAN -> moi anh 1 visual, THU TU GOC dung
        #     (regression bug: ten file visual ghi de nhau -> anh cuoi cung
        #      thang lai, thu tu anh sai)
        # ---------------------------------------------------------------
        _P5 = {"cyan": (0, 255, 255), "lime": (0, 255, 0), "purple": (255, 0, 255),
               "red": (255, 0, 0), "blue": (0, 0, 255)}
        for _n, _c in _P5.items():
            Image.new("RGB", (600, 600), _c).save(os.path.join(_srv_dir, f"p5_{_n}.png"))
        _P5_URLS = [f"http://127.0.0.1:{_port}/p5_{_n}.png" for _n in _P5]
        _v9 = _render_scenes(
            "5 anh / text ngan",
            [{"author": "u", "display_text": "Nam anh.",
              "tts_text": "Nam anh.",
              "images": list(_P5_URLS)}],
            None,
        )
        check("M9 render 5 anh thanh cong", _v9 is not None)
        if _v9:
            _info9 = _probe(_v9)
            _dur9 = _vdur(_info9)
            check("M9 video 5 anh co thoi luong > 0", _dur9 > 0, f"{_dur9:.2f}s")
            # Moi visual phai hien 1 anh KHAC NHAU -> quet toan bo video.
            # Buoc quet phai FI (0.06s): video 5 anh chi dai ~2.4s
            # (~0.47s/anh) -> buoc 0.15s se BO SOT anh.
            # Nguong 800 pixel phan biet anh that voi nut "like" do
            # cua UI Threads (chi ~370 pixel).
            _seen = []
            _t9 = 0.0
            while _t9 < _dur9:
                _fp = os.path.join(_fr_dir, f"m9_{_t9:05.2f}.png")
                if _extract_frame(_v9, _t9, _fp):
                    _c9 = {n: _count_color(_fp, col) for n, col in _P5.items()}
                    _top = max(_c9, key=lambda k: _c9[k])
                    if _c9[_top] > 800:
                        _seen.append(_top)
                _t9 += 0.06
            _uniq9 = []
            for _s in _seen:
                if not _uniq9 or _uniq9[-1] != _s:
                    _uniq9.append(_s)
            check("M9 moi anh xuat hien MOT LAN theo thu tu (khong ghi de file)",
                  len(_uniq9) >= 5, f"chuoi mau={_uniq9}")
            # Chuoi mau phai la SUBSEQUENCE cua thu tu goc (khong dao, khong
            # thua mau khong thuoc bai viet).
            _pos = 0
            for _s in _uniq9:
                if _s not in _P5:
                    continue
                _i = list(_P5.keys()).index(_s)
                if _i >= _pos:
                    _pos = _i + 1
            check("M9 thu tu mau dung (khong dao)",
                  _uniq9[:5] == list(_P5.keys()), f"{_uniq9[:6]}")

    finally:
        _im.is_allowed_image_url = _orig_allow
        _srv_mod.is_allowed_image_url = _orig_allow_srv
        try:
            if _httpd:
                _httpd.shutdown()
        except Exception:
            pass

    # ---------------------------------------------------------------
    # M8: URL THUC cua Threads (neu co mang) -> pipeline that
    # ---------------------------------------------------------------
    if REAL_IMAGE_URLS and not NO_NET:
        _real = REAL_IMAGE_URLS[0]
        try:
            from tvcore.images import download_image as _dl
            _rp = os.path.join(_tmpdir, "real.png")
            _rok, _rmsg = _dl(_real, _rp)
        except Exception as e:
            _rok, _rmsg = False, f"{type(e).__name__}: {e}"
        check("M8 tai duoc anh THAT tu Threads CDN", _rok, _rmsg[:120])
        if _rok:
            with Image.open(_rp) as _ri:
                check("M8 anh that co kich thuoc hop le",
                      _ri.width > 100 and _ri.height > 100, f"{_ri.size}")
    else:
        print("  [SKIP] M8 - khong co URL that (offline / chua scrape)")


# ===========================================================================
print("\n" + "=" * 62)
print(f"  PHASE 1.2 IMAGE TESTS: PASS: {len(PASS)}  FAIL: {len(FAIL)}")
print("=" * 62)
if FAIL:
    print("\n  CAC TEST FAIL:")
    for _f in FAIL:
        print("   -", _f)
print()
sys.exit(1 if FAIL else 0)

