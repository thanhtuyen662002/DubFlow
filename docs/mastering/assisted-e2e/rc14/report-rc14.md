# Assisted Production Stabilization & Documentary Pilot Entry Gates — Windows E2E, DubFlow 1.1.96-rc14

- Date: 2026-11-12/13. Owner's Windows 11 machine, CPU only ("Chỉ dùng CPU" on; no NVIDIA GPU), one job at a time.
- Folder: `D:\Projects\DubFlow\_sandbox\assisted\rc14\` (this report, `results.json`, `screenshots\`, `logs\`, `tools\`, `investigate\`).
- rc14 = rc13 + #803 (issue #801): forward-only neutral filler weighs the frames it leaves to hold, and when every safe filler leaves a held frame, shorter shots that leave less are considered. Target: the rc13 W2 i1.u2 held frame.
- Every W was run from the start on rc14. rc13 numbers (`rc13\report-rc13.md`, `rc13\results.json`) are the baseline only. The harness is `rc13\tools` copied with rc13 → rc14; `score_w3.py` is byte-identical (`3bf42516…fc31`).
- Not done, by design: no release, publish, tag or stable install; the user's normal DubFlow install and data were not touched; `_sandbox\models-u` was not touched; no question was asked of the owner.
- AI key: the app read its own key from Windows Credential Manager (Claude, model model). I never printed, logged or wrote the key; scans report counts only. The profile path is redacted as `%USERPROFILE%`; 0 files under rc14 `logs`, `investigate` or `tools` contain it.

Labels: **UI** = the real rc14 `DubFlow.exe`, driven by real mouse/keyboard events over WebView2 CDP and Win32 messages for native dialogs; never Tauri IPC. This is CDP automation, **not human review**. **File check** = hashes, ffprobe/ffmpeg filters, run files, engine modules from the rc14 bundle run read-only with `python -B` (0 `.pyc` afterwards).

## Summary

| W | Verdict | Key numbers |
|---|---|---|
| W1 Product replanning | **PASS** | 4 approvals → Pin, Exclude, Replace on Câu 3, re-render after each: **4/4 kept, 0 INVALIDATED**; replanned 0 / 1 / 1 (u3 only); re-render 9.1 / 8.3 / 8.3 s. **Editor opens** after Final QC, behind a warning dialog for 1 unreviewed sentence; there is no QC block in the editor |
| W2 Compilation replanning | **PASS** | Rebuild after the exclusion: **dựng 1 phần (i1), dùng lại 7**, 12.4 s, **4/4 kept, 0 INVALIDATED**, vision asked 0. **HELD_FRAME 0 in every part** (rc13: 1 in i1). **i1 keeps its source-audio moment** (18000–21500 ms). Export: **"Kiểm tra cuối: đạt."**, final.mp4 written. i1 in the final has 0 frozen stretches ≥ 0.5 s |
| W5 on W2 (Pin i3.u2) | **FAIL** | "dựng 1 phần, dùng lại 7", 22.7 s, 0 INVALIDATED, HELD_FRAME 0. But the part re-rendered was **i1, not i3**, and **i1 lost its source-audio moment** (12990 → 9490 ms) with unchanged plan inputs. Cause: intro's multi-frame look split `i1-s1c4` (P2-N22 class) |
| W3 Long-form (Gate B) | **Gate B FAIL** | GT sha256 `afa979d2…f66497` = lock. **recall@5 46.7 % (28/60)** (rc12 31/60, rc13 33/60); **serious silent misses 0** (rc13: i1.u2, i16.u1); false SOURCE_INSUFFICIENT 0; HELD_FRAME 0. Draft 676 s, peak RAM 3.5 GB |
| W4 A/V per boundary | **PASS** | Synthetic 17/17 OK, 0 skipped, no adapter, encoder mpeg4. W2 compilation.mp4 and final.mp4 (export 2): **7/7 measured**, worst 33.3 ms (one frame), exit 0. W3: **21/21**, worst 0 ms, exit 0 |
| W5 Regression | **PASS** | Compilation v1 **byte-identical** `9afa62bc…c6d3` (= rc13). Cancel → `CANCELLED`, never COMPLETED. Key/e-mail scan **0/0/0**. (The resume case is the W5-on-W2 FAIL above.) |
| W6 Human validation | **BLOCKED** | No participant. Not simulated |

## 0. Build and install

| Item | Value |
|---|---|
| Actions run | `43279224888` "Windows Release": in progress at start, **success** at 18:46:19Z (waited for it) |
| Commit | `96400fb990097ae81df7c87fe1f4fa8039a4d58e` (= `release-evidence.json` `source_sha` = origin/main = #803 merge), version `1.1.96-rc14`, channel `candidate`, `production_qualified: false`, `unsigned-candidate` |
| Artifact | `DubFlow-1.1.96-rc14-windows-x64` (id 13973951823), via `tools\dl.py` (token only to api.github.com). Outer zip `fe12c0db…6995` = API digest |
| Zip SHA-256 | `b93c6604fbbd6fb0ae26d7d90d234c33ef5f8252f41f4b0f340427ab638a5f3c` = `release.sha256` = release evidence |
| `app\bin\DubFlow.exe` | `11effd5b5601ba98cccba82c3ba27d5986ac2593c5884e8fdc1a0430399b3573` |
| Install | Extracted with Python `zipfile` (8 765 entries) to `rc14\app\versions\1.1.96-rc14\`. `current.json` written (manifest `692d1c7b…e29e37`). Models copied from the rc13 sandbox without `tts-sentence-cache` (1 747 MB). Data root `rc14\app` |
| CDP proof | `logs\cdp-proof-processes.txt`: DubFlow.exe pid 9212; CDP browser `msedgewebview2.exe` pid 19020 (`remote-debugging-port=9449`, no `--type`); its parent is `…\rc14\app\versions\1.1.96-rc14\app\bin\DubFlow.exe`; user-data-dir `…\rc14\app\control\webview2\312e…3134\EBWebView`. **PROOF OK** |
| Footer | "Phiên bản **v1.1.96-rc14** candidate" |
| Settings (UI) | "Chỉ dùng CPU" on (`01-settings-cpu.png`). Output folder `rc14\out`, then `rc14\c3v1` for W5 v1, then back, via the native folder dialog. AI provider Claude (already saved) |

## W1 — Product replanning (P2-N5)

Run `run-d8b6cb492cf1`: Bán hàng "Máy pha espresso tay cầm", 12.900.000đ, `media\espresso\clip-01..04.mkv`, script 1, Toàn màn hình, Trúc Ly, Cân bằng, vision Claude (same inputs as rc13).

| # | Step | How | Result |
|---|---|---|---|
| 1 | Draft | UI | COMPLETED in **161 s** (rc13 146 s); 6 sentences to review (u1–u6 MATCH_AVAILABLE / NEEDS_REVIEW) |
| 2 | Approve u1, u4, u5, u6 | UI | 4 APPROVE / USER_APPROVED |
| 3 | Pin u3 (`v1c2`) + re-render | UI | **9.1 s**; kept 4/4, INVALIDATED 0, replanned 0 |
| 4 | Exclude u3 2nd shot (`v2c2`) + re-render | UI | **8.3 s**; kept 4/4, INVALIDATED 0, replanned u3 only. (First attempt: the panel had closed after step 3, so the click found no button and nothing was recorded; I reopened "Duyệt cảnh" and repeated the step) |
| 5 | Replace u3 ("Chọn cảnh khác…" → "Dùng cảnh này", `v1c8`) + re-render | UI | **8.3 s**; kept 4/4, INVALIDATED 0, replanned u3 only (`v1c8, v1c2, v1c4`) |
| 6 | Editor after Final QC (step NOT RUN on rc13) | UI "Chỉnh sửa" | A dialog appears first: **"Còn câu chưa duyệt cảnh — 1 câu có cảnh AI chưa chắc chắn và bạn chưa duyệt…"** (u2), with buttons "Để tôi duyệt trước" / "Vẫn mở". With "Vẫn mở" the editor opens: **"Chỉnh sửa · Máy pha espresso tay cầm · video 1"**, timeline "1 đoạn · 0:25". **The editor shows no QC block**; the only QC signal is that dialog. `W1-5-editor.png` |

Records: 7 (4 APPROVE, PIN, EXCLUDE, REPLACE), 12 fields each, `run_id` = folder, 0 INVALIDATED.

Isolation observation: the candidate's Bán hàng list also shows two older runs ("Nồi chiên không dầu Lumi 5 lít", 01/11 and 02/11) from `%USERPROFILE%\Videos\DubFlow Output`, the default output folder of the normal install, although the output folder is `rc14\out`. I only read the list; nothing there was opened or changed.

## W2 — Compilation replanning (#801 / #803)

Run `run-c488afb28448`: same programme as rc12/rc13 (items m2, m3, m4, m5, m1; m2-lop-hoc in i1; titles, narration, hook/intro/outro, i5 source sound on, licensed, Trúc Ly, Cân bằng, vision on).

| # | Step | How | Result |
|---|---|---|---|
| 1 | Draft | UI | COMPLETED in **240 s** (rc13 255 s); 8 parts; **HELD_FRAME 0**. Difference from rc13: the multi-frame looks split `i1-s1c1` (0–6000 ms) into `i1-s1c1f1/f2/f3`, and i1.u1 used all three |
| 2 | Approve i2 and outro | UI | 4 USER_APPROVED |
| 3 | Exclude in i1 | UI | The rc12/rc13 exclusion was `i1-s1c1` (0–6000 ms). In rc14 that footage is three sub-shots, so I pressed "Bỏ cảnh này" on all three shots of i1.u1 → EXCLUDE `i1-s1c1f1`, `f2`, `f3` (3 records). This is the same footage as before, but three user actions instead of one |
| 4 | "Dựng lại các phần đã đổi" | UI | **12.4 s**; "Lần dựng gần nhất: **dựng 1 phần, dùng lại 7**"; only `part-i1.mp4` rewritten |
| 5 | Provenance | file check | hook, intro, i3, i4, i5: plan byte-identical. i2: plan `reasons` text only (geometry, provenance and receipt unchanged). outro: provenance `inputs.part` changed (`shown`), geometry and receipt unchanged → reused. i1: changed (the user's exclusion). Vision asked 0 in every part. **Nothing unexpected re-rendered** |
| 6 | Approvals | file check | **4/4 kept, 0 INVALIDATED** |
| 7 | Final QC per part | file check | **HELD_FRAME: hook 0, intro 0, i1 0, i2 0, i3 0, i4 0, i5 0, outro 0.** Other lines: render ok (compilation.mp4), duration "chưa đo", captions ok (last caption ends at 75690 ms), audio "chưa đo đỉnh âm", timeline ok ("không có khoảng trống, chồng lấn, đoạn âm hay khung đứng"), pending ok, decisions "10 câu chưa duyệt" (before step 8), claims ok |
| 8 | i1 plan | file check | 12990 ms: u1 6000–7950, 10050–12000, 12000–12765; u2 12765–17590; **moment 18000–21500 (reaction, source sound)**. **i1 keeps its source-audio moment** |
| 9 | Settle the rest + export | UI | Approved hook, intro, i1, i3, i4, i5. "Xuất bản cuối…" → **"Bản cuối (lần xuất 1): …\final.mp4 — Kiểm tra cuối: đạt."** final.mp4 sha256 `4161e913…c4da` (= compilation.mp4), h264 1920×1080 30 fps + aac, 76.4 s. The stale line "Còn 10 câu ở 6 phần…" still showed for an instant before export (P3-N21) |
| 10 | Watching i1 in the final (8.4–21.4 s of the programme) | ffmpeg on final.mp4 (automation, not human review) | Contact sheet at 2 fps (`W2-i1-final-sheet.png`): the "#5 Cỗ máy domino trong lớp học" strip, then classroom footage of students behind tables with the domino run, subtitles following the narration, moving picture throughout. From about 9.5 s into i1 there is a different angle of the room with no subtitle (the source-audio moment). `freezedetect` (noise 0.003, ≥ 0.5 s): **0 frozen stretches**. Scene cuts at 1.97 s (6000–7950 → 10050), 9.5 s (moment start) and 10.17 s (a camera move inside the moment) |

**W5 on W2: Pin i3.u2 + "Dựng lại các phần đã đổi"** (UI): 22.7 s, "dựng 1 phần, dùng lại 7", **0 INVALIDATED**, Final QC READY_TO_FINALIZE, HELD_FRAME 0 everywhere.
- **The part re-rendered was i1, not i3.** The pin only changed i3's segment 2 `needs_review`; i3's receipt was unchanged, so it was reused.
- **i1 was re-planned with unchanged `provenance.inputs` and lost its moment**: 12990 → 9490 ms. The same narration pieces remain; the reaction segment 18000–21500 is gone.
- Read-only replay (rc14 bundle, cached vision only, 0 network asks; `investigate\w2\`): intro's multi-frame look now covers `i1-s1c4` (@18525, @20976). That splits c4 (18000–21502) into c4f1/f2/f3 of 1138–1225 ms each, so no candidate after 17590 is ≥ MOMENT_MIN 2000 ms and `_moment` returns None. In the draft and the W2 rebuild c4 was whole.
- The look moved after the approvals (approved shots are locked and not looked at again, so the multi-frame budget went to c4). This is the P2-N22 class (#796): shared vision state re-plans an item without any change to that item.
- Export 2 after this: "Kiểm tra cuối: đạt.", final.mp4 sha256 `48954053…d88` (i1 without its moment).

Verdict W2: **PASS** for the directive's W2 steps (re-render set, approvals, HELD_FRAME 0, moment kept, export passes). **W5-on-W2: FAIL** (an unrelated pin silently re-planned and re-rendered i1 and dropped its moment).

## W3 — Long-form evidence (Gate B)

| # | Step | How | Result |
|---|---|---|---|
| a | Ground truth | file check | Copied byte for byte from rc13; `W3-ground-truth.json` **`afa979d239b6a68be03349308e233809efddc84cd2ed13e0d869913c51f66497`** = first token of the lock, checked at 19:12:45Z before the programme; lock `6e07cee4…`, narrations `0c73f3ca…`. Not edited. Film sha256 `834cf4f3…` unchanged |
| b | Programme | UI (`ffill.ps1`) | "Thêm mới", leftover items removed, hook cleared; 20 items, kind Danh sách, 105 s windows, narrations **20/20 equal**, rights permitted, vision on, no target and no explicit budget (`logs\W3-form.json`, `W3-form-check.txt`) |
| c | Draft | UI | COMPLETED in **676 s** (rc13 721 s); 22 parts, all content; peak RAM **3 534 MB** |
| d | vision_report | file check | Budget **142** ("auto: 2 × 71 câu, tối thiểu 80, tối đa 400"), **139 used**, asked 139; **broad 95**; **targeted 2 per part** (i1–i20 and outro; 42 in all); **multi_frame 2** (intro only), 0 in every item. Coverage `milli`: i1 315, i2 194, i3 240, i4 179, i5 254, i6 161, i7 189, i8 122, i9 430, i10 124, i11 122, i12 271, i13 115, i14 236, i15 235, i16 262, i17 117, i18 147, i19 310, i20 191, intro 139, outro 237 (`logs\W3-vision-report.json`) |
| e | Score | `score_w3.py --lock` | Lock check passed; 64 scored, 0 unmatched; **recall@5 = 0.4667 (28/60)**; chosen-hit 0.383; **serious silent misses 0**; **false SOURCE_INSUFFICIENT 0**; 4/4 missing flagged; READY 6, NEEDS_REVIEW 42, MATCH_NOT_FOUND 16 |
| f | vs rc13 | file check | Gained hit@5: i8.u1, i14.u2. Lost: i1.u1, i3.u3, i4.u2, i9.u2, i12.u3, i17.u2, i17.u3. rc13's silent misses i1.u2 and i16.u1 are not silent in rc14 |
| g | Final QC | file check | **HELD_FRAME 0** in all 22 plans; state REVIEW_PENDING. The UI acceptance + export was not run (not asked) |

**Gate B: FAIL.** recall@5 is 46.7 % against ≥ 85 %; it moves between runs (rc12 51.7, rc13 55.0, rc14 46.7) with the same per-part look budget (2 targeted looks per item, no multi-frame). The rc13 investigation found that the vision answers differ between cold-cache runs. Nothing was tuned, and the ground truth was not changed.

## W4 — A/V per boundary

| # | Step | Result |
|---|---|---|
| 1 | Synthetic: `tests/mastering/test_av_boundaries.py` from origin/main `96400fb9`, unchanged, rc14 bundle python/ffmpeg, `DUBFLOW_AV_REPORT=logs\W4-synthetic.jsonl` | **17 run, 17 OK, 0 skipped, 0 failed** (62 s), encoder mpeg4, no adapter. Worst: CFR/VFR/mixed 0.7 ms; silent/stills/short 13.3 ms; 10-part join 0.7 ms (29/29); reference method 19.0 ms (9/9); baselines still fail as intended (267.3 ms with 10 unmeasured; 55.0 ms) |
| 2 | W2 `compilation.mp4` (after the W5 pin) | **exit 0, 7 measured, 0 unmeasured**; audio 0 at every join; av: J1 −33.3, J2 0, J3 0, J4 0, J5 0, J6 −33.3, J7 0; worst 33.3 ms; passed (`logs\W4-joins-W2-compilation.json`) |
| 3 | W2 `final.mp4` (export 2, after the pin) | identical: exit 0, 7/7, worst 33.3 ms (`logs\W4-joins-W2-final.json`). Export 1's final (before the pin) could not be probed per join afterwards, because `part-i1.mp4` was re-rendered by the pin step (export 1 = compilation at that time, sha `4161e913…`) |
| 4 | W3 `compilation.mp4` | **exit 0, 21 measured, 0 unmeasured, av 0.0 ms at every join** (`logs\W4-joins-W3.json`) |

Thresholds unchanged (per join ≤ max(40 ms, one frame), cumulative ≤ 80 ms). rc13: W2 7/7 (worst 33.3), W3 21/21 (0).

## W5 — Regression

| # | Case | Result |
|---|---|---|
| 1 | Compilation v1 (UI, `rc14\c3v1`, "Ghép thử hai phim ngắn" → "Dùng lại thiết lập này cho bản mới") | COMPLETED in 45 s; `run-3a23934d9b98\compilation.mp4` = **`9afa62bcfb3ae46343d28f606b39a5d68db722c7285582c1112c7e9a1002c6d3`** = rc13 = rc12 = rc10 |
| 2 | Resume / re-render one part | see W5 on W2: one part re-rendered, 7 reused, but it was the wrong part (i1) → **FAIL** recorded under W2 |
| 3 | Cancel (UI, 3-script Bán hàng, "Hủy" 20 s in) | RUNNING ("Đang phân tích cảnh cho video 1/3…") → **CANCELLED "Đã hủy."**, still CANCELLED 35 s later, never COMPLETED; 0 videos; 0 engine processes left. Button read **"Tạo 2 video"** with 3 scripts (P3-N13) |
| 4 | Records | W1 7, W2 18 (14 APPROVE, 3 EXCLUDE, 1 PIN); all 12 fields; `run_id` = folder; **0 INVALIDATED** |
| 5 | Sources | 30/30 media equal rc13; film unchanged |
| 6 | Key / e-mail scan (app closed) | 11 365 files, 4.94 GiB, 0 unreadable: **claude 0, gemini 0, email 0**; self-test PASS (`logs\keyscan.txt`) |

## W6 — Human validation

**BLOCKED**: no participant. Not simulated.

## Performance vs rc13

| Measure | rc13 | rc14 |
|---|---|---|
| Espresso draft (W1) | 146 s | 161 s |
| Product re-render (W1) | 9.8 / 9.3 / 9.3 s | 9.1 / 8.3 / 8.3 s |
| Top 5 draft (W2) | 255 s | 240 s |
| Partial re-render (W2) | 12.1 s (1 part) | 12.4 s (1 part) |
| Pin re-render (W5 on W2) | 31 s | 22.7 s |
| Long-form draft (W3) | 721 s | 676 s |
| Peak RAM (host + webview + engine) | W1 3 631, W2 3 113, W3 3 485 MB | W1 3 729, W2 3 194 (until W3 start), W3 3 534 MB |

## Defects

| ID | Sev | Status | What |
|---|---|---|---|
| P2-N17 / #801 | P2 | **Fixed in the W2 case** | After the exclusion: 0 HELD_FRAME, moment kept, export passes |
| P2-N22 (#796) | P2 | **Open, reproduced via approvals** | After approvals in hook/intro/i1, an unrelated pin rebuild re-planned i1 (unchanged inputs): intro's multi-frame look split `i1-s1c4`, and i1 lost its 3.5 s source-audio moment silently (re-rendered, 0 INVALIDATED, export still "đạt") |
| Gate B / P2-N11 | P1 gate | **FAIL** | recall@5 46.7 % (28/60); 0 silent misses; 0 false SI |
| Note | P3 | New observation | Repeating "exclude shot X" needs 3 clicks when a multi-frame look has split X into f1–f3 |
| Note | P3 | New observation | The candidate's Bán hàng list shows runs from the default `%USERPROFILE%\Videos\DubFlow Output` even with the output folder set elsewhere (read only) |
| P3-N21 | P3 | Open | "Còn 10 câu ở 6 phần…" shown after every sentence was approved, until export |
| P3-N13 | P3 | Open | "Tạo 2 video" with 3 scripts |

## Evidence separation

| Kind | What |
|---|---|
| CI | Run 43279224888 success |
| UI (real rc14 DubFlow.exe via CDP + Win32 dialogs; automation, not human review) | Settings; W1 all steps incl. editor; W2 1–4, 9; W5 pin; W3 b–c; W5 v1, cancel |
| File checks | snapshots, plan/editorial/review/receipt/status files, `score_w3.py`, bundle `final_qc`/`quality`/`sync_probe`, the synthetic test, ffmpeg freezedetect/scene on final.mp4, the read-only replay, hashes, scans |
| Not run | W6; W3 UI acceptance + export; per-join probe of export 1's final (its i1 part was re-rendered); W5 3-video approval flow, dubbing, engine kill |
