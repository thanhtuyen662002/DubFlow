# Gate C — Windows E2E of DubFlow 1.1.96-rc4 (Phase 4 readiness, Epic #637)

- Date: 2026-11-11. Owner's Windows 11 machine, CPU only, one job at a time.
- Folder: `D:\Projects\DubFlow\_sandbox\phase4-gate\rc4\`.
  - Sub-folders: `screenshots\`, `logs\`, `results\` (+ `results.json`), `semantic\`, `tools\`.
- Not done, by design:
  - no release, publish or tag;
  - the stable install `D:\Tools\DubFlow` and its data were not touched;
  - `_sandbox\models-u` was not touched.
- AI key handling:
  - The app read its own saved key from Windows Credential Manager.
  - The harness removed `DUBFLOW_AI_KEY`/`DUBFLOW_VIBI_KEY` from the app's environment.
  - Headless semantic runs put the Gemini key into the child process environment only.
  - No key was printed or written anywhere. The key scan reports counts only.

## Summary

| Scenario | Verdict | Why |
|---|---|---|
| C1 Raw footage Top 5 (desktop UI) | **FAIL** | Every step ran and the video is correct, but two steps needed workarounds. P1-1: an offered alternative cannot be pinned. P1-2: the run is not listed after a reopen with the default output folder |
| C2 Partial re-render (desktop UI) | **FAIL** | The re-render itself is correct (3/8 parts re-rendered, pins kept, invalidation, sync). But P1-3 needed a workaround, and P1-4 lets the final-export gate be bypassed after edits |
| C3 Legacy v1 (desktop UI + packaged runtime) | **PASS** | FFmpeg/FFprobe argv identical to rc3 (24/24). Video byte-identical (rc3 headless = rc4 headless = rc4 UI). Unknown `schema_version` 9 shows a clear message; files are unchanged |
| C4 Errors/security (desktop UI + packaged runtime) | **PASS with exceptions** | No crash, no escape, no key leak. The unreadable-file case cannot be reached through the Windows file dialog. Several P2 message/state issues |
| Semantic E2E rerun (packaged runtime) | **Regressions** | P3 (Hũ tăm) has 1 silent miss (was 0). P1/B1 coverage is lower. P2 (Máy thổi bụi) no longer falls back. B5 (Top 5) reaches 100 %, but stolen key footage is ×3 |
| Source media | **Unchanged** | 6/6 test copies, 6/6 Top 5 originals, 2/2 dubbing originals (SHA-256) |
| Credential scan | **0 hits** | 11 810 files, 6.13 GiB: rc4 data dir with the WebView2 profile, outputs, logs, results, semantic outputs, plus the session scratchpad |

**Gate C overall: FAIL.** There are four open desktop P1s (P1-1…P1-4) and two semantic P1s (P1-5, P1-6).

## 0. Build identity

| Item | Value |
|---|---|
| Artifact | `DubFlow-1.1.96-rc4-windows-x64`, run `42974952007` "Windows Release" = success. Publish job skipped |
| Commit | `7c4fb1fcbca42134294a18279130a03aeb889c0b` (= `release-evidence.json` `source_sha`) |
| Channel | `channel: candidate`, `production_qualified: false`, unsigned candidate |
| Zip SHA-256 | `30e39375…6fa518`, matches the evidence and the `.sha256` file |
| `app\bin\DubFlow.exe` SHA-256 | `9bd55b1fff70b9e71440cd010ae56a09ed5b33eb2768d62371c10c3b7dea846a` |
| FFmpeg | `--enable-version3`, no `--enable-gpl`, no `--enable-nonfree` (LGPL). 0 `.pyc` files in the bundle |
| Machine | Windows 11 Home 10.0.26300, i7-12700H, 39.7 GB RAM, CPU only |
| Install method | As rc3. Zip extracted (Python `zipfile`; `tar` mangles Unicode names) to `rc4\app\versions\1.1.96-rc4\`. `rc4\app\current.json` written. Models copied from the rc3 sandbox (TTS sentence cache excluded). Data root = `rc4\app` |

## 1. How the UI was driven, and proof

- I started `rc4\app\versions\1.1.96-rc4\app\bin\DubFlow.exe` with `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9444` (`tools\launch.ps1`).
- `tools\cdp.mjs` drives the page:
  - real mouse clicks via `Input.dispatchMouseEvent` at DOM element centres;
  - typing via Ctrl+A, Backspace, then `Input.insertText`;
  - screenshots.
- Native Open/folder dialogs were filled with Win32 messages (`tools\filedlg.ps1`).
- `<select>` exception: the select is clicked, then its value is set and `input`/`change` are dispatched (as in rc2/rc3).
- Never used:
  - Tauri IPC from the page;
  - edits to app files;
  - mocks.
- `eval` was used only to read the DOM.
- Proof that the target is the rc4 WebView2 (`logs\cdp-proof.json`, `logs\cdp-proof-processes.json`):
  - CDP `SystemInfo.getProcessInfo` gives browser pid 13536.
  - Its parent is pid 21844 = `…\phase4-gate\rc4\app\versions\1.1.96-rc4\app\bin\DubFlow.exe`.
  - Page: `http://tauri.localhost/`.
  - Browser command line: `--user-data-dir=…\rc4\app\control\webview2\312e312e39362d726334\EBWebView` and `--remote-debugging-port=9444`.
  - The same check was repeated after each reopen (pids 5684 and 5296; `logs\cdp-proof-reopen*.json`).
