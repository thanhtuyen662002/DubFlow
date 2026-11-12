# rc12 — điều tra nguyên nhân gốc (chỉ đọc; mọi lần chạy đều trên bản sao trong rc12\investigate\)

Cách làm: tôi không sửa repo, không build/cài/mở DubFlow. Mã nguồn đọc từ `git show a0648076:<path>` (align.py của bundle giống hệt a0648076). Tôi viết một harness replay (`investigate\scripts\replay.py`) gọi `raw.run_program` của bundle trên bản sao thư mục W2. Vision giả chỉ đọc cache: một lần hỏi mạng sẽ bị ghi `ASK_ATTEMPT` rồi lỗi; có 0 lần như vậy. TTS chỉ lấy từ bản sao `tts-sentence-cache`. Montage thay bằng clip đen. Hai trạng thái được replay: bản nháp (không có file review) và lần dựng lại (review-i2/outro như lúc 19:11; review-i1 rev 1 = chỉ EXCLUDE i1-s1c1).
**Kiểm chứng replay:** khoá receipt của cả 8 phần trùng từng byte với file thật: hook 434c3f38, intro ee47a34f, i1 6d9d3344, i2 1025f414, i4 04fbc682, i5 94aca1e1, outro 9f0ba37e. i3 thật khác vì W5 dựng lại nó sau đó. Bản nháp → lần dựng lại chỉ đổi khoá i1 (7755c8cf→6d9d3344) và i4 (1f913f82→04fbc682), đúng "dựng 2 phần, dùng lại 6". Thứ tự frame vision trong replay khớp mtime của cache thật.

## 1. P2-N18 — vì sao i4 (không ai đụng) bị lập lại kế hoạch và dựng lại

**Không input nào trong digest kế hoạch của i4 thay đổi.** `plan-i4.json` provenance giống hệt ở bản nháp và lần dựng lại:
`{"i4-s1": "7a22f2beffd6e7c4858ed3ee0ebe4f71…", "part": "e22bd5d02e0770ac293733f7ae61cb7cc4e885f2d42c3992ff27207670e79f3d"}`
(`inputs.part` gồm spoken, kind, title, keep, editorial+edits (rỗng), `vision`={provider, model, budget 80}, `shown`=[] (với item luôn rỗng), trim.) Bộ "shown/avoid" không đổi. Budget cũng không đổi (80, "auto: 2 × 14 câu, tối thiểu 80").

**Thứ thay đổi là bằng chứng vision của i4-s1c4, qua phần ngân sách chia theo lượt chạy.** `raw.run_program`: `fair() = (budget − describer.taken) // (số phần còn lại)`. Bước multi-frame lấy `limit = min(8, room//2)` cảnh. `taken` được tính cả frame lấy từ cache. Ở lần dựng lại, các phần đứng trước i4 tiêu ít frame hơn:

| sau phần | taken nháp | taken dựng lại | lý do |
|---|---|---|---|
| intro | 47 | 47 | như nhau |
| i1 | 54 | 52 | nháp: targeted 5 + multi-frame i1-s1c1 (2). Dựng lại: targeted 5 (c4f3, c3f3, c3f2, c4f1, c4f2); c1 bị EXCLUDE ⇒ `locked` ⇒ không có multi-frame |
| i2 | 58 | 52 | nháp: multi-frame i2-s1c1, c2 (4). Dựng lại: hai cảnh đó đã duyệt (`edits.keep`) ⇒ locked ⇒ 0 |
| i3 | 62 | 58 | room 22→28; multi-frame limit 2→3 (thêm i3-s1c13) |
| i4 targeted | room (80−62)//3=6 → [c3f3, c3f1, c3f2] | room (80−58)//3=7 → cùng 3 id đó | **targeted giống hệt nhau** (các sub-shot đã có trong cache) |
| i4 multi-frame | room (80−65)//3=5 → limit 2 → [i4-s1c1, i4-s1c2] | room (80−61)//3=6 → limit 3 → [i4-s1c1, i4-s1c2, **i4-s1c4**] | |

