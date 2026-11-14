# Human Timestamp Feasibility Test: phiên P1 (2026-11-14)

**HUMAN (real participant), n = 1.** Issue #832, theo [PROTOCOL.md](PROTOCOL.md). Người tham gia là chủ dự án và chưa xem hai phim trước phiên. Phiên chạy trên trang thử nghiệm offline trên máy dự án, không phải DubFlow.exe và không phải Desktop UI E2E. Không gọi AI hay Vision API ở bước nào, chi phí 0 đồng.

Các gate giữ nguyên: Long-Form Production Gate B = FAIL, Human Validation Gate D = BLOCKED, Documentary Assisted Pilot = NOT READY, Documentary Auto = NO-GO. Không phát hành stable, không bắt đầu Phase 4.

## 1. Kết luận theo PROTOCOL §6 (cố định trước phiên): NEGATIVE

TS (tua phim, đánh mốc rồi chọn trong gợi ý) chậm hơn RP (chọn trong danh sách cảnh) khoảng 2,7 lần (trung vị 80,7 s so với 30,0 s mỗi câu) mà không chọn đúng nhiều hơn (TS 8/12, RP 8/11). Tỉ lệ chọn đúng TS 67 %, RP 73 %. Đây là điều kiện NEGATIVE "TS chậm hơn RP từ 20 % trở lên mà chọn đúng không cao hơn" của §6. Các điều kiện hợp lệ đều đạt (32 câu, 2 video, 9 câu không có footage) và không có sai lệch giao thức. Hai điều kiện NEGATIVE còn lại không xảy ra: sai số mốc trung vị 0 s, 75 % trong ±15 s; TS không gán cảnh cho câu không có footage nào.

**Đề xuất: không phát triển Human-Guided Timestamp thành tính năng ở thời điểm này.** Một phiên NEGATIVE với một người không mở Gate D.

## 2. Thiết kế

- Bộ 32 câu mới, khác vòng AI proxy ([AI_PROXY_TEST_REPORT.md](AI_PROXY_TEST_REPORT.md)), vì người tham gia đã đọc số câu không có footage và mã câu của vòng đó:
  - 16 câu video 1 (Battle of Russia, 8 phần W3 không dùng ở vòng AI: i3, i5, i6, i9, i13, i14, i15, i19);
  - 16 câu video 2 ("Divide and Conquer", 1943, Public Domain, cùng file và sha256 như vòng AI; 8 cửa sổ 100 s mới k1–k8).
- 9 câu không có footage (1 ở video 1, 8 ở video 2). Con số được chọn ngẫu nhiên trong khoảng 5–9 và giữ kín đến sau phiên; người tham gia chỉ biết là có một số câu như vậy.
- Nhãn video 1: nhãn W3 đã khoá từ rc11. Nhãn video 2: một agent riêng viết chỉ từ ảnh 1 khung/giây, khoá sha256 và mã hoá trước phiên.
- Thứ tự ABBA: V1-a RP, V1-b TS, V2-b TS, V2-a RP. Không câu nào được làm bằng cả hai cách. 2 câu luyện tập không tính.
- RP: video 1 hiện danh sách production đã lưu của rc16 (cảnh đang chọn, cảnh thay thế, "Chọn cảnh khác…"); video 2 không có run production nên chỉ có pool offline (5 shot đầu theo thời gian), như đã ghi ở vòng AI.
- TS: trình phát HTML5 cả phim, không có giờ in trên hình (khác vòng AI). Gợi ý video 1 lấy từ `hintexp.suggest` (xếp theo `rel`); video 2 không có evidence nên gợi ý xếp theo khoảng cách tới mốc.
- Chấm bằng `hintexp.score_human` (bản 423d0e0, trong PR này là a5e2623) và bằng bảng theo đúng gợi ý người tham gia đã thấy ("as-shown"). Hai cách chỉ khác ở TS Recall@3: chính thức 7, as-shown 8. Lệch ở một câu video 2, vì script tính lại gợi ý video 2 bằng `rel` thay vì theo khoảng cách. Recall@1 và @5 trùng nhau. Kết luận §6 không phụ thuộc vào Recall.

## 3. Số đo

| Chỉ số | RP | TS |
|---|---:|---:|
| Câu (có footage / không có) | 16 (11 / 5) | 16 (12 / 4) |
| Recall@1 / @3 / @5 của danh sách phần mềm (as-shown) | 4 / 6 / 6 | 4 / 8 / 8 (chính thức 4 / 7 / 8) |
| Chọn đúng cảnh | 8/11 | 8/12 |
| Nhận nhầm cảnh | 1 | 4 |
| Có footage nhưng trả lời "không có cảnh" | 2 | 0 |
| Câu không có footage được nhận ra | 5/5 | 4/4 |
| Câu không có footage bị gán cảnh | 0 | 0 |
| Thao tác: trung vị; tổng | 4,5; 77 | 3; 43 |
| Tìm lại (RP: mở thêm "Chọn cảnh khác…"; TS: đánh mốc lại) | 9 | 2 |
| Thời gian người dùng: trung vị mỗi câu; tổng | 30,0 s; 10,1 phút | 80,7 s; 21,5 phút |

Mốc thời gian (TS, 12 câu có footage): sai số trung vị 0 s; 9/12 trong ±5 s (cũng là trong ±15 và ±30 s); 3/12 lệch hơn 30 s (37,5; 46,5; 54,5 s), cả ba ở video 1, khi độ chắc chắn khai là ±15, ±15 và ±5 s. Độ chắc chắn khai: ±5 s ở 6 câu, ±15 s ở 5, ±30 s ở 1. Một câu được đánh mốc trúng cảnh đúng nhưng 5 gợi ý không chứa cảnh đó; sau 2 lần đánh mốc lại thì chọn nhầm. Đó là lỗi xếp hạng gợi ý, không phải lỗi mốc.

