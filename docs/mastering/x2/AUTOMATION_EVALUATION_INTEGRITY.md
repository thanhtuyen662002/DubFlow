# Tính toàn vẹn của phép đo tự động hóa (X0)

Epic #637 · 2026-11-14 · chỉ thị "DUBFLOW — SOURCE-GROUNDED AUTOMATION FEASIBILITY" · main `cf0c2a9` · dữ liệu: lần chạy W3 của **1.1.96-rc16** (bản sao chỉ đọc, đã kiểm sha256 của ground truth khớp lock).

Báo cáo này **chỉ kiểm tra cách đo**. Không sửa planner, scoring, READY policy hay nhãn. Mọi con số đều có nhãn nguồn: **measured** (đo trên file đã lưu), **replay** (lập kế hoạch lại offline bằng code hiện tại), **oracle** (dùng ground truth bên trong phép thử), **AI-judged** (Claude phân loại, chưa có người kiểm), **heuristic** (luật từ khóa).

Báo cáo không chứa câu narration, câu trả lời Vision, khoảng thời gian GT hay khung hình. Các đơn vị chỉ được gọi bằng mã (`i2.u1`…).

## 1. Kết luận ngắn

| Câu hỏi | Kết luận | Nhãn |
|---|---|---|
| Kết quả oracle "acq+interp 56/60" có đo năng lực không? | **Không.** Oracle đặt nguyên văn câu narration vào làm câu trả lời Vision (`w3.py:483-506`), vì vậy nó là cận trên của bước *chấm điểm khi đã có chữ hoàn hảo*. Nó không phải cận trên của Vision. | measured (đọc code) |
| W3 có đại diện cho narration của sản phẩm không? | **Không.** 60/60 câu có footage là chú thích hình, được viết cho phép thử (rc8 `metrics.json`). Không có câu bình luận, cảm xúc hay khái quát nào (ACCEPTABLE_ILLUSTRATIVE_BROLL = 0). | AI-judged + heuristic |
| Mẫu số có nhất quán không? | Có, nếu ghi đủ: 64 câu = 60 có footage + 4 "missing". READY 4/64. recall@k và chosen_hit tính trên 60. | measured |
| Thước đo "chosen_hit" có quá dễ dãi không? | **Có.** Chỉ cần một mảnh trong câu trúng là tính đúng. Ví dụ `i1.u2` là READY nhưng chỉ 13,8 % thời lượng trúng; phần còn lại là một thẻ chữ khác. | measured |
| rc12–rc16 có phải mẫu độc lập không? | **Không.** Cùng phim, cùng 64 câu, cùng GT, code khác nhau. Chỉ dùng để xem độ ổn định, không được cộng thành n lớn hơn. | — |

## 2. Kiểm tra benchmark W3

### 2.1 Nguồn gốc dữ liệu (measured)

- Phim: *Why We Fight 5a — The Battle of Russia, Part 1*, khoảng 35,8 phút, sha256 `834cf4f3…b3c32b`. Phim là tư liệu công cộng; bản trên máy kiểm thử là dữ liệu riêng của máy.
- Cách gắn nhãn (trường `method` trong GT): lấy một khung hình mỗi 2 s trong từng cửa sổ 105 s, xếp thành contact sheet, rồi đọc khoảng thời gian từ sheet. **Độ phân giải 2 s.**
- Khóa GT: sha256 `afa979d2…f66497`, khóa lúc 2026-11-12T09:23:33Z, trước khi tạo chương trình. Đã kiểm lại khớp lock.
- Narration: rc8 `metrics.json` ghi rõ narration được viết cho phép thử. Câu được viết ra từ chính các contact sheet dùng để gắn nhãn, nên người viết narration và người gắn nhãn dùng chung một nguồn nhìn. Đây là **rò rỉ từ vựng hình ảnh**: câu dùng đúng những từ mô tả khung hình.

### 2.2 Cấu trúc 64 câu (measured)

| | Số câu |
|---|---|
| Câu có footage (`kind: footage`) | 60 |
| Câu "missing" (không có cảnh phù hợp) | 4 |
| Câu có 2 khoảng GT | 4 |
| Phần (item) | 20, mỗi phần 1 nguồn, cắt 105 s |

