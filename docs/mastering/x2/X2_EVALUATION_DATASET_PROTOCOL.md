# Giao thức dữ liệu REAL-DEV / REAL-HOLDOUT cho X2

Epic #637 · 2026-11-14 · chỉ thị "SAFETY TRIAGE & X2 EVALUATION READINESS". **Trạng thái: HUMAN LABELING PENDING.** Chưa có nguồn nào được chốt, chưa có câu nào được viết, chưa có nhãn nào. Tài liệu này chỉ là giao thức và mẫu manifest.

## 1. Hai tập tách biệt

| | REAL-DEV | REAL-HOLDOUT |
|---|---|---|
| Kích thước | 20–30 câu | ≥ 100 câu (mục tiêu 120 để còn dư sau khi loại câu tranh chấp) |
| Nguồn | 2 nguồn | 4–6 nguồn, **không trùng** nguồn của DEV (tách theo video, không chỉ tách câu) |
| Thể loại | 1 có thoại + 1 ít thoại | ≥ 3 thể loại, không chỉ phim chiến tranh (§2) |
| Dùng để | Kiểm tra schema cue, prompt, harness, cách chấm; **được** phân tích lỗi và chỉnh thiết kế | Đo **một lần** sau khi mọi thứ đã khóa; **không** dùng để chọn threshold, prompt, cửa sổ cue hay heuristic xếp hạng |
| Nhãn | Người gắn; được sửa khi phát hiện sai, có ghi lại | Hai người gắn độc lập + phân xử; khóa trước khi đo |
| Khóa | Khóa mềm (ghi phiên bản) | sha256 cho manifest nguồn, lời dẫn, nhãn, quy tắc chấm, code đánh giá (commit) — **trước** phép đo cuối |

W3 và Gate S REAL-DEV vẫn chỉ là dữ liệu dev cũ. Không đưa chúng vào bất kỳ tập nào ở đây.

## 2. Thành phần nguồn (đề xuất, chờ xác minh quyền)

| Nhóm | Vì sao cần | Gợi ý nguồn (phải xác minh giấy phép từng file) |
|---|---|---|
| Video sản phẩm / review ngắn có thoại (zh hoặc vi) | Gần nhất với người dùng Bán hàng / Tổng hợp | Nguồn chủ dự án đã có quyền dùng, chỉ giữ trên máy, không đưa vào repo |
| Tư liệu / tin có giọng đọc (en) | Lời nói gần với hình, có tên riêng và số | Kho phạm vi công cộng (ví dụ tác phẩm của cơ quan chính phủ Mỹ, kho Prelinger) |
| Vlog / hướng dẫn có người nói trước máy | Lời nói **không** mô tả hình (người nói, hình là mặt người) | Video có giấy phép CC-BY, ghi công đầy đủ |
| Footage gần như không lời (nhạc, cảnh thiên nhiên) | Kiểm tra đường không có transcript | Kho CC0 / phạm vi công cộng |

Mỗi nguồn ghi rõ: đường dẫn trên máy, sha256, thời lượng, ngôn ngữ, tỉ lệ có lời nói (đo bằng ASR local), giấy phép, người xác nhận quyền, ngày xác nhận. **Không** dùng nguồn chưa có người xác nhận quyền.

## 3. Thành phần câu (REAL-HOLDOUT)

| Loại câu | Tối thiểu | Ghi chú |
|---|---|---|
| Lời dẫn trực tiếp (có cảnh thể hiện đúng điều được nói) | 35 % | Tính cả câu có claim tên/số/chữ trên hình (gắn cờ `factual`) |
| Lời dẫn khái quát / bình luận | 20 % | Chấp nhận B-roll minh họa |
| B-roll minh họa (cảnh phù hợp nhưng không chứng minh) | 20 % | |
| Không có footage | **≥ 20 %** | Trong đó **≥ một nửa là hard negative**: gần giống cảnh có thật (cùng chủ đề, khác chi tiết: khác thành phố, khác sản phẩm, khác hành động) |

Một câu có thể mang nhiều yêu cầu. Nhãn tách hai trục: *phù hợp hình* (DIRECT / ILLUSTRATIVE / MISMATCH / NONE / UNKNOWN) và *xác minh claim* (có claim cần kiểm? claim có hiện trên hình?).

