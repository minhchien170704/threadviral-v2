# PHASE 1.2 — THREAD IMAGE SUPPORT — BÁO CÁO

Branch: `feature/phase-1-2-thread-images`
Nền: `feature/phase-1-1-review-fixes` (`1e54ebf`, `0d3bc85`)

---

## 1. Nguyên nhân trước đây KHÔNG hiển thị ảnh

Khảo sát toàn bộ pipeline cho thấy hệ thống **chưa hề có khái niệm "ảnh bài viết"**:

| Tầng | Trạng thái trước Phase 1.2 | Vấn đề |
|---|---|---|
| Scraper (`tvcore/scrape.py`) | Playwright chỉ đọc `innerText`, `avatarUrl`, `timeAgo`, `isVerified`, stats | **Không đọc `<picture>`/`srcset`** → không có URL ảnh nào thoát ra khỏi DOM |
| Data model (`tvcore/utils.py`) | `SCENE_FIELDS` không có `images` | Cấu trúc scene không mang được ảnh |
| API (`server.py`) | `SceneItem` không có field `images` | Client không thể gửi ảnh, response không trả ảnh |
| Script/Scene | Không có bước nào xử lý ảnh | Ảnh rơi mất ngay ở ranh giới scraper → model |
| Pacing (`tvcore/pacing.py`) | `plan_visual_slices()` chỉ tính theo số phần text | Không có khái niệm "số ảnh cần hiển thị" → không thể chia visual để mỗi ảnh có 1 khoảng thời gian |
| Render card (`tvcore/cards.py`) | `draw_single_box()` chỉ vẽ avatar + text + footer | Không có khung ảnh, không có khả năng contain/không méo |
| FFmpeg | Overlay card PNG | Chỉ nhận PNG card, không có đường vào cho ảnh |
| Frontend (`index.html`) | Preview không có vùng ảnh | Người dùng không thấy tiền hình ảnh |

**Kết luận**: ảnh không hiển thị vì ảnh **chưa từng được thu thập**. Thiếu `images` ở đầu chuỗi → không có gì để truyền. Không phải lỗi hiển thị, mà là thiếu hoàn toàn tính năng.

Ngoài ra có **3 lỗi thật phát hiện trong lúc triển khai** (đã sửa, xem §6):

1. **File visual ghi đè nhau** → thứ tự ảnh sai (bug nghiêm trọng nhất).
2. **Card chừa khoảng trống** khi ảnh lỗi (reserve height trước khi biết ảnh có load được không).
3. **`-ss` đặt trước `-i`** trong lệnh trích frame → reset PTS, filter `enable='between(t,...)'` không bật → tưởng ảnh biến mất.

---

## 2. Module đã thay đổi

| File | Thay đổi |
|---|---|
| `tvcore/images.py` | **MỚI** — module an toàn tải & xử lý ảnh (allowlist host, chống SSRF, timeout, giới hạn dung lượng, magic bytes, decode PIL, tải song song, giữ thứ tự) |
| `tvcore/scrape.py` | Thêm trích xuất ảnh từ `<picture>` + `srcset`; `_extract_image_urls()` chọn biến thể độ phân giải lớn nhất; gắn `images` cho bài gốc và mọi comment |
| `tvcore/utils.py` | `SCENE_FIELDS` += `"images"`; `scene_to_dict()` chuẩn hoá `images` thành `List[str]` |
| `tvcore/config.py` | `MAX_IMAGES_PER_SCENE = 10` |
| `server.py` | `SceneItem.images: List[str]`; validator `_validate_images()` (kiểu, độ dài ≤ 2048, allowlist host, cap số ảnh) |
| `tvcore/pacing.py` | `plan_image_slices(image_count, segment_count)` — ánh xạ ảnh → visual theo thứ tự gốc |
| `tvcore/cards.py` | `fit_image_contain()`, `_img_probe()`, `draw_post_image()`; `draw_single_box()` nhận `image_path` + `image_badge`; `split_scene_if_long()` giữ `images` |
| `tvcore/render.py` | `_download_scene_images()` (tải ảnh song song cho mọi scene); `_n_eff = max(_n_text, _n_img)`; truyền `image_path`/`image_badge` vào card; **fix tên file visual** |
| `index.html` | `#prevImageBox` / `#prevImageStrip` — preview ảnh + badge thứ tự |
| `tools/test_phase_1_2_images.py` | **MỚI** — 131 assertions (unit + integration + E2E render thật) |
| `tools/test_phase_1_2_e2e_real.py` | **MỚI** — E2E với dữ liệu Threads thật (scrape → render → FFprobe → trích frame) |

