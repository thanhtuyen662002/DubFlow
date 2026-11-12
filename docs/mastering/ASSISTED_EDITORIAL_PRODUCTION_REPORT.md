# Assisted Editorial Production: báo cáo

- Epic #637, ngày 2026-11-12, theo chỉ thị "DUBFLOW — ASSISTED EDITORIAL PRODUCTION: Safety Fixes, Human Review & Documentary Readiness".
- Mốc ban đầu: rc7 `7acd262`.
- Các bản kiểm trên Windows (đều là candidate, `publish=false`, không phát hành stable):
  - rc8 `d398f9d`;
  - rc9 `07e4fb5`;
  - rc10 `a4ec540` (bản kiểm cuối).

## 1. Quyết định

| # | Quyết định | Kết quả |
|---|---|---|
| 1 | Assisted Production cho Bán hàng / Tổng hợp | **PASS**, xem §11. Còn mở hai lỗi P2 (P2-N5 hiệu suất duyệt, P2-N11 phim dài), không lỗi nào chặn |
| 2 | Documentary Assisted | **READY FOR LIMITED DESIGN/PILOT**, có điều kiện, xem [DOCUMENTARY_ASSISTED_READINESS.md](DOCUMENTARY_ASSISTED_READINESS.md) |
| 3 | Documentary Auto | **NO-GO** |

**Assisted không phải Auto.**
- Gate S (Semantic Auto) vẫn **FAIL**. Không chế độ Auto nào được bật.
- Mức Mạnh vẫn chỉ là một mức mastering. Nó không bao giờ được gọi là "tự động hoàn toàn".
- Kết quả PASS ở đây là PASS của quy trình *AI nháp + người duyệt + kiểm tra cuối*, không phải PASS về độ chính xác của AI. Bản nháp AI trên rc9 có 0/28 câu READY.

## 2. Phạm vi

**Đã làm:**
- Workstream A: an toàn.
- Workstream B: duyệt người và cổng kiểm tra cuối.
- Workstream C: số liệu.
- Workstream D: thiết kế phim tài liệu.
- Windows E2E A–F trên bản candidate.

**Không làm** (theo chỉ thị):
- không dựng lại engine, không thêm model;
- không làm Phase 4;
- không làm timeline editor mới, không làm bảng duyệt thứ hai (dùng lại `review_panel.ts`);
- không hạ ngưỡng, không chỉnh nhãn.

## 3. Các PR

| PR | Nội dung |
|---|---|
| #769 | A1 (#764): câu mở đầu nói về sản phẩm mà cảnh không có sản phẩm thì cần duyệt |
| #770 | A3 (#766): `status.json` ghi nguyên tử, thử lại có giới hạn, không mất CANCELLED, giữ stdout/stderr, COMPLETED chỉ khi thành công |
| #772 | B1: hàng đợi duyệt, Hợp đồng Quyết định của Người, các thao tác mới |
| #774 | B2: Kiểm tra cuối trước khi xuất |
| #776 | Lỗi rc8: P1-N3, P2-N6, P2-N7, P3-N8, P3-N9 (một phần) |
| #778 | Lỗi rc9: P2-N14 (cảnh người dùng ghim không bao giờ được tính là AI khớp), P3-N9 |

