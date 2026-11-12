# Assisted Production Stabilization & Documentary Pilot Entry Gates — Windows E2E, DubFlow 1.1.96-rc15

- Date: 2026-11-12/13. Owner's Windows 11 machine, CPU only ("Chỉ dùng CPU" on; no NVIDIA GPU), one job at a time.
- Folder: `D:\Projects\DubFlow\_sandbox\assisted\rc15\` (this report, `results.json`, `screenshots\`, `logs\`, `tools\`).
- rc15 = rc14 + #804 (issue #796, P2-N22): hook, intro and outro look at the items' footage for themselves only, so an edit or approval there never re-plans an item; excluding a sub-shot whose shot is whole on a later run leaves out the whole shot.
- Every W was run from the start on rc15. rc14 numbers are the baseline only. The harness is `rc14\tools` with rc14 → rc15; `score_w3.py` is byte-identical (`3bf42516…fc31`).
- Not done, by design: no release, publish, tag or stable install; the user's normal DubFlow install and data were not touched; `_sandbox\models-u` was not touched; no question was asked of the owner.
- AI key: the app read its own key from Windows Credential Manager. I never printed, logged or wrote the key; scans report counts only. The profile path is redacted as `%USERPROFILE%`; 0 files under rc15 `logs`, `investigate` or `tools` contain it.

Labels: **UI** = the real rc15 `DubFlow.exe` driven over WebView2 CDP + Win32 dialogs (automation, **not human review**). **File check** = hashes, ffprobe/ffmpeg filters, run files, rc15 bundle modules run read-only with `python -B`.

## Summary

| W | Verdict | Key numbers |
|---|---|---|
| W1 Product replanning | **PASS** | 4 approvals → Pin / Exclude / Replace on Câu 3, re-render after each: **4/4 kept, 0 INVALIDATED**; replanned 0 / 1 / 1 (u3); re-render 8.3 / 7.7 / 7.8 s. Editor opens after Final QC (no dialog this time; there is no QC block in the editor) |
| W2 Compilation replanning | **PASS** | Exclusion `i1-s1c1f2`, `i1-s1c1f3` → **dựng 1 phần (i1), dùng lại 7**, 12.7 s, **4/4 kept, 0 INVALIDATED**, vision asked 0. **HELD_FRAME 0 in every part. i1 keeps its moment** (18000–21500). Export 1: **"Kiểm tra cuối: đạt."** |
| W5 on W2 (Pin i3.u2) | **PASS** | **"dựng 0 phần, dùng lại 8"** (the pin keeps the shot i3.u2 already shows); **i1 not re-rendered, plan-i1.json byte-identical**, moment kept, 0 INVALIDATED, HELD_FRAME 0. Export 2: "Kiểm tra cuối: đạt.", same file |
| W3 Long-form (Gate B) | **Gate B FAIL** | GT `afa979d2…f66497` = lock. **recall@5 48.3 % (29/60)** (rc12 31, rc13 33, rc14 28); **serious silent misses 3: i1.u1, i1.u2, i16.u1**; false SOURCE_INSUFFICIENT 0; HELD_FRAME 0. Draft 721 s |
| W4 A/V per boundary | **PASS** | Synthetic 17/17 OK, 0 skipped, no adapter. W2 final 1, compilation 1 and final 2: **7/7 measured**, worst 33.3 ms, exit 0. W3 **21/21**, worst 0 ms, exit 0 |
| W5 Regression | **PASS** | v1 **byte-identical** `9afa62bc…c6d3`; resume reuses the rest (W5 on W2); cancel → `CANCELLED`, never COMPLETED; key/e-mail scan **0/0/0** |
| W6 Human validation | **BLOCKED** | No participant. Not simulated |

## 0. Build and install

| Item | Value |
|---|---|
| Actions run | `43298731065` "Windows Release": **success** at 20:51:34Z (waited for it) |
| Commit | `6b1a0cf25d5f8602e433ccec25821a6a0ef937f1` (= `release-evidence.json` `source_sha` = origin/main = #804 merge), `1.1.96-rc15`, `candidate`, `production_qualified: false`, `unsigned-candidate` |
| Artifact | `DubFlow-1.1.96-rc15-windows-x64` (id 13982143495) via `tools\dl.py`; outer zip `4eddda9a…b361` = API digest |
| Zip SHA-256 | `efafe2cfcb32b7f698ad012fde8c76760f0b994a691bd654affa3eeee178fc74` = `release.sha256` = release evidence |
| `app\bin\DubFlow.exe` | `104970442e961c7407733cfcd510a6d926dd68c7ea3ae2f26abdb638443fe86f` |
| Install | 8 765 entries to `rc15\app\versions\1.1.96-rc15\`; `current.json` (manifest `3d7122c3…02cd9a`); models from the rc14 sandbox without `tts-sentence-cache` (1 747 MB) |
| CDP proof | DubFlow.exe pid 4560; CDP browser `msedgewebview2.exe` pid 11048 (port 9449, no `--type`), parent = `…\rc15\app\versions\1.1.96-rc15\app\bin\DubFlow.exe`; **PROOF OK** |
| Footer | "Phiên bản **v1.1.96-rc15** candidate" |
| Settings (UI) | "Chỉ dùng CPU" on; output `rc15\out` (`rc15\c3v1` for W5 v1, then back); AI Claude (saved) |

## W1 — Product replanning

Run in `rc15\out\DubFlow Output\Ban hang - Máy pha espresso tay cầm\`: same inputs as rc14.

| # | Step | Result |
|---|---|---|
| 1 | Draft (UI) | COMPLETED in **166 s** (rc14 161 s); u2 READY, others NEEDS_REVIEW; "5 câu cần xem lại" |
| 2 | Approve u1, u4, u5, u6 (UI) | 4 USER_APPROVED |
| 3 | Pin u3 + re-render | **8.3 s**; kept 4/4, INVALIDATED 0, replanned 0 |
| 4 | Exclude u3 2nd shot + re-render | **7.7 s**; kept 4/4, INVALIDATED 0, replanned u3 only |
| 5 | Replace u3 ("Chọn cảnh khác…" → "Dùng cảnh này", `v1c8`) + re-render | **7.8 s**; kept 4/4, INVALIDATED 0, replanned u3 only |
| 6 | Editor after Final QC (UI "Chỉnh sửa") | Opens directly: "Chỉnh sửa · Máy pha espresso tay cầm · video 1", 0:25. No unreviewed-sentence dialog (the only open sentence, u2, is READY); **no QC block in the editor** (`W1-5-editor.png`) |

## W2 — Compilation replanning (#796 / #804)

Run `run-28d1787167b3`: same programme as rc14 (m2, m3, m4, m5, m1; m2-lop-hoc in i1; vision on).

| # | Step | Result |
|---|---|---|
| 1 | Draft (UI) | COMPLETED in **255 s** (rc14 240 s); **HELD_FRAME 0**; i1 12990 ms with moment 18000–21500. i1-s1 candidates: c1f1 0–1950, c1f2 1950–4050, c1f3 4050–6000, c2f1/f2/f3 6000–12000, c3 12000–18000, c4 18000–21502 |
| 2 | Approve i2 and outro (UI) | 4 USER_APPROVED |
| 3 | Exclude 0–6000 ms in i1 (UI) | i1.u1 used c1f2 and c1f3 → "Bỏ cảnh này" on both → EXCLUDE **`i1-s1c1f2`**, **`i1-s1c1f3`**. **`i1-s1c1f1` (0–1950) could not be excluded from the UI**: no sentence uses it, and the alternatives list offers only "Dùng cảnh này" |
| 4 | "Dựng lại các phần đã đổi" (UI) | **12.7 s**; "**dựng 1 phần, dùng lại 7**"; only `part-i1.mp4` rewritten |
| 5 | Provenance | hook, intro, i3, i4, i5: plan byte-identical. i2: text only (geometry/provenance/receipt same). outro: `inputs.part` changed (`shown`), geometry and receipt same → reused. Vision asked 0 everywhere. Nothing unexpected re-rendered |
| 6 | Approvals | **4/4 kept, 0 INVALIDATED** |
| 7 | i1 plan after | 12990 ms: u1 **0–1950 (c1f1, the not-excluded third of the shot)**, 6000–8715; u2 8715–13540; **moment 18000–21500 kept** |
| 8 | Final QC per part | **HELD_FRAME: hook 0, intro 0, i1 0, i2 0, i3 0, i4 0, i5 0, outro 0.** Other lines: render ok; duration "chưa đo"; captions ok (last ends at 72824 ms); audio "chưa đo đỉnh âm"; timeline ok; pending ok; decisions "10 câu chưa duyệt"; **claims: "chưa xử lý, không được coi là đã kiểm: i1: câu 2, i5: câu 2"** (both MATCH_NOT_FOUND) |
| 9 | Approve all remaining (UI, as rc14) + export | hook, intro, i1 (incl. the critical u2), i3, i4, i5 (incl. the critical u2) approved → "Xuất bản cuối…" → **"Bản cuối (lần xuất 1) … Kiểm tra cuối: đạt."** final.mp4 sha256 `d6af11aa…85f5` (= compilation.mp4). i1 in the final (8.4–21.4 s): `freezedetect` **0 frozen stretches**; sheet `W2-i1-final-sheet.png` |
| 10 | W5 on W2: Pin i3.u2 + "Dựng lại các phần đã đổi" (UI) | **25.9 s, "dựng 0 phần, dùng lại 8"**. The pin keeps `i3-s1c10`, the shot i3.u2 already showed, so nothing needed rendering. The directive expected i3 only; the result was none. **i1 was not re-rendered, and `plan-i1.json` is byte-identical before and after**; moment kept; **0 INVALIDATED**; HELD_FRAME 0; QC READY_TO_FINALIZE. Note: this rebuild asked vision for 6 new frames (intro 2, i3 2, i4 2). Plan `reasons`/`confidence` text changed in hook, intro, i3, i4 and outro (i3 also `inputs.part` for the pin); geometry and receipts unchanged |
| 11 | Export 2 | "Bản cuối (lần xuất 2) … Kiểm tra cuối: đạt.", same sha `d6af11aa…` |

## W3 — Long-form evidence (Gate B)

| # | Step | Result |
|---|---|---|
| a | Ground truth | Copied byte for byte; **`afa979d239b6a68be03349308e233809efddc84cd2ed13e0d869913c51f66497`** = lock first token, checked at 21:05:52Z before the programme; not edited |
| b | Programme (UI `ffill.ps1`) | 20 items, Danh sách, 105 s windows, narrations 20/20 equal, permitted, vision on, no target or explicit budget |
| c | Draft | COMPLETED in **721 s** (rc14 676 s); peak RAM **3 536 MB** |
| d | vision_report | Budget **142** ("auto: 2 × 71 câu…"), **139 used**, asked 139; **broad 95**; **targeted 42** (2 per part for intro and i1–i20); **multi_frame 2** (outro only), 0 in every item. Coverage `milli`: i1 315, i2 194, i3 235, i4 179, i5 280, i6 161, i7 189, i8 122, i9 430, i10 124, i11 122, i12 271, i13 115, i14 236, i15 236, i16 225, i17 117, i18 234, i19 310, i20 191, intro 145, outro 232 |
| e | Score (`score_w3.py --lock`) | **recall@5 0.4833 (29/60)**; chosen-hit 0.417; **serious silent misses 3: i1.u1, i1.u2, i16.u1** (all READY, chosen shots miss the ground truth); **false SOURCE_INSUFFICIENT 0**; 4/4 missing flagged; READY 8, NEEDS_REVIEW 41, MATCH_NOT_FOUND 15 |
| f | vs rc14 | Gained: i4.u2, i9.u2, i15.u1, i16.u2, i18.u1. Lost: i5.u3, i16.u1, i16.u3, i18.u3 |
| g | Final QC | **HELD_FRAME 0** in all 22 plans; REVIEW_PENDING. UI acceptance + export not run (not asked) |

**Gate B: FAIL.** recall 48.3 % (target 85 %), and 3 silent misses (target 0). Nothing was tuned, and the ground truth was not changed. As in rc12–rc14, each item gets 2 targeted looks and no multi-frame look. In rc13 the investigation found that the vision answers differ between cold-cache runs.

## W4 — A/V per boundary

| # | Step | Result |
|---|---|---|
| 1 | Synthetic `test_av_boundaries.py` (origin/main `6b1a0cf2`, unchanged) on the rc15 bundle | **17 run, 17 OK, 0 skipped, 0 failed**, encoder mpeg4, no adapter (`logs\W4-synthetic*.txt/jsonl`) |
| 2 | W2 final 1 (`--final final.mp4`, before the pin) | **exit 0; 7 measured, 0 unmeasured**; audio 0 at every join; av J1 −33.3, J2 0, J3 0, J4 0, J5 0, J6 −33.3, J7 −33.3; worst **33.3 ms** |
| 3 | W2 compilation 1 | identical: exit 0, 7/7, worst 33.3 ms |
| 4 | W2 final 2 (after the pin) | exit 0, 7/7, worst 33.3 ms (same file) |
| 5 | W3 compilation | **exit 0; 21 measured, 0 unmeasured; av 0.0 ms at every join** |

## W5 — Regression

| # | Case | Result |
|---|---|---|
| 1 | Compilation v1 (UI, `rc15\c3v1`) | COMPLETED in 40 s; `compilation.mp4` = **`9afa62bcfb3ae46343d28f606b39a5d68db722c7285582c1112c7e9a1002c6d3`** = rc14 |
| 2 | Re-render one part | W5 on W2: 0 parts needed, 8 reused, i1 untouched → **PASS** |
| 3 | Cancel (UI, 3-script Bán hàng, "Hủy" at 20 s) | RUNNING → **CANCELLED "Đã hủy."**, still CANCELLED 35 s later; 0 videos; 0 engine processes. Button read "Tạo 2 video" with 3 scripts (P3-N13) |
| 4 | Records | 24 (W1 7, W2 17): 12 fields each, `run_id` = folder, **0 INVALIDATED** |
| 5 | Sources | 30/30 media equal rc14; film unchanged |
| 6 | Key / e-mail scan (app closed) | 10 917 files, 4.89 GiB, 0 unreadable: **claude 0, gemini 0, email 0**; self-test PASS |

## W6 — Human validation

**BLOCKED**: no participant. Not simulated.

## Performance vs rc14

| Measure | rc14 | rc15 |
|---|---|---|
| Espresso draft (W1) | 161 s | 166 s |
| Product re-render (W1) | 9.1 / 8.3 / 8.3 s | 8.3 / 7.7 / 7.8 s |
| Top 5 draft (W2) | 240 s | 255 s |
| Partial re-render (W2) | 12.4 s | 12.7 s |
| Pin re-render (W5 on W2) | 22.7 s (re-rendered i1) | 25.9 s (0 parts) |
| Long-form draft (W3) | 676 s | 721 s |
| Peak RAM | W1 3 729, W2 3 194, W3 3 534 MB | W1 3 532, W2 2 983, W3 3 536 MB |

## Defects

| ID | Sev | Status | What |
|---|---|---|---|
| P2-N22 / #796 | P2 | **Fixed for the case run** | After approvals in hook/intro/items, the pin rebuild left i1 byte-identical (rc14: i1 re-planned, moment lost) |
| P2-N17 / #801 | P2 | **Fixed (W2)** | 0 HELD_FRAME, moment kept, export passes |
| Gate B / P2-N11 | P1 gate | **FAIL** | recall@5 48.3 % (29/60); **3 serious silent misses** (i1.u1, i1.u2, i16.u1); 0 false SI |
| Note | P3 | New | A sub-shot that no sentence uses (here `i1-s1c1f1`) cannot be excluded from the review panel (alternatives offer only "Dùng cảnh này"); the rebuilt plan then shows that excluded shot's first third |
| Note | — | Observation | A pin rebuild asks vision for new frames (6 here) and rewrites plan text of untouched parts; geometry and renders unchanged |
| P3-N21 | P3 | Open | Stale "Còn N câu…" line until export |
| P3-N13 | P3 | Open | "Tạo 2 video" with 3 scripts |

## Evidence separation

| Kind | What |
|---|---|
| CI | Run 43298731065 success |
| UI (real rc15 DubFlow.exe via CDP + Win32 dialogs; automation, not human review) | Settings; W1 all steps incl. editor; W2 1–4, 9–11; W3 b–c; W5 v1, cancel |
| File checks | snapshots, plans/editorials/reviews/receipts/status, `score_w3.py`, bundle `final_qc`/`quality`/`sync_probe`, synthetic test, ffmpeg freezedetect, hashes, scans |
| Not run | W6; W3 UI acceptance + export; W5 3-video approval flow, dubbing, engine kill |
