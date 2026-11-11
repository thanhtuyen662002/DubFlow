# Gate S final closure — Windows E2E, DubFlow 1.1.96-rc7 (workstream D)

- Date: 2026-11-12. Owner's Windows 11 machine, CPU only, one job at a time.
- Folder: `D:\Projects\DubFlow\_sandbox\gate-s-final\rc7\` (evidence, screenshots, logs; screenshots stay local).
- rc7 = rc6 `85b9d81` + #753 (P2-14), #755 (P2-5), #757 (P2-6), #759/#761 (real eval set, VFR frame rate, vision warnings in part logs), #763 (subtitled footage is not a text card, per-sentence signals). Commit `7acd262`.
- Not done, by design:
  - no release, publish or tag;
  - the stable install and its data were not touched;
  - `_sandbox\models-u` was not touched.
- AI keys:
  - The app read its own key from Windows Credential Manager.
  - Host-integration runs put a key, or a fake key, into the child environment only.
  - Nothing was printed or written. Scans report counts only.

Every step below is labelled with how it was run:

- **UI (CDP):** the real rc7 `DubFlow.exe`, driven over WebView2 CDP with real mouse and keyboard events and Win32 file dialogs.
- **Host integration:** the packaged rc7 engine started the way the desktop host starts it, with the same argv and environment, outside the UI.
- **Unit/CI:** not re-run here.

## Summary

| Item | Verdict | Evidence |
|---|---|---|
| D1 Pin persistence (P2-5) | **PASS** (UI) | Pin + approve survive a re-render and an app restart; a source change invalidates them with a reason |
| D2 Final freshness (P2-6) | **PASS** (UI) | Old final shown as stale after the re-render and after reopening; re-export gives revision 2, and `final.json` sha256 = the file |
| D3 Provenance (P2-14) | **PASS for provenance; new P1 found** | Vision failure gives `signals_only`, 0 READY, and the UI says "chưa có bằng chứng nội dung". A misleading file name never makes a sentence READY. **But a product's first sentence is READY as a "hook" over unrelated footage** (P1-N1) |
| D4 Legacy | **PASS** (UI + host integration) | v1 render is byte-identical to rc4/5/6; argv 24/24 identical to rc6; unknown version refused with no write; product (balanced/off) and dubbing smokes produce valid files |
| D5 Security | **PASS** (UI + scans) | Junction and missing output folders refused; cancel/resume works; sources unchanged; 0 key/e-mail hits with a corrected scanner (the old scanner's Claude needle was wrong, see E-1) |
| Six-video regression (host integration) | **Mixed: no unflagged miss/contra/steal; three sets worse than rc6 code on identical evidence** | P2 coverage 77.6 → 65.0 %, B5 95.7 → 92.6 % (new 1785 ms contra in the outro, flagged), B1 stolen 1745 → 3330 ms (flagged). P1, P3 and D1 are equal. Every unit is flagged NEEDS_REVIEW |
| Build | **PASS on attempt 2** | Attempt 1 failed in the installed pipeline smoke with `Permission denied` on a job JSON (P2-N2) |

## 0. Build identity

| Item | Value |
|---|---|
| Artifact | `DubFlow-1.1.96-rc7-windows-x64`, run `43077136313` "Windows Release". Attempt 1: failure. Attempt 2 (failed job rerun): success |
| Commit | `7acd262bf2bd14adb18d6990d6664183fe80314f` (= `release-evidence.json` `source_sha`) |
| Channel | candidate, `production_qualified: false`, `unsigned-candidate` |
| Zip SHA-256 | `79ad547b…2470b25` (= `release.sha256`) |
| `app\bin\DubFlow.exe` SHA-256 | `e0d4985ba5d4127e5c59496482685e5b5a085c6d14e15c4e857b41ccb6eb130a` |
| FFmpeg / bytecode | `--enable-version3` only (LGPL); 0 `.pyc` |
| Engine in the bundle | identical to a `7acd262` checkout (0 differing engine files) |
| Runtime vs rc6 | 33 files differ: 20 console-script launcher `.exe` + 13 `RECORD`; no `.py`/`.pyd`/`.dll` differs |
| Machine | Windows 11 Home 10.0.26300, i7-12700H, 39.7 GB RAM, CPU only |
| Install | As rc4–rc6: zip extracted with Python `zipfile` to `rc7\app\versions\1.1.96-rc7\`, `current.json` written (manifest `b019135e…`), models copied from the rc6 sandbox without the TTS cache. Data root = `rc7\app` |

## 1. UI driving and proof

- Tools: `launch.ps1` (rc7 exe, CDP port 9447), `cdp.mjs`, `filedlg.ps1` (Win32 Open/folder dialog).
- Never used: Tauri IPC from the page, edits to app files, mocks.
- Helpers: temporary `data-e2e` attributes were added to the panel "Đóng" button and the queue's "Lồng tiếng" button, to target them. `<select>` values were set after a real click, as in earlier rounds.
- Proof (`logs\cdp-proof*.json`, `logs\cdp-proof-processes.txt`):
  - CDP browser pid 21084; its parent is pid 2268 = `…\rc7\app\versions\1.1.96-rc7\app\bin\DubFlow.exe`.
  - `--user-data-dir=…\rc7\app\control\webview2\312e312e39362d726337\EBWebView --remote-debugging-port=9447`, page `http://tauri.localhost/`.
  - Repeated after each reopen: browser 8872 / parent 512, then browser 9832 / parent 392, same exe.
