# PHASE 1.1 — FIX REPORT (REVIEW BLOCKERS)

**Branch:** `feature/phase-1-1-review-fixes`
**Base:** `feature/phase-1-foundation-fixes` @ `ccc906e`
**Baseline main:** `8fdc4ba` (KHÔNG bị thay đổi)
**Commit Phase 1.1:** `1e54ebf`
**Ngày:** 2026-11-01
**Review:** độc lập — kết luận `REQUEST CHANGES` (2 CRITICAL, 6 HIGH, 3 MEDIUM)

---

## 0. TÓM TẮT KẾT LUẬN

| Mã | Mức | Vấn đề | Trạng thái |
|----|------|--------|-----------|
| **C1** | P0 | Visual không phủ hết audio (video trống giữa audio) | ✅ **FIXED** |
| **C2** | P0 | `score_reasons` không giải thích đúng `total` | ✅ **FIXED** |
| **H1** | P1 | `NaN` / `Infinity` lọt qua `validate_weights` | ✅ **FIXED** |
| **H2** | P1 | QC pass 2 kiểm tra thời lượng chết | ✅ **FIXED** |
| **H3** | P1 | `sanitize_viral_claim` sinh câu vô nghĩa / lặp chủ ngữ | ✅ **FIXED** |
| **H4** | P1 | Bỏ sót claim "xôn xao"; sửa nhầm câu trung tính | ✅ **FIXED** |
| **H5** | P1 | `detect_hook_angle` gán humor nhầm ("vài") | ✅ **FIXED** |
| **H6** | P1 | `normalize_angle` không nhận alias không dấu / nhiều từ | ✅ **FIXED** |
| **M2** | P2 | `verify_bugs.py` chỉ `print`, không assert / exit code | ✅ **FIXED** |
| **M4** | P2 | Hai nguồn tính timeline độc lập | ✅ **FIXED** (có tài liệu + 1 nguồn duy nhất) |
| **M5** | P2 | Reply card lặp toàn bộ text gốc khi chia visual | ✅ **FIXED** |

**Kết quả test:** `run_tests.py` 230/230 PASS · `test_e2e.py` 18/18 PASS · `verify_bugs.py` 58/58 PASS (exit 0) · `test_phase_1_1.py` **223/223 PASS** (exit 0).

> ⚠️ **Không tự kết luận APPROVE.** Báo cáo này cung cấp bằng chứng; quyết định approve thuộc về reviewer. Xem §12 "Giới hạn còn lại".

---

## 1. C1 — Visual không phủ hết audio (P0)

### 1.1 Nguyên nhân gốc

Trong `tvcore/render.py` (vòng lặp dựng timeline) có **hai bước độc lập** quyết định số visual:

```python
_visual_segs = split_visual_segments(dur1, MAX_SCENE_SECONDS)   # N = ceil(dur/12)
_n_vis = len(_visual_segs)                                      # ví dụ N = 3
_text_parts = split_visual_text_parts(sc["display_text"], _n_vis) # trả về K = 2
...
if len(_text_parts) > 1:
    for _vi, _tp in enumerate(_text_parts):   # ← chỉ lặp K = 2 lần!
        _seg = _visual_segs[_vi]
        timeline_segments.append({... "start": cur_time + _seg["start"],
                                   "end":   cur_time + _seg["end"]})
```

- `split_visual_segments()` chia **theo thời lượng** → `N = ceil(27.3 / 12) = 3` đoạn: `[0–9.1] [9.1–18.2] [18.2–27.3]`.
- `split_visual_text_parts()` chia **theo số dòng** → nếu text chỉ wrap được 8 dòng và mỗi thẻ chứa tối đa 7 dòng, chỉ được `K = 2` phần (và còn bị gộp phần cuối nếu ngắn → có thể còn `K = 1`).

**Khi `K < N`:** vòng lặp chỉ render `K` visual. Đoạn thứ `N` (`18.2–27.3s`) **không có ảnh nào được đưa vào `timeline_segments`** → FFmpeg overlay kết thúc ở `18.2s` trong khi audio vẫn phát đến `27.3s` → **9.1 giây màn hình trống**.

Nguyên nhân sâu hơn: **thiếu một nguồn quyết định duy nhất**. Hai hàm tính độc lập, không có ràng buộc nào buộc `len(text_parts) == len(visual_segs)`.

### 1.2 Giải pháp

**a) `tvcore/pacing.py` — thêm `plan_visual_slices()` (nguồn chia visual DUY NHẤT):**

```python
def plan_visual_slices(audio_duration, text_parts=1, max_segment=MAX_SCENE_SECONDS):
    n_min  = max(1, int(math.ceil(dur / seg_max)))   # số đoạn tối thiểu theo thời lượng
    n_text = max(1, int(text_parts or 1))             # số phần text thực tế
    count  = max(n_min, n_text)                       # ← điểm mấu chốt

    part_index = []
    for i in range(count):
        if n_text >= count:
            part_index.append(i)                       # mỗi visual 1 phần text
        else:
            part_index.append(min(i, n_text - 1))      # phần text CUỐI lặp lại
    segments = split_visual_segments(dur, seg_max, n_segments=count)
```

- `count = max(n_min, n_text)` → **luôn đủ visual để phủ kín audio**.
- Khi `n_text < count`: các visual sau dùng **lại phần text cuối cùng** → **có hình trong suốt**, không tạo đoạn trống, và **thứ tự nội dung text vẫn được giữ nguyên** (đọc theo thứ tự, phần cuối hiển thị lâu hơn).
- `split_visual_segments()` được mở rộng thêm tham số `n_segments` (ép số phần) — vẫn chia đều nên **tổng == audio_duration, sai số ≤ 0.001s**.

**b) `tvcore/render.py` — vòng lặp render dùng `plan_visual_slices()`:**

```python
_probe_parts = split_visual_text_parts(sc["display_text"], 99, ...)  # chia TRƯỚC
_text_parts  = _probe_parts or [sc["display_text"]]                  # fallback an toàn
_plan_v      = plan_visual_slices(dur1, len(_text_parts), MAX_SCENE_SECONDS)
_visual_segs = _plan_v["segments"]
_part_index  = _plan_v["part_index"]
```

