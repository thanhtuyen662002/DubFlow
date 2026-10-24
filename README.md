# DubFlow Local

Local-first, one-click video localization pipeline focused on Douyin and Bilibili.

## Hướng dẫn sử dụng

DubFlow chuyển video tiếng Trung/Anh thành video tiếng Việt **ngay trên máy bạn**: nhận dạng lời thoại, dịch, tạo phụ đề và (tùy chọn) lồng tiếng Việt. Không cần GPU, không gửi video lên dịch vụ đám mây.

### Cài đặt (Windows 10/11 x64)
1. Vào [Releases](https://github.com/thanhtuyen662002/DubFlow/releases), tải `DubFlow-<phiên bản>-windows-x64-setup.exe`.
2. Chạy file cài đặt. Ứng dụng tự mở sau khi cài xong (không cần cài Python hay Node).
3. Lần xử lý đầu tiên sẽ tải model nhận dạng/dịch/giọng đọc (vài trăm MB); các lần sau chạy ngoại tuyến.

### Xử lý một video
1. **Thêm video**: kéo thả file vào cửa sổ, bấm **Thêm video**, hoặc dán link vào ô **Dán link…** rồi bấm **Tải link**.
2. Chọn video trong danh sách. Bật **Lồng tiếng Việt** nếu muốn có giọng đọc (chọn giọng ở danh sách bên dưới); tắt để chỉ tạo phụ đề và giữ audio gốc.
3. Bấm **Bắt đầu xử lý** và theo dõi các bước ở bảng bên phải. Có thể đóng ứng dụng giữa chừng — lần sau sẽ tiếp tục từ checkpoint.
4. Khi xong, bấm **Mở video** hoặc **Mở thư mục**. Kết quả gồm `final_vi.mp4`, phụ đề `captions_vi.srt` / `captions_vi.ass` và gói chỉnh sửa trong thư mục `editable`.

Video thêm từ máy được xuất vào thư mục `DubFlow Output` cạnh video gốc; video tải từ link được xuất vào `Videos\DubFlow Output`.

### Sửa bản dịch
Bấm **Xem & sửa bản dịch** ở video đã xong. Mỗi dòng hiện câu gốc và câu tiếng Việt; câu lồng tiếng lỗi hoặc đọc quá nhanh được đánh dấu. Sửa câu (ngắn gọn, tự nhiên hơn) rồi bấm **Lưu & xử lý lại** — DubFlow chỉ chạy lại phần cần thiết và chỉ lồng tiếng lại các câu đã sửa.

### Link Bilibili / Douyin
- **Link video trực tiếp** (`.mp4`, …): dán và tải ngay.
- **Bilibili**: dán link video `bilibili.com/video/BV…`. Nếu trang báo giới hạn truy cập, đợi vài phút rồi thử lại.
- **Douyin**: lần đầu, dòng tải sẽ báo cần phiên truy cập. Bấm **Mở Douyin**, đợi trang hiện video (có thể đăng nhập nếu muốn), rồi bấm **Lưu phiên & thử lại**. Phiên được mã hóa trên máy và hết hạn sau khoảng 23 giờ.

Chỉ tải và xử lý video bạn có quyền sử dụng.

### Gặp lỗi?
- Thông báo lỗi hiện ngay trong dòng video; có thể bấm **Xử lý lại với tùy chọn khác** để tạo bản mới (bản cũ vẫn được giữ).
- Báo lỗi tại [Issues](https://github.com/thanhtuyen662002/DubFlow/issues), kèm thông báo lỗi và file `job_manifest.json` / `qc_report.json` trong thư mục kết quả nếu có.

## For contributors

GitHub is the durable engineering control plane for requirements, architecture, execution state, CI evidence, risks, decisions, Issues, pull requests, and releases.

### Start here

1. `AGENTS.md` — engineering constitution and non-negotiable invariants.
2. `docs/PRODUCT_SYSTEM_DESIGN.md` — product, UX, backend/frontend and AI pipeline design.
3. `docs/RISK_REGISTER.md` — product + engineering kill paths and recovery rules.
4. `docs/ENGINEERING_EXECUTION_PROTOCOL.md` — Issue/PR leases, anti-stall rules and CI lanes.
5. `docs/PROJECT_STATE.yaml` — durable current project state.
6. `docs/AUDIT_BOOTSTRAP.md` — bootstrap architecture/delivery audit.
7. `docs/adr/` — accepted architecture decisions.

Agents must not begin implementation from an Epic and must not push directly to `main`.
