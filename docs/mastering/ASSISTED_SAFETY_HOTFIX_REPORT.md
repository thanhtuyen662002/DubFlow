# Sửa an toàn: không READY trên cảnh sai (#802, Workstream A)

Epic #637 · 2026-11-13 · chỉ thị "DUBFLOW — ASSISTED SAFETY HOTFIX & LONG-FORM RETRIEVAL FEASIBILITY" · PR #808 (merge `3ae4e6f`), bổ sung PR #813 (issue #812, merge `dd492e5`) · bản kiểm Windows **1.1.96-rc16** (`205e3ff`, candidate, `publish=false`).

## 1. Kết luận

**Gate A (#802 Semantic Safety): PASS.**

- Ba ca nghiêm trọng đã biết (i1.u2, i16.u1, i1.u1) không còn READY trên cảnh sai, cả khi replay bằng chứng cũ lẫn khi vision chạy mới trên Windows.
- Bỏ sót không báo trên bốn bộ bằng chứng rc12–rc15: **5 → 0**. Trên lần chạy mới rc16: **0/60**.
- READY precision trên rc16 (vision mới): **4/4**. Câu READY đúng có đủ bằng chứng vẫn READY; không có quy tắc "mọi câu đều cần duyệt".
- Giá phải trả (§6a, cùng bằng chứng): 3 câu READY đúng thành cần duyệt (rc12 i1.u1, rc13 và rc15 i9.u1); số câu phải duyệt tăng 0–3 mỗi bộ. Tỉ lệ phải duyệt trên rc16 là 56/60.

Gate A **không** làm Gate S đạt. Gate S (Documentary Auto) vẫn **FAIL**: trên Gate S thật, đầu ra trước và sau sửa giống từng byte (§7).

## 2. Nguyên nhân (#802)

Ba ca, tái hiện bằng replay tất định trên bằng chứng đã lưu của W3 (Why We Fight, nhãn khoá `afa979d2…f66497`). Replay chéo trên PC: gói rc13 cùng cache vision rc12 cho đúng lựa chọn của rc12. Vậy thay đổi planner giữa rc12 và rc13 không phải nguyên nhân; nguyên nhân là **câu trả lời vision đổi chữ giữa các lần chạy** (cache trống mỗi lần chạy mới), cộng với việc planner không kiểm điều câu nói khẳng định.

| Ca | Lời dẫn | Cảnh đúng | Cảnh được chọn (READY) | Vì sao READY | Loại lỗi |
|---|---|---|---|---|---|
| i1.u2 | "Dòng chữ lớn The Battle of Russia hiện lên trên nền huy hiệu đại bàng." | c100011 (thẻ tiêu đề) | rc13: c100017 (thẻ trích dẫn Stimson), 625 > 621, READY 736; rc15: c100017 lại được chọn, READY | vision rc13 tả thẻ tiêu đề là "phù hiệu chim" thay vì "huy hiệu"; một hoà sát nút bị lật; không gì kiểm tiêu đề có được viết trên cảnh không | xếp hạng sai + READY sai |
| i16.u1 | "Bản đồ có đường thẳng đánh dấu khoảng cách một nghìn năm trăm dặm." | c100509 (quả địa cầu, "1500 MILES") | rc13: c100505 (bản đồ Nam Tư), 640; rc15: c100518 ("500,000 SQUARE MILES"), 638 | số đọc bằng chữ không bao giờ khớp "1500 MILES"; các bản đồ chung từ "bản đồ / đánh dấu / dặm" vượt REVIEW_BELOW 600; ở rc15 quả địa cầu đúng còn bị coi là thẻ chữ | bằng chứng đúng nhưng không được hiểu + READY sai + mâu thuẫn số bị bỏ qua |
| i1.u1 | "Một tấm thẻ chữ trắng trên nền xám cho biết bộ phim có giá trị lịch sử." | c100002 ("This film is considered of historical value") | rc15: c100004 (thẻ tối "historic past/traditions"), 624 | c100002 không được nhìn ở rc15 (rc12 nhìn, vision 950, chọn đúng); không gì kiểm điều tấm thẻ nói | thiếu bằng chứng (R3) + READY sai |

A3 (i1.u1) là ca thứ ba, chỉ thấy ở rc15. Nó được xử lý cùng #802 dù issue ban đầu chỉ nêu hai ca rc13.

## 3. Thay đổi code (PR #808)

Module mới `engine/dubflow/mastering/claims.py` đọc ba loại khẳng định trong một câu:

- **số lượng**: chữ số (1500 / 1.500 / 1,500) hoặc số tiếng Việt viết bằng chữ (một nghìn năm trăm), kèm đơn vị đứng sau (dặm/miles, dặm vuông/square miles, km, km², %, người…). Chỉ một dãy có hệ số mới là số, nên "hai người" hay "năm" (chỉ năm) không phải số lượng; "hai mươi năm" là 20.
- **cụm từ**: một tên nước ngoài đi sau một từ chỉ chữ viết, hoặc một chuỗi trong ngoặc kép. Phải khớp đúng thứ tự từ (cách nhau tối đa 2 từ).
- **nội dung viết**: điều mà tấm thẻ hay màn hình được nói là ghi.

Bằng chứng **nội dung** của cảnh (chỉ vision và OCR; tên file và lời thoại không bao giờ là bằng chứng) xác nhận, mâu thuẫn (cùng đơn vị, khác giá trị) hoặc để chưa xác nhận từng khẳng định. Trong `align.py`:

- Cảnh xác nhận một khẳng định được +150 (+300 nếu câu nói về thứ hiển thị) và được chấp nhận cho câu. Khi cảnh cho thấy mọi khẳng định hiển thị, độ tin cậy dùng mức khớp đầy đủ, vẫn bị chặn bởi độ tin cậy của nguồn bằng chứng.
- Cảnh mâu thuẫn bị −300 và không bao giờ được nhận là minh hoạ của câu.
- Câu nói về thứ hiển thị mà cảnh được chọn không cho thấy điều câu nói, hoặc có cảnh mâu thuẫn một con số, thành NEEDS_REVIEW với ghi chú nêu điều còn thiếu. Nhật ký có `signals.claims`.
- Câu có số viết bằng chữ chỉ được coi là "nói về chữ" khi nó nói về thứ hiển thị.
- Ghim là lựa chọn của người dùng, không bị kiểm. Ngoại lệ (sau khi gộp #805): một ghim đã bị loại trừ bỏ ra thì không còn là lựa chọn của người dùng.
- Bản ghi cache vision có `made_by`: prompt, provider, model được hỏi, model đã trả lời.
- Recipe: `product-editorial-v18`, `compilation-program-v19`.

Không ngưỡng nào đổi; REVIEW_BELOW vẫn 600. Không thêm quy tắc "chênh điểm nhỏ thì duyệt": trên dữ liệu phát triển, quy tắc đó không tách đúng/sai tốt hơn việc kiểm khẳng định.

Sửa theo review:
- vòng 1 (10 điểm, 8 test fail trước và pass sau; hai điểm "câu nói về chữ" và hiệu năng không có test riêng): km², đếm người, lời thoại trong ngoặc, số nhỏ không đơn vị, ngày tháng, "biển", "2 tỷ", độ dài nhãn, câu nói về chữ, hiệu năng;
- Lead (3 điểm, 3 test): lời thoại không chứng minh khung hình; một số trần không chứng minh số lượng có đơn vị; tiêu đề phải đúng thứ tự từ;
- #812 (PR #813, 2 test, đã merge sau rc16): ghi chú duyệt liệt kê từ của tấm thẻ theo thứ tự abc ("giá lịch phim sử trị"); sửa thành thứ tự trong câu ("phim giá trị lịch sử"). Chỉ đổi cách hiển thị, không đổi điều được xác nhận.

### Tái lập bằng chứng (5.4)

Khoá cache vision là sha256 của: phiên bản prompt, provider, model, bộ nhãn và sha256 của **nội dung khung**. Cùng khung, cùng prompt, cùng model thì dùng lại câu trả lời; đổi bất kỳ thành phần nào thì hỏi lại. Replay trên bằng chứng đã lưu cho 22/22 kế hoạch giống bản đã lưu khi code không đổi (§6; [LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md §10](LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md)). Một lần chạy mới (cache trống) vẫn có thể nhận chữ khác: §6 đo đúng điều đó.

## 4. Chính sách READY trước và sau

| | Trước (`43cdc2c`) | Sau (`3ae4e6f`) |
|---|---|---|
| Điều kiện READY | độ tin cậy ≥ 600 từ khớp từ ngữ, không có tín hiệu rủi ro | như trước, **và** mọi khẳng định hiển thị (số + đơn vị, tiêu đề, nội dung thẻ) được bằng chứng nội dung của cảnh được chọn xác nhận |
| Mâu thuẫn số | không xét | cảnh mâu thuẫn: −300, không bao giờ được nhận; còn mâu thuẫn trong cảnh được chọn thì NEEDS_REVIEW |
| Từ chung ("bản đồ", "đánh dấu", "chim") | đủ để vượt ngưỡng | vẫn tính điểm, nhưng không xác nhận một khẳng định cụ thể |
| Câu không có khẳng định hiển thị | READY theo điểm | không đổi |
| Ghim | không kiểm | không kiểm (trừ ghim đã bị loại trừ) |

## 5. Test

`tests/mastering/test_semantic_claims.py` (41 test trên main sau #813; 39 trước đó). Mỗi nhóm fail trên head trước nó và pass sau.

| Yêu cầu | Test |
|---|---|
| A1 i1.u2 | `TitleTests`: thẻ có tiêu đề thắng; thiếu tiêu đề thì thẻ đại bàng khác không bao giờ READY; tiêu đề gần giống không phải tiêu đề |
| A2 i16.u1 | `MapTests`: bản đồ có khoảng cách thắng kể cả khi được tả là chữ; bản đồ số khác không bao giờ READY; bản đồ không có số không READY; hai bản đồ thì con số quyết định; các cách viết của số đều xác nhận |
| A3 i1.u1 | `StatedCardTests`: thẻ nói đúng điều câu nói thì READY; thẻ nói điều khác không bao giờ READY; một cảnh không phải tấm thẻ |
| A4 âm | số gần nhau khác nghĩa (`MapTests` 500,000 vs 1500; `LeadFindingsTests` số trần); hai tên gần giống (`test_a_similar_title_is_not_the_title`, `test_a_title_is_its_words_in_order`); hai bản đồ khác đối tượng (`test_two_maps_the_number_decides`); thẻ tiêu đề và cảnh tình huống (`test_a_scene_is_not_the_card`); mô tả dài nhiều từ chung (`test_a_long_generic_description_is_no_proof`); một cảnh tả bằng câu khác nhau (`test_rewordings_of_the_number_all_verify`); chênh điểm nhỏ (`test_small_margin_between_two_maps_without_the_number`); chênh lớn nhưng thiếu bằng chứng (`test_without_the_title_another_eagle_card_is_never_ready`); tên file / lời thoại không phải bằng chứng |
| A5 dương | khớp tiêu đề chính xác, số đã xác nhận, địa danh trên bản đồ (`test_a_named_place_on_a_map_is_ready`), cảnh thường vẫn READY (`test_a_plain_scene_is_ready_as_before`), một ứng viên vượt trội có đủ bằng chứng (`test_one_dominant_verified_candidate`), số không hiển thị trong câu thường không phải lý do duyệt |
| Cache | `CacheProvenanceTests`: câu trả lời có `made_by` và đọc lại giống hệt |

Toàn bộ `tests/mastering` pass: 385 test sau khi gộp #805, 387 sau #813.

## 6. Replay trên hai bộ bằng chứng vision và lần chạy mới

Mọi số trên 60 câu có footage của W3 (4 câu không có footage được gắn cờ ở mọi lần chạy, 4/4). "Phải duyệt" = NEEDS_REVIEW hoặc MATCH_NOT_FOUND. "Cảnh báo thừa" = phải duyệt nhưng cảnh được chọn đúng. "Bỏ sót không báo" = READY trên cảnh sai.

### 6a. Bằng chứng đã lưu (W1, cache)

Replay offline trên PC Windows bằng công cụ `tests/mastering/longform/w3.py` của worktree `205e3ff` (mã nguồn của rc16), trên bằng chứng đã lưu của bốn lần chạy. Phần này **không** chạy qua DubFlow.exe; phần qua DubFlow.exe là §6b và §8.

| Bộ | Kế hoạch giống bản lưu | recall@1/3/5 sau | Bỏ sót không báo (lưu → sau) | READY precision (lưu → sau) | Phải duyệt (lưu → sau) | Cảnh báo thừa (lưu → sau) |
|---|---|---|---|---|---|---|
| rc12 | 20/22 | 18/29/32 | 0/60 → 0/60 | 5/5 → 4/4 | 55/60 → 56/60 | 20/60 → 22/60 |
| rc13 | 19/22 | 22/31/33 | 2/60 → 0/60 | 5/7 → 5/5 | 53/60 → 55/60 | 21/60 → 23/60 |
| rc14 | 20/22 | 20/24/28 | 0/60 → 0/60 | 6/6 → 6/6 | 54/60 → 54/60 | 17/60 → 17/60 |
| rc15 | 19/22 | 23/28/30 (lưu 21/27/29) | 3/60 → 0/60 | 5/8 → 5/5 | 52/60 → 55/60 | 20/60 → 22/60 |

Chỉ các phần i1, i9, i16 đổi kế hoạch. Theo từng câu:
- rc13 và rc15 **i1.u2**: READY/sai → READY/đúng (2 READY đúng mới).
- rc13 và rc15 **i16.u1**: READY/sai → NEEDS_REVIEW/đúng.
- rc12 **i16.u1**: NEEDS_REVIEW/sai → NEEDS_REVIEW/đúng (cảnh đúng giờ được chọn nhưng câu vẫn cần duyệt).
- rc15 **i1.u1**: READY/sai → NEEDS_REVIEW/sai (cảnh đúng vẫn không được nhìn; giờ được báo).
- rc12 **i1.u1**: READY/đúng → NEEDS_REVIEW/đúng (cảnh báo thừa mới: mô tả tiếng Việt không nhắc lại chữ trên thẻ).
- rc13 và rc15 **i9.u1**: READY/đúng → NEEDS_REVIEW/đúng (cảnh báo thừa mới). Câu nói "193.000.000 người"; khung ghi "POPULATION 193,000,000", không có đơn vị người, nên chưa xác nhận theo luật đơn vị chặt. Vision rc15 còn đọc nhầm thành 93,000,000. **Không chỉnh** trên tập W3 (cấm lặp heuristic trên 60 câu); ghi backlog.

Tổng bốn bộ: bỏ sót không báo **5 → 0**; READY đúng mới **2**; READY đúng thành cần duyệt **3** (rc12 i1.u1, rc13 i9.u1, rc15 i9.u1). Cảnh báo thừa tăng 6 (20+21+17+20 → 22+23+17+22): 3 câu vừa nêu, cộng 3 câu i16.u1 nay chọn đúng nhưng vẫn cần duyệt.

Trên bộ rút gọn trong test (rc12–rc15, 8 cửa sổ × 3 câu): READY precision 7/12 → 8/8; bỏ sót không báo 5 → 0; chọn đúng 13/24 → 18/24; cảnh báo thừa 6 → 10. Đây là ca chẩn đoán, không phải phép đo độc lập.

### 6b. Vision chạy mới (rc16, cache trống, Windows)

| Câu | Cảnh chọn | Đúng? | Trạng thái |
|---|---|---|---|
| i1.u1 | c100004 | sai (c100002 không được nhìn) | NEEDS_REVIEW |
| i1.u2 | c100011 + c100017 | đúng | READY |
| i16.u1 | c100509 + c100511 | đúng | NEEDS_REVIEW |

Trên 60 câu có footage: READY **4/4** đúng (precision 100 %), phải duyệt **56/60** (93 %), cảnh báo thừa **20/60**, bỏ sót không báo **0/60**. rc15 (trước sửa): READY 5/8, phải duyệt 52/60, cảnh báo thừa 20/60, bỏ sót không báo 3/60.

Phần lớn câu phải duyệt là cảnh sai đã được báo đúng (36/60), do recall thấp (Gate B, xem [LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md](LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md)). Trên cùng bằng chứng, #802 thêm 0–3 câu phải duyệt mỗi bộ (§6a). Hai lần chạy mới rc15 và rc16 có câu trả lời vision khác nhau, nên không so trực tiếp được với nhau.

## 7. Hồi quy

- Bộ hồi quy tổng hợp (11 split: dev…dev5, holdout…holdout7, 297 câu): đầu ra giống từng byte với base `43cdc2c`.
- Gate S thật (`--replay`, real-dev và real-holdout): giống từng byte. Gate S vẫn **FAIL** (real-dev coverage 333/1000, READY precision 333/1000; real-holdout coverage 268/1000, READY precision 0; bỏ sót không báo 0).
- W4 trên rc16: xem [WHOLE_SHOT_EXCLUSION_REPORT.md §9](WHOLE_SHOT_EXCLUSION_REPORT.md) và [PHASE4_READINESS.md](PHASE4_READINESS.md).

## 8. Windows E2E (W1)

DubFlow.exe 1.1.96-rc16 (release run 43361381416, head `205e3ff`, zip sha256 `5945c88b…2840` = `release.sha256`, candidate, `production_qualified: false`), chạy trong sandbox riêng, CPU, có chứng minh tiến trình CDP ở hai lần mở app.

- Cache: bốn bộ bằng chứng rc12–rc15 replay offline trên PC bằng mã nguồn của rc16 (§6a), không qua DubFlow.exe. PASS.
- Vision mới: W3 chạy lại từ đầu qua UI của DubFlow.exe (§6b). PASS.
- Bảng Duyệt cảnh và hàng đợi trên lần chạy mới (không gọi AI thêm):
  - i1.u1: "Cần xem lại", ghi chú "chưa thấy trong cảnh điều lời câu nói là hiển thị (…): cần kiểm tra"; hàng đợi của i1 là "1 cần xem · 2 tuỳ chọn". Cảnh đang dùng sai nhưng đã được báo.
  - i1.u2: "Đạt", thẻ tiêu đề (đúng).
  - i16.u1: "Cần xem lại", cảnh "1500 MILES" (đúng); hàng đợi của i16 là "1 nghiêm trọng · 2 cần xem".
- Kiểm tra cuối: REVIEW_PENDING, "67 câu chưa duyệt". "Xuất bản cuối…" không xuất thẳng mà hỏi "Xuất bản cuối khi còn câu chưa duyệt? Còn 67 câu…"; chọn "Để tôi duyệt trước" thì không có bản cuối nào được ghi. Dòng tóm tắt khẳng định của Kiểm tra cuối không liệt kê i1.u1, dù câu này có một khẳng định chưa xác nhận và nằm trong 67 câu chưa duyệt (#817, P2-N27). i16.u1 không có trong dòng đó là đúng: khẳng định "1.500 dặm" đã được xác nhận; câu này chờ duyệt vì độ phủ 81 %.
- Không gọi phần tự động hoá CDP là người duyệt.

## 9. Gate A

| Điều kiện | Kết quả |
|---|---|
| 1. Không còn READY im lặng ở ba ca đã biết | ✓ (cache 4 bộ và vision mới) |
| 2. Test âm đạt | ✓ |
| 3. Test dương không bị chuyển hết sang NEEDS_REVIEW | ✓ (test dương READY; trên cùng bằng chứng, phải duyệt tăng 0–3 câu mỗi bộ, 3 câu READY đúng thành cần duyệt; vision mới READY 4/4) |
| 4. Replay bằng chứng ổn định | ✓ (22/22 khi code không đổi) |
| 5. Cache và provenance đúng | ✓ (`made_by`, khoá theo nội dung khung) |
| 6. Không hồi quy nghiêm trọng | ✓ (tổng hợp và Gate S giống từng byte) |
| 7. Windows candidate E2E đạt | ✓ (W1: vision mới, hàng đợi và Kiểm tra cuối qua DubFlow.exe; cache replay offline trên PC) |
| 8. Không tăng độ tin cậy giả, không sửa nhãn | ✓ |

**Gate A: PASS.** Gate S: **FAIL**, không đổi.

## 10. Backlog

- i9.u1: câu có số người, khung chỉ có số trần ("POPULATION 193,000,000") nên bị báo thừa (#817). Cần một tập độc lập để quyết định có cho số trần xác nhận số lượng có đơn vị người hay không.
- #812: thứ tự từ trong ghi chú duyệt, đã merge (PR #813, `dd492e5`). Bản rc16 chưa có sửa này, nên ghi chú trên Windows vẫn hiện từ theo thứ tự abc.
- #817 (P2-N27): i9.u1 và dòng khẳng định của Kiểm tra cuối.
