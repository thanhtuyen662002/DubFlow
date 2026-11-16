# D1 / cue lineage / hard negatives: closing conclusions (plan phases 3–6)

Date 2026-11-16. Evidence level: **ENGINEERING_EVALUATION** (`SOLO_AUTOMATION_TRACK.md` §1). Paid API: **$0**. No frame, audio or video left the machine (scope `transcript_text_only`). No READY threshold, Gate B/D, #844 or production default changed. Formal X2 evaluation stays **FORMAL_EVALUATION_BLOCKED** (no human labels).

Each phase below ends in one of: **ADOPT** (turn on by default), **KEEP OFF** (experiment kept, default off, with the measured reason), or **CLOSED NEGATIVE** (do not pursue at this evidence level).

## Phase 3 — D1, play the right part of the shot: **KEEP OFF (timing win, picture net zero)**

Tool: `tests/mastering/x2/d1_phase3.py` (locked by `test_d1_phase3.py`). It scores every D1 rule on the picture-truth scenarios of `d1_bench.py`, plus one mirror scenario `picture_with_speech` (inside one unsplit shot, the thing appears when it is named). `d1_bench` alone only had the pan where the picture comes *before* the speech, so it could show a D1 loss but never a gain.

Reorder + forward-only, 13 scenarios, 48 units with footage:

| Rule | Played over its cue | Picture-right ms (of 150,000) | Picture worse / better | Shots changed | New timeline problems | READY changed |
|---|---|---|---|---|---|---|
| off (production) | 22 | 114,000 | — | — | — | — |
| engine `cue_inpoint_lead_ms` 0 / 150 / 300 / 500 | 42 | 114,000 | 2 / 2 | 2 (montage) | 0 | 0 |
| engine 800 | 42 | 113,400 | 2 / 2 | 3 | 0 | 0 |
| `d1_same_shot.reanchor` | 30 | 108,000 | 4 / 2 | 0 | 0 | 0 |
| `d1_same_shot.reanchor` + clamp (#881) | 44 | 108,000 | 4 / 2 | 0 | 0 | 0 |

Reading:
- Every rule moves far more pieces onto their cue (22 → 42–44 of 48). That is the timing defect D1 was about, and it is fixed.
- On the picture, the gain and the loss are exact mirrors: the rule wins when the thing appears as it is named and loses when it was shown first. A transcript cue cannot tell those two apart. Net picture change of the engine rule: **0 ms**.
- The lead does not matter between 0 and 500 ms. 800 ms starts to cost picture. 300 ms stays the tested value.
- The same-shot rule never changes the shot, but it ignores vision evidence, so it also moves pieces whose keyframe description already matches (`picture_before_speech_seen`). It loses 6,000 ms more picture than the engine rule. It stays a research tool and is **not** ported to `engine/`.
- Real d-s2 (Owner spot checks 2026-11-16, not labels): better p3, worse r1, tie p5/m1, unclear p4. That agrees with "net zero".

Decision: `cue_inpoint_lead_ms` stays **off** on every production path. Turning it on needs per-shot picture evidence of *when* the subject is visible (frames or a vision description at the cue time). That is outside the $0 / transcript-only scope, so Phase 3 closes here. Issue #867 stays the place for that decision.

## Phase 4 — cross-language cue lineage: **KEEP OFF (works when cue refs exist; no producer of cue refs at $0)**

Tool: `tests/mastering/x2/cue_lineage.py` (fixture, re-run on main `95c99278`) plus the REAL-DEV runs already recorded in `/mnt/project-files/reports/bao-cao-tong-hop-d1-x2-lineage-so.md`.

| Fixture, reorder | none | lexical | region | ranked |
|---|---|---|---|---|
| Top-1 right (of 10) | 1 | 3 | **7** | **8** |
| Cited cue covered | 0 | 2 | 7 | 5 |
| No-footage units given footage (of 5) | 0 | 1 | 1 | 2 |
| READY | 0 | 0 | 0 | 0 |

The engine wiring (`align.assign(..., lineage=...)`) gives 6/10 top-1 in both modes. In reorder mode it adds one REPEAT (region) or two (ranked) timeline problems that the prototype does not have.

REAL-DEV (Vietnamese sentences over an English source, d-s1): footage touching the cited cue, off 2/12 → region 7/12 (reorder).

Decision: lineage stays an off-by-default planner input. It needs narration sentences that cite cue ids. The only producer designed for that is the 1a script draft, which is a paid call ($2) that is not approved. Without it nothing in the product makes cue references, so turning lineage on changes nothing. The REPEAT in the engine reorder wiring is recorded here as the first thing to fix if lineage is ever turned on.

## Phase 5 — wrong-meaning footage (hard negatives): **SAFETY HOLDS; assignment-level blocking CLOSED NEGATIVE at this evidence level**

Tool: `tests/mastering/x2/hard_negatives.py` on the `solo_bench` video fixture.

| Kind | Units | Offered footage | Assigned | Reviewed | READY |
|---|---|---|---|---|---|
| entity | 2 | 2 | 2 | 2 | 0 |
| action | 2 | 2 | 2 | 2 | 0 |
| time | 1 | 1 | 1 | 1 | 0 |
| none | 2 | 0 | 0 | 2 | 0 |

Final QC blocks the part. The word-contrast prototype flags 5/5 negatives on the fixture, but also 1/9 positives there (the montage) and 5/6 real positives on REAL-DEV d-s1. The earlier number/name blocker flagged 10/12 correct paraphrases on d-s2. The number check alone (#873/#874) is precise on its 24-case independent set: 10/12 caught, 2 unknown, 0/12 false alarms. It covers time and number negatives only.

Decision:
- Safety invariant: **PASS**. No hard negative reaches READY on any arm, fixture or REAL-DEV.
- Blocking wrong-meaning footage at assignment time from transcript words alone: **CLOSED NEGATIVE**. Lexical evidence cannot tell "same name, other action" from a paraphrase without false alarms. It needs picture evidence or source-linked claims.
- #844 stays design-only (5/23 false warnings). The number check stays a research tool. It is not wired into the planner, because any wiring would change READY behaviour, which needs the Owner's gate.

## Phase 6 — integration and regression: **PASS (nothing to integrate by default)**

- Phases 3–5 add no production change. Integration means: the experiments stay importable and off, and their tests run in the `mastering` CI component.
- Editorial regression (`python -m tests.mastering.regression.runner all`): output identical before and after this change (the change touches only `tests/mastering/x2/` and this document).
- `mastering` component: 857 tests on CI for main + #861, OK (24 skipped).

## What would reopen these phases

| Phase | Needs |
|---|---|
| 3 (D1 on by default) | Per-shot evidence of when the subject is visible: frames or vision at the cue time. Today that is a scope + cost decision |
| 4 (lineage on by default) | A producer of cue-citing narration, e.g. 1a ($2, not approved), and a fix for the reorder REPEAT |
| 5 (block at assignment) | Picture evidence or source-linked claims. #844 needs a design that does not warn on correct READY units |
| Formal X2 | Human labels. Blocked by the solo-founder constraint |
