# DubFlow — Báo cáo đóng Phase 3: Mastering Lồng tiếng trên Windows (Workstream A)

Epic #637 · 2026-11-11
Bản kiểm tra là các bản candidate, không phát hành. Chạy bằng runtime đóng gói, FFmpeg LGPL đi kèm, chỉ dùng CPU, không có khóa AI.

| Lần | Bản build | Nội dung |
|---|---|---|
| rc1 | 1.1.96-rc1, run 42922932629, build từ **20d755b** (không phải b7fee92 như dự kiến) | Code Lồng tiếng giống b7fee92; chỉ `product/job.py` khác |
| rc2 | 1.1.96-rc2, run 42934489360, build từ `da4acb0` | Có các bản sửa #714 và hành trình desktop |

## Trả lời thẳng

Workstream A **đạt** trên runtime đóng gói rc2. Những gì kiểm trên Windows:
- Âm lượng: đỉnh sau AAC ≤ −1,5 dBTP ở mọi output có mastering, tuyến tính, không limiter.
- Mức Tắt: trùng từng byte với khi không có mastering, 0 lần gọi mạng.
- Phụ đề: đầu phụ đề lệch 0 ms so với TTS. Cuối phụ đề tắt tối đa +200 ms sau giọng, trừ khi luật "tối thiểu 1 s" giữ lâu hơn (tối đa +0,8 s).
- Hủy/tiếp tục: video giống từng byte.
- Fallback B2→B1: đúng.
- Bộ lọc hình bị FFmpeg từ chối: job không còn hỏng.
- Chạy lại không đổi: dùng lại video.

Ba lỗi của rc1 đã được sửa ở #714 và kiểm lại trên rc2. Còn mở hai mục thông tin:
- Chạy lại vẫn trộn B2 (khoảng 4 s), mức P1.
- Khi một bộ lọc hình bị từ chối, plan bỏ luôn bộ lọc màu hợp lệ.

## 1. Âm lượng (rc1)

- Dữ liệu: X, Y và 3 clip tổng hợp.
  - Clip to: −10 LUFS, đỉnh sát 0 dBTP.
  - Clip nhỏ: −28 LUFS, crest cao.
  - Clip có LRA rộng: 21,8.
- Tiêu chí đỉnh: **15/15** output có mastering đạt TP ≤ −1,5 dBTP **sau AAC** (từ −2,4 đến −5,6). Thêm 3/3 lần áp thẳng lên audio cực đoan: từ −2,5 đến −8,5.
- Kiểu chuẩn hóa: cả 18 đều là `loudnorm` **Linear**. Không có limiter và không nén động. Limiter vẫn chưa có và nếu làm sẽ là một mức riêng, không phải mặc định.
- Độ to: trong ±1 LU so với mục tiêu planner. Khi đỉnh không cho tăng tuyến tính, mục tiêu bị hạ và QC ghi lý do. Từ #714, ghi chú nói rõ khi bản trộn bị giảm so với đầu vào, ví dụ −28 → −30 LUFS.
- Tắt và B1 giữ nguyên âm thanh gốc, nên có thể vượt −1,5 dBTP (B1 của X lên +1,2 dBTP). Đây là thiết kế: không chỉnh âm thanh gốc. Hai trường hợp này không được tính là đạt tiêu chí đỉnh.

## 2. Phụ đề

