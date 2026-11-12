# Assisted Production Stabilization & Documentary Pilot Entry Gates — Windows E2E, DubFlow 1.1.96-rc12

- Date: 2026-11-12. Owner's Windows 11 machine, CPU only ("Chỉ dùng CPU" on; no NVIDIA GPU), one job at a time.
- Folder: `D:\Projects\DubFlow\_sandbox\assisted\rc12\` (this report, `results.json`, `screenshots\`, `logs\`, `tools\`).
- rc12 = rc11 + #788 (P2-N5 follow-ups), #790 (P2-N11 follow-ups, recipe `compilation-program-v18`), #792 (sync_probe: unmeasured is never a pass; exit 0/1/2).
- Every W was run from the start on rc12. Nothing measured on the stopped rc11 run is used as evidence. No rc11 DubFlow process was running at start.
- Not done, by design: no release, publish, tag or stable install; the user's normal DubFlow install and data were not touched; `_sandbox\models-u` was not touched; no question was asked of the owner.
- AI key: the app read its own key from Windows Credential Manager (vision provider Claude, model (model as saved in the app)). I never printed, logged or wrote the key; scans report counts only. The profile path is redacted as `%USERPROFILE%` in written files; one settings screenshot that showed the download folder (profile path) was deleted. Disclosure: one read-only DOM dump of the page's hidden dialogs went to my console (not to any file). It included the app's own masked key hint, which shows only the last 4 characters.

Labels: **UI** = the real rc12 `DubFlow.exe`, driven by real mouse/keyboard events over WebView2 CDP (`Input.dispatchMouseEvent`, `Input.insertText`) and Win32 messages for native file/folder dialogs; never Tauri IPC. This is CDP automation, **not human review**. **File check** = hashes, ffprobe, run files, engine modules from the rc12 bundle run read-only with `python -B`.

## Summary

| W | Verdict | Key numbers |
|---|---|---|
| W1 Product replanning | **PASS** | 4 approvals → Pin, Exclude, Replace on Câu 3, re-render after each: **4/4 kept, 0 INVALIDATED** at every step (rc10: 2 of 5 voided). Units replanned 0 / 1 / 1 (only u3). 1 part (video-1) re-rendered per step: 10.9 / 10.4 / 10.9 s. 0 re-reviews. Editor opens |
| W2 Compilation replanning | **FAIL (export)**; replanning PASS | i2 + outro all approved (4), Exclude in i1, "Dựng lại các phần đã đổi": **dựng 2 phần, dùng lại 6**, 31.3 s, **4/4 kept, 0 INVALIDATED**. outro.u1 is **not** voided any more, even though outro.u2 uses the excluded shot. But the untouched i4 was also replanned and re-rendered. Export: **"Chưa xuất. Kiểm tra cuối chưa đạt"**, 5 × HELD_FRAME in i1 |
| W3 Long-form (Gate B) | **Gate B FAIL** | GT sha256 `afa979d2…f66497` = lock; 64 units (60 footage, 4 missing). **recall@5 51.7 %** (target ≥ 85 %), chosen-hit 41.7 %, **0 serious silent misses, 0 false SOURCE_INSUFFICIENT**, **HELD_FRAME 0**. Draft 781 s, peak RAM 3.6 GB. rc8 baseline 16/23 signals_only, 193/214 MATCH_NOT_FOUND → rc12 **0/22** signals_only, **16/71** MATCH_NOT_FOUND |
| W4 A/V per boundary | **Synthetic PASS (with adapter); real joins NOT PASSED (unmeasured)** | Synthetic test from main: run unchanged → 5 ERROR (the test hardcodes `boxblur`, which the bundled LGPL FFmpeg lacks). Re-run with the blur string swapped to `gblur` → 15/15 OK, worst A/V 13.3 ms, encoder `mpeg4`. Real: sync_probe **exit 2** on W3 (21 joins, **0 measured**) and W2 (7 joins, 0 measured). Audio lag is never "clear" |
| W5 Regression | **PASS** | 3-video Bán hàng settled, 3/3 editors open; dubbing default **Tắt**, QC passed; compilation v1 **byte-identical** `9afa62bc…`; re-render of one part (i3) reused 7; cancel → `CANCELLED`; key/e-mail scan **0/0/0** |
| W6 Human validation | **BLOCKED** | No participant. Not simulated |

## 0. Build and install

| Item | Value |
|---|---|
| Actions run | `43208543157` "Windows Release": in progress at 11:22Z, **success** at 11:57Z (waited for it) |
| Commit | `a0648076e4841df1ec1889de23646a0b4918a802` (= `release-evidence.json` `source_sha` = origin/main = #792 merge), version `1.1.96-rc12`, channel `candidate`, `production_qualified: false`, `unsigned-candidate` |
| Artifact | `DubFlow-1.1.96-rc12-windows-x64` (id 13942297666), via `tools\dl.py` (16 range requests; token sent only to api.github.com). Outer zip `51a6d521…dfe7d74` = API digest |
| Zip SHA-256 | `fb1bc7a1f748bc1ffff1fe01ba67e4273b95a5e8d2f8d501458e7718cdc79747` = `release.sha256` = release evidence |
| `app\bin\DubFlow.exe` | `e536d3ee895e8a21502ab07763a3c892df2edc4f363730e37ee2781058198dbc` |
| Install | extracted with Python `zipfile` (8 765 entries) to `rc12\app\versions\1.1.96-rc12\`; `current.json` written (manifest `77db0961…b1490a`); models copied from the rc10 sandbox without `tts-sentence-cache`; data root `rc12\app`; **0 `.pyc`** after every engine run (`-B`) |
| CDP proof | `logs\cdp-proof-processes.txt`: launch 1, DubFlow.exe pid 18392. CDP browser `msedgewebview2.exe` pid 11976 (`remote-debugging-port=9449`, no `--type`), whose parent is `…\rc12\app\versions\1.1.96-rc12\app\bin\DubFlow.exe`. user-data-dir `…\rc12\app\control\webview2\312e…3132\EBWebView`, page `http://tauri.localhost/`. **PROOF OK** |
| Footer | "Phiên bản **v1.1.96-rc12** candidate" |
| Settings (UI) | "Chỉ dùng CPU" on (`01-settings-cpu.png`); output folder `rc12\out`, then `rc12\c3v1` for W5 v1, then back to `rc12\out`, always through the native folder dialog; AI provider Claude (already saved) |

