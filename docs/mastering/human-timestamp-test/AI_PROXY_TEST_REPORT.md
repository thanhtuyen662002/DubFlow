# AI PROXY TEST — EXPLORATORY, NOT HUMAN VALIDATION

Human-Guided Timestamp Retrieval (issue #832), ngày 2026-11-14. Chạy trên máy dự án (Windows) bằng harness offline, `hintexp.py` ở commit 4490a00. Không gọi Vision API trả phí, không dùng khoá AI của app, không thêm UI, không đổi production planner, không ghi GitHub.

Đây là kết quả của AI đóng vai người kiểm tra. Nó không phải Human Validation và không phải Desktop UI E2E. Các gate giữ nguyên: Long-Form Gate B = FAIL, Human Validation Gate D = BLOCKED, Documentary Assisted Pilot = NOT READY, Documentary Auto = NO-GO. Không phát hành stable, không bắt đầu Phase 4.

## 1. Nguồn video

| | Video 1 | Video 2 |
|---|---|---|
| Phim | Battle of Russia (run rc16 có sẵn) | "Divide and Conquer" (1943), U.S. War Department, loạt Why We Fight |
| Nguồn | Run W3 đã dùng từ rc11 | Wikimedia Commons: https://commons.wikimedia.org/wiki/File:U.S._War_Department_-_Divide_and_Conquer_(1943).webm (gốc archive.org, mục 0176_Divide_and_Conquer_19_00_41_00) |
| Giấy phép | như các vòng W3 trước | Public Domain |
| Thông số | | 27:47, 640×480, 456.765.846 B |
| SHA-256 | | 8280d64248a6a1f8a1d5b5c0120e38d348e7d61a04306af6dce49ee242dd6594 (SHA-1 khớp giá trị Commons công bố; tải HTTPS thường, không DRM, không đăng nhập) |

## 2. Thiết kế

- 32 câu: 16 câu mỗi video. 25 câu có footage, 7 câu không có footage phù hợp.
- Ba vai tách riêng:
  - Tạo nhãn: video 1 dùng nhãn W3 đã khoá từ rc11. Video 2 do một agent riêng viết nhãn chỉ từ ảnh 1 khung/giây; nhãn được khoá sha256 ec4edc09…, mã hoá, bản rõ bị xoá trước khi reviewer bắt đầu.
  - Reviewer: hai agent AI R1 và R2, mỗi agent chỉ dùng một công cụ không đọc nhãn. Kiểm tra transcript cho thấy họ chỉ gọi công cụ được phép và chỉ đọc ảnh công cụ đó tạo.
  - Chấm điểm: script, không dùng phán đoán.
- Hai phương pháp:
  - RP: giả lập offline Review Panel (cảnh đang chọn, phương án, "Chọn cảnh khác…"), chỉ có ảnh đại diện, không phát video.
  - TS: reviewer xem khung hình, ghi mốc thời gian gần đúng, rồi chọn trong danh sách gợi ý quanh mốc đó.
- Thứ tự cân bằng: R1 theo ABBA, R2 theo BAAB. Không reviewer nào gặp cùng một đoạn phim bằng cả hai cách. Mốc thời gian không lấy từ nhãn.

## 3. Kết quả thực đo trên AI proxy

| Chỉ số | RP (giả lập offline) | TS |
|---|---:|---:|
| Recall@1 / @3 / @5 của danh sách phần mềm đưa ra (n = 25) | 4 / 5 / 9 | 18 / 24 / 25 |
| Chọn đúng cảnh | 24/25 | 25/25 |
| Nhận nhầm cảnh | 0 | 0 |
| Có footage nhưng không tìm được | 1 | 0 |
| Câu không có footage được nhận ra | 7/7 | 7/7 |
| Sai số mốc: trung vị; trong ±5 / ±15 / ±30 s | không áp dụng | 0 s; 25 / 25 / 25 |
| Thao tác: trung vị mỗi câu; tổng | 4; 128 | 3; 96 |
| Số lần tìm lại | 40 | 0 |
| Thời gian thực thi của AI/automation, trung vị mỗi câu | 14,2 s | 10,9 s |

- Recall@5 theo video: RP 6/13 (video 1) và 3/12 (video 2); TS 13/13 và 12/12.
- Theo reviewer: R1 chọn đúng 12/12 (RP) và 13/13 (TS); R2 chọn đúng 12/13 (RP) và 12/12 (TS).
- Thời gian ở bảng là thời gian chạy của AI và automation, không phải thời gian thao tác của người dùng.
- Các số trên là số "as-shown", tức danh sách reviewer thực sự thấy. Script chấm chính thức tính lại gợi ý TS của video 2 bằng cách xếp hạng khác, nên số chính thức của TS là R@1/@3/@5 = 10 / 24 / 25, chọn đúng 25/25. Hai cách chỉ lệch ở R@1.
- Chọn đúng của RP cao (24/25) dù danh sách RP chỉ đạt R@5 9/25, vì reviewer AI duyệt toàn bộ shot qua "Chọn cảnh khác…" (40 lần tìm lại). Người thật có thể không kiên nhẫn như vậy.

## 3b. Kiểm tra độc lập trên cloud

Từ bảng 64 dòng (32 câu × 2 phương pháp, không có câu chữ), phía cloud tính lại bằng script riêng và khớp toàn bộ số ở mục 3: RP 4/5/9, chọn đúng 24/25, 1 không tìm được, 128 thao tác, 40 lần tìm lại, trung vị 14,2 s; TS 18/24/25, chọn đúng 25/25, sai số 0 s, 96 thao tác, trung vị 10,9 s; 7/7 câu không footage ở cả hai phương pháp. Mỗi câu xuất hiện đúng một lần ở mỗi phương pháp. 7 câu không footage là i4.u4, i8.u4, i18.u4 (video 1) và j2.u2, j4.u2, j6.u2, j8.u2 (video 2). Bảng nằm ở [ai-proxy/units-all.csv](ai-proxy/units-all.csv) (64 dòng, chỉ mã câu và số đo). Bảng được chép lại qua tin nhắn nên sha256 khác file gốc trên máy dự án (7a6bbcaa…146f); nội dung từng ô đã đối chiếu. Đầu ra của `score_human` trên máy dự án: RP 4/5/9, TS 10/24/25, sai số TS trung vị 0 s, `INCONCLUSIVE` vì 7 sai lệch (mục 5).

SHA-256 trên máy dự án: nhãn W3 `afa979d2…f66497`; nhãn video 2 (bản rõ, theo lock) `ec4edc09…09cfc6`; file lock video 2 `a595914d…d827c3`; đầu ra R1 `243b089e…90ee57`, R2 `a030ba98…5af10e`; results.json `39e80dc0…ddd389`. Quét chuỗi giống khoá AI trên đầu ra và báo cáo: 0.

## 4. Phân loại

- Thực đo (trên AI proxy): toàn bộ bảng mục 3.
- Mô phỏng: không có.
- Oracle: không dùng; nhãn chỉ dùng để chấm.
- Không thực hiện được: Desktop UI E2E; thời gian thao tác của người thật; xem video bằng trình phát thật; danh sách production cho video 2 (cần dựng run, tức là dịch, TTS và vision có phí); cách ly cưỡng chế bằng quyền truy cập.

## 5. Tiêu chí PROTOCOL §6

Script chấm chính thức (score-human) báo INCONCLUSIVE vì "7 sai lệch". Cả 7 là câu không có footage: reviewer trả lời "không có cảnh" và không ghi mốc, và script đếm việc thiếu mốc là sai lệch. Đây là lỗi của script chấm. Nó được sửa **sau khi** thấy ở vòng này (commit trong PR #834): hàng TS có `marked_missing` mà không có mốc nay là câu trả lời hợp lệ; hàng TS có footage mà thiếu mốc vẫn là sai lệch. Cùng lúc, `score-human --ai-proxy` gắn nhãn kết quả là AI proxy và luôn cho kết luận `EXPLORATORY`, chỉ ghi cách đọc §6 vào `protocol_reading`. Nếu không tính lỗi này, các ngưỡng §6 đều đạt trên số liệu AI. Kết quả vẫn không phải SUCCESS, vì §6 được viết cho người thật và thời gian đo được là thời gian của AI.

## 6. Giới hạn (đọc trước khi dùng số liệu)

1. Sai số mốc 0 s không có giá trị thông tin cho người thật. Khung hình reviewer xem có ghi sẵn giờ nguồn, nên việc đánh mốc gần như cho không. Người thật tua video sẽ sai số khác, và đó chính là điều cần đo. TS Recall@5 25/25 vì thế gần như chắc chắn quá lạc quan.
2. So sánh RP và TS không cân xứng: với video 2, danh sách RP chỉ xếp theo thời gian, không phải danh sách production; RP cũng không phát được video. Recall của RP trên video 2 không đại diện cho Review Panel thật.
3. Nhãn và reviewer là AI cùng họ mô hình, cùng nhìn ảnh 1 khung/giây. Nhãn video 2 chưa được người kiểm tra lại.
4. Cách ly chỉ dựa vào chỉ dẫn và mã hoá: nhãn W3 của video 1 vẫn ở dạng rõ trên đĩa, khoá giải mã nhãn video 2 có trong transcript. Không tuyên bố blind test.
5. Hiểu biết dồn từ câu trước: mỗi reviewer xem và đánh mốc cả hai câu của cùng một phần trong một lệnh, nên câu thứ hai được lợi từ câu thứ nhất.
6. Video 2 không có run production. Pool là các điểm cắt cảnh ffmpeg (ngưỡng 0,30, mảnh ≤ 12 s); câu được lấy từ 8 cửa sổ 100 s cố định. Gợi ý TS của video 2 xếp theo khoảng cách thời gian vì pool không có evidence.
7. Mẫu nhỏ: 32 câu, 2 video, 2 reviewer AI. W3 là REAL-DEV: GENERALIZATION NOT VERIFIED.

## 7. Đề xuất: vòng nhỏ với người thật

Tín hiệu đủ để đáng thử với người, không đủ để kết luận. Đề xuất theo PROTOCOL của #832:
- 1–2 người chưa xem nhãn, mỗi người 90–120 phút, cùng 32 câu và 2 video.
- TS dùng trình phát video thật (không có giờ in trên hình), RP dùng DubFlow.exe.
- Đo sai số mốc thật, thời gian thao tác thật, chọn đúng, nhận nhầm, không tìm được.
- Script chấm đã sửa cho câu "không có cảnh" không có mốc (công bố là sửa sau vòng AI).
- Cho người tham gia biết một số câu thật sự không có cảnh, nhưng không cho biết câu nào hay bao nhiêu câu.

Quyết định cần chủ dự án: ai tham gia; video 2 dựng run thật (có phí dịch, TTS, vision, cần duyệt) hay giữ pool offline với giới hạn ghi rõ.

Bản đầy đủ (có đường dẫn cục bộ) nằm trên máy dự án, ngoài repo; sha256 `30a064b3…bacc947`.
