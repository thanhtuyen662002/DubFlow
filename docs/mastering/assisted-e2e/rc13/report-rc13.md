# Assisted Production Stabilization & Documentary Pilot Entry Gates — Windows E2E, DubFlow 1.1.96-rc13

- Date: 2026-11-12/13. Owner's Windows 11 machine, CPU only ("Chỉ dùng CPU" on; no NVIDIA GPU), one job at a time.
- Folder: `D:\Projects\DubFlow\_sandbox\assisted\rc13\` (this report, `results.json`, `screenshots\`, `logs\`, `tools\`, `investigate\`).
- rc13 = rc12 + #797 (P2-N17: an excluded shot is never counted ahead of the moment, a moment is never bought with held frames; P2-N18: each part's vision share is fixed), #798 (a sentence showing one shot twice keeps its approval), #799 (P2-N19: sync_probe matches joins on onsets; P3-N20: test blur from FFmpeg). Known, not fixed: #796 / P2-N22 (no intro/outro/hook edit was made in W2).
- Every W was run from the start on rc13. rc12 numbers are used only as the baseline. The harness is `rc12\tools` copied with rc12 → rc13; `score_w3.py` is byte-identical (`3bf42516…fc31`). The new scripts are `w2new.ps1`, `dwait.ps1`, `rebuild.ps1` and `pcancel.ps1`.
- Not done, by design: no release, publish, tag or stable install; the user's normal DubFlow install and data were not touched; `_sandbox\models-u` was not touched; no question was asked of the owner.
- AI key: the app read its own key from Windows Credential Manager (Claude, model model). I never printed, logged or wrote the key; scans report counts only. The profile path is redacted as `%USERPROFILE%`, and 0 written files under rc13 `logs`, `investigate` or `tools` contain it. Disclosures, console only, never written to a file: the Accounts tab text showed the app's own masked key hint (last 4 characters), and the Save-folders tab text showed the download folder path, which contains the profile name. No screenshot was taken of either tab.

Labels: **UI** = the real rc13 `DubFlow.exe`, driven by real mouse/keyboard events over WebView2 CDP (`Input.dispatchMouseEvent`, `Input.insertText`) and Win32 messages for native file/folder dialogs; never Tauri IPC. This is CDP automation, **not human review**. **File check** = hashes, ffprobe, run files, engine modules from the rc13 bundle run read-only with `python -B` (0 `.pyc` in the bundle afterwards).

## Summary

| W | Verdict | Key numbers |
|---|---|---|
| W1 Product replanning | **PASS** | 4 approvals → Pin, Exclude, Replace on Câu 3, re-render after each: **4/4 kept, 0 INVALIDATED** at every step. Units replanned 0 / 1 / 1 (u3 only). 1 part (video-1) re-rendered per step: 9.8 / 9.3 / 9.3 s (rc12 10.9 / 10.4 / 10.9) |
| W2 Compilation replanning | **FAIL (export)**; replanning **PASS** | Same programme and edits as rc12 (approve i2 + outro, exclude `i1-s1c1`). "Dựng lại các phần đã đổi": **dựng 1 phần, dùng lại 7** (only i1; rc12 i1 + i4), 12.1 s, **4/4 kept, 0 INVALIDATED**. i3, i4, i5 plans are byte-identical and 0 vision asks were made (P2-N18 fixed here). Final QC: **HELD_FRAME 1** in i1 (i1.u2, 1126 ms; rc12 had 5). Export: "Chưa xuất. Kiểm tra cuối chưa đạt. Dòng thời gian: i1: HELD_FRAME i1.u2 giữ một khung hình 1126 ms". i1 also lost its own-sound moment |
| W5 on W2 (Pin i3.u2) | **PASS** | dựng 1 phần (i3), dùng lại 7, 31 s; **0 INVALIDATED** (rc12 wrongly voided i1.u1 as plan_changed) |
| W3 Long-form (Gate B) | **Gate B FAIL** | GT sha256 `afa979d2…f66497` = lock (checked before the programme). **recall@5 55.0 % (33/60)**, rc12 51.7 % (31/60); target ≥ 85 %. **Serious silent misses 2** (i1.u2, i16.u1; rc12 0). **False SOURCE_INSUFFICIENT 0**. **HELD_FRAME 0**. Draft 721 s, peak RAM 3.5 GB |
| W4 A/V per boundary | **PASS** | Synthetic test from main, unchanged, no adapter: **17/17 OK, 0 skipped**, encoder `mpeg4`. Real: W2 **7/7 joins measured**, worst \|A/V\| **33.3 ms** (one frame), exit 0. W3 **21/21 measured**, worst **0 ms**, exit 0. rc12: 0/28 measured |
| W5 Regression | **PASS** | Compilation v1 **byte-identical** `9afa62bc…c6d3` (= rc12/rc10). Resume reuses the rest (above). Cancel → `CANCELLED`, never COMPLETED. Key/e-mail scan **0/0/0** |
| W6 Human validation | **BLOCKED** | No participant. Not simulated |

## 0. Build and install

| Item | Value |
|---|---|
| Actions run | `43250339299` "Windows Release": in progress at 15:41Z, **success** at 16:16:50Z (waited for it) |
| Commit | `f46723aa93553da321e480e6cc0d6e62b6d9a08e` (= `release-evidence.json` `source_sha` = origin/main = #799 merge), version `1.1.96-rc13`, channel `candidate`, `production_qualified: false`, `unsigned-candidate` |
| Artifact | `DubFlow-1.1.96-rc13-windows-x64` (id 13961841624), via `tools\dl.py` (16 range requests; token sent only to api.github.com). Outer zip `3f7eeb7b…bfad1` = API digest |
| Zip SHA-256 | `fc3861735e3dcb121987cbc15528d5f3f3be8d3024de061afbee6f983824d366` = `release.sha256` = release evidence |
| `app\bin\DubFlow.exe` | `a9b10f1e08093ad1b5027b6f2716fca6aacb1e4e6c6720af5b8575524c740775` |
| Install | Extracted with Python `zipfile` (8 765 entries) to `rc13\app\versions\1.1.96-rc13\`. `current.json` written (manifest `b7b2fb8d…169b65e`). Models copied from the rc12 sandbox without `tts-sentence-cache` (1 747 MB). Data root `rc13\app`. 0 `.pyc` |
| CDP proof | `logs\cdp-proof-processes.txt`: launch 1, DubFlow.exe pid 12152. CDP browser `msedgewebview2.exe` pid 4196 (`remote-debugging-port=9449`, no `--type`); its parent is `…\rc13\app\versions\1.1.96-rc13\app\bin\DubFlow.exe`. user-data-dir `…\rc13\app\control\webview2\312e…3133\EBWebView`, page `http://tauri.localhost/`. **PROOF OK** |
| Footer | "Phiên bản **v1.1.96-rc13** candidate" |
| Settings (UI) | "Chỉ dùng CPU" switched on (`01-settings-cpu.png`). Output folder `rc13\out`, then `rc13\c3v1` for W5 v1, then back to `rc13\out`, always through the native folder dialog. AI provider Claude (already saved) |