Sau đó mỗi visual được render 1 ảnh riêng (cache theo cặp `(phần text, có footer)`), và **audio chỉ thêm vào `audio_segment_paths` ĐÚNG 1 LẦN** cho cả nhóm → audio không bị cắt/lặp.

> **Lưu ý kỹ thuật quan trọng (bug đã phát hiện khi viết test):**
> Bản đầu tiên tôi chia text lại theo `count` **sau khi** tính plan. Điều này **sai**, vì `split_visual_text_parts()` có logic "gộp phần cuối nếu quá ngắn" → có thể trả về **ít hơn** `count` phần → phần thừa bị nhân bản → **lặp lại toàn bộ text**. Test "thứ tự nội dung text được giữ nguyên" bắt được lỗi này (`len=428 vs 293`). Fix cuối cùng: **chia text trước → lấy `K` thật → `plan_visual_slices(dur, K)`**.

**c) `tvcore/qc.py` — thêm check `visual_gap_audio`:** nếu visual phủ `< audio - 0.05s` → **error** (không còn là check chết).

### 1.3 Regression tests C1

**`tools/test_phase_1_1.py` — 223 checks, bao gồm:**

| Nhóm | Loại | Nội dung |
|------|------|----------|
| C1 unit | UNIT | 7 case bắt buộc: audio 12.5s/text 1 dòng, 24s/8 dòng, 25s/1 dòng, 27.3s/8 dòng, 36s/1 dòng, 27.3s/1 dòng, 30s/3 dòng. Mỗi case kiểm: không khoảng trống (≤0.05s), segment đầu = 0, segment cuối = audio_duration, tổng visual == audio, mỗi visual có phần text, mỗi đoạn ≤ 12s |
| C1 split | UNIT | `split_visual_segments(n=1,2,3,5,8)` — liên tục + phủ kín 27s |
| C1 integration | INTEGRATION | Mô phỏng **đúng logic render.py**: mỗi visual → 1 phần text thật; tổng visual == audio; **thứ tự nội dung text giữ nguyên**; text rỗng → fallback an toàn |
| C1 render | **RENDER (thật)** | **6 video thật** + FFprobe + đọc timeline từ **lệnh FFmpeg thật** |

**6 kịch bản render thật** (đều chạy TTS + FFmpeg thật, không mock):

| # | Kịch bản | Kết quả |
|---|----------|---------|
| 1 | nền tắt, text dài | ✅ 3 visual, phủ 35.570/35.570s, gap 0.000s |
| 2 | nền tắt, **display 1 dòng + audio dài** | ✅ 2 visual, phủ 22.430/22.430s, gap 0.000s |
| 3 | **nền gameplay BẬT**, display 1 dòng + audio dài | ✅ 2 visual, phủ 22.430/22.430s, gap 0.000s |
| 4 | **có reply**, text dài | ✅ 3 visual, phủ 35.570/35.570s, gap 0.000s |
| 5 | nhiều cảnh (dài + ngắn) | ✅ phủ 38.610/38.610s, gap 0.000s |
| 6 | nền gameplay bật, text dài | ✅ 3 visual, phủ 35.570/35.570s, gap 0.000s |

**Cách kiểm tra visual gap (không phải "chỉ đếm phần tử trong Python"):**

```python
# Bắt lệnh FFmpeg THẬT, rồi trích overlay enable='between(t,start,end)'
for m in re.finditer(r"overlay=[^:]+:[^:]+:enable='between\(t,([-\d.]+),([-\d.]+)\)'", joined):
    spans.append((float(m.group(1)), float(m.group(2))))
```

→ Sau đó gộp span, tính `covered = Σ(end−start)`, và assert:
- `covered ≥ video_duration − 0.05` (phủ ≥ 99.8%)
- danh sách khoảng trống giữa các visual **rỗng**
- span đầu `start == 0`
- kèm `ffprobe -show_streams` để đo `video.duration` vs `audio.duration` (thực tế, không phải giá trị ước lượng).

**Bằng chứng FFprobe thật** (đầy đủ: `docs/phase_1_1_ffprobe_evidence.txt`):

```
TRUONG HOP: A. nen tat / text dai
  File            : outputs\ghien_comment_125f2bed.mp4
  Container       : mov,mp4,m4a,3gp,3g2,mj2
  Video stream    : codec=h264 1080x1920 fps=30/1 dur=35.600s
  Audio stream    : codec=aac dur=35.570s
  AUDIO KHONG CAT : 35.570s audio <= 35.600s video -> OK
  AUDIO KHONG LAP : audio < video -> OK
  Timeline visual (3 overlay spans):
       0.000 ->   11.857   (11.857s)
      11.857 ->   23.713   (11.856s)
      23.713 ->   35.570   (11.857s)
  Tong thoi luong visual  : 35.570s / video 35.570s
  THIEU (khoang trong)    : 0.000s -> OK (khong co khoang trong)
  Khoang trong giua cac visual: KHONG CO
  Khoang im lang > 1.5s : 0 -> OK

TRUONG HOP: B. nen tat / display 1 dong + audio dai
  Video stream    : codec=h264 1080x1920 fps=30/1 dur=22.433s
  Audio stream    : codec=aac dur=22.430s
  AUDIO KHONG CAT : 22.430s audio <= 22.433s video -> OK
  Timeline visual (2 overlay spans):
       0.000 ->   11.215   (11.215s)
      11.215 ->   22.430   (11.215s)
  THIEU (khoang trong)    : 0.000s -> OK (khong co khoang trong)

TRUONG HOP: C. nen gameplay BAT / display 1 dong + audio dai
  ... cung ket qua 22.430s, 0 khoang trong ...

TRUONG HOP: D. co reply / text dai
  ... 3 spans: 0-11.857, 11.857-23.713, 23.713-35.570, 0 khoang trong ...
```

**Kết luận:** Trước fix, video 27.3s có visual `0–18.2s` rồi **trống 9.1s**. Sau fix, **mọi khoảng thời gian đều có visual, gap = 0.000s**.

---

