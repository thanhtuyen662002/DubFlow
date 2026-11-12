# Assisted Editorial Production — Windows E2E, DubFlow 1.1.96-rc8

- Date: 2026-11-12. Owner's Windows 11 machine, CPU only ("Chỉ dùng CPU" on; no NVIDIA GPU), one job at a time.
- Folder: `D:\Projects\DubFlow\_sandbox\assisted\rc8\` (this report, `results.json`, `metrics.json`, `screenshots\`, `logs\`, `tools\`).
- rc8 = rc7 + #769 (P1-N1 product opening), #770 (status.json writes, engine logs), #772 (review queue, decision records), #774 (Final QC gate). Directive "Assisted Editorial Production", Epic #637.
- Not done, by design: no release, publish or tag; the stable install and its data were not touched; `_sandbox\models-u` was not touched; no question was asked of the owner.
- AI key: the app read its own key from Windows Credential Manager (vision provider Claude). Nothing was printed or written; scans report counts only. The user profile path is written as `%USERPROFILE%`.

Labels used for every step:

- **UI (CDP):** the real rc8 `DubFlow.exe`, real mouse and keyboard events over WebView2 CDP, Win32 messages for the native file and folder dialogs.
- **Host diagnostic:** the packaged rc8 engine module run headless from the install (same runtime), outside the UI. Not counted as a UI step.
- **File check:** hashes, ffprobe, reading run files.

## Summary

| Scenario | Result | One line |
|---|---|---|
| A Safety | **PASS** | Product opening over footage without the product is NEEDS_REVIEW with the reason, queued as Nghiêm trọng, Final QC blocks the editor until settled. Kill → FAILED, cancel → CANCELLED, two 2.5 s share-deny locks on status.json → run still COMPLETED. |
| B Assisted Product (3 videos) | **PASS with defects** | Every queue/decision action used through the UI, position kept across panel reopen and app restart, QC "render lại" gate seen, all 3 videos settled and opened in the editor. Defects: approvals of other sentences are voided by a pin/replace elsewhere (P2-N5), result row says "Cảnh đã đạt kiểm tra tự động" after human settling (P1-N3). |
| C Assisted Compilation (Top 5, 5 sources) | **PASS** | Confirm dialog with unreviewed sentences; QC blocked export listing "Mục nghiêm trọng"; settled, exported: `final.json` revision 1, sha256 = `final.mp4`. Ranking unchanged. |
| D Missing footage | **PASS (with a stated limitation)** | "Không có cảnh phù hợp" removes the sentence from the queue, writes nothing for the planner, same button takes it back; "Chọn cảnh khác…" gives a REPLACE record. Timestamp-in-footage and import-more are not separate UI actions in rc8 (limitation). |
| E Regression and security | **PASS with one P2** | Dubbing smoke, Mastering Off product, v1 byte-identical (`9afa62bc…`), unknown schema refused without writing, sources unchanged, scans 0. Bán hàng accepts a junction as the output root (pre-existing, P2-N7); Tổng hợp refuses. |
| F Long-form (20 min 1 s) | **Run complete, final not exportable** | 20-min Tổng hợp from Why We Fight; partial re-renders reuse 21–22 of 23 parts; QC measured duration/captions/audio OK and blocked export on frozen frames (HELD_FRAME) and 200+ critical sentences. |

## 0. Build and install

| Item | Value |
|---|---|
| Artifact | `DubFlow-1.1.96-rc8-windows-x64`, Actions run `43141184228` "Windows Release", conclusion success |
| Commit | `d398f9d3b5f2a5a91482dbf3e37d6145bfce67a5` (= `release-evidence.json` `source_sha`), channel candidate, `production_qualified: false`, `unsigned-candidate` |
| Zip SHA-256 | `7acfdfad65f2b5855e91b530d2eb3d22b773e795c971589e9574527d590d7eb9` (= `release.sha256` and evidence) |
| `app\bin\DubFlow.exe` SHA-256 | `0f6d9e8cfda1964ef003135d5f96a565f38449c59f06dd46025731f024883a32` |
| Install | as rc7: zip extracted with Python `zipfile` to `rc8\app\versions\1.1.96-rc8\`, `current.json` written (manifest `bc2b6513…`), models copied from the rc7 sandbox without the TTS cache. Data root = `rc8\app`. 0 `.pyc` |
| Download note | `gh run download` stalled at 0 bytes for 20 min; the same artifact was fetched with 16 parallel HTTP range requests and its zip hash checked against the evidence |

## 1. UI driving and proof

- Tools: `tools\launch.ps1` (rc8 exe, CDP port 9448), `tools\cdp.mjs`, `tools\filedlg.ps1` (Win32 Open/folder dialogs), `tools\q.ps1` (reads the review panel's queue line and badges).
- Proof (`logs\cdp-proof-*.json`, `logs\cdp-proof-processes.txt`): three launches; each time the CDP browser process's parent was `…\rc8\app\versions\1.1.96-rc8\app\bin\DubFlow.exe` (pids 8452, 1616, 5224), `--user-data-dir=…\rc8\app\control\webview2\312e312e39362d726338\EBWebView`, page `http://tauri.localhost/`.
- Never used: Tauri IPC from the page, edits to app files. `<select>` values were set after a real click (as in rc7). Settings: output folder set to `rc8\out` (later `rc8\c3v1` and a junction for E), "Chỉ dùng CPU" turned on.
- Harness slips (mine, corrected): (1) my first panel "Đóng" click hit the window's own close button behind the overlay (no effect); (2) "Thêm mới" in Bán hàng needs up to three clicks before the "Làm trống" confirm appears when a run is selected; (3) adding the same video file twice in Tổng hợp is de-duplicated, so F used 20 hard links of the film; (4) my first wait loops matched old text and once mis-read "xuất bản cuối" as a result; all re-read correctly before recording.