## W1 — Product replanning (P2-N5)

Run `run-56303fae910a`: Bán hàng "Máy pha espresso tay cầm", 12.900.000đ, clips `media\espresso\clip-01..04.mkv` (same inputs as rc12), script 1, Toàn màn hình, Trúc Ly, Cân bằng, vision Claude.

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Draft | UI (`pnew.ps1`) | COMPLETED in **146 s** (rc12 156 s). u1 NOT_NEEDED, u2–u6 MATCH_AVAILABLE, all NEEDS_REVIEW; queue "6 cần xem" (rc12: u2 READY, "5 cần xem · 1 tuỳ chọn") | `logs\W1-log.txt`, `snap-W1-00-draft.json` |
| 2 | Approve 4 | UI (`act.ps1`) | u1, u4, u5, u6 → 4 APPROVE / USER_APPROVED (as rc12) | `logs\W-review-log.txt`, `snap-W1-0-approved4.json` |
| 3 | Pin u3 (shot 1 `v1c2`) + re-render | UI | **9.8 s**; kept **4/4**, INVALIDATED **0**, replanned **0** | `snap-W1-1-pin.json`, `W1-1-pin.png` |
| 4 | Exclude u3 2nd shot (`v2c2`) + re-render | UI | **9.3 s**; kept 4/4, INVALIDATED 0; replanned **u3 only** (`v2c2` → `v2c3`, pinned `v1c2` kept) | `snap-W1-2-exclude.json` |
| 5 | Replace u3 ("Chọn cảnh khác…" → "Dùng cảnh này" on the first alternative, `v1c8`) + re-render | UI | **9.3 s**; kept 4/4, INVALIDATED 0; replanned **u3 only** (`v1c8, v1c2, v3c4`) | `snap-W1-3-replace.json`, `W1-3-replace-picker.png` |

