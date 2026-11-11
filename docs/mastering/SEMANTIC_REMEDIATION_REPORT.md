# DubFlow — Báo cáo sửa chất lượng ngữ nghĩa (Workstream B)

Epic #637 · 2026-11-11 · main `005344e`
Không phát hành bản stable. Mọi quy tắc đều tổng quát; không có ngưỡng nào chỉnh riêng cho một video, không đổi ground truth.

## Trả lời thẳng

Cả 5 lỗi ngữ nghĩa trong chỉ thị đã có nguyên nhân gốc được sửa. Kiểm chứng bằng bộ hồi quy cố định:

| Lỗi | Trước | Sau | PR |
|---|---|---|---|
| Máy thổi: bỏ sót không báo | 1 câu (dev2), 1 câu (holdout4) | **0** ở mọi bộ | #706 |
| Tăm: câu "tháo đế" bị gán nhầm cảnh | khớp sai qua hư từ ("chuyển động") | đúng cảnh, 5/5 câu | #706 |
| Espresso: câu mở đầu lấy cảnh chính của câu sau | 6000 ms cảnh chính bị lấy (dev2) | **0 ms** | #706 |
| Top 5: mở đầu bằng thẻ tiêu đề | thẻ chữ thắng cảnh thật | cảnh thật; thẻ chỉ dùng cho câu nói về chữ | #706 |
| Why We Fight: Kremlin/Kavkaz | lời dẫn bị dồn vào 0–263 s, nguồn dài bị cắt ở 1202/2147 s | lời dẫn trải trên toàn bộ nguồn, ứng viên trải đều | #710 |

Còn mở:
- Tên riêng song ngữ (Kavkaz / Caucasus, Kremlin / Кремль) chưa được ghép tự động.
- `dev3-street-food` còn lấy 1,4 s cảnh chính của câu sau. Được ghi là KNOWN_OPEN và không được phép xấu hơn.
- Số câu bị cảnh báo thừa (false alarm) tăng từ 33 lên 42 trên 133 câu, chủ yếu ở dev2. Xem mục 4.

## 1. Trạng thái bằng chứng và phân bổ cảnh toàn cục