## 2. C2 — `score_reasons` phải giải thích đúng `total` (P0)

### 2.1 Nguyên nhân gốc

`tvcore/content.py::score_comment()` tính:

```python
total = Σ(parts[k] * w[k] for k in WEIGHT_KEYS) + like_bonus
```

nhưng `score_reasons` chỉ liệt kê tiêu chí **vượt ngưỡng**:

```python
def _add(key, label, score, threshold, why):
    if score >= threshold:                     # ← chỉ thêm khi VƯỢT ngưỡng
        score_reasons.append({... "contribution": score * w[key] ...})
```

**Hệ quả:** các tiêu chí **dưới ngưỡng vẫn đóng góp vào `total`** (vì `total` dùng toàn bộ 5 parts) nhưng **không có mặt trong `score_reasons`** → `Σ contribution` lệch `total` tới **0.14** → phần "giải thích điểm" **nói dối**.

### 2.2 Giải pháp

- `score_reasons` giờ liệt kê **MỌI** thành phần thực sự đóng góp: 5 tiêu chí + `likes` (nếu `n_likes > 0`).
- Mỗi entry có **đủ** `name`, `key`, `label`, `score`, `weight`, `contribution`, `is_primary`, `why`.
- `is_primary` = tiêu chí ≥ ngưỡng nổi bật (ngưỡng **chỉ dùng để gắn cờ**, KHÔNG dùng để lọc).
- Công thức scoring **không đổi** (`total = Σ parts×w + like_bonus`) — đúng yêu cầu "không thay đổi công thức nếu không có lý do".
- **Hiệu chỉnh sai số làm tròn**: sau khi làm tròn 4 chữ số, `Σ contribution` có thể lệch `total` vài `1e-5`. Một bước hiệu chỉnh đặt phần dư vào entry cuối → `Σ contribution == total` **chính xác tuyệt đối** (sai số 0.000000 trong test).
- `likes` bonus chỉ xuất hiện **1 lần** (test riêng khẳng định).

### 2.3 Regression tests C2

`tools/test_phase_1_1.py` (mục `[UNIT] C2`):

| Test | Kết quả mẫu |
|------|--------------|
| Nhiều tiêu chí dưới threshold | `sum=0.243300 total=0.243300 diff=0.000000` |
| Có likes bonus | `sum=0.434800 total=0.434800 diff=0.000000` |
| Comment ngắn ("ok") | `sum=0.015000 total=0.015000` |
| Comment viral | `sum=0.316000 total=0.316000` |
| Câu hỏi ý kiến | `sum=0.400200 total=0.400200` |
| Weights tùy chỉnh | `sum=0.522100 total=0.522100` |
| Comment bị loại | `rejection_reason` đầy đủ cho 3/3 comment loại |
| Selected comments | `score_reasons` khớp `score` |
| Mọi entry có `name`/`score`/`weight`/`contribution` | ✅ |
| 5 tiêu chí + `likes` đều có mặt | ✅ |
| `likes` không cộng trùng | `n=1, bonus=0.0501` |
| Mọi entry có `is_primary` | ✅ |

Tất cả assert `abs(sum(contribution) - total) <= 1e-4`.

---

## 3. H1 — NaN / Infinity (P1)

### 3.1 Nguyên nhân gốc

`validate_weights()` chỉ kiểm:

```python
if fv < 0: raise WeightError(...)
if fv > 1: raise WeightError(...)
```

Với `NaN`, **cả hai so sánh đều `False`** → `NaN` lọt qua → `total = NaN` → JSON response chứa `NaN` (**không hợp lệ RFC 8259**) → `JSON.parse` phía JS ném exception. `+Infinity` bị chặn (>1) nhưng `-Infinity` cũng bị chặn (<0) — chỉ `NaN` là lỗ hổng thật; **tuy nhiên server cũng chưa trả 400** cho body sai khuôn dạng (Pydantic trả 422).

### 3.2 Giải pháp

**a) `tvcore/content.py::validate_weights()`:**

```python
if not math.isfinite(fv):
    raise WeightError(f"Trọng số '{k}' phải là số hữu hạn (không được NaN/Infinity).")
if fv < 0: ...
if fv > 1: ...
```

**b) `server.py` — chặn ngay tầng API:**

- Thêm `field_validator("weights")` trên `AnalyzeRequest` → `math.isfinite()` check → raise `ValueError` với message rõ ràng.
- Thêm `RequestValidationError` handler → trả **HTTP 400** (thay vì 422 mặc định của Pydantic) kèm `detail` mô tả field và lỗi.

> Không âm thầm thay giá trị không hợp lệ bằng default → client luôn nhận 400 + message.

### 3.3 Regression tests H1

**UNIT** (`validate_weights` raise `WeightError`): `NaN`, `+Infinity`, `-Infinity`, `NaN` ở tất cả key, âm, `> 1`, tất cả `0`, sai kiểu.
**UNIT**: `NaN` không lọt vào `score_comment()`.
**UNIT**: `json.dumps(..., allow_nan=False)` raise → chứng minh `NaN` không phải JSON hợp lệ (nên phải chặn trước).
**E2E** (`fastapi.testclient.TestClient` — HTTP thật, body thật):

| Payload | Kỳ vọng | Thực tế |
|---------|----------|---------|
| `interesting=NaN` | 400 | ✅ 400 |
| `interesting=+Infinity` | 400 | ✅ 400 |
| `interesting=-Infinity` | 400 | ✅ 400 |
| `interesting=-0.1` | 400 | ✅ 400 |
| `interesting=5.0` | 400 | ✅ 400 |
| weights hợp lệ | 200 | ✅ 200 |
| response chứa `NaN`/`Infinity` | không | ✅ không |

Response 200 cũng được kiểm `Σ contribution == score` và không chứa chuỗi `NaN`/`Infinity`.

---

## 4. H2 — QC pass 2 phải kiểm tra thời lượng thực (P1)

### 4.1 Nguyên nhân gốc

`tvcore/render.py` gọi:

```python
qc2 = check_script(scenes, speed=..., audio_durations=audio_durations)   # ← thiếu scene_durations
```