Records: 7 (4 APPROVE, PIN, EXCLUDE, REPLACE), 12 contract fields each, `run_id` = folder name, 0 INVALIDATED. Same counts as rc12. Not re-run this time: the editor-opens check (rc12 step 7).

## W2 — Compilation replanning (P2-N17, P2-N18)

Run `run-9efd240d4592`: raw Tổng hợp "Top 5 cỗ máy phản ứng dây chuyền ấn tượng" (top_n, countdown), items added in rc12's order m2, m3, m4, m5, m1 (`media\top5`, same files; m2-lop-hoc in i1), titles, narrations and hook/intro/outro as rc12 (`cfill.ps1`), i5 source sound on, licensed, Trúc Ly, Cân bằng, **vision on** ("Dùng AI để xem khung hình" ticked, as in rc12). Form logged in `W2-log.txt`; `W2-01-form.png`.

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Draft | UI (`w2new.ps1`, `dwait.ps1`) | COMPLETED in **255 s** (rc12 263 s); 8 parts, "dựng 8 phần, dùng lại 0"; 14 sentences to review. Difference from rc12: outro now uses i5 shots (`i5-s1c1`, `i5-s1c11`), not `i1-s1c1` | `snap-W2-0-*.json` |
| 2 | Approve i2 and outro (as rc12) | UI (`cpart.ps1`) | i2.u1, i2.u2, outro.u1, outro.u2 USER_APPROVED | `W2-review-log.txt` |
| 3 | Exclude in i1 | UI | i1.u1 "Bỏ cảnh này" → EXCLUDE `i1-s1c1` (the shot rc12 excluded) | `W2-review-log.txt`, `logs\W2-pre\review-i1.json` |
| 4 | "Dựng lại các phần đã đổi" | UI (`rebuild.ps1`) | **12.1 s**; "Lần dựng gần nhất: **dựng 1 phần, dùng lại 7** phần không đổi". Only `part-i1.mp4` rewritten (mtime) | `W2-log.txt`, `W2-parts-mtime-*.txt` |
| 5 | Provenance | file check (`logs\W2-pre` vs after) | **i1**: plan, provenance and receipt key changed (the user's exclusion). **i2**: provenance and receipt key unchanged, so it was reused. Only segment `reasons`/`confidence_milli` text in plan-i2 changed: its own shots were locked by the approvals, so it took fewer multi-frame looks. **outro**: `inputs.part` changed (`shown` includes i1's new choice), receipt key unchanged, reused. **hook, intro, i3, i4, i5**: plan byte-identical. Vision counters (cumulative): i3 58→54, i4 62→58, i5 66→62 frames, **asked 0** in every part (no new vision call). So the lower spend in i1/i2 no longer changes another part's looks (rc12: i4 got 2 new frames and was re-planned) | `logs\W2-pre\`, rebuild check in this report |
| 6 | Approvals | file check (`snap-W2-2-post-*.json`) | **4/4 kept, 0 INVALIDATED**; re-reviews 0; units replanned i1.u1, i1.u2 only | `snap-W2-2-post-*.json` |
| 7 | Settle the rest | UI | Approved hook, intro, i1, i3, i4, i5 (i5.u2 is MATCH_AVAILABLE in rc13; rc12 marked it "Không có cảnh phù hợp") | `W2-review-log.txt` |
| 8 | Export | UI `watchexport.mjs` "Xuất bản cuối…" | At 12 ms (stale line, P3-N21 still present): "Còn 10 câu ở 6 phần cần xem lại…". At 4.6 s: **"Chưa xuất. Kiểm tra cuối chưa đạt. Dòng thời gian: i1: HELD_FRAME i1.u2 giữ một khung hình 1126 ms"**. No final.mp4 | `W2-export-line-watch.json`, `W2-03-export-qc-heldframe.png` |
| 9 | Final QC detail | file check (bundle `final_qc` + `quality.timeline_problems`) | HELD_FRAME per part: hook 0, intro 0, **i1 1**, i2 0, i3 0, i4 0, i5 0, outro 0. Other lines: render ok (compilation.mp4), duration "chưa đo", captions ok (last caption ends at 71590 ms), audio "chưa đo đỉnh âm", pending ok, decisions "10 câu chưa duyệt" (before step 7), claims ok | `W2-qc-post-rebuild.json` |

