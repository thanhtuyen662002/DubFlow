# Phim tài liệu Assisted — Cổng vào Pilot (chỉ thiết kế)

Epic #637 · 2026-11-12 · chỉ thị "DUBFLOW — ASSISTED PRODUCTION STABILIZATION & DOCUMENTARY PILOT ENTRY GATES".

Tài liệu này **chỉ là thiết kế**:
- không có mã pilot nào;
- không có Documentary Auto;
- không có engine, model hay timeline editor mới.

Nó nối tiếp [DOCUMENTARY_ASSISTED_READINESS.md](DOCUMENTARY_ASSISTED_READINESS.md).

## 1. Kết luận

**NOT READY FOR PILOT IMPLEMENTATION.**

Kết luận "READY TO START LIMITED DOCUMENTARY ASSISTED PILOT IMPLEMENTATION" chỉ được dùng khi cả năm cổng ở §2 đều PASS. Trên Windows rc15: cổng 1 PASS, cổng 2 **FAIL**, cổng 3 PASS cho Tổng hợp (phim tài liệu chưa đo), cổng 4 **BLOCKED**, cổng 5 PASS (thiết kế). Vì vậy kết luận là NOT READY.

Documentary Auto vẫn **NO-GO** (Gate S FAIL, [PHASE4_READINESS.md](PHASE4_READINESS.md)).

## 2. Năm cổng

| Cổng | Điều kiện | Trạng thái | Bằng chứng |
|---|---|---|---|
| 1 · Quyết định của người được giữ (A) | Duyệt thành ràng buộc; mọi lần huỷ có nguyên nhân thật và bản ghi xung đột; 0 huỷ tránh được; 0 thay đổi im lặng; đổi kế hoạch theo giao dịch; Windows W1/W2 đạt | **PASS** (Windows rc15: W1 giữ 4/4, W2 dựng 1 phần, W5 dựng 0; 0 huỷ) | [LOCALIZED_REPLANNING_REPORT.md](LOCALIZED_REPLANNING_REPORT.md); #782, #797, #798, #803, #804 |
| 2 · Bằng chứng phim dài (B) | recall@5 ≥ 85 % trên câu có footage, nhãn ≥ 40 câu khoá trước khi chấm; 0 bỏ sót im lặng nghiêm trọng; 0 SOURCE_INSUFFICIENT sai; 0 khung đứng ngoài ý muốn; báo ngân sách | **FAIL** (rc15: recall@5 29/60, 3 bỏ sót không báo) | [LONG_FORM_EVIDENCE_REPORT.md](LONG_FORM_EVIDENCE_REPORT.md); #784, W3, #802 |
| 3 · Đồng bộ A/V từng chỗ nối (C) | Mỗi chỗ nối ≤ max(40 ms, 1 khung); cộng dồn ≤ 80 ms; đo từ bản giải mã; có cả media tổng hợp và footage thật | **PASS** cho Tổng hợp (rc15: 28/28 đo được, tệ nhất 33,3 ms); phim tài liệu chưa đo | [AV_BOUNDARY_SYNC_REPORT.md](AV_BOUNDARY_SYNC_REPORT.md); #786, #799, W4 |
| 4 · Người thật (D) | Ít nhất một phiên thật theo giao thức | **BLOCKED** (không có người tham gia) | [HUMAN_EDITORIAL_VALIDATION_REPORT.md](HUMAN_EDITORIAL_VALIDATION_REPORT.md) |
| 5 · Thiết kế tích hợp | Ánh xạ hợp đồng §3, đủ trường §4, Kiểm tra cuối §5, kế hoạch test §6, không cần engine mới | **PASS (thiết kế)** | tài liệu này |

Cổng 5 PASS chỉ nghĩa là thiết kế đủ để bắt đầu viết code khi bốn cổng kia đạt. Nó không nghĩa là đã có code.

## 3. Ánh xạ hợp đồng

Phim tài liệu (`engine/dubflow/docu/`, ADR-0049) đi riêng khỏi bộ lập kế hoạch chung (ADR-0052). Pilot **không** thay `docu/matcher`. Nó thêm một **bộ chuyển đổi** từ kết quả của matcher sang nhật ký biên tập `editorial-v1` mà hàng đợi, bảng Duyệt cảnh và Kiểm tra cuối đã đọc được.

| Phim tài liệu | Hợp đồng biên tập chung | Ghi chú |
|---|---|---|
| cảnh của kịch bản (`Scene.scene_id`) | **part** (`review-<scene>.json`) | như một phần của Tổng hợp |
| câu lời dẫn trong cảnh | **unit** (`<scene>.u<n>`) | thời điểm câu lấy từ phụ đề của lời dẫn đã đọc (`narration`), không từ ước lượng |
| trích dẫn của cảnh (`Ref`: nguồn, start, end) | **evidence locator** (`citation`: `source_id`, `start_ms`, `end_ms`, `script_sha256`) | trích dẫn là chứng cứ kịch bản đưa ra, không phải bằng chứng hình |
| khoảng nguồn của lát cắt (`Slice`: nguồn, start, end) | **candidate range** (`pieces`: shot id, offset, length) | một lát cắt nằm trong một cú máy (Tier-3), nên ánh xạ được sang một shot id |
| tầng khớp (`CITED`, `AROUND`, `SAME_SOURCES`, `ANY_SOURCE`, `REUSED`) | **selection provenance** (`tier`, `reused`, `crossed`) | ghi nguyên, không đổi thành điểm tin cậy |
| lát cắt chèn (bản đồ, infographic) | unit có `kind: generated` | không bao giờ tính là footage |