---

## 3. Nguồn lấy ảnh & câu trả lời khảo sát (§3 đề bài)

**1. Hiện scraper có lấy URL ảnh không?** → **Không** (trước Phase 1.2).
**2. Nếu có, ảnh được lưu ở đâu?** → Không có. Nay lưu ở `scene["images"]` (list URL) → file cục bộ PNG trong `temp/images/`.
**3. Có phân biệt ảnh bài viết với avatar không?** → **Có.** Threads đặt ảnh bài viết trong `<picture>`; avatar nằm ngoài `<picture>` và có `alt="...profile picture"`. Scraper bỏ qua mọi `<img>` có `alt` chứa `profile picture`, và chỉ lấy `<picture>` trong container lá (post thật). Poster/thumbnail video cũng không nằm trong `<picture>` → không bị lẫn.
**4. Một bài nhiều ảnh có giữ thứ tự không?** → **Có.** `querySelectorAll('picture')` trả về theo thứ tự DOM = thứ tự carousel gốc; `dedupe_images()` loại trùng theo key URL (bỏ query string) nhưng **giữ nguyên thứ tự**.
**5. Ảnh có được truyền tới render không?** → **Có**, xem §5.
**6. Có giới hạn kích thước, timeout và xử lý ảnh lỗi không?** → **Có**, xem §8.

**Nguồn ảnh**: URL `srcset` của CDN Threads/Instagram (`*.fbcdn.net`, `*.cdninstagram.com`) — lấy trực tiếp từ DOM, **không đoán/bịa URL, không dựng URL từ HTML**.

---

## 4. Số lượng ảnh lấy được (dữ liệu thật)

| Bài Threads | URL | Số ảnh scraper lấy | Số ảnh tải được | Kích thước |
|---|---|---|---|---|
| Zuck – 1 ảnh | `threads.com/@zuck/post/DeZtp-TkQHh` | 1 | 1 | 2160×3260 |
| Zuck – **carousel 7 ảnh** | `threads.com/@zuck/post/Ddt7cL5EfUG` | **7** | **7/7** | toàn bộ tải thành công |
| Reply có ảnh | (trong cùng thread) | 1 | 1 | ok |

Tên file tải về giữ đúng thứ tự: `*_img0.png … *_img6.png`.

---

## 5. Cách giữ thứ tự ảnh (Scraper → Data model → API → Script/Scene → Render)

```
Scraper        <picture> theo thứ tự DOM
               -> _extract_image_urls()   (chọn srcset lớn nhất, dedupe giữ thứ tự)
               -> scene["images"] = List[str]
Data model     SCENE_FIELDS += "images" ; scene_to_dict() chuẩn hoá List[str]
API            SceneItem.images: List[str]  + validator _validate_images()
               (loại phần tử rác, chặn URL ngoài allowlist, cap MAX_IMAGES_PER_SCENE)
Script/Scene   split_scene_if_long() giữ nguyên "images" khi tách scene
Render         _download_scene_images() -> meta["image_paths"] (song song, giữ thứ tự)
               plan_image_slices(n_img, n_visual) -> ảnh gán TUẦN TỰ cho các visual
               draw_single_box(image_path=..., image_badge="2/7")
```

Các điểm "ảnh không bị mất" đã được bảo đảm & kiểm thử:

- Chọn comment (`select_comments`) → comment có `images` riêng, giữ nguyên.
- Tạo narration (`tts_text`) → URL ảnh **không** bị chèn vào text đọc (chỉ nằm ở trường riêng).
- Chia scene (`split_scene_if_long`) → copy `images`.
- Chia visual segment (`plan_visual_slices`) → `_n_eff = max(_n_text, _n_img)` đảm bảo **số visual ≥ số ảnh**, nên mỗi ảnh có ít nhất một khoảng thời gian riêng.
- Tạo reply card → `draw_single_box()` cho reply **không** truyền ảnh → ảnh không che bình luận (§7).
- Render nhiều cảnh → `_download_scene_images()` chạy cho **mọi** scene trong `scenes_meta`.

---

## 6. Layout đã hỗ trợ