Expected vs found: only the parts the user changed should be rebuilt (i1 = exclusion; i2 and outro only had approvals, which do not change their footage). Outro does not show a changed shot. Found: **only i1** re-rendered, so no unexpected item. P2-N18 is fixed for this case.

P2-N17 is fixed only in part. Before the rebuild, i1 played 0–4665 and its moment at 13950–16050, with **0 HELD_FRAME** on the draft. After the exclusion, the plan runs u1 from 12000 and u2 forward to 21502, then holds `i1-s1c2f1` for 1126 ms. It has **no source moment** any more (duration 9490 ms instead of 11590). Root cause, from a read-only replay (`investigate\answer.md`): forward-only `align._assign` starts u1 at its best match (12000), so the 6 s of unexcluded footage before it (6000–12000) can only come back as a repeat, and u2 skips `c4f1` (18000–19138, 1138 ms ≥ 1126). The moment-first plan held 7540 ms, so "never buy a moment with held frames" correctly rejected it.

**W5 on W2: Pin i3.u2** (UI, "Ghim" on shot 1) then "Dựng lại các phần đã đổi": **31 s, dựng 1 phần (i3), dùng lại 7**. Only `part-i3.mp4` was rewritten. **0 INVALIDATED records** (i1.u1, whose approval rc12 voided as `plan_changed`, keeps USER_APPROVED). Plans of hook, i2, i4, i5 and outro differ only in `reasons`/`confidence_milli` text (geometry identical, receipts identical, reused). `logs\W5-resume-log.txt`, `logs\W5-pre\`.

## W3 — Long-form evidence (P2-N11, Gate B)

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| a | Source | file check | `phase2-e2e\footage\long\Why_We_Fight.5a.The_Battle_Of_Russia_Part1.webm` sha256 `834cf4f3…b3c32b` before and after (unchanged); 20 hard links `media\long\wwf-01..20.webm` (= rc12) | `logs\W3-source-hash.txt`, `media-hashes-after.json` |
| b | Ground truth | file check | Copied byte for byte from rc12 (= rc11): `W3-ground-truth.json` **`afa979d239b6a68be03349308e233809efddc84cd2ed13e0d869913c51f66497`** = first token of the lock (checked at 16:32:40Z, before the programme); lock `6e07cee4…`, narrations `0c73f3ca…`. Not edited | `logs\W3-gt-check.txt` |
| c | Programme | UI (`ffill.ps1`) | "Thêm mới", the 5 leftover W2 items removed with "Xóa mục", hook cleared; 20 items via the native dialog, kind **Danh sách**, item k = wwf-k, 105·(k−1)–105·k, narrations **20/20 equal** to `W3-narrations.json`, notes empty, rights permitted, intro/outro from rc8 inputs, Trúc Ly, Cân bằng, vision on, **no target duration, no explicit vision budget** | `logs\W3-form.json`, `W3-form-check.txt`, `W3-01-form.png` |
| d | Draft | UI (`dwait.ps1`) | COMPLETED in **721 s** (rc12 781 s); 22 parts, all mode content; peak RAM **3 485 MB** (python 1 577 MB) | `logs\W3-log.txt`, `W3-ram.csv` |
| e | vision_report | file check | Program budget **142** ("auto: 2 × 71 câu, tối thiểu 80, tối đa 400"), **139 used**, all asked (cold cache); **broad 95** before planning; **targeted 2 per part** (intro 2, i1–i20 2 each, outro 2 = 44); **multi_frame 0** in every part. Coverage `milli`: intro 144, i1 402, i2 194, i3 237, i4 179, i5 254, i6 161, i7 189, i8 122, i9 430, i10 124, i11 122, i12 271, i13 115, i14 236, i15 235, i16 323, i17 117, i18 158, i19 310, i20 191, outro 242 | `logs\W3-vision-report.json` |
| f | Score | `python tools\score_w3.py logs\W3-ground-truth.json <run> --lock logs\W3-ground-truth.lock` | Lock check passed; 22 logs, 64 scored, 0 unmatched; **recall@5 = 0.550 (33/60)**; chosen-hit 0.433; **serious silent misses 2: i1.u2, i16.u1** (READY, chosen shots miss the ground truth, though a hit is in the top 5); **false SOURCE_INSUFFICIENT 0**; 4/4 missing cases flagged; states READY 7, NEEDS_REVIEW 42, MATCH_NOT_FOUND 15 | `logs\W3-score.json` |
| g | vs rc12 | file check | Gained hit@5: i4.u2, i9.u2, i12.u3, i16.u1, i17.u2, i17.u3. Lost: i8.u1, i14.u3, i17.u1, i18.u1. i1.u2 was READY + chosen hit in rc12, READY + chosen miss in rc13. i16.u1 was NEEDS_REVIEW + miss in rc12, READY + chosen miss in rc13 | — |
| h | Final QC | file check | Timeline check passed for all 22 plans: **HELD_FRAME 0**, no gap/overlap/empty. `final_qc` state REVIEW_PENDING (sentences open). The UI acceptance + export (done in rc12) was **NOT RUN** on rc13: not part of this directive's W3 list | `logs\W3-qc.json` |

**Gate B: FAIL.** recall@5 is 55.0 % against a target of ≥ 85 % (+3.3 points vs rc12). Serious silent misses are 2 against a target of 0 (rc12 had 0). False SOURCE_INSUFFICIENT is 0 and HELD_FRAME is 0. Nothing was tuned, and the ground truth was not changed. As in rc12, each 105 s part gets about 4–5 broad looks plus 2 targeted looks and no multi-frame look, so most of each item's footage is never seen by vision.

## W4 — A/V per boundary (P2-9, P2-N19, P3-N20)

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Synthetic | `tests/mastering/test_av_boundaries.py` from origin/main `f46723aa` (= rc13 commit), **unchanged, no adapter**, rc13 bundle python `-B`, `PYTHONPATH=<bundle>\app`, `DUBFLOW_TEST_FFMPEG/FFPROBE` = bundle `runtime\media`, `DUBFLOW_AV_REPORT=logs\W4-synthetic.jsonl` | **17 run, 17 OK, 0 skipped, 0 failed** (68 s). Encoder **mpeg4** (bundle FFmpeg has no libx264). The blur now comes from `render.available_blur` (P3-N20 fixed). Worst per-boundary A/V: CFR 24/25/29.97, VFR, mixed 0.7 ms; silent/stills/short 13.3 ms; 10-part join 0.7 ms (29 measured, 0 unmeasured); reference method on the new join 19.0 ms (9/9). Baselines still fail as intended (old copy join 267.3 ms, 10 unmeasured; old join reference 55.0 ms) | `logs\W4-synthetic-unittest.txt`, `logs\W4-synthetic.jsonl` |
| 2 | Real, W2 | `sync_probe --final compilation.mp4 --parts <8 parts in programme order> --fps 30` (bundle python/ffmpeg; no final.mp4, export refused) | **exit 0**; **7 measured, 0 unmeasured**; audio_ms 0 at every join; video_ms/av_ms: J1 −33.3, J2 0, J3 0, J4 0, J5 0, J6 −33.3, J7 −33.3; **worst \|av\| 33.3 ms** ≤ max(40, 33.3); passed | `logs\W4-joins-W2.json` |
| 3 | Real, W3 | same, 22 parts | **exit 0**; **21 measured, 0 unmeasured**; audio 0, video 0, **av 0.0 ms at all 21 joins**; passed | `logs\W4-joins-W3.json` |
| 4 | vs rc12 | — | rc12: 0/28 measured (exit 2). rc12 investigate variant (a)+(b)+(c): 28/28, 26 × 0.0 and 2 × −33.3 (W2 J1, J6). rc13: 28/28 measured, W2 −33.3 at J1, J6, J7 (video one frame, audio 0); W3 all 0 | — |

Thresholds unchanged: per join ≤ max(40 ms, one frame), cumulative ≤ 80 ms. Both real programmes are within them, with no join unmeasured.

## W5 — Regression

| # | Case | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Compilation v1 | UI (output folder `rc13\c3v1`, open "Ghép thử hai phim ngắn", "Dùng lại thiết lập này cho bản mới", tab "Video đã lồng tiếng", "Ghép video") | COMPLETED in 50 s, `run-4ef244555522\compilation.mp4` = **`9afa62bcfb3ae46343d28f606b39a5d68db722c7285582c1112c7e9a1002c6d3`** = rc12 = rc10 | `logs\W5-v1-log.txt`, `W5-E-04-v1-result.png` |
| 2 | Re-render one part | UI (W2 Pin i3.u2) | rendered **i3 only**, reused 7, 0 INVALIDATED (see W2) | `logs\W5-resume-log.txt` |
| 3 | Cancel | UI: 3-script Bán hàng draft, "Hủy" 20 s in | status.json RUNNING ("Đang phân tích cảnh cho video 1/3…") → **`CANCELLED` "Đã hủy."**; still CANCELLED 35 s later, never COMPLETED; 0 video files; list row "Đã hủy"; 0 engine processes left. The button read **"Tạo 2 video"** with 3 scripts (P3-N13, still open) | `logs\W5-cancel-status.txt`, `W5-F-cancel.png` |
| 4 | Decision records | `tools\records.py` | W1 7 records, W2 16 records (14 APPROVE, 1 EXCLUDE, 1 PIN); all 12 fields, `run_id` = folder; **0 INVALIDATED** (scanned after the W5 pin) | `logs\records.json` |
| 5 | Sources | file check | Espresso ×4, Top 5 ×5, long ×20 and the dubbing clip equal rc12 (**30/30**); W3 film unchanged | `logs\media-hashes-after.json` |
| 6 | Key / e-mail scan (app closed) | `tools\keyscan2.py` | 11 201 files, 4.96 GiB, 0 unreadable: **claude 0, gemini 0, email 0**; self-test PASS. Scope: rc13 data root (incl. WebView2 profile, versions, models), `out`, `c3v1`, logs, screenshots, tools, investigate, w3sheets, build evidence | `logs\keyscan.txt` |

Not run in W5 (not asked this time): the 3-video Bán hàng approval flow, dubbing, an engine kill.

## W6 — Human validation

**BLOCKED**: no participant. Not simulated. Nothing in this report is human review.

## Added investigation (coordinator request): the W2 HELD_FRAME

Read-only replay on a copy (`investigate\w2replay\rebuild\`) with the rc13 engine, cached vision only (0 network asks). The replayed `plan-i1.json` equals the real one byte for byte. Findings are in `investigate\answer.md`; geometry for a media-free unit test is in `investigate\i1-geometry.json`, and every evidence item per i1 candidate is in `investigate\i1-evidence-items.json`. Summary is under W2 above.

## Performance vs rc12

| Measure | rc12 | rc13 |
|---|---|---|
| Espresso 1-video draft (W1) | 156 s | 146 s |
| Product re-render (W1) | 10.9 / 10.4 / 10.9 s | 9.8 / 9.3 / 9.3 s |
| Top 5 draft (W2, vision on) | 263 s | 255 s |
| Tổng hợp partial re-render (W2) | 31.3 s (2 parts) | 12.1 s (1 part) |
| One-part re-render (W5 pin i3) | ~15 s | 31 s |
| Long-form draft (W3) | 781 s | 721 s |
| Peak RAM (host + webview + engine) | W1 3 768, W2 3 277, W3 3 637 MB | W1 3 631, W2 3 113, W3 3 485 MB |

Draft times are polled every 15 s (W2/W3) or 5 s (W1), as in rc12.

## Defects

| ID | Sev | Status | What | Repro |
|---|---|---|---|---|
| P2-N5 | P2 | **Fixed for the cases run** | 0 approvals voided: W1 (3 actions), W2 (exclusion), W5 pin on W2. 0 INVALIDATED records of 18 approvals | W1, W2 |
| P2-N18 | P2 | **Fixed (W2 case)** | After the exclusion only i1 was re-rendered; i3–i5 plans byte-identical, 0 new vision asks | W2 step 4–5 |
| P2-N17 | P2 | **Partly fixed, still FAIL** | 1 HELD_FRAME left in i1.u2 (1126 ms, rc12: 5), and i1 loses its own-sound moment after the exclusion. Cause: forward-only `_assign` starts u1 at its best match (12000) and skips c4f1, so 6000–12000 can only return as a repeat. Final QC refuses the export | W2: same programme, approve i2 + outro, exclude `i1-s1c1`, rebuild, approve rest, "Xuất bản cuối…" |
| Gate B / P2-N11 | P1 gate | **FAIL** | recall@5 55.0 % (target 85 %); **2 serious silent misses** (i1.u2, i16.u1; rc12 0); 0 false SI; 0 held frames | W3 |
| P2-N19 | P2 | **Fixed** | 28/28 real joins measured (W2 worst 33.3 ms, W3 0 ms) | W4 |
| P3-N20 | P3 | **Fixed** | Synthetic test passes unchanged on the bundled FFmpeg | W4 §1 |
| P3-N21 | P3 | Open | Ghép video result line shows "Còn 10 câu ở 6 phần…" after every sentence was approved, until export is clicked | W2 step 8 |
| P3-N13 | P3 | Open | "Tạo 2 video" with 3 scripts | W5 #3 |
| P2-N22 | P2 | Known (#796), not exercised | No intro/outro/hook edit was made | — |
| Note | — | Observation | After a rebuild, plan JSON of untouched parts can change in `reasons`/`confidence_milli` text only (shared vision evidence): geometry, receipts and renders unchanged | W2 step 5, W5 pin |

## Evidence separation

| Kind | What |
|---|---|
| CI | Run 43250339299 success (as shipped in the artifact) |
| UI (real rc13 DubFlow.exe via CDP + Win32 dialogs; automation, not human review) | Settings; W1 all steps; W2 1–4, 7–8; W5 pin; W3 c–d; W5 v1, cancel |
| File checks | snapshots, review/editorial/plan/status/receipt files, `score_w3.py`, bundle `final_qc`/`quality`/`sync_probe`, the synthetic test, the replay investigation (all `-B`), hashes, scans |
| Not run | W6 (no participant); W3 UI acceptance + export; W1 editor-open check; W5 3-video approval flow, dubbing, engine kill |
