# Gate C — Windows E2E of DubFlow 1.1.96-rc5 (Phase 4 readiness, Epic #637)

- Date: 2026-11-11. Owner's Windows 11 machine, CPU only, one job at a time.
- Folder: `D:\Projects\DubFlow\_sandbox\phase4-gate\rc5\`.
  - Sub-folders: `screenshots\`, `logs\`, `results\` (+ `results.json`), `semantic\`, `tools\`.
- rc5 = rc4 + #743 (desktop P1-1…P1-4) + #745 (semantic fixes).
- Not done, by design:
  - no release, publish or tag;
  - the stable install `D:\Tools\DubFlow` and its data were not touched;
  - `_sandbox\models-u` was not touched.
- AI key handling:
  - The app read its own saved key from Windows Credential Manager. The harness removed `DUBFLOW_AI_KEY`/`DUBFLOW_VIBI_KEY` from the app's environment.
  - Headless runs put the key into the child process environment only.
  - No key was printed or written anywhere. The key scan reports counts only.

## Summary

| Scenario | Verdict | Why |
|---|---|---|
| C1 Raw footage Top 5 (desktop UI) | **PASS** (workflow) | Every step ran through the UI with no workaround. P1-1 and P1-2 are fixed. The video is correct: 5 items in the user's order, titles, narration, subtitles, 4 source moments. Semantic review states are still weak (see Semantic) |
| C2 Partial re-render (desktop UI) | **PASS** | 3/8 parts re-rendered, only i2 TTS redone, the valid pin kept, the changed pin INVALIDATED. Edited narration is in the voice and subtitles, in sync. P1-3 and P1-4 are fixed |
| C3 Legacy v1 (smoke) | **PASS** | The v1 project opens. "Dùng lại thiết lập" → "Ghép video" gives a video byte-identical to rc4 (`9afa62bc…`). FFmpeg/FFprobe argv identical to rc4, 24/24. Unknown `schema_version` 9: clear message, files unchanged |
| C4 Errors/security | **PASS with exceptions** | No crash, no write outside allowed folders, 0 key hits. Cancel is now CANCELLED and resume works first time. P2-7 is only partly fixed. P2-10 unchanged. (A first reading of a "stale output folder" defect was my harness error, withdrawn: see C4 #3b) |
| Semantic E2E rerun (packaged runtime) | **Regressions** | Better: silent miss 0 (P3 fixed), D1 A/V −108 → −8 ms, B1 88.8 → 100 %, B5 stolen 8596 → 2943 ms. Worse: P2 61.3 → **38.4 %**, P3 50.9 → **42.1 %**, P1 66.4 → 62.3 %. **B1 intro takes 4.2 s of another sentence's key shot and is READY (unflagged)**. A wrong SOURCE_INSUFFICIENT appears (P2 u4, desktop #5 Câu 2) |
| Source media | **Unchanged** | 5/5 test media (the rc4 originals and the rc5 copies) and 6/6 Top 5 originals have the same SHA-256 before and after |
| Credential scan | **0 hits** | 2 727 files, 3.34 GiB (38 were locked by the running WebView2). Then the data dir was re-scanned with the app closed: 317 files, 0 unreadable, 0 hits |

**Gate C overall:**
- The desktop part has no open P1: P1-1…P1-4 are fixed and verified through the UI.
- The semantic part still has P1s: P1-5 (open), P1-7 and P1-8 (new).
- The gate stays **FAIL** until the semantic P1s are closed.

## 0. Build identity

| Item | Value |
|---|---|
| Artifact | `DubFlow-1.1.96-rc5-windows-x64`, run `42994644537` "Windows Release" = success |
| Commit | `35bb1153f99a1f4e27b2dabd1fd4609b5b89d14a` (= `release-evidence.json` `source_sha`) |
| Channel | `channel: candidate`, `production_qualified: false`, unsigned candidate |
| Zip SHA-256 | `472af1ca…0a9f4993`. This matches the evidence and `release.sha256` |
| `app\bin\DubFlow.exe` SHA-256 | `45ff21afaf22d9ab0ab01a5232d8db7d5f79691fd06a3be377614368dbaef8cc` |
| FFmpeg | `--enable-version3` only: no gpl, no nonfree. The bundle has 0 `.pyc` files |
| Machine | Windows 11 Home 10.0.26300, i7-12700H, 39.7 GB RAM, CPU only |
| Install method | Same as rc4. The zip was extracted (Python `zipfile`) to `rc5\app\versions\1.1.96-rc5\`, then `rc5\app\current.json` was written (manifest SHA `6cdf7ebf…`). Models were copied from the rc4 sandbox, without the TTS sentence cache. Data root = `rc5\app` |
| Download note | The first `gh run download` was cut off. The artifact was fetched again with `gh api …/artifacts/13845885925/zip` (737 MB) |

## 1. How the UI was driven, and proof

- Same tools as rc4, ported to `rc5\tools` (`launch.ps1`, `cdp.mjs`, `filedlg.ps1`):
  - real mouse clicks via `Input.dispatchMouseEvent` at element centres;
  - typing with Ctrl+A, Backspace, then `Input.insertText`;
  - native Open and folder dialogs filled with Win32 messages.
- `<select>` exception (as in rc2–rc4): the control is clicked, then its value is set and `input`/`change` are dispatched. In C4 #10 this was done for the "AI mặc định" select, whose element has no visible box.
- Targeting helpers that changed no app state:
  - In C1 the ↓ buttons were tagged with a temporary `data-e2e` attribute so the right one could be clicked.
- Never used:
  - Tauri IPC from the page;
  - edits to app files;
  - mocks.
- Proof (`logs\cdp-proof*.json`, `logs\cdp-proof-processes.json`):
  - CDP browser pid 15032; its parent is pid 13044 = `…\phase4-gate\rc5\app\versions\1.1.96-rc5\app\bin\DubFlow.exe`.
  - Command line: `--user-data-dir=…\rc5\app\control\webview2\312e312e39362d726335\EBWebView --remote-debugging-port=9445`.
  - Page `http://tauri.localhost/`.
  - The check was repeated after reopen: browser 1960, parent 19176, the same exe.