## 4. Lời dẫn theo đường sản phẩm

- Lời dẫn được tạo **như người dùng sẽ tạo**: từ tiêu đề, ghi chú nguồn và transcript (bước AI viết nháp của X2, **cần phê duyệt chi phí riêng**), hoặc do một người viết nhận đúng các đầu vào đó.
- Người viết **không** xem contact sheet, keyframe hay video trước khi viết.
- Câu không có footage (kể cả hard negative) do **một người khác** viết từ mô tả chủ đề, rồi được trộn vào. Họ chỉ biết danh sách nguồn ở mức tiêu đề.
- Lời dẫn khóa sha256 trước khi gắn nhãn.

## 5. Gắn nhãn

1. Hai người gắn độc lập. Người gắn không phải người viết lời dẫn và không xem kết quả bất kỳ phép chạy nào.
2. Với mỗi câu: đánh dấu **mọi** khoảng nguồn chấp nhận được (mốc đầu–cuối theo giây, độ phân giải ≤ 1 s, xem ở tốc độ bình thường), loại DIRECT/ILLUSTRATIVE; hoặc NONE; claim hiện hay không; UNKNOWN kèm lý do.
3. So khớp: hai nhãn đồng ý khi cùng loại và các khoảng chồng ≥ 50 %. Cohen's κ được báo cáo.
4. Phân xử: người thứ ba quyết các câu bất đồng. Câu không phân xử được bị loại khỏi holdout và **được đếm và báo cáo**.
5. Nhãn AI (nếu có, chỉ ở DEV) phải ghi `AI-judged` và không bao giờ được gọi là human-verified.

**Khối lượng ước tính** (khoảng 3 phút mỗi câu mỗi người, tính cả tìm mọi khoảng chấp nhận được): 120 câu × 2 người × 3 phút ≈ **12 giờ**, cộng phân xử khoảng 20 % ≈ 2 giờ, cộng DEV 30 câu ≈ 1,5 giờ. Tổng ≈ **15–16 giờ công**. Ước tính cũ (2–4 giờ) là quá thấp.

Đây là công việc dựng benchmark, cần người kiểm độc lập. Chủ dự án không nên tự làm việc này như một thao tác dùng DubFlow.

## 6. Mẫu manifest (giữ trên máy, không đưa vào repo)

```json
{
  "schema": "dubflow-x2-dataset-v1",
  "split": "REAL-HOLDOUT",
  "status": "HUMAN_LABELING_PENDING",
  "sources": [
    {"source_id": "h-s1", "path": "<máy kiểm thử>", "sha256": "...", "duration_ms": 0, "language": "zh",
     "genre": "product", "speech_ratio": null, "license": "...", "rights_confirmed_by": null, "rights_confirmed_at": null}
  ],
  "narration": {"sha256": null, "method": "ai-draft-with-cues | human-from-inputs", "locked_at": null},
  "sentences": [
    {"sentence_id": "h-001", "source_ids": ["h-s1"], "text_sha256": "...", "planned_type": "direct|general|illustrative|none",
     "hard_negative": false, "factual": false}
  ],
  "labels": {"sha256": null, "labelers": 2, "adjudicated": 0, "excluded": 0, "kappa": null, "locked_at": null},
  "scoring_rules_commit": null
}
```

Repo chỉ giữ code harness, schema này và các con số tổng hợp. Không giữ lời dẫn, nhãn, khung hình hay câu trả lời Vision.

## 7. Việc con người phải làm trước khi dữ liệu hợp lệ

1. Chọn và **xác nhận quyền sử dụng** từng nguồn (chủ dự án hoặc người được giao).
2. Tìm 2–3 người gắn nhãn độc lập (không phải chủ dự án, không phải người viết lời dẫn) và một người phân xử.
3. Một người viết câu "không có footage" và hard negative.
4. Gắn nhãn, phân xử, ký xác nhận khóa nhãn.
5. Phê duyệt riêng ngân sách AI cho bước viết lời dẫn và Vision của X2 (xem `X2_POC_DESIGN_V2.md`).
