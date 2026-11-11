# DubFlow — Báo cáo tích hợp Tổng hợp từ video gốc vào desktop (Gate C)

Epic #637 · chỉ thị "PHASE 4 READINESS GATE CLOSURE" ngày 2026-11-11 · không phát hành bản stable.

Bằng chứng Windows gốc (tiếng Anh, có bảng từng bước): `docs/mastering/gate-c/report-rc4.md`, `report-rc5.md`, `report-rc6.md` và các file `rc*-results.json` cạnh chúng. Ảnh chụp, log và video nằm trên máy kiểm thử trong `_sandbox\phase4-gate\rc4|rc5|rc6\` (không đưa vào repo).

## 1. UI đã triển khai

- Trang **Ghép video** có hai chế độ rõ ràng: "Video đã lồng tiếng" (đường cũ, spec v1) và **"Video gốc (nhiều nguồn)"** (spec v2). PR #729, sửa ở #743.
- Chế độ video gốc gồm: nguồn theo từng mục, thiết lập chương trình, chọn mức Tối ưu hậu kỳ, tùy chọn AI, "Dựng bản nháp", thẻ Kết quả, bảng Duyệt cảnh theo từng phần, "Sửa chương trình…", "Dựng lại các phần đã đổi", "Xuất bản cuối…".
- Các file: `apps/desktop/src/features/compilation/raw_view.ts`, `raw_model.ts`, `view.ts`; bảng duyệt dùng lại `features/mastering/review_panel.ts` của Phase 5.

## 2. IPC mới hoặc dùng lại

Host Rust `apps/desktop/src-tauri/src/compilation_raw.rs`:

| Lệnh | Việc |
|---|---|
| `compilation_raw_render` | Kiểm request, dựng spec v2, tạo thư mục chạy mới, chạy engine |
| `compilation_raw_rerender` | Dựng lại trong cùng thư mục chạy; có request thì thay spec (giữ `spec-previous.json`) |
| `compilation_raw_cancel` | Hủy job đang chạy |
| `compilation_raw_open` | Đọc một lần chạy (từ chối spec không phải v2 với lý do đọc được) |
| `compilation_review` | Xem / ghim / bỏ / duyệt / hoàn tác / chấp nhận của từng phần (gọi `engine.dubflow.mastering.review --part`) |
| `compilation_keyframe` | Trả ảnh keyframe của cảnh trong thư mục chạy |
| `compilation_raw_finalize` | Xuất bản cuối, có hoặc không có chấp nhận rủi ro |

Mọi đường dẫn thư mục chạy đi qua `raw_dir()`: đường dẫn tuyệt đối, không `..`, đúng dạng `DubFlow Output\Tong hop - …\run-<12 hex>`, từng cấp là thư mục thật (không reparse point), rồi so containment theo thành phần sau `canonicalize()`. Không có kiểm tra `startsWith()` trên chuỗi.

## 3. Spec v1/v2

- v1 (`schema_version: 1`): giữ nguyên. C3 trên rc4/rc5: 24/24 lệnh FFmpeg/FFprobe giống rc3, video giống từng byte (`9afa62bc…`), spec không có `mastering`.
- v2 (`schema_version: 2`): spec dựng ở host từ request đã kiểm; engine `engine/dubflow/compilation/raw.py`.
- Phiên bản lạ (thử với định dạng 9): "Dự án này được tạo bằng phiên bản DubFlow mới hơn (định dạng 9); hãy cập nhật DubFlow để mở." File không bị ghi đè (SHA-256 trước = sau).

## 4. Nhập nguồn

- "Thêm video…" (hộp thoại Windows, nhiều file) và "Chọn từ Hàng đợi".
- Định dạng không hỗ trợ bị từ chối khi thêm ("Chưa hỗ trợ định dạng: x-unsupported.ogv.").
- Xóa nguồn trước khi chạy: có.
- Nguồn là symlink: từ chối ("Video nguồn phải là tệp thật trên máy, không phải lối tắt").
- Mỗi nguồn có id `<mục>-s<k>`; quyền sử dụng và ghi công đi theo nguồn.
- Thiếu (P2-1): danh sách nguồn chưa hiện thời lượng và định dạng.

## 5. Chọn mức Tối ưu hậu kỳ

Bốn mức Tắt / Nhẹ / Cân bằng / Mạnh. Mô tả chỉ nói điều engine làm (`features/mastering/model.ts`, workflow `compilation`):

- **Tắt:** "Video gốc luôn cần chọn cảnh theo lời bình, nên Tắt được dựng như Nhẹ."
- **Nhẹ:** chọn cảnh theo từng câu, cắt trong ranh giới cảnh, giữ thứ tự nguồn và thứ hạng của bạn, chuẩn hóa âm lượng, nhịp chậm.
- **Cân bằng:** như Nhẹ, nhịp vừa, mờ dần đầu và cuối phần; câu mở màn có thể dùng một cảnh nổi bật tới 6 s lấy từ phần sau, luôn cần duyệt.
- **Mạnh:** như Cân bằng, nhịp nhanh hơn, cảnh mở màn tới 8 s, không đổi thứ hạng.

## 6. Thiết lập chương trình

Chủ đề, loại (Top N, …), số mục, tiêu đề và thứ hạng từng mục, nút "Đánh số đếm ngược", lời bình (hoặc "AI viết lời bình"), câu mở màn / giới thiệu / kết, ngôn ngữ, giọng đọc, thời lượng mục tiêu (chỉ báo, "không tự cắt bớt"), mức tối ưu, AI xem khung hình, AI gợi ý thứ hạng.

**Thứ hạng của người dùng không bao giờ bị AI đổi.** Gợi ý của AI chỉ hiện ở mục "AI gợi ý thứ hạng" kèm nút "Dùng thứ tự gợi ý…". Video rc4/rc5/rc6 đi đúng thứ tự người dùng #5→#1.

## 7. Bảng Duyệt cảnh

Dùng lại bảng của Phase 5, thêm cho Tổng hợp: phần chương trình, thứ hạng, tiêu đề mục, lời bình, khoảnh khắc âm thanh gốc. Mỗi câu có trạng thái (READY, NEEDS_REVIEW, MATCH_NOT_FOUND, SOURCE_INSUFFICIENT, USER_APPROVED, INVALIDATED), coverage, lý do, cảnh đang dùng, tối đa 3 cảnh thay thế, xem trước ở đúng câu kèm lời đọc và phụ đề.

## 8. Ghim / bỏ

- "Dùng cảnh này" (ghim), "Bỏ cảnh này" (loại), "Duyệt cảnh này", hoàn tác.
- Lưu ở `.dubflow-work/review-<phần>.json` có lịch sử phiên bản.
- Ghim được tôn trọng sau khi dựng lại (segment "chọn bởi người dùng"); loại được tôn trọng.
- Khi nguồn đổi: ghim/loại chuyển INVALIDATED ("Cảnh đã chọn không còn trong nguồn (nguồn đã đổi); hãy chọn lại."). Không tự gán cảnh khác như đã duyệt.

## 9. Lưu bền

- Thư mục chạy giữ `spec.json`, `program.json`, `status.json`, quyết định review, kế hoạch, phần đã dựng và bản cuối.
- Danh sách lần chạy được nhớ (localStorage `dubflow.compilation.runs.v1`, #743), nên lần chạy cạnh video gốc vẫn hiện sau khi mở lại app (rc4 P1-2, đã sửa).
- Đóng rồi mở lại (rc5 C1 bước 18–20): lần chạy, ghim, INVALIDATED, ô chấp nhận và "Bản cuối" còn nguyên.

## 10. Dựng lại một phần

rc5 C2: sửa lời một mục và đổi điểm bắt đầu nguồn của một mục khác, rồi "Lưu và dựng lại".

- **40,4 s**, "dựng 3 phần, dùng lại 5". `rendered [intro, i1, i2]`, `reused [hook, i3, i4, i5, outro]`. Chỉ hash của `part-intro`, `part-i1`, `part-i2`, `i2.wav`, `compilation.mp4` đổi. TTS chỉ chạy cho i2. Vision hỏi 2 khung. 3 lần encode + nối.
- Ghim còn hợp lệ (i5) được giữ; ghim của nguồn đã đổi INVALIDATED.
- Lời mới có trong giọng đọc và phụ đề: lệch +90 ms ở đầu câu.
- `accept_unreviewed` được xóa trên phần dựng lại, nên "Xuất bản cuối lại" hỏi xác nhận lại (rc4 P1-4, đã sửa ở #743).

## 11. Kết quả Scenario C1–C4

| Scenario | rc4 | rc5 | rc6 | Ghi chú |
|---|---|---|---|---|
| C1 Top 5 từ video gốc | Chạy hết, cần lách P1-1, P1-2 | **ĐẠT**: 21 bước qua UI, không lách | Smoke **ĐẠT** | Video 5 mục đúng thứ tự, lời, phụ đề, 4 khoảnh khắc âm thanh gốc; −18,5 LUFS, TP −1,4 dBFS |
| C2 Dựng lại một phần | P1-3, P1-4 | **ĐẠT** | không chạy (rc6 không đổi desktop) | Mục 10 |
| C3 Dự án cũ v1 | **ĐẠT** | **ĐẠT** | không chạy | Mục 3 |
| C4 Lỗi và bảo mật | ĐẠT có ngoại lệ, P2-7, P2-8 | **ĐẠT có ngoại lệ** | quét khóa | Mục 17 |

Hai ngoại lệ C4: (1) **file không đọc được** không đưa vào được qua UI vì chính hộp thoại Windows từ chối file đó, nên ca này không tính; (2) các P2 về thông báo (mục 19).

## 12. Bản candidate

| Bản | Run | Commit | `DubFlow.exe` SHA-256 |
|---|---|---|---|
| 1.1.96-rc4 | 42974952007 | `7c4fb1f` | `9bd55b1f…846a` |
| 1.1.96-rc5 | 42994644537 | `35bb115` | `45ff21af…aef8cc` |
| 1.1.96-rc6 | 43019112717 | `85b9d81` | `83aa539a…e64f46` |

Kênh candidate, `publish=false`, `production_qualified: false`, chưa ký. FFmpeg chỉ `--enable-version3` (LGPL), 0 file `.pyc`.

## 13. Môi trường Windows

Windows 11 Home 10.0.26300, i7-12700H, 39,7 GB RAM, chỉ CPU. Mỗi bản cài vào thư mục riêng trong `_sandbox\phase4-gate\rcN\` với thư mục dữ liệu riêng; không đụng bản stable và dữ liệu của nó.

## 14. Bằng chứng UI

- Lái giao diện thật: chạy `DubFlow.exe` của bản rc với cổng gỡ lỗi WebView2, bấm chuột thật (`Input.dispatchMouseEvent`) và gõ (`Input.insertText`) qua CDP; hộp thoại mở file/thư mục của Windows điền bằng thông điệp Win32.
- Chứng minh đúng tiến trình: trình duyệt CDP là con của `…\rcN\app\versions\1.1.96-rcN\app\bin\DubFlow.exe`, trang `http://tauri.localhost/`, lặp lại sau khi mở lại app.
- Không gọi IPC Tauri từ trang, không sửa file app, không mock. Ngoại lệ ghi rõ: `<select>` được đặt giá trị sau khi bấm thật; vài nút được gắn thuộc tính `data-e2e` tạm để nhắm.
- Phân loại bằng chứng: unit/integration (CI), runtime đóng gói chạy không giao diện (E2E ngữ nghĩa, so argv C3, lỗi AI ở C4), desktop UI (C1, C2, phần lớn C3/C4). Xem mục "Evidence separation" của từng báo cáo.

