# ThreadViral v2 — Góc Threads AI Video Maker

Công cụ tự động biến bài viết Threads thành video dọc (TikTok / Reels / Shorts) 9:16, 1080×1920, 30 FPS.

Không chỉ ghép ảnh — pipeline phân tích nội dung, chấm điểm bình luận, viết kịch bản, đồng bộ thời gian và kiểm tra chất lượng trước khi xuất.

---

## 🎬 Pipeline 4 giai đoạn

```
Cào Threads → Phân tích → Lọc & chấm điểm → Viết kịch bản → Đồng bộ → QC → Xuất
```

### Giai đoạn 1 — Lọc & chấm điểm bình luận (`tvcore/content.py`)

| Tiêu chí | Trọng số |
|---|---|
| Độ thú vị | 25% |
| Khả năng gây thảo luận | 25% |
| Hài hước / bất ngờ | 20% |
| Liên quan bài đăng | 20% |
| Dễ hiểu, ngắn gọn | 10% |

- **Chống trùng lặp:** Jaccard trên tập từ khóa (≥0.82) + SequenceMatcher (≥0.86) → bắt được cả trùng tuyệt đối lẫn gần giống
- **Lọc spam:** link, SĐT, quảng cáo, hashtag-only, chữ in hoa, nội dung vô nghĩa
- **Chọn đa dạng (MMR):** không chỉ chọn top điểm — phạt bình luận có góc nhìn giống bình luận đã chọn
- **Phân loại chủ đề:** hài hước / tình yêu / drama / tâm sự / tranh luận / đời sống
- **Đánh giá mức hấp dẫn** + nhận diện bài thiếu ngữ cảnh

### Giai đoạn 2 — AI viết kịch bản (`tvcore/script.py`)

- **3 câu hook** theo 3 góc tiếp cận khác nhau: gây tò mò / chất thảo luận / chạm cảm xúc / hài / bất ngờ
- Tóm tắt bài viết · lời dẫn chuyển cảnh · **nhận xét biên tập** · CTA
- 2 tầng: Gemini (nếu có API key) → fallback template nội bộ offline
- Ràng buộc: trung thành bài gốc, không bịa tình tiết, không đổi ý nghĩa bình luận

### Giai đoạn 3 — Thời lượng động (`tvcore/pacing.py`)

Thời lượng cảnh = `max(thời gian đọc tối thiểu, thời lượng TTS thực tế)` + đệm

| Số từ | Thời gian |
|---|---|
| 1–10 | 2–3s |
| 11–20 | 3–4s |
| 21–35 | 4–6s |
| 36–50 | 6–8s |
| >50 | chia nhiều cảnh |

Nếu giọng đọc ngắn hơn thời gian đọc → **thêm im lặng**, không cắt lời đọc.

### Giai đoạn 4 — Kiểm tra chất lượng (`tvcore/qc.py`)

**Trước render:** chữ bị cắt · bình luận trùng · lời đọc lệch chữ hiển thị · spam · thời lượng không đủ đọc · thiếu watermark

**Sau render:** khung hình đen (`blackdetect`) · hình đứng yên (`freezedetect`) · lệch thời lượng · thiếu audio

Mỗi lỗi kèm **thời điểm xảy ra** + **cách sửa**.

---

## 🖼️ Giao diện Threads chân thực

- Icon lấy từ **SVG thật của Threads** (512×512 PNG), recolor không méo (`maxdiff=0`)
- Layout theo metrics đo từ app Threads thật: avatar 76px, tên 33px bold, thời gian 27px xám, icon hành động 42px, badge verified 34px
- Nền tối phía sau card (scrim 42%) → gameplay không gây mất tập trung

## 📐 Vùng an toàn TikTok / Reels

| Cạnh | Vùng an toàn |
|---|---|
| Trái | 40px |
| Phải | 170px (cột nút) |
| Trên | 150px |
| Dưới | 380px (caption + nav) |

## 🔊 Chuẩn âm thanh

`loudnorm=I=-14:TP=-1.5:LRA=11` → **-14 LUFS**, True Peak **-1.5 dBFS**

---

## 🚀 Cài đặt

```bash
pip install -r requirements.txt
playwright install chromium
```

Cần `ffmpeg` trong `bin/` hoặc trong `PATH`. Kiểm tra bằng:

```bash
ffmpeg -version
```

> **Ghi chú Windows:** dùng `Chay_ThreadViral_App.bat` (tự tạo venv + cài deps).
> **Ghi chú Linux/macOS:** `python app_launcher.py` hoặc `python server.py` — ffmpeg phải có trong `PATH`.

### 📦 Tài nguyên đi kèm (Git LFS)

Video nền (`backgrounds/`) và nhạc nền (`bgm/`) được lưu bằng **Git LFS** —
clone repo sẽ tự tải về đầy đủ:

```bash
git clone https://github.com/minhchien170704/threadviral-v2.git
cd threadviral-v2
git lfs install        # chỉ cần 1 lần trên máy
git lfs pull          # nếu clone bằng --skip-smudge hoặc file còn là pointer
```

> Nếu chưa cài `git-lfs`: <https://git-lfs.github.com>
> Không có LFS thì tool vẫn chạy, chỉ là **không có video nền / nhạc nền**
> (FFmpeg sẽ dùng nền đen đơn sắc).

## ▶️ Chạy

```bash
python server.py          # http://127.0.0.1:8000
# hoặc
python app_launcher.py    # launcher tự kiểm tra môi trường
```

## 🧪 Test

```bash
python tools/run_tests.py    # 132 unit test
python tools/test_e2e.py     # 18 e2e test (scrape + render + ffprobe)
python tools/verify_bugs.py  # bằng chứng cho 6 bug đã review
```

**150/150 pass.**

## 📦 API

| Endpoint | Mô tả |
|---|---|
| `POST /api/scrape` | Cào bài + bình luận (đã lọc trùng & chấm điểm) |
| `POST /api/analyze` | Phân tích bài + chấm điểm từng bình luận |
| `POST /api/script` | Sinh 3 hook + kịch bản đầy đủ |
| `POST /api/timeline` | Tính thời lượng tối ưu từng cảnh |
| `POST /api/qc` | Kiểm tra kịch bản trước render |
| `POST /api/qc/video` | Kiểm tra file video sau render |
| `GET /api/stats` | Thống kê + lịch sử + hiệu quả theo hook |
| `POST /api/stats/analytics` | Nhập số liệu TikTok Analytics |
| `POST /api/render` | Render (job nền + SSE tiến trình) |

## 📁 Cấu trúc

```
tvcore/
  content.py    GĐ1 - chấm điểm, lọc trùng, MMR, phân loại chủ đề
  script.py     GĐ2 - hook, kịch bản, nhận xét biên tập
  pacing.py     GĐ3 - thời lượng động
  qc.py         GĐ4 - kiểm tra trước/sau render
  analytics.py  Thống kê, lịch sử, preset
  cards.py      Vẽ thẻ Threads (Pillow)
  render.py     Engine FFmpeg
  scrape.py     Playwright cào Threads
  tts.py        Edge-TTS + cache
  icons.py      Icon Threads SVG
```

---

## ⚠️ Lưu ý bản quyền

- Chỉ đổi/xoá watermark của bên khác khi **được phép**
- Nội dung bình luận giữ nguyên ý nghĩa gốc, không bịa
- Bản quyền media (video nền, nhạc) thuộc về chủ sở hữu tương ứng