### Vị trí ảnh trong card 9:16
Ảnh đặt **dưới nội dung text, trên footer thống kê** → không che avatar, tên tài khoản, badge verified hay chữ.

```
┌─────────────────────────┐
│ (avatar) zuck ✓   25/09  │  ← header
│ Agrippa said it's time… │  ← text
│ ┌─────────────────────┐ │
│ │      ẢNH BÀI VIẾT   │ │  ← contain, bo góc, viền xám
│ │            (2/7)    │ │  ← badge thứ tự
│ └─────────────────────┘ │
│ ♥ 12K  💬 340  ↻ 90  ↗  │  ← footer
└─────────────────────────┘
```

### Nguyên tắc hình học
- **Không méo tỷ lệ**: `fit_image_contain()` — scale theo `min(w/sw, h/sh)`, căn giữa, nền trung tính.
- **Ưu tiên contain** (không crop) → không che chủ thể/chữ trong ảnh.
- **Khung ảnh** = `min(42% chiều cao vùng an toàn, ...)`, tối thiểu 180px, có viền xám nhạt, bo góc 18px.
- **Text dài**: vòng lặp giảm cỡ chữ (tối thiểu 62% cỡ gốc) để vừa; nếu vẫn tràn → **cắt bớt chiều cao ảnh** (tối thiểu 140px) → nếu vẫn không đủ → **bỏ ảnh** (fallback card chữ) chứ không để tràn.
- **Gameplay background**: có lớp scrim đen 42% phía sau card + blur/giảm saturation nền → ảnh trên card trắng luôn nổi bật, không bị “chìm”.
- **Tắt gameplay**: card được đặt trên nền phẳng, ảnh hiển thị đúng, không phụ thuộc nền.

### Nhiều ảnh
`plan_image_slices(image_count, segment_count)`:
- `n_img <= n_seg` → `[0,1,2,…,n_img-1, n_img-1, …]` (ảnh cuối được giữ, **không có frame trống**).
- `n_img > n_seg` → lấy `n_seg` ảnh đầu theo thứ tự gốc (`step = n_img/n_seg`).

### Badge thứ tự
Khi bài có >1 ảnh, mỗi card hiện badge dạng `2/7` ở góc phải ảnh → người xem biết ảnh nào đang hiện.

---

## 7. Xử lý lỗi & bảo mật

| Rủi ro | Xử lý |
|---|---|
| Timeout tải ảnh | `IMAGE_TIMEOUT_SECONDS = 15`, `requests.get(..., timeout=...)` → bắt `requests.exceptions.Timeout` |
| File quá lớn | `IMAGE_MAX_BYTES = 12MB` — kiểm tra `Content-Length` trước **và** trong lúc stream (`iter_content`), ngắt ngay khi vượt |
| Giải nén bomb | `IMAGE_MAX_PIXELS = 40M` — chặn trước khi `im.load()` |
| MIME giả / file không phải ảnh | Kiểm tra **cả** `Content-Type` header **và** magic bytes (`sniff_image_format`: JPEG/PNG/WEBP); chỉ chấp nhận `ALLOWED_FORMATS` |
| Ảnh hỏng / không giải mã được | `_decode_image()` trả `None` → bỏ qua ảnh đó |
| URL 404 / hết hạn | HTTP ≠ 200 → bỏ qua ảnh đó, **không** crash video |
| URL không an toàn (SSRF) | `is_allowed_image_url()`: scheme phải http/https; host phải thuộc allowlist Meta CDN; IP literal private/loopback/link-local bị chặn. Áp dụng ở **3 lớp**: API validator, scraper, downloader |
| URL độc hại từ client | `server.py::_validate_images()` lọc URL ngoài allowlist, cắt URL > 2048 ký tự, cap `MAX_IMAGES_PER_SCENE` |
| FFmpeg với URL mạng | **Không bao giờ**. Ảnh luôn được tải về **file cục bộ PNG** rồi mới đưa vào PIL → overlay PNG → FFmpeg |
| File tạm | `download_image()` ghi `.part` rồi `os.replace` (atomic) và dọn `.part` trong `finally`. File ảnh + file visual được đưa vào `temp_files` → xóa trong `finally` của `render_video()` |
| Một ảnh lỗi | `prepare_images_async()` bỏ qua ảnh lỗi, trả list ảnh hợp lệ; render tiếp tục với ảnh còn lại (nếu không còn ảnh nào → card chữ như cũ) |