## 15. Video đầu ra

rc5 C1: H.264 1920×1080 + AAC, 76,43 s, −18,5 LUFS, LRA 5,4, true peak −1,4 dBFS. Thứ tự hook, intro, #5…#1, outro. Khung đầu mỗi mục hiện đúng tiêu đề. `captions.srt` 28 dòng. Video 76,433 s / âm thanh 76,445 s. rc6: bản cuối 75,893 / 75,905 s.

## 16. Hiệu năng (chỉ ghi nhận)

| Chỉ số | rc4 | rc5 | rc6 |
|---|---|---|---|
| Nhập/probe | tức thì (không probe khi thêm) | như rc4 | như rc4 |
| Bản nháp C1 (lạnh, Claude vision) | 327 s; 80 khung / 75 hỏi | 291 s; 70 / 67 | 330 s; 80 / 77 |
| Dựng lại sau quyết định | 2/8 phần | 1/8 phần, khoảng 10 s, vision 0 | – |
| C2 dựng lại sau sửa | 33,5 s | 40,4 s, 3 encode | – |
| RAM đỉnh (C2, cả cây tiến trình) | 2,85 GB | 2,89 GB (python 1,36, ffmpeg 0,90, WebView2 0,58) | – |
| Hộp thoại xuất bản cuối | vài giây | 12,9 s lần đầu, rồi 2,8 s | 3,5 s |
| Cache | thư mục làm việc 80 MB; TTS 7,3 MB | 88 MB; 7,5 MB | – |

