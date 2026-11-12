# Phim tài liệu — Sẵn sàng cho chế độ Assisted (chỉ thiết kế)

Epic #637 · 2026-11-12 · chỉ thị "DUBFLOW — ASSISTED EDITORIAL PRODUCTION", Workstream D.
Tài liệu này **chỉ là thiết kế**. Không có mã Phase 4 nào, không có Documentary Engine mới, không có timeline editor mới.

## 1. Quyết định

| # | Quyết định | Kết quả |
|---|---|---|
| 2 | Documentary Assisted | **READY FOR LIMITED DESIGN/PILOT**. Điều kiện "Quyết định 1 = PASS" đã đạt trên Windows E2E rc9 và rc10 ([ASSISTED_EDITORIAL_PRODUCTION_REPORT.md](ASSISTED_EDITORIAL_PRODUCTION_REPORT.md)). Pilot chỉ được bắt đầu khi đủ các điều kiện vào ở §7 (bộ chuyển đổi nhật ký, kiểm tra cuối §5, P2-9). Chưa có code nào. |
| 3 | Documentary Auto | **NO-GO**. Semantic Gate S FAIL (REAL-HOLDOUT coverage 26,8 % so với ngưỡng 90 %, READY 0/20). Đặt lại tên chế độ không biến FAIL thành PASS. |

## 2. Hiện trạng (đã đo, không suy đoán)

- **Pipeline phim tài liệu** (ADR-0049, `engine/dubflow/docu/`) đi riêng, không qua bộ lập kế hoạch mastering chung (ADR-0052):
  - kịch bản trích dẫn khoảng nguồn cho từng cảnh;
  - `matcher.plan` lấp mỗi khe lời dẫn theo thứ tự ưu tiên: đúng khoảng trích dẫn (CITED), quanh nó (AROUND), cùng nguồn (SAME_SOURCES), nguồn bất kỳ (ANY_SOURCE), cảnh đã dùng (REUSED);
  - mỗi lát cắt ghi lại tầng ưu tiên đã dùng, có đánh dấu `reused`, và cảnh nào phải tìm xa hơn thì có ghi chú.
- Việc khớp là **khớp theo trích dẫn và thời gian, không khớp theo ngữ nghĩa hình ảnh**. Trích dẫn đúng thì cảnh thường đúng. Kịch bản trích sai, hoặc phải tìm xa hơn, thì không có gì kiểm tra rằng hình nói đúng điều lời dẫn nói.
- **Kiểm tra số liệu đã có** (`docu/units.py`): mỗi con số trong lời dẫn phải khớp một đại lượng của nguồn, một tỉ lệ chuẩn hoặc một hằng số chuẩn được nêu trong câu (sai số ±10 % với tiền quy đổi từ nhân dân tệ).
- **Nguồn gốc và quyền sử dụng**: mỗi nguồn có `origin_url` và `credit`, và có tuỳ chọn in credit cuối phim.
- **Bằng chứng ngữ nghĩa trên phim tài liệu** (bộ hồi quy D1 "Why We Fight", cùng bằng chứng rc6/rc7): coverage 43,0 %, không đổi qua #763. Mọi câu sai đều bị gắn cờ, không có bỏ sót im lặng. Con số này đo **bộ lập kế hoạch mastering** trên phim tài liệu, không đo `docu/matcher`.
- **Lệch A/V P2-9** (Why We Fight −41,3 ms ở bước ghép) vẫn mở.

## 3. Phạm vi pilot đề xuất

Dùng lại nguyên những gì Bán hàng/Tổng hợp đã có. **Không làm giao diện mới.**