Theo video:

| Video | RP: list@5, chọn đúng, thời gian trung vị | TS: list@5, chọn đúng, thời gian trung vị |
|---|---|---|
| Video 1 (có danh sách production) | 5/7, 5/7, 21 s | 4/8, 4/8, 47 s |
| Video 2 (pool offline) | 1/4, 3/4, 33 s | 4/4, 4/4, 98 s |

Theo loại câu: ở câu có footage, thời gian trung vị RP 15,2 s, TS 48,4 s; ở câu không có footage, RP 32,3 s, TS 117,8 s (với TS, muốn chắc là không có cảnh thì phải tua gần hết phim). TS chậm hơn ở cả hai loại câu.

Thời lượng phiên: 36 phút từ câu luyện tập đầu đến câu cuối, kể cả nghỉ; các câu đo chiếm 31,6 phút.

## 4. Phân loại

- Thực đo với người thật: toàn bộ mục 3.
- Mô phỏng: không có. Oracle: không dùng; nhãn chỉ dùng để chấm.
- Không thực hiện được: Desktop UI E2E (RP chạy trên trang offline vì DubFlow.exe có thể ghi vào run và gọi vision có phí); danh sách production cho video 2 (cần dựng run có phí, chủ dự án chọn giữ pool offline, 0 đồng); người tham gia thứ hai.

## 5. Kiểm tra độc lập

Theo PROTOCOL §7, repo chỉ nhận số tổng hợp và sha256; bảng từng câu không nằm trong repo. Một bảng từng câu đã lược bỏ (32 dòng, không có câu chữ, chỉ mã câu, cờ trúng/nhầm và số đo; cờ do script trên máy dự án đánh theo nhãn) được chuyển riêng cho một bên kiểm tra không ở trên máy dự án. Bên đó cộng lại bằng script riêng và khớp mọi số ở mục 3: RP list 4/6/6, chọn đúng 8/11, nhầm 1, 2 câu có footage bị trả lời "không có cảnh", thao tác trung vị 4,5 (tổng 77), tìm lại 9, thời gian trung vị 30,05 s (tổng 10,1 phút); TS list 4/8/8, chọn đúng 8/12, nhầm 4, thao tác 3 (43), tìm lại 2, 80,65 s (21,5 phút); tỉ lệ 2,68 (script báo 2,69 do làm tròn); 9/9 câu không footage được nhận ra, 0 bị gán cảnh; sai số mốc 9/12 trong ±5 s, ba câu lệch 37,5, 46,5 và 54,5 s; theo video RP 21,1 s và 33,2 s, TS 47,2 s và 97,8 s. Mỗi câu xuất hiện đúng một lần. Bên kiểm tra không đối chiếu được từng câu với nhãn vì nhãn chỉ có trên máy dự án.

Bảng này được xuất ra sau phiên và chép qua tin nhắn; sha256 của bản đã nhận: `eae71c05…80b19d9c`.

SHA-256 trên máy dự án: units.csv `c71b9a44…089b94`, log.jsonl `05c05200…1bfc9e`, session.csv `b9b8b251…413a786d`, human-results.json `a80f5807…07010aa`; nhãn video 2 `bc7a7789…1d5573`; nhãn W3 `afa979d2…f66497`. Quét rò rỉ trên máy dự án: 0.

## 6. Đọc kết quả

- Phần tốn thời gian nhất của TS là tự tua phim tìm chỗ. RP có sẵn danh sách để chọn.
- Ở video 1, nơi phần mềm đã có bằng chứng, RP vừa nhanh hơn vừa đúng hơn.
- TS chỉ đúng hơn ở video 2, nơi RP không có danh sách production, nhưng chỉ có 4 câu và chậm khoảng 3 lần.
- So với vòng AI proxy (TS 25/25, sai số 0 s nhờ giờ in trên hình): người thật sai số lớn hơn, nhận nhầm nhiều hơn và chậm hơn nhiều, đúng như giới hạn đã ghi cho vòng AI.

## 7. Giới hạn

1. Một người tham gia, là chủ dự án; 11–12 câu có footage mỗi cách. Không đủ để khái quát.
2. RP chạy trên trang offline, không phải DubFlow.exe; video 2 không có danh sách production.
3. Gợi ý TS video 2 xếp theo khoảng cách tới mốc; `score_human` tính lại bằng `rel`, nên Recall@3 chính thức lệch 1 so với as-shown. Nếu còn phiên sau, script chấm nên dùng đúng danh sách đã hiện (`suggested_ids` trong form).
4. Nhãn của cả hai video do AI làm từ ảnh khung hình, chưa có người kiểm tra lại. Ba câu lệch hơn 30 s có thể là cảnh na ná ở nhiều chỗ; nhãn và kết quả không bị sửa.
5. Tỉ lệ câu không có footage là 9/32 (28 %), cao hơn mức 20–25 % của PROTOCOL, và lệch về video 2 (8/16), vì con số được chọn ngẫu nhiên trong 5–9 rồi giữ kín.
6. Thứ tự ABBA với một người: hiệu ứng học và mệt không tách được khỏi phương pháp.
7. W3 là REAL-DEV: GENERALIZATION NOT VERIFIED.

## 8. Đề xuất

- Không phát triển Human-Guided Timestamp thành tính năng ở thời điểm này.
- Nếu còn theo hướng này, chỉ nên thử dạng gợi ý mốc tự động để người dùng không phải tự tua; việc đó cần ADR riêng và phép thử mới.
- Gate D vẫn BLOCKED. Không thêm UI, không đổi production planner, không gọi vision có phí.