- Harness slips (mine, all corrected):
  1. **Rerender button hidden by the panel overlay.** My first "Dựng lại các phần đã đổi" click hit the page's button under the open review panel, so nothing ran. I clicked the panel's own button.
  2. **Window X blocked by the panel.** My first window-X click was blocked by the panel overlay. The app was then closed properly (window X with the panel closed).
  3. **Status polling crashed a job.** My PowerShell loop read `status.json` during a render and crashed that job (see P2-N3). From then on I watched progress through the UI text only.

## 2. D1 — Pin persistence (P2-5), UI

Programme "D1 ghim và duyệt": raw Tổng hợp, kind list, 3 items (m2 domino class, m3 library books, m5 idea machine), Trúc Ly voice, Cân bằng, AI vision (Claude).

| # | Step | Result | Screenshot |
|---|---|---|---|
| 1 | Draft render | 3 parts in ≈2 min; 1 READY, 5 NEEDS_REVIEW | `D1-02-rendered.png` |
| 2 | Review i1, Câu 1: choose an alternative shot ("Dùng cảnh này", 4,1–6,0 s), then "Duyệt cảnh này" | `review-i1.json` revision 2: `i1.u1 pin [i1-s1c1f3]`, `approved [i1-s1c1f3]` | `D1-04-pin.png`, `D1-05-approved.png` |
| 3 | Re-render ("Dựng lại các phần đã đổi" in the panel) | "dựng 1 phần, dùng lại 2", 10 s. The plan uses the pinned shot; Câu 1 at 100 %. The panel says **"Bạn đã duyệt"** and **"Đang dùng (đã ghim)"**. **Not invalidated** | `D1-07-after-rerender.png`, `D1-08-review-after-rerender.png` |
| 4 | Close (window X) and reopen | Same: "Bạn đã duyệt", "Đang dùng (đã ghim)", 100 % | `D1-10-reopen-review.png` |
| 5 | Make the pinned shot disappear: "Sửa chương trình…", item 1 start = 7 s, "Lưu và dựng lại" | `review-i1.json`: `invalidated: "Cảnh đã chọn không còn trong nguồn (nguồn đã đổi); hãy chọn lại."`. Run warning shown. The panel shows **"Cần chọn lại"** with the reason. The plan starts at 7000 ms | `D1-13-…`, `D1-14-invalidated.png` |

