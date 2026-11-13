# Assisted Safety Hotfix & Long-Form Retrieval Feasibility — Windows E2E, DubFlow 1.1.96-rc16

- Date: 2026-11-13. Owner's Windows 11 machine, CPU only ("Chỉ dùng CPU" on; no NVIDIA GPU), one job at a time.
- Folder: `D:\Projects\DubFlow\_sandbox\assisted\rc16\` (this report, `results.json`, `screenshots\`, `logs\`, `tools\`).
- rc16 = rc15 + #808 (#802: a displayed claim must be seen in the chosen shot, or the sentence is not READY) + #810 (offline W3 tool) + #811 (#805: whole-shot exclusion "Bỏ cả cảnh gốc").
- Harness: `rc15\tools` with rc15 → rc16; `score_w3.py` is byte-identical (`3bf42516…fc31`). rc15 results are the baseline.
- Not done, by design: no release, publish, tag or stable install. I did not touch the user's normal DubFlow install or data, or `_sandbox\models-u`. No question was asked of the owner. Nothing was pushed.
- AI key: the app read its own key from Windows Credential Manager. The key was never printed, logged or written; scans report counts only. 0 written files contain the profile path.

Labels: **UI** = the real rc16 `DubFlow.exe` driven over WebView2 CDP + Win32 dialogs. This is automation, **not human review**. **File check** = hashes, ffmpeg, run files, and bundle modules run read-only with `python -B`. **Offline** = `tests.mastering.longform.w3` from worktree `_sandbox\hf\main16` (205e3ff), run on saved evidence.

## Summary

| W | Verdict | Key numbers |
|---|---|---|
| W1 #802 (cached) | **PASS** (safety) | Replay rc12–rc15 with rc16 code: **serious silent misses 0 in all 4** (rc13 2 → 0, rc15 3 → 0). New false alarms (READY+hit → flagged): rc12 i1.u1, rc13 i9.u1, rc15 i9.u1 |
| W1 #802 (fresh) | **PASS** (safety) | rc16 W3, cold vision: **READY precision 4/4**; review rate 56/60; flagged-but-right 20/60; **silent misses 0/60**. i1.u1 miss but NEEDS_REVIEW; i1.u2 hit, READY; i16.u1 hit, NEEDS_REVIEW |
| W2 #805 whole-shot exclusion | **PASS, with a deviation** | The 0–6000 ms shot was **not split** in rc16 (whole `i1-s1c1`), so the test ran on shot `i1-s1c2` 6000–12000 (f1/f2 used, **f3 unused**). Confirmation listed all 3 pieces. Rebuild re-rendered **i1 only, reused 7**; 0 INVALIDATED; HELD_FRAME 0; no plan segment overlaps 6000–12000. Kept after a full app restart. Undo restored the draft plan. Export: **"Kiểm tra cuối: đạt."** |
| W3 Long-form (Gate B) | **Gate B FAIL** | GT = lock. **recall@5 51.7 % (31/60)**; recall@1 20, @3 26; **silent misses 0**; false SI 0; HELD_FRAME 0. Draft **676 s**. Feasibility tool: R3_NOT_LOOKED 26 of the 36 misses |
| W4 Legacy regression | **PASS** | Espresso: 4 approvals kept through Pin / Exclude / Replace (0 INVALIDATED); v1 **byte-identical `9afa62bc…c6d3`**; partial re-render reuses the rest (W2); cancel → CANCELLED; sync_probe W2 7/7 (worst 33.3 ms), W3 21/21 (0 ms) |
| W5 Security | **PASS** | Key/e-mail scan 2 917 files: **claude 0, gemini 0, email 0** (self-test PASS); profile path in written files 0; review JSON absolute paths 0/9; sources unchanged 25/25 |
| Human validation | **BLOCKED** | No participant. Not simulated |

## 0. Build and install

| Item | Value |
|---|---|
| Actions run | `43361381416` "Windows Release": **success** (2026-11-13T04:23:33Z), head `205e3ff0090aa05812b603a4d43be2114bded481` |
| Artifact | `DubFlow-1.1.96-rc16-windows-x64` (id 14009299818) via `tools\dl.py`; outer zip `5dcb50c2…42f9` = API digest |
| Zip SHA-256 | `5945c88b246bd0bc29aa37b52f319c8eeb6b6d97953b898f136824401b292840` = `release.sha256` = `release-evidence.json` (`source_sha` 205e3ff0, 1.1.96-rc16, candidate, `production_qualified: false`, unsigned-candidate) |
| `app\bin\DubFlow.exe` | `5d9c103655fd63fe9b552c5800228e0c7a9f21c4a372171ba3d61625922a5d0e` |
| Install | 8 767 files to `rc16\app\versions\1.1.96-rc16\`; `current.json` manifest `6b4d7857…4244`. Models copied from the rc15 sandbox without `tts-sentence-cache` (1 746 MB). Media hard-linked from rc15 (30 files) |
| CDP proof | launch1 pid 11156 and launch2 pid 7860: CDP browser parent = `…\rc16\app\versions\1.1.96-rc16\app\bin\DubFlow.exe`; **PROOF OK** both times |
| Footer | "Phiên bản **v1.1.96-rc16** candidate" |
| Settings (UI) | "Chỉ dùng CPU" on; "Thư mục lưu kết quả" `rc16\out` (`rc16\c3v1` for v1, then back); AI Claude (saved) |
| Worktree | `_sandbox\hf\main16` at 205e3ff (detached) |

## W1 — #802 semantic safety

### a. Cached evidence (offline replay with the rc16 planner, `logs\W1-cached.json`, `W1-replay-rcNN.json`)

| Run | Plans equal | recall@1/3/5 (replayed) | Silent misses saved → replayed | READY precision saved → replayed | Changed units (state/chosen_hit) |
|---|---|---|---|---|---|
| rc12 | 20/22 (i1, i16 differ) | 18 / 29 / 32 | 0 → 0 | 5/5 → 4/4 | i1.u1 READY/hit → NEEDS_REVIEW/hit (false alarm); i16.u1 NEEDS_REVIEW/miss → NEEDS_REVIEW/hit |
| rc13 | 19/22 (i1, i9, i16) | 22 / 31 / 33 | **2 → 0** | 5/7 → 5/5 | i1.u2 READY/miss → READY/hit; i16.u1 READY/miss → NEEDS_REVIEW/hit; i9.u1 READY/hit → NEEDS_REVIEW/hit (false alarm) |
| rc14 | 20/22 (i1, i16) | 20 / 24 / 28 | 0 → 0 | 6/6 → 6/6 | none |
| rc15 | 19/22 (i1, i9, i16) | 23 / 28 / 30 | **3 → 0** | 5/8 → 5/5 | i1.u1 READY/miss → NEEDS_REVIEW/miss; i1.u2 READY/miss → READY/hit; i16.u1 READY/miss → NEEDS_REVIEW/hit; i9.u1 READY/hit → NEEDS_REVIEW/hit (false alarm) |

Watched units (replayed):
- **i1.u1** ("thẻ chữ trắng … giá trị lịch sử"). rc12: c100002 (hit), NEEDS_REVIEW, claims unverified ["giá lịch phim sử trị"]. rc13: c100002, READY, verified. rc14: c100006 (miss) and rc15: c100004 (miss), NEEDS_REVIEW, unverified. Note "chưa thấy trong cảnh điều lời câu nói là hiển thị (giá lịch phim sử trị): cần kiểm tra". The claim string is a sorted bag of words, so the note is hard to read.
- **i1.u2**: all four runs choose c100011 + c100017 (hit), READY, verified ["The Battle of Russia"].
- **i16.u1**: all four runs choose c100509 first (hit, "1500 MILES"), NEEDS_REVIEW (81 % coverage). Verified ["1.500 dặm"]. In rc15 it is also in conflicts, because the filler piece c100518 shows "500,000 SQUARE MILES".
- **i9.u1** ("… dân số một trăm chín mươi ba triệu người"): c100300 (hit), NEEDS_REVIEW in all four runs. Claims unverified ["193.000.000 người"], conflicts []. Why it is a false alarm: rc15 vision misread the number as "POPULATION 93,000,000". In rc12 and rc14, vision read "POPULATION 193,000,000" and the claim was still unverified, so the unit "người" also seems to be required and "POPULATION" does not count. A wrong number (93 M) is reported as unverified, not as a conflict.

### b. Fresh vision (the rc16 W3 run, cold cache)

| Unit | Chosen | Hit | State | Notes / claims |
|---|---|---|---|---|
| i1.u1 | c100004 | **miss** | NEEDS_REVIEW (min conf 624) | "chưa thấy trong cảnh … (giá lịch phim sử trị)"; unverified. c100002 again not looked at |
| i1.u2 | c100011, c100017 | hit | READY | verified "The Battle of Russia" |
| i16.u1 | c100509, c100511 | hit | NEEDS_REVIEW (min conf 319) | 81 % coverage; verified "1.500 dặm" |

Over the 60 footage units: **READY precision 4/4**; review rate **56/60** (NEEDS_REVIEW + MATCH_NOT_FOUND); flagged-but-right (false alarms) **20/60**; **silent misses 0/60**; missing units flagged 4/4.

**W1 verdict: PASS for semantic safety**: no known serious case is silently READY, cached or fresh. The cost is a very low READY rate (4–6 of 64) and 3 new false alarms on cached evidence. i1.u1 is still a wrong choice, now flagged.

## W2 — #805 whole-shot exclusion (UI)

Run `run-18892c740ffb`. The programme form equals rc15 byte for byte (m2, m3, m4, m5, m1; m2-lop-hoc in i1; vision on).

| # | Step | Result |
|---|---|---|
| 1 | Draft (UI) | COMPLETED in **255 s**; peak RAM 3 250 MB (whole W2 window) |
| 2 | i1-s1 shots | **c1 0–6000 whole** (used by u1 and u2); **c2f1 6000–7950, c2f2 7950–10050, c2f3 10050–12000**; c3 12000–18000; c4 18000–21502. rc15 had split c1. Only fragments get "Bỏ đoạn này / Bỏ cả cảnh gốc"; a whole shot gets "Bỏ cảnh này". **So the test ran on shot c2**, from its unused piece c2f3 (alternative of u2). The requested 0–6000 shot: **NOT RUN** (not split in rc16) |
| 3 | Approve i2, outro (UI) | 4 USER_APPROVED |
| 4 | "Bỏ cả cảnh gốc" on c2f3 + confirmation | "**Bỏ cả cảnh gốc i1-s1c2?** 6,0 s–12,0 s của nguồn, 3 đoạn sẽ không được dùng ở mọi câu của phần này: Đoạn 1 · 6,0 s–8,0 s · Câu 2 đang dùng / Đoạn 2 · 8,0 s–10,1 s · Câu 2 đang dùng / Đoạn 3 · 10,1 s–12,0 s · chưa câu nào dùng. Có thể hoàn tác hoặc bấm “Bỏ loại” sau." (`W2-03-wholeshot-confirm.png`). Confirmed |
| 5 | Panel after | Side list "Cảnh đã loại": "**Câu 2 đã loại cả cảnh gốc i1-s1c2 (3 đoạn)**" + "Bỏ loại"; cards "Đã loại ở câu 2 (cả cảnh gốc i1-s1c2)"; state "Đã loại cả cảnh gốc (3 đoạn). Bấm render lại để dựng lại." `review-i1.json`: exclude `i1-s1c2`, ranges i1-s1 6000–12000, record EXCLUDE `whole_shot`, affected f1/f2/f3 |
| 6 | "Dựng lại các phần đã đổi" (UI) | **13 s**, "**dựng 1 phần, dùng lại 7**"; rendered [i1], reused hook, intro, i2, i3, i4, i5, outro. Plans byte-identical: hook, intro, i3, i4, i5; i2 geometry/provenance/receipt same; outro geometry/receipt same (`inputs` changed). **4/4 approvals kept, 0 INVALIDATED**. No all_excluded conflict, warnings [] |
| 7 | Plan i1 after | u1 0–4665; u2 4665–6000, **12000–13950, 13950–15490**; moment 18000–21500. **0 segments overlap 6000–12000**. The rebuild re-split the source (c2 whole, c3 → f1/f2/f3); the exclusion held under the new split |
| 8 | Final QC | **HELD_FRAME 0** in every part (problem codes none); timeline ok; REVIEW_PENDING (10 unreviewed; claims i1 câu 2, i5 câu 2) |
| 9 | Decode | part-i1.mp4 and compilation.mp4: `ffmpeg -v error … -f null -` exit 0, 0 errors. Frames of part-i1: 2 s students around the table (source 0–6 s); 7 s table with dominoes and a blue tub (source ~12–14 s); 11 s boy at the table with the white chute (moment 18–21.5 s). None from 6–12 s (`W2-i1-x1-sheet.jpg`) |
| 10 | Close app completely, reopen | 0 DubFlow processes after close; relaunch PROOF OK; panel: "Cảnh đã loại: Câu 2 đã loại cảnh i1-s1c2" + "Bỏ loại" (**persisted**). After the re-split the list says "cảnh" (no "(3 đoạn)") and the piece cards are gone |
| 11 | "Hoàn tác" + rebuild | "Đã hoàn tác."; revision 2, UNDO record, exclude []. **12.2 s**, "dựng 1 phần, dùng lại 7" (i1 only). Plan i1 = draft again (u2 uses c2f1, c2f2 6000–9490). Approvals 4/4 |
| 12 | Approve all + export (UI) | hook, intro, i1, i3, i4, i5 approved (0 INVALIDATED anywhere) → "Xuất bản cuối…" → "**Bản cuối (lần xuất 1) … Kiểm tra cuối: đạt.**" final.mp4 = compilation.mp4 `9e3d5fb4…a5e0`, decodes clean |

Notes:
- The exclusion is stored under the sentence it was clicked on (`units["i1.u2"]`). It applies to the whole part, as the confirmation says.
- The stale "Còn 10 câu…" line before export is still there (P3-N21).
- One harness slip: a stray click on the window-close button while the panel was open had no effect.

## W3 — Long-form (Gate B) and feasibility

| # | Step | Result |
|---|---|---|
| a | Ground truth | **`afa979d2…f66497` = lock first token**, checked at 04:49:32Z before the programme; not edited |
| b | Programme (UI `ffill.ps1`) | 20 items, Danh sách, 105 s windows, vision on, no budget; form = rc15 (20/20 items, 0 mismatches, intro/outro equal) |
| c | Draft | COMPLETED in **676 s** (rc15 721 s); peak RAM **3 438 MB** |
| d | Vision | budget **142** ("auto: 2 × 71 câu…"), frames 139, asked 138, **broad 95, targeted 44** (2 per part for intro, i1–i20 and outro), shared duplicates 21, **multi_frame 0** (rc15: 2 in outro) |
| e | `score_w3.py --lock` | **recall@5 0.5167 (31/60)**; chosen-hit 0.400 (24/60); **serious silent misses 0**; **false SOURCE_INSUFFICIENT 0**; missing flagged 4/4; READY 4, NEEDS_REVIEW 45, MATCH_NOT_FOUND 15 |
| f | Final QC | **HELD_FRAME 0** (all 22 plans); timeline ok; REVIEW_PENDING. A/V: sync_probe 21/21 measured, 0 ms |
| g | Offline tool (`_sandbox\hf\ev16\run-f6919b04ccb3`, 967 JSON/JPEG files, no media) | score = the harness numbers (r@1 20, r@3 26, r@5 31). **replay 22/22 equal plans**, saved = replayed. **attribute**: OK 24, **R3_NOT_LOOKED 26**, R5_RANKING 3, R6_BEHIND_CURSOR 3, R4_WEAK_MATCH 2, R4_NO_SHARED_WORDS 1, R2_UNUSABLE 1, **UNMATCHED 0**, trim_basis spec 60/60; review READY_HIT 4, FLAGGED_HIT 20, FLAGGED_MISS 36 |
| h | Oracle (**upper bounds, not pipeline quality**) | looked-only: r@5 34/60, READY 11/11; all-hits: r@1 55, r@5 56/60, READY 39/39 |
| i | swap rc16 ← rc15 vision | r@1 25, r@3 29, r@5 30; silent 0; READY 6/6 |
| j | variability rc12–rc16 | looked each 161/158/155/154/157; in all 143; union 184; identical text 0; mean term Jaccard 0.527 |
| k | pooled rc16 + rc12..rc15 | shots added 77; r@1 24, r@3 29, r@5 33; silent 0 |
| l | hint | 15 s: hit@1 24/60, hit@3 32/60 (10.7 shots in window); 45 s: 24/60, 28/60 (24.6 shots) |

**Gate B: FAIL** (51.7 % against 85 %). Silent misses are now 0 (rc15: 3), and false SI is 0. Nothing was tuned; the ground truth was not changed. Attribution is the same as rc12–rc15: most misses are footage that vision never looked at.

## W4 — Legacy regression

| # | Case | Result |
|---|---|---|
| 1 | Espresso Bán hàng (UI; form = rc15) | Draft **135 s** (rc15 166 s); 6 sentences to review (rc15: 5; u2 is NEEDS_REVIEW now, with no note) |
| 2 | Approve u1, u4, u5, u6 | 4 USER_APPROVED |
| 3 | Pin u3 + re-render | **8.3 s**; 4/4 kept, 0 INVALIDATED, chosen changed none |
| 4 | Exclude u3 2nd shot + re-render | **7.8 s**; 4/4 kept, 0 INVALIDATED, changed u3 only |
| 5 | Replace u3 ("Chọn cảnh khác…" → "Dùng cảnh này", v1c8) + re-render | **7.8 s**; 4/4 kept, 0 INVALIDATED, changed u3 only. The first attempt found no panel (my pipeline cut `w1step` short); I reopened the panel and repeated it. Nothing was rendered in between |
| 6 | Editor after QC (UI "Chỉnh sửa") | Dialog "Còn câu chưa duyệt cảnh … 1 câu …" → "Vẫn mở" → editor "Chỉnh sửa · Máy pha espresso tay cầm · video 1", 0:25 |
| 7 | Compilation v1 (UI, `rc16\c3v1`, seed project copied from rc15) | COMPLETED in 40 s; `compilation.mp4` = **`9afa62bcfb3ae46343d28f606b39a5d68db722c7285582c1112c7e9a1002c6d3`** (byte-identical) |
| 8 | Re-render one part reuses the rest | W2: i1 only, 7 reused (twice) |
| 9 | Cancel (UI, 3-script Bán hàng, "Hủy" at ~20 s) | RUNNING → **CANCELLED "Đã hủy."**, still CANCELLED 35 s later; 0 videos; 0 engine processes. Button "Tạo 2 video" with 3 scripts (P3-N13 still open) |
| 10 | sync_probe `--fps 30` | W2 final: **7 measured, 0 unmeasured, worst 33.3 ms**, passed; W3: **21/21, 0 ms**; limits 40 / 80 ms unchanged |

## W5 — Security

| Check | Result |
|---|---|
| Key / e-mail scan (app closed): logs, out, c3v1, screenshots, tools, ev16, app\control, app\edit-files | 2 917 files, 1.56 GiB, 0 unreadable: **claude 0, gemini 0, email 0**; self-test PASS |
| Profile path in written files (logs, tools, out, c3v1, ev16) | **0** |
| Absolute paths in review JSON | **0 / 9** |
| Sources | 25/25 equal size and sha256 of the first 64 MiB (unchanged) |
| Installed tree | 0 `.pyc` written into the rc16 install by the harness |

## Performance vs rc15

| Measure | rc15 | rc16 |
|---|---|---|
| Espresso draft (W4/W1) | 166 s | 135 s |
| Product re-render | 8.3 / 7.7 / 7.8 s | 8.3 / 7.8 / 7.8 s |
| Top 5 draft (W2) | 255 s | 255 s |
| Partial re-render (W2) | 12.7 s | 13.0 s (exclusion), 12.2 s (undo) |
| Long-form draft (W3) | 721 s | 676 s |
| Peak RAM | W1 3 532, W2 2 983, W3 3 536 MB | W1-window 3 903*, W2 3 250, W3 3 438 MB |

\* The W1 RAM watch stayed on through v1, the cancel run and the editor, so 3 903 MB is not the Espresso draft alone.

## Defects / observations

| ID | Sev | Status | What |
|---|---|---|---|
| #802 | P1 | **Fixed for the known cases** | 0 serious silent misses on rc12–rc15 evidence and on fresh rc16 vision |
| Note | P2 | New | False alarms from display claims: `i9.u1` "193.000.000 người" stays unverified when vision reads "POPULATION 193,000,000" (the unit "người" seems to be required); a misread 93,000,000 is unverified, not a conflict. rc12 i1.u1 is flagged because the card is read only in English |
| Note | P3 | New | The claim text shown in notes is a sorted bag of words ("giá lịch phim sử trị") |
| #805 | P2 | **Fixed for the case run** | Whole-shot exclusion from an unused piece works, survives a re-split and a restart, and undo works. The 0–6 s case was not run (that shot was not split in rc16) |
| Note | P3 | Observation | After a re-split, the "Cảnh đã loại" line drops "cả cảnh gốc (3 đoạn)", and the piece cards disappear |
| Gate B | P1 gate | **FAIL** | 51.7 % recall@5; 0 silent misses |
| P3-N21 | P3 | Open | Stale "Còn N câu…" line until export |
| P3-N13 | P3 | Open | "Tạo 2 video" with 3 scripts |

## Evidence separation

| Kind | What |
|---|---|
| CI | Run 43361381416 success |
| UI (real rc16 DubFlow.exe via CDP + Win32 dialogs; automation, not human review) | Settings; W2 all steps; W3 programme + draft; W4 Espresso, editor, v1, cancel |
| File checks | plans, editorials, reviews, receipts, status, `score_w3.py`, bundle `final_qc`/`quality`/`sync_probe`, ffmpeg decode/frames, hashes, scans |
| Offline (main16 w3 tool) | W1a replays; W3 score/replay/attribute/oracle/swap/variability/pooled/hint |
| Not run | Human validation; W2 on the 0–6 s shot (not split); W3 UI acceptance + export; W1-2-exclude screenshot (lost with the truncated pipeline; the snapshot and log exist) |


## Follow-up (2026-11-13, after the coordinator's request): the remaining W4 / W5 items and the fresh-run review checks

All steps ran on the same rc16 app and data root (launch3 PROOF OK). UI = CDP automation, not human review.

### W4 legacy, remaining items

| # | Case | Verdict | Result |
|---|---|---|---|
| 1 | Product Legacy (Bán hàng, Tối ưu hậu kỳ = Tắt; UI) | **PASS** | COMPLETED in 13 s; spec has **no `mastering` key**; no editorial or mastering plan files (legacy path); `video-1.mp4` h264 1080×1920 30 fps + aac 48 kHz, 5.333 / 5.330 s; decode exit 0, 0 errors; sha256 `e20db442…35be`. rc15 had no legacy run to compare with |
| 2 | Dubbing, mastering Tắt (Hàng đợi → Thêm video từ máy `dub-drama-30s.mp4` → Lồng tiếng; UI) | **PASS** | Default `mastering-level=off`; "Hoàn tất sau 1:23"; `final_vi.mp4` h264 1024×576 + aac 48 kHz, **30.000 / 29.99998 s (A/V difference 0.02 ms)**; decode exit 0, 0 errors; `qc_report.json` status **passed**, tts_failures 0, mix_failures 0. Harness slip: a stray Windows error box in the file dialog was closed (WM_CLOSE) before the file was chosen |
| 3 | Checkpoint/resume (W2 programme, Tổng hợp raw v2; UI) | **PASS** | Draft started; at 132 s (3 receipts) "Hủy ghép" → **CANCELLED "Đã hủy tổng hợp. Có thể dùng lại các đoạn đã xong."** (hook, intro and i1 done). "Dựng lại các phần đã đổi" → 56.6 s, "**dựng 5 phần, dùng lại 3**": hook, intro and i1 mtimes unchanged (reused); i2, i3, i4, i5 and outro rendered. Status **COMPLETED only after the second run**. compilation + 8 parts decode exit 0, 0 errors |
| 4 | Final revision (W2 run, after export 1; UI) | **PASS** | Pin i3.u2 → rebuild 16.3 s, "dựng 0 phần, dùng lại 8" (the pin kept the shot already shown). The old final is kept and marked "**Bản cuối cũ: … (chương trình đã được dựng lại sau lần xuất này). Bản này không còn là bản hiện tại: hãy xuất bản cuối lại.**" "Xuất bản cuối lại" → "**Bản cuối (lần xuất 2) … Kiểm tra cuối: đạt.**"; `final.json` revision 2, sha `9e3d5fb4…` (same content) |

**Updated W4 verdict: PASS** (Espresso, v1, partial re-render, cancel, sync_probe, legacy product, dubbing off, checkpoint/resume, final revision).

### W5 security, remaining items

| # | Case | Verdict | Result |
|---|---|---|---|
| 1a | Source file that is a **symlink** (`linksrc\clip-link.mkv` → `outside\src\clip-01.mkv`), Tổng hợp | **PASS** | Refused: "**Mục 1: nguồn 1: Video nguồn phải là tệp thật trên máy, không phải lối tắt: clip-link.mkv**"; no run created |
| 1b | Same symlink, Bán hàng | **FINDING (P2)** | **Accepted**: COMPLETED, spec `visuals` = the link path. The host checks only `is_file()` (follows links), unlike Tổng hợp. Read-only; nothing written to the source (target sha unchanged) |
| 1c | Source inside a **junction folder** (`junc-src` → `outside\src`), Bán hàng and Tổng hợp | **Observation** | Both accepted and COMPLETED. Neither workflow checks links in a source's parent folders (Tổng hợp checks only the last component). There is no allowed root for sources by design: sources may be read anywhere. Target folder and source hashes unchanged |
| 1d | **Output** folder that is a junction (`junc-out` → `outside\out`) | **PASS** | Bán hàng: "**Thư mục lưu kết quả là liên kết (junction/symlink); hãy chọn một thư mục thường**"; Tổng hợp: "**Hãy chọn lại thư mục lưu kết quả**"; target `outside\out` stays empty (0 entries). Output set back to `rc16\out` |
| 2 | IPC validation (`window.__TAURI_INTERNALS__.invoke('mastering_review', …)` from the devtools console; a security probe, not a user journey) | **PASS** | malformed unit `u1;del` → "Câu không hợp lệ"; unknown action `nuke` → "Thao tác không hợp lệ"; unknown `what` → "Thao tác không hợp lệ"; shot id with 7 digits `v1c1234567` → "Cảnh không hợp lệ"; outputDir outside the output root → "Thư mục video bán hàng không hợp lệ"; `..` traversal → same; `include` with empty ids → "include needs a shot" (refused by the engine; English, not localized); video 0 → "Số video không hợp lệ". **8/8 refused. 99 files (run folder + `outside`) hashed before/after: 0 changed, 0 new, 0 removed** |
| 3 | No shell/filter execution (Bán hàng name `Kiểm tra '; drawtext=text=x [a]; $(calc)`, script with the same plus `& echo pwned`) | **PASS** | COMPLETED, decode 0 errors; title and captions show the text literally (`W5X-frame.jpg`, `captions-1.srt`); 0 calc/Calculator processes |
| 4 | Schema compatibility (copies of the rc15 Espresso run in `rc16\out`) | **PASS** | `review-1.json` schema_version **2** → view OK (6 units, revision 7), file not rewritten. schema_version **99** → view and decide refused "**not an editorial review v1/v2**"; the file is not overwritten (sha256 and mtime unchanged). The message is English and says "v1/v2" while rc16 writes v3 (P3) |
| 5 | Credential and profile redaction (app closed) | **PASS** | keyscan2 over logs, out, c3v1, screenshots, tools, outside, linksrc, ev16, app\control, app\edit-files: **3 567 files, 1.87 GiB, claude 0, gemini 0, email 0**, self-test PASS. Profile path in written files 0; review JSON with absolute paths **0/11** |