## W1 — Product replanning (P2-N5, #782 + #788)

Run `run-8d5a6a8c0678`: Bán hàng "Máy pha espresso tay cầm", 12.900.000đ, clips `media\espresso\clip-01..04.mkv` (= rc8/rc10 inputs), script 1 of rc10 §1b, Toàn màn hình, Trúc Ly, Cân bằng, vision Claude. Recipe `product-editorial-v17`.

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Draft | UI (`pnew.ps1`) | COMPLETED in **156 s** (rc10: 156 s). u1 NOT_NEEDED/NEEDS_REVIEW, u2 READY, u3–u6 NEEDS_REVIEW; queue "5 cần xem · 1 tuỳ chọn" | `logs\W1-log.txt`, `W1-01-panel-open.png`, `snap-W1-00-draft.json` |
| 2 | Approve 4 | UI (`act.ps1`) | u1, u4, u5, u6 "Duyệt cảnh này" → 4 APPROVE / USER_APPROVED; queue "1 cần xem · 1 tuỳ chọn" | `W-review-log.txt`, `snap-W1-0-approved4.json` |
| 3 | Pin (u3) + re-render | UI | **10.9 s**, video-1 rewritten. Approvals kept **4/4**, INVALIDATED **0**, units replanned **0**, re-reviews **0** (queue "1 tuỳ chọn" = READY u2) | `snap-W1-1-pin.json`, `W1-ui-1-pin.json`, `W1-1-pin.png` |
| 4 | Exclude (u3, 2nd shot `v2c2`) + re-render | UI | **10.4 s**. Kept **4/4**, INVALIDATED **0**; replanned **u3 only** (`v2c2` → `v3c1`, pinned `v1c2` kept) | `snap-W1-2-exclude.json` |
| 5 | Replace (u3, "Chọn cảnh khác…" opens the shot list → "Dùng cảnh này" on alternative `v1c8`, score 535) + re-render | UI | **10.9 s**. Kept **4/4**, INVALIDATED **0**; replanned **u3 only** (`v1c8, v1c2, v1c4`); header "AI thấy khớp 52 % · cảnh bạn chọn 25 %" | `snap-W1-3-replace.json`, `W1-3-replace-picker.png` |
| 6 | Result row | UI | "Bạn đã duyệt hoặc chọn cảnh cho 5 câu AI chưa chắc; không còn câu chờ duyệt" | `W1-4-result-row.png` |
| 7 | Editor after Final QC | UI "Chỉnh sửa" | Opens ("Chỉnh sửa · Máy pha espresso tay cầm · video 1"), no QC block; status COMPLETED, `unreviewed 0`, `user_settled 5` | `W1-5-editor.png` |
| 8 | Compare rc10 | — | rc10 §1b step 6: **2 of 5** approvals voided by a re-plan. rc12: **0 of 4** across three actions on another sentence | — |

