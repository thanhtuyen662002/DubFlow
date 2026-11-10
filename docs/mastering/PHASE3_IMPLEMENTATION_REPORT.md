# DubFlow — Báo cáo triển khai Phase 3: Mastering trong Lồng tiếng (`dubbing_preserve`)

Epic #637 · 2026-11-11 · main khi viết: `a5b4468` (+ #696, #698 đang chờ)
Không phát hành bản stable. Lồng tiếng không cần Gemini ở bất kỳ mức nào. Mức Tắt không có lời gọi AI nào.

## Trả lời thẳng

Lồng tiếng có 4 mức mastering: Tắt, Nhẹ, Cân bằng, Mạnh. Đã chạy thật trên Windows (lần 9) với hai video tiếng Trung 100 s, không có khóa AI, 0 kết nối mạng.

| Hạng mục | Kết quả lần 9 |
|---|---|
| Tắt | Giống hệt từng byte với job không có file mastering |
| Phụ đề | Bám đúng lúc giọng Việt bắt đầu, lệch 0 ms |
| Fallback B2→B1 | Đưa phụ đề về đúng thời gian nguồn |
| Hủy rồi tiếp tục | Ra video giống hệt từng byte |

Còn hai lỗi mức trung bình:
1. Âm lượng ở một video lùi về nén động và đỉnh sau AAC vượt trần. Đã sửa ở #698 nhưng chưa chạy lại trên Windows.
2. Job lồng tiếng chạy lại không đổi vẫn trộn và render lại. Đây là hành vi có từ trước, xảy ra cả khi mastering tắt.

## 1. ADR-0055

