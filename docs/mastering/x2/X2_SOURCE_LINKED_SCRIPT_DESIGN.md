# X2 — Lập kịch bản có liên kết nguồn (chỉ thiết kế)

Epic #637 · 2026-11-14 · main `cf0c2a9`. **Không triển khai, không gọi AI.** Tài liệu này là đề xuất một phép thử nhỏ, có khả năng bác bỏ giả thuyết, kèm ngân sách để chủ dự án xem xét.

## Giả thuyết

Nếu khi AI viết lời bình từ transcript, mỗi câu được giữ liên kết `cue nguồn → khoảng thời gian nguồn → câu kịch bản`, thì khoảng thời gian đó là một vùng tìm cảnh tốt hơn tìm mù trong cả nguồn. Người dùng không phải tự đưa timestamp.

Giả thuyết **bị bác bỏ** nếu, trên bộ thử, vùng do cue chỉ ra chứa cảnh chấp nhận được ở **dưới 50 %** số câu, hoặc không cải thiện recall@5 so với baseline ít nhất 10 điểm phần trăm, hoặc làm tăng số câu no-footage bị gán cảnh.

## Trả lời 10 câu hỏi của chỉ thị

1. **Lúc viết lời bình, hệ thống có những thông tin nguồn nào?** `raw.resolve` (`compilation/raw.py:136-145`) đưa cho AI: tiêu đề mục, ghi chú mục, ghi chú nguồn, và transcript ghép thành một chuỗi, cắt ở 1.500 ký tự. Transcript chỉ có khi người dùng đưa file `.srt` hoặc có `captions_vi.srt` cạnh video (`compilation/program.py:115-122`). Tổng hợp **không tự phiên âm**.
2. **Mốc thời gian bị mất ở đâu?** Ở `raw.py:139-142`: `parse_srt` trả về `(start, end, text)` nhưng chỉ giữ `text`, sau đó ghép lại và cắt 1.500 ký tự. Câu trả lời của AI (`narration`, `needs_check`) không có trường nào trỏ về cue.
3. **Có giữ được provenance của cue mà không sai danh tính nguồn không?** Được, nếu mỗi cue mang `source_id` + `start_ms/end_ms` theo trục thời gian của nguồn đã cắt. Mỗi câu AI viết sẽ trả kèm danh sách mã cue (ví dụ `["s1#12","s1#13"]`), và harness kiểm tra mã đó có tồn tại. Mã cue không hợp lệ thì bị bỏ, không tự sửa.
4. **Một câu có dùng nhiều nguồn/cue không?** Có. Lược đồ đề xuất: `cues: [{source_id, cue_index}]`, có thể rỗng. Vùng tìm là hợp của các khoảng cue, mở rộng ±W giây (W là tham số thử: 5/15/30 s).
5. **Câu tổng hợp nhiều sự kiện thì sao?** Lưu nhiều cue rời nhau. Vùng tìm là nhiều khoảng, không gộp thành một khoảng bao trùm. Câu không có cue (bình luận chung) ghi `cues: []` và rơi về cách tìm hiện tại.
6. **Transcript đúng nhưng hình không liên quan thì sao?** Đây là rủi ro chính. Quy tắc: **cue chỉ thu hẹp vùng tìm, không bao giờ là bằng chứng hình.** READY vẫn đòi bằng chứng hình (Vision hoặc người). Phép thử đo riêng "vùng cue có cảnh chấp nhận được không" bằng nhãn người.
7. **Có dùng vùng nguồn mà vẫn cho chọn B-roll ngoài vùng không?** Có. Vùng cue dùng để *ưu tiên* (xếp trước, dồn ngân sách nhìn), không dùng để *loại* ứng viên ngoài vùng. Phép thử báo cả hai số: recall@5 trong vùng và recall@5 toàn cục.
8. **Cần sửa contract nào (nếu đi tiếp, không phải bây giờ)?** `DRAFT_SCHEMA`/`DRAFT_SYSTEM` (thêm `cues` cho từng câu), dữ liệu đầu vào cho AI (cue có mã và giờ, thay cho chuỗi phẳng 1.500 ký tự), `editorial-*.json` (lưu `cues` theo đơn vị), và một tín hiệu xếp hạng mới trong planner. Mọi thay đổi này đều là production, **không thuộc phạm vi hiện tại**.
9. **Có ảnh hưởng Legacy không?** Lời bình do người dùng tự nhập không có cue nên không đổi. Run cũ không có trường `cues` thì đọc như rỗng. Bán hàng và Phim tài liệu không dùng `raw.resolve`. Không có thay đổi lược đồ runtime trong phép thử, vì phép thử chạy hoàn toàn trong harness.
10. **Chi phí AI văn bản dự kiến?** Xem mục Ngân sách bên dưới: khoảng **2 USD** cho một lượt, trần đề xuất **10 USD**.

## Phép thử đề xuất (X2-POC, cần phê duyệt riêng)

**Dữ liệu:** ≥ 2 nguồn có thoại thật (không dùng W3 làm kết luận), tổng ≥ 20 mục. Transcript lấy từ ASR local của X1, nên $0.

**Các bước:**

1. Harness dựng đầu vào có mã cue cho từng mục.
2. Gọi AI văn bản viết lời bình kèm `cues`. Đây là lần duy nhất tốn tiền.
3. **Khóa** lời bình và cue (sha256) trước khi xem bất kỳ cảnh nào.
4. Người gắn nhãn (không phải người viết lời bình hay người chạy thử) đánh dấu mọi cảnh chấp nhận được cho từng câu, theo taxonomy X0.
5. So sánh offline ($0): A baseline (toàn nguồn), B vùng cue ±W, C vùng cue làm tín hiệu xếp hạng trên toàn nguồn. Đo recall@1/3/5, tỉ lệ vùng trúng, mức thu hẹp pool, gán sai câu no-footage, và tỉ lệ câu có cue hợp lệ. Vision không được gọi; dùng mô tả Vision đã cache nếu có.

**Tiêu chí:** như phần Giả thuyết. Kết quả trên dưới 100 câu chỉ là tín hiệu, không phải nghiệm thu.

## Ngân sách (ước tính)

Giá lấy theo bảng giá hiện hành, model mặc định của app là Claude Opus 5.5 ($4 / $20 cho mỗi triệu token vào/ra):

- Khoảng 20–60 lần gọi. Mỗi lần ~3.000 token vào (cue có mã) và ~800 token ra.
- 60 lần ≈ 180 nghìn token vào (~0,72 USD) + 48 nghìn token ra (~0,96 USD) ≈ **1,7 USD**.
- Chạy 3 lần để đo độ dao động ≈ 5 USD. **Trần đề xuất: 10 USD.** Dừng ngay khi chạm trần.
- Chi phí người: gắn nhãn khoảng 100–200 câu, ước tính 2–4 giờ công. Đây là phần đắt nhất.

## Những gì X2 không làm

- Không coi mốc transcript là mốc cảnh đúng.
- Không cho transcript đẩy câu lên READY.
- Không đổi planner, schema hay UI cho tới khi có chỉ thị riêng.

> **Ghi chú 2026-11-14:** phần "Phép thử đề xuất" và "Ngân sách" ở trên đã được thay bằng `X2_POC_DESIGN_V2.md`. Ước tính ~2 USD cũ chỉ tính phần viết lời dẫn, chưa tính Vision, thinking và thử lại. Ước tính 2–4 giờ gắn nhãn cũ cũng quá thấp; con số mới là khoảng 15–16 giờ.
