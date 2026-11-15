# X2 PoC — thiết kế đã chỉnh (v2)

Epic #637 · 2026-11-14 · thay thế phần thí nghiệm và ngân sách của `X2_SOURCE_LINKED_SCRIPT_DESIGN.md` (10 câu trả lời về contract vẫn giữ). **Chưa chạy. Không có chi phí AI nào được phép.**

## 1. Chuỗi được thử

`Cue nguồn → khoảng thời gian nguồn → câu lời dẫn → ứng viên footage → xác minh hình`

Điều kiện bắt buộc trong harness:

- Cue chỉ giúp **định vị/xếp hạng** ứng viên. Cue không bao giờ là bằng chứng hình.
- Transcript không tạo READY, không nâng READY, và không góp vào độ tin cậy dùng cho READY. Harness áp dụng quy tắc an toàn đề xuất ở #836 cho **mọi** nhánh, kể cả A, để các nhánh so sánh được và an toàn.
- Mỗi câu có thể trích 0, 1 hoặc nhiều cue, thuộc 1 hoặc nhiều nguồn.
- Mã cue không tồn tại bị **từ chối** và ghi lại. Không đoán lại, không sửa.
- Không loại ứng viên ngoài vùng cue (trừ nhánh B, vốn được định nghĩa là chỉ tìm trong vùng; B được báo cáo như một cận dưới về an toàn).
- Không giả định hình ở đúng mốc lời nói.
- Không dùng GT để chọn cue. Cue chỉ đến từ câu trả lời của bước viết lời dẫn.

## 2. Các nhánh (cùng một lời dẫn đã khóa)

| Nhánh | Mô tả |
|---|---|
| **A** | Production hiện tại, không có transcript làm bằng chứng (đúng như một nguồn thô không có `.srt`) |
| **B** | Chỉ tìm trong vùng cue ±W |
| **C** | Tìm toàn nguồn; vùng cue chỉ là tín hiệu xếp hạng |
| **D (ablation)** | Như C, cùng lượng transcript đưa vào, nhưng **bỏ liên kết cue–câu**: transcript của cả nguồn được dùng như một khối văn bản để xếp hạng. Tách lợi ích của provenance khỏi lợi ích của "có thêm chữ" |

W (cửa sổ) và mọi tham số khác chỉ được chọn trên REAL-DEV, ghi vào commit, rồi khóa trước khi chạy REAL-HOLDOUT.

Vision: mỗi khung hình chỉ được mô tả **một lần** cho mọi nhánh (cache theo nội dung khung). Mỗi nhánh có cùng ngân sách khung hình mỗi câu. Như vậy chênh lệch giữa các nhánh đến từ *khung nào được nhìn*, không đến từ câu trả lời Vision khác nhau.

## 2b. Nguồn không lời và câu không có cue (chốt trước thí nghiệm, 2026-11-15)

Nguồn không lời: ASR local chạy xong (`asr_status: ok`) và trả về 0 cue. README x2-prep §3.3 định nghĩa nguồn này và cách tính `speech_ratio`. Một câu **không có cue hợp lệ** có thể vì ba lý do, và mỗi câu ghi đúng một lý do:

- `no_cue_source`: câu chỉ nói về nguồn không lời;
- `no_cue_written`: câu bình luận chung, người viết hoặc AI không trích cue;
- `cue_rejected`: mọi cue trích ra đều bị từ chối vì không tồn tại.

| Nhánh | Cần cue ASR? | Câu không có cue được xử lý thế nào | Ghi kết quả |
|---|---|---|---|
| **A** | Không | Tìm toàn nguồn, như mọi câu | Như mọi câu |
| **B** (chỉ trong vùng cue ±W) | Có | Vùng tìm rỗng nên không có ứng viên. **Không** chuyển sang tìm toàn nguồn, vì B được định nghĩa là cận dưới về an toàn | `MATCH_NOT_FOUND`, lý do `no_cue_region`. recall@k của câu đó là 0 |
| **C** (toàn nguồn, cue để xếp hạng) | Không bắt buộc | Không có tín hiệu cue, nên xếp hạng như A (đúng câu 5 của `X2_SOURCE_LINKED_SCRIPT_DESIGN.md`: "rơi về cách tìm hiện tại") | Như mọi câu |
| **D** (khối transcript, bỏ liên kết) | Không bắt buộc | Khối transcript của nguồn không lời là rỗng, nên xếp hạng như A | Như mọi câu |

Quy tắc báo cáo:

- **Mẫu số giữ nguyên.** Câu không có cue và câu thuộc nguồn không lời vẫn được tính trong mọi mẫu số:
  - recall@k và final correctness: trên câu có footage;
  - READY: trên mọi câu;
  - no-footage false assignment: trên câu không footage.

  Không loại câu khó khỏi bảng chính.
- **Báo thêm bảng con** theo từng nhánh, cho ba nhóm: câu có cue hợp lệ; câu không cue (tách theo ba lý do trên); câu thuộc nguồn không lời. Mỗi ô ghi k/n.
- Ghi `speech_ratio`, `cue_count` và `asr_model` của từng nguồn cạnh kết quả, để thấy nhánh nào phụ thuộc lời nói.
- Không tạo cue hay transcript giả cho nguồn không lời. Không mượn cue của nguồn khác.
- Với tiêu chí ở §4, so sánh C với A và C với D trên **toàn bộ** câu (bảng chính). Bảng con chỉ để giải thích kết quả, không dùng để chọn nhánh thắng.

## 3. Chỉ số (tất cả theo hợp đồng X0 + #837)