| Thành phần | Dùng lại | Thay đổi tối thiểu cho phim tài liệu |
|---|---|---|
| Hợp đồng quyết định của người | `mastering/review.py` schema v2 (#772): APPROVE, PIN, EXCLUDE, REPLACE, REVIEW_LATER, MARK_SOURCE_MISSING, UNDO, INVALIDATED | Mỗi **cảnh** của kịch bản là một *part* (`review-<scene>.json`, như phần của Tổng hợp). Mỗi câu lời dẫn là một *unit*. |
| Hàng đợi duyệt | `queue()`/`priority()`, nhãn "chưa hiệu chỉnh" (`queue_calibrated: false`) | Thêm lý do nghiêm trọng riêng cho phim tài liệu (§4). |
| Bảng Duyệt cảnh | `review_panel.ts` (một bảng duy nhất) | Mở theo cảnh. "Chọn cảnh khác…" liệt kê lát cắt của mọi nguồn đã nhập. |
| Kiểm tra cuối | `mastering/final_qc.py` (#774) | Thêm các kiểm tra §5. Trạng thái giữ nguyên (RENDER_PENDING … READY_TO_FINALIZE). |
| Bản cuối và bản cũ | bản ghi revision + sha256 của Tổng hợp (P2-6) | Ghi tương tự cho `documentary.mp4`. |
| Nhật ký quyết định của AI | `docu/matcher` ghi tầng ưu tiên và `reused` của từng lát cắt | Chuyển sang dạng nhật ký mà bảng duyệt đọc được (một bộ chuyển đổi, không phải engine mới). |

## 4. Mục nào là nghiêm trọng (Critical)

Một câu lời dẫn vào nhóm **Nghiêm trọng** khi có ít nhất một điều sau:

1. Câu nêu con số, ngày tháng, tên riêng hoặc nhân quả ("vì", "dẫn đến"), và lát cắt của nó không thuộc tầng CITED.
2. `units.py` không chấp nhận một con số trong câu.
3. Lát cắt lấy từ ANY_SOURCE hoặc REUSED, tức là hình đến từ nguồn kịch bản không trích dẫn, hoặc là hình đã chiếu.
4. Nguồn của lát cắt thiếu `credit`, hoặc thiếu `origin_url`.
5. Quyết định cũ bị huỷ hiệu lực (INVALIDATED), như ở Bán hàng/Tổng hợp.

**Cần xem lại**: tầng AROUND hoặc SAME_SOURCES với câu không nêu thông tin cụ thể.
**Tuỳ chọn**: tầng CITED với câu đã qua kiểm số.

Các nhóm này nói *vì sao* một câu cần người xem. **Chúng không phải xác suất đúng.** Không có điểm tin cậy nào được hiển thị, vì không có điểm nào đã được hiệu chỉnh.

## 5. Kiểm tra cuối riêng cho phim tài liệu

Thêm vào các kiểm tra chung của #774 (dòng thời gian, thời lượng, phụ đề, đỉnh âm, quyết định chưa render, câu chưa duyệt, câu nghiêm trọng):

- **claims**: mọi câu nhóm 1–2 ở §4 phải được người duyệt (APPROVE, PIN/REPLACE, hoặc MARK_SOURCE_MISSING kèm câu lời dẫn đã sửa). "Chấp nhận các câu chưa duyệt" **không** xử lý được các câu này, giống #774.
- **credits**: mọi nguồn có hình xuất hiện trong bản dựng đều có credit. Nếu bật in credit thì danh sách credit trong video phải khớp danh sách nguồn đã dùng.
- **reuse**: không lát cắt nào chiếu lại cùng một khoảng nguồn mà không có đánh dấu `reused`.
- **sync**: lệch A/V ở mỗi điểm ghép không vượt `SYNC_TOLERANCE_MS`. Trước pilot, P2-9 phải được kiểm chính xác đến từng khung hình tại mỗi điểm ghép.
- **stale**: bản cuối không trỏ tới một revision kế hoạch cũ.

## 6. Công sức người duyệt (ước lượng có nguồn)

- **D1**: coverage 43 %, nên khoảng 57 % thời lượng lời dẫn cần người xem hoặc thay cảnh.
- **REAL-HOLDOUT** (Bán hàng/Tổng hợp): phải xem 42/42 câu, khoảng 6–7 câu mỗi video.

Phim tài liệu dài hơn nhiều, nên công sức tăng theo số câu chứ không cố định. **Chưa đo trên một phim tài liệu thật trong chế độ Assisted**: đây là việc đầu tiên của pilot (§7). Chỉ số cần đo giống Workstream C: số câu đã xem, số lần thay cảnh, số câu đánh dấu thiếu nguồn, thời gian duyệt, và chất lượng chấm trên **video cuối**. Nếu không có người độc lập chấm thì ghi rõ "agent-evaluated".

## 7. Kế hoạch pilot (giới hạn)

1. **Điều kiện vào**:
   - Quyết định 1 = PASS;
   - P2-9 đã được kiểm chính xác đến từng khung hình tại các điểm ghép;
   - P2-N11 (ngân sách vision cho phim dài) đã được xử lý;
   - có bộ chuyển đổi nhật ký `docu/matcher` sang nhật ký mà bảng duyệt đọc được, kèm test;
   - các kiểm tra cuối §5 có test.
2. **Tập pilot**: 2 phim tài liệu, 10–15 phút, nguồn có credit đầy đủ. Một phim dùng nguồn đã có (bộ "tháp nuôi lợn"), một phim nguồn mới chưa từng chạy. Nhãn sự thật được khoá bằng hash trước khi chạy.
3. **Chạy trên DubFlow.exe bản candidate trên Windows** qua UI thật, có chứng minh tiến trình. Không tính một test chỉ gọi IPC trực tiếp là hành trình người dùng.
4. **Tiêu chí đạt pilot**:
   - 0 câu nhóm 1–2 lọt vào bản cuối mà chưa được người duyệt;
   - 0 nguồn thiếu credit;
   - không lệch A/V vượt ngưỡng;
   - người duyệt đi hết hàng đợi mà không phải mở công cụ khác.
5. **Không thuộc pilot**: chế độ Auto, khớp ngữ nghĩa bằng mô hình mới, tự viết lại lời dẫn.

## 8. Vì sao chưa phải "READY" vô điều kiện

- Phim tài liệu chưa nối vào hợp đồng duyệt và kiểm tra cuối chung. Bộ chuyển đổi ở §3 là việc kỹ thuật thật và **chưa được làm** (thuộc Phase 4, đang NO-GO).
- Bán hàng/Tổng hợp Assisted đã PASS (rc9, rc10). Tuy vậy, phim dài 20 phút trên rc8 cho thấy 193/214 câu không có cảnh vì ngân sách vision (P2-N11), nên với luồng dài, công sức duyệt chưa chấp nhận được nếu chưa giải quyết lỗi đó.
- P2-9 (lệch A/V ở bước ghép) không tái hiện trên rc8 (−5,4 ms ở phim 20 phút), nhưng chưa kiểm chính xác đến từng khung hình tại mỗi điểm ghép.

## 9. Không thay đổi

Không đổi bất biến nào sau đây:

- giữ nguyên lời lồng tiếng;
- spec Tổng hợp v1/v2;
- đường Bán hàng cũ;
- Mastering Off;
- nguồn gốc và quyền sử dụng;
- timebase;
- checkpoint/resume;
- bảo mật Tauri;
- giám sát worker;
- bộ render.

Không hạ ngưỡng nào. Gate S vẫn FAIL và được ghi riêng trong [PHASE4_READINESS.md](PHASE4_READINESS.md).