Hệ quả:
- quyết định của người (APPROVE, PIN, EXCLUDE, REPLACE, REVIEW_LATER, MARK_SOURCE_MISSING, UNDO, INVALIDATED) dùng y nguyên schema v2;
- đổi kế hoạch cục bộ (#782) áp dụng nguyên: một câu đã duyệt giữ lát cắt của nó (`Edits.keep`), và chỉ bị huỷ khi có nguyên nhân thật;
- matcher cần nhận thêm hai đầu vào từ bộ chuyển đổi: các khoảng bị khoá (đã duyệt, đã ghim) và các khoảng bị loại. Đó là tham số, không phải engine mới.

## 4. Trường bắt buộc của mỗi unit

| Trường | Nguồn | Vì sao |
|---|---|---|
| `unit_id`, `part_id`, `text`, `start_ms`, `end_ms` | kịch bản + phụ đề lời dẫn | định danh và vị trí |
| `citation[]` | `Ref` của cảnh | người duyệt thấy kịch bản trích gì |
| `pieces[]`, `tier`, `reused`, `crossed` | `Slice` | biết hình đến từ đâu, có phải tìm xa không |
| `claims[]`: số, ngày, tên riêng, nhân quả | `units.py`, `entities` | quyết định câu có phải Nghiêm trọng không |
| `numbers_checked` | `units.py` | con số đã khớp đại lượng nguồn hay chưa |
| `credit`, `origin_url` của mỗi nguồn | `project` | quyền sử dụng |
| `basis` | băm lời câu + vật liệu (như #782) | phát hiện câu đổi hoặc nguồn đổi |
| **`claim_verified`** | người duyệt | lời dẫn nói đúng sự thật (đối chiếu nguồn) |
| **`visual_reviewed`** | người duyệt | hình phù hợp với lời |

`claim_verified` và `visual_reviewed` là **hai trường riêng**:
- duyệt hình không bao giờ đặt `claim_verified`;
- xác nhận lời không bao giờ đặt `visual_reviewed`;
- không trường nào do AI đặt.

## 5. Kiểm tra cuối cho phim tài liệu

Thêm vào các kiểm tra chung của `final_qc.py` (render, timeline, duration, captions, audio, pending, decisions, claims):

| Kiểm tra | Đạt khi | Không đo được thì |
|---|---|---|
| `claims_verified` | mọi câu có số, ngày, tên riêng hoặc nhân quả đều có `claim_verified` | Chưa đo, không đạt |
| `visuals_reviewed` | mọi câu Nghiêm trọng đều có `visual_reviewed` (hoặc MARK_SOURCE_MISSING kèm lời đã sửa) | Chưa đo, không đạt |
| `numbers` | `units.py` chấp nhận mọi con số còn trong lời cuối | Chưa đo, không đạt |
| `credits` | mọi nguồn có hình trong bản dựng có credit và origin; credit in trong video khớp danh sách nguồn đã dùng | Chưa đo, không đạt |
| `reuse` | không khoảng nguồn nào chiếu hai lần mà không có `reused` | Chưa đo, không đạt |
| `sync` | `sync_probe` trên bản cuối: mỗi chỗ nối lát cắt ≤ max(40 ms, 1 khung), cộng dồn ≤ 80 ms | Chưa đo, không đạt |
| `stale` | bản cuối trỏ tới revision kế hoạch hiện tại (sha256 như Tổng hợp) | Chưa đo, không đạt |

Như #774, "Chấp nhận các câu chưa duyệt" không gỡ được `claims_verified`, `visuals_reviewed` hay `numbers`.

## 6. Kế hoạch test khi bắt đầu code (không làm trong đợt này)

1. **Bộ chuyển đổi**: một kịch bản 3 cảnh, 8 câu, 3 nguồn. Kiểm ánh xạ §3 và đủ trường §4. Kiểm rằng mỗi tầng giữ nguyên.
2. **Quyết định**: chạy bộ ngẫu nhiên A5 của #782 trên nhật ký phim tài liệu. Yêu cầu 0 huỷ tránh được và 0 thay đổi im lặng.
3. **Kiểm tra cuối**: mỗi kiểm tra §5 có một ca đạt, một ca không đạt và một ca "Chưa đo".
4. **Đồng bộ**: `docu/roughcut` nối lát cắt chỉ có hình bằng concat demuxer rồi ghép lời dẫn liền một dải. Lệch tích luỹ theo số lát cắt chưa được đo. Ca đầu tiên là một bản dựng tổng hợp 40 lát cắt đo bằng `sync_probe.measure_markers`. Nếu lệch, sửa theo cách của #786 (một lưới khung).
5. **Windows**: hai phim 10–15 phút (một bộ nguồn cũ, một bộ mới), qua UI thật trên bản candidate, nhãn sự thật khoá bằng hash trước khi chạy.

## 7. Ngoài phạm vi

Các việc sau không thuộc pilot:
- Documentary Auto;
- khớp ngữ nghĩa bằng model mới;
- tự viết lại lời dẫn;
- giao diện timeline;
- đổi `docu/matcher` ngoài hai tham số khoá/loại ở §3.

Các việc này nằm trong [PHASE5_BACKLOG.md](PHASE5_BACKLOG.md).
