# DubFlow — Sẵn sàng cho Phase 4 (Phim tài liệu): GO / NO-GO

Epic #637 · 2026-11-11 · main `005344e`

## Quyết định: **NO-GO**

Chưa bắt đầu Phase 4. Mastering Lồng tiếng (Phase 3) và MVP desktop cho Bán hàng và Lồng tiếng (Phase 5) đã đạt trên Windows thật. Còn hai cổng P0 chưa đạt, và chỉ thị không cho phép dời P0 sang phase sau để tuyên bố xong:

1. **Độ phủ ngữ nghĩa trên bộ held-out vẫn dưới 90 %.** holdout 82,8 %, holdout2 78,4 %. Phase 4 dùng chính lớp chọn cảnh theo lời dẫn này cho phim tài liệu, nên điểm yếu sẽ chuyển thẳng sang đó. Bộ độc lập mới holdout4 đạt 100 %, nhưng chỉ có 20 câu.
2. **Tổng hợp chưa có chọn mức Tối ưu hậu kỳ trên desktop.** Desktop Tổng hợp vẫn dùng spec v1 (dựng từ video đã lồng tiếng). Planner Tổng hợp mới chỉ chạy được qua engine/MCP.

## Bảng cổng

| Cổng | Nội dung | Kết quả | Bằng chứng |
|---|---|---|---|
| A | Phase 3 đóng trên Windows, runtime đóng gói | **ĐẠT** | PHASE3_CLOSURE_REPORT (rc1, rc2, rc3) |
| B | Sửa ngữ nghĩa (5 lỗi, bỏ sót không báo = 0) | **ĐẠT**. Độ phủ held-out < 90 %: **KHÔNG ĐẠT** | SEMANTIC_REMEDIATION_REPORT |
| C | Chọn mức theo workflow | Bán hàng ✓, Lồng tiếng ✓, **Tổng hợp ✗** | PHASE5_MVP_REPORT |
| D | Bảng Duyệt cảnh + QC, lưu bền, cổng xuất | **ĐẠT** (rc3, sau khi sửa D1/D2) | PHASE5_MVP_REPORT §5 |
| E | Hiệu năng trên CPU | **ĐẠT**. Mastering thêm khoảng 5–6 s / 100 s; panel 0,5 s; render lại 6,4 s | PHASE3 §6, PHASE5 §5 |
| F | Bảo mật, khóa, không AI ở Tắt | **ĐẠT**. 0 lần lộ khóa qua 3 lần quét; Tắt 0 mạng | PHASE3 §4, PHASE5 §4 |
| Scenario 7 | Hành trình desktop thật | **ĐẠT** qua CDP trên DubFlow.exe thật. Không dùng computer use | PHASE5_MVP_REPORT §5 |

## Điều kiện để chuyển sang GO

1. Độ phủ ≥ 90 % trên một bộ held-out mới chưa từng xem, đủ lớn (≥ 40 câu), và giữ 0 bỏ sót không báo. Không chỉnh ngưỡng trên holdout và holdout2.
   - Nhiều khung hình mỗi ứng viên.
   - Ghép tên riêng song ngữ (Kavkaz / Caucasus).
   - Giảm cảnh báo thừa trên dev2/dev3 (từ 33 tăng lên 42 trên 133 câu).
2. Chọn mức cho Tổng hợp trên desktop, kèm spec v2 từ nguồn thô, và kiểm trên Windows như Scenario 7.
3. Nên làm, không chặn:
   - D3: số câu cần xem lại trên thẻ run;
   - D7: dùng lại bản trộn B2 khi chạy lại;
   - một lần kiểm Scenario 7 bằng chuột thật nếu được cấp computer use cho bản rc.

## Không làm

Không có code Phase 4 nào. Không phát hành bản stable: rc1, rc2 và rc3 là bản candidate với `publish=false`.
