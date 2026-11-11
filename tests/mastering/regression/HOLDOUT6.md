# holdout6 — the second Gate S independent set

Written 2026-11-11 on main `9368e52`, after holdout5 was evaluated once and failed check A (key footage of a later sentence used by the opening hook: 8500 ms in three scenarios, one of them not flagged), and **before** any change made to fix that. It is frozen in `FROZEN.json` in this PR (#732) and is not run here. The check-A change is developed and checked on the dev sets only; holdout6 is then evaluated once, and the result is reported whatever it is.

**Disclosure.** The same agent wrote this set after seeing the holdout5 failure, so it knows the failure mode it is meant to test (an opening sentence about a result that a later sentence shows). That makes it a fair test of the hook rules but not a blind sample of real videos. It was written from the product and topic descriptions only, without running any planner on it, and no label is changed after the run: a wrong label is reported as wrong, and a corrected set would be a new set.

44 sentences in 9 scenarios over 6 content groups:

| Scenario | Group | Mode | Sentences |
|---|---|---|---:|
| coffee-grinder | kitchen | reorder | 5 |
| rice-noodle-pot | kitchen | reorder | 5 |
| hair-straightener | beauty | reorder | 5 |
| sunscreen | beauty | reorder | 5 |
| camping-lantern | outdoor | reorder | 5 |
| jump-rope | outdoor | reorder | 4 |
| top5-fails | entertainment | forward only | 5 |
| japan-trip | travel-names (multilingual place names) | reorder | 5 |
| solar-system | education | reorder | 5 |

43 sentences have footage; one (`holdout6-solar-system` u5, Pluto) has none and must be flagged, never silently READY.

Format: the same as `HOLDOUT5.md` (`group`, candidate `frames`, `transcript`; ground truth in the `quality.Truth` schema). It is not part of `runner.load("all")`; the runner summary knows the `holdout6-` prefix.
