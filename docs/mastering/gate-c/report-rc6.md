# Gate C/S check — Windows, DubFlow 1.1.96-rc6 (Phase 4 readiness, Epic #637)

- Date: 2026-11-11. Owner's Windows 11 machine, CPU only, one job at a time.
- Folder: `D:\Projects\DubFlow\_sandbox\phase4-gate\rc6\`.
- rc6 = rc5 + PR #747:
  - the #745 half weight for unaccented single words is removed;
  - a half-matched opening now flags its pieces;
  - in a multi-source programme every intro piece is needs_review.
- Scope of this round:
  - the semantic real-footage regressions, fresh and replayed on the saved rc5 evidence;
  - a desktop smoke run of the C1 Top 5 programme;
  - a key scan and a source-media check.
  - C2–C4 were **not** re-run: rc6 has no desktop change compared with rc5.
- Not done, by design:
  - no release, publish or tag;
  - the stable install and its data were not touched;
  - `_sandbox\models-u` was not touched.
- AI key handling:
  - The app read its own key from Windows Credential Manager.
  - Headless runs put the key into the child environment only.
  - Nothing was printed or written. The key scan reports counts only.

## Summary

| Item | Verdict | Why |
|---|---|---|
| Semantic E2E (fresh, packaged rc6 runtime) | **Improved; no unflagged miss or steal left in the six sets** | 0 silent misses and **0 unflagged stolen key footage** in all six sets. Coverage rc5 → rc6: P1 62.3 → **72.4**, P2 38.4 → **59.6**, P3 42.1 → **50.7**, B1 100 → 99.2, B5 100 → 96.6, D1 43.0 → 48.0. Remaining: false alarms stay high (B5 11, B1 8), review load grows (B5 31 flags, D1 16), and the D1 A/V offset is −41 ms (P2) |
| Code effect alone (rc6 code on the saved rc5 evidence) | **Positive or neutral** | P1 62.3 → 78.4 (halving removed). B1: the 4200 ms unflagged intro steal is now **flagged** (0 unflagged). P2, P3 and D1 equal the control (the rc5 code on the same replay); B5 is close (stolen 3025 vs 2943 ms, repeats 4 vs 2, with 2 frames asked again) |
| Desktop smoke (real rc6 DubFlow.exe via CDP) | **PASS** | Same Top 5 programme built through the UI and planned. 14 sentences: **READY 1, NEEDS_REVIEW 13, MATCH_NOT_FOUND 0, SOURCE_INSUFFICIENT 0** (rc5: 0 / 13 / 0 / 1). Intro piece `needs_review: true`. Draft rendered (8/8 parts, 330 s). Final export asked for confirmation (13 sentences listed; "Để tôi duyệt trước" left no final), then exported on acceptance |
| Credential scan | **0 hits** | 5 447 files, 2.76 GiB, 0 unreadable: data dir with the WebView2 profile, outputs, logs, results, semantic outputs and runs, replays, TTS cache, session scratchpad (app closed first) |
| Source media | **Unchanged** | 5 rc4 originals + 5 rc6 copies, SHA-256 before = after (10/10) |

## 0. Build identity

| Item | Value |
|---|---|
| Artifact | `DubFlow-1.1.96-rc6-windows-x64`, run `43019112717` "Windows Release" = success (installed-pipeline, desktop launch and inventory smokes passed) |
| Commit | `85b9d8173ec9c366703510e733ae0c25d503b059` (= `release-evidence.json` `source_sha`) |
| Channel | candidate, `production_qualified: false`, unsigned |
| Zip SHA-256 | `e53a5f3d…71a9bf` (= `release.sha256`) |
| `app\bin\DubFlow.exe` SHA-256 | `83aa539a3709876084cb8305b68cdc8c7b3e7629ba7f7b9a8a839940a9e64f46` |
| FFmpeg | `--enable-version3` only (LGPL). 0 `.pyc` files |
| Code check | `LOOSE_WORD_SHARE` is no longer in `engine/dubflow/mastering/align.py` |
| Machine | Windows 11 Home 10.0.26300, i7-12700H, 39.7 GB RAM, CPU only |
| Install | Same as rc4/rc5: zip extracted with Python `zipfile` to `rc6\app\versions\1.1.96-rc6\`, `current.json` written (manifest `6b125203…`), models copied from the rc5 sandbox without the TTS cache. Data root = `rc6\app` |

## 1. How the UI was driven, and proof

- Tools: same as rc5 (`tools\launch.ps1`, `cdp.mjs`, `filedlg.ps1`).
  - Real mouse events and `Input.insertText`.
  - Win32 Open dialog.
  - `<select>` values set after a real click, as in earlier rounds.
  - The ↓ buttons were tagged with a temporary `data-e2e` attribute to target them.
  - No IPC calls, no app-file edits, no mocks.
- Proof (`logs\cdp-proof.json`, `logs\cdp-proof-processes.txt`):
  - CDP browser pid 17976; its parent is pid 8184 = `…\phase4-gate\rc6\app\versions\1.1.96-rc6\app\bin\DubFlow.exe`.
  - `--user-data-dir=…\rc6\app\control\webview2\312e312e39362d726336\EBWebView --remote-debugging-port=9446`.
  - Page `http://tauri.localhost/`.
