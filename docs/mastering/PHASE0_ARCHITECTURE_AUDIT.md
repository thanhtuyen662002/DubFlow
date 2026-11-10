# Phase 0 — Architecture Audit: Unified Intelligent Video Production & Mastering Engine

Ngày: 2026-11-10. Đọc trực tiếp trên `main` = `e4ffe90` (v1.1.94, sau PR #636). Tag mới nhất: `v1.1.94`.
Tài liệu kỹ thuật (`reports/tai-lieu-ky-thuat-dubflow.md`) chỉ dùng để định hướng; mọi kết luận dưới đây
đều dẫn tới file/hàm trong code.

## 1. Tóm tắt

DubFlow **đã có** phần lớn các "khối thực thi" mà đặc tả cần: tách cảnh (`docu/shots.py`), chọn cảnh có
quy tắc (`docu/matcher.py`), framing an toàn (`docu/safezone.py`), soundscape (`docu/soundscape.py`), bộ dựng
có lớp với zoom/crop/màu/LUT/chuyển cảnh/âm thanh/phụ đề (`src-tauri/src/edit.rs`), render song song an toàn
(`edit_parts.rs`, `media/adapter.py::_render_parts`), ducking bằng số nguyên (`mix/adapter.py`), kiểu thời gian
tick nguyên (`crates/media-contracts`, `asr/adapter.py::TimePoint`).

Thứ **chưa có** là một tầng *quyết định* dùng chung: mỗi workflow tự hard-code cách chọn cảnh/nhịp
(Bán hàng: các đoạn ≤5 s chia đều, quay vòng; Tổng hợp: một đoạn cắt do người dùng chọn cho mỗi mục; Phim tài
liệu: matcher riêng; Lồng tiếng: giữ nguyên hình). Không có hợp đồng "kế hoạch hậu kỳ" có phiên bản, không có
policy theo workflow, không có cache phân tích media dùng chung.

Vì vậy hướng đi là: **thêm `engine/dubflow/mastering/` làm Planner** (quyết định + lưu kế hoạch), **không thêm
renderer mới**; mỗi workflow chuyển kế hoạch sang input của renderer hiện có của nó. Khi mức tối ưu = `off`
(mặc định), không đường nào gọi planner, nên kết quả cũ giữ nguyên từng byte của lệnh FFmpeg.

## 2. Bản đồ năng lực theo code thật

Ký hiệu: **R** = tái sử dụng nguyên trạng · **E** = có, cần mở rộng · **N** = chưa có, làm mới · **X** = có
trong đặc tả cũ (Dynamic Pacing & Studio Mastering) nhưng sai, loại bỏ/sửa thiết kế.

| Năng lực | Trạng thái | Bằng chứng trong code | Ghi chú |
|---|---|---|---|
| Ranh giới cảnh | **R** | `docu/shots.py`: `scdet` 320 px ngưỡng 8.5 + phát hiện chuyển cảnh chậm từ thumbnail xám 32×18 @5 fps (`slow_cuts`, `merge`), cache theo `identity()` | Chỉ trả giây float; thumbnail bị xóa sau khi tính → không giữ được mức chuyển động. Adapter mới dùng lại các hàm thuần (`parse`, `merge`, `slow_cuts`, `_frames`, `_difference`) trong **một** lượt giải mã. |
| Mức chuyển động | **N** | Chưa có; `shots.changes()` tính được chênh lệch giữa thumbnail nhưng không ai lưu | Tính từ cùng thumbnail của lượt tách cảnh, không cần model. |
| Khoảng lặng | **E** | `worker/b2_audio.py::_speech_resumes_ms` (RMS 260 ≈ −42 dBFS, khung 20 ms) chỉ cho cửa sổ lồng tiếng | Adapter mới dùng `silencedetect` của FFmpeg LGPL trong cùng lượt. |
| Transcript / word timestamps | **R** | `worker/whisper_cues.py` (word-aligned, `timing_basis`), ADR-0022/0032 | Planner chỉ tham chiếu cue id, không sao chép. |
| Chọn cảnh theo lời dẫn | **E** | `docu/matcher.py::plan` (một lát = một cú máy, 1.8–6.5 s, nhịp `RHYTHM`, ưu tiên cảnh được trích dẫn, tránh lặp) | Chỉ docu dùng; nằm trong float giây. Phase 4 mở rộng tiêu chí ngữ nghĩa, không viết lại. |
| Hook | **N / X** | Không có code "3 s chuyển động mạnh nhất trong 50–85 %" — chỉ có trong đặc tả cũ. Shorts (`features/shorts/ai.ts`) có prompt AI chọn đoạn hook theo phụ đề | Loại bỏ quy tắc cố định; Phase 2+ chấm điểm ứng viên. Lồng tiếng: mặc định tắt. |
| Micro-pacing 200 ms | **X** | Không tồn tại trong code | Không triển khai. Cắt chỉ tại khoảng lặng/ranh giới cảnh có đánh giá an toàn. |
| Zoom theo chu kỳ 105→115→108 % | **X** | Không tồn tại. Hiện có: Ken Burns 4 % (`roughcut.KEN_BURNS_ZOOM`), overscan 108 % (preset do người dùng chọn, `safezone.OVERSCAN`), zoom tối đa 114 % để che phụ đề (`safezone.MAX_ZOOM`), editor `Zoom{from,to,x,y}` 1–3× | Planner đề xuất zoom theo vùng chú ý trong giới hạn workflow; không có chu kỳ. |
| Lật ngang, xoay 0.5–1°, kéo giãn 2–3 % khi xuất | **X** | Editor có `flip_h`, `rotate` 90/180/270 do người dùng chọn; không có tự động | Planner **không bao giờ** sinh flip/xoay lẻ/kéo giãn (nguyên tắc 12). Kiểm thử chặn. |
| Framing an toàn (phụ đề cháy, logo, chữ) | **R** | `docu/safezone.py` (`framing`, `frame_logos`, `watermark_state`), OCR PP-OCRv4 | Dùng lại làm "vùng bảo vệ" cho zoom. |
| Reframe dọc/ngang | **R** | `reframe/`, editor `aspect`/`fill` (`edit.rs::reframe_filter`) | |
| B-roll theo kịch bản | **R** | `docu/visual.py`, `matcher.py`, overrides + chỉnh lát cắt (ADR-0051) | |
| Bán hàng: chọn/xếp cảnh | **E** | `product/render.py::plan`: đoạn bằng nhau ≤5 s, quay vòng theo thứ tự, không biết ranh giới cảnh | Điểm nối Phase 1: thay *chỉ* bước phân bổ đoạn khi bật tối ưu. |
| Bán hàng: kịch bản | **E** | `product/script.py` (câu + caption theo thời lượng TTS thật), `product/reuse.py` | Chưa ràng buộc câu ↔ cảnh (Phase 2). |
| Tổng hợp | **E** | `compilation/job.py`: chỉ nhận `final_vi.mp4` trong DubFlow Output (`load_spec`, `compilation.rs::item_dir`); mỗi mục một đoạn cắt; `loudnorm I=-16` mỗi clip; receipt theo đoạn | Mở rộng nhận footage chưa lồng tiếng cần ADR (đổi điều kiện đầu vào). |
| Phim tài liệu: cấu trúc kịch bản | **E** | `docu/script.py::CHAPTERS` cố định 4 hồi | Giữ làm preset tương thích (Phase 4). |
| Âm thanh: ducking/mix | **R** | `mix/adapter.py` (`duck_gain_milli` mặc định 350, mẫu nguyên), `mix/streaming.py`; editor `StemLevels` 0–2, `normalize` −14 LUFS; compilation −16 LUFS; soundscape −31/−22 LUFS | Tỷ lệ "100/10/8 %" của đặc tả cũ **không có** trong code; audio policy sẽ tham chiếu các tham số có sẵn. |
| Tách giọng / stems | **R** | `separation/`, editor `edit_stems`/`edit_separate` | |
| Phụ đề động (highlight từ) | **N** | Có word timestamps và ASS (`subtitle/`, `product/layout.py`), chưa có karaoke/highlight | Phase 3/5. Với kịch bản mới phải bám TTS cuối (như `product/script.captions`). |
| Màu / LUT | **E** | Editor `Color`, `lut` (.cube), docu `LOOK` curves cố định | Chưa có đo màu để cân giữa các nguồn (Phase 2/4). |
| Chuẩn hóa xuất | **E** | `export/adapter.py`, `qc/provenance.py`, ADR-0018/0045; compilation kiểm tra kích thước/fps/thời lượng | Thiếu QC peak/loudness đo lại sau render cho mọi workflow (Phase 5). |
| Render song song an toàn | **R** | `edit_parts.rs::blocked/whole`: không cắt trong zoom/reverse/freeze, ±1 s quanh chuyển cảnh | Planner phải tránh gán zoom cho *mọi* đoạn của video dài, nếu không sẽ mất điểm chia → render chậm. Ghi trong ADR-0052. |
| Tick nguyên | **R / lệch** | Rust `media-contracts`; Python `asr/adapter.py::TimeBase/TimePoint`; `media.CANONICAL_TIME_BASE = 1/1000`; dubbing dùng ms nguyên | Docu (`visual_plan.json`) và editor (`Segment.start/end: f64`) dùng giây float. Kế hoạch mới dùng tick nguyên; chuyển sang float chỉ ở biên renderer. |
| GPU | **R** | `media/encoders.py`: NVENC chỉ khi thử mã hóa thành công, luôn có CPU fallback | Planner không cần GPU. |
| Nguồn gốc / quyền | **E** | `docu project.Source.credit`, `source_credit.json`, ADR-0044, `artifacts` provenance | Kế hoạch mang `rights` + `origin` cho mỗi nguồn. |

## 3. Bản đồ phụ thuộc (mục tiêu)

```
                 ┌────────────────────── engine/dubflow/mastering ──────────────────────┐
 media files ──► │ analysis.py (1 lượt FFmpeg: scdet + thumbnail + silencedetect, cache) │
                 │      │ dùng lại: docu/shots.py (parse, merge, slow_cuts, _difference)  │
 transcript ───► │      ▼                                                                │
 script      ──► │ policy.py (workflow × level × style → thao tác được phép + tham số)   │
                 │      ▼                                                                │
                 │ planners/<workflow>.py → plan.py (MasteringPlan v1, tick nguyên)      │
                 │      ▼                     persistence (lưu/tải/sửa/so khớp input)   │
                 └──────┼────────────────────────────────────────────────────────────────┘
                        ▼  adapter theo workflow (không có renderer mới)
   product/render.ffmpeg_argv │ compilation/render │ docu/roughcut + visual_plan │ media/adapter (Lồng tiếng)
                        ▼
                 Chỉnh sửa (edit.rs ExportRequest, edit_parts.rs) — vẫn mở được mọi kết quả như cũ
```

Quy tắc phụ thuộc: `mastering` chỉ import các module thuần (`docu.shots`, `media.encoders`); workflow import
`mastering`, không ngược lại. `mastering` không import `worker`, không ghi SQLite, không đụng supervisor
(nguyên tắc 7–8).

## 4. Rủi ro thay đổi

| # | Rủi ro | Mức | Giảm thiểu |
|---|---|---|---|
| M1 | Job cũ đổi kết quả khi chưa bật | Cao | `off` là mặc định; workflow không gọi planner khi `off`. Test so khớp lệnh FFmpeg/segment trước-sau. |
| M2 | Hai mô hình thời gian (tick vs float) lệch nhau | Trung bình | Kế hoạch chỉ lưu tick; hàm chuyển duy nhất `ticks → ms/giây` ở biên renderer, có test làm tròn. |
| M3 | Zoom phủ mọi đoạn làm `edit_parts` không chia được | Trung bình | Policy giới hạn tỉ lệ đoạn có zoom; ADR-0052 ghi điều kiện. |
| M4 | Phân tích media lặp lại (chậm) | Trung bình | Một lượt giải mã/nguồn, cache theo kích thước + mtime + recipe; tải lại kế hoạch không phân tích lại. |
| M5 | AI/phân tích độ tin cậy thấp quyết định sai | Trung bình | `confidence_milli` + `needs_review` + fallback về hành vi cũ của workflow. |
| M6 | Đụng conflict domain (contracts, editor, docu engine) với thread khác | Trung bình | Namespace mới `contracts/mastering`, `engine/dubflow/mastering`, `tests/mastering`; mỗi PR một Issue. |
| M7 | Tổng hợp mở rộng sang footage chưa lồng tiếng đổi điều kiện bảo mật đường dẫn (`item_dir`) | Trung bình | ADR riêng trước Phase 2. |
| M8 | Không có máy Windows/GPU NVIDIA để đo | Thấp-TB | Đo trên runtime đóng gói ở máy Oscar (CPU). Phần GPU ghi "chưa xác minh". |

## 5. ADR cần thiết

| ADR | Nội dung | Khi nào |
|---|---|---|
| **ADR-0052** Shared mastering plan & policy | Hợp đồng `contracts/mastering` v1 (kế hoạch + tùy chọn), tick nguyên, policy engine, cache phân tích, nguyên tắc "không renderer mới", `off` = hành vi cũ | Phase 1 (PR này) |
| ADR-0053 Compilation từ footage chưa lồng tiếng | Nới `final_vi.mp4`-only, giữ an toàn đường dẫn, provenance/quyền | Trước Phase 2 phần Tổng hợp |
| ADR-0054 Mastering trong Lồng tiếng | Cách planner ảnh hưởng `media/adapter` render plan mà không phá checkpoint/resume; pin recipe vào job | Trước Phase 3 |
| ADR-0055 Docu narrative presets | Mở rộng cấu trúc ngoài 4 hồi, giữ 4 hồi là preset tương thích | Trước Phase 4 |

## 6. Thứ tự Issue/PR

Phase 1 (mỗi dòng = một Issue + một PR nhỏ; PR sau xếp chồng lên PR trước khi cần):

1. **Audit + ADR-0052** (chỉ tài liệu).
2. **Contract kế hoạch hậu kỳ v1**: `contracts/mastering/schema-v1.json`, `engine/dubflow/mastering/plan.py` (kiểu tick nguyên, kiểm tra, ánh xạ timeline↔nguồn), lưu/tải nguyên tử, fixture, test, đăng ký CI.
3. **Policy Engine + mastering options**: `policy.py` 4 workflow × 4 mức × 8 style, bất biến (không flip, không đảo thứ tự ở Lồng tiếng…), test bảng.
4. **Scene analysis adapter**: `analysis.py` một lượt FFmpeg (cảnh, chuyển động, khoảng lặng), cache tick nguyên, test với media tổng hợp bằng FFmpeg.
5. **Kết nối thử Bán hàng**: khi spec có `mastering.level != off`, planner chọn đoạn theo cú máy (không vắt qua cut, không lặp liền), ghi `mastering_plan.json`, render bằng `product/render.ffmpeg_argv` có sẵn; mặc định không đổi.

Phase 2–5 theo mục 9 của đặc tả; mỗi phase mở Issue riêng sau khi Phase trước merge.
