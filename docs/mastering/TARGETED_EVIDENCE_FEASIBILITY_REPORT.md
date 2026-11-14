# Thu bằng chứng có mục tiêu cho phim dài: báo cáo khả thi (Stage 2, #815)

Epic #637 · 2026-11-14 · chỉ thị "Release Safety Closure & Targeted Evidence Acquisition ADR" (Stage 2, §14–§26, §24.B) · PR #831 (thay #830; task #829, thiết kế #815) · harness `tests/mastering/longform/targeted.py` tại `716e984` (lần chạy của quyết định được ghi nhận); lần tính lại theo khoá chính xác tại `468cb05`; sửa phân loại tại `7d90f35`; mã cuối cùng và lần chạy lại cuối tại `4607031`; các commit sau đó chỉ sửa tài liệu (xem §4.4) · pre-registration tại commit `c60ab02` (tổ tiên của `716e984`).

Báo cáo này chỉ gồm **thí nghiệm offline có giới hạn** và một ADR đề xuất. Không có thay đổi nào ở planner, engine, app, contract hay giá trị mặc định của sản phẩm. Harness không gọi AI, không dùng model mới, embedding, GPU hay retrieval engine mới. Không có nhãn nào bị sửa.

## Trạng thái số liệu E3–E5 (2026-11-14)

Các số E3–E5 trong báo cáo này đều lấy từ lần chạy `716e984`: recall từ câu trả lời đã lưu, độ phủ cache và chênh lệch quy nguyên E5. Lần chạy đó ghép câu trả lời của lần chạy cho bằng byte JPEG cộng nhà cung cấp và model ở mức lần chạy, **chưa** kiểm khoá cache đầy đủ của production. Khoá đầy đủ gồm phiên bản prompt, nhà cung cấp, model, bộ nhãn và byte JPEG, và câu trả lời đã ghi phải khớp bản ghi cache.