- The app was always closed with its own window X button (`#window-close`).
- Harness slips, both corrected:
  - Once the accept checkbox was ticked on #5 instead of #3: the review panel had not closed, because my "Đóng" click hit the window's own "Đóng" title. I unticked it and ticked #3. The undo history of `review-i1.json` shows both writes.
  - My RAM watcher for C1 failed: `psutil` is missing from the system Python. The C1 peak RAM is therefore **not recorded**. C2 used the venv Python.

## 2. Test media

- I reused the rc4 media, read-only. Byte copies are in `rc5\media` (C1 output goes beside the source).
  - The SHA-256 of the rc4 originals and of the copies match `rc4\report-rc4.md`.
  - Both were re-checked at the end: `results\media-after.check.txt`, 10/10 OK.
  - `results\originals-after.check.txt`: the 6/6 Top 5 originals are also OK.
- `x-unsupported.ogv` was copied to `rc5\unsupported`.

| File | Container | fps | Audio |
|---|---|---|---|
| m1-purdue-2398.mp4 | MP4 | 23.976 | AAC |
| m2-lop-hoc-2995.webm | WEBM | 29.90 | Vorbis |
| m3-thu-vien-2997.webm | WEBM | 29.97 | Vorbis |
| m4-mo-phong-3d-20fps.mp4 | MP4 | 20 | none |
| m5-idea-machine-25.mkv | MKV | 25 | Opus |