`docs/adr/ADR-0055-dubbing-mastering.md`, trạng thái **Accepted** (#686).

Quyết định chính:
- Tùy chọn nằm trong `mastering.json` cạnh output, nên giao thức supervisor không đổi.
- Không có file nghĩa là Tắt. File hỏng thì job thất bại với `MASTERING_OPTIONS_INVALID`.
- Không đổi độ dài hoặc thứ tự ở mọi mức.
- Hook chỉ là **gợi ý** khi `enable_climax_hook = true` (mặc định false).
- Mọi thao tác đều đo trước và có giới hạn.
- Mọi lỗi trong mastering không làm hỏng job: job render như Tắt và ghi `MASTERING_SKIPPED`.

## 2. Thay đổi trong worker (`engine/dubflow/worker/production_job.py`)

- `_mastering_options`: đọc `mastering.json`.
- `_dubbing_mastering`: stage `mastering` trong checkpoint, sau B2 (hoặc sau phụ đề khi B2 tắt).
  - Đầu vào hash gồm: tùy chọn, recipe, nguồn, hash bản trộn.
  - Ghi `.dubflow-work/mastering/dubbing-plan.json` và, khi cần chỉnh âm lượng, `final_mix_mastered.wav`.
  - `final_mix.wav` để chỉnh sửa vẫn là bản chưa chuẩn hóa.
- `_voice_timed` (#692): ở mức Cân bằng/Mạnh, viết lại `captions_vi.srt/.ass` theo vị trí TTS thật. Lỗi thì giữ phụ đề theo nguồn và ghi `MASTERING_CAPTIONS_SKIPPED`.
- Hai đường fallback B2→B1 gọi `source_timed_captions()` để đưa phụ đề về thời gian nguồn trước khi render lại.
- `qc_report.json` có khối `mastering`: mức, recipe, ghi chú, có chuẩn hóa âm lượng không, bộ lọc hình, gợi ý cao trào, số đo. Sau fallback, ghi chú được sửa cho đúng (#698).
- Render identity chỉ thêm `mastering` khi mastering bật, nên mức Tắt giữ nguyên identity.

## 3. Thay đổi trong render (`engine/dubflow/media/adapter.py`)

- Thêm tham số tùy chọn `render(..., mastering_filters=())`.
- Chỉ nhận bộ lọc qua danh sách cho phép (regex `_MASTERING_FILTER`): `crop`, `scale`, `setsar`, `colorlevels`.
- Bộ lọc được chèn sau lớp che phụ đề nguồn và trước phụ đề cháy, nên phụ đề không bị chỉnh màu.
- Không truyền khi rỗng, nên lệnh FFmpeg của mức Tắt giống hệt trước.

## 4. Thành phần dùng lại

- Checkpoint/resume và render identity của job Lồng tiếng.
- `_write_subtitles` (SRT/ASS) và kiểu phụ đề.
- `TtsArtifact.slot_start` / `actual_end` và `B2AudioResult.member_cue_ids`.
- FFmpeg LGPL: `loudnorm` (đo và áp, 2 lượt), `colorlevels`, `crop`/`scale`.
- Chính sách và phiên bản kế hoạch dùng chung (`mastering/policy.py`, `mastering/plan.py`; ADR-0052).
- Không thêm model, không dùng GPU, không gọi AI.

## 5. Các mức

| Mức | Âm lượng | Màu | Khung hình | Phụ đề | Gợi ý cao trào |
|---|---|---|---|---|---|
| Tắt | — | — | — | như cũ | — |
| Nhẹ | `loudnorm` tuyến tính tới −16 LUFS, đỉnh ≤ −2,5 dBTP trước AAC (#698); bỏ qua nếu đã trong 1 LU | `colorlevels` khi dải sáng bị nén, mỗi đầu tối đa 0,06 | — | như cũ | — |
| Cân bằng | như Nhẹ | như Nhẹ | chỉ cắt khi có viền 4 phía, phóng đều ≤ `max_zoom` | theo giọng TTS, ≤ 2 dòng × 42 ký tự, ≥ 1 s, không chồng | — |
| Mạnh | như Nhẹ | mỗi đầu tối đa 0,10 | như Cân bằng (max_zoom 1,12) | như Cân bằng | chỉ khi `enable_climax_hook`, chỉ là gợi ý |

Không có: zoom chu kỳ 105→115→108 %, mix cố định 100/10/8 %, đổi cao độ, tiếng nền giả, xóa âm thanh nguồn, cắt 200 ms, lật hình, xoay, kéo giãn.

## 6. Thay đổi timeline được phép

Không có. Thời lượng output bằng nguồn ở mọi mức. Lần 9: 100,033 s / 3001 khung, bằng mức Tắt. Thứ tự không đổi.

Gợi ý cao trào chỉ được ghi lại (`needs_review: true`, `applied: false`). Video Mạnh có hook giống hệt từng byte video Cân bằng.

Một teaser thật sẽ cần timeline đích riêng và remap toàn bộ. Việc này thuộc Phase 5.

## 7. Giữ nguyên lời thoại

- Không `atempo`, không `asetrate`, không đổi cao độ.
- TTS và bản trộn B2 giữ nguyên.
- Mastering chỉ nhân một hệ số tuyến tính lên toàn bản trộn. Từ #698, mục tiêu được hạ khi cần để `loudnorm` không chuyển sang nén động.
- Đã kiểm bằng FFmpeg thật: hai bản trộn thử đều báo "Normalization Type: Linear".

## 8. Kết quả đồng bộ (Windows lần 9, Test C)

| | X phim ngắn (23 cụm) | Y truyện có nhạc (13 cụm) |
|---|---|---|
| Đầu phụ đề so với TTS `slot_start` | lệch tối đa 0 ms, trung bình 0 | lệch tối đa 0 ms, trung bình 0 |
| Cuối phụ đề so với `actual_end` | tối đa 628 ms, trung bình 123 ms | tối đa 367 ms, trung bình 33 ms |

- Độ lệch ở cuối phụ đề hoàn toàn do quy tắc tối thiểu 1 s.
- ≤ 2 dòng × 42 ký tự; 0 phụ đề dưới 1 s khi còn chỗ; 0 chồng nhau; phụ đề cuối không quá hết video.
- Phụ đề cháy khớp SRT ở 6 khung lấy mẫu.
- Fallback B2→B1 (chèn lỗi QC): 47/47 phụ đề về đúng thời gian nguồn.

## 9. QC âm thanh

| Video | Trước | Sau | Ghi chú |
|---|---|---|---|
| Y | −10,9 LUFS | −16,0 LUFS, đỉnh −5,6 dBTP | đạt |
| X | −15,2 LUFS | −16,0 LUFS | `loudnorm` lùi về nén động (LRA 7,6 → 6,5); đỉnh MP4 sau AAC −0,7 dBTP, vượt trần −1,5 |

- Lỗi của X đã sửa ở #698. Kế hoạch giữ `đỉnh + tăng ≤ −2,5 dBTP` và hạ mục tiêu khi cần.
- Kiểm bằng FFmpeg n9.0.2 thật: −12,0 LUFS / +0,3 dBTP thành −16,3 LUFS / −3,8 dBTP sau AAC, Linear.
- **Chưa chạy lại trên Windows.**

## 10. QC hình

- Màu: X và Y được kéo dải sáng bằng `colorlevels` (trắng đo được 227 và 223), không đổi độ bão hòa.
- Crop: X và Y không có viền nên không cắt. Từ #698, lý do không cắt được ghi lại. Clip thử có viền 4 phía được cắt và phóng 1,08× đều hai chiều, không méo.

## 11. Resume / invalidation (Test D)

| Thao tác | Kết quả |
|---|---|
| Hủy ở cụm TTS 10/23 rồi tiếp tục | dùng lại ASR, bản dịch và 10 cụm; video giống hệt từng byte job chạy liền mạch — **ĐẠT** |
| Đổi mức | lập lại kế hoạch và render lại — **ĐẠT** |
| Đổi giọng | tổng hợp lại 22 cụm (49 s) — **ĐẠT** |
| Sửa 1 câu dịch | chỉ 1 artifact đổi, phụ đề cập nhật — **ĐẠT** |
| Đổi kiểu phụ đề | chỉ ASS đổi, render lại — **ĐẠT** |
| Chạy lại không đổi | **KHÔNG ĐẠT:** trộn và render lại (15,8–22,9 s) dù output giống hệt từng byte |

Lỗi chạy lại không đổi có từ trước Phase 3: worker luôn render lại sau một lần chạy B2 thành công. Việc này nằm trong backlog.

## 12. E2E Windows (lần 9, `d41b944`)

Điều kiện chạy:
- Máy của Oscar, runtime 1.1.95, FFmpeg LGPL của bản cài, bản sao model trong sandbox, chạy từng job một.
- Worker được gọi trực tiếp qua giao thức lệnh, **không có khóa AI**.
- 33 lần chạy: 0 sự kiện mạng (audit hook), 0 kết nối (psutil trên cả cây tiến trình).

| Test | Kết quả |
|---|---|
| A — đường cũ | **ĐẠT.** Không file và mức Tắt giống hệt từng byte; argv chỉ khác tên file tạm |
| B — Nhẹ | **ĐẠT MỘT PHẦN.** Lỗi âm lượng của X (mục 9) |
| C — Cân bằng/Mạnh, 2 thể loại | **ĐẠT** |
| D — resume/invalidation | **ĐẠT**, trừ chạy lại không đổi |
| E — đường dẫn Unicode | **ĐẠT** |
| E — đo hỏng | **ĐẠT.** `MASTERING_SKIPPED`, job vẫn hoàn tất |
| E — ổ thấp | chỉ giả lập: `STORAGE_INSUFFICIENT`, dừng sạch sau 1,5 s. Ổ đầy thật **KHÔNG CHẠY** (cần quyền admin) |

Unit test trên Windows: mastering 154, product 40, compilation 9, production/worker 146 (1 bỏ qua). Tất cả OK.

Quét khóa AI: 3594 file, 0 lần xuất hiện.

## 13. Hiệu năng CPU

| Trường hợp | Thời gian | RAM đỉnh |
|---|---|---|
| Lạnh, X | 82 s | 1,76 GB |
| Lạnh, Y | 38 s | 1,40 GB |
| Có cache, mức Tắt | ~14 s | ~1,04 GB |
| Có cache, Nhẹ / Cân bằng / Mạnh | ~21 s | ~1,04 GB |

Mastering thêm khoảng 7 s cho video 100 s: một lượt đo `loudnorm`, một lượt áp, 16 khung mẫu.

## 14. PR

| PR | Issue | Commit | Nội dung |
|---|---|---|---|
| #686 | #685 | 0acc213 | ADR-0055, `enable_climax_hook`, chính sách `dubbing_preserve` (bỏ cắt khoảng lặng, hook chỉ khi bật) |
| #688 | #687 | bb08163 | Stage mastering, đo âm lượng và hình, bộ lọc màu và khung, `render(mastering_filters=)` |
| #692 | #691 | a5b4468 | Phụ đề theo giọng Việt; sửa 4 điểm review (fallback B1, giới hạn thời lượng, không chồng khi trùng đầu, cắt từ quá dài) |
| #696 | #693 | (chờ) | Gợi ý cao trào ở mức Mạnh, không bao giờ render |
| #698 | #697 | (chờ) | Âm lượng tuyến tính có chừa chỗ cho AAC; ghi chú QC sau fallback; lý do không cắt khung |

## 15. Giới hạn và rủi ro

- #698 chưa được kiểm lại trên Windows.
- Hạ mục tiêu để giữ tuyến tính có nghĩa là bản trộn có đỉnh cao sẽ nhỏ hơn −16 LUFS. Ghi chú nói rõ mức thật.
- Chạy lại không đổi vẫn render lại (hành vi cũ, tốn 16–23 s).
- Gợi ý cao trào chỉ dựa trên năng lượng âm thanh 5 s, không hiểu nội dung.
- Ổ đầy thật chưa được kiểm.
- Danh sách giọng nghe thử trên desktop có giọng không có trong catalog 1.1.95 (ví dụ `vi-minh-khang`). Worker khi đó lùi về B1 với `VOICE_ID_UNKNOWN`. Đây là lỗi ngoài phạm vi mastering, đã ghi vào backlog.
- Desktop chưa có giao diện chọn mức. `mastering.json` hiện do test, harness E2E hoặc người dùng nâng cao viết.

## 16. Phase 5

- Giao diện chọn mức và xem `qc_report.json` → `mastering`: ghi chú, âm lượng trước/sau, bộ lọc.
- Duyệt gợi ý cao trào: nghe thử, chấp nhận hoặc bỏ. Teaser thật cần timeline đích riêng và remap.
- Bỏ render lại khi B2 dùng lại toàn bộ và identity không đổi.
- Đồng bộ danh sách giọng của desktop với catalog.
- Chi tiết: `docs/mastering/PHASE5_BACKLOG.md`.
