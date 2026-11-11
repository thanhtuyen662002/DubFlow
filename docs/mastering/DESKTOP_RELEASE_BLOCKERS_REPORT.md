# DubFlow — Báo cáo ba lỗi chặn phát hành trên desktop (P2-5, P2-6, P2-14)

Epic #637 · chỉ thị "DUBFLOW — GATE S FINAL CLOSURE" (2026-11-11 21:56) · bản kiểm: 1.1.96-rc7 (candidate, `publish=false`, commit `7acd262`).

**Kết luận Release Gate: FAIL.**

Cả ba lỗi P2-5, P2-6 và P2-14 đã được sửa và kiểm qua UI thật của DubFlow.exe rc7 (D1, D2, D3a và D3b về nguồn gốc bằng chứng). Release Gate vẫn FAIL vì D3 yêu cầu "review flags correct", và mục này không đạt: trong D3b, câu đầu của Bán hàng được READY trên cảnh không liên quan (P1-N1, #764).

Bằng chứng Windows: [gate-s-real/rc7/report-rc7.md](gate-s-real/rc7/report-rc7.md).

## 1. P2-5 — Ghim/duyệt bị INVALIDATED sai (#754 → #755)

**Lỗi.** Người dùng ghim một cảnh rồi bấm Duyệt cảnh này. Sau khi Dựng lại, câu đó bị chuyển sang INVALIDATED, dù planner đã đặt đúng cảnh được ghim.

**Nguyên nhân.** `review.reconcile` so sánh danh sách cảnh mới với danh sách cảnh lúc duyệt và đòi hai danh sách giống hệt nhau. Nó không xét tới trường hợp:
- cảnh được ghim là thứ mà lần dựng lại vừa áp dụng;
- planner thêm cảnh lấp sau cảnh ghim.

**Sửa (`engine/dubflow/mastering/review.py`).**
- Khi duyệt một câu có cảnh ghim chưa được áp dụng, phần duyệt sẽ duyệt cả cảnh ghim đó.
- Lúc reconcile, phần duyệt được giữ khi plan bắt đầu bằng các cảnh ghim theo đúng thứ tự. Planner được phép thêm cảnh lấp phía sau.
- Duyệt cũ có kèm ghim cũng theo đúng quy tắc này.
- INVALIDATED chỉ xảy ra khi có lý do thật:
  - cảnh không còn (nguồn đổi hoặc bị xoá);
  - câu không còn;
  - plan chiếu thứ khác hoặc bỏ cảnh ghim khỏi đầu câu;
  - người dùng ghim cảnh mới.

**Test.** Mọi test dưới đây đều lỗi trước khi sửa và qua sau khi sửa.
- `test_review`: duyệt kèm ghim vẫn còn sau dựng lại, sau lưu/mở lại và sau lần dựng thứ hai. Có thêm các trường hợp INVALIDATED thật.
- Job Bán hàng: ghim + duyệt, dựng lại một video, đọc lại file review → USER_APPROVED.
- Tổng hợp thô với FFmpeg thật (`test_compilation_raw`): CLI desktop `decide pin` + `decide approve`, dựng lại → USER_APPROVED, không có cảnh báo "đã đổi sau khi bạn duyệt".

**Windows UI (D1): PASS** (UI qua CDP, rc7 §2).
- Đổi cảnh, ghim, duyệt rồi dựng lại: chỉ 1 phần được dựng lại (10 s). Plan dùng cảnh ghim, panel hiện "Bạn đã duyệt / Đang dùng (đã ghim)".
- Đóng rồi mở lại app: ghim và duyệt vẫn còn.
- Sửa nguồn (mục 1 bắt đầu từ 7 s): duyệt bị huỷ với lý do "Cảnh đã chọn không còn trong nguồn…", panel chuyển sang "Cần chọn lại".

## 2. P2-6 — "Bản cuối" trỏ tới kết quả cũ sau khi dựng lại (#756 → #757)

**Lỗi.** Người dùng xuất bản cuối, rồi sửa chương trình và dựng lại bản nháp. Màn hình vẫn hiện "Bản cuối" như thể đó là bản hiện hành.

**Nguyên nhân.** Bản cuối không có danh tính (revision hay hash). Trạng thái "đã có bản cuối" chỉ dựa vào việc file có tồn tại.

**Sửa.**
- `apps/desktop/src-tauri/src/compilation_raw.rs`:
  - Mỗi lần xuất bản cuối ghi `.dubflow-work/final.json`, gồm: revision tăng dần, sha256 của file cuối, và danh tính của bản nháp đã dùng (độ dài, mtime tính bằng ns).
  - `compilation_raw_open` trả về `final_state`, nhận một trong bốn giá trị:
    - `current`;
    - `stale` kèm lý do `rerendered` (bản nháp đã đổi);
    - `rendering` (đang dựng);
    - `unknown` (bản xuất cũ, chưa có record).
- `raw_model.ts` / `raw_view.ts`: `finalText()` hiển thị "Bản cuối cũ … hãy xuất bản cuối lại" khi stale. Bản cũ không bị xoá và không bị coi là bản hiện hành.

**Test.**
- Rust `final_export_needs_every_part_reviewed_or_accepted`: revision 1 + sha256; mở lại → current; bản nháp ghi lại → stale/rerendered; RUNNING → rendering; xuất lại → revision 2, hash mới; record bị xoá → unknown.
- Desktop: `compilation_raw.test.mjs` với các trường hợp finalText/finalState.

**Windows UI (D2): PASS** (UI, rc7 §3).
- Xuất lần 1: revision 1, sha `0b8c427c…`.
- Sửa rồi dựng lại: UI báo "Bản cuối cũ… (chương trình đã được dựng lại sau lần xuất này)". Vẫn đúng sau khi mở lại app.
- Xuất lại: revision 2, sha `9f7f212c…`, khớp `final.mp4`.

## 3. P2-14 — AI thất bại nhưng plan vẫn `mode: content` nhờ tên file (#752 → #753)

**Lỗi.** Bản rc5 trên Windows: mọi lời gọi AI đều lỗi 401, nhưng mọi phần của Tổng hợp thô vẫn ghi `mode: content`, vì từ khoá trong tên file được tính là bằng chứng nội dung.

**Nguyên nhân.** `Evidence.content` và độ tin của khớp tính mọi nguồn gốc như nhau, kể cả `metadata` (tên file).

**Sửa.**
- `evidence.py`:
  - `CONTENT_ORIGINS = {user, transcript, vision}`.
  - Phân loại nguồn gốc: `vision`, `transcript`, `user_note`, `ocr`, `filename_metadata`, `signal`.
  - `provenance_counts()` đếm theo từng loại.
- `align.py`: độ tin của mảnh chỉ dùng độ tin và độ liên quan từ nguồn nội dung. Tên file vẫn được dùng để xếp hạng cảnh, nhưng không bao giờ nâng cảnh lên READY.
- `review.py`:
  - `content_mode(log)`: log cũ ghi `content` nhưng chỉ có tên file sẽ được đọc là `signals_only`, và phán quyết được tính lại.
  - `view()` trả về `provenance`.
- Planner ghi `provenance` vào decision log. Contract `editorial-v1.json` thêm trường tuỳ chọn nên log cũ vẫn hợp lệ. Cảnh báo ghi rõ "tên file không tính".
- Desktop: bảng Duyệt cảnh tóm tắt nguồn gốc bằng chứng (`provenanceText`).

**Test.** `tests/mastering/test_provenance.py` gồm 7 trường hợp của chỉ thị (vision OK, vision lỗi, không có khoá, ghi chú người dùng, chỉ tên file, tên file sai nội dung, transcript) cộng một log cũ. 8/8 lỗi trước khi sửa, qua sau khi sửa.

**Windows (D3).** Engine chạy theo host integration (cùng argv/env với desktop), kết quả hiển thị trên UI (rc7 §4).
- **(a) Khoá sai, chỉ đặt trong env con:** Tổng hợp thô và Bán hàng đều `signals_only`, provenance vision 0, 0 câu READY. UI ghi "Chưa có bằng chứng nội dung: không câu nào được coi là sẵn sàng", kèm cảnh báo 401. **PASS.**
- **(b) Tên file sai nội dung, vision hoạt động:** video domino 3D đổi tên thành tên nồi chiên. Tên file không làm câu nào READY: **PASS về nguồn gốc bằng chứng**. Nhưng câu 1 được READY như câu mở đầu "không cần cảnh riêng" trên cảnh mà Vision ghi "Không thấy sản phẩm" → **P1-N1 (#764)**. Lỗi này nằm ở quy tắc câu mở đầu, không phải ở tên file. D3 vì thế không đạt hoàn toàn.

## 4. Lỗi phát sinh trực tiếp được sửa (#760 → #761)

Hai lỗi lộ ra khi chạy thật:
- **v6 (Bilibili, tốc độ khung hình thay đổi):** mọi plan đều hỏng vì fps 10294000/343133 vượt giới hạn của contract. Sửa trong `analysis.probe` bằng `limit_denominator(1001)`.
- **Hết hạn mức vision giữa chừng:** log từng phần không có cảnh báo nào (thất bại im lặng). Giờ báo cáo vision và log từng phần đều ghi cảnh báo của describer.

## 5. Code đã thay đổi

| PR | File chính |
|---|---|
| #753 | engine/dubflow/mastering/{evidence,align,review}.py, planners/{product,compilation}.py, contracts/mastering/editorial-v1.json, apps/desktop/src/features/mastering/{model,review_panel}.ts |
| #755 | engine/dubflow/mastering/review.py |
| #757 | apps/desktop/src-tauri/src/compilation_raw.rs, apps/desktop/src/features/compilation/{raw_model,raw_view}.ts |
| #761 | engine/dubflow/mastering/analysis.py, engine/dubflow/compilation/raw.py |

## 6. Persistence và project revisions

- Quyết định duyệt và ghim được lưu trong file review của từng plan, và đối chiếu với plan mới sau mỗi lần dựng (P2-5).
- Bản cuối có revision và sha256 trong `final.json`. Trạng thái hiển thị được tính từ record này mỗi lần mở dự án, không giữ trong bộ nhớ (P2-6).

## 7. Security và compatibility

- Không có thay đổi nào ở kiểm tra đường dẫn hay IPC.
- Log cũ (không có `provenance`, không có `signals`) vẫn hợp lệ theo contract và được đọc lại đúng (test old-log của P2-14).
- Dự án có bản cuối xuất trước bản sửa P2-6 hiện `unknown`, không bị coi là current.
- Spec v1 của Tổng hợp không đổi.
- **Windows D4 (legacy): PASS.**
  - Dự án v1 dựng qua UI cho `9afa62bc…`, trùng từng byte với rc4/5/6. argv ffmpeg/ffprobe giống rc6 24/24.
  - Định dạng 9 bị từ chối với thông báo rõ, 34/34 file không đổi.
  - Bán hàng (Cân bằng và Tắt) và lồng tiếng 30 s chạy qua UI, ffprobe đầu ra hợp lệ.
- **Windows D5 (security): PASS** (UI + quét).
  - Thư mục xuất là junction, hoặc đã bị xoá: bị từ chối, không ghi gì ra ngoài.
  - Huỷ job rồi mở lại: dựng tiếp được ngay lần đầu.
  - Nguồn 7/7 không đổi.
  - Quét 8 759 file (data dir gồm hồ sơ WebView2, output, log): khoá Claude 0, khoá Gemini 0, e-mail 0. Bộ quét có tự kiểm tra bằng canary.
  - Kiểm thử IPC chỉ chạy trong CI (Rust unit test), không chạy lại trên Windows.
- **Đính chính bằng chứng cũ (E-1):** bộ quét của rc5/rc6 giải mã khoá Claude theo UTF-16, trong khi desktop lưu UTF-8. Vì vậy kết quả "0 lần trùng khoá Claude" của rc5/rc6 không có giá trị. Kết quả với khoá Gemini của rc5/rc6 vẫn đúng. Lần quét rc7 đã dùng bộ quét sửa lại: 0.

## 8. Open issues

| ID | Mức | Nội dung | Issue |
|---|---|---|---|
| P1-N1 | P1 | Câu đầu của Bán hàng READY như hook trên cảnh không liên quan | #764 |
| P1-N2 | P1 (đã gắn cờ) | Ba bộ hồi quy thật kém hơn code rc6 trên cùng bằng chứng (P2, B5, B1) | #765 |
| P2-N3 | P2 | Engine chết khi `status.json` đang bị mở (WinError 5 ở `os.replace`, không thử lại). stderr bị bỏ nên không có dấu vết | #766 |
| P2-N2 | P2 | CI: installed smoke một lần bị "Permission denied" khi đọc job JSON, lần chạy lại đạt. Có thể cùng loại với P2-N3 | #766 |
| P2-9 | P2 | Why We Fight lệch A/V −41,3 ms ở bước ghép | cũ |
| P2-10 | P2 | Thông báo thư mục xuất là junction hoặc đã mất quá mơ hồ | cũ |
| P2-7 | P2 | Sau khi huỷ, trang vẫn ghi "Sẵn sàng xuất bản cuối." (nút xuất đã bị khoá đúng) | cũ |
| P2-4 | P2 | Trước khi dựng lại, cảnh đã chọn/bỏ vẫn hiện "Đang dùng" cũ | cũ |
| P3-N4 | P3 | Hộp cảnh báo ghi "Không có cảnh báo." trong khi run có cảnh báo invalidation | – |

Không có P0.

## 9. Release Gate

| Điều kiện (chỉ thị §16) | Kết quả |
|---|---|
| Pin/approval không bị invalidated sai | ✓ D1 |
| Bản cuối không còn chỉ nhầm revision | ✓ D2 |
| Evidence provenance đúng khi AI thất bại | ✓ D3a |
| UI state khớp persisted state | ✓ D1, D2 (P2-4, P3-N4 là chi tiết hiển thị) |
| Legacy workflows không regression | ✓ D4 |
| Windows E2E đạt | ✗ D3 "review flags correct" không đạt (P1-N1) |
| Bảo mật đạt | ✓ D5 (IPC chỉ kiểm trong CI) |

**Release Gate: FAIL**, vì một P1 còn mở trong D3. Ba bản sửa P2-5, P2-6, P2-14 tự chúng đã đạt và được giữ lại.