**Trạng thái bằng chứng (#702).** `review.annotate()` ghi cho mỗi câu một trong các trạng thái sau:
- `MATCH_AVAILABLE`;
- `MATCH_NOT_FOUND`;
- `SOURCE_INSUFFICIENT`: chỉ khi mọi cảnh dùng được đều có mô tả nội dung mà không cảnh nào khớp;
- `NOT_NEEDED`.

Từ đó có trạng thái duyệt `READY` / `NEEDS_REVIEW` / `MATCH_NOT_FOUND` / `SOURCE_INSUFFICIENT`. Quyết định của người dùng (`USER_APPROVED`, ghim, loại) nằm riêng trong `review-<n>.json` và không bao giờ sửa phán đoán của planner.

**Phân bổ cảnh toàn cục (#706, #710).** `align.assign` xếp mọi câu trên toàn bộ kho cảnh cùng lúc, không theo thứ tự từng câu:
- Một câu chỉ "giành" được cảnh khi nó gần cảnh đó hơn hẳn các câu khác (độ mạnh tương đối), không chỉ vì đến trước.
- Câu mở đầu và kết thúc được xếp sau các câu nội dung.
- Ở chế độ sắp xếp lại, câu nào bị bỏ đói sẽ bị phạt.
- Câu đệm không được lấy phần còn lại của một cảnh mà câu khác đã dùng. Thay vào đó là "lặp lại" có khai báo, và lặp lại không được tính là đã minh họa.
- Tổng hợp từ nguồn thô (#710): lời dẫn của mỗi mục được xếp trên toàn bộ cảnh của mục đó trước, rồi mới chọn khoảnh khắc nguồn từ phần phát sau lời dẫn. Với nguồn dài hơn giới hạn, 400 ứng viên được trải đều trên toàn bộ thời lượng.

## 2. Nguyên nhân gốc và cách sửa

**Hư từ (tăm).**
- Nguyên nhân: cặp từ "~chuyển động" khớp qua hư từ, nên câu "tháo đế" bị gán cảnh chuyển động.
- Sửa (`evidence.py`):
  - thêm hư từ tiếng Việt (có dấu và không dấu);
  - cặp từ không bao giờ nối qua hư từ;
  - chuẩn hóa số và giá (600K / 600.000đ / 600 nghìn).
- Ngoài ra (`align.py`): từ xuất hiện ở đa số cảnh có trọng số khớp bằng 0.

**Thẻ chữ (Top 5).**
- Nguyên nhân: điểm trung tính vẫn ưu tiên thẻ chữ (độ mạnh 1000 − 300) hơn cảnh thật.
- Sửa: khi nguồn có cảnh thật dùng được, độ liên quan của thẻ chữ bằng 0 với câu không nói về chữ. Thẻ chữ cũng không được thưởng hook.

**Lấy cảnh chính (espresso).**
- Nguyên nhân: câu hook khớp yếu lấy mất cảnh duy nhất của câu sau.
- Sửa:
  - quy tắc giành cảnh theo độ mạnh tương đối;
  - câu mở đầu/kết thúc được xếp sau cùng;
  - quy tắc "đuôi cảnh": không lấy phần còn lại của cảnh đã được câu khác dùng.

**Bỏ sót không báo (máy thổi).**
- Nguyên nhân: câu chung chung được lấp bằng cảnh vụn hoặc cảnh lặp mà không bị gắn cờ.
- Sửa: câu chung chung có cảnh vụn hoặc lặp bị gắn cờ, và cảnh lặp không được tính là minh họa.

**Nguồn dài (Why We Fight).**
- Nguyên nhân: chọn khoảnh khắc nguồn trước rồi mới xếp lời dẫn vào trước nó. Thêm vào đó, 400 ứng viên đầu tiên theo thứ tự nguồn bỏ mất nửa sau video 36 phút.
- Sửa: xếp lời dẫn trước, rồi chọn khoảnh khắc. Đường cũ được giữ làm phương án dự phòng. Ứng viên trải đều.
- Recipe: `compilation-program-v12`, candidates `+spread400`.

## 3. Bộ dữ liệu và cách dùng

| Bộ | Vai trò | Ghi chú |
|---|---|---|
| dev | dev | có từ Phase 2 |
| dev2 | dev, **dùng để chỉnh** | 5 dạng lỗi từ lần chạy thật: máy thổi, tăm, espresso, thẻ chữ hook, thẻ tiêu đề mở đầu |
| dev3 | dev | trước là `holdout3`; đã xem khi chẩn đoán cổng "lấy cảnh chính", nên đổi thành dev theo chỉ thị |
| holdout | held-out | viết trước Phase 2 hardening, chỉ báo cáo |
| holdout2 | held-out | **không dùng để chỉnh** |
| holdout4 | held-out mới, độc lập | viết sau khi dev3 bị dùng, không xem trước khi chạy |

Các file ground truth được khóa bằng hash trong `FROZEN.json`, và test `test_ground_truth_matches_the_frozen_hashes` chặn mọi thay đổi.

## 4. Kết quả (`python -m tests.mastering.regression.runner all`)

So sánh main `b7fee92` (trước Workstream B) với main hiện tại. Cùng bộ dữ liệu, cùng runner.

| Bộ | Độ phủ | Câu đạt | Bỏ sót không báo | Cảnh chính bị lấy | Cảnh báo thừa |
|---|---|---|---|---|---|
| dev | 74,8 → 74,8 % | 11/17 → 11/17 | 0 → 0 | 7000 → 7000 ms | 2 → 2 |
| dev2 | 82,9 → **93,9 %** | 14/23 → **20/23** | 1 → **0** | 6000 → **0 ms** | 4 → 11 |
| dev3 | 91,0 → 97,4 % | 18/23 → 21/23 | 0 → 0 | 1500 → 1400 ms | 9 → 11 |
| holdout | 84,1 → 82,8 % | 15/23 → 15/23 | 0 → 0 | 0 → 0 | 4 → 5 |
| holdout2 | 78,4 → 78,4 % | 14/23 → 14/23 | 0 → 0 | 0 → 0 | 8 → 8 |
| **holdout4** (độc lập) | 95,4 → **100 %** | 19/20 → **20/20** | 1 → **0** | 0 → 0 | 6 → 5 |

Đọc kết quả cho thẳng:
- **Bỏ sót không báo bằng 0 ở mọi bộ**, kể cả bộ độc lập holdout4.
- **holdout: airfryer giảm 78,8 → 73,1 %** do quy tắc "đuôi cảnh" ngắn. Số câu đạt không đổi.
- **Cảnh báo thừa tăng 33 → 42 trên 133 câu**, chủ yếu ở dev2, do quy tắc gắn cờ câu chung chung có cảnh vụn. Mức tăng này không phải là cách đạt cổng: các câu đạt tăng vì được minh họa đúng, không phải vì bị gắn cờ, và holdout4 có ít cảnh báo hơn. Tuy vậy, độ chính xác của cờ cần cải thiện ở vòng sau.
- `dev-long-forward` vẫn lấy 7000 ms cảnh chính, giống như trước. Đây là compilation chỉ-tiến với nguồn quá ít.

## 5. Còn mở và hướng xử lý

1. **Tên riêng song ngữ**: ghép tên theo bảng phiên âm hoặc ngữ cảnh khi tìm lại cảnh. Chưa làm, và không thêm model nặng.
2. **Nhiều khung hình mỗi ứng viên**: cảnh hòa tan và keyframe xám bị mô tả sai. Hướng sửa là lấy 2–3 khung hình mỗi cảnh.
3. **dev3-street-food** (1,4 s) và **dev-long-forward** (7 s): chế độ chỉ-tiến không có cảnh thay thế. Cần cảnh báo rõ hơn thay vì lấy cảnh.
4. **Độ chính xác của cờ** trên dev2/dev3.