Hai frame mới là `i4-s1c4@18900` và `@23100` (cache `585e2431…`, `e429f66e…`, ghi lúc 19:13:00). Đây là đúng 2 lần "asked" trong report. Chúng mô tả: "quả bóng đỏ lăn đến cuối máng trượt màu xanh lá…" và "máy quay đĩa than màu đỏ…". Hai mô tả khớp với frame giữa nên được `merge` (không tách cảnh). Bằng chứng vision của i4-s1c4 tăng từ 1 lên 3 mục ⇒ i4.u1 "Á quân là cỗ máy hoạt hình nhiều màu sắc.": coverage none (best 326) → full (280), chosen `[i4-s1c2f2, i4-s1c3f3]` → `[i4-s1c4]`. Kế hoạch vẽ ra đổi ⇒ khoá receipt đổi ⇒ dựng lại.
Bộ đếm vision trong editorial-i4 (luỹ kế): nháp `frames 69, targeted 8, multi_frame 22`; dựng lại `frames 67, targeted 8, multi_frame 20`. Bản thật ghi asked=2 vì hai frame đó lúc ấy chưa có trong cache.
Kết luận: i4 thay đổi vì **phần ngân sách vision còn lại cho từng phần phụ thuộc vào những gì các phần trước đã tiêu trong lượt này**. Exclude ở i1 và các duyệt ở i2 khoá cảnh, nên i1/i2 tiêu ít hơn, và i4 có thêm 1 cảnh multi-frame. Đây không phải targeted look, cũng không phải do avoid set. i5 cũng nhận thêm multi-frame i5-s1c14 (frame mới lúc 19:13:13), nhưng kế hoạch vẽ của i5 không đổi nên được dùng lại. Outro có `inputs.part` đổi (be92cfea → 111bf68b, vì `shown` thêm lựa chọn mới của i1/i4) nhưng cũng được dùng lại.

## 2. P2-N17 — 5 HELD_FRAME ở i1 sau khi dựng lại

i1 = `m2-lop-hoc-2995.webm`. ffprobe: format 21.514 s, 29483/986 fps; plan source duration 21502 ms (picture). i1 bắt đầu ở giây 8400 ms của chương trình. Narration 9490 ms: u1 0–4665, u2 4665–9490. Replay cho ra plan trùng hẳn `plan-i1.json` hiện tại và trùng các pieces đã được duyệt lúc 12:14:21Z.

| # | lúc (phần / chương trình) | dài | câu | segment kế hoạch |
|---|---|---|---|---|
| s1 | 0 / 8400 | 1950 | i1.u1 | i1-s1 src 12000–13950 broll (i1-s1c3f1), không giữ khung |
| s2 HELD | 1950 / 10350 | 1950 | i1.u1 | src 12975–12975 still (giữa c3f1, **chiếu lại chính c3f1**), repeat |
| s3 HELD | 3900 / 12300 | 765 | i1.u1 | src 6975 still (giữa c2f1 6000–7950), repeat |
| s4 HELD | 4665 / 13065 | 1185 | i1.u2 | src 6975 still (c2f1), repeat |
| s5 HELD | 5850 / 14250 | 1950 | i1.u2 | src 11025 still (c2f3 10050–12000), repeat |
| s6 HELD | 7800 / 16200 | 1690 | i1.u2 | src 9000 still (c2f2 7950–10050), repeat |
| s7 | 9490 / 17890 | 2100 | — | reaction src 13950–16050 (âm thanh nguồn) |

