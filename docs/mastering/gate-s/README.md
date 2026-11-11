# Gate S evaluation records (Issue #730)

| File | What |
|---|---|
| `holdout5-0a633a9.md`, `.json` | The first and only evaluation of the independent held-out set `holdout5.json`, run on commit `0a633a9` (main `52e099d` + the evaluation script) at 2026-11-11T06:20:25Z. Nothing in the planner, its weights or the labels changed after it. |
| `holdout6-01f8f4d.md`, `.json` | The single evaluation of `holdout6.json` (frozen by #733 before the check-A change #735), run on main `01f8f4d` at 2026-11-11T06:38:55Z. |
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

## holdout6 result (after check A, #735)

| Criterion | Bar | holdout6 | |
|---|---|---|---|
| Duration coverage | ≥ 90 % | 92.3 % (132 000 of 143 000 ms) | pass |
| Silent misses | 0 | **1** (rice-noodle-pot u5 "Miễn phí giao hàng hôm nay": its words are on a text card, the card rule set the card aside, the sentence counted as a generic call to action and was filled with neutral footage without a flag) | **fail** |
| Contradicting footage | 0 | 0 ms | pass |
| A. Stolen key footage | none unflagged | 3 000 ms (jump-rope u1, flagged: the opening has no match and the counter shot was claimed by no sentence) | pass (flagged) |
| Sentence without footage | flagged | flagged, evidence SOURCE_INSUFFICIENT | pass |
| Timeline problems | none | none | pass |

Per group: beauty 98.4 %, education 100 %, entertainment 100 %, kitchen 91.9 % (1 silent), outdoor 71.9 %, travel names 100 %. Only the rows of the two scenarios named above were read after the run. The next change is evaluated on holdout7, frozen by #736 before it.