---

## 8. Test

### Unit (A–K) — `tools/test_phase_1_2_images.py`
| Nhóm | Nội dung |
|---|---|
| A | `is_allowed_image_url`: http/https ok, `file://`/`javascript:`/`data:` bị chặn, host ngoài allowlist bị chặn, IP private/loopback bị chặn |
| B | `pick_best_srcset`: chọn đúng biến thể `w` lớn nhất; `srcset` rỗng → `""`; URL không allowlist bị bỏ |
| C | `dedupe_images`: loại trùng, **giữ thứ tự** |
| D | `sniff_image_format`: JPEG/PNG/WEBP đúng; file HTML/EXE → `None` |
| E | `download_image`: URL lỗi, timeout, file sai định dạng, file quá lớn, MIME không hợp lệ → `(False, lý do)`, **không ném exception** |
| F | `plan_image_slices`: 0 ảnh, 1 ảnh, nhiều ảnh, số ảnh > số visual, số ảnh < số visual (lặp ảnh cuối, **không** frame trống) |
| G | `scene_to_dict` chuẩn hoá `images` → `List[str]`; scene cũ không có `images` → `[]` |
| H | `server.SceneItem` validator: loại phần tử rác, chặn URL ngoài allowlist, cap số ảnh, tương thích ngược |
| I | `draw_single_box`: ảnh ngang / dọc / vuông → đều render được, **không méo** (kiểm tra tỉ lệ pixel) |
| J | Card hỏng → ảnh lỗi → card vẫn vẽ được, **không có khoảng trống** |
| K | `split_scene_if_long` giữ `images` |

### Integration (L)
- L1: scraper (mock) → API `/api/scrape` → response có `images`.
- L2: API → scene → render pipeline nhận `images`.
- L3: ảnh + text dài; nhiều ảnh + reply; ảnh + gameplay; ảnh không gameplay; ảnh lỗi nhưng video vẫn render.

### E2E render thật (M) — FFprobe + trích frame + kiểm pixel
| Test | Nội dung |
|---|---|
| M1 | 1 ảnh, gameplay **ON** → frame có pixel màu của ảnh, chiếm ≥3% khung hình; ảnh vẫn hiện ở giữa video |
| M2 | 1 ảnh, gameplay **OFF** → ảnh hiện đúng, ≥3% khung hình |
| M3 | Ảnh lỗi → video vẫn render, không crash |
| M4 | 3 ảnh → frame 1/2/3 lần lượt là cyan → lime → purple (**thứ tự gốc**) |
| M5 | Ảnh + text dài (audio 34.87s) → video 34.90s ≥ audio, ảnh **không biến mất** khi visual chia segment, **không có khoảng trống** |
| M6 | Ảnh + reply → ảnh vẫn hiện, reply card không bị che, audio ok |
| M7 | URL ảnh 404 → video **vẫn render** (2.37s, audio aac) |
| M8 | Tải ảnh thật từ Threads CDN → ok, kích thước 2160×3260 |
| **M9** | **5 ảnh + text ngắn → mỗi ảnh 1 visual, thứ tự tuyệt đối: `['cyan','lime','purple','red','blue']`** (regression test cho bug ghi đè file) |

**Kết quả**: `PHASE 1.2 IMAGE TESTS: PASS: 131  FAIL: 0`

### Regression
- `tools/test_phase_1_1.py` → **PASS: 223, FAIL: 0**
- `tools/run_tests.py` → **PASS: 230, FAIL: 0**

---

## 9. Bằng chứng render thật

### 9.1 Video thật từ dữ liệu Threads thật

Lệnh:
```
python tools/test_phase_1_2_e2e_real.py "https://www.threads.com/@zuck/post/Ddt7cL5EfUG"
```

Kết quả:
```
scenes=8
root images = 7
  [0] .../823965183_17989304049103224_2823186847710225415_n.webp
  [1] .../821972247_17989304061103224_4453555141637471915_n.webp
  [2] .../824759758_17989304064103224_4776561777000666078_n.webp
  [3] .../825016683_17989304076103224_6127175043996382170_n.webp
  [4] .../825016677_17989304028103224_5917484667433795396_n.webp
  [5] .../822286014_17989304037103224_5095661334066140787_n.webp
  [6] .../823000524_17989304025103224_6511660523214713912_n.webp
downloaded 7/7
render: {'status': 'success', 'filename': 'ghien_comment_447f8132.mp4', 'duration': 11.15}
VIDEO: dur=11.17s audio=11.15s vcodec=h264 acodec=aac size=18964348
```