RAM đỉnh của bản nháp C1 không đo được (công cụ đo thiếu `psutil`).

## 17. Kiểm thử bảo mật

| Ca | Kết quả |
|---|---|
| Nguồn bị xóa sau khi thêm | Lỗi rõ, không tạo thư mục chạy |
| File hỏng | FAILED "… không có hình để phân tích" |
| Định dạng không hỗ trợ | Từ chối khi thêm |
| Thiếu lời bình | "Mục 1: chưa có lời bình …", không ghi gì |
| Nguồn symlink ra ngoài | Từ chối |
| Thư mục lưu là junction | Từ chối, `outside\out` trống (thông báo mơ hồ, P2-10) |
| Thư mục lưu không ghi được | "Không tạo được thư mục tổng hợp" |
| AI không có khóa | Ô AI bị tắt, có giải thích; video vẫn dựng được |
| AI lỗi (khóa giả chỉ trong env tiến trình con) | Hoàn tất với cảnh báo, giữ thứ tự người dùng; khóa giả xuất hiện trong 0 file |
| Hủy job / mở lại | rc5: CANCELLED, dựng tiếp được ngay lần đầu |
| Quét khóa AI | rc4 11 810 file, rc5 2 727 + 317, rc6 5 447 file: **0 lần trùng** |
| Video nguồn | SHA-256 trước = sau (rc5 10/10 + 6/6, rc6 10/10) |
| Ghi ngoài vùng cho phép | Không |

