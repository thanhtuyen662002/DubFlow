# Đồng bộ tiếng–hình tại từng chỗ nối (Workstream C, P2-9)

Epic #637 · 2026-11-12 · chỉ thị "DUBFLOW — ASSISTED PRODUCTION STABILIZATION & DOCUMENTARY PILOT ENTRY GATES" · mốc rc10 `a4ec540` · PR #786 (issue #785).

## 1. Kết luận

**Gate C (P2-9): ĐẠT trên Windows rc13, rc14 và rc15.**

- Mỗi chỗ nối được đo từ bản đã giải mã. Trên rc15: W2 7/7 (bản cuối, `compilation.mp4`, bản cuối sau khi ghim) và W3 21/21. **0 chỗ nối chưa đo.**
- Tệ nhất 33,3 ms (đúng một khung ở 30 fps, phía hình), dưới max(40 ms, một khung). Lệch cộng dồn lớn nhất 33,3 ms, dưới 80 ms (A/V đo theo thời gian tuyệt đối của bản ghép nên đã gồm phần trôi tích luỹ).
- Synthetic `tests/mastering/test_av_boundaries.py` 17/17 trên bundle, không adapter (P3-N20).
- rc12 là 0/28 đo được; bản sửa đo theo onset là PR #799 (§4b).
- Ngoài phạm vi: phim tài liệu (`docu/roughcut`) chưa được đo (§7).

## 2. Cách đo

Đo **từ bản đã giải mã**, như trình phát nhìn thấy. Không dùng thời điểm bắt đầu của container: con số −5,4 ms của rc8 chỉ là chênh lệch đầu file, không cho biết gì về lệch bên trong.

`engine/dubflow/mastering/sync_probe.py` có hai cách:

| Cách | Dùng cho | Làm gì |
|---|---|---|
| **markers** | media tổng hợp | Mỗi đoạn có màu và âm riêng. Khung đổi màu là thời điểm hình, cửa sổ 2 ms đổi âm là thời điểm tiếng của chỗ nối. So với kế hoạch và với nhau. |
| **reference** | footage thật, mọi chỗ nối phần | File của phần là mốc của chính nó. Quanh mỗi chỗ nối của bản cuối, tìm phần mở đầu của phần sau trong tiếng đã giải mã (đường bao 1 ms, tương quan, ±300 ms) và trong hình (khung xám nhỏ, ±10 khung). Hai độ trễ là lệch tiếng và lệch hình; hiệu của chúng là lệch A/V. |

