# Gate S real runs — REAL-DEV fresh Vision recordings

Engine: clone of `agent/issue-758-real-eval` at `f973af2be4aaa60fa3a6fdfae74673f9099d0492`, run through `tests/mastering/real/capture.py` with the
1.1.96-rc6 packaged Python runtime, bundled ffmpeg/ffprobe, the VieNeu voice pack, `--cpu-only`.
Vision provider/model: gemini / `gemini-3.5-flash-lite` (free tier). Vision cache empty at the start of every run
(new output folder). Key: Credential Manager into the child process only; never logged.

Specs (one per video, sentences byte-identical to `real-dev.json`):
- Product (v1): one visual = the video, script = the 7 sentences in order, mastering balanced, vision_budget 120.
- Compilation v2 (v2–v6): programme kind `list`; the engine requires ≥2 items, so the sentences are split in order into
  two items over the same source (first half / second half), keep_source_audio off, no hook/intro/outro,
  mastering balanced, vision_budget 120 (v4: 240).

Columns: AI calls = frames sent to the model (`asked`, includes failed requests); frames seen = frames the model
answered; cache files = described frames cached; cache hits = 0 (fresh). States = editorial unit states.

| run | video | rc | wall s | peak RAM MB (tree) | AI calls | frames seen | cache files | cache hits | candidates | unit states | vision warnings | valid Vision run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fresh1 | v1 | 0 | 261.3 | 2361 | 31 | 28 | 28 | 0 | 21 | MATCH_NOT_FOUND 1, NEEDS_REVIEW 5, READY 1 | 1 | yes |
| fresh1 | v2 | 0 | 510.3 | 2177 | 179 | 157 | 48 | 0 | 148 | MATCH_NOT_FOUND 3, NEEDS_REVIEW 4 | 0 | yes |
| fresh1 | v3 | 0 | 579.6 | 2162 | 213 | 169 | 62 | 0 | 143 | MATCH_NOT_FOUND 5, NEEDS_REVIEW 2 | 0 | yes |
| fresh1 | v4 | 0 | 599.2 | 2114 | 212 | 160 | 72 | 0 | 172 | MATCH_NOT_FOUND 1, NEEDS_REVIEW 5, READY 1 | 0 | yes |
| fresh1 | v5 | 0 | 38.5 | 2006 | 28 | 8 | 4 | 0 | 26 | MATCH_NOT_FOUND 6 | 0 | yes |
| fresh1 | v6 | 1 | 36.1 | 1314 | 0 | 0 | 0 | 0 | 0 | - | 0 | no |
| fresh2 | v1 | 0 | 20.4 | 1030 | 8 | 0 | 0 | 0 | 15 | MATCH_NOT_FOUND 6, NEEDS_REVIEW 1 | 2 (quota) | no |
| fresh2 | v2 | 0 | 32.0 | 931 | 20 | 0 | 0 | 0 | 148 | MATCH_NOT_FOUND 7 | 0 | no |
| fresh2 | v3 | 0 | 35.7 | 875 | 32 | 0 | 0 | 0 | 138 | MATCH_NOT_FOUND 7 | 0 | no |
| fresh2 | v4 | 0 | 35.6 | 772 | 20 | 0 | 0 | 0 | 172 | MATCH_NOT_FOUND 7 | 0 | no |
| fresh2 | v5 | 0 | 19.3 | 774 | 20 | 0 | 0 | 0 | 26 | MATCH_NOT_FOUND 6 | 0 | no |
| fresh2 | v6 | 1 | 26.0 | 136 | 0 | 0 | 0 | 0 | 0 | - | 0 | no |

## Validity

- **Valid fresh Vision recordings: fresh1 v1–v4.** fresh1 v5 is partial: 8 of 28 frames answered, the remaining
  requests failed silently as the free-tier daily quota ran out (no warning was written to the editorial log for v5).
- **fresh2 (variance run) is invalid:** the Gemini daily quota was exhausted; every request failed (0 frames seen),
  so the planner ran on signals only. Not committed; to be re-run after the quota resets.
- **v6 (BV1KmbhzXEib) fails before planning in both runs:** `Phần 1: source.fps.numerator must be an integer in
  [0, 1000000]`. ffprobe reports `avg_frame_rate=10294000/343133` (r_frame_rate 30/1, time_base 1/16000); the engine
  uses the unreduced average rate. Engine bug, not fixed here (labels and sources stay frozen).
- Only fresh1 v1–v5 call/result files are committed (`fresh1/<video>/*.call.json`, `*.result.json`).