Parts re-rendered per step: 1 (a product video is one part). Records: 7 (4 APPROVE, PIN, EXCLUDE, REPLACE), 12 contract fields each, `run_id` = folder name.

## W2 — Compilation replanning

Run `run-6838a67c5795`: raw Tổng hợp "Top 5 cỗ máy phản ứng dây chuyền ấn tượng" (top_n, countdown), 5 items from the rc10 C inputs (`media\top5\m2,m3,m4,m5,m1`, licensed), hook/intro/outro as rc10 C, Trúc Ly, Cân bằng. **Vision on** (I ticked "Dùng AI để xem khung hình"; it is off by default).

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Draft | UI | COMPLETED in **263 s**, 8 parts (hook, intro, i1–i5, outro), "dựng 8 phần, dùng lại 0"; 14 unreviewed, 0 READY | `W2-01-form.png`, `snap-W2-0-*.json` |
| 2 | Approve all of i2 and outro | UI (`cpart.ps1`) | i2.u1, i2.u2, outro.u1, outro.u2 USER_APPROVED; outro.u2 uses `i1-s1c1` (an i1 shot) | `W2-review-log.txt`, `snap-W2-1-pre-*.json` |
| 3 | Exclude in i1 | UI | i1.u1 "Bỏ cảnh này" on `i1-s1c1` (the shot outro.u2 uses as well) → EXCLUDE record | log |
| 4 | "Dựng lại các phần đã đổi" | UI | **31.3 s**; "Lần dựng gần nhất: **dựng 2 phần, dùng lại 6** phần không đổi"; rendered **i1, i4**, reused hook, intro, i2, i3, i5, outro | `W2-log.txt`, `W2-parts-mtime-*.txt` |
| 5 | Counts | file check (`snap-W2-2-post-*.json`) | Approvals kept **4/4**, INVALIDATED **0**. outro.u1 and outro.u2 kept, outro reused, so the rc10 case (outro.u1 voided) **no longer happens**. Replanned: i1.u1, i1.u2 (expected) **and i4.u1** (unexpected: `[i4-s1c2f2, i4-s1c3f3]` → `[i4-s1c4]`). i4 had no decisions, so no approval was voided, but an untouched part was re-planned and re-rendered. Its `vision.asked` was 2 on rebuild (i3: 0), so a new vision call changed it. Re-reviews: 0 | `W2-review-log.txt` |
| 6 | Settle the rest | UI | approved hook, intro, i1, i3, i4, i5.u1; i5.u2 (MATCH_NOT_FOUND) "Không có cảnh phù hợp" | log |
| 7 | Export | UI `watchexport.mjs` "Xuất bản cuối…" | At 5.0 s: **"Chưa xuất. Kiểm tra cuối chưa đạt. Dòng thời gian: i1: HELD_FRAME i1.u1 giữ một khung hình 1950 ms; …765 ms; i1.u2 1185 ms; 1950 ms; 1690 ms"**. No final.mp4 | `W2-export-line-watch.json`, `W2-03-export-qc-heldframe.png` |
| 8 | QC detail | file check (bundle `final_qc` + `quality.timeline_problems`, read-only) | **HELD_FRAME 5, all in i1** (the part re-planned after the exclusion). Its plan uses `i1-s1c3f1` twice in a row in i1.u1 | `logs\W2W3-final-qc.json` |