## 2. Scenario A — Safety

Run A: Bán hàng "Nồi chiên không dầu chống dính", one clip (`media\mislead\noi chien khong dau chong dinh.mp4` = rc7's m4 3D-domino clip renamed), the rc7 D3 air-fryer script, Cân bằng, AI vision Claude.

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Draft | UI | COMPLETED in 103 s. `editorial-1.json` u1 `{evidence: NOT_NEEDED, state: NEEDS_REVIEW}`, note "câu mở đầu nói về sản phẩm nhưng AI không thấy sản phẩm trong cảnh được chọn: cần kiểm tra". u2/u3 SOURCE_INSUFFICIENT, u4 (CTA) READY. **The rc7 P1-N1 false READY is gone.** | `A-01-form.png` |
| 2 | Review panel | UI | "Hàng đợi duyệt: 3 nghiêm trọng · 1 tuỳ chọn (nhóm theo cờ của AI, chưa hiệu chỉnh)". Câu 1 badges "Nghiêm trọng · Cần xem lại", "Vì sao nghiêm trọng: câu nêu thông tin cụ thể nhưng không có bằng chứng hình", evidence line "…không cần cảnh riêng, nhưng cảnh đang dùng cần kiểm tra". Shot text "Không thấy nồi chiên không dầu" visible | `A-02-review-open.png`, `A-03-editor-confirm.png` |
| 3 | Open editor unsettled | UI | Confirm "Còn câu chưa duyệt cảnh" → "Vẫn mở" → **editor not opened**: "Kiểm tra cuối chưa đạt. Mục nghiêm trọng: chưa xử lý, không được coi là đã kiểm: câu 1, câu 2, câu 3" | `A-04-qc-blocked.png` |
| 4 | Settle | UI | Câu 1 "Chọn cảnh khác…" → imported shot Clip 1 0,0–2,0 s (REPLACE); Câu 2, 3 "Không có cảnh phù hợp" | `A-05-settled.png` |
| 5 | Editor before re-render | UI | "Còn lựa chọn chưa render. Lựa chọn đã render: chưa render lại sau khi bạn đổi cảnh: câu 1. Hãy render lại…" | `A-06-render-pending.png` |
| 6 | Re-render, editor | UI | "Render lại video này" 4 s; plan segment p1 = source 0–1950 ms "chọn bởi người dùng"; editor opens | `A-07-editor-open.png` |
| 7 | Close app, reopen | UI | Câu 1 "Bạn đã chọn cảnh", "Đang dùng (đã ghim)"; Câu 2/3 "Bạn ghi: không có cảnh phù hợp" | `A-08-reopen.png` |
| 8 | Final video | agent-evaluated | 4/4 sentences show 3D dominoes (the fixture has no air fryer). Exportable by policy because every sentence is a human decision, but not a usable product video: **Gate C for A = BLOCKED by footage** | `A-09-final-keyframes.jpg` |

Engine/status safety (Bán hàng "Máy pha espresso tay cầm", 3 scripts, UI):

| # | Case | How | Result | Evidence |
|---|---|---|---|---|
| S1 | Cancel mid-run (25 s, scene analysis) | UI "Hủy" | `run-9139120ff488\status.json` = `{"state":"CANCELLED","message":"Đã hủy.",…}`, valid JSON; `engine-stdout.log` (119 B) and `engine-stderr.log` (0 B) exist; no python/ffmpeg left; list shows "Đã hủy" after app restart | `SAFE-01-cancelled.png` |
| S2 | Kill engine mid-run (20 s) | `Stop-Process` on the engine python (pid 548) | `run-b42231883e31\status.json` = `FAILED`, `status_source: host`, "Bộ tạo video đã dừng. Hãy thử lại.", valid JSON, never COMPLETED; both log files exist (empty: the process was killed); list shows the failure after restart | `SAFE-02-killed.png`, `logs\SAFE-kill-procs.txt` |
| S3 | status.json held with `FileShare.None` 2.5 s, twice, while RUNNING | `tools\holdlock.ps1` | Both locks taken while `state=RUNNING` (10:15:32, 10:16:05); the run ended **COMPLETED 3/3** in 153 s; `engine-stderr.log` 0 B | `logs\B-holdlock.txt` |

(The first lock attempt on run A missed the run: the re-render took 4 s and finished before the 5 s delay; it is not counted.)

## 3. Scenario B — Assisted Product (3 videos)

Run `run-87d6e2cb52fa`: Espresso footage (phase2 P1 "n" clips 1–4), name "Máy pha espresso tay cầm", 12.900.000đ, 3 scripts (1 = P1 truth script, 2–3 written for this test, `logs\inputs-B-espresso.txt`), template Toàn màn hình, Trúc Ly, **Mastering Cân bằng**, vision Claude. Draft 153 s for 3 videos.

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Queue at draft | UI | v1 "5 cần xem · 1 tuỳ chọn", v2 "1 nghiêm trọng · 4 cần xem", v3 "5 cần xem", each with "(nhóm theo cờ của AI, chưa hiệu chỉnh)" | `B-v1-01-queue.png`, `B-v2-01-queue.png`, `B-v3-01-queue.png` |
| 2 | Walk queue | UI "Câu tiếp theo ›" | v1: u1 → u3 → u4 → u5 → u6 → u2 (READY last); v3: u1 → u2 → u3; v2 opened on the critical u5 first | `logs\…` (q.ps1 output in transcript) |
| 3 | Position kept, panel | UI | v1 on u3 → close panel → reopen: current u3; `review-1.json` `position: "u3"` | |
| 4 | Position kept, app restart | UI | Tested on C (§4 #4): i5.u2 kept across window close and relaunch. On B after restart each panel opened on the first queued sentence because the saved sentence was no longer queued (by design) | `C-04-reopen-position.png` |
| 5 | Actions | UI | Duyệt (APPROVE) ×17; Ghim (PIN) v1 u3; Bỏ cảnh (EXCLUDE) v1 u3; Chọn cảnh khác… (REPLACE) v2 u5, v2 u2, v3 u3; Để sau (REVIEW_LATER) v1 u6, v3 u5; Không có cảnh phù hợp (MARK_SOURCE_MISSING) v2 u5 and back; Hoàn tác (UNDO) v1 u6 | `B-v1-02-decided.png`, `D-*.png`, `B-v3-02-decided.png` |
| 6 | Editor before re-render | UI | v1: confirm (1 unreviewed) → "Còn lựa chọn chưa render… câu 3… Hãy render lại để áp dụng các lựa chọn mới trước khi xuất." | `B-v1-03-render-pending.png` |
| 7 | Re-render each video | UI panel "Render lại video này" | 7.2 s / 6.8 s / 8.2 s | |
| 8 | Editor after round 1 | UI | v3 opens. **v1 and v2 blocked**: "Mục nghiêm trọng: câu 1, câu 5, câu 6" (v1) — approvals voided because the re-plan gave those sentences other shots (P2-N5). Panel: "3 câu cần chọn lại vì lựa chọn trước không còn hợp lệ", each "Cần chọn lại" with the reason | `B-v1-06-invalidated.png` |
| 9 | Round 2 / 3 | UI | v1: re-approved u1/u5/u6 after looking at the new shots, re-render 6.0 s, stable. v2: approved u1, replaced u2 (tamping shot), re-render 6.9 s, stable | |
| 10 | Editor | UI | All 3 videos: QC passes, editor opens | `B-v1-07-editor-final.png` … |
| 11 | Close app, reopen | UI | All decisions shown as saved ("Bạn đã duyệt", "Bạn đã chọn cảnh"); killed/cancelled runs listed with their state | `B-v1-08-reopen.png` … |
| 12 | Decision records | file check | Every record of A, B, C has all 12 contract fields (`run_id, plan_revision, speech_ref, source_id, shot_id, source_ms, type, decision_revision, approval_state, input_fingerprint, decided_at, invalidation_reason`); user records' `run_id` = folder name only (`run-87d6e2cb52fa`). **INVALIDATED records have `run_id: ""`** (P3-N8) | `logs\metrics-AB-records.json`, `logs\metrics-C-records.json` |
| 13 | USER_APPROVED vs AI | UI | In the panel, an approved sentence says "Bạn đã duyệt" and "Đánh giá của AI vẫn là “Cần xem lại”" (correct). **But the result row under each video says "Cảnh đã đạt kiểm tra tự động" once every sentence is settled by the user** (P1-N3) | `B-09-results-label-auto-pass.png` |

## 4. Scenario C — Assisted Compilation (Top 5, 5 sources)

Run `run-912c0a26c383`: raw Tổng hợp "Top 5 cỗ máy phản ứng dây chuyền ấn tượng", Top N, items m2 (#5), m3 (#4), m4 (#3), m5 (#2), m1 (#1) from read-only copies of `_sandbox\phase4-gate\rc4\media`, narration = phase2 B5 texts, hook/intro/outro, rights "có giấy phép", #1 "Âm thanh gốc: Luôn giữ", Trúc Ly, **Cân bằng**, AI vision on.

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Draft | UI | 283 s, 8 parts; ranking shown #5 → #1 as entered; 14 sentences, 1 READY, queue 13 cần xem + 1 tuỳ chọn, 0 critical | `C-02-draft.png` |
| 2 | Xuất bản cuối with unreviewed | UI | Confirm "Xuất bản cuối khi còn câu chưa duyệt? Còn 13 câu…" → "Để tôi duyệt trước" → "Chưa xuất." | `C-03-final-confirm.png` |
| 3 | Review every part | UI (`tools\cpart.ps1`) | queue logged before/after, one "Câu tiếp theo ›" per part, actions: APPROVE, EXCLUDE (i1.u2), Ghim (i4.u1), Để sau (i5.u1), Không có cảnh phù hợp (outro.u2) | `C-review-*.png`, `logs\C-review-log.txt` |
| 4 | Position across app restart | UI | i5 walked to u2 → app closed (window X) → relaunched → panel opens on **i5.u2**; `review-i5.json` `position: "i5.u2"` | `C-04-reopen-position.png` |
| 5 | Re-render | UI "Dựng lại các phần đã đổi" | "dựng 2 phần, dùng lại 6 phần không đổi", ≈25 s. i1.u1 and i4.u2 invalidated (same cascade as B, P2-N5); warnings box lists them (rc7 P3-N4 fixed) | |
| 6 | Xuất bản cuối with critical unsettled | UI | **Blocked**: "Chưa xuất. Kiểm tra cuối chưa đạt. Mục nghiêm trọng: chưa xử lý, không được coi là đã kiểm: i1: câu 1, i4: câu 2". No `final.mp4`. (No confirm dialog this time: the QC failure is shown before asking for acceptance) | `C-05-final-qc-blocked.png` |
| 7 | Settle, export | UI | 3 sentences approved; "Xuất bản cuối…" 6.3 s → "Bản cuối (lần xuất 1): …\final.mp4" | `C-06-exported.png` |
| 8 | final.json | file check | `{"kind":"compilation_final","revision":1,"sha256":"8663fb69…"}` = SHA-256 of `final.mp4` = `compilation.mp4` | |
| 9 | Export line shows QC | UI | **No**: the line shows only "Bản cuối (lần xuất 1): <path>"; QC results appear only when QC fails (P3-N9). Host diagnostic of the same QC: all 8 checks ok (duration 76455 vs 76433 ms, captions end 75723 ms, peak −1.5 dBTP, timeline clean) | `logs\C-final-qc-host.json` |
| 10 | Ranking | UI + file | #5…#1 unchanged from draft to final; AI rank suggestion was off | |
| 11 | Final quality | agent-evaluated | 11 correct, 3 acceptable, 0 wrong; Purdue source audio kept | `C-07-final-keyframes.jpg` |

## 5. Scenario D — Missing footage

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | B v2 u5 (critical MATCH_NOT_FOUND) → "Không có cảnh phù hợp" | UI | Leaves the queue ("1 nghiêm trọng · 4 cần xem" → "4 cần xem"); badge "Bạn ghi: không có cảnh phù hợp"; `review-2.json` `{"u5": {"pin": [], "exclude": [], "source_missing": true}}`; `mastering_plan-2.json` and `editorial-2.json` byte-identical before/after; `Review.edits()` (rc8 source) sends only pins/excludes to the planner | `D-01-marked-missing.png` |
| 2 | Same button "Bỏ ghi “không có cảnh”" | UI | Back in the queue as Nghiêm trọng; record MARK_SOURCE_MISSING with `detail` | `D-02-unmarked.png` |
| 3 | "Chọn cảnh khác…" → imported shot Clip 2 0,0–3,4 s | UI | Record `type: REPLACE`, `shot_id: v2c1`, `source_ms: [0, 3426]`; the picker lists every imported shot with source and time | `D-03-replaced.png` |
| 4 | Also | UI | A u2/u3 and C outro.u2 marked missing; QC treats them as settled | |
| 5 | "Set a timestamp in existing footage", "import more footage" | — | **Not separate UI actions in rc8 — limitation, not a pass.** Today: pick a listed shot (timestamps are the scene cuts, not free), or add clips on the create page and make the run again (the panel says so: "Cần cảnh mới: thêm clip ở trang tạo video rồi tạo lại"); in Tổng hợp, "Sửa chương trình…" can add a source or change Bắt đầu/Kết thúc of an item and re-render | |

## 6. Scenario E — Regression and security

| # | Case | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Dubbing smoke | UI (Hàng đợi → Thêm video từ máy → Lồng tiếng) | Default "Tối ưu hậu kỳ tự động" = **Tắt** (unchanged); `dub-drama-30s` done ≈85 s; h264 1024×576 + aac 48 kHz, 30.000 / 29.99998 s; `qc_report.json` status passed, tts_failures 0, `captions_vi.srt` | `E-03-dub-setup.png`, `E-04-dub-done.png` |
| 2 | Bán hàng Mastering Tắt | UI | COMPLETED; spec has **no `mastering` key**; h264 1080×1920 30 fps + aac 48 kHz, 7.900 / 7.900 s (run written through the junction of #5) | |
| 3 | Compilation v1 | UI ("Dùng lại thiết lập này cho bản mới" → "Ghép video") on a copy of rc7's v1 project | `compilation.mp4` = **`9afa62bcfb3ae463…`** = rc4/5/6/7; 45 s | |
| 4 | Unknown schema (format 9) | UI | "Dự án này được tạo bằng phiên bản DubFlow mới hơn (định dạng 9)…"; no reuse button; 2 files byte- and mtime-identical, no new files | `E-01-future-schema.png` |
| 5 | Junction output root | UI | **Bán hàng accepted** `rc8\junc-out` (junction → `rc8\outside\out`) and wrote the run there. rc8 `product.rs` only checks `is_dir()` for the root (unchanged since rc7); Tổng hợp refuses (`compilation_raw.rs`, "Hãy chọn lại thư mục lưu kết quả", rc7 D5). P2-N7 | `E-02-junction-refused.png` (name kept; shows the run starting) |
| 6 | Sources unchanged | file check | 11/11 read-only media copies unchanged; rc4 originals = copies 5/5; v1 sources 2/2 unchanged; Why We Fight unchanged | `logs\media-hashes-before.json` |
| 7 | Key / e-mail scan (app closed) | `tools\keyscan2.py` | 2 019 files, 2.51 GiB, 0 unreadable: **claude 0, gemini 0, email 0**; self-test PASS. Scope: data dir incl. WebView2 profile, all outputs, v1 copy, junction target, logs, screenshots, tools | `logs\keyscan.txt` |
| 8 | IPC validation tests | — | Not run here (CI only) | |

## 7. Scenario F — Long-form

Source: `_sandbox\phase2-e2e\footage\long\Why_We_Fight.5a.The_Battle_Of_Russia_Part1.webm` (US government film 1943, public domain; 2146.8 s). Raw Tổng hợp "Danh sách", 20 items, each a 105 s window of the film (20 hard links of the same read-only file, because the form de-duplicates one path), narration written for this test (`logs\inputs-F-*.json`), Cân bằng, vision on.

| # | Step | How | Result |
|---|---|---|---|
| 1 | Draft | UI | 831 s, 23 parts, **870.8 s** — the form's estimate said 19 min 51 s (P3-N10: the estimate over-predicts by ~37 %) |
| 2 | Extend 10 narrations | UI "Sửa chương trình…" → "Lưu và dựng lại" | 344 s, "dựng 11 phần, dùng lại 12", 1190.1 s |
| 3 | Outro/intro, intro | UI | 67 s ("2 / 21") → 1197.2 s; 56 s ("1 / 22") → **1201.09 s (20 min 1 s)** |
| 4 | Review navigation, replace | UI | Part #9: "6 nghiêm trọng"; picker shows "Mục 9 · nguồn 1 (wwf-09.webm) · 14:59,2–15:06,8"; REPLACE; "Dựng lại các phần đã đổi" 56 s, "dựng 2 phần (i9, outro), dùng lại 21" |
| 5 | Xuất bản cuối | UI | Confirm (213 unreviewed) → accept → after ≈90 s with no progress text: "Chưa xuất. Kiểm tra cuối chưa đạt. Dòng thời gian: i1: HELD_FRAME … giữ một khung hình 2600 ms …; Mục nghiêm trọng: …". No `final.mp4` |
| 6 | QC checks on 20 min | host diagnostic | render ok; **duration ok** (1201120 vs plan 1201100 ms); **captions ok** (last ends 1200373 ms); audio ok (−1.5 dBTP); timeline **FAIL** (HELD_FRAME 0.5–2.6 s in i1, i2); pending ok; decisions accepted; claims **FAIL** |
| 7 | A/V at joins (P2-9) | file check | Per-part A/V ≤ 31.7 ms; summed part audio is 301 ms shorter than the final's audio, i.e. each join is realigned; final A/V **−5.4 ms**. rc7's −41.3 ms not reproduced at file level; a frame-accurate check at each join was not done |
| 8 | Peak RAM (DubFlow tree) | `tools\ramwatch.ps1` | draft 4 940 MB (engine 2 049, ffmpeg 2 321); 11-part re-render 5 280 MB |
| 9 | status.json transitions | UI text + files | RUNNING → COMPLETED each time; `engine-stdout.log` ends with the COMPLETED status; `engine-stderr.log` 0 B. I did not poll the file during the run |
| 10 | Review load | file check | 214 sentences: 1 READY, 20 NEEDS_REVIEW, 193 MATCH_NOT_FOUND; 16/23 parts `signals_only` because the 80-frame vision budget covered 54 scenes of a 20-min film (warned in the run). Not reviewed fully; final not produced |

## 8. Metrics (Workstream C)

Review wall time = harness time of scripted clicks (with 2–3 s waits per click), not human time. Quality is **agent-evaluated** from 2 key frames per sentence of the exported video; there was no independent human. Full data: `metrics.json`.

| Video / part | Sentences | READY at draft | Queue at draft (crit / review / opt) | Opened | Actions (records) | INVALIDATED | Missing | Review wall s | Re-renders (s) | Final QC | Quality (correct / acceptable / wrong) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A video 1 | 4 | 1 | 3 / 0 / 1 | 3 | REPLACE 1, MISSING 2 | 0 | 2 | ≈60 | 1 (4) | pass after settling | 0 / 0 / 4 (no product in footage) |
| B v1 | 6 | 1 | 0 / 5 / 1 | 6 | APPROVE 8, PIN 1, EXCLUDE 1, UNDO 1, LATER 1 | 3 | 0 | 97 + 2 + 24 | 2 (8.2, 6.0) | pass | 4 / 2 / 0 |
| B v2 | 5 | 0 | 1 / 4 / 0 | 5 | APPROVE 5, REPLACE 2, MISSING 2 (mark, unmark) | 2 | 1→0 | 54 + 6 | 2 (6.8, 6.9) | pass | 2 / 3 / 0 |
| B v3 | 5 | 0 | 0 / 5 / 0 | 5 | APPROVE 4, REPLACE 1, LATER 1 | 0 | 0 | 39 | 1 (7.2) | pass | 3 / 2 / 0 |
| C hook | 1 | 0 | 0 / 1 / 0 | 1 | APPROVE 1 | 0 | 0 | 8 | | | 1 / 0 / 0 |
| C intro | 1 | 0 | 0 / 1 / 0 | 1 | APPROVE 1 | 0 | 0 | 8 | | | 1 / 0 / 0 |
| C #5 (i1) | 2 | 0 | 0 / 2 / 0 | 2 | APPROVE 3, EXCLUDE 1 | 1 | 0 | 10 + 10 | | | 1 / 1 / 0 |
| C #4 (i2) | 2 | 0 | 0 / 2 / 0 | 2 | APPROVE 2 | 0 | 0 | 10 | | | 1 / 1 / 0 |
| C #3 (i3) | 2 | 1 | 0 / 1 / 1 | 2 | APPROVE 1 | 0 | 0 | 8 | | | 2 / 0 / 0 |
| C #2 (i4) | 2 | 0 | 0 / 2 / 0 | 2 | PIN 1, APPROVE 2 | 1 | 0 | 10 + 8 | | | 2 / 0 / 0 |
| C #1 (i5) | 2 | 0 | 0 / 2 / 0 | 2 | APPROVE 2, LATER 1 | 0 | 0 | 6 + 8 | | | 2 / 0 / 0 |
| C outro | 2 | 0 | 0 / 2 / 0 | 2 | APPROVE 1, MISSING 1 | 0 | 1 | 10 | | | 1 / 1 / 0 |
| **C total** | 14 | 1 | 0 / 13 / 1 | 14 | 17 | 2 | 1 | 97 | 1 (≈25, 2 of 8 parts) + export 6.3 | pass, exported rev 1 | 11 / 3 / 0 |
| F (20 min) | 214 | 1 | (193 MATCH_NOT_FOUND → critical) | 6 (part #9) | REPLACE 1 | 0 | 0 | — | 4 partial (344, 67, 56, 56) | **FAIL** (HELD_FRAME, claims) | not exported |

Headline (agent-evaluated, B + C, 30 sentences): AI draft READY 2/30; after review 20 correct, 10 acceptable, 0 wrong in the exported videos. Decisions: 30 APPROVE, 3 REPLACE, 2 PIN, 2 EXCLUDE, 3 REVIEW_LATER, 3 MARK_SOURCE_MISSING records (one mark later taken back), 1 UNDO; 7 approvals voided by re-plans of other sentences, which cost 2 extra review rounds on B v1/v2 and 1 on C. Re-renders after review: 6–8 s per product video, ≈25 s for 2 of 8 compilation parts. No time-saving claim is made: there was no manual-editing baseline.

Datasets: Espresso (B, P1 footage) — run. Máy thổi bụi (P2) and Hũ tăm (P3) — footage exists on this PC but **not run this round** (time); not counted. Top 5 (C) — run. Diverse program with situational audio — partly covered by C (#1 keeps source audio); no separate run. Why We Fight (F) — run.

## 9. Defects

| ID | Sev | Status | What | Repro |
|---|---|---|---|---|
| P1-N3 | **P1** | New | After the user settles every flagged sentence, the Bán hàng result row says **"Cảnh đã đạt kiểm tra tự động"** — human decisions presented as an automatic pass (directive: USER_APPROVED must never read as AI confidence). Cause: `product/view.ts` `renderResults` shows that text whenever `report.unreviewed` is 0 | B run: settle all sentences of a video, re-render → row text; `B-09-results-label-auto-pass.png` |
| P2-N5 | P2 | New | Approval does not hold the approved shots: a pin/exclude/replace on one sentence re-plans the video (or part) and voids approvals of other sentences whose shots move ("Cảnh của câu đã đổi sau khi bạn duyệt"). Safe (flagged, QC blocks), but 7 of 30 approvals were voided and needed extra rounds | B v1: approve u1,u4,u5,u6; Ghim+Bỏ cảnh on u3; render → u1,u5,u6 INVALIDATED. C i1: approve u1, exclude in u2, render → i1.u1 invalidated |
| P2-N6 | P2 | New | After a user pin of a shot with relevance 0, the sentence reads "Có cảnh khớp lời câu này. chỉ 56 % … có cảnh khớp lời" (`evidence: MATCH_AVAILABLE`, `matched_footage: 0`) and the P1-N1 note disappears: the user's illustration is described as matching evidence | A: pin the domino shot on the air-fryer opening, re-render, reopen panel (`A-08-reopen.png`) |
| P2-N7 | P2 | Pre-existing | Bán hàng accepts a junction as the output root and writes behind it; Tổng hợp refuses. Product only checks `is_dir()` on the root | Settings output = junction; Bán hàng "Tạo video" → files in the junction target |
| P2-N11 | P2 | New (long-form) | 20-min programme: the 80-frame vision budget leaves 16/23 parts `signals_only`, 193/214 sentences MATCH_NOT_FOUND (critical); and the planner holds frames 0.5–2.6 s (HELD_FRAME) — QC catches both, but the long-form draft cannot be finalised without reviewing ~200 sentences | F run, `logs\F-final-qc-host.json` |
| P3-N8 | P3 | New | System INVALIDATED records have `run_id: ""` (the contract field is empty) | any INVALIDATED record in `review-1.json` (B) |
| P3-N9 | P3 | New | Export state line after a successful final shows only the path, not the QC results; the long QC (≈90 s on 20 min) shows no progress text | C step 9, F step 5 |
| P3-N10 | P3 | New | "Lời bình đọc khoảng …" estimate over-predicts (19:51 estimate → 14:31 actual) | F draft |
| P3-N12 | P3 | New | Compilation panel message after an exclusion says "Bấm “Render lại video này”" while the button is "Dựng lại các phần đã đổi"; run warning lines read "Câu 1.u1", "Câu 4.u2" | C i1 exclude; C warnings box |
| P3-N13 | P3 | New | Bán hàng button reads "Tạo 2 video" while 3 scripts are selected (3 videos were made) | B form with 3 scripts |
| P2-9 | P2 | Not reproduced | rc7 long-form A/V −41.3 ms: rc8 F final A/V −5.4 ms, joins realigned at file level | §7 #7 |

Fixed or confirmed since rc7: P1-N1 (product opening READY on unrelated footage) fixed (A); P2-N3 (status.json held open kills the job) fixed (S3); P3-N4 (warning box "Không có cảnh báo" with invalidations) fixed (B #8, C #5).

## 10. Gates

| Gate | Verdict | Evidence |
|---|---|---|
| A Safety | **PASS** | A #1–3 (P1-N1 fixed, flagged, QC blocks); S1–S3 (CANCELLED/FAILED never COMPLETED, valid JSON, logs kept, locked status survives); no false READY seen in A, B, C (B+C READY 2/30, both agent-checked correct; A's READY is the CTA "Đặt mua ngay…", no product claim); stale/invalid decisions flagged as critical and block export. The P1-N3 label is a wording defect on the result row, scored under B |
| B Human Review | **FAIL (one P1)** | Everything the gate needs works through the UI (queue, next, position across reopen and restart, all 7 actions, picker with source/time, missing-footage both ways, re-render, reopen, invalidation warnings, contract records) — but P1-N3 shows human-settled videos as "Cảnh đã đạt kiểm tra tự động", which the brief requires never to happen. With P1-N3 fixed this would be PASS with P2-N5/N6 open |
| C Final Video Quality | **PASS for B and C; BLOCKED for A; FAIL for F** | B 3 videos and C exported finals: 0 wrong of 30 sentences (agent-evaluated), QC all checks ok (C host diagnostic), `final.json` rev 1 = sha256; sources unchanged. A: footage has no product (BLOCKED by input, by design of the repro). F: QC FAIL (HELD_FRAME, critical) — not exportable, correctly refused |
| D Production Usability | **PASS with limitations** | B and C completed through the real UI; review, re-render and render counts measured (harness time, not human time); no step needed JSON editing. Limitations: P2-N5 extra rounds, no free timestamp / import from the panel, long-form review load (P2-N11), no human-time baseline |
| E Regression and Security | **PASS with P2-N7 (pre-existing)** | Dubbing (default Tắt), Mastering Off, v1 byte-identical, unknown schema refused without writing, sources 11/11 + 2/2 + originals unchanged, key/e-mail 0/0/0. IPC tests not run here (CI) |

## 11. Evidence separation

| Kind | What |
|---|---|
| CI | Run 43141184228 success (installed pipeline, desktop launch, SDK, PCM smokes, as shipped in the artifact) |
| UI (real rc8 DubFlow.exe via CDP + Win32 dialogs) | A #1–7, S1, B all steps, C all steps, D, E #1–5, F #1–5 |
| Process action | S2 engine kill (`Stop-Process`), S3 share-deny locks (`holdlock.ps1`) |
| Host diagnostic | `final_qc` module run headless for C and F (`logs\*-final-qc-host.json`) |
| File checks / agent evaluation | ffprobe, hashes, records, joins, key-frame sheets, key/e-mail scan |
| Not run | IPC validation tests (CI only); Máy thổi bụi and Hũ tăm datasets; a separate diverse-audio compilation; a human reviewer or manual-editing baseline |
