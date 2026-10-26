# #175 local qualification evidence (2026-10-26)

Evidence gathered towards #175 on the installed, signed **DubFlow 1.1.0** (stable, source `15e1b2d836725a3021bdc75c9b9306d6dc28a4df`) on a Windows 11 workstation (CPU only, no NVIDIA GPU). The media are owner-provided or project test clips kept outside Git; none are committed. This is not full #175 acceptance; see "Not run".

Every job used the desktop host's exact `start_job` arguments: Chinese source, Argos, dubbing with `vi-mai-anh-vieneu3-v1`, one job at a time like **Xử lý tất cả**. Peak memory is the summed working set of the supervisor, worker, FFmpeg and FFprobe processes, sampled every 2 s.

## Long form (2 h 03 min)
| Item | Value |
|---|---|
| Source | the owner-provided 246 s Douyin video concatenated 30× (stream copy): 7384.7 s, 972 MB, SHA-256 `604bf1be67ffeed58d00b4b7e0796d4d9c8f5c66a69f8ad382df42e5f8bdfb1e`. Real speech, repeated content. |
| Run | 2026-10-26 02:49:08Z – 04:22:39Z, **5610 s (0.76× real time)** |
| Result | COMPLETED (`completed_partial_dubbing`), QC `passed`, output 7384.6 s with video and audio (source 7384.7 s) |
| Dubbing | 3797 cues, **79 undubbed (2.1 %)** |
| Peak memory | **2839 MiB**, bounded for a two-hour source |

## Batch (100 items)
| Item | Value |
|---|---|
| Inputs | 96 twenty-second clips cut from real speech videos (Douyin, Twitter/zh, two speakers, speech+music, elephants narration, cooking EN/portrait, hard-subbed), plus 4 deliberately bad files: two truncated MP4s, one without audio and one empty |
| Run | 2026-10-26 02:10:43Z – 02:49:08Z, **38.3 min**, median 21.7 s per job |
| Completed | 93, all with QC `passed` and output durations matching their sources (±0.5 s). 18 of 892 cues undubbed (2 %). |
| Failures | 7 failed in under 2 s each and did **not** affect later jobs: the 4 bad inputs (`MEDIA_PROBE_FAILED` ×3, `AUDIO_STREAM_MISSING` ×1) and 3 elephant-narration clips at 100–160 s that contain no speech (`ASR_EMPTY`; the full-length run has no cue between 96 s and 168 s) |
| Defect found | 1 clip fell back to subtitles only. Whisper looped "1.5.5.5…" hundreds of times, translation copied it and the native TTS exited. Fixed in #286 (repetition loops collapsed before translation and TTS). |
| Peak memory | **2142 MiB** |

## Batch (500 items, installed 1.1.1)
| Item | Value |
|---|---|
| Runtime | installed, signed **DubFlow 1.1.1** (source `9cf32309cf828619a8c2330c8e9ad796aa271b26`), with the same job arguments as above |
| Inputs | 475 twenty-second clips at many offsets (Douyin 226, Twitter/zh 101, elephants narration 81, speech+music 21, two speakers 18, cooking EN/portrait and hard-subbed 3, Douyin at half-second offsets 25), plus 25 bad files: 10 truncated, 5 empty, 5 without audio and 5 random-byte "MP4"s |
| Run | 2026-10-26 05:52:16Z – 08:55:19Z, **3 h 03 min** sequential, median 22.7 s per job, longest 113 s |
| Completed | **466**: QC `passed` for all, every output has video and audio, all durations match their sources (±0.5 s). 92 of 3691 cues undubbed (**2.5 %**). |
| Bad inputs | all 25 failed fast (1.5–2 s) with typed reasons: `MEDIA_PROBE_FAILED` ×20, `AUDIO_STREAM_MISSING` ×5. None affected another job. |
| Other failures | 9 × `ASR_EMPTY`, none of them a product defect. Five Douyin clips start at 221–225 s, after the video's last speech ends at 219.9 s. Four elephant clips start at 28–31 s and hold only the 2–5 s English tail of a sentence; the run forced Chinese, and the narration resumes at 52 s. |
| Peak memory | **3512 MiB** summed over one job's process tree. Every job starts fresh supervisor and worker processes, so memory cannot accumulate across the batch. |

## CI lanes on exact main
- Release / Soak run [40257299667](https://github.com/thanhtuyen662002/DubFlow/actions/runs/40257299667) on `46b15af7076ff087b2a4c9488d2d74218f2b43c9`: passed **Release/Soak** (bounded release rehearsal, chaos assertions, interrupted source acquisition and atomic publication, generated-PCM hard process exit and recovery) and **Windows source session boundary**.
- Each Windows Release run qualifies a clean runner: install, auto-launch, staged and installed pipeline smokes, synthetic long form, batch failure isolation, corrupt media/state, hard-killed supervisor recovery, TTS checkpoint recovery, voice-version isolation and source clock. Signed stable 1.1.0 was run [40225945746](https://github.com/thanhtuyen662002/DubFlow/actions/runs/40225945746).
- Desktop GUI flow (#271): see `CHINESE_DESKTOP_REVIEW_EVIDENCE.md`.

## Not run
- **Physical interruptions:** reboot, sleep/resume, power loss, disk-pressure exhaustion, and removable/NAS loss. They need manual or hardware interventions; only the process hard-kill/recovery lanes above are automated.
- **GPU OOM / CPU fallback.** There is no NVIDIA GPU here, and the shipped profile is CPU-only by design.
- **Real 2–6 h single recordings.** The long-form source is real speech concatenated, not one continuous recording.
