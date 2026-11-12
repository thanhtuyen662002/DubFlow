# Assisted Production — Báo cáo đóng các lỗi an toàn (Workstream A)

Epic #637 · 2026-11-12 · chỉ thị "DUBFLOW — ASSISTED EDITORIAL PRODUCTION" · mốc ban đầu rc7 `7acd262` · bản kiểm Windows: 1.1.96-rc8 `d398f9d` (candidate, `publish=false`).

## 1. Tóm tắt

| Mục | Vấn đề | Kết quả | Bằng chứng |
|---|---|---|---|
| A1 (P0) #764 | P1-N1: câu mở đầu của Bán hàng được READY trên cảnh không có sản phẩm | **Đã sửa**, PR #769 | §3 |
| A2 (P1) #763/#765 | rc7 kém hơn rc6 trên ba bộ hồi quy thật | **Đã phân loại**, không rollback, không đổi code | §4 |
| A3 (P1) #766 | `status.json` bị khoá trên Windows, mất trạng thái, mất stderr | **Đã sửa**, PR #770 | §5 |
| Windows E2E Kịch bản A | Kiểm lại A1 và A3 trên rc8 và rc9 | **PASS** | [rc8](assisted-e2e/rc8/report-rc8.md) §2, [rc9](assisted-e2e/rc9/report-rc9.md) §2 |

## 2. Phạm vi và những gì không làm

- Không dựng lại engine, không thêm model mới, không làm Phase 4.
- Không hạ ngưỡng nào, không sửa nhãn sau khi xem kết quả.
- Không chỉnh trọng số cho riêng một video.
- Gate S (Semantic Auto) **vẫn FAIL** và được ghi riêng. Chế độ Assisted không thay đổi kết luận đó.

## 3. A1: câu mở đầu nói về sản phẩm (#764, PR #769)

**Nguyên nhân.** Mọi câu đầu của Bán hàng đều là "hook". Quy tắc `generic` trong `align.assign` cho câu hook là NOT_NEEDED rồi READY, kể cả khi cảnh được chọn chắc chắn không có sản phẩm.

**Sửa.** Một câu hook của sản phẩm bị gắn cờ `needs_review` khi thoả đủ các điều kiện sau:

- đang ở chế độ nội dung;
- không có ghim của người dùng;
- không cảnh nào được chọn có `product_visible = true`, và ít nhất một cảnh có `false`.