- Chỗ im lặng hoặc hình đứng được báo **"không đo được"**, không bao giờ coi là khớp.
- Kết quả chung (`sync_probe.verdict`, PR #792):
  - `false` khi một chỗ nối đo được bị lệch;
  - `null`, tức là **không đạt**, khi còn chỗ nối không đo được;
  - `true` chỉ khi mọi chỗ nối đều đo được và nằm trong ngưỡng.
- Mã thoát của CLI: 0 khớp, 1 lệch, 2 chưa đo đủ.
- Trước #792, cách reference vẫn báo đạt nếu các chỗ nối đo được đều khớp, kể cả khi còn chỗ không đo được.
- Ngưỡng của chỉ thị, không đổi: mỗi chỗ nối ≤ max(40 ms, 1 khung); cộng dồn ≤ 80 ms.
- CLI: `python -m engine.dubflow.mastering.sync_probe --final <bản cuối> --parts <các phần theo thứ tự> --fps 30`.

## 3. Nguyên nhân (đã đo trên media tổng hợp)

| # | Nguyên nhân | Lệch đo được |
|---|---|---|
| a | Trong một phần, `loudnorm` (chế độ động) bỏ tới ~100 ms cuối của đoạn dài ≥ 3 s (3123 ms còn 3046), và `apad,atrim=duration` không bù lại | tiếng nguồn của **mọi đoạn sau** sớm 61–77 ms |
| b | `trim=duration` làm tròn từng đoạn riêng | hình trôi tới +42 ms so với kế hoạch sau 9 đoạn |
| c | Tiếng của mỗi phần ngắn hơn hình (cắt theo ms kế hoạch, hình làm tròn theo khung) | dồn sang chỗ nối |
| d | Ghép bằng concat demuxer chép nguyên gói: mỗi phần mang priming và padding AAC của nó | lời dẫn +21 đến +32 ms mỗi chỗ nối, cộng dồn; 10 phần: −267,6 ms A/V ở chỗ nối thứ 25 |
| e | Concat demuxer dời hình có B-frame +21 ms (`start_time 0.021029`) | +21 ms mỗi chỗ nối |

## 4. Sửa (PR #786)

- **Một lưới khung** (`frame_counts`): đoạn thứ k kết thúc ở khung round(end_k × FPS). Hình không bao giờ lệch kế hoạch quá nửa khung, dù bao nhiêu đoạn.
- **Tiếng mỗi đoạn đúng tới từng mẫu**: `apad=whole_len` rồi `atrim=end_sample` bằng số mẫu của các khung của đoạn (1600 mẫu/khung ở 48 kHz, 30 fps).
- **Phần là số khung nguyên**, tiếng dài đúng bằng hình. Lời dẫn, nhạc và bản trộn cũng được đệm/cắt tới đúng số mẫu đó.
- **Ghép**: hình chép nguyên (không mã hoá lại), đưa về 0 (`setts`). Tiếng của từng phần được giải mã, cắt đúng số mẫu, ghép bằng concat filter và mã hoá **một lần**. Bản dự phòng mã hoá lại cũng làm như vậy.
- **Bán hàng**: các đoạn dùng cùng lưới khung.
- **Tổng hợp v1** (clip + ghép của package) không đổi từng byte (có test).

## 4b. Sửa sau Windows rc12 (PR #799)

Issue #795, merge `f46723a`. Trên rc12, cách reference không đo được chỗ nối thật nào (P2-N19, §6), và test tổng hợp cần một bản chuyển blur trên FFmpeg của bundle (P3-N20). Bảng ở §2 mô tả cách đo tiếng trước PR này.

Thay đổi trong `sync_probe.measure_joins`:
- Tiếng của chỗ nối được so trên **onsets** (`onsets()`, sai phân bậc nhất của đường bao 1 ms), không còn trên chính đường bao. Onsets không mang gợn của cao độ giọng.
- Đoạn mốc của tiếng dài **3 s** (`AUDIO_REF_MS` = 2 × `REF_MS`). Gần cuối chương trình, đoạn mốc được rút ngắn để mọi độ trễ được tìm (±300 ms) đều nằm trong file. Nó không ngắn hơn 1,5 s khi chương trình còn đủ dài.
- Quy tắc đỉnh rõ giữ nguyên: ≥ 0,3 và ≥ 2 × đỉnh khác. Nhưng "đỉnh khác" nay lấy **ngoài ±20 ms** (`SIDELOBE_MS`), không còn ngoài ±2 bin. 20 ms là một phần của giới hạn 40 ms.
- Phần hình không đổi (khung xám, ±10 khung).
- **Không đổi**: ngưỡng (mỗi chỗ nối ≤ max(40 ms, 1 khung), cộng dồn ≤ 80 ms) và kết quả chung. Chỗ nối không đo được vẫn không bao giờ là đạt; CLI vẫn thoát 2.

P3-N20: `BoundaryTests` lấy blur từ `render.available_blur` (blur mà sản phẩm dùng với FFmpeg đó), không còn viết cứng `boxblur`.

Test (`tests/mastering/test_av_boundaries.py`):

| Test | Cần FFmpeg | Kiểm |
|---|---|---|
| `PitchedVoiceTests` (giọng tổng hợp có xung cao độ 120–220 Hz) | không | 2 seed × có/không nhạc nền × độ lệch thật 0, +17, −23 ms = 12 ca. Cách mới ra đúng độ lệch ở cả 12. Cách cũ (đường bao, 1,5 s, ±2 bin) không thấy đỉnh rõ; test khẳng định điều này ở ca không nhạc nền (seed 1) |
| `PitchedVoiceTests`, im lặng | không | Im lặng không bao giờ được đo |
| Reference, mốc ghép cũ | có | Nay là `assertIs(passed, False)`: kết quả không đo được (`None`) không còn lọt qua mốc |
| Reference trên footage giống thật | có | Đo cả 9 chỗ nối. Test này bắt được chỗ nối cuối hỏng khi đoạn mốc 3 s chưa được rút ngắn gần cuối |

Bằng chứng (tách riêng):

| Loại | Kết quả |
|---|---|
| Linux cục bộ, có FFmpeg | Nhánh này một mình (`87d6df7`): `tests.mastering.test_av_boundaries` 17 OK (184 s). Ba nhánh sửa rc12 (#793, #794, #795) gộp lại: `tests/mastering` 308 OK, `tests/compilation` 9 OK, `tests/product` 50 OK (2 bỏ qua). Ẩn FFmpeg: 17 chạy, 5 bỏ qua, 0 lỗi |
| CI hosted (`ef2a771`, run 43249542861) | `tests/ci` 38 OK; `tests/mastering` 308 OK, **14 bỏ qua** (các test cần FFmpeg, nên `BoundaryTests` không chạy ở đây). `PitchedVoiceTests` và `VerdictTests` chạy không cần FFmpeg. Governance đạt. Không phải toàn bộ registry |
| Sự cố CI | Head đầu (`90e8e8c`) trượt CI hosted: chèn `PitchedVoiceTests` làm decorator bỏ qua-khi-thiếu-FFmpeg của `BoundaryTests` chuyển sang lớp mới. Đã sửa ở `87d6df7` |
| Windows | **Chưa có.** Bản sửa nằm trong rc13 (release run 43250339299 trên `f46723a`, candidate), kết quả chưa có, xem §6. Con số 28/28 ở §6 rc12 là replay của các biến thể trong bản điều tra, không phải chạy mã đã merge |

Còn lại: xác nhận trên Windows rc13 với chỗ nối thật; hai giá trị −33,3 ms phía hình ở replay rc12 chưa được điều tra.

## 5. Kết quả trên media tổng hợp (Linux, libx264, `tests/mastering/test_av_boundaries.py`)

| Ca | Chỗ nối | Lệch A/V lớn nhất | Hình so với kế hoạch lớn nhất | Kết quả |
|---|---|---|---|---|
| CFR 24 | 5 | 0,7 ms | 15,3 ms | ĐẠT |
| CFR 25 | 5 | 0,7 ms | 15,3 ms | ĐẠT |
| CFR 29,97 | 5 | 0,7 ms | 15,3 ms | ĐẠT |
| VFR (25, bỏ mỗi khung thứ 7) | 5 | 0,7 ms | 15,3 ms | ĐẠT |
| Trộn 24/25/29,97/30/50/60 trong một phần | 5 | 0,7 ms | 16,3 ms | ĐẠT |
| Nguồn không tiếng, ảnh tĩnh, đoạn 20 ms và 40 ms (lời dẫn làm mốc) | 5 | 13,3 ms | 13,3 ms | ĐẠT |
| 10 phần ghép, mọi chỗ nối đoạn | 29 | 1,3 ms | 16,0 ms | ĐẠT |
| cùng ca, cách ghép cũ (mốc) | 29 (8 không đo được) | 267,6 ms | 37,1 ms | KHÔNG ĐẠT |
| 10 phần có hoạ tiết, cách reference, ghép mới | 9 chỗ nối | 0,0 ms | – | ĐẠT, 0 không đo được |
| cùng ca, ghép cũ (mốc) | 9 chỗ nối | 55,0 ms | – | KHÔNG ĐẠT |

- Hình lệch kế hoạch tới ~16 ms là nửa khung (33,3 ms ở 30 fps): đó là giới hạn của việc cắt theo khung, không phải lệch A/V.
- Ca 13,3 ms là đoạn 20 ms, ngắn hơn một khung, nên hiện một khung; đoạn sau bù lại.
- CI Linux không có FFmpeg nên bỏ qua các test này. Chúng chạy cục bộ và trên Windows (W4).

Số liệu thô: `/mnt/project-files/assisted/stabilization/av-boundaries-linux.jsonl`.

## 6. Windows và footage thật (W4)

### rc12 (a064807)

Windows 11, chỉ CPU, bundle rc12 (`1.1.96-rc12`). Kiểm bằng file (python và FFmpeg của bundle, `-B`), không qua UI.

**Tổng hợp.** `test_av_boundaries.py` lấy từ origin/main `a064807` (= commit rc12).

| Lần chạy | Kết quả |
|---|---|
| Nguyên bản | Encoder **mpeg4** (FFmpeg LGPL của bundle không có libx264). **10 OK, 5 ERROR**: test viết cứng `boxblur=2:1`, filter này không có trong FFmpeg của bundle. Sản phẩm không bị ảnh hưởng (nó dùng `render.available_blur`, ở đây là `gblur`) |
| Có bản chuyển (`tools\w4adapt.py`: chỉ đổi `boxblur=2:1` thành `gblur=sigma=2`; ngưỡng và cách đo giữ nguyên) | **15/15 OK** (65 s) |

Lệch A/V lớn nhất ở lần có bản chuyển:

| Ca | Lệch A/V lớn nhất | Kết quả |
|---|---|---|
| CFR 24/25/29,97, VFR, trộn tốc độ | 0,7 ms | ĐẠT |
| Không tiếng, ảnh tĩnh, đoạn ngắn | 13,3 ms | ĐẠT |
| 10 phần ghép, 29 chỗ nối, 0 không đo được | 0,7 ms | ĐẠT |
| Reference, ghép mới, 9/9 đo được | 19,0 ms | ĐẠT |
| Mốc: ghép copy cũ (10 không đo được) | 267,3 ms | KHÔNG ĐẠT, như mong đợi |
| Mốc: ghép cũ, cách reference | 55,0 ms | KHÔNG ĐẠT, như mong đợi |

**Chỗ nối thật: 0 trên 28 đo được, KHÔNG ĐẠT (chưa đo).**

| Lần chạy | Chỗ nối | Đo được | Mã thoát | Hình | Tiếng |
|---|---|---|---|---|---|
| W3 (22 phần, video dài) | 21 | 0 | 2 | 0,0 ms ở mọi chỗ nối | không đo được |
| W2 (8 phần, Top 5) | 7 | 0 | 2 | 0,0 ms (chỗ nối 1 và 6: −33,3 ms, một khung) | không đo được |

- Đo trên `compilation.mp4` của mỗi run. Không có bản cuối: Kiểm tra cuối từ chối xuất (W2: HELD_FRAME ở i1; W3: mục nghiêm trọng chưa xử lý).
- Ngưỡng của chỉ thị không xác nhận được trên footage thật. Không đo được không phải là đạt.

**Vì sao (bản điều tra rc12, §3).** Hình đo được 28/28 (khung khớp rõ). Tiếng bị loại 28/28.
- Cao độ giọng (≈110–250 Hz) làm gợn đường bao 1 ms. Tương quan vì vậy vẫn đạt 0,81–0,88 ở vài ms cạnh đỉnh (đỉnh phụ ở ±4–9 ms), so với 1,0 ở đỉnh.
- Tỉ số đỉnh/đỉnh phụ thật là 1,00–1,36. Quy tắc "≥ 2 × mọi độ trễ cách > 2 bin" không bao giờ đạt với lời dẫn trên nhạc.
- Ví dụ W2: chỗ nối vào i2, i3, outro có đỉnh ở 0 ms (1,000; 1,000; 0,925), đỉnh phụ 0,815 (+6 ms), 0,808 (−6 ms), 0,837 (+4 ms).

**Replay các biến thể** (bản điều tra, trên bản sao file rc12 Windows, dùng hàm của bundle; chẩn đoán, không phải kết quả gate). Ngưỡng và quy tắc đỉnh rõ giữ nguyên; chỉ đổi tín hiệu hoặc vùng loại trừ.

| Biến thể | W2 (7) | W3 (21) |
|---|---|---|
| Hiện tại (đường bao) | 0 | 0 |
| (a) onsets | 6 | 10 |
| (b) đỉnh phụ ngoài ±20 ms | 0 | 0 |
| (c) đoạn mốc 3 s | 0 | 0 |
| (a)+(b) | 7 | 20 |
| (a)+(c) | 6 | 19 |
| **(a)+(b)+(c)** | **7** | **21** |

- (a)+(b)+(c): **28/28 đo được**. 26 chỗ nối 0,0 ms; 2 chỗ nối −33,3 ms (W2 hook→intro và i4→i5). Hai giá trị này đến từ phía hình (hình lệch −1 khung, tiếng 0). Cả hai trong max(40 ms, 1 khung). Chưa điều tra thêm.
- Ca tổng hợp reference: mọi biến thể cho kết quả giống hệt cách hiện tại (ghép mới 9/9, tệ nhất 19 ms). Mốc ghép cũ vẫn trượt (−43 đến −55 ms). Các biến thể không làm mất khả năng bắt trôi.
- File W2 lúc điều tra là bản ra của lần dựng lại ở W5 (i3 dựng lại), không phải đúng file W4 đã đo. Số đường bao của i2/i3/outro vẫn trùng.
- (a)+(b)+(c) là bản sửa của PR #799 (§4b). rc12 không chứa nó.

### rc13

Bản 1.1.96-rc13 (`f46723a`, chứa PR #799), Windows, `sync_probe.py` và FFmpeg của bundle. Ngưỡng không đổi.

| Lần chạy | Chỗ nối | Đo được | Chưa đo | A/V tệ nhất | Mã thoát |
|---|---|---|---|---|---|
| Synthetic (`tests\mastering\test_av_boundaries.py` từ main, không adapter) | | 17/17 test OK, 0 skip, encoder mpeg4 | | | |
| W2 thật (8 phần, Top 5) | 7 | **7** | 0 | 33,3 ms (một khung, ở J1, J6, J7) | 0 |
| W3 thật (22 phần, video dài) | 21 | **21** | 0 | 0 ms | 0 |

- rc12: 0/28 đo được. rc13: **28/28 đo được, mọi chỗ nối trong max(40 ms, một khung)**. Số chỗ nối đo được trùng với replay biến thể (a)+(b)+(c) của bản điều tra rc12 (28/28). Giá trị gần trùng: replay có 2 chỗ −33,3 ms (W2 J1, J6), rc13 có 3 (W2 J1, J6, J7); tất cả trong max(40 ms, một khung).
- P3-N20 đã sửa: test synthetic chạy trên bundle mà không cần adapter.
- Đo trên `compilation.mp4` của mỗi run, vì chưa có bản cuối: W2 bị Kiểm tra cuối chặn (HELD_FRAME ở i1), W3 không chạy bước xuất.

**Gate C (P2-9) trên Windows rc13: ĐẠT** (28/28 chỗ nối đo được, 0 chưa đo). Lệch cộng dồn: A/V của mỗi chỗ nối đo theo thời gian tuyệt đối của bản ghép, nên phần trôi tích luỹ đã nằm trong số đo; lớn nhất 33,3 ms, dưới 80 ms.

### rc14

Bản 1.1.96-rc14 (`96400fb`). Synthetic 17/17 OK, không adapter. W2: `compilation.mp4` và bản cuối xuất lần 2 đều **7/7 đo được**, tệ nhất 33,3 ms (một khung), mã thoát 0. W3: **21/21**, 0 ms. Bản cuối xuất lần 1 không đo được từng chỗ nối vì phần i1 đã bị dựng lại ở bước ghim sau đó (W5 trên W2).

**Gate C trên rc14: ĐẠT**, lần đầu có cả bản cuối đã xuất (W2).

### rc15

Bản 1.1.96-rc15 (`6b1a0cf`). Ngưỡng không đổi.

| Lần chạy | Chỗ nối | Đo được | Chưa đo | A/V tệ nhất | Mã thoát |
|---|---|---|---|---|---|
| Synthetic (`test_av_boundaries.py` từ main) | | 17/17 test OK | | | |
| W2, bản cuối xuất lần 1 (`--final`) | 7 | **7** | 0 | 33,3 ms (J1, J6, J7, −1 khung phía hình) | 0 |
| W2, `compilation.mp4` | 7 | **7** | 0 | 33,3 ms | 0 |
| W2, bản cuối sau W5 (ghim i3.u2) | 7 | **7** | 0 | 33,3 ms | 0 |
| W3 (22 phần) | 21 | **21** | 0 | 0 ms | 0 |

**Gate C trên rc15: ĐẠT.**

## 7. Hồi quy

| Luồng | Kiểm |
|---|---|
| Bán hàng | test product, lưới khung trong argv; W5 |
| Tổng hợp v1 | argv ghép không đổi (test); bản xuất so sha256 với rc10 (W5) |
| Tổng hợp v2 / raw | test raw (render thật 8 phần, ghép copy), W2/W3 |
| Video dài | W3/W4 |
| Lồng tiếng Tắt | W5 |
| Dựng lại / tiếp tục | phần không đổi được dùng lại (receipt), W2 |

Phim tài liệu (`docu/roughcut`) nối lát cắt theo cách khác và **chưa được đo**. Việc đó là điều kiện vào pilot ([DOCUMENTARY_PILOT_ENTRY_GATES.md](DOCUMENTARY_PILOT_ENTRY_GATES.md) §6).
