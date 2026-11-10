# DubFlow — Báo cáo Phase 2 Quality Hardening

Epic #637 · 2026-11-11 · main khi viết: `a5b4468`
Phạm vi: `product_showcase` (Bán hàng) và `compilation_entertainment` (Tổng hợp). Không phát hành bản stable nào.

## Trả lời thẳng

**Gate A (≥ 90 % lời đọc được minh họa đúng) CHƯA ĐẠT.** Số thật ở mục 5 và 11.

- Bộ tổng hợp có nhãn: dev 74,8 %, holdout 84,1 %, holdout2 (chưa từng dùng để chỉnh) 78,4 %.
- Video thật trên Windows: espresso 68,4 %, máy thổi bụi 42,3 %, hũ tăm 52,7 %, Top 5 năm nguồn 94,5 %, Why We Fight 43,0 %.

Phần đã chắc chắn tốt hơn:
- 0 lỗi timeline trên mọi bộ: 0 mẩu vượt clip, 0 mẩu vụn, 0 khung đứng hình, 0 phát ngược.
- 0 "silent miss" trên các bộ tổng hợp: câu không đủ cảnh luôn bị cờ "cần xem lại".
- Chạy lại nhanh hơn nhiều: Top 5 chạy lại 2,5 s, trước đó 34,5 s.
- Đường cũ không đổi đến từng byte.

## 1. P0 đã sửa

