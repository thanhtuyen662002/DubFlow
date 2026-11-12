# Assisted Editorial Production — Windows E2E, DubFlow 1.1.96-rc9

- Date: 2026-11-12. Owner's Windows 11 machine, CPU only ("Chỉ dùng CPU" on; no NVIDIA GPU), one job at a time.
- Folder: `D:\Projects\DubFlow\_sandbox\assisted\rc9\` (this report, `results.json`, `metrics.json`, `screenshots\`, `logs\`, `tools\`).
- rc9 = rc8 + #776 (P1-N3, P2-N6, P2-N7, P3-N8, P3-N9). Directive "Assisted Editorial Production", Epic #637.
- Not done, by design: no release, publish or tag; the stable install and its data were not touched; `_sandbox\models-u` was not touched; no question was asked of the owner.
- AI key: the app read its own key from Windows Credential Manager (vision provider Claude). Nothing was printed or written; scans report counts only. The user profile path is written as `%USERPROFILE%`.

Labels used for every step (same as rc8):

- **UI (CDP):** the real rc9 `DubFlow.exe`, real mouse and keyboard events over WebView2 CDP (`Input.dispatchMouseEvent`, `Input.insertText`), Win32 messages for the native file and folder dialogs. Never Tauri IPC from the page.
- **File check:** hashes, ffprobe, reading run files (`review-N.json`, `editorial-N.json`, `final.json`).
- **Source read:** reading the rc9 source at commit `07e4fb5` through the GitHub API to explain a result (not a test step).

## Summary

| Scenario | Result | One line |
|---|---|---|
| A Safety (smoke) | **PASS** | Air-fryer opening over the domino clip is NEEDS_REVIEW, queued Nghiêm trọng, QC blocks the editor; pin of the domino shot → "Cảnh do bạn chọn. AI không xác nhận…", result row has no "tự động". New residual: the plan now records that sentence READY with 100 % coverage (P2-N14). |
| B Assisted Product (3 videos) | **PASS with P2s** | Every queue/decision action through the UI; position kept across panel reopen and app restart; QC "render lại" and critical gates seen; all 3 settled, re-rendered and opened in the editor; decisions survive restart. **P1-N3 fixed** (3/3 rows say "Bạn đã duyệt hoặc chọn cảnh cho N câu AI chưa chắc"). **P2-N6 fixed** for the evidence line. **P3-N8 fixed**. P2-N5 still open (5 of 17 approvals voided). |
| C Export line (copy of rc8 C) | **Export PASS, P3-N9 NOT fixed** | "Xuất bản cuối lại" → `final.json` revision 2, then 3, sha256 = `final.mp4`. The line shows only "Bản cuối (lần xuất N): <path>"; "Kiểm tra cuối: đạt" is written and immediately overwritten by a panel rebuild. |
| D Missing footage | **PASS (rc8 limitation unchanged)** | "Không có cảnh phù hợp" leaves the queue, plan/editorial byte-identical, same button takes it back; "Chọn cảnh khác…" gives REPLACE. |
| E Regression and security | **PASS** | **P2-N7 fixed**: Bán hàng refuses the junction ("Thư mục lưu kết quả là liên kết (junction/symlink); hãy chọn một thư mục thường"), junction target empty before/after; Tổng hợp still refuses. Dubbing default Tắt, done 90 s, QC passed. Key/e-mail scan 0/0/0 over 9 926 files. Sources unchanged. |
| Metrics extra (P2 Máy thổi bụi, P3 Hũ tăm) | **Run** | 1 video each, Cân bằng, reviewed through the UI until the editor opened. Draft 3/2/1 and 2/2/2 (correct/acceptable/wrong) → final 4/2/0 and 4/2/0, agent-evaluated against the phase2 truth labels. |

## 0. Build and install

| Item | Value |
|---|---|
| Artifact | `DubFlow-1.1.96-rc9-windows-x64`, Actions run `43155582509` "Windows Release", conclusion success (waited for it: in progress at 05:01Z, done ≈05:10Z) |
| Commit | `07e4fb52c56363eabf2b0d8798a0ba5dd603ba2a` (= `release-evidence.json` `source_sha` = PR #776 merge), channel candidate, `production_qualified: false`, `unsigned-candidate` |
| Download | 16 parallel HTTP range requests (`tools\dl.py`; the GitHub token is only sent to api.github.com, not to the blob redirect). Outer artifact zip `40c75a51…e556ef` |
| Zip SHA-256 | `abd4a0879fffc6a4876706280ceddb56f633b90a024415d50363cfc028038081` (= `release.sha256` and release evidence) |
| `app\bin\DubFlow.exe` SHA-256 | `45ac4c48d97f4eeddc614f34eab492e607072e67b0348b5f4888acd4c96d60c0` |
| Install | as rc8: zip extracted with Python `zipfile` to `rc9\app\versions\1.1.96-rc9\` (8 764 entries), `current.json` written (manifest `f8e54704…`), models copied from the rc8 sandbox without `tts-sentence-cache`. Data root = `rc9\app`. 0 `.pyc` |

## 1. UI driving and proof

- Tools: `tools\start.ps1` (launch + proof), `tools\launch.ps1` (rc9 exe, CDP port 9449, AI key env vars removed from the child), `tools\cdp.mjs`, `tools\filedlg.ps1`, `tools\q.ps1`, `tools\act.ps1` (one real click per action, logs queue + badge), `tools\rerender.ps1` (times "Render lại video này" until `video-N.mp4` is rewritten and `status.json` is not RUNNING), `tools\shots.ps1`, `tools\pnew.ps1`, `tools\psheet.py`, `tools\watchexport.mjs`.
- Proof (`logs\cdp-proof-processes.txt`, `logs\cdp-proof-*.json`): three launches; each time the CDP browser (`msedgewebview2.exe` with `remote-debugging-port=9449`, no `--type`) was a child of `…\rc9\app\versions\1.1.96-rc9\app\bin\DubFlow.exe` (pids 24008, 16312, 3708), `--user-data-dir=…\rc9\app\control\webview2\312e312e39362d726339\EBWebView`, page `http://tauri.localhost/`. App closes were the window's own close button (`button.window-btn.close`).
- Settings through the UI: "Chỉ dùng CPU" on; output folder `rc9\out`, then the junction (E), then `rc9\ccopy` (C), then back to `rc9\out`. AI provider Claude ("Đã lưu"; key not read by me).
- Harness slips (mine, recorded, not counted): (1) my first proof script read the CDP listener with `Get-NetTCPConnection`/netstat, which failed; replaced by the process command line (launch 1 was re-done, launch 2 proven with `-ProofOnly` right after it started); (2) a `clicktext "Tạo"` meant to read the button pressed it and started B (the form was complete, so B ran as intended); (3) the first P2 run (`run-eaddebc0ca09`) used the stale form of the refused junction test (espresso clip-02 + the P2 script) — excluded; `pnew.ps1` now clears the form via "Làm trống, bắt đầu mới" and checks 4 clips before creating; (4) the first `rerender.ps1` stopped timing when the panel closed (v1 round 1 not timed).