`tvcore/qc.py::check_script()` khi thiếu `scene_durations` thì **tự tính lại**:

```python
_scene_dur = scene_duration(text, _aud, speed)
```

Mà `scene_duration()` **luôn trả `>= audio_duration`** (`base = max(est, audio, read)`) → điều kiện `_scene_dur + 0.01 < _aud` **không bao giờ đúng** → check `audio_longer_than_scene` **chết hoàn toàn**, không bao giờ bắt được lỗi.

### 4.2 Giải pháp

**a) `tvcore/render.py`:**

- **Di chuyển QC pass 2 xuống SAU khi đã dựng xong timeline** (Bước 2).
- Thu thập thời lượng **thực tế** trong vòng lặp scene:
  - `scene_audio_durs` = thời lượng audio chính của cảnh (đã pad, đo từ file).
  - `scene_visual_durs` = **tổng** thời lượng các visual của cảnh (cộng từ các segment đã đưa vào `timeline_segments`).
- Truyền cả hai vào `check_script(..., audio_durations=scene_audio_durs, scene_durations=scene_visual_durs)`.

**b) `tvcore/qc.py`:**

- **Xoá fallback** `scene_duration(text, _aud, speed)` — không được tự tính lại bằng chính hàm đang được kiểm tra.
- Thiếu `scene_durations` → **warning** `missing_scene_duration` ("thiếu thời lượng cảnh thực tế - không kiểm tra được audio có bị cắt mất không") thay vì im lặng bỏ qua.
- Thêm **error** `visual_gap_audio` khi `scene_dur + 0.05 < audio` → bắt đúng lỗi C1 ở tầng QC.

### 4.3 Regression tests H2

| Test | Kỳ vọng | Thực tế |
|------|----------|---------|
| Thiếu `scene_durations` | warning `missing_scene_duration` | ✅ `['missing_scene_duration']` |
| `scene_durations=[12.0]`, audio `27.0` | error `audio_longer_than_scene` | ✅ `['visual_gap_audio', 'audio_longer_than_scene']` |
| `scene_durations=[18.0]`, audio `27.0` | error `visual_gap_audio` | ✅ |
| `scene_durations=[27.15]`, audio `27.0` | không error | ✅ `[]` |
| Kiểm tra mã nguồn `qc.py` | không còn `scene_duration(text, _aud, speed)` | ✅ |
| **Render thật** (6 video) | QC pass 2 chạy với duration thực → không error → render thành công | ✅ 6/6 |

> Test "render pipeline thực sự gọi QC với duration thực tế" được thực hiện gián tiếp: nếu QC pass 2 vẫn dùng fallback (luôn ≥ audio) thì check `visual_gap_audio` **không bao giờ** bắt lỗi; sau fix, 6 video render thật với visual phủ kín → QC pass với `scene_visual_durs` thực tế → không error → render thành công. Test mục 4 ở trên chứng minh trực tiếp rằng QC **bắt được** lỗi khi đưa duration sai.

---

## 5. H3 — `sanitize_viral_claim` sinh câu vô nghĩa (P1)

### 5.1 Nguyên nhân gốc

`tvcore/script.py` thay **từng cụm từ rời rạc** bằng `re.sub`:

```python
_VIRAL_REPLACEMENTS = [
    (r"cả\s+threads\s+đang\s+phát\s+sốt", "Bài này đang được bàn luận"),
    (r"tranh\s+luận\s+không\s+hồi\s+kết", "đang được tranh luận"),
    ...
]
```

Câu gốc: *"Câu chuyện này đang làm Threads tranh luận không hồi kết."*
→ `cả threads...` không khớp, nhưng `làm mưa`/`tranh luận không hồi kết` → thay → *"Câu chuyện này đang làm Threads **đang được tranh luận**."* → **vô nghĩa** (lặp "đang", chủ ngữ sai).

Câu *"Cả Threads đang phát sốt vì bài này"* → *"**Bài này đang được bàn luận** vì bài này"* → **lặp chủ ngữ**.

### 5.2 Giải pháp

- Thay bảng `_VIRAL_REPLACEMENTS` bằng **`_VIRAL_SENTENCE_REPLACEMENTS`** — ưu tiên **thay CẢ CÂU / cụm dài** bằng mẫu trung tính **đã viết tay** (đọc tự nhiên, không lặp chủ ngữ, không thêm thông tin chưa được chứng minh). Các mẫu câu chính là các template hook/CTA cũ đã bịa viral.
- Sau đó mới áp các cụm rời rạc cho phần còn sót.
- Bổ sung bước **sửa lỗi ngữ pháp phát sinh**: `đang làm Threads đang được tranh luận` → `đang được tranh luận`; `đang được được` → `đang được`; `bàn luận. vì bài này` → `bàn luận.`.
- Dọn khoảng trắng/dấu kép thừa.

**Ví dụ đã sửa đúng:**

| Input | Output |
|-------|--------|
| *"Câu chuyện này đang làm Threads tranh luận không hồi kết."* | *"Câu chuyện này được kể lại qua vài dòng."* |
| *"Cả Threads đang phát sốt vì bài này"* | *"Bài này đang được bàn luận."* |
| *"Có một món ăn đang làm mưa Threads mà ít ai biết."* | *"Có một món ăn đang được quan tâm mà ít người để ý tới."* |

---

## 6. H4 — Bỏ sót claim / sửa nhầm câu trung tính (P1)

### 6.1 Nguyên nhân gốc

Hai vấn đề độc lập:

1. **Bỏ sót**: pattern `cả\s+threads\s+đang` là tiền tố chung → *"Threads xôn xao"* **không khớp** → claim bịa **lọt qua** mà không bị sanitize.
2. **Sửa nhầm**: pattern `cả\s+threads\s+đang` quá rộng → *"cả Threads đang bàn luận về bài này"* (câu **trung tính, có nghĩa đúng**) bị đánh dấu là viral **và bị sửa**, làm mất ý nghĩa gốc.

### 6.2 Giải pháp

