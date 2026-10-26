# Chinese real-media desktop review evidence (#271)

Status: **#271 accepted on the shipped app.** The real desktop window of the installed, signed DubFlow 1.1.0 ran the whole Chinese review/revision flow on the owner-provided real video (see "Desktop GUI run").
- The first run (1.0.0 contract) failed because the revision overwrote the original; that was fixed in #273/#274/#275/#282.

This is not full #175 qualification.

## What ran

| Item | Value |
| --- | --- |
| Harness | `scripts/release/chinese_desktop_review_smoke.py` (report schema `dubflow-chinese-desktop-review-smoke-v1`) |
| Run | 2026-10-25 13:15:26Z – 13:21:09Z, local Windows 11 workstation, CPU |
| Runtime | DubFlow **1.0.0**, stable, source `e8b1c9b97f18bfcdaa0e06995882a637124e62a0`, Ed25519-signed release manifest. Installed from the published `DubFlow-1.0.0-windows-x64.zip` into an isolated install root with the bundled `bootstrap.py`, which verified the signature (`production_qualified: true`). The engine and supervisor are unchanged between `e8b1c9b` and the tested base `2e5d72a`, apart from one supervisor test file. |
| Source | Owner-provided Douyin download, used with the repository owner's authorization given in chat on 2026-10-25. Kept outside the repository and not committed. SHA-256 `bbba2c9be2d6717d694c05eba090b5f85af341123d313732bbf7cef9224785b1`, 32,395,084 bytes, 246.155 s, H.264 1024×576 + AAC 44.1 kHz stereo. **No subtitle sidecar** (checked by the harness). |
| Models | ASR `faster-whisper-1.2.1-bounded-word-gap-v4`, binding `sha256:fc7417634a1036fefed61c54c066f91aeb2c8ee474f21161f2b6cd1901c709b8`; translation Argos `argos-pinned-routes-v1`, route zh→en→vi, packages `zh-en` 1.9 (`62e7af5a3a48b530e47b7b3e5c78c2de79073ecd815750d2bf3ab35b4a67da2d`) and `en-vi` 1.9 (`86957101aa4099aa9a1a7492e41987d938d3cf0fdaf4fb684c0797a9d567dd16`); profile `cpu-local-file-b2`; dubbing voice `vi-mai-anh-vieneu3-v1`. Models were copied from a local cache; the app verified them and nothing was downloaded. |
| Production command | Same arguments as the desktop host's `start_job` for a Chinese Argos job with dubbing: `dubflow-supervisor.exe run --root <install>\versions\1.0.0 --job-id smoke271-… --source <source> --output-dir <work>\run-… --data-root <install> --model-root <install>\models --status-path <install>\control\jobs\smoke271-….json --enable-dubbing --tts-voice-id vi-mai-anh-vieneu3-v1 --source-language zh` |

Reproduce with an installed DubFlow and your own authorized Chinese video:

```bash
python -m scripts.release.chinese_desktop_review_smoke --source <video> --install-root <LocalAppData>/DubFlow --work-root <empty folder> --report report.json --tts-voice-id vi-mai-anh-vieneu3-v1
```

## Results

| Stage | Seconds | Exit | State | Reason |
| --- | --- | --- | --- | --- |
| production | 255.8 | 0 | COMPLETED | `completed_partial_dubbing` |
| revision (after review) | 85.6 | 0 | COMPLETED | `completed_partial_dubbing` |

Passed for the production run and again for the revision:
- Chinese transcript: 124 cues, all with CJK text; explicit routing recorded as `source_language: zh`, `language_authority: requested`.
- Vietnamese translation on all 124 cues, with Argos provenance (backend, route and package hashes in `job_manifest.json`).
- Manifest source path matches the source; all 17 manifest artifacts match their on-disk SHA-256.
- `captions_vi.srt`, `captions_vi.ass` and `editable/timeline.json` present.
- QC `passed` (18 warnings, 19 after the revision).
- Output playable: H.264 1024×576 + AAC 48 kHz stereo, 246.133 s.

Review (replay of the host `review_cues`/`save_review` file contract, not the GUI):
- An invalid save (blank text) is rejected and changes nothing on disk. A retried valid edit of cue `asr-800-12960-c7517eabd1a4` is persisted in `translation_overrides.json`.
- The revision's new captions contain the edited text, and its manifest records `translation.reviewed_overrides`.

**Failed:**
- `revision: output separate from the original`: `save_review` returns the **same** folder, and the desktop then starts the revision there.
- `revision: original validated output still available`: `final_vi.mp4`, `captions_vi.srt`, `captions_vi.ass`, `job_manifest.json` and `qc_report.json` were all **replaced in place**. The original validated output is no longer available after a review revision.

Exact reproduction of the defect:
1. Complete any job.
2. Open **Xem & sửa bản dịch**, edit one cue, then **Lưu & xử lý lại**.
3. `save_review` (`apps/desktop/src-tauri/src/main.rs`) writes `translation_overrides.json` beside the outputs and returns that folder.
4. `QueueController.reviseJob` pins the revision's `outputDir` to it, and the worker re-renders into the same file names.

The deterministic desktop test from #267 checks queue state with a fake `save_review` folder. It does not show that the earlier files survive on disk.

## Additional quality finding (not an acceptance failure)

