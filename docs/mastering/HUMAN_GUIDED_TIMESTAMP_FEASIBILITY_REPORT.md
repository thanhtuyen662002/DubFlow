# Khả thi truy hồi cảnh theo mốc thời gian do người dùng đưa (thí nghiệm offline)

Epic #637 · issue #832 · đề xuất #815 (Phương án D) · chạy 2026-11-14 · công cụ `tests/mastering/longform/hintexp.py` · không gọi AI, chi phí $0.

> **Kết luận: INCONCLUSIVE.** Đây là thí nghiệm **khám phá**, không đăng ký trước. Nó không thay kết luận đã đăng ký trước của #815 (Phương án B: **NOT SUPPORTED**). Nó không đổi gate nào: Long-Form Production Gate B = **FAIL**, Human Validation Gate D = **BLOCKED**, Documentary Assisted Pilot = **NOT READY**, Documentary Auto = **NO-GO**, Phase 4 = **NO-GO**.

Câu hỏi: nếu người biên tập đưa một **mốc thời gian gần đúng** trong video nguồn cho mỗi câu lời dẫn, thì việc tìm cảnh quanh mốc đó có giúp tìm được cảnh đúng hơn cách xếp hạng hiện tại không, và mốc cần chính xác đến đâu.

Câu trả lời ngắn:

- Nếu mốc lệch tối đa ±5 s, cảnh đúng gần như luôn nằm trong 5 gợi ý. Nhưng con số này đến từ **mô phỏng dựa trên nhãn đúng**, không phải từ người thật.
- Ở ±15 s, lợi ích giảm còn khoảng một nửa. Ở ±30 s, lợi ích gần như không còn.
- Mốc tự suy từ vị trí câu (POS, không dùng nhãn) chỉ cải thiện ít.
- Chưa có người thật nào đánh mốc, nên chưa biết người dùng đạt độ chính xác nào. Vì vậy kết luận là INCONCLUSIVE, và bước tiếp theo là bài kiểm tra người dùng trong [human-timestamp-test/PROTOCOL.md](human-timestamp-test/PROTOCOL.md).

## 1. Bốn nhóm, và nhóm nào được gọi là gì

| Nhóm | Mốc lấy từ đâu | Nhãn đúng dùng để | Được trình bày là |
|---|---|---|---|
| **BASE** | không có mốc: xếp hạng production đã lưu | chấm điểm | kết quả hiện tại |
| **ORACLE5** | tâm khoảng đúng, cửa sổ ±5 s | **tạo mốc** và chấm điểm | **trần lý thuyết**, không phải truy hồi |
| **SIM e** (e = 5, 15, 30) | tâm khoảng đúng cộng độ lệch ngẫu nhiên đều trong [−e, e] (20 seed cố định); SIMworst lấy đúng −e và +e; cửa sổ ±e | **tạo mốc** và chấm điểm | **mô phỏng** độ chính xác mà người dùng cần đạt, không phải truy hồi |
| **POS e** | không dùng nhãn: trim nguồn của phần (đầu vào của job) và vị trí câu trong lời dẫn của phần, nội suy tuyến tính; cửa sổ ±e | chỉ chấm điểm | mốc tự động thô, không phải người dùng |

Chỉ BASE và POS là kết quả không nhìn nhãn đúng. ORACLE và SIM **dùng nhãn đúng làm đầu vào**, nên chúng chỉ cho biết "nếu người dùng chính xác đến mức đó thì được bao nhiêu". Chúng không chứng minh rằng người dùng đạt được mức đó.

## 2. Dữ liệu

- Một video dài: *Why We Fight: The Battle of Russia* (2146,8 s), trong chương trình Tổng hợp 20 phần (i1–i20) của các lần chạy W3.
- Lần chạy: rc12 `run-6206f16daf09`, rc13 `run-76080efb6da4`, rc14 `run-b3e7c7967a6e`, rc15 `run-8f64b0a50e04`, rc16 `run-f6919b04ccb3`. Đây là cùng các lần chạy của [LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md](LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md).
- Nhãn đúng W3: 64 câu (60 có footage, 4 không có), sha256 `afa979d2…f66497`, khoá trước rc12. Nhãn không bị sửa.
- Đây là **tập phát triển** (REAL-DEV). Nó đã được dùng để chẩn đoán từ rc12, nên không phải tập giữ riêng độc lập.
- Video nguồn, khung hình, nội dung câu và thư mục run nằm trên máy Windows của dự án, **không** có trong repo vì repo công khai. Kết quả chỉ chứa số tổng hợp.

## 3. Phương pháp

Với mỗi câu, công cụ lấy mốc theo nhóm, rồi lấy mọi ứng viên của phần có chồng lên cửa sổ [mốc − r, mốc + r], và xếp hạng theo một trong hai cách:

- **rel:** độ liên quan của chính `Scorer` trong run (cùng bằng chứng đã lưu, không gọi AI). Đây là cách chính.
- **dist:** khoảng cách từ tâm cảnh tới mốc. Đây là cách phụ để so sánh.

Mỗi nhóm chạy hai chế độ:

- **Không wf:** mọi câu đều được đánh mốc (64 mốc).
- **+wf (quy trình thật):** câu đã READY giữ lựa chọn production; chỉ câu bị gắn cờ mới được đánh mốc.

Câu không có footage luôn nhận mốc POS (vì không có khoảng đúng để lấy tâm). Nếu cửa sổ của câu đó có cảnh, câu đó bị tính là "bị gán cảnh".

Luật chấm giống W3 (`w3.hits`): cảnh trúng khi chồng lên khoảng đúng ít nhất 1 s, hoặc ít nhất một nửa đoạn ngắn hơn. Recall@k tính trên 60 câu có footage.

Số đo cho mỗi nhóm:

| Khoá trong JSON | Nghĩa |
|---|---|
| `r1`, `r3`, `r5` | số câu có footage có cảnh đúng trong top 1/3/5 |
| `wrong_top1` | câu được đánh mốc, cửa sổ có cảnh, nhưng cảnh hạng 1 sai (cảnh báo sai nếu tự chọn hạng 1) |
| `manual_needed` | câu không có cảnh đúng trong top 3: người dùng vẫn phải tìm tay |
| `marks` | số mốc người dùng phải đưa (công sức) |
| `missing_given_a_pick` | câu không có footage mà cửa sổ vẫn có cảnh |
| `empty_window` | câu được đánh mốc nhưng cửa sổ trống |
| `mean_shots_in_window` | số cảnh trung bình trong cửa sổ |

SIM báo trung bình, nhỏ nhất và lớn nhất qua 20 seed. Seed là `random.Random(str(s))` với s = 0..19, rút độ lệch theo thứ tự câu trong nhãn.

## 4. Kết quả rc16, xếp hạng rel, chế độ +wf

BASE rc16: r1/r3/r5 = 20/26/31 trên 60; chọn trúng 24; 56 câu bị gắn cờ, 4 câu READY; không có bỏ sót không báo.

| Nhóm | r1 / r3 / r5 | Khoảng r5 qua seed | Top 1 sai | Còn phải chọn tay | Số mốc | Câu không footage bị gán cảnh |
|---|---|---|---|---|---|---|
| BASE | 20 / 26 / 31 | | | | 0 | |
| ORACLE5 (trần) | 34 / 59 / 60 | | 26 | 1 | 60 | 4 / 4 |
| SIM5 (mô phỏng) | 37,4 / 54,5 / 59,6 | 58–60 | 22,6 | 5,5 | 60 | 4 / 4 |
| SIMworst5 | 38 / 50 / 58 | 56–60 | 22 | 10 | 60 | 4 / 4 |
| SIM15 (mô phỏng) | 27,6 / 38,9 / 45,6 | 42–53 | 32,4 | 21,1 | 60 | 4 / 4 |
| SIMworst15 | 31 / 43,5 / 46 | 32–60 | 29 | 16,5 | 60 | 4 / 4 |
| SIM30 (mô phỏng) | 25,1 / 32,1 / 36,9 | 34–40 | 34,9 | 27,9 | 60 | 4 / 4 |
| SIMworst30 | 29,5 / 42 / 44,5 | 32–57 | 30,5 | 18 | 60 | 4 / 4 |
| POS5 (không nhãn) | 16 / 23 / 25 | | 44 | 37 | 60 | 4 / 4 |
| POS15 (không nhãn) | 22 / 26 / 38 | | 38 | 34 | 60 | 4 / 4 |
| POS30 (không nhãn) | 23 / 27 / 32 | | 37 | 33 | 60 | 4 / 4 |

Cách đọc:

- **ORACLE5 là trần:** ngay cả khi mốc nằm đúng tâm, cảnh hạng 1 vẫn sai ở 26 câu. Mốc chỉ thu hẹp danh sách; người dùng vẫn phải chọn trong danh sách gợi ý. Vì vậy, nếu có, đây chỉ là **chế độ hỗ trợ** (Assisted), không phải tự động chọn.
- **SIM5 so với SIM15:** recall@5 giảm từ khoảng 60 xuống khoảng 46 trên 60. Ở ±30 s, recall@5 khoảng 37, gần mức BASE 31.
- **POS15** đạt recall@5 38 trên 60, nhỉnh hơn BASE, nhưng recall@3 vẫn 26 (bằng BASE). Mốc tự suy từ vị trí câu không đủ thay người dùng.
- **Câu không có footage:** cả 4 câu đều bị gán cảnh ở mọi nhóm, vì cửa sổ nào cũng có cảnh (`empty_window` = 0 ở mọi nhóm). Nếu thử với người dùng, cần có lựa chọn "không có cảnh phù hợp" rõ ràng.
- **Công sức:** 60 mốc cho một video 35 phút (56 câu bị gắn cờ cộng 4 câu không có footage).

Độ lệch của mốc POS so với khoảng đúng gần nhất (60 câu có footage): trung vị 9,4 s; 16 câu ≤ 5 s; 42 câu ≤ 15 s; 54 câu ≤ 30 s. Con số này giống nhau ở mọi lần chạy, vì trim và lời dẫn không đổi.

Số cảnh trung bình trong cửa sổ: khoảng 4,2 ở ±5 s, 10,2 ở ±15 s, 17,3 ở ±30 s.

## 5. Các lần chạy khác (rel, +wf)

| Lần chạy | BASE r1/r3/r5 | ORACLE5 r1/r3/r5 | SIM5 r5 | SIM15 r5 | SIM30 r5 | POS5 r5 | POS15 r5 | POS30 r5 |
|---|---|---|---|---|---|---|---|---|
| rc12 | 17 / 28 / 31 | 37 / 58 / 60 | 59,5 | 45,2 | 36,9 | 26 | 38 | 34 |
| rc13 | 20 / 31 / 33 | 33 / 58 / 60 | 59,6 | 45,0 | 36,0 | 28 | 37 | 31 |
| rc14 | 20 / 24 / 28 | 34 / 59 / 60 | 59,6 | 45,1 | 36,2 | 26 | 38 | 32 |
| rc15 | 21 / 27 / 29 | 34 / 56 / 58 | 57,6 | 44,4 | 35,8 | 26 | 35 | 32 |
| rc16 | 20 / 26 / 31 | 34 / 59 / 60 | 59,6 | 45,6 | 36,9 | 25 | 38 | 32 |

Năm lần chạy cho cùng một hình dạng. Đây là cùng một video và cùng bộ nhãn, nên sự ổn định này **không** thay cho một tập giữ riêng.

Hai so sánh phụ (rc16):

- **Không wf** (đánh mốc cả 64 câu): kết quả gần như trên, ví dụ ORACLE5 33/59/60, SIM5 36,6/54,1/59,6, SIM15 26,9/38,2/45,1, POS15 20/24/36.
- **Xếp hạng dist:** chỉ tốt khi mốc gần như đúng tâm (ORACLE5 59/60/60). Khi mốc lệch có hệ thống thì sụp (SIMworst5 r1 18; SIMworst15 6,5/13,5/19). Vì vậy `suggest` dùng rel.

Số đầy đủ của mọi nhóm, mọi chế độ và mọi lần chạy nằm trong `hint-results.json` (§7). Tệp này chỉ chứa số, không có nội dung câu hay đường dẫn.

## 6. Vì sao INCONCLUSIVE