## 18. Tương thích

Đường v1 không đổi (24/24 argv, video giống từng byte). Mức tối ưu không bắt buộc cho đường cũ. Dự án v1 mở được ở chế độ chỉ đọc, kèm "Dùng lại thiết lập này cho bản mới".

## 19. Những phần còn thiếu

| ID | Mức | Nội dung |
|---|---|---|
| P2-1 | P2 | Danh sách nguồn chưa hiện thời lượng, định dạng |
| P2-2 | P2 | Đường dẫn ghép sai từ hộp thoại được nhận mà không cảnh báo |
| P2-3 | P2 | Chấp nhận trong bảng không làm mới thẻ Kết quả |
| P2-4 | P2 | Cảnh bị bỏ vẫn hiện "Đang dùng" tới lần dựng lại |
| P2-5 | P2 | Duyệt sau khi ghim luôn bị INVALIDATED ở lần dựng áp ghim |
| P2-6 | P2 | Sau dựng lại, "Bản cuối" vẫn chỉ bản cũ như bản hiện hành |
| P2-7 | P2 | Sau khi hủy, bảng vẫn nói "Sẵn sàng xuất bản cuối." (nút xuất bị tắt) |
| P2-9 | P2 | Một phần ngắn hơn âm thanh 63 ms (rc5); ghép phần lệch −41 ms (rc6, D1) |
| P2-10 | P2 | Từ chối thư mục junction không nói lý do |
| P2-11 | P2 | Hộp thoại xuất bản cuối lần đầu chậm (12,9 s ở rc5, 3,5 s ở rc6) |
| P2-14 | P2 | Khi mọi lời gọi AI lỗi, phần vẫn ghi `mode: content` chỉ từ tên file |
| – | giới hạn | File không đọc được không thử được qua UI (hộp thoại Windows từ chối) |
| – | ngữ nghĩa | Chất lượng chọn cảnh trên video thật: xem SEMANTIC_GATE_CLOSURE_REPORT (Gate S) |

Không còn P0 hay P1 nào của desktop: rc4 P1-1…P1-4 đã sửa (#743) và kiểm lại qua UI trên rc5.

## 20. Quyết định Gate C: **ĐẠT**

| Tiêu chí | Kết quả |
|---|---|
| C1 Workflow v2 qua desktop | Đạt |
| C2 Duyệt cảnh (cảnh AI chọn, coverage, trạng thái, thay thế, ghim/bỏ, xác nhận, dựng lại) | Đạt |
| C3 Lưu bền | Đạt |
| C4 Tương thích v1 | Đạt |
| C5 Bảo mật | Đạt |
| C6 E2E Windows thật C1–C4 | Đạt (C4 có ngoại lệ đã ghi: file không đọc được không thử được qua UI) |

Các báo cáo chạy trên Windows ghi "Gate C FAIL" ở rc4 và rc5 vì gộp cả lỗi ngữ nghĩa. Ở đây các lỗi ngữ nghĩa được tính vào Gate S. Phần tích hợp desktop đạt mọi tiêu chí C1–C6.
