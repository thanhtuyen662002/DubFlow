# DubFlow — Báo cáo đóng cổng ngữ nghĩa (Gate S)

Epic #637 · chỉ thị "PHASE 4 READINESS GATE CLOSURE" ngày 2026-11-11 · không phát hành bản stable.

## 1. HEAD và phạm vi thay đổi

- Bắt đầu: main `be8b201` (PHASE4_READINESS = NO-GO, hai cổng P0 mở).
- Bộ quyết định holdout7 được chạy một lần ở main `7c4fb1f`.
- Sau đó các lần chạy E2E trên Windows (rc4, rc5, rc6) tìm ra lỗi trên video thật, được sửa ở #745, #747, #749. Kết thúc: main sau #749 (bản rc6 = `85b9d81`).
- PR thuộc Gate S, theo thứ tự merge:

| PR | Nội dung |
|---|---|
| #721 | holdout5, bộ độc lập thứ nhất, khoá trước mọi thay đổi |
| #723 | Ghép tên riêng đa ngôn ngữ (`entities.py`) |
| #725 | Bằng chứng nhiều khung hình thích ứng (`frames.py`, keyframe dự phòng) |
| #727 | Quyết định review theo từng phần cho Tổng hợp thô (engine) |
| #731 | Script đánh giá `evaluate.py`, chạy holdout5 một lần |
| #733 | holdout6, khoá trước khi sửa kiểm tra A |
| #735 | Kiểm tra A: câu mở đầu/kết không lấy cảnh của câu sau; nếu buộc phải lấy thì gắn cờ |
| #737 | Ghi kết quả holdout6, khoá holdout7 |
| #739 | Câu chỉ khớp một thẻ chữ không bao giờ READY im lặng |
| #741 | Ghi kết quả holdout7 và trạng thái sau của mọi bộ |
| #745 | Lỗi rc4 trên video thật: cảnh khớp bị bỏ "để an toàn" (0 %), hình hết trước tiếng (D1 −108 ms), câu mở đầu khớp một phần không bị gắn cờ |
| #747 | Lỗi rc5: gỡ luật nửa điểm cho từ không dấu (làm mất khớp đúng), cờ của câu mở đầu khớp một phần đi xuống từng mảnh, phần giới thiệu luôn cần duyệt |
| #749 | S5: khớp gần (một số từ trùng) không bao giờ là SOURCE_INSUFFICIENT |

## 2. Nguyên nhân gốc

