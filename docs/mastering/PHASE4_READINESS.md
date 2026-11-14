# DubFlow — Sẵn sàng cho Phase 4 (Phim tài liệu): GO / NO-GO

Epic #637 · cập nhật mới nhất 2026-11-14 (chỉ thị "Release Safety Closure & Targeted Evidence Acquisition ADR": Stage 1 đóng theo quyết định A của chủ dự án; Stage 2 #815 chỉ gồm ADR và thí nghiệm offline; bản kiểm cuối 1.1.96-rc18 = `1687771` (main sau bốn PR của Stage 1), candidate, `publish=false`; main hiện tại sau #827 = `cf0c2a9`). Phần "Quyết định (Gate S final closure)" bên dưới giữ nguyên từ 2026-11-12 (rc7 = `7acd262`).

## Cập nhật 2026-11-14: chỉ thị "Release Safety Closure & Targeted Evidence Acquisition ADR"

Bản kiểm cuối của Stage 1: **1.1.96-rc18** (`1687771`, release run 43454280200, candidate, `publish=false`, `production_qualified: false`), Windows 11, chỉ CPU. Stage 2 (#815) chỉ gồm ADR và thí nghiệm offline có giới hạn trên bằng chứng W3 đã lưu; không đổi planner, engine, ứng dụng, contract hay mặc định nào của production, và harness không gọi AI. Không phát hành stable. Mỗi hàng là một kết quả riêng.

| Gate | Status | Bằng chứng |
|---|---|---|
| #821 AAC True Peak & QC | **PASS** | Trong phạm vi đã kiểm chứng. Quyết định A của chủ dự án (2026-11-14 00:57 UTC). [RELEASE_SAFETY_CLOSURE_REPORT.md](RELEASE_SAFETY_CLOSURE_REPORT.md) §5, §6, §12.1 (UI qua CDP trên DubFlow.exe rc18) và §12.5 (runtime đóng gói, không qua giao diện: fallback B1, tiếp tục cùng run, render lại với đầu vào không đổi), cùng unit test và CI. PR #822, #825. Đã ghi đóng trên issue #821. §1 của báo cáo vẫn ghi PENDING_OWNER_DECISION vì quyết định đến sau khi PR #827 đã merge; theo yêu cầu của chủ dự án, không mở PR mới chỉ để sửa dòng đó |
| #816 Product Source Security | **PASS** | Trong phạm vi đã kiểm chứng. Quyết định A (2026-11-14). [RELEASE_SAFETY_CLOSURE_REPORT.md](RELEASE_SAFETY_CLOSURE_REPORT.md) §8, §12.2: S1–S3 và S5–S8 qua UI và IPC của DubFlow.exe rc18, 15/15 phép thử IPC bị từ chối; S4 một phần (name-surrogate bị từ chối; nhánh reparse point không phải name-surrogate N/A, chưa có tệp thật nào được thử), W2.4b NOT VERIFIED. PR #823. Đã ghi đóng trên issue #816. §1 của báo cáo vẫn ghi PENDING_OWNER_DECISION (lý do như trên) |
| #817 Factual Claim Safety | **PASS** | Trong phạm vi đã kiểm chứng. Quyết định A (2026-11-14). [RELEASE_SAFETY_CLOSURE_REPORT.md](RELEASE_SAFETY_CLOSURE_REPORT.md) §9, §12.3: Kiểm tra cuối chặn xuất khi còn câu nghiêm trọng chưa xử lý; W3.5b NOT VERIFIED trên DubFlow.exe. PR #824. Đã ghi đóng trên issue #817. §1 của báo cáo vẫn ghi PENDING_OWNER_DECISION (lý do như trên) |
| Release Safety Closure | **PASS** | Trong phạm vi đã kiểm chứng. Quyết định A (2026-11-14), ghi đóng trên #821, #816, #817 và #463. Ba loại bằng chứng được tách riêng: Windows Desktop UI E2E (DubFlow.exe 1.1.96-rc18 điều khiển bằng tự động hoá CDP, không phải người duyệt) cho các hành trình hiện có; kiểm thử tích hợp engine đóng gói trên Windows (fallback B1, tiếp tục cùng run, chạy lại với đầu vào không đổi); unit test và CI. "Thử lại" trong UI tạo một run mới; tiếp tục cùng run chỉ được kiểm ở engine, chưa phải tính năng UI hoàn chỉnh. Không có P0 hay P1 chặn Stage 1 trong các ca đã chạy. Giữ nguyên các giới hạn đã ghi trong báo cáo của PR #827 (§16, §17.1) |
| Targeted Evidence ADR | **PROPOSED** | [ADR-0057](../adr/ADR-0057-targeted-evidence-acquisition.md) "Targeted Evidence Acquisition for Long-Form Assisted Editorial Retrieval", issue #815. Chưa được duyệt |
| Targeted Evidence Feasibility | **PASS** | Nghiên cứu đã hoàn tất. [TARGETED_EVIDENCE_FEASIBILITY_REPORT.md](TARGETED_EVIDENCE_FEASIBILITY_REPORT.md); harness offline PR #831 (thay #830; task #829), bản ghi chạy đã che dữ liệu ở [assisted-e2e/stage2](assisted-e2e/stage2/stage2-offline-results.md). Kết luận chính: định vị theo kịch bản và mốc thời gian **không được ủng hộ**. Quyết định đăng ký trước tại `c60ab02`: trên rc16, nhánh chính chọn được khung trúng cho 2 câu R3, so với P+ 1 và shuffled_prior 0, mức ngẫu nhiên 2,95, p = 1,0; chỉ thắng cả hai ở rc12, rc13, rc16 và chỉ vượt mức ngẫu nhiên ở rc13. Số liệu replay E3–E5 là **UNVERIFIED**: lần chạy `716e984` chưa kiểm khoá cache đầy đủ của production. Chúng sẽ được tính lại theo quy tắc khoá chính xác (`a558396`), và lần tính lại là exploratory. Quyết định E2 không phụ thuộc vào các số này. Khuyến nghị không triển khai Phương án B. Phần recall là mô phỏng một phần (tối đa 7,5 % khung được chọn có câu trả lời trong cache ở ngân sách matched (thêm 40 khung trên phần đã chi), 5,6 % ở 4x). **GENERALIZATION NOT VERIFIED** |
| Long-Form Production Gate B | **FAIL** | rc16 recall@5 31/60 (51,7 %) trên tập phát triển W3 (REAL-DEV); chưa có tập nghiệm thu độc lập. Stage 2 không tuyên bố Gate B |
| Human Validation Gate D | **BLOCKED** | Không có người tham gia; tự động hoá CDP không tính là người duyệt |
| Documentary Assisted Pilot Entry | **NOT READY** | Gate B FAIL và Gate D BLOCKED |
| Documentary Auto Gate S | **FAIL** | NO-GO, không đổi. Stage 1 và Stage 2 không thay đổi Semantic Auto; kết luận Gate S giữ nguyên như các khối bên dưới |

Targeted Evidence Feasibility không gộp với Long-Form Production Gate B, và một PoC offline không thay đổi kết luận Documentary Auto. Oracle (dùng nhãn thật) chỉ là cận trên: chọn đúng cửa sổ quanh cảnh thật và cho câu trả lời đúng (lấy từ nhãn) tại các khung đó cho 42/60 trên rc16 (+11), nhưng không tín hiệu nào không dùng nhãn thật đã thử đạt được mức đó; đây không phải kết quả có thể đạt trong production. Bước tiếp theo, nếu chủ dự án muốn tiếp tục, cần một lượt gọi thật có giới hạn được chủ dự án duyệt (tệp batch đã chuẩn bị, chưa chạy) và/hoặc một tập REAL-HOLDOUT mới (≥ 40 câu, ≥ 2 video dài độc lập, nhãn khoá trước khi đánh giá).

**Phase 4 vẫn NO-GO.** Không phát hành stable; bản mới nhất vẫn là candidate 1.1.96-rc18, `publish=false`. Không có code Phase 4.

## Cập nhật 2026-11-13: chỉ thị "Assisted Safety Hotfix & Long-Form Retrieval Feasibility"

Bản kiểm cuối: **1.1.96-rc16** (`205e3ff`, release run 43361381416, candidate, `publish=false`), Windows, chỉ CPU. Không phát hành stable. Mỗi hàng là một kết quả riêng; Feasibility không gộp với Production Gate.

| Gate | Status | Bằng chứng |
|---|---|---|
| #802 Semantic Safety | **PASS** | [ASSISTED_SAFETY_HOTFIX_REPORT.md](ASSISTED_SAFETY_HOTFIX_REPORT.md): bỏ sót không báo 5 → 0 trên bằng chứng rc12–rc15; vision mới rc16: READY 4/4, bỏ sót không báo 0/60, phải duyệt 56/60; 3 cảnh báo thừa mới (i1.u1 rc12, i9.u1 rc13 và rc15). PR #808 |
| #805 Exclusion Correctness | **PASS** | [WHOLE_SHOT_EXCLUSION_REPORT.md](WHOLE_SHOT_EXCLUSION_REPORT.md): W2 "Bỏ cả cảnh gốc" từ một mảnh chưa dùng; kế hoạch i1 không có đoạn nào chồng 6–12 s, video giải mã 0 lỗi, các khung trích ra đều ngoài 6–12 s; giữ 4/4 duyệt; còn sau khi đóng/mở; hoàn tác rồi dựng lại về đúng bản nháp. Sai lệch: chạy trên `i1-s1c2` vì rc16 không chia cảnh 0–6 s. PR #811 |
| Long-Form Retrieval Feasibility | **PASS** | [LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md](LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md): 36/36 câu trượt được gán tầng; nút thắt R3 (thu bằng chứng) 26/36; oracle thu + hiểu 56/60 so với oracle chỉ hiểu 34/60; replay 22/22. PR #810 |
| Long-Form Production Gate B | **FAIL** | rc16 recall@5 31/60 (51,7 %) trên tập phát triển W3; chưa có tập nghiệm thu độc lập (yêu cầu ≥ 85 % trên tập độc lập); 0 bỏ sót không báo, 0 SOURCE_INSUFFICIENT sai, HELD_FRAME 0 |
| Existing Localized Replanning Gate A | **PASS** | W2 rc16: dựng 1 phần, dùng lại 7, 0 INVALIDATED; W4 ghim/bỏ/thay 8,3 / 7,8 / 7,8 s, giữ 4/4 |
| Existing A/V Sync Gate C | **PASS** | sync_probe rc16: W2 7/7 chỗ nối, tệ nhất 33,3 ms; W3 21/21, 0 ms |
| Human Validation Gate D | **BLOCKED** | không có người tham gia; tự động hoá CDP không tính là người duyệt |
| Documentary Assisted Pilot Entry | **NOT READY** | Gate B FAIL và Gate D BLOCKED |
| Documentary Auto (Gate S) | **FAIL → NO-GO**, không đổi | Gate S thật `--replay` giống từng byte trước và sau #808 và #813 ([ASSISTED_SAFETY_HOTFIX_REPORT.md §7](ASSISTED_SAFETY_HOTFIX_REPORT.md)) |

Windows E2E trên rc16: W1 **PASS**, W2 **PASS** (có sai lệch ghi ở trên), W3 **FAIL** (Gate B), W4 **PASS**, W5 **PASS** với một phát hiện P2 (Bán hàng nhận file nguồn là symlink, chỉ đọc; Tổng hợp từ chối, #816). Human validation **BLOCKED**. Báo cáo đã che dữ liệu: [assisted-e2e/rc16](assisted-e2e/rc16/report-rc16.md).

- W4: Bán hàng Legacy và lồng tiếng với mastering "Tắt" chạy xong, giải mã 0 lỗi; Tổng hợp v1 trùng từng byte (`9afa62bc…c6d3`); huỷ cho CANCELLED rồi chạy lại dùng lại 3 phần đã xong; bản cuối có revision (lần xuất 2, "Kiểm tra cuối: đạt.").
- W5: IPC 8/8 lời gọi sai bị từ chối, 0 file đổi; thư mục kết quả là junction bị từ chối ở cả hai luồng; chuỗi filter/shell hiện nguyên văn; review v2 đọc được, v99 bị từ chối và không bị ghi đè; quét 3 567 file: claude 0 / gemini 0 / email 0, đường dẫn hồ sơ 0.

Hiệu năng (rc15 → rc16, Windows CPU): bản nháp W1 166 → 135 s, W2 255 → 255 s, W3 721 → 676 s; dựng lại W2 sau một thao tác 12,7 → 13,0 s (hoàn tác 12,2 s); RAM đỉnh W2 3250 MB, W3 3438 MB, W1 3903 MB (cửa sổ đo gồm cả tổng hợp v1 và lần huỷ). Vision W3: 142 khung ngân sách, 138 câu hỏi gửi đi.

Đã merge sau rc16: PR #813 (#812, thứ tự từ trong ghi chú duyệt, chỉ hiển thị). Còn mở: #816 (P2-N26), #817 (P2-N27: i9.u1 và dòng khẳng định của Kiểm tra cuối), #818 (P3-N28), P3-N21, #765. Đề xuất tiếp theo: thu bằng chứng có mục tiêu cho phim dài (Phương án B), issue #815, cần ADR trước khi viết code, xem [LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md §17](LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md).

**Phase 4 vẫn NO-GO.** Gate S vẫn FAIL; không có code Phase 4.

## Cập nhật 2026-11-12: chỉ thị "Assisted Production Stabilization & Documentary Pilot Entry Gates"

Bản kiểm cuối: **1.1.96-rc15** (`6b1a0cf`, release run 43298731065, candidate, `publish=false`), Windows, chỉ CPU. Không phát hành stable. Mỗi hàng là một kết quả riêng.

| Hạng mục | Kết quả | Bằng chứng |
|---|---|---|
| Assisted Production Stability | **PASS** | [LOCALIZED_REPLANNING_REPORT.md](LOCALIZED_REPLANNING_REPORT.md): W1 giữ 4/4 duyệt, huỷ 0 sau Ghim / Bỏ / Thay; W2 dựng 1 phần, dùng lại 7, HELD_FRAME 0, xuất "Kiểm tra cuối: đạt."; W5 trên W2 dựng 0, dùng lại 8. PR #782, #797, #798, #803, #804 |
| Long-Form Assisted Readiness | **FAIL** | [LONG_FORM_EVIDENCE_REPORT.md](LONG_FORM_EVIDENCE_REPORT.md): recall@5 29/60 (48,3 %) so với ≥ 85 %; 3 bỏ sót không báo (i1.u1, i1.u2, i16.u1); 0 SOURCE_INSUFFICIENT sai; HELD_FRAME 0. Nhãn khoá sha256 `afa979d2…f66497`, không sửa. Backlog #802 |
| Documentary Assisted Pilot Entry | **NOT READY FOR PILOT IMPLEMENTATION** (thiết kế PASS) | [DOCUMENTARY_PILOT_ENTRY_GATES.md](DOCUMENTARY_PILOT_ENTRY_GATES.md): cổng B FAIL, cổng D BLOCKED ([HUMAN_EDITORIAL_VALIDATION_REPORT.md](HUMAN_EDITORIAL_VALIDATION_REPORT.md): không có người tham gia; tự động hoá CDP không tính là người duyệt) |
| Documentary Auto (Gate S) | **FAIL → NO-GO**, không đổi | Không có thay đổi nào trong vòng này; xem các mục bên dưới |
| Windows E2E | W1 **PASS**, W2 **PASS**, W3 **FAIL** (Gate B), W4 **PASS**, W5 **PASS**, W6 **BLOCKED** | [assisted-e2e/rc15](assisted-e2e/rc15/); rc13, rc14 ở [assisted-e2e/rc13](assisted-e2e/rc13/), [assisted-e2e/rc14](assisted-e2e/rc14/). Gate C: 28/28 chỗ nối đo được, tệ nhất 33,3 ms ([AV_BOUNDARY_SYNC_REPORT.md](AV_BOUNDARY_SYNC_REPORT.md)) |
| Security/Compatibility | **PASS** | Tổng hợp v1 xuất trùng từng byte với rc13 (sha256 `9afa62bc…c6d3`); huỷ một lần dựng cho CANCELLED, không bao giờ COMPLETED; quét khoá và e-mail (ứng dụng đã đóng) trên 10.917 file, 4,89 GiB của sandbox rc15 (thư mục dữ liệu, đầu ra, log…): claude 0 / gemini 0 / email 0 |

Hiệu năng (rc15, Windows CPU): bản nháp W1 166 s, W2 255 s, W3 721 s; dựng lại sau một thao tác: W1 8,3 / 7,7 / 7,8 s, W2 12,7 s (1 phần), W5 trên W2 25,9 s (0 phần); RAM đỉnh 3,0–3,5 GB.

**Phase 4 vẫn NO-GO.** Không có code Phase 4 trong vòng này.

## Cập nhật 2026-11-12: chỉ thị "Assisted Editorial Production"

Mỗi chế độ có kết quả riêng. Đổi tên một chế độ không biến FAIL thành PASS.

| Hạng mục | Kết quả | Bằng chứng |
|---|---|---|
| Semantic Auto (Gate S) | **FAIL**, không đổi | REAL-HOLDOUT coverage 26,8 %, READY 0/20. Không hạ ngưỡng nào. Bản nháp rc9 có 0/28 câu READY |
| Assisted Safety | **PASS** | [ASSISTED_SAFETY_CLOSURE_REPORT.md](ASSISTED_SAFETY_CLOSURE_REPORT.md): #764 qua #769, #766 qua #770, #765 phân loại không đổi code, Windows Kịch bản A trên rc8 và rc9 |
| Assisted Production (Bán hàng / Tổng hợp) | **PASS** (Quyết định 1), còn mở P2-N5, P2-N11 | [ASSISTED_EDITORIAL_PRODUCTION_REPORT.md](ASSISTED_EDITORIAL_PRODUCTION_REPORT.md): hàng đợi, hợp đồng quyết định, kiểm tra cuối; sau duyệt 0 câu sai trên 28 câu (rc9) và 0 trên 30 câu (rc8), do agent chấm |
| Documentary Assisted Pilot | **READY FOR LIMITED DESIGN/PILOT, có điều kiện** (Quyết định 2) | [DOCUMENTARY_ASSISTED_READINESS.md](DOCUMENTARY_ASSISTED_READINESS.md). Chỉ là thiết kế, chưa có code |
| Documentary Auto | **NO-GO** (Quyết định 3) | Gate S FAIL |
| Windows E2E | **PASS** trên rc9 và rc10 (bản kiểm cuối `a4ec540`) cho A–E; F (phim dài) bị kiểm tra cuối chặn đúng | [assisted-e2e/rc8](assisted-e2e/rc8/report-rc8.md), [assisted-e2e/rc9](assisted-e2e/rc9/report-rc9.md) |
| Security/Compatibility | **PASS** | junction bị từ chối ở cả hai luồng; v1 trùng từng byte; schema lạ bị từ chối; quét khoá và e-mail 0/0/0 |

**Phase 4 vẫn NO-GO.** Gate S vẫn FAIL, và vẫn không có code Phase 4.

## Quyết định (Gate S final closure): **NO-GO**

Hai cổng không đạt:

**Gate S (độ chính xác ngữ nghĩa): FAIL.** Kết quả trên REAL-HOLDOUT (42 câu từ 6 video thật mới, nhãn khoá hash trước khi chạy, chấm một lần):
- Coverage chỉ đạt 26,8 % so với ngưỡng 90 %.
- Không có câu nào READY.
- Mọi câu đúng đều bị gắn cờ.
- Phần an toàn đạt: 0 bỏ sót im lặng nghiêm trọng, phát hiện lỗi nghiêm trọng 22/22.

**Release Gate (desktop): FAIL.** Ba lỗi P2-5, P2-6, P2-14 đã được sửa và kiểm qua UI thật của rc7. Tuy vậy, D3 phát hiện một P1 mới: câu đầu của Bán hàng được READY trên cảnh không liên quan (#764).

Vòng hiệu chỉnh có kiểm soát đã chạy một lần (#763) rồi dừng, theo §15 của chỉ thị. Không ngưỡng nào bị hạ, không nhãn nào bị sửa sau khi xem kết quả.

## Bảng cổng

| Gate | Result | Evidence |
|---|---|---|
| Phase 3 Closure | **PASS** | [PHASE3_CLOSURE_REPORT.md](PHASE3_CLOSURE_REPORT.md). rc7 smoke lồng tiếng qua UI cho đầu ra hợp lệ ([rc7 §5](gate-s-real/rc7/report-rc7.md)) |
| Semantic Gate S | **FAIL** | [REAL_WORLD_SEMANTIC_CALIBRATION_REPORT.md](REAL_WORLD_SEMANTIC_CALIBRATION_REPORT.md) §19. REAL-HOLDOUT: coverage 25168/93810, READY 0/20, cờ thừa 6/6. Ba bộ hồi quy thật kém hơn rc6 trên cùng bằng chứng (#765) |
| Desktop Release Gate | **FAIL** | [DESKTOP_RELEASE_BLOCKERS_REPORT.md](DESKTOP_RELEASE_BLOCKERS_REPORT.md) §9. D1, D2 PASS; D3 nguồn gốc bằng chứng PASS nhưng cờ duyệt sai (P1-N1 #764); D4, D5 PASS |
| Compilation Gate C | **PASS** | [COMPILATION_DESKTOP_REPORT.md](COMPILATION_DESKTOP_REPORT.md). rc7: Tổng hợp thô v2 qua UI (D1, D2), v1 trùng từng byte với rc4–rc6 |
| Timeline Regression | **PASS** | Bộ hồi quy cố định giống từng byte trước/sau ([REAL_WORLD §14](REAL_WORLD_SEMANTIC_CALIBRATION_REPORT.md)). Lệch A/V trên sáu video như rc6; P2-9 (Why We Fight −41,3 ms ở bước ghép) vẫn mở |
| Windows E2E | **FAIL** | [gate-s-real/rc7/report-rc7.md](gate-s-real/rc7/report-rc7.md). D1–D5 và sáu video đều đã chạy trên DubFlow.exe rc7, có chứng minh tiến trình CDP. D3 không đạt hoàn toàn (P1-N1) |
| Security/Compatibility | **PASS** | rc7 D5: junction bị từ chối, nguồn không đổi, quét khoá/e-mail 8 759 file = 0 (bộ quét đã sửa). IPC chỉ kiểm trong CI. Đính chính: quét khoá Claude của rc5/rc6 không có giá trị (E-1) |

## Điều kiện GO (chỉ thị §20)

| Điều kiện | Kết quả |
|---|---|
| Gate S PASS | ✗ |
| Release Gate PASS | ✗ (P1-N1) |
| Compilation Gate C tiếp tục PASS | ✓ |
| Phase 3 không regression | ✓ |
| Windows E2E đạt | ✗ (D3) |
| Bảo mật đạt | ✓ |
| Không còn P0 | ✓ |
| Bằng chứng nghiệm thu rõ ràng | ✓ |
| Không đổi tiêu chí sau khi biết kết quả | ✓ |

## Hướng xử lý tiếp theo (chi phí/lợi ích)

1. **Assisted Editorial Review**, phương án sản phẩm, không phải đạt Gate S.
   - Với Tổng hợp và Bán hàng, AI tạo bản nháp. Người dùng duyệt các câu bị gắn cờ trong bảng Duyệt cảnh, đã có sẵn ghim/bỏ/duyệt, cảnh thay thế và tín hiệu từng câu.
   - Đo trên REAL-HOLDOUT: phải xem 42/42 câu, khoảng 6–7 câu mỗi video. Hệ thống không bỏ sót im lặng, ngoại trừ câu mở đầu của Bán hàng (P1-N1).
   - Giới hạn: không câu nào được READY tự động.
   - Điều kiện tiên quyết: sửa P1-N1 trước.
2. **Sửa P1-N1 (#764)**, nhỏ: câu mở đầu nêu thông tin cụ thể mà không có khớp nội dung thì phải gắn cờ.
3. **Xem lại #763 trên ba bộ hồi quy thật (#765)**, và sửa P2-N3 (#766: ghi `status.json` thử lại khi bị khoá, giữ stderr của engine).
4. **Phân bổ toàn cục cho nguồn chỉ đi tới, và ngân sách vision thích ứng.** Là thay đổi thuật toán và chi phí, cần một vòng đánh giá mới với tập held-out mới.
5. **Khớp ngữ nghĩa bằng mô hình** thay cho khớp từ ngữ. Cần chỉ thị riêng, vì chỉ thị hiện tại cấm thêm model.

## Phần đã đạt và được giữ lại

- P2-5: ghim và duyệt bền sau khi dựng lại và mở lại app; chỉ bị huỷ khi có lý do thật.
- P2-6: Bản cuối có revision và sha256, hiện "cũ" sau khi dựng lại.
- P2-14: tên file không bao giờ là bằng chứng nội dung.
- Lỗi VFR và lỗi vision thất bại im lặng (#761).
- Bộ đánh giá footage thật: 83 câu khoá hash, bản ghi lời gọi planner, replay tất định.
- 5 tín hiệu tách riêng trong log từng câu.
- Gate C.

## Không làm

- Không có code Phase 4.
- Không phát hành bản stable: rc7 là bản candidate với `publish=false`.
