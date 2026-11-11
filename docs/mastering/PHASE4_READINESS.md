# DubFlow — Sẵn sàng cho Phase 4 (Phim tài liệu): GO / NO-GO

Epic #637 · 2026-11-12 · cập nhật sau chỉ thị "DUBFLOW — GATE S FINAL CLOSURE" · main sau #763 (bản kiểm cuối: 1.1.96-rc7 = `7acd262`, candidate, `publish=false`)

## Quyết định: **NO-GO**

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