## 2. Scenario A — Safety smoke

Run `run-f509e3034616`: Bán hàng "Nồi chiên không dầu chống dính", 990.000đ, one clip (`rc8\media\mislead\noi chien khong dau chong dinh.mp4`, the 3D-domino clip), the rc8 A air-fryer script, Toàn màn hình, Trúc Ly, Cân bằng, vision Claude.

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Draft | UI | COMPLETED in 90 s. `editorial-1.json` u1 `NOT_NEEDED/NEEDS_REVIEW`, note "câu mở đầu nói về sản phẩm nhưng AI không thấy sản phẩm trong cảnh được chọn: cần kiểm tra"; u2/u3 SOURCE_INSUFFICIENT; u4 (CTA "Đặt mua ngay…") READY. Same as rc8: no false READY on the opening | `A-01-form.png` |
| 2 | Review panel | UI | "Hàng đợi duyệt: 3 nghiêm trọng · 1 tuỳ chọn (nhóm theo cờ của AI, chưa hiệu chỉnh)"; Câu 1 "Nghiêm trọng · Cần xem lại", "Vì sao nghiêm trọng: câu nêu thông tin cụ thể nhưng không có bằng chứng hình" | `A-02-review-open.png` |
| 3 | Position across app restart | UI | "Câu tiếp theo ›" to u2 → window close → relaunch (launch 3) → panel opens on **u2**; `review-1.json` `position: "u2"` | `A-03-position-after-restart.png` |
| 4 | Editor unsettled | UI | Confirm → "Vẫn mở" → **not opened**: "Kiểm tra cuối chưa đạt. Mục nghiêm trọng: chưa xử lý, không được coi là đã kiểm: câu 1, câu 2, câu 3" | `A-04-qc-blocked.png` |
| 5 | Settle | UI | Câu 1 **Ghim** on the domino shot in use (PIN); Câu 2, 3 "Không có cảnh phù hợp" | `A-05-settled.png` |
| 6 | Re-render, evidence line | UI | 4.7 s. Panel reopened: Câu 1 "Bạn đã chọn cảnh", **"Cảnh do bạn chọn. AI không xác nhận cảnh này khớp lời câu: hãy tự xem lại."**, "Đang dùng (đã ghim)"; never "Có cảnh khớp". **But** the header reads "Hình khớp 100 % thời lượng câu" (P2-N14) | `A-06-pinned-evidence.png` |
| 7 | Result row | UI | "Bạn đã duyệt hoặc chọn cảnh cho **2** câu AI chưa chắc; không còn câu chờ duyệt" — no "tự động" (P1-N3 fixed). The user settled 3 sentences; the count says 2 because the pinned opening is now READY in the plan (P2-N14) | `A-07-result-row.png` |
| 8 | Plan after the pin | file check | `editorial-1.json` u1: `review {evidence: MATCH_AVAILABLE, state: READY}`, `coverage {milli: 1000, status: full, matched_footage: 0, best_relevance: 0}`; `review_summary {READY: 2, SOURCE_INSUFFICIENT: 2}` | `logs\B-review-log.txt` |
| 9 | Editor | UI | Opens ("Chỉnh sửa · Nồi chiên…") | `A-08-editor-open.png` |
| 10 | Final video | agent-evaluated | 4/4 sentences show 3D dominoes (no air fryer in the footage). Exportable only because every sentence is a user decision: **Gate C for A = BLOCKED by footage** (as rc8) | `A-10-final-keyframes.jpg` |

