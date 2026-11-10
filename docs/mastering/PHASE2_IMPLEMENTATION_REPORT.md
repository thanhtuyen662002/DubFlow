# DubFlow — Báo cáo triển khai Phase 2 (Content-Aware Editing)

Epic #637 · ngày 2026-11-10 · main tại thời điểm viết: `15a188f`
Phạm vi: `product_showcase` (Bán hàng) và `compilation_entertainment` (Tổng hợp). Không phát hành bản stable nào trong Phase 2.

## Trả lời thẳng: Content-Aware Editing đã hoạt động đến mức nào?

**Đã hoạt động thật, nhưng chưa đủ tin cậy để chạy mà không cần người xem lại.**

- Khi có bằng chứng nội dung, máy chọn cảnh theo **nội dung**: AI nhìn hình mô tả từng khung, ghi chú của người dùng, hoặc phụ đề nguồn. Lý do chọn ghi rõ từ khớp, ví dụ *khớp lời “đồng hồ áp suất”*. Đây không phải chọn theo tín hiệu (cắt cảnh, độ nét, chuyển động) rồi gọi là "theo nội dung". Kế hoạch ghi rõ `mode: content` hay `mode: signals_only`. Ở `signals_only`, độ tin cậy bị chặn ở mức thấp và mọi đoạn bị đánh dấu cần xem lại.
- Độ chính xác câu ↔ cảnh, đo bằng khung giữa mỗi câu trên video thật (Bán hàng, 6 câu, Windows):

  | Lần | main | Không ghim | Có ghim 1 câu |
  |---|---|---|---|
  | 2 | d02af35 | 2/6 | ~4/6 |
  | 3 | 6a80f49 | 3/6 + ½ | 2/6 + ½ |
  | 4 | 80ae83b | 2/6 + ½ | 3/6 |
  | 5 | 6ecf277 | **4/6 + 2×½** | **5/6 + ½** |
  | 6 | 44124b0 | 4/6 + 2×½ | 5/6 + ½ |
  | 7 | 15a188f | **5/6 + ½** | **5/6 + ½** |

- Ở lần xác nhận cuối (lần 7): Bán hàng đúng 5/6 câu + 1 câu một nửa, cả khi không ghim. Top 5 và bộ dài 36 phút chạy hết, 0 khung đứng hình, 0 mẩu dưới 0,5 s. Chạy lại không đổi thì kế hoạch y hệt và AI không phải hỏi lại.
- 8 trên 13 đoạn Bán hàng vẫn bị đánh dấu "cần xem lại", vì độ tin cậy 0,44–0,69 thấp hơn ngưỡng. Kết luận: máy dựng được bản nháp có ý nghĩa; người dùng vẫn cần xem lại và ghim vài cảnh.
- Tổng hợp (Top 5 từ footage chưa lồng tiếng) chạy hết từ đầu đến cuối và ra video có thứ hạng, tiêu đề, phụ đề, cảnh giữa mỗi mục khớp nội dung. Ở lần 5, 4/5 mục kết bằng khoảnh khắc âm thanh nguồn.
- **Khi không có khóa AI và không có ghi chú/phụ đề**, hệ thống chỉ còn chọn theo tín hiệu. Báo cáo, kế hoạch và giao diện đều ghi rõ điều đó.

## 1. Trạng thái #647

PR #647 (Phase 1: chia phần Bán hàng theo cảnh, sau tùy chọn mastering) **đã merge** (88e3cd9). Phase 1 được đóng trong Phase 2 bằng #650:
- dấu vân nội dung (content fingerprint) cho cache phân tích và kế hoạch đã lưu;
- vô hiệu kế hoạch khi nguồn, kịch bản, tùy chọn hoặc phiên bản công thức thay đổi;
- test timebase.

## 2. ADR

- **ADR-0053 – Tổng hợp từ footage chưa lồng tiếng.** Hai đường trong một job: `schema_version: 1` giữ nguyên đường `final_vi.mp4` cũ đến từng byte; `schema_version: 2` là đường footage thô, gồm chương trình, kế hoạch theo phần, montage và khoảnh khắc nguồn.
- **ADR-0054 – Lớp biên tập dùng chung.** Gồm ứng viên cảnh, bằng chứng nội dung có nguồn gốc và độ tin cậy, bộ mô tả hình tùy chọn qua AI của người dùng, và ghép kịch bản ↔ cảnh. Không có bằng chứng nội dung thì ghi `signals_only`, không giả là theo nội dung. Đánh lại số: dubbing mastering thành ADR-0055, preset tường thuật phim tài liệu thành ADR-0056.