- recall@1/3/5 (danh sách ứng viên)
- Final chosen correctness: lenient, strict
- Tỉ lệ thời lượng footage chấp nhận được, trên timeline
- Source range hit rate (vùng cue có chứa khoảng nhãn không)
- Candidate pool reduction
- No-footage false assignment (tách riêng hard negative)
- False READY và severe escape (định nghĩa #837)
- Tỉ lệ cue không hợp lệ bị từ chối
- Chi phí AI thực tế (từ `usage` của từng response), chi phí CPU (giây, RAM đỉnh)
- Human intervention dự kiến = số câu không READY / tổng số câu (ước tính, không phải số đo người dùng)

Mọi tỉ lệ ghi k/n và khoảng tin cậy Clopper-Pearson 95 %. Mọi kết quả có nhãn: dev / held-out / measured / AI-judged / human.

## 4. Tiêu chí

- **Tín hiệu tích cực (chỉ để đề xuất bước tiếp):** trên REAL-HOLDOUT, C hơn A ít nhất 10 điểm phần trăm recall@5 **và** hơn D có ý nghĩa (CI không chồng hoàn toàn); severe escape của C không cao hơn A; no-footage false assignment không cao hơn A.
- **Bác bỏ:** vùng cue chứa khoảng chấp nhận được ở < 50 % câu có cue; hoặc C không hơn D (lợi ích chỉ đến từ thêm chữ); hoặc severe escape tăng.
- **INCONCLUSIVE:** holdout < 100 câu sau phân xử, κ < 0,6, hoặc chi phí chạm trần trước khi xong.

## 5. Trình tự và điểm dừng

1. $0: chọn nguồn, xác nhận quyền, ASR local, dựng manifest. *(chờ người)*
2. **Phê duyệt chi phí lần 1:** chạy DEV (~25 câu): viết lời dẫn có cue, Vision. Dừng và báo cáo.
3. Chỉnh thiết kế trên DEV, khóa tham số và code đánh giá.
4. **Phê duyệt chi phí lần 2:** viết lời dẫn HOLDOUT, khóa; người gắn nhãn; khóa nhãn; chạy Vision và bốn nhánh; báo cáo một lần.

## 6. Ngân sách tối đa dự kiến (chưa thực hiện, chưa được duyệt)

**Provider/model:** app gọi Claude qua `engine/dubflow/ai/client.py`; W3 dùng `claude-opus-5-5`. Bảng giá hiện hành (Anthropic API, tra ngày 2026-11-14): Opus 5.5 **$4 / $20** cho mỗi triệu token vào/ra. Thinking của Opus 5.5 **không tắt được** (mặc định effort medium) và được tính như token ra. Client hiện **không** gửi tham số thinking/effort, nên chi phí ra thực tế cao hơn phần chữ trả lời.

| Hạng mục | Số request | Token vào / request | Token ra / request (gồm thinking) | Ước tính | Trường hợp xấu (chạm `max_tokens`) |
|---|---|---|---|---|---|
| Viết lời dẫn có cue — DEV (2 nguồn, ~8 mục) | ~8 | ~4.000 | ~2.000 | ~$0,45 | ~$2,7 (max 16.000) |
| Viết lời dẫn có cue — HOLDOUT (~40 mục) | ~40 | ~4.000 | ~2.000 | ~$2,2 | ~$13 (max 16.000) |
| Vision — DEV (~25 câu × 2 khung × hợp 4 nhánh ≈ 200 khung, lô 4) | ~50 | ~2.300 (4 ảnh 512 px + prompt) | ~1.600 | ~$2,1 | ~$8,7 (max 8.192) |
| Vision — HOLDOUT (~120 câu × 2 × hợp ~3 ≈ 720 khung) | ~180 | ~2.300 | ~1.600 | ~$7,4 | ~$31 |
| Thử lại (client tự thử tối đa 3 lần cho lỗi 429; cộng 30 % dự phòng) | | | | ~$3,6 | |
| **Tổng** | | | | **≈ $16** | **≈ $56** |

Đề xuất trần: **$25 cho lần 1 + lần 2 cộng lại**, chia **$6 (DEV)** và **$19 (HOLDOUT)**. Nếu chạm trần thì dừng và ghi INCONCLUSIVE, không xin thêm giữa chừng.

**Trần cứng thật sự:**

1. Lớp 1 (nhà cung cấp): đặt giới hạn chi tiêu cho workspace/key dùng riêng cho thí nghiệm trên Anthropic Console, bằng đúng trần. Đây là lớp duy nhất chặn được kể cả khi harness lỗi. Chủ dự án phải tự đặt.
2. Lớp 2 (harness): bọc transport, đọc `usage` (input/output, cả thinking) của **mọi** response, cộng chi phí theo bảng giá. Dừng trước request tiếp theo nếu chi phí đã dùng + chi phí xấu nhất của request đó > trần.
3. **Không đo được usage → dừng ngay.** Nếu một response thiếu `usage`, harness tính nó theo `max_tokens` và dừng toàn bộ phép chạy.
4. Không chạy song song; ghi sổ chi phí sau mỗi request.

**Chi phí người** (không phải tiền API): khoảng 15–16 giờ công gắn nhãn và phân xử (xem `X2_EVALUATION_DATASET_PROTOCOL.md` §5).

## 7. Quyền dữ liệu

- Chỉ dùng nguồn đã có người xác nhận quyền (§2 của giao thức dữ liệu).
- Gửi khung hình và transcript tới API là gửi dữ liệu ra ngoài. Chỉ gửi nguồn mà giấy phép cho phép xử lý bởi bên thứ ba. Không gửi nguồn có người thật riêng tư khi chưa có đồng ý.
- Kết quả, lời dẫn, nhãn chỉ giữ trên máy kiểm thử; repo chỉ giữ code và số tổng hợp.