Lưu ý: mỗi phần chỉ là một cửa sổ 105 s. Phép thử **không đo truy hồi trên cả phim dài**. Nó đo việc chọn cảnh trong khoảng 105 s mà người dùng đã cắt sẵn. Cụm từ "long-form retrieval" trong các báo cáo trước cần được hiểu theo phạm vi này.

### 2.3 Phân loại theo taxonomy của chỉ thị

Phân loại **AI-judged + heuristic** (luật từ khóa trên câu: chữ, trích, bản đồ, báo, số, tên…, sau đó Claude đọc lại). Chưa có người kiểm.

| Lớp | Số câu | chosen_hit | READY |
|---|---|---|---|
| DIRECT_VISUAL_MATCH (mô tả một cảnh cụ thể, không cần đọc chữ) | 26 | 10 | 1 |
| FACTUAL_CLAIM_REQUIRING_EVIDENCE (tên, số, ngày, câu trích, nhãn bản đồ, tít báo phải hiện trên hình) | 34 | 14 | 3 |
| ACCEPTABLE_ILLUSTRATIVE_BROLL (câu bình luận; cảnh minh họa chấp nhận được) | **0** | — | — |
| SEMANTIC_MISMATCH | không áp dụng cho câu (đây là nhãn của cặp câu–cảnh, xem §2.4) | | |
| NO_SUITABLE_FOOTAGE | 4 | — | 0 |
| UNKNOWN/AMBIGUOUS | 0 câu; nhưng ít nhất 3 cặp câu–cảnh có GT không chắc (§4) | | |

Hệ quả:

1. **W3 thiếu hẳn lớp quan trọng nhất của sản phẩm.** Narration Tổng hợp thật chủ yếu là bình luận và kể chuyện, nên phần lớn thuộc ACCEPTABLE_ILLUSTRATIVE_BROLL. W3 có 0 câu loại này. Kết quả W3 không cho biết DubFlow xử lý loại câu đó ra sao.
2. **Hơn một nửa W3 (34/60) phụ thuộc chữ trên hình.** DubFlow không có OCR nhận dạng (chỉ có bộ phát hiện PP-OCRv4). Vì vậy kết quả của lớp này đo khả năng Vision AI đọc chữ, không đo retrieval.
3. **4 câu "missing" quá dễ.** Cả 4 đều là chủ đề lạ hẳn với phim chiến tranh đen trắng (voi, trực thăng, hàng dừa, thả diều). Không có câu "gần đúng" nào, ví dụ một trận đánh khác hay một thành phố khác. Kết quả 4/4 được gắn cờ vì thế không chứng minh hệ thống từ chối được cảnh gần đúng.

### 2.4 Lỗi ngữ nghĩa thật trong kết quả (measured, AI-judged)

Có những lỗi nằm ngay trong cặp câu–cảnh mà hệ thống đã chọn, chứ không ở GT. Ví dụ: `i10.u3` chọn cảnh kỵ binh diễu hành cho câu về bộ binh đội mũ sắt; `i14.u2` chọn bản đồ có tên thành phố cho câu trích. Đây là SEMANTIC_MISMATCH thật, có độ tin cậy 576 và 476 (§3 trong báo cáo X3).

## 3. Kiểm tra oracle