**FFprobe**: `dur=11.17s`, `audio=11.15s` → video ≥ audio (không cắt giọng đọc), codec `h264` + `aac`, dung lượng 18.9 MB.

### 9.2 Frame trích từ MP4 (bằng chứng ảnh xuất hiện)

Frames được trích bằng `ffmpeg -i <video> -ss T -frames:v 1 out.png` (đặt `-ss` **sau** `-i` để không reset PTS).

| Frame | Thời điểm | Độ bão hoà | Nội dung |
|---|---|---|---|
| `real_0.png` | 0.70s | 1.76% | card + ảnh 1 |
| `real_1.png` | 2.09s | 12.94% | card + ảnh 2 |
| `real_2.png` | 3.49s | 44.43% | card + ảnh 3 (màu mạnh) |
| `real_3.png` | 4.89s | 33.00% | card + ảnh 4 |
| `real_4.png` | 6.28s | 11.17% | card + ảnh 5 |
| `real_5.png` | 7.68s | 8.55% | card + ảnh 6 |
| `real_6.png` | 9.07s | 10.04% | card + ảnh 7 |
| `real_7.png` | 10.47s | 23.96% | card + ảnh 7 (giữ) |

Độ bão hoà màu thay đổi liên tục theo thời gian → **ảnh thực sự thay đổi trong video**, không phải ảnh tĩnh. Ảnh hiển thị rõ trong card, không méo tỷ lệ, không che avatar/tên/chữ.

> Ảnh chụp frame: xem `temp/probe/frames/real_*.png` (sinh ra bởi `tools/test_phase_1_2_e2e_real.py`).

### 9.3 Bằng chứng thứ tự ảnh tuyệt đối

Test M9 dùng 5 ảnh màu thuần (cyan → lime → purple → red → blue) và quét toàn bộ video:

```
[PASS] M9 moi anh xuat hien MOT LAN theo thu tu (khong ghi de file) - chuoi mau=['cyan', 'lime', 'purple', 'red', 'blue']
[PASS] M9 thu tu mau dung (khong dao) - ['cyan', 'lime', 'purple', 'red', 'blue']
```

→ Ảnh xuất hiện **lần lượt theo đúng thứ tự gốc**, mỗi ảnh một visual riêng, không ghi đè, không lặp sai.

### 9.4 Bằng chứng audio không bị cắt/lặp/mất

- Video 1 ảnh thật: `v=11.17s` vs `a=11.15s` → không cắt.
- Text dài (audio 34.87s): `v=34.90s` vs `a=34.87s` → không cắt, không lặp.
- Audio là 1 file AAC liên tục cho cả nhóm visual (thêm vào `audio_segment_paths` **đúng 1 lần**) → không lặp.

---

## 10. Ảnh chụp frame video

Các frame thật được lưu tại `temp/probe/frames/real_0.png` … `real_7.png` (video `outputs/ghien_comment_447f8132.mp4`).

![frame 0 - ảnh bài viết hiển thị trong card 9:16](temp/probe/frames/real_0.png)
![frame 3 - ảnh thứ tự 4 trong carousel](temp/probe/frames/real_3.png)

Frame cho thấy: card Threads trắng, header có avatar + tên + tick xanh, text nằm trên, **ảnh bài viết nằm giữa text và footer thống kê**, badge thứ tự ở góc phải ảnh, footer có like/comment/repost/share. Ảnh **không** che avatar, tên hay chữ.

---

## 11. Những giới hạn chưa giải quyết