Notes:

- **Step 5 render failure.** The first render of step 5 ended FAILED: "Bộ tổng hợp đã dừng. Hãy thử lại."
  - Cause: my `status.json` polling (P2-N3).
  - The part had already been rendered before the crash, so the next "Dựng lại" reported "dựng 0 phần, dùng lại 3" and the right state.
- **Two small UI points, both P3:**
  - Before the re-render, the panel still shows the old shots as "Đang dùng" (known P2-4).
  - The panel's warning box says "Không có cảnh báo." while the run has the invalidation warning.

## 3. D2 — Final freshness (P2-6), UI

| # | Step | Result |
|---|---|---|
| 1 | "Xuất bản cuối…" → "Tôi chấp nhận, xuất bản cuối" | `final.json` revision 1, sha256 `0b8c427c…` = `final.mp4` = `compilation.mp4`. "Bản cuối (lần xuất 1)" |
| 2 | Edit the narration of item 3, "Lưu và dựng lại" | "dựng 1 phần, dùng lại 2", 15 s. **"Bản cuối cũ: … (chương trình đã được dựng lại sau lần xuất này). Bản này không còn là bản hiện tại: hãy xuất bản cuối lại."** |
| 3 | Close and reopen | Same stale message (`D2-05-reopen-stale.png`) |
| 4 | "Xuất bản cuối lại" → accept | `final.json` **revision 2**, sha256 `9f7f212c…` = `final.mp4` = `compilation.mp4`. "Bản cuối (lần xuất 2)" (`D2-07-reexported.png`) |

## 4. D3 — Evidence provenance (P2-14)

Engine runs: host integration (packaged rc7 engine, desktop argv/env). Display: UI. I placed the run folders under the chosen output root and opened them in the app.

The UI cannot inject a wrong key into the child environment without changing the saved key, which I did not do.

**(a) Vision fails (fake key in the child env only)**

| Run | Mode | Provenance | Units | UI |
|---|---|---|---|---|
| Raw Tổng hợp (D1 programme) | `signals_only` ×3 | vision 0, `filename_metadata` 4/2/7 | MATCH_NOT_FOUND 5, NEEDS_REVIEW 1, **READY 0** | "Bằng chứng: tên file 4 cảnh (tên file chỉ để xếp hạng, không tính là đã thấy nội dung). **Chưa có bằng chứng nội dung: không câu nào được coi là sẵn sàng.**" + 401 warning (`D3-02-…`) |
| Bán hàng (P2 spec) | `signals_only` | vision 0, `filename_metadata` 8 | MATCH_NOT_FOUND 5, NEEDS_REVIEW 1, **READY 0** | same wording + 401 warning (`D3-04-…`) |

**(b) Misleading file name, vision working**

- Setup:
  - m4 (3D domino simulation) was copied as `noi chien khong dau chong dinh.mp4`.
  - The product script is about an air fryer (4 sentences).
- Result: `mode: content`, provenance vision 13 / filename 13.
  - Sentences 2–3 (basket, fries) are SOURCE_INSUFFICIENT. **The file name did not make anything READY.**
  - **Sentence 1, "Chiếc nồi chiên không dầu này có lòng nồi chống dính phủ ceramic.", is READY as "Câu mở đầu/kêu gọi: không cần cảnh riêng"** on domino footage, although the vision text of that shot says "Không thấy sản phẩm".
  - Sentence 4 (CTA) is READY, which is correct.
- Verdict: **P1-N1**, see §9.

## 5. D4 — Legacy paths