Rà soát của Lead đã chỉ ra thiếu sót này. Harness nay chỉ nhận câu trả lời khi khoá chính xác được chứng minh (`a558396`, PR #831).

**Tính lại theo khoá chính xác (exploratory, 2026-11-14).** E3–E5 đã được tính lại trên máy của chủ dự án từ đúng năm lần chạy đã lưu, tại `a558396`, `b17bb53` và, sau lần rà soát thứ hai của Lead, tại `468cb05`. Quy tắc cuối cùng chỉ lấy khoá cache cũ từ cặp nhà cung cấp và model mà spec của lần chạy yêu cầu, hoặc từ `made_by` của chính bản ghi. Báo cáo của lần chạy ghi model đã trả lời nên không bao giờ được dùng làm khoá. Cả ba lần đều mang nhãn PREREGISTRATION MISMATCH (exploratory) theo thiết kế, và lần chạy A, B giống nhau từng byte.

- rc16 là lần chạy chính và là nguồn của mọi số E3–E5 trong báo cáo này. Khoá của rc16 được chứng minh bằng `made_by`, và phần rc16 không đổi dưới quy tắc cuối. So với `716e984` chỉ có `open:A+` ở 4x mô phỏng được 5/160 khung (trước là 6/160), và nhánh `A+` closed world cùng trần mô phỏng còn 19 khung (trước là 20). Không có giá trị recall, fixed, broken, silent miss, READY hay cảnh báo nào thay đổi.
- rc12–rc15 là các lần chạy hỗ trợ. Spec của chúng không ghi model và bản ghi cache không có `made_by`, nên không chứng minh được khoá nào. Chúng không mô phỏng được khung nào, và số E3–E5 của chúng trở về baseline của chính lần chạy. Chúng chỉ phục vụ quyết định E2, vốn dựa trên việc chọn khung và giống hệt ở mọi head.

**Sửa phân loại sau lần tính lại (`7d90f35`).** Lần rà soát tiếp của Lead chỉ ra rằng việc phân loại khung (khung nào tính là nhìn có mục tiêu, và vì vậy A0 bỏ câu trả lời nào) vẫn coi việc có khoá là bằng chứng. Tại `7d90f35`, phân loại cần cùng bằng chứng câu trả lời như phần ghép lần chạy cho: một khung chỉ là targeted khi bản ghi của chính nó dưới khoá đã chứng minh là câu trả lời nó đã ghi và không có khung broad hay khung trước nào giữ cùng byte JPEG; nếu không thì là shared (một lần nhìn đã chứng minh trong nhóm cho câu trả lời đó) hoặc unproven (A0 giữ lại). A0 cũng xoá hộp sản phẩm, hook và nhóm sản phẩm của các câu trả lời nó bỏ, và câu trả lời phát lại đặt các trường này từ bản ghi như production. Lần chạy lại trên máy tại `7d90f35` (exploratory, PREREGISTRATION MISMATCH, chạy A và B giống từng byte) không làm đổi nhánh nào ở lần chạy nào: mọi giá trị E2, E3, điểm, chênh lệch và E5 bằng lần tính lại `468cb05`, và điểm A cùng quy nguyên không đổi. Chỉ phân loại của rc16 thay đổi: 10 khung có cùng byte JPEG với một khung có mục tiêu trước đó nay là shared (targeted 43, shared 14, trước là 53 và 4; khung có mục tiêu trong các phần mục 31, trước là 40), không có khung unproven, và mẫu early bias còn 35 câu (trước là 39). 

**Đóng an toàn khi không có khoá (`4607031`, head mã cuối).** Khi không có một khoá duy nhất được chứng minh bằng câu trả lời (không có cache, không chứng minh được, hoặc nhiều hơn một), harness không còn suy ra lượt nhìn có mục tiêu hay bản sao từ nhóm: mọi câu trả lời ngoài lượt broad tái dựng là unproven và A0 giữ lại. Lần chạy lại trên máy tại `4607031` (exploratory, A = B) giữ nguyên bảng quyết định, toàn bộ rc16 (151 nhánh, phân loại và phần markdown) và các nhánh chính, đối chứng ở mọi lần chạy. Ở rc12–rc15 (không có khoá chứng minh được) mọi câu trả lời ngoài broad nay là unproven (59–63 mỗi lần chạy, 0 targeted), nên 12 nhánh B0/respend của mỗi lần chạy trở về baseline của chính nó (fixed/broken 0/0, r@5 bằng A); không nhánh nào khác đổi. Điểm A và quy nguyên không đổi ở cả năm lần chạy. Vì vậy các số E3–E5 của rc16 trong báo cáo này đúng ở head mã cuối. Chúng vốn chỉ là mô phỏng một phần, và khuyến nghị không dựa vào chúng.

Quyết định E2 đã đăng ký trước ("not supported") không dùng câu trả lời của lần chạy cho, nên vẫn là quyết định được ghi nhận tại `716e984`.

## Kết luận

- **Quyết định đã đăng ký trước: "not supported".** Các tín hiệu định vị theo kịch bản và thời gian (không dùng nhãn) **không** chỉ ra cảnh đúng tốt hơn luật ưu tiên hiện có hay đối chứng xáo trộn một cách có ý nghĩa thống kê hoặc nhất quán, và không vượt mức ngẫu nhiên. Trên rc16, nhánh chính chọn được khung trúng cho 2 câu R3, so với 1 của P+ và 0 của shuffled_prior; kỳ vọng ngẫu nhiên là 2,95; p = 1,0 so với P+ (§5.2).
- **Khuyến nghị: không triển khai Option B của #815 (thu bằng chứng theo kịch bản/thời gian) vào production lúc này** (§14).
- **Stage 2 Feasibility: PASS với tư cách một nghiên cứu**: đủ 12 mục §21 (§15). Kết quả thực chất của nghiên cứu là "định vị không được ủng hộ, không triển khai".
- **ADR-0057: PROPOSED**, chưa được duyệt.
- **GENERALIZATION NOT VERIFIED**: W3 là tập phát triển (REAL-DEV), không phải tập độc lập (§11).
- **Long-Form Production Gate B: FAIL**, không đổi. Báo cáo này không tuyên bố Gate B, không tuyên bố sẵn sàng sản xuất.

## 1. Baseline truy hồi hiện tại

E1 chạy offline trên PC Windows bằng `tests/mastering/longform/w3.py` ở main `cf0c2a9`, trước mọi thí nghiệm mới, trên năm lần chạy W3 đã lưu. Nhãn sha256 `afa979d2…f66497` khớp khoá. Đây là baseline, không phải kết quả mới.

| Lần chạy | recall@1 | recall@3 | recall@5 | Cảnh chọn trúng | Bỏ sót không báo (trạng thái đã lưu) | READY precision | Câu trả lời vision (ngân sách 142) |
|---|---:|---:|---:|---:|---:|---|---:|
| rc12 | 17 | 28 | 31 | 25 | 0 | 5/5 | 141 |
| rc13 | 20 | 31 | 33 | 26 | 2 | 5/7 | 139 |
| rc14 | 20 | 24 | 28 | 23 | 0 | 6/6 | 139 |
| rc15 | 21 | 27 | 29 | 25 | 3 | 5/8 | 139 |
| rc16 | 20 | 26 | **31** | 24 | 0 | 4/4 | 138 |

- Mẫu số là 60 câu có footage. Bỏ sót không báo ở rc13 (2) và rc15 (3) thuộc trạng thái đã lưu trước #802; replay bằng code sau #802 đưa cả hai về 0.
- Nhóm duyệt rc16 (trạng thái đã lưu): READY_HIT 4, FLAGGED_HIT 20, FLAGGED_MISS 36, SILENT_MISS 0.
- Độ phủ của bước tạo ứng viên: R1 = 0 và R2_DISCOVERY = 0 ở mọi lần chạy, nên 60/60 câu có ứng viên phủ đoạn đúng; 59/60 có ứng viên dùng được (i18.u2 là R2_UNUSABLE). Dò cảnh không phải nút thắt.
- **Baseline A của harness** (rc16, replay bỏ các chỉnh sửa duyệt): recall@1/3/5 20/26/31, chọn trúng 24, READY precision 5/5 (ready_rate 5/64), bỏ sót không báo nghiêm trọng 0, footage không ghép được 0/60. Nhóm duyệt của A: READY_HIT 5, FLAGGED_HIT 19, FLAGGED_MISS 36. A khác trạng thái đã lưu ở READY (5 so với 4) vì harness bỏ các ghim, loại trừ và duyệt mà người dùng làm sau khi đã xem kết quả; các chỉnh sửa đó chứa một phần thông tin nhãn. Mọi nhánh ở §5 được so với A.

Oracle trên từng lần chạy (giới hạn trên, dùng nhãn; recall@5):

| Lần chạy | Đã lưu | Replay | ORACLE hiểu bằng chứng (chỉ cảnh đã nhìn) | ORACLE thu + hiểu (mọi cảnh đúng) |
|---|---:|---:|---:|---:|
| rc12 | 31 | 32 | 37 | 56 |
| rc13 | 33 | 33 | 33 | 56 |
| rc14 | 28 | 28 | 33 | 56 |
| rc15 | 29 | 30 | 34 | 55 |
| rc16 | 31 | 31 | 34 | 56 |

## 2. Các tầng thất bại

Mỗi câu trượt được gán tầng sớm nhất làm mất footage của nó (luật của `w3.attribute`, giữ nguyên từ [LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md](LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md) §3):

| Tầng | Ý nghĩa |
|---|---|
| R2 Dò cảnh | không ứng viên nào phủ đoạn đúng (R2_DISCOVERY), hoặc có nhưng bị đánh dấu không dùng được (R2_UNUSABLE) |
| R3 Thu bằng chứng | cảnh đúng có trong danh sách ứng viên nhưng **AI chưa nhìn thấy** nó |
| R4 Hiểu bằng chứng | AI **đã nhìn** cảnh đúng nhưng câu trả lời không chung từ với lời dẫn (R4 không chung từ) hoặc khớp yếu (R4 yếu) |
| R5 Xếp hạng | đã nhìn, khớp đủ mạnh, nhưng **không được chọn** |
| R6 Phân bổ | cảnh đúng bị con trỏ đi tới chặn (R6 sau con trỏ) hoặc câu khác đã lấy (R6 bị lấy) |

Như chỉ thị §15 yêu cầu, báo cáo tách **"chưa nhìn thấy cảnh đúng"** (R3) khỏi **"đã nhìn nhưng không hiểu hoặc không chọn"** (R4, R5).

| Tầng | rc12 | rc13 | rc14 | rc15 | rc16 |
|---|---:|---:|---:|---:|---:|
| OK | 25 | 26 | 23 | 25 | 24 |
| R3 chưa nhìn | 24 | 23 | 28 | 25 | **26** |
| R5 xếp hạng | 3 | 3 | 2 | 5 | 3 |
| R4 yếu | 3 | 2 | 2 | 0 | 2 |
| R4 không chung từ | 1 | 1 | 1 | 1 | 1 |
| R6 sau con trỏ | 2 | 3 | 3 | 3 | 3 |
| R6 bị lấy | 1 | 1 | 0 | 0 | 0 |
| R2 | 1 | 1 | 1 | 1 | 1 |

- R3 là tầng lớn nhất ở mọi lần chạy (23–28 câu). Ở rc16, 26 trong 36 câu trượt là R3; chỉ 6 câu trượt sau khi AI đã nhìn cảnh đúng (R4 3, R5 3), và 3 câu là R6.
- Kiểm toán code (§3) thêm ba điểm làm rõ R3. Phần nhìn có mục tiêu hiện tại chỉ có 2 khung mỗi phần ở W3 (`(142 − 95) // 22`). Nó lệch về đầu cửa sổ: trong 39 lượt nhìn có mục tiêu ở rc16, 20 lượt rơi vào một phần tư đầu của cửa sổ đi tới của câu, trung vị ở 0,201 chiều dài cửa sổ (tứ phân vị 0,040 và 0,642). Và nhật ký không ghi lý do từng ứng viên chưa được nhìn (chưa được chọn, bị cắt vì hết ngân sách, gọi lỗi, hay không dùng được).
- Hai cách đo "đã nhìn" cho kết quả khác nhau một chút (§6), nên có thể có ca cảnh đúng đã được nhìn nhưng khung gửi cho AI nằm ngoài đoạn đúng. Công cụ hiện tại tính ca đó là R4, trong khi thực chất AI chưa thấy khoảnh khắc đúng.

## 3. Đề xuất kiến trúc

Thiết kế đầy đủ nằm ở [ADR-0057: Targeted Evidence Acquisition for Long-Form Assisted Editorial Retrieval](../adr/ADR-0057-targeted-evidence-acquisition.md) (22 mục theo chỉ thị §20, trạng thái **PROPOSED**). ADR-0056 vẫn giữ cho preset tường thuật phim tài liệu. Tóm tắt bốn lượt:

| Lượt | Vai trò | Trạng thái trong Stage 2 |
|---|---|---|
| Pass 1 Phủ rộng | nhìn rải đều theo thời gian ở mức nền, giữ đa dạng cảnh, giữ ánh xạ nguồn/thời gian, dùng lại cache; không gửi mọi khung cho AI | đã có trong production (lượt broad: thứ tự van der Corput, chia vòng theo phần, `budget − budget//3` = 95 khung ở W3); dùng nguyên làm nền |
| Pass 2 Định vị theo kịch bản | ưu tiên vùng có tín hiệu lúc lập kế hoạch: trim của mục, thứ tự và vị trí câu (prior), cảnh đã nhìn có điểm Scorer cao nhất (anchor), khoảng chưa nhìn (gap), phụ đề nguồn nếu có. Chỉ giúp định vị, không tự chứng minh cảnh đúng | **đã thử offline** (E2); không tín hiệu nào được ủng hộ (§5.2) |
| Pass 3 Nhìn có mục tiêu | nhìn bổ sung trong cửa sổ quanh tâm được chọn; ±W là giả thuyết, không phải giá trị đã nghiệm thu | thử W = 5, 10, 15, 30 s và cả cửa sổ đi tới; **chưa gọi vision thật**; chỉ phát lại câu trả lời đã lưu (E3) |
| Pass 4 Xếp hạng và kiểm tra | dùng lại matching, ranking và allocation hiện có; kiểm tra phạm vi nguồn, recall ứng viên, an toàn READY; ghi phần cải thiện nhờ bằng chứng mới | dùng nguyên planner hiện có trong replay; theo dõi bỏ sót không báo, READY sai, cảnh báo thừa (E5) |

Những tín hiệu W3 **không** có lúc lập kế hoạch: mốc trích dẫn cho từng câu (spec Tổng hợp chỉ có trim 105 s cho mỗi mục), phụ đề nguồn (không có cue nào được lưu), OCR (không có nhận dạng chữ trước vision; thêm OCR là model mới, chỉ thị §26 cấm). Trim do chính người viết nhãn đặt, nên harness có nhóm đối chứng shuffled_prior.

Chính sách ngân sách, cache, huỷ, tiếp tục, lỗi provider và cách ghi thiếu bằng chứng nằm ở ADR-0057 §8 đến §14 và §17.

## 4. Cấu hình thí nghiệm

### 4.1 Môi trường và phiên bản

| Hạng mục | Giá trị |
|---|---|
| Máy | PC Windows của chủ dự án, chỉ CPU, offline; không mở app, không gọi AI hay vision, không ghi lên GitHub, không dùng khoá |
| Python | 3.12 (`py -3.12 -B`, `PYTHONUTF8=1`) |
| Harness | `tests/mastering/longform/targeted.py`, `targeting.py`, `targeted_prereg.json`, `test_targeted.py` tại head `716e984`; recipe `w3-targeted-v2` |
| Test | 71 test, "OK (skipped=1)", 11,6 s trên PC, tại `716e984` |
| Nhãn | W3 sha256 `afa979d2…f66497` = khoá |
| Preflight | 5/5 lần chạy PASS: 22 phần, có spec, có báo cáo vision, có thư mục cache vision, 0 ảnh khung thiếu (712 ở rc12, 710 ở các lần khác); classification_basis exact |
| Lần chạy | rc12 `run-6206f16daf09`, rc13 `run-76080efb6da4`, rc14 `run-b3e7c7967a6e`, rc15 `run-8f64b0a50e04`, rc16 `run-f6919b04ccb3` (chính) |

Bằng chứng gốc (nhật ký, ảnh khung, câu trả lời vision) nằm trên PC Windows và không được đẩy lên GitHub vì repo công khai. Tóm tắt lần chạy: [assisted-e2e/stage2/stage2-offline-results.md](assisted-e2e/stage2/stage2-offline-results.md).

### 4.2 Các thí nghiệm

| Thí nghiệm | Nội dung |
|---|---|
| E1 Baseline | §1: recall, độ phủ ứng viên và thu bằng chứng, bỏ sót không báo, khung/lời gọi, thời gian, RAM |
| E2 Định vị | mỗi nhánh chọn thêm khung chỉ từ "planning view" không chứa nhãn; số câu R3 có khung trúng, so với kỳ vọng ngẫu nhiên |
| E3 Phát lại bằng chứng | khung được chọn nhận câu trả lời đã lưu chỉ khi khoá cache chính xác của production được chứng minh: phiên bản prompt, nhà cung cấp, model đã yêu cầu, bộ nhãn và byte JPEG, và câu trả lời đã ghi phải khớp bản ghi cache (từ `a558396`/`468cb05`; lần tính lại exploratory, xem §4.4 và mục "Trạng thái số liệu E3–E5"); nếu không có thì ghi "không mô phỏng được". Lần chạy ban đầu `716e984` chỉ dùng phép xấp xỉ trước rà soát (JPEG giống từng byte và cùng nhà cung cấp, model ở mức lần chạy), **không** phải khoá cache production |
| E4 Ngân sách | matched (bằng số khung có mục tiêu mà lần chạy đã trả, rc16: 40), 2x (80), 4x (160), cộng thêm trên phần đã chi; và một nhánh cùng tổng ngân sách (respend B0) |
| E5 Gán lỗi | chuyển tầng từng câu: R3 sửa được, R3 chuyển sang R4/R5/R6, câu trúng bị mất, bỏ sót không báo mới, READY sai, cảnh báo thừa |

### 4.3 Nhánh

| Nhánh | Cách chọn khung |
|---|---|
| A | baseline, không thêm khung |
| A+ | rải đều (`vision.allocate`) trên các cảnh chưa nhìn |
| P+ | luật hiện có của production (`vision.targets`, chỉ đi tới) với ngân sách lớn hơn |
| prior | tâm = vị trí tương đối của câu trong lời dẫn chiếu lên trim |
| shuffled_prior | đối chứng âm: tâm của prior bị dịch vòng giữa các câu cùng phần |
| anchor | tâm = khung của cảnh đã nhìn có Scorer.relevance cao nhất trong cửa sổ đi tới |
| gap | rải trên các cảnh chưa nhìn trong cửa sổ đi tới |
| mixed | Pass 2: lần lượt tín hiệu không phải vision, anchor, prior, gap |
| B0-mixed, B0-P+ | bỏ các khung có mục tiêu đã trả (40 ở rc16) rồi chi lại bằng mixed hoặc luật production |
| ALL_DONOR_ITEMS | trần: mọi khung có câu trả lời từ lần chạy khác |
| ORACLE_* | giới hạn trên dùng nhãn, bảng riêng (§10) |

Điều kiện: **open** (điều kiện production, mọi cảnh chưa nhìn và dùng được), **closed_world** (chỉ cảnh có câu trả lời từ lần chạy khác; không phải điều kiện production), **ceiling**. Cửa sổ: 5, 10, 15, 30 s và cửa sổ đi tới (fwd); mọi cửa sổ đều được báo cáo, không chọn "cửa sổ tốt nhất".

### 4.4 Kỷ luật

- **Mù nhãn.** Bộ chọn nằm trong `targeting.py`, không nhận đối tượng nhãn; có kiểm tra AST theo danh sách import cho phép. Mỗi lần chạy, một tiến trình con chạy `select --all-arms` không có tham số nhãn, và kết quả phải giống từng byte với phần đã nhúng: **PASS 5/5**.
- **Đăng ký trước.** Quyết định chính cố định ở commit `c60ab02`, là tổ tiên của head `716e984`, trước mọi lần chạy trên dữ liệu thật. Digest khớp: targeting `fd884a26…427ee92`, analysis `4dff01c5…711a7f`. Nhãn kết quả: **PREREGISTERED**.
  - Nhánh chính: `open:mixed:W=fwd:+matched`, câu được gắn cờ (partial/none và needs_review).
  - Chỉ số chính: số câu R3 có ít nhất một khung được chọn nằm trong đoạn đúng.
  - Đối chứng: `open:P+:-:+matched` và `open:shuffled_prior:W=fwd:+matched`.
  - Quyết định được ghi nhận là lần chạy tại `716e984`. Sau rà soát PR #830, commit `c1e97a5` làm chặt harness: đăng ký trước không kiểm chứng được (không có git) bị coi là exploratory, cờ `preregistered` của lô gọi thật lấy từ cổng, và tệp perf chỉ ghi tên lần chạy. Commit này không đổi `targeting.py` (`fd884a26…`) nhưng đổi `targeted.py`, nên chạy lại từ `c1e97a5` trở đi sẽ có nhãn PREREGISTRATION MISMATCH (exploratory) theo thiết kế và cho digest đầu ra khác. Bản ghi đăng ký trước giữ nguyên và được một test khoá. Muốn tái lập quyết định thì checkout `716e984`; mọi lần chạy dữ liệu sau này (kể cả lô gọi thật) cần một bản đăng ký trước mới, commit trước khi chạy.
  - Lần rà soát thứ hai của Lead (PR #831) dẫn tới `a558396` (chỉ nhận câu trả lời theo khoá cache chính xác của production), `b17bb53` (đưa lại các số đếm ghép nối vào báo cáo) và `468cb05` (khoá cache cũ chỉ lấy theo cặp nhà cung cấp và model mà spec yêu cầu, không ghép chéo với model đã trả lời trong báo cáo lần chạy). Cả ba commit đều đổi `targeted.py` và không đổi `targeting.py` (`fd884a26…`). Vì vậy các lần tính lại trên máy tại `a558396`, `b17bb53` và `468cb05` đều mang nhãn PREREGISTRATION MISMATCH (exploratory) theo thiết kế: digest `analysis_sha256` hiện tại khác bản đăng ký. Chúng không được coi là đăng ký trước, `targeted_prereg.json` không bị sửa, và quyết định E2 được ghi nhận vẫn là lần chạy tại `716e984`. Lần rà soát tiếp theo dẫn tới `7d90f35` (phân loại khung và A0 cần cùng bằng chứng câu trả lời; câu trả lời phát lại mang các trường sản phẩm và hook của bản ghi). Sau đó `4607031` đóng an toàn khi không có khoá chứng minh được (mọi câu trả lời ngoài broad là unproven, A0 giữ lại). `4607031` là head mã cuối; lần chạy lại trên máy tại đây mang nhãn exploratory theo thiết kế, không đổi rc16 hay giá trị quyết định nào. Các commit sau đó chỉ sửa tài liệu.
  - Luật: "được ủng hộ trên REAL-DEV" chỉ khi trên rc16 nhánh chính hơn cả hai đối chứng (McNemar chính xác p < 0,05 so với P+), hơn cả hai ở **cả 5 lần chạy**, và vượt kỳ vọng ngẫu nhiên. Recall và chi phí chỉ được quyết bằng lô gọi thật do chủ dự án duyệt.
- **Tất định.** Chạy A và chạy B cùng lệnh cho ba tệp giống từng byte:

| Tệp | sha256 (A = B) |
|---|---|
| `targeted-run-f6919b04ccb3.json` | `eeea89bb52b6d3a0634b0cd92cd53111f890d87339ef23c993e4adee78e7a04f` |
| `targeted-run-f6919b04ccb3.md` | `c513db2c3b9d3ec0b9d16490d7f0835cd34c63483d402943a1178bf47dbff382` |
| `real-call-batch-run-f6919b04ccb3.json` | `8d0bc9e38c9940e36f3736a91294ea76b93c4831198ef657d227b22ebc894ace` |

## 5. Recall@1/@3/@5

### 5.1 rc16, điều kiện open

Mọi con số dưới đây là **mô phỏng một phần** (§6): rất ít khung được chọn có câu trả lời đã lưu, nên phần lớn hàng giữ nguyên baseline.

| Nhánh | Ngân sách | Khung mô phỏng được | recall@1 | recall@3 | recall@5 | Chọn trúng | Sửa/hỏng (p) |
|---|---|---|---:|---:|---:|---:|---|
| A (baseline) | 0 | - | 20 | 26 | 31 | 24 | - |
| A+ | matched / 2x | 2/40, 2/80 | 20 | 26 | 31 | 24 | 0/0 (1,0) |
| A+ | 4x | 6/160 | 22 | 28 | **33** | 26 | 2/0 (0,5) |
| P+ | matched / 2x | 1/40, 3/80 | 20 | 26 | 31 | 24 | 0/0 (1,0) |
| P+ | 4x | 6/160 | 20 | 26 | 31 | 24 | 1/1 (1,0) |
| **mixed W=fwd (chính)** | matched | 0/40 | 20 | 26 | 31 | 24 | 0/0 (1,0) |
| mixed W=fwd | 2x | 1/80 | 20 | 26 | 31 | 24 | 0/0 (1,0) |
| mixed W=fwd | 4x | 2/160 | 21 | 27 | 32 | 25 | 1/0 (1,0) |
| mixed W=5/10/15/30 | matched / 2x / 4x | 0/40, 0–1/80, 2–4/160 | 20–21 | 26–27 | 31–32 | 24–25 | 0/0 đến 1/1 |
| anchor (mọi W) | matched / 2x / 4x | 0/40, 1/80, 1–4 trên 108–160 | 20–21 | 26–27 | 31–32 | 24–25 | 0/0 đến 1/1 |
| gap W=fwd | matched / 2x / 4x | 3/40, 3/80, 6/160 | 20–21 | 25–26 | 30–31 | 23–24 | 0/1 đến 1/1 |
| prior (mọi W) | matched / 2x / 4x | 2/40, 2/80, 3–4 | 20 | 25 | 29–30 | 23 | 0/1 đến 0/2 (0,5) |
| shuffled_prior (mọi W) | matched / 2x / 4x | 0–9 | 20–21 | 25–27 | 30–32 | 23–25 | tối đa 1/0 hoặc 0/1 |
| B0-mixed (cùng tổng ngân sách; thiên về luật production theo cách dựng) | 40 chi lại | 9–10/40 | 16 | 25 | 28 | 21 | 4/7 (0,5488) |
| B0-P+ (cùng tổng ngân sách) | 40 chi lại | 40/40 | 20 | 26 | 31 | 24 | 0/0 (1,0) |

- Tóm tắt: matched 30–31/60 (A 31); 2x 30–32; 4x 29–33. Tốt nhất là A+ rải đều ở 4x, 33/60 (sửa 2, hỏng 0, p = 0,5, **không có ý nghĩa thống kê**). Các nhánh prior hơi âm (29–30/60, mất 1–2 câu).
- B0-P+ chi lại đúng 40 khung mà lần chạy đã trả và cho lại đúng A (31/60). Đây là phép kiểm nhất quán của cách tái dựng.
- B0-mixed (bỏ 40 khung có mục tiêu của production rồi chi lại bằng mixed) cho 28/60, sửa 4, hỏng 7, p = 0,55. Cùng tổng ngân sách, phép so sánh này thiên về luật production theo cách dựng và không cho thấy mixed tốt hơn: B0-P+ chi lại đúng các khung của lần chạy với chính câu trả lời của chúng (40/40 mô phỏng được), còn B0-mixed chỉ có câu trả lời cho 9–10 trên 40 khung, nên 28/60 chủ yếu đo phần bằng chứng bị bỏ đi trong mô phỏng, không phải việc định vị kém hơn.
- "Sửa/hỏng" là số câu đổi hit@5 so với A, kiểm định McNemar chính xác hai phía.

**closed_world và ceiling** (chỉ cảnh có câu trả lời từ lần chạy khác; **không phải điều kiện production**): recall@5 27–32/60. Cao nhất 32/60, ở anchor W=5 s (chỉ 1 khung) và shuffled_prior W=30 s (7 khung). Trần ALL_DONOR_ITEMS 30/60 (sửa 3, hỏng 4): thêm toàn bộ câu trả lời của các lần chạy khác vẫn không vượt baseline; chúng vừa sửa 3 câu vừa làm hỏng 4 câu. Chữ của vision cho cùng một khung khác nhau giữa các lần chạy (§11), nên câu trả lời mượn từ lần chạy khác không trung tính.

**Supporting runs.** rc12–rc15 chạy ngân sách matched ở cả hai điều kiện; báo cáo này chỉ dùng chúng cho quyết định §5.2. Bảng đầy đủ nằm trong tệp markdown do harness sinh ra trên PC.

### 5.2 Quyết định đã đăng ký trước (chỉ số chính)

| Lần chạy | Nhánh chính: câu R3 có khung trúng | P+ | shuffled_prior | Kỳ vọng ngẫu nhiên | p so với P+ | Hơn cả hai | Vượt ngẫu nhiên |
|---|---:|---:|---:|---:|---:|---|---|
| rc12 | 2 | 1 | 0 | 2,8006 | 1,0 | có | không |
| rc13 | 3 | 1 | 1 | 2,8077 | 0,625 | có | có |
| rc14 | 2 | 3 | 2 | 3,1269 | 1,0 | không | không |
| rc15 | 2 | 2 | 1 | 3,1563 | 1,0 | không | không |
| **rc16** | **2** | 1 | 0 | 2,9506 | 1,0 | có | không |

- "Kỳ vọng ngẫu nhiên" là số câu R3 kỳ vọng có khung trúng nếu lấy ngẫu nhiên đều cùng số khung từ tập khung hợp lệ của nhánh.
- Nhánh chính hơn cả hai đối chứng chỉ ở rc12, rc13 và rc16, và chỉ vượt ngẫu nhiên ở rc13. Không lần chạy nào có p < 0,05.
- Cổng: blindness check PASS, PREREGISTERED. **Kết quả: "not supported"** (kết quả trước cổng cũng là "not supported").
- Ở ngân sách matched, các nhánh open trừ shuffled_prior chọn được khung trúng cho 1–4 câu R3 với kỳ vọng ngẫu nhiên 2,37–4,05; shuffled_prior có 0–1 (kỳ vọng 0–2,95). Nhánh rải đều A+ có 4 (kỳ vọng 2,80), nhiều hơn nhánh chính. Ở 4x: A+ 13 (10,05), P+ 11 (10,33), mixed fwd 10 (10,33), gap 15 (10,33), prior 6–10, shuffled_prior 0–7. Các so sánh phụ này không được đăng ký trước, chỉ trên một lần chạy, và không thay đổi kết luận.

## 6. Độ phủ thu bằng chứng

**EAC (evidence acquisition coverage), rc16, baseline A.** EAC khung: 27/60 câu có một khung đã gửi cho vision nằm trong đoạn đúng. EAC ứng viên: 29/60 câu có một ứng viên đã nhìn chồng đoạn đúng. Tính theo từng khoảng thời gian nhãn (64 khoảng của các câu có footage; một câu có thể có nhiều khoảng): 27/64 (khung) và 29/64 (ứng viên). EAC chỉ tính câu trả lời vision trong các phần mục, khác cách `attribute` coi mọi bằng chứng nội dung là "đã nhìn", nên 29 không bằng 60 trừ số câu R3 và R2.

**Phân loại lượt nhìn rc16** (basis exact, từ khoá cache): broad 95, broad_shared 5, targeted 53, targeted_shared 4, multi-frame 0; khung có mục tiêu trong các phần mục 40. Tại head mã cuối `7d90f35` (bản sao giống từng byte được tính là shared): targeted 43, targeted_shared 14, khung có mục tiêu trong các phần mục 31, không có unproven; ngân sách matched vẫn là 40 theo báo cáo vision của log. Lượt broad tái dựng được (RECONSTRUCTED): 95 lượt chọn, 95 đã quan sát, 0 thiếu trong cache.

**Nguồn câu trả lời để mô phỏng.** Donor là bốn lần chạy còn lại. Ở rc16, chỉ 20 cảnh chưa nhìn có câu trả lời từ donor (donor supply reachable 20); 29 câu trả lời dùng chung bị loại; 0 lệch provider. Năm lần chạy nhìn gần như cùng các khung (lượt broad cố định), nên kho câu trả lời này rất nhỏ.

Phát lại từ donor còn thiên về luật production theo cách dựng: B0-P+ chi lại chính các khung của lần chạy với chính câu trả lời của chúng (40/40 mô phỏng được), còn B0-mixed chỉ có câu trả lời cho 9–10 trên 40 khung. Vì vậy so sánh cùng tổng ngân sách (B0) không đo được việc mixed định vị tốt hay kém hơn trên khung mới.

Độ phủ cache theo nhánh (rc16, open; khung mô phỏng được trên khung đã chọn ở matched / 2x / 4x):

| Nhánh | Mô phỏng được (không mô phỏng được) |
|---|---|
| mixed W=5 s | 0/40 (40), 0/80 (80), 4/160 (156) |
| mixed, các W khác (gồm nhánh chính fwd) | 0/40, 1/80, 2/160 |
| anchor | 0/40, 1/80, 1–4 trên 108–160 |
| A+ | 2/40, 2/80, 6/160 |
| P+ | 1/40, 3/80, 6/160 |
| gap | 3/40, 3/80, 6/160 |
| prior | 2/40, 2/80, 3–4 trên 112–160 |
| shuffled_prior | 0–3 trên 13–40, tối đa 7 trên 80–129, 9/160 |
| B0-mixed | 9–10/40 |
| B0-P+ | 40/40 |
| closed_world, ceiling | 100 % theo cách dựng, nhưng chỉ 0–20 khung |

**Trên các nhánh open, tối đa 7,5 % khung được chọn mô phỏng được ở ngân sách matched và tối đa 5,6 % ở 4x.** Vì vậy E3/E4/E5 là **mô phỏng một phần: cả lợi ích lẫn tác hại đều bị lấy mẫu thiếu**. Các hàng recall ở điều kiện open phần lớn chính là baseline không đổi. Không con số nào ở đây là cận dưới. Chỉ số định vị §5.2 không phụ thuộc vào cache, nên nó là kết quả chính.

## 7. Lời gọi vision

**Baseline production (W3, mỗi lần chạy mới, cache trống).** Ngân sách 142 khung (`max(80, min(400, 2 × 71 câu))`), 138–141 câu trả lời vision mỗi lần (§1). Ở rc16: broad 95, có mục tiêu 44, nhiều khung 0 ([LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md](LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md) §6). Số lời gọi vision thật của baseline **chưa đo**: engine chỉ đếm khung (`Describer.asked`), không đếm lời gọi hay token.

**Stage 2 đã gọi 0 lời gọi AI.** Số lời gọi dưới đây là **ước lượng** của harness: `calls_est = Σ theo phần ceil(số khung của phần / 4)` (vision gửi 4 khung một lô), không tính thử lại khi lỗi FORMAT. Khung lấy từ cache tính vào ngân sách nhưng không tốn lời gọi.

| Nhánh (rc16, open) | Khung thêm matched / 2x / 4x | Lời gọi ước lượng matched / 2x / 4x |
|---|---|---|
| A+ rải đều | 40 / 80 / 160 | 20 / 20 / 40 |
| P+ production | 40 / 80 / 160 | 19 / 24 / 51 |
| mixed W=fwd (chính) | 40 / 80 / 160 | 19 / 24 / 51 |
| shuffled_prior W=fwd | 40 / 80 / 160 | 19 / 24 / 51 |
| anchor, gap, prior, mixed (các W còn lại, đủ khung) | 40 / 80 / 160 | 18–19 / 24–30 / 47–53 |
| anchor W=5 s, prior W=5 s (hết khung hợp lệ ở 4x) | 40 / 80 / 108–112 | 17–18 / 27–28 / 33 |

Khoảng chung trên các nhánh open chọn đủ ngân sách: matched 15–20 lời gọi (phần lớn 18–20; shuffled_prior W=15 s là 15), 2x 20–30 (riêng A+ là 20: 80 khung rải đều chia chẵn thành các lô 4 khung trong từng phần; các nhánh khác 24–30), 4x 40–53. Các nhánh bị giới hạn bởi tập khung hợp lệ nhỏ hơn: anchor và prior W=5 s ở 4x (108 và 112 khung, 33 lời gọi); shuffled_prior W=5 s và W=10 s ở mọi ngân sách (13 và 31 khung, 7 và 13 lời gọi), W=15 s ở 2x và 4x (58 khung, 20 lời gọi), W=30 s ở 4x (129 khung, 41 lời gọi). Riêng ngân sách matched đã là 40 khung, tức khoảng 28 % của ngân sách 142 hiện có; 4x là 160 khung, nhiều hơn toàn bộ ngân sách hiện có.

## 8. CPU/RAM

| Hạng mục | Thời gian | RAM đỉnh | Ghi chú |
|---|---|---|---|
| Harness `targeted all`, 5 lần chạy, chạy A | 591,1 s | 350 MB | chỉ CPU; chuẩn bị 35,3 s; mỗi lần chạy 79,3–110,6 s (rc16 110,6 s) |
| Harness, chạy B (kiểm tất định) | 611,7 s | 350 MB | cùng lệnh |
| Pha tiêu biểu mỗi lần chạy | nhánh khoảng 38–45 s; upper bounds khoảng 42–45 s; E1 khoảng 4,5 s; select khoảng 4,4 s | | |
| E1 `w3.py` score / attribute / oracle | 0,4–0,7 s / 0,8–0,9 s / 4,7–5,6 s mỗi lần chạy | khoảng 29 / 33 / 40 MB | chỉ CPU |
| Tham chiếu: bản nháp W3 trong production, rc16 | 676 s | 3438 MB | [LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md](LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md) §14 |

Chi phí CPU và RAM của việc trích thêm khung và gọi vision thêm trong production **chưa đo**: Stage 2 không chạy Pass 3 thật. Harness là công cụ kiểm thử, không chạy trong app.

## 9. Chi phí ước tính

Không có giá mỗi lời gọi nào được đo hay được cung cấp (harness chạy không có `--cost-per-call`, cột chi phí để trống). Báo cáo này **không đặt giá**. Chi phí được biểu diễn bằng khung, lời gọi và một công thức:

- `chi phí thêm mỗi job = calls_est × P`, với `P` là giá mỗi lời gọi của provider tại thời điểm chạy.
- Với nhánh chính trên rc16: khoảng 19 × P (matched), 24 × P (2x), 51 × P (4x). Với A+: 20 × P, 20 × P, 40 × P.
- Công thức chưa tính thử lại khi lỗi FORMAT và chưa có số liệu token.

**Lô gọi thật.** Harness đã ghi `real-call-batch-run-f6919b04ccb3.json`: hợp các khung không mô phỏng được của A+, P+, mixed W=fwd và shuffled_prior W=fwd ở ngân sách matched, điều kiện open, chỉ rc16. Mỗi nhánh thành phần có 38–40 khung không mô phỏng được; tổng số khung sau khi hợp không có trong tài liệu bằng chứng của báo cáo này. Lô này **chưa chạy** và chỉ được chạy khi chủ dự án duyệt, có trần chi phí rõ ràng. Theo đăng ký trước, recall và chi phí (E4) chỉ được quyết bằng lô này, không bằng phát lại cache.

## 10. Oracle và non-oracle

Mọi hàng ORACLE dùng nhãn. Chúng là **giới hạn trên offline**, không phải kết quả đạt được và không phải kết quả nghiệm thu.

| Loại | Hàng (rc16) | recall@5 | Chọn trúng | So với A (31) |
|---|---|---:|---:|---|
| non-oracle | A baseline | 31 | 24 | - |
| non-oracle | tốt nhất open, A+ 4x | 33 | 26 | +2 (sửa 2/hỏng 0, p 0,5) |
| non-oracle | nhánh chính mixed W=fwd matched | 31 | 24 | 0 |
| ORACLE | thu + hiểu, mọi cảnh đúng (ORACLE_ACQ+INTERP_all_hits) | 56 | 55 | +25 (sửa 26/hỏng 1) |
| ORACLE | thu đúng ở cửa sổ quanh nhãn, W = 10/15/30/fwd, ngân sách matched | 42 | 37 | +11 (sửa 14/hỏng 3, p 0,0127) |
| ORACLE | như trên, W = 5 s | 41 | 38 | +10 |
| ORACLE | chỉ hiểu đúng các cảnh đã nhìn (INTERPRETATION_looked_only) | 34 | 30 | +3 |
| ORACLE | câu trả lời chuẩn trên khung do A+ matched chọn | 34 | 29 | +3 |
| ORACLE | câu trả lời chuẩn trên khung do prior matched chọn | 32–34 | 25–27 | +1 đến +3 |
| ORACLE | câu trả lời chuẩn trên khung do P+ matched chọn | 32 | 25 | +1 |
| ORACLE | câu trả lời chuẩn trên khung do shuffled_prior matched chọn | 31–32 | 24–25 | 0 đến +1 |
| ORACLE | câu trả lời chuẩn trên khung do gap matched chọn | 31 | 27 | 0 |
| ORACLE | câu trả lời chuẩn trên khung do mixed hoặc anchor matched chọn | 29–30 | 25 | −2 đến −1 |
| ORACLE | chọn khung ở cửa sổ quanh nhãn, nhưng chỉ phát lại cache (open và closed_world) | 31–32 | 25–26 | 0 đến +1 |
| ORACLE | gợi ý vị trí (xếp hạng trong ±W quanh nhãn, không thêm bằng chứng) | hit@3: ±5 s 59, ±10 s 38, ±15 s 32, ±30 s 29 trên 60 | - | - |

Đọc bảng:

- Nếu biết chính xác chỗ cần nhìn và AI hiểu đúng, cùng ngân sách matched sẽ thêm khoảng +11 recall@5 trên REAL-DEV. Không tín hiệu non-oracle nào được thử mang lại phần đó.
- Khi cho câu trả lời chuẩn trên chính các khung mà từng nhánh chọn, khung của nhánh chính (mixed) đáng giá ít hơn khung rải đều (A+): 30 so với 34. Vậy vấn đề nằm ở **định vị**, không ở câu trả lời của vision.
- Hàng "chọn khung quanh nhãn nhưng chỉ phát lại cache" cho 31–32 dù vị trí là hoàn hảo. Điều này xác nhận phát lại cache gần như không đo được thay đổi recall (§6).
- Hàng gợi ý vị trí chỉ xếp lại các cảnh trong cửa sổ quanh nhãn; nó không thêm bằng chứng nào, và một phần hit@3 ở cửa sổ hẹp đến từ việc cửa sổ chỉ chứa ít cảnh.

## 11. Tính hợp lệ của dữ liệu

- **Tập:** W3 REAL-DEV ("Why We Fight: The Battle of Russia", 64 câu, trong đó 60 câu có footage; nhãn khoá sha256 `afa979d2…f66497`). Tên này khác với các tập `REAL-DEV`/`REAL-HOLDOUT` của Gate S trong `tests/mastering/real/`.
- **Đây là tập phát triển.** Nó đã được dùng để chẩn đoán từ rc12 và để thiết kế harness này. Nó không phải tập độc lập.
- **Năm lần chạy không độc lập:** chung footage, chung nhãn, và mỗi lần chạy dùng bốn lần còn lại làm donor. Riêng chữ của vision đã dịch recall@5 khoảng ±2–3 câu giữa các lần chạy ([LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md](LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md) §13).
- Mọi phát lại bỏ chỉnh sửa duyệt theo mặc định; phần mở đầu/kết thúc không được tính là đã nhìn cho các mục, như production.
- **GENERALIZATION NOT VERIFIED.** Gate B không được tuyên bố và giữ FAIL.

**Kế hoạch NEW REAL-HOLDOUT (chỉ thị §19), trước mọi quyết định production:**

1. ≥ 40 câu, từ ≥ 2 video dài độc lập với W3, footage thật, có cả câu dễ và câu khó.
2. Nhãn khoá bằng sha256 trước lần đánh giá cuối; không dùng tập này để chọn ngân sách, cửa sổ hay ngưỡng; không sửa nhãn sau khi xem kết quả.
3. Đăng ký trước nhánh, chỉ số, đối chứng và luật quyết định (cùng cơ chế `targeted_prereg.json`) trước khi chạy.
4. Chạy baseline E1 trên tập mới, rồi chạy định vị offline (E2) và một lô gọi thật có trần do chủ dự án duyệt, so cặp với baseline.
5. Đặt tên tập khác hẳn các tập của Gate S để không bị đọc nhầm thành tập đã có.

## 12. Cải thiện so với baseline

**Không có cải thiện nào có ý nghĩa thống kê.**

| Câu hỏi | Kết quả (rc16 trừ khi ghi khác) |
|---|---|
| Định vị (chỉ số chính) | "not supported": 2 so với P+ 1 và shuffled_prior 0, kỳ vọng 2,95, p 1,0; không nhất quán qua 5 lần chạy |
| recall@5 ở ngân sách matched | 30–31/60 so với A 31 |
| recall@5 ở 2x / 4x | 30–32 / 29–33; tốt nhất A+ 4x 33 (p 0,5) |
| Cùng tổng ngân sách (B0-mixed) | 28/60, sửa 4, hỏng 7; thiên về luật production theo cách dựng (§6), nên không phải tác hại đo được |
| R3 sửa được (nhánh chính, matched) | 0; 0 chuyển sang R4/R5/R6; 0 câu trúng bị mất (0/40 khung mô phỏng được) |
| R3 sửa được (E5, mọi nhánh open thêm ngân sách) | matched: 0 ở mọi nhánh; 2x: 1 ở anchor W=5 s và shuffled_prior W=30 s, 0 ở các nhánh khác; 4x: A+ 2; P+, anchor (mọi W), mixed (mọi W), gap và shuffled_prior W=30 s mỗi nhánh 1; prior 0 |
| R3 chuyển sang R4/R5/R6 | 1 ở mọi nhánh prior và gap (mọi W, mọi ngân sách), và ở P+, mixed W=5 s, shuffled_prior W=fwd tại 4x; 0 ở các nhánh khác |
| Câu trúng bị mất | 1 ở các nhánh vừa nêu (prior W=5 s 4x mất 2) và ở anchor W=10 s 4x; 0 ở các nhánh khác |
| Phần cải thiện nhờ bằng chứng mới | harness có ghi cho từng câu sửa được (bằng chứng mới, bản trùng, hay gián tiếp qua con trỏ/xếp hạng), nhưng bản ghi Stage 2 không trích phần tách này (ví dụ cho 2 câu sửa được của A+ 4x), nên không báo cáo ở đây |
| Bỏ sót không báo mới | 0 ở mọi nhánh |
| Thay đổi READY sai | 0 ở mọi nhánh |
| Cảnh báo thừa thêm | 0 ở mọi nhánh |
| READY precision | 5/5 ở mọi nhánh (6/6 ở A+ 4x) |

Các số E5 an toàn (bỏ sót, READY sai, cảnh báo) là tốt nhưng cũng là **mô phỏng một phần**: phần lớn nhánh không nhận được bằng chứng mới nào, nên chúng không chứng minh Pass 3 thật sẽ an toàn.

## 13. Các phương án

Chi tiết ở [ADR-0057](../adr/ADR-0057-targeted-evidence-acquisition.md) §18.

| Phương án | Bằng chứng Stage 2 | Đánh giá |
|---|---|---|
| A Giữ cách lấy mẫu hiện tại, chỉ tăng ngân sách | A+ 4x 33/60 (+2, p 0,5) trên mô phỏng một phần (6/160 khung mô phỏng được); trước đó, gộp lượt nhìn thêm 69–77 cảnh chỉ thêm 1–5 câu vào recall@5 (báo cáo khả thi trước, §12) | không có lợi ích có ý nghĩa đến 4x; chi phí tăng tuyến tính theo khung |
| B Định vị theo kịch bản/thời gian (#815) | quyết định đăng ký trước "not supported"; khung của mixed có giá trị oracle thấp hơn khung rải đều | **không triển khai** |
| C Thêm model ngữ nghĩa hoặc embedding | không thử; thuộc điều kiện dừng §26 (model mới) | ngoài phạm vi chỉ thị; cần ADR riêng |
| D Tăng mốc thời gian do người dùng chỉ | chỉ có giới hạn trên oracle: biết vị trí và hiểu đúng thì +11 (p 0,0127); gợi ý vị trí ±5 s cho hit@3 59/60 (ORACLE; không thêm bằng chứng, cửa sổ ±5 s chỉ chứa vài cảnh nên con số này chủ yếu phản ánh việc thu hẹp, không phải chất lượng bằng chứng; ±15 s cho 32/60). Độ chính xác của mốc do người dùng thật đưa ra **chưa đo** | trong phạm vi chỉ thị, là phương án duy nhất có nguồn vị trí không nằm trong các tín hiệu đã thử (C chưa thử và ngoài phạm vi); là câu hỏi sản phẩm/UX, cần issue và bằng chứng riêng |
| E Kết hợp phủ rộng và có mục tiêu có giới hạn | Pass 1 là lượt broad hiện có; phần có mục tiêu thừa hưởng định vị không được ủng hộ của B; cùng tổng ngân sách B0-mixed 28/60 (sửa 4, hỏng 7), nhưng phép so sánh này thiên về luật production theo cách dựng (§6), nên chỉ cho thấy không có cải thiện | không có cơ sở hơn B; giữ luật production hiện có |

## 14. Khuyến nghị

1. **Không triển khai Option B (#815) vào production planner lúc này.** Quyết định đăng ký trước là "not supported", và không có cải thiện recall có ý nghĩa.
2. **Không tăng ngân sách vision mặc định** (Option A): không có lợi ích có ý nghĩa đến 4x trên mô phỏng một phần, trong khi 4x đã nhiều hơn toàn bộ ngân sách hiện có.
3. **Option C** cần ADR riêng nếu được đặt ra; không làm trong chỉ thị này.
4. **Option D** là phương án duy nhất trong phạm vi chỉ thị có nguồn vị trí không nằm trong các tín hiệu đã thử (Option C chưa thử và ngoài phạm vi); giới hạn trên oracle cho thấy định vị đúng có dư địa, nhưng mốc do người dùng chỉ không phải nhãn. Nó cần một issue sản phẩm/UX riêng với bằng chứng riêng trước khi thiết kế.
5. **Option E** (phủ rộng kết hợp có mục tiêu có giới hạn) chính là cấu trúc production hiện có. Thay phần có mục tiêu bằng mixed không cho thấy cải thiện (B0-mixed 28/60; phép so sánh này thiên về luật production theo cách dựng), và nó thừa hưởng định vị không được ủng hộ của Option B. Giữ nguyên luật production hiện có.
6. **Bằng chứng tiếp theo, nếu chủ dự án muốn tiếp tục:** (a) chạy lô gọi thật đã chuẩn bị, có trần, sau khi chủ dự án duyệt; và/hoặc (b) xây NEW REAL-HOLDOUT (§11). Chỉ sau đó mới đánh giá lại ADR-0057.
7. Những điểm kiểm toán (lệch đầu cửa sổ của lượt có mục tiêu; thiếu trạng thái thu bằng chứng cho từng ứng viên; `coverage_map` tính mọi nguồn nội dung) có thể được nêu thành issue riêng. Báo cáo này không sửa chúng.

## 15. Stage 2 PASS/FAIL

| # | Điều kiện §21 | Kết quả | Ở đâu |
|---|---|---|---|
| 1 | ADR đầy đủ | ✓ 22 mục, **PROPOSED** | [ADR-0057](../adr/ADR-0057-targeted-evidence-acquisition.md) |
| 2 | Có baseline trước | ✓ E1 bằng `w3.py` ở main `cf0c2a9`, năm lần chạy | §1 |
| 3 | ≥ 1 thí nghiệm mô phỏng/offline PoC | ✓ E2–E5, đăng ký trước, tất định | §4, §5 |
| 4 | Không đổi production planner | ✓ chỉ thêm tệp dưới `tests/mastering/longform/`; không đổi `engine/`, `contracts/`, app hay registry | §4.1; PR #830 |
| 5 | Không dùng nhãn ẩn tạo kết quả giả | ✓ bộ chọn mù nhãn, blindness check PASS 5/5; oracle tách bảng riêng | §4.4, §10 |
| 6 | Recall@1/3/5 | ✓ | §1, §5 |
| 7 | Khung/lời gọi và chi phí | ✓ khung và lời gọi ước lượng; chi phí là công thức theo giá provider, không đặt giá | §7, §9 |
| 8 | Gán lỗi | ✓ R2–R6 năm lần chạy; E5 chuyển tầng | §2, §12 |
| 9 | So sánh phương án | ✓ A–E | §13; ADR-0057 §18 |
| 10 | Quyết định đề xuất rõ ràng | ✓ không triển khai Option B | §14 |
| 11 | Kế hoạch kiểm thử tập độc lập trước production | ✓ NEW REAL-HOLDOUT | §11 |
| 12 | Giới hạn kỹ thuật và chiến lược rollback | ✓ mô phỏng một phần, runs không độc lập, chưa gọi thật; rollback | §6, §11; ADR-0057 §19, §21 |

**Stage 2 Targeted Evidence Feasibility: PASS** với tư cách một nghiên cứu khả thi. Kết quả thực chất: định vị theo kịch bản/thời gian **không được ủng hộ**, khuyến nghị **không triển khai**. PASS này không gộp với Production Gate.

**Windows validation (chỉ thị §22).** Thí nghiệm chạy offline bằng Python 3.12 trên PC Windows, chỉ CPU, với nguồn bằng chứng là năm lần chạy W3 đã lưu, harness tại `716e984` (quyết định được ghi nhận) và tính lại exploratory tại `468cb05` (xem §4.4), recipe `w3-targeted-v2`, replay tất định (§4.4). PoC offline này **không phải desktop E2E** và **không phải bằng chứng sẵn sàng sản xuất**. Không cần bản Windows RC riêng cho một thuật toán chưa tích hợp.

| Hạng mục | Trạng thái |
|---|---|
| Targeted Evidence ADR (ADR-0057) | **PROPOSED** |
| Targeted Evidence Feasibility | **PASS** (nghiên cứu; quyết định "not supported") |
| Long-Form Production Gate B | **FAIL**, GENERALIZATION NOT VERIFIED |
| Human Validation Gate D | **BLOCKED** |
| Documentary Assisted Pilot Entry | **NOT READY** |
| Documentary Auto Gate S | **FAIL**, NO-GO |

Stage 1 (#821, #816, #817, Release Safety Closure) đã PASS trong phạm vi đã kiểm chứng theo quyết định A của chủ dự án ngày 2026-11-14; báo cáo này không đổi trạng thái đó. **Phase 4 vẫn NO-GO.** Không phát hành stable.