45 of 124 cues (36%) were not dubbed. All 45 have code `DURATION_FIT_REQUIRED`: natural Vietnamese speech cannot fit the cue at the safe speaking rate. As designed, those cues keep the original audio, the job ends `completed_partial_dubbing`, and the manifest records `B2_AUDIO_DEGRADED`. 17 cues used `TTS_INTERCUE_GAP_USED`. On fast real Douyin speech this rate is high enough to matter to users.

## Re-run after the #273 fix

The fix makes `save_review` create a sibling folder "<name> (sửa N)". That folder holds a copy of the worker cache (without the rebuilt `b2-audio` and `export-pending`), the reviewed text and `revision_of.json`, which records the original folder and the SHA-256 of its manifest. The original folder is only read.

The harness replay was updated to that contract, and the same real video was run again on the same installed 1.0.0 runtime (2026-10-25 13:38:40Z – 13:43:33Z). The Rust host change is covered by its own unit test (`review_revisions_go_to_new_sibling_folders_and_leave_the_original_untouched`). It is not part of an installed build yet, so this run exercises the worker and the file contract, not the shipped host binary.

| Stage | Seconds | Exit | State | Reason |
| --- | --- | --- | --- | --- |
| production | 196.0 | 0 | COMPLETED | `completed_partial_dubbing` |
| revision in "… (sửa 1)" | 95.6 | 0 | COMPLETED | `completed_partial_dubbing` |

**Verdict: pass, 31/31 checks.**
- The production checks above pass again.
- An invalid save creates nothing; the retried save persists the edit in the new folder.
- The revision's captions contain the edit, and `reviewed_overrides` is recorded.
- The revision folder is separate from the original.
- All five original outputs (`final_vi.mp4`, both caption files, `job_manifest.json`, `qc_report.json`) are byte-identical after the revision.
- `revision_of.json` points to the original folder and its manifest hash.

The revision took about half the production time because it reused the copied transcription, translation and TTS cache. The undubbed-cue finding is unchanged (45/124, `DURATION_FIT_REQUIRED`).

Follow-up (#273, S5): the first fix removed any existing `<target>.partial` folder before building, which could delete a user's folder or another save in progress. Each save now creates its own uniquely named temporary folder with a create-only call. It removes only that folder on failure, and it never moves onto an existing name. Sentinel tests cover this in the Rust host and the harness. The same real video re-run (2026-10-25 16:41:56Z – 16:46:13Z) passed 31/31 checks: production took 194.7 s and the revision 60.6 s, and no temporary folder was left behind.

Follow-up (#273, no-replace publication): revisions are now published by reserving the `(sửa N)` name with a create-only directory call. That call fails on any existing path, empty folders included. The save then moves its own entries into the reservation and removes only those entries on failure. Rust and harness tests cover empty and non-empty existing folders and six concurrent saves. The same real video, re-run on the installed, signed **1.1.0** (2026-10-26 01:56:01Z – 02:00:44Z), passed 31/31 checks: production took 235.5 s and the revision 45.0 s.

## Desktop GUI run (installed 1.1.0)

- **Setup.** The installed, signed **DubFlow 1.1.0** (`DubFlow.exe` from its version root, data under an isolated LocalAppData) was started with WebView2's documented `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port`. Its real window was driven over the DevTools protocol, so every action went through the real UI code and the real Tauri host commands (`expand_video_paths`, `start_job`, `review_cues`, `save_review`). The only setup outside the UI was the results-folder preference: the per-viewer `dubflow.outputRoot.v1` entry was pointed at a sandbox folder so the run would not write next to the user's video.
- **Run 2026-10-26 02:04–02:10 UTC.** Six screenshots were kept locally; they are not committed because they show local paths.

| Step | Result |
|---|---|
| Video added through the window's native drag-drop event (`tauri://drag-drop`) | queued; **dubbing on by default** with the catalog default voice `vi-truc-ly-vieneu3-v1` |
| Source language set to Tiếng Trung in the real select; **Bắt đầu** clicked | COMPLETED in 4 m 52 s, `completed_partial_dubbing`; the result card warns about the undubbed line |
| **Xem & sửa bản dịch** opened | 124 cues listed, 1 with failed dubbing |
| Blank text typed into a cue | **Lưu & xử lý lại** disabled, so a failed save is impossible from the UI (the host also rejects it) |
| Valid edit "Đôi mắt đã bị hạ." → "Đôi mắt đã bị hạ (đã duyệt)"; **Lưu & xử lý lại** clicked | revision job queued with `revisionOf` and output folder `… (sửa 1)` |
| Revision | COMPLETED in 42 s; the queue shows both jobs and a "Bản sửa" tag |
| On disk | the original folder's `final_vi.mp4`, both captions, `job_manifest.json` and `qc_report.json` are **byte-identical** before and after. The revision folder is separate, its captions contain the edit, and `revision_of.json` links the original folder and manifest SHA-256 |

Finding from this run, fixed in the same PR: the dialog summary flagged "79 câu đọc quá nhanh". It still judged pace per cue, although the engine dubs close cues as phrases at up to 1.5x. Pace is now computed per dubbing phrase, and only phrases above 5.25 syllables/s are flagged.

## Not run

- Hachimi translator, multi-voice, background separation and blur were not part of this run.
- Long-form (2–6 h), batch, chaos and GPU scope remain under #175.

## Deterministic coverage kept

- `apps/desktop/test/translation_review_flow.test.mjs`: Chinese/Auto/English routing, review edit/undo, failed-save preservation, revision options.
- `tests/release/test_chinese_desktop_review_smoke.py`: path, sidecar and report validation, the replayed review contract, and the desktop-equivalent supervisor command.

These are tests, not real-media evidence.