| # | Step | Kind | Result |
|---|---|---|---|
| 1 | Output root = `rc7\c3v1`; v1 project listed | UI | "Ghép thử hai phim ngắn · 2 phần", "Bản tương lai" |
| 2 | Unknown version | UI | "Dự án này được tạo bằng phiên bản DubFlow mới hơn (định dạng 9); hãy cập nhật DubFlow để mở." No reuse button. **34/34 files unchanged** |
| 3 | v1: "Dùng lại thiết lập này cho bản mới" → "Ghép video" | UI | `compilation.mp4` = **`9afa62bc…`** = rc4/rc5/rc6 |
| 4 | Argv rc6 vs rc7 (same v1 spec, every subprocess logged) | host integration | **ffmpeg/ffprobe 24/24 identical**, same output hash |
| 5 | Bán hàng smoke (2 P2 clips, own script, Cân bằng) | UI | COMPLETED; h264 1080×1920 30 fps + aac 48 kHz, 8.233 / 8.220 s; mode content; "2 câu cần xem lại" |
| 6 | Bán hàng, Mastering Tắt | UI | COMPLETED; the spec has no `mastering`; 8.233 / 8.220 s |
| 7 | Dubbing smoke (zh drama 30 s, Trúc Ly, Mastering Cân bằng) | UI | Done in 1:30; h264 1024×576 + aac, 30.000 / 29.99998 s; `captions_vi.srt`. (A first clip had no speech, which the app reported clearly: "Không tìm thấy lời thoại"; that was my input choice) |

## 6. D5 — Security

| # | Case | Kind | Result |
|---|---|---|---|
| 1 | Output folder = junction to `outside\out` | UI | Render refused: "Hãy chọn lại thư mục lưu kết quả" (wording still vague, P2-10). `outside\out` empty |
| 2 | Output folder chosen, then deleted | UI | Refused the same way; folder not recreated |
| 3 | Source through a junction (`d5\junc-src\m3-outside.webm`) | UI | Accepted read-only (a user-chosen file); output inside the chosen root; the source copy is unchanged |
| 4 | Cancel a running job (AI vision on) | UI | CANCELLED, no python/ffmpeg left, "Xuất bản cuối…" disabled. The page still says "Sẵn sàng xuất bản cuối." (P2-7, unchanged) |
| 5 | Reopen the cancelled job | UI | "Dựng lại các phần đã đổi" COMPLETED first try (3 parts, 40 s) |
| 6 | Sources unchanged | file hashes | 7/7 media files + the outside copy unchanged; rc6 media vs rc7 copies 5/5 equal |
| 7 | Key and e-mail scan (app closed) | `tools\keyscan2.py` | 8 759 files / 3.93 GiB, 0 unreadable: **claude 0, gemini 0, e-mail 0**. Scope: data dir with the WebView2 profile, TTS cache, all outputs, d3/d5/repro/regression, logs, results, the Gate S runs. Self-test with canaries: PASS |
| 8 | IPC validation | — | **Not run here** (Rust unit tests in CI only) |

## 7. Six-video semantic regression (host integration)

- **How it was run:**
  - Engine from a `7acd262` checkout plus the packaged runtime, through `tests/mastering/real/capture.py`. The runtime used is the rc6 one, which matches rc7 except for launcher/RECORD files.
  - Fresh Claude vision (`claude-opus-5-5`, 0 errors in all six runs).
  - Specs: `phase2-e2e\specs-u` with the provider set to claude.
- **Replays:** `capture.replay` of the same recorded calls with the rc6 code (`85b9d81`) and with the rc7 code. The harness was placed inside each checkout, so the engine import is the checkout under test (asserted).
- **Scoring:** everything is scored by the `7acd262` quality scorer against the phase2 truth.

