# DubFlow — Backlog Phase 5 (Mastering)

Epic #637 · 2026-11-11. Nguồn: báo cáo Phase 2 Quality Hardening, báo cáo Phase 3, E2E Windows lần 8 và lần 9.

## 1. Duyệt và phê duyệt (UI review/approval)

- Màn hình duyệt cảnh theo câu (Bán hàng, Tổng hợp), hiển thị cho mỗi câu:
  - coverage, `needs_review`, lý do chọn cảnh;
  - 3 phương án thay thế; ghim hoặc loại cảnh ngay trong màn hình.
- Hiện cảnh báo fallback. Desktop chưa đọc khóa `mastering`: khi kế hoạch bị từ chối, kết quả có `needs_review` và `warnings` nhưng người dùng không thấy.
- Duyệt hook:
  - chọn cảnh mở đầu;
  - chặn thẻ chữ và thẻ tiêu đề trong hook Tổng hợp (lần 8 và 9: "In times of change", thẻ Purdue).
- Duyệt gợi ý cao trào Lồng tiếng: nghe thử 5 s, chấp nhận hoặc bỏ. Một teaser thật cần timeline đích riêng và remap phụ đề, giọng, che phụ đề nguồn.
- Bảng QC mastering Lồng tiếng: âm lượng trước/sau, đỉnh sau AAC, bộ lọc màu và khung, kèm lý do.

## 2. Chỉnh sửa trực quan

- Kéo/thả cảnh vào câu trên timeline; kéo dài hoặc rút ngắn mẩu trong giới hạn cảnh. Đã có bất biến `_settle` để kiểm.
- Video dài: người dùng đánh dấu mốc (ví dụ bản đồ Kavkaz 11:11) làm bằng chứng `user`. Lần 8 và 9: Why We Fight 0 ‰ cho Kremlin và bản đồ.
- Sửa thời gian phụ đề theo giọng ngay trên sóng âm.

## 3. Giao diện nâng cao

- Chọn mức mastering (Tắt / Nhẹ / Cân bằng / Mạnh) và phong cách cho từng chế độ, có giải thích ngắn từng mức.
- Mục tiêu âm lượng theo nền tảng: −14 LUFS cho YouTube, −16 LUFS mặc định. Luôn tuyến tính.
- Màu: xem trước trước/sau trên khung mẫu.

## 4. Trải nghiệm sản xuất

- Lồng tiếng chạy lại không đổi không cần trộn và render lại. Hiện tốn 16–23 s cho video 100 s, output giống hệt từng byte.
- Đồng bộ danh sách giọng nghe thử của desktop với catalog của bản cài. Lần 9: `vi-minh-khang` không có trong 1.1.95, worker lùi về B1 với `VOICE_ID_UNKNOWN`.
- Hạn mức AI nhìn hình cho video dài, có ước tính chi phí. Cache khung tương thích giữa các cách lấy mẫu: lần 8 chỉ trúng 23/80.
- Kiểm thử ổ đầy thật trên Windows (cần quyền admin), ngoài phần đã giả lập.
- Chất lượng chọn cảnh còn mở:
  - hook Bán hàng lấy cảnh của câu sau (espresso, stolen 2381 ms);
  - P3 cảnh lệch một câu;
  - P2 CTA 5 mẩu và lặp cảnh khi footage khan;
  - REPEAT ở intro/outro.