- **Siet pattern**: mỗi pattern **bắt buộc chứa phần định tính** (cường điệu). `cả\s+threads\s+đang` → tách thành `cả\s+threads\s+đang\s+phát\s+sốt`, `cả\s+threads\s+xôn\s+xao`, `cả\s+threads\s+đang\s+cười`.
- **Bổ sung claim thực sự mang tính cường điệu**: `threads\s+xôn\s+xao`, `đang\s+xôn\s+xao`.
- Câu trung tính **không còn khớp pattern nào** → `has_unsupported_viral_claim()` trả `False` và `sanitize_viral_claim()` **giữ nguyên chuỗi**.

### 6.3 Regression tests H3/H4

**8 claim viral phải bị nhận diện VÀ loại hết sau sanitize:**
*"Cả Threads đang phát sốt với bài này"* · *"Hàng nghìn người tranh cãi"* · *"Cả mạng xã hội chia thành hai phe"* · *"Bài viết gây bão"* · *"Ai cũng phải bật cười"* · *"Threads xôn xao"* · *"làm mưa Threads"* · *"Bài chuyện này đang làm Threads tranh luận không hồi kết."*

**4 câu trung tính phải giữ nguyên (byte-for-byte) VÀ không bị coi là viral:**
*"cả Threads đang bàn luận về bài này"* · *"Câu chuyện này có một câu hỏi khiến người đọc phải chọn một phe."* · *"Một món ăn trong bài này mà ít người để ý tới."* · *"Bài này đặt ra một câu hỏi khiến người đọc phải tự trả lời."*

**Không lặp chủ ngữ:** 3 câu mẫu → không từ nào lặp.
**Đọc tự nhiên:** 8 claim → không có `"  "` (khoảng trắng kép), không `".."`, không `"đang được được"`.

---

## 7. H5 — `detect_hook_angle` (P1)

### 7.1 Nguyên nhân gốc

```python
if re.search(r"\b(haha|vcl|vl|vai|kms|hai|loi|cringe|meme|buon cuoi)\b", t):
    return "humor"
```

- **`vai`** (viết tắt "vãi") khớp `"vài"` → câu *"…qua **vài** dòng…"* (dùng để kể drama) bị gán **humor** nhầm.
- **`hai`** khớp rất nhiều từ vô nghĩa theo ngữ cảnh.
- Thiếu ranh giới từ chặt chẽ (`\b` với dấu tiếng Việt không đáng tin).
- Thiếu nhận diện **emoji cười** (😂).
- Thiếu các cụm kích thích tranh luận: *"mọi người thấy sao"*, *"mọi người nghĩ gì"*.

### 7.2 Giải pháp

- **Bỏ `vai`** khỏi regex humor.
- Chỉ dùng cụm **rõ nghĩa**: `haha|hehe|hihi|huhu` · `cười|buồn cười|hài hước|hài hài|hề hề` · `vcl|vl|kms|cringe|meme|comedy|troll|funny`.
- Thêm **emoji cười** `[\U0001F600-\U0001F64F]`.
- Thêm ranh giới từ `(?<!\w)...(?!\w)` cho mọi cụm.
- Kiểm `"cười"` trên bản **CÓ DẤU** để `"cuối"` (cuối bài) không bị nhầm.
- Thêm cụm tranh luận: `thấy sao|nghĩ sao|nghĩ gì|thấy gì|bạn nghĩ|bạn thấy|mọi người thấy|mọi người nghĩ`.
- **Thứ tự ưu tiên rõ ràng**: `?` → humor → debate → emotion → surprise → **curiosity (fallback trung tính)**.
- Quan trọng: các pattern so khớp trên bản **không dấu** (`t = deaccent(raw)`) nên pattern cũng phải **không dấu** — sửa lỗi ban đầu tôi viết pattern có dấu nhưng so khớp trên `t`.

### 7.3 Regression tests H5

| Input | Kỳ vọng | Thực tế |
|-------|----------|---------|
| *"Có một vụ drama ngoài đời được kể lại qua vài dòng trong bài này."* | `curiosity` (**không** humor) | ✅ |
| *"HAHAHA đỉnh quá 😂"* | `humor` | ✅ |
| *"Chi tiết này thật là vui, mọi người cười nhiều quá"* | `humor` | ✅ |
| *"Mọi người thấy sao về chuyện này"* | `debate` | ✅ |
| *"Bạn nghĩ sao về chuyện này?"* | `debate` | ✅ |
| *"Không ai ngờ chi tiết này lại ở đây"* | `surprise` | ✅ |
| *"Câu chuyện này chạm đúng chỗ tôi đang trải qua"* | `emotion` | ✅ |
| *"Chi tiết cuối bài này mới là thứ đáng chú ý nhất."* | `curiosity` (**"cuối" ≠ humor**) | ✅ |
| `""` (rỗng) | `curiosity` | ✅ |
| *"Một câu chuyện bình thường không có gì đặc biệt"* | `curiosity` | ✅ |

---

## 8. H6 — `normalize_angle` (P1)

### 8.1 Nguyên nhân gốc

```python
key2 = deaccent(key)          # "to mo"
if key2 in _ANGLE_ALIASES:    # _ANGLE_ALIASES chỉ có "tò mò" -> không khớp
    ...
key3 = key2.replace(" ", "") # "tomo"
for k, v in _ANGLE_ALIASES.items():
    if k.replace(" ", "") == key3:   # "tòmò" != "tomo" -> không khớp
```

Lỗi nằm ở **chỗ so khớp**: bảng alias lưu khóa **có dấu** (`"tò mò"`), nhưng biến so khớp đã **bỏ dấu** (`"to mo"`) → không bao giờ trùng. Tương tự `"thu vi"` vs `"thú vị"`, `"hai huoc"` vs `"hài hước"`.

### 8.2 Giải pháp

- Chuẩn hoá **một chiều cho cả hai vế**: `_norm(s) = deaccent(s).replace(" ", "")`.
- Và bổ sung alias mới: `to mo`, `tòmò`, `thú vị`/`thu vi` → `surprise`, `hài hước`/`hai huoc`/`hài`/`funny` → `humor`, `bất ngờ`/`bat ngo` → `surprise`.
- Giữ nguyên toàn bộ alias tiếng Anh cũ.

### 8.3 Regression tests H6

