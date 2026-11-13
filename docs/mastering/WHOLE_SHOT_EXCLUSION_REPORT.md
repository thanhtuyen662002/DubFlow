# Loại trừ cả cảnh gốc (#805, Workstream B)

Epic #637 · 2026-11-13 · chỉ thị "DUBFLOW — ASSISTED SAFETY HOTFIX & LONG-FORM RETRIEVAL FEASIBILITY" · PR #811 (merge `205e3ff`) · bản kiểm Windows **1.1.96-rc16** (`205e3ff`, candidate, `publish=false`).

## 1. Kết luận

**Gate B (#805 Exclusion Correctness): PASS**, với một sai lệch kịch bản được ghi rõ (§8).

- Bảng Duyệt cảnh loại được một mảnh chưa dùng, một cảnh thay thế, một mảnh đang dùng, và cả cảnh gốc cùng mọi mảnh của nó.
- Trên Windows rc16: sau "Bỏ cả cảnh gốc" và dựng lại, kế hoạch của phần i1 không còn đoạn nào chồng lên đoạn đã loại (6–12 s của nguồn), video giải mã 0 lỗi và các khung trích ra đều nằm ngoài 6–12 s; 4/4 duyệt được giữ, 0 huỷ; đóng và mở lại app thì loại trừ còn; hoàn tác rồi dựng lại thì kế hoạch về đúng bản nháp.
- Lần kiểm thứ hai (§8b): "Bỏ đoạn này" trên một mảnh đang dùng và trên một mảnh thay thế, rồi "Bỏ cả cảnh gốc", đóng hẳn app, mở lại và lập kế hoạch lại khi loại trừ còn hiệu lực; so từng khung video giải mã ở 5 fps không thấy khung nào gần đoạn bị loại hơn đoạn đang dùng.
- Gate này chỉ nói về loại trừ. Nó không đổi Gate B phim dài (vẫn FAIL).

## 2. Nguyên nhân

Tìm thấy trên Windows rc15, W2 (Top 5, mục i1):
- Vision nhiều khung chia cảnh `i1-s1c1` (0–6 s) thành ba mảnh `f1`, `f2`, `f3`.
- Người dùng muốn bỏ cả cảnh. `f2` và `f3` đang được dùng nên loại được bằng "Bỏ cảnh này".
- `f1` (0–1,95 s) không câu nào dùng, chỉ nằm trong danh sách thay thế. Danh sách đó chỉ có "Dùng cảnh này", nên không loại được từ UI.
- Sau "Dựng lại các phần đã đổi", i1.u1 dùng `f1`: một phần ba đoạn người dùng muốn bỏ vẫn hiện trong video.

Planner đã làm đúng với loại trừ nó nhận được (f2, f3). Lỗi là bảng duyệt không diễn đạt được loại trừ thứ ba, và loại trừ được nhận diện bằng **chuỗi id**, không bằng **đoạn footage**. Một lần chia lại cho id khác thì loại trừ cũ không còn trỏ đúng chỗ.

## 3. Danh tính cảnh gốc và mảnh

| Khái niệm | Định nghĩa |
|---|---|
| Cảnh gốc (parent) | một cú máy từ bước dò cảnh, id `<part>-s<k>c<n>` |
| Mảnh (fragment) | một đoạn con do vision nhiều khung chia ra, id `<parent>f<i>`, có `parent` trong nhật ký |
| Họ (family) | cảnh gốc cùng mọi mảnh của nó trong lần lập kế hoạch này (`families` trong view duyệt) |
| Loại trừ | id được chọn **cộng** đoạn footage nó chỉ tới, ghi là `ranges: {id: [[source, start_ms, end_ms], …]}` (review v3) |

Planner (`align.exclusion`) bỏ ra:
- id được nêu;
- mọi mảnh của một cảnh gốc được nêu là cả cảnh (`whole_shot`);
- mọi cảnh nằm ít nhất một nửa trong một đoạn đã ghi, dù bây giờ mang tên gì;
- mọi mảnh của một lần chia mới cắt ngang đoạn đã ghi mà hiện ít nhất 250 ms của đoạn đó (`SLIVER_MS`), ghi trong `exclusions.widened`.

Một id bị đánh số lại sang footage khác thì **không** bị bỏ; quyết định báo footage đã mất (`shot_gone`). Review v1/v2 (không có `ranges`) đọc như trước.

## 4. Phạm vi loại trừ

- Loại trừ áp dụng cho kế hoạch nó được tạo ra: video Bán hàng, hoặc một phần của Tổng hợp (`review-<part>.json`), ở mọi câu của phần đó. Tooltip nói rõ "ở mọi câu của phần/video này".
- Không có loại trừ chỉ cho một câu: quyết định được lưu dưới câu nơi người dùng bấm, nhưng áp dụng cho mọi câu của phần/video đó.
- Không có loại trừ toàn dự án (qua nhiều phần của Tổng hợp): hợp đồng không có phạm vi này; mỗi phần có `review-<part>.json` riêng.
- Một mảnh vẫn là một mảnh: "Bỏ đoạn này" chỉ bỏ mảnh đó. Cả cảnh chỉ bị bỏ bằng id của cảnh gốc, sau hộp xác nhận.
- Lượt nhìn có mục tiêu và khung nhìn thêm không bao giờ đi vào cảnh đã loại (Bán hàng và Tổng hợp).
- Cảnh gốc đã bỏ cả vẫn nằm trong nhóm được chia lại, để lần sau vẫn nhận ra các mảnh của nó (`usable_pool(..., again=…)`).

## 5. Giao diện (`review_panel.ts`, không thiết kế lại)

- "Bỏ cảnh này" trên mọi cảnh thay thế và trong danh sách "Chọn cảnh khác…" (B2).
- "Bỏ đoạn này" trên một mảnh đang dùng (B3).
- "Bỏ cả cảnh gốc" trên mọi mảnh của một cảnh đã chia (B1). Trước khi gửi, hộp xác nhận liệt kê từng mảnh, thời điểm trong nguồn, và câu nào đang dùng nó. Huỷ thì không gửi gì.
- Cảnh đã loại hiện "Đã loại ở câu N" và chỉ còn nút "Bỏ loại".
- Danh sách "Cảnh đã loại": mỗi loại trừ, câu tạo ra nó, số mảnh nó đang bao, và có phải dùng lại không.
- Shell desktop: id cảnh tới 6 chữ số (nguồn dài bị cắt đánh số từ 100001; trước đây shell từ chối, nên không ghim/loại được và không đọc được ảnh khoá); thêm hành động `include`.

`apps/desktop/test/mastering_panel.test.mjs` (mới, trong `npm test`) gắn bảng duyệt thật lên một DOM nhỏ. Nó bấm "Bỏ cả cảnh gốc" (từ một mảnh thay thế chưa dùng và từ "Mọi cảnh đã nhập"), kiểm tra hộp xác nhận, huỷ, id cảnh gốc được gửi, thẻ đã loại, danh sách bên, hoàn tác và "Bỏ loại". Với "Bỏ cảnh này" và "Bỏ đoạn này" nó chỉ kiểm tra nút có hiện; điều các nút đó gửi đi được kiểm ở phía engine (`WholeShotExclusionTests`). Nó fail trên `75a4983` (cảnh thay thế không có nút bỏ cả cảnh).

## 6. Lưu trữ, dựng lại, xung đột

**Lưu trữ và hoàn tác (B5):**
- Quyết định nằm trong `review-<part>.json` (v3), có revision.
- "Bỏ loại" (`include`) gỡ mọi loại trừ đang bao cảnh đó trong một thay đổi (bản ghi `UNDO`, `detail: include`). Hoàn tác đưa nó trở lại.
- Loại trừ được bỏ sau khi dựng thì Kiểm tra cuối coi câu đó là chưa xong, nên bản cuối bị coi là cũ. Tương tự với hoàn tác, đặt lại, và footage video đang hiện mà một loại trừ mới bao.

**Dựng lại:**
- Chỉ phần có quyết định đổi được dựng lại; các phần khác dùng lại.
- Một ghim mà loại trừ đã bỏ ra bị huỷ với xung đột `excluded` và lời nhắn "Cảnh bạn đã ghim bị loại trừ… hãy chọn cảnh khác hoặc bỏ loại trừ." Nó không còn được tính là lựa chọn của người dùng, nên kiểm tra khẳng định của #802 áp dụng lại.

**Xung đột (B6):**
- Loại trừ làm không còn footage dùng được thì bị từ chối ("Không loại được…").
- Ghim một cảnh câu khác đã loại bị từ chối, và ngược lại.
- Footage planner buộc phải dùng lại (không còn gì khác) được liệt kê trong `exclusions.reused`, có cảnh báo, và câu đó mang xung đột `all_excluded`. Không có lần dùng lại im lặng nào.

## 7. Test (fail trước, pass sau)

| Gate | Test |
|---|---|
| B1 | `test_review.py::WholeShotExclusionTests`; `mastering_panel.test.mjs` (bỏ cả cảnh gốc từ một mảnh chưa dùng, hộp xác nhận, gửi id cảnh gốc) |
| B2 / B3 | `WholeShotExclusionTests` (điều được gửi và kế hoạch); `mastering_panel.test.mjs` chỉ kiểm tra "Bỏ cảnh này" / "Bỏ đoạn này" có hiện trên cảnh thay thế |
| B4 | `WholeShotExclusionTests` (chia mới với id khác, id bị dời); `MergedReviewFindingsTests` (lần chia cắt ngang đoạn đã ghi); `test_frames.py` (cảnh gốc bị bỏ cả vẫn được chia lại) |
| B5 | lưu/mở lại v3, `include`, hoàn tác, v2 vẫn đọc, ranges sai bị từ chối; `test_final_qc.py` (bỏ loại, hoàn tác, đặt lại sau khi dựng là chưa xong) |
| B6 | từ chối, xung đột `all_excluded` và gỡ nó; `test_compilation_raw.py` (nhật ký left_out/widened/reused, cảnh báo dùng lại) |
| Ngân sách vision | `test_product_editorial.py` (FFmpeg thật): sau khi loại cảnh được nhắm, không lượt nhìn nào đi vào nó |
| Gộp với #802 | `test_semantic_claims.py::PinLeftOutTests` |

Hồi quy trên `86a982c`: bộ tổng hợp 11 split và Gate S thật `--replay` giống từng byte với base; `tests/mastering` 385 OK, `tests/product` 50 OK (2 bỏ qua), `tests/compilation` 9 OK, `npm test` và `tsc --noEmit` OK. Không đổi recipe hay phiên bản planner; kế hoạch không có loại trừ giữ nguyên danh tính và nhật ký.

## 8. Windows E2E (W2)

DubFlow.exe 1.1.96-rc16, Top 5, lần chạy `run-18892c740ffb`, bản nháp 255 s, RAM đỉnh 3250 MB. Thao tác qua UI thật, có chứng minh tiến trình CDP; không phải người duyệt.

**Sai lệch kịch bản.** rc16 không chia `i1-s1c1` (0–6 s) như rc15: cảnh đó nguyên, được u1 và u2 dùng. Cảnh được chia là `i1-s1c2`: `f1` 6000–7950, `f2` 7950–10050, `f3` 10050–12000 (u2 dùng f1, f2; f3 chưa dùng). W2 chạy trên `i1-s1c2`, bắt đầu từ mảnh chưa dùng `f3` trong danh sách thay thế. Đây đúng là tình huống của B1/B2, nhưng không phải cảnh 0–6 s.

| Bước | Kết quả |
|---|---|
| "Bỏ cả cảnh gốc" từ `f3` (chưa dùng) | hộp xác nhận liệt kê 3 mảnh, thời điểm và câu dùng; danh sách bên hiện "Câu 2 đã loại cả cảnh gốc i1-s1c2 (3 đoạn)" |
| Quyết định | `review-i1.json`: exclude `[i1-s1c2]`, ranges `i1-s1` 6000–12000, bản ghi EXCLUDE `whole_shot`, ảnh hưởng f1–f3 |
| Dựng lại | 13 s; 1 phần dựng lại, 7 dùng lại; duyệt giữ 4/4, 0 INVALIDATED; kế hoạch hook, intro, i3, i4, i5 giống từng byte; HELD_FRAME 0; không có `all_excluded` |
| Kế hoạch | `plan-i1`: không đoạn nào chồng lên 6000–12000, kể cả khi lần dựng lại chia nguồn khác đi (c2 nguyên, c3 bị chia). Cảnh đã loại trở lại nguyên với đúng id `i1-s1c2`, nên vẫn khớp theo id |
| **Video giải mã** | phần i1 và bản ghép giải mã exit 0, 0 lỗi; 3 khung trích ra (2 s, 7 s, 11 s của phần) ứng với nguồn 0–6 s, ~12–14 s, 18–21,5 s, không khung nào thuộc 6–12 s. Đây là lấy mẫu, cộng với kế hoạch ở dòng trên; không phải so từng khung |
| Đóng / mở lại app | loại trừ còn ("Câu 2 đã loại cảnh i1-s1c2") |
| Hoàn tác, dựng lại | revision 2, bản ghi UNDO, danh sách exclude rỗng; dựng lại 12,2 s, chỉ i1 (1/7); `plan-i1` về đúng bản nháp (u2 dùng c2f1, c2f2) |
| Duyệt hết, xuất | "Kiểm tra cuối: đạt"; bản cuối sha256 `9e3d5fb4…`, giải mã 0 lỗi |

Còn mở: P3-N21 (dòng hàng đợi không cập nhật cho tới khi xuất); sau một lần chia lại, dòng trong "Cảnh đã loại" mất "(3 đoạn)" (P3, #818). Cảnh 0–6 s của kịch bản gốc: NOT RUN (rc16 không chia nó).

### 8b. Bổ sung trên Windows (lần kiểm thứ hai, cùng lần chạy W2)

Bấm thật trong bảng duyệt của rc16 (tự động hoá, không phải người duyệt; chứng minh tiến trình ở hai lần mở app mới). Không thay bước UI nào bằng lời gọi IPC. Trạng thái đầu: i1.u2 dùng c1, c2f1 (6000–7950) và c2f2 (7950–10050); mọi câu đã duyệt.

| Bước | Kết quả |
|---|---|
| **B3** "Bỏ đoạn này" trên c2f1 (đang dùng) | không có hộp xác nhận; danh sách bên "Câu 2 đã loại cảnh i1-s1c2f1"; dựng lại 12,9 s, "dựng 1 phần, dùng lại 7"; `plan-i1` không đoạn nào ở 6000–7950, mảnh anh em f2 vẫn được dùng; duyệt của i1.u2 được thay bằng quyết định, 0 INVALIDATED. Hoàn tác + dựng lại 13,1 s: về đúng bản nháp |
| **B2** "Bỏ đoạn này" trên mảnh thay thế chưa dùng c3f1 (12000–13950) | dựng lại 8,9 s, "dựng 0 phần, dùng lại 8" (không dùng nên kế hoạch không đổi); 0 đoạn ở 12000–13950. Hoàn tác + dựng lại 8,5 s |
| **B5** "Bỏ cả cảnh gốc" trên c2, dựng lại | hộp xác nhận liệt kê 3 mảnh; 12,7 s, chỉ i1; 0 đoạn ở 6000–12000. Duyệt của i1.u2 bị huỷ có lý do: "Cảnh bạn đã duyệt không còn trong nguồn; hãy chọn lại." (các mảnh nó đã duyệt bị loại); phần khác 0 huỷ |
| Đóng hẳn app (0 tiến trình), mở lại | loại trừ còn ("Câu 1 đã loại cảnh i1-s1c2", "Bỏ loại"); i1.u2 "Nghiêm trọng · Cần chọn lại" |
| Lập kế hoạch lại khi loại trừ còn hiệu lực | "Bỏ đoạn này" trên c3f3 chưa dùng (16050–18000), dựng lại 11,8 s (chỉ i1); `plan-i1.json` và `editorial-i1.json` được ghi lại, nên planner đã chạy; `edits.exclude` = [c2, c3f3]; 0 đoạn ở 6000–12000 và 0 ở 16050–18000 |
| Hoàn tác ×2 + dựng lại | 12,0 s (i1); về bản nháp; cả hai duyệt của i1 hiện "Bạn đã duyệt" |
| **Video giải mã** ở trạng thái lập kế hoạch lại | part-i1 và các nguồn giải mã exit 0, 0 lỗi. So **từng khung** ở 5 fps bằng dHash 64 bit: 65 khung của phần, 30 khung của nguồn bị loại (6–12 s), 18 khung của đoạn nguồn đang dùng (12–15,49 s, đối chứng). Cắt giữa 4:3 (phần là 16:9 có nền, nguồn 4:3): khoảng cách nhỏ nhất tới đoạn bị loại là 4, chỉ 1 khung ≤ 6; đối chứng: nhỏ nhất 0, trung vị 1, 18 khung ≤ 6. Khung gần duy nhất là khung đầu (t = 6,0 s) của đoạn bắt đầu ở nguồn 12,0 s: cách đoạn bị loại 4 nhưng cách đoạn đang dùng 3 (cùng một cú quay liền). Không khung nào của phần gần đoạn bị loại hơn đoạn đang dùng |

Quan sát (P3, #818): lần dựng lại đầu sau "Bỏ cả cảnh gốc" bỏ mất khoảnh khắc có tiếng gốc 18000–21500 của i1 (phần dài 9490 ms thay vì 12990 ms); lần lập kế hoạch lại sau đó đưa nó trở lại. Việc chọn khoảnh khắc phụ thuộc lịch sử chỉnh sửa.

**Kiểm trên Windows và bằng test:**

| | Windows rc16 | Test đơn vị |
|---|---|---|
| B1 mảnh chưa dùng, cả cảnh gốc | ✓ (f3 → cả i1-s1c2) | ✓ |
| B2 cảnh thay thế | ✓ ("Bỏ cả cảnh gốc" từ mảnh thay thế f3; "Bỏ đoạn này" trên mảnh thay thế c3f1, §8b) | ✓ |
| B3 một mảnh | ✓ (c2f1, f2 vẫn dùng, §8b) | ✓ |
| B4 chia lại với id khác | ✓ một phần (lần dựng lại chia nguồn khác đi, nhưng cảnh đã loại trở lại với đúng id nên vẫn khớp theo id; trường hợp footage bị loại mang id khác chỉ được kiểm bằng test đơn vị) | ✓ |
| B5 lưu, mở lại, hoàn tác | ✓ (đóng hẳn, mở lại, lập kế hoạch lại khi loại trừ còn hiệu lực, so từng khung video giải mã, hoàn tác, §8b) | ✓ |
| B6 xung đột | — (không gặp trên W2; huỷ duyệt có lý do khi các mảnh đã duyệt bị loại, §8b) | ✓ |

## 9. Hồi quy cũ (W4) và bảo mật (W5)

Trên DubFlow.exe rc16 (thư mục dữ liệu riêng, tự động hoá CDP, không phải người duyệt).

**W4 hồi quy cũ: PASS.**

| Mục | Kết quả |
|---|---|
| Bán hàng Legacy (mastering "Tắt") | COMPLETED 13 s; spec không có khoá mastering, không ghi file biên tập/kế hoạch; video h264 1080×1920 30 fps + aac 48 kHz, 5,333 / 5,330 s, giải mã 0 lỗi |
| Bán hàng có mastering (Espresso) | bản nháp 135 s; Ghim / Bỏ / Thay 8,3 / 7,8 / 7,8 s; giữ 4/4 duyệt, 0 INVALIDATED, chỉ câu được sửa đổi |
| Tổng hợp spec v1 | xuất trùng từng byte với rc13–rc15 (sha256 `9afa62bc…c6d3`) |
| Tổng hợp thô v2 | W2 (§8) |
| Lồng tiếng, mastering "Tắt" (mặc định) | "Hoàn tất sau 1:23"; `final_vi.mp4` h264 1024×576 + aac, 30,000 / 29,99998 s; giải mã 0 lỗi; QC đạt, 0 lỗi TTS, 0 lỗi trộn |
| Dựng lại cục bộ | W2: 1 phần dựng, 7 dùng lại |
| Ghim / loại trừ | như trên và §8 |
| Bản cuối có revision | ghim i3.u2 rồi dựng lại: 16,3 s, "dựng 0 phần, dùng lại 8"; bản cuối cũ được đánh dấu "không còn là bản hiện tại"; "Xuất bản cuối lại" cho "Bản cuối (lần xuất 2) … Kiểm tra cuối: đạt." |
| Đồng bộ A/V | sync_probe W2 7/7 chỗ nối, tệ nhất 33,3 ms; W3 21/21, 0 ms |
| Huỷ / tiếp tục | huỷ ở 132 s (3 phần xong): CANCELLED, "Có thể dùng lại các đoạn đã xong."; chạy lại 56,6 s, "dựng 5 phần, dùng lại 3"; COMPLETED chỉ sau lần chạy thứ hai; mọi phần giải mã 0 lỗi. Lần huỷ ở W1 cho CANCELLED, 0 video |

**W5 bảo mật: PASS, có một phát hiện P2.**

| Mục | Kết quả |
|---|---|
| IPC Tauri | 8/8 lời gọi sai bị từ chối (câu sai dạng, thao tác lạ, id cảnh 7 chữ số, thư mục ngoài gốc, `..`, include rỗng, số video 0); 99 file băm trước/sau: 0 đổi, 0 thêm, 0 xoá |
| Chứa nguồn | Tổng hợp từ chối file nguồn là symlink. **Bán hàng nhận file nguồn là symlink** và dựng nó (chỉ đọc, đích không đổi): P2, #816. Nguồn nằm trong thư mục junction được nhận ở cả hai luồng (nguồn không có gốc cho phép theo thiết kế; nguồn không đổi) |
| Thư mục kết quả là junction | bị từ chối ở cả hai luồng; đích junction vẫn rỗng |
| Chuỗi filter/shell trong tên và kịch bản Bán hàng | hiện nguyên văn trong tiêu đề và phụ đề; giải mã 0 lỗi; 0 tiến trình lạ |
| Không sửa nguồn | 25/25 nguồn không đổi |
| Schema | review schema v2 của rc15 đọc được và không bị ghi lại; v99 bị từ chối ở cả view và decide, không bị ghi đè. Thông báo bằng tiếng Anh và nói v1/v2 (P3, #818) |
| Che dữ liệu | app đã đóng: 3 567 file, 1,87 GiB: claude 0 / gemini 0 / email 0; đường dẫn hồ sơ người dùng 0; JSON duyệt có đường dẫn tuyệt đối 0/11. Lần quét trước đó 2 917 file: 0 / 0 / 0 |

## 10. Gate B

| Điều kiện | Kết quả |
|---|---|
| 1. Loại được cảnh thay thế chưa dùng qua UI | ✓ |
| 2. Loại được cả cảnh gốc | ✓ |
| 3. Planner tôn trọng mọi mảnh trong phạm vi | ✓ (kế hoạch và video giải mã) |
| 4. Không xuất hiện lại trái quyết định | ✓ |
| 5. Không mất duyệt không liên quan | ✓ (4/4, 0 INVALIDATED) |
| 6. Lưu trữ và hoàn tác | ✓ |
| 7. Windows Desktop E2E đạt | ✓ (W2, với sai lệch ở §8) |
| 8. Bán hàng / Tổng hợp cũ không hồi quy | ✓ (W4, §9) |

**Gate B (#805): PASS.**