## 3. Issue / PR / commit

| PR | Issue | Commit | Nội dung |
|---|---|---|---|
| #650 | #649 | c87ab1a | Content fingerprint cho cache và kế hoạch, vô hiệu hóa, test timebase |
| #652 | #651 | c600bec | ADR-0053, ADR-0054 |
| #654 | #653 | 28fc619 | Lớp biên tập: candidates, evidence, vision adapter, align |
| #656 | #655 | 63f312e | Bán hàng: mỗi câu minh họa bằng cảnh khớp; clip một cảnh được phân tích |
| #658 | #657 | 50ddc7d | Tổng hợp từ footage chưa lồng tiếng (chương trình v2, kế hoạch theo phần, montage) |
| #660 | #659 | 650b092 | Gemini: schema dùng một kiểu + nullable (lần 1: AI xem 0 khung) |
| #662 | #661 | d02af35 | Sửa lỗi lần 1: WebM, render lại, nháp AI, khoảnh khắc nguồn, khớp tiếng Việt |
| #664 | #663 | 6a80f49 | Khớp theo lời: thang điểm liên quan, mô tả AI không dấu, khung đứng hình |
| #666 | #665 | 80ae83b | Thang confidence của AI, lô bị cắt, khóa render lại, mẩu vụn, cảnh lặp |
| #668 | #667 | 6ecf277 | Cảnh khớp nhất được dùng dù ngắn; cảnh để dành cho câu khớp hơn |
| #670 | #669 | 44124b0 | Cảnh ngắn không làm câu sau hết footage; claim 50 %; hạn mức AI tính cả cache |
| #672 | #671 | 15a188f | Chỉ tiến: mọi cảnh không làm câu sau hết footage; hook để dành cảnh cho câu sau |
| (PR này) | — | — | Báo cáo này |

Khoảng 4 000 dòng thêm, 45 dòng xóa (88e3cd9..main, chưa tính báo cáo).

## 4. Module mới

- `engine/dubflow/mastering/candidates.py`: ứng viên cảnh từ phân tích Phase 1, gồm chất lượng (độ sáng, độ nét, chi tiết), dhash, keyframe, nhóm trùng lặp.
- `engine/dubflow/mastering/evidence.py`: bằng chứng có nguồn gốc (`vision`, `note`, `transcript`, `signal`) và độ tin cậy; tách từ tiếng Việt có dấu và không dấu (khớp "lỏng" `~`); nhãn đóng.
- `engine/dubflow/mastering/vision.py`: bộ mô tả hình qua AI của người dùng (Gemini đã kiểm chứng):
  - prompt `vision-v3`;
  - kiểm tra từng câu trả lời; tự đổi thang 0–100 hoặc 0–1 sang 0–1000;
  - lô 4 khung; lô bị cắt thì hỏi lại chia đôi;
  - cache theo nội dung khung + model + prompt; hạn mức khung.
- `engine/dubflow/mastering/align.py`: chia kịch bản thành câu, chấm điểm (idf, liên quan theo câu, phù hợp chức năng câu, chất lượng), gán cảnh. Có chế độ chỉ tiến (giữ thứ tự nguồn); chống lặp, chống mẩu vụn, chống bỏ trống câu sau; ghim/loại cảnh; nhật ký quyết định với lý do và phương án thay thế.
- `engine/dubflow/mastering/planners/compilation.py`: kế hoạch từng phần của Tổng hợp (hook, intro, mục, outro): khoảnh khắc nguồn chọn trước, lời bình phủ phần trước đó.
- `engine/dubflow/compilation/program.py`, `engine/dubflow/compilation/raw.py`: chương trình v2, nháp lời bình AI (cache `ai-answers.json`), remux WebM, receipt theo phần (chỉ render lại phần đổi).
- `contracts/mastering/editorial-v1.json`: hợp đồng dữ liệu biên tập.
- Test mới: `tests/mastering/test_editorial.py`, `test_product_editorial.py`, `test_compilation_raw.py`, `test_invalidation.py`, `test_timebase.py`.

## 5. Module dùng lại / mở rộng