Note: after all approvals the panel line still read "Còn 10 câu ở 6 phần…" until export was clicked (stale text; export did run).

## W3 — Long-form evidence (P2-N11, Gate B)

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| a | Source | file check | `phase2-e2e\footage\long\Why_We_Fight.5a.The_Battle_Of_Russia_Part1.webm` sha256 `834cf4f3…b3c32b` **before and after** (unchanged); 20 hard links `media\long\wwf-01..20.webm` | `logs\W3-source-hash.txt` |
| b+c | Ground truth | file check | Copied byte for byte from rc11: `W3-ground-truth.json` sha256 **`afa979d239b6a68be03349308e233809efddc84cd2ed13e0d869913c51f66497`** = first token of `W3-ground-truth.lock` ("locked 2026-11-12T09:23:33.358010Z before the programme was created"); lock `6e07cee4…`, narrations `0c73f3ca…`; original mtimes (UTC) 09:23:33.2858801 / 09:23:33.3580101 / 09:23:33.2871724 (kept by the copy); `w3sheets` 20/20 identical. **64 units** (≥ 40). Not edited | `logs\W3-ground-truth.*`, `logs\W3-narrations.json` |
| d | Programme | UI (`ffill.ps1`) | "Thêm mới" in Ghép video, leftover W2 items removed with "Xóa mục", 20 items added via the native dialog, kind **Danh sách**, item k = wwf-k, Start 105·(k−1), End 105·k, narration from `W3-narrations.json` (**20/20 equal**, notes empty, rights permitted), hook cleared, intro/outro from rc8 inputs, Trúc Ly, Cân bằng, vision on, no explicit vision budget | `logs\W3-form-check.txt`, `logs\W3-form.json`, `W3-01-form.png` |
| d | Draft | UI | COMPLETED in **781 s**; 22 parts (intro, i1–i20, outro), all mode **content**; 352.1 s; peak RAM **3 637 MB** (python 1 577 MB) | `logs\W3-log.txt`, `logs\W3-ram.csv` |
| d | vision_report | file check | Program budget **142** ("auto: 2 × 71 câu, tối thiểu 80, tối đa 400"), all used (outro cumulative frames 142, asked 141); **broad 95** (spread over 22 parts), targeted 45, multi_frame 2, shared_duplicates 22. UI warning: "Giới hạn 142 khung hình cho AI nhìn: 95 cảnh trải đều các phần…; **568 cảnh** trở lên chọn theo thông tin khác." | `logs\W3-vision-report.json` |
| d | coverage_map | file check | Per part `milli` (share of footage with vision evidence): i1 402, i2 194, i3 237, i4 179, i5 254, i6 161, i7 189, i8 122, i9 430, i10 124, i11 226, i12 271, i13 115, i14 151, i15 235, i16 262, i17 111, i18 234, i19 338, i20 191 (intro 149, outro 252) | same |
| e | Score | `python tools\score_w3.py logs\W3-ground-truth.json <run> --lock logs\W3-ground-truth.lock` | lock check passed; 22 logs, 64 scored, 0 unmatched; **recall@5 = 0.5167 (31/60)**; chosen-hit 0.4167; **serious silent misses 0**; **false SOURCE_INSUFFICIENT 0**; 4/4 missing cases flagged; states READY 5 (all hits), NEEDS_REVIEW 44, MATCH_NOT_FOUND 15; footage units with MATCH_NOT_FOUND 13 | `logs\W3-score.json` |
| e | Final QC | UI export + file check | Accepted the open sentences in 21 parts with "Tôi chấp nhận xuất video với các câu chưa duyệt" (a user acceptance, **not** an approval). Export: **"Chưa xuất. Kiểm tra cuối chưa đạt. Mục nghiêm trọng: chưa xử lý…: intro câu 4, i3 câu 3, i4 câu 2, 3, i7 câu 3, i8 câu 1, 4, i9 câu 2, i11–i12, i14–i15 câu 3"** (critical items are never covered by acceptance). Timeline check **passed: HELD_FRAME 0**, no gap, overlap or empty piece (all 22 plans) | `logs\W3-export-line.txt`, `W3-05-export-result.png`, `logs\W2W3-final-qc.json`, `logs\W3-accept-log.txt` |
| e | rc8 baseline | file check | rc8: 16/23 parts signals_only, 193/214 MATCH_NOT_FOUND. rc12: **0/22** signals_only, **16/71** units MATCH_NOT_FOUND | — |