| | rc1 | rc2 (sau #714) |
|---|---|---|
| Đầu phụ đề so với giọng thật | 0 ms so với TTS; sớm hơn giọng thật 0–110 ms | 0 ms so với TTS; sớm hơn giọng thật 20–110 ms |
| Cuối phụ đề so với giọng thật | **trễ 0,24–0,97 s (TB), tối đa 2,6 s: KHÔNG ĐẠT** | **cuối không bị luật 1 s chi phối: +0 đến +200 ms. Mọi trường hợp > 0,3 s (tối đa +0,8 s) là phụ đề phải giữ đủ 1 s. Trung bình +0,13 đến +0,30 s: ĐẠT** |
| B2→B1 | 47/47 phụ đề về đúng thời gian nguồn | không chạy lại (đã đạt ở rc1, code không đổi) |

- Nguyên nhân cuối phụ đề trễ: câu TTS ngắn được đệm im lặng đến hết khe nguồn, và phụ đề tắt theo hết khe.
- Sửa (#714): phụ đề tắt khi giọng dừng, cộng 200 ms; vẫn giữ tối thiểu 1 s và không chồng nhau.
- Con số "lệch 0 ms" trong báo cáo Phase 3 trước chỉ đúng cho đầu phụ đề.

## 3. Hủy, tiếp tục và thay đổi (rc1)

- Hủy lúc TTS, lúc đo, lúc áp âm lượng và lúc render rồi tiếp tục: cả 4 ra video **giống từng byte** bản chạy liền.
- Đổi mức, đổi giọng (có hủy giữa chừng) và đổi kiểu phụ đề đều đạt.
- Sửa 1 câu dịch: chỉ 1/22 cụm TTS được tạo lại.
- Lỗi bộ lọc âm thanh: job vẫn xong với `MASTERING_SKIPPED`.
- Lỗi bộ lọc hình: rc1 **thất bại** (`MEDIA_COMMAND_FAILED`, nhánh B1 cũng hỏng, plan lỗi được lưu lại). #714 sửa: render không có bộ lọc, ghi `MASTERING_VIDEO_SKIPPED`, plan quên bộ lọc. rc2: **ĐẠT**. Job xong (rc 0) với `MASTERING_VIDEO_SKIPPED`. Plan còn `video_filters: []`. Lần chạy sạch kế tiếp dùng lại video, 0 lỗi. Ghi chú: plan bỏ cả bộ lọc màu hợp lệ cho tới khi đổi mức.

## 4. Mức Tắt và AI

- Tắt cho video giống từng byte với khi không có `mastering.json` (5/5).
- Tắt không ghi plan và không có stage mastering.
- 48 lần chạy worker: 0 sự kiện mạng, 0 kết nối. Không cần Gemini ở mức nào.
- Không đổi độ dài hoặc thứ tự ở mọi mức.

## 5. Dùng lại khi chạy lại không đổi (P1)

- rc1: chạy lại vẫn trộn và render lại (15,8–16,3 s), trong khi tiếp tục sau hủy chỉ mất 9,2–9,7 s.
- #714: video đã xong được dùng lại khi identity khớp. Identity gồm âm thanh theo hash, phụ đề, che, plan.
- rc2: **ĐẠT**. `final_vi.mp4` được dùng lại (0 encode), mất 11,6–12,1 s (rc1 là 15,8–16,3 s). Vẫn trộn B2 lại (khoảng 4,3 s); đây là P1 còn mở.

## 6. Hiệu năng (rc1)

- X chạy lạnh mất 83 s (CPU 300 s, RAM đỉnh 1,7 GB).
- Khi đã có cache: Tắt khoảng 15 s, các mức có mastering khoảng 20 s. Mastering thêm khoảng 5–6 s cho video 100 s.

## 7. Không chạy

- Không có mức "strong" riêng: Mạnh chính là `dynamic`.
- Không đổi tốc độ giọng: worker không có tham số này.
- Không bắt gói tin mạng ở mức hệ điều hành; chỉ theo dõi kết nối của tiến trình.
- rc1 gọi worker trực tiếp; đường đi qua DubFlow.exe nằm ở hành trình desktop của rc2.

Quét khóa: rc1 quét 10 519 file, 0 lần xuất hiện. rc2: quét 11 018 file, cả khóa Gemini và Claude, 0 lần xuất hiện.

## 8. rc2: âm lượng (spot check)

| Output | Mix trước I / TP | Mục tiêu | Normalization | MP4 sau AAC I / TP / LRA |
|---|---|---|---|---|
| X balanced | −15,2 / −0,6 | −17,2 (hạ, có lý do) | Linear | −17,2 / −2,5 / 8,4 |
| Y balanced | −10,9 / −0,4 | −16,0 | Linear | −16,0 / −5,6 / 9,7 |
| S-quiet balanced | −18,6 / −0,8 | −20,4 (hạ; ghi chú nói rõ "nhỏ hơn bản trộn gốc 1.8 dB") | Linear | −20,3 / −2,5 / 13,7 |
| S-hot / S-wide balanced | −13,6 / −0,8 · −13,2 / −2,0 | −16,0 | Linear | −16,0 / −3,2 · −16,0 / −4,9 |

- Tắt: `8306af78…`, trùng none và trùng rc1.
- X light trùng rc1 từng byte.
- X balanced đổi hash. Đây là dự kiến, vì giờ phụ đề tắt theo giọng thật.

Báo cáo gốc trên máy kiểm thử: `_sandbox\phase3-closure\report-rc1.md` và `_sandbox\phase3-closure\rc2\report-rc2.md`.

## 9. rc3 (run 42953009548, `005344e`): hồi quy sau #717

#717 thay numpy bằng `array` trong `_voiced_end_ms`, vì image CI không có numpy. Sau thay đổi:
- X Tắt `8306af78…`: trùng rc1 và rc2.
- X Cân bằng `90bfb861…`: trùng rc2 từng byte, cùng SRT.
- Cuối phụ đề không bị luật 1 s chi phối: tối đa +200 ms.
- Mạnh giờ có gợi ý cao trào 1:15–1:20 (Y). Video trùng từng byte trước và sau Giữ/Bỏ, nên gợi ý không bao giờ được chèn vào video.
- Quét khóa: 0 / 10 049 file.