| Lỗi | Nguyên nhân |
|---|---|
| Tên riêng song ngữ (Kavkaz/Caucasus, Kremlin/Кремль) không khớp | Khớp chữ thuần, không có bảng thực thể hay bí danh. Chữ bỏ dấu khiến "thủy điện" trùng "Thụy Điển" |
| Cảnh dài chỉ đúng một đoạn, khung giữa mờ hay tối | Mỗi cảnh chỉ được mô tả bằng một khung giữa |
| Kiểm tra A: hook lấy cảnh chính của câu sau (holdout5: 8 500 ms, 1 lần không gắn cờ) | Câu mở đầu được xếp chọn sau cùng, nhưng cảnh nó khớp (thứ nó hứa, ví dụ "bass mạnh") vẫn nằm trong tập khớp của nó. Hình phạt "cảnh của câu khác" chỉ áp cho câu chưa chọn, mà lúc hook chọn thì mọi câu đã chọn rồi |
| holdout6: câu kêu gọi "Miễn phí giao hàng hôm nay" READY im lặng trên cảnh sai | Quy tắc thẻ chữ (#706) bỏ thẻ chữ khỏi câu không nói về chữ. Câu này mất cảnh khớp duy nhất, bị coi là câu chung chung không cần khớp, nên được lấp bằng cảnh trung tính mà không gắn cờ |
| rc4 Top 5 (desktop): "Có cảnh khớp… Hình khớp 0 %" | Chạy tiến: cảnh khớp duy nhất bị loại vì "phía sau không đủ cho các câu sau", rồi một cảnh trung tính cũng không an toàn được dùng; câu sau chiếu lại chính cảnh khớp |
| rc4 Why We Fight: lệch A/V −108 ms | Khoảnh khắc nguồn đặt sát cuối file, nhưng hình của nguồn hết sớm hơn container 87 ms |
| rc4 Hũ tăm: câu mở đầu sai cảnh, READY | "ấn" (lời) / "nhấn" (AI): độ liên quan 41 < 60, câu bị coi là chung chung nên không bao giờ bị gắn cờ |
| rc5 Espresso/Hũ tăm giảm coverage | Luật nửa điểm cho từ không dấu của #745: Gemini thường viết không dấu, luật làm mất khớp đúng (replay cùng bằng chứng: 62,3 → 78,4 % khi gỡ) |
| rc5 B1: phần giới thiệu lấy 4,2 s cảnh chính của một mục, READY | Phần giới thiệu là một phần riêng; kiểm tra A (owners_after) không thấy câu của các mục |
| rc5: "nguồn không có cảnh này" sai (desktop #5 Câu 2, Máy thổi bụi u4) | Mọi cảnh đã được mô tả và không cảnh nào vượt ngưỡng khớp 60; khớp gần (37, 57) vẫn bị kết luận là nguồn thiếu |

## 3. Những sửa đổi đã triển khai

- **#735 (kiểm tra A).** Một câu mở đầu/kết (hook, intro, cta, outro) không ghim chỉ giữ những cảnh khớp mà không câu nội dung nào phía sau cũng khớp. Cảnh lấp trung tính của nó cũng tránh những cảnh đó (−200). Nếu cuối cùng vẫn phải dùng cảnh của câu sau (nguồn không còn gì khác), câu bị gắn cờ cần kiểm tra kèm ghi chú.
- **#739 (thẻ chữ).** Một câu mà từ của nó chỉ khớp thẻ chữ bị để dành thì không còn là "câu chung chung". Nó được ghi chú "lời câu chỉ khớp một thẻ chữ" và cần kiểm tra.
- **#745, #747, #749** (từ E2E Windows, xem mục 15b): đặt cảnh khớp của câu trước cảnh lấp khi phía trước không còn gì an toàn; chỉ dựng trong phần còn hình của nguồn; câu mở đầu khớp một phần bị gắn cờ, cả câu lẫn từng mảnh; phần giới thiệu của chương trình nhiều nguồn luôn cần duyệt; SOURCE_INSUFFICIENT chỉ khi không cảnh nào có từ chung với câu.
- Một thay đổi bị gỡ: luật nửa điểm cho từ không dấu (#745) làm giảm coverage thật nên bị bỏ ở #747.
- Không đổi ngưỡng (MIN_RELEVANCE, REVIEW_BELOW, MATCH_FULL), trọng số khớp hay ground truth cũ nào.

## 4. Bằng chứng nhiều khung hình (#725)

- **Keyframe dự phòng.** Khung giữa không dùng được (mờ, tối, chuyển cảnh) thì lấy thêm khung ở 25 % và 75 %. Khung đầu tiên dùng được làm keyframe. Chỉ tốn 2 lần cắt FFmpeg, không gọi AI.
- **Khung thêm.** Sau kế hoạch đầu, chọn tối đa 8 cảnh mỗi kế hoạch: cảnh dài (≥ 6 s) gần câu thiếu cảnh, cảnh dài đang được dùng, cảnh có câu trả lời yếu. Lấy 2 khung mỗi cảnh, ở 15 % và 85 %, mô tả bằng Describer hiện có (có ngân sách, cache theo nội dung khung).
- Khung đồng ý thì thêm bằng chứng. Khung bất đồng thì tách cảnh thành các phần `<id>f1..f3`, mỗi phần ≥ 400 ms.
- Trong harness, phần cuối một cảnh dài lên từ 0 % lên 67 % thời lượng câu, cảnh có khung giữa tối lên từ 0 % lên 100 %.

## 5. Ghép tên đa ngôn ngữ (#723)

- Bảng khoảng 60 thực thể (địa danh, sông, biển, công trình) với dạng tiếng Việt, tiếng Anh, tiếng gốc và chữ viết gốc.
- Bí danh của người dùng lấy từ ghi chú (`A = B`, `A còn gọi là B`, `aka`).
- Bậc tin cậy: EXACT 1000, NORMALIZED 950, VERIFIED 850, CONTEXTUAL 600.
- Kiểm thử âm tính: Austria/Australia, Kazan/Kavkaz, Georgia (bang Mỹ), "áo", "viên", "thủy điện", polish/china. Dạng mơ hồ cần ngữ cảnh. Dạng không dấu mơ hồ không bao giờ khớp. Không dùng độ giống chuỗi.
- Trên holdout7 (`korea-places`: Gyeongbok, Namsan ↔ N Seoul Tower, Haeundae, Seongsan, Myeongdong) 5/5 câu đúng.

## 6. Bộ held-out mới

| Bộ | Viết lúc | Câu | Nhóm | Vai trò |
|---|---|---:|---:|---|
| holdout5 | trước mọi thay đổi Gate S (main `be8b201`) | 62 | 8 | đánh giá lần 1. Trượt kiểm tra A |
| holdout6 | sau khi holdout5 trượt, trước #735 | 44 | 6 | đánh giá lần 2. 1 bỏ sót im lặng |
| holdout7 | sau khi holdout6 trượt, trước #739 | 42 | 9 | **bộ quyết định** |

**Công khai.** Cả ba bộ đều do cùng một agent viết. holdout6 và holdout7 được viết sau khi đã thấy lỗi của bộ trước, nên chúng kiểm tra đúng dạng lỗi đó. Chúng không phải mẫu mù từ video thật. Không bộ nào được chạy trước khi khoá. Không nhãn nào bị sửa sau khi chạy. Bộ đã bị đọc theo từng dòng không còn được gọi là độc lập.

## 7. Bằng chứng khoá ground truth

`tests/mastering/regression/FROZEN.json` lưu sha256 của mọi bộ. `test_regression.FrozenFixtures` làm CI đỏ nếu một byte thay đổi.

| Bộ | sha256 | Khoá (commit, giờ UTC) | Chạy (commit, giờ UTC) |
|---|---|---|---|
| holdout5 | `9153154a…a575cfa` | `2afc26b`, 05:22:53 | `0a633a9`, 06:20:25 |
| holdout6 | `84ef6ca8…f3e4fb` | `0e3823c`, 06:26:52 | `01f8f4d`, 06:38:55 |
| holdout7 | `7f15177e…8994a51` | PR #737, merge `29d95a8` | `7c4fb1f`, 06:46:41 |

Thứ tự merge trên main chứng minh mỗi bộ được khoá trước PR thuật toán mà nó đánh giá: #733 trước #735, #737 trước #739.

## 8. Coverage theo từng bộ

Coverage tính theo thời lượng, chỉ trên các câu có footage.

| Bộ | Trước (`be8b201`) | Sau (`7c4fb1f`) | Câu đạt |
|---|---:|---:|---:|
| dev | 74,8 % | 74,8 % | 11/17 |
| dev2 | 93,9 % | 93,9 % | 20/23 |
| dev3 | 97,4 % | 97,4 % | 21/23 |
| dev4 (mới, dev của #735) | 90,3 %* | 93,1 % | 12/13 |
| dev5 (mới, dev của #739) | 86,4 % | 86,4 % | 6/7 |
| holdout (đã đọc) | 82,8 % | 82,8 % | 15/23 |
| holdout2 (đã đọc) | 78,4 % | 78,4 % | 14/23 |
| holdout4 | 100 % | 100 % | 20/20 |
| holdout5 (đã đọc sau lần chạy) | 93,9 % (`0a633a9`) | 92,4 % | 56/62 |
| holdout6 (đã đọc sau lần chạy) | 92,3 % (`01f8f4d`) | 92,3 % | 38/44 |
| **holdout7 (độc lập)** | — | **100 %** | **42/42** |

\* blender-result trước #735. dev và holdout/holdout2 dưới 90 % như trước. Đó là các bộ khó, đã được dùng để chỉnh từ Phase 2/3, và không đổi.

## 9. Coverage theo nhóm nội dung (holdout7)

beauty, education, electronics, entertainment (chỉ chạy tiến), fitness, food, household, pets, travel-names: mỗi nhóm 100 %.

holdout6 (lần chạy duy nhất): beauty 98,4 %, education 100 %, entertainment 100 %, kitchen 91,9 %, outdoor 71,9 %, travel-names 100 %.

## 10. Silent miss

holdout7: **0**. holdout6: 1 ở lần chạy, sửa ở #739, chạy lại sau sửa còn 0 (không độc lập). holdout5: 0. Mọi bộ hồi quy: 0.

## 11. False alarm

| Bộ | Câu bị gắn cờ nhưng đúng |
|---|---|
| holdout7 | 17 trên 41 câu có footage; 24/24 câu không cờ đều đúng; 0/17 câu có cờ thật sự sai |
| holdout6 | 20 |
| holdout5 | 27 |

Cờ không được dùng làm cách đạt: 59 % câu holdout7 không có cờ và đều đúng. Tuy vậy, tỉ lệ cờ thừa còn cao (khoảng 40 %). Đây là rủi ro mở cho Phase 4 về công sức duyệt.

## 12. Contra

0 ms trên mọi bộ, trước và sau.

## 13. Stolen footage

| Bộ | Cảnh chính bị lấy | Không gắn cờ |
|---|---:|---:|
| holdout7 | 0 | 0 |
| holdout6 | 3 000 ms | 0 |
| holdout5 | 8 500 ms | 2 500 ms |
| dev-long-forward, dev3-street-food | 7 000, 1 400 ms | 0 (KNOWN_OPEN, chạy tiến khi nguồn khan hiếm) |

Ca holdout5 còn lại là speaker u1. Câu mở đầu chung chung dùng cảnh picnic mà u5 "dã ngoại" nói tới. u5 không có khớp chữ (picnic ≠ dã ngoại), nên planner không biết cảnh đó thuộc u5. Bản thân u5 bị gắn cờ.

## 14. So sánh trước/sau

- Sau `7c4fb1f`, bộ quyết định holdout7 không được dùng để chỉnh. Các thay đổi #745/#747/#749 đến từ video thật trên Windows. Chạy lại mọi bộ sau mỗi PR: coverage, bỏ sót im lặng, contra và stolen giống từng dòng; holdout7 vẫn 100 %, 42/42.
- False alarm sau #749: dev 3, dev2 11, dev3 11, holdout 4, holdout2 8, holdout4 5, holdout5 27, holdout6 20, holdout7 19 (17 lúc chạy quyết định; +2 do câu mở đầu khớp một phần của #745).
- SOURCE_INSUFFICIENT sai (footage có thật) trên các bộ: 21 → 10 sau #749. Câu thật sự không có footage vẫn được báo đúng (holdout, holdout6, holdout7: 1/1).

## 15. Regression

- `tests/mastering`: 190 test OK (gồm test FFmpeg thật của Tổng hợp). CI: governance, integration, desktop-editor-windows xanh trên mọi PR.
- Không có lỗi timeline (OUTSIDE_SOURCE, mảnh vụn, khung giữ không chủ đích, khoảng trống, thời lượng âm) trên mọi bộ.

### 15b. E2E trên video thật (Windows, runtime đóng gói)

Bằng chứng: `docs/mastering/gate-c/report-rc4.md`, `report-rc5.md`, `report-rc6.md`, `rc4-semantic-triage.md`, `rc5-followup-p1-7-p1-8.md`. Vision mới mỗi lần chạy (Gemini flash-lite; Claude trên desktop), nên mỗi lần chạy có cách mô tả khác nhau. Để tách tác động của code, rc6 còn chạy lại kế hoạch bằng code rc6 trên bằng chứng AI đã lưu của rc5.

| Video | rc4 | rc5 | rc6 (mới) | rc6 code trên bằng chứng rc5 | Bỏ sót im lặng rc6 | Lấy cảnh câu khác rc6 (cờ / không cờ) | A/V rc6 |
|---|---:|---:|---:|---:|---:|---|---:|
| Espresso | 66,4 % | 62,3 % | **72,4 %** | 78,4 % | 0 | 1 523 / 0 ms | −3 ms |
| Máy thổi bụi | 61,3 % | 38,4 % | **59,6 %** | 38,4 % | 0 | 0 / 0 | −3 ms |
| Hũ tăm | 50,9 % (1 bỏ sót) | 42,1 % | **50,7 %** | 48,8 % | 0 | 1 000 / 0 | +10 ms |
| Top 5 cũ (B1) | 88,8 % | 100 % (4 200 ms không cờ) | **99,2 %** | 100 % | 0 | 4 105 / 0 | −11 ms |
| Top 5 năm nguồn (B5) | 100 % | 100 % | **96,6 %** | 100 % | 0 | 6 216 / 0 | −11 ms |
| Why We Fight (D1) | 43 % (−108 ms) | 43 % | **48 %** | 43 % | 0 | 0 / 0 | −41 ms |

- Đã đạt trên video thật: 0 bỏ sót im lặng và 0 ms cảnh chính bị lấy mà không gắn cờ ở cả sáu video.
- Không đạt: coverage của ba video bán hàng chỉ 50–72 %, xa ngưỡng 90 % của bộ held-out. Mức tụt của Máy thổi bụi ở rc5 là do cách AI mô tả (replay bằng code rc5 và rc6 cho cùng 38,4 %), không phải do code.
- **Gần như câu nào cũng cần duyệt.** Top 5 trên desktop (rc6): 14 câu, READY 1, NEEDS_REVIEW 13; chỉ 1/27 mảnh đạt độ tin cậy ≥ 600. Nguyên nhân: với một khớp chỉ từ AI (tin cậy 850 × trust 800 = 680), cần độ liên quan ≥ 353 mới qua 600, trong khi câu lời bình thật chỉ đạt 120–380. Các bộ held-out có mô tả ngắn, trùng từ nhiều, nên không lộ ra lỗi này.

## 16. AI calls / ngân sách

- Mỗi khung được mô tả là một lời gọi vision, gộp lô trong app. Trần khung thêm: 8 cảnh × 2 khung mỗi kế hoạch. Ghép tên không gọi AI.
- holdout7: 41 keyframe + 2 khung thêm. holdout6: 47 + 2. holdout5: 76 + 36.
- Desktop Top 5 (Claude vision): rc5 70 khung / 67 hỏi, rc6 80 / 77. Phân tích thêm khung lấy khoảng 1/3 số lần hỏi.

## 17. Lỗi chưa xử lý

| ID | Mức | Nội dung |
|---|---|---|
| P1-5 | **P1** | Độ tin cậy của khớp chỉ từ AI hiếm khi đạt REVIEW_BELOW trên video thật: gần như mọi câu cần duyệt (Top 5 desktop 13/14). Vi phạm tinh thần "không để needs_review trên mọi câu thành cách đạt". Không chỉnh ngưỡng trên các video này |
| P1-6 | **P1** | Coverage video bán hàng thật 50–72 %; bộ held-out tổng hợp (do agent viết) đạt 92–100 % nhưng không đại diện cho mô tả AI dài, nhiều từ chung của video thật |
| P2-9 | P2 | Why We Fight lệch A/V −41 ms dồn ở bước ghép phần (mỗi phần ≤ 20 ms) |
| P2-15 | P2 | Cờ thừa cao (B5 31 cờ, holdout7 19/41) |
| — | rủi ro | Từ đồng nghĩa khác chữ (picnic/dã ngoại; "ấn"/"nhấn") vẫn làm mất khớp; nay bị gắn cờ, nhưng không được tìm thấy |
| — | rủi ro | #749 chưa chạy trên Windows (chỉ unit test và các bộ hồi quy) |

## 18. Quyết định Gate S: **FAIL**

| Tiêu chí | Kết quả |
|---|---|
| S1 Coverage độc lập ≥ 90 % (≥ 40 câu, ≥ 4 nhóm, khoá trước) | Đạt trên holdout7 (100 %, 42 câu, 9 nhóm) |
| S2 0 bỏ sót im lặng | Đạt: held-out, mọi bộ hồi quy và sáu video thật (rc6) |
| S3 Lỗi nghiêm trọng | Đạt: 0 contra READY, 0 ms cảnh chính bị lấy mà không cờ (rc6) |
| S4 Regression | Đạt theo nghĩa hẹp: không bộ nào tụt so với trước Gate S; ground truth cũ không đổi |
| **S5 Confidence & warnings** | **Không đạt.** Trên video thật gần như mọi câu bị gắn cờ (P1-5): cờ không còn phân biệt câu tốt và câu xấu. SOURCE_INSUFFICIENT sai đã sửa (#749) nhưng chưa kiểm trên Windows |
| S6 Timeline | Đạt (D1 −41 ms là P2) |

Bộ held-out đạt, nhưng bằng chứng trên video thật cho thấy độ chính xác và hiệu chỉnh độ tin cậy chưa chuyển sang footage thật (P1-5, P1-6). Theo chỉ thị ("không hạ ngưỡng", "không để needs_review trên mọi câu thành cách đạt"), Gate S là **FAIL**.

Việc cần làm để đạt:
1. Một bộ held-out từ **video thật** (mô tả AI đã lưu, nhãn khoá trước), đo hiệu chỉnh độ tin cậy trên đó.
2. Hiệu chỉnh độ tin cậy của khớp chỉ từ AI trên các bộ dev (không trên video E2E hay bộ held-out mới), rồi đánh giá một lần trên bộ held-out thật.
3. Chạy lại sáu video E2E và desktop Top 5 trên một bản candidate mới, gồm cả #749.
