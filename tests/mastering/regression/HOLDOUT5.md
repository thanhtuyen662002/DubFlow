# holdout5 — the Gate S independent set

Written 2026-11-11 on main `be8b201`, **before** any algorithm change of the Phase 4 readiness gate directive, and frozen in `FROZEN.json` in the same PR (#720). It is not part of `runner.load("all")`, and no result on it — per scenario or in aggregate — is read before the Gate S evaluation PR. If a label turns out to be wrong after that run, it is reported as such and the set is not edited to raise the score: a corrected set would be a new set.

62 sentences in 11 scenarios over 8 content groups (electronics, household, tools, entertainment, education, technical explanation, multilingual names, long multi-source); 61 have footage, one (`holdout5-night-market-long` u5, fireworks) has none and must be flagged.

## Fields beyond the existing scenario format

The runner of `be8b201` reads `text` as what the vision model says about the shot's middle keyframe and ignores these:

- `group` (scenario): the content group, for per-group results.
- `frames` (candidate): `[{at_ms, text, usable}]` — what other frames of the same shot show (source time). A planner that looks at more than the middle frame may learn them; one that does not, cannot. `usable: false` on the candidate with usable `frames` models a middle frame that is dark or blurred while the rest of the shot is fine.
- `transcript` (candidate): the source's own speech inside the shot, in its original language (here English archive narration), as a transcript would give it.

Ground truth (`truth`) uses the existing `quality.Truth` schema: `accept`, `contra`, `key`, `footage_exists`, `note`.
