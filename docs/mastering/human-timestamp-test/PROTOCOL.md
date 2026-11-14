# Human Timestamp Feasibility Test: giao thức

Epic #637 · issue #832 · đề xuất #815 (Phương án D) · soạn 2026-11-14 · trạng thái: **CHƯA CHẠY**. Chưa có người tham gia, chưa có số liệu.

Bài kiểm tra này trả lời một câu hỏi: **người thật có đánh được mốc thời gian đủ chính xác và đủ nhanh để việc "đánh mốc rồi chọn trong danh sách gợi ý" tốt hơn cách tìm cảnh bằng Review Panel hiện tại hay không.** Thí nghiệm offline ([HUMAN_GUIDED_TIMESTAMP_FEASIBILITY_REPORT.md](../HUMAN_GUIDED_TIMESTAMP_FEASIBILITY_REPORT.md)) chỉ cho biết mốc cần chính xác đến đâu (±5–15 s). Nó không đo được người thật có làm được không, nên kết luận ở đó là INCONCLUSIVE.

Ràng buộc:

- Không gọi AI hay vision API. Không thêm model. Không đổi planner, engine hay UI production.
- Không mô phỏng người dùng. Không tự tạo số liệu.
- Tự động hoá CDP không phải người duyệt và không được tính.
- Nếu chưa có người tham gia, bài kiểm tra ở trạng thái **NOT RUN** và Human Validation Gate D vẫn là **BLOCKED**.

## 1. Hai cách được so sánh

| Mã | Cách làm | Công cụ |
|---|---|---|
| **RP** (Review Panel) | Người tham gia tìm và chọn cảnh bằng Review Panel hiện có: xem cảnh đang dùng và "Cảnh thay thế", mở "Chọn cảnh khác…" để xem mọi cảnh đã nhập, bấm "Dùng cảnh này", hoặc "Không có cảnh phù hợp". | DubFlow.exe bản candidate, run đã dựng sẵn |
| **TS** (Timestamp) | Người tham gia mở video nguồn bằng trình phát video thường, tìm và **ghi mốc thời gian gần đúng** (mm:ss) cùng độ chắc chắn (±5, ±15 hoặc ±30 s). Người điều phối chạy `hintexp.py suggest` để ra **tối đa 5 cảnh gợi ý** quanh mốc đó. Người tham gia xem các khoảng thời gian gợi ý trong trình phát rồi chọn một cảnh, hoặc ghi "không có cảnh phù hợp". | Trình phát video (VLC, Media Player), biểu mẫu, `hintexp.py` (công cụ offline trong `tests/`, không phải tính năng) |

TS không có giao diện riêng, và bài kiểm tra **không** xây giao diện mới. Thao tác "chọn trong gợi ý" làm trên biểu mẫu, nên thời gian chọn ở TS chỉ là ước lượng của một quy trình chưa có UI. Báo cáo phải ghi rõ giới hạn này.

## 2. Tài liệu đầu vào

- **Ít nhất hai video dài**, mỗi video ít nhất 15 phút. Video phải có lời dẫn tiếng Việt đã dựng sẵn và nhãn cảnh đúng đã khoá bằng sha256 trước phiên.
  - Video 1: run W3 rc16 (*Why We Fight: The Battle of Russia*), nhãn `afa979d2…f66497`.
  - Video 2: **cần chủ dự án chọn** (§8). Nếu chưa có video thứ hai có nhãn khoá, phải gắn nhãn mới trước khi chạy, và người gắn nhãn không được là người tham gia.
- **20–40 câu**, chia đều giữa hai video, trong đó khoảng 20–25 % là câu **không có footage phù hợp** (ví dụ 32 câu: 25 có footage, 7 không có).
- **Chọn câu trước phiên** bằng một quy tắc cố định, không do người tham gia chọn. Ưu tiên câu mà production chưa chọn trúng (không READY), để bài kiểm tra đo đúng chỗ khó. Danh sách câu và sha256 của nó được ghi vào `forms/selection.csv` trước phiên.
- Câu được chia thành **bốn khối** (V1-a, V1-b, V2-a, V2-b) cân bằng theo: số câu, số câu không có footage, và hạng của cảnh đúng trong production (trong top 5 hay không). Cân bằng chỉ dùng thống kê đã có, không cho người tham gia xem.