| Oracle | Hàm | Dùng GT? | Nó thực sự đo gì | Được trình bày như năng lực? |
|---|---|---|---|---|
| Evidence oracle (`only_looked`) | `oracle_evidence(..., only_looked=True)` | Có | Planner/scorer làm gì khi cảnh đúng **đã được nhìn** có câu Vision trùng nguyên văn câu narration (độ tin cậy 900). Đây là cận trên của *diễn giải với chữ hoàn hảo*. | Phải ghi **oracle, vòng tròn** |
| Evidence oracle (acq+interp) | `oracle_evidence(..., only_looked=False)` | Có | Như trên, nhưng cho **mọi** cảnh đúng theo GT, kể cả cảnh chưa nhìn. Con số 56/60 nói rằng nếu biết sẵn đáp án và viết đáp án bằng đúng chữ của câu hỏi thì scorer tìm ra nó. **Không nói gì về Vision hay retrieval.** | Đã bị hiểu như cận trên khả thi. Cần sửa cách gọi |
| Ranking/timestamp oracle | `hint(gt, run, window_s)` | Có (lấy điểm giữa GT làm gợi ý) | Người dùng **mô phỏng** chỉ đúng vị trí ±window. Chỉ là cận trên của thao tác gợi ý; không có người thật. | Đã ghi SIM/ORACLE trong nhánh #832 (đã đóng) |
| Donor oracle | `swap`, `pooled` | Không | Câu trả lời Vision thật của **lần chạy khác cùng phim** (rc12–15). Đây là proxy cho tăng ngân sách Vision. Không vòng tròn, nhưng các lần chạy không độc lập và cache có thể trùng. | Được, nếu ghi "donor, không độc lập" |
| Attribution | `attribute` | Có | Chẩn đoán từng câu lỗi ở giai đoạn nào (R1…R7). Chỉ để chẩn đoán. | Đúng cách dùng |
| Candidate oracle | (không có hàm riêng) | — | — | — |

**Quy tắc mới đề xuất:** mọi oracle dùng chữ của câu narration làm bằng chứng phải được gọi là *oracle vòng tròn*. Không được dùng nó để ước tính lợi ích của tăng ngân sách Vision. Một oracle Vision trung thực phải dùng *mô tả độc lập với câu*, ví dụ câu trả lời Vision thật lấy từ lần chạy khác (donor), hoặc mô tả do người viết **trước** khi thấy narration.

## 4. Độ chắc của GT (AI-judged, cần người kiểm)

- Độ phân giải 2 s, cộng luật "chồng ≥ 1000 ms hoặc ≥ nửa đoạn ngắn hơn", làm cho một cảnh cạnh bên rất dễ được tính là trúng.
- `i14.u1`: một mảnh được tính trúng GT, nhưng chữ thành phố trên cảnh đó không phải một trong bốn thành phố câu nêu. GT đặt khoảng bao trùm cả chuỗi tên.
- `i20.u3`: cảnh bị tính trượt có mô tả Vision rất gần câu (mô hình sa bàn thành phố). Có thể GT bỏ sót cảnh này.
- `i19.u3`: cảnh được tính trúng chỉ có một người đội mũ, không thấy hành động trao súng ở khung giữa.

Ước lượng: khoảng 3/60 cặp có GT đáng ngờ, tức khoảng 5 %. Con số này **ngang tầm** với các khác biệt đang được dùng để ra quyết định, ví dụ READY 4 so với 5.

## 5. Hợp đồng đo (đề xuất, áp dụng từ X3 trở đi)

1. **Mẫu số luôn ghi rõ.** `units_scored` (64) = `footage` (60) + `missing` (4). recall@k, chosen_hit, flagged_* tính trên 60. ready_rate tính trên 64. Mọi tỉ lệ ghi dạng `k/n`.
2. **Hai mức đúng.** *Lenient* = một mảnh trong câu trúng GT (luật đã khóa của W3). *Strict* = mọi mảnh trúng. Thêm *tỉ lệ thời lượng trúng*. READY phải báo cả hai mức. READY mà có trên 50 % thời lượng sai được tính là lỗi nghiêm trọng.
3. **Lỗi nghiêm trọng** (severe escape): READY trên câu "missing"; READY mà không mảnh nào trúng; READY có claim (tên/số) không có trên hình.
4. **Độ bất định:** khoảng tin cậy Clopper-Pearson 95 % cho mọi tỉ lệ. Với 0 lỗi, cần ít nhất **59** lần chấp nhận đúng liên tiếp để cận dưới ≥ 95 %, và **299** lần để cận dưới ≥ 99 %.
5. **Không cộng các lần chạy cùng bộ dữ liệu.** rc12–16 là các lần chạy lại của cùng 64 câu. Chỉ được dùng để xem độ ổn định.
6. **Nhãn kết quả bắt buộc:** measured / replay / simulated / oracle / donor / exploratory / dev / held-out / human / AI-judged / unknown.
7. **Nhiều cảnh cùng chấp nhận được:** nhãn holdout ghi *mọi* khoảng chấp nhận được (trực tiếp hoặc minh họa). Một mảnh trúng bất kỳ khoảng nào được tính là chấp nhận được, nhưng chỉ khoảng DIRECT mới xác minh được factual claim.
8. **Không có footage:** đúng khi câu không READY *và* trạng thái báo thiếu (MATCH_NOT_FOUND/SOURCE_INSUFFICIENT) hoặc có cờ rõ. Lỗi khi câu READY hoặc khi cảnh được chọn mà không kèm cờ.
9. **Retrieval khác final selection:** recall@k đo *danh sách* ứng viên; chosen_hit đo *lựa chọn*; READY đo *quyết định tự chấp nhận*. Ba số này không được thay thế cho nhau.
10. **Human intervention rate** = số câu cần người mở ra xem / tổng số câu, ước tính từ trạng thái review đã lưu. Thời gian người dùng chỉ được báo khi **đo trên người thật**; mọi ước tính khác phải ghi "ước tính".
11. **Chất lượng timeline:** dùng Final QC hiện có (timeline, duration, captions, audio, decisions, claims). Câu READY trong một video FINAL_QC_FAILED vẫn là chưa xong.

