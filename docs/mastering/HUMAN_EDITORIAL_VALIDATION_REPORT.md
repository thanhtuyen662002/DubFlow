# Kiểm nghiệm biên tập với người thật (Workstream D)

Epic #637 · 2026-11-12 · chỉ thị "DUBFLOW — ASSISTED PRODUCTION STABILIZATION & DOCUMENTARY PILOT ENTRY GATES".

## 1. Kết luận

**Gate D: BLOCKED.** Chưa có người tham gia nào ngồi duyệt một phiên thật.

- Không có dữ liệu người nào được tạo ra, ước lượng hay mô phỏng.
- Các lượt chạy tự động qua UI thật bằng CDP trong Windows E2E (W1–W5) là **kiểm thử tự động**, không phải "người duyệt". Chúng không được tính vào Gate D.
- Không có tuyên bố nào về năng suất ("nhanh hơn X %", "tiết kiệm Y phút"), vì chưa có phiên người thật và chưa có mốc làm tay công bằng.

Gate D chỉ chuyển sang PASS hoặc FAIL khi có ít nhất một phiên thật theo giao thức ở §3, với số đo ghi theo §5.

## 2. Vì sao BLOCKED, không phải FAIL

FAIL nghĩa là đã đo và không đạt. Ở đây chưa đo được gì: chưa có người tham gia. Gate D là điều kiện bắt buộc của Gate 4 trong [DOCUMENTARY_PILOT_ENTRY_GATES.md](DOCUMENTARY_PILOT_ENTRY_GATES.md), nên kết luận cuối ở đó là **NOT READY FOR PILOT IMPLEMENTATION** cho tới khi Gate D có kết quả.

## 3. Giao thức phiên

### 3.1 Người tham gia

- Ít nhất **một** người thật dùng DubFlow cho việc của mình (chủ dự án hoặc một người biên tập video). Không phải agent, không phải kịch bản tự động.
- Ghi vai trò và mức quen DubFlow (mới / đã dùng / dùng hằng ngày). Không ghi tên, e-mail hay thông tin cá nhân nào khác.
- Người tham gia được biết đây là phiên đo, và được phép dừng bất cứ lúc nào.

### 3.2 Ba phiên

| Phiên | Luồng | Đầu vào | Việc người tham gia làm |
|---|---|---|---|
| H1 | Bán hàng | 1 sản phẩm thật, 3–6 clip thật, lời bán hàng 6–10 câu | Tạo bản nháp, đi hết hàng đợi, xử lý mọi câu Nghiêm trọng, qua Kiểm tra cuối, mở editor |
| H2 | Tổng hợp | 4–8 mục, nguồn thật, 2–5 phút | Tạo bản nháp, duyệt theo phần, ghim/thay/bỏ ít nhất một cảnh, dựng lại, "Xuất bản cuối" |
| H3 | Phim dài | Why We Fight 20 phút hoặc một nguồn thật ≥ 15 phút | Tạo bản nháp, duyệt ít nhất một phần trọn vẹn, đánh dấu "Không có cảnh phù hợp" ở câu thật sự thiếu, ghi lại chỗ khó |

Tất cả chạy trên **DubFlow.exe bản candidate trên Windows**, thư mục dữ liệu riêng, không phát hành.

### 3.3 Mốc làm tay công bằng (bắt buộc nếu muốn nói về năng suất)

- Cùng người, cùng đầu vào (hoặc đầu vào tương đương, độ dài lệch ≤ 10 %), cùng tiêu chí "xong".
- Làm tay bằng công cụ người đó vẫn dùng (CapCut, Premiere…) hoặc bằng DubFlow với Mastering Tắt.
- Thứ tự đảo giữa hai cách (lần này Assisted trước, lần sau làm tay trước), để không ai được lợi vì đã quen nội dung.
- Thiếu mốc này thì chỉ báo số đo tuyệt đối của phiên Assisted, không so sánh.

### 3.4 Người quan sát

