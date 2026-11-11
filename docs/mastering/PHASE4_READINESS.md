# DubFlow — Sẵn sàng cho Phase 4 (Phim tài liệu): GO / NO-GO

Epic #637 · 2026-11-11 · cập nhật sau chỉ thị "PHASE 4 READINESS GATE CLOSURE" · main sau #749 (bản kiểm cuối: 1.1.96-rc6 = `85b9d81`)

## Quyết định: **NO-GO**

Gate C (Tổng hợp từ video gốc trên desktop) **đạt**. Gate S (độ chính xác ngữ nghĩa) **không đạt**. Chỉ thị: "Nếu chỉ một Gate đạt, kết luận vẫn là NO-GO; phần đã đạt phải được nghiệm thu riêng và giữ lại."

Lý do Gate S không đạt:

- Bộ held-out độc lập holdout7 đạt 100 %, 0 bỏ sót im lặng.
- Nhưng trên video thật, gần như mọi câu bị gắn cờ cần xem lại (Top 5 trên desktop: 13/14), vì độ tin cậy của khớp chỉ từ AI hiếm khi vượt ngưỡng (P1-5). Cờ không còn phân biệt câu tốt và câu xấu, trái với S5 và với quy tắc "không để `needs_review` trên mọi câu thành cách đạt".
- Coverage của ba video bán hàng thật chỉ 50–72 % (P1-6). Bộ held-out tổng hợp không đại diện cho mô tả AI dài, nhiều từ chung của video thật.
- Không ngưỡng nào được hạ và không P1 nào được dời sang Phase 4 để đổi kết luận.

## Bảng cổng

| Gate | Status | Evidence | Open Risks |
|---|---|---|---|
| Phase 3 Closure | **PASS** | [PHASE3_CLOSURE_REPORT.md](PHASE3_CLOSURE_REPORT.md) (rc1–rc3) | – |
| Semantic Accuracy | **FAIL** | [SEMANTIC_GATE_CLOSURE_REPORT.md](SEMANTIC_GATE_CLOSURE_REPORT.md), [gate-s/](gate-s/), [gate-c/report-rc6.md](gate-c/report-rc6.md) §3 | P1-5 hiệu chỉnh độ tin cậy trên video thật; P1-6 coverage video bán hàng 50–72 %; cờ thừa cao; #749 chưa chạy trên Windows |
| Compilation Desktop | **PASS** | [COMPILATION_DESKTOP_REPORT.md](COMPILATION_DESKTOP_REPORT.md), [gate-c/report-rc5.md](gate-c/report-rc5.md) | 11 lỗi P2 về hiển thị/thông báo (mục 19 của báo cáo) |
| Timeline Regression | **PASS** | SEMANTIC_GATE_CLOSURE_REPORT §15; gate-c/report-rc6.md §3 | P2-9: Why We Fight lệch A/V −41 ms ở bước ghép phần (mỗi phần ≤ 20 ms) |
| Windows E2E | **PASS** | gate-c/report-rc4.md, report-rc5.md, report-rc6.md: C1–C4 qua UI thật của DubFlow.exe bản rc (CDP + hộp thoại Win32), có chứng minh tiến trình; sáu video thật chạy lại trên runtime đóng gói | File không đọc được không thử được qua UI (hộp thoại Windows từ chối); RAM đỉnh bản nháp C1 không đo |
| Security/Compatibility | **PASS** | COMPILATION_DESKTOP_REPORT §17–18 | v1: 24/24 argv giống, video giống từng byte; quét khóa 0 lần trùng (3 vòng); không ghi ngoài vùng cho phép |

## Điều kiện GO (chỉ thị §20)

| # | Điều kiện | Kết quả |
|---|---|---|
| 1 | Semantic Gate đạt | ✗ |
| 2 | Held-out mới ≥ 40 câu đạt ≥ 90 % | ✓ holdout7 100 % (42 câu, 9 nhóm) |
| 3 | 0 bỏ sót im lặng | ✓ held-out, hồi quy, sáu video thật |
| 4 | Không có regression ngữ nghĩa nghiêm trọng chưa giải quyết | ✗ P1-5, P1-6 trên video thật |
| 5 | Tổng hợp video gốc chạy qua desktop | ✓ |
| 6 | Chọn mức tối ưu của Tổng hợp chạy được | ✓ |
| 7 | Quyết định duyệt được lưu và dựng lại được | ✓ |
| 8 | Spec v1 không bị phá | ✓ |
| 9 | C1–C4 kiểm trên Windows | ✓ (C4 có ngoại lệ đã ghi) |
| 10 | Timeline, bảo mật, cache đạt | ✓ |
| 11 | Không còn P0 | ✓ |
| 12 | Bằng chứng ghi rõ | ✓ |

## Việc cần làm để chuyển sang GO

1. Tạo bộ held-out từ **video thật**: dùng mô tả AI đã lưu của các lần chạy (không gọi lại), khoá nhãn bằng hash trước khi chạy.
2. Hiệu chỉnh độ tin cậy của khớp chỉ từ AI (P1-5) trên các bộ dev, không trên video E2E hay bộ held-out mới. Đo precision/recall của cờ, rồi đánh giá một lần trên bộ held-out thật.
3. Bản candidate mới (gồm #749) chạy lại sáu video thật và desktop Top 5 trên Windows.
4. Không chặn GO, nên làm: các P2 của desktop (Compilation report §19), P2-9 lệch ở bước ghép.

## Phần đã đạt và được giữ lại

- Tổng hợp từ video gốc trên desktop (spec v2): nhập nguồn, thiết lập chương trình, bốn mức tối ưu, bảng Duyệt cảnh có mục/thứ hạng/lời bình/khoảnh khắc nguồn, ghim/bỏ/duyệt/INVALIDATED, xem trước, bản nháp và cổng xuất bản cuối, lưu bền, dựng lại từng phần.
- Đường Tổng hợp cũ (v1) không đổi.
- Ghép tên riêng đa ngôn ngữ (#723), bằng chứng nhiều khung hình (#725), kiểm tra A–F, các sửa lỗi từ video thật (#745, #747, #749).

## Không làm

Không có code Phase 4 nào. Không phát hành bản stable: rc4, rc5 và rc6 là bản candidate với `publish=false`.