## 3. C1 — Raw footage Top 5 (all steps through the desktop UI)

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Open app | exe + CDP | v1.1.96-rc5 candidate | `00-launch.png` |
| 2–3 | Ghép video, raw mode | `#nav-compilation`, "Video gốc (nhiều nguồn)" | Form shown, same texts as rc4 | `C1-01-raw-mode.png` |
| 4a | Add 5 videos | "Thêm video…" + Win32 dialog | "Đã thêm 5 video". **Still no duration or format shown (P2-1)** | `C1-03-…` |
| 4b | Unsupported file | `.ogv` | "Chưa hỗ trợ định dạng: x-unsupported.ogv." Not added | `C1-03-sources-unsupported.png` |
| 4c | Remove before running | the first dialog call joined 5 names into one path (same as rc4); "Xóa mục" | The bogus path was accepted without a check (**P2-2 unchanged**), then removed through the UI | `C1-02-bad-joined-path.png` |
| 5–7 | Title, items, order, voice, target, mastering, AI | ↓ ×4 (m1 to the end), "Đánh số đếm ngược", the rc4 texts, Trúc Ly, 2 min, Cân bằng, AI vision + AI ranking ticked | Ranks 5,4,3,2,1. Duration note "khoảng 1 phút 19 giây… không tự cắt bớt". Notice "…được gửi tới Anthropic…" | `C1-04-setup-filled.png` |
| 8–10 | Draft render | "Dựng bản nháp" | Done in **≈291 s** (cold). "dựng 8 phần, dùng lại 0". Claude vision: 70 frames, 67 asked (43 broad, 3 targeted, 24 multi-frame) | `C1-06-result.png` |
| 11–12 | Review panel, all 8 parts | "Duyệt cảnh" ×8 | Texts saved in `results\C1-review-text\part0..7.txt`. Sentence states below | `C1-08-review-part*.png` |
| 13 | Replace a weak shot | #5 Câu 1: "Dùng cảnh này" on the alternative 0,0–2,0 s (`i1-s1c1f1`, a split-shot id) | **Saved** (P1-1 fixed). No candidate has a nested split id any more (0 ids like `c3f2f3`). The exact rc4 id no longer exists, so that repro now passes by construction | `C1-09-pin-click.png`, `review-i1.json` |
| 14 | Pin, exclude, approve | "Bỏ cảnh này" on Câu 1 8,0–10,1 s and Câu 2 12,0–14,0 s; "Duyệt cảnh này" on Câu 1 | `review-i1.json` rev 4: pin `[i1-s1c1f1]`, exclude `[i1-s1c2f2]` / `[i1-s1c3f1]`, Câu 1 approved. Excluded shots still show "Đang dùng" until the re-render (**P2-4 unchanged**) | `C1-10-review-i1-decisions.png` |
| 14b | Preview | "▶ Xem câu này" | The panel player plays the run video at t = 12.36 s | `C1-11-review-preview.png` |
| 15 | Accept explicitly | #3 panel: tick "Tôi chấp nhận…" | `review-i3.json` `accept_unreviewed: true`. Kết quả still said "Còn 14 câu…" (**P2-3 unchanged**) | `C1-14-i3-accept.png` |
| 15b | Final export gate | "Xuất bản cuối…" → "Để tôi duyệt trước" | The dialog lists 11 sentences per part. Then "Chưa xuất. Bản nháp vẫn ở trong thư mục dự án.", no `final.mp4`. **The first dialog took 12.9 s to appear** (P2-11 worse) | `C1-13-final-export-confirm.png` |
| 16 | Render with decisions | "Dựng lại các phần đã đổi" | "dựng 1 phần, dùng lại 7". Only `part-i1` and `compilation.mp4` changed. The pin is used (segment `0–1950` "chọn bởi người dùng") and both excludes are respected. The Câu 1 approval became INVALIDATED ("Cảnh của câu đã đổi sau khi bạn duyệt"; **P2-5 unchanged**) | `results\C1-2-after-decisions-rerender\` |
| 16b | Final export | "Tôi chấp nhận, xuất bản cuối" (dialog after 2.8 s, 12 sentences) | `final.mp4` = `compilation.mp4` = `5b11af47…` | `C1-16-final-exported.png` |
| 17 | Check the final video | `tools\checkvid.py` + start/mid frames | See the notes after this table | `results\C1-3-final-export\`, `C1-18-item-starts.jpg`, `C1-18b-item-title-frames.jpg` |
| 18–19 | Close and reopen | window X, relaunch (pid 19176, CDP proof repeated) | **The run is listed** ("Top 5 … 5 phần") with the default "Cạnh video gốc" setting (P1-2 fixed) | `C1-19-after-reopen-list.png` |
| 20 | Decisions kept | open run → Kết quả → Duyệt cảnh #5 / #3 | "Đang dùng (đã ghim)", INVALIDATED warning shown, the accept checkbox stays ticked, "Bản cuối: …final.mp4" | `C1-22-reopen-review-i1.png` |
| 21 | AI ranking | `program.json` | The suggestion has the same order as the user's #5→#1, with reasons. It is shown only under "AI gợi ý thứ hạng" with "Dùng thứ tự gợi ý…". The video follows the user's order. The button itself was not clicked this time | `C1-06-result.png` |

Final video (step 17):

- H.264 1920×1080 + AAC, 76.43 s, −18.5 LUFS, LRA 5.4, true peak −1.4 dBFS.
- Parts in order: hook, intro, i1#5, i2#4, i3#3, i4#2, i5#1, outro.
- Item start frames show "#5 Cỗ máy domino trong lớp học" … "#1 Nhà vô địch Purdue 2019". Burned subtitles are in place, and `captions.srt` has 28 cues.
- Source moments: i1, i2, i4 and i5 each end with a 3.5 s `audio_mode: source` segment. i3 has no audio (silent 3D source).
- Part video vs audio:
  - i4 is now 13.067 vs 13.070 s (was −103 ms: the picture-end clamp works);
  - **i2 is still −63 ms** (13.167 vs 13.230; P2-9 partly fixed);
  - the file overall is video 76.433 vs audio 76.445 s, 2291 frames.

Review states, desktop Top 5 (14 sentences, first render):

| State | Count |
|---|---|
| NEEDS_REVIEW | 13 |
| SOURCE_INSUFFICIENT | 1 (#5 Câu 2) |
| READY | 0 |

- "Có cảnh khớp" together with "Hình khớp 0 %":
  - The bare contradiction from rc4 is gone.
  - The only sentence at 0 % with a match (#1 Câu 1) now says "Có cảnh khớp lời câu này **nhưng chưa đặt được vào câu** (cảnh quá ngắn hoặc cần cho câu sau), nên câu đang dùng cảnh chung". This is consistent.
- #3 Câu 1 now finds "máy tính" (100 %).
- **#5 Câu 2 is now SOURCE_INSUFFICIENT**: "Mọi cảnh dùng được đều đã được xem và không cảnh nào khớp: nguồn có vẻ không quay nội dung này". Yet the three shots it uses are described as rows of dominoes on tables ("hàng domino…", "hàng quân domino xếp đứng"). That is a wrong verdict (P1-8).

## 4. C2 — Partial re-render from C1 (desktop UI)

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 0 | A still-valid pin elsewhere | #1 panel, "Ghim" on Câu 1 | `review-i5.json`: `i5.u1 pin [i5-s1c1]` | |
| 1 | Edit narration of #4 | after the reopen: "Sửa chương trình…" | **The voice list is loaded: 25 options, Trúc Ly selected** (P1-3 fixed). Narration replaced with the rc4 C2 text | `C2-01-edit-program.png` |
| 2 | Change footage of #5 | source 1 "Bắt đầu" = 4 | | |
| 3 | Save and re-render | "Lưu và dựng lại" | Done in **40.4 s**. "dựng 3 phần, dùng lại 5" | `C2-02-rerender-done.png` |
| 4 | What changed | `status.json` + hashes | `rendered [intro, i1, i2]`, `reused [hook, i3, i4, i5, outro]`. Hashes changed only for `part-intro`, `part-i1`, `part-i2`, `i2.wav` and `compilation.mp4`. TTS ran only for i2. Vision asked 2 frames (the new trim). 3 encodes (`h264_mf`) + concat | `results\C2-0-before\`, `results\C2-1-after\` |
| 5 | Decisions | `review-*.json` | i5 pin kept. i1.u1 pin/exclude INVALIDATED: "Cảnh đã chọn không còn trong nguồn (nguồn đã đổi); hãy chọn lại." (also a run warning). **`accept_unreviewed` reset to false on the re-rendered i1 and i2**; unchanged i5 keeps it | |
| 6 | Edit visible and in sync | `captions.srt`, `silencedetect` on `i2.wav` | New text cue at 21.700 s; speech onset 21.400 + 0.390 = 21.790 s (+90 ms; rc4 +77 ms). Mid pause 26.52–27.13 s vs next cue 27.070 s. In sync | |
| 7 | Final export after the edits | "Xuất bản cuối lại" | **The confirmation appears again** (2.9 s): "Còn 5 câu … Mở đầu (1 câu), #5 (2 câu), #4 (2 câu)…" (P1-4 fixed). Accepted → `final.mp4` = new `compilation.mp4` `9b588566…`, 77.33 s. Before re-export, Kết quả still showed "Bản cuối: …final.mp4" for the old file (**P2-6 unchanged**) | `C2-04-reexport-confirm.png`, `C2-05-reexported.png` |

## 5. C3 — Legacy v1 (smoke)

- I copied the rc4 v1 project to `rc5\c3v1\DubFlow Output\`. Its sources are the rc4 `final_vi.mp4` files, read-only.
- "Thư mục lưu kết quả" was set to `rc5\c3v1`.
- My first copy lacked the "DubFlow Output" layer, so it was not listed. That was my error, fixed by moving the folder.

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | v1 run listed | refresh | "Ghép thử hai phim ngắn · 2 phần" and "Bản tương lai · Chưa rõ trạng thái" | `C3-01-list.png` |
| 2 | Read-only setup | Thiết lập | Stored setup shown read-only, with "Dùng lại thiết lập này cho bản mới". No Mastering selector on the legacy path | `C3-02-v1-setup.png` |
| 3 | Render | "Dùng lại thiết lập…" → "Ghép video" | COMPLETED 6/6 in ≈42 s. **`compilation.mp4` = `9afa62bc…` = rc4** | `C3-06-v1-result.png` |
| 4 | Spec | `.dubflow-work\spec.json` | Equal to rc4's `C3-spec-v1.json` (`schema_version: 1`, no `mastering`) | `results\C3-spec-v1-rc5ui.json` |
| 5 | Argv vs rc4 | `tools\c3cmp.py` with the rc5 runtime, every argv logged | **FFmpeg/FFprobe 24/24 identical**. Video `9afa62bc…`. rc5 also started the TTS process (cold cache for these sentences) | `results\C3-argv-compare.txt` |
| 6 | Unknown version | open "Bản tương lai" | "Dự án này được tạo bằng phiên bản DubFlow mới hơn (định dạng 9); hãy cập nhật DubFlow để mở." "Dùng lại thiết lập" is hidden. Files unchanged (SHA-256 before = after) | `C3-07-unknown-version.png` |

## 6. C4 — Errors and security

| # | Case | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Missing source | UI: add `missing-later.webm`, delete the file, render | "Mục 1: nguồn 1: Không tìm thấy video nguồn: missing-later.webm". No run folder | `C4-02-missing-source.png` |
| 2 | Unsupported format | UI (`.ogv`) | Reported, not added (C1 4b) | |
| 3 | Output folder = junction | Cài đặt › Thư mục lưu (Win32 folder dialog) → `c4\junc-out` → render | Setting saved; render refused "Hãy chọn lại thư mục lưu kết quả" (wording vague: **P2-10 unchanged**). `outside\out` stays empty | `C4-09-junction-render.png` |
| 3b | Output folder changed again (**withdrawn, harness error**) | After 3, I set other folders in Cài đặt and clicked render | My clicks never reached the render button. Cài đặt is a full page, not a dialog: my "Đóng" click found nothing and the page stayed open. The hidden render button was "NOT FOUND", so the old state text was what I read. The desktop reads `localStorage dubflow.outputRoot.v1` live (`main.ts outputRoot()`, `raw_view.ts render()` → `host.outputRoot()`). No defect | `C4-out-ok-refused.png` (still on Cài đặt) |
| 4 | Output folder not writable | ACL deny write on `c4\ro-out`, set it, restart (not needed, see 3b), render | "Không tạo được thư mục tổng hợp". Folder empty. ACL removed afterwards | `C4-10-readonly-out.png` |
| 5 | AI without key | UI: Cài đặt › Tài khoản › "AI mặc định" = ChatGPT (no saved key) | AI checkboxes disabled. "Chưa có khóa AI trong Cài đặt › Tài khoản: DubFlow chọn cảnh bằng tín hiệu hình ảnh, phụ đề và ghi chú của bạn." A render then completed. Provider set back to Claude. Credential Manager untouched | `C4-11-ai-no-key.png` |
| 6 | AI failure (invalid key) | **packaged runtime**: the C1 spec with the fake key `invalid-test-key-…` in the child env only (`run_rc5.py --bad-key`) | COMPLETED in 46 s with 9 warnings, e.g. "AI không đề xuất được thứ hạng (Khóa Claude (Anthropic) không hợp lệ … (401) …); giữ thứ tự của bạn." The fake key appears in 0 output/log files. Every part still reports `mode: content` from file-name metadata only (P2-14) | `semantic\runs\C4-aifail.*` |
| 7 | Cancel a running job | UI "Hủy ghép" during "Đang ghép video · 0%" (AI vision on, 2 long sources) | `status.json` **CANCELLED**, "Đã hủy tổng hợp. Có thể dùng lại các đoạn đã xong." No python/ffmpeg left. The panel still says "Sẵn sàng xuất bản cuối." with no video (export button disabled): **P2-7 partly fixed** | `C4-12-cancelled.png` |
| 8 | Reopen + next render | "Dựng lại các phần đã đổi" on the cancelled run | **COMPLETED on the first try**, no `PermissionError`/WinError 5 (P2-8 fixed). Other renders afterwards completed normally | `C4-14-resume.png` |
| 9 | Nothing outside allowed folders | after all cases | `outside\out` empty. Every run folder is under the chosen output root or beside the source | |
| 10 | Credential scan | `tools\keyscan.py` (Gemini + Claude keys, UTF-8 and UTF-16LE) | 2 727 files / 3.34 GiB: **0 hits** (38 locked by the running WebView2). Data dir re-scanned with the app closed: 317 files, 0 unreadable, **0 hits** | `results\keyscan.txt` |

## 7. Semantic E2E on real footage (packaged rc5 runtime, headless)

- Same specs, footage and frozen truth as rc4 (`phase2-e2e\specs-u`, `results-main-f75bd2b\truth`).
- Fresh vision (Gemini `gemini-3.5-flash-lite`). Model root `rc5\semantic\models` (a copy).
- Run: `sh rc5\tools\seq_sem.sh`.
- Scoring: `runtime\python.exe -X utf8 -B -I tools\analyze_rc5.py <run> semantic\out\<dir> <truth> results\semantic`, using rc5's own `engine.dubflow.mastering.quality`.

| Set | Coverage rc4 → rc5 | Units ok | Contra ms | Stolen key ms (flagged / **unflagged**) | Silent miss | False alarm | Repeats | A/V offset | Wall rc5 (rc4) |
|---|---|---|---|---|---|---|---|---|---|
| P1 Espresso (after pin) | 66.4 → 62.3 % | 2/6 → 3/6 | 2352 → 0 | 0 → 1000 / 0 | 0 → 0 | 1 → 2 | 0 → 0 | −3 → −3 ms | 159 s (145) |
| P2 Máy thổi bụi | 61.3 → **38.4 %** | 0/6 → 0/6 | 1725 → 624 | 3564 → 2449 / 0 | 0 → 0 | 0 → 0 | 0 → 0 | −3 → −3 ms | 55 s (143) |
| P3 Hũ tăm | 50.9 → **42.1 %** | 1/6 → 0/6 | 1648 → 0 | 2648 → 0 / 0 | **1 → 0** | 1 → 0 | 0 → 0 | +10 → +10 ms | 206 s (168) |
| Top 5 old (B1) | 88.8 → **100 %** | 9/11 → 11/11 | 1169 → 0 | 4661 → 3618 / **0 → 4200** | 0 → 0 | 7 → 8 | 3 → 2 | −11 → −11 ms | 225 s (140); identical rerun 7.6 s |
| Top 5, 5 sources (B5) | 100 → 100 % | 13/13 | 0 → 0 | **8596 → 2943** / 0 | 0 → 0 | 11 → 12 | 3 → 2 | −11 → −11 ms | 132 s (121) |
| Why We Fight long (D1) | 43.0 → 43.0 % | 2/4 | 0 → 0 | 0 / 0 | 0 → 0 | 2 → 2 | 0 → 0 | **−108 → −8 ms** | 179 s (191) |

- Review flags: B1 26 → 22, B5 22 → 27, D1 11 → 7.
- Timeline problems: no SLIVER, GAP, OVERLAP, HELD_FRAME, BACKWARDS or OUTSIDE_SOURCE in any set.
- Where the D1 offset comes from: ffprobe stream durations of `compilation.mp4`. The may-y-tuong moment is now clamped to the picture end (#745).

What changed, read from the run logs (`semantic\out\*\.dubflow-work`):

**B1 `intro.u1` (new unflagged steal)**
- The intro "Cùng điểm lại năm cỗ máy …" plays `purdue-2019-s1 27356–31556`.
- That is 4.2 s inside purdue-2019.u2's key shot ("đội ngồi xem", 21000–34000).
- The segment has `needs_review: false`, confidence 672. The unit is **READY**.
- rc4 flagged the same kind of steal.

**P2 u4 "Hai anh em ngồi thử máy…" (wrong SOURCE_INSUFFICIENT)**
- The unit is now SOURCE_INSUFFICIENT.
- `v2c1` is described as "hai người đàn ông đang ngồi cầm máy thổi bụi…". Its best relevance is 57, just under the 60 cutoff; rc4 scored 60 and accepted it.
- Meanwhile the hook u1 lost its match. `v3c1` is now described **without Vietnamese marks** ("canh quay goc nhin tu tren xuong khi may thoi bui dang thoi la…"), and #745 counts unmarked single words at half weight.
- So the hook took `v2c1` (the shot u4 needs: stolen 2449 ms, flagged).

**P3**
- Every unit is now flagged, so there is no silent miss, but coverage fell.
- u6 now carries the new note "có cảnh khớp … nhưng không đặt được vào câu".

## 8. Performance (record only)

| Measure | rc5 | rc4 |
|---|---|---|
| Import/probe in UI | 5 files added instantly (no probe at import) | same |
| C1 first draft (cold, Claude vision) | **≈291 s**. Frames 70, asked 67 (43 broad, 3 targeted, 24 multi-frame). 8 part encodes + concat | 327 s; 80 / 75 |
| C1 peak RAM | **not recorded** (harness: `psutil` missing in system Python) | not recorded |
| C1 decision re-render | 1/8 parts (i1), about 10 s, vision 0 | 2/8 parts |
| C2 edit re-render | **40.4 s**, 3/8 parts, 3 encodes, vision 2 asked, TTS only i2. Peak tree **2.89 GB** (python 1.36, ffmpeg 0.90, WebView2 0.58) | 33.5 s; 2.85 GB |
| Final export | copy, about 10 s including the dialog. The confirmation dialog took **12.9 s** the first time, then 2.8–2.9 s | "a few seconds" |
| C3 v1 render | UI ≈42 s; headless 44.5 s (cold TTS for those sentences) | 42 s; 36.3 s |
| Cache | C1 work folder 88 MB; TTS sentence cache 7.5 MB | 80 MB; 7.3 MB |
| Headless semantic | see the table in §7. B1 225 s vs 140 s, P3 206 s vs 168 s (fresh vision), P2 55 s vs 143 s | |

## 9. Defects

| ID | Sev | Status in rc5 | Defect | Exact repro |
|---|---|---|---|---|
| P1-1 | P1 | **Fixed** | Nested split ids are no longer produced (0 in C1); a split-shot alternative (`i1-s1c1f1`) pins fine | C1 #5 Câu 1 → "Dùng cảnh này" |
| P1-2 | P1 | **Fixed** | A raw run beside its sources is listed after reopen | C1 steps 18–19 |
| P1-3 | P1 | **Fixed** | Voice list loaded in "Sửa chương trình…" after a restart | C2 step 1 |
| P1-4 | P1 | **Fixed** | Re-export after edits asks again (re-rendered parts reset `accept_unreviewed`) | C2 step 7 |
| P1-5 | P1 (semantic) | **Open** | Review states stay uninformative on the desktop Top 5: 13/14 NEEDS_REVIEW, 1 SOURCE_INSUFFICIENT, 0 READY. 0 of 26 pieces reach confidence ≥ 600 (max 577) | C1 inputs → Duyệt cảnh any part |
| P1-6 | P1 (semantic) | **Partly fixed** | P3 silent miss fixed (0) and D1 A/V fixed. But P1 66.4 → 62.3 %, **P2 61.3 → 38.4 %**, **P3 50.9 → 42.1 %**. One cause: #745's half weight for unmarked words meets Gemini descriptions written without marks (P2 v3c1) | `tools\seq_sem.sh` + `tools\analyze_rc5.py` |
| P1-7 | P1 (semantic) | **New** | B1 `intro.u1` shows 4.2 s of `purdue-2019.u2`'s key shot (27356–31556) and is **READY**, `needs_review: false`. That is 4200 ms of stolen key footage, unflagged | `s-B1` → `semantic\out\B1\.dubflow-work\mastering\plan-intro.json` |
| P1-8 | P1 (semantic) | **New** | SOURCE_INSUFFICIENT ("nguồn có vẻ không quay nội dung này") where the source shows it. Desktop #5 Câu 2: the chosen shots are described as domino rows on tables. P2 u4: `v2c1` "hai người đàn ông đang ngồi cầm máy thổi bụi" scores 57 < 60 | C1 → Duyệt cảnh #5 Câu 2; `s-P2` editorial u4 |
| P2-1 | P2 | Open | The raw source list shows no duration or format | Add any video |
| P2-2 | P2 | Open | The draft accepts a non-existent joined path without warning | Type `"a.mp4,b.webm"` as one name in the Open dialog |
| P2-3 | P2 | Open | Accepting in the panel does not refresh Kết quả ("Còn 14 câu…") | #3 → tick accept → Đóng |
| P2-4 | P2 | Open | An excluded shot stays "Đang dùng" with no marker until the re-render | #5 → Bỏ cảnh này |
| P2-5 | P2 | Open | An approval after a pin is always invalidated by the re-render that applies the pin | Pin → Duyệt cảnh này → Dựng lại |
| P2-6 | P2 | Open | After a re-render "Bản cuối: …final.mp4" still shows the old final as current | C2 before re-export |
| P2-7 | P2 | Partly fixed | Cancel now records CANCELLED with a Vietnamese message, but the panel says "Sẵn sàng xuất bản cuối." with no video | Hủy ghép during analysis |
| P2-8 | P2 | **Fixed** | Resume after cancel works on the first try | C4 #8 |
| P2-9 | P2 | Partly fixed | i4 clamp works (0 ms), but part i2 (`m3-thu-vien-2997.webm`) is still 63 ms shorter than its audio | C1 `part-i2.mp4`: v 13.167 s, a 13.230 s |
| P2-10 | P2 | Open | The junction output-folder refusal gives no reason | Output folder = junction |
| P2-11 | P2 | Worse | "Xuất bản cuối…" showed nothing for 12.9 s before its dialog (first open) | C1 step 15b |
| ~~P2-13~~ | – | **Withdrawn** | Harness error, not an app defect (C4 #3b) | – |
| P2-14 | P2 | Open (as rc4) | With every AI call failing (401), every part still reports `mode: content`, based on file-name metadata only | `run_rc5.py --bad-key` on the C1 spec |

## 10. Evidence separation

| Kind | What |
|---|---|
| Unit / integration (CI, not re-run here) | Run `42994644537` Windows Release = success, with installed-pipeline, desktop-runtime and source-sdk smokes passed (`release-evidence.json`). PR checks of #743/#745 were not inspected here |
| Packaged runtime (headless, rc5 runtime + FFmpeg) | Semantic E2E (10 runs + 6 scorings), C3 argv/output comparison, C4 #6 AI failure |
| Desktop UI (real DubFlow.exe via CDP + Win32 dialogs) | C1 1–21, C2 0–7, C3 1–4 and 6, C4 1–5 and 7–9 |
| Not run / not counted | C1 peak RAM (harness); clicking "Dùng thứ tự gợi ý…" (checked in `program.json` only); unreadable source file (the OS dialog refuses it, as in rc4); symlink source (not in this round's scope) |