- Người quan sát (agent hoặc người) chỉ ghi lại, **không gợi ý thao tác**.
- Câu hỏi của người tham gia được ghi nguyên văn, và câu trả lời được ghi là "có trợ giúp".

## 4. Câu hỏi sau phiên

Mỗi câu một thang 1–5, kèm một dòng tự do:

1. Tôi hiểu vì sao từng câu nằm trong nhóm Nghiêm trọng / Cần xem / Tuỳ chọn.
2. Khi tôi đã duyệt một câu, nó vẫn được giữ sau khi tôi sửa câu khác (P2-N5).
3. Khi một quyết định của tôi bị huỷ, tôi hiểu lý do và biết phải làm gì.
4. Với phim dài, phần lớn câu có cảnh đề xuất đáng xem (P2-N11).
5. Tiếng và hình khớp nhau ở chỗ chuyển cảnh và chỗ nối phần (P2-9).
6. Kiểm tra cuối chặn đúng thứ cần chặn, không chặn thừa.
7. Tôi sẽ dùng bản này cho việc thật.

## 5. Số đo của mỗi phiên

Ghi từ nhật ký app (`review-*.json`, `final_qc`, thời điểm các bản ghi quyết định) và từ người quan sát.

| Số đo | Cách lấy |
|---|---|
| Thời gian từ bấm Tạo đến khi Kiểm tra cuối đạt | đồng hồ người quan sát và thời điểm bản ghi |
| Thời gian người duyệt thật sự thao tác | tổng các khoảng giữa bản ghi quyết định, bỏ khoảng chờ dựng |
| Số câu đã xem / tổng số câu | bản ghi quyết định |
| Số lần Duyệt, Ghim, Thay, Bỏ, Để sau, Thiếu cảnh, Hoàn tác | bản ghi quyết định |
| Số quyết định bị huỷ, và bao nhiêu trong đó người tham gia đồng ý là đúng lý do | INVALIDATED + hỏi lại |
| Số câu Nghiêm trọng còn treo khi người tham gia nghĩ là xong | `final_qc` |
| Số lần phải hỏi người quan sát | ghi chép |
| Lỗi nhìn thấy trên **video cuối** (sai cảnh, lệch tiếng, khung đứng) | người tham gia xem lại bản cuối, đánh dấu thời điểm |

## 6. Tiêu chí

Gate D **PASS** khi, trên mọi phiên đã chạy:

- 0 câu Nghiêm trọng lọt vào bản cuối mà chưa được người duyệt;
- 0 quyết định bị mất mà không có bản ghi INVALIDATED và lý do;
- người tham gia đi hết hàng đợi mà không phải mở công cụ khác;
- không có lỗi lệch tiếng/hình nào người tham gia thấy trên video cuối;
- câu 2, 3 và 6 ở §4 đạt từ 4 trở lên.

Gate D **FAIL** khi một tiêu chí trên không đạt. Kết quả FAIL được ghi nguyên số đo, không chạy lại cho tới khi đạt.

Năng suất không thuộc tiêu chí đạt. Nó chỉ được báo khi có mốc làm tay ở §3.3.

## 7. Dữ liệu và quyền riêng tư

- Không ghi tên, e-mail, đường dẫn hồ sơ người dùng (dùng `%USERPROFILE%`), khoá AI.
- Video và nguồn của người tham gia ở lại trên máy họ. Báo cáo chỉ ghi số đo và thời điểm.
- Lượt quét khoá và e-mail trên mọi tệp báo cáo phải ra 0.

## 8. Đã làm trong đợt này

- Viết giao thức này.
- Chuẩn bị đầu vào H3 (Why We Fight, 2146,8 s) trên máy Windows trong thư mục riêng.
- Mọi hành trình H1–H3 đã chạy **tự động** trong Windows E2E W1–W3 để chắc chắn người tham gia không gặp lỗi chặn. Kết quả đó ở báo cáo của từng workstream, không ở đây.

## 9. Việc tiếp theo

Một phiên H1, H2 hoặc H3 với người thật theo §3. Kết quả ghi vào §10 của báo cáo này, cùng với ngày, bản candidate và số đo §5.