- `mastering/plan.py` (ADR-0052): thêm vai trò segment, `speech_refs`, `needs_review`, lý do, provenance.
- `mastering/analysis.py` (Phase 1): một lượt FFmpeg cho cắt cảnh, chuyển động, im lặng, có cache. Dùng lại nguyên; thêm keyframe.
- `mastering/planners/product.py`: từ chia phần theo cảnh (Phase 1) thành minh họa từng câu.
- `product/job.py`, `product/render.py`, `product/__main__.py`: gọi lớp biên tập, giữ renderer và cache cũ.
- `compilation/job.py`, `compilation/render.py`, `compilation/layout.py`: renderer cũ, thêm montage và still.
- `ai/client.py`: schema tương thích Gemini (nullable).
- `docu/shots.py`: dùng chung đo chất lượng khung.
- Đường cũ: Test C lần 1 so dòng lệnh FFmpeg của Bán hàng/Tổng hợp khi tắt mastering với main trước Phase 2: **giống hệt**.

## 6. Tính năng chạy được (đã kiểm trên video thật, Windows)

- Bán hàng:
  - mỗi câu kịch bản được minh họa bằng cảnh khớp nội dung, có lý do và độ tin cậy;
  - ghim/loại cảnh;
  - sửa một câu thì AI không phải nhìn lại (asked 0);
  - chạy lại y hệt: dùng lại kế hoạch, 1 lần encode.
- Tổng hợp Top 5 từ footage thô (MP4 + WebM AV1/Opus), gồm:
  - hook lấy từ phần sau;
  - thẻ hạng #5→#1, tiêu đề, thanh chương trình, phụ đề;
  - lời bình AI viết nháp cho mục thiếu lời, có cảnh báo;
  - mỗi mục kết bằng khoảnh khắc âm thanh nguồn (4/5; mục còn lại không có tiếng);
  - chạy lại không đổi: 0/8 phần render lại; đổi lời 1 mục: 1/8.
- AI nhìn hình qua khóa Gemini của người dùng (Credential Manager). Khóa không bao giờ bị in hay ghi: mỗi lần quét 0 lần xuất hiện, lần 5 quét 13 820 file.
- Đường cũ `final_vi.mp4` (schema 1) không đổi.

## 7. Tính năng thử nghiệm

- Độ tin cậy và ngưỡng "cần xem lại": hiện phần lớn đoạn vẫn bị đánh dấu.
- Phát hiện "nhiều sản phẩm" theo tên sản phẩm AI trả về.
- Khoảnh khắc nguồn chọn theo âm lượng/chuyển động/hook. Chưa nhận diện được "đoạn cao trào" theo nghĩa.
- Bộ dài (36 phút, nhiều nguồn): chạy được, nhưng hạn mức 80 khung nghĩa là phần lớn cảnh chọn theo tín hiệu.

## 8. E2E Bán hàng (Windows, máy của Oscar, runtime 1.1.95, CPU)

Máy pha espresso, 4 clip, kịch bản 6 câu, mastering balanced, AI Gemini, 25 khung.