## 3. Chống thiên lệch

1. **Không lặp câu giữa hai cách.** Một người không bao giờ làm cùng một câu bằng cả RP và TS, vì lần thứ hai đã biết cảnh đúng.
2. **Đảo thứ tự kiểu ABBA** trong mỗi người: V1-a RP, V1-b TS, V2-a TS, V2-b RP. Người thứ hai (nếu có) dùng BAAB. Khối nào gán cho cách nào được ghi trước phiên.
3. **Người tham gia không biết cảnh đúng:**
   - không phải người gắn nhãn;
   - chưa từng duyệt các câu đó trong app;
   - không được xem nhãn, báo cáo W3 hay danh sách câu trúng hay trượt.

   Nếu người tham gia đã xem phim, ghi lại mức quen (chưa xem / đã xem một phần / đã xem kỹ).
4. **Người điều phối chỉ ghi, không gợi ý.** Câu hỏi của người tham gia được ghi nguyên văn, và câu đó được đánh dấu "có trợ giúp".
5. **Chấm điểm sau phiên**, bằng script, đối chiếu với nhãn đã khoá. Người tham gia không được biết kết quả trước khi xong mọi khối.
6. **Run cố định:** RP và TS dùng đúng một run đã dựng sẵn cho mỗi video (cùng ứng viên, cùng bằng chứng đã lưu). Không dựng lại giữa phiên.

## 4. Diễn tiến phiên (khoảng 90–120 phút cho 32 câu)

1. **Chuẩn bị** (người điều phối): kiểm tra sha256 của nhãn và `selection.csv`. Mở run trong DubFlow.exe với thư mục dữ liệu riêng. Mở video nguồn trong trình phát. Bật đồng hồ ghi thời gian.
2. **Làm quen** (5 phút, câu luyện tập không thuộc danh sách đo): thử mỗi cách một câu.
3. **Bốn khối** theo thứ tự đã gán. Với mỗi câu:
   - **RP:** bắt đầu tính giờ khi người tham gia đọc câu. Dừng khi bấm "Dùng cảnh này", "Duyệt cảnh này" hoặc "Không có cảnh phù hợp". Số thao tác lấy từ bản ghi quyết định của Review Panel, cộng với đếm tay số lần mở "Chọn cảnh khác…" và số lần phát cảnh.
   - **TS, bước 1:** tính giờ từ lúc đọc câu tới lúc ghi xong mốc và độ chắc chắn.
   - **TS, bước 2:** người điều phối chạy `hintexp.py suggest` (thời gian chạy không tính cho người tham gia). Rồi tính giờ từ lúc người tham gia nhận danh sách gợi ý tới lúc chọn xong. Nếu không thấy cảnh đúng, người tham gia được đánh mốc lại; mỗi lần đánh lại là một "lần tìm lại".
   - Cả hai cách: ghi độ tự tin 1–5, và "có trợ giúp" nếu có.
4. **Nghỉ 5 phút** giữa hai video.
5. **Câu hỏi sau phiên** (`forms/post-session.md`).
6. **Chấm điểm** (sau phiên): `hintexp.py score-human` đọc `forms/units.csv`, tính sai số mốc, recall@1/3/5 của danh sách gợi ý và tỉ lệ chọn đúng, rồi ghi `human-results.json` ở thư mục cục bộ.

## 5. Số đo

| Số đo | RP | TS | Cách lấy |
|---|---|---|---|
| Sai số mốc: |mốc − khoảng đúng gần nhất| (s), và tỉ lệ ≤ 5, ≤ 15, ≤ 30 s | – | có | script, sau phiên |
| Recall@1/3/5 của danh sách gợi ý | top 5 của production (cố định) | gợi ý từ mốc của người thật | script |
| Chọn đúng: cảnh cuối cùng chồng lên khoảng đúng | có | có | script |
| Nhận biết thiếu footage: câu không có footage được đánh "không có cảnh phù hợp"; câu có footage bị đánh nhầm là thiếu | có | có | script |
| Thời gian mỗi câu (s): tổng và từng bước | có | có (bước 1 + bước 2) | đồng hồ và bản ghi |
| Số thao tác | bản ghi quyết định + đếm tay | đếm tay | biểu mẫu |
| Số lần tìm lại | số lần mở "Chọn cảnh khác…" sau lần đầu | số lần đánh mốc lại | biểu mẫu |
| Câu cần trợ giúp | có | có | biểu mẫu |

