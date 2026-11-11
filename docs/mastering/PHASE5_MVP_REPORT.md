# DubFlow — Báo cáo Phase 5 MVP (Workstream C)

Epic #637 · 2026-11-11 · main `005344e` · bản candidate 1.1.96-rc2 và rc3 (không phát hành)

## Trả lời thẳng

| Hạng mục | Code + test | Windows CI | Hành trình desktop thật (Scenario 7) |
|---|---|---|---|
| Chọn mức Tối ưu hậu kỳ: Bán hàng | ✓ #704 | ✓ | ✓ rc2 + rc3 |
| Chọn mức: Lồng tiếng | ✓ #708 | ✓ | ✓ rc2 + rc3 |
| Chọn mức: Tổng hợp | ✗ chưa làm | — | — |
| Bảng Duyệt cảnh | ✓ #702, #704, #712 | ✓ | ✓ rc2 (lỗi D2) → ✓ rc3 |
| Bảng QC Hậu kỳ Lồng tiếng | ✓ #708 | ✓ | ✓ rc2 (lỗi D1, D5) → ✓ rc3 |
| Bảo mật Tauri | ✓ #704, #708, #712, #717 | ✓ (junction test trên Windows) | ✓ rc2 (junction, symlink bị từ chối) |

**Tổng hợp chưa có chọn mức:** desktop Tổng hợp vẫn dùng spec v1, dựng từ video đã lồng tiếng, nên chưa có đường mastering. Planner Tổng hợp (#710) chỉ chạy qua engine/MCP. Mục này chưa xong và không được tính là đạt.

## 1. Chọn mức theo workflow

- Bốn mức: Tắt / Nhẹ / Cân bằng / Mạnh. Mô tả của mỗi mức viết riêng cho từng workflow; ở Lồng tiếng, Mạnh "chỉ gợi ý" cao trào.
- Bán hàng: request có `mastering` chỉ khi bật. Mẫu "chỉ chữ" không nhận mức.
- Lồng tiếng: mức đi theo từng video trong hàng chờ và được lưu làm mặc định (`dubflow.defaults.v2`). Host kiểm mức trước khi tạo thư mục rồi ghi `mastering.json`; mức Tắt xóa file.
- Mức Tắt không ghi plan và không gọi AI. Lồng tiếng không bao giờ cần Gemini.

## 2. Bảng Duyệt cảnh (Bán hàng)

- Trạng thái mỗi câu: READY / NEEDS_REVIEW / MATCH_NOT_FOUND / SOURCE_INSUFFICIENT, cùng USER_APPROVED và INVALIDATED cho quyết định của người dùng.
- Thao tác: duyệt, bỏ duyệt, ghim (cũng dùng để thay cảnh), loại, đặt lại, hoàn tác, xác nhận cảnh báo, chấp nhận xuất khi còn câu chưa duyệt.
- Mỗi thao tác được lưu ngay vào `review-<n>.json` với `schema_version` và `revision`. Bản ghi có phiên bản hoặc trường lạ bị từ chối.
- Render lại: chỉ video được chọn (`only: [n]`), ghi vào `spec-rerender.json`, còn `spec.json` giữ nguyên; các video khác được giữ.
- Cổng xuất (#712) đóng khi có lỗi: không đọc được review, review sai dạng, hoặc lưu thất bại thì không mở trình xuất.

## 3. Bảng QC Hậu kỳ (Lồng tiếng)

Bảng chỉ đọc `qc_report.json` của bản xuất, không tính lại. Các hàng:
- âm lượng: LUFS trước/sau, TP sau AAC so với trần −1,5 dBTP;
- màu;
- khung hình;
- quay về B1;
- gợi ý cao trào với nút Giữ/Bỏ. Lựa chọn chỉ được ghi lại, không bao giờ chèn vào video.

## 4. Bảo mật Tauri

| Yêu cầu | Cách làm |
|---|---|
| Kiểm IPC | Mức, mã câu, mã cảnh, thao tác, số video đều kiểm ở host (Rust) theo danh sách cho phép; số lượng id có giới hạn |
| Không đọc file tùy ý | Thư mục run phải nằm dưới "DubFlow Output". Mọi thư mục từ đó xuống, cùng `.dubflow-work`, phải là thư mục thường (không symlink/junction/reparse point) và được kiểm chứa sau khi canonicalize. Keyframe và `qc_report.json` mở bằng `FILE_FLAG_OPEN_REPARSE_POINT` / `O_NOFOLLOW` và kiểm lại trên handle đã mở |
| Không rò khóa | Khóa chỉ vào env của tiến trình con và bị `pop` ngay khi đọc. Route vision gửi lên chỉ có tên nhà cung cấp. Quét khóa rc1: 0 |
| Không shell injection | Mọi tiến trình con chạy bằng `Command` với argv, không qua shell |
| CSP | Giữ nguyên, không thêm nguồn mới |
| Không ghi vào thư mục nguồn | Mọi thứ ghi vào thư mục output / `.dubflow-work` |
| Không có bộ lọc FFmpeg thô từ UI | UI chỉ gửi tên mức. Bộ lọc do planner sinh và adapter chỉ nhận `crop`/`scale`/`setsar`/`colorlevels` qua regex cho phép |
| Schema migration | File review/QC có `schema_version`. Phiên bản lạ bị từ chối, file thiếu nghĩa là chưa có quyết định. `dubflow.defaults.v1` được chuyển lên v2 |

## 5. Hành trình desktop thật (Scenario 7)

Chạy trên DubFlow.exe thật của bản rc2 rồi rc3 trên Windows 11 (i7-12700H, chỉ CPU), với thư mục dữ liệu riêng, không đụng bản stable.

**Cách điều khiển.** Không dùng được computer use: tên "DubFlow" chỉ phân giải được về bản stable, và yêu cầu quyền đã bị từ chối. Vì vậy:
- click, gõ và chụp màn hình qua cổng debug WebView2 của chính tiến trình rc (CDP, thao tác chuột thật trên DOM thật);
- hộp thoại chọn file gốc của Windows điền bằng Win32.

Không gọi IPC trực tiếp và không sửa file của app để giả lập.

| Bước | rc2 | rc3 |
|---|---|---|
| Lồng tiếng: chọn Cân bằng, chạy, thẻ Hậu kỳ với số thật | ✓ (−16,6 → −18,5 LUFS, −2,5 dBTP sau AAC; khớp đo độc lập) | ✓ |
| Lồng tiếng Mạnh: gợi ý cao trào, Giữ/Bỏ, mở lại | ✗ D1: không bao giờ có gợi ý | ✓ 1:15–1:20, Giữ/Bỏ được lưu sau khi mở lại, video trùng từng byte trước và sau |
| Thẻ QC nói lý do khi âm lượng bị hạ | ✗ D5 | ✓ |
| Bán hàng: render Cân bằng, 2 video | ✓ 113 s | ✓ 110 s |
| Duyệt cảnh: duyệt / ghim / loại / hoàn tác | ✓ (mở panel 0,53 s) | ✓ |
| Đóng rồi mở lại app: quyết định còn | ✓ trên đĩa, nhưng ✗ D2: run không hiện trong danh sách | ✓ run hiện lại, quyết định còn |
| Phương án thay thế không trùng cảnh đang dùng | ✗ D4 | ✓ 0/26 |
| Xuất khi còn câu chưa duyệt: hỏi lại, Hủy là chặn | ✓ | — (không đổi) |
| Render lại 1 video, video kia giữ nguyên hash | ✓ 6,4–6,6 s | ✓ 6,4 s |
| Junction ở keyframe, symlink ở review | ✓ bị từ chối, file đích không bị ghi | — |
| File nguồn không đổi | ✓ 6/6 SHA-256 | ✓ |
| Quét khóa (Gemini và Claude) | 0 / 11 018 file | 0 / 10 049 file |

**Gate E** (app thật, CPU):
- Lồng tiếng lạnh có tách nền mất 142–224 s, RAM đỉnh 3,5–3,6 GB.
- Bán hàng 2 video mất khoảng 110 s, RAM đỉnh 2,9–4,0 GB.
- Mở panel Duyệt cảnh mất 0,53–0,56 s. Render lại 1 video mất 6,4 s.

**Không kiểm được:**
- Cảnh báo planner còn sau khi mở lại: bộ espresso không sinh cảnh báo nào.
- Dòng B2→B1 trên thẻ desktop: không có fallback thật. Nhánh này đã kiểm ở rc1 bằng harness.

**Còn mở (thấp):**
- D3: thẻ run giữ số "N câu cần xem lại" lúc render cho tới khi render lại.
- D6: khi một bộ lọc hình bị từ chối, plan bỏ mọi bộ lọc màu.
- D7: chạy lại không đổi vẫn trộn B2, khoảng 4 s.
- D8: LRA hiển thị lấy từ loudnorm.

## 6. Chưa làm

- Chọn mức cho Tổng hợp trên desktop (cần spec v2 từ nguồn thô).
- Nút "tạo lại" từng câu, ngoài ghim/loại và render lại.