## 3. Scenario B — Assisted Product (3 videos)

Run `run-11a60c9fd78c`: Espresso footage (`rc8\media\espresso\clip-01..04.mkv`, = phase2 P1 "n"), "Máy pha espresso tay cầm", 12.900.000đ, the 3 rc8 scripts (`logs\inputs-B-espresso.txt`), Toàn màn hình, Trúc Ly, **Cân bằng**, vision Claude. Draft **152 s** for 3 videos. Button read "Tạo 2 video" with 3 scripts selected (P3-N13 still open); 3 videos were made.

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Queue at draft | UI | v1 "6 cần xem", v2 "1 nghiêm trọng · 4 cần xem", v3 "5 cần xem", each "(nhóm theo cờ của AI, chưa hiệu chỉnh)"; 0 READY | `B-v1-01-queue.png`, `B-v2-01-queue.png`, `B-v3-01-queue.png` |
| 2 | Walk queue | UI "Câu tiếp theo ›" ×7 | v1: u1 → u2 → u3 → u4 → u5 → u6 → u1 (wraps); v2 opened on its critical u5 | `logs\B-review-log.txt` |
| 3 | Position, panel | UI | v1 on u3 → "Đóng" → reopen: current u3; `review-1.json` `position: "u3"` | log |
| 4 | Position, app restart | UI | Done on A (§2 #3): u2 kept across window close + relaunch. B panels after restart open with an empty queue (all settled) | `A-03-position-after-restart.png` |
| 5 | Actions | UI | Duyệt (APPROVE) ×17; Ghim (PIN) v1 u3; Bỏ cảnh (EXCLUDE) v1 u3; Chọn cảnh khác… → Dùng cảnh này (REPLACE) v2 u5 (Clip 2 0,0–3,4 s), v2 u2 (tamping, Clip 1 7,0–8,3 s), v3 u3 (unrelated shot, Clip 1 9,7–10,4 s); Để sau (REVIEW_LATER) v1 u6, v3 u5; Hoàn tác (UNDO) v1 u6; Không có cảnh phù hợp v2 u5 and back (§5) | `B-v1-02-decided.png`, `B-v2-02-decided.png`, `B-v3-02-decided.png` |
| 6 | Editor before re-render | UI | v1: confirm "Còn câu chưa duyệt cảnh… 1 câu" → "Vẫn mở" → "Còn lựa chọn chưa render. Lựa chọn đã render: chưa render lại sau khi bạn đổi cảnh: câu 3. Hãy render lại…" | `B-v1-03-editor-confirm.png`, `B-v1-04-render-pending.png` |
| 7 | Re-render each video | UI "Render lại video này" | v1 r1 (not timed, slip 4), r2 7.0 s; v2 6.8 s, 5.3 s; v3 6.8 s, 5.3 s | log |
| 8 | Invalidations after round 1 | UI | v1 "3 nghiêm trọng": u1, u5, u6 "Cần chọn lại… Cảnh của câu đã đổi sau khi bạn duyệt"; v2 u1 (its shot was taken by the u5 replacement); v3 u4. Warning box lists each. **P2-N5 still open** | `B-v1-05-invalidated.png` |
| 9 | Critical gate | UI | v3 with u4 invalidated: "Vẫn mở" → "Kiểm tra cuối chưa đạt. Mục nghiêm trọng: chưa xử lý, không được coi là đã kiểm: câu 4" | `B-v3-04-qc-blocked.png` |
| 10 | Round 2 | UI | Re-approved after looking at the new shots, re-rendered; all three queues "Hàng đợi duyệt trống: mọi câu đã được bạn xử lý." | log |
| 11 | **P1-N3** result rows | UI | v1 "Bạn đã duyệt hoặc chọn cảnh cho 6 câu AI chưa chắc; không còn câu chờ duyệt", v2 "…cho 5 câu…", v3 "…cho 5 câu…". **No "tự động"** on any row; same text after app restart | `B-v1-06-result-row-user-settled.png`, `B-09-results-rows-user-settled.png`, `logs\B-result-rows-final.txt` |
| 12 | **P2-N6** pinned unrelated shot | UI | v3 u3 ("Hai dòng espresso chảy đều xuống hai tách…") replaced by Clip 1 9,7–10,4 s (a tamped portafilter). After re-render, panel closed and reopened, and again after app restart: "Bạn đã chọn cảnh", **"Cảnh do bạn chọn. AI không xác nhận cảnh này khớp lời câu: hãy tự xem lại."**, "Đang dùng (đã ghim) Clip 1 · 9,7 s–10,4 s"; never "Có cảnh khớp". Same on v1 u3, v2 u2/u5, A u1, P2 u3–u5, P3 u1/u3/u4 | `B-v3-03-P2N6-pinned-unrelated.png`, `B-v3-09-P2N6-after-restart.png` |
| 13 | Editor | UI | All 3 open ("Chỉnh sửa · Máy pha espresso tay cầm · video 1/2/3") | `B-v1-07-editor-open.png`, `B-v2-07-…`, `B-v3-07-…` |
| 14 | Close app, reopen | UI | Launch 2: every decision shown as saved ("Bạn đã duyệt", "Bạn đã chọn cảnh"), queues empty, rows unchanged | `B-v1-08-reopen.png`, `B-v2-08-reopen.png`, `B-v3-08-reopen.png` |
| 15 | Decision records | file check | Every record of A, B, P2, P3 has all 12 contract fields; **every record's `run_id` is the run folder name, including all 8 INVALIDATED records** (P3-N8 fixed) | `logs\metrics-B-records.json`, `logs\metrics-A-P2-P3-records.json` |
| 16 | Final videos | file check + agent | 1080×1920 30 fps h264 + aac 48 kHz; 24.67 s / 18.0 s / 18.0 s; quality §8 | `B-v*-10-final-keyframes.jpg` |

P2-N5 count (approvals voided by re-plans of other sentences): B **5 of 17** (v1 3, v2 1, v3 1); P2 1 of 4; P3 3 of 6; total **9 of 27**. Two of them kept exactly the same shots in a new order (P2 u1, P3 u5 round 1), and in P3 an exclusion in u1 made the planner take u5's correct shot (u5 coverage 69 % → 29 %), voiding u5 and u6.

## 4. Scenario C — export line (P3-N9)

Copy of rc8 run `run-912c0a26c383` (Top 5, 5 sources) in `rc9\ccopy`, output folder set to `rc9\ccopy` in Settings.

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Open the run | UI | Listed, "Bản nháp đã dựng", parts and warnings as in rc8; "Xuất bản cuối lại" | `C-01-export-confirm.png` |
| 2 | Export | UI | ≈3–5 s, no confirm (accepted in rc8). `final.json` revision **2**, then **3** on a second export; `sha256 = 8663fb69…` = SHA-256 of `final.mp4` | file check |
| 3 | Export line | UI | **"Bản cuối (lần xuất 2): …\final.mp4"** only. A 100 ms sampler over 60 s (`tools\watchexport.mjs`) saw only "Bản cuối (lần xuất 2)" → "Bản cuối (lần xuất 3)"; **"Kiểm tra cuối: đạt" never appears** | `C-02-export-line-no-qc.png`, `logs\C-export-line-watch.json` |
| 4 | Why | source read | `raw_view.ts` `finalExport` sets `note.textContent = "Đã xuất: … Kiểm tra cuối: đạt…"`, then `await showRun(run.dir)` rebuilds the result panel (`host.result.replaceChildren`) and the new `.compilation-raw-export-state` shows `finalText(run)`. The unit test checks the host answer (`qc.passed`), not the visible line | — |

## 5. Scenario D — Missing footage

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | B v2 u5 (critical, "Chưa tìm thấy cảnh khớp") → "Không có cảnh phù hợp" | UI | "1 nghiêm trọng · 4 cần xem" → "4 cần xem"; badge "Bạn ghi: không có cảnh phù hợp"; `review-2.json` u5 `{"pin": [], "exclude": [], "source_missing": true}`; `mastering_plan-2.json` and `editorial-2.json` hashes identical before/after | `D-01-marked-missing.png` |
| 2 | "Bỏ ghi “không có cảnh”" | UI | Back as Nghiêm trọng (MARK_SOURCE_MISSING ×2 records) | `D-02-unmarked.png` |
| 3 | "Chọn cảnh khác…" | UI | Picker lists "Cảnh thay thế" (with score and reason) then "Mọi cảnh đã nhập" (every imported shot, "Clip N · a–b s", vision text) and "Cần cảnh mới: thêm clip ở trang tạo video rồi tạo lại…"; "Dùng cảnh này" → REPLACE record | `B-v2-02-decided.png` |
| 4 | A u2/u3 marked missing | UI | QC treats them as settled; editor opens | §2 |
| 5 | Free timestamp / import from the panel | — | Unchanged from rc8: not separate actions (limitation, not a pass) | — |

## 6. Scenario E — Regression and security

| # | Case | How | Result | Evidence |
|---|---|---|---|---|
| 1 | **P2-N7** Bán hàng, junction output | UI | New junction `rc9\junc-out` → `rc9\outside\out`, chosen in Settings through the folder dialog. Bán hàng "Tạo video" → **"Thư mục lưu kết quả là liên kết (junction/symlink); hãy chọn một thư mục thường"**, no run in the list; target listed before and after: only the empty `outside\out` | `E-01-settings-junction.png`, `E-02-junction-refused.png`, `logs\E-junction-target-before.txt`, `…-after.txt` |
| 2 | Tổng hợp, junction output | UI | Raw Danh sách with 2 sources → "Dựng bản nháp" → "Hãy chọn lại thư mục lưu kết quả"; target still empty | `E-03-compilation-junction-refused.png` |
| 3 | Dubbing smoke | UI (Hàng đợi → Thêm video từ máy → Lồng tiếng → Bắt đầu) | Default "Tối ưu hậu kỳ tự động" = **Tắt** (`mastering-level=off`); `dub-drama-30s` done in ≈90 s ("Hoàn tất"); `final_vi.mp4` h264 1024×576 + aac 48 kHz, 30.000 / 29.99998 s; `qc_report.json` status passed; `captions_vi.srt` | `E-04-dub-setup.png`, `E-05-dub-done.png` |
| 4 | Sources unchanged | file check | rc8 media 11/11 = rc8 baseline hashes; phase2 p2/p3 clips 8/8 not modified today; C copy `final.mp4` = `compilation.mp4` hash unchanged | `logs\media-hashes-after.json` |
| 5 | Key / e-mail scan (app closed) | `tools\keyscan2.py` | 9 926 files, 3.12 GiB, 0 unreadable: **claude 0, gemini 0, email 0**; self-test PASS. Scope: rc9 data dir incl. WebView2 profile, `out`, `ccopy`, junction target, logs, screenshots, tools | `logs\keyscan.txt` |
| 6 | Not run this round | — | Mastering Off product, compilation v1, unknown schema (rc8 PASS; no change in #776 touches them); IPC tests (CI only) | — |

## 7. Extra datasets — Máy thổi bụi (P2) and Hũ tăm (P3)

Bán hàng, 4 clips each (`phase2-e2e\footage\product\p2|p3\clip-01..04.mkv`), the truth scripts (`logs\inputs-P2-P3.txt`), Toàn màn hình, Trúc Ly, Cân bằng, vision Claude, 1 video each. Decisions made by me against `phase2-e2e\truth\p2-may-thoi.json` / `p3-hu-tam.json` (Clip N = vN).

| # | Step | P2 Máy thổi bụi (`run-d3e3d71f4703`) | P3 Hũ tăm (`run-3dbb88d451e7`) |
|---|---|---|---|
| 1 | Draft | 54 s; 6 sentences, 0 READY; "2 nghiêm trọng · 4 cần xem" | 59 s; 0 READY; "1 nghiêm trọng · 5 cần xem" |
| 2 | Draft shots vs truth | u1, u2, u6 correct; u3 right shot + a contra shot (girl with box); u4 acceptable; u5 **wrong** (hands on body instead of the girl with the box) | u2, u5 correct; u4 correct + filler; u6 acceptable; u1 **wrong** (base-removed shot = contra); u3 **wrong** (man pressing, not the base compartment) |
| 3 | Actions (UI) | APPROVE u1, u2, u6; EXCLUDE u3 contra shot + PIN u3 wheel shot; PIN u4; REPLACE u5 → Clip 3 3,6–5,4 s (girl with box) | APPROVE u2, u5, u6; EXCLUDE + PIN u4 (mirror); REPLACE u1 → Clip 1 2,9–5,5 s (pressing); REPLACE u3 → Clip 4 0,0–2,7 s (base compartment); later EXCLUDE the contra filler the planner put after the u1 replacement |
| 4 | Re-renders | 7.3 s, 5.3 s | 6.3 s, 5.8 s, 5.8 s, 5.7 s |
| 5 | Invalidations | u1 after round 1 (same 3 shots, new order) | u5 after round 1 (same shots, new order); u5, u6 after the u1 exclusion |
| 6 | Result row | "Bạn đã duyệt hoặc chọn cảnh cho 6 câu AI chưa chắc; không còn câu chờ duyệt" | same |
| 7 | Editor | opens | opens |
| 8 | Final (agent-evaluated) | 4 correct, 2 acceptable (u4, u5 end on gutter-blowing filler), 0 wrong | 4 correct, 2 acceptable (u5 shows the girl holding the jar but not taking a toothpick; u6), 0 wrong |

## 8. Metrics (Workstream C)

Review wall time = harness time of scripted real clicks (2–3 s waits per click), including re-renders and gate tests inside the window; not human time. Quality is **agent-evaluated** from 2 key frames per sentence of the rendered video (`screenshots\*-10-final-keyframes.jpg`) and, for P2/P3, the truth labels. Draft quality = the AI's shots at draft time, read in the panel before any decision. Full data: `metrics.json`.

| Video | Sentences | READY at draft | Queue at draft (crit / review / opt) | Draft quality (c / a / w) | Opened | Actions (records) | INVALIDATED | Missing | Review wall s | Re-renders (s) | Final QC | Final quality (c / a / w) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 4 | 1 (CTA) | 3 / 0 / 1 | 0 / 0 / 4 | 4 | PIN 1, MISSING 2 | 0 | 2 | ≈15 (+ restart test) | 1 (4.7) | blocked, then pass | 0 / 0 / 4 (no product in footage) |
| B v1 | 6 | 0 | 0 / 6 / 0 | 3 / 3 / 0 | 6 | APPROVE 8, PIN 1, EXCLUDE 1, LATER 1, UNDO 1 | 3 | 0 | 173 | 2 (–, 7.0) | pass | 3 / 3 / 0 |
| B v2 | 5 | 0 | 1 / 4 / 0 | 2 / 3 / 0 | 5 | APPROVE 4, REPLACE 2, MISSING 2 (mark, unmark) | 1 | 1→0 | 101 | 2 (6.8, 5.3) | pass | 2 / 3 / 0 |
| B v3 | 5 | 0 | 0 / 5 / 0 | 3 / 2 / 0 | 5 | APPROVE 5, REPLACE 1, LATER 1 | 1 | 0 | 94 | 2 (6.8, 5.3) | blocked once, then pass | 2 / 3 / 0 |
| P2 | 6 | 0 | 2 / 4 / 0 | 3 / 2 / 1 | 6 | APPROVE 4, PIN 2, EXCLUDE 1, REPLACE 1 | 1 | 0 | 75 | 2 (7.3, 5.3) | pass | 4 / 2 / 0 |
| P3 | 6 | 0 | 1 / 5 / 0 | 2 / 2 / 2 | 6 | APPROVE 6, PIN 1, EXCLUDE 2, REPLACE 2 | 3 | 0 | 110 | 4 (6.3, 5.8, 5.8, 5.7) | pass | 4 / 2 / 0 |
| **B + P2 + P3** | **28** | **0** | 4 / 24 / 0 | **13 / 12 / 3** | 28 | 27 APPROVE, 4 PIN, 4 EXCLUDE, 6 REPLACE, 2 LATER, 1 UNDO, 2 MISSING | **9** | 0 | 553 | 12 (5.3–7.3) | 5/5 pass | **15 / 13 / 0** |

Headline (agent-evaluated, 28 sentences of 5 product videos): the AI draft had 0 READY, 13 correct / 12 acceptable / 3 wrong; after review through the UI the rendered videos had 15 correct / 13 acceptable / **0 wrong**. The user replaced 6 shots, pinned 4, excluded 4 and confirmed 27 approvals, of which **9 were voided** by re-plans of other sentences (P2-N5), costing one extra round on 5 of 5 videos. Re-render after review: 5.3–7.3 s per video; draft 152 s for 3 Espresso videos, 54–59 s for 1 P2/P3 video. No time-saving claim: there was no manual-editing baseline.

Datasets: Espresso (B) — run. Máy thổi bụi (P2) and Hũ tăm (P3) — **run this round** (1 video each). Top 5 compilation — export only (copy of rc8 C). Long-form — not re-run (no #776 change touches it).

## 9. Defects

| ID | Sev | Status | What | Repro |
|---|---|---|---|---|
| P1-N3 | P1 | **Fixed (verified)** | Result rows of user-settled videos now read "Bạn đã duyệt hoặc chọn cảnh cho N câu AI chưa chắc; không còn câu chờ duyệt"; never "tự động" on 6 settled videos (B ×3, A, P2, P3), also after restart | §3 #11 |
| P2-N6 | P2 | **Fixed (evidence line)** | A pinned/replaced shot reads "Cảnh do bạn chọn. AI không xác nhận cảnh này khớp lời câu: hãy tự xem lại."; never "Có cảnh khớp", after reopen and restart | §3 #12, §2 #6 |
| P2-N14 | **P2** | **New (residual of P2-N6)** | After a user pin of a shot with relevance 0, the re-plan writes the sentence as `MATCH_AVAILABLE / READY`, `coverage {milli: 1000, status: full, matched_footage: 0, best_relevance: 0}` in `editorial-1.json`. Visible effects: the sentence header says "Hình khớp 100 % thời lượng câu" next to "Cảnh do bạn chọn. AI không xác nhận…"; the result row counts the user-settled sentences as 2 instead of 3 (the pinned one is now the planner's READY, so `settled_by_user` skips it); `review_summary` counts it READY. The UI badge still says "Bạn đã chọn cảnh" and QC treats it as a user decision, so no export is unsafe — but at file level the user's pin has become AI READY (the directive: USER_APPROVED/PINNED must not become AI confidence). Also seen partially on B v3 u3 (pinned unrelated shot, header "Hình khớp 100 %" because the planner adds a matching shot after it) | A: draft, Ghim the domino shot on Câu 1, render, reopen panel (`A-06-pinned-evidence.png`, `A-07-result-row.png`) |
| P2-N5 | P2 | Open (known) | Approvals voided by re-plans of other sentences: **9 of 27** (B 5/17, P2 1/4, P3 3/6). Includes voids where the sentence kept the same shots in a new order, and an exclusion in one sentence that took another sentence's correct shot (P3 u5 69 % → 29 %) | B v1: approve u1,u2,u4,u5,u6, Ghim+Bỏ cảnh u3, render → u1,u5,u6 INVALIDATED. P2: render → u1 invalidated with identical shots reordered |
| P2-N7 | P2 | **Fixed (verified)** | Bán hàng refuses a junction save folder before writing; target empty before/after; Tổng hợp still refuses | §6 #1–2 |
| P3-N8 | P3 | **Fixed (verified)** | All 8 INVALIDATED records carry `run_id` = run folder name | §3 #15 |
| P3-N9 | P3 | **Not fixed in the UI** | After a successful "Xuất bản cuối", the line shows only "Bản cuối (lần xuất N): <path>"; the "Kiểm tra cuối: đạt (chưa đo: …)" text is written and immediately replaced by `showRun()` | §4 |
| P3-N13 | P3 | Open | Bán hàng button reads "Tạo 2 video" with 3 scripts selected (3 videos made) | B form |
| P3-N10, P3-N12, P2-N11 | — | Not re-tested | No #776 change touches them | rc8 §9 |

Observations (not filed): (1) the alternatives' reason text reads "khớp lời “hai” (AI claude/claude-opus-5-5, tên file)", listing the file name as a signal next to the vision model; with these generic file names (`clip-0N.mkv`) it cannot have helped, but the invariant "never use the file name as visual evidence" may deserve a look at what "tên file" contributes there. (2) After the refused junction run, "Thêm mới" with no run selected kept the previous form (clip and script) silently; with a run selected it asks "Giữ nội dung đang nhập / Làm trống, bắt đầu mới".

## 10. Gates

| Gate | Verdict | Evidence |
|---|---|---|
| A Safety | **PASS** | A: opening NEEDS_REVIEW with reason, queued Nghiêm trọng, QC blocks the editor (§2 #1–4); 0 READY at draft across B, P2, P3 (28 sentences); invalidated decisions flagged critical and block the editor (B v3 §3 #9); decisions survive restart; `final.json` revision/sha correct. P2-N14 is a file-level conflation of a user pin with READY after the user decided (not an AI-only false READY at draft); scored under B |
| B Human Review | **PASS (P2-N5, P2-N14 open)** | Queue, next, position across panel reopen and app restart, all 7 actions, picker with source/time, missing-footage both ways, re-render, reopen, invalidation warnings, contract records with `run_id` everywhere. P1-N3 fixed (the rc8 FAIL reason). Open: P2-N5 (extra rounds), P2-N14 (header and count after a pin) |
| C Final Video Quality | **PASS for B, P2, P3 and the C export; BLOCKED for A** | 5 product videos reviewed and rendered: 0 wrong of 28 sentences (agent-evaluated), QC passed (editor opens), sources unchanged; C export rev 2/3, sha = `final.mp4`. A: no product in footage (BLOCKED by input, by design). Export to `final.mp4` was only exercised for Tổng hợp (Bán hàng has no separate final export) |
| D Production Usability | **PASS with limitations** | Everything through the real UI; review/re-render times and counts measured (harness time); no JSON editing. Limitations: P2-N5 rounds, no free timestamp / import from the panel, P3-N9 export line, no human-time baseline |
| E Regression and Security | **PASS** | P2-N7 fixed, Tổng hợp still refuses junction, dubbing default Tắt and QC passed, sources unchanged, key/e-mail 0/0/0. Not re-run: Mastering Off product, compilation v1, unknown schema (PASS in rc8, untouched by #776); IPC tests (CI) |

## 11. Evidence separation

| Kind | What |
|---|---|
| CI | Run 43155582509 success (installed pipeline, desktop launch, SDK, PCM smokes, as shipped in the artifact) |
| UI (real rc9 DubFlow.exe via CDP + Win32 dialogs) | A #1–7, #9; B all steps; C #1–3; D #1–4; E #1–3; P2, P3 all steps |
| File checks / agent evaluation | hashes, ffprobe, `review-N.json`, `editorial-N.json`, `final.json`, key-frame sheets, key/e-mail scan |
| Source read | `raw_view.ts` at `07e4fb5` to explain P3-N9 |
| Not run | Long-form F, Mastering Off product, compilation v1, unknown schema, engine kill/cancel/lock (rc8 PASS, not touched by #776); IPC tests (CI only); a human reviewer or manual-editing baseline |