**Gate B: FAIL.** recall@5 is 51.7 % against a target of ≥ 85 %. The other two targets are met (0 serious silent misses, 0 false SOURCE_INSUFFICIENT), and there are 0 held frames. Nothing was tuned, and the ground truth was not changed. Likely cause, from the run itself: the programme-wide budget of 142 frames gives about 4–5 broad looks per 105 s part. That leaves 57–89 % of each part's footage unseen by vision. No labelling error was noticed.

## W4 — A/V per boundary (P2-9, #786 + #792)

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Synthetic, as is | `tests/mastering/test_av_boundaries.py` from origin/main `a0648076` (= rc12 commit), rc12 bundle python `-B`, `PYTHONPATH=<bundle>\app`, `DUBFLOW_TEST_FFMPEG/FFPROBE` = bundle `runtime\media`, `DUBFLOW_AV_REPORT=logs\W4-synthetic.jsonl` | Encoder **mpeg4** (bundled LGPL FFmpeg has no libx264; libopenh264 present). **10 OK, 5 ERROR**: FFmpeg "No option name near '2:1'", because the test's own blur string `boxblur=2:1` does not exist in the bundled FFmpeg. The product itself uses `render.available_blur` → `gblur` | `logs\W4-synthetic-unittest.txt` |
| 2 | Synthetic, adapted | `tools\w4adapt.py`: the same test file, with only `boxblur=2:1` → `gblur=sigma=2` swapped in FFmpeg argv; thresholds and measurements untouched | **15/15 OK** (65 s). Worst per-boundary A/V: CFR 24/25/29.97, VFR and mixed 0.7 ms; silent/stills/short 13.3 ms; 10-part join 0.7 ms (29 boundaries, 0 unmeasured); reference method on the new join 19.0 ms (9/9). Baselines fail as intended (old copy join 267.3 ms with 10 unmeasured; old join reference 55.0 ms) | `logs\W4-synthetic-adapted.txt`, `logs\W4-synthetic.jsonl` |
| 3 | Real, W3 | `sync_probe --final compilation.mp4 (joined partial; no final) --parts <22 parts in program order> --fps 30` (bundle python/ffmpeg) | **exit 2**: 21 joins, **0 measured, 21 unmeasured**; video_ms 0.0 at every join, audio_ms null. Not a pass | `logs\W4-joins-W3.json` |
| 4 | Real, W2 | same, 8 parts | **exit 2**: 7 joins, **0 measured**; video_ms 0.0 (joins 1 and 6: −33.3 ms = one frame), audio null | `logs\W4-joins-W2.json` |
| 5 | Why unmeasured (diagnostic, not a verdict) | sync_probe's own envelope/correlation on W2 joins i2, i3, outro | Correlation peaks at lag 0 (1.000, 1.000, 0.925). The next-best lag more than 2 bins away scores 0.881, 0.866, 0.837. `best_lag` requires best ≥ 2 × others, so narration over music never counts as a "clear" peak, and every real join is reported unmeasured | — |