19 case: `tò mò`→curiosity · `to mo`→curiosity · `tòmò`→curiosity · `thú vị`→surprise · `thu vi`→surprise · `hài hước`→humor · `hai huoc`→humor · `debate`→debate · `conflict`→debate · `thanh luan`→debate · `humor`→humor · `emotion`→emotion · `relatability`→emotion · `surprise`→surprise · `curiosity`→curiosity · `DEBATE`→debate · `Unexpected`→surprise · `None`→None · `""`→None · `"khong_hop_le"`→None.

---

## 9. P2 — M2, M4, M5

### M2 — `tools/verify_bugs.py` phải assert + exit code

**Trước:** script chỉ `print` kết quả → **luôn exit 0** → regression không bao giờ làm CI đỏ.

**Sau:**
- Thêm helper `check(name, cond, detail)` ghi vào `PASS`/`FAIL`.
- Cuối script: `sys.exit(1 if FAIL else 0)` + in danh sách check fail.
- Giữ **toàn bộ** phần in tiến độ cũ (không xoá test cũ).
- Bổ sung check cho **C1 / C2 / H1 / H3 / H4 / H5 / H6** (bảo đảm lỗi đã sửa không tái xuất hiện).
- **Thực tế đã trả lời review:** vì assert viết đúng hành vi, 3 check ban đầu tôi viết **sai kỳ vọng** (không phản ánh hành vi thật) đã **fail** → buộc tôi phải đối chiếu lại hành vi thật của `tokens()` / `spam_reasons()` và sửa **kỳ vọng** (không sửa hành vi ngoài phạm vi). Cụ thể:
  - `tokens()` **giữ dấu** tiếng Việt (đúng thiết kế, dùng cho so khớp deaccent-tolerant) → assert theo hướng ngược lại là sai.
  - `spam_reasons()` chỉ bắt các mẫu quảng cáo **có dấu** rõ ràng → câu viết không dấu không khớp pattern là hành vi hiện tại, **không phải regression Phase 1.1**.

### M4 — Rà soát nguồn timeline

| Hàm | Caller | Kết luận |
|-----|--------|----------|
| `plan_timeline()` | `server.py:511` (`/api/timeline`) | **GIỮ** — là API công khai |
| `adjust_to_target_total()` | `tools/run_tests.py` | **GIỮ** — còn dùng, xoá sẽ phá test |
| `split_visual_segments()` | `plan_visual_slices()` + `run_tests.py` | **GIỮ** (là helper của plan) |
| `needs_visual_split` (field) | `plan_timeline()` | **GIỮ** |
| `visual_segments` (field) | `plan_timeline()` | **GIỮ** |
| `plan_visual_slices()` | `render.py` | **NGUỒN CHIA VISUAL DUY NHẤT** |

**Hành động:**
- `render.py` **không còn gọi trực tiếp** `split_visual_segments()` — chỉ dùng `plan_visual_slices()` (test kiểm bằng regex trên mã nguồn, bỏ qua comment).
- Ghi rõ **nguồn timeline chính thức** trong docstring `tvcore/pacing.py`.
- **Không xoá hàm nào** (đều còn caller hoặc compatibility requirement) → **không refactor rộng**.

### M5 — Reply card lặp toàn bộ text gốc

**Nguyên nhân:** reply card dùng `box1_img` (ảnh thẻ gốc chứa **toàn bộ** text của scene). Khi scene đã chia visual, thẻ gốc chứa text đầy đủ → reply card hiển thị lại toàn bộ → lặp nội dung.

**Fix (`tvcore/render.py`):**
- Lưu `_last_visual_img` = ảnh của **visual cuối** (chứa phần text cuối).
- Reply card dùng `_last_visual_img` thay cho `box1_img` khi scene đã chia (`_did_split`).
- Giữ cơ chế resize vùng an toàn dọc (stacked card) như cũ.
- **Stats (likes/comments/reposts/shares)**: hiển thị ở **visual đầu** (như cũ) → không mất thông tin.
- **Footer**: ở **visual cuối**; cache ảnh theo cặp `(phần text, có footer)` để visual cuối vẫn hiện footer dù chia sẻ ảnh với visual trước → **hành vi nhất quán**.

**Test:** `M5 render.py co logic dung anh visual CUOI cho reply card` (kiểm source có `_last_visual_img` + `_did_split`) + **render thật** trường hợp "co reply / text dai" (PASS, 3 visual, phủ kín 35.570s).

---

## 10. KẾT QUẢ CHẠY TEST (TRƯỚC / SAU)

### 10.1 Trước khi sửa (baseline `ccc906e`)

| Suite | Kết quả | Ghi chú |
|-------|---------|---------|
| `run_tests.py` | 230 PASS / 0 FAIL | ✅ (chưa bắt được C1/C2/H1–H6) |
| `test_e2e.py` | 18 PASS / 0 FAIL | ✅ (video ngắn, không kích hoạt C1) |
| `verify_bugs.py` | **exit 0** dù có vấn đề | ❌ **không có assert** (M2) |
| **C1 repro thủ công** | ❌ **video 27.3s trống 9.1s** (`0–18.2s` có visual, `18.2–27.3s` trống) | **CRITICAL** |
| **C2 repro thủ công** | ❌ `Σ contribution` lệch `total` **0.14** | **CRITICAL** |
| H1 | ❌ `validate_weights({...NaN})` **không raise** | HIGH |
| H2 | ❌ `check_script` không bao giờ phát hiện `audio_longer_than_scene` | HIGH |
| H3 | ❌ *"Câu chuyện này đang làm Threads **đang được tranh luận**."* | HIGH |
| H4 | ❌ *"cả Threads **đang** bàn luận về bài này"* bị sửa nhầm; *"Threads xôn xao"* bị bỏ sót | HIGH |
| H5 | ❌ *"…qua **vài** dòng…"* → `humor` (sai) | HIGH |
| H6 | ❌ `normalize_angle("to mo")` → `None` | HIGH |

### 10.2 Sau khi sửa (commit `1e54ebf`)