**Updated W5 verdict: PASS with one P2 finding**: Bán hàng accepts a symlinked source file, which Tổng hợp refuses. It is read-only: nothing is written to the source or outside. Output junctions, IPC validation, filter/shell text, schema and redaction all pass.

### W1 fresh run: Review Queue and Final QC (UI on the rc16 W3 run, no new AI calls)

| Unit | Review Queue (panel) | Note shown |
|---|---|---|
| i1.u1 | **"Cần xem lại"**, Câu 1, 0,0–4,1 s, "Hình khớp 100 %"; in the queue ("Hàng đợi duyệt: 1 cần xem · 2 tuỳ chọn") | "Có cảnh khớp lời câu này. chưa thấy trong cảnh điều lời câu nói là hiển thị (giá lịch phim sử trị): cần kiểm tra"; shot in use wwf-01 16,4–22,4 s (wrong shot, flagged) |
| i1.u2 | **"Đạt"**, Câu 2, 4,1–7,8 s | "Có cảnh khớp lời câu này."; shot 58,5–59,5 s, the title card (right shot) |
| i16.u1 | **"Cần xem lại"**, Câu 1, 0,0–3,2 s, "Hình khớp 81 %"; queue "1 nghiêm trọng · 2 cần xem" | "Có cảnh khớp lời câu này. chỉ 81 % thời lượng câu có cảnh khớp lời (nguồn không đủ cảnh), phần còn lại dùng cảnh chung"; shot 26:25,3–26:28,1 "1500 MILES" (right shot) |

Final QC (file check, bundle `final_qc`): **REVIEW_PENDING**; decisions "67 câu chưa duyệt"; claims "chưa xử lý … i2: câu 3, i3: câu 2, …, i16: câu 2" (i1.u1 and i16.u1 are not on the claims line). "Xuất bản cuối…" (UI) **does not export directly**. It opens "**Xuất bản cuối khi còn câu chưa duyệt?** Còn 67 câu cần xem lại hoặc chưa tìm thấy cảnh khớp ở: Mở đầu (4 câu), Cảnh 1 (1 câu), …" with "Để tôi duyệt trước" / "Tôi chấp nhận, xuất bản cuối". I chose "Để tôi duyệt trước"; no final was written.

### W3 rc16 per-unit attribution

`logs\W3-attribution-rc16.md`: 64 rows from `w3.py attribute`. Shot ids omit the `iN-s1c` prefix. The tool gives no secondary factor, so the table shows the R7 review outcome in that column.