Worst measured per-join A/V on real output: **none measured** (0 of 28 joins). Thresholds (≤ max(40 ms, 1 frame), cumulative ≤ 80 ms) cannot be confirmed on real footage. Per the rule, unmeasured is not a pass.

## W5 — Regression

| # | Case | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Bán hàng 3 videos | UI (`pnew.ps1`, `bflow.ps1`) | Espresso clips, the 3 rc10 B scripts; button read **"Tạo 2 video"** with 3 scripts (P3-N13, still open); 3 videos; draft **172 s**; approvals 6 + 4 + 4, re-render 8.9 / 5.7 / 6.2 s; queues settled (v2, v3 keep 1 tuỳ chọn = READY); rows "…cho 6 / 4 / 4 câu…; không còn câu chờ duyệt"; **3/3 editors open**; 0 INVALIDATED | `logs\W5B-flow.txt`, `W5B-result-rows.txt`, `W5-B-*.png` |
| 2 | Decision records | `tools\records.py` | W1, W2, W5B: every record has the 12 fields, `run_id` = folder; **0 INVALIDATED records in rc12** | `logs\records.json` |
| 3 | Dubbing | UI (Hàng đợi → Thêm video từ máy → Lồng tiếng) | `mastering-level` default **off [Tắt]**; `dub-drama-30s` "Đã xong sau 2:04"; `final_vi.mp4` h264 1024×576 + aac 48 kHz, 30.000 s; `qc_report.json` **passed** | `W5-E-01-dub-setup.png`, `W5-E-02-dub-done.png`, `logs\W5-dub-log.txt` |
| 4 | Compilation v1 | UI (open "Ghép thử hai phim ngắn" in `rc12\c3v1`, "Dùng lại thiết lập này cho bản mới", tab "Video đã lồng tiếng", "Ghép video") | COMPLETED in 65 s, `run-7b929c1ab919\compilation.mp4` = **`9afa62bcfb3ae46343d28f606b39a5d68db722c7285582c1112c7e9a1002c6d3`** = rc10 (and rc4–rc8) | `logs\W5-v1-log.txt`, `W5-E-04-v1-result.png` |
| 5 | Re-render of one part | UI (W2: Ghim on i3.u2, "Dựng lại các phần đã đổi") | rendered **i3 only**, reused hook, intro, i1, i2, i4, i5, outro; i3 approvals kept; only `part-i3.mp4` rewritten (~15 s) | `logs\W5-resume-log.txt`, `W5-resume-parts-*.txt` |
| 6 | Cancel | UI "Hủy" during a 3-video Bán hàng draft, 20 s in | status.json RUNNING → **`CANCELLED` "Đã hủy."**, never COMPLETED; no video files; list row "Đã hủy"; 0 engine processes left | `logs\W5-cancel-status.txt`, `W5-F-cancel.png` |
| 7 | Sources | file check | Espresso ×4, Top 5 ×5 and the dubbing clip match rc8 (**10/10**); W3 film unchanged | `logs\media-hashes-after.json` |
| 8 | Key / e-mail scan (app closed) | `tools\keyscan2.py` | 11 114 files, 4.26 GiB, 0 unreadable: **claude 0, gemini 0, email 0**; self-test PASS. Scope: rc12 data root incl. WebView2 profile, `out`, `c3v1`, logs, screenshots, tools, w3sheets | `logs\keyscan.txt` |