Lần 5 (6ecf277), không ghim, 13 đoạn:
- u1 hook: tay cầm, TAMP, REDISTRIBUTE, cà phê chảy. Đúng, nhưng lấy mất cảnh nén của u3 (đã giảm ở #670/#672; lần 7 vẫn còn, xem mục 14).
- u2 crema: đúng, độ tin cậy 0,694.
- u3 xay: GRIND, độ tin cậy 0,904. Đúng.
- u4 đồng hồ áp suất: cảnh 1,18 s, độ tin cậy 0,877, sau đó crema. Đúng một nửa: chỉ phủ 1/3 câu.
- u5 espresso xuống tách: đúng.
- u6 lời kêu gọi: ly crema. Đúng một nửa.
- Không mẩu nào dưới 0,5 s; không cảnh báo sai.
- Có ghim u2 + sửa câu 6: 5/6 + ½.

Lần 6 (44124b0): mọi cảnh và độ tin cậy giống hệt lần 5; AI hỏi mới 0 khung (25/25 từ cache).

Lần 7 (15a188f), lần xác nhận cuối:
- Không ghim: **5/6 + ½**. u6 nay đúng (cà phê chảy vào ly + crema). u4 vẫn một nửa.
- Hook vẫn dùng cảnh TAMP/REDISTRIBUTE. Khung hình đúng là tay cầm, nên không sai nội dung, nhưng u3 mất cảnh nén. Lý do: hai cảnh này dưới 50 % điểm cảnh tốt nhất của u3 (GRIND), nên không được coi là cảnh "của" u3.
- Có ghim u2 + sửa câu 6: 5/6 + ½.
- 0 mẩu dưới 0,5 s (ngắn nhất 0,603 s).

Lịch sử sửa: AI xem 0 khung (#660) → thang liên quan sai (#664) → AI trả confidence thang 0–100 (#666) → cảnh khớp nhất ngắn hơn 2 s bị loại (#668) → câu đầu lấy cảnh của câu sau (#668, #670, #672; còn một phần).

## 9. E2E Tổng hợp

Top 5 từ 4 nguồn (có 1 WebM AV1/Opus), mục #4 do AI viết nháp lời, 48 khung AI.

Lần 5:
- COMPLETED 8/8 phần, video 76,6 s;
- 0 khung đứng hình, 0 mẩu dưới 0,5 s;
- một cảnh dùng tối đa 2 lần (lần 3: 4 lần), ở intro (trùng với mục #4 và #1);
- khung trích ra đúng thứ hạng, tiêu đề, phụ đề.

Bộ dài 36 phút (nhiều nguồn, 80 khung AI):
- lần 4: 0 khung đứng hình;
- lần 5: 2–3 khung đứng hình. Đây là thoái lui do #668; sửa ở #670 và #672.

Lần 6–7:
- Top 5 giống lần 5 từng segment: 0 khung đứng hình, 0 mẩu dưới 0,5 s, chạy lại không đổi 0/8 phần.
- Bộ dài: lần 6 còn 2 khung đứng hình + 1 mẩu 0,185 s, do câu 1 nhảy tiến quá xa; đã sửa ở #672.
- **Lần 7: bộ dài COMPLETED, 0 khung đứng hình, 0 mẩu dưới 0,5 s** (ngắn nhất 0,56 s); AI hỏi mới 0 khung.
- Còn lại: câu 2 Why We Fight ("Bản đồ … Kavkaz") nay chạy cảnh thật, nhưng là thẻ chữ và kỵ binh, không có bản đồ. Câu này chưa khớp nội dung: phần nguồn trước khoảnh khắc đã chọn không có bản đồ trong 80 khung AI đã xem.

## 10. Bằng chứng ghép kịch bản ↔ footage

- Mỗi segment trong kế hoạch có `speech_refs` (câu nào), lý do (ví dụ *khớp lời “đồng hồ áp suất” (vision)*, *cảnh cận sản phẩm hợp câu feature*), độ tin cậy, `needs_review`, và 3 phương án thay thế kèm điểm.
- Lần 4: điểm nội dung đứng đầu đã đúng cảnh (u3→GRIND 776, u4→đồng hồ áp suất 682, u5→espresso 745). Bộ chọn cảnh khi đó không lấy được các cảnh này vì chúng ngắn hơn 2 s (#668 sửa).
- Khung giữa mỗi câu được trích ra và chấm tay mỗi lần (bảng ở đầu báo cáo).
- Test đơn vị dựng lại từng lỗi thật, mỗi test đều fail trước khi sửa:
  - cảnh mềm nhưng đúng thắng cảnh nét nhưng sai;
  - mô tả không dấu vẫn khớp;
  - cảnh khớp nhất 1,18 s;
  - hook không lấy cảnh của câu sau;
  - cảnh ngắn không làm câu sau thiếu footage.

## 11. Kết quả test

- Linux (CI và máy cloud, FFmpeg LGPL 9.0.2): tests/mastering 107, product 40, compilation 9, ai 28, docu 152, đều OK. CI `governance` và `integration` xanh trên mọi PR.
- Windows (runtime 1.1.95 trên máy Oscar), lần 5: mastering 103, product 40, compilation 9, ai 28, docu 152, đều OK. Lần 7 (15a188f): mastering 107, product 40, compilation 9, ai 28, docu 152, đều OK.

## 12. Thời gian, RAM, số lần encode (Windows, CPU, `--cpu-only`)

| Lần chạy | Lần 4 | Lần 5 | Lần 7 (cache AI) |
|---|---|---|---|
| Bán hàng lần đầu (AI xem hình) | 83,0 s / 2,80 GB | 35,0 s / 3,31 GB (AI từ cache) | 25,9 s / 3,32 GB, AI hỏi 0 |
| Bán hàng chạy lại y hệt | 20,3 s / 2,82 GB, 1 encode | 20,3 s / 3,30 GB, 1 encode | 20,8 s / 3,34 GB, 1 encode |
| Bán hàng sửa 1 câu + ghim | 21,8 s / 3,02 GB | 21,3 s / 3,47 GB | 20,3 s / 3,47 GB |
| Top 5 lần đầu | 172,8 s / 2,85 GB | 76,7 s / 2,83 GB | 70,0 s / 2,76 GB |
| Top 5 chạy lại không đổi | 37,0 s / 2,16 GB, 0/8 phần | 37,6 s / 2,21 GB, 0/8 phần | 34,5 s / 2,20 GB, 0/8 phần |
| Top 5 đổi lời 1 mục | 40,6 s, 1/8 phần | 41,6 s, 1/8 phần | 40,1 s, 1/8 phần (lần 6) |
| Bộ dài 36 phút | 226,9 s / 2,67 GB | 176,8 s lạnh / 2,82 GB | 76,4 s / 2,73 GB (lạnh, lần 6: 233,4 s / 2,83 GB; chạy lại có cache: 17,7 s) |

Lần 1 (trước sửa lỗi): Bán hàng 26/53 s, 3,2 GB; Top 5 71/117 s; bộ dài 85/180 s. Lúc đó AI chưa xem được khung nào, nên nhanh hơn.

## 13. Rủi ro

- **Độ chính xác phụ thuộc lần hỏi AI đầu tiên**: câu trả lời AI thay đổi theo lần hỏi. Từ #670, chạy lại dùng đúng các khung đã cache, nên kế hoạch lặp lại y hệt (lần 6 và 7 đã kiểm).
- Phụ thuộc AI nhìn hình: không có khóa thì chỉ còn chọn theo tín hiệu; mỗi lần dùng tốn quota của người dùng.
- Hạn mức khung (25–80) so với video dài: phần lớn cảnh của bộ dài không được AI xem.
- Thang điểm và ngưỡng đã được chỉnh trên một bộ Bán hàng và một bộ Top 5. Có nguy cơ khớp quá sát hai bộ này (overfit).
- Mỗi sửa lỗi bộ chọn cảnh có thể gây thoái lui ở bộ khác (#668 → khung đứng hình ở bộ dài). Cần một bộ hồi quy E2E cố định.
- Chỉ kiểm trên CPU. Chưa chạy trên máy có GPU NVIDIA.

## 14. Việc chưa xong

- Chạy lại vẫn đọc lại giọng (~14 s Bán hàng, ~29 s Top 5), và video ghép cuối vẫn encode lại. Cần cache TTS theo câu.
- Hook Bán hàng còn dùng cảnh nén/rải bột mà câu u3 cũng có thể dùng (vì dưới ngưỡng 50 % của u3).
- Câu 2 Why We Fight (bộ dài) không có cảnh bản đồ: AI chỉ xem 80 khung của 36 phút.
- Intro Top 5 còn trùng cảnh với mục #1/#4 (tối đa 2 lần).
- Cảnh khớp ngắn chỉ phủ một phần câu; phần còn lại là cảnh khác.
- Chưa có cân màu giữa các nguồn.
- Chưa có OCR (chữ trên sản phẩm), chưa có ASR cho nguồn không phụ đề.
- Chưa có overlay tên sản phẩm/điểm nổi bật.
- Chưa có giao diện desktop cho xem lại, ghim và loại cảnh (Phase 5). Hiện chỉ dùng qua spec và CLI/MCP.
- `ai-answers.json` và cache AI nằm trong thư mục output, nên đổi thư mục output thì phải hỏi lại AI.
- Issue #649, #651, #653, #655, #657 được đóng cùng báo cáo này.

## 15. Đề xuất Phase 3

1. Bộ hồi quy E2E cố định (Bán hàng, Top 5, bộ dài) chạy trên Windows với cache AI cố định, chấm khung giữa câu tự động bằng nhãn chuẩn.
2. Cache TTS theo câu và bỏ encode lại video ghép khi không đổi.
3. Phủ trọn câu: khi cảnh khớp nhất ngắn, ưu tiên cảnh cùng nhóm nội dung cho phần còn lại.
4. OCR và ASR làm thêm nguồn bằng chứng; AI nhìn nhiều khung hơn mỗi cảnh dài.
5. Giao diện xem lại: hiện lý do, độ tin cậy và phương án thay thế; ghim bằng một lần bấm (Phase 5).
6. Cân màu giữa nguồn; overlay tên sản phẩm.
7. Hiệu chỉnh ngưỡng "cần xem lại" trên nhiều bộ hơn trước khi bật mặc định.