| Set | Run | Coverage % | Units ok | Silent | False alarms | Contra ms | Stolen ms | Repeats | Flagged units | A/V ms |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 Espresso | rc7 fresh | 82.7 | 3/6 | 0 | 2 | 380 | 0 | 0 | 5 | −3.3 |
| P1 | rc6 code replay | 82.7 | 3/6 | 0 | 2 | 380 | 0 | 0 | 5 | |
| P2 Máy thổi bụi | rc7 fresh | **65.0** | 2/6 | 0 | 2 | 935 | 2774 | 0 | 6 | −3.3 |
| P2 | rc6 code replay | **77.6** | 3/6 | 0 | 3 | 1917 | 1211 | 0 | 6 | |
| P3 Hũ tăm | rc7 fresh | 43.9 | 0/6 | 0 | 0 | 1000 | 1000 | 0 | 6 | +10.0 |
| P3 | rc6 code replay | 43.9 | 0/6 | 0 | 0 | 1000 | 1000 | 0 | 6 | |
| B1 Top 5 old | rc7 fresh | 94.9 | 10/11 | 0 | 10 | 0 | **3330** | 4 | 11 | −11.3 |
| B1 | rc6 code replay | 94.9 | 10/11 | 0 | 10 | 0 | **1745** | 4 | 11 | |
| B5 Top 5 (5 src) | rc7 fresh | **92.6** | 11/13 | 0 | 11 | **1785** | 2585 | 4 | 13 | −11.3 |
| B5 | rc6 code replay | **95.7** | 12/13 | 0 | 12 | 0 | 2585 | 3 | 13 | |
| D1 Why We Fight | rc7 fresh | 43.0 | 2/4 | 0 | 2 | 0 | 0 | 2 | 4 | **−41.3** |
| D1 | rc6 code replay | 43.0 | 2/4 | 0 | 2 | 0 | 0 | 2 | 4 | |

- **Determinism:** the rc7-code replay reproduces the rc7 fresh plans exactly in all six sets.
- **Code effect, same evidence:**
  - Equal in P1, P3 and D1.
  - **Worse in P2, B5 and B1.** Every affected unit is flagged NEEDS_REVIEW, so there is no silent miss and no unflagged contra or steal:
    - P2 u3: 696 → 0 ‰. u5 gains 1839 ms of stolen key footage. u6: 946 → 746 ‰, no longer ok.
    - B5 `outro.u1`: 1000 → 0 ‰, with 1785 ms contra.
    - B1 `mo-phong-3d.u1`: +1585 ms stolen.
- **Review load:** every unit of every set is flagged; the false alarms are as high as in rc6.
- **Not comparable with the rc6 report:** the rc6 report's fresh numbers used Gemini vision, so they are not like-for-like. The replay table above is the comparison.
- **A/V:** P1/P2/P3/B1/B5 as in rc6. D1 −41.3 ms (P2-9, open).

## 8. Performance and cost (record only)

| Run | Wall s | Peak RAM (tree / engine) MB | Vision requests / frames sent / cache hits | ffprobe / ffmpeg / encodes | Scene analysis done by | First encode at |
|---|---|---|---|---|---|---|
| P1 (fresh) | 142.1 | 3507 / 1335 | 10 / 36 / 0 | 8 / 42 / 1 | 18 s | 136 s |
| P2 | 51.8 | 2171 / 1261 | 3 / 12 / 0 | 8 / 18 / 1 | 14 s | 46 s |
| P3 | 50.8 | 2135 / 1258 | 4 / 13 / 0 | 8 / 18 / 1 | 14 s | 46 s |
| B1 | 318.1 | 2723 / 1453 | 21 / 76 / 4 | 14 / 97 / 8 | 5 s | 182 s |
| B5 | 238.6 | 2573 / 1316 | 21 / 73 / 6 | 14 / 95 / 8 | 5 s | 129 s |
| D1 | 271.2 | 2610 / 1464 | 21 / 80 / 0 | 6 / 465 / 3 | 53 s | 225 s |
| B1 unchanged rerun (vision cached) | **7.1** | 99 / 58 | 0 / 0 / 80 | 6 / 30 / 0 | – | – |

- **AI cost:** 80 requests and 290 frames for the six fresh sets. Tokens are not exposed by the engine, so they were not recorded.
- **UI timings:**

