# DubFlow — Báo cáo hiệu chỉnh ngữ nghĩa trên footage thật (Gate S final closure)

Epic #637 · chỉ thị "DUBFLOW — GATE S FINAL CLOSURE" (2026-11-11 21:56) · không phát hành bản stable · không có code Phase 4.

**Kết luận: Gate S FAIL.** Trên REAL-HOLDOUT (42 câu, 6 video thật mới), hệ thống đạt 3 trong 7 ngưỡng đã đăng ký trước:
- Đạt: 0 bỏ sót im lặng nghiêm trọng, 0 cảnh trái nghĩa nghiêm trọng bị READY, phát hiện lỗi nghiêm trọng 22/22.
- Không đạt:
  - coverage 26,8 % (ngưỡng ≥ 90 %);
  - không câu nào READY (precision 0/0, tỉ lệ READY 0/20);
  - cờ thừa 6/6.

Theo §15 của chỉ thị, vòng hiệu chỉnh có kiểm soát đã chạy một lần và dừng ở đây. Báo cáo đề xuất **Assisted Editorial Review** (§18) như một lựa chọn sản phẩm. Đề xuất này không phải là đạt.

## 1. HEAD và PR

- Bắt đầu: main `dc39a61` (rc6 `85b9d81` + #749 + báo cáo #751).
- Kết thúc phần thuật toán: main `7acd262`. Bản candidate 1.1.96-rc7 được build từ commit này.

| PR | Issue | Nội dung |
|---|---|---|
| #753 | #752 | P2-14: nguồn gốc bằng chứng; tên file không phải nội dung (xem DESKTOP_RELEASE_BLOCKERS_REPORT) |
| #755 | #754 | P2-5: ghim/duyệt không bị huỷ sai khi dựng lại |
| #757 | #756 | P2-6: Bản cuối có revision và sha256, hiện "cũ" sau khi dựng lại |
| #759 | #758 | Nền đánh giá: PROTOCOL.md, nhãn REAL-DEV/REAL-HOLDOUT khoá hash, `capture.py` (ghi lời gọi planner), `evaluate.py` (chấm điểm) |
| #761 | #760 | Lỗi lộ ra khi chạy thật: tốc độ khung hình VFR (v6 hỏng plan), lỗi vision không ghi vào log từng phần (thất bại im lặng) |
| #763 | #762 | Vòng hiệu chỉnh duy nhất: phụ đề in sẵn không phải thẻ chữ; tách 5 tín hiệu trong log từng câu |

## 2. Nguyên nhân gốc (phân tích trên REAL-DEV)

Phân tích dùng hai lần chạy Claude Vision thật trên REAL-DEV. Replay bằng code main cho kết quả giống hệt bản chạy tươi, nên hai bên khớp nhau. Các con số tái lập được bằng `tests/mastering/real/evaluate.py`, kèm các công cụ chẩn đoán đọc lời gọi planner đã ghi.

| # | Nguyên nhân | Bằng chứng | Có sửa bằng hiệu chỉnh nhẹ được không |
|---|---|---|---|
| R1 | **Phụ đề in sẵn bị coi là thẻ chữ.** Vision gắn nhãn `text_on_screen` cho mọi khung có phụ đề. `_text_card` chỉ xét nhãn này nên giữ lại phần lớn cảnh của video phụ đề Trung, như thể chúng là màn hình chữ. | v2: 88/88 và 96/96 cảnh có mô tả bị coi là thẻ chữ; v3 56/93; v5 28/30; v6 64/92. Hệ quả: câu có cảnh đúng vẫn bị MATCH_NOT_FOUND và mang ghi chú sai "lời câu chỉ khớp một thẻ chữ". Ví dụ v3-s3 có cảnh nhíp gắp mũi ở 44,8–48,7 s, là hai khớp tốt nhất của chính câu đó; v5-s1 có coverage 1000 ‰ mà vẫn MATCH_NOT_FOUND. | Có, đây là lỗi. Đã sửa ở #763. |
| R2 | **Độ liên quan theo từ ngữ không phân biệt được cảnh đúng và cảnh sai.** Lời bình tiếng Việt và mô tả Vision dài dùng nhiều từ chung. Câu viết gián tiếp (không gọi tên vật) gần như không khớp. | Nhóm tin cậy 450–599: chỉ 26–43 % thời lượng nằm trên cảnh đúng; nhóm 600–749: 49–59 %. Mảnh có relevance ≥ 400 chỉ đúng 44 %, trong khi nhiều mảnh đúng chỉ có relevance khoảng 100. Câu gián tiếp trên REAL-DEV: coverage 1336/25260. | Không. Thay đổi ánh xạ điểm hay ngưỡng không tạo ra được tín hiệu mà relevance không mang. |
| R3 | **Phân bổ tham lam, chỉ đi tới.** Cảnh điểm cao hơn ở xa phía sau được chọn, nên mất cảnh của chính câu đó và của các câu sau. | v4-s2: chọn cảnh ở 200 s ("trợ lý", "bộ não") thay vì cảnh Claude Code ở 58–88 s. v5-s1 nhảy tới 21,7 s. Thứ tự lời bình khác thứ tự nguồn (v3-s2 → s3) thì không thể phủ đủ khi chỉ đi tới. | Không. Cần phân bổ toàn cục (quy hoạch động), là thay đổi thuật toán, nằm ngoài vòng nhẹ. |
| R4 | **Nhiều cảnh chưa bao giờ được AI nhìn.** Ngân sách vision không đủ cho nguồn dài. | Ứng viên có mô tả vision: v2 88/148, v3 93/140, v6 92/262. Cảnh đúng không có mô tả thì không khớp được (v3-s6: 7/15 ứng viên đúng không có mô tả). | Không, nếu chỉ replay. Cần chạy tươi với ngân sách lớn hơn, tức tăng chi phí. |
| R5 | **Nhãn agent hẹp hơn hình.** Ví dụ v5-s1: cảnh Edison ngồi ghế cầm hai quả cầu trên đĩa cũng khớp lời, nhưng nhãn chỉ chấp nhận 0–11 s. | Nhãn đã khoá. Ghi nhận là hạn chế của nhãn agent, không sửa nhãn. | Không áp dụng. |

## 3. Dataset REAL-DEV

`tests/mastering/real/real-dev.json`: 41 câu, 6 video (v1–v6), 4 nhóm.

| Nhóm | Câu | Video |
|---|---|---|
| product-household | 7 | v1 Máy pha cà phê viên nén (Bán hàng) |
| entertainment | 14 | v2 phim ngắn cổ trang, v3 phim ngắn "dị vật trong mũi" (phụ đề Trung) |
| educational-technical | 13 | v4 hướng dẫn Obsidian + Claude (màn hình), v5 hoạt hình mẹo chợp mắt |
| long-form-documentary | 7 | v6 tòa nhà nuôi lợn Hồ Bắc |

Loại câu: footage_present 15, synonym_or_indirect 6, near_match 6, partial 6, footage_insufficient 6, generic 2.

## 4. Dataset REAL-HOLDOUT

`tests/mastering/real/real-holdout.json`: 42 câu, 6 video (v7–v12).

| Nhóm | Câu |
|---|---|
| product-household | 7 (v7, Bán hàng) |
| entertainment | 21 |
| educational-technical | 7 |
| long-form-documentary | 7 |

Loại câu: footage_present 16, synonym_or_indirect 6, near_match 6, partial 6, footage_insufficient 6, generic 2.

- Không có nhóm điện tử/dụng cụ: trên máy không có nguồn nào ngoài video hồi quy, và không tải thêm (PROTOCOL.md §1).
- Sáu video hồi quy (Espresso, Máy thổi bụi, Hũ tăm, B1, B5, Why We Fight) không nằm trong REAL-DEV hay REAL-HOLDOUT.

## 5. Ground truth và frozen hashes

- **Người viết nhãn:** đây là nhãn của agent (Claude), không phải nhãn đã được người kiểm.
  - Nhãn được viết từ khung hình của chính video, 1 khung/s, hoặc 1 khung/2 s với video dài hơn 5 phút. Mỗi khoảng thời gian được kiểm lại ở đúng giây đó.
  - Lời bình được viết từ cùng các khung hình đó, trước khi planner hay Vision thấy video.
- **Khoá:** nhãn được commit ở `agent/gate-s-real-labels` (4eb29b8, 1e52263) trước mọi lần chạy, rồi đưa vào main không đổi bởi #759.

| File | sha256 |
|---|---|
| real-dev.json | `c15f57d9328cfb1f4f942cadde5aef94d13b7601e24e294adfe08d73d5dd2ba4` |
| real-holdout.json | `3dd97b3b2467a7988fdca1b039b51e094ad5f8b4738150a92e4af814c57cc3f9` |
| sources.json | `1041b7d1a1df1b19f30ce8b2945f00fa6b126ae469f8e8341cd446651f3a974d` |

- **Kiểm tra:** `test_real_capture` kiểm cả ba hash.
- **Lúc chấm holdout:** sha256 của real-holdout.json vẫn đúng như trên. Không nhãn nào bị sửa sau khi thấy kết quả.
- **Một lần duy nhất:** REAL-HOLDOUT chỉ được chấm một lần, sau khi #763 đã merge. Kết quả holdout không được dùng để sửa gì.

## 6. Bằng chứng dữ liệu có thật

- Mỗi video có sha256 trong `sources.json`. Mỗi câu có khoảng thời gian chấp nhận, khoảng trái nghĩa và khoảng chỉ đúng một phần, tính theo ms của nguồn.
- **Bản chạy tươi:** dùng engine của repo chạy qua `capture.py` trên máy Windows của chủ dự án, với runtime đóng gói rc6, FFmpeg đi kèm, `--cpu-only`.
- **Vision:** Claude `claude-opus-5-5`, gọi thật, không có lỗi.

| Tập | Số khung gửi Vision | Ghi chú |
|---|---|---|
| REAL-DEV | 575 (lần 1), 583 (lần 2) | |
| REAL-HOLDOUT | 732 | v7 40, v8 105, v9 195, v10 188, v11 105, v12 99 |

- **Bản ghi:**
  - Mỗi lời gọi planner được lưu thành `*.call.json` gồm ứng viên, mọi mục bằng chứng đúng như mô hình trả lời, và lời bình kèm độ dài đọc. Kết quả được lưu thành `*.result.json` gồm plan và decision log.
  - Tất cả nằm ở nhánh `agent/gate-s-real-runs` (REAL-DEV: 245f6cd; REAL-HOLDOUT: 969e456) và được đưa vào repo cùng báo cáo này.
  - 22 file holdout có sha256 khớp 22/22 với danh sách ghi lại ngay sau lần chạy, trước khi commit hay chấm. Danh sách được commit ở `tests/mastering/real/runs/HOLDOUT_SHA256.txt` và được `test_gate_s_result` kiểm tra.
- **Quét bảo mật:** khoá AI 0 lần trùng, e-mail 0, không có tên hồ sơ Windows cụ thể nào. Đường dẫn trong nhãn chỉ dùng placeholder `%USERPROFILE%` hoặc thư mục dự án chung `D:\Projects\DubFlow`.

## 7. Phương pháp hiệu chỉnh

Phương pháp theo PROTOCOL.md §5: một vòng có kiểm soát, chỉ trên REAL-DEV và các bộ dev cũ, chỉ dùng cách nhẹ.

**1. Sửa R1 (#763).** Một khung chỉ là thẻ chữ khi chữ là thứ duy nhất nó cho thấy, tức là có `text_on_screen` nhưng không có nhãn cảnh nào: people, action, reaction, object, establishing, explanation, product_*.
- Các dấu hiệu trong mô tả ("thẻ tiêu đề", "màn hình chữ"…) giữ nguyên.
- Có test lỗi trước khi sửa và qua sau khi sửa: `tests/mastering/test_gate_s_calibration.py`, 4 test (main: 1 fail + 2 error; sau sửa: OK).

**2. Tách tín hiệu (§5.2).** Mỗi câu trong decision log có `signals`:
- `evidence_reliability`: độ tin của nguồn nội dung, giá trị thấp nhất và cao nhất trên các mảnh; tên file không tính.
- `semantic_relevance`: khớp tốt nhất trong nguồn, và khớp yếu nhất trong các mảnh đã chọn.
- `temporal_coverage`: tỉ lệ ‰ thời lượng câu nằm trên cảnh minh họa.
- `allocation`: số ms trên cảnh của chính câu, cảnh chung, cảnh lặp và khung giữ.
- `review_risk`: độ tin thấp nhất, số mảnh bị gắn cờ, câu có cần xem không.

Contract `editorial-v1.json` đã cập nhật. Phần `signals` chỉ giải thích phán quyết, không thay đổi phán quyết nào.

**3. Ánh xạ điểm, hiệu chỉnh ngưỡng: không làm.**
- Bảng độ tin cậy ở §8 cho thấy confidence không tách được cảnh đúng và cảnh sai. Trên REAL-DEV, các câu đúng có điểm chồng lấn với các câu sai (ví dụ v2-s3 relevance 228 sai, v4-s7 relevance 226 đúng).
- Mọi ánh xạ đơn điệu sẽ hoặc nâng cả hai lên READY (precision giảm), hoặc giữ cả hai ở NEEDS_REVIEW (như hiện nay).
- Không giảm REVIEW_BELOW và không đổi trọng số.

**Tránh overfit.** Thay đổi duy nhất về hành vi (R1) là quy tắc về ý nghĩa nhãn, không có tham số số học nào. Các bộ dev/holdout cũ cho kết quả giống từng byte (§14).

## 8. READY precision

Ngưỡng: ≥ 95 %.

| Tập | READY đúng / READY |
|---|---|
| REAL-DEV lần 1, trước (= tươi) | 1/3 |
| REAL-DEV lần 1, sau #763 (replay) | 1/3 |
| REAL-DEV lần 2, trước | 2/2 |
| REAL-DEV lần 2, sau #763 | 2/2 |
| **REAL-HOLDOUT replay `7acd262`** | **0/0 → FAIL** (không có READY nào thì không thể đạt) |
| REAL-HOLDOUT tươi (code lúc chạy) | 0/0 |

READY sai trên REAL-DEV lần 1:
- v4-s3: câu chỉ đúng một phần, coverage 0, được READY. Không tính là nghiêm trọng vì `serious_if_wrong=false`.
- v4-s1: coverage 893 ‰, thiếu 7 ‰ so với mức đúng 900 ‰.

**Bảng độ tin cậy của mảnh** (khoảng confidence → tỉ lệ thời lượng mảnh nằm trên cảnh chấp nhận):

| Tập | 0–299 | 300–449 | 450–599 | 600–749 | 750–1000 |
|---|---|---|---|---|---|
| REAL-DEV lần 1 trước | 4 % (24 mảnh) | 13 % (29) | 43 % (18) | 59 % (4) | – |
| REAL-DEV lần 1 sau | 0 % (5) | 1 % (41) | 26 % (33) | 49 % (5) | – |
| REAL-HOLDOUT replay | 49 % (4) | 0 % (31) | 14 % (38) | 66 % (4) | – |
| REAL-HOLDOUT tươi | 21 % (28) | 0 % (27) | 16 % (23) | 100 % (2) | – |

Không mảnh nào đạt ≥ 750. Mức 600–749 có rất ít mảnh, và ngay cả ở mức đó chỉ khoảng nửa thời lượng là đúng.

## 9. Review recall đối với lỗi nghiêm trọng

Ngưỡng: 100 %.

| Tập | Lỗi nghiêm trọng bị gắn cờ / tổng lỗi nghiêm trọng |
|---|---|
| REAL-DEV lần 1 trước → sau | 20/20 → 23/23 |
| REAL-DEV lần 2 trước → sau | 21/21 → 22/22 |
| **REAL-HOLDOUT replay** | **22/22 → PASS** |
| REAL-HOLDOUT tươi | 24/24 |

## 10. Tỉ lệ cảnh báo thừa

Ngưỡng: ≤ 25 %.

| Tập | Câu đúng bị gắn cờ / câu đúng |
|---|---|
| REAL-DEV lần 1 trước → sau | 5/6 → 5/6 |
| REAL-DEV lần 2 trước → sau | 5/7 → 6/8 |
| **REAL-HOLDOUT replay** | **6/6 → FAIL** |
| REAL-HOLDOUT tươi | 7/7 |

Tỉ lệ cần duyệt trên REAL-HOLDOUT: 42/42 câu không READY. Đây đúng là trường hợp "cảnh báo gần như mọi câu" mà chỉ thị yêu cầu không được tính là đạt. Vì vậy Gate S là FAIL, kể cả khi recall đạt 100 %.

## 11. Bỏ sót im lặng

Ngưỡng: 0.

- Bỏ sót im lặng nghiêm trọng (lỗi nghiêm trọng mà vẫn READY): 0 trên mọi tập, cả trước và sau.
- Cảnh trái nghĩa nghiêm trọng bị READY: 0.
- Tổng thời lượng trái nghĩa đặt vào câu:
  - REAL-HOLDOUT: 12070 ms (replay), 13934 ms (tươi).
  - REAL-DEV: 11360 ms (lần 1), 9915 ms (lần 2).
  - Toàn bộ đều nằm ở câu đã bị gắn cờ.
- SOURCE_INSUFFICIENT sai (câu có cảnh mà bị báo thiếu nguồn): 0 trên REAL-HOLDOUT, 0 trên REAL-DEV sau #763.

**PASS** cho cả hai ngưỡng an toàn.

## 12. Coverage theo nhóm

Coverage = Σ ms chấp nhận / Σ ms cần, chỉ tính các câu MATCH_AVAILABLE.

| Nhóm | REAL-DEV sau (replay lần 1) | REAL-HOLDOUT replay | REAL-HOLDOUT tươi |
|---|---|---|---|
| product-household | 4980/11070 (44,9 %) | 7700/11150 (69,0 %) | 7700/11150 (69,0 %) |
| entertainment | 4981/29300 (17,0 %) | 15028/48120 (31,2 %) | 11512/48120 (23,9 %) |
| educational-technical | 15333/32065 (47,8 %) | 0/17110 (0,0 %) | 3930/17110 (22,9 %) |
| long-form-documentary | 4745/17510 (27,0 %) | 2440/17430 (13,9 %) | 1185/17430 (6,7 %) |
| **Tổng** | 30039/89945 (33,3 %) | **25168/93810 (26,8 %)** | 24327/93810 (25,9 %) |

Coverage theo loại câu trên REAL-HOLDOUT replay:
- footage_present: 14633/68695 (21,3 %).
- synonym_or_indirect: 10535/25115 (41,9 %).

## 13. Coverage từng video

| Video | REAL-DEV lần 1 trước | Sau | Video | REAL-HOLDOUT replay | Tươi |
|---|---|---|---|---|---|
| v1 | 44,9 % | 44,9 % | v7 | 69,0 % | 69,0 % |
| v2 | 14,3 % | 25,2 % | v8 | 32,6 % | 19,1 % |
| v3 | 11,0 % | 11,0 % | v9 | 28,3 % | 28,3 % |
| v4 | 92,0 % | 71,8 % | v10 | 0,0 % | 22,9 % |
| v5 | 57,1 % | 10,8 % | v11 | 13,9 % | 6,7 % |
| v6 | 16,6 % | 27,0 % | v12 | 32,1 % | 24,6 % |

**Sáu video hồi quy** (bộ hồi quy thực tế, không phải held-out).
- Cách chạy: Windows, Claude Vision tươi, engine `7acd262` với runtime đóng gói. Replay cùng bằng chứng bằng code rc6 `85b9d81`, chấm theo nhãn phase2. Chi tiết: [gate-s-real/rc7/report-rc7.md](gate-s-real/rc7/report-rc7.md) §7.

| Bộ | rc6 code (replay) | rc7 code (tươi = replay) | Bỏ sót im lặng | Gắn cờ |
|---|---|---|---|---|
| P1 Espresso | 82,7 % | 82,7 % | 0 | 5/6 |
| P2 Máy thổi bụi | 77,6 % | **65,0 %** | 0 | 6/6 |
| P3 Hũ tăm | 43,9 % | 43,9 % | 0 | 6/6 |
| B1 Top 5 | 94,9 % (stolen 1745 ms) | 94,9 % (stolen **3330 ms**) | 0 | 11/11 |
| B5 Top 5 (5 nguồn) | 95,7 % | **92,6 %** (contra 1785 ms ở outro) | 0 | 13/13 |
| D1 Why We Fight | 43,0 % | 43,0 % | 0 | 4/4 |

- Ba bộ kém hơn trên cùng bằng chứng: P2, B5, B1. Đây là P1-N2 (#765).
- Nguyên nhân có thể là quy tắc thẻ chữ của #763, cùng cơ chế R2/R3 trên REAL-DEV.
- Mọi câu bị ảnh hưởng đều đã được gắn cờ.

## 14. Regression

**Các bộ cũ** (dev, dev2, dev3, holdout, holdout2, holdout4, holdout5, holdout6, holdout7): `python -m tests.mastering.regression.runner all|holdout5|holdout6|holdout7` cho đầu ra giống từng byte trước và sau #763. Không có thay đổi nào: các bộ này dùng mô tả văn bản, không có nhãn `text_on_screen` từ Vision.
- dev 74,8 %
- dev2 93,9 %
- dev3 97,4 %
- holdout 82,8 %
- holdout2 78,4 %
- holdout4 100 %
- holdout5 92,4 %
- holdout6 92,3 %
- holdout7 100 %

**Test:** mastering 210/210, tests/product và tests/compilation OK, CI xanh trên #763.

**REAL-DEV, tác động của #763** (replay, trước → sau):
- Lần 1:
  - Coverage 40,7 → 33,3 %.
  - MATCH_NOT_FOUND 15 → 5.
  - Stolen 2280 → 6907 ms.
- Lần 2:
  - Coverage 34,7 → 34,2 %.
  - MATCH_NOT_FOUND 19 → 5.
  - Stolen 2693 → 10221 ms.

Trên sáu video hồi quy, cũng với bằng chứng giống hệt, #763 làm P2, B5, B1 kém đi; P1, P3, D1 không đổi (§13, P1-N2 #765). Các bộ cố định không đổi.

Lỗi thẻ chữ đã che mất R2/R3. Khi cảnh có phụ đề được dùng lại, khớp từ ngữ và bộ phân bổ chỉ đi tới chọn sai cảnh ở xa (v4-s2, v5-s1). Phán quyết trung thực hơn: không còn MATCH_NOT_FOUND và ghi chú "thẻ chữ" sai trên câu có cảnh đúng. Nhưng coverage không tăng. Báo cáo ghi đúng như vậy, không chỉnh thêm để bù.

## 15. Biến thiên giữa các nhà cung cấp và giữa các lần chạy

**Gemini (gemini-3.5-flash-lite):** cả 5 lần chạy REAL-DEV đều KHÔNG HỢP LỆ.
- Tỉ lệ khung không được trả lời là 9,7–71 %, do lỗi 503 rồi hết hạn mức miễn phí theo ngày. Quy tắc 5 % được đặt trước khi chạy Claude.
- Không có so sánh Gemini/Claude hợp lệ. Đây là giới hạn của báo cáo.
- Lần chạy lỗi này đã lộ ra lỗi thất bại im lặng, đã sửa ở #761.

**Claude, hai lần chạy tươi trên REAL-DEV** (cùng code, cache trống):
- Coverage 40,7 % và 34,7 %.
- 11/41 câu đổi phán quyết. 3 câu đổi coverage ≥ 50 điểm phần trăm (v1-s5, v4-s2, v4-s7).
- v4-s1: lần 1 READY ở 893 ‰ (gần đúng, không nghiêm trọng), lần 2 NEEDS_REVIEW ở 1000 ‰.
- Không lần nào có câu chuyển từ đúng sang sai nghiêm trọng mà vẫn READY.

**REAL-HOLDOUT, tươi (code lúc chạy) so với replay (`7acd262`):** coverage 25,9 → 26,8 %. MATCH_NOT_FOUND 12 → 4 nhờ R1.

## 16. Cache và replay

- **Replay tất định:** `capture.replay` lập lại plan từ lời gọi đã ghi, không gọi AI.
  - Trên mọi lời gọi REAL-DEV, replay bằng code main trước #763 cho kết quả giống hệt bản chạy tươi (`test_real_capture` kiểm tra).
  - Mọi so sánh trước/sau trong báo cáo đều là replay trên cùng bằng chứng.
- **Cache vision trong một lần chạy:** chỉ trúng khi hai mục cùng một nguồn dùng lại keyframe giống hệt.
  - Lần 1: v2 3, v3 13, v4 6, v5 6, v6 11 lần trúng.
  - Các keyframe còn lại khác byte nên không trúng.
- **Ngân sách vision:** 120 khung/phần, riêng v4 là 240. Không đủ cho các nguồn dài (R4).

## 17. Chi phí và hiệu năng

**REAL-DEV, Claude, CPU-only, runtime rc6:**

| Video | Khung gửi | Thời gian (s) | RAM đỉnh (MB) |
|---|---|---|---|
| v1 | 31 | 98 | 911 |
| v2 | 97 | 306 | 950 |
| v3 | 106 | 316 | 905 |
| v4 | 198 | 588 | 891 |
| v5 | 34 | 119 | 937 |
| v6 | 109 | 289 | 2028 |
| **Tổng** | **575** | **1714** | |

- REAL-HOLDOUT: 732 khung.
- Không có lỗi vision trong 18 lần chạy Claude.
**Bản rc7 trên Windows** (i7-12700H, CPU-only; [report-rc7.md](gate-s-real/rc7/report-rc7.md) §8):

| Hạng mục | Kết quả |
|---|---|
| Sáu bộ hồi quy, vision tươi | 80 request, 290 khung, 0 lỗi |
| Thời gian mỗi bộ | 51–318 s |
| RAM đỉnh | 2,1–3,5 GB (cả cây tiến trình), 1,3–1,5 GB (engine) |
| Số lần encode mỗi bộ | 1–8 |
| B1 chạy lại không đổi, vision từ cache | 7,1 s, 80/80 lần trúng, 0 lời gọi AI |

Qua UI:

| Bước | Thời gian |
|---|---|
| Bản nháp 3 phần có vision | ≈ 120 s |
| Dựng lại 1 phần sau khi ghim | 10 s |
| Dựng lại sau khi sửa một câu | 15 s |
| Tiếp tục sau khi huỷ | 40 s |
| Lồng tiếng 30 s | 90 s |

Engine không xuất số token, nên chi phí chỉ đo được bằng số khung và số request.
- Không có vision: kết quả signals_only, xem D3a.

## 18. Hạn chế còn lại và đề xuất

**Hạn chế:**
- Nhãn là nhãn của agent, chưa có người kiểm.
- Không có nhóm điện tử/dụng cụ.
- Không có so sánh Gemini hợp lệ.
- Mỗi tập chỉ có 20 câu đủ điều kiện, nên khoảng tin cậy rộng. Dù vậy, 0/20 READY và 26,8 % coverage nằm rất xa ngưỡng.
- Compilation v2 cần ít nhất 2 mục, nên mỗi video được chia thành 2 mục trên cùng một nguồn. Mỗi mục phát nguồn từ đầu, chỉ đi tới.

**Lỗi mới từ rc7:**
- **P1-N1 (#764).** Câu đầu của Bán hàng được coi là hook, nên thành NOT_NEEDED và READY trên footage không liên quan, dù câu nêu thông tin cụ thể về sản phẩm và Vision ghi "Không thấy sản phẩm". Đây là bỏ sót im lặng trên đường mở đầu. Nó không bị bộ chấm REAL bắt, vì câu mở đầu của REAL-HOLDOUT/DEV được dán nhãn generic.
- **P1-N2 (#765).** Ba bộ hồi quy thật kém hơn rc6 trên cùng bằng chứng.

**Nguyên nhân còn mở:**
- R2: khớp từ ngữ.
- R3: phân bổ tham lam chỉ đi tới.
- R4: ngân sách vision.

Cả ba cần thay đổi thuật toán hoặc chi phí, không phải hiệu chỉnh nhẹ. Phương án tiếp theo theo thứ tự chi phí/lợi ích:

1. **Assisted Editorial Review** (đề xuất sản phẩm, KHÔNG phải đạt Gate S).
   - Với Tổng hợp và Bán hàng, AI tạo bản nháp. Người dùng duyệt các câu bị gắn cờ trong bảng Duyệt cảnh, đã có sẵn: ghim/bỏ/duyệt, cảnh thay thế, tín hiệu từng câu.
   - Chi phí duyệt đo trên REAL-HOLDOUT: 42/42 câu phải xem, khoảng 6–7 câu cho mỗi video 30–40 s lời bình.
   - Hệ thống đã an toàn theo nghĩa: 0 bỏ sót im lặng, recall 100 %.
   - Giới hạn tự động hoá: không có câu nào READY tự động. Người dùng phải coi mọi câu là cần xem.
2. **Phân bổ toàn cục** cho nguồn chỉ đi tới (R3). Chi phí: thay đổi `align.assign`. Cần vòng dev/holdout mới, với tập held-out mới.
3. **Khớp ngữ nghĩa bằng mô hình** (embedding hoặc hỏi lại Vision theo câu) thay cho khớp từ ngữ (R2). Chi phí: thêm lời gọi AI hoặc thêm model. Chỉ thị này cấm thêm model mới, nên việc này cần một chỉ thị riêng.
4. **Ngân sách vision thích ứng** theo độ dài nguồn (R4). Chi phí: số khung gửi AI tăng theo độ dài nguồn (chưa đo; v2/v3/v6 hiện chỉ 35–66 % ứng viên có mô tả).

## 19. PASS/FAIL

| Ngưỡng (REAL-HOLDOUT, đăng ký trước) | Giá trị | Kết quả |
|---|---|---|
| Coverage ≥ 90 % | 25168/93810 = 26,8 % | **FAIL** |
| 0 bỏ sót im lặng nghiêm trọng | 0 | PASS |
| 0 cảnh trái nghĩa nghiêm trọng READY | 0 | PASS |
| READY precision ≥ 95 % | 0/0 (không có READY) | **FAIL** |
| Review recall lỗi nghiêm trọng = 100 % | 22/22 | PASS |
| Cảnh báo thừa ≤ 25 % | 6/6 = 100 % | **FAIL** |
| READY rate ≥ 60 % | 0/20 | **FAIL** |

Tiêu chí §15 của chỉ thị:
- #9 (không có regression nghiêm trọng): không đạt hoàn toàn. Có P1-N2 trên ba video hồi quy thật, dù mọi câu bị ảnh hưởng đã được gắn cờ.
- #11 (video E2E thật được công bố đúng): đạt, xem §13.

**Gate S: FAIL.** Không ngưỡng nào bị thay đổi. Không nhãn nào bị sửa. REAL-HOLDOUT chỉ được dùng một lần.