Not run in W5: an engine kill (only the one cancel was asked).

## W6 — Human validation

**BLOCKED**: no participant. Not simulated. Nothing in this report is human review.

## Performance vs rc10

| Measure | rc10 | rc12 |
|---|---|---|
| Espresso 1-video draft | 156 s | 156 s (W1) |
| Espresso 3-video draft | 156 s | 172 s (W5) |
| Product re-render | 4.7–8.8 s | 10.4–10.9 s (W1, after pin/exclude/replace); 5.7–8.9 s (W5) |
| Tổng hợp partial re-render | ≈65 s (copy of rc8 run, 1 exclusion) | 31.3 s (2 parts, W2); ~15 s (1 part, W5) |
| Top 5 draft | — (rc10 used a copy) | 263 s (vision on) |
| Long-form draft | not run in rc10 | 781 s for 20 × 105 s (352 s output) |
| Peak RAM (host + webview + engine) | not recorded | W1 3 768 MB, W2 3 277 MB, W3 3 637 MB, W5 3 581 MB |

## Defects

| ID | Sev | Status | What | Repro |
|---|---|---|---|---|
| P2-N5 | P2 | **Fixed for the cases run** | 0 approvals voided across W1 (3 actions), W2 (exclusion of a shot that another part uses) and W5 (resume): 0 INVALIDATED records of 31 approvals (W1 4, W2 13, W5 14) | W1, W2 |
| Gate B / P2-N11 | P1 gate | **FAIL** | recall@5 51.7 % (target 85 %) on the locked 64-unit ground truth; 0 silent misses, 0 false SI | W3 |
| P2-N17 | P2 | **New** | Replanning after an exclusion leaves i1 with 5 HELD_FRAME (765–1950 ms) and repeats `i1-s1c3f1` twice in one sentence. Final QC refuses the export | W2: Top 5, vision on, approve i2 + outro, "Bỏ cảnh này" on i1.u1, "Dựng lại các phần đã đổi", approve rest, "Xuất bản cuối…" |
| P2-N18 | P2 | **New** | An untouched part (i4) was re-planned and re-rendered by "Dựng lại các phần đã đổi" after an exclusion in i1 (`vision.asked` 2 on rebuild). No approvals were there, so none was voided, but i4's shots changed silently | W2 step 4 |
| P2-N19 | P2 | **New** | sync_probe's audio "clear peak" rule (best ≥ 2 × any lag more than 2 bins away) never holds for real narration plus music. All 28 real joins in W2 and W3 are unmeasured, so Gate C cannot pass on real output | W4 §3–5 |
| P3-N20 | P3 | **New** | `test_av_boundaries.py` hardcodes `boxblur=2:1`; on the bundled LGPL FFmpeg 5 of its 15 tests ERROR (filter missing). The product is unaffected (it uses `gblur`) | W4 §1 |
| P3-N21 | P3 | **New** | Ghép video result line keeps "Còn N câu ở M phần cần xem lại…" after those sentences were approved, until export is clicked | W2 step 6 |
| P3-N13 | P3 | Open | "Tạo 2 video" with 3 scripts (3 are made) | W5 #1 |

## Evidence separation

| Kind | What |
|---|---|
| CI | Run 43208543157 success (as shipped in the artifact) |
| UI (real rc12 DubFlow.exe via CDP + Win32 dialogs; automation, not human review) | W1 all steps; W2 1–7; W3 d, export; W5 1, 3–6 |
| File checks | snapshots, review/editorial/plan/status files, `score_w3.py`, bundle `final_qc`/`quality`/`sync_probe` run read-only (`-B`), hashes, ffprobe, scans |
| Not run | W6 (no participant); engine kill; a full approval pass of W3's 66 open sentences, and so a W3 final.mp4 (the export was refused on critical items) |
