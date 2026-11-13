# Release Safety Closure: Báo cáo đóng ba lỗi an toàn phát hành (#821, #816, #817)

Epic #637 · 2026-11-13 · chỉ thị "DUBFLOW — RELEASE SAFETY CLOSURE & TARGETED EVIDENCE ACQUISITION ADR" (Stage 1, §24.A) · mốc của chỉ thị: main `74c16d0` (sau PR #800) · main sau khi merge bốn PR của Stage 1: `1687771` · bản tái hiện Windows: **1.1.96-rc17** và bằng chứng W3 đã lưu của **1.1.96-rc16** · bản nghiệm thu Windows: **1.1.96-rc18** (release run 43454280200, kênh candidate; Windows E2E W1–W4 đã chạy ngày 2026-11-13 trên DubFlow.exe của rc18, điều khiển bằng tự động hoá CDP, không phải người duyệt).

> **Bản cuối, sau Windows E2E trên rc18.** Mọi dòng PASS ở §12 dựa trên lượt chạy DubFlow.exe của bản candidate rc18 (§11) hoặc trên tệp do chính lượt chạy đó tạo ra (quét giải mã, quét khoá, đọc spec và review JSON); bằng chứng phụ ngoài app (lần phát lại run W3 của rc16 trên engine của rc18) không được tính là PASS. Các lượt chạy được điều khiển bằng tự động hoá CDP; đây **không** phải người duyệt. Dòng nào không chạy thật trên DubFlow.exe rc18 được ghi BLOCKED, NOT VERIFIED hoặc N/A kèm lý do, không bao giờ PASS. Đường dẫn bằng chứng trên PC Windows được ghi tương đối: `rc18\…` nằm dưới `_sandbox\assisted\rc18`, `stage1\…` nằm dưới `_sandbox\assisted\stage1`; thư mục hồ sơ người dùng, nếu cần nhắc, được ghi là `%USERPROFILE%`. Các phép kiểm "trước merge" (unit test, CI trên từng nhánh, chạy thử trên PC Windows bằng mã nguồn của nhánh) được ghi rõ là **trước merge**; chúng không phải nghiệm thu và không thay cho Windows E2E trên DubFlow.exe (chỉ thị §12, §22).

## 1. Kết luận

**Stage 1 (Release Safety Closure): PASS trên bản candidate 1.1.96-rc18 cho phạm vi chỉ thị, kèm giới hạn nghiệm thu.** Windows E2E trong phạm vi chỉ thị (W1: B2 có Mastering, input gây overshoot, Mastering tắt, với phép đo độc lập sau AAC đối chiếu `qc_report.json` và thẻ Hậu kỳ; W2: S1–S8; W3: Kiểm tra cuối chặn xuất khi còn khẳng định nghiêm trọng chưa xử lý; W4: mười mục hồi quy) là PASS trên DubFlow.exe rc18, trừ các mục chưa kiểm hoặc N/A trong phạm vi: S4 reparse point không phải name-surrogate (N/A), W2.4b không tạo run khi nguồn bị từ chối (NOT VERIFIED), W3.5b đuôi "và N câu khác" trên DubFlow.exe (NOT VERIFIED) và W4.8b các dòng READY đúng của #802 vẫn READY trên DubFlow.exe (NOT VERIFIED, chỉ có lần phát lại ngoài app). Các phần nhỏ hơn chưa kiểm, như đầu ra Mastering tắt so với rc17 và phép đo độc lập của W1d, cũng nằm ở §17.1. Các ca thêm ngoài chỉ thị: W1d' fallback B1 BLOCKED (chỉ được phủ bằng unit test), W1e render lại Lồng tiếng với đầu vào không đổi NOT VERIFIED, W1f hủy trong lúc Mastering rồi tiếp tục BLOCKED. Không có lỗi P0 hay P1 mới được ghi nhận; riêng W1f, biên bản không ghi lệnh hủy có tới engine hay không (§16.6, phát hiện 1). Danh sách đủ ở §17.1.

| Cổng (chỉ thị §13, §25) | Kết quả |
|---|---|
| #821 AAC True Peak & QC | PASS. Video của ca rc17 (`dub-drama-30s.mp4`, W1a, mix mới): không vượt trần, tức mức vượt của rc17 không tái hiện; đo độc lập −2,40 dBTP, trùng `qc_report.json` và thẻ Hậu kỳ. Input tổng hợp gây overshoot (`dub-drama-30s-bursts.mp4`, W1b): lần thử AAC đầu +0,42 dBTP, giảm 2,22 dB, bản xuất −1,65 dBTP, `corrected`. Mastering tắt không có khối `mastering`; đầu ra Mastering tắt không được so với rc17 (NOT VERIFIED). Phép đo độc lập dùng cùng FFmpeg và cùng bộ đo loudnorm với ứng dụng (§5.5, §6.3, §12.1). B1 chưa chạy trên Windows (§17.1) |
| #816 Product Source Security | PASS. S1–S3 và S5–S8 PASS qua UI và IPC của DubFlow.exe; S4 một phần: name-surrogate bị từ chối và hard link được nhận (PASS), reparse point không phải name-surrogate (OneDrive Files-On-Demand, dedup) N/A. 15/15 phép thử IPC bị từ chối; SHA-256 nguồn không đổi. W2.4b (không tạo run khi nguồn bị từ chối): NOT VERIFIED (§8.3, §12.2) |
| #817 Factual Claim Safety | PASS. Kiểm tra cuối chặn xuất khi còn 6 câu nghiêm trọng, kể cả khi đã tích chấp nhận câu chưa duyệt ở mọi phần; chỉ xuất được sau khi từng câu được xử lý (§12.3). Đuôi "và N câu khác" chỉ thấy ngoài app (W3.5b, NOT VERIFIED trên DubFlow.exe) |
| Windows E2E W1–W4 | PASS cho phạm vi chỉ thị, chạy bằng tự động hoá CDP (không phải người duyệt), trừ các mục chưa kiểm hoặc N/A trong phạm vi (S4 không phải name-surrogate, W2.4b, W3.5b, W4.8b) và các ca thêm W1d', W1f BLOCKED, W1e NOT VERIFIED; danh sách đủ ở §17.1 |
| Security | PASS. W2 (S4 một phần, OneDrive N/A), 15/15 phép thử IPC; quét khoá trên 2 083 tệp (1,43 GiB) ở các thư mục nêu ở W4.10: khoá AI 0, e-mail 0, đường dẫn hồ sơ người dùng 0. Các nơi ghi khác của ứng dụng không được biên bản nêu là đã quét (NOT VERIFIED, §17.1). Thư mục `mastering` là junction: NOT VERIFIED trên rc18, chỉ có CI Windows (§13, §17.1) |
| Legacy regression | PASS. Bán hàng, Tổng hợp raw v2, Tổng hợp v1, Đóng gói, ảnh nền thư viện và mục "Dubbing Off" của chỉ thị đều chạy được (§12.4). "Dubbing Off" được ánh xạ tới W1c (Lồng tiếng với Mastering tắt) và W1d (Lồng tiếng tắt, chỉ Vietsub); xem W4.4 |

Tại thời điểm chốt báo cáo:

- Cả ba lỗi đã được tái hiện trên Windows (rc17, và bằng chứng W3 đã lưu của rc16) **trước khi** sửa (§4, §8, §9).
- Ba bản sửa đã được merge vào main qua bốn PR, mỗi issue một nhánh (chỉ thị §23): #822 và #825 cho #821, #823 cho #816, #824 cho #817. main hiện ở `1687771` (§2, §3).
- Trước khi build candidate, một lượt rà soát chéo đối kháng trên bản ghép ba nhánh đã tìm ra hai lớp xác nhận sai của #817, một chỗ hở của #816 và các điểm cần gia cố trên Windows của #821. Tất cả đã được sửa trước candidate (§14).
- Bản candidate **1.1.96-rc18** đã được build (§11) và được nghiệm thu trên Windows 11 bằng tự động hoá CDP (§12). Lượt nghiệm thu ghi 10 phát hiện, mức cao nhất là P2; không có P0 hay P1 được ghi nhận. Với W1f, biên bản không ghi lệnh hủy có tới engine hay không, nên khả năng một lệnh hủy bị bỏ qua chưa được loại trừ (§16.5, §16.6, §17.1).
- Stage 2 (#815) chưa bắt đầu tại thời điểm chốt báo cáo. Theo chỉ thị, Stage 2 được mở khi Stage 1 đạt; Stage 2 chỉ gồm audit, thí nghiệm offline E1–E5, ADR ở trạng thái PROPOSED và báo cáo khả thi, không thay đổi planner production.
- Không phát hành stable: Stage 1 đạt không phải là bản phát hành ổn định, và rc18 có `production_qualified: false`.
## 2. HEAD

| Mục | Commit | Ghi chú |
|---|---|---|
| main (mốc của chỉ thị) | `74c16d0` | Merge PR #800 (#715) |
| Merge PR #822 (#821) | `d894ad4` | nhánh `agent/issue-821-aac-true-peak` @ `93db95f` (8 commit trên mốc: `2e03808` (claim), `26fcfec`, `96c70a5`, `508ebac`, `4bb1a6f`, `47e926e`, `8c1e0ef`, `93db95f`) |
| Merge PR #823 (#816) | `afbcdec` | nhánh `agent/issue-816-source-links` @ `4f95d1d` (`7dfc58c` (claim), `53ad7be`, `6f24bf5`, `2d9a69b`, `9ef3f51`, `49a857f`, merge main `8cbfa18`, `4f95d1d`) |
| Merge PR #825 (#821, phần bổ sung sau rà soát) | `45d2581` | cùng nhánh #821 @ `760f8c5` (`88c9c77`, `c47b4d7`, merge main `2a22036`, merge main `760f8c5`) |
| Merge PR #824 (#817) | `1687771` | nhánh `agent/issue-817-claim-status` @ `b87fb91` (`aa209c2` (claim), `900c7d0`, `13d23ec`, `1f9a2d4`, `963fee1`, merge main `29ace56`, merge main `b87fb91`) |
| **main hiện tại** | `1687771341792874c55cc532d1bc593c8ac23e95` | tree `3de03596f4c7089d9198e8888ecfba80b5dbbcdf` |
| Bản ghép cục bộ `agent/stage1-integration` (đã cũ) | `71573d7`, tree `6270b80c524f56f1ce4e95a6d31fc106a3adb26f` | mốc `74c16d0` + #821@`8c1e0ef` + #816@`2d9a69b` + #817@`13d23ec`. **Chỉ để kiểm trước merge**: không đẩy, không tag, không phải nguồn của candidate. Không chứa `93db95f` và không chứa commit sửa nào sau rà soát, nên tree của main **khác** `6270b80…` (đúng như dự kiến sau các lượt sửa) |
| Commit của bản candidate rc18 | `1687771341792874c55cc532d1bc593c8ac23e95` | trùng main hiện tại: release run 43454280200 ghi head_sha này, và `release-evidence.json` trong gói ghi source_sha `1687771…` (§11) |

So với mốc `74c16d0`, main thay đổi 32 tệp, +4540 −125. Ba issue vẫn sửa các tệp mã không trùng nhau, trừ `pr-desktop-editor-windows.yml` (cả #816 và #821 thêm bước, ở hai chỗ khác nhau).
## 3. PR và Issue

| Issue | PR | Nhánh @ head cuối | Phạm vi | CI bắt buộc trên head cuối (trước merge) | Merge |
|---|---|---|---|---|---|
| #821 AAC True Peak vượt trần nhưng QC vẫn PASS | #822 | `agent/issue-821-aac-true-peak` @ `93db95f` | `production_job.py`, `planners/dubbing.py`, `qc.ts`, `mastering.rs`, ADR-0055, workflow Windows, test | không có run id trên `93db95f` trong hồ sơ; `93db95f` nằm trong cây `760f8c5` của PR #825, có PR Fast 43452281356, PR Integration 43452281230, PR Desktop Editor Windows 43452281478 (§3.1). Log đã lưu của các head cũ: `508ebac`, `8c1e0ef` | `d894ad4` |
| #821, phần bổ sung sau rà soát chéo | #825 | cùng nhánh @ `760f8c5` | `production_job.py`, `qc.ts`, ADR-0055, workflow Windows, test | PR Fast 43452281356, PR Integration 43452281230, PR Desktop Editor Windows 43452281478; cả ba success | `45d2581` |
| #816 Product chấp nhận symlink nguồn không nhất quán với Compilation | #823 | `agent/issue-816-source-links` @ `4f95d1d` | `product.rs`, `compilation_raw.rs`, `compilation.rs`, `package.rs`, `product/job.py`, `product/__main__.py`, `compilation/program.py`, `compilation/job.py`, `package/job.py`, workflow Windows, test | PR Fast 43450934203, PR Integration 43450933998, PR Desktop Editor Windows 43450934028; kết quả từng run không ghi trong hồ sơ | `afbcdec` |
| #817 Số người được xác nhận sai; Kiểm tra cuối bỏ sót câu đã gắn cờ | #824 | `agent/issue-817-claim-status` @ `b87fb91` | `claims.py`, `align.py`, `review.py`, `final_qc.py`, hai planner, contract `editorial-v1.json`, test | PR Fast run 43453594599 và PR Integration run 43453594641 trên head `b87fb91`, cả hai success. PR không đổi tệp desktop hay Rust nào | `1687771` |

Thứ tự theo quyết định của chủ dự án ghi trên #463 (bình luận 6851211506): làm tuần tự, mỗi lúc một PR mở, #821 rồi #816 rồi #817. #489 / PR #820 được nhả, PR đã đóng và để sau Stage 1. #815 (Stage 2) chưa bắt đầu.

### 3.1. CI trước merge

**Trên head cuối của từng nhánh** (đây là CI của PR, không phải nghiệm thu):

| Nhánh @ head | Lane | Run | Kết quả | Nội dung đã chạy |
|---|---|---|---|---|
| #821 (PR #825) @ `760f8c5` | PR Fast | 43452281356 | success | không có log chi tiết trong hồ sơ |
| #821 (PR #825) @ `760f8c5` | PR Integration | 43452281230 | success | không có log chi tiết trong hồ sơ |
| #821 (PR #825) @ `760f8c5` | PR Desktop Editor Windows | 43452281478 | success | host Rust: 222 test đạt, 2 ignored. Bước source-rule của #816 trên Windows: 28 test, 6 test chạy với junction thật, không bỏ qua ca nào. Bước "mastering planted-link tests": 4 test, không bỏ qua ca nào |
| #816 (PR #823) @ `4f95d1d` | PR Fast | 43450934203 | success | không có log chi tiết trong hồ sơ |
| #816 (PR #823) @ `4f95d1d` | PR Integration | 43450933998 | success | không có log chi tiết trong hồ sơ |
| #816 (PR #823) @ `4f95d1d` | PR Desktop Editor Windows | 43450934028 | success | không có log chi tiết trong hồ sơ |
| #817 (PR #824) @ `b87fb91` | PR Fast | 43453594599 | success | không có log chi tiết trong hồ sơ |
| #817 (PR #824) @ `b87fb91` | PR Integration | 43453594641 | success | không có log chi tiết trong hồ sơ |

Head `760f8c5` đã chứa main sau khi merge #822 và #823, nên run Windows 43452281478 là lần chạy host Rust và hai bước link trên cây có cả #821 và #816. PR #824 không đổi tệp Rust hay desktop nào.

**Trên các head cũ hơn** (log đã lưu trước merge; giữ lại làm lịch sử):

| Nhánh @ head | Lane | Run | Nội dung đã chạy |
|---|---|---|---|
| #821 @ `508ebac` | PR Integration | 43409401657 | mastering 389 OK (15 bỏ qua), worker 174 OK (2 bỏ qua), governance đạt |
| #821 @ `508ebac` | PR Desktop Editor Windows | 43409401502 | host Rust `209 passed; 0 failed; 2 ignored`; media adapter 50, không bỏ qua ca nào |
| #821 @ `8c1e0ef` | PR Integration | log đã lưu không ghi run id | mastering 389 OK (15 bỏ qua), worker 180 OK (5 bỏ qua), governance đạt |
| #821 @ `8c1e0ef` | PR Desktop Editor Windows | log đã lưu không ghi run id | host Rust `209 passed; 0 failed; 2 ignored`, có `mastering::tests::qc_card_reads_the_exports_mastering_block_and_records_the_climax_choice ... ok`; bước "mastering planted-link tests": 3 test, không bỏ qua ca nào, FFmpeg n9.0.2 LGPL của ứng dụng |
| #816 @ `2d9a69b` | PR Integration | 43409405992 | mastering 387 OK (15 bỏ qua), product 63 OK (7 bỏ qua trên Linux), compilation 9 OK (1 bỏ qua), governance đạt |
| #816 @ `2d9a69b` | PR Desktop Editor Windows | 43409406048 | host Rust `217 passed; 0 failed; 2 ignored`, gồm các test mới của `product::tests` và `compilation_raw::tests::refuses_bad_fields_unsupported_formats_and_links` |

Hai test Rust "ignored" ở mọi run là `source::tests::real_link_downloads_through_the_supervisor_service` và `source::tests::real_session_save_reaches_the_protected_store`, có sẵn từ trước và không thuộc Stage 1.

**CI trên commit của candidate** (main `1687771341792874c55cc532d1bc593c8ac23e95`, sự kiện push):

| Lane | Run | Kết quả |
|---|---|---|
| PR Fast | 43454266200 | success |
| PR Integration | 43454266032 | success |
| Windows Release | 43454280200 | success; head_sha `1687771…`, kênh candidate, job publish bị bỏ qua |
| PR Desktop Editor Windows | không chạy lại khi push lên main | lane này chạy trên head của PR trước khi merge: run 43452281478 trên head `760f8c5` của PR #825 (cây có cả #821 và #816). PR #824 (head `b87fb91`) chỉ chạy PR Fast (43453594599) và PR Integration (43453594641), cả hai success; lane Windows không chạy vì PR #824 không đổi tệp desktop hay Rust nào |

CI bắt buộc trên commit của candidate đạt. Lane Windows desktop không có lần chạy trên chính commit `1687771`; lần chạy gần nhất có giá trị của nó là trên head của PR đã merge, như ghi ở trên.
### 3.2. Bản ghép cục bộ (trước merge, đã cũ)

Bản ghép `71573d7` (tree `6270b80…`) được dùng cho lượt kiểm và lượt rà soát chéo trước merge. Nó có trước `93db95f` và trước mọi commit sửa sau rà soát, nên các số dưới đây **không** mô tả main hiện tại. Linux, FFmpeg n9.0.2 LGPL trừ khi ghi khác:

| Bộ test trên `71573d7` | Kết quả |
|---|---|
| `tests/mastering` | 430 OK, 0 bỏ qua (387 của mốc + 2 của #821 + 41 của #817) |
| `tests/product` | 63 OK (2 bỏ qua: hai ca "Windows file sharing") |
| `tests/compilation` | 9 OK |
| Hai bộ của #821 (`test_dubbing_mastering`, `test_dubbing_master`) | 56 OK (2 bỏ qua: hai ca "Windows reparse points"), cả với FFmpeg 6.1.1 |
| `tests/release` | 71 OK (11 bỏ qua: các ca chỉ chạy trên Windows hoặc cần PowerShell) |
| `tests/ci` | 38 OK |
| `tests/docu` | 152 OK (mốc `74c16d0`: 152 OK) |
| `scripts/validate_governance.py` | đạt |
| Desktop: `npm test --prefix apps/desktop`; `npx tsc --noEmit` | exit 0; exit 0 |
| Bộ hồi quy tổng hợp (`tests.mastering.regression.runner all`) | đầu ra giống từng byte với mốc `74c16d0` |
| Toàn bộ `tests/production` | **không xanh**: 316 test, `FAILED (failures=2, errors=19, skipped=9)`. Cả 21 nằm trong `tests/production/media/test_adapter.py`, tệp mà Stage 1 không đụng; mốc `74c16d0` hỏng đúng tập đó trong cùng môi trường (289 test, `failures=2, errors=19`). 20 ca là do máy Linux này (trình thông dịch là symlink, adapter từ chối); ca còn lại (`RealFfmpegPartsTests.test_parts_rendered_at_once_match_the_single_pass_frame_for_frame`) cần bộ mã hoá của Windows và không đánh giá được trên Linux. 27 test Stage 1 thêm vào cây này: 25 đạt, 2 bỏ qua (chỉ chạy trên Windows) |

Host Rust không biên dịch được trên máy Linux (thiếu gdk); chỉ chạy trên lane Windows. Trên cây có cả #821 và #816 (`760f8c5`): 222 test đạt, 2 ignored (run 43452281478, §3.1).

Kết quả test trên head sau các lượt sửa, do từng lượt sửa báo lại (Linux, trước merge):

| Nhánh @ commit | Kết quả |
|---|---|
| #821 @ `c47b4d7` | hai bộ #821: 63 OK (2 bỏ qua, chỉ chạy trên Windows), với FFmpeg 6.1.1 và n9.0.2; `tests/mastering` 389 OK; `tests/ci` 38 OK; desktop `npm test` và `tsc --noEmit` exit 0; governance đạt |
| #816 @ `49a857f` | `tests/product` 74 OK (7 bỏ qua: 5 test junction chỉ chạy trên Windows và 2 ca có sẵn); `tests/compilation` 9 OK; `tests/package` 13 OK; `tests.mastering.test_compilation_raw` 19 OK; `tests/ci` 38 OK; governance đạt. Kết quả Linux ở `4f95d1d` (commit cuối, thêm test junction thứ sáu) không có trong hồ sơ; CI của `4f95d1d` ở §3.1 |
| #817 @ `963fee1` | `tests.mastering.test_semantic_claims` 79 OK; `tests/mastering` 445 OK; `tests/compilation` 9 OK; `tests.product.test_product` 40 OK; governance đạt; replay bộ hồi quy tổng hợp giống từng byte với trước lượt sửa |
## 4. #821: nguyên nhân

Tái hiện trên Windows rc17, run `run-a20f4c88c438` (Lồng tiếng 30 s, Mastering bật, đường B2). Trả lời chín câu hỏi của chỉ thị §3.2:

| # | Câu hỏi | Trả lời (mã ở `74c16d0`, số đo ở rc17) |
|---|---|---|
| 1 | True peak trước AAC | −2,51 dBTP (`final_mix_mastered.wav`, đầu vào của bộ mã hoá) |
| 2 | Sau AAC tăng bao nhiêu | +1,20 dB, thành −1,31 dBTP; vượt trần −1,5 dBTP 0,19 dB |
| 3 | Gain được tính thế nào | `planners/dubbing.py::_loudness`: một `loudnorm` tuyến tính (`linear=true`), đích hạ xuống sao cho đỉnh **dự đoán** của mix ≤ −1,5 − `AAC_HEADROOM_DB` |
| 4 | Có biên an toàn cho AAC không | Có, cố định `AAC_HEADROOM_DB = 1.0`. AAC cộng 1,20 dB nên biên không đủ |
| 5 | Lần đo cuối lấy từ file nào | `final_vi.mp4` sau AAC (`output_loudness`, có từ #715) |
| 6 | QC dùng true peak trước hay sau AAC | Không dùng số nào để quyết định. Số sau AAC chỉ được lưu; không dòng nào so nó với −1,5 |
| 7 | Vì sao QC vẫn PASSED | `qc_report.json` ghi `"status": "passed"` là hằng chuỗi. `downgrade` chỉ đến từ danh sách cảnh báo, và không cảnh báo nào được thêm cho đỉnh vượt trần (ở run này chỉ có `TTS_INTERCUE_GAP_USED`). Chỉ thẻ Hậu kỳ trên desktop (`qc.ts`, `TRUE_PEAK_CEILING = -1.5`) so với trần, nên thẻ cảnh báo còn báo cáo ghi passed |
| 8 | Fallback | B1 và Vietsub-only giữ âm thanh gốc; được đo sau AAC, không so với trần, không cảnh báo |
| 9 | Đo không được mà vẫn PASS | Có. Phép đo hỏng cho `output_loudness = None`, không cảnh báo, không phân biệt với im lặng |

Nguyên nhân gốc gồm hai phần độc lập:

1. **Trần chỉ được dự đoán trước AAC, không bao giờ được kiểm sau AAC.** Mức AAC cộng thêm phụ thuộc nội dung, nên không biên cố định nào bảo đảm được trần.
2. **Trạng thái QC không phụ thuộc phép đo.** Ba câu trả lời khác nhau (render xong, đạt chuẩn Mastering, kết quả QC cuối) bị gộp vào một hằng "passed".

Bộ lọc của run rc17: `loudnorm=I=-17.2:TP=-1.5:LRA=11:measured_I=-15.42:measured_TP=-0.73:measured_LRA=7.60:measured_thresh=-26.04:offset=0.77:linear=true:print_format=summary,aresample=48000`. Tham số AAC (`media/adapter.py`): `-c:a aac -b:a 192k`, WAV đã master là input 1 (`-map 1:a:0`).

## 5. True peak trước và sau AAC

### 5.1. rc17, trước sửa (Windows, đo bằng FFmpeg đóng gói)

| Điểm đo | I (LUFS) | True peak (dBTP) | LRA |
|---|---|---|---|
| Mix trước Mastering | −15,42 | −0,73 | 7,60 |
| `final_mix_mastered.wav` (trước AAC) | −17,20 | −2,51 | |
| `final_vi.mp4` (AAC LC, 48 kHz, stereo, 193 kb/s) | −17,22 | **−1,31** | |

AAC cộng +1,20 dB vào đỉnh, nhiều hơn biên 1,0 dB.

### 5.2. Mức vượt không đơn điệu theo gain

Mã hoá lại cùng mix rc17 cho kết quả tất định (cùng đầu vào, cùng số đo). Thêm một mức giảm tuyến tính trước AAC:

| Giảm thêm trước AAC (dB) | 0 | 0,2 | 0,4 | 0,6 |
|---|---|---|---|---|
| True peak sau AAC (dBTP) | −1,31 | −2,29 | −2,77 | −2,54 |

Giảm 0,2 dB làm đỉnh hạ 0,98 dB; giảm 0,6 dB lại cho đỉnh **cao hơn** giảm 0,4 dB. Vậy không thể tính trước mức giảm cần thiết: phải mã hoá rồi đo lại sau mỗi lần chỉnh. Điều này khớp yêu cầu của chỉ thị §5 "không được dùng kết quả dự đoán thay cho true peak sau AAC".

### 5.3. Bản sửa: mã hoá thử, giảm tuyến tính, đo lại (PR #822, bổ sung ở PR #825)

Trong bước Mastering của Lồng tiếng, khi âm thanh nghe được là mix lồng tiếng (B2):

1. Mã hoá thử đúng WAV mà bước render sẽ dùng, với đúng tham số âm thanh của render (`AAC_RENDER_ARGS = ("-c:a","aac","-b:a","192k")`; một test khẳng định `adapter.py` dùng đúng bộ tham số này, một lần, không `-af`).
2. Đo bản thử bằng cùng máy đo với phép đo cuối (`loudnorm`, `input_tp`).
3. Nếu đỉnh > −1,5 dBTP: giảm thêm = (đỉnh − (−1,5)) + `AAC_TRIM_MARGIN_DB` (0,3 dB), **cộng dồn** và luôn tính từ PCM gốc, không bao giờ từ AAC đã giải mã. Mix được làm lại bằng bộ lọc của planner nối thêm `volume=-X.XXdB` (hoặc chỉ `volume` khi planner không cần bộ lọc).
4. Mã hoá thử và đo lại. Tối đa `AAC_TRIM_TRIES = 3` lần giảm sau lần thử đầu, tức nhiều nhất 4 lần mã hoá thử. Không lặp vô hạn.
5. Hết lượt mà vẫn vượt: giữ lần thử có đỉnh đo thấp nhất; QC ghi `failed` (§6).
6. Kết quả thử `{first_true_peak, true_peak, trim_db, tries}` được ghi vào plan và stage; khi resume không mã hoá lại. Một lần thử không chạy được ghi `aac_trial: null`, giữ nguyên bộ lọc của planner, không bao giờ biến thành `MASTERING_SKIPPED`, và được thử lại ở lần chạy sau.
7. Sau render, `final_vi.mp4` vẫn được đo một lần; kết luận đạt hay không dựa trên **phép đo này**, không dựa trên bản thử.

Chỉ dùng gain tuyến tính: không limiter, không compressor, không `loudnorm` động, không đổi LRA. `_loudness` và `AAC_HEADROOM_DB` không đổi. `adapter.py` không đổi, nên định danh render của mọi job khác không đổi. Recipe: `dubbing-master-v4` (plan v3 đã lưu, kể cả job rc17, được lập lại). ADR-0055 được bổ sung mục Consequences (2026-11-13, #821).

**Bổ sung sau rà soát chéo (PR #825, `88c9c77` và `c47b4d7`):**

- **Mỗi lần thử một tệp riêng**: `aac_trial0.m4a`, `aac_trial1.m4a`… trong thư mục tạm riêng. Một tệp thử còn bị giữ mở trên Windows không bao giờ là đầu ra của lần mã hoá kế tiếp, nên số đo vượt trần đã có không bị mất.
- **Tệp bận không làm mất mức giảm đã đo**: việc dọn tệp thử không còn ném lỗi đè lên số đo; mọi lần đổi tên (`_replace_busy`) thử lại khi gặp `PermissionError` (WinError 5, 32, 33), 6 lần, lùi dần từ 0,05 s, tổng khoảng 1,6 s. `_atomic_json` cũng dùng cách này, nên nó áp cho **mọi** lần ghi checkpoint và JSON của worker, không riêng Mastering. Trước đó, một lần từ chối tạm thời (antivirus, công cụ đồng bộ) làm bước Mastering rơi về tắt và bản xuất không được giảm.
- **Dọn thư mục tạm cũ**: đầu bước, sau khi kiểm thư mục `mastering` không phải link, các thư mục thật tên `.scratch-*` nằm trực tiếp trong đó (do một worker bị kill để lại) được xoá. Link hay tệp cùng tên bị để nguyên; một mục không đọc được thì bỏ qua, không làm tắt Mastering.
- **B2 im lặng là cảnh báo** (§6.2).

### 5.4. Số đo trước merge (không phải nghiệm thu)

**Trên PC Windows**, nhánh #821 @ `508ebac`, FFmpeg n9.0.2 LGPL của ứng dụng, 2026-11-13 khoảng 11:00Z. Đây là gọi trực tiếp hàm `_aac_conform` trên mix của run rc17, **không** qua DubFlow.exe:

| Ca | Đỉnh lần thử đầu (dBTP) | Giảm thêm (dB) | Số lần thử | Đỉnh sau AAC (dBTP) | I (LUFS) |
|---|---|---|---|---|---|
| Mix rc17 + bộ lọc của rc17 | −1,31 | 0,49 | 2 | **−2,78** | −17,71 (rc17: −17,22) |
| Tổng hợp, không bộ lọc planner | +0,56 | 3,50 | 3 | −2,95 | |
| Tổng hợp, `loudnorm` nhẹ | | 0,88 | 2 | −1,84 | |

- Một lần mã hoá độc lập bằng `-c:a aac -b:a 192k` cho cùng số đo. Tệp tạm được dọn sạch.
- 50 test của hai bộ #821 OK trên n9.0.2; test FFmpeg thật `RealAacTrimTests` có chạy, không bị bỏ qua.
- Cái giá ở ca rc17: âm lượng tích hợp hạ 0,49 LU (−17,22 thành −17,71). Đỉnh sau sửa thấp hơn trần 1,28 dB dù chỉ giảm 0,49 dB, đúng với tính không đơn điệu ở §5.2. Thẻ Hậu kỳ nêu mức giảm này (§6.3), theo yêu cầu "nếu phải hạ integrated loudness để giữ true peak, báo rõ trong QC".
- Phép kiểm này làm ở `508ebac`. Kiểm lại ở `8c1e0ef` trên PC Windows (2026-11-13 khoảng 12:30Z, vẫn **trước merge** và trước các commit `93db95f`, `88c9c77`, `c47b4d7`): hai bộ Python của #821 56 OK (1 ca chỉ chạy trên POSIX bị bỏ qua); ba test link Windows đạt; ca rc17 qua `_aac_conform` cho lần thử đầu −1,31, giảm 0,49 dB, 2 lần thử, đo độc lập I −17,71 LUFS và đỉnh −2,78 dBTP. Chưa có phép kiểm nào trên PC Windows ở head cuối `760f8c5` ngoài CI (§3.1).

**Trên Linux**, test FFmpeg thật `RealAacTrimTests` (tín hiệu: các cụm nhiễu cắt cứng 40 ms mỗi 400 ms ở −2,5 dBTP, đo +0,56 dBTP sau AAC):

| FFmpeg | Không bộ lọc planner | Có `loudnorm` tuyến tính của planner |
|---|---|---|
| n9.0.2 LGPL | giảm 3,5 dB, 3 lần thử, −2,95 dBTP | lần đầu −0,92; giảm 0,88 dB, 2 lần thử, −1,84 dBTP |
| 6.1.1 | giảm 3,5 dB, 3 lần thử, −3,04 dBTP | |

Mỗi kết quả được kiểm bằng một lần mã hoá riêng với tham số của render.

### 5.5. Số đo nghiệm thu trên bản candidate

| Mục | Giá trị |
|---|---|
| Video của ca rc17 (`dub-drama-30s.mp4`, mix mới, không vượt trần: mức vượt của rc17 không tái hiện; B2, Mastering bật, "Cân bằng"): true peak của `final_vi.mp4`, đo độc lập bằng FFmpeg | **−2,40 dBTP** (loudnorm của FFmpeg đóng gói chạy trên `final_vi.mp4`; giải mã 0 lỗi). Ca W1a, `rc18\logs\W1*-check.json`. PASS |
| Cùng ca: `mastering.conformance.true_peak_dbtp` trong `qc_report.json` | −2,40 dBTP, là phép đo `final_vi.mp4` sau render (§5.3, bước 7). Trùng phép đo độc lập. PASS qua `conformance.true_peak_dbtp`; trường `output_loudness` (đỉnh và I) không được chép trong biên bản: NOT VERIFIED |
| Cùng ca: `mastering.conformance` (status, trim_db, tries) | `{path: B2, status: passed, tries: 1, trim_db: 0}`; `result` {`technical_render: success`, `mastering_conformance: passed`, `final_qc: warning`}, cảnh báo là `TTS_INTERCUE_GAP_USED`. PASS |
| Cùng ca: I (LUFS) sau AAC | −17,42 LUFS (mix trước Mastering −15,6 LUFS, đỉnh −0,7 dBTP). Mức vượt của rc17 **không tái hiện** trên mix của lần chạy này: planner dự đoán −2,5 dBTP, lần thử AAC đo −2,40 dBTP, không cần giảm |
| Input gây overshoot (ca thứ hai, `dub-drama-30s-bursts.mp4`, "Cân bằng") | Lần thử AAC đầu, tức bản xuất nếu không giảm thêm: **+0,42 dBTP** (vượt trần 1,92 dB). Sau sửa: giảm 2,22 dB, đúng công thức (0,42 + 1,5) + 0,3; 2 lần thử; `final_vi.mp4` đo độc lập I −19,36 LUFS, đỉnh **−1,65 dBTP**; `conformance` {B2, `corrected`, tries 2, trim_db 2,22, đỉnh −1,65}; `result` {`success`, `corrected`, `final_qc: passed`}. Bộ lọc kết thúc bằng `aresample=48000,volume=-2.22dB`. Thư mục `mastering` chỉ còn `dubbing-plan.json` và `final_mix_mastered.wav`, không còn tệp thử hay thư mục tạm. Ca W1b, `rc18\logs\W1*-check.json`. PASS |
| Giới hạn của phép đo độc lập | Phép đo độc lập dùng cùng FFmpeg n9.0.2 và cùng bộ đo loudnorm (`input_tp`) với ứng dụng: đó là một lần gọi độc lập, không phải một bộ đo độc lập. Không có phép đo bằng công cụ khác (ví dụ `ebur128=peak=true` hoặc một trình đo BS.1770 khác); một lỗi chung của bộ đo này hay của bản build FFmpeg này sẽ không lộ ra (§17.1) |

## 6. Hành vi QC: ba trạng thái

### 6.1. Ba câu trả lời tách riêng

| Trạng thái của chỉ thị §4 | Trường trong `qc_report.json` | Giá trị |
|---|---|---|
| TECHNICAL_RENDER_SUCCESS | `mastering.result.technical_render`; `status` | `success`; `status` giữ hằng `"passed"` (báo cáo hợp lệ và render qua kiểm tra kỹ thuật; mọi lỗi thật đều ném lỗi trước đó; supervisor coi mọi giá trị khác là báo cáo không đọc được) |
| MASTERING_CONFORMANCE | `mastering.conformance.status` và `mastering.result.mastering_conformance` | `passed`, `corrected`, `failed`, `unmeasured`, `silent`, `not_applicable` |
| FINAL_QC_RESULT | `mastering.result.final_qc`; `downgrade` | `passed` hoặc `warning`. `warning` khi có bất kỳ cảnh báo không thuộc loại thông tin; supervisor khi đó báo `completed_with_warnings` |

`mastering.conformance` = `{status, ceiling_dbtp: -1.5, true_peak_dbtp, path, trim_db, tries}`, thêm `ceiling_met` khi đường không phải B2. Cảnh báo được thêm **trước khi** `downgrade` được tính. `mastering.result` cũng được tính lại trên đường `EDITABLE_LINK_FALLBACK`.

### 6.2. Từng trường hợp

| Trường hợp (Mastering bật) | `conformance.status` | Cảnh báo | `final_qc` |
|---|---|---|---|
| B2, đỉnh sau AAC ≤ −1,5, không cần giảm | `passed` | không | `passed` (nếu không có cảnh báo khác) |
| B2, đã giảm thêm và đỉnh sau AAC ≤ −1,5 | `corrected` | không | `passed` (nếu không có cảnh báo khác) |
| B2, đỉnh sau AAC > −1,5 (hết lượt, hoặc không có kết quả thử) | `failed` | `MASTERING_TRUE_PEAK_OVER` | `warning` |
| B2, không đo được `final_vi.mp4` | `unmeasured` | `MASTERING_TRUE_PEAK_UNMEASURED` | `warning` |
| B2, đầu ra im lặng (bản xuất lồng tiếng không có âm thanh đo được sau AAC) | `silent` | `MASTERING_OUTPUT_SILENT` (kể cả khi mix đã im lặng trước Mastering; theo PRODUCT_SYSTEM_DESIGN §19.3 "Không silence toàn video") | `warning` |
| B1 (quay về âm thanh gốc), đỉnh ≤ −1,5 | `not_applicable`, `ceiling_met: true` | không | theo các cảnh báo khác |
| B1, đỉnh > −1,5 | `not_applicable`, `ceiling_met: false` | `MASTERING_TRUE_PEAK_OVER … (B1, âm thanh gốc, không chỉnh)` | `warning` |
| B1, không đo được | `not_applicable`, `ceiling_met: null`, `unmeasured: true` | `MASTERING_TRUE_PEAK_UNMEASURED … (B1, âm thanh gốc, không chỉnh)` | `warning` |
| Vietsub-only (không có tiếng lồng), đo được | `not_applicable`, `path: "none"` | không | theo các cảnh báo khác |
| Vietsub-only, không đo được | `not_applicable`, `unmeasured: true` | **không** (xem §16.1) | theo các cảnh báo khác |
| Mastering tắt | không có khối `mastering` | không | không đổi so với trước |

Khi B2 không đạt trần sau khi hết lượt, video vẫn được xuất kèm cảnh báo; nó không bị chặn (§15, quyết định 2).

### 6.3. Thẻ Hậu kỳ trên desktop

`qc_view` (`mastering.rs`) chuyển tiếp `conformance`; `qc.ts` đọc chặt (trạng thái hay đường lạ, trần khác, số sai, bản ghi tự mâu thuẫn, bản ghi `silent` có kèm đỉnh hay nằm cạnh một phép đo đầu ra, đều cho `null`). Dòng "Âm lượng":

| Trạng thái | Dòng hiển thị | Tông |
|---|---|---|
| `corrected` | thêm ". Đã giảm thêm X dB sau AAC để đỉnh ≤ −1,5 dBTP" | thường |
| `failed` | "Không đạt trần −1,5 dBTP sau AAC dù đã giảm thêm … qua N lần thử mã hóa"; khi mọi lần giảm đều bị loại: "dù đã thử giảm N lần (không lần nào hạ được đỉnh nên giữ bản chưa giảm)" | cảnh báo |
| `unmeasured` | "không đo được sau AAC nên chưa xác nhận trần −1,5 dBTP" | cảnh báo |
| `silent` (B2) | "… Bản xuất lồng tiếng không có âm thanh đo được sau AAC (video im lặng): cần kiểm tra lại." | cảnh báo |
| B1 dưới trần | "(dưới trần −1,5 dBTP). Đây là âm thanh gốc (B1), không được chỉnh". Trước `93db95f` thẻ ghi "(trần −1,5 dBTP: đạt)", tức báo B1 như thể Mastering đã đạt | thông tin |
| B1 vượt trần | thêm "Đây là âm thanh gốc (B1), không được chỉnh" | cảnh báo |
| Vietsub-only, đo được | "Vietsub: âm thanh gốc, không áp trần −1,5 dBTP. Đo bản xuất: …" | thông tin, kể cả khi vượt trần |
| Vietsub-only, không đo được | "Vietsub: âm thanh gốc, không áp trần −1,5 dBTP. Bản xuất không đo được sau AAC." (trước `88c9c77` thẻ ghi "chưa xác nhận trần −1,5 dBTP" dù trần không áp cho Vietsub) | thông tin |

Báo cáo không có bản ghi `conformance` (bản xuất cũ) được đọc như trước.

Trên bản candidate rc18, thẻ Hậu kỳ khớp `qc_report.json` ở cả ba ca có thẻ, và khớp phép đo độc lập ở W1a và W1b (ảnh chụp `rc18\screenshots\W1*-card.png`, chụp bằng tự động hoá CDP): PASS. Ở W1d thẻ chỉ khớp `qc_report.json` về đỉnh (−0,08 làm tròn thành −0,1 dBTP); biên bản không có phép đo độc lập nào cho W1d, và số −10,2 LUFS trên thẻ không đối chiếu được với số nào trong biên bản: NOT VERIFIED.

| Ca | Thẻ (nguyên văn) | `qc_report.json` | Đo độc lập |
|---|---|---|---|
| W1a, B2 không cần giảm | mqc-ok: "Trước −15,6 LUFS, đỉnh −0,7 dBTP → sau −17,4 LUFS, đỉnh −2,4 dBTP sau AAC (trần −1,5 dBTP: đạt). Thấp hơn mục tiêu vì đỉnh của bản trộn đã cao…" | `passed`, đỉnh −2,40 | I −17,42 / đỉnh −2,40 |
| W1b, B2 đã giảm | "…sau −19,4 LUFS, đỉnh −1,6 dBTP sau AAC (trần −1,5 dBTP: đạt). Đã giảm thêm 2,2 dB sau AAC để đỉnh ≤ −1,5 dBTP. …" | `corrected`, trim_db 2,22, đỉnh −1,65 | I −19,36 / đỉnh −1,65 |
| W1d, Vietsub (Lồng tiếng tắt) | mqc-info: "Vietsub: âm thanh gốc, không áp trần −1,5 dBTP. Đo bản xuất: −10,2 LUFS, đỉnh −0,1 dBTP sau AAC" | `not_applicable`, path none, ceiling_met false, đỉnh −0,08 | không ghi trong biên bản (NOT VERIFIED) |

Thẻ làm tròn tới một chữ số thập phân. Mastering tắt (W1c) không có thẻ, đúng thiết kế.

## 7. Hồi quy B1 / B2 / Tắt

### 7.1. Test (trước merge)

`tests/production/worker/test_dubbing_mastering.py`, lớp `AacTrueCeilingTests`, theo mười ca của chỉ thị §6:

| # | Ca của chỉ thị | Test |
|---|---|---|
| 1 | B2 có loudnorm | `test_b2_loudnorm_within_the_ceiling_after_aac_needs_no_trim` |
| 2 | B2 không cần filter | `test_b2_without_a_planner_filter_gets_a_volume_only_trim` |
| 3 | B2 input peak cao | `test_b2_with_a_high_input_peak_stays_linear` |
| 4 | B2 gần ceiling | `test_b2_near_the_ceiling_exactly_at_it_passes_and_just_over_is_trimmed` |
| 5 | B2 overshoot sau AAC (số rc17) | `test_b2_post_aac_overshoot_rc17_is_trimmed_linearly_then_passes`; `test_a_non_monotonic_overshoot_is_measured_again_after_every_trim`; `test_exhausted_tries_keep_the_lowest_peak_and_report_failed_with_a_warning`; FFmpeg thật: `RealAacTrimTests` |
| 6 | B1 fallback | `test_the_b1_fallback_is_measured_and_warned_but_never_trimmed` |
| 7 | Mastering Off | `test_mastering_off_runs_no_trial_and_its_report_is_unchanged` |
| 8 | Measurement failure | `test_failed_measurements_are_unmeasured_with_a_warning_and_never_fail_the_job`; `test_a_trial_that_could_not_run_is_tried_again_on_the_next_run` |
| 9 | Cancel/resume | `test_resume_reuses_the_trial_result_without_encoding_again` (đếm số lần gọi) |
| 10 | Re-render không đổi đầu vào | `test_a_render_from_unchanged_input_gets_the_same_filter_and_trim` |

Thêm: Vietsub-only (`test_a_vietsub_only_job_is_not_applicable_and_not_warned`), tham số AAC bằng của render, các trạng thái `conformance`, lời cảnh báo.

**Fail trước, pass sau.** Trên base `74c16d0` với chỉ các tệp test mới: `Ran 46 tests, FAILED (failures=6, errors=20)`; mọi test mới đều fail, trừ một ngoại lệ có chủ đích: `test_mastering_off_runs_no_trial_and_its_report_is_unchanged` đạt trên base vì nó là chốt hồi quy (kỳ vọng `OFF_QC` được ghi từ base `74c16d0`). Desktop: `mastering.test.mjs` mới fail trên base ở khẳng định đầu tiên về `conformance`. Sau sửa: hai bộ #821 OK (50 test ở `508ebac`), bộ worker 174 OK, `tests/mastering` 389 OK.

Test thêm ở PR #825 (fail trước, pass sau trên Linux): B2 im lặng là cảnh báo (`test_a_silent_dubbed_export_is_a_warning_never_a_pass`, hai nhánh của `test_conformance_states`); tệp bận (`test_a_busy_trial_file_plan_or_mastered_mix_never_loses_the_measured_trim`, `test_a_rename_refused_for_another_reason_or_for_too_long_still_fails`, `test_a_trial_file_that_stays_held_open_is_never_the_next_encodes_output`); dọn thư mục tạm (`test_scratch_directories_left_by_a_killed_worker_are_swept_at_the_stage_start`, `test_a_stale_scratch_directory_that_cannot_be_examined_never_turns_mastering_off`); bộ đọc của khung test không nhìn xuyên qua link (`test_the_job_reader_never_looks_through_a_planted_folder_link`). Test thứ tư của bước Windows là `test_a_linked_mastering_folder_renders_as_off_before_anything_is_written`: chạy cả worker với thư mục `mastering` là symlink, và trên Windows luôn thêm một junction. Sau PR #825: hai bộ #821 63 OK trên Linux (2 bỏ qua, chỉ chạy trên Windows); trên CI Windows bước planted-link chạy 4 test, không bỏ qua ca nào (run 43452281478).

| Đường | Điều được giữ | Bằng chứng trước merge |
|---|---|---|
| B2 | chỉ gain tuyến tính; đạt trần sau AAC hoặc báo `failed` | các test 1–5, 8–10; số đo §5.4 |
| B1 | âm thanh gốc được đo, ghi, cảnh báo nếu vượt; **không bao giờ** bị giảm hay limit; mix đã master không bị đụng | test 6 (hai biến thể) |
| Tắt | không mã hoá thử, không đo, không khoá QC mới; báo cáo giống hệt base | test 7 |

### 7.2. Windows, bản candidate

| Đường | Kết quả |
|---|---|
| B2, Mastering bật | PASS. W1a (`passed`, không giảm, −2,40 dBTP) và W1b (`corrected`, giảm 2,22 dB, −1,65 dBTP); số đo độc lập, `qc_report.json` và thẻ khớp nhau (§5.5, §6.3) |
| B1 fallback, Mastering bật | BLOCKED. Không gây được fallback B1 từ UI nếu không cố ý làm hỏng B2, nên ca W1d' không chạy. Hành vi B1 (đo, ghi fallback, không chỉnh âm thanh) chỉ được phủ bằng unit test trước merge (`test_the_b1_fallback_is_measured_and_warned_but_never_trimmed`, hai biến thể); không có lần chạy nào trên DubFlow.exe rc18 |
| Mastering tắt: không có khoá, khối hay thư mục mastering mới | PASS. Ca W1c: các khoá của `qc_report.json` là `audio, background, caption_blur, downgrade, output_probe, source_probe, status, validated_at_utc, warnings`; không có khối `mastering` hay khoá `conformance`, không có `mastering.json`, không có thư mục `mastering`, không có thẻ. Đỉnh sau AAC +0,20 dBTP, không áp trần (hành vi cũ) |
| Mastering tắt: đầu ra không đổi so với rc17 hay base | NOT VERIFIED trên rc18. Lượt nghiệm thu không so bản xuất hay báo cáo với rc17 hay base; điều này chỉ được chốt bằng test `test_mastering_off_runs_no_trial_and_its_report_is_unchanged` (trước merge, §17.1) |

## 8. #816: kiểm chứng bảo mật nguồn

### 8.1. Trước sửa (rc17, Windows)

| Nguồn | Bán hàng | Tổng hợp |
|---|---|---|
| Tệp thật | nhận | nhận |
| Symlink tệp | **nhận** | từ chối: "Mục 1: nguồn 1: Video nguồn phải là tệp thật trên máy, không phải lối tắt: clip-link.mkv" |
| Hard link | nhận | nhận |
| Tệp dưới thư mục junction | **nhận** | **nhận** (chỉ thành phần cuối được kiểm) |
| `..` qua IPC `product_render` | **nhận**, đường dẫn thô có `..` được lưu vào spec | từ chối: "Đường dẫn video nguồn không hợp lệ" |
| `product_image` với `..`, symlink, junction | **nhận** | |

Nguyên nhân: `product.rs::local_file` chỉ kiểm `is_absolute && is_file && extension`, dùng cho ảnh, clip, nhạc, ảnh nền và `product_image`. Phía Tổng hợp, `compilation_raw.rs::source_file` chỉ kiểm thành phần cuối. Worker Python (`_file`) không từ chối link nào. SHA-256 của nguồn (`7da54045cfb6c91d…`) không đổi trong mọi ca: lỗi là đọc ngoài chính sách, không phải ghi vào nguồn.

### 8.2. Chính sách sau sửa (một quy tắc cho Bán hàng và Tổng hợp)

| Đối tượng | Quyết định | Thông báo (`{kind}` = "Video nguồn", "Ảnh", "Nhạc nền", "Ảnh nền") |
|---|---|---|
| Đường dẫn tương đối, có `.` hoặc `..`, hoặc đường dẫn thiết bị `\\.\` | từ chối (đọc trên chính chuỗi đường dẫn, vì `components()` che một `.` ở giữa) | "Đường dẫn {kind} không hợp lệ: {tên}" |
| Không có tệp | từ chối | "Không tìm thấy {kind}: {tên}" |
| Thành phần cuối là symlink, junction, mount point (reparse point loại name-surrogate), hoặc không phải tệp thường | từ chối | "{kind} phải là tệp thật trên máy, không phải lối tắt: {tên}" |
| **Bất kỳ** thư mục nào trên đường tới tệp là symlink hoặc junction; thư mục không đọc được | từ chối | "{kind} nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: {tên}" |
| Hard link | **nhận**: đó là chính tệp, không dẫn đi đâu khác; công cụ dedup và sao lưu để lại hard link | |
| Reparse point không phải name-surrogate (OneDrive Files-On-Demand, tệp dedup) | **nhận**: đó là chính tệp; video trên OneDrive của người dùng phải dùng được | |
| Tệp thật ở bất kỳ đâu trên máy | **nhận**: không có quy tắc containment hay tiền tố chuỗi | |

Phạm vi sau rà soát chéo (PR #823, `9ef3f51`, `49a857f`, `4f95d1d`):

| Đầu vào | Trước rà soát | Trên main |
|---|---|---|
| Nhạc nền của Tổng hợp v1 ("Video đã lồng tiếng") và của Đóng gói | `is_absolute && is_file && extension` ở host, `_file` ở worker: đi theo symlink, junction và nhận `..` | theo đúng quy tắc trên: `product::music_source` ở host, `_picked_music` ở worker. Thông báo cũ được giữ cho đường dẫn tương đối, thiếu tệp, sai định dạng |
| `transcript` (.srt) mà một spec Tổng hợp raw v2 gọi tên | `_file` | `_file` rồi `_plain_file`. Desktop không bao giờ gửi trường này; chỉ spec viết tay hoặc spec cũ bị ảnh hưởng |
| Ảnh nền là bản sao trong thư viện | host nhận mọi tên 64 ký tự hex trong thư mục thư viện; worker chỉ kiểm một thư mục ngay trên ảnh nền | chỉ bỏ qua các thư mục phía trên khi **chứng minh được** là bản sao của ứng dụng: tên `<sha256>.<ext>`, nằm trực tiếp trong thư mục thư viện, là tệp thường, có trong `index.json` của thư viện, và SHA-256 của nội dung bằng tên. Host và worker cùng một phép chứng minh; worker nhận thư mục thư viện qua `--library-dir` trên dòng lệnh, không bao giờ từ spec. Mọi ảnh nền khác theo đủ quy tắc nguồn |
| Các thư mục phía trên tệp, ở worker | | `_plain_file` kiểm **mọi** thư mục tổ tiên, tới gốc |

Các `final_vi.mp4` mà Tổng hợp v1 và Đóng gói đọc là đầu ra của chính ứng dụng trong DubFlow Output, không phải nguồn người dùng chọn; chúng không đổi.

- Host (Rust): `product.rs::check_source` / `plain_source_file`; `is_redirect` = `file_type().is_symlink()`. Dùng cho ảnh, clip, nhạc, `product_image`, `product_background_add` của Bán hàng và `source_file`, `music_file` của Tổng hợp raw, và nhạc của Tổng hợp v1 (`compilation.rs`) và Đóng gói (`package.rs`). Ở lần render đầu (`product_render`) và các lệnh nêu trên, mọi kiểm tra chạy trước `create_dir_all`, nên một tệp bị từ chối không tạo run nào; điều này chỉ được phủ bằng test (W2.4b). `product_rerender` không áp lại quy tắc nguồn ở host: trên rc18 host nhận lệnh render lại khi nguồn đã bị tráo thành symlink hay nằm dưới junction, và chỉ worker chặn (S6; §16.2, B9). `edit::is_link` không đổi.
- Ảnh nền lúc render: bản sao đã chứng minh được của thư viện (bảng trên), hoặc theo đủ quy tắc nguồn.
- Worker (Python): `product/job.py::_plain_file` áp cùng quy tắc, cùng thông báo, cùng thứ tự, khi nạp spec (ảnh, clip, nhạc), trong `compilation/program.py` (`_source`, nhạc, `transcript`), và qua `_picked_music` trong `compilation/job.py` và `package/job.py`. `_redirect` = `S_ISLNK`, hoặc trên Windows `FILE_ATTRIBUTE_REPARSE_POINT` cùng bit name-surrogate `0x20000000` của `st_reparse_tag`.

**Cửa sổ TOCTOU còn lại (đã ghi trong mã).** Host đọc đĩa ở mỗi lần gọi và không giữ gì; worker kiểm lại khi nạp spec. Sau đó FFmpeg mở tệp theo đường dẫn. Khoảng giữa lần kiểm của worker và lần mở của FFmpeg không đóng được (FFmpeg cần mở lại một đầu vào tìm kiếm được theo đường dẫn). Đây là thư mục của chính người dùng; việc cùng một người dùng tráo tệp nằm ngoài mô hình đe doạ.

### 8.3. Bảng S1–S8

Cột "Trước merge" **không phải nghiệm thu**. "CI Windows" là lane PR Desktop Editor Windows, run 43409406048 trên `2d9a69b` (runner Windows, junction thật bằng `mklink /J`). "PC Windows" là chạy `tests/product` và `_plain_file` trên fixture thật ở nhánh `2d9a69b` (63 OK, 0 bỏ qua; log `_sandbox\assisted\premerge\t816.log`), không qua DubFlow.exe.

| Ca | rc17 (trước sửa) | Chính sách sau sửa | Trước merge | Bản candidate (nghiệm thu) |
|---|---|---|---|---|
| S1 Tệp thường | nhận | nhận, như trước | CI Windows: `a_real_file_anywhere_is_a_source_and_a_hard_link_too` ok. PC Windows: tệp thường và hard link được nhận | PASS. Bán hàng COMPLETED; Tổng hợp raw khởi chạy run với 2 mục (UI) |
| S2 Symlink tệp | Bán hàng nhận; Tổng hợp từ chối | từ chối ở cả hai, cùng thông báo | PC Windows: symlink bị từ chối, PNG là symlink bị từ chối. CI Windows: `a_linked_photo_clip_or_music_is_refused` ok, nhưng log không cho biết runner có tạo được symlink tệp hay test đã bỏ qua nhánh đó (cargo ẩn stderr của test đạt) | PASS. Bị từ chối qua UI ở cả hai luồng. Bán hàng: "Video nguồn phải là tệp thật trên máy, không phải lối tắt: clip-link.mkv". Tổng hợp: "Mục 1: nguồn 1: Video nguồn phải là tệp thật trên máy, không phải lối tắt: clip-link.mkv" |
| S3 Thư mục junction | cả hai nhận | từ chối ở mọi độ sâu | CI Windows: `a_file_below_a_linked_folder_is_refused` ok. PC Windows: tệp dưới junction **thật** bị từ chối | PASS. Tệp dưới junction NTFS thật bị từ chối qua UI ở cả hai luồng: "Video nguồn nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: clip-01.mkv" (Tổng hợp thêm "Mục 1: nguồn 1:") |
| S4 Reparse point | | name-surrogate bị từ chối; loại khác (OneDrive, dedup) được nhận | Chỉ có test tổng hợp trên thẻ reparse (`test_a_name_surrogate_reparse_point_is_a_link_and_another_reparse_point_is_the_file`). Chưa thử tệp OneDrive Files-On-Demand thật ở đâu | PARTIAL. Từ chối reparse point loại name-surrogate: PASS (cùng bằng chứng với S2, S3: symlink và junction bị từ chối). Hard link được nhận ở cả hai luồng: PASS (hard link không phải reparse point). Nhận reparse point không phải name-surrogate (tệp giữ chỗ OneDrive Files-On-Demand, tệp dedup), tức nhánh riêng của S4: NOT VERIFIED, **N/A** trên máy kiểm (ứng dụng OneDrive không chạy; thư mục OneDrive của chủ dự án không bị đụng tới) |
| S5 Path traversal | Bán hàng nhận `..` qua IPC và lưu vào spec; Tổng hợp từ chối | từ chối `..`, `.`, đường dẫn tương đối, `\\.\` | CI Windows: `traversal_relative_missing_folders_and_other_formats_are_refused`, `a_render_checks_its_photos_clips_and_music_before_any_run`, `a_photo_preview_or_a_background_to_keep_follows_the_rule` ok. PC Windows: `..` bị từ chối | PASS. 15/15 phép thử IPC gửi từ trang bị từ chối (`rc18\logs\W2-ipc.json`): `product_render` (video `..`, nhạc `..`, nhạc symlink), `product_image` (`..`, symlink, junction), `compilation_raw_render` (video `..`, nhạc `..`, nhạc dưới junction), `compilation_render` của Tổng hợp v1 và `package_video` của Đóng gói (nhạc `..`, symlink, junction). Thông báo nhạc ở ba luồng sau là thông báo chung (§16.6, phát hiện 3) |
| S6 Race | không thử được qua UI | host đọc đĩa mỗi lần gọi; worker kiểm lại; cửa sổ worker tới FFmpeg còn mở (§8.2) | CI Windows: `the_rule_reads_the_disk_on_every_call` ok. Python: `test_a_source_swapped_for_a_link_after_the_desktop_checked_it_is_refused` (tệp bị tráo, và thư mục trên đường bị tráo). Mức phủ: test tích hợp host và worker, **không** qua UI | PASS (qua DubFlow.exe). Sau một run Bán hàng "Nhẹ" COMPLETED, tệp nguồn bị thay bằng symlink: host nhận `product_rerender` (host không áp lại quy tắc nguồn khi render lại; §16.2, B9), engine FAILED "Video nguồn phải là tệp thật…: clip-f.mkv". Thư mục bị thay bằng junction: engine FAILED "Video nguồn nằm trong thư mục là lối tắt…: clip-s.mkv". Cửa sổ giữa lần kiểm của worker và lần FFmpeg mở tệp vẫn không thử được (§8.2) |
| S7 Toàn vẹn nguồn | SHA-256 không đổi (`7da54045cfb6c91d…`) | không đổi | Python: `test_a_render_only_reads_its_sources` (render FFmpeg thật, SHA-256 nguồn không đổi, spec giữ đường dẫn đã kiểm) | PASS. SHA-256 trước và sau như nhau (`rc18\logs\W2-sha-before.txt`, `rc18\logs\W2-sha-after.txt`): clip `7da54045…`, ảnh PNG `6a65dc5d…`, nhạc MP3 `a5217ab8…` |
| S8 Hồi quy legacy | | Bán hàng và Tổng hợp xử lý tệp hợp lệ như trước | `tests/product` 63 OK; `tests/compilation` 9 OK; `tests.mastering.test_compilation_raw` 19 OK; CI Windows: host Rust 217 passed | PASS. Bán hàng tệp thường COMPLETED; Tổng hợp raw v2 COMPLETED; Tổng hợp v1 qua UI (2 video đã lồng tiếng, `tune.mp3` thường) COMPLETED trong 18 s; Đóng gói qua UI (intro, `tune.mp3`) "Đã tạo video đóng gói." trong 12 s, ra `final_vi_packaged.mp4`; ảnh nền thư viện: `product_background_add` cho id bằng SHA-256 `6a65dc5d…`, lưu ở `rc18\app\backgrounds`, xem trước qua `product_image` 1 953 byte, `product_render` mẫu "ban-tin" dùng ảnh đó COMPLETED |

**Fail trước, pass sau.** Python trên base `74c16d0` với tệp test mới: `Ran 11 tests, FAILED (failures=3, errors=6)` (base nhận clip là link và `..`; không có `_plain_file`, `_redirect`); hai chốt hồi quy đạt trên base có chủ đích. Rust trên base: `sources_and_music_keep_tong_hops_texts` FAILED ở khẳng định "tệp dưới thư mục symlink bị từ chối"; các test mới của `product.rs` không biên dịch được trên base.

**Cập nhật sau lượt kiểm đầu (vẫn là trước merge, không phải nghiệm thu):**

- **Test link không còn đạt rỗng.** Khi biến `CI` được đặt, một symlink không tạo được làm test **hỏng** thay vì bỏ qua (Rust `link_made`, và hàm `symlink()` của test Python). Điều này trả lời chỗ chưa rõ ở ô S2: trên head cuối, nếu runner không tạo được symlink thì lane đã đỏ.
- **Lane Windows chạy bộ test của worker với junction thật.** Bước "source-rule tests" chạy `PlainFileTests`, `SpecTests`, `CompilationSourceTests`, `LegacyMusicTests`, `JunctionTests`; bước tự hỏng nếu có ca bị bỏ qua hoặc nếu không đủ 6 test junction đạt. Trên run 43452281478: 28 test, 6 test với junction thật, không bỏ qua ca nào. Test render đủ (`ReadOnlyRenderTests`, S7) vẫn chỉ chạy ở lane Linux.
- **Chạy thử phủ trên PC Windows (12:30Z, trên các head CŨ `8c1e0ef` + `2d9a69b` + `13d23ec`, DubFlow.exe build cục bộ từ bản ghép, không phải candidate; chi tiết ở §12.0):** S1 tệp thường đạt ở cả hai luồng; S2 symlink bị từ chối ở cả hai; S3 junction NTFS thật bị từ chối ở cả hai; hard link được nhận; `product_image` với symlink và junction bị từ chối; S5 `..` qua IPC bị từ chối (`product_render`, `product_image`, `compilation_raw_render`); S6 tráo nguồn sau một run đã xong: host nhận `product_rerender`, engine từ chối; S7 SHA-256 không đổi; S8 luồng cũ chạy được. Lượt này có trước các commit về nhạc của Tổng hợp v1 và Đóng gói, transcript và phép chứng minh thư viện, nên **không** phủ các phần đó.
- S4 vẫn như cũ: chưa có tệp reparse không phải name-surrogate thật nào được thử ở đâu, kể cả trong nghiệm thu rc18 (OneDrive N/A).

## 9. #817: bằng chứng cho khẳng định

### 9.1. Tái hiện (bằng chứng W3 đã lưu của rc16, `run-f6919b04ccb3`, engine đóng gói)

- Dòng khẳng định của Kiểm tra cuối in `critical[:12]` và không đếm phần còn lại. View có **15** câu nghiêm trọng; `i18.u1`, `i18.u4`, `i20.u2` bị mất khỏi dòng.
- `review.py::priority` không bao giờ đọc `signals.claims`. Mức nghiêm trọng chỉ đến từ: INVALIDATED, chế độ không phải nội dung, MATCH_NOT_FOUND / SOURCE_INSUFFICIENT, và ghi chú "không thấy sản phẩm" của Bán hàng. Vì vậy các câu có khẳng định chưa xác nhận chỉ ở mức "cần xem": `intro.u1` ("1.943", display false), `i1.u1` (display true), `i5.u2` ("Union of Soviet Socialist Republics"), `i9.u1` ("193.000.000 người", display true), `i13.u2` và `i14.u3` ("năm 1941").
- Số đếm của run: 15 nghiêm trọng, 52 cần xem, 4 tuỳ chọn, 71 câu.
- `i9.u1`: cảnh được chọn c100300, vision "… dòng chữ lớn về dân số … POPULATION 193,000,000", độ tin cậy 800.

### 9.2. Trạng thái của từng khẳng định (`claims.py`)

| Trạng thái | Khi nào |
|---|---|
| CONFIRMED | các cảnh được chọn cho thấy điều được khẳng định |
| CONTRADICTED | một cảnh được chọn cho thấy con số khác cùng loại: nhãn nằm trong `conflicts` hoặc `disputed`, **kể cả** khi một mảnh khác xác nhận nó (dạng rc15 i16.u1) |
| UNVERIFIED | không gì các cảnh được chọn cho thấy nói lên điều đó |
| NOT_APPLICABLE | câu không nói về thứ hiển thị (`display` false): con số của nó không phải điều cảnh phải cho thấy |

`statuses()` và `unresolved()` là hàm đọc. Bốn khoá cũ của `signals.claims` không đổi; thêm một khoá tuỳ chọn `disputed`, chỉ ghi khi không rỗng, nên log không có số đếm bị tranh chấp giống từng byte với trước. Quyết định của người dùng **không** đổi trạng thái: sau khi duyệt hay đánh dấu thiếu cảnh, câu không còn bị hỏi lại nhưng khẳng định vẫn là UNVERIFIED. Câu đã ghim thì khác: sau khi lập lại kế hoạch nó không có `signals.claims`, nên khẳng định hiển thị của nó không được kiểm và không mang trạng thái nào (§15, quyết định 5; §16.3, C1).

### 9.3. Con số gắn với thứ nó đếm

- Một số đếm (người, máy bay, xe tăng, hoặc con số không đơn vị mà đếm người) chỉ được xác nhận bởi **một con số cùng giá trị được gắn với cùng loại đối tượng**: đơn vị đứng sau con số, hoặc một từ chỉ dân số đứng ngay cạnh nó trên cùng dòng.
- Từ điển nhỏ, có ghi trong mã (`MEASURE_WORDS`): population, people, persons, inhabitants, residents, citizens, dân số, cư dân, người, dân. Giữa từ và con số được có tối đa hai từ nối ("of", "khoảng", "là"…) và một năm. Một số từ cũng tính khi đứng sau con số ("193,000,000 INHABITANTS", "193 triệu dân").
- Một dòng kết thúc ở xuống dòng (cũng là ranh giới giữa các mục bằng chứng) hoặc dấu ";": từ ở dòng này không gắn con số ở dòng khác.
- Con số có đơn vị khác, có từ hay ký hiệu tiền tệ, "#"/"№", hậu tố thứ tự, hoặc từ chỉ tuổi hay thời gian đứng sau thì không bao giờ được gắn với người.
- Con số trần không có từ gắn: UNVERIFIED.
- **Lỗ ngược đã đóng**: "Biểu đồ dân số 1500" không còn được xác nhận bởi "1500 miles", bởi số trần 1500, hay bởi "POPULATION\n1500 MILES".
- Đọc số mới, mỗi loại có test: từ thang tiếng Anh sau chữ số (thousand / million / billion), số thập phân trước từ thang ("1.5 million", "1,5 tỷ"), nhóm nghìn cách bằng dấu cách ("1 500"). Nhóm cách bằng dấu cách không được nối sau từ chỉ ngày, hạng hay danh sách ("Ngày 15 300 lính", "Top 10 100").

### 9.4. Tranh chấp (disputed) khác mâu thuẫn (conflict)

| | `conflicts` (#802) | `disputed` (#817) |
|---|---|---|
| Khi nào | cảnh cho thấy cùng đơn vị, khác giá trị | cảnh gắn một con số **khác** (từ 100 trở lên) với người, bằng từ chỉ dân số hoặc bằng đơn vị ("POPULATION 93,000,000", "93,000,000 PEOPLE" cho 193.000.000 người) |
| Xếp hạng cảnh | −300, cảnh không bao giờ được nhận | **không** phạt, cảnh không bị loại: một chữ số vision đọc nhầm không được đẩy lựa chọn khỏi cảnh đúng |
| Trạng thái | CONTRADICTED | CONTRADICTED |
| Hàng đợi duyệt | cần duyệt | nghiêm trọng, lý do "cảnh cho thấy con số khác với lời câu: …"; không bao giờ READY, kể cả khi một mảnh khác cho thấy đúng con số |

Test `test_a_misread_population_does_not_move_the_choice`: biểu đồ bị đọc nhầm "POPULATION 93,000,000" vẫn được chọn. Một nhãn không bao giờ vừa là conflict vừa là disputed.

### 9.5. Năm

- Một năm cạnh từ chỉ dân số là năm của biểu đồ, không phải con số bị so: "Population (1941): 193,000,000", "POPULATION 2020 = 193,000,000", "Dân số (1941): 193 triệu", "Dân số 193 triệu năm 1941", "dân số: 193.000.000 (năm 1941)" đều xác nhận số đếm.
- Một con số trần trong khoảng 1000–2100 không bao giờ tranh chấp một số đếm.
- "năm" / "year" ngay sau con số chỉ là thời lượng khi không có năm theo sau.
- Ngày tháng và năm không bao giờ xác nhận một số đếm (`test_dates_and_years_never_confirm_a_count`).

### 9.6. Mức ưu tiên duyệt (`review.py`)

- Sau bước trả về sớm cho quyết định đã chốt, câu có khẳng định hiển thị chưa giải quyết là **nghiêm trọng**. Lý do: "cảnh cho thấy con số khác với lời câu: …" và/hoặc "câu nói điều hiển thị trên hình nhưng cảnh chưa cho thấy: …", tối đa 3 nhãn.
- Câu có con số nhưng không nói về thứ hiển thị không bao giờ bị nâng mức. Khẳng định đã CONFIRMED không bị nâng mức. Log cũ không có `signals` giữ mức cũ.
- `view()` có `claims: [{label, status}]` cho từng câu.
- Quyết định INVALIDATED là nghiêm trọng trở lại. Một lần lập lại kế hoạch giữ nguyên cảnh thì giữ lượt duyệt.

### 9.7. Test và hồi quy (trước merge)

Mười sáu ca của chỉ thị §11:

| # | Ca | Test |
|---|---|---|
| 1 | Số người đúng ngữ cảnh | `test_people_count_in_the_right_context_is_confirmed`, `test_the_chart_with_the_population_is_ready` |
| 2 | Số người sai ngữ cảnh | `test_people_count_in_the_wrong_context_stays_unverified` |
| 3 | Số trùng, đơn vị khác | `test_the_same_figure_with_another_unit_never_confirms_a_count` |
| 4 | Số viết bằng chữ tiếng Việt | `test_vietnamese_number_words` |
| 5 | Số có dấu phân tách | `test_separators`, `test_english_scale_words` |
| 6 | Ngày tháng và năm | `test_dates_and_years_never_confirm_a_count`, `test_a_year_beside_the_population_word_leaves_the_figure_its_count` |
| 7 | Giá tiền | `test_prices_never_confirm_a_count` |
| 8 | Khoảng cách | `test_a_distance_never_confirms_a_count_and_the_reverse_hole_is_closed` |
| 9 | Diện tích | `test_an_area_never_confirms_a_count` |
| 10 | Vision mơ hồ | `test_a_vague_vision_description_is_no_proof` |
| 11 | OCR đúng nhưng không chứng minh | `test_ocr_read_right_but_not_proving_the_claim` |
| 12 | Sau split/replan | `test_a_claim_split_over_pieces_is_checked_on_every_piece`, `test_merged_pieces`, `test_a_replan_with_the_same_shots_keeps_an_approval` |
| 13 | Sau pin/replace | `test_a_pinned_claim_is_the_users_shot`, `test_an_approved_claim_is_not_reasked_and_stays_unverified` |
| 14 | Chưa được duyệt | `test_an_unverified_display_claim_is_critical`, `test_accepting_the_unreviewed_does_not_wave_an_unverified_claim_through` |
| 15 | Được duyệt hợp lệ | `test_an_approved_claim_passes_as_the_users_acceptance` |
| 16 | INVALIDATED | `test_an_invalidated_approval_of_a_claim_is_critical_again`, `test_an_invalidated_approval_is_listed_again` |

- 41 test mới: 22 trong `test_semantic_claims.py` (41 thành 63), 9 trong `test_review_queue.py` (15 thành 24), 10 trong `test_final_qc.py` (12 thành 22). Không khẳng định cũ nào bị đổi hay làm yếu ở commit đầu; ở lần sửa theo review, test "merged pieces" được đổi có chủ đích, có chú thích.
- **Fail trước, pass sau.** Trên base `74c16d0` với ba tệp test mới: `Ran 107 tests, FAILED (failures=15, errors=20)`. Bốn test mới đạt trên base có chủ đích, là chốt hồi quy.
- Sau sửa: `tests/mastering` 428 OK trên nhánh; 430 OK trên bản ghép `71573d7` (§3.2).
- Hồi quy #802: mọi test của `test_semantic_claims.py` có từ #802 vẫn đạt.
- Bộ hồi quy tổng hợp và Gate S replay sau commit đầu (`900c7d0`): đầu ra giống từng byte, chỉ khác chuỗi RECIPE. Sau lần sửa theo review (`13d23ec`): replay của bộ hồi quy tổng hợp giống hệt trước và sau. Gate S vẫn FAIL, không đổi.
- Recipe ở thời điểm đó: `compilation-program-v20`, `product-editorial-v19`. Trên main: `compilation-program-v22`, `product-editorial-v21` (§9.8).
- **Không chỉnh gì trên tập 60 câu W3** (nhãn khoá). Mọi hành vi mới được dẫn bằng test tổng hợp viết từ nguyên tắc. W3 chỉ được replay như một quan sát.

Quan sát trước merge trên bằng chứng W3 đã lưu của rc16 (không phải nghiệm thu; làm ở `13d23ec`, **trước** hai lượt sửa sau rà soát): replay Kiểm tra cuối cho dòng "20 câu chưa xử lý… và 8 câu khác", tức 15 câu cũ cộng `i1.u1`, `i5.u2`, `i9.u1`, `i13.u2`, `i14.u3`; `intro.u1` có `display` false nên là NOT_APPLICABLE và không được thêm. `tests/mastering` trên PC Windows ở `13d23ec`: 428 OK, 0 bỏ qua. Lần phát lại trên main: run W3 đã lưu của rc16 được phát lại trên engine đóng gói của rc18 (main `1687771`), ngoài DubFlow.exe; kết quả ở §10 và W3.5b (§12.3).

### 9.8. Hai lượt sửa sau rà soát chéo (`1f9a2d4`, `963fee1`)

Rà soát chéo (§14) cho thấy quy tắc gắn con số với người ở §9.3 tạo ra kết quả CONFIRMED / READY **sai** mà mốc `74c16d0` không có. Quy tắc trên main:

- **Danh sách đóng ở cả hai phía của con số.** Thứ được đứng sau một con số gắn với từ chỉ dân số là một danh sách đóng: không gì, dấu câu, một năm, từ nối trước năm, "và" / "and", một từ chỉ người, và một số dạng chú thích (từ mở trường kế tiếp như AREA / Diện tích, dấu đóng ngoặc kép, từ chỉ vị trí của chú thích, bốn dấu chấm dẫn trở lên). Danh sách được áp lại sau "và", sau năm và sau "in <năm>". Thứ đứng trước từ chỉ dân số cũng bị kiểm: từ chỉ tỉ lệ hay thuộc tính trong ba từ trước nó (mật độ, density, growth, tăng, thu nhập, GDP, giá, tuổi thọ…), "per / mỗi / đầu / trên", một con số ngay trước, "nhân dân" / "công dân" đều không gắn.
- **Đơn vị** (người, máy bay, xe tăng) chỉ gắn khi đứng sau con số trên cùng dòng, giữa chỉ có khoảng trắng (được có dấu chú thích `* + †`).
- **Không bao giờ xác nhận một số đếm:** giá tiền (ký hiệu tiền tệ Unicode, mã tiền, từ chỉ giá), ngày tháng và giờ (d-m-y, y-m-d, khoảng năm, "hh:mm", năm trần sau tên tháng, mùa hay giới từ), nhãn và mã (con số dính chữ như T-34, KV-1, B-17; từ nhãn như hình, bảng, trang, lô, mã, số điện thoại, tuổi), mật độ trên diện tích ("1500 người/km2", "… per sq mi"), trục năm hay khoảng năm sau từ chỉ dân số ("POPULATION 1500-2000").
- **Vẫn xác nhận:** số đếm theo ngày, tháng, bên hay trung đoàn ("1500 máy bay/ngày"); con số có dấu phân tách nghìn không bao giờ là năm ("1.500", "1,500"); con số sau dấu hai chấm theo ngày hay tháng ("Tháng 6: …").
- **Phía câu thoại:** một số đếm được kể trong lời không bao giờ được chứng minh bởi con số trần hay bởi cùng con số với đơn vị khác (trường không so sánh `Quantity.wide`). Câu nói về giá, ngày dạng gạch chéo hay mã thì không nêu số đếm nào.
- **Dòng:** một dòng kết thúc ở mọi ký tự xuống dòng, ở ";" và ở tab (tab là ranh giới ô). Nhóm nghìn cách bằng dấu cách chỉ được nối ở nơi chờ một số lượng (đầu dòng, sau dấu mở giá trị, sau từ chỉ dân số hay từ nối, sau vài động từ như ghi, có, mất, with, lost).

Test: lớp `TiedFigureTests` (lượt một) và `SecondReviewTests` (lượt hai). Lượt hai giữ **138 chuỗi không được xác nhận** (`test_none_of_these_confirms_the_count`; 130 chuỗi trong đó được CONFIRMED trước lượt sửa, 8 chuỗi là chốt) và **61 chuỗi phải xác nhận** (`test_all_of_these_still_confirm_the_count`; 50 chuỗi bị mất trước lượt sửa, 11 chuỗi là chốt), cùng 17 cặp câu và bằng chứng cho phía câu thoại. `test_semantic_claims.py` trên main có 79 test. Sau lượt hai: `tests/mastering` 445 OK; replay bộ hồi quy tổng hợp giống từng byte. Ba khẳng định thêm ở lượt một bị đảo có chủ đích ở lượt hai ("1500 máy bay/ngày" và "1500 tanks per month" xác nhận lại như ở mốc; "POPULATION 1500 - 1941" không còn xác nhận).

Recipe đổi vì thứ xác nhận một số đếm đã đổi: `compilation-program-v22`, `product-editorial-v21`. Plan đã lưu được lập lại một lần sau khi cập nhật. Không chỉnh gì trên tập W3; mọi chuỗi là test tổng hợp. Các dạng còn xác nhận sai và các dạng đúng nhưng bị đưa đi duyệt: §16.3.

## 10. Kiểm tra cuối: liệt kê đủ

Đường dữ liệu được kiểm (chỉ thị §10): kịch bản, nhận diện khẳng định, bằng chứng, trạng thái duyệt, Kiểm tra cuối, UI. `check_parts` đọc `log.units` của **mọi** phần qua `view()`, nên nhãn qua nhiều phần được gộp đủ (test với i1 và i16).

| Hành vi | Trước | Sau | Test |
|---|---|---|---|
| Dòng "Mục nghiêm trọng" | 12 câu đầu, không đếm phần còn lại | "N câu chưa xử lý, không được coi là đã kiểm: " + 12 câu đầu (`LISTED`) + " và N câu khác" | `test_every_critical_sentence_is_counted_and_none_is_dropped` (15 nghiêm trọng cho 12 + "và 3 câu khác"), `test_twelve_are_listed_without_a_tail` |
| "Chấp nhận các câu chưa duyệt" khi còn khẳng định nghiêm trọng | base cho READY_TO_FINALIZE | vẫn `FINAL_QC_FAILED` | `test_accepting_the_unreviewed_does_not_wave_an_unverified_claim_through`, `test_an_accept_from_an_earlier_run_does_not_wave_a_new_claim_through` |
| Khẳng định bị mâu thuẫn | không có trên dòng | được liệt kê | `test_a_contradicted_claim_is_listed` |
| Câu người dùng đã xử lý trong khi khẳng định còn chưa xác nhận | | thêm như thông tin, không chặn: "; cảnh chưa xác nhận điều câu nói, người dùng đã chấp nhận: …" (tối đa 6, rồi số đếm), cho câu đã duyệt hoặc đánh dấu "Không có cảnh phù hợp". Câu đã ghim **không** có trong dòng này, trong `user_accepted` hay `critical_units`: sau khi lập lại kế hoạch nó không có `signals.claims` (§15, quyết định 5; §16.3, C1) | `test_an_approved_claim_passes_as_the_users_acceptance`; `test_a_pinned_claim_is_the_users_shot` (dùng log mà planner ghi; dòng chỉ nêu câu đã duyệt và câu "Không có cảnh phù hợp") |
| Lượt duyệt bị INVALIDATED | | được liệt kê lại | `test_an_invalidated_approval_is_listed_again` |
| Khẳng định đã xác nhận nhưng độ phủ thấp (rc16 i16.u1) | không trên dòng | vẫn không trên dòng | `test_a_verified_claim_with_low_coverage_is_not_on_the_claims_line` |
| Qua nhiều phần | | liệt kê đủ | `test_claims_are_listed_across_parts` |
| Danh sách đủ cho máy đọc (sau rà soát chéo) | chỉ có dòng chữ, cắt ở 12 câu | kết quả Kiểm tra cuối có `critical_units` ở mức trên cùng, và mục kiểm "claims" có `units` và `user_accepted`: mỗi phần tử `{part, unit_id, number, claims: [{label, status}]}`, **đủ, không bao giờ cắt**. Dòng chữ không đổi | `test_the_result_carries_every_critical_sentence_for_a_machine` |

Desktop không cần đổi: lý do đã hiện qua `queue_reasons`, trường lạ của view và của kết quả Kiểm tra cuối bị bỏ qua, và dòng chi tiết nằm trong 400 ký tự mà desktop hiển thị (có test). Các trường mới của kết quả Kiểm tra cuối chưa có JSON schema trong `contracts/mastering`; chúng được mô tả trong docstring của `final_qc.py`.

Trên bản candidate rc18 (§12.3): PASS. Trong lượt W3 qua UI, dòng "Mục nghiêm trọng" liệt kê đúng 6 câu (i1, i2, i4, i5, i6, i7), trùng tập câu có huy hiệu Nghiêm trọng trên Bảng duyệt và trùng `claims.units` của engine. Đuôi "và N câu khác" chỉ được thấy trong lần phát lại run W3 đã lưu của rc16 trên engine đóng gói của rc18, không qua DubFlow.exe: "20 câu chưa xử lý…, i11: câu 3 và 8 câu khác", `claims.units` đủ 20 câu, trùng tập nghiêm trọng của view (W3.5b, NOT VERIFIED trên DubFlow.exe).

## 11. Bản candidate Windows

| Mục (chỉ thị §22) | Giá trị |
|---|---|
| Phiên bản | **1.1.96-rc18** |
| Commit | `1687771341792874c55cc532d1bc593c8ac23e95` (release run 43454280200 ghi head_branch `main`, head_sha trùng main, kết luận success) |
| Release run | 43454280200 |
| Kênh và `publish` | kênh `candidate`; job publish bị bỏ qua (skipped), không có gì được phát hành |
| Artifact | `DubFlow-1.1.96-rc18-windows-x64`, id 14056021630, digest của artifact do GitHub ghi: `sha256:bb5725f1bc0b55d840e0d5069226907d078301071e17c4da000a3f85dd923fd6` |
| SHA-256 của chính tệp zip, đối chiếu `release.sha256` | `DubFlow-1.1.96-rc18-windows-x64.zip`: `db266d144e7f5250e2f5b952c61a8aaa99c4eba7fdd732c3ca284b7efc6657c9`, trùng `release.sha256`, tệp `.zip.sha256` và `release-evidence.json`. Digest của artifact (`sha256:bb5725f1…23fd6`) trùng `artifact.zip` đã tải. SHA-256 của DubFlow.exe: `4ad44467…f024` |
| Runtime: Python đóng gói | Python 3.12.10 |
| Runtime: FFmpeg LGPL của ứng dụng | `n9.0.2-17-g2a571b6068-20260930`, LGPL, không có libx264 |
| `production_qualified` | `false` (theo `release-evidence.json`: source_sha `1687771…`, kênh candidate) |
| Hệ điều hành, CPU-only, thư mục dữ liệu riêng | Windows 11 Home 10.0.26300 (build 26300.9818); Intel i7-12700H, 14 nhân / 20 luồng, 39,7 GB RAM; GPU duy nhất là Intel Iris Xe; chỉ dùng CPU (tuỳ chọn "Chỉ dùng CPU" bật). Giải nén vào `rc18\app\versions\1.1.96-rc18` với thư mục dữ liệu riêng, không chạy trình cài đặt. Sau khi sửa lỗi giải nén của khung kiểm (§16.6, phát hiện 9), cả 8766 tệp trong manifest khớp SHA-256: 0 tệp thừa, 0 tệp thiếu, 0 tệp .pyc |
| Chứng minh tiến trình CDP gắn đúng DubFlow.exe | Hai lần mở app (launch1, launch2) đều có tiến trình cha của WebView2 là DubFlow.exe của rc18 (PROOF OK). Chân trang: "Phiên bản v1.1.96-rc18 candidate". CDP là tự động hoá, không phải người duyệt |

Digest của artifact là của gói artifact trên GitHub; nó không thay cho SHA-256 của tệp zip mà người kiểm tải về. Không phát hành stable.
## 12. Windows E2E W1–W4

### 12.0. Chạy thử phủ trước merge (không phải nghiệm thu, trên các head CŨ)

Làm trên PC Windows, 2026-11-13 khoảng 12:30Z, từ bản ghép tree `6270b80` (mốc `74c16d0` + #821@`8c1e0ef` + #816@`2d9a69b` + #817@`13d23ec`). **Bản ghép này có trước `93db95f` và trước mọi commit sửa sau rà soát chéo** (B2 im lặng, tệp bận, nhạc của Tổng hợp v1 và Đóng gói, transcript, phép chứng minh thư viện, hai lượt sửa #817). Vì vậy các kết quả dưới đây không nói gì về những phần đó, và không dòng nào là kết quả của rc18.

- **OVL1** = rc17 đã giải nén, thay 10 tệp engine bằng engine của bản ghép. Host (Rust) và UI vẫn là của rc17.
- **OVL2** = OVL1 cộng DubFlow.exe build cục bộ từ bản ghép (không phải build của CI: node 24.22.1 so với 22.14.0 của CI, phiên bản rơi về v0.1.0). Test Rust trên Windows của bản build này: `product::` 16/16, `compilation_raw::` 7/7, `mastering::` 11/11.

| Việc | Bản | Kết quả |
|---|---|---|
| W1, `dub-drama-30s.mp4`, "Cân bằng" | OVL1 | mix −15,5 LUFS / −0,7 dBTP; lần thử đầu −2,45 dBTP, không giảm; `conformance` {B2, `passed`, 1 lần thử, giảm 0, đỉnh −2,45}; `result` {`success`, `passed`, `final_qc: warning`} (cảnh báo là `TTS_INTERCUE_GAP`); đo độc lập I −17,41 / đỉnh −2,45. **Mức vượt của rc17 không tái hiện trên mix của lần chạy này** (`run-23e3efeb8450`) |
| W1, input gây overshoot `dub-drama-30s-bursts.mp4` | OVL1 | {B2, `corrected`, 3 lần thử, giảm 2,78 dB, đỉnh −1,71}; đo độc lập I −19,92 / đỉnh −1,71 (`run-465463649564`) |
| W1, cùng input, UI mới | OVL2 | {B2, `corrected`, 2 lần thử, giảm 2,18 dB, đỉnh −1,88}; đo độc lập I −19,42 / đỉnh −1,88; thẻ: "(trần −1,5 dBTP: đạt). Đã giảm thêm 2,2 dB sau AAC…" |
| W1 Mastering Off | OVL1 | không có khoá hay thư mục `mastering`; `status` passed (`run-d7784b8af199`) |
| W3, chương trình raw 5 mục | OVL1 | i1 "1.500 người" trên "1500 MILES": UNVERIFIED, nghiêm trọng. i2 "193 triệu người" trên "POPULATION 193,000,000": CONFIRMED, READY. i3 "năm 1942" (báo năm 1941): NOT_APPLICABLE (`display` false), cần xem, không nghiêm trọng. i4 "500.000 dặm" so với SQUARE MILES và i5 giá tiền so với ACRES: NOT_APPLICABLE, nghiêm trọng do MATCH_NOT_FOUND. Kiểm tra cuối khi chọn chấp nhận câu chưa duyệt ở mọi phần: `FINAL_QC_FAILED`, "3 câu chưa xử lý…: i1, i4, i5"; UI "Chưa xuất. Kiểm tra cuối chưa đạt.", không có tệp cuối |
| W2 S1–S8 qua UI thật | OVL2 | xem §8.3 |
| Quét khoá | | 1292 tệp: 0 / 0 / 0 |

Ghi nhận của lượt chạy thử: (1) câu có con số mà lời không trình bày là hiển thị thì NOT_APPLICABLE và không nghiêm trọng (i3), xem §15, quyết định 3; (2) `status` mức trên cùng là "passed" trong khi `mastering.result.final_qc` là "warning", đúng chủ đích (§15, quyết định 2); (3) dòng cũ "Còn 4 câu ở 4 phần cần xem lại…" vẫn hiện sau khi đã tích chấp nhận (lỗi cũ P3-N21, không thuộc Stage 1).
### 12.1. W1: Audio Ceiling

Bằng chứng: `rc18\logs\W1*-check.json` và `rc18\screenshots\W1*-card.png`, mỗi ca một tệp theo mã ca của lượt chạy (W1a, W1b, …). Số "độc lập" là loudnorm của FFmpeg đóng gói chạy trên `final_vi.mp4`; mọi lần giải mã 0 lỗi.

| # | Bước | Kết quả |
|---|---|---|
| W1.1 | Dựng Lồng tiếng có Mastering (B2), xuất AAC | PASS. W1a (`dub-drama-30s.mp4`, "Cân bằng") và W1b (`dub-drama-30s-bursts.mp4`, "Cân bằng") dựng xong trên DubFlow.exe rc18, đường B2, xuất AAC. W1a là lần chạy lại sau khi sửa lỗi giải nén (phát hiện 9); lần W1a đầu bị thay thế |
| W1.2 | Đo true peak độc lập từ `final_vi.mp4` | PASS. W1a: I −17,42 LUFS, đỉnh −2,40 dBTP. W1b: I −19,36 LUFS, đỉnh −1,65 dBTP. Cả hai dưới trần −1,5 dBTP; giải mã 0 lỗi. Phép đo dùng cùng FFmpeg n9.0.2 và cùng bộ đo loudnorm với ứng dụng, tức là một lần gọi độc lập chứ không phải một bộ đo độc lập (§5.5, §17.1) |
| W1.3 | So với `qc_report.json` (`conformance`, `result`, `warnings`; `output_loudness` không được chép riêng trong biên bản: NOT VERIFIED) | PASS qua `conformance.true_peak_dbtp` và `result`. W1a: `conformance` {B2, `passed`, tries 1, trim 0, đỉnh −2,40}, `result` {`success`, `passed`, `final_qc: warning`} vì `TTS_INTERCUE_GAP_USED`. W1b: {B2, `corrected`, tries 2, trim_db 2,22, đỉnh −1,65}, `result` {`success`, `corrected`, `final_qc: passed`}, ghi chú "Âm lượng sau AAC: giảm thêm 2,2 dB để đỉnh ≤ −1,5 dBTP." Đỉnh trong báo cáo trùng phép đo độc lập ở cả hai ca |
| W1.4 | Trạng thái đạt hay không đạt khớp phép đo | PASS. `passed` khi lần thử đầu đã dưới trần (W1a); `corrected` khi phải giảm và bản xuất dưới trần (W1b); `not_applicable` với `ceiling_met: false` cho Vietsub đo được −0,08 dBTP (W1d). Không ca nào báo đạt trong khi số đo vượt trần. Ca `failed` (hết lượt mà vẫn vượt) không xảy ra trên rc18 |
| W1.5 | Input gây overshoot (input tổng hợp `dub-drama-30s-bursts.mp4`, thay cho ca rc17; ca rc17 không còn vượt) | PASS. Video của ca rc17 không còn vượt trên mix mới (W1a), nên dùng input tổng hợp `dub-drama-30s-bursts.mp4`: lần thử AAC đầu +0,42 dBTP, giảm (0,42 + 1,5) + 0,3 = 2,22 dB, sau AAC −1,65 dBTP. Bộ lọc kết thúc bằng `aresample=48000,volume=-2.22dB`; không còn tệp thử hay thư mục tạm (§5.5) |
| W1.6 | Thẻ Hậu kỳ khớp `qc_report.json` | PASS. Thẻ khớp `qc_report.json` ở W1a, W1b, W1d và khớp phép đo độc lập ở W1a, W1b. Ở W1d thẻ chỉ khớp `qc_report.json` (−0,08 thành −0,1 dBTP); biên bản không có phép đo độc lập cho W1d (NOT VERIFIED) (§6.3; `rc18\screenshots\W1*-card.png`). Thẻ W1b nêu "Đã giảm thêm 2,2 dB sau AAC để đỉnh ≤ −1,5 dBTP." |
| W1.7a | Mastering Off: không có khoá, khối hay thư mục mastering mới | PASS. W1c: không có khối `mastering`, không có `mastering.json`, không có thư mục `mastering`, không có thẻ; đỉnh +0,20 dBTP, không áp trần như trước (§7.2) |
| W1.7b | Mastering Off: đầu ra không đổi so với rc17 hay base | NOT VERIFIED trên rc18. Không so với rc17 hay base; chỉ có test `test_mastering_off_runs_no_trial_and_its_report_is_unchanged` (§7.2, §17.1) |

Các ca thêm, ngoài phạm vi chỉ thị (do phiên điều phối yêu cầu thêm; không thuộc điều kiện Stage 1). Ba ca W1d', W1e, W1f không kiểm được trên DubFlow.exe và được ghi là giới hạn ở §17.1:

| # | Ca | Kết quả |
|---|---|---|
| W1d | Vietsub (Lồng tiếng tắt), Mastering "Cân bằng" | PASS. `conformance` {path none, `not_applicable`, ceiling_met false, đỉnh −0,08}; `result.mastering_conformance` `not_applicable`; thẻ thông tin "Vietsub: âm thanh gốc, không áp trần −1,5 dBTP…"; phụ đề "Không có giọng lồng tiếng để canh, giữ thời gian phụ đề như cũ." Biên bản không có phép đo độc lập cho W1d (§6.3) |
| W1d' | Fallback B1 | BLOCKED. Không gây được từ UI nếu không cố ý làm hỏng B2. Chỉ được phủ bằng unit test (§7.2) |
| W1e | Render lại với đầu vào không đổi (dùng lại kết quả thử AAC) | NOT VERIFIED. "Xử lý lại với tùy chọn khác" tạo một run **mới**, không phải resume; TTS khác nên mix và lần thử cũng khác (lần đầu +0,38 dBTP, giảm 2,79 dB, 3 lần thử, −1,85 dBTP; đo độc lập −1,85). Lồng tiếng không có đường UI nào render lại cùng run (phát hiện 2). Run mới này là một ca `corrected` đạt trần thứ hai, không phải bằng chứng cho việc dùng lại |
| W1f | Hủy trong lúc Mastering rồi tiếp tục (Lồng tiếng) | BLOCKED. Hai lần thử; biên bản ghi "lệnh hủy tới sau bước Mastering và job vẫn hoàn tất", và ghi nút "Hủy xử lý" (`#cancel-processing`) bị ẩn và vô hiệu suốt run trừ khi job đang chạy là job được chọn (phát hiện 1). Biên bản không ghi lệnh hủy có thực sự được gửi tới engine hay không. Nếu không gửi được (nút ẩn), job hoàn tất bình thường; nếu engine đã nhận lệnh hủy mà vẫn hoàn tất, đó là lỗi đúng đắn, ứng viên P1. Điều này chưa được xác định (§16.6, §17.1). Resume sau hủy chỉ được phủ bằng test `test_resume_reuses_the_trial_result_without_encoding_again` |

### 12.2. W2: Product Source Security

| # | Bước | Kết quả |
|---|---|---|
| W2.1 | Chọn tệp hợp lệ (S1, S8) | PASS. Bán hàng, Tổng hợp raw v2, Tổng hợp v1, Đóng gói và ảnh nền thư viện chạy xong với tệp thường (§8.3) |
| W2.2 | Symlink tệp (S2) | PASS. Bị từ chối qua UI ở Bán hàng và Tổng hợp; thông báo nêu "không phải lối tắt" và tên tệp |
| W2.3 | Junction (S3) và reparse point (S4) | PASS cho S3 và phần name-surrogate của S4: tệp dưới junction NTFS thật bị từ chối ở cả hai luồng; symlink và junction (reparse point loại name-surrogate) bị từ chối. Hard link được nhận ở cả hai luồng (PASS; hard link không phải reparse point). S4 nói chung là PARTIAL: nhánh reparse point không phải name-surrogate là N/A (W2.3b) |
| W2.3b | Tệp giữ chỗ OneDrive Files-On-Demand (S4) | N/A. Ứng dụng OneDrive không chạy trên máy kiểm; thư mục OneDrive của chủ dự án không bị đụng tới. Chủ dự án đã chọn cho phép tệp OneDrive (§15, quyết định 1) |
| W2.4a | Hành vi UI: thông báo lỗi dễ hiểu (S2, S3, S5) | PASS. Thông báo nêu lý do và tên tệp, không lộ đường dẫn đầy đủ; 15/15 phép thử IPC bị từ chối (`rc18\logs\W2-ipc.json`). Nhạc `..` hay symlink ở Tổng hợp raw, Tổng hợp v1 và Đóng gói vẫn bị từ chối nhưng với thông báo chung (phát hiện 3) |
| W2.4b | Không tạo run khi nguồn bị từ chối (S5) | NOT VERIFIED. Biên bản nghiệm thu không ghi việc kiểm thư mục run sau mỗi lần từ chối; điều này (mọi kiểm tra chạy trước `create_dir_all`, §8.2) chỉ được phủ bằng test |
| W2.5 | Đầu ra và provenance của nguồn trong spec (S6) | PASS. Spec giữ đường dẫn người dùng đã chọn; 11 tệp review JSON có 0 đường dẫn tuyệt đối (W4.9). S6: engine từ chối nguồn bị tráo thành symlink hay nằm dưới junction sau khi host đã nhận lệnh render lại |
| W2.6 | SHA-256 nguồn trước và sau (S7) | PASS. Không đổi (`rc18\logs\W2-sha-before.txt`, `rc18\logs\W2-sha-after.txt`) |

Kết quả từng ca S1–S8 ghi ở §8.3.

### 12.3. W3: Factual Claim Safety

Chương trình raw 7 mục tạo qua UI, `run-01f1c271c609`, vision bật, bản nháp mất 210 s. Mục 1–5 dùng các đoạn phim Why We Fight có thẻ chữ "1500 MILES", "POPULATION 193,000,000", báo Dallas 1941, "500,000 SQUARE MILES", "2,310,000,000 ACRES". Hai thẻ tổng hợp mới "POPULATION 1500 FEET" và "GIÁ VÉ 1.500 đồng/người" nằm ở `stage1\media`, khung hình ở `stage1\logs\W3-new-cards.png`.

| Câu | Khẳng định trong lời | Thẻ chữ trên hình | Trạng thái và mức duyệt | Kết luận |
|---|---|---|---|---|
| i1.u1 | 1.500 người | 1500 MILES | chọn sai cảnh (bản đồ Balkans): MATCH_NOT_FOUND, UNVERIFIED; Nghiêm trọng | PASS (không xác nhận) |
| i2.u1 | 193.000.000 người | POPULATION 193,000,000 | một mảnh đọc "POPULATION 193,000,000", mảnh kia "93,000,000": tranh chấp, CONTRADICTED; Nghiêm trọng, "cảnh cho thấy con số khác với lời câu". Lượt chạy thử phủ trước merge (§12.0) cho CONFIRMED | PASS (an toàn; báo động giả do vision đọc nhầm, phát hiện 6) |
| i3.u1 | năm 1942 | báo Dallas 1941 | NOT_APPLICABLE (`display` false); Cần xem lại | theo chính sách (§15, quyết định 3; phát hiện 7) |
| i4.u1 | 500.000 dặm | 500,000 SQUARE MILES | NOT_APPLICABLE; nghiêm trọng chỉ do MATCH_NOT_FOUND | PASS (không xác nhận) |
| i5.u1 | 2.310.000.000 đồng | 2,310,000,000 ACRES | NOT_APPLICABLE; nghiêm trọng chỉ do MATCH_NOT_FOUND | PASS (không xác nhận) |
| i6.u1 | dân số 1.500 người | POPULATION 1500 FEET | UNVERIFIED; Nghiêm trọng | PASS |
| i7.u1 | 1.500 người mua vé | GIÁ VÉ 1.500 đồng/người | UNVERIFIED; Nghiêm trọng | PASS |

| # | Bước | Kết quả |
|---|---|---|
| W3.1 | Video có con số khác loại | PASS. Không con số khác loại nào được xác nhận: i1 (người trên "1500 MILES") UNVERIFIED; i4 (dặm trên SQUARE MILES) và i5 (đồng trên ACRES) NOT_APPLICABLE, nghiêm trọng do MATCH_NOT_FOUND; i3 (năm 1942 trên báo 1941) NOT_APPLICABLE, "Cần xem lại" (phát hiện 7); i7 ("1.500 người mua vé" trên "GIÁ VÉ 1.500 đồng/người") UNVERIFIED, nghiêm trọng: lớp xác nhận sai R2 đã đóng trên rc18 |
| W3.2 | Khẳng định về số người | PASS cho hướng an toàn: số sai không được xác nhận; i6 "dân số 1.500 người" trên "POPULATION 1500 FEET": UNVERIFIED, nghiêm trọng; lớp xác nhận sai R1 đã đóng trên rc18. Hướng xác nhận đúng: NOT VERIFIED trên DubFlow.exe. i2 "193.000.000 người" là CONTRADICTED, nghiêm trọng, vì vision đọc một mảnh thành "93,000,000" (báo động giả, phát hiện 6), nên lượt này không cho thấy một số người đúng được CONFIRMED (§17.1) |
| W3.3 | Footage không chứng minh khẳng định | PASS. i1 chọn sai cảnh (bản đồ Balkans): MATCH_NOT_FOUND và UNVERIFIED, nghiêm trọng. Không câu nào được xác nhận bởi cảnh không chứng minh nó |
| W3.4 | Bảng Duyệt cảnh: mức nghiêm trọng và lý do | PASS. Huy hiệu "Nghiêm trọng / Chưa tìm thấy cảnh khớp" (i1, i4, i5) và "Nghiêm trọng / Cần xem lại" (i2, i6, i7), mỗi câu có dòng "Vì sao nghiêm trọng: …". Dòng đếm câu cần xem lại bị cũ (phát hiện 5) |
| W3.5a | Kiểm tra cuối: dòng khẳng định đủ câu | PASS. "Mục nghiêm trọng: 6 câu chưa xử lý, không được coi là đã kiểm: i1: câu 1, i2: câu 1, i4: câu 1, i5: câu 1, i6: câu 1, i7: câu 1"; `claims.units` của engine là đúng 6 câu đó |
| W3.5b | Kiểm tra cuối: đuôi "và N câu khác" khi quá 12 câu | NOT VERIFIED trên DubFlow.exe: lượt UI chỉ có 6 câu nghiêm trọng. Lần phát lại tự động, chỉ đọc, run W3 đã lưu của rc16 trên engine đóng gói của rc18 (không qua DubFlow.exe) cho "20 câu chưa xử lý, không được coi là đã kiểm: i1: câu 1, … i11: câu 3 và 8 câu khác", `claims.units` đủ 20 câu, trùng tập nghiêm trọng của view, `user_accepted` rỗng |
| W3.6 | Không xuất được bản cuối khi còn khẳng định nghiêm trọng, kể cả khi chọn chấp nhận câu chưa duyệt | PASS. Tích chấp nhận câu chưa duyệt ở cả 7 phần (`review-*.json` của `run-01f1c271c609`, accept=True) rồi bấm "Xuất bản cuối…": UI "Chưa xuất. Kiểm tra cuối chưa đạt." kèm 6 câu; engine `FINAL_QC_FAILED`, `claims.units` là 6 câu đó; không có `final.mp4`. Sau khi xử lý từng câu (i1, i4, i5 "Không có cảnh phù hợp"; i2, i6, i7 "Duyệt cảnh này"; các thao tác "người dùng" ở đây do tự động hoá CDP thực hiện, không phải người duyệt): UI "Bản cuối (lần xuất 1): …\final.mp4 · Kiểm tra cuối: đạt.", engine `READY_TO_FINALIZE`, chi tiết "không còn mục nghiêm trọng; cảnh chưa xác nhận điều câu nói, người dùng đã chấp nhận: i1: câu 1, i2: câu 1, i6: câu 1, i7: câu 1"; `user_accepted` ghi 4 câu đó kèm trạng thái khẳng định; `final.mp4` giải mã 0 lỗi. Màn hình xuất không nêu các câu người dùng đã chấp nhận (phát hiện 4) |

Phần tự động hoá CDP không phải người duyệt.

### 12.4. W4: Regression

| # | Mục | Kết quả |
|---|---|---|
| W4.1 | Product Assisted | PASS. Bán hàng Assisted (Espresso, "Cân bằng", vision): bản nháp 120 s, 6 câu cần duyệt |
| W4.2 | Compilation raw v2 | PASS. Top 5, "Cân bằng", vision: COMPLETED, 8 phần |
| W4.3 | Compilation legacy v1 | PASS. Tổng hợp v1 qua UI (2 video đã lồng tiếng, nhạc `tune.mp3` thường) COMPLETED trong 18 s; Đóng gói "Đã tạo video đóng gói." trong 12 s (§8.3, S8) |
| W4.4 | Dubbing Off | PASS. Chỉ thị không nói rõ nghĩa của mục này; biên bản nghiệm thu ánh xạ nó tới W1c, tức Lồng tiếng với Mastering tắt (§7.2, W1.7a). Nếu "Dubbing Off" hiểu là Lồng tiếng tắt (chỉ Vietsub), bằng chứng trên rc18 là W1d: `conformance` {path none, `not_applicable`}, thẻ thông tin, phụ đề giữ thời gian cũ. Cả hai ca đã chạy trên DubFlow.exe rc18 |
| W4.5 | Checkpoint/resume | PASS (Tổng hợp). Bản nháp Top 5 mới bị hủy ở giây 141 khi đã xong 3 phần: CANCELLED, "Đã hủy tổng hợp. Có thể dùng lại các đoạn đã xong." "Dựng lại các phần đã đổi" mất 78 s: dựng i2, i3, i4, i5, outro; dùng lại hook, intro, i1 (mtime không đổi); `compilation.mp4` giải mã 0 lỗi. Hủy rồi tiếp tục với Lồng tiếng: BLOCKED (W1f) |
| W4.6 | Localized Replanning | PASS. Bước 1–2 (duyệt 4 câu, rồi ghim u3): dựng lại 9,8 s, giữ 4/4 lượt duyệt, 0 lượt bị INVALIDATED. Bước 3 (Bỏ, rồi Thay ở u2): 9,3 s mỗi lần; 2 lượt duyệt bị INVALIDATED có lý do rõ, đúng thiết kế vì lựa chọn mới lấy cảnh đã được duyệt ở câu khác: u6 "Cảnh bạn đã duyệt bị loại trừ ở câu 2; hãy chọn cảnh khác hoặc bỏ loại trừ.", u1 "Cảnh bạn đã duyệt đã được ghim cho câu 2; hãy chọn cảnh khác cho câu này."; bảng: "2 câu cần chọn lại vì lựa chọn trước không còn hợp lệ." |
| W4.7 | Whole-shot exclusion | PASS (#805). "Bỏ cả cảnh gốc" trên phương án chưa dùng i1-s1c1 (0,0–2,0 s); hộp xác nhận "Bỏ cả cảnh gốc i1-s1c1? 0,0 s–6,0 s của nguồn, 3 đoạn…" liệt kê từng đoạn và câu đang dùng; bản ghi EXCLUDE whole_shot [0,6000], ảnh hưởng f1–f3; bảng bên "Câu 1 đã loại cả cảnh gốc i1-s1c1 (3 đoạn)"; dựng lại 20 s chỉ dựng i1, dùng lại 7 phần; các đoạn của plan-i1 bắt đầu từ 6000, không chồng lên 0–6000 |
| W4.8a | Existing READY safety (#802): không khẳng định hiển thị chưa giải quyết hay bị tranh chấp nào thành READY | PASS. Trên DubFlow.exe (W3): không câu nào có khẳng định hiển thị chưa giải quyết hay bị tranh chấp thành READY; i2 CONTRADICTED là nghiêm trọng và chặn xuất |
| W4.8b | Existing READY safety (#802): các dòng READY đúng vẫn READY | NOT VERIFIED trên DubFlow.exe: trong lượt W3 qua UI không câu nào READY, nên hồi quy #802 không được chạy trong app. Chỉ có lần phát lại tự động, chỉ đọc, run W3 đã lưu của rc16 trên engine đóng gói của rc18, ngoài DubFlow.exe: i1.u2 READY (CONFIRMED "The Battle of Russia"); i1.u1 và i9.u1 nghiêm trọng; i16.u1 cần xem (CONFIRMED 1.500 dặm) |
| W4.9 | Source provenance | PASS. Spec giữ đường dẫn người dùng chọn; 11 tệp review JSON có 0 đường dẫn tuyệt đối |
| W4.10 | Security / credential redaction | PASS. Quét khoá trên `out`, `logs`, `screenshots`, `tools`, `app\control`, `app\backgrounds`, fixture của `rc18` và `stage1`: 2 083 tệp, 1,43 GiB; mẫu khoá AI 0 (hai họ mẫu), e-mail 0; self-test của trình quét PASS. Đường dẫn hồ sơ người dùng (`%USERPROFILE%`) trong tệp đã ghi: 0. Không chụp màn hình trang Tài khoản. Biên bản không nêu riêng thư mục dữ liệu của ứng dụng (ngoài `app\control`, `app\backgrounds`), log của supervisor và worker, thư mục làm việc `.dubflow-work` của các run hay log sự cố là đã được quét: các nơi đó là NOT VERIFIED (§17.1) |

Thêm: quét giải mã 22 đầu ra của lượt nghiệm thu, 0 lỗi.

## 13. Bảo mật

| Hạng mục | Thay đổi | Bằng chứng trước merge | Bản candidate |
|---|---|---|---|
| Nguồn của Bán hàng, Tổng hợp (raw và v1) và nhạc của Đóng gói (#816) | một quy tắc ở host và worker; ảnh nền thư viện phải chứng minh được bằng index và SHA-256 (§8.2) | §8.3; CI Windows trên head cuối: 28 test source-rule, 6 với junction thật, không bỏ qua ca nào | PASS cho S1–S3 và S5–S8; S4 một phần, nhánh không phải name-surrogate N/A (§8.3); 15/15 phép thử IPC bị từ chối (`rc18\logs\W2-ipc.json`) |
| Tệp trung gian của Mastering (#821, phát hiện của Lead trên PR #822) | mọi đầu ra FFmpeg trung gian (PCM tạm của planner, tệp AAC thử, các bản giảm) chỉ được ghi trong một thư mục riêng tạo bằng `tempfile.mkdtemp(prefix=".scratch-")` trong thư mục `mastering`, xoá trong `finally`. Kết quả tới `final_mix_mastered.wav` bằng `os.replace`; link nằm ở đích bị gỡ trước, không bao giờ bị ghi xuyên qua. Thư mục `mastering` là symlink hay junction bị từ chối trước khi đọc hay ghi bất cứ gì: job render như Mastering tắt, kèm `MASTERING_SKIPPED` | `PlantedLinkTests`: link ở năm tên cũ trỏ tới tệp đánh dấu ngoài job; tệp đánh dấu giữ nguyên byte, SHA-256 và `st_mtime_ns`. Trước sửa, test cho thấy tệp ngoài bị ghi đè bằng byte `RIFF…WAVE`. CI Windows trên head cuối `760f8c5` (run 43452281478): bốn test (symlink, junction, link ở mix đã master, và cả worker chạy với thư mục `mastering` là junction), không bỏ qua ca nào. Mỗi lần thử AAC ghi một tệp riêng trong thư mục tạm; thư mục tạm cũ chỉ bị dọn khi là thư mục thật | NOT VERIFIED trên rc18: không ca nào đặt thư mục `mastering` là junction qua DubFlow.exe; chỉ có CI Windows (4 test, run 43452281478). Quan sát được trên rc18: sau ca W1b, thư mục `mastering` chỉ còn `dubbing-plan.json` và `final_mix_mastered.wav`, không còn tệp thử hay thư mục `.scratch-*` |
| Thẻ QC của desktop (#715, có sẵn) | không đổi | `mastering::tests::the_qc_card_neither_reads_nor_writes_through_a_link` ok trên CI Windows | |
| Khoá AI và e-mail trong log, báo cáo và bằng chứng | không đổi mã | | PASS trên các thư mục được quét: 2 083 tệp, 1,43 GiB: khoá AI 0, e-mail 0, đường dẫn hồ sơ người dùng 0; self-test PASS. Các nơi ghi khác của ứng dụng không được nêu là đã quét: NOT VERIFIED (W4.10, §17.1) |

Cửa sổ còn lại, đã ghi trong mã và không đóng trong Stage 1:

- #816: giữa lần kiểm của worker và lần FFmpeg mở tệp theo đường dẫn (§8.2).
- #821: thư mục `mastering` chỉ được kiểm một lần; tráo thư mục trong lúc chạy nằm ngoài phạm vi.
- #816: `captions_vi.srt` và `source_credit.json` nằm cạnh video nguồn vẫn được đọc kể cả khi chính chúng là symlink (§16.2).

Không quy tắc nào dùng so khớp tiền tố chuỗi hay `canonicalize()` đơn lẻ để bảo đảm containment (chỉ thị §7.2).

## 14. Rà soát chéo đối kháng trước candidate

Sau khi ba nhánh có CI xanh và trước khi build candidate, bản ghép `71573d7` được đưa qua một lượt kiểm lại toàn bộ test (§3.2) và ba lượt rà soát độc lập, mỗi lượt cố bác bỏ kết luận "ba bản sửa đã đủ". Mỗi phát hiện chỉ được giữ khi một lượt kiểm thứ hai tái hiện được trên bản ghép. Đây là rà soát mã và chạy thử trên Linux; **không phải nghiệm thu**.

### 14.1. Phát hiện được xác nhận

| # | Issue | Mức | Phát hiện | Xử lý trước candidate |
|---|---|---|---|---|
| R1 | #817 | chặn phát hành | **Lớp xác nhận sai thứ nhất: từ chỉ dân số.** Một con số đứng sau từ chỉ dân số được gắn với người bất kể từ đứng sau nó: "POPULATION 1500 FEET", "DÂN SỐ 1500 HA", "POPULATION 1500 ACRES", "NGƯỜI 1500 XE", "POPULATION 1500 HOUSEHOLDS", "dân số 1500 hộ", "POPULATION 1500 PER SQ MI" đều xác nhận "1.500 người" và cho READY. Thêm hai đường: từ chỉ dân số ở câu trước, và "Hình 1 500 người". Mốc `74c16d0` để các ca này UNVERIFIED, nên đây là **hồi quy do chính bản sửa gây ra** | sửa ở `1f9a2d4`, rồi `963fee1` (§9.8) |
| R2 | #817 | chặn phát hành | **Lớp xác nhận sai thứ hai: đơn vị.** Đơn vị được khớp với từ kế tiếp bất kể có gì ở giữa, và các chốt giá, thứ tự, thời gian không áp cho đơn vị: "1.500 đồng/người", "1.500đ/người", "$1500 / người", "1941, người dân…", "22/6/1941: người Đức…", "JUNE 22, 1941 PEOPLE OF RUSSIA", "Lô số 1500 - người bán hàng", "…ghi 1500. Người đi bộ…" đều được đọc là số người | sửa ở `1f9a2d4`, rồi `963fee1` (§9.8) |
| R3 | #821 | chặn phát hành | Bản ghép chứa #821 ở `8c1e0ef`, không có `93db95f`: thẻ Hậu kỳ báo B1 dưới trần là "(trần −1,5 dBTP: đạt)", trái chỉ thị §4.3 (B1 không được báo như thể Mastering B2 đã đạt). Mọi bằng chứng của bản ghép là của một cây không phải cây sẽ merge | `93db95f` nằm trong PR #822 (merge `d894ad4`); thẻ nay ghi "(dưới trần −1,5 dBTP). Đây là âm thanh gốc (B1), không được chỉnh" (§6.3) |
| R4 | #816 | lớn | **Nhạc của Tổng hợp v1 và Đóng gói nằm ngoài quy tắc.** Tổng hợp v1 là chế độ mặc định của tab và là mục W4.3; nhạc người dùng chọn vẫn đi theo symlink, junction và nhận `..`, ở cả host và worker | sửa ở `9ef3f51` (§8.2) |
| R5 | #821 | lớn | **Gia cố trên Windows.** Một lần từ chối chia sẻ tạm thời (WinError 5/32) khi dọn hay đổi tên tệp thử AAC làm mất kết quả thử đã có; bản xuất được render không giảm, trên trần. Chỉ tái hiện bằng cách chèn lỗi | sửa ở `88c9c77` và `c47b4d7` (§5.3) |
| R6 | #821 | lớn, không phải lỗi mã | B2 vẫn vượt trần sau khi hết lượt được xuất với `final_qc: warning` và `status: "passed"`; chưa có quyết định của chủ dự án được ghi lại | không đổi mã; §15, quyết định 2 |
| R7 | #816 | lớn, không phải lỗi mã | S4 chưa có bằng chứng trên Windows, và quy tắc mới **nới** bảo vệ có sẵn của Tổng hợp raw (trước đây từ chối mọi reparse point) | không đổi mã; §15, quyết định 1 |

Phát hiện nhỏ đã xử lý cùng lượt: B2 im lặng không có cảnh báo (nay là `MASTERING_OUTPUT_SILENT`); docstring của `_is_link` mô tả hẹp hơn hành vi; thư mục tạm của worker bị kill không được dọn; lời thẻ Vietsub-only không đo được; test link có thể đạt mà không tạo được link (nay hỏng trên CI); bộ test của worker chưa chạy với junction thật trên Windows (nay có trong lane); Kiểm tra cuối chỉ có danh sách cắt ở 12 câu cho máy đọc (nay có `critical_units`).

### 14.2. Rà soát các lượt sửa

Mỗi lượt sửa lại được rà soát trước khi merge:

- **#821 (`c47b4d7`)**: bộ đọc của khung test dùng `rglob`, mà trên Windows với Python 3.12 sẽ đi vào junction và đọc tệp ngoài job như thể là plan; nay đi bằng một phép duyệt không bao giờ vào link. Các lần thử AAC dùng chung một đường dẫn; nay mỗi lần một tệp. Một thư mục tạm cũ không đọc được làm tắt Mastering; nay được bỏ qua.
- **#816 (`49a857f`, `4f95d1d`)**: `transcript` mà spec gọi tên chưa theo quy tắc; ảnh nền thư viện chỉ được nhận theo tên; worker chỉ kiểm một thư mục phía trên ảnh nền. Cả ba đã sửa (§8.2).
- **#817 (`963fee1`)**: lượt rà soát thứ hai tìm thêm các dạng lọt của lượt sửa đầu (sau "và", sau năm, trục năm, mật độ, nhãn, tiền tệ, tab và ký tự xuống dòng), và các ca **đúng** mà lượt sửa đầu làm mất. Kết quả là quy tắc danh sách đóng ở cả hai phía và hai bộ chuỗi 138 / 61 (§9.8).

Các phát hiện R1–R5 đều đã được sửa **trước** khi rc18 được build. Trên DubFlow.exe rc18 (§12): R1 và R2 được kiểm bằng hai thẻ tổng hợp "POPULATION 1500 FEET" và "GIÁ VÉ 1.500 đồng/người", cả hai cho UNVERIFIED và nghiêm trọng (W3.1, W3.2); R4 được kiểm qua UI và IPC (nhạc của Tổng hợp v1 và Đóng gói bị từ chối với `..`, symlink và junction; luồng hợp lệ chạy được). R3 (thẻ B1) và R5 (tệp bận trên Windows) không chạy được trên rc18: B1 không gây được từ UI, và tệp bận chỉ tái hiện được bằng chèn lỗi. Hai mục này vẫn chỉ được phủ bằng test trên Linux và CI Windows (§17.1). Những gì lượt rà soát để lại, không sửa: §15 (quyết định) và §16 (giới hạn đã biết).

## 15. Quyết định của chủ dự án

Mỗi mục nêu hành vi đang có trong rc18 và lý do. Mục 1 đã có trả lời của chủ dự án. Mục 2–7 chưa có phê duyệt rõ ràng nào được ghi lại: chúng là hành vi đã chọn trong mã, chủ dự án có thể đổi.

1. **Chính sách reparse point (#816).** Chỉ reparse point loại name-surrogate (symlink, junction, mount point) bị từ chối; tệp giữ chỗ của OneDrive Files-On-Demand và tệp dedup được nhận, để video trên OneDrive dùng được. Hệ quả: Tổng hợp raw nay nhận các tệp mà trước đây `edit::is_link` từ chối. Trả lời của owner (2026-11-13 17:34Z): giữ phương án cho phép tệp OneDrive; owner cho biết tệp tải về được lưu vào thư mục Downloads, không dùng OneDrive. Thẻ quyết định đóng với lựa chọn "Cho phép tệp OneDrive".
2. **B2 vẫn vượt trần sau khi hết lượt thử (#821).** Video vẫn được xuất, với `mastering.conformance.status: "failed"`, cảnh báo `MASTERING_TRUE_PEAK_OVER`, `final_qc: "warning"` và job `completed_with_warnings`. `status` mức trên cùng của `qc_report.json` giữ nguyên chữ `"passed"`, vì supervisor (`committed_qc_summary`) và hai smoke phát hành (`production_smoke.py`, `chinese_desktop_review_smoke.py`) đòi đúng chuỗi đó; đổi nó sẽ làm desktop báo "chưa xác minh được chất lượng" thay cho "hoàn tất kèm cảnh báo". Trên rc18 không ca nào hết lượt thử (W1.4).
3. **Con số mà lời thoại không trình bày là hiển thị trên hình (#817).** Khẳng định đó là NOT_APPLICABLE và không làm câu thành nghiêm trọng. Chỉ câu nói về thứ hiển thị mới bị đòi cảnh phải cho thấy. Trên rc18, i3 (năm 1942 trên báo 1941) theo đúng hành vi này (§16.6, phát hiện 7).
4. **Khẳng định hiển thị ở chế độ Bán hàng (#817).** Câu mà bộ nhận diện coi là nói về thứ hiển thị ("màn hình hiển thị 1.000 nits") mà cảnh chưa cho thấy thì thành NGHIÊM TRỌNG, và chặn "chấp nhận các câu chưa duyệt" cho tới khi có người quyết định từng câu. Chưa đo trên run Bán hàng thật: biên bản nghiệm thu rc18 (W4.1) không ghi nhận câu nào thuộc dạng này.
5. **Câu đã ghim (#817).** Câu người dùng ghim cảnh không mang trạng thái khẳng định nào: sau khi lập lại kế hoạch nó không có `signals.claims`, không nằm trong `critical_units` hay `user_accepted`. Nó không bao giờ được xuất mà người dùng chưa quyết định (ghim là quyết định cho từng câu), nhưng khẳng định hiển thị của nó không được kiểm và không được nhắc ở đâu. Chỗ hở này được ghi là phát hiện C1 (P2) ở §16.3.
6. **Các dạng xác nhận sai còn lại của #817, thừa hưởng từ mốc.** Ký hiệu kiểu máy và tên riêng trước con số ("Ju 87 planes", "Bf 109 aircraft", "Boeing 747 aircraft", "Kursk 1943 xe tăng"), và cận hay phủ định ("dưới 1500 người", "không phải 1500 người") vẫn xác nhận, như ở mốc `74c16d0`; không dạng nào là hồi quy. Đóng chúng cần một danh sách tên mở, nên để lại (danh sách đủ ở §16.3).
7. **Vietsub-only khi Mastering bật (#821).** Bản xuất chỉ có Vietsub (không có tiếng lồng) giữ âm thanh gốc, không áp trần −1,5 dBTP và không có cảnh báo, kể cả khi đỉnh sau AAC vượt trần; thẻ Hậu kỳ ở tông thông tin. Trên rc18, W1d xuất ở −0,08 dBTP với "Cân bằng" bật, không cảnh báo (§6.2, §16.1 A8).

## 16. Rủi ro còn mở và giới hạn đã biết

Lấy từ mô tả các PR, từ phần "rủi ro còn lại" và "ghi chú cho chủ dự án" của từng lượt sửa, đã khử trùng lặp và bỏ các mục đã được CI trả lời.

### 16.1. #821

- A1. Một lần thử AAC hỏng mọi lần (bộ mã hoá hỏng dai dẳng) làm bước Mastering chạy lại ở mỗi lần chạy job. Chi phí có giới hạn nhưng lặp lại.
- A2. Khoá `unmeasured` chỉ có khi phép đo hỏng, nên hình dạng bản ghi có điều kiện; `qc.ts` coi thiếu khoá là false.
- A3. Thư mục `mastering` chỉ được kiểm một lần; tráo thư mục trong lúc chạy (TOCTOU) nằm ngoài phạm vi. Mọi link ở thư mục `mastering` tắt Mastering cho job đó, kèm `MASTERING_SKIPPED`.
- A4. Nếu mix đã master hay plan bị khoá lâu hơn thời gian thử lại (khoảng 1,6 s), Mastering vẫn rơi về tắt kèm `MASTERING_SKIPPED`, như trước.
- A5. Việc thử lại khi tệp bận nằm trong `_atomic_json`, nên áp cho mọi lần ghi checkpoint và JSON của worker: một `PermissionError` dai dẳng nay tốn thêm tới khoảng 1,6 s trước khi hỏng như cũ.
- A6. Việc dọn thư mục tạm cũ giả định một worker cho mỗi thư mục đầu ra. Một worker thứ hai chạy cùng lúc trong cùng `.dubflow-work/mastering` sẽ bị xoá thư mục tạm đang dùng; chưa kiểm có khoá nào ngăn việc đó không.
- A7. Thấy khi rà soát, không đổi: `_held_bytes(work_dir)` dùng `rglob("*")`, trên Windows với Python 3.12 sẽ đi vào một junction đặt dưới thư mục làm việc. Nó chỉ đọc kích thước cho ước lượng dung lượng và không ghi gì, nhưng sẽ đếm cả tệp ngoài job.
- A8. Vietsub-only không đo được sau AAC: chỉ ghi `unmeasured: true`, thẻ ở tông thông tin, không cảnh báo, `final_qc` theo các cảnh báo khác. Trần không áp cho Vietsub nên để vậy (§15, quyết định 7).
- A9. B2 im lặng báo `completed_with_warnings` kể cả khi mix lồng tiếng đã im lặng từ trước Mastering.
- A10. Ở ca rc17 (số trước merge), bản sửa hạ âm lượng tích hợp 0,49 LU và để đỉnh thấp hơn trần 1,28 dB (§5.4). Do tính không đơn điệu, mức dư này không tinh chỉnh được bằng dự đoán. Trong lượt chạy thử phủ, mức vượt của rc17 không tái hiện trên mix mới của cùng video (§12.0), nên ca nghiệm thu W1.5 cần một input chắc chắn gây overshoot. Trên rc18 điều này lặp lại: ca W1a không cần giảm (−2,40 dBTP), nên W1.5 dùng input tổng hợp `dub-drama-30s-bursts.mp4` (lần thử đầu +0,42 dBTP).
- A11. rc18 chứa PR #825, và ca W1b cho thấy các tệp thử được dọn sạch (không còn tệp thử hay thư mục tạm). Việc thử lại khi tệp bận, việc dọn thư mục tạm của worker bị kill và thư mục `mastering` là junction không chạy được qua DubFlow.exe; chúng vẫn chỉ được phủ bằng CI Windows (4 test) và bằng lỗi chèn trên Linux.

### 16.2. #816

- B1. Cửa sổ cuối giữa lần kiểm của worker và lần FFmpeg mở tệp còn mở, như đã ghi (§8.2).
- B2. `captions_vi.srt` và `source_credit.json` nằm cạnh video nguồn vẫn được đọc kể cả khi là symlink. Chúng do một run Hàng đợi ghi ra, không phải đường dẫn spec gọi tên; các thư mục phía trên đã được kiểm qua video. Bỏ qua hay từ chối một tệp như vậy là việc chủ dự án quyết.
- B3. Chưa có test nào với một tệp reparse không phải name-surrogate **thật** (kiểu OneDrive) trên runner Windows; chỉ có test tổng hợp trên thẻ reparse. Bảng thẻ nào bị từ chối, thẻ nào được nhận lấy từ giá trị thẻ đã công bố và từ cách Rust std và CPython xử lý, không từ một lần chạy Windows.
- B4. Quy tắc từ chối cả tệp thật nằm dưới một thư mục junction hợp lệ của người dùng (thư viện chuyển sang ổ khác qua junction, thư mục được chuyển hướng). Nay điều này áp cả cho nhạc của Tổng hợp v1 và Đóng gói: một yêu cầu cũ có nhạc là symlink hay nằm dưới junction sẽ bị từ chối. Trên rc18, tệp dưới junction NTFS thật bị từ chối ở cả hai luồng với thông báo "Video nguồn nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: …" (S3). Ảnh hưởng trên thư viện thật của người dùng (thư mục chuyển hướng) vẫn chưa đo.
- B5. Đường dẫn thư viện của ảnh nền được so từng phần và so đúng từng ký tự; một cách viết khác của cùng thư mục (chữ hoa thường khác, dạng `\\?\`) không được nhận là thư viện và rơi về quy tắc nguồn đầy đủ. Khi đó ảnh vẫn được nhận, trừ khi gốc dữ liệu là junction.
- B6. Ảnh nền bị thiếu nay báo "Không tìm thấy ảnh nền: …" thay cho "Không mở được file: …".
- B7. Bước source-rule của lane Windows đòi đúng 6 test junction; thêm hay bớt một test junction phải sửa con số đó. Test render đủ (`ReadOnlyRenderTests`) không nằm trong bước Windows, nên phép kiểm "render chỉ đọc nguồn" chưa có lần chạy Windows nào ngoài E2E.
- B8. Ngoài phạm vi #816, do audit nêu: thư mục run mới của `product_render` được tạo (`create_dir_all`) mà không kiểm chuỗi thư mục thường hay containment như `product_dir` làm cho các lệnh khác. Cần một issue riêng; không sửa trong Stage 1.
- B9. `product_rerender` không áp quy tắc nguồn ở host; chỉ worker chặn. Trên rc18 (S6), host nhận lệnh render lại khi nguồn đã bị tráo thành symlink hay nằm dưới junction, và engine từ chối. Lớp phòng thủ ở host vì vậy chỉ có ở lần render đầu. Cần một issue riêng; không sửa trong Stage 1.

### 16.3. #817

**C1 (P2). Câu đã ghim bị bỏ khỏi bản ghi của Kiểm tra cuối.** Sau khi lập lại kế hoạch, câu người dùng ghim cảnh không có `signals.claims`, nên khẳng định hiển thị của nó không được kiểm, không nằm trong `critical_units` hay `user_accepted`, và không được nhắc trên dòng "người dùng đã chấp nhận". Đây là một chỗ hở của yêu cầu "Kiểm tra cuối liệt kê đủ" (#817). Câu đó không bao giờ được xuất khi người dùng chưa quyết định (ghim là quyết định cho từng câu). Nguồn: mô tả PR #824; chưa quan sát trên rc18. Đóng nó cần ghi khẳng định cho câu đã ghim trong `align.py` và đổi một test có chủ đích của #802 (§15, quyết định 5).

**Còn xác nhận sai** (mọi dạng cũng được xác nhận ở mốc `74c16d0`, không dạng nào là hồi quy):

- Tên riêng hay mã ngắn trước một con số cách bằng dấu cách: "Kursk 1943 xe tăng", "Ju 87 planes", "Bf 109 aircraft", "Boeing 747 aircraft", "APOLLO 11 MEN"; một năm trần đầu dòng trước từ chỉ người ("1941 người Đức tấn công").
- Con số sau một con số khác: "ĐT: 0912 1500 người", "Tập 2 1500 người".
- "15% of 1500 people", "c. 1500 people lived here", "-1500 người", cận và phủ định ("dưới 1500 người", "không phải 1500 người"), và cuối của một khoảng viết cách. Khoảng viết liền ("1000-1500 người") thì không xác nhận.
- "POPULATION 1941" vẫn xác nhận một khẳng định đúng 1.941 người; cần vậy để "POPULATION 1500" xác nhận 1.500.

**Mới so với mốc, có chủ đích:** một chú thích dân số được xác nhận khi theo sau là hết câu, dấu đóng ngoặc kép, từ mở trường kế tiếp hay từ chỉ vị trí. Vì vậy "POPULATION 1500 OVER 65" hay "POPULATION 1500. FEET" sẽ xác nhận 1.500 người.

**Đúng nhưng bị đưa đi duyệt** (cảnh báo thừa, không phải bỏ sót):

- Số đếm trông như năm đứng sau tên tháng, mùa hay giới từ: "June 1500 people", "Tháng 6 1500 người", "sau 1500 người". Viết "1.500" hoặc sau dấu hai chấm thì xác nhận.
- "1500 PEOPLE OF LENINGRAD"; "1,500 PEOPLE OF …" thì xác nhận.
- Từ chỉ dân số sau một từ thuộc tính trong ba từ ("Biểu đồ tăng trưởng dân số 1500"), hoặc một danh từ ngoài danh sách sau con số ("POPULATION 193,000,000 USSR").
- Số đếm sau từ nhãn ("Khu 1500 người", "Division 1500 men", "Top 1500 người"); nhóm cách bằng dấu cách sau một từ ngoài danh sách ("KILLED 1 500 MEN" đọc thành 1 và 500 người, tức **tranh chấp** với khẳng định 1.500).
- Đơn vị sau tab, xuống dòng, ngoặc hay gạch: "193,000,000\nPEOPLE" là UNVERIFIED (mốc xác nhận ca này). Một OCR tách con số lớn khỏi đơn vị sẽ đưa cảnh đó đi duyệt.
- Ba dấu chấm được đọc là dấu lửng (không gắn), bốn trở lên là dấu chấm dẫn (gắn).
- Phía câu thoại: một số đếm có đơn vị bị từ chối mềm ("Top 1500 người", "1.500 người/km2") là khẳng định số đếm chặt, nên khung hình cho thấy đúng nhãn hay mật độ đó không xác nhận nó.
- Từ chỉ dân số ở dòng OCR khác với con số (biểu đồ nhiều dòng), hoặc có tên thực thể hay "census" chen giữa: UNVERIFIED, không bao giờ CONTRADICTED.

**Khác:**

- Các danh sách từ là danh sách đóng, chỉ tiếng Việt và tiếng Anh.
- Một con số bị tranh chấp cạnh một mảnh xác nhận làm câu thành CONTRADICTED và cần duyệt, nên có thể thêm câu vào hàng đợi duyệt.
- Recipe đổi ba lần trên nhánh trước khi merge (v20/v19, v21/v20, v22/v21); mã băm của mã render cũng đổi (`align.py`). Các phần Tổng hợp và Bán hàng đã lưu được lập kế hoạch lại một lần sau khi cập nhật. Một chuỗi fixture được đọc khác ("75-77 men" không còn là "77 people"); replay hồi quy không đổi.
- Các trường mới của Kiểm tra cuối (`critical_units`, `units`, `user_accepted`) chưa có JSON schema; desktop hiện bỏ qua chúng.
- Quy tắc "con số cạnh từ chỉ dân số xác nhận số người" vẫn chưa có fixture độc lập `tuned_on: false`. Cơ sở là chỉ thị (§9.2) và các chuỗi tổng hợp viết từ nguyên tắc. Tập độc lập trong backlog của ASSISTED_SAFETY_HOTFIX_REPORT §10 còn mở; một fixture viết sau quy tắc và theo quy tắc thì không độc lập.

### 16.4. Ghi nhận khác

- D1. Recipe trên main: `dubbing-master-v4`, `compilation-program-v22`, `product-editorial-v21`. Plan đã lưu được lập lại một lần sau khi cập nhật.
- D2. Các phép kiểm trước merge trên PC Windows (§5.4, §8.3, §12.0) làm trên các head cũ (`508ebac`, `8c1e0ef`, `2d9a69b`, `13d23ec`) hoặc trên bản ghép `6270b80`. Nghiệm thu trên rc18, tức main `1687771`, nằm ở §11 và §12.
- D3. Lỗi cũ P3-N21 (dòng "Còn 4 câu ở 4 phần cần xem lại…" còn hiện sau khi tích chấp nhận) thấy lại trong lượt chạy thử phủ và trên rc18 (§16.6, phát hiện 5); không thuộc Stage 1.

### 16.5. Lỗi P0 / P1 mới

Không có lỗi P0 hay P1 nào được ghi nhận. Windows E2E trên rc18 ghi 10 phát hiện; mức cao nhất là P2 (phát hiện 1, khung kiểm và UX). Không phát hiện nào chặn phát hành (§16.6). Một điểm chưa loại trừ: với W1f, biên bản không ghi lệnh hủy có tới engine hay không; nếu engine đã nhận lệnh hủy mà vẫn hoàn tất job, phát hiện 1 phải được xếp lại thành ứng viên P1 (§17.1).

### 16.6. Phát hiện của nghiệm thu rc18 (đề xuất đưa vào backlog)

Mười phát hiện của lượt Windows E2E trên rc18. Không phát hiện nào là P0 hay P1. Tất cả được đề xuất đưa vào backlog và không chặn Stage 1.

| # | Mức | Phát hiện | Đề xuất backlog |
|---|---|---|---|
| 1 | P2 (khung kiểm và UX) | Nút "Hủy xử lý" (`#cancel-processing`) bị ẩn và vô hiệu suốt một run xử lý lại khi job đang chạy không phải job được chọn, nên không hủy được trong lúc Mastering. Hủy rồi tiếp tục với Lồng tiếng (W1f) chưa được kiểm. Biên bản ghi lệnh hủy "tới sau bước Mastering và job vẫn hoàn tất" nhưng không ghi lệnh hủy có được gửi tới engine hay không. Mức P2 dựa trên cách hiểu là lệnh hủy không gửi được; nếu engine đã nhận lệnh hủy mà vẫn hoàn tất, đây là lỗi đúng đắn, ứng viên P1 | Từ log CDP, xác định lệnh hủy có được gửi và engine có nhận không, rồi xếp lại mức nếu cần. Cho hủy job đang chạy từ mọi màn hình, hoặc chỉ rõ job nào đang chạy; sau đó chạy lại W1f trên Windows |
| 2 | P3 | "Xử lý lại với tùy chọn khác" luôn tạo run mới với TTS mới; Lồng tiếng không có đường UI nào render lại cùng run với đầu vào không đổi, nên việc dùng lại kết quả thử AAC chưa được kiểm trên Windows (W1e) | Thêm đường render lại cùng run, hoặc một kịch bản kiểm thay thế; sau đó chạy lại W1e |
| 3 | P3 | Nhạc `..` hay symlink ở Tổng hợp raw, Tổng hợp v1 và Đóng gói nhận thông báo chung "Không mở được nhạc nền; chọn MP3, M4A, …"; Bán hàng và các ca junction nói rõ lý do. Tệp vẫn bị từ chối | Dùng chung thông báo của quy tắc nguồn cho nhạc ở mọi luồng |
| 4 | P3 | Sau khi xuất, màn hình chỉ ghi "Kiểm tra cuối: đạt."; không nêu các câu người dùng đã chấp nhận dù khẳng định chưa xác nhận hoặc bị mâu thuẫn (i1, i2, i6, i7), trong khi Kiểm tra cuối có ghi chúng | Hiện danh sách `user_accepted` trên màn hình xuất |
| 5 | P3 (P3-N21 cũ) | Dòng "Còn N câu ở N phần cần xem lại…" còn hiện sau khi tích chấp nhận và cả sau khi mọi câu nghiêm trọng đã được xử lý. Sau một lần dựng lại không đổi gì, "Lần dựng gần nhất: dựng 8 phần, dùng lại 0" không khớp `status.json` (dùng lại cả 8) | Sửa cùng P3-N21 |
| 6 | Quan sát | Vision đọc một mảnh của thẻ POPULATION thành "93,000,000", làm khẳng định đúng 193 triệu người thành CONTRADICTED. Hỏng theo hướng an toàn | Theo dõi tỉ lệ báo động giả do đọc nhầm; không chỉnh quy tắc trên tập W3 |
| 7 | Quan sát (chính sách) | Con số mà lời không trình bày là hiển thị (năm 1942 so với báo 1941, khoảng cách so với diện tích, giá so với mẫu Anh) là NOT_APPLICABLE; chỉ thành nghiêm trọng khi không có cảnh khớp. i3 ở mức "Cần xem lại" | Chủ dự án xác nhận hoặc đổi (§15, quyết định 3) |
| 8 | Quan sát (kiểm mẫu bằng một phép soát tự động phụ, không phải người duyệt) | Câu có "ghi" hay "cho biết" tạo một khẳng định loại "written" từ chính các từ của câu; khẳng định đó vẫn chưa xác nhận kể cả khi con số đã được xác nhận ("POPULATION 1,500", "500,000 SQUARE MILES") | Rà lại cách tạo khẳng định "written", với fixture độc lập |
| 9 | Lỗi khung kiểm (đã sửa) | `unzip` của Git Bash giải mã sai tên 18 tệp mẫu giọng tiếng Việt (vieneu), ở sandbox này và cả sandbox rc17 trước đó. Các tệp đó được giải nén lại từ zip với tên UTF-8 và 18 bản sai tên bị xoá; cả 8766 mã băm nay khớp. Lần W1a đầu chạy trước khi sửa và được thay bằng lần chạy lại. Số đo rc17 ở §4 và §5.1 không được đo lại | Quy trình nghiệm thu dùng công cụ giải nén giữ tên UTF-8 và đối chiếu manifest trước lần chạy đầu |
| 10 | Không thực hiện được | Fallback B1 không gây được từ UI. OneDrive Files-On-Demand: ứng dụng OneDrive không chạy | Thêm cách gây B1 có kiểm soát cho nghiệm thu; thử OneDrive trên máy có ứng dụng nếu chủ dự án cần |

## 17. Stage 1: PASS / FAIL

| Điều kiện (chỉ thị §13) | Kết quả |
|---|---|
| #821 PASS | PASS. W1.1–W1.6 và W1.7a trên DubFlow.exe rc18; W1.7b (đầu ra Mastering tắt so với rc17) NOT VERIFIED (§5.5, §6.3, §7.2, §12.1, §17.1) |
| #816 PASS | PASS. S1–S3, S5–S8 PASS; S4 một phần (reparse point không phải name-surrogate, OneDrive N/A); W2.4b NOT VERIFIED (§8.3, §12.2, §17.1) |
| #817 PASS | PASS. W3 (§10, §12.3): Kiểm tra cuối chặn xuất khi còn câu nghiêm trọng chưa xử lý, kể cả khi đã tích chấp nhận câu chưa duyệt. W3.5b (đuôi "và N câu khác") NOT VERIFIED trên DubFlow.exe |
| Windows E2E PASS | PASS cho phạm vi chỉ thị (W1–W4) trên DubFlow.exe rc18, chạy bằng tự động hoá CDP, không phải người duyệt, trừ các mục chưa kiểm hoặc N/A trong phạm vi và các ca thêm ngoài chỉ thị; xem toàn bộ §17.1 |
| Security PASS | PASS. W2 (S4 một phần, OneDrive N/A), 15/15 phép thử IPC, quét khoá 0 khoá AI và 0 e-mail trên các thư mục được quét; các nơi ghi khác của ứng dụng NOT VERIFIED (§13, W4.10, §17.1). Thư mục `mastering` là junction: NOT VERIFIED trên rc18, chỉ có CI Windows (§17.1) |
| Legacy regression PASS | PASS. S8, W4.2–W4.4 (Dubbing Off ánh xạ tới W1c và W1d), Đóng gói và ảnh nền thư viện (§8.3, §12.4) |
| Không có P0 hoặc P1 chặn phát hành mới còn mở | PASS. Không có P0 hay P1 nào được ghi nhận; mức cao nhất là P2 (§16.5, §16.6). Chưa loại trừ: lệnh hủy ở W1f có tới engine hay không (§17.1) |
| Mọi phép đo có bằng chứng | PASS. `rc18\logs\W1*-check.json`, `rc18\screenshots\W1*-card.png`, `rc18\logs\W2-sha-before.txt`, `rc18\logs\W2-sha-after.txt`, `rc18\logs\W2-ipc.json`, `stage1\media`, `stage1\logs\W3-new-cards.png`, `review-*.json` của `run-01f1c271c609`; thông báo UI được chép nguyên văn trong biên bản lượt chạy CDP. Ngoại lệ đã nêu: W1d không có phép đo độc lập, `output_loudness` không được chép riêng, W2.4b không ghi việc kiểm thư mục run; phép đo độc lập dùng cùng bộ đo với ứng dụng (§17.1) |
| CI bắt buộc đạt | PASS. PR Fast 43454266200 và PR Integration 43454266032 trên push của main `1687771`; Windows Release 43454280200. Lane PR Desktop Editor Windows chạy trên head của PR đã merge (run 43452281478 trên `760f8c5`) và không chạy lại trên main (§3.1) |
| Không đổi chính sách Mastering hay workflow mặc định ngoài phạm vi được duyệt | PASS. Trần −1,5 dBTP và chỉ gain tuyến tính như chỉ thị; Mastering tắt không có khoá mới (W1.7a; đầu ra không đổi so với rc17 là NOT VERIFIED, W1.7b); workflow mặc định không đổi. Quyết định 1 đã có trả lời của chủ dự án. Quyết định 2–7 chưa có phê duyệt rõ ràng được ghi lại; chúng là hành vi trong phạm vi các bản sửa, chủ dự án có thể đổi (§15) |

**Stage 1: PASS** trên bản candidate 1.1.96-rc18 (main `1687771`) cho phạm vi chỉ thị, trừ các mục chưa kiểm hoặc N/A trong phạm vi: S4 reparse point không phải name-surrogate (N/A), W2.4b (NOT VERIFIED), W3.5b trên DubFlow.exe (NOT VERIFIED), W4.8b (NOT VERIFIED, chỉ có lần phát lại ngoài app); và các ca thêm W1d' (BLOCKED, chỉ được phủ bằng unit test), W1f (BLOCKED), W1e (NOT VERIFIED). Danh sách đủ ở §17.1.

Hệ quả:

- Release Safety Closure (Stage 1) hoàn tất cho phạm vi của chỉ thị, với các giới hạn ở §17.1.
- Stage 2 (#815) chưa bắt đầu tại thời điểm chốt báo cáo; theo chỉ thị, Stage 2 được mở khi Stage 1 đạt và không gồm thay đổi production nào.
- Không phát hành stable; chỉ có bản candidate rc18 (`production_qualified: false`), job publish bị bỏ qua.

### 17.1. Giới hạn của nghiệm thu

Các mục dưới đây không chạy, hoặc không chạy được, trên DubFlow.exe rc18. Không mục nào được tính là PASS.

- **W1d' fallback B1: BLOCKED.** Không gây được từ UI nếu không cố ý làm hỏng B2. Hành vi B1 và thẻ B1 (phát hiện R3 của rà soát chéo) chỉ được phủ bằng unit test (`test_the_b1_fallback_is_measured_and_warned_but_never_trimmed`).
- **W1e render lại Lồng tiếng với đầu vào không đổi: NOT VERIFIED.** Không có đường UI (phát hiện 2). Việc dùng lại kết quả thử AAC chỉ được phủ bằng `test_a_render_from_unchanged_input_gets_the_same_filter_and_trim` và `test_resume_reuses_the_trial_result_without_encoding_again`.
- **W1f hủy trong lúc Mastering rồi tiếp tục (Lồng tiếng): BLOCKED** do nút hủy không dùng được khi job đang chạy không được chọn (phát hiện 1). Checkpoint/resume chỉ được kiểm trên Tổng hợp (W4.5).
- **W1f: lệnh hủy có tới engine hay không: NOT VERIFIED.** Biên bản ghi lệnh hủy tới sau bước Mastering và job vẫn hoàn tất, nhưng không ghi lệnh hủy có được gửi tới engine. Nếu engine đã nhận mà vẫn hoàn tất, phát hiện 1 là ứng viên P1 (§16.6).
- **S4 reparse point không phải name-surrogate (tệp giữ chỗ OneDrive Files-On-Demand, tệp dedup): N/A.** Ứng dụng OneDrive không chạy trên máy kiểm. S4 vì vậy chỉ PARTIAL: phần name-surrogate và hard link PASS.
- **W2.4b không tạo run khi nguồn bị từ chối: NOT VERIFIED.** Biên bản không ghi việc kiểm thư mục run.
- **W3.5b đuôi "và N câu khác": NOT VERIFIED trên DubFlow.exe.** Chỉ thấy trong lần phát lại run rc16 trên engine đóng gói của rc18, ngoài app.
- **W4.8b các dòng READY đúng của #802 vẫn READY: NOT VERIFIED trên DubFlow.exe.** Trong lượt W3 qua UI không câu nào READY. Chỉ có lần phát lại run rc16 trên engine của rc18, ngoài app (i1.u2 READY; i1.u1, i9.u1 nghiêm trọng; i16.u1 cần xem).
- **Phép đo độc lập không dùng bộ đo khác.** Phép đo độc lập dùng cùng FFmpeg n9.0.2 và cùng bộ đo loudnorm với ứng dụng; không có phép đo bằng công cụ khác (ví dụ `ebur128=peak=true` hoặc một trình đo BS.1770 khác).
- **W1d không có phép đo độc lập** trong biên bản; thẻ chỉ được đối chiếu với `qc_report.json`, và số −10,2 LUFS trên thẻ không đối chiếu được.
- **Trường `output_loudness` của `qc_report.json`** không được chép riêng trong biên bản; đối chiếu chỉ qua `conformance.true_peak_dbtp` và `result`.
- **Phạm vi quét khoá.** Biên bản chỉ nêu `out`, `logs`, `screenshots`, `tools`, `app\control`, `app\backgrounds`, fixture và `stage1`. Thư mục dữ liệu của ứng dụng ngoài hai thư mục trên, log của supervisor và worker, thư mục làm việc `.dubflow-work` và log sự cố không được nêu là đã quét: NOT VERIFIED.
- **Thư mục `mastering` là junction, thử lại khi tệp bận, dọn thư mục tạm của worker bị kill: NOT VERIFIED trên rc18.** Chỉ có CI Windows (4 test, run 43452281478) và lỗi chèn trên Linux.
- **Các trạng thái `failed`, `unmeasured`, `silent` của B2** không xảy ra trên rc18; chỉ được phủ bằng test.
- **Một số người đúng được CONFIRMED** không quan sát được trên DubFlow.exe trong lượt này: i2 thành báo động giả (phát hiện 6).
- **Mastering tắt: đầu ra không đổi so với rc17 hay base: NOT VERIFIED trên rc18** (W1.7b); chỉ có test `test_mastering_off_runs_no_trial_and_its_report_is_unchanged`.
- **Mọi lượt chạy do tự động hoá CDP điều khiển**; không người nào xem hay nghe các bản xuất.

## 18. Những điều không được hiểu sai

- Stage 1 PASS **không** phải bản phát hành stable và **không** phải Phase 4. rc18 là bản candidate với `production_qualified: false`; job publish bị bỏ qua.
- Stage 1 PASS không đổi các kết luận khác: Gate S (Documentary Auto) vẫn FAIL, Gate B phim dài vẫn FAIL, Phase 4 vẫn NO-GO.
- Mọi lượt W1–W4 trên rc18 do tự động hoá CDP điều khiển. Đây **không** phải người duyệt: không người nào xem hay nghe các bản xuất trong lượt này.
- Fallback B1 và hủy rồi tiếp tục với Lồng tiếng **không** được chạy trên Windows; B1 chỉ được phủ bằng unit test. Render lại Lồng tiếng với đầu vào không đổi chưa được kiểm (§17.1).
- OneDrive Files-On-Demand là N/A: chưa có tệp giữ chỗ OneDrive thật nào được thử, trên rc18 hay ở đâu khác.
- Lần phát lại run W3 của rc16 trên engine của rc18 không chạy qua DubFlow.exe; nó chỉ là bằng chứng phụ.
- "Kiểm tra cuối: đạt." sau khi người dùng xử lý từng câu **không** có nghĩa là khẳng định đã được xác nhận: i1, i2, i6, i7 được xuất vì thao tác chấp nhận (do tự động hoá CDP thực hiện trong nghiệm thu), và vẫn là UNVERIFIED hoặc CONTRADICTED trong `user_accepted`.
- Mức vượt trần của rc17 không tái hiện trên mix mới của cùng video; ca overshoot trên rc18 dùng input tổng hợp `dub-drama-30s-bursts.mp4`.
- CI xanh trên từng nhánh, lượt rà soát chéo, phép kiểm trước merge và lượt chạy thử phủ trên PC Windows **không** phải Windows E2E trên DubFlow.exe candidate.
- Bản ghép cục bộ `71573d7` và lượt chạy thử phủ dùng các head cũ; chúng không phải main và không phải nguồn của candidate.
- Các phát hiện của rà soát chéo "đã sửa trước candidate" nghĩa là đã merge kèm test. Trên rc18, R1, R2 và R4 đã được thấy chạy đúng; R3 và R5 thì chưa (§14).
- Quyết định 2–7 ở §15 là hành vi đang có trong mã, chưa có phê duyệt rõ ràng của chủ dự án.
- `qc_report.json` có `"status": "passed"` chỉ có nghĩa là báo cáo hợp lệ và render qua kiểm tra kỹ thuật. Đạt trần hay không nằm ở `mastering.conformance`; kết quả QC cuối nằm ở `mastering.result.final_qc` và `downgrade`.
- Người dùng duyệt hay ghim một cảnh **không** biến khẳng định thành CONFIRMED. Câu đã ghim còn không mang trạng thái khẳng định nào và không có trong bản ghi của Kiểm tra cuối (§16.3, C1).