```
$ python tools/run_tests.py
  PASS: 230   FAIL: 0                                     -> exit 0

$ python tools/test_e2e.py
  PASS: 18   FAIL: 0                                      -> exit 0

$ python tools/verify_bugs.py
  VERIFY BUGS: PASS: 58  FAIL: 0                          -> exit 0

$ python tools/test_phase_1_1.py
  PHASE 1.1 REGRESSION: PASS: 223  FAIL: 0               -> exit 0
```

**Không test nào bị xoá.** `run_tests.py` và `test_e2e.py` vẫn PASS nguyên vẹn sau khi sửa (chứng minh không phá hành vi cũ).

### 10.3 Phân loại test

| Suite | Phân loại |
|-------|-----------|
| `test_phase_1_1.py` — `[UNIT] C1/C2/H1/H2/H3/H4/H5/H6/M2/M4/M5` | **UNIT** — hàm đơn lẻ, không I/O |
| `test_phase_1_1.py` — `[INTEGRATION] C1` | **INTEGRATION** — `cards.split_visual_text_parts` + `pacing.plan_visual_slices` phối hợp, mô phỏng đúng logic `render.py` |
| `test_phase_1_1.py` — `[E2E] H1` | **E2E** — `fastapi.testclient.TestClient` gọi HTTP thật vào `/api/analyze` |
| `test_phase_1_1.py` — `[RENDER] C1` | **RENDER THẬT** — TTS + FFmpeg thật, đo bằng `ffprobe`, đọc timeline từ lệnh FFmpeg thật |
| `test_e2e.py` | **E2E** — scrape Threads → render → đo LUFS → trích frame kiểm vùng an toàn |
| `run_tests.py` | **UNIT + INTEGRATION** |
| `verify_bugs.py` | **UNIT** (script kiểm tra có assert + exit code) |

---

## 11. FILE & FUNCTION ĐÃ THAY ĐỔI

| File | Function / phần | Thay đổi |
|------|-----------------|----------|
| `tvcore/pacing.py` | **`plan_visual_slices()`** (mới) | Nguồn chia visual DUY NHẤT — `count = max(n_min, n_text)`, `part_index` ánh xạ phần text |
| `tvcore/pacing.py` | `split_visual_segments()` | Thêm tham số `n_segments` (ép số phần) |
| `tvcore/pacing.py` | docstring module | Ghi rõ nguồn timeline chính thức |
| `tvcore/render.py` | vòng lặp dựng timeline | Dùng `plan_visual_slices()`; cache ảnh theo `(part, footer)`; `_last_visual_img`; `_did_split`; `scene_visual_durs` / `scene_audio_durs` |
| `tvcore/render.py` | QC pass 2 | Chuyển **sau** khi dựng timeline; truyền `scene_durations` thật |
| `tvcore/render.py` | reply card | Dùng ảnh visual **cuối** khi scene đã chia (M5) |
| `tvcore/render.py` | import | `from .pacing import MAX_SCENE_SECONDS, plan_visual_slices, scene_duration` |
| `tvcore/content.py` | **`score_comment()`** | `score_reasons` đầy đủ 5 tiêu chí + likes, có `name`/`score`/`weight`/`contribution`/`is_primary`; hiệu chỉnh sai số làm tròn |
| `tvcore/content.py` | **`validate_weights()`** | `math.isfinite()` → `WeightError` |
| `tvcore/qc.py` | `check_script()` | Bỏ fallback `scene_duration()`; thêm `missing_scene_duration` (warning) + `visual_gap_audio` (error) |
| `tvcore/script.py` | **`sanitize_viral_claim()`** | Thay cả câu trước; bảng `_VIRAL_SENTENCE_REPLACEMENTS`; sửa lỗi ngữ pháp phát sinh |
| `tvcore/script.py` | `_VIRAL_CLAIM_PATTERNS` | Siết — mỗi pattern có phần định tính; thêm `xôn xao` |
| `tvcore/script.py` | **`detect_hook_angle()`** | Bỏ `vai`; emoji cười; ranh giới từ; thêm cụm debate; thứ tự ưu tiên rõ ràng |
| `tvcore/script.py` | **`normalize_angle()`** | Chuẩn hoá 1 chiều (bỏ dấu + bỏ khoảng trắng) |
| `tvcore/script.py` | `_ANGLE_ALIASES` | Thêm alias có/không dấu, nhiều từ |
| `server.py` | `AnalyzeRequest` | `field_validator("weights")` → `math.isfinite()` |
| `server.py` | `@app.exception_handler(RequestValidationError)` | Trả **HTTP 400** kèm message rõ ràng |
| `server.py` | import | `import math`, `RequestValidationError`, `JSONResponse` |
| `tools/verify_bugs.py` | toàn bộ | `check()` + assert + `sys.exit(1)`; thêm check C1/C2/H1/H3/H4/H5/H6 |
| `tools/test_phase_1_1.py` | **mới** | 223 checks (unit + integration + E2E + render thật + FFprobe) |
| `tools/collect_ffprobe_evidence.py` | **mới** | Sinh `docs/phase_1_1_ffprobe_evidence.txt` |

---

## 12. NHỮNG VẤN ĐỀ CHƯA GIẢI QUYẾT / GIỚI HẠN

1. **Không tự kết luận APPROVE.** Báo cáo cung cấp bằng chứng; reviewer cần xác nhận lại.

2. **C1 — chiến lược "phần text cuối lặp lại"**:
   Khi `n_text < count` (text ngắn, audio dài), các visual sau dùng **lại phần text cuối**. Ưu điểm: không có khoảng trống. Đánh đổi: người xem thấy cùng nội dung lặp lại.
   → **Có thể cải thiện** bằng cách hiện "typing"/progress indicator hoặc tách phần cuối thành các phần nhỏ hơn — **chưa làm** vì nằm ngoài phạm vi (liên quan UI).

3. **C1 — thời lượng audio thay đổi giữa các lần chạy** (TTS không hoàn toàn tất định): test dùng audio thật nên số visual có thể ±1 giữa các lần chạy. Test **không assert số lượng visual cụ thể** — chỉ assert tính chất của timeline (liên tục, phủ kín, bắt đầu 0, kết thúc đúng audio). Đây là chủ ý để test không flaky.