- The app was closed with its window X.

## 2. Desktop smoke — Top 5 from raw footage (desktop UI)

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Open, Ghép video, raw mode | `#nav-compilation`, "Video gốc (nhiều nguồn)" | Form shown | |
| 2 | Same programme as rc5 C1 | 5 videos via the Win32 dialog; m1 moved to the end with ↓; rc4/rc5 titles, narration, hook/intro/outro; "Đánh số đếm ngược"; Trúc Ly; 2 min; Cân bằng; AI vision + AI ranking | Ranks 5,4,3,2,1 = m2, m3, m4, m5, m1 | `C1-04-setup-filled.png` |
| 3 | Plan + draft render | "Dựng bản nháp" | "dựng 8 phần, dùng lại 0" in **330 s** (cold). Claude vision: 80 frames, 77 asked | `C1-06-result.png`, `results\C1-1-after-render\` |
| 4 | Review states | `editorial-*.json` (`results\C1-1-units.txt`) | **READY 1** (#4 Câu 2), **NEEDS_REVIEW 13**, MATCH_NOT_FOUND 0, SOURCE_INSUFFICIENT 0. #2 Câu 1 and #1 Câu 1 are at 0 % and say "có cảnh khớp lời câu này nhưng không đặt được vào câu… cần kiểm tra". 1 of 27 pieces reaches confidence ≥ 600 | |
| 5 | Intro pieces flagged | `plan-intro.json` | `intro.s1` i5-s1 49294–53494, confidence 584, **needs_review: true**; unit NEEDS_REVIEW | |
| 6 | Final export gate | "Xuất bản cuối…" → "Để tôi duyệt trước" → again → "Tôi chấp nhận, xuất bản cuối" | Dialog after 3.5 s: "Còn 13 câu cần xem lại… Câu mở màn (1), Mở đầu (1), #5 (2), #4 (1), #3 (2), #2 (2), #1 (2), Kết thúc (2)". First answer: no `final.mp4`. Second: `final.mp4` = `compilation.mp4` = `0bd50e74…`, video 75.893 s / audio 75.905 s | `C1-13-final-export-confirm.png`, `C1-16-final-exported.png` |

## 3. Semantic E2E on real footage (packaged rc6 runtime)

- Fresh runs: `sh tools\seq_sem.sh` (10 runs), then `runtime\python.exe -X utf8 -B -I tools\analyze_rc6.py <run> semantic\out\<set> <truth> results\semantic`.
  - Specs: `phase2-e2e\specs-u`. Truth: `results-main-f75bd2b\truth`. Vision: fresh Gemini `gemini-3.5-flash-lite`.
- Replays: `tools\replay.py <code> <label> rc5\semantic\out <work>`.
  - It copies each rc5 run folder, deletes its saved plans and decision logs, and plans again with the given code, reusing the cached vision. It then scores with rc6 `quality`.
  - The control replays the rc5 code the same way.
  - Frames asked again (so slightly fresh evidence): P3 1 frame in both replays; B5 2 frames in the rc6 replay.

| Set | Run | Coverage % | Units ok | Silent | False alarms | Contra ms | Stolen ms flagged / unflagged | Repeats | A/V ms | Review flags |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 Espresso (pin) | rc4 | 66.4 | 2/6 | 0 | 1 | 2352 | 0 / 0 | 0 | −3.3 | – |
| P1 Espresso (pin) | rc5 | 62.3 | 3/6 | 0 | 2 | 0 | 1000 / 0 | 0 | −3.3 | – |
| P1 Espresso (pin) | **rc6 fresh** | **72.4** | 4/6 | 0 | 3 | 0 | 1523 / 0 | 0 | −3.3 | – |
| P1 Espresso (pin) | rc5 code on rc5 evidence (control) | 62.3 | 3/6 | 0 | 2 | 0 | 1000 / 0 | 0 | −3.3 | – |
| P1 Espresso (pin) | rc6 code on rc5 evidence | 78.4 | 3/6 | 0 | 2 | 0 | 1000 / 0 | 0 | −3.3 | – |
| P2 Máy thổi bụi | rc4 | 61.3 | 0/6 | 0 | 0 | 1725 | 3564 / 0 | 0 | −3.3 | – |
| P2 Máy thổi bụi | rc5 | 38.4 | 0/6 | 0 | 0 | 624 | 2449 / 0 | 0 | −3.3 | – |
| P2 Máy thổi bụi | **rc6 fresh** | **59.6** | 2/6 | 0 | 2 | 600 | 0 / 0 | 0 | −3.3 | – |
| P2 Máy thổi bụi | rc5 code on rc5 evidence (control) | 38.4 | 0/6 | 0 | 0 | 624 | 2449 / 0 | 0 | −3.3 | – |
| P2 Máy thổi bụi | rc6 code on rc5 evidence | 38.4 | 0/6 | 0 | 0 | 624 | 2449 / 0 | 0 | −3.3 | – |
| P3 Hũ tăm | rc4 | 50.9 | 1/6 | 1 | 1 | 1648 | 2648 / 0 | 0 | +10.0 | – |
| P3 Hũ tăm | rc5 | 42.1 | 0/6 | 0 | 0 | 0 | 0 / 0 | 0 | +10.0 | – |
| P3 Hũ tăm | **rc6 fresh** | **50.7** | 1/6 | 0 | 1 | 1000 | 1000 / 0 | 0 | +10.0 | – |
| P3 Hũ tăm | rc5 code on rc5 evidence (control) | 48.8 | 0/6 | 0 | 0 | 0 | 1467 / 0 | 0 | +10.0 | – |
| P3 Hũ tăm | rc6 code on rc5 evidence | 48.8 | 0/6 | 0 | 0 | 0 | 1467 / 0 | 0 | +10.0 | – |
| B1 Top 5 old | rc4 | 88.8 | 9/11 | 0 | 7 | 1169 | 4661 / 0 | 3 | −11.4 | 26 |
| B1 Top 5 old | rc5 | 100.0 | 11/11 | 0 | 8 | 0 | 3618 / **4200** | 2 | −11.3 | 22 |
| B1 Top 5 old | **rc6 fresh** | **99.2** | 11/11 | 0 | 8 | 0 | 4105 / **0** | 1 | −11.3 | 23 |
| B1 Top 5 old | rc5 code on rc5 evidence (control) | 100.0 | 11/11 | 0 | 8 | 0 | 3618 / 4200 | 2 | −11.3 | 22 |
| B1 Top 5 old | rc6 code on rc5 evidence | 100.0 | 11/11 | 0 | 9 | 0 | 9578 / **0** | 2 | −11.3 | 25 |
| B5 Top 5 (5 src) | rc4 | 100.0 | 13/13 | 0 | 11 | 0 | 8596 / 0 | 3 | −11.4 | 22 |
| B5 Top 5 (5 src) | rc5 | 100.0 | 13/13 | 0 | 12 | 0 | 2943 / 0 | 2 | −11.3 | 27 |
| B5 Top 5 (5 src) | **rc6 fresh** | **96.6** | 12/13 | 0 | 11 | 0 | 6216 / 0 | 3 | −11.3 | 31 |
| B5 Top 5 (5 src) | rc5 code on rc5 evidence (control) | 100.0 | 13/13 | 0 | 12 | 0 | 2943 / 0 | 2 | −11.3 | 27 |
| B5 Top 5 (5 src) | rc6 code on rc5 evidence | 100.0 | 13/13 | 0 | 12 | 0 | 3025 / 0 | 4 | −11.3 | 31 |
| D1 Why We Fight | rc4 | 43.0 | 2/4 | 0 | 2 | 0 | 0 / 0 | 0 | −108.0 | 11 |
| D1 Why We Fight | rc5 | 43.0 | 2/4 | 0 | 2 | 0 | 0 / 0 | 0 | −8.0 | 7 |
| D1 Why We Fight | **rc6 fresh** | **48.0** | 2/4 | 0 | 2 | 0 | 0 / 0 | 3 | **−41.3** | 16 |
| D1 Why We Fight | rc5 code on rc5 evidence (control) | 43.0 | 2/4 | 0 | 2 | 0 | 0 / 0 | 0 | −8.0 | 7 |
| D1 Why We Fight | rc6 code on rc5 evidence | 43.0 | 2/4 | 0 | 2 | 0 | 0 / 0 | 0 | −8.0 | 7 |

What the replays show (code effect only):

**P1**
- rc6 code on the rc5 evidence gives 78.4 % against 62.3 % for the control: removing the half weight brings u1 back (0 → 875 ‰).

**P2**
- Equal to the control (38.4 %). The fresh rc6 run reaches 59.6 % with new Gemini wording.
- So the rc5 drop was vision wording, not code, as reported in the rc5 follow-up.

**B1**
- Same coverage.
- The rc5 unflagged 4200 ms intro steal is now **flagged**: 9578 flagged / 0 unflagged against 3618 / 4200 for the control. This is the multi-source intro rule.

**P3, D1**
- Equal to the control. P3's control is 48.8 % here, not 42.1 %, because 1 frame was asked again in both replays.

**B5**
- Same coverage. Stolen 3025 vs 2943 ms (all flagged), repeats 4 vs 2, flags 31 vs 27.
- 2 frames were asked again in the rc6 replay, so this is not purely code.

Notes on the fresh rc6 runs:

**D1 A/V −41.3 ms (rc5 −8.0)**
- Per-part video/audio: intro 3.500 / 3.480, may-y-tuong 10.800 / 10.820, why-we-fight 20.067 / 20.056 s.
- No part is short of picture by more than 20 ms (the picture-end clamp holds: the may-y-tuong moment ends at 40040 = source picture end).
- The extra offset builds up at the concat.
- The why-we-fight part is longer this time (different shots from the fresh vision). D1 also has 3 REPEATs this time.

**B5 96.6 %**
- `goldberg-lop-hoc.u2` at 595 ‰, flagged.
- Stolen 6216 ms, all flagged: intro 2471, purdue-2019.u1 3145, mo-phong-3d.u1 600.

## 4. Performance (record only)

| Measure | rc6 | rc5 |
|---|---|---|
| Desktop C1 draft (cold, Claude vision) | 330 s, 80 frames / 77 asked | 291 s, 70 / 67 |
| Final-export dialog | 3.5 s | 12.9 s (first) / 2.8 s |
| Headless walls P1 / P2 / P3 / B1 / B5 / D1 | 118 / 41 / 123 / 198 / 264 / 186 s | 159 / 55 / 206 / 225 / 132 / 179 s |
| Replays | about 1–2 min per set; vision asked 0 except P3 (1) and B5 (2) | |

## 5. Defects (status in rc6)

| ID | Sev | Status | Note |
|---|---|---|---|
| P1-5 | P1 (semantic) | **Partly fixed** | Desktop Top 5 now 1 READY / 13 NEEDS_REVIEW / 0 SOURCE_INSUFFICIENT. Still almost every sentence needs review: 26 of 27 pieces are under confidence 600 |
| P1-6 | P1 (semantic) | **Fixed for the code part** | The rc5 drops were the halving (P1, part of P3) plus vision wording (P2). Fresh rc6: P1 72.4, P2 59.6, P3 50.7 % (rc4: 66.4 / 61.3 / 50.9) |
| P1-7 | P1 (semantic) | **Fixed** | 0 unflagged stolen key footage in every set, fresh or replayed |
| P1-8 | P1 (semantic) | **Not reproduced this round** | 0 SOURCE_INSUFFICIENT on the desktop Top 5 and on P2 u4. The rc5 cause (common words in a one-source part; a relevance threshold edge) is unchanged in code, so it can return with other vision wording |
| P2-9 | P2 | Open | D1 −41.3 ms at the concat (each part ≤ 20 ms) |
| P2-15 | P2 | Open (new observation) | Review load grows: B5 31 flags (rc5 27), D1 16 (rc5 7). False alarms B5 11, B1 8 |
| P2-1…P2-7, P2-10, P2-11, P2-14 | P2 | Not re-tested (no desktop change since rc5) | See `report-rc5.md` |

## 6. Evidence separation

| Kind | What |
|---|---|
| CI (not re-run) | Run `43019112717` Windows Release success, installed smokes passed (`release-evidence.json`) |
| Packaged runtime (headless) | 10 fresh semantic runs + 6 scorings; 12 replays (rc6 and rc5 code on the rc5 evidence) |
| Desktop UI (real DubFlow.exe via CDP + Win32 dialog) | Smoke steps 1–6 |
| Not run | C2, C3, C4 (no desktop change); RAM peaks |