Câu đó được kèm ghi chú "câu mở đầu nói về sản phẩm nhưng AI không thấy sản phẩm trong cảnh được chọn: cần kiểm tra". Hàng đợi (#772) đưa câu này vào nhóm **Nghiêm trọng**. Kiểm tra cuối (#774) chặn xuất cho tới khi câu được xử lý, và chấp nhận "các câu chưa duyệt" cũng không gỡ được chặn này.

**Không đổi:**

- ghim hợp lệ;
- câu có bằng chứng sản phẩm;
- Tổng hợp, lồng tiếng và Mastering Off;
- tên tệp vẫn không bao giờ là bằng chứng hình.

Recipe được đổi thành `product-editorial-v16` để không dùng lại bộ nhớ đệm cũ.

**Test.** `tests/mastering/test_opening_claim.py` gồm 5 test. Các số khoá của `test_gate_s_result` không đổi.

## 4. A2: #763 so với #765 trên cùng bằng chứng

Phát lại 6 bộ rc7fresh bằng các lời gọi planner đã ghi, với ba phiên bản rc6 `85b9d81`, rc7 `7acd262` và #769. Chấm bằng quality CLI hiện tại, dùng truth của results-main-f75bd2b.

| Bộ | rc6 | rc7 / #769 |
|---|---|---|
| P1 | 82,7 % | như nhau |
| P2 | 77,6 %, ok 3/6, contra 1917, stolen 1211 | 65,0 %, ok 2/6, contra 935, stolen 2774 |
| P3 | 43,9 % | như nhau |
| B1 | 94,9 %, stolen 1745 | 94,9 %, stolen 3330 |
| B5 | 95,7 %, ok 12/13, contra 0 | 92,6 %, ok 11/13, contra 1785 |
| D1 | 43,0 % | như nhau |

Không có bỏ sót im lặng ở cả ba phiên bản: mọi câu bị ảnh hưởng đều được gắn cờ.

**Phân loại từng thay đổi:**

- **P2 u3: TỆ HƠN, sai cảnh, có gắn cờ.** Ở rc6, cảnh đúng là nhờ may.
- **P2 u5: LẪN LỘN, có gắn cờ.** contra giảm từ 706 xuống 0, nhưng câu này lấy mất cảnh chính của một câu khác.
- **P2 u6: CÙNG CẢNH, độ phủ thấp hơn, có gắn cờ.**
- **B5 outro.u1: TỆ HƠN, sai cảnh, có gắn cờ.** Thẻ trích dẫn được gán nhãn establishing kèm chữ, nên không còn bị coi là thẻ chữ.
- **B1 mo-phong-3d.u1: ĐÚNG CẢNH nhưng lấy mất cảnh chính của câu sau, có gắn cờ.** Nguyên nhân là khớp từ giả.

**Đã thử và loại bỏ.** Quy tắc "chữ trên màn hình chỉ kèm establishing thì là thẻ chữ" sẽ chặn nhầm 156 khung hình thật có phụ đề, tức là đưa lại đúng lỗi #763 đã sửa. Đây là giới hạn của cách khớp từ ngữ (R2) và không sửa được bằng heuristic.

**Quyết định.**

- Giữ #763, không đổi code.
- Ở chế độ Assisted, các câu này đều nằm trong hàng đợi duyệt.
- Chi tiết: `/mnt/project-files/assisted/a2-765-notes.md`.
- #765 còn mở trên GitHub: việc đóng issue cần chủ dự án.

## 5. A3: `status.json` (#766, PR #770)

**Engine** (`product/job.py`):

- `_atomic_text` ghi ra tệp tạm có fsync, rồi `os.replace`.
- Khi gặp PermissionError hoặc WinError 5/32/33, nó thử lại có giới hạn: 6 lần, rồi 12 lần cho trạng thái kết thúc, với backoff nhân đôi tối đa 1 giây.
- Nếu thất bại, nó xoá tệp tạm. Không bao giờ để lại JSON hỏng.
- `Status.write` không ném lỗi, ghi stderr và đếm `status_write_failures`.

**Host** (`engine_run.rs`):

- stdout và stderr của engine được giữ trong `.dubflow-work`.
- `settle_after_exit` quyết định trạng thái cuối:
  - COMPLETED chỉ khi dòng cuối của engine là COMPLETED **và** tiến trình thoát thành công;
  - nếu không, có dấu huỷ thì là CANCELLED;
  - còn lại là FAILED, kèm tên tệp stderr.
- Bán hàng có luồng theo dõi tiến trình.

**Test:**

- `tests/product/test_status_write.py` gồm 10 test, chạy trên Windows CI; CI thất bại nếu có test bị bỏ qua.
- Các test Rust có trường hợp share-mode trên Windows.

## 6. Bất biến được giữ

Các bất biến sau không đổi:

- giữ nguyên lời lồng tiếng;
- spec Tổng hợp v1 và raw v2;
- đường Bán hàng cũ;
- Mastering Off;
- nguồn gốc và quyền sử dụng;
- timebase;
- checkpoint/resume;
- bảo mật Tauri;
- giám sát worker;
- bộ render.

Bộ test mastering có 241 test, đều đạt. tests/product và tests/compilation đạt. Desktop: tsc, npm test và build đạt. `cargo check --tests` đạt cho đích Windows. CI Windows "desktop-editor-windows" xanh trên #769, #770, #772 và #774.

## 7. Windows E2E, Kịch bản A (rc8)

Chạy trên DubFlow.exe rc8 `d398f9d` và rc9 `07e4fb5` (candidate), qua UI thật bằng CDP và hộp thoại Win32.

| # | Kiểm tra | Kết quả |
|---|---|---|
| A1 | Bán hàng "Nồi chiên không dầu…" trên clip domino (lặp lại D3 của rc7) | Câu 1 là NEEDS_REVIEW, có ghi chú "câu mở đầu nói về sản phẩm nhưng AI không thấy sản phẩm…", nằm nhóm **Nghiêm trọng**. Lỗi READY sai của rc7 không còn |
| A1 | Mở editor khi chưa xử lý | Kiểm tra cuối chặn: "Mục nghiêm trọng: … câu 1, câu 2, câu 3" |
| A1 | Sau khi xử lý (thay cảnh, đánh dấu thiếu cảnh) và dựng lại | editor mở được; dòng kết quả ghi đây là quyết định của người dùng (rc9) |
| A3 | Huỷ giữa chừng | `CANCELLED`, JSON hợp lệ, có `engine-stdout.log` và `engine-stderr.log` |
| A3 | Kill engine | `FAILED`, `status_source: host`, không bao giờ COMPLETED |
| A3 | Khoá `status.json` bằng FileShare.None, 2 × 2,5 s, khi đang RUNNING | run vẫn COMPLETED 3/3, stderr trống |

Trên rc9, bản nháp của 28 câu Bán hàng không có câu nào READY sai.

## 8. Lỗi còn mở

- #765: phân tích xong (§4). Issue còn mở trên GitHub vì việc đóng nó cần chủ dự án.
- P2-9: lệch A/V ở bước ghép trên phim dài, xem Kịch bản F.
- Lỗi mới từ rc8 và rc9: P1-N3, P2-N6, P2-N7, P2-N14, P3-N8, P3-N9 đã được sửa (#776, #778). P2-N5 và P2-N11 còn mở (xem ASSISTED_EDITORIAL_PRODUCTION_REPORT.md §13).

## 9. Kết luận Gate A (Safety)

**Gate A Safety: PASS.**
- Không câu nào READY sai.
- Câu mở đầu không có bằng chứng sản phẩm luôn phải duyệt.
- Trạng thái run không bao giờ là COMPLETED khi lỗi.
- CANCELLED không bị mất.
- JSON không hỏng.
- stderr được giữ.
- Kiểm tra cuối chặn mọi câu nghiêm trọng còn treo.

## 10. Những điều không được hiểu sai

- Chế độ Assisted **không** phải Auto. USER_APPROVED không có nghĩa là AI chắc chắn, và cũng không có nghĩa là đúng ngữ nghĩa.
- Bản dựng thành công không có nghĩa là đạt kiểm tra cuối.
- Gate S vẫn FAIL. Documentary Auto vẫn NO-GO.