## 6. Tiêu chí, cố định trước khi chạy

Áp dụng trên các câu đã hoàn thành. Mẫu nhỏ, nên chỉ báo số mô tả (trung vị, tỉ lệ, khoảng), không tuyên bố ý nghĩa thống kê.

**Điều kiện hợp lệ.** Thiếu một trong các điều kiện sau thì kết quả là **INCONCLUSIVE**:

- ít nhất 20 câu hoàn thành ở cả hai cách cộng lại;
- ít nhất 2 video;
- ít nhất 4 câu không có footage;
- không có vi phạm §3.

**SUCCESS**, khi đạt tất cả:

1. Mốc TS: trung vị sai số ≤ 15 s, và ít nhất 70 % câu có footage có mốc trong ±15 s.
2. Recall@5 của gợi ý từ mốc người thật ≥ 75 % số câu có footage ở khối TS.
3. Chọn đúng ở TS không thấp hơn RP quá 5 điểm phần trăm.
4. Một trong hai lợi ích:
   - trung vị thời gian mỗi câu ở TS ≤ 80 % của RP (nhanh hơn ít nhất 20 %); hoặc
   - chọn đúng ở TS cao hơn RP ít nhất 15 điểm phần trăm.
5. Nhận biết thiếu footage ở TS không kém RP: số câu không có footage mà vẫn bị gán cảnh ở TS không nhiều hơn ở RP.

**NEGATIVE**, khi đạt bất kỳ điều nào:

- Trung vị sai số mốc > 30 s, hoặc dưới 50 % câu có mốc trong ±15 s.
- TS chậm hơn RP từ 20 % trở lên mà chọn đúng không cao hơn.
- TS gán cảnh cho nhiều câu không có footage hơn RP.

**INCONCLUSIVE:** mọi trường hợp còn lại.

Dù kết quả là gì, bài kiểm tra này **không** đổi Gate B, không mở pilot, không mở Phase 4, và không phải là duyệt xây tính năng. SUCCESS chỉ cho phép đề xuất (qua ADR riêng) bước tiếp: một tập giữ riêng mới và thiết kế UI có duyệt.

## 7. Dữ liệu và quyền riêng tư

- Biểu mẫu đã điền, `human-results.json`, video, khung hình và nội dung câu **chỉ nằm trên máy**, trong `_sandbox`, ngoài repo.
- Không ghi tên, e-mail hay thông tin cá nhân. Người tham gia được ghi bằng mã (P1, P2), vai trò và mức quen DubFlow.
- Repo chỉ nhận **số tổng hợp** và sha256 của biểu mẫu, qua một PR riêng, sau khi quét rò rỉ (đường dẫn, e-mail, chuỗi giống khoá) bằng 0.
- Không gọi AI ở bất kỳ bước nào. `hintexp.py` chỉ đọc file run đã lưu.

## 8. Quyết định cần chủ dự án duyệt trước khi chạy

1. **Người tham gia:** ai làm (chủ dự án hay một người biên tập khác), và người đó có đáp ứng §3.3 không.
2. **Video thứ hai** và nhãn cảnh đúng cho video đó: dùng video có sẵn, hay gắn nhãn mới (ai gắn).
3. **Số câu và tỉ lệ thiếu footage** (đề xuất: 32 câu, 7 câu không có footage).
4. **Tiêu chí §6:** giữ nguyên, hay chỉnh **trước** khi chạy. Sau khi chạy thì không sửa.
5. **Thời gian:** một phiên khoảng 90–120 phút, trên máy Windows của chủ dự án.

## 9. Tệp đi kèm

- [PARTICIPANT_GUIDE.md](PARTICIPANT_GUIDE.md): hướng dẫn cho người tham gia.
- [FACILITATOR_GUIDE.md](FACILITATOR_GUIDE.md): hướng dẫn cho người điều phối, gồm lệnh chạy `hintexp.py`.
- `forms/selection.csv`: danh sách câu, khối và cách được gán (điền trước phiên).
- `forms/units.csv`: kết quả từng câu (điền trong phiên).
- `forms/session.csv`: thông tin phiên.
- [forms/post-session.md](forms/post-session.md): câu hỏi sau phiên.
