# holdout7 — the third Gate S independent set

Written 2026-11-11 on main `01f8f4d`, after holdout6 was evaluated once (92.3 % coverage, **1 silent miss**: a call to action whose words appear on a text card), and **before** any change made for that. It is frozen in `FROZEN.json` in this PR (#736) and not run here. The change is developed on the dev sets only; holdout7 is then evaluated once and reported as it comes out.

**Disclosure.** The same agent wrote it after seeing the holdout5 and holdout6 failures (hooks showing a later sentence's shot; text on a card). It was written from product and topic descriptions without running any planner on it, and no label is changed after the run.

42 sentences in 9 scenarios over 9 content groups (electronics, household, food, fitness, entertainment, travel names, education, pets, beauty); 41 have footage, one (`holdout7-printing-press` u5, a portrait) has none and must be flagged. Format as in `HOLDOUT5.md`. Not part of `runner.load("all")`.
