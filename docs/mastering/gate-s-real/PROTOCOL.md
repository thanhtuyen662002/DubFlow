# Gate S final closure: real-world evaluation protocol

Directive "DUBFLOW — GATE S FINAL CLOSURE" (2026-11-11 21:56), Epic #637. This file was committed with the labels and the scorer, **before** any planner or Vision run on the new sources and before any calibration change. Nothing in it is changed after a result is seen.

## 1. Data

- **Sources.** 12 real videos that are not in the regression set (Espresso, Máy thổi bụi, Hũ tăm, B1, B5, Why We Fight). They were chosen from what is on the owner's PC (`_sandbox\gate-s-final\inventory.md`). Each was split into REAL-DEV or REAL-HOLDOUT **before** any run.
- **Groups.** Household products, entertainment (live action and AI drama, animation), educational/technical, and long-form documentary. No electronics/tools source exists on the PC apart from the regression video, so that group is missing. This is reported as a limit; nothing was downloaded to fill it.
- **Labels.** These are **agent labels**: written by Claude from the video's own frames, at 1 frame/s, or 1 frame per 2 s for videos over 5 minutes. Spans were checked again at the exact seconds. The narration was written for each video from the same frames, before the planner or the Vision model saw the video.
- **Integrity.** The label files' sha256 are in `tests/mastering/real/HASHES.txt` and were committed before the first scored run.
  - A label is never edited after a result is seen.
  - If REAL-HOLDOUT is used to fix anything, it becomes dev and a new holdout is needed.
- **Workflows.** Household videos run through Bán hàng (product); the others through raw Tổng hợp (one item per video, keep source audio off).

## 2. Runs

- **Fresh.** The rc build's own engine runs each video with real Vision (`tests/mastering/real/capture.py` wraps the job and records every planner call). The record holds the candidates, every evidence item as the model answered, the narration with spoken lengths, and the plan and decision log the run produced.
- **Replay.** `capture.replay` plans each recorded call again with the code checked out now. It uses the same evidence and makes no AI call. Old code and new code are compared on identical inputs. Replay starts from the evidence after the targeted second look. It does not re-ask the model.
- **Scoring.** `python -m tests.mastering.real.evaluate <labels> <results> [--replay]`.

## 3. Per-sentence definitions (fixed)

A sentence's **verdict** is what the desktop shows: `review.annotate` over its plan, giving READY, NEEDS_REVIEW, MATCH_NOT_FOUND or SOURCE_INSUFFICIENT. **Flagged** means anything but READY. A user's approval never counts.

**Footage measures**

| Term | Definition |
|---|---|
| need | Timeline ms of the sentence's pieces. |
| accepted | Timeline ms of those pieces that fall on its *acceptable* spans. Partial spans and contra spans count 0. A neutral repeat, hold, filler or wrong object counts 0. |
| contra | Timeline ms on its *contra* spans. |
| stolen | Timeline ms on another sentence's acceptable spans that are not this sentence's own, when that other sentence's acceptable footage lasts less than 1.5 × its need. |
| coverage | accepted / need, in ‰. |

**Labelled state of the sentence**

| State | Truly correct | Serious error |
|---|---|---|
| MATCH_AVAILABLE | coverage ≥ 900 ‰, contra 0 and stolen 0 | coverage < 500 ‰, or contra > 0 when `serious_if_wrong` |
| NOT_NEEDED | contra 0 | contra > 0 when `serious_if_wrong` |
| SOURCE_INSUFFICIENT | never | when `serious_if_wrong` (whatever is shown does not show it) |

**Eligible** means MATCH_AVAILABLE, and its acceptable spans together last ≥ 900 ‰ of its need.

## 4. Totals and pre-registered REAL-HOLDOUT thresholds

| Measure | Definition | Threshold |
|---|---|---|
| Coverage | Σ accepted / Σ need over MATCH_AVAILABLE sentences | ≥ 90 % |
| Serious silent misses | serious error and READY | 0 |
| Serious contra READY | contra > 0, serious, READY | 0 |
| READY precision | truly correct READY / READY (no READY at all fails) | ≥ 95 % |
| Review recall on serious errors | flagged serious / serious | 100 % |
| False-alarm rate | flagged truly correct / truly correct | ≤ 25 % |
| READY rate | READY eligible / eligible | ≥ 60 % |

- Numerators and denominators are published.
- Any miss means Gate S **FAIL**.
- The thresholds are not changed after a result.

The **reliability bins** show piece confidence (0–299, 300–449, 450–599, 600–749, 750–1000) against the share of the piece's time on acceptable footage. They are reported, not thresholded.

## 5. Calibration rules

- Tune on REAL-DEV and the existing dev sets only.
- Light methods only: score mapping, threshold calibration, per-provider analysis. No new model. Do not simply lower REVIEW_BELOW.
- Each change needs a repro, a test that fails before and passes after, the regression comparison on every existing set, and its effect on REAL-DEV.
- One controlled round. If REAL-HOLDOUT still fails, the result is FAIL, and "Assisted Editorial Review" is proposed as a product option, not as a pass.
