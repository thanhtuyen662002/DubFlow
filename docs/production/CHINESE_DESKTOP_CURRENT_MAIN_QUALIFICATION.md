# Chinese desktop review on current Main (#439)

Status: **passed on exact Main `99edc85d12c53d536ac56adcf2830cbca8093bda`.** The real desktop window and Tauri host of an install built from that commit ran the whole Chinese→Vietnamese review and revision flow on the owner-authorized real video. The original output stayed byte-identical. The revision went to its own folder.

This is one leaf of #175, not full #175 qualification. Earlier evidence for 1.0.0–1.1.1 is in `CHINESE_DESKTOP_REVIEW_EVIDENCE.md`.

## Build and install

| Item | Value |
| --- | --- |
| Source | Main `99edc85d12c53d536ac56adcf2830cbca8093bda` (post-merge PR #461), clean checkout; `SOURCE_DATE_EPOCH` = commit time `1793728249` |
| Why a local build | Latest stable v1.1.49 was built from `3f059de`, before #458. No release was triggered, signed or published for this task. |
| Desktop host | `npm ci`, `npm run tauri:build` with `DUBFLOW_RELEASE_VERSION=1.1.50-main.99edc85`, `DUBFLOW_RELEASE_CHANNEL=candidate`, `RUSTFLAGS=-C target-feature=+crt-static` (same steps as `release.yml`). `DubFlow.exe` SHA-256 `6bea6bb2364c0c3027cce80167873b9872d256cdcef009fc5ba5c658a95191b6` |
| Supervisor | `cargo build --locked --release`, same flags. `dubflow-supervisor.exe` SHA-256 `33705f96cb428e8c3011aafb1ccdb294118a4d918b4b7f6ee23d405b0fd62bc8` |
| Runtime | `runtime/` of the published v1.1.49 ZIP (SHA-256 `c2c5348313c5d6c7297588537a5d16e14aeac938e691f9c574caa5f81b6d5061`, matched the release checksum file). `packaging/runtime` is unchanged between `3f059de` and `99edc85`, so it holds the same pinned Python 3.12.10, packages, FFmpeg and source SDK that CI would produce. |
| Bundle | `python -B -m packaging.release.builder … --version 1.1.50-main.99edc85 --source-sha 99edc85… --release-channel candidate --require-desktop-host --require-supervisor`. ZIP SHA-256 `c0df359668b88c6cc55fc73975988fa5f0e63f56be4ca23367434ba9bab77c26`, 8,590 manifest artifacts. Unsigned candidate: not a release. |
| Manifest | `version 1.1.50-main.99edc85`, `release_channel candidate`, `source_sha 99edc85d12c53d536ac56adcf2830cbca8093bda`; installed manifest SHA-256 `64eb3580a43b5391f63f0c7eec55f77e2625c68b0a0821b290d825a54c13ad42` |
| Install | The bundle's own `bootstrap.py` (the command `setup.ps1` runs) into an isolated `LOCALAPPDATA` under the sandbox: `ready: true`, `production_qualified: false` (candidate). |
| Models | Copied from the local cache of the 1.1.1 run. Before the run, the ASR, Argos and VieNeu files matched `production-cpu-v1`, `production-translation-v1` and `production-vieneu-v1` by size and SHA-256. The model folder had the same 59 files after both runs: **nothing was downloaded**. |
| Host | Windows 11 Home 10.0.26300, CPU only (no NVIDIA GPU), Node 24.22.1, Rust 1.98.1, WebView2 Edg/156.0.4314.58 |
| Source video | Owner-authorized non-customer Douyin video, already present in the user's Downloads folder; not copied, uploaded or committed. SHA-256 `bbba2c9be2d6717d694c05eba090b5f85af341123d313732bbf7cef9224785b1`, 32,395,084 bytes; no subtitle sidecar. |

## Desktop GUI run

Run 2026-11-03 18:28:38–18:33:12 UTC, one continuous session. The app was opened through the installed `DubFlow.cmd`, with WebView2's `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port`. A script drove the real window over the DevTools protocol. It clicked the page's own buttons and fired the textareas' own input events, so every action ran the real UI code and the real Tauri commands (`start_job`, `review_cues`, `save_review`). The only setup outside the UI was the per-viewer results-folder preference (`dubflow.outputRoot.v1`), pointed at a sandbox folder. Nine screenshots are kept locally and not committed, because they show local paths.

| Step | Result |
|---|---|
| Video added through the window's native drop event (`tauri://drag-drop`) | Queued; dubbing on by default, voice `vi-truc-ly-vieneu3-v1` |
| Source language: the real select offers Tự động / Tiếng Trung / Tiếng Anh; **Tiếng Trung** chosen; **Bắt đầu** clicked | COMPLETED in 3 m 40 s, `completed_partial_dubbing`. The card says 4 segments (3 s, 1.9 % of speech) keep the original audio. |
| **Xem & sửa bản dịch** opened | "124 câu · 4 câu lồng tiếng lỗi · 20 câu có thể không kịp lồng tiếng". The issues-only filter is on, with 24 cues shown. Save and Hoàn tác are disabled. |
| Valid edit "Đôi mắt đã bị hạ." → "Đôi mắt đã bị hạ (đã duyệt)" | Save and Hoàn tác enabled |
| **Hoàn tác** clicked | Text back to "Đôi mắt đã bị hạ."; Save and Hoàn tác disabled again |
| Same edit typed again (reapply) | Save enabled |
| Blank text ("   ") typed | **Lưu & xử lý lại disabled** |
| Blank text sent straight to the host's `save_review` (both a real and an unknown cue id) | Rejected: "Câu sửa không hợp lệ: không được để trống hoặc dài quá 2000 ký tự". No new folder; the original outputs were unchanged. |
| Valid edit restored; **Lưu & xử lý lại** clicked | Revision job queued with `revisionOf`, source language `zh`, output folder `… (sửa 1)` |
| Revision | COMPLETED in 39 s (the UI shows 0:41), `completed_partial_dubbing`; the queue shows both jobs with a "Bản sửa" tag |

The review has a single **Hoàn tác** (and Ctrl+Z); it has no redo button. "Reapply" here means typing the edit again after the undo.

## Output checks

The checks were made from disk after the run with the installed runtime's `ffprobe`/`ffmpeg` (local script, not committed). Folder names are relative to `<results>\DubFlow Output\douyinsaver.com-7697621631518166282 - vi\`.

| | Original `run-da5e01c7f23c` | Revision `run-da5e01c7f23c (sửa 1)` |
|---|---|---|
| Language | `source_language: zh`, `language_authority: requested` | same |
| ASR | `faster-whisper-1.2.1-bounded-word-gap-v4`, model binding `sha256:7c5de258…a1b2d881`, chunk language `zh` | same |
| Translation | `argos-pinned-routes-v1`, route zh→en→vi, `requested_source_language: zh`, argostranslate 1.11.0; packages `zh-en` 1.9 `62e7af5a…4a67da2d`, `en-vi` 1.9 `86957101…a567dd16` | same, plus `reviewed_overrides` `sha256:9166f52f…ec5d9fb` |
| Cues | 124 | 124; the edit is in `translation_overrides.json` (cue `asr-213760-227840-f3db7ac59699`) and on line 11 of `captions_vi.srt` |
| Manifest artifacts | 19/19 present inside the folder and matching size and SHA-256 | 19/19 |
| QC | `passed`, 21 warnings | `passed`, 22 warnings |
| `final_vi.mp4` | H.264 1024×576 + AAC 48 kHz stereo, 246.133 s; a full `ffmpeg -xerror` decode reports no error | same streams and duration; full decode clean |
| `job_manifest.json` SHA-256 | `afdd54391be01f49fe3ef7ea3a3035b0206683379cba4a0304923311f5a739d0` | `01758742cc403c34319feaa4420a1eb93d51cbb70c0d56d47345417946a43029` |

The original's five validated files had the same SHA-256 before the review, after the revision, and again after the harness run below:

| File | SHA-256 |
|---|---|
| `final_vi.mp4` | `c69fe33611d9588a9b3758635a6b76df758149b5479ed2fe2b5cb013415970c6` |
| `captions_vi.srt` | `7e11b6fd52cdbf018bd24955cf954d73f67b7c641d3abafb441154491ca3a572` |
| `captions_vi.ass` | `3496d5394ddcca49ee734166cc803aa91e94005460f24df0e31d18a14891b975` |
| `job_manifest.json` | `afdd54391be01f49fe3ef7ea3a3035b0206683379cba4a0304923311f5a739d0` |
| `qc_report.json` | `61a0e6f4a5da6c0985ce6d0f045c5a895aa16f69b786e3589c4e2f5fad3da4f1` |

The revision's `revision_of.json` (schema 1) names the original folder and `original_manifest_sha256: sha256:afdd5439…5a739d0`, the original manifest's actual hash. The title folder held exactly the original and `(sửa 1)`.

## Deterministic checks on the same checkout

| Command | Result |
|---|---|
| `npm ci --prefix apps/desktop` | 29 packages, 0 vulnerabilities |
| `npm test --prefix apps/desktop` | all 27 suites passed, including "desktop Chinese review and revision flow tests passed" |
| `npm run build --prefix apps/desktop` | OK |
| `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml --locked` | 99 passed, 0 failed, 2 ignored |
| Supervisor `cargo test` (app / state, `--locked`, release flags) | 74 passed, 2 ignored / 20 passed |

The ignored tests are not part of this flow:
- Host: `real_link_downloads_through_the_supervisor_service` and `real_session_save_reaches_the_protected_store` need `DUBFLOW_DEV_ROOT` and live sites.
- Supervisor: the two `execution_lock` helpers run inside child processes of other tests.

## Smoke harness

`python -B -m scripts.release.chinese_desktop_review_smoke --source <video> --install-root <isolated install> --work-root <empty folder> --report <report> --tts-voice-id vi-mai-anh-vieneu3-v1`, using the installed runtime, 2026-11-03 18:34:08–18:38:11 UTC:
- **Verdict: pass, 31/31 checks.** Runtime `1.1.50-main.99edc85`, source SHA `99edc85…`.
- Production took 198.5 s and the revision 42.6 s, both `completed_partial_dubbing`.
- `save_review_returned_same_folder: false`.

As its report says, the harness replays the host file contract (`gui: NOT_RUN`). It supports the GUI run above; it does not replace it.

## Findings

- No product defect was found.
- Quality note, not an acceptance failure: with the default voice `vi-truc-ly-vieneu3-v1`, 4 dubbing segments (3 s, 1.9 % of speech) kept the original audio. On 1.0.0 this was 45 of 124 cues. With `vi-mai-anh-vieneu3-v1`, the harness run had 0 failed cues and 20 `TTS_INTERCUE_GAP_USED` warnings.

## Limits

- **Build.** The install is a local, unsigned candidate built from exact Main, not a published or signed stable release. Its runtime folder comes from v1.1.49's published ZIP, as described above.
- **Install path.** It was installed with `bootstrap.py`, not the one-click `setup.exe`.
- **Input.** The drop was the window's native drop event, sent through Tauri's event plugin, not an OS mouse drag. Clicks and typing went through the page's DOM, not physical input.
- **Not run.** Hachimi, multi-voice, background separation, long-form, batch and GPU were not part of this run; they remain under #175.