| Step | Time |
|---|---|
| D1 cold draft, 3 parts with vision | ≈120 s |
| Pin re-render, 1 part | 10 s |
| Edit re-render, 1 part | 15 s |
| Resume after cancel, 3 parts | 40 s |
| Dubbing, 30 s clip | 90 s |
| Product smoke | not timed precisely |

- **Stage times:** taken from subprocess timestamps. TTS runs in-process and is not split out.

## 9. Defects

| ID | Sev | Status | What | Repro |
|---|---|---|---|---|
| P1-N1 | **P1 (semantic)** | **New** | A product's first sentence is treated as a hook (NOT_NEEDED) and auto-READY, even when it states a specific product fact and the vision text says the product is not visible. Result: unrelated footage shown silently under a product claim | D3b: `d3\product-mislead.json` (m4 renamed to an air-fryer name), host integration with Claude → `editorial-1.json` u1 `{evidence: NOT_NEEDED, state: READY}` |
| P1-N2 | **P1 (semantic, flagged)** | **New in rc7 code** | Same evidence, rc7 code vs rc6 code: P2 −12.6 pts coverage (u3, u5, u6), B5 outro contra 1785 ms, B1 +1585 ms stolen. All flagged, no silent miss. Likely from #763 per-sentence signals | `tools\reg_score.py` on `regression\rc7fresh\*` (replays with both checkouts) |
| P2-N2 | P2 | New (CI) | Installed pipeline smoke failed once: `invalid JSON at …\control\jobs\smoke-live-owner.json: [Errno 13] Permission denied`; passed on rerun | Run 43077136313 attempt 1 log |
| P2-N3 | P2 | New | The engine dies when `status.json` is held open by another reader. `_atomic_text` → `os.replace` raises `PermissionError [WinError 5]`, which is not retried; the failure handler writes status again, also fails, the process exits with status RUNNING, and the host writes "Bộ tổng hợp đã dừng". The host discards the child's stderr (`Stdio::null`), so no diagnostic remains. Real-world trigger: antivirus or backup tools opening the file | `tools\holder.py` keeps `status.json` open while `tools\repro_key.py none <spec>` runs → traceback in `logs\D1-hold.stderr.txt` |
| P2-9 | P2 | Open | D1 A/V −41.3 ms at the concat | §7 |
| P2-10 | P2 | Open | Junction/missing output folder message "Hãy chọn lại thư mục lưu kết quả" gives no reason | D5 #1–2 |
| P2-7 | P2 | Open (partly) | After cancel the page still says "Sẵn sàng xuất bản cuối." (export correctly disabled) | D5 #4 |
| P2-4 | P2 | Open | Before re-render, chosen/excluded shots still show the old "Đang dùng" | D1 step 2 |
| P3-N4 | P3 | New | Review panel warning box says "Không có cảnh báo." while the run carries the invalidation warning | D1 step 5 |
| E-1 | Evidence | Fixed for rc7 | The rc5/rc6 `keyscan.py` decoded the Claude key as UTF-16 (the desktop stores UTF-8; the blob has an even length), so its Claude needle was wrong. **Claude-key "0 hits" in rc5/rc6 are not valid evidence.** Gemini needles were correct (odd length → UTF-8 fallback). rc7 uses `keyscan2.py` with a self-test | §6 #7 |

## 10. Evidence separation

| Kind | What |
|---|---|
| CI | Run 43077136313 attempt 2 success (installed pipeline, desktop launch, SDK, PCM smokes); attempt 1 failure (P2-N2) |
| UI (real rc7 DubFlow.exe via CDP + Win32 dialogs) | D1, D2, D3 display, D4 #1–3 and #5–7, D5 #1–5 |
| Host integration (packaged rc7 engine, desktop argv/env) | D3 engine runs, D4 argv compare, P2-N3 repro, six-video regression and replays, performance |
| Scans | Key/e-mail scan, source hashes |
| Not run | IPC validation tests (CI only), token accounting |
