# Gate S real runs — REAL-DEV fresh Vision recordings

Engine: the repository checked out at the commit named per run set, run through `tests/mastering/real/capture.py`
(wrapped by a counting shim that only counts vision cache lookups, frames sent/answered and request errors; no engine
change) with the 1.1.96-rc6 packaged Python runtime, bundled ffmpeg/ffprobe, the VieNeu voice pack, `--cpu-only`.
Key: Credential Manager into the child process only; never logged or written.

- `fresh1/` (Gemini, gemini-3.5-flash-lite): clone `f973af2` (agent/issue-758-real-eval).
- `fresh-claude/`, `fresh-claude-2/` (Claude, claude-opus-5-5): clone `69d58e1` (agent/issue-760-real-run-defects).

Specs (one per video, sentences byte-identical to `real-dev.json`, same split for every run):
- Product (v1): one visual = the video, script = the sentences in order, mastering balanced, vision_budget 120.
- Compilation v2 (v2–v6): programme kind `list`; the engine needs ≥2 items, so the sentences are split in order into
  two items over the same single source file (first ⌈n/2⌉ / rest), keep_source_audio off, no hook/intro/outro,
  mastering balanced, vision_budget 120 (v4: 240).
- Vision cache empty at the start of every run (new output folder). Cache hits are hits inside the run (the second
  item's copy of the same keyframes).

Validity rule (decided before the Claude runs): INVALID when frames sent but not answered (request errors, quota,
5xx, missing answers) exceed 5 % of frames sent.

Columns: sent = frames sent to the model; answered = frames the model described; errors = frames in failed requests
(Gemini rows: sent − answered from the editorial log, the shim did not exist yet); unique keyframes / cache hits from
the shim (n/m = not measured); peak RAM = process tree. TTS sentence cache was warm for the Claude runs (shared voice
cache in the run's model root), so their wall time and RAM are lower than the Gemini runs.

| run | video | model | rc | sent | answered | errors | unique keyframes | cache hits | candidates | wall s | peak RAM MB | unit states | validity |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fresh1 (gemini) | v1 | gemini-3.5-flash-lite | 0 | 31 | 28 | 3 | n/m | n/m | 21 | 261.3 | 2361 | MATCH_NOT_FOUND 1, NEEDS_REVIEW 5, READY 1 | INVALID |
| fresh1 (gemini) | v2 | gemini-3.5-flash-lite | 0 | 179 | 157 | 22 | n/m | n/m | 148 | 510.3 | 2177 | MATCH_NOT_FOUND 3, NEEDS_REVIEW 4 | INVALID |
| fresh1 (gemini) | v3 | gemini-3.5-flash-lite | 0 | 213 | 169 | 44 | n/m | n/m | 143 | 579.6 | 2162 | MATCH_NOT_FOUND 5, NEEDS_REVIEW 2 | INVALID |
| fresh1 (gemini) | v4 | gemini-3.5-flash-lite | 0 | 212 | 160 | 52 | n/m | n/m | 172 | 599.2 | 2114 | MATCH_NOT_FOUND 1, NEEDS_REVIEW 5, READY 1 | INVALID |
| fresh1 (gemini) | v5 | gemini-3.5-flash-lite | 0 | 28 | 8 | 20 | n/m | n/m | 26 | 38.5 | 2006 | MATCH_NOT_FOUND 6 | INVALID |
| fresh-claude | v1 | claude-opus-5-5 | 0 | 31 | 31 | 0 | 31 | 0 | 15 | 98.4 | 911 | MATCH_NOT_FOUND 2, NEEDS_REVIEW 3, READY 1, SOURCE_INSUFFICIENT 1 | VALID |
| fresh-claude | v2 | claude-opus-5-5 | 0 | 97 | 97 | 0 | 57 | 3 | 148 | 305.8 | 950 | MATCH_NOT_FOUND 3, NEEDS_REVIEW 4 | VALID |
| fresh-claude | v3 | claude-opus-5-5 | 0 | 106 | 106 | 0 | 66 | 13 | 140 | 315.6 | 905 | MATCH_NOT_FOUND 6, NEEDS_REVIEW 1 | VALID |
| fresh-claude | v4 | claude-opus-5-5 | 0 | 198 | 198 | 0 | 112 | 6 | 192 | 587.5 | 891 | NEEDS_REVIEW 5, READY 2 | VALID |
| fresh-claude | v5 | claude-opus-5-5 | 0 | 34 | 34 | 0 | 21 | 6 | 32 | 118.7 | 937 | MATCH_NOT_FOUND 2, NEEDS_REVIEW 3, SOURCE_INSUFFICIENT 1 | VALID |
| fresh-claude | v6 | claude-opus-5-5 | 0 | 109 | 109 | 0 | 69 | 11 | 264 | 288.5 | 2028 | MATCH_NOT_FOUND 2, NEEDS_REVIEW 5 | VALID |
| fresh-claude-2 | v1 | claude-opus-5-5 | 0 | 31 | 31 | 0 | 31 | 0 | 15 | 104.4 | 922 | MATCH_NOT_FOUND 2, NEEDS_REVIEW 3, READY 1, SOURCE_INSUFFICIENT 1 | VALID |
| fresh-claude-2 | v2 | claude-opus-5-5 | 0 | 96 | 96 | 0 | 56 | 4 | 148 | 307.6 | 940 | MATCH_NOT_FOUND 3, NEEDS_REVIEW 4 | VALID |
| fresh-claude-2 | v3 | claude-opus-5-5 | 0 | 109 | 109 | 0 | 69 | 10 | 140 | 334.4 | 874 | MATCH_NOT_FOUND 5, NEEDS_REVIEW 2 | VALID |
| fresh-claude-2 | v4 | claude-opus-5-5 | 0 | 204 | 204 | 0 | 118 | 0 | 180 | 584.8 | 877 | MATCH_NOT_FOUND 2, NEEDS_REVIEW 4, READY 1 | VALID |
| fresh-claude-2 | v5 | claude-opus-5-5 | 0 | 34 | 34 | 0 | 21 | 6 | 32 | 100.9 | 986 | MATCH_NOT_FOUND 3, NEEDS_REVIEW 2, SOURCE_INSUFFICIENT 1 | VALID |
| fresh-claude-2 | v6 | claude-opus-5-5 | 0 | 109 | 109 | 0 | 69 | 11 | 264 | 278.7 | 943 | MATCH_NOT_FOUND 4, NEEDS_REVIEW 3 | VALID |

Totals (Claude, REAL-DEV v1–v6): fresh-claude 575 frames sent,
1714 s; fresh-claude-2 583 frames sent,
1711 s. 0 vision errors in all 12 Claude REAL-DEV runs.

## Notes

- **Gemini fresh1 v1–v5 are all INVALID** under the 5 % rule (unanswered 9.7 %–71 %: 503s, then the free-tier daily
  quota). Kept for the record only; not usable for calibration or the provider comparison as complete runs. v5 also
  shows the silent-fallback defect fixed in #760 (no warning in the part log). Gemini fresh2 (quota-dead) and v6 on
  Gemini (VFR frame-rate defect, fixed in #760) are not committed.
- First Claude v1 attempt: every request failed with UnicodeEncodeError because the run script decoded the stored
  key as UTF-16 (the desktop app stores UTF-8). Script fixed; that attempt is discarded (not committed).
- REAL-HOLDOUT (v7–v12): one Claude run (`fresh-claude/v7`–`v12`, same engine clone and settings as REAL-DEV), kept
  off the repository and unscored until the calibration round (#763) was merged, then committed (969e456; sha256 22/22
  equal to the list taken right after the run, committed as `HOLDOUT_SHA256.txt` and checked by `test_gate_s_result`) and scored once (REAL_WORLD_SEMANTIC_CALIBRATION_REPORT.md).
  Frames sent: v7 40, v8 105, v9 195, v10 188, v11 105, v12 99 (732), 0 vision errors.