4. **H2 — QC pass 2 chạy SAU khi tạo ảnh** (để có duration thực). Nghĩa là nếu visual có gap, hệ thống đã tạo ảnh xong mới báo lỗi → tốn một chút thời gian/CPU trước khi fail. Đánh đổi này là **cố ý**: đảm bảo QC dùng **số đo thực tế** thay vì tính lại (yêu cầu chính của H2).

5. **H3/H4 — bảng sanitize vẫn là danh sách thủ công.** Mọi claim viral **mới** (ngoài danh sách) vẫn lọt. Giải pháp bền vững hơn là LLM-based rewrite + validator, cần dependency mới → **không làm** trong Phase 1.1.

6. **H5 — ngôn ngữ tiếng Anh.** `detect_hook_angle` so khớp trên bản không dấu; từ tiếng Anh giữ nguyên → OK. Nhưng cụm tiếng Anh viết không dấu ("haha" ✓, "lol" **chưa** có) → có thể bổ sung.

7. **H6 — alias mới** (`thú vị` → `surprise`) là **quyết định diễn giải**: `thú vị` có thể thuộc `curiosity` cũng hợp lý. Tôi chọn `surprise` vì `surprise` đã là nhóm "bất ngờ / thú vị lạ". Cần reviewer xác nhận.

8. **`verify_bugs.py` — 3 kỳ vọng cũ.** Tôi đã viết assert theo phỏng đoán rồi phát hiện sai (xem §M2). Đã sửa kỳ vọng theo **hành vi thật** — **không sửa hành vi** ngoài phạm vi Phase 1.1. Nếu reviewer muốn `tokens()` bỏ dấu hoặc `spam_reasons()` nhận dấu cụ không dấu → đó là task khác.

9. **M4 — `plan_timeline()` và `plan_visual_slices()` vẫn là 2 hàm**, vì `plan_timeline` phục vụ API `/api/timeline` (ước lượng theo text, chưa có audio) còn `plan_visual_slices` phục vụ render (có audio thật). Chúng **không tính timeline cho cùng một mục đích** → chưa phải 2 nguồn mâu thuẫn. Đã ghi rõ trong docstring.

10. **Chưa kiểm tra hồi quy pixel-level**: chưa so sánh frame-by-frame video trước/sau để đảm bảo thẻ hiển thị **đúng nội dung** ở mọi đoạn. Test hiện tại khẳng định **timeline** (có/không gap, phủ kín) chứ không khẳng định **nội dung từng đoạn**. Đề xuất bổ sung ở Phase 1.2.

11. **`auto_split` / `plan_timeline` chưa tích hợp `plan_visual_slices`**: `/api/timeline` vẫn trả `visual_segments` theo cách cũ (chỉ theo thời lượng) → **có thể lệch** với những gì render thực tế làm. Chưa sửa vì `/api/timeline` là API ước lượng pre-render (chưa có audio thật). **Nên đồng bộ ở Phase 1.2.**

---

## 13. COMMIT & XÁC NHẬN `main` KHÔNG BỊ THAY ĐỔI

```
* 1e54ebf (HEAD -> feature/phase-1-1-review-fixes) fix(phase-1.1): giai quyet toan bo review blockers (C1, C2, H1-H6, M2/M4/M5)
* ccc906e (feature/phase-1-foundation-fixes) fix: split visual segments with ceil so each segment <= 12s
* 2c30a3b fix: validate hook angle generation + improve script factual grounding
* 76bd0d8 ...
* 8fdc4ba (main, origin/main) feat(lfs): dua video nen + nhac nen vao Git LFS - clone ve co san
```

### Xác nhận:

- ✅ **Commit Phase 1.1: `1e54ebf`**
- ✅ Branch mới: **`feature/phase-1-1-review-fixes`**, tạo từ `feature/phase-1-foundation-fixes` (`ccc906e`).
- ✅ **`main` KHÔNG bị thay đổi**: `main` vẫn ở `8fdc4ba` — không commit nào trên `main`, không `rebase`/`merge`/`reset` lên `main`.
- ✅ **Không rewrite lịch sử**: commit mới nằm trên đỉnh `ccc906e`.
- ✅ Không thêm dependency mới (`requirements.txt` không đổi).
- ✅ Không thay đổi giao diện / tính năng ngoài phạm vi.
- ✅ Không xoá test cũ — `run_tests.py` (230) và `test_e2e.py` (18) vẫn PASS nguyên vẹn.
- ✅ Không giảm tiêu chuẩn QC — QC pass 2 **bổ sung** 2 check mới (`visual_gap_audio`, `missing_scene_duration`), không nới lỏng check nào.

---

## 14. TÓM TẮT BẰNG CHỨNG

| Yêu cầu | Bằng chứng |
|---------|-----------|
| Visual phủ kín toàn bộ audio, không gap > 0.05s | FFprobe + timeline từ lệnh FFmpeg thật: `gap = 0.000s` ở **6/6** kịch bản render |
| Tổng visual == audio (sai số ≤ 0.05s) | `35.570/35.570`, `22.430/22.430`, `38.610/38.610` — sai số **0.000s** |
| Audio không bị cắt/lặp/mất | `audio_duration ≤ video_duration` và `audio < video` ở 6/6; `silencedetect` không có khoảng im lặng > 1.5s |
| `Σ contribution == total` (≤ 1e-4) | 12 case, `diff = 0.000000` |
| NaN/Inf bị từ chối, API 400 | 5 case `validate_weights` + 6 case HTTP (400/400/400/400/400/200) |
| QC pass 2 dùng duration thực | 4 case `check_script` + 6 render thật không error |
| Sanitize đọc tự nhiên, không lặp chủ ngữ, giữ câu trung tính | 8 claim viral + 4 câu trung tính (byte-for-byte) |
| Hook angle chính xác | 10 case `detect_hook_angle` + 19 case `normalize_angle` |
| `verify_bugs.py` có assert + exit code | 58/58 PASS, exit 0; fail → exit 1 |
| `main` không bị thay đổi | `main` vẫn ở `8fdc4ba` |
