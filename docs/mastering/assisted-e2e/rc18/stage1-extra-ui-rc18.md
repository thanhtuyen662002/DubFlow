# rc18 extra rows A-D (PC, CDP on rc18 DubFlow.exe, launch3 proof OK), 2026-11-13 ~19:10Z

## A Earlier W1f/W1g cancels
- Cancel IPC sent: NO. cdp.mjs click only clicks visible elements; its NOT FOUND reply was swallowed by the wrapper. W1h trace (logs\W1h-log.txt): #cancel-processing hidden=true/disabled=true the whole run (another job was selected). The "cancel clicked" log lines hit nothing.
- Engine received cancel: NO. app\control\jobs.sqlite3: both jobs (...304196e03c3b, ...fc9fbf0ff7fa) succeeded, no error; job json COMPLETED, completed_with_warnings; supervisor logs empty.
- Limit: no per-click IPC log in the app; evidence consistent with no cancel sent.
- P1 candidate: NO (no cancel received and ignored).

## B W1f3 dubbing cancel during the post-production measurement (checkpoint b2-audio, before the mastering stage; bursts input, Cân bằng; logs\W1f3-log.txt, stage1\tools\dubcancel.ps1)
Cancel during the AAC trial encode or after the mastering stage completed was not exercised (NOT VERIFIED).
- Cancel button lives in the Thiết lập tab footer of the selected job; on Kết quả tab laid out at width 0. With running job selected + Thiết lập open: visible (width 311).
- Click at 92.4 s, progress "Đang đo âm lượng và hình ảnh để hậu kỳ": clicked #cancel-processing (real mouse event).
- Engine: UI "Đã hủy". Job json state FAILED, reason cancelled, message "Đã hủy theo yêu cầu", checkpoint b2-audio (800/1000). No qc_report.json (also none 20 s later). Never COMPLETED. Checkpoint stages: audio, b2-audio, b2-generation, models, probe, separation, subtitles, transcript, translation (no mastering, no render). PASS.
- Leftovers: .dubflow-work\mastering empty; 0 .scratch-*, aac_trial*, trim* anywhere in the run. PASS.
- UI after cancel: "Thử lại" and "Xử lý lại với tùy chọn khác" (hint "Tạo bản mới để đổi giọng hoặc tùy chọn. Kết quả cũ vẫn được giữ lại.").
- "Thử lại" = NEW run run-be0996e4d27e, 104 s (full run); cancelled run folder untouched. New run: aac_trial {first +0.26, tries 3, trim 2.51, TP -1.68}, conformance {B2, corrected, tries 3, trim_db 2.51, TP -1.68}, final_qc passed; independent I -19.73 / TP -1.68; decode 0 errors. Card: "Trước -15,3 LUFS, đỉnh -0,7 dBTP → sau -19,7 LUFS, đỉnh -1,7 dBTP sau AAC (trần -1,5 dBTP: đạt). Đã giảm thêm 2,5 dB sau AAC để đỉnh ≤ -1,5 dBTP. …" (screenshots\W1f3-resume-card.png).
- Verdict: cancel PASS; resume of the same run NOT AVAILABLE in UI.

## C W1e unchanged-input re-render: NOT AVAILABLE in UI (completed job: only "Xử lý lại với tùy chọn khác" = new run; cancelled: "Thử lại" = new run; no render-this-run-again action; editor "Xuất video" is a different path).

## D W1d' B1 fallback: BLOCKED, no user-reachable way. Lồng tiếng setup has no voice-provider choice; Settings → Giọng đọc shows Vibi.pro "Chưa có khóa", "Chọn giọng…" needs a key (none entered); tab states cloud key/credit failure falls back to the built-in voice, not B1. B1 covered by unit tests only.

## Findings
1. P2 (UX): "Hủy xử lý" only in the Thiết lập tab of the running job; hidden or width 0 otherwise.
2. P3: after cancel, "Thử lại" starts a new run from scratch (~104 s) instead of resuming checkpoints; cancelled run folder left with "Đã hủy". Not a safety issue.
3. Clarification: earlier W1f attempts never sent a cancel (harness misses, not app evidence).
Scans: 2 263 files, AI-key patterns 0, email 0; profile path 0. App closed; sandbox kept.