Mỗi segment HELD có lý do "nhắc lại cảnh đã chiếu (nguồn không còn cảnh phù hợp phía trước)" + "giữ một khung hình vì nguồn ngắn hơn lời bình". Không piece nào vượt quá độ dài nguồn.
**Phân loại:** không phải lỗi render (trim/fps), cũng không phải nguồn hết sớm. Đây là các piece bị khai báo là repeat do bộ lập kế hoạch tạo ra. `planners/compilation.plan_part` biến mọi piece có `offset < used_end` thành một khung giữ ở giữa cảnh. Chuỗi nguyên nhân, thấy được qua trace `_assign` (investigate\w2replay\rebuild2\trace-i1-rebuild2.txt):
1. Lượt đầu (narration trên toàn bộ footage, có exclude) là kế hoạch tốt, đi tiến: u1 = c2f1, c2f2, c2f3; u2 = c3f1, c3f2, c3f3, kết thúc ở 16825. Nhưng `_moment(..., ends={i1-s1:16825})` không tìm được moment ≥ `MOMENT_MIN_MS` 2000 phía sau. Phần còn lại của c3f3 chỉ 1175 ms; c4 đã bị multi-frame look của intro tách thành c4f1/f2/f3 = 1138/1225/1139 ms. Vì vậy planner rẽ sang nhánh "moment trước".
2. Ở nhánh đó, `_moment(by_source, pool, …, need_before_ms=9490)` dùng `_ahead()` trên `pool` **có cả i1-s1c1 đã bị loại (0–6000)**. Nó đặt moment ở 13950 (c3f2), tưởng có 13950 ms phía trước, trong khi phần không bị loại chỉ có 6000–13950 = 7950 ms cho 9490 ms narration.
3. Trong `_before(pool, 13950)`, aligner đi tiến cho u1 lấy cảnh khớp duy nhất là c3f1 (12000–13950, cảnh cuối cùng trước moment) trước tiên. Phía trước không còn gì, nên mọi piece sau đó thành `tier=repeat`: c3f1 lần nữa, rồi c2f1, c2f1, c2f3, c2f2. Planner giữ một khung cho từng piece ⇒ 5 HELD (tổng 7540 ms; chỉ 1950 ms là hình chuyển động).
Bản nháp đã có 1 HELD_FRAME theo đúng cơ chế này (i1.u2 1540 ms tại 7950, repeat c3f1; moment cũng ở 13950). Exclude làm số HELD tăng từ 1 lên 5.
**Phát hiện phụ (report bỏ sót):** lần dựng lại ở W5 (Ghim i3.u2, 13:11:38Z) đã ghi một bản ghi `INVALIDATED` cho i1.u1, `conflict.cause "plan_changed"`, shots [i1-s1c3f1, i1-s1c2f1]. Pieces không đổi. Nguyên nhân: `decision.approved` = ['i1-s1c3f1','i1-s1c2f1'] (đã khử trùng lặp), còn `unit.chosen` = ['i1-s1c3f1','i1-s1c3f1','i1-s1c2f1'] ⇒ nhánh `list(decision.approved) != chosen` trong `review.reconcile` báo plan_changed. `logs\records.json` được quét lúc 19:53 (trước W5 lúc 20:11), nên dòng "0 INVALIDATED" trong report là đã cũ; status.json của W2 có cảnh báo này.

## 3. P2-N19 — sync_probe exit 2

Lưu ý: compilation.mp4 và các part của W2 hiện là bản ra của lần W5 (20:11, i3 đã dựng lại), không phải file W4 đo lúc 19:4x. Số envelope của i2/i3/outro vẫn trùng diagnostic trong report: ngoài ±2 bin là 0.881/0.866/0.837. Script: `investigate\scripts\w4variants.py`, dùng `sp.audio/frames/envelope` của bundle. Kết quả nằm ở `investigate\w4\*.json` và `*-table.txt`.
3 mối nối của W2 (envelope 1 ms, REF 1500, ±300):

| mối nối | audio: đỉnh (lag) | lớn thứ hai ngoài ±3 bin | tỉ số hiện tại (ngoài ±2) | video frame_lag: tốt nhất / thứ hai (SAD) | bị loại bởi |
|---|---|---|---|---|---|
| J3 →i2 @20000 | 1.000 (0 ms) | 0.815 (+6 ms) | 1.135 < 2 | lag 0 SAD 0 / lag +1 SAD 126067 (rõ) | **chỉ audio** |
| J4 →i3 @32700 | 1.000 (0) | 0.808 (−6) | 1.154 | 0 / 0 vs +1 / 86009 (rõ) | chỉ audio |
| J7 →outro @69367 | 0.925 (0) | 0.837 (+4) | 1.105 | 0 / 0 vs +1 / 95173 (rõ) | chỉ audio |