## 5b. Các lần chạy dùng chung dữ liệu và heuristic chọn sau khi xem kết quả

- W3 (cùng phim, cùng 64 câu, cùng GT) được chạy ở rc11 (tạo contact sheet), rc12, rc13, rc14, rc15, rc16 và các lần replay sau đó (main16, #831 baseline A–D). Tất cả là **một** bộ dữ liệu.
- Có ít nhất một thay đổi heuristic được làm **sau khi xem kết quả W3**: các sửa lỗi của #802/#805/#817 đều xuất phát từ các ca lỗi trong W3 hoặc Gate S. Ví dụ replay ở main cho READY 5 thay vì 4 vì #817. Vì vậy W3 đã là tập phát triển, không còn là tập kiểm tra.
- Gate S REAL-DEV cũng đã được xem kết quả và dùng để chỉnh, nên không thể làm holdout.

## 5c. Diễn giải lại các kết luận cũ

| Kết luận cũ | Diễn giải đúng |
|---|---|
| "Oracle acq+interp 56/60: chỉ cần Vision nhìn đúng cảnh là tìm được" | Cận trên vòng tròn của scorer khi đáp án được viết bằng chữ của câu. Không phải cận trên của Vision. **Không** chứng minh tăng ngân sách Vision sẽ đạt 85 %. |
| "R3 (cảnh đúng chưa được nhìn) là nút thắt chính, 26/36" | Vẫn đúng như một **chẩn đoán đếm** (measured). Nhưng việc nhìn thêm sẽ giúp bao nhiêu thì chưa được đo trung thực; chỉ có proxy donor. |
| "recall@5 31/60, Gate B FAIL" | Vẫn có giá trị như số đo dev, với hai giới hạn: chỉ trong cửa sổ 105 s và narration là chú thích hình. |
| "READY precision 4/4, 0 serious silent miss" | Đúng theo luật lenient. Theo luật strict là 3/4, và CI 95 % [0,40; 1,00] quá rộng để kết luận về độ an toàn. |
| "Human-guided timestamp" (#832) | Kết quả SIM/ORACLE, đã ghi INCONCLUSIVE. Không dùng như bằng chứng. |

## 5d. Điều gì vẫn có giá trị

- GT được khóa trước khi chạy, có sha256 và có thể kiểm tra lại.
- Bộ công cụ `w3.py` (score, replay, attribute, donor) cho phép chạy lại offline mà không tốn tiền. Replay khớp 21/22 phần, phần lệch được giải thích bởi #817.
- Trạng thái review an toàn trên W3: 4/4 câu missing được gắn cờ, 0 câu READY không trúng mảnh nào.
- Chẩn đoán theo giai đoạn (R1…R7) vẫn dùng được để định hướng.

## 6. REAL-DEV và REAL-HOLDOUT

> **Ghi chú 2026-11-15 (Owner duyệt):** Bảng dưới là định nghĩa của lần rà soát X0. Ở đó, W3 (64 câu, rc12–16) được gọi là REAL-DEV, nhưng W3 chỉ là **development dataset cũ**: đã bị nhìn và đã dùng để chọn luật. Với X2, REAL-DEV và REAL-HOLDOUT theo `X2_EVALUATION_DATASET_PROTOCOL.md`: nguồn mới, có quyền được xác nhận, không chứa W3 hay Gate S REAL-DEV. Thứ tự DEV → khóa tham số → HOLDOUT và mọi quy tắc khóa, chống rò rỉ bên dưới giữ nguyên.

| Tập | Định nghĩa | Dùng để |
|---|---|---|
| **REAL-DEV** | W3 hiện có (64 câu) cùng các lần chạy rc12–16. Đã bị nhìn nhiều lần, đã dùng để chọn luật. | Phát triển, chẩn đoán, chọn luật. **Không** dùng để công bố độ chính xác. |
| **REAL-HOLDOUT** | Bộ mới, chưa ai trong dự án nhìn: ≥ 2 nguồn dài khác W3 (khác thể loại, có ít nhất một nguồn có thoại); ≥ 100 câu viết theo **đường sản phẩm** (narration Tổng hợp thật, có bình luận); ≥ 20 % câu không có footage, trong đó một nửa là câu "gần đúng"; nhãn do **người** gắn và kiểm chéo bởi người thứ hai. | Đo cuối cùng, chỉ một lần cho mỗi luật đã chốt. |

## 7. Giao thức khóa nhãn và chống rò rỉ

1. Người viết narration **không** được xem contact sheet hay khung hình trước khi viết. Họ viết từ tiêu đề hoặc nội dung chủ đề, giống người dùng thật.
2. Người gắn nhãn gắn khoảng thời gian **sau khi** narration đã khóa. Họ không thấy kết quả của bất kỳ lần chạy nào.
3. Nhãn có ba trạng thái: trúng, chấp nhận được (minh họa), sai. Kèm ghi chú cho UNKNOWN.
4. Khóa sha256 của narration và nhãn trước lần chạy đầu tiên. Luật READY phải được chốt và commit **trước khi** mở holdout.
5. Code đánh giá không được đọc nhãn holdout ngoài bước chấm. Có test bảo đảm hàm planner và replay không nhận đối số GT, trừ các hàm oracle có tên rõ ràng.
6. Không đưa narration, nhãn, khung hình hay câu trả lời Vision của máy kiểm thử vào repo công khai. Repo chỉ giữ code, các con số tổng hợp và mã đơn vị.
7. Test "không gọi ra ngoài": chạy harness với proxy chết (`HTTP(S)_PROXY=127.0.0.1:9`). Thêm một test đơn vị chặn `socket.connect`.

## 8. Kết quả X0

**X0 PASS** theo tiêu chí của chỉ thị:

- Phát hiện oracle đã được kiểm chứng trong code, và phạm vi ảnh hưởng đã được ghi (§3, §5c).
- Có bảng phân loại giới hạn W3 (§2.3).
- Có hợp đồng đo với mẫu số rõ ràng (§5).
- Có định nghĩa REAL-DEV/REAL-HOLDOUT (§6).
- Có giao thức khóa nhãn và chống rò rỉ (§7).
- Không sửa nhãn nào.

X0 PASS chỉ có nghĩa là **cách đo đã được làm rõ**. Nó không thay đổi bất kỳ gate nào.

## 9. Giới hạn của báo cáo này

- Phân loại taxonomy là AI-judged và heuristic, chưa có người kiểm.
- Không xem lại khung hình, chỉ đọc mô tả Vision đã lưu. Nghi vấn về GT ở §4 cần người xem.
- Chỉ dùng một bộ dữ liệu (W3) và một phim.

## 10. Đính chính (2026-11-14)

§5 mục 2 cần được hiểu là: tỉ lệ thời lượng tính trên **các đoạn đặt trên timeline** (`pieces`: offset, length), không tính trên độ dài cả ứng viên. Báo cáo X3 bản đầu chưa áp dụng đúng điều này và chưa áp dụng định nghĩa lỗi nghiêm trọng ở §5 mục 3. X3 §7 đã đính chính. Đề xuất regression test cho hợp đồng đo nằm ở `SAFETY_TRIAGE_X2_READINESS.md`.
