# Q175 — Real 500-item CPU batch evidence

**Status: RUN — 500 queue entries were executed through the real desktop UI. One acceptance deviation is listed under [Deviations](#deviations).**

- Parent gate: #175
- Leaf task: #290
- Main at assignment: `462c34ad8885e154973b6057c7ea2feb4a3103c6`
- Stable release: v1.1.1, binary source `9cf32309cf828619a8c2330c8e9ad796aa271b26`

## Tested code

The batch ran in two segments because the machine lost power partway through.

| Segment | Build | Source SHA |
|---|---|---|
| A | Installed, signed stable **DubFlow 1.1.1** | `9cf32309cf828619a8c2330c8e9ad796aa271b26` |
| B | Unpublished local build (`0.1.0-dev`), assembled from the repository | Desktop host `a706b4a3`; supervisor, worker and engine identical to `9cf3230` |

- Segment B's host had the power-loss recovery later shipped in v1.1.2 (`ae876f6`, #295/#296).
  - It started on a host built from `b7d81f8` and moved to one built from `a706b4a` within minutes.
  - `git diff 9cf3230 a706b4a` touches only `apps/desktop` and docs, so every job ran the released 1.1.1 pipeline code.
- Segment B used an unsigned runtime: the same pinned Python packages and the shipped LGPL FFmpeg, not the signed bundle.

## Run conditions

- **Runtime profile:**
  - production CPU path on an Intel Core i7-12700H with 40 GB RAM, Windows 11;
  - no GPU, and other work ran on the same machine during the run.
- **Models** (one data root for both segments):
  - ASR `faster-whisper-small`;
  - Argos zh→vi translation (the default; HachimiMT was not selected);
  - TTS voice `vi-mai-anh-vieneu3-v1` from the installed VieNeu pack.
- **Job options:** every entry used Chinese source (`zh`), dubbing with that voice, and burned-in subtitles. These are the options a user sets once; they were set through the app's saved defaults.
- **Operator:** a Claude agent drove the operator's own desktop session, a real interactive Windows login and not an isolated CI runner. The installed app window was driven over the WebView DevTools protocol:
  1. Dropped the 500 files onto the window, using the same `tauri://drag-drop` event as a mouse drop, in ten drops of 50.
  2. Clicked **Xử lý tất cả** once.
  3. Polled the queue that the UI persists, until every entry was terminal.

  The app did all the queueing, ordering, starting and status handling. The driver only clicked and read.

### Media set

The set is a local, authorized test set with no customer data. No media is committed.

| Kind | Count | Detail |
|---|---:|---|
| Valid clips | 475 | 20-second cuts at many offsets from real speech videos: Douyin 226, Twitter/zh 101, narrated elephant documentary 81, speech over music 21, two speakers 18, cooking EN/portrait and hard-subbed 3, Douyin at half-second offsets 25 |
| Controlled invalid files | 25 | 10 truncated MP4s, 5 empty files, 5 MP4s without an audio stream, 5 random-byte ".mp4" files |

- There were **496 unique source hashes** across the 500 entries. The 5 empty files share one hash; every valid clip is distinct.
- All 500 entries were executed as separate jobs (500 queue rows and 500 supervisor job records). None were deduplicated or skipped.

## Timeline (UTC, 2026-10-26)

| Time | Event |
|---|---|
| 08:58:11 | Segment A: 500 entries queued; **Xử lý tất cả** clicked on installed 1.1.1 |
| 10:30:11 | Last driver sample before the power cut: 155 completed, 25 failed, 1 running, 319 waiting |
| ~10:31 | **Real power loss.** The machine shut down while `clip-157` was mid-pipeline. The operator had pressed **Dừng hàng đợi** shortly before, so the queue was stopping after the running video. |
| 11:30:10 | Machine back. The queue, results folder and saved choices carried over (#295). `clip-157` showed as interrupted, and **Xử lý tất cả** (319 waiting) was clicked. |
| 11:43:10 | The driver re-attached to the already-running queue without touching it. Two earlier attachments ended when the driver itself crashed, not the app. |
| 14:27:34 | Every entry terminal. Segment B lasted 9848 s (2 h 44 m). |
| later | `clip-157` was run again with the UI's **Xử lý phiên bản mới**; it completed in 46 s |

The total wall time was about 5 h 29 min across both segments, excluding the 1 h outage.

- Segment A's measured span is 1 h 32 min up to its last sample.
- The summed run time of the 466 successful jobs is 4.25 h; the median job took 29 s.

## Batch outcome

| Metric | Observed value |
|---|---|
| Queue entries submitted | 500 |
| Entries actually executed (dedup skips excluded) | 500 (+1 UI re-run of `clip-157`) |
| Valid jobs with a finished, QC-passed video | **466 / 475**. 408 `completed_with_warnings` and 58 `completed_partial_dubbing`; the latter keep the original audio where a phrase could not be dubbed. |
| Valid jobs ending FAILED | 9 × `ASR_EMPTY`, each 3–6 s, on clips with no Chinese speech (details below) |
| Controlled invalid jobs | **25 / 25 FAILED fast** (1.3–1.5 s each): 20 × `MEDIA_PROBE_FAILED`, 5 × `AUDIO_STREAM_MISSING` |
| Other failures / codes | `clip-157`: `JOB_ID_CONFLICT` after the power loss (see below); it completed when re-run |
| Outputs playable and duration-checked | **466 / 466**: each has a video and an audio stream, each fully decodes with no FFmpeg errors, and each output is within 0.04 s of its source duration (median 0.00 s) |
| Output/hash/QC verification | **466 / 466** have `qc_report.json` status `passed` and a recorded SHA-256 (all distinct; listed below) |
| Peak working set | **3516 MiB**: the summed working set of `DubFlow.exe`, `dubflow-supervisor.exe`, `python.exe`, `ffmpeg.exe` and `ffprobe.exe`, sampled every 5 s in segment B. Segment A peaked at 2141 MiB. |
| Peak scratch/output disk usage | **18.6 GiB** at the end of the run for 466 result folders, of which 13.1 GiB is the per-job `.dubflow-work` checkpoints kept for review |
| Total wall time | about 5 h 29 min of processing (above) |

## Failure isolation and provenance

- **Invalid inputs.** Every one of the 25 invalid inputs failed in about 1.4 s with a specific code and a Vietnamese explanation in the queue. In each case **Xử lý tất cả** went on to the next entry. No invalid input stalled the queue, produced a partial output, or affected a sibling.
- **ASR_EMPTY (9 valid clips).** These failures are correct, not errors in the pipeline:
  - `clip-222`…`226` are Douyin cuts taken after the last speech in the source video;
  - `clip-395`…`398` are elephant-documentary cuts whose only speech is short English fragments, while the job forced Chinese.

  Each failed in 3–6 s with the "no speech found" hint.
- **Power loss (`clip-157`).**
  1. The job was cut off under 1.1.1. Its supervisor record remains `running`, and its partial checkpoints are kept.
  2. After restart the host reported the job as interrupted, instead of "Đang xử lý" forever (#295).
  3. The run was started from a different build (dev host), so the supervisor refused to resume it with `JOB_ID_CONFLICT` rather than mix checkpoints across builds.
  4. The host recorded FAILED with that reason, and the queue moved on.
  5. A fresh run through the UI's new-version action then completed (`completed_partial_dubbing`).

  No other job was touched.
- **Duplicate start (`clip-422`), fixed in #298/#299.**
  1. The batch pump started `clip-422` a second time, about 1 s after its first start. A poll had read the supervisor's initial `QUEUED` status, so the queue briefly saw no active job.
  2. The second supervisor was refused with `JOB_ALREADY_RUNNING`, and the host recorded that refusal as FAILED.
  3. The first supervisor **completed the job normally** 25 s later: status `COMPLETED`, outputs present and verified below. The queue had already stopped polling, so it kept showing FAILED.

  The execution lock prevented any double processing or output corruption. After the fix, a started job ignores the supervisor's initial `QUEUED`, and a `JOB_ALREADY_RUNNING` refusal never fails the job.
- **Results folder.** At 14:09Z the app's results-folder setting changed in the running window. No driver action was involved, so it was most likely the operator.
  - The 43 entries started after that (`clip-433` onward) wrote to the new folder; the earlier 421 stayed in the first.
  - No output was moved or lost. The per-item check below reads each output from where the app recorded it.
- **Data integrity:**
  - no data loss, cross-job artifact use or cleanup failure was observed;
  - all 466 output hashes are distinct;
  - every output folder holds `final_vi.mp4`, `captions_vi.srt`/`.ass`, `job_manifest.json`, `qc_report.json` and `editable/`.

## Deviations

- **Valid/invalid split.** The task asked for at least 480 valid entries and at least 4 invalid ones. This batch used **475 valid entries and 25 controlled invalid ones**: more failure-isolation coverage, 5 fewer valid entries. This is recorded as is rather than rerunning 500 jobs.
- **Build.** Segment B ran on an unpublished local build. Its host is the v1.1.2 power-loss fix, and its pipeline is unchanged from 1.1.1. Segment A ran on installed, signed 1.1.1.

## Required CI on final PR HEAD

The checks are listed on the PR. They are documentation checks only and are not the 500-item evidence above.

## Limits

This leaf does not accept #175. It does not cover:
- a continuous 2–6 h recording (#291);
- sleep, disk-full, removable or NAS loss;
- GPU behavior.

It does cover one real power loss in the middle of the batch, as described above.

## Per-item results

- **Source s / Output s:** probed durations.
- **Decodes:** a full `ffmpeg -v error -f null` decode with no errors.
- **QC:** `qc_report.json` status.

For `clip-157`, the output is from the UI re-run. For `clip-422`, the queue showed FAILED (#298), but the job completed and the output is the one it recorded.

| Entry | Source s | Queue result | Output s | Decodes | QC | final_vi.mp4 SHA-256 |
|---|---:|---|---:|---|---|---|
| bad-01-truncated | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-02-truncated | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-03-truncated | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-04-truncated | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-05-truncated | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-06-truncated | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-07-truncated | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-08-truncated | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-09-truncated | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-10-truncated | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-11-empty | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-12-empty | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-13-empty | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-14-empty | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-15-empty | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-16-noaudio | 20.167 | FAILED `AUDIO_STREAM_MISSING` | — | — | — | — |
| bad-17-noaudio | 20.067 | FAILED `AUDIO_STREAM_MISSING` | — | — | — | — |
| bad-18-noaudio | 20.1 | FAILED `AUDIO_STREAM_MISSING` | — | — | — | — |
| bad-19-noaudio | 20.167 | FAILED `AUDIO_STREAM_MISSING` | — | — | — | — |
| bad-20-noaudio | 20.167 | FAILED `AUDIO_STREAM_MISSING` | — | — | — | — |
| bad-21-garbage | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-22-garbage | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-23-garbage | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-24-garbage | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| bad-25-garbage | — | FAILED `MEDIA_PROBE_FAILED` | — | — | — | — |
| clip-001 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `344da32bb2dc7f0d8c4e0744a4bad74173ac883aeb7229e12fe7f57d663a92aa` |
| clip-002 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `98253ca9039061672d9ed41af2c4edc8d2f7e622d134a36ae5cdc79166db63cc` |
| clip-003 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `6af2f6996648815f55ae197ca2f41149d8435568b9f4453067a3751de74af797` |
| clip-004 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `7cd7f81b7af7e594e35716053da129e608b36964653956dea6064dc067d4637e` |
| clip-005 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `64d0680f1fcc01c1c7038af117135d091880c42cfd3ef90299b9646fd3fe2b90` |
| clip-006 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `f24561cdf06a29f1a2a80944922f0e77bbee8f7c9ddb70e16c2945f34e19a4c3` |
| clip-007 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `ba3f2b87ec6b84244b222db879498ddc039732d3d5635a5e3e4144070e2cd2d8` |
| clip-008 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `df2dd0afbbbefd10b65a15fc054ba24742c8771200af94ebe2501e77d80bb6a8` |
| clip-009 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `3db5a7a787bb116edd33be4dfc9adc51551c2fcf1beefa6003cf36ec5c04fe44` |
| clip-010 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `7b15b98b16b915e665407959bb0af45c71550a951d1b1e2f7bfc2f2cfc6bd2b4` |
| clip-011 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `94245e9f85ec644405959d9d6956e0eda16c26bf5a48d49c016ab1ea333103de` |
| clip-012 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `06ca6970f4657b6d99943944c4cfb73d6260e91e168fecfccd83838bc409ca3a` |
| clip-013 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `06fb024829fca3e98eb799045210c63f93a7661f7a3b548b2070e483bec93fba` |
| clip-014 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `f5982d0a3873525d54e323b8206e8b5af60df90774ae742686a0d5071a404d7a` |
| clip-015 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `89b81f04ec18bbcb82f8e56ae8cc8f3be7674b7ab53ea0351b3dc9e23235fe7d` |
| clip-016 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `9fb81580afb347a0a20004f040bb07b537d1706687261059c6f034b45dabf37f` |
| clip-017 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `53bc7491c5943c83d5a2fd4b0671ec1dc333b8a721311ca40b553dd19e6a853b` |
| clip-018 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `214ae2d3fca13b0b6f583ad6bad50020a9cfe5e91f8e09ffc24e91938be60b9c` |
| clip-019 | 20.133 | completed_partial_dubbing | 20.133 | yes | passed | `e7f3659a5f27f3ec993d5a7cb5e8dd0bfbe3d95c312bac464ae4d274ffe996a7` |
| clip-020 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `d115578a4e4f8bc5f78b4942ad333e60c7f72c603ccc171882a86ec4bf89ce16` |
| clip-021 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `639c5e28d5d9cb7fd9a2c10c1698414934688fd3a9bd2de2a0617a7ea00ed0fd` |
| clip-022 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `1b7462ff900b58eec119d0433217efa042c1dc1f758daa90d195629856b42ab3` |
| clip-023 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `7846f7d0a926c4363c40617079d1347f41eff095402e5b1e145ba1d19c862fe0` |
| clip-024 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `d0ffe52ba7266eeb142f3a414d43690706708cf13521a5224cae14b749d6faf7` |
| clip-025 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `051d75d0bac200644968d871c69732df27b0d83c19fb2f7fa8fb345ff2686c52` |
| clip-026 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `504341affb7d158685a386e426b9543dda73ac5ea665b30b1ed1f7b3fabdd571` |
| clip-027 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `1cdf00a34c8b3d9e3a56f93b8bf3e1a28e46e63469c45818e7a8fe7a50152c12` |
| clip-028 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `119c0208d91bf2a8633cf58d90576824d5bc1c768de8e43e579fc4a76e33eac6` |
| clip-029 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `845e6f7c6256462eabda54ecce88bbc5a23be4400e558cd89e3343246735079b` |
| clip-030 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `5a94ef076ddaff952357f0c0b0c2f72317508a9dc96f6526b751b05740dbcef8` |
| clip-031 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `2f8549622049aa99ba14bc3bcdc93214a9e3b739191126dd25a83dce71e683f3` |
| clip-032 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `358a0f05f835b5c26c5d7b4f2386333e1a6d54487641ea3e3a267a5b4e95b004` |
| clip-033 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `353b82480f7f4be35ee4e9726539cb59044b28fa08b827ec82b57ab81c86cd43` |
| clip-034 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `ab6d76210fccbb98f040382653bc6ecd56d35ba5e5dded2b2559261821dad5d9` |
| clip-035 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `06f91b57a169c6b6440e7e26ba3dfd12897b7d2fab947fd21f090dcd6a203e46` |
| clip-036 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `fadd5ce08cb09e7a908728588074547fa7d1bcfdb031e4a302032eed8c2bab97` |
| clip-037 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `3e22cbdd4edf98d82e1e1ff97b9855e3a251c01322e797258e0e4477f91abd47` |
| clip-038 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `99750f874f7e11787e592998359e3fd9089884d22902577cf85eed2e1aa5cc3b` |
| clip-039 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `c7e63df9ae1b2b16f30012e7afc4775a6cd863c4a6c4d9796107633a0cd80e9a` |
| clip-040 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `0e66afec7c3c7d2681bfd38136e70174f500cedc59bab57b599173a76bc24ce6` |
| clip-041 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `80c2f775a372e6486d8444e589af012b3b8b5a36ed3f71c48edfd7b8fb2b19eb` |
| clip-042 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `f4ca4490fef3056d2c4f53bf9bac0aa317570889f73e60ffa00ffb2ce107b1ef` |
| clip-043 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `31a6b25ced94ebea5c2cd9d1964312f1b8d9c9221868dcb03dd5a49d73a3f3df` |
| clip-044 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `f13249333bac632e5514f3d7e98c3808899a1a1a75a9a4955a078e135d61fb8e` |
| clip-045 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `a52592a09165e29cde0b9b2952f4538401df3924bf49e62bf5d81909f3466b1d` |
| clip-046 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `71b740d214e0c6f17719ee2fa4dadff9721b2c4bbdf4a6eaefa0e5afb0a0b2df` |
| clip-047 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `bdaa446c3192802a1b713afc0994c2fbefdec46e57fbf742b1fb39b1b5f0a79c` |
| clip-048 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `e023493cdab01e551b676bbb4b281817e02e2f4bf8457a4cd7afc43328baf2ca` |
| clip-049 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `92e42785d4b90b602028c0acb37af4876fc90d7443720daf7f45e81fa84c1134` |
| clip-050 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `92e2b7f0fd96ccd8e49ba45ae124d323eb1c930528938afe14895de292d1e5c3` |
| clip-051 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `f4b5b4fb22216c6b0ed9b6947020ba9a0f1fd00a0a3c7e08e0d44a319b2a707a` |
| clip-052 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `ed6e3c58962e8db151242f2ab96560cab4c40eb666cc21f3d238b21f838d1a6f` |
| clip-053 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `c344367b394b7208234774f7a26f1d636e436f1d8a28d95de386b6887853d8fa` |
| clip-054 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `478b111f4174d25893c05300a6c29c247d35e6bcfe28ac55bfd14edcba979134` |
| clip-055 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `6b7ac6b5c25fcf0ae5ac7b3b343b6363ac03be98596c0292cf11909c8f6accd4` |
| clip-056 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `b87f5f57922bac9c9318d9da02036bbc88f990cb216af6480622e478ed2ce233` |
| clip-057 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `1f9dc3c156ca9718ed22a7c2163c3bdb51cb4ab1761b586b68071a914178b44c` |
| clip-058 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `07224ac8bf7edea6d69c999f16a4668acf972fa1d703ffa1aedb65fe065fed3f` |
| clip-059 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `f1a034fa9392df7323d3e5af6949676ee0cd5e037c6f898352ee6eec008b0e57` |
| clip-060 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `7b22e529f6e4abaf1e7d2196d1615a1e8275e64428943178d4dbb557857e68ac` |
| clip-061 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `f14b1110ad0d78a3f27a94d652f5143c3d3eca51e178fda26b3c2d163df2915e` |
| clip-062 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `0848a7adecfc1610328a95c5aa35009777de11310583c9443dd2ecfff674dad9` |
| clip-063 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `f7a6ccf20d24f68a998ebbac96dc42dd400c0330e89c48d71bcc138dffe183e7` |
| clip-064 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `14b933ce20ac0aaf1c820ffa5c419dcb54945d56cd6e7ea0db625ce8eab3fd15` |
| clip-065 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `3b41d1e408654fca1262061fb8b6c4f0d7c12082cc4a959b8378920aa8c19c65` |
| clip-066 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `0df89eb88a6f7831781b8ff6c3be58c99a0bb881f25cc42767b0dcbbf3ab4dab` |
| clip-067 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `6ef243265db280bd46efdd2becef117ac72f7ffc1f4390e1ad32c01ca56d35c9` |
| clip-068 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `17ae984862d9492c2e02a4e9ca32831b47c391be0cb4c473e51d80d6285a0a7c` |
| clip-069 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `a6cb8038a4907911b25000cb1ee1874290936ba85b5982454ec915d3b0480f4f` |
| clip-070 | 20.067 | completed_partial_dubbing | 20.067 | yes | passed | `974de285b532187be613e9754489c7fc130f06e0fd2a743359a9deb789b861d7` |
| clip-071 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `f525ce02382590adf27c2a5eea8733f8fffd9b6194ecb7509e76c311b2562aab` |
| clip-072 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `a1e35694d49c599c0470e3439e87105cb6b5622fff3b50ad2f784db8fc6f7562` |
| clip-073 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `4e294f23a7c3d16bfa7d7aa247d473b51410cc74e8e84a39e0cb3c2062fe14c4` |
| clip-074 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `5a97b00d82ef4ded16bf64737de52861717a93d58df24087548668c6e07ddccd` |
| clip-075 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `751e4c0757b6b9b50e096dce2f51b321b0516362a34f78dd2e8b644a7d3ea4b1` |
| clip-076 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `89ace5370a1ded996739e66b13aa52122f666619b20fbc487f27955dbab747a5` |
| clip-077 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `2a96cebd0c63594ad9cb4e88b9b06d56fc150da9d0c27aaa537188671642e36a` |
| clip-078 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `50fadcf9334f2ae3c1eac94b2a0bcbf4a50381cb82d275a411e6a2ed2b219b32` |
| clip-079 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `bc0111528bf6a1481ef7e402ecaab28d4d12940d4894a54f0e3d31bb0a1225ab` |
| clip-080 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `0bf98bcd9d6294e4c75b11ae91d95b7cefd3277cae1a3bd7093356fb77ad4eae` |
| clip-081 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `92c764e9decc1e856951e4443b37bf6f4948d9f3a0703fa44537f6c7808fd4be` |
| clip-082 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `5b643402329babf2c01327c6a0a4b79c8cb2eaf0eb3c4359cf41d804d7eadd07` |
| clip-083 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `5c8c0e91dae60a713ee08b773ff481072e9ba7ed647fe730d34012d6418b268a` |
| clip-084 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `f712118f9be88659434ed37c207ebc30a11de437cd43b8862013ac495c46e04a` |
| clip-085 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `8f59ba3d3b94bd0e87834b9967759fdf2f000e00b9f3895e0e9b1bd8716a9fa6` |
| clip-086 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `270221ec585a994353407cd78fb9b8fe1139da824c3c959c1f5f3ca2d70d7d7d` |
| clip-087 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `ce091b489a238664312c26a0f8eb31ce3a7ee96f3d7e47a3a93496bdec490c79` |
| clip-088 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `c4f68f228149da9313a844840776f58a0c3304186df23055c4ee12ff5b2980b7` |
| clip-089 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `119d1e3511d1fe332175f9810e32cdf85a83b05b882de69f0ddb7cf8b381d187` |
| clip-090 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `d41657dd9e65b3a3a6d5907aee97b439b254b8f55337e892ac957839851a7b68` |
| clip-091 | 20.067 | completed_partial_dubbing | 20.067 | yes | passed | `85a89cb7df3690e7b9afd59d9291fd2a56feef84405a6c076ec672b3b4783342` |
| clip-092 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `5710a51f18dc18ce04f481fc83622f09ac21c0bca503ce6c2c21b83e258931ea` |
| clip-093 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `d4f032df204e1e7e8d7ec2e09726c46fbb075475ae97859065b7502048386bb3` |
| clip-094 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `43420eed7b8bd33da5578816b5a924d2bda7f0c4889c707973ce1877aeb0e791` |
| clip-095 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `9d95db9d7ebea2fd933691c8ffa9841857f971d74f41698989310dae81a2a3bd` |
| clip-096 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `3e1e461c3ca6ed528b8a6e8d7c8a8da024e36f6c2b39c8578551bfddfbde523f` |
| clip-097 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `5edcfeebd633732cfb1cb2f862b495dfa3b939a63371917af772f12973fa5d25` |
| clip-098 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `67e2d7bdb31065f65fc88bed544ad7ffdf45d707fa7b2c032855d8afde5acca4` |
| clip-099 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `1baa695eaf4a9b905ce261f7fa6b2c38dbd0cfdb502ef31f8e122a81e8fc9863` |
| clip-100 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `f67a52ce3cfa7acd929b1a42088db75b1aeb5832620b03369cdc27106e01d9ab` |
| clip-101 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `b30ee36fd10179dbb0168ab84f6cc451766fe32ed91cf617e54209e224a5e9bd` |
| clip-102 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `7cb372862c8fbf4104a703b5a8f3383844cc41ac3da6acf1471921719ee7fd8e` |
| clip-103 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `754d70c698f63bdda2da8a4c045b20b5f84120f2840d98f0ad440b78a0dd44a3` |
| clip-104 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `1c610ec23ff3463aef9abfab719097755b1c151d5f79f9f47537fdc2efc410bf` |
| clip-105 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `e75308c75437a485455f77550e81b3654ad326b254501df6076fa907fc552aa5` |
| clip-106 | 20.067 | completed_partial_dubbing | 20.067 | yes | passed | `2248f28cc068f67c506d14803952e57b94002ea9136f4bbb76d05d9ea21280be` |
| clip-107 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `cea740008ac5b367b9feb86951d8b8f2cb8e095677e67a71f2395ad127f265ab` |
| clip-108 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `04c2c3bc17adb23d9cf7375efb22a5777be282be842ab8c3d21f41105e613210` |
| clip-109 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `7a6837d8be6276133986cc07bc86bcf07e5a8822afee307e8946d8fef25221ce` |
| clip-110 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `8f31c5ab04ba03d5644d9f09e45023a6342eb7856d1177a71981956761634981` |
| clip-111 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `af6188c2e88cc1a5a592cbe5026f77bf2e08da2ddac02101a46dfa9ee3343b97` |
| clip-112 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `df2832d54c107b5b81aa8c4b379bc50584763b77969b8c26657cb1a84f84521d` |
| clip-113 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `23e4809fe3f0d6b780bc9a9b520cfd7972e1e7c634a2a9207647ee1e63b1a0e9` |
| clip-114 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `9a68d5fd20486f128c29f87c2a5dff97d31c7b7c4a1ba4ece2a0bf75db209a43` |
| clip-115 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `2020a0ecd6d9f346555791ea14a9963665d6c200e482860eb1ccbc28b137ebbd` |
| clip-116 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `85ae3eeeaf2e79c94bac9279a32a32ae355904ee681ff593782324ab10b4ea6a` |
| clip-117 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `412b0af9de39caeb4f09023901a8723a0297d3d627c74d2b49fd30d89060044b` |
| clip-118 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `cdbdf176ef547c5f914e028607c4e68a7b9efe57bc6beb8b65df19924b3f02e2` |
| clip-119 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `6ef61f6eb4a59409a9496789a63674be2b492afa7a7ad69e35de94f9c70b72ec` |
| clip-120 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `abeeb6dda26c71cfcac9d665a05396e9cf063d3d147c825c0db3a483fd9c4875` |
| clip-121 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `1e228f55d495bec93e73adbc9ea8a58cd892e175d67b710d715cabe3309ed755` |
| clip-122 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `c20f66ad4c6f74137c824c0ecee357b9ae83e2b6434cdb5dc18f1c03ce5c18df` |
| clip-123 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `827ebbafc69aa385c2ddf47739359de7141fb0f3c1f8f184e7453a645405160b` |
| clip-124 | 20.067 | completed_partial_dubbing | 20.067 | yes | passed | `ffb2d4eeb6ce318a28898704e958e7dbaca22615b12ec6aeb3a57dd09d33b2f3` |
| clip-125 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `12875df86307f3d72181f36e9162fc3687e11579283494a300d5cf68c3b1bc4a` |
| clip-126 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `77b85e39a6d6d7b7d0ccd3b462ea07c74d31efbc5f400e6062ff8ebeb8755113` |
| clip-127 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `3cf1324a8c041f86ee15ac99ac21805a3d16207afd5f02af449833b0c0c9b2dc` |
| clip-128 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `56665594b9e9f02ce514c70ceb9513a84a352fb8a471dc0e138cf9a3cd4d9d4d` |
| clip-129 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `d16eae21c2ddf306b191a14713c7b77cbb3aa0ad822e018b048e83a56d9bc82d` |
| clip-130 | 20.067 | completed_partial_dubbing | 20.067 | yes | passed | `16258a9d30467ed422d71942dab05e1c90db0decf9d2e4f2293c2d6feac2d211` |
| clip-131 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `dc6f4fd1615662966c12a96db5f780fbe1aa88f7de71b8798b13da50404745e4` |
| clip-132 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `116b4747e4f09e7de430b490292655be326839d64fc22807ea7f1dd6e97f386d` |
| clip-133 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `4429e000019a8ad9d06d7e6b94d522bbac8eb4681b4e9d6be975af1b7080d329` |
| clip-134 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `6ca53f0514266cdd9c638f8abff75cc70c862adc606176e98e7a652edcb34a37` |
| clip-135 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `e9c31ad7f77f35a01ac9d7dd2a521b31e9d53b597205e7ac4bda59dd72a6d2ba` |
| clip-136 | 20.067 | completed_partial_dubbing | 20.067 | yes | passed | `d97949582e986d91b2159bd8d1047c69aa4322039459a2cca954f71189c8d983` |
| clip-137 | 20.067 | completed_partial_dubbing | 20.067 | yes | passed | `f257f341f8e11c765acc6bb2164e003ccbd9ce4e4202068edd54c571422b1da5` |
| clip-138 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `5b1c9b86e8dcdc7932d882a824d77056ce4234d0cfecd8e5b28622a158494315` |
| clip-139 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `0d645beb669868427935cbac98d97bb6f7df425b81764f5035596bd3aa3c41ab` |
| clip-140 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `19ea9496fbcec67fc237d0b3d97625953009d1299cd1d9af23e32d42e6a77a47` |
| clip-141 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `2ee63eac1185a7cc4d6109e72f11e14d777259d70505dba54c17a44744e38e1f` |
| clip-142 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `c937ae1a059b5ecde5393a297bc2dae9d9434e4756e1f810403d033c7a1ad145` |
| clip-143 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `d103b35bc0f68a55a4d2f7d00a61d12fe24b956f11d1c9f06af1284c8e5e5104` |
| clip-144 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `740f5f103fc91ef4d21fb09cdeea0514ae328e9dc49ca508f1eaa153f0840a95` |
| clip-145 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `36fb90681630c825c8d4380f24c4adcbde88eb4e3e26ee880ca113d6d25cf61a` |
| clip-146 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `42a08cb7a00db5b18e5f2ee549d9cc7d302467222e304489f90c0d3125ad5555` |
| clip-147 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `21a3d31f0c2c756f2703cf9ed65cce8e9649659f3d851429d12347f89bd4c2a6` |
| clip-148 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `eb0530bc3071cc2a41c6c4a89f99246c12cde153c7e673494fb260d725f79192` |
| clip-149 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `375e012af1a27971d7455a979352cdc95cda2688c4454ebda4c612d1ed6c6fef` |
| clip-150 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `af468d96a5447772e9233378de00926ba04cc117a65ff289a26866410239b181` |
| clip-151 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `22c20edb961c029a8342f6d9452968096213e3722a7a5e1399f6bac27196b765` |
| clip-152 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `45ee992bf4e43f2130f0cf06b5415f74581f9d8fde0e869e97f809a0895e5117` |
| clip-153 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `253ec7bffb380095568fb1860909be0aa37c4c80453d21491d53f88ae4a782ec` |
| clip-154 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `fdbbd4e5002c9c6954a8389d300922df7820d461eb763c01ac0625973a2d4ec3` |
| clip-155 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `6340c77757c9f8ba524a1fb9ee3c821f7950fd36ac1a8999b4da344471850ece` |
| clip-156 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `3eb6f89390110ffbffd02ceb9ef75c591ae01e667125f51c98db118cd3590945` |
| clip-157 | 20.167 | FAILED `JOB_ID_CONFLICT` after power loss; UI re-run completed_partial_dubbing | 20.167 | yes | passed | `4d2a9f5173432df61b43c40e880c3ddaaaac549a871038396e8e4575a8cfa842` |
| clip-158 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `dbc5ce983f0adf95ad8fab6eaabf1319750572af2ccfab818a1471088b71023c` |
| clip-159 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `b7664d202fc47fa493af590f1420b579e9d6b3a6ce6f43ef29627d5a153c4912` |
| clip-160 | 20.067 | completed_partial_dubbing | 20.067 | yes | passed | `6b3e251eb534753838706736a22e9045e5cfc61a90ce6901c47b7cdaa6bbeea3` |
| clip-161 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `de1b8887361bf7ce46d4b32628e986eae3675a6975390e748a677077fe3fcbc8` |
| clip-162 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `22d8bc16ff5050494ff6cbd8c940ad0852afacc78d716bcc27c90149ce2fc20f` |
| clip-163 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `4b7c089cd61a0845968e734ead8bcc78115b5ac6a6f07cbd56b4adc5d2d1eff4` |
| clip-164 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `a089f565fdcb5ab580175074ba26bab3b4ae268bc8edfd6c46a32048df254fc4` |
| clip-165 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `e64d073ecfae704d20ac9150bef71f8b2767f349acdf614905f9d7f5800539df` |
| clip-166 | 20.067 | completed_partial_dubbing | 20.067 | yes | passed | `366d996898641b0f89c1046464b62fcb419a3a50891d8b2f3a7e86ad37d913db` |
| clip-167 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `a1284dac19a2923cfa1dcb619fd274886962797cb08dc825ed6229bcc53cb96e` |
| clip-168 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `17490a5779bb61abf14147b0693a56728d15d4525a5a8c69c97fef8b5e9375eb` |
| clip-169 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `89aaf1abc5078e5da9caa2b3a6bd68d3fab18c42c696f7f29ec3c337921c689f` |
| clip-170 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `37e3ed09810db673c57eb9b2f72fa33337319a8cc7526ccf273e1503323fda79` |
| clip-171 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `c308736c4c30832deea0f9e977a26a0d3392064ed660532fdfa086d9ba3f7667` |
| clip-172 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `66784fdd024676b1b2253a3533a724ccecf961a70b7bf3d1e4c979e93283aab7` |
| clip-173 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `e97a6b4bb97a5cb7d261022c49c807fb59f5da82fc48afe0c788ddce5760b9d9` |
| clip-174 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `87229c6395d3489fa8917e4f6aef3e0cf104c8b3a9e84ac9d036b6afac8ef001` |
| clip-175 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `b3ce47a21a1ec7528de80bea53a8582d7924b6bc8d1ebf227bc6957cbc6db628` |
| clip-176 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `6c29cf1270e6ee1e15cc1d36841f62affa2371e01fea124c743e773e5f246406` |
| clip-177 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `b1b5ac6fd8b50af69dac485d16468ee78a551fed08e65e41456443d70fd592e6` |
| clip-178 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `d529c2ce71be6ceacf45c43f1f94a058d269035f4f80766e8a746f17fa7adef6` |
| clip-179 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `787a9824a3501143874a4e1273409616be2cdbd959d1be17926525bb5b360b2e` |
| clip-180 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `15db75efcb00b4f5952fc59423b8c3cc33fdd01fee71c4abe27d7a8496b9d37e` |
| clip-181 | 20.067 | completed_partial_dubbing | 20.067 | yes | passed | `beda23be654e03264aabbd6889d2a36970533902f822b8cacbb1b74f99c780ce` |
| clip-182 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `bdda4570fcc0522953037f2c26c696f3d29a6e0208c887d0c1e9c6a029aaf605` |
| clip-183 | 20.067 | completed_partial_dubbing | 20.067 | yes | passed | `8677ed0b6a8ede32bc5ea9f177b002568fa59e0c51e5e12df247207f4a9701c0` |
| clip-184 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `0bd2f47976e1f40517c30f5357fe0a91229b568e4ca17b9694f2fb28d134ad66` |
| clip-185 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `10b1816d23b9c41860ebdc6c9c6c9eb0b4d8d6915d8141d7fcd7086da7a43c70` |
| clip-186 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `2abacc5470d208ec3500f03f38d00d533d5111cdb632cd3a191aed0b3e4b512f` |
| clip-187 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `3da4c38ae0b73809c3288a17f0259c6489400722b70169f8e128ca8b1cf75a1f` |
| clip-188 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `4463f6992064796e08599aff1adf23a6b7d46e0ee788e5c24e136df1c38df14e` |
| clip-189 | 20.067 | completed_partial_dubbing | 20.067 | yes | passed | `86d6bc3372c64e86ccdce5c547fe715b3195346635a1a1a8ec0f1fa47f10fa4c` |
| clip-190 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `ec0451c80859d62bcde3b9c9364754dd6699c340a5a7902073bcfb23e7fe1302` |
| clip-191 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `42e5e89ba625d19ce8e8fd748931367a7cbc7e3187b7316f567dfac6eb92e1e6` |
| clip-192 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `2599d74b20a3595b2b580cd828178980bd309fa5a8a1d5a5ff61d748c7f54cdc` |
| clip-193 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `6abd8bfcf979fe061ef1805024ba48edb261141f4ec74d77c607961c9ec6f71c` |
| clip-194 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `7644af2b200300bd8790523f2c84dabf89a1c13233feeb77b843d554d465c35f` |
| clip-195 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `777ac89c64c77590aa06d0c275ab53ddf3540cf195e90a4842a58aa858ba2e1b` |
| clip-196 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `48f3faa419b96837c6975aeaf5b53094e4ebd8e1d457def85f9ef3e2c21b52b0` |
| clip-197 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `d331870afe4bb0467d2a1a82bf0f5d5a330750829fb834a21e14a70820fa30e9` |
| clip-198 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `f0c12f34cf4dd3f363d2dfbf86251779e6a7bf66ba8b4f2852375d36c47c5eba` |
| clip-199 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `48b585d31312719ad7fcb87bd0b8ce5c4ca290fa310667c5e31b721b9d56af5e` |
| clip-200 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `6bf46e43a292f40e7bcbbcfd9ca3f2be83b50add77af21cb801c89acfb625222` |
| clip-201 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `03e73bbcb863bd28679fa213c9c0379c4716517f887c4f03bafbebfb9afbaab6` |
| clip-202 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `2d97c50750732be3486d77fc166672e47632bf98e27a465bc6a7043ac22bd805` |
| clip-203 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `51c8b9bd200fe5df87f2954e766a70c3811e0cc56f77b635847f952cd1891e16` |
| clip-204 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `836356dceb376aa19e51c3406fe17f995304fdac56a254f380882a62c31c4d54` |
| clip-205 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `25b003ebb53f2daec8fac0e371b78116030f66fbd63797ce9c091dac9a6e2655` |
| clip-206 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `acf253623f61851999fa5bded18b703e8bc4ec78cc2e4ae3ef27b34a228e7069` |
| clip-207 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `30934970b8f5bcfb870fa96fb9c245ee374cb57156d4ff7644efa3aefb216557` |
| clip-208 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `dd3e2cf13019d3fae9e9e522ae516123faf65c6f029b0de3f3b572ab19177c67` |
| clip-209 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `02ee4da124ac564cf6422ab096ed2250042c2ab7b20664bf7b57c4001728b9bb` |
| clip-210 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `e4db018a0e7aac53014389c8a602a6bfa7bd3d3bf83485c2808923c94a3c9cf3` |
| clip-211 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `b70a8d6ba76ededdc026f6a3d60dcf0debcfdda7882aa6ac9fed00c8dcf8047a` |
| clip-212 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `74ff33bd05a0928943edb39e403828cc4a60a03f5d25ea01045c3fea16afd88c` |
| clip-213 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `a7e1b9c4fdd8a6ce362e900dcfa1bef8e232c6072e2b5aa583998099c096df03` |
| clip-214 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `10b958c3bd69208529aa17dfd11a51d4320c8173f96287076e3633678d2a05d5` |
| clip-215 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `58b7273c3c690045f7144fe1cd80cca77ef76f370233cd5c3c6639eb1d17818f` |
| clip-216 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `fbb9af74e8ef9dea42e6b4f9ba1a6492716995eedee141dbbe35689c814e487f` |
| clip-217 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `ef17931d42080b01671c4acf733f13789b47ce9fd2a34d7813c836f3a236634d` |
| clip-218 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `4d6e580a6a19baabe032f2388adfa7e422160f072a0cbfab3e37d40285490365` |
| clip-219 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `a977b1177d83bf587e0083e1673a96f4dd065353cdb2592defdd9466272bed5d` |
| clip-220 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `f520d8c2ec0f5166b10bcce73c7618b23467fec7f918dce2154ec216952165a9` |
| clip-221 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `03d9743878d77d5bb59ee6e55318a176a1c7d2ca48111e43490b3c83810b725c` |
| clip-222 | 20.167 | FAILED `ASR_EMPTY` | — | — | — | — |
| clip-223 | 20.1 | FAILED `ASR_EMPTY` | — | — | — | — |
| clip-224 | 20.133 | FAILED `ASR_EMPTY` | — | — | — | — |
| clip-225 | 20.1 | FAILED `ASR_EMPTY` | — | — | — | — |
| clip-226 | 20.067 | FAILED `ASR_EMPTY` | — | — | — | — |
| clip-227 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `93fc775d346dc10c997fb39e88bf653c3befa940c1c914318aeb918234e96b6b` |
| clip-228 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `51286acf24bcdff642addc8bf48c149b4e3ac67353305df89a40190ae7a0dfd3` |
| clip-229 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `506c9ef80aeb59a8f272dbaab8dc5fb334bb92437b3d9ba9b325e71539fd3d67` |
| clip-230 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `edbee220b9fcdaf76c7370e2333cd59e9fe8e85f5250442963a16406bae22d87` |
| clip-231 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `e4c0f5e884c42ecb76fc8bb40ca552a5e06594a9c07eac0805cd0e04629a55c1` |
| clip-232 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `44b71009a7eba93218fae443cdb7bfd722eeb5889bdc4c2c0a00ab03c9b5c5f0` |
| clip-233 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `3f96b0dd3ba849893302cb740ed0413df9527c1db0bced97cb21ae2e7f181292` |
| clip-234 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `975afd4b11d68b657f7aaf17966737fde9b5599516a3170afa95555fe25064ed` |
| clip-235 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `1863ca88cafe63abe4b16b5486cfd6162f98685d5fe7f92342ea4119ba1d9a8d` |
| clip-236 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `29810141e00b8711dc1f20f16f0563d9329c03161c93043169184844a6c44624` |
| clip-237 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `86f353f021119caadf296d5527c0c6841c11938c31d854756db46589ee4fdad8` |
| clip-238 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `4febe78ecca00e3be985b5155501528bacfd28b24a098c0346196456d161d003` |
| clip-239 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `a5ac23991e81428fe38e50b44512ab7676ddfd6dc1f706c4bdda6f964e4cac80` |
| clip-240 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `b0c9c1fa848d14666bb0ae4bf965e183e62d2aad55a37a82bcf979a1df8c3283` |
| clip-241 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `c33c36cdde1e01fc3c573100e2e7c1d7d63c4ad8fb67b45dcd7195d5550806fd` |
| clip-242 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `4b5d0a438c9689a08e86cf93078fa5c00acff0171c51c6a32a65b8426141e04f` |
| clip-243 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `fade1dabed04cfcd14f90ef30798f1f0e382fb3ff6ced6f3a38cbd05b1caf281` |
| clip-244 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `21ca0ac4a7f37475ed85f8cac4e3ec0bc49e64a4a6214eec43f76c53df459d0d` |
| clip-245 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `376beb68f8bd71a898fc09f0e422f042ba71fcb2deb1e9dea2967744bf290bac` |
| clip-246 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `dcc5b94b312825b05e452a4873417534ea060178b459bec24039c93c545b9f95` |
| clip-247 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `8c7af5dcf8a058b5d095017076bfe66306fa66d4208ccab0e05fd737e289f28b` |
| clip-248 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `9c61f5503ac6487127f9bfe56285df1f6341eed0627b6e007a2a2bdf5131b114` |
| clip-249 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `42c3e83efb89dd0138a18d85faff3a367630cf28ff36077b512ae0fa4a812ce8` |
| clip-250 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `32339bee80e463316614d18468ee64e067499022b61d14ec6cb6eb5f318a1faa` |
| clip-251 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `4244cd565a2ec84f0b5205c4da6b949519962f4d6116f6aaf1dd777fa059d064` |
| clip-252 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `96de7cbc7759349ecc0cf497ae4790653c7bba1839dc9813919263a24cd8ac06` |
| clip-253 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `7854850782e6313d140ccf120ddbe3cc66bf388d2b993bedccbcf109bd0f2ce8` |
| clip-254 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `686d1d4e6ea5ac1bf1e318b5a001dd7472a19947928394a251aff0606e2f9e97` |
| clip-255 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `a740a8e4bbb0905d810dabf1fcfa86c27c43e68887c5f96584fdbfb3e0479c25` |
| clip-256 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `987ae864cc58b08f7c35688d17c5f4a7774e63c37cb7023a45e5d722c3f8e662` |
| clip-257 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `2056f2d3eacb0e3251df1c16ceca93288b7e2184f172df31e9706e63b024ac6a` |
| clip-258 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `a8cf3b5da1baeea19357acb7df7069d869af57b5f9a2e992b58a87f6f664261e` |
| clip-259 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `972ddd55cd3a823006c2f69bfada3c126864d2c457691065fd41e97e46e2e9e4` |
| clip-260 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `21328c62b96936636ad6719aafaeca38a0de7b2a5a94955730f11758638f54f3` |
| clip-261 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `fe458ae88c9dccb278534f0839756c810e39b18c6779c13e889d4df50a2be8eb` |
| clip-262 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `67ae9e11aa235b666685ee32aba210993f939e692d49670568eb3cefbe12aca1` |
| clip-263 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `51b3ddd11c442d1a598194ab2c54989cfaddb3c0b1ebea43f14e98ae0053020f` |
| clip-264 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `9fcdb184a431c629347621e0c9e54741349bf551487ec1cad13bcd8c03242535` |
| clip-265 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `18f864f07517942f5274c88aea4bcc53d9a168d783bac2a13aad43a0b9a02c6b` |
| clip-266 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `31a426fa01361bb890d7784935fe8a6f84063d8e3cea89d76d2ef7f4fe813b72` |
| clip-267 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `6553961f0eff0b3f79bc52e449b966208279cff62ed80b6ccd7cd794b5a80f56` |
| clip-268 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `75d4258f2fbecbc22617abc5d6993657164a2a7e2f98999173026a38ec6d26e9` |
| clip-269 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `1776cd5e34faaaa82ed5886c914abf2df328b31d52dd6f6908ae81d881b3f88b` |
| clip-270 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `56fe92d664042e1d3116a00177e789cf9ab90f51819bb2d60961477f2425ff47` |
| clip-271 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `98e8c1b72f00a4f006fc6ae352799ac722891bbb4f1ffdc26cf3e20daa951ac6` |
| clip-272 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `f90e0a674010dab63f55efa1f089526833d583fff6fdc55f9c39aa429d68fd5e` |
| clip-273 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `5b893e6bd252d78c15f2f296c568ba0d507082b2d447843211e6a968512889ee` |
| clip-274 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `fd67c2ceb4f427b7dec20c4f42324e88eb88d9fed07265eaf0223fa9f0b2b65a` |
| clip-275 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `47d7addef8e9a65b83c9c5fecaf78bec785b10f22fd90fa55c2a5253d9f69fde` |
| clip-276 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `c343cf990ced5cb9d26fdd9fb458e67b5f2442312f57cbd9cc8640d5dcb0fcb3` |
| clip-277 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `dc23eb1680c56d08662302022787aca4d3cacd2e57a9c18f1b61ac88d143d039` |
| clip-278 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `e69e2a7ff1d8ca74bd7f9d2f1cf663f36650679c158d57547eb5e94453f26494` |
| clip-279 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `9abe8ff4b578f5f8cd44468305f06c9984c3a8238abd9096ad9f0b58d1329885` |
| clip-280 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `69038b0e022dcc4bc36a2ae8d0a71e684f9bedcce9be304a65e012710d0c0cd7` |
| clip-281 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `3da6d1ce856518e78037275bfdb3dc9b0181fb15d6204c21d90939589ce15b8d` |
| clip-282 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `2f223effebe2918b28bdd21e227094d7f8fa0d4e63d6cb0005fef29f38eb492a` |
| clip-283 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `7aac63978158c2f2f324c8834f55c1ea016f5117875eda7521aaf2080720e7ec` |
| clip-284 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `488f0c652d62ed0bf9ee9f0207308590d4287725c4f13e30b1322e41726bf6d7` |
| clip-285 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `bd9724206dd043dad0015dcffa00b5c56713de4b95d3b8d084e275f218299d8d` |
| clip-286 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `5608c856d4705d6b6aa85234c90b635fbe5a4641e59d45b793ea68f28047a1f8` |
| clip-287 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `921d92c354997a2d082ee2ae79201a77fa12298d46762902adbd7bf4e4151314` |
| clip-288 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `323b492f865607e8a0e2525a0b789addbead0189730721fb2f1983c3d4867e69` |
| clip-289 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `f39380f2ac7226c7e151071f45eb43f7de0c8db3175cd6ffe6cb2a63b38a888c` |
| clip-290 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `4db05b7d7ece4a5ac835dbbd9a23e45aaed6f3d827caf74465493b7c5df58302` |
| clip-291 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `8458de8cf770f592c44d3671970dbd19dae9c0bfc5f518825ff69ea6c20a074c` |
| clip-292 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `fc91dc820947998f79ccb2c180e05a06d3a63a8fad94c9d37aa2d0e7b61d1dfc` |
| clip-293 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `ec1d66e5372f6a6b34d0fde944fb912535f98dabbf4be3455234d22f43c36caf` |
| clip-294 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `2589e0154d1ce2b346e94b8c73411f450b238d1d1b078c17650b7506feb6c158` |
| clip-295 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `4471e5c264b1cb5b182f8fe9a4209d7fac44e8d4ad80cfb0f0387806624467c3` |
| clip-296 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `c0a95804cc2124a17a2aab17bff62c00753288f9bc4580a3be140fea23c30423` |
| clip-297 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `46610f63ed0ddcdac3712fcf737ae6322f246e52913cb3137ca0b985a2cf87d1` |
| clip-298 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `d432c46d5deddc8c4a37bfe83d19a6017da44dd006ad2bacf8078ec1a9e8a8ee` |
| clip-299 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `fcb53ab4d3d58769bf9d31b89c9fec0c78867c121b6b03cf707e5585d853500d` |
| clip-300 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `c7192edceade68375dac75eb62a222f7d419fb04c45c567c4f4a0575992b017f` |
| clip-301 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `41fc3894482361ecafd4113a4204beb8c67f371b3e715c8ad7c698403b8ea818` |
| clip-302 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `4cf427f18364afb2fba38b4de920fdad3e73e91b0ff9ec5837f46175ee884d01` |
| clip-303 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `909a183ec6f2ede764cd4d56d4f2af8f23b4f992fe42aedc10d0a10b683d5839` |
| clip-304 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `dc083a6e3b98ea37a5f590f9c364c1337d3a38c54411ab157f4bfa713cdfa203` |
| clip-305 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `82c432f927350e60b6297f96aefefd0ccc72744b76b317a1eae5a5a2b5cc3aa5` |
| clip-306 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `6dd078a0de35faaa3a7425447fb9668d7a7ebdb202220a5fdeed3d79a75a7c86` |
| clip-307 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `84cc8798d9b481deff3b0df3dbf4f878a2bc793dc497e4b2535fda73962358ce` |
| clip-308 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `8d1acfcee4b35c5518f47fd345900c4b770c82a448916447a655c5c784a8582b` |
| clip-309 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `6a194167f1283c238afee2f68646aa020c177c483c770f57d523c610e22a7bab` |
| clip-310 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `2c19513cd22e7bc856a6c068a7d513745a6c305beb8eeb35f5fa8cf3b819a45e` |
| clip-311 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `a7f6de96d6a8d79db7582d4e6368f1c7bb7793d8e6ec9514d1f55960f64fb1b3` |
| clip-312 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `eda8a740cfe332aadec8a9921a445ad9182886dadca60408f320dfe64d8cbc7c` |
| clip-313 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `f22a619fa95911a89efe6ff884d60fd3845cf277671bf796c7c125567a06a384` |
| clip-314 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `b3cf94b9c86168470fb9891ff5df387b5a524b1daed1687b43c401df2f463467` |
| clip-315 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `74c278b8f2beca4cd02afb7064930de4ae0a3220d85b7923536590e1a77752c2` |
| clip-316 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `24ab0fb70073fbbdb17050383e2afbf4943e791aaf88e9b239e859c6df256d1c` |
| clip-317 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `3e9ffcc70a0165c1ea96baa470e6f16721f3cba559858d435bfff78aa6aa93c2` |
| clip-318 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `83bca35825f29e8c4a045a66c9540d8cb7133ae80c357336bc748d3d84c5f93e` |
| clip-319 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `1cc6ffe89680e3d7913655c66a046808b44223f0477b8634d4c0dc33e92c5e55` |
| clip-320 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `6f5e92ec9e271956c203c67f96b4de378b74f2083ff32677d5400e6f327cff0a` |
| clip-321 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `75c8151387b0fffe92ffebb934fca1790d45eee9b121bd484e283914dc9169bf` |
| clip-322 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `ac3ffad9d8e4541c093388d66517170d45148ced4dd50ddfd27ba1317259b6a3` |
| clip-323 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `2ae4c44c9cd1937ae6009a287af88cdb7bbeb76835c8c32f4d20cd831140ece4` |
| clip-324 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `2eb403ce37331cd236ea7221c6028b1284d8bf0799619735be205b2666b64d34` |
| clip-325 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `2e33f71d9bd74faf4a8f7126f8443ff81c46506f880c1480e6cc2790f62f03f6` |
| clip-326 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `0cd917829401a2d0f0886a094435fa709c466cb39a37c241d1dc8ca4fb0454e6` |
| clip-327 | 20.0 | completed_with_warnings | 20.0 | yes | passed | `94c18894ccc0062e7368e7189b241d62ed881089a2578425be4ee45e37d6e0fd` |
| clip-328 | 20.08 | completed_with_warnings | 20.04 | yes | passed | `b53a859fc29049c04dacb904ee669698d628a7092c50812e21ae05e358890327` |
| clip-329 | 20.16 | completed_with_warnings | 20.16 | yes | passed | `df71645a4b351884eca46657878b870fd98778de5600b7ed38d5e63af3018606` |
| clip-330 | 20.12 | completed_with_warnings | 20.12 | yes | passed | `2c2e322a16d9f468523711fc20d90ce0c53dc35671d7516f4eb5b6ecfcc597eb` |
| clip-331 | 20.08 | completed_with_warnings | 20.08 | yes | passed | `9e8d6d1fda36bbfed83ca7df4d09ad6ec3e517e83347e49b8935b7c5d4cf2f4f` |
| clip-332 | 20.2 | completed_with_warnings | 20.2 | yes | passed | `4c42257445d5bebb2261058596872b54f1f94ecaa75f5261d41e60f62f47da65` |
| clip-333 | 20.16 | completed_with_warnings | 20.16 | yes | passed | `5bfc1ce134297354dc41f335749680f43042fbb2c9530d407c29d1033e0c9d12` |
| clip-334 | 20.12 | completed_partial_dubbing | 20.12 | yes | passed | `22e9e05ee94ca2470a7b7436f74c5542a0c6d907b1c82b4847defd86eab2bc09` |
| clip-335 | 20.08 | completed_with_warnings | 20.08 | yes | passed | `aeeb06a4e1d81cbb88adc10f494699979a64ac7bba3315641552388aca9c14af` |
| clip-336 | 20.2 | completed_with_warnings | 20.2 | yes | passed | `92d3ccf9803676ed05d95ecf8fb1d6eebd9f233bb19d8378593fac1ff6022bb9` |
| clip-337 | 20.16 | completed_with_warnings | 20.16 | yes | passed | `6a45742cc0ffffbe8f0b4c9eb9dd33216a3883b27d080f1b00554c5bf1f66155` |
| clip-338 | 20.08 | completed_with_warnings | 20.04 | yes | passed | `0f5c3159cf9042033c158867daa472baf7ecba5e8bc3891f12c1bd19bd495828` |
| clip-339 | 20.16 | completed_with_warnings | 20.16 | yes | passed | `5573e662d2844956e856ce9dc1ebdf73eb005cff9e84e56532b761ea53226291` |
| clip-340 | 20.12 | completed_with_warnings | 20.12 | yes | passed | `54508cd8a42db87fc97608da14a8cbd78734dcb1bffb41377c57ea7d5288d059` |
| clip-341 | 20.08 | completed_with_warnings | 20.08 | yes | passed | `c25c75fb7075afdf03f5aefecd7d9c7e955dd8c644367e25af14d5e0d74e56f2` |
| clip-342 | 20.2 | completed_with_warnings | 20.2 | yes | passed | `8f12f93519d716e43a5ea98c98c83b6a7487a1451bc20faba498056075f66533` |
| clip-343 | 20.16 | completed_with_warnings | 20.16 | yes | passed | `e9fd68d4e4fa24afb6e2d3b679ffeecc6bff0af84d14998a07582b5e5d3bc56f` |
| clip-344 | 20.12 | completed_partial_dubbing | 20.12 | yes | passed | `63c8ad535bb134f4d9e70da798b47297b49ae48dc8500cef3cb33695c1a8ad0d` |
| clip-345 | 20.08 | completed_with_warnings | 20.08 | yes | passed | `b0c04d6a888a122d75e7040bd13f2beca35b1b7b35c2abac1d749659e5103a7c` |
| clip-346 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `63f2cf0ce158cedf0cf6f5c42669135f5ee40963ee5149459dd311a5dc8f3930` |
| clip-347 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `ef210ffabb7a56b8f417987f9ffe469478df945859a97967e211385a6bed873b` |
| clip-348 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `21a42013fdd9ef6e78d7bdc82bd4aeccadb5c97dfe0fc90f856bc71a155015ff` |
| clip-349 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `fdd741b57a1e4112dc73754e96f1d29a238e87aa4d7377ba0efd5a15a9e53a48` |
| clip-350 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `025c36a784dcfbc5182de4cbab263c4cbf58c555f0faf491f657cb06c7eea61a` |
| clip-351 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `4f6f6934f85d2e44b091f1ad8574571a486b0205e7364079e5c9ed4596824058` |
| clip-352 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `d5ba71ec4ad2e24862596e4453ec74ae15c3c8c1e571db7e6727810fd2b2655d` |
| clip-353 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `cc693fb71fe207a9690b738defc45330d734aed7d13a3402c4bb5971d9857b02` |
| clip-354 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `d6953996cbedf62a28a1a5a7a5482c33580f3e7cf4de9007fdd76d096eb95d96` |
| clip-355 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `a1f9fe5d7d26600a0be321f93407dee0f11bf6f7ae65ecc3ebbbf6ff029a0a5b` |
| clip-356 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `43d114c449e48640060c6392efbb2a2be8b8fbe504e4c416ef920f35780d0d74` |
| clip-357 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `1d5ef942d1b939593fe9486e1db3facba70f01d963793fdee5dfa7eb6baecde0` |
| clip-358 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `4c95408488087b1815585b69bef1c8dfd61fb3d6b04598feeca35fb9e399b75d` |
| clip-359 | 20.167 | completed_partial_dubbing | 20.167 | yes | passed | `f055b60f1d07cf66ca3702e9964745e9c423176bbdf448679727efb4cb551780` |
| clip-360 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `115ae1f52ae721131a15f3e0bc92c8184c1f24d9b17ae76e6085829dc0f17e5c` |
| clip-361 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `25a88920663bf67efb5eb559d6697b1bf2ad3098753c8810f03bf94d9f583f8f` |
| clip-362 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `513d305bd56b2fd6dbf387e8474728c5cc2392353b633fcc17e1a714b257117c` |
| clip-363 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `33b431effd079d924afe9411c71b953db1f952e1a5e416e32eddd53d5a266897` |
| clip-364 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `bbe606322681f01db40f419c5b330c64ed04d6308cd884be16ff73312a25c045` |
| clip-365 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `c50b7750ac894099a34911d4a23eedf531334097444a06766e59e2e738bd4648` |
| clip-366 | 20.0 | completed_with_warnings | 20.0 | yes | passed | `042aa5b5c8f6bcf2e9814da77cc02fcd07e8dffedb0974484fcdf559a2c349fd` |
| clip-367 | 20.011 | completed_with_warnings | 20.0 | yes | passed | `5927cb10e3ca86b8720e5fe830b9271ebcc5385718dfdb95ebe60730bd0f2e2a` |
| clip-368 | 20.013 | completed_with_warnings | 20.0 | yes | passed | `d4409bd4d2d5a6e385175762203817af0630212cce2eb051eb25897f38c30cb4` |
| clip-369 | 20.016 | completed_with_warnings | 20.0 | yes | passed | `cb8f76d87821205af5a9e812f15277b412fcfa8d6f09455aed351f1f3b5c0ec5` |
| clip-370 | 20.019 | completed_with_warnings | 20.0 | yes | passed | `ef951c61facab2b50920bf8595daae44f5b608dae1f4c678462903f16be99ae1` |
| clip-371 | 20.0 | completed_with_warnings | 20.0 | yes | passed | `3f4b84eae50e09233fc9a5a7c25f2d12747088245d05f20e8650d2afd35f2890` |
| clip-372 | 20.003 | completed_with_warnings | 20.0 | yes | passed | `50a9be47b6c310b1793ca6d78e7db401c8c4c5c69b6cdd929fd20d54b17a7755` |
| clip-373 | 20.005 | completed_with_warnings | 20.0 | yes | passed | `bed8a3222f4ad761b6f4cf10aa648d5a48fc002310f0b6552084af64a2368c89` |
| clip-374 | 20.008 | completed_with_warnings | 20.0 | yes | passed | `3391c574a4d9ddcdb5c678cec5555c31f6daf58ca1e559786610ba04933dc6e4` |
| clip-375 | 20.011 | completed_with_warnings | 20.0 | yes | passed | `aa1e99e4f0341ef3dacd465a67ce63bdc6b9e282a829a3341ef1c864f5645013` |
| clip-376 | 20.013 | completed_with_warnings | 20.0 | yes | passed | `54ca2aed50a5369a7315e61ed5b377e8da28b6ed414999b5c611a864e72b20e2` |
| clip-377 | 20.016 | completed_with_warnings | 20.0 | yes | passed | `0f933153f44ee93f9ea1972d9986655fa1446787b9d52f6f60c9ca2c2c00a20b` |
| clip-378 | 20.019 | completed_with_warnings | 20.0 | yes | passed | `48862d97a69df037e96ad01d9eee76d572f708be014fc9b1c8c29ce8306e9d0d` |
| clip-379 | 20.0 | completed_with_warnings | 20.0 | yes | passed | `6b34505cda4f1eb0b0da345f6050ebfc0b26874d78434395f3e567c4f6f87868` |
| clip-380 | 20.003 | completed_with_warnings | 20.0 | yes | passed | `adbfbcf5379222c5b453a3b1035e8fe3b64e9cfd2f9a184351f186972e74f219` |
| clip-381 | 20.005 | completed_with_warnings | 20.0 | yes | passed | `344250233ae17215a133fb6fe91201e29fe9efab25925c799bfe868712c90b0f` |
| clip-382 | 20.008 | completed_with_warnings | 20.0 | yes | passed | `694a7965d68063c2ab5c0f1a3d8dc4112933684b44c67f76606da300cb830a74` |
| clip-383 | 20.011 | completed_with_warnings | 20.0 | yes | passed | `0af57c7f125b86be77f5bb2c24e0237bdfa5e82e3c7371a635b6dd9244b53fac` |
| clip-384 | 20.013 | completed_with_warnings | 20.0 | yes | passed | `0857b737e290ff6317ee9f7c8a15f7fcb6171f17c079fb72f4c8f4d750b26804` |
| clip-385 | 20.016 | completed_partial_dubbing | 20.0 | yes | passed | `8fdb49378b2adccd216cde76b25f7a762b73cc5da9e48dd1e40615c63974adfc` |
| clip-386 | 20.019 | completed_with_warnings | 20.0 | yes | passed | `ddbd55493e2b318f90d5665514aa886964a8e7c6bc4f0d970d90567970a43da6` |
| clip-387 | 20.0 | completed_with_warnings | 20.0 | yes | passed | `ab2d2d8f436be83e0e5d945911e7beda019a2b846fdb4b57bac4b116961f3794` |
| clip-388 | 20.003 | completed_with_warnings | 20.0 | yes | passed | `91b840f08c392e2ea29d49f4fa6010918b0ec847038bc6d46674fb68d3d0a9b1` |
| clip-389 | 20.005 | completed_with_warnings | 20.0 | yes | passed | `37603c68003676befa44adcde624957a18c30e815c636e9e765b261bcb9dc26b` |
| clip-390 | 20.008 | completed_with_warnings | 20.0 | yes | passed | `f71ee0bc55d0ae39972660682466ba20f3611b5375b26d37298efceea6954df9` |
| clip-391 | 20.011 | completed_partial_dubbing | 20.0 | yes | passed | `5aef34e6facd74c2bc8b724a814fc6a2a341e40fa492140e172d70048f5e13d6` |
| clip-392 | 20.013 | completed_with_warnings | 20.0 | yes | passed | `9dc74accd665b63630e5e9fced2c051cd2ddbac5d9d9998487454b0cfaf4b056` |
| clip-393 | 20.016 | completed_with_warnings | 20.0 | yes | passed | `815f7c9e111aca55c93b6b42e4a22f1c4f0dd0ee37991664284cea21499411be` |
| clip-394 | 20.019 | completed_with_warnings | 20.0 | yes | passed | `b823d3fcaa4302deefbe3bd616fd79c0eea0d83ed643160a3d9075dfe67e5d92` |
| clip-395 | 20.0 | FAILED `ASR_EMPTY` | — | — | — | — |
| clip-396 | 20.003 | FAILED `ASR_EMPTY` | — | — | — | — |
| clip-397 | 20.005 | FAILED `ASR_EMPTY` | — | — | — | — |
| clip-398 | 20.008 | FAILED `ASR_EMPTY` | — | — | — | — |
| clip-399 | 20.011 | completed_with_warnings | 20.0 | yes | passed | `8f32c101be655bde655bd53c4882e5ab027008b7efaf6badae9581becfacd245` |
| clip-400 | 20.013 | completed_with_warnings | 20.0 | yes | passed | `2e1755af6fe458cc5283ac46fc79b970035f17590b7cdc1834ad6a8dd58014f3` |
| clip-401 | 20.016 | completed_with_warnings | 20.0 | yes | passed | `4ca0bfa097c46b4b2a90f8ec0a53a2fd1322f0d39a1abdc237b46bc3b9e042fb` |
| clip-402 | 20.019 | completed_with_warnings | 20.0 | yes | passed | `26af0ddd59f04e7ace3934067aa3067dc8fb4ad92c3f9e54aad3bd1781dc159b` |
| clip-403 | 20.0 | completed_with_warnings | 20.0 | yes | passed | `beb3d06fa12b0213325a2178076400636df9c4b995add665583bf42fb4b7c49f` |
| clip-404 | 20.003 | completed_with_warnings | 20.0 | yes | passed | `2de3d7dd1cd64bfcc13ab4495a0976a1a4e4eaa6e6f82419446b8e85e6219d78` |
| clip-405 | 20.005 | completed_with_warnings | 20.0 | yes | passed | `a27dddf1b4dc8fb4226a56f0d549ec87b82e24ed6b00300f12f27d217477b3d1` |
| clip-406 | 20.008 | completed_with_warnings | 20.0 | yes | passed | `80d04d81326061bce2da4eb0b2c2a34dad7475aea56329ba2aedd3f32cfd9471` |
| clip-407 | 20.011 | completed_with_warnings | 20.0 | yes | passed | `55e21426127e58e481b88c062d92af525a410dbc84204a3e0ec26de65e00e9db` |
| clip-408 | 20.013 | completed_with_warnings | 20.0 | yes | passed | `e2c79f3187119d371f55452590c72ef744191a722370e0075887cc71ed37388a` |
| clip-409 | 20.016 | completed_with_warnings | 20.0 | yes | passed | `ca860383bf0c9e77f7ba7cdaa8c54a9d21c87b65e6b5ac7cd27495da2c050220` |
| clip-410 | 20.019 | completed_with_warnings | 20.0 | yes | passed | `94f63fb20acca97e5abc07446e7bb904d5c2b67e8e0ad7c94304ef6ab8d8c596` |
| clip-411 | 20.0 | completed_with_warnings | 20.0 | yes | passed | `67960e04432d2fd27b6ec0055b8f0d1d0d147502ea20b324881d5f3464887810` |
| clip-412 | 20.003 | completed_with_warnings | 20.0 | yes | passed | `10c822bb79882c1a2f0e223759c068604785a21b05ac2d82baebd3b8d3668457` |
| clip-413 | 20.005 | completed_with_warnings | 20.0 | yes | passed | `a0b16c2888769610c34fe217780121167368030277e7a3f5f8606ab60ff93ccd` |
| clip-414 | 20.008 | completed_with_warnings | 20.0 | yes | passed | `19a32b3815aeba519f956a770ceedce1d440f58b0e2452ecafc3c473fc89a0c9` |
| clip-415 | 20.011 | completed_with_warnings | 20.0 | yes | passed | `1b1a7842229b2c4a2d53a7d0c043c3561268a74ce61df6a5b62b6b4d7828654b` |
| clip-416 | 20.013 | completed_with_warnings | 20.0 | yes | passed | `694f7d5728287db6593712bc92ed292af29986d23689844d39bc59ecdb7d3426` |
| clip-417 | 20.016 | completed_with_warnings | 20.0 | yes | passed | `5ba0e51ef8a12ce59ab04ff1edac79fbc310bb844c2f26ef3d7e1d7a7285670c` |
| clip-418 | 20.019 | completed_with_warnings | 20.0 | yes | passed | `ef7d6f2311e798e2525d619c43143afacf1f3e10de1356be9b484544b2252319` |
| clip-419 | 20.0 | completed_with_warnings | 20.0 | yes | passed | `8643f71e0576eb8044faa54ed5ee42c6e550ad0659871a303568e53b05c36021` |
| clip-420 | 20.003 | completed_with_warnings | 20.0 | yes | passed | `d9a67426c200842929162446c6b5bf40e05ae0e9bfadb50c122b7650332d7b82` |
| clip-421 | 20.005 | completed_with_warnings | 20.0 | yes | passed | `b0574de8832ab6dacdc3731613e463fe90b79b932c6adbae83a1e23d4a5219bd` |
| clip-422 | 20.008 | completed_with_warnings (queue showed FAILED `JOB_ALREADY_RUNNING`, #298) | 20.0 | yes | passed | `ca4e61dc9731ea3268e18ebeaca8da6ec4274f5cf04dea1cfe31cb89f351e617` |
| clip-423 | 20.011 | completed_with_warnings | 20.0 | yes | passed | `2c30387145443f73c13001f82a7f6bbe3b701f94ceaaf3711f58ac3d58e5662c` |
| clip-424 | 20.013 | completed_with_warnings | 20.0 | yes | passed | `49037926d7ae2090335a45ac085611c8f662379be7783b131776b85a10ae093b` |
| clip-425 | 20.016 | completed_with_warnings | 20.0 | yes | passed | `26649780b851c1e93e22e131cd318a92e58955f91419dfbf7f9881c4f8dae4b8` |
| clip-426 | 20.019 | completed_with_warnings | 20.0 | yes | passed | `908718988edaa33f39cbf9e7907da226d9191567732fc05047f9120b8b403adf` |
| clip-427 | 20.0 | completed_with_warnings | 20.0 | yes | passed | `6a3a71c63a2faea8d9bfc9e8383c1eae8c30f8e3fce91a005a23130da176908b` |
| clip-428 | 20.003 | completed_with_warnings | 20.0 | yes | passed | `94190ddfb1801dff87c2dc7f55e31b36d032466ddf6574899dea442df26d9df2` |
| clip-429 | 20.005 | completed_with_warnings | 20.0 | yes | passed | `a9591f126c5c8213b6288fa94f5b794763950e835b6a4740ed98a7dd4f8298dc` |
| clip-430 | 20.008 | completed_with_warnings | 20.0 | yes | passed | `1c013a9ec7d6c48d297bc79102b64c0e0b12dcae308a7e3d76cf0b6512e31427` |
| clip-431 | 20.011 | completed_with_warnings | 20.0 | yes | passed | `7361bfe793ba8a7114db95d6ac376d29372ed6eebe1c08289fccd40e6bcfece3` |
| clip-432 | 20.013 | completed_with_warnings | 20.0 | yes | passed | `ee71bb88dc745c7b3ea12b1c30afb04b5a11dccb825d6d1016245bed0d1264f6` |
| clip-433 | 20.016 | completed_with_warnings | 20.0 | yes | passed | `46a1b718546654a7959c1f49010b99b74e356e8492346e6b67e78d5a9c1cf673` |
| clip-434 | 20.019 | completed_with_warnings | 20.0 | yes | passed | `8f097232bffcb2bc0b4b6c04083f14b348237340761edd365645d45d77f8f470` |
| clip-435 | 20.0 | completed_with_warnings | 20.0 | yes | passed | `cb4b592ae6899925ca4b6e2ab70b05bf195095ee838505de21ed29697861da35` |
| clip-436 | 20.003 | completed_with_warnings | 20.0 | yes | passed | `00c5547d9c1a901293425c39cbc7a6effa06d4a01ab9ad287735c201ed5f1aa2` |
| clip-437 | 20.005 | completed_with_warnings | 20.0 | yes | passed | `26f82cf57c7becd565c88b3941229e842b06c40e4f02fe48d995dabf4cc737e9` |
| clip-438 | 20.008 | completed_with_warnings | 20.0 | yes | passed | `857b1fc508894bb7b7c2a64a9d36ffad4c76b501381a054b15ec9ffe4062c05c` |
| clip-439 | 20.011 | completed_with_warnings | 20.0 | yes | passed | `6042a4e2c457297318bc607f9c685e76d27a9e4082d4cd43edb40bf42e116aad` |
| clip-440 | 20.013 | completed_with_warnings | 20.0 | yes | passed | `e7b55ea1c7d89fc4f2a84c30738aa195ab81ebeed45d178cf96af9b4c757b11d` |
| clip-441 | 20.016 | completed_with_warnings | 20.0 | yes | passed | `81821c32f423ac0bd5266bfb02c9c84aa5d29c4fd7d4dd669bd909a9e3f6a01d` |
| clip-442 | 20.019 | completed_with_warnings | 20.0 | yes | passed | `2b910772f35256fae13dde5889011349f05fdf83a0f49e486c254d2b8d7e43ac` |
| clip-443 | 20.0 | completed_with_warnings | 20.0 | yes | passed | `eb01ba1369a63ad92387185c3ed170e75efc0f8d34d47e20379b93c510bf4f66` |
| clip-444 | 20.003 | completed_with_warnings | 20.0 | yes | passed | `06825c276e1c1cca7545eb0538f497e05031554b66f9b71d8fc90d464dd67c60` |
| clip-445 | 20.005 | completed_with_warnings | 20.0 | yes | passed | `103beaf95e51965443d4693477c61fda721ea563f761dbe8eef4e30cb59da4bc` |
| clip-446 | 20.008 | completed_with_warnings | 20.0 | yes | passed | `187e8b1de2628316738e00d9bf552dbeac7572b0f0a1b094d834222f1727bb42` |
| clip-447 | 20.011 | completed_with_warnings | 20.0 | yes | passed | `37982b3b8b824bbd918be2b46770846fb541a400f98b0cdbb79eda27ead7ba98` |
| clip-448 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `7e861105c86d955add6d765584316ac6c6de5178afd08af4f2e5599829e899b2` |
| clip-449 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `c9c0eb8c182c9967fbd308a3cbc5a0cec20b545292dfbf388e31f6c2ea949a5d` |
| clip-450 | 20.067 | completed_with_warnings | 20.033 | yes | passed | `317dbf2f760bd2492aa51dbd683d8674340b5b4f2cd01bca5a76542771d9d98c` |
| clip-451 | 20.067 | completed_partial_dubbing | 20.067 | yes | passed | `856def2a17deac0de9c2fcdba624b16d164491e0604a7fdb2213f91c1817a119` |
| clip-452 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `3758f25cad0e0d477f04d4d6ec4c79ada3c6afa793b9b039416ac2731bc54dfd` |
| clip-453 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `d1a95f59be586c00bfa1c4cbf2c3dd682075ac3967b7615f21f18a0a75c0fabf` |
| clip-454 | 20.1 | completed_partial_dubbing | 20.1 | yes | passed | `396899e911530f3424cc88bce2af9cc5377c67f480968d9a88062bad1f07b9b6` |
| clip-455 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `43939b964170091a10448a8aff505e9a2b294be84a470eb64cacb046111c2bdd` |
| clip-456 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `72188d59d6113808882e5f427633f4a5767f66ed4515d9bf9e9b1d87781bfa43` |
| clip-457 | 20.1 | completed_with_warnings | 20.067 | yes | passed | `48e88749d41c326517d21b65669a2df80ea37691f127ccf2dc869acc52d005af` |
| clip-458 | 20.133 | completed_partial_dubbing | 20.133 | yes | passed | `c487fec2df2885fed795d1de5c4e5dc501aebf4699d94954da51ef026e3d92a8` |
| clip-459 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `59f90527cd9b8ea8c26ecee75f3da2f1731fcded07457862c5a36e1893afd31a` |
| clip-460 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `7e05a551126737fc3136ce195402c341de9e3ceb34a7390f0f766296ee45f903` |
| clip-461 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `38cf34532c86a8185901fa3f65174648212fa2c4a2ff4dc33fc6e11e95ccd82c` |
| clip-462 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `987ef26e00cf21394cd862d8b7e1b0b840a44405eaa177ae37baf316d09dd0d7` |
| clip-463 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `1347eb547bb11aaa6bb79abe577a59838e996a024e29f4d7105ec68c4cfeff29` |
| clip-464 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `a44966bbf5f4cd4446ac10997d820b1cbf4a7dffe20fa715761133c4925b5f69` |
| clip-465 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `bf73fb7317923357aefe91280dc402da593492fdc35fffe45ccf39c5794e2ecd` |
| clip-466 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `3a11b6fad24027b72956c3732a531f8453030445d48ccdc5f05321c0d7c6dc24` |
| clip-467 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `1a764970b987d7cf5e9786f8850cb2bf143615eccfb4698921c7598956a5a7aa` |
| clip-468 | 20.133 | completed_with_warnings | 20.133 | yes | passed | `408aa2f39f738d93f37ef892e69e06d25b2f0123a76d218af78c7c7c0c53aa1b` |
| clip-469 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `f369189272e60d3b9e1601c4eb700db17cef57aaaeefbeb6fe5af6b5d45bd347` |
| clip-470 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `2c8652c02e7030daab59d196260edb4cdee82657a8e73842e83f0023b0592d09` |
| clip-471 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `ea9ca6307f7f0a8734e5fa4b0a5d3abe8d561d6de7aa647a37748c3e458ee11f` |
| clip-472 | 20.1 | completed_with_warnings | 20.1 | yes | passed | `89cab2080287ab0ef6aa5ee1bfc4a8b4b2be3229d1cc6794cc7d33280243554f` |
| clip-473 | 20.067 | completed_with_warnings | 20.067 | yes | passed | `5c0a264fac8b0e2c75dc6fad60ac7fc662626555de91593a71115fdd5d9b9f94` |
| clip-474 | 20.133 | completed_partial_dubbing | 20.133 | yes | passed | `3fabbd87d9ad12b87869df064e6af4f74f2c8f33505cb1b76b194107c9ae9178` |
| clip-475 | 20.167 | completed_with_warnings | 20.167 | yes | passed | `012c1af1dc6da1292543b01999c311242b34fcf84f0b7929b61897793295cb20` |