Trên cả 28 mối nối thật, video đo được 28/28. Audio bị loại 28/28. Đỉnh phụ luôn ở ±4…9 ms, đúng chu kỳ cao độ giọng (≈110–250 Hz) làm biến điệu envelope 1 ms, nên quy tắc "≥ 2 × bất kỳ lag nào cách > 2 bin" không bao giờ đạt với narration (tỉ số thật 1.00–1.36).
Các biến thể. Không đổi ngưỡng của directive (≤ max(40 ms, 1 khung), luỹ kế ≤ 80). Tôi giữ quy tắc chấp nhận đỉnh của sync_probe (≥0.3 và ≥2× sidelobe), chỉ đổi tín hiệu hoặc vùng loại trừ:

| biến thể | W2 (7) | W3 (21) | av_ms |
|---|---|---|---|
| hiện tại (envelope) | 0 | 0 | — |
| (a) onset = sai phân bậc nhất của envelope | 6 (J6 không đo được: 1.81) | 10 | 0.0; W2 J1 −33.3 |
| (b) envelope, sidelobe ngoài ±20 ms | 0 (tỉ số 1.44–1.69) | 0 (1.34–1.96) | — ; PSR(±20) thật 3.4–10.2 |
| (c) envelope, REF_MS×2 = 3000 | 0 (1.07–1.22) | 0 (1.08–1.36) | — |
| (a)+(b) | 7 | 20 (J14: 1.807) | 0.0; W2 J1, J6 −33.3; W3 J18 +3.0 |
| (a)+(c) | 6 (J7: 1.686) | 19 (J10, J15) | 0.0; W2 J1, J6 −33.3 |
| (a)+(b)+(c) | 7 | 21 | **28/28 đo được**: 26 × 0.0, 2 × −33.3 (W2 J1 hook→intro, J6 i4→i5; video −1 khung, audio 0). Đều ≤ giới hạn 40 ms |