| P0 | Sửa | Bằng chứng |
|---|---|---|
| Phủ một phần bị lấp bằng cảnh không liên quan mà không nói | #678: phủ theo câu. Thứ tự lấp: cảnh khớp trước, cảnh trung tính sau, và ghi rõ `coverage` cùng `needs_review` khi dưới 90 % | test `test_partial_coverage_is_flagged_not_hidden`; silent misses 0 trên dev, holdout, holdout2 |
| Cảnh ngắn làm câu sau hết footage | #678: phân bổ toàn cục, câu khan cảnh được lấp trước; chế độ chỉ tiến cân phần footage bị mất | gate `no stolen keys` (trừ `dev-long-forward`, vốn khan cảnh) |
| Mẩu vụn, đứng hình, gián đoạn ngoài ý muốn | #678, #695: lượt `_settle` mỗi câu. Mỗi mẩu nằm trong cảnh của nó, không mẩu nào dưới 0,5 s khi có nhiều mẩu, tổng đúng bằng câu. Thiếu thì cảnh đang chiếu kéo dài, rồi đến cảnh chưa ai dùng, cuối cùng mới chiếu lại và có ghi rõ | 150 kịch bản ngẫu nhiên × 2 chế độ; trên main cũ có mẩu vượt cảnh, câu rỗng và mẩu 414 ms |
| P2 máy thổi bụi: `OUTSIDE_SOURCE`, kế hoạch bị từ chối, ghép thường mà không cảnh báo | #695 (xem dòng trên). Thêm: khi kế hoạch bị từ chối, kết quả có `needs_review: true` và cảnh báo | Windows lần 9: kế hoạch được nhận, mẩu cuối 5666 < 5805 ms, mẩu ngắn nhất 593 ms, coverage 6,4 → 42,3 % |
| Video dài chọn sai cảnh phải cảnh báo | #684: AI nhìn rộng trước, rồi nhìn đúng câu chưa phủ; câu thiếu cảnh có cờ | Why We Fight: câu Kremlin và bản đồ Kavkaz 0 ‰ nhưng đều có cờ review |
| Sửa video ngắn không làm hỏng video dài | Bộ hồi quy cố định chạy mọi PR (`tests/mastering/test_regression.py`) | Top 5 và Why We Fight giống hệt nhau ở lần 8 và lần 9 |
| TTS/phụ đề lệch | Cache TTS theo câu (#680); ghép bằng stream copy (#682) | Lệch A/V cuối file −11,3 ms và −8,0 ms (lần 7: +16,7 ms); giải mã toàn file 0 lỗi |
| Cache/kế hoạch cũ bị dùng lại | Recipe tăng ở mọi thay đổi planner: product-editorial-v9→v11, compilation-program-v8→v10 | test invalidation; chạy lại không đổi thì dùng lại, đổi 1 câu thì chỉ đọc lại 1 câu |
| Mặc định cũ đổi | Không đổi | Job không có khóa mastering: argv FFmpeg giống hệt, video giống hệt từng byte (lần 8 và 9) |
| Chọn không chắc phải có `needs_review` | Cờ theo coverage và độ tin cậy; fallback cũng có cờ (#695) | silent misses 0 trên bộ tổng hợp |
| Thời lượng sai đơn vị trong harness (review #676) | #690: đổi tick → ms | test time base 1/90000 |

## 2. P0 chưa sửa (lý do, fallback)

1. **Hook vẫn lấy cảnh của câu sau trên video thật.** Espresso: stolen 2381 ms, tức cảnh dàn/nén của câu 3. Top 5: hook chọn thẻ chữ "In times of change" và thẻ tiêu đề.
   - Trên bộ tổng hợp: stolen 0 (trừ `dev-long-forward`).
   - Đã thử bỏ khỏi hook mọi cảnh mà câu khác khớp: holdout-airfryer giảm 78,8 → 73,1 % vì nhãn cho phép hook dùng chung cảnh. Đã hoàn lại, vì không được chỉnh theo một video.
   - Cần dữ liệu kế hoạch thật của espresso để sửa đúng gốc.
   - Fallback: cảnh của hook có lý do và độ tin cậy, người dùng ghim hoặc loại được.
2. **P3 hũ tăm: cảnh duy nhất "tháo đế thấy ngăn tăm" bị đẩy sang câu sau.** Câu 3 được 0 ‰, câu 4 bị contra. Chưa sửa, chưa tái hiện được trên bộ tổng hợp. Fallback: câu 3 có cờ review.
3. **Why We Fight: không tìm ra Kremlin và bản đồ Kavkaz** (0 ‰). AI chỉ nhìn 75 trong số khoảng 2 000 cảnh. Fallback: có cờ review.
4. **P2: 0/6 câu đạt** dù không còn lỗi timeline. CTA dùng 5 mẩu 593–1111 ms và lặp cảnh của câu 2. Nguồn: 28 s footage cho 22 s lời, một cảnh mờ. Fallback: cờ review trên câu thiếu cảnh. Còn 2 silent miss: câu 2 log "full" nhưng nhãn chỉ 356 ‰; CTA là câu chung nên không có cờ.
5. **REPEAT ở intro/outro** của Top 5 (3 lần) và Why We Fight (1 lần), luôn được khai báo là "chiếu lại".

## 3. Bộ dữ liệu

Tổng hợp. Fixture khóa bằng hash SHA-256 trong `tests/mastering/regression/FROZEN.json`; test thất bại nếu fixture đổi.
- `dev.json`: espresso, nồi cơm không dấu, phim dài chỉ tiến, bản đồ muộn. Đây là bộ dùng để chỉnh.
- `holdout.json`: nồi chiên, máy sấy, tai nghe, Top 5 năm nguồn. Đã xem một lần để tìm lỗi tai nghe (#678), không sửa nhãn nào.
- `holdout2.json`: máy xay, sạc dự phòng, bàn chải, du lịch năm nguồn. Chưa từng xem.

Video thật (Windows, máy của Oscar). Truth được viết trước khi chạy, bằng cách xem 1–4 khung/giây:
- P1 espresso: 4 clip, 6 câu.
- P2 máy thổi bụi mini (Douyin): 4 clip, 6 câu.
- P3 hũ tăm tự động (Douyin): 4 clip, 6 câu.
- Top 5 năm nguồn độc lập, 13 câu. Nguồn thứ 5 là domino sách ở thư viện Chișinău (Commons, CC BY 2.0).
- Why We Fight (36 phút, nhiều nguồn), 4 câu. Mốc: Kavkaz 11:11, Ural 12:43, Kremlin 12:46, đập Dnepr 32:12.

## 4. Ground truth

- Mỗi câu có danh sách đoạn **chấp nhận** (`accept`), đoạn **sai nghĩa** (`contra`) và **cảnh khóa** của câu khác (`key`).
- Chấm điểm bằng `engine/dubflow/mastering/quality.py`:
  - coverage = thời gian câu nằm trên đoạn chấp nhận;
  - câu "ok" khi ≥ 900 ‰ hoặc khi không có footage và đã bị cờ;
  - silent miss = câu thiếu cảnh mà không có cờ;
  - false alarm = cờ trên câu đã đạt;
  - stolen = thời gian một câu chiếu cảnh khóa của câu khác.
- **Giới hạn đã nêu rõ:** git không chứng minh được nhãn holdout viết trước thay đổi planner, vì cùng PR (#676, #678). Hash chỉ khóa nhãn từ #690 trở đi. Holdout2 chưa từng được xem.

## 5. Coverage theo bộ (main hiện tại)

| Bộ | Coverage | Câu ok | Contra | Stolen | Silent miss | False alarm | Lỗi timeline |
|---|---|---|---|---|---|---|---|
| dev (4) | 74,8 % | 11/17 | 0 | 7000 ms (dev-long-forward) | 0 | 2 | 0 |
| holdout (4) | 84,1 % | 15/23 | 0 | 0 | 0 | 4 | 0 |
| holdout2 (4, chưa xem) | 78,4 % | 14/23 | 0 | 0 | 0 | 8 | 0 |
| P1 espresso (Windows) | 68,4 % | 3/6 | 2352 ms | 2381 ms | 0 | 3 | 0 |
| P2 máy thổi bụi (Windows, lần 9) | 42,3 % | 0/6 | 0 | 2839 ms | 2 | 0 | 0 |
| P3 hũ tăm (Windows) | 52,7 % | 2/6 | 1750 ms | 0 | 0 | 1 | 0 |
| Top 5 năm nguồn (Windows) | 94,5 % | 12/13 | 3240 ms | 2794 ms | 0 | 7 | REPEAT 3 |
| Why We Fight (Windows) | 43,0 % | 2/4 | 0 | 0 | 0 | 2 | REPEAT 1 |

Tự xem khung giữa mỗi câu:
- espresso: 5/6 + 1 nửa (không đổi so với lần 7);
- P2: 2 đúng, 3 đúng một nửa, 1 sai;
- P3: 3/6 + 2 nửa;
- Top 5: 13/14.

## 6. Trước / sau

| | Trước (main `e5f0405`, trước hardening) | Sau (main hiện tại) |
|---|---|---|
| dev coverage | 80,9 %, có SLIVER và phát ngược | 74,8 %, 0 lỗi timeline. Giảm vì cảnh không khớp không còn được tính là "phủ"; câu thiếu cảnh giờ có cờ |
| P2 máy thổi bụi | kế hoạch bị từ chối, ghép thường 6,4 %, 6 silent miss, không cờ | kế hoạch được nhận, 42,3 %, 2 silent miss |
| Ghép Top 5 | encode lại 7,34 s | stream copy 0,19 s |
| Chạy lại Top 5 không đổi | 34,5 s | 2,5 s, 0 encode, TTS 100 % từ cache |
| Chạy lại espresso không đổi | 20,8 s | 7,6 s, không nạp model TTS |
| Sửa 1 câu espresso | đọc lại cả kịch bản | đọc lại đúng 1 câu, 13,2 s |
| Why We Fight: số khung AI xem | 80, chọn đều | 54 khung xem rộng + 21 khung nhắm vào câu chưa phủ |

## 7. Hồi quy E2E

- Bộ hồi quy cố định chạy trong CI ở mọi PR mastering (`tests/mastering/test_regression.py`). Các gate: 0 lỗi timeline, contra 0, silent miss 0, không lấy cảnh khóa (trừ bộ khan cảnh), espresso ≥ 900 ‰, fixture đúng hash, 150 kịch bản ngẫu nhiên giữ bất biến về mẩu.
- Windows lần 8 (main `f75bd2b`) và lần 9 (`d41b944` = main + #695 + #692 + #693):
  - P1, P3, Top 5 và Why We Fight cho số liệu giống hệt nhau;
  - OUTSIDE, SLIVER, HELD đều 0;
  - P2 hết lỗi.
- Tương thích: job Bán hàng không có khóa mastering cho cùng một lệnh FFmpeg và video giống hệt từng byte giữa các lần chạy.

## 8. Hiệu năng (Windows, CPU, runtime 1.1.95)

| Job | Thời gian | RAM đỉnh |
|---|---|---|
| P1 espresso, lần đầu | 41,4 s | 3,35 GB |
| P1 chạy lại không đổi | 7,6 s | Python 75 MB (không nạp TTS) |
| P1 sửa 1 câu | 13,2 s | 3,36 GB |
| P3 lần đầu (nạp TTS + hỏi AI) | 87,4 s | 2,1 GB |
| Top 5 năm nguồn | 45,3 s | 2,49 GB |
| Top 5 cũ | 62,1 s (lần 7: 70,0 s) | 2,64 GB |
| Why We Fight 36 phút | 154,6 s (82 s chờ AI; FFmpeg ~52 s); lần 9 có cache: 72,5 s | 1,34 GB (lần 7: 2,73 GB) |

## 9. Cache / TTS

- `engine/dubflow/tts/sentence_cache.py` (#680): cache WAV 16-bit theo khóa định danh và lời câu.
  - Định danh = hash manifest model + recipe suy luận + giọng + tốc độ.
  - LRU 512 MB, đặt tại `model_root/tts-sentence-cache`.
  - Engine chỉ khởi động khi có câu chưa cache.
- Đo được:
  - chạy lại không đổi: 0 câu mới, không nạp model;
  - sửa 1 câu: đúng 1 câu đọc lại;
  - Top 5 và phim dài: 100 % từ cache.
- Cache AI nhìn hình: khóa theo nội dung khung + model + prompt. Cách lấy mẫu mới làm cache 80 khung của lần 7 chỉ trúng 23 (thấp).

## 10. PR / commit

| PR | Issue | Commit | Nội dung |
|---|---|---|---|
| #676 | #675 | fd911a2 | Harness chất lượng, bộ dev/holdout có nhãn |
| #678 | #677 | c5b9b69 | Phủ theo câu, phân bổ cảnh toàn cục, holdout2 |
| #680 | #679 | d4e72c5 | Cache TTS theo câu |
| #682 | #681 | 7ed07cb | Ghép chương trình bằng stream copy |
| #684 | #683 | f75bd2b | Lấy mẫu khung thích ứng: rộng rồi nhắm |
| #690 | #689 | 5d6ef5f | Sửa theo review #676: tick → ms, khóa hash, công khai test bị bỏ qua |
| #695 | #694 | 5ec00c9 | Mẩu nằm trong cảnh, không mẩu vụn khi footage ít, fallback có cờ |

Ngoại lệ quy trình đã ghi: #676 dùng nhánh `agent/hardening-harness`, mở không ở dạng Draft, và merge trước khi review của chủ repo đến. Từ #689 trở đi, nhánh đặt tên `agent/issue-<N>-<slug>` và PR mở ở dạng Draft.

Test bị bỏ qua: runner tích hợp Linux không có FFmpeg nên 8 test media không chạy trong CI (liệt kê trong #690). Chúng chạy và đạt khi chạy cục bộ với FFmpeg LGPL n9.0.2, và chạy trên Windows: mastering 154 test OK.

## 11. Gate

| Gate | Kết quả |
|---|---|
| A ngữ nghĩa ≥ 90 % | **CHƯA ĐẠT.** Tổng hợp 74,8 / 84,1 / 78,4 %; video thật 42–95 % (mục 5) |
| B toàn vẹn timeline | **ĐẠT.** 0 OUTSIDE, SLIVER, HELD hoặc phát ngược trên mọi bộ tổng hợp và video thật (lần 9). REPEAT đều có khai báo |
| C hồi quy | **ĐẠT** với bộ cố định trong CI; Windows lần 8 → 9 không có hồi quy |
| D tương thích | **ĐẠT.** Không có khóa mastering thì argv và video giống hệt từng byte |
| E hiệu năng Windows | **ĐẠT** theo số đo ở mục 8. Thiếu: chưa đo ổ đầy thật |

## 12. Việc cho Phase 5

- Màn hình duyệt cảnh theo câu: thấy coverage, cờ, lý do và phương án thay thế; ghim/loại cảnh.
- Duyệt hook: chọn cảnh mở đầu; chặn thẻ chữ hoặc tiêu đề trong hook Tổng hợp.
- Hiện cảnh báo fallback (`needs_review`, `warnings`) trong ứng dụng; desktop hiện chưa đọc khóa `mastering`.
- Video dài: cho người dùng đánh dấu mốc; tăng hạn mức AI có kiểm soát chi phí.
- Xem `docs/mastering/PHASE5_BACKLOG.md`.