A2 (#765) đã được phân loại trên cùng bằng chứng, không đổi code. Chi tiết: [ASSISTED_SAFETY_CLOSURE_REPORT.md](ASSISTED_SAFETY_CLOSURE_REPORT.md) §4.

## 4. Workstream A: an toàn

Xem [ASSISTED_SAFETY_CLOSURE_REPORT.md](ASSISTED_SAFETY_CLOSURE_REPORT.md).

Windows E2E Kịch bản A đạt trên rc8 và rc9:
- Câu mở đầu "nồi chiên" trên footage domino là NEEDS_REVIEW, nằm ở nhóm Nghiêm trọng, và kiểm tra cuối chặn editor.
- Kill engine cho ra FAILED; Huỷ cho ra CANCELLED. JSON luôn hợp lệ và log được giữ.
- Khoá `status.json` bằng FileShare.None hai lần, mỗi lần 2,5 s, run vẫn kết thúc COMPLETED.

## 5. Hàng đợi duyệt (B1)

- Ba nhóm: **Nghiêm trọng / Cần xem / Tuỳ chọn**. Luôn kèm dòng "(nhóm theo cờ của AI, chưa hiệu chỉnh)" (`queue_calibrated: false`).
- Một câu là Nghiêm trọng khi:
  - không tìm thấy cảnh;
  - nguồn không có cảnh;
  - câu nêu thông tin cụ thể mà không có bằng chứng hình;
  - con số không có trong thông tin sản phẩm;
  - quyết định cũ bị huỷ hiệu lực;
  - hoặc đang ở `signals_only`.
- "Câu tiếp theo ›" đi qua hàng đợi mà không phải mở lại bảng. Vị trí được giữ khi đóng/mở bảng và khi khởi động lại app (kiểm trên rc8 và rc9).
- Thao tác: Duyệt, Ghim, Bỏ cảnh, Chọn cảnh khác… (mọi cảnh đã nhập, kèm nguồn và thời điểm), Để sau, Không có cảnh phù hợp, Hoàn tác. Cả 7 thao tác đều đã chạy qua UI thật.

## 6. Hợp đồng Quyết định của Người

- `review-*.json` schema v2. Mỗi bản ghi có đủ các trường:
  - `run_id` (chỉ tên thư mục), `plan_revision`, `speech_ref`, `source_id`, `shot_id`, `source_ms`;
  - `type`, `decision_revision`, `approval_state`, `input_fingerprint`, `decided_at`, `invalidation_reason`.
- Loại quyết định: APPROVE, PIN, REPLACE, EXCLUDE, REVIEW_LATER, MARK_SOURCE_MISSING, UNDO. Riêng INVALIDATED do hệ thống ghi.
- Các quy tắc:
  - USER_APPROVED ≠ SEMANTIC_CORRECT.
  - Ghim không có hiệu lực mãi mãi: footage đổi thì quyết định bị huỷ, kèm lý do.
  - READY ≠ USER_APPROVED.
  - Câu do người dùng xử lý luôn được nói là của người dùng (P1-N3, #776).
  - Cảnh người dùng ghim không bao giờ được tính là bằng chứng của AI (P2-N14, #778): thời lượng đó nằm ở `coverage.user_ms`, không ở `illustrated_ms`.
- File phiên bản 1 vẫn đọc được. Bản ghi loại lạ bị từ chối.

## 7. Thiếu footage

- **"Không có cảnh phù hợp"**: câu rời hàng đợi, nhưng nhận định của planner vẫn giữ và planner không nhận gì. Bấm lại cùng nút để huỷ.
- **"Chọn cảnh khác…"**: chọn bất kỳ cảnh nào đã nhập, ghi thành bản ghi REPLACE.
- **Giới hạn (không tính là đạt):** chưa có thao tác "đặt mốc thời gian tự do trong footage" hay "nhập thêm footage" ngay từ bảng duyệt.
  - Hiện người dùng chỉ chọn được trong các cảnh theo điểm cắt.
  - Với Bán hàng: thêm clip ở trang tạo rồi tạo lại.
  - Với Tổng hợp: dùng "Sửa chương trình…" để thêm nguồn hoặc đổi Bắt đầu/Kết thúc.

## 8. Cổng hoàn tất và Kiểm tra cuối (B2)

`engine/dubflow/mastering/final_qc.py` chạy các kiểm tra sau. Bản dựng thành công **không** có nghĩa là đạt.

| Kiểm tra | Nội dung |
|---|---|
| render | run COMPLETED và có file đầu ra |
| timeline | không đoạn nào vượt quá cuối nguồn; không có đoạn âm hay rỗng, khoảng trống, chồng lấn, khung đứng |
| duration | dài đúng như kế hoạch, sai số ±120 ms |
| captions | không phụ đề nào chạy quá cuối video |
| audio | đỉnh thật ≤ 0 dBTP |
| pending | không còn lựa chọn nào chưa render |
| decisions | không còn câu chưa duyệt, trừ khi người dùng đã chấp nhận |
| claims | không câu Nghiêm trọng nào còn treo, kể cả khi đã chấp nhận |

- Kiểm tra nào không đo được thì ghi "Chưa đo", không bao giờ tính là đạt. Lỗi luôn đóng.
- Các trạng thái engine trả về: RENDERING, RENDER_PENDING, REVIEW_PENDING, FINAL_QC_FAILED, READY_TO_FINALIZE.
- Host giữ thêm hai trạng thái Finalized và Stale Final, dựa trên bản ghi revision và sha256 của bản xuất.
- **Bán hàng:** editor chỉ mở khi kiểm tra cuối đạt.
- **Tổng hợp:** "Xuất bản cuối" bị chặn và liệt kê những gì chưa đạt. Khi đạt, dòng xuất ghi "Kiểm tra cuối: đạt".

## 9. Mức mastering và kiểm soát biên tập

Hai thứ này tách biệt:
- **Mức mastering** (Tắt / Nhẹ / Cân bằng / Mạnh) chỉ đổi cách dựng.
- **Kiểm soát biên tập** (hàng đợi, quyết định, kiểm tra cuối) luôn là của người dùng.

Mặc định của lồng tiếng vẫn là **Tắt** (kiểm trên rc8 và rc9). Mastering Tắt cho Bán hàng không gửi khoá `mastering` nào.

## 10. Windows E2E

Chạy trên máy Windows 11 của chủ dự án, chỉ CPU. Thao tác qua UI thật của DubFlow.exe candidate (CDP với sự kiện chuột và phím thật, hộp thoại Win32). Mỗi lần mở app đều chứng minh target CDP thuộc đúng DubFlow.exe.

| Kịch bản | rc8 | rc9 | rc10 |
|---|---|---|---|
| A An toàn (hook) | PASS | PASS | **PASS** (P2-N14 đã sửa) |
| B Bán hàng Assisted, 3 video | FAIL (P1-N3) | **PASS** | **PASS** |
| C Tổng hợp Assisted, Top 5, 5 nguồn | PASS | xuất được (P3-N9) | **PASS** (P3-N9 đã sửa) |
| D Thiếu footage | PASS, có giới hạn | PASS | — |
| E Hồi quy và bảo mật | PASS (P2-N7) | **PASS** (P2-N7 đã sửa) | — |
| F Phim dài, 20 phút 1 giây | Dựng xong; kiểm tra cuối chặn đúng | không chạy lại | — |

Báo cáo gốc:
- [assisted-e2e/rc8/report-rc8.md](assisted-e2e/rc8/report-rc8.md)
- [assisted-e2e/rc9/report-rc9.md](assisted-e2e/rc9/report-rc9.md)

### 10.1 rc10

Bản kiểm cuối: rc10 `a4ec540`, run 43166968609. Zip `99509a3b…` khớp release evidence. Báo cáo gốc nằm trên máy kiểm: `_sandbox\assisted\rc10\report-rc10.md`.

| Kiểm tra | Kết quả |
|---|---|
| P2-N14: ghim cảnh domino vào câu mở đầu nồi chiên | `illustrated_ms` 0, `user_ms` 3465, câu không READY, recipe v17. Panel ghi "cảnh bạn chọn 100 % thời lượng câu", không còn "Hình khớp 100 %" hay "Có cảnh khớp". Dòng kết quả ghi "…cho 3 câu…". Editor mở được; mở lại app vẫn giữ nguyên |
| Ghim một cảnh AI có thấy khớp (Espresso u2) | vẫn được tính là AI khớp: `illustrated_ms` 4090, `user_ms` 0 |
| P3-N9: Top 5, bỏ một cảnh, dựng lại, xuất | Hiện "Bản cuối cũ… không còn là bản hiện tại". Khi còn 2 câu chưa duyệt thì có hộp xác nhận. Dòng "Bản cuối (lần xuất 4): …final.mp4 / Kiểm tra cuối: đạt." xuất hiện ở giây 5,4 và giữ nguyên suốt 60 s theo dõi. `final.json` revision 4, sha256 trùng với file |
| Hồi quy: Bán hàng 3 video | duyệt hết, dựng lại, cả 3 editor mở được. Bản ghi đủ 12 trường. P2-N5: 3 trên 22 lần duyệt bị huỷ hiệu lực |
| Lồng tiếng, v1, nguồn, quét | mặc định Tắt, QC đạt; v1 trùng từng byte `9afa62bc…`; 11/11 nguồn không đổi; khoá và e-mail 0/0/0 trên 9 929 file |

Cổng trên rc10:
- A: PASS.
- B: PASS.
- C: PASS; riêng A là BLOCKED vì footage không có sản phẩm.
- D: PASS, có giới hạn.
- E: PASS.

## 11. Bảng cổng

| Cổng | Kết quả | Bằng chứng |
|---|---|---|
| A Safety | **PASS** | rc8 và rc9 Kịch bản A; 0 câu READY sai |
| B Human Review | **PASS** (rc9, rc10) | Đủ 7 thao tác, hàng đợi, vị trí, bản ghi hợp đồng. P1-N3, P2-N6, P2-N14 đã sửa và được kiểm trên UI thật |
| C Final Video Quality | **PASS** cho các video Bán hàng và bản xuất Tổng hợp; **BLOCKED** cho A (footage không có sản phẩm, cố ý); **FAIL** cho F (kiểm tra cuối chặn đúng) | §12 |
| D Production Usability | **PASS có giới hạn** | §7, P2-N5 |
| E Regression & Security | **PASS** | dubbing mặc định Tắt; v1 trùng từng byte; schema lạ bị từ chối mà không ghi gì; nguồn không đổi; junction bị từ chối; quét khoá và e-mail = 0 |

## 12. Số liệu (Workstream C)

**Người chấm là agent ("agent-evaluated").** Không có người chấm độc lập. Chất lượng được đánh giá trên **video cuối**, dựa trên 2 khung hình chính cho mỗi câu.

| Lần chạy | Số câu | READY ở bản nháp | Bản nháp (đúng / chấp nhận được / sai) | Sau duyệt (đúng / chấp nhận được / sai) |
|---|---|---|---|---|
| rc9: Espresso ×3, Máy thổi bụi, Hũ tăm | 28 | 0 | 13 / 12 / 3 | **15 / 13 / 0** |
| rc8: Espresso ×3 + Top 5 | 30 | 2 | — | 20 / 10 / 0 |

- **Thao tác trên rc9:** 27 APPROVE, 6 REPLACE, 4 PIN, 4 EXCLUDE, 2 REVIEW_LATER, 2 MISSING, 1 UNDO.
- **P2-N5:** 9 trong 27 lượt duyệt bị huỷ do kế hoạch được lập lại, làm mỗi video tốn thêm một vòng duyệt.
- **Thời gian:** render lại 5,3–7,3 s mỗi video Bán hàng. Tổng hợp mất khoảng 25 s cho 2/8 phần.
- **Thời gian duyệt là thời gian của script tự động, không phải thời gian người làm.** Không có mốc đối chứng với biên tập thủ công, nên không đưa ra kết luận về tiết kiệm thời gian.
- **Bộ dữ liệu:**
  - Espresso, Máy thổi bụi, Hũ tăm: đã chạy.
  - Top 5: đã chạy.
  - Why We Fight (dài): đã chạy, nhưng không xuất được vì kiểm tra cuối chặn.
  - Chương trình đa dạng: chỉ một phần trong C (#1 giữ âm thanh gốc), chưa chạy riêng.

## 13. Lỗi

| ID | Mức | Trạng thái |
|---|---|---|
| P1-N1 (#764) | P1 | Đã sửa (#769), kiểm trên rc8 và rc9 |
| P2-N3 (#766) | P2 | Đã sửa (#770), kiểm trên rc8 |
| P1-N3 | P1 | Đã sửa (#776), kiểm trên rc9 |
| P2-N6 | P2 | Đã sửa (#776), kiểm trên rc9 |
| P2-N14 | P2 | Đã sửa (#778), kiểm trên rc10 |
| P2-N7 | P2 | Đã sửa (#776), kiểm trên rc9 |
| P3-N8 | P3 | Đã sửa (#776), kiểm trên rc9 |
| P3-N9 | P3 | Đã sửa (#778), kiểm trên rc10 |
| P2-N5 | P2 | **Mở.** Một lần ghim, loại hay thay cảnh làm lập lại kế hoạch cả video, nên huỷ các lượt duyệt khác. Không mất an toàn (câu bị gắn cờ, kiểm tra cuối chặn) nhưng tốn thêm vòng duyệt. Cần planner giữ nguyên phân bổ cục bộ, tức là một thay đổi thuật toán |
| P2-N11 | P2 | **Mở.** Phim 20 phút: ngân sách vision 80 khung khiến 16/23 phần ở `signals_only` và 193/214 câu không tìm thấy cảnh; có khung đứng. Kiểm tra cuối chặn đúng |
| P2-9 | P2 | Không tái hiện: A/V −5,4 ms trên phim 20 phút |
| #765 | — | Đã phân tích; issue còn mở trên GitHub (việc đóng cần chủ dự án) |
| P3-N10, P3-N12, P3-N13 | P3 | Mở: ước lượng thời lượng lời bình lệch khoảng 37 %; câu chữ ở Tổng hợp; nút "Tạo 2 video" khi chọn 3 kịch bản |
| P3-N15 | P3 | Mở: khi ghim một cảnh AI *có* thấy khớp, panel vẫn ghi "AI không xác nhận…" cạnh "Hình khớp 100 %". Sai theo hướng thận trọng, không làm lộ điều gì sai |
| P3-N16 | P3 | Mở: cảnh báo ở Tổng hợp ghi "Câu utro.u1" cho câu của phần kết |

## 14. Bất biến

Không đổi:
- giữ nguyên lời lồng tiếng;
- spec Tổng hợp v1 (bản dựng trùng từng byte với rc4–rc7, `9afa62bc…`) và raw v2;
- đường Bán hàng cũ; Mastering Off;
- nguồn gốc (tên file chỉ dùng để xếp hạng, không bao giờ tính là nội dung);
- timebase; checkpoint/resume (dựng lại một phần dùng lại 21–22 trên 23 phần);
- bảo mật Tauri; giám sát worker; bộ render.

Recipe được nâng (product-editorial-v17, compilation-program-v16) để không dùng lại bộ nhớ đệm cũ.

## 15. Bảo mật

- Quét khoá AI và e-mail trên rc8 (2 019 file) và rc9 (9 926 file): **0 / 0 / 0**. Chỉ báo số đếm.
- Không bản ghi nào chứa đường dẫn đầy đủ.
- Junction bị từ chối ở cả Bán hàng và Tổng hợp.

## 16. Giới hạn

- Không có người chấm độc lập, không có mốc thời gian của người làm thật.
- Phim dài chưa dùng được với Assisted (P2-N11).
- Chưa có thao tác đặt mốc thời gian tự do hay nhập thêm footage từ bảng duyệt.
- P2-N5 làm tăng số vòng duyệt.

## 17. Bước tiếp theo

Không bước nào dưới đây là Phase 4, và mỗi bước cần quyết định riêng:

1. Chống huỷ dây chuyền khi lập lại kế hoạch (P2-N5): giữ phân bổ của các câu đã duyệt.
2. Ngân sách vision thích ứng cho phim dài (P2-N11).
3. Thao tác "mốc thời gian tự do" trong "Chọn cảnh khác…".
4. Một vòng có người duyệt thật để đo thời gian và chất lượng độc lập.
