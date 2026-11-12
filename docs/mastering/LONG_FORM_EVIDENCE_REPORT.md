# Bằng chứng hình cho video dài (Workstream B, P2-N11)

Epic #637 · 2026-11-12 · chỉ thị "DUBFLOW — ASSISTED PRODUCTION STABILIZATION & DOCUMENTARY PILOT ENTRY GATES" · mốc rc10 `a4ec540` · PR #784 (issue #783).

## 1. Kết luận

**Gate B (P2-N11): FAIL.** Trên bốn bản candidate Windows (rc12–rc15), cùng chương trình 20 phút và cùng 60 câu nhãn đã khoá (sha256 `afa979d2…f66497`):

| | rc12 | rc13 | rc14 | rc15 | Mục tiêu |
|---|---|---|---|---|---|
| recall@5 | 31/60 | 33/60 | 28/60 | 29/60 | ≥ 85 % (51/60) |
| Bỏ sót không báo | 0 | 2 | 0 | 3 | 0 |
| SOURCE_INSUFFICIENT sai | 0 | 0 | 0 | 0 | 0 |
| HELD_FRAME | 0 | 0 | 0 | 0 | 0 |

- Đạt: 0 SOURCE_INSUFFICIENT sai và 0 khung đứng ở mọi bản. Phần của mỗi mục trong ngân sách vision cố định (#797).
- Không đạt: recall@5 dừng ở khoảng 50 %, vì ngân sách 142 khung chỉ đủ cho lượt nhìn rộng và 2 khung mỗi mục (§6 rc12). Bỏ sót không báo xuất hiện hoặc biến mất giữa các lần chạy (0, 2, 0, 3). Ở rc13 nguyên nhân là chữ của vision khác nhau giữa các lần chạy (#802, P2-N23); rc15 chưa điều tra.
- Không hạ ngưỡng, không sửa nhãn. Video dài **không** sẵn sàng cho sản xuất có hỗ trợ.

## 2. Lỗi (rc8, Kịch bản F)

Một chương trình Tổng hợp 20 phút: 20 mục, mỗi mục là một cửa sổ 105 s của "Why We Fight: The Battle of Russia" (2146,8 s).

- 16/23 phần ở `signals_only`: AI không nhìn khung nào của phần đó.
- 193/214 câu MATCH_NOT_FOUND, tức là thành Nghiêm trọng.
- Kiểm tra cuối chặn xuất vì giữ khung (HELD_FRAME 0,5–2,6 s).

## 3. Nguyên nhân

| # | Nguyên nhân | Hậu quả |
|---|---|---|
| R1 | Ứng viên cảnh rải đều trên **cả phim** rồi mới cắt theo cửa sổ của mục | một cửa sổ 105 s chỉ còn 12 ứng viên, dẫn tới lặp cảnh và giữ khung |
| R2 | Lượt nhìn rộng rải 54 khung trên 20 bản sao (hard link) của cùng một phim | chỉ 7 khung rơi vào cửa sổ các mục dùng |
| R3 | Lượt nhìn có mục tiêu và nhìn nhiều khung lấy từ nguồn nguyên vẹn; phần đầu tiêu hết ngân sách | i1: 8 khung có mục tiêu và 18 khung nhìn nhiều |
| R4 | Ngân sách cố định 80 khung | không tăng theo độ dài lời dẫn |

## 4. Sửa (PR #784)

| Cơ chế | Nội dung |
|---|---|
| Ứng viên trong cửa sổ | `candidates.extract(within=trim)`: cửa sổ được lọc **trước** khi giới hạn số ứng viên, nên một cửa sổ có mọi cú máy của nó. Nguồn ngắn không đổi. Bộ nhớ đệm tính cả cửa sổ. |
| Bản đồ phủ | `coverage_map` trong nhật ký mỗi phần: số cảnh và ms có bằng chứng, và các đoạn dài nhất chưa ai nhìn (`unseen`). |
| Nhìn nhiều lượt, thích nghi | Lượt rộng chỉ nhìn footage các phần dùng (trong cửa sổ). Các phần lần lượt nhận khung, phần nào cũng được nhìn trước khi phần nào được lượt thứ hai. Thứ tự rải đều (van der Corput). |
| Trùng lặp | Một ảnh giống hệt ở nhiều nơi chỉ được nhìn một lần, câu trả lời được chia sẻ (`shared_duplicates`, đếm riêng với khung đã tính tiền). |
| Ngân sách | Khi spec không đặt `vision_budget`: 2 khung mỗi câu lời dẫn, tối thiểu 80, tối đa 400 (`budget_rule` ghi trong báo cáo vision). Spec đặt thì giữ đúng như đặt. Chế độ ngân sách nằm trong digest của phần, nên 80 tự động và 80 đặt tay không dùng chung kết quả. |
| Chia công bằng | Phần còn lại của ngân sách chia đều cho các phần chưa lập kế hoạch; phần đầu không lấy lượt của phần sau. Lượt có mục tiêu chỉ chọn trong cửa sổ của phần. |
| Dự phòng | Footage chưa ai nhìn **không bao giờ** là SOURCE_INSUFFICIENT: câu không có bằng chứng thì là MATCH_NOT_FOUND (phải duyệt), không phải "nguồn không có cảnh". |

Recipe đổi thành `compilation-program-v17`.

Lead review trên #784 nêu hai lỗi, cả hai đã sửa trước khi merge:
- `budget_set` chưa vào digest;
- khung trùng lặp bị đếm như khung đã tính tiền.

### 4b. Bổ sung sau review độc lập (PR #790, issue #789)

| Lỗi | Sửa |
|---|---|
| Id cảnh trong một cửa sổ không ổn định: đánh số 1…N trong từng cửa sổ, nên dời cửa sổ thì cùng một cảnh mang id khác, và một id của cả phim (1…400) có thể chỉ sang cảnh khác | Đánh số từ `WITHIN_BASE` (100 000) theo vị trí trong cả nguồn. Cache cửa sổ cũ không được dùng lại. Ghim hay loại trừ mang id cũ thành `shot_gone` (có báo), không bao giờ trỏ sang cảnh khác |
| Lệch một ở giới hạn: nguồn có đúng 400 cửa sổ vẫn bị cắt theo cửa sổ | `<=`: nguồn đó có cùng ứng viên và cache dù có hay không có cửa sổ |
| Bản trùng đã có câu trả lời vẫn được nhìn lại ở lượt có mục tiêu | `Look.answered()`: các bản trùng được tính là đã nhìn |
| Thứ tự rải đều bắt đầu từ cảnh đầu đoạn | Bắt đầu từ giữa đoạn, cảnh đầu nhìn sau cùng |

Recipe đổi thành `compilation-program-v18`. Bán hàng không đổi (không dùng cửa sổ hay `allocate`).

### 4c. Sau Windows rc12 (PR #797)

Lỗi (P2-N18, issue #793, thấy ở W2 trên Windows rc12): phần vision của một phần được tính lại trong lượt chạy, `fair() = (budget − taken) // số phần còn lại`. Phần trước tiêu ít khung hơn (cảnh bị loại hay đã duyệt thì bị khoá, không nhìn thêm), nên phần sau có thêm khung, nhìn thêm một cảnh, và kế hoạch đổi. Ở W2, i4 không ai đụng tới vẫn bị lập lại kế hoạch và dựng lại.

Sửa: `PartLooks` (`engine/dubflow/compilation/raw.py`) cố định phần vision của mỗi phần **một lần, trước khi phần nào được lập kế hoạch**, từ những gì lượt nhìn rộng để lại. Một phần không tiêu quá phần của nó, và không quá phần ngân sách còn lại. Cùng PR sửa P2-N17 (giữ khung để có chỗ cho khoảnh khắc âm thanh gốc), không thuộc báo cáo này. Recipe không đổi.

Ý nghĩa cho video dài:
- mỗi mục nhận `(budget − broad) // số phần`;
- khung một mục không dùng không còn chuyển sang các mục sau;
- ngân sách không tăng. Với số của W3 rc12: (142 − 95) // 22 = 2 khung mỗi phần, bằng mức các mục đã nhận ở rc12 (outro nhận 2 thay vì 3). Vì vậy #797 không được kỳ vọng tự đóng khoảng cách Gate B. Phân tích câu trượt ở rc12 (§6) chỉ vào ngân sách và độ phủ vision của mỗi mục.

Câu hỏi thiết kế cho owner (chưa chọn):
- tăng ngân sách (hệ số khung mỗi câu, hay mức trần 400): phủ nhiều hơn, nhưng tốn chi phí vision và thời gian hơn;
- chia khác với cùng ngân sách: ví dụ bớt khung cho lượt rộng để mỗi mục còn khung cho lượt có mục tiêu và nhìn nhiều khung, hoặc chia theo phần footage chưa ai nhìn (`coverage_map`) thay vì chia đều.

Lựa chọn nào cũng không được chỉnh bằng cách thử trên bộ nhãn W3 đã khoá.

Test (`tests/mastering/test_compilation_raw.py`, `PartIndependenceTest`):

| Test | Cần FFmpeg |
|---|---|
| Phần trước tiêu bao nhiêu thì phần vision của phần sau cũng không đổi | không |
| Một phần không tiêu quá phần của nó, không quá phần ngân sách còn lại | không |
| Cả chương trình chạy hai lần, lần hai mục 1 tốn gấp đôi khung: các phần sau được hỏi đúng các khung như lần đầu, kế hoạch giống từng byte, không phần nào dựng lại | có |

CI hosted (run 43247787049 trên `d2ba877`, commit của PR): `tests/mastering` 304 OK, 14 skipped. Hai test quy tắc chia đã chạy. Test cả chương trình nằm trong số skipped (CI không có FFmpeg), nên chưa có bằng chứng CI cho nó. Chưa có bằng chứng Windows cho #797: rc13 (release run 43250339299 trên `f46723a`, candidate) mang thay đổi này; kết quả Windows chưa có.

Còn lại (issue #796, P2-N22, backlog, chưa sửa): một sửa đổi ở intro/outro/hook vẫn có thể lập lại kế hoạch một mục qua trạng thái vision dùng chung. Lượt nhìn nhiều khung của các phần đó tách cảnh của mục, và lượt có mục tiêu ghi vào `evidence` chung. `PartLooks` không phủ trường hợp này.

## 5. Test (`tests/mastering/test_long_form_evidence.py`, 17 test)

| Test | Kết quả |
|---|---|
| Một cửa sổ 105 s của phim 36 phút có mọi cú máy 2,4 s (≥ 43), trước đây < 25 | đạt |
| Nguồn ngắn: ứng viên như cũ dù có hay không có cửa sổ | đạt |
| Phân bổ: mọi khung nằm trong cửa sổ, các phần lệch nhau tối đa 1 khung | đạt |
| Lượt nhìn rộng cũ (mốc): < 10 khung trong cửa sổ | đạt (tái hiện) |
| Một ảnh trùng ở hai nơi: nhìn một lần, câu trả lời chia sẻ | đạt |
| Một khung có 4 bản trùng: tính 1 khung, 4 chia sẻ | đạt |
| Thứ tự rải đều phủ cả đoạn với mọi tiền tố | đạt |
| Ngân sách tự động theo lời dẫn, giới hạn 400; ngân sách spec giữ nguyên; digest khác nhau | đạt |
| Bản đồ phủ nói đúng phần có bằng chứng và đoạn chưa ai nhìn | đạt |
| Footage chưa nhìn không bao giờ là SOURCE_INSUFFICIENT | đạt |
| Dời cửa sổ: cảnh giữ id; id trong cửa sổ không trùng id của cả phim; cache riêng (#790) | đạt |
| Nguồn có đúng 400 cửa sổ: như không cắt (#790) | đạt |
| Cảnh đã có câu trả lời qua bản trùng không bị nhìn lại; cách cũ thì có (#790) | đạt |
| Lượt nhìn đầu tiên của một đoạn là giữa đoạn (#790) | đạt |

## 6. Chấm trên footage thật (W3, Windows)

Quy tắc, cố định **trước khi chấm**:
- nhãn sự thật ≥ 40 câu, khoá bằng sha256 trước khi tạo chương trình;
- top 5 của một câu = cảnh đã chọn theo thứ tự, rồi các cảnh thay thế, khác nhau, lấy 5 cái đầu;
- trúng khi chồng lấn ≥ 1000 ms hoặc ≥ nửa đoạn ngắn hơn;
- recall@5 tính trên câu có footage; câu "thiếu thật" (phim không có) phải được gắn cờ.

Công cụ chấm: `tools\score_w3.py` (từ chối chấm nếu nhãn đổi sau khi khoá).

### rc12 (a064807)

Bằng chứng Windows: bản rc12 thật (`DubFlow.exe`), điều khiển bằng tự động hoá UI (CDP và hộp thoại Win32). Đây là tự động hoá, không phải người duyệt.

| Mục | Kết quả |
|---|---|
| Nguồn | "Why We Fight: The Battle of Russia", sha256 `834cf4f3…b3c32b` giống nhau trước và sau lần chạy; 20 hard link `wwf-01..20.webm` |
| Nhãn sự thật | **64 câu** (60 có footage, 4 thiếu thật). Chép nguyên từng byte từ rc11. `W3-ground-truth.json` sha256 `afa979d239b6a68be03349308e233809efddc84cd2ed13e0d869913c51f66497`, trùng với file khoá (khoá lúc 2026-11-12T09:23:33Z, trước khi tạo chương trình W3 nào) |
| Chương trình | Tổng hợp kiểu Danh sách, 20 mục; mục k là cửa sổ 105·(k−1) đến 105·k s; lời dẫn từ `W3-narrations.json` (20/20 trùng); không có hook; intro/outro từ input rc8; vision bật, không đặt `vision_budget` |
| Bản nháp | COMPLETED sau 781 s; 22 phần (intro, i1–i20, outro), đều ở chế độ `content`; 352,1 s; RAM đỉnh 3 637 MB |
| Vision | ngân sách 142 (tự động: 2 × 71 câu), dùng hết; broad 95, targeted 45, multi_frame 2, shared_duplicates 22 |
| Xuất | Kiểm tra cuối từ chối: còn mục nghiêm trọng chưa xử lý (chấp nhận câu chưa duyệt không phủ mục nghiêm trọng). Không có final.mp4. Lượt duyệt đủ 66 câu đang mở: NOT RUN |

Điểm (`tools\score_w3.py`, kiểm tra khoá đạt; 64 câu chấm, 0 câu không khớp):

| Chỉ số | rc12 | Mục tiêu Gate B |
|---|---|---|
| recall@5 | **51,7 %** (31/60) | ≥ 85 %: **chưa đạt** |
| Bỏ sót nghiêm trọng không báo | 0 | đạt |
| SOURCE_INSUFFICIENT sai | 0 | đạt |
| Câu thiếu thật được gắn cờ | 4/4 | |
| chosen-hit | 41,7 % | |
| HELD_FRAME (22 kế hoạch) | 0 | |

Trạng thái câu: READY 5 (đều trúng), NEEDS_REVIEW 44, MATCH_NOT_FOUND 15. So với rc8: `signals_only` 16/23 phần thành 0/22; MATCH_NOT_FOUND 193/214 câu thành 16/71.

**Gate B: FAIL.** recall@5 51,7 % so với mục tiêu 85 %. Nhãn sự thật không bị sửa. Không tham số nào được chỉnh theo bộ nhãn này: rc12 chạy với ngân sách tự động mặc định.

Phân tích 29 câu trượt (điều tra rc12 §4, đọc file của lần chạy Windows, chỉ đọc; nhãn không đổi, không chấm lại):

| Loại | Số câu | Ghi chú |
|---|---|---|
| (a) không có ứng viên trúng trong cửa sổ | 0 | |
| (d) range nằm ngoài cửa sổ | 0 | mọi range nằm trong cửa sổ 105 s của mục |
| (b) có ứng viên trúng, nhưng chưa bao giờ được nhìn | **22** | ví dụ i4.u2: `i4-s1c100091` (330,2–340,3 s) chứa đoạn nhãn 332,5–335 s nhưng không ai nhìn, top 5 ở 349,9–371,7 s. i18.u2: ứng viên trúng duy nhất bị đánh "quá tối" nên không được nhìn |
| (c) đã nhìn nhưng không vào top 5 | 7 | xem bên dưới |

Nguyên nhân con của (c):
- ràng buộc đi tiến: câu trước đã dùng footage nằm sau trong phim (i7.u2, i11.u2, i15.u2);
- vision mô tả một khoảnh khắc khác trong cảnh dài, và không còn ngân sách nhìn nhiều khung (i15.u1, i12.u3);
- câu chỉ khớp một thẻ chữ, thẻ đó được để dành cho câu nói về chữ (i9.u2);
- lệch từ vựng hay số: vision đọc "1500 MILES", câu nói "một nghìn năm trăm dặm" (i16.u1).

Phép tính ngân sách:
- ngân sách 142 = 2 × 71 câu (trong khoảng 80–400);
- lượt rộng tiêu 95 khung trước khi lập kế hoạch, còn 47 cho 22 phần;
- mỗi phần nhận `fair()` = (142 − taken) // số phần còn lại = **2 khung** (outro 3);
- lượt có mục tiêu lấy `min(room, max(8, room // 2))` = 2; sau đó room < 2, nên **multi_frame = 0 cho mọi mục** (chỉ intro có 2);
- kết quả: mỗi mục chỉ có 6–9 cảnh được nhìn trên 20–49 cảnh. Phần footage có bằng chứng vision (`milli`) là 111–430 ‰, tức 57–89 % footage của mỗi mục AI chưa nhìn.

Kết luận của phân tích: ứng viên không thiếu (loại a = 0). 22/29 câu trượt vì footage đúng có trong cửa sổ nhưng chưa bao giờ được nhìn. Điểm nghẽn là ngân sách và độ phủ vision của mỗi mục (xem §4c).

### rc13

Bản 1.1.96-rc13 (`f46723a`, run 43250339299; rc12 cộng PR #797, #798, #799), Windows, chỉ CPU, cùng chương trình và cùng lời dẫn như rc12. Nguồn: `rc13\report-rc13.md` và `rc13\investigate\` trên máy Windows.

| Mục | rc13 |
|---|---|
| Nhãn sự thật | chép nguyên từng byte. sha256 `afa979d2…f66497` trùng file khoá, kiểm trước khi tạo chương trình. Không sửa |
| Bản nháp | COMPLETED sau 721 s (rc12 781 s); RAM đỉnh 3 485 MB |
| Vision | ngân sách 142, dùng 139; broad 95; mỗi phần 2 lần nhìn targeted; multi_frame 0 |
| Xuất | NOT RUN: bước chấp nhận và xuất W3 qua UI không chạy |

| Chỉ số | rc12 | rc13 | Mục tiêu Gate B |
|---|---|---|---|
| recall@5 | 51,7 % (31/60) | **55,0 % (33/60)** | ≥ 85 %: **chưa đạt** |
| Bỏ sót nghiêm trọng không báo | 0 | **2** (i1.u2, i16.u1) | 0: **chưa đạt** |
| SOURCE_INSUFFICIENT sai | 0 | 0 | đạt |
| HELD_FRAME | 0 | 0 | đạt |

**Gate B: FAIL trên rc13.** recall@5 55,0 % và 2 câu nghiêm trọng bị bỏ sót mà không báo.

Vì sao có 2 câu bỏ sót không báo (điều tra chỉ đọc trên bản sao, `rc13\investigate\w3` trên máy Windows; file điều tra không chép vào repo, [report-rc13.md](assisted-e2e/rc13/report-rc13.md) chỉ ghi trạng thái từng câu):
- Câu trả lời của vision khác nhau giữa hai lần chạy (cùng khung, khác chữ). Replay kế hoạch rc13 với câu trả lời của rc12 cho lại kết quả rc12, nên thay đổi trong planner không phải nguyên nhân.
- i1.u2: hai ứng viên gần hoà (625 và 621). Vision viết "phù hiệu chim" thay cho "huy hiệu", thứ tự đổi. Câu READY ở cả rc12 và rc13; ở rc12 cảnh được chọn trúng nhãn, ở rc13 trượt.
- i16.u1: một lần nhìn targeted viết "đánh dấu … bản đồ". Độ tin cậy lên từ 519 thành 640, vượt `REVIEW_BELOW` 600, nên câu không còn bị đánh dấu cần xem.
- Ghi vào backlog: issue #802 (P2-N23), trạng thái READY lật theo cách vision dùng chữ. Không chỉnh gì theo bộ nhãn đã khoá.

### rc14

Bản 1.1.96-rc14 (`96400fb`; rc13 cộng PR #803), cùng chương trình, cùng nhãn đã khoá (sha256 `afa979d2…f66497`, kiểm trước khi tạo chương trình). Nguồn: `rc14\report-rc14.md`.

| Chỉ số | rc12 | rc13 | rc14 | Mục tiêu |
|---|---|---|---|---|
| recall@5 | 51,7 % (31/60) | 55,0 % (33/60) | **46,7 % (28/60)** | ≥ 85 % |
| Bỏ sót nghiêm trọng không báo | 0 | 2 | **0** | 0 |
| SOURCE_INSUFFICIENT sai | 0 | 0 | 0 | 0 |
| HELD_FRAME | 0 | 0 | 0 | 0 |

- Vision: ngân sách 142, dùng 139; broad 95; targeted 2 mỗi phần (42); multi_frame 2 (chỉ ở intro). Bản nháp 676 s.
- **Gate B: FAIL trên rc14.** Ba lần chạy cùng chương trình cho 31, 33 và 28 trên 60: điểm dao động theo câu trả lời của vision giữa các lần chạy (issue #802), còn khoảng cách tới 85 % là do phần lớn footage chưa bao giờ được nhìn (§6 rc12). Không chỉnh gì theo bộ nhãn.

### rc15

Bản 1.1.96-rc15 (`6b1a0cf`; rc14 cộng PR #804), cùng chương trình, cùng nhãn đã khoá (sha256 `afa979d2…f66497`, kiểm trùng với dòng khoá trước khi tạo chương trình; không sửa). Chấm bằng `tools\score_w3.py --lock`.

| Chỉ số | rc12 | rc13 | rc14 | rc15 | Mục tiêu |
|---|---|---|---|---|---|
| recall@5 | 51,7 % (31/60) | 55,0 % (33/60) | 46,7 % (28/60) | **48,3 % (29/60)** | ≥ 85 % |
| Bỏ sót nghiêm trọng không báo | 0 | 2 | 0 | **3** (i1.u1, i1.u2, i16.u1) | 0 |
| SOURCE_INSUFFICIENT sai | 0 | 0 | 0 | 0 | 0 |
| HELD_FRAME | 0 | 0 | 0 | 0 | 0 |

- Vision: ngân sách 142, dùng 139; broad 95; targeted 42; multi_frame 2 (outro). Bản nháp 721 s, RAM đỉnh 3,5 GB.
- Cả 3 câu bỏ sót không báo đều READY với cảnh chọn trượt nhãn. i1.u2 và i16.u1 là hai câu của rc13; i1.u1 là câu mới. rc15 **không** điều tra nguyên nhân; giả thuyết vẫn là #802 (chữ của vision khác nhau giữa các lần chạy cache lạnh), chưa được kiểm trên rc15.
- **Gate B: FAIL trên rc15.** Bốn lần chạy cho 31, 33, 28, 29 trên 60. Không chỉnh gì theo bộ nhãn.

## 7. Giới hạn

- Nhãn sự thật do agent gán (nhìn tờ ảnh mỗi 2 s), không có người độc lập. Báo cáo ghi rõ "agent-labelled".
- Một phim, nhiều cửa sổ. "Một video khác ≥ 15 phút" và "chương trình dài nhiều nguồn" của chỉ thị: xem §6, dòng nào không chạy được ghi NOT RUN.
- Gate B là **FAIL** trên Windows rc12: recall@5 51,7 % (31/60) so với mục tiêu 85 %. 22/29 câu trượt có ứng viên đúng trong cửa sổ nhưng AI chưa bao giờ nhìn: ngân sách tự động 142 khung, lượt rộng tiêu 95, mỗi mục còn 2 khung và không có lượt nhìn nhiều khung (§6). #797 cố định phần của mỗi mục nhưng không tăng ngân sách, nên không được kỳ vọng tự đóng khoảng cách này (§4c).
- Ngân sách 400 khung là giới hạn cứng. Phim dài hơn khoảng 200 câu sẽ có phần chỉ dựa vào tín hiệu khác, và `coverage_map` nói phần nào.
