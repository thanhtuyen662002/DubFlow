# Gate S evaluation records (Issue #730)

| File | What |
|---|---|
| `holdout5-0a633a9.md`, `.json` | The first and only evaluation of the independent held-out set `holdout5.json`, run on commit `0a633a9` (main `52e099d` + the evaluation script) at 2026-11-11T06:20:25Z. Nothing in the planner, its weights or the labels changed after it. |
| `runner-all-before-be8b201.txt` | Regression runner on main `be8b201` (before the Gate S changes #723, #725). |
| `runner-all-after.txt` | The same runner on `0a633a9`: byte-identical to the line above. |
| `<set>-after.md` | `evaluate.py` on the old sets at `0a633a9`. The script did not exist on `be8b201` (its runner has no decision log), so the before side of those is the runner line, which is identical. |

## holdout5 result against the Gate S criteria

| Criterion | Bar | holdout5 | |
|---|---|---|---|
| Duration coverage (sentences with footage) | ≥ 90 % | 93.9 % | pass |
| Silent misses | 0 | 0 | pass |
| Contradicting footage | 0 | 0 ms | pass |
| A. Hook / early sentence takes a later sentence's key shot | none | 8 500 ms on 3 videos (speaker 2 500 ms not flagged; lunchbox 3 000 ms and top5-pets 3 000 ms flagged) | **fail** |
| Sentence without footage reported honestly | flagged, not READY | flagged (NEEDS_REVIEW), but its evidence state says MATCH_AVAILABLE instead of SOURCE_INSUFFICIENT | partial |
| Timeline problems (repeats, gaps, backwards in forward-only) | none | none | pass |

After the run, only the rows of the three units with stolen key shots (and the one sentence without footage) were read, to write this table. holdout5 stays `tuned_on: false`; any fix of check A is evaluated on a new set frozen before that fix.