1. **Video trong bài viết chưa được hỗ trợ.** Threads render video bằng `<video>`, không phải `<picture>` → scraper không lấy được. Ảnh tĩnh trong bài có video vẫn lấy được nếu `<picture>` tồn tại. (Ngoài phạm vi Phase 1.2.)
2. **Không liên kết ảnh ↔ câu narration.** Chưa có phân tích ảnh (không OCR, không Gemini) → ảnh hiển thị **lần lượt theo thứ tự gốc** khi không đủ thông tin để gắn ảnh cụ thể với một câu nói. Không tự bịa caption.
3. **Ảnh GIF:** `sniff_image_format` chỉ nhận JPEG/PNG/WEBP → GIF bị bỏ qua (có chủ ý, tránh rủi ro render).
4. **Ảnh AVIF:** header `Accept` có AVIF nhưng `ALLOWED_FORMATS` không gồm AVIF → ảnh AVIF sẽ bị từ chối (Threads CDN chủ yếu trả WEBP/JPEG nên không ảnh hưởng thực tế).
5. **Nhiều ảnh hơn số visual** → chỉ hiển thị `n_seg` ảnh đầu. Có thể mở rộng thành lưới ảnh nhỏ (slideshow nhiều ảnh/1 visual) trong phase sau.
6. **Chất lượng ảnh phụ thuộc CDN.** URL Threads có thể hết hạn → ảnh lỗi bị bỏ qua (video vẫn render, không có ảnh).
7. **Không có cache ảnh giữa các job.** Mỗi lần render tải lại (đơn giản, an toàn; có thể thêm cache theo URL hash sau).
8. **Ảnh reply/comment** chỉ hiển thị ở card của chính comment đó; ảnh bài gốc **không** được gắn vào reply card (chủ ý, để không che bình luận).
9. **Overlay gameplay** phủ lên card có thể làm giảm tương phản cục bộ; đã giảm bằng scrim 42% + giảm saturation nền nhưng chưa tự động đo tương phản theo vùng ảnh.

---

## 12. Commit hash

| Commit | Nội dung |
|---|---|
| `cc77d63` | `feat(phase-1.2): ho tro anh bai viet Threads (scraper -> API -> render)` — toàn bộ thay đổi Phase 1.2 (module, test, report) |

Lịch sử branch:

```
cc77d63 feat(phase-1.2): ho tro anh bai viet Threads (scraper -> API -> render)
0d3bc85 docs(phase-1.1): them PHASE_1_1_FIX_REPORT.md va bang chung FFprobe
1e54ebf fix(phase-1.1): giai quyet toan bo review blockers (C1, C2, H1-H6, M2/M4/M5)
```

## 13. Xác nhận `main` không thay đổi

```
$ git rev-parse main
8fdc4ba12f804cd9c1ef566a7a9c0cd65e43d03e

$ git rev-parse origin/main
8fdc4ba12f804cd9c1ef566a7a9c0cd65e43d03e

$ git log --oneline main -1
8fdc4ba feat(lfs): dua video nen + nhac nen vao Git LFS - clone ve co san
```

✅ `main` **không bị sửa** — vẫn ở `8fdc4ba`, khớp với `origin/main`. Tất cả thay đổi nằm trên branch `feature/phase-1-2-thread-images`.

```
$ git diff main HEAD --stat
 tools/test_phase_1_2_e2e_real.py    |  151 +++++
 tools/test_phase_1_2_images.py      | 1051 +++++++++++++++++++++++++++++++++++
 tvcore/cards.py                     |  196 ++++++-
 tvcore/config.py                    |    1 +
 tvcore/images.py                    |  382 +++++++++++++
 tvcore/pacing.py                    |  229 +++++++-
 tvcore/render.py                    |  273 ++++++++-
 tvcore/scrape.py                    |   60 +-
 tvcore/utils.py                     |   12 +
 ...
 23 files changed, 5829 insertions(+), 136 deletions(-)
```

## 14. Kết luận

✅ **FIXED** — ảnh bài viết Threads **thực sự xuất hiện trong video render cuối cùng**, được chứng minh bằng:

1. Render thật từ bài Threads thật (carousel 7 ảnh) → MP4 11.17s, h264+aac.
2. Trích frame từ MP4 → ảnh hiện rõ trong card 9:16, độ bão hoà màu thay đổi theo thời gian (ảnh thật sự đổi, không phải ảnh tĩnh).
3. Test M9 chứng minh thứ tự ảnh tuyệt đối `['cyan','lime','purple','red','blue']` trong video.
4. 131/131 test Phase 1.2 PASS; 223/223 test Phase 1.1 PASS (không hồi quy); 230/230 full suite PASS.

Tính ổn định của Phase 1.1 **không bị đánh đổi**: toàn bộ logic pacing/audio/QC cũ giữ nguyên, ảnh chỉ được thêm vào như một lớp hiển thị bổ sung với fallback an toàn (ảnh lỗi → bỏ qua, card chữ như cũ).