- The app was closed with its own window X button (`#window-close`) and restarted from the same exe.

## 2. Test media (C1)

`media\`: copies made with the packaged FFmpeg. Originals are in `_sandbox\phase2-e2e\footage\top5*`, Wikimedia Commons, CC licences recorded in `specs-u\B5.json`.

| File | Container | fps | Duration | Audio | SHA-256 (before = after) |
|---|---|---|---|---|---|
| m1-purdue-2398.mp4 | MP4 (H.264 transcode of the AV1 webm) | 23.976 | 64.6 s | AAC, ambient | `ea469d591737…` |
| m2-lop-hoc-2995.webm | WEBM (copy) | 29.90 | 21.5 s | Vorbis, classroom | `e40a33f51e19…` |
| m3-thu-vien-2997.webm | WEBM (copy) | 29.97 | 14.6 s | Vorbis, hall | `83af8496ab5e…` |
| m4-mo-phong-3d-20fps.mp4 | MP4 (H.264) | 20 | 53.2 s | none | `0de0cb27163b…` |
| m5-idea-machine-25.mkv | MKV (remux of VP9/Opus) | 25 | 40.1 s | Opus | `b09398e6affc…` |
| x-unsupported.ogv (negative test) | OGG/Theora | 29.90 | 41.5 s | Vorbis | `68b368a2e32d…` |

- No source has subtitles. Full facts: `results\media-before.json` and `results\media-after.json`.
- Originals re-checked at the end: `results\originals-after.check.txt`, 6/6 OK.

## 3. C1 — Raw footage Top 5 (all steps through the desktop UI via CDP)

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Open app | exe + CDP | rc4 v1.1.96-rc4 candidate | `00-launch.png` |
| 2 | Ghép video (Tổng hợp) | click `#nav-compilation` | OK | |
| 3 | Raw mode | click "Video gốc (nhiều nguồn)" | Form shown. Text: MP4, MOV, MKV, WEBM, M4V, AVI; "DubFlow chỉ đọc video nguồn" | `C1-01-raw-mode.png` |
| 4a | Add 5 videos | "Thêm video…" + Win32 dialog | "Đã thêm 5 video", one item per file. Provenance = file name, full path as tooltip, rights selector (default "Chưa rõ"; I set "Có giấy phép"). **No duration or format is shown** (P2-1) | `C1-03-…png` |
| 4b | Unsupported file | dialog with `x-unsupported.ogv` | "Chưa hỗ trợ định dạng: x-unsupported.ogv." Not added | `C1-03-sources-unsupported.png` |
| 4c | Remove a source before running | "✕" on a source, then "Xóa mục" | Source and empty item removed. My first dialog call joined five names into one non-existent path; the draft accepted that path without a check (P2-2), and I removed it via the UI | `C1-02-bad-joined-path.png` |
| 5 | Topic, kind | type title; Kiểu = Top N (default) | "Top 5 cỗ máy phản ứng dây chuyền ấn tượng" | |
| 5b | Items, ranks, order | ↓ buttons (m1 moved to the end), "Đánh số đếm ngược", titles and narration typed | #5 Lớp học (m2), #4 Thư viện (m3), #3 3D (m4), #2 Idea Machine (m5), #1 Purdue (m1). Hook/intro/outro typed | `C1-04-setup-filled.png` |
| 6 | Language, voice, target | Language fixed to Tiếng Việt (only option); Trúc Ly; target 2 min | Duration note: "khoảng 1 phút 19 giây: ngắn hơn mục tiêu 2 phút… DubFlow không tự cắt bớt" | |
| 7 | Mastering | select Cân bằng | The four descriptions are quoted after this table | |
| 7b | AI options | tick "Dùng AI để xem khung hình", "AI gợi ý thứ hạng" | Note: frames, titles, notes and narration go to Anthropic with the saved key | |
| 8–10 | Program, narration, editorial plan, draft render | "Dựng bản nháp" | Draft in 327 s (cold). "dựng 8 phần, dùng lại 0". Progress showed "AI xem thêm khung hình của cảnh dài (n/8)" | `C1-05`, `C1-06-result.png` |
| 11–12 | Review panel, five items | "Duyệt cảnh" per part | See the notes after this table | `C1-08`, `C1-10`, `C1-12`, `results\C1-review-*.txt` |
| 13 | Replace a weak shot | "Bỏ cảnh này", then "Dùng cảnh này" on an alternative | The first alternative (`i1-s1c3f2f3`) returned **"Cảnh không hợp lệ"** and was not saved (P1-1). I used another alternative (`i1-s1c1f1`) for Câu 1 and excluded `i1-s1c2f2`. Câu 2 exclusion `i1-s1c3f3` saved | `C1-09b-pin-click.png`, `review-i1.json` |
| 14 | Pin the new shot | "Dùng cảnh này" = pin | `review-i1.json` rev 3: `pin [i1-s1c1f1]`, `exclude [i1-s1c2f2]`, and `exclude [i1-s1c3f3]` on Câu 2. Also "Duyệt cảnh này" on Câu 1 | `C1-10-review-i1-decisions.png` |
| 14b | Preview | "▶ Xem câu này" | The panel player plays the run video at the sentence (t = 15.7 s), with narration and subtitle | `C1-11-review-preview.png` |
| 15 | Accept explicitly | #3 panel: tick "Tôi chấp nhận xuất video với các câu chưa duyệt"; final-export dialog "Tôi chấp nhận, xuất bản cuối" | `accept_unreviewed: true` written. No warnings to acknowledge ("Không có cảnh báo."), so "Đã xem" was not testable | `C1-14-i3-accept.png` |
| 15b | Gate: draft allowed, final needs confirmation | "Dựng lại các phần đã đổi" with 13 unreviewed (allowed). Then "Xuất bản cuối…" | A dialog lists every part with its count. "Để tôi duyệt trước" → "Chưa xuất…", no `final.mp4`. After accepting #3 the dialog lists 11 sentences (#3 dropped) | `C1-13-final-export-confirm.png`, `C1-15-…` |
| 16 | Render with decisions | "Dựng lại các phần đã đổi" | "dựng 2 phần, dùng lại 6": `rendered [i1, outro]`. Only `part-i1`, `part-outro` and `compilation.mp4` changed hash | `results\C1-2-after-decisions-rerender\` |
| 16b | Final export | "Tôi chấp nhận, xuất bản cuối" | `final.mp4` written in about 9 s (dialog delay included), byte-identical to `compilation.mp4` (`0375255acf18…`) | `C1-16-final-exported.png` |
| 17 | Check the final video | `tools\checkvid.py` | See the notes after this table | `results\C1-3-final-export\video-check.json`, `C1-17`, `C1-18-item-starts.jpg` |
| 18–19 | Close and reopen | window X, relaunch | **Run not listed** ("Chưa có video ghép nào") even after refresh, although `control\runs\3848b499….json` records it (P1-2). It appears only after "Thư mục lưu kết quả" is set to the folder that holds it (`rc4\media`) | `C1-19-after-reopen-list.png`, `C1-20-…` |
| 20 | Decisions kept | open run → Kết quả → Duyệt cảnh #5 | Pin ("Đang dùng (đã ghim)"), excludes, accept checkbox ticked, `Bản cuối: …final.mp4`. The Câu 1 approval became **INVALIDATED** ("Cảnh của câu đã đổi sau khi bạn duyệt"): the re-render added `c1f3` next to the pin. Shown as a warning | `C1-21`, `C1-22-reopen-review-i1.png` |
| 21 | AI ranking never reorders the user | `program.json` + "Dùng thứ tự gợi ý…" | Video and chapters follow the user's #5→#1. The AI suggestion was the same order with reasons. "Dùng thứ tự gợi ý…" loads it into the editable list ("Lưu và dựng lại"), so it can be edited before export | `C1-23-suggested-order-in-editor.png` |

Mastering descriptions shown for the four levels (step 7):

- **Tắt:** "Video gốc luôn cần chọn cảnh theo lời bình, nên Tắt được dựng như Nhẹ."
- **Nhẹ:** "Chọn cảnh theo từng câu lời bình, cắt trong ranh giới cảnh, giữ thứ tự trong mỗi nguồn và thứ hạng của bạn; chuẩn hóa âm lượng. Nhịp cắt chậm, không mờ dần giữa các phần."
- **Cân bằng:** "Như Nhẹ, nhịp cắt vừa và mờ dần ở đầu và cuối mỗi phần; câu mở màn (nếu bạn viết) có thể dùng một cảnh nổi bật tới 6 giây lấy từ phần sau, luôn cần bạn duyệt."
- **Mạnh:** "Như Cân bằng, nhịp cắt nhanh hơn; cảnh mở màn được dài tới 8 giây. Không đổi thứ hạng."

Review panel (steps 11–12):

- Each part shows:
  - section ("Mục xếp hạng · i1" / "Phần chương trình · hook");
  - rank and title ("#5 · Cỗ máy domino trong lớp học");
  - narration;
  - per sentence: time range, coverage %, review state, chosen shots with source moment ("Mục 1 · nguồn 1 (m2-lop-hoc-2995.webm) · 2,0 s–4,1 s"), AI description, alternatives with score and reason, Ghim / Bỏ / Duyệt / Đặt lại / Hoàn tác.
- Review states: 12 NEEDS_REVIEW and 1 SOURCE_INSUFFICIENT (#3 Câu 1). Every sentence needs review; see P1-5.

Final video (step 17):

- H.264 1920×1080 + AAC, 76.41 s, −18.4 LUFS, LRA 5.4, true peak −1.5 dBFS.
- Parts in `program.json` order: hook, intro, i1#5, i2#4, i3#3, i4#2, i5#1, outro.
- Item start frames show "#5 Cỗ máy domino trong lớp học" … "#1 Nhà vô địch Purdue 2019". Subtitles are burned in and `captions.srt` is written. Mid-part frames match each item's content.
- Source audio moments:
  - each item with audio ends with an `audio_mode: source` segment of about 3.5 s;
  - #3 (silent 3D source) is narration only.
- Every part is CFR 30 fps. Part video is up to 103 ms shorter than its audio (i2 −63 ms, i4 −103 ms), so the concatenated file has 2287 frames for 76.41 s (avg 29.93 fps; small gaps at part boundaries, P2-9).

## 4. C2 — Partial re-render from C1 (desktop UI via CDP)

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 0 | A still-valid pin elsewhere | #1 panel, "Ghim" on Câu 1 | `review-i5.json`: `i5.u1 pin [i5-s1c1]` | |
| 1 | Edit narration of #4 | "Sửa chương trình…" → narration replaced | | `C2-01-edit-program.png` |
| 2 | Change footage of #5 | source 1 "Bắt đầu" = 4 (trims the pinned 0–2 s shot) | | |
| 3a | Save and re-render (first try) | "Lưu và dựng lại" | **"Hãy chọn giọng đọc."**: after a reopen the voice list is empty (0 options), so editing an existing run cannot be saved (P1-3). Nothing was written | `C2-03-voice-error.png` |
| 3b | Workaround, then re-render | "Thêm mới" → "Video gốc" (loads voices) → run → "Sửa chương trình…" → same edits → "Lưu và dựng lại" | Done in **33.5 s** (UI time, polling included). "dựng 3 phần, dùng lại 5" | `C2-02-rerender-done.png` |
| 4 | What changed | `status.json` + hashes | See the notes after this table | `results\C2-0-before\`, `results\C2-1-after\` |
| 5 | Decisions | `review-*.json` | i5 pin **kept**. i1.u1 pin/exclude **INVALIDATED**: "Cảnh đã chọn không còn trong nguồn (nguồn đã đổi); hãy chọn lại." Also shown as a run warning | |
| 6 | Edited narration heard and in subtitles | `captions.srt`, `silencedetect` on `i2.wav` | New text in the SRT ("Hạng tư thuộc về thư viện Hasdeu ở Chișinău, …"). Speech onset 21.767 s vs cue 21.690 s (−77 ms). The mid pause 26.51–27.12 s matches the cue gap 26.107–27.060 s. In sync | `C2-05-midpart-sheet.jpg` |
| 7 | Final export after the edits | "Xuất bản cuối lại" | **No confirmation dialog.** `final.mp4` was overwritten (`2be1d5624471…`) although #4 has new, never-seen sentences and #5 has an INVALIDATED pin. `status.json` says `unreviewed: 0` because the earlier "accept all" (`accept_unreviewed: true` on every part) survived the edits (P1-4) | `C2-04-reexport-no-confirm.png` |

What changed (step 4):

- `rendered_parts [i1, i2, outro]`, `reused_parts [hook, intro, i3, i4, i5]`.
- Hashes changed only for `part-i1`, `part-i2`, `part-outro`, `i2.wav` and `compilation.mp4`.
- TTS re-ran only for i2.
- Vision: 0 frames asked (80/80 cache hits).
- 3 encodes (`h264_mf`) plus a stream-copy concat.

## 5. C3 — Legacy v1 (desktop UI + packaged runtime)

- No v1 project existed in the test data. I made one through the legacy path of the rc4 UI from dubbed outputs.
- I dubbed two 30 s cuts of the rc3 dubbing sources in the rc4 app:
  - Hàng đợi › Thêm video từ máy → Lồng tiếng → Bắt đầu;
  - X30 completed;
  - Y30 failed correctly with `ASR_EMPTY` (no speech in that cut), so I used Y40 (40–70 s), which completed.

| # | Step | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Legacy mode, pick dubbed videos | "Video đã lồng tiếng" → "Chọn từ Hàng đợi" → X30, Y40 | The picker lists "Đã lồng tiếng" / "Chưa lồng tiếng". It inserted Y40 before X30 (list order, not click order; P2-12). I used ↑ to make X30 first | `C3-01`, `C3-05-v1-setup.png` |
| 2 | Legacy form | title, intro/outro, part titles and leads, voice | No Mastering selector on the legacy path (not forced) | |
| 3 | Render | "Ghép video" | "Đã tạo video tổng hợp. (6/6 đoạn)" in about 42 s. 72.92 s, H.264 1080p30 + AAC. Chapters: Mở đầu, Phần 1 X30, Phần 2 Y40, Kết thúc | `C3-06-v1-result.png` |
| 4 | Spec v1 | `.dubflow-work\spec.json` | `schema_version: 1`, `final_vi.mp4` sources in user order, no `mastering` key | `results\C3-spec-v1.json` |
| 5 | Same behaviour as rc3 | the same spec through the rc3 engine and the rc4 engine (packaged runtimes, `tools\c3cmp.py`, every argv logged) | **FFmpeg/FFprobe argv identical, 24/24** (`results\C3-argv-compare.txt`). Videos **byte-identical**: rc3 headless = rc4 headless = rc4 UI = `9afa62bcfb3ae463…`. The only process difference: rc3 started the TTS process (cold cache), rc4 hit the cache | |
| 6 | Persistence | app closed and reopened twice | The v1 run is listed ("2 phần"); "Thiết lập" shows the stored setup read-only; "Kết quả" shows the video and actions | `C3-08`, `C3-09-list-after-restart.png` |
| 7 | Unknown `schema_version` | test project with `schema_version: 9` | Listed as "Bản tương lai · Chưa rõ trạng thái". Both tabs say "Dự án này được tạo bằng phiên bản DubFlow mới hơn (định dạng 9); hãy cập nhật DubFlow để mở." "Dùng lại thiết lập" is hidden. Files unchanged (SHA-256 before = after, no new files) | `C3-07-unknown-version.png`, `results\C3-unknown-version-before.sha256` |

## 6. C4 — Errors and security

| # | Case | How | Result | Evidence |
|---|---|---|---|---|
| 1 | Missing narration | UI | "Mục 1: chưa có lời bình (viết lời bình, hoặc bật “AI viết lời bình”)." Nothing written | `C4-01` |
| 2 | Source deleted after import | UI, file deleted before render | "Mục 2: nguồn 1: Không tìm thấy video nguồn: missing-later.webm". No run folder | `C4-02` |
| 3 | Unsupported format | UI (.ogv) | Reported, not added (C1 step 4b) | `C1-03` |
| 4 | Corrupt file (`garbage.mp4`, random bytes) | UI | Run FAILED: "Mục 2: garbage: garbage.mp4 không có hình để phân tích". Shown in the list after the main thread was free again (see note) | `C4-04`, list after restart |
| 5 | Unreadable file (ACL deny read) | UI | **Not testable via UI**: the Windows Open dialog itself refuses the file and the app never receives it. Not counted | |
| 6 | Symlink source → outside | UI | "Video nguồn phải là tệp thật trên máy, không phải lối tắt: symlink.webm" | `C4-05-symlink.png` |
| 7 | Source inside a junction directory | UI | Accepted: only the file's own link status is checked. The source is read-only and the output stays in the output root | `results\…C4 junction` |
| 8 | Output folder = junction | UI (Cài đặt › Thư mục lưu, Win32 folder dialog) | "Hãy chọn lại thư mục lưu kết quả". `outside\out` stays empty (wording vague: P2-10) | `C4-09` |
| 9 | Output folder not writable | UI (ACL deny write) | "Không tạo được thư mục tổng hợp" | `C4-10` |
| 10 | AI without key | UI: Cài đặt › Tài khoản › ChatGPT (no saved key) | AI checkboxes disabled. "Chưa có khóa AI trong Cài đặt › Tài khoản: DubFlow chọn cảnh bằng tín hiệu hình ảnh, phụ đề và ghi chú của bạn." A render without AI completed. Provider restored to Claude afterwards. Credential Manager was not touched | `C4-11-ai-no-key.png` |
| 11 | AI failure (invalid key) | **packaged runtime**: the C1 spec with a fake key `invalid-test-key-…` in the child env only (`run_rc4.py --bad-key`) | COMPLETED (84 s). 9 warnings, e.g. "AI không đề xuất được thứ hạng (Khóa Claude (Anthropic) không hợp lệ hoặc không có quyền (401): invalid x-api-key); giữ thứ tự của bạn." The fake key appears in 0 output/log files | `semantic\runs\C4-aifail.*` |
| 12 | Invalid project version | UI | See C3 step 7 | |
| 13 | Cancel a running job | UI "Hủy ghép" during "Đang phân tích nguồn" | Stopped. No python/ffmpeg left. State **FAILED** with the raw message "Mục 2: m4-mo-phong-3d-20fps: cancelled" (P2-7). The panel says "Sẵn sàng xuất bản cuối." with no video (export button disabled) | `C4-12`, `C4-13` |
| 14 | Reopen the cancelled job | UI "Dựng lại các phần đã đổi" | 1st try: FAILED "Bộ tổng hợp đã dừng. Hãy thử lại."; `status.json.partial` shows `PermissionError [WinError 5] Access is denied` on the work folder (P2-8). 2nd try: COMPLETED | `C4-14`, `C4-15` |
| 15 | Nothing outside allowed folders | after all cases | `outside\out` is empty. All run folders are under the chosen output root | |
| 16 | Credential scan | `tools\keyscan.py` (Gemini + Claude, UTF-8 and UTF-16LE) | 11 810 files, 6.13 GiB, **0 hits**, 0 unreadable. Scope: data dir with the WebView2 profile, models, outputs, logs, results, semantic outputs, scratchpad | `results\keyscan.txt` |

Harness note (not an app defect):

- In one sequence my harness left a native Windows message box open (the refusal to open `locked.webm`). While it was open:
  - a render with the junction source looked hung;
  - the failed run kept showing "Đang bắt đầu dựng…".
- Both resolved as soon as the modal dialog closed:
  - the queued render then ran;
  - the failure message appeared.

## 7. Semantic E2E on real footage (packaged rc4 runtime, headless)

- Inputs: same specs, footage and frozen truth as the f75bd2b round (`phase2-e2e\specs-u`, `results-main-f75bd2b\truth`).
- Vision ran fresh (no cache copied in).
- Model root: `rc4\semantic\models` (a copy; `models-u` not touched).
- Scoring: rc4's own `engine.dubflow.mastering.quality` (`tools\analyze_rc4.py`).
- Raw output: `results\semantic\`, `semantic\runs\`.

| Set | Coverage f75bd2b → rc4 | Units ok | Contra ms | Stolen key ms | Silent miss | False alarm | Timeline problems | Wall rc4 |
|---|---|---|---|---|---|---|---|---|
| P1 Espresso (after pin) | 73.6 % → **66.4 %** | 4/6 → 2/6 | 2352 → 2352 | 3030 → 0 | 0 → 0 | 2 → 1 | none | 145 s cold; 8 s identical rerun; 16 s edit; 10 s pin |
| P2 Máy thổi bụi | 6.4 %* (mastering **fallback**, OUTSIDE_SOURCE) → **61.3 %**, planned | 0/6 → 0/6 | 3268* → 1725 | 8818* → 3564 | **6* → 0** | 0 → 0 | none | 143 s |
| P3 Hũ tăm | 52.7 % → 50.9 % | 2/6 → 1/6 | 1750 → 1648 | 0 → 2648 | **0 → 1** (u1 46 %, not flagged) | 1 → 1 | none | 167 s |
| Top 5 old (B1) | 93.4 % → **88.8 %** | 10/11 → 9/11 | 3240 → 1169 | 2794 → 4661 | 0 → 0 | 5 → 7 | REPEAT 2 → 3 | 140 s; identical rerun 7.6 s, 0/8 parts |
| Top 5, 5 sources (B5) | 94.5 % → **100 %** | 12/13 → 13/13 | 3240 → 0 | 2794 → **8596** | 0 → 0 | 7 → 11 | REPEAT 3 → 3 | 121 s (73 frames asked) |
| Why We Fight long (D1) | 43.0 % → 43.0 % | 2/4 → 2/4 | 0 → 0 | 0 → 0 | 0 → 0 | 2 → 2 | none; **A/V drift −8 → −108 ms** | 191 s |

\* P2 at f75bd2b was scored on the fallback plain concat.

- Review states / stolen and repeated shots:
  - B1: 26 review flags (was 14);
  - B5: 22 (was 16);
  - D1: 11 (was 14).
- No SLIVER, GAP, OVERLAP, HELD_FRAME, BACKWARDS or OUTSIDE_SOURCE anywhere.
- Desktop C1 (same five sources and narration as B5): every sentence came back NEEDS_REVIEW or SOURCE_INSUFFICIENT (see P1-5). It was not scored against the B5 truth, because the UI assigns item ids `i1…i5` and the truth uses named ids.

## 8. Performance (record only)

| Measure | Value |
|---|---|
| Import/probe in UI | Adding 5 files is instant (no probe at import). Probing/preparing happens at render ("Đang chuẩn bị chương trình…", about 15 s) |
| C1 first draft (cold, Claude vision) | 327 s. Frames 80, asked 75 (broad 43, targeted 5, multi-frame 32). TTS 8 parts. 8 part encodes + 1 concat (copy). Peak RAM **not recorded** (my RAM watcher had a syntax error at that point) |
| C1 decision re-render | 2/8 parts. Vision asked 0. About 10–15 s (not timed precisely) |
| C2 edit re-render | 33.5 s. 3/8 parts. 3 encodes. Vision 0/80 asked (cache hit). TTS only i2. Peak tree 2.85 GB (python 1.37 GB, ffmpeg 1.21 GB, WebView2 0.66 GB) |
| C3 v1 render | UI about 42 s. Headless: rc3 44.6 s (cold TTS), rc4 36.3 s (warm TTS). Peak tree 1.37 GB |
| Final export | Copy of `compilation.mp4` (no encode). The confirm dialog takes a few seconds to appear, with no busy indicator (P2-11) |
| Cache size | C1 run work folder 80 MB (mastering/vision 8.6 MB); TTS sentence cache 7.3 MB |
| Headless semantic, rc4 vs f75bd2b | B5 121 s with 73 frames asked (f75bd2b 45 s with vision cached); B1 identical rerun 7.6 s (2.5 s); D1 191 s (155 s); P1 identical rerun 7.7 s (7.6 s) |
| vs rc3 | Not directly comparable: rc3 measured Lồng tiếng and Bán hàng. RAM peaks stay below rc3's 3.5 GB |

## 9. Defects

| ID | Sev | Defect | Exact repro |
|---|---|---|---|
| P1-1 | P1 | The review panel offers alternatives whose id the host rejects. "Dùng cảnh này" → "Cảnh không hợp lệ", nothing saved. Host `compilation_raw.rs::is_shot` → `mastering::shot_number` accepts only `c<n>` or `c<n>f<n>`; the engine emits merged ids like `i1-s1c3f2f3` (the same check exists for Bán hàng `v<n>c<n>`) | C1 run → #5 Duyệt cảnh → Câu 2 → "Dùng cảnh này" on "15,4 s–16,1 s" (`editorial-i1.json` alternative `i1-s1c3f2f3`) |
| P1-2 | P1 | A raw Tổng hợp run does not reappear after an app restart when "Thư mục lưu kết quả" = "Cạnh video gốc" and the sources are not Hàng đợi jobs. `outputRoots()` scans only the chosen root plus queue job folders; `control\runs\*.json` is not used (Bán hàng solved this with localStorage in rc3) | Default output setting → raw run from files in any folder → close (X) → reopen → Ghép video: "Chưa có video ghép nào" |
| P1-3 | P1 | After a restart, "Sửa chương trình…" → "Lưu và dựng lại" fails with "Hãy chọn giọng đọc." The voice `<select>` has 0 options, because voices load only in `showDraft()` (raw-mode toggle) | Restart → Ghép video → open a v2 run → Kết quả → Sửa chương trình… → edit → Lưu và dựng lại |
| P1-4 | P1 | The final-export gate is bypassed after edits. `accept_unreviewed` from an earlier "Tôi chấp nhận, xuất bản cuối" (written to every part) survives narration and footage changes, so re-export runs with no confirmation. It ships never-seen sentences and an INVALIDATED pin; `status.json` shows `unreviewed: 0` | C1: Xuất bản cuối → accept → Sửa chương trình (change narration of one item, trim the source of another) → Lưu và dựng lại → "Xuất bản cuối lại" → exports at once |
| P1-5 | P1 (semantic) | Review states are uninformative on the desktop Top 5. 13/13 sentences need review (28 flags). Texts contradict each other: "Có cảnh khớp lời câu này. chỉ 0 % thời lượng câu có cảnh khớp lời (nguồn không đủ cảnh)". #5 Câu 2 is rated 0 % while all three chosen shots show the described domino rows on tables. #3 Câu 1 is SOURCE_INSUFFICIENT although the whole source is the 3D simulation it describes | C1 inputs → Duyệt cảnh #5 Câu 2, #3 Câu 1 |
| P1-6 | P1 (semantic) | Regression vs f75bd2b: P3 Hũ tăm u1 is a **silent miss** (46 %, not flagged; was 0). P1 Espresso drops 73.6 → 66.4 % (4/6 → 2/6). B1 drops 93.4 → 88.8 %. B5 stolen key footage 2794 → 8596 ms; false alarms rise (B1 5→7, B5 7→11) | `tools\seq_sem.sh` + `tools\analyze_rc4.py` on `specs-u` |
| P2-1 | P2 | The raw source list shows no duration or format (requirement 11.1) | Add any video |
| P2-2 | P2 | The draft accepts a path that does not exist (returned by the dialog for a typed name) with no warning until render | Type `a.mp4,b.webm` in the Open dialog |
| P2-3 | P2 | Accepting in the review panel does not refresh Kết quả: part rows still say "2 câu cần xem lại" and the top line "Còn 13 câu…" until the next render | #3 → tick accept → Đóng |
| P2-4 | P2 | An excluded shot stays "Đang dùng" with no marker until re-render. The status says "Bấm “Render lại video này”…" while the button is "Dựng lại các phần đã đổi". The warning label reads "Câu 1.u1" | #5 → Bỏ cảnh này |
| P2-5 | P2 | Approving a sentence after pinning is always invalidated by the re-render that applies the pin (the planner adds shots around the pin) | Pin → Duyệt cảnh này → Dựng lại |
| P2-6 | P2 | After a re-render, "Bản cuối: …final.mp4" still shows the old final as current | C2 before re-export |
| P2-7 | P2 | A cancelled job is recorded FAILED with a raw English message ("…: cancelled"). The panel says "Sẵn sàng xuất bản cuối." without a video | Hủy ghép during analysis |
| P2-8 | P2 | The first resume after cancel failed: engine `PermissionError [WinError 5]` on the work folder. The user sees only "Bộ tổng hợp đã dừng. Hãy thử lại."; a retry works | C4 #14 |
| P2-9 | P2 | Part video is up to 103 ms shorter than its audio; the concat gives 2287 frames for 76.41 s (VFR gaps). D1 A/V drift −108 ms (f75bd2b −8 ms) | C1 `part-i4.mp4`: v 12.967 s, a 13.070 s |
| P2-10 | P2 | The junction output-folder refusal says only "Hãy chọn lại thư mục lưu kết quả" (no reason) | Output folder = junction |
| P2-11 | P2 | "Xuất bản cuối…" shows nothing for several seconds before its dialog | Click once and wait |
| P2-12 | P2 | The dubbed picker ignores click order (uses list order) | Pick X30 then Y40 |

## 10. Evidence separation

| Kind | What |
|---|---|
| Unit / integration (CI, not re-run here) | `7c4fb1f`: PR Fast, PR Integration (governance, integration) success. #739: desktop-editor-windows pass. Windows Release build with installed-pipeline, desktop-runtime and source-sdk smokes passed |
| Packaged runtime (headless, rc4 runtime + FFmpeg) | C3 rc3-vs-rc4 argv/output comparison; C4 AI invalid key; semantic E2E (10 runs) |
| Desktop UI (real DubFlow.exe via CDP + Win32 dialogs) | C1, C2, C3 UI part, C4 #1–#4, #6–#10, #12–#14. Lồng tiếng of X30/Y40 |
| Not run | Unreadable source through the UI (OS dialog refuses); warning acknowledgement (no warnings in the panel); the C1 first-render RAM peak |