Hai giá trị −33.3 đến từ phía video: SAD tốt nhất khác 0 (162140 so với 193847; 170100 so với 188019). Tôi chưa điều tra thêm.
Synthetic: tôi tạo lại đúng case `test_the_reference_method_…` của W4 (adapter gblur, mpeg4) vào `investigate\w4synth\`. sync_probe hiện tại vẫn cho kết quả như W4 (new: 9/9, tệ nhất 19.0; old: tệ nhất 55.0). Mọi biến thể (a), (b), (c) và các tổ hợp đều đo được 9/9 với **av y hệt**: new [0, 0, 0, 0, 0, 0, 19, 7, 16] ms; baseline cũ [−45, −45, −55, −54, −43, −43, −52, −52, −52] (vẫn trượt). PSR synthetic 22–51. Như vậy các biến thể không làm mất khả năng bắt lỗi drift.

## 4. Gate B (W3, recall@5 51.7 % = 31/60) — 29 câu trượt

Phân loại theo `score_w3.hits` (chồng ≥ 1000 ms hoặc ≥ ½ phần ngắn hơn), trên pool candidate của chính phần đó (`editorial-<part>.json`). GT không đổi, không chấm lại (`investigate\gateb.json`).
- **(a) không có candidate trúng trong cửa sổ: 0.**
- **(d) range ngoài cửa sổ / khác: 0.** Mọi range đều nằm trong cửa sổ 105 s của mục.
- **(b) có candidate trúng nhưng không có bằng chứng vision: 22.** Gồm i2.u2, i3.u2, i4.u2, i4.u3, i5.u2, i7.u3, i8.u2, i8.u3, i10.u1, i10.u3, i11.u1, i11.u3, i12.u1, i13.u2, i14.u2, i16.u2, i17.u2, i17.u3, i18.u2, i19.u2, i20.u1, i20.u2. Trong đó i18.u2: candidate trúng duy nhất là i18-s1c100602 bị đánh "quá tối" nên không bao giờ được xem.
  - i4.u2 "Trang sách mở ra với con số 1812": GT 332.5–335 s. i4-s1c100091 (330.2–340.3 s) trúng nhưng chưa ai xem. Top5 ở 349.9–371.7 s; MATCH_NOT_FOUND.
  - i8.u3 "nữ diễn viên ba lê…": GT 826.5–832.5. i8-s1c100291 (826.9–831.9) chưa xem. Top5 ở 791–821 s.
  - i17.u3 "Chữ Sevastopol… bức tượng": GT 1713.5–1719.5. c100559/560/561 trúng, cả 3 chưa xem. Top5 ở 1702–1728 s.
- **(c) có bằng chứng vision nhưng không vào top 5: 7.** Gồm i7.u2, i9.u2, i11.u2, i12.u3, i15.u1, i15.u2, i16.u1. Nguyên nhân con:
  - Ràng buộc đi tiến: một câu trước đã dùng footage nằm sau trong phim (i7.u2, i11.u2, i15.u2). Ví dụ i7.u2: cảnh múa ở i7-s1c100210 có relevance 988, nhưng i7.u1 đã chọn c100222 @669.4 s (range GT thứ hai của u1) ⇒ "có cảnh khớp… nhưng không đặt được".
  - Vision mô tả khoảnh khắc khác trong cảnh (i15.u1: keyframe của c100477 dài 6.8 s là "bản đồ… '500,000 SQUARE MILES'" chứ không phải bản đồ Mỹ; i12.u3: keyframe c100412 là xe quân sự, còn tháp đồng hồ nằm ở c100413 chưa xem). Không còn ngân sách multi-frame.
  - Lời câu chỉ khớp thẻ chữ, bị để dành cho câu nói về chữ (i9.u2: "khung chữ… câu trích dẫn… bìa sách", relevance 15).
  - Lệch từ vựng/số (i16.u1: vision "mũi tên… '1500 MILES'" so với câu "một nghìn năm trăm dặm", c100509 không vào top 3 alternatives).

Số vision theo phần. Ngân sách 142, broad 95 trước khi lập kế hoạch. Mỗi phần nhận `fair()` = (142 − taken)//phần còn lại = **2 frame** (outro 3). targeted = min(room, max(8, room//2)) = 2; sau đó room < 2 nên **multi-frame = 0 cho mọi item** (chỉ intro có 2).

| phần | broad | targeted | multi | số shot | shot có bằng chứng | milli |
|---|---|---|---|---|---|---|
| intro | — | 2 | 2 | 712 | 104 | 149 |
| i1…i20 | 5,5,6,5,6,5,4,4,4,5,4,5,4,4,5,5,5,4,5,5 | 2 mỗi phần | 0 | 20,31,36,35,34,46,49,47,29,35,34,33,31,27,29,37,49,40,34,36 | 8,8,8,7,9,7,6,6,7,7,9,8,6,6,7,9,7,6,8,7 | 402,194,237,179,254,161,189,122,430,124,226,271,115,151,235,262,111,234,338,191 |
| outro | — | 3 | 0 | 712 | 161 | 252 |

(broad của từng phần = số frame trong 95 frame đầu tiên của cache có candidate thuộc pool phần đó; tổng 95. Frame của intro/outro rơi vào footage của các item.)
⇒ 22/29 câu trượt là do footage đúng chưa bao giờ được nhìn: mỗi item chỉ có 6–9 shot được xem, trên 20–49 shot.

## 5. report-rc12.md (toàn văn)

Bản đầy đủ (câu trả lời + toàn văn report, 36,9 KB) nằm ở `D:\Projects\DubFlow\_sandbox\assisted\rc12\investigate\answer.md`. Toàn văn report gốc chép nguyên dưới đây:
