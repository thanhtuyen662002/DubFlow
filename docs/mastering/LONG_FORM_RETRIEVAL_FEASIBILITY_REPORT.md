# Khả thi truy hồi cảnh cho phim dài (Workstream C)

Epic #637 · 2026-11-13 · chỉ thị "DUBFLOW — ASSISTED SAFETY HOTFIX & LONG-FORM RETRIEVAL FEASIBILITY" · công cụ chẩn đoán PR #810 (issue #809) · bản kiểm Windows **1.1.96-rc16**.

Báo cáo này là **chẩn đoán**. Nó không thay đổi thuật toán truy hồi, không chỉnh heuristic trên tập 60 câu, và không sửa nhãn. Kết quả chính:

- **Gate C Feasibility: PASS** (§18).
- **Long-Form Production Gate B: FAIL** (§19): recall@5 trên rc16 là 31/60 (51,7 %), mục tiêu ≥ 85 %.
- Nút thắt chính là **thu thập bằng chứng** (R3): ở rc16, 26 trên 36 câu trượt (72 %) có cảnh đúng trong danh sách ứng viên nhưng AI chưa bao giờ nhìn cảnh đó.

## 1. HEAD và baseline

| | Commit | Ghi chú |
|---|---|---|
| Trước sửa | `43cdc2c` (main sau #807, = code của rc15 `6b1a0cf` cộng tài liệu) | baseline |
| Sau sửa | `205e3ff` (main sau #808, #810, #811) | rc16, release run 43361381416; mọi số rc16 trong báo cáo này |
| HEAD hiện tại | `dd492e5` (main sau #813) | #813 chỉ đổi thứ tự từ trong ghi chú duyệt; bộ hồi quy tổng hợp và Gate S thật giống từng byte; W3 không chạy lại |
| Công cụ | `tests/mastering/longform/w3.py` (`21fb55f`, cập nhật theo review Lead) | chỉ đọc lần chạy đã lưu, không gọi AI |

Lần chạy: rc12 `run-6206f16daf09`, rc13 `run-76080efb6da4`, rc14 `run-b3e7c7967a6e`, rc15 `run-8f64b0a50e04`, rc16 `run-f6919b04ccb3`. Mỗi lần là một lần chạy mới với cache vision trống. Bằng chứng (nhật ký, kế hoạch, ứng viên, câu trả lời vision, ảnh khung) nằm trên PC Windows, không đẩy lên GitHub vì repo công khai.

## 2. Bộ dữ liệu và nhãn khoá

- Một chương trình Tổng hợp 20 phút: 20 mục, mỗi mục là một cửa sổ 105 s của "Why We Fight: The Battle of Russia" (2146,8 s), cùng lời dẫn ở mọi lần chạy (dạng 20/20 giống rc15).
- Nhãn: 64 câu; 60 câu có footage, 4 câu không có (chỉ số 12, 25, 41, 57). Mỗi câu có các đoạn nguồn chấp nhận được (giây trong phim). Câu được ghép bằng lời dẫn đã chuẩn hoá.
- Khoá: sha256 `afa979d2…f66497`, khoá trước khi chấm rc12. Công cụ chỉ đọc nhãn sau khi sha256 khớp khoá; nhãn bị đổi thì bị từ chối (có test).
- Luật chấm: top 5; trúng khi chồng ≥ 1 s hoặc ≥ một nửa đoạn; recall@1/3/5; bỏ sót không báo nghiêm trọng; READY precision; gắn cờ nhưng đúng. Câu không ghép được vẫn nằm trong mẫu số (sửa theo Lead).
- Không sửa nhãn nào sau khi thấy kết quả.

Đây là **tập phát triển**: nó đã được dùng để chẩn đoán từ rc12. Nó không phải tập độc lập cho Gate B (§19).

## 3. Phân rã pipeline

| Tầng | Câu hỏi | Dấu hiệu thất bại trong nhật ký |
|---|---|---|
| R1 Nguồn | đoạn đúng có nằm trong phần nguồn mục được phép dùng (trim) không? | đoạn đúng ngoài trim của spec |
| R2 Dò cảnh | có ứng viên nào phủ đoạn đúng, và dùng được không? | không ứng viên nào chồng đoạn đúng (R2_DISCOVERY), hoặc có nhưng không dùng được (R2_UNUSABLE) |
| R3 Thu bằng chứng | AI có nhìn ứng viên đúng không? | ứng viên đúng không có câu trả lời vision |
| R4 Hiểu bằng chứng | câu trả lời có chung từ với lời dẫn, và đủ mạnh không? | không chung từ (R4n) hoặc khớp yếu (R4w) |
| R5 Xếp hạng | ứng viên đúng đã nhìn, khớp đủ mạnh, có được chọn không? | không bị con trỏ hay câu khác chặn nhưng vẫn không được chọn (có thể vẫn nằm trong top 5, ví dụ i2.u3 hạng 4) |
| R6 Phân bổ | ứng viên đúng có bị luật phân bổ chặn không? | mọi cảnh đúng kết thúc trước con trỏ của nguồn chỉ đi tới (R6_BEHIND), hoặc câu khác đã lấy. Công cụ không xét top 5 ở tầng này, khác định nghĩa R6 của chỉ thị §11 |
| R7 Trạng thái duyệt | kết quả có được báo đúng không? | READY trên cảnh sai (bỏ sót không báo) |

Mỗi câu trượt được gán **tầng sớm nhất** đã làm mất footage của nó. UNKNOWN và UNMATCHED không bao giờ bị giấu.

## 4. Ma trận gán lỗi

rc16 (60 câu có footage; trim lấy từ spec cho 60/60):

| Tầng | Số câu | Tỉ lệ trên 60 | Tỉ lệ trên 36 câu trượt |
|---|---:|---:|---:|
| OK (cảnh được chọn trúng) | 24 | 40,0 % | — |
| R1 Nguồn | 0 | 0 % | 0 % |
| R2 Dò cảnh | 1 | 1,7 % | 2,8 % |
| R3 Thu bằng chứng | **26** | **43,3 %** | **72,2 %** |
| R4 Hiểu bằng chứng | 3 | 5,0 % | 8,3 % |
| R5 Xếp hạng | 3 | 5,0 % | 8,3 % |
| R6 Phân bổ (sau con trỏ) | 3 | 5,0 % | 8,3 % |
| UNMATCHED / UNKNOWN | 0 | 0 % | 0 % |

36/36 câu trượt được gán một tầng có bằng chứng kiểm tra được (100 %, ngưỡng ≥ 90 %).

Năm lần chạy (cùng nhãn, cùng luật):

| Tầng | rc12 | rc13 | rc14 | rc15 | rc16 |
|---|---:|---:|---:|---:|---:|
| OK | 25 | 26 | 23 | 25 | 24 |
| R2 | 1 | 1 | 1 | 1 | 1 |
| R3 | 24 | 23 | 28 | 25 | 26 |
| R4 (yếu + không chung từ) | 4 | 3 | 3 | 1 | 3 |
| R5 | 3 | 3 | 2 | 5 | 3 |
| R6 | 3 | 4 | 3 | 3 | 3 |

(rc12–rc15 chấm bằng phiên bản công cụ trước bản sửa theo Lead; R4 và R6 của chúng là tổng các nhánh con.)

### Bảng từng câu (rc16, `run-f6919b04ccb3`)

Bảng gọn dưới đây. Bản ghi đủ các trường tối thiểu của chỉ thị §12 (lời dẫn, cảnh đúng có được nhìn không, chữ vision của cảnh đúng, ràng buộc phân bổ, nguyên nhân phụ, đường dẫn bằng chứng trong sandbox) nằm ở [assisted-e2e/rc16/W3-attribution-rc16.md](assisted-e2e/rc16/W3-attribution-rc16.md). Id cảnh bỏ tiền tố `iN-s1c`. "Cảnh đúng" là các cảnh chồng đoạn nhãn. "Hạng" là vị trí của cảnh đúng tốt nhất trong top 5 (— nếu không có). Công cụ chỉ gán một tầng chính; cột nguyên nhân phụ là "none" ở mọi câu. Ràng buộc phân bổ chỉ có ở 3 câu R6 (con trỏ: i7.u2 673682 ms, i11.u2 1127522 ms, i15.u2 1512320 ms); rc16 không có câu nào bị câu khác lấy mất cảnh.

Đọc bảng: một câu R3 có thể vẫn có hạng (ví dụ i6.u2 hạng 4) khi cảnh đúng vào top 5 nhờ tín hiệu khác ngoài vision, nhưng chính cảnh đó chưa bao giờ được nhìn.

| unit | GT ranges (s) | right shots | rank | chosen | hit | state | stage | R7 (review) | trim |
|---|---|---|---|---|---|---|---|---|---|
| i1.u1 | 1.5–10 | 100002 | — | 100004 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i1.u2 | 57.5–68 | 100011,100012 | 1 | 100011,100017 | y | READY | OK | READY_HIT | spec |
| i1.u3 | 73.5–98 | 100015,100016,100017,100018,100019 | 1 | 100019 | y | READY | OK | READY_HIT | spec |
| i2.u1 | 126.5–130.5 | 100024,100025 | 1 | 100024,100025 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i2.u2 | 142–150.5 | 100031,100032,100033 | — | 100027,100028,100035 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i2.u3 | 168–176.5 | 100039,100040,100041 | 4 | 100035,100037 | n | MATCH_NOT_FOUND | R5_RANKING | FLAGGED_MISS | spec |
| i3.u1 | 209.5–214.5;215–225.5 | 100051,100052,100053,100054 | 1 | 100051,100055 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i3.u2 | 235.5–241.5 | 100058,100059 | — | 100055,100069,100073 | n | MATCH_NOT_FOUND | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i3.u3 | 279.5–285.5 | 100074,100075 | 1 | 100074,100075 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i4.u1 | 315.5–319 | 100086 | 3 | 100090,100095 | n | NEEDS_REVIEW | R4_WEAK_MATCH | FLAGGED_MISS | spec |
| i4.u2 | 332.5–335 | 100091 | — | 100095 | n | MATCH_NOT_FOUND | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i4.u3 | 394.5–402.5 | 100110 | — | 100097,100098 | n | MATCH_NOT_FOUND | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i4.u4 | — (missing) | — | — | 100103,100104 | — | NEEDS_REVIEW | MISSING_OK | — | — |
| i5.u1 | 421–448 | 100120,100121,100122,100123,100124 | 1 | 100124 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i5.u2 | 465–469.5 | 100131,100132,100133 | — | 100128,100129,100141 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i5.u3 | 499–505.5 | 100146,100147 | 2 | 100145,100146 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i6.u1 | 548–553 | 100165,100166,100167 | 1 | 100166,100168,100169 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i6.u2 | 567.5–571.5 | 100177,100178 | 4 | 100176,100182 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i6.u3 | 598–607 | 100188,100189,100190 | 2 | 100187,100188 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i7.u1 | 645.5–649.5;669.5–672.5 | 100205,100206,100207,100222 | 1 | 100222 | y | READY | OK | READY_HIT | spec |
| i7.u2 | 650.5–657.5 | 100209,100210,100211 | — | 100222 | n | NEEDS_REVIEW | R6_BEHIND_CURSOR | FLAGGED_MISS | spec |
| i7.u3 | 723.5–726.5 | 100244,100245 | — | 100225,100226 | n | MATCH_NOT_FOUND | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i8.u1 | 750.5–754.5 | 100253,100254 | 4 | 100246 | n | MATCH_NOT_FOUND | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i8.u2 | 764.5–768 | 100259 | — | 100269,100281 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i8.u3 | 826.5–832.5 | 100291 | — | 100282,100285,100286 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i8.u4 | — (missing) | — | — | 100287,100291 | — | MATCH_NOT_FOUND | MISSING_OK | — | — |
| i9.u1 | 865.5–871.5 | 100300 | 1 | 100300 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i9.u2 | 873.5–883.5 | 100301,100302 | — | 100304,100306 | n | MATCH_NOT_FOUND | R5_RANKING | FLAGGED_MISS | spec |
| i9.u3 | 898–907.5 | 100306,100307 | 1 | 100307 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i10.u1 | 946–948.8 | 100322 | — | 100325,100329 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i10.u2 | 966–969 | 100330,100331 | 1 | 100331,100332 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i10.u3 | 1034.5–1040.5 | 100352,100353 | — | 100348 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i11.u1 | 1055.5–1063.5 | 100356,100357 | — | 100355,100373,100380 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i11.u2 | 1085.5–1095.5 | 100364–100369 (6) | — | 100382,100383 | n | NEEDS_REVIEW | R6_BEHIND_CURSOR | FLAGGED_MISS | spec |
| i11.u3 | 1095.5–1101.5 | 100369 | — | 100383 | n | MATCH_NOT_FOUND | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i12.u1 | 1166–1169 | 100389,100390 | — | 100391,100395 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i12.u2 | 1208.5–1228 | 100402,100403,100404,100405 | 1 | 100405 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i12.u3 | 1244.5–1248.5 | 100412,100413 | — | 100407 | n | MATCH_NOT_FOUND | R4_NO_SHARED_WORDS | FLAGGED_MISS | spec |
| i13.u1 | 1269–1272 | 100424 | 1 | 100424,100426 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i13.u2 | 1309–1318 | 100439,100440,100441 | — | 100435,100437 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i13.u3 | 1317.5–1321.5 | 100442,100443 | 1 | 100443,100444 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i13.u4 | — (missing) | — | — | 100444,100446 | — | NEEDS_REVIEW | MISSING_OK | — | — |
| i14.u1 | 1380.5–1393 | 100454–100459 (6) | 1 | 100454,100458,100462 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i14.u2 | 1416.5–1422.5 | 100465 | 4 | 100462 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i14.u3 | 1456.5–1464.5 | 100475,100476 | — | 100462,100471 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i15.u1 | 1471–1475.5 | 100477 | — | 100481,100486,100491 | n | NEEDS_REVIEW | R5_RANKING | FLAGGED_MISS | spec |
| i15.u2 | 1491–1494 | 100484 | — | 100492,100493,100494 | n | MATCH_NOT_FOUND | R6_BEHIND_CURSOR | FLAGGED_MISS | spec |
| i15.u3 | 1511.5–1521.5 | 100491,100492,100493,100494 | 2 | 100499 | n | NEEDS_REVIEW | R4_WEAK_MATCH | FLAGGED_MISS | spec |
| i16.u1 | 1584.5–1588.5 | 100509 | 1 | 100509,100511 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i16.u2 | 1642.5–1646.5 | 100521 | — | 100511 | n | MATCH_NOT_FOUND | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i16.u3 | 1650.5–1653 | 100525,100526 | 2 | 100523,100525,100526 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i17.u1 | 1687.5–1693.5 | 100543,100544,100545,100546 | — | 100547,100553,100554 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i17.u2 | 1703.5–1706 | 100554 | 1 | 100554,100555 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i17.u3 | 1713.5–1719.5 | 100559,100560,100561 | 1 | 100559,100565 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i18.u1 | 1800.5–1805 | 100595 | — | 100589,100591 | n | MATCH_NOT_FOUND | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i18.u2 | 1824.5–1828.5 | 100602 | — | 100598,100608 | n | NEEDS_REVIEW | R2_UNUSABLE | FLAGGED_MISS | spec |
| i18.u3 | 1844.5–1850.5 | 100610,100611 | 4 | 100608,100609,100618 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i18.u4 | — (missing) | — | — | 100619,100621 | — | MATCH_NOT_FOUND | MISSING_OK | — | — |
| i19.u1 | 1931.5–1941;1947.5–1953 | 100646,100648,100649 | 1 | 100646 | y | READY | OK | READY_HIT | spec |
| i19.u2 | 1911.5–1917.5 | 100637 | — | 100654,100655 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i19.u3 | 1975.5–1981.5;1987.5–1995 | 100656,100657,100659,100660,100661 | 1 | 100657 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |
| i20.u1 | 2004.5–2010.5 | 100664,100665 | — | 100689,100690,100692 | n | NEEDS_REVIEW | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i20.u2 | 2050.5–2053 | 100685,100686 | — | 100692 | n | MATCH_NOT_FOUND | R3_NOT_LOOKED | FLAGGED_MISS | spec |
| i20.u3 | 2078.5–2084.5 | 100693 | 2 | 100692,100693 | y | NEEDS_REVIEW | OK | FLAGGED_HIT | spec |


## 5. Recall của bước tạo ứng viên

- R1 = 0: đoạn đúng luôn nằm trong phần nguồn của mục.
- R2_DISCOVERY = 0 ở mọi lần chạy: **60/60** câu có một ứng viên phủ đoạn đúng.
- R2_UNUSABLE = 1 (i18.u2: cảnh đúng 100602 có trong danh sách nhưng bị đánh dấu không dùng được): 59/60 câu có ứng viên dùng được.
- Vậy dò cảnh không phải nút thắt. Phép thử "oracle candidate pool" (§11) thêm 0 câu; sửa bộ lọc chất lượng thêm tối đa 1.

## 6. Độ phủ thu bằng chứng

- Ngân sách vision rc16: 142 khung, như rc12–rc15; 139 khung được tính, 138 câu hỏi gửi đi; 95 khung nhìn rộng, 44 khung có mục tiêu, 0 khung nhìn nhiều.
- Câu trượt vì cảnh đúng chưa bao giờ được nhìn (R3): rc12 24, rc13 23, rc14 28, rc15 25, rc16 26 trên 60 (38–47 %). Đây là tầng lớn nhất ở mọi lần chạy.
- Ngân sách chia ra khoảng 7 khung mỗi mục (142 / 20), trong khi riêng ±15 s quanh một câu đã có khoảng 11 ứng viên (§12).

## 7. Recall xếp hạng

| | rc12 | rc13 | rc14 | rc15 | rc16 |
|---|---|---|---|---|---|
| recall@1 | 17 | 20 | 20 | 21 | 20 |
| recall@3 | 28 | 31 | 24 | 27 | 26 |
| recall@5 | 31 | 33 | 28 | 29 | **31** (51,7 %) |
| cảnh được chọn trúng | 25 | 26 | 23 | 25 | 24 |

rc12–rc15 là số đã lưu (code trước sửa). Replay code sau sửa trên bằng chứng cũ: rc12 32, rc13 33, rc14 28, rc15 30 (recall@5).

## 8. Lỗi phân bổ

- rc16: 3 câu (R6_BEHIND: i7.u2, i11.u2, i15.u2): mọi cảnh đúng kết thúc trước con trỏ của nguồn chỉ đi tới, nên planner không thể dùng chúng; chúng cũng không nằm trong top 5 (hạng —).
- rc12–rc15: 3–4 câu mỗi lần (gồm 1 câu bị câu khác lấy mất ở rc12 và rc13).
- Gỡ hết lỗi phân bổ (giới hạn trên) thêm tối đa 3 câu.

## 9. Lỗi phân loại duyệt

rc16 (sau #802), 60 câu có footage:

| Kết quả | Số câu |
|---|---:|
| READY, trúng | 4 |
| NEEDS_REVIEW, trúng (cảnh báo thừa) | 20 |
| NEEDS_REVIEW, trượt (báo đúng) | 36 |
| READY, trượt (**bỏ sót không báo**) | **0** |

rc15 (trước sửa): READY trúng 5, cảnh báo thừa 20, báo đúng 32, bỏ sót không báo 3 (i1.u1, i1.u2, i16.u1). 4/4 câu không có footage được gắn cờ ở mọi lần chạy, 0 SOURCE_INSUFFICIENT sai. Phân tích ba ca bỏ sót: [ASSISTED_SAFETY_HOTFIX_REPORT.md §2](ASSISTED_SAFETY_HOTFIX_REPORT.md).

## 10. Replay tất định

- Lập kế hoạch lại mọi phần từ nhật ký, kế hoạch và phân tích đã lưu, với cùng câu trả lời vision.
- Code không đổi: **22/22** phần cho đúng kế hoạch đã lưu, ở cả năm lần chạy.
- Code sau #802 trên bằng chứng cũ: 19–20/22 giống; chỉ i1, i9, i16 đổi.
- Kết luận: khác biệt giữa các lần chạy đến từ câu trả lời vision, không từ planner.

## 11. Thử nghiệm oracle (giới hạn trên, không phải chất lượng thật)

| Thử nghiệm | Cách làm | recall@5 | So với thật |
|---|---|---|---|
| Thật rc16 | — | 31 | — |
| Oracle hiểu bằng chứng | thay câu trả lời vision của **các cảnh đã nhìn** bằng nội dung chuẩn | 34 | +3 |
| Oracle thu + hiểu bằng chứng | mọi cảnh đúng có câu trả lời chuẩn | **56** (recall@1 55, chọn trúng 55) | **+25** |
| Thật rc15 | — | 29 | — |
| Oracle hiểu, rc15 | như trên | 34 | +5 |
| Oracle thu + hiểu, rc15 | như trên | 55 (recall@1 54, chọn trúng 54) | +26 |
| Oracle candidate pool | thêm cảnh đúng vào danh sách ứng viên | không chạy riêng: 60/60 câu đã có ứng viên phủ đoạn đúng (§5) | 0 |

- Khi bằng chứng đúng **và có mặt**, xếp hạng và phân bổ tìm được cảnh đúng cho 55–56/60 câu. Vậy matching/ranking không phải nút thắt chính.
- Hiểu tốt hơn các cảnh đã nhìn chỉ thêm 3–5 câu. Phần lớn khoảng trống là các cảnh **chưa được nhìn**.
- Oracle dùng nhãn chỉ ngoài sản xuất, trên bản sao. Không có con số oracle nào là kết quả nghiệm thu.

## 12. Phân tích ngân sách vision

Không chạy thêm ngân sách thật trên tập này (nó sẽ tốn lời gọi AI và chỉnh trên tập phát triển). Thay vào đó, hai phép đo dùng câu trả lời thật đã có:

**Gộp lượt nhìn (proxy ngân sách lớn hơn, không gọi AI mới):** replay một lần chạy như thể nó cũng đã nhìn mọi cảnh mà các lần chạy khác nhìn.

| | Cảnh được nhìn thêm | recall@1/3/5 | Chọn trúng | Bỏ sót không báo |
|---|---|---|---|---|
| rc15 + các lần khác | +71 | 24/32/34 (thật 21/27/29) | 28 (thật 25) | 3 (code trước sửa) |
| rc12 + các lần khác | +69 | —/—/32 (thật 31) | 26 (thật 25) | 0 |
| rc16 + rc12–rc15 | +77 | 24/29/33 (thật 20/26/31) | 28 (thật 24) | 0 |

Đây là số **cảnh** có thêm câu trả lời (proxy), không phải số khung. Mỗi lần chạy có khoảng 155–161 cảnh được nhìn; thêm khoảng 70–77 cảnh (gần +50 %) theo cách rải hiện tại chỉ thêm 1–5 câu vào recall@5. Ngân sách lớn hơn **không** tương xứng nếu vẫn nhìn rải.

**Theo gợi ý của người dùng (thử nghiệm 5):** chỉ xếp hạng các cảnh trong ±N s quanh thời điểm người dùng chỉ.

| Cửa sổ | Cảnh trung bình mỗi cửa sổ | hit@1 | hit@3 |
|---|---|---|---|
| rc16 ±15 s | ~11 | 24 | 32 |
| rc16 ±45 s | ~25 | 24 | 28 |
| rc15 ±15 s | 10,7 | 27 | 31 |
| rc15 ±45 s | 24,6 | 26 | 28 |

Thu hẹp phạm vi tìm giúp xếp hạng một chút (hit@3 32 so với recall@3 26), nhưng không làm hiện các cảnh chưa ai nhìn. Một cửa sổ ±15 s chỉ có khoảng 11 cảnh: nhìn hết chúng tốn khoảng 11 khung mỗi câu được gợi ý, ít hơn nhiều so với nhìn rải toàn phim. Điều này chưa đo bằng lời gọi thật.

## 13. Biến thiên của provider

- Số cảnh được nhìn mỗi lần: rc12 161, rc13 158, rc14 155, rc15 154, rc16 157. Hợp rc12–rc15: 180; thêm rc16: 184. Cảnh được nhìn ở mọi lần chạy: 143.
- Cùng một khung, các lần khác nhau **không bao giờ** cho cùng một câu chữ (0 câu giống hệt). Độ trùng từ trung bình (Jaccard) 0,527.
- Hoán đổi câu trả lời giữa hai lần chạy, giữ nguyên mọi thứ khác (recall@5): rc15←rc12 31, rc15←rc13 33, rc13←rc12 32, rc16←rc15 30.
- recall@5 dao động 28–33 trên năm lần chạy (rc12–rc15 code trước sửa, rc16 code sau sửa). Replay bằng cùng code sau sửa trên năm bộ bằng chứng cũng cho 28–33 (32, 33, 28, 30, 31). Chỉ riêng chữ của vision đã dịch kết quả khoảng ±2–3 câu. Đây là lý do một thay đổi nhỏ trên tập này không thể được gọi là cải thiện nếu không có tập độc lập.
- Biến thiên này đã làm lật trạng thái duyệt ở rc13 và rc15 (#802). Sau #802, lật chữ không còn sinh READY trên cảnh sai ở năm lần chạy.

## 14. Chi phí, CPU, RAM

| | rc15 | rc16 |
|---|---|---|
| Bản nháp W3 (thời gian tới bản nháp đầu) | 721 s | 676 s |
| RAM đỉnh | ~3,5 GB | 3438 MB |
| Khung vision tính / gửi | 142 ngân sách | 139 / 138 |
| GPU | không | không |

- Chi phí AI tỉ lệ với số khung gửi đi (một lời gọi cho mỗi khung không có trong cache). Mỗi lần chạy W3 mới gửi khoảng 138 khung.
- Mọi phép chẩn đoán (§10–§13) chạy offline trên câu trả lời đã có: 0 lời gọi AI, vài giây CPU mỗi phép.

## 15. Khả năng cải thiện

| Nếu sửa được tầng | Giới hạn trên recall@5 (rc16) | Bằng chứng |
|---|---|---|
| hiện tại | 31/60 | |
| R6 phân bổ | +3 → 34 | §8 |
| R5 xếp hạng | +3 → 34 | §4 |
| R4 hiểu bằng chứng | +3 → 34 | oracle hiểu §11 |
| R3 thu bằng chứng (cùng với hiểu đúng) | +25 → 56 | oracle thu + hiểu §11 |
| R2 dò cảnh | +1 | §5 |

Chỉ R3 có đủ dư địa để chạm 85 % (51/60). Các tầng khác cộng lại tối đa 41/60 (68 %).

## 16. Các lựa chọn kỹ thuật

| | A · Dò cảnh tốt hơn | B · Thu bằng chứng tốt hơn | C · Khớp ngữ nghĩa tốt hơn | D · Xếp hạng / phân bổ tốt hơn | E · Model ngữ nghĩa mạnh hơn |
|---|---|---|---|---|---|
| Giải quyết | R2 | R3 (và một phần R4) | R4 | R5, R6 | R4 |
| Tác động dự kiến (W3) | ≤ +1 | lên tới +25 (giới hạn trên) | ≤ +3–5 | ≤ +6 | ≤ +3–5 (như C) |
| Chi phí làm | thấp–vừa | vừa: chọn khung theo lời dẫn và theo cửa sổ thời gian; nhận mốc thời gian người dùng đưa | vừa | thấp–vừa | cao |
| Chi phí AI | 0 | tăng có giới hạn: khoảng +1 khung mỗi câu nếu nhìn theo mục tiêu thay vì rải; ±15 s quanh mốc người dùng ≈ 11 khung | 0 | 0 | model mới, chi phí mới |
| CPU / RAM | nhẹ | trích thêm khung: nhẹ | nhẹ | nhẹ | có thể cần GPU |
| Bảo trì | thấp | vừa (luật phân bổ ngân sách) | vừa (từ điển, chuẩn hoá) | thấp | cao |
| Rủi ro | thấp | chi phí tăng nếu không có trần; phải giữ chia công bằng giữa các phần | quá khớp với một phim | quá khớp với một phim | phụ thuộc, chi phí, hồi quy rộng |
| Dùng lại cho phim tài liệu | có | **có** (phim tài liệu cũng tìm cảnh trong nguồn dài) | có | có | có |
| Dữ liệu nghiệm thu cần | tập độc lập mới | tập độc lập mới ≥ 40 câu, nhãn khoá trước khi chạy, ≥ 2 phim dài khác | như B | như B | như B, cộng đánh giá chi phí |
| Không giải quyết | R3 | R5/R6; biến thiên chữ của vision | R3 | R3 | R3 |

## 17. Khuyến nghị xếp hạng (chi phí / lợi ích)

1. **B · Thu bằng chứng có mục tiêu.** Dùng ngân sách hiện có để nhìn các cảnh trong cửa sổ thời gian của từng câu thay vì rải đều, và cho phép người dùng (chế độ Assisted) đưa mốc thời gian để nhìn hết ±15 s quanh đó. Đây là chỗ duy nhất có đủ dư địa (§15). Theo §21 của chỉ thị, việc này đổi cách phân bổ ngân sách và có thể đổi chi phí, nên cần **issue/ADR riêng** (#815) và một tập độc lập mới trước khi làm.
2. **D · Phân bổ.** Cho phép quay lại trước con trỏ khi cảnh đúng có bằng chứng mạnh và không gây lặp. Rẻ, tối đa +3–6.
3. **C · Chuẩn hoá khớp.** Tiếp nối #802 (số, đơn vị, tên riêng xuyên ngôn ngữ). Rẻ, tối đa +3–5; đồng thời giảm cảnh báo thừa (ví dụ i9.u1).
4. **A · Dò cảnh.** Chỉ 1 câu; không ưu tiên.
5. **E · Model mạnh hơn.** **Không có cơ sở**: oracle cho thấy khi bằng chứng có mặt, matching hiện tại đã tìm được 55–56/60. Không triển khai trong nhiệm vụ này.

Không phương án nào được thử trên tập W3 để chọn người thắng.

## 18. Gate C Feasibility

| Điều kiện | Kết quả |
|---|---|
| 1. Gán lỗi ≥ 90 % ca trượt, kiểm tra được | ✓ 36/36 (100 %), rc16 |
| 2. Số riêng cho dò cảnh, bằng chứng, xếp hạng, phân bổ, duyệt | ✓ §5–§9 |
| 3. Replay tất định | ✓ 22/22, năm lần chạy |
| 4. Ít nhất một oracle | ✓ hai oracle, hai lần chạy |
| 5. Phân tích ngân sách vision | ✓ §12 (proxy bằng câu trả lời thật, có ghi rõ giới hạn) |
| 6. Phân tích bỏ sót không báo | ✓ §9, ba ca rc15 |
| 7. Kết luận nút thắt có bằng chứng | ✓ R3 (72 % ca trượt; oracle +25 so với +3) |
| 8. Khuyến nghị xếp hạng chi phí / lợi ích | ✓ §17 |
| 9. Không chỉnh nhãn đã khoá | ✓ sha256 khớp khoá ở mọi lần chấm |
| 10. Không làm suy giảm quy trình đang dùng | ✓ công cụ chỉ ở `tests/`; không đổi code sản phẩm |

**Gate C Feasibility: PASS.**

## 19. Long-Form Production Gate (Gate B cũ), riêng biệt

| Điều kiện | rc16 |
|---|---|
| recall@5 ≥ 85 % trên tập nghiệm thu **độc lập** | ✗ 31/60 (51,7 %) trên tập phát triển; chưa có tập độc lập |
| 0 bỏ sót không báo nghiêm trọng | ✓ 0/60 |
| 0 SOURCE_INSUFFICIENT sai nghiêm trọng | ✓ 0 |
| Timeline / QC đạt | một phần: HELD_FRAME 0 ở 22/22 kế hoạch, timeline ổn; Kiểm tra cuối REVIEW_PENDING (còn câu chưa duyệt) |
| Windows E2E đạt | một phần: bản nháp W3 chạy trên DubFlow.exe rc16; chưa chạy nghiệm thu qua UI và xuất bản cuối của W3 |
| Chi phí trong giới hạn | ✓ 142 khung, 676 s, 3438 MB |

**Long-Form Production Gate B: FAIL.** Feasibility PASS không có nghĩa là sẵn sàng sản xuất.
