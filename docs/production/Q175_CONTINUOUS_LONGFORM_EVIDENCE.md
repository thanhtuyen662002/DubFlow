# #175: one continuous 2–6 h recording (#291)

**Result:** one real 2 h 52 min recording finished on the installed, signed **DubFlow 1.1.6** with the production CPU profile. It ran with dubbing (zh → vi) and with the background music kept.

- **Outcome:** `COMPLETED` (`completed_partial_dubbing`), QC `passed`.
- **Output:** the video's duration matches the source, it decodes without errors, and 1.0% of speech was left undubbed.
- **One interruption:** the PC was shut down from the Start menu partway through. The job resumed from its checkpoints only after a manual change to the retry state of this test install. This is described under [Interruption and manual intervention](#interruption-and-manual-intervention).

This is evidence only. It does not close #175, and it does not prove a 500-item batch, sleep/power-loss behaviour, disk-pressure behaviour or GPU behaviour.

## Runtime
| Item | Value |
|---|---|
| Build | Installed, signed DubFlow **1.1.6** stable, release source `07526787bba5cbd983d2c3b37e77fc6975531fa0`. This is the binary that ran; it was **not** built from current main. |
| Current main at write-up | `8ff58dc734f93b96658c5de34184ff48721ffc93`. It adds #313 (link retry) and #315 (dubbing progress) since 1.1.6, and does not change the pipeline's outputs. |
| Install | A copy of the signed 1.1.6 install in a test folder, with the release manifest unchanged. The worker runs from the bundled runtime. |
| Path | Desktop UI: drop the file into the queue, select it, then set **Lồng tiếng** on, **Ngôn ngữ gốc** = Chinese and **Giữ nhạc nền** on, and leave subtitles burned in. Then press **Xử lý tất cả**. The UI was driven over the WebView2 DevTools protocol, so it clicked the same controls a user clicks. |
| Profile | `cpu-local-file-b2` |
| ASR | `faster-whisper-1.2.1-bounded-word-gap-v4`, model binding `sha256:973199d52d093f104b88cd893167f5ede19edfc18034b4610caba83abd29a5b3`, 87 chunks, uncalibrated confidence |
| Translation | `argostranslate-1.11.0` (`argos-pinned-routes-v1`, CTranslate2 4.8.2, int8, beam 4), profile `d3567dd8c92bf82c1fbb31f4057bb007dc22f09754d6d8f19f150946de6f01bd` |
| TTS | `dubflow-production-tts` 3.5.0, `vieneu-v3-turbo-onnx-v1`. Model `vieneu-v3-turbo-fp32` `61b85e3-moss-ceff0d0-sdk3.8.3` (`sha256:30163bab69ff85db094314385a56510129293f600fc130e53d1c9b3b4b276113`). Runtime `vieneu-3.8.3+sea-g2p-0.9.1+onnxruntime-1.30.0+tokenizers-0.23.2+numpy-2.2.6`. Voice `vi-truc-ly-vieneu3-v1` 1.0.0 (`sha256:379d234f28bd0bf5af0aabcfb403b5e22703ecbcb2a5fb134f2f644978d6a3ed`) |
| Background | `spleeter-2stems-onnx-v1`, profile `sha256:943a49c5b57d1e14fc3758aac04812261377b3130db681835edde7e2b9fe7277` |
| Mix | `dubflow-aud-0` 2.0.1, `pcm-stream-duck-v1`, `owned-python/numpy-2.2.6` |
| Machine | Windows 11 Home (build 26300), Intel Core i7-12700H (14 cores, 20 threads), 39.7 GB RAM, Intel Iris Xe only (no NVIDIA GPU). The pipeline runs on the CPU only. |

## Source
| Item | Value |
|---|---|
| Recording | An owner-supplied Chinese-language upload from Bilibili. The owner fetched it with DubFlow's link download before the test, not as part of the timed run. The file is kept outside Git. Licensing is as stated by the owner and was not checked independently. |
| SHA-256 | `179bd786ad716ff34db4f55bdc9f2945064e2a4eac77bbc3bd76a7cbacf77380`, 373,426,151 bytes |
| Duration | 10,346.687667 s container (**2 h 52 min 27 s**). Canonical video timeline is 0–10,346,680 ticks at 1/1000. |
| Streams | H.264 852×480, 25 fps, time base 1/16000. AAC 48 kHz stereo, time base 1/48000. 288.7 kb/s overall. |
| Continuity | One file with one timeline. All 258,667 video packets step by exactly 640 ticks (1/25 s), with no gaps, jumps or edit-list breaks. The content itself was not reviewed, so it can only be called one continuous timeline, not proven to be one camera take. It is not a concatenation, loop or synthetic fixture made by this project. |

## Run
| Item | Value |
|---|---|
| Run 1 | 2026-10-27 06:12:59Z – about 10:19:10Z, **about 4 h 06 min**. It covered ASR (done between 07:53Z and 08:03Z), translation and subtitles (by 08:23Z), separation (557 s) and dubbing from 08:31Z. It ended at phrase 1,228 of 1,949 when the PC was shut down. |
| Interruption | The PC shut down at 10:19Z and booted at 10:50Z. The job was resumed at 11:26Z. See below. |
| Run 2 | 2026-10-27 11:26:11Z – 12:08:38Z, **42 min**. It reused ASR, translation and separation from their checkpoints and the 1,228 synthesized phrases. It finished dubbing, then ran the mix, render and QC. |
| Processing time | **about 4 h 48 min** for 2 h 52 min of media, about **1.7× real time**. Total elapsed time was 5 h 55 min, including the 67 min between the shutdown and the resume. |
| Other load | During part of run 1, an unrelated user FFmpeg encode was running on the same CPU. Dubbing slowed from about 11 to about 8.5 phrases per minute while it ran. |
| Terminal status | `COMPLETED`, reason `completed_partial_dubbing`: "Đã xuất video; lồng tiếng chưa đầy đủ. Audio gốc được giữ ở các đoạn lỗi." |

## Output
| Item | Value |
|---|---|
| Video | `final_vi.mp4`, 506,096,433 bytes, SHA-256 `75bf98dbe278d8b7ee24f0cf437de3db9f59f0f5186ddbfcd5079e2eaa0fee17` |
| Probe | H.264 852×480 and AAC 48 kHz stereo. Video, audio and container are each **10,346.680 s**, against 10,346.688 s for the source container. The canonical timelines are equal (10,346,680 ticks). |
| Playability | Full decode `ffmpeg -v error -xerror -i final_vi.mp4 -f null -`: exit 0, **0 errors**, 35 s |
| QC | `qc_report.json` status `passed`, `downgrade: true` (partial dubbing), 564 warnings, validated 12:08:00Z, `sha256:8909372f5993f6053079f03d0a31752b1aa94b8431f3c419a136a48cad5425c7` |
| Captions | SRT `sha256:e94d763f2eafb57ad8ac7326ff4c9d58c36884c7453a779d695521957e689170`, ASS `sha256:52b34f360e08a93d3a8bd4938949ad41face9eb3527285e75d2e0e8f52f83f22` |
| Final mix | `sha256:62e0f71a872319c0d95890c4b4581c6809935277dc02636af2f9e053f766729a` |
| Background stem | `sha256:b37ee4089a8f8663ed0cadc8f06600efc7f3f4b8b68722a93f0da75141765627` |

## Coverage
| Item | Value |
|---|---|
| Cues | 6,106 recognized cues, translated and subtitled. They were merged into **1,949** dubbing phrases. |
| Undubbed | **31 phrases (91 cues)**, all `DURATION_FIT_REQUIRED`: the Vietnamese speech did not fit even with every bounded fit tier. Each of those spans keeps the source audio (`SEPARATION_SOURCE_RESTORED`). |
| Undubbed ratio | **96 s of 9,549 s of speech = 1.00%** (0.92% of the video) |
| Fitting | 755 phrases were speed-adjusted, 81 used `TTS_FAST_FIT`, and 360 used an unverified gap between cues (`TTS_INTERCUE_GAP_USED`). |
| Background separation | `spleeter-2stems`, 557 s. Source RMS −23.8 dB, removed −23.8 dB, background bed RMS −45.6 dB. |

## Memory and disk
| Item | Value |
|---|---|
| Peak working set | **3,039 MB** total across the install's processes, at 11:56:21Z. The largest single process was the VieNeu TTS worker, at **2,887 MB**. Sampled every 30 s from 09:50Z to 12:11Z, which covers dubbing, mix, render and QC. |
| Not measured | Peak memory for ASR, translation and separation (06:12Z–09:50Z). The sampler matched processes by install path and missed the workers, which run with a `\\?\` prefix, so it reported only about 150 MB. That data was discarded and is not used. The earlier 2 h concatenated run peaked at 2,839 MiB (`Q175_LOCAL_QUALIFICATION.md`). |
| Disk | The job's output folder holds **33.74 GB** in 16,689 files: work folder 25.84 GB, `editable/` 7.41 GB, and final video plus captions about 0.5 GB. This is about 90× the source. Each 1.85 GB PCM WAV (source audio, background stem, dialogue stem, final mix) is stored 2–3 times as separate copies, not links, and the mix keeps a 3.70 GB `combined.pcm`. By this measure a 4 h 38 min source would need about 55 GB free. |

## Interruption and manual intervention
**What happened**

1. **10:19:10Z:** the PC was shut down from the Start menu. Windows logged event 1074 from `StartMenuExperienceHost`.
2. **During the shutdown:** the worker was killed twice. The supervisor counted each kill as a retryable `WORKER_EOF` and started another attempt each time. That used the whole stage budget and left the stage at `recovering` with attempt 3/3.
3. **After the reboot:** the app listed the job as interrupted ("Bị gián đoạn … bấm Bắt đầu để tiếp tục từ chỗ dừng"). Pressing **Bắt đầu xử lý** failed at once with `SUPERVISOR_JOB_ERROR: durable state error: invalid state input: stage retry budget is exhausted`, and the job became `FAILED`.
4. **Retry in the app:** the desktop's retry queues a new job ID with a new work folder, so it would not have reused about 1 h 45 min of ASR or the 1,228 phrases.

**Defect:** in 1.1.6, being interrupted by an OS shutdown uses up the retry budget, so the advertised resume does not work. This is being fixed separately. This evidence does not count that path as passing.

**What was changed, by decision of the repository owner and only in the test install**

1. **Backed up** the install's `jobs.sqlite3`.
2. **Reset the durable state:**
   - job `failed` → `recovering`;
   - stage attempt 3 → 0;
   - retry condition set to `manual-reset-after-os-shutdown`;
   - last errors cleared.
3. **Set the host status file** for the job back from `FAILED` to `RUNNING`, so the host reports it as interrupted.
4. **Set the queue entry** in the desktop's local storage back to `QUEUED`/`interrupted`.
5. **Pressed Bắt đầu xử lý** again with the same job ID.

**What was not changed:** no artifact, checkpoint, cached phrase, option, model or source file was modified, and no product code was changed.

**Result:** the job resumed as attempt 1/3, went straight to dubbing (70%) and reused every checkpoint. The first new phrase was written about 2.5 min later, mostly spent loading the voice. Reuse is confirmed by:
- the same B2 generation folder;
- the phrase count going from 1,228 to 1,232;
- the next phrase falling at 1:49:17 of the video.

**What this shows:**
- Checkpoints and the TTS cache survive a real OS shutdown.
- The durable retry budget does not survive one.
- The final output was produced by the unmodified 1.1.6 pipeline, and its wall time spans both runs.

## Not covered
- **Retry budget across a shutdown:** resuming after a shutdown without manual help is not shown, because 1.1.6 fails that path.
- **ASR-phase peak memory:** see [Memory and disk](#memory-and-disk).
- **Other recordings:** two other owner-supplied recordings, 3 h 57 min and 4 h 38 min, were removed from this run. They are to be run on another machine with an NVIDIA GPU, and this document makes no claim about them.
- **Out of scope for #291:** sleep/resume, power loss, disk-pressure exhaustion, removable or NAS loss, GPU behaviour, 500-item batches and human listening quality.