1. **Không có mốc của người thật.** Nhóm duy nhất cho thấy lợi ích lớn (ORACLE5, SIM5) dùng nhãn đúng để tạo mốc. Chưa biết người biên tập thật đánh mốc chính xác đến ±5, ±15 hay ±30 s, và mất bao lâu.
2. **Một video phát triển.** Kết quả có thể không khái quát cho video khác.
3. **Không chạy lại planner.** Thí nghiệm chỉ xếp hạng lại ứng viên đã lưu. Nó không đo được READY sai hay bỏ sót không báo nếu mốc được đưa vào planner thật.
4. **Câu không có footage** vẫn nhận cảnh ở mọi nhóm, nên cần đo khả năng người dùng nhận ra thiếu footage.

Điều kiện dừng đề xuất: nếu bài kiểm tra người dùng cho thấy sai số mốc thực tế thường lớn hơn 15 s, thì dừng hướng này (ở mức đó lợi ích mô phỏng chỉ còn khoảng một nửa và cần nhiều thao tác).

## 7. Tái hiện

Yêu cầu: Python 3.12, repo DubFlow ở commit của PR này, và thư mục run W3 cùng tệp nhãn đã khoá (trên máy dự án, không có trong repo). Không cần mạng hay khoá API.

```
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1
py -3.12 -B tests\mastering\longform\hintexp.py study ^
  --gt <thư mục assisted>\rc16\logs\W3-ground-truth.json ^
  --lock <thư mục assisted>\rc16\logs\W3-ground-truth.lock ^
  --run rc12=<thư mục run-6206f16daf09> --run rc13=<thư mục run-76080efb6da4> ^
  --run rc14=<thư mục run-b3e7c7967a6e> --run rc15=<thư mục run-8f64b0a50e04> ^
  --run rc16=<thư mục run-f6919b04ccb3> ^
  --out hint-results.json
```

- Công cụ từ chối chạy nếu sha256 của nhãn không khớp tệp khoá.
- Kết quả có tính tất định (seed cố định).
- Chạy khoảng 65 s, bộ nhớ đỉnh khoảng 43 MB.
- Kết quả gốc ngày 2026-11-14: `hint-results.json`, sha256 `9c5e36c2…e495d`, 109.850 byte.
- Script gốc trên máy dự án (sha256 `8844829d…67bdcf`) đã được chuyển vào repo thành `hintexp.py`, có thêm tham số dòng lệnh và hai lệnh cho bài kiểm tra người dùng (`suggest`, `score-human`). Phần `study` giữ nguyên logic. Kết quả kiểm lại bằng bản trong repo: xem §8.

Kiểm thử đơn vị (dữ liệu tổng hợp, không phải kết quả):

```
python -B -m unittest tests.mastering.longform.test_hintexp
```

## 8. Kiểm lại bằng bản trong repo

Đã chạy trên máy dự án ngày 2026-11-14 (offline, 0 lời gọi AI): lệnh `study` của `hintexp.py` trong repo, với đúng các tham số ở §7, cho `hint-results.json` **giống từng byte** kết quả gốc (sha256 `9c5e36c2…e495d`, 109.850 byte), trong khoảng 74 s. Logic `study` trong repo vì vậy tái hiện đúng các số ở §3–§5. Hai lệnh mới (`suggest`, `score-human`) chỉ dùng cho bài kiểm tra người dùng, chưa có dữ liệu người tham gia nên chưa chạy thật; chúng chỉ được kiểm bằng dữ liệu tổng hợp trong `test_hintexp.py`.

## 9. Bước tiếp theo

- **Human Timestamp Feasibility Test** ([human-timestamp-test/PROTOCOL.md](human-timestamp-test/PROTOCOL.md)): 20–40 câu từ ít nhất hai video dài, so sánh Review Panel với cách đánh mốc rồi chọn trong gợi ý. Tiêu chí SUCCESS / INCONCLUSIVE / NEGATIVE được cố định trước. Trạng thái: **CHƯA CHẠY**, chờ chủ dự án duyệt người tham gia, video thứ hai và nhãn của nó.
- Nếu bài kiểm tra người dùng thành công: cần một tập giữ riêng mới (ít nhất 2 video dài, ít nhất 40 câu) trước khi đề xuất bất kỳ thay đổi sản phẩm nào, qua ADR riêng.
- Không xây tính năng timestamp, không thêm UI, không mở rộng kiến trúc truy hồi, không gọi vision, không phát hành stable.
