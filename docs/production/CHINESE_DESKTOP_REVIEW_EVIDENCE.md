# Chinese real-media desktop review evidence (#271)

Status: **FAIL — Issue #271 not accepted.** The production Chinese path passed on real media; the review revision replaced the original output in place, which acceptance item 4 forbids. The desktop GUI was not driven (**NOT_RUN**). This is not full #175 qualification.

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

## Not run

- **Desktop GUI**: the review/revision was exercised through the host's file contract, not by clicking the WebView UI.
- Hachimi translator, multi-voice, background separation and blur were not part of this run.
- Long-form (2–6 h), batch, chaos and GPU scope remain under #175.

## Deterministic coverage kept

- `apps/desktop/test/translation_review_flow.test.mjs`: Chinese/Auto/English routing, review edit/undo, failed-save preservation, revision options.
- `tests/release/test_chinese_desktop_review_smoke.py`: path, sidecar and report validation, the replayed review contract, and the desktop-equivalent supervisor command.

These are tests, not real-media evidence.
