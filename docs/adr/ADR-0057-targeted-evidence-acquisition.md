# ADR-0057: Targeted Evidence Acquisition for Long-Form Assisted Editorial Retrieval

Status: Proposed (not approved). Epic #637. Issue #815, task #829, PR #831 (supersedes #830).

Builds on ADR-0053 (raw footage compilation) and ADR-0054 (editorial layer). ADR-0056 stays reserved for documentary
narrative presets, as ADR-0054 states. Evidence:
[TARGETED_EVIDENCE_FEASIBILITY_REPORT.md](../mastering/TARGETED_EVIDENCE_FEASIBILITY_REPORT.md) (the Stage 2
feasibility report), [stage2-offline-results.md](../mastering/assisted-e2e/stage2/stage2-offline-results.md) (run
summary) and [LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md](../mastering/LONG_FORM_RETRIEVAL_FEASIBILITY_REPORT.md) (the
earlier study that proposed this work in its §17).

## Status of the E3–E5 numbers (2026-11-14)

The E3–E5 replay numbers in this ADR (recall from saved answers, cache coverage, E5 attribution deltas) come from the
`716e984` run. That run matched donor answers by JPEG bytes and by the run-level provider and model. It did **not**
verify the full production cache key: prompt version, provider, model, label set and JPEG bytes, with the logged answer
equal to the cached record. Lead review found this gap. The harness now admits an answer only under that exact key
(`a558396`, PR #831).

**Exact-key recompute (exploratory, 2026-11-14).** E3–E5 were recomputed from the same five saved runs on the owner's
PC at `a558396`, `b17bb53` and, after a second Lead review, at `468cb05`. The final rule takes a legacy key basis only
from the provider and model the run spec asks for as a pair, or from a record's own `made_by`, never from the run
reports (which keep the model that answered). All three re-runs are labelled PREREGISTRATION MISMATCH (exploratory) by
design, and runs A and B were byte-identical each time.

- rc16, the primary run and the source of every E3–E5 number in this ADR: the key basis is proven from `made_by`. Its
  report section is unchanged by the final rule. Against `716e984` only one simulated frame fewer in `open:A+` at 4x
  (5/160, was 6/160) and in the closed-world `A+` and ceiling arms (19 frames, was 20); no recall, fixed, broken,
  silent-miss, READY or warning value changed.
- rc12–rc15, the supporting runs: their specs name no model and their records have no `made_by`, so no key can be
  proven. They simulate nothing, and their E3–E5 values fall back to their own baseline. They serve only the E2
  decision, which is selection-based and identical at every head.

**Classification fix after the recompute (`7d90f35`).** A further Lead review found that frame classification (which
looks count as targeted, and so what A0 removes) still took the presence of a key as proof. At `7d90f35` it needs the
same answer proof as donor matching: a frame is targeted only when its own record under the proven key is the answer it
logged and no broad or earlier frame holds the same JPEG bytes; otherwise it is shared (a proven look in its group gave
the answer) or unproven (kept in A0). A0 also clears the product box, hook and product fields of the answers it
removes, and replayed answers set them from the record, as production does. The PC re-run at `7d90f35` (exploratory,
PREREGISTRATION MISMATCH, runs A and B byte-identical) changes no arm in any run: every E2, E3, score, delta and E5
value equals the `468cb05` recompute, and A scores and attribution are unchanged. Only rc16's classification moves:
10 frames that hold the same JPEG bytes as an earlier targeted frame are now shared (targeted 43, shared 14, was 53 and
4; targeted frames in item parts 31, was 40), no frame is unproven, and the early-bias sample has 35 units (was 39).

**Fail-closed fallback (`4607031`, historical).** Without a single answer-proven basis (no cache, none proven, or
more than one), the harness no longer infers targeted or shared looks from grouping: every answer outside the
reconstructed broad pass is unproven and A0 keeps it. The PC re-run at `4607031` (exploratory, A = B) leaves the
decision table, rc16 (all 151 arms, classification and markdown section) and the primary and comparator arms of every
run unchanged. In rc12–rc15 (no provable key) every non-broad answer is now unproven (59–63 per run, 0 targeted), so
their 12 B0/respend arms each fall back to that run's own baseline (fixed/broken 0/0, r@5 equal to A); no other arm
changes. A scores and attribution are unchanged in all five runs. The rc16 E3–E5 numbers here held at `4607031`.

**Later review fixes (`2b24f93` to `e6d000f`).** Further reviews tightened classification and A0 (shared copies carry
the whole answer, split pieces, answered broad groups, side metadata in the own-record proof; run record §16). The
latest completed Owner-PC re-runs, at `4558305` and `669f298` (exploratory, A = B), change no decision value, no rc16
arm and no rc16 class; rc12 moves one id (multi_frame to unproven) and its 6 open B0 arms. `e6d000f`
makes the own-record proof also compare the side metadata; its Owner-PC re-run (exploratory, A = B) equals `669f298`
in every class, arm and decision value (rc16: 148 own records exact, 0 side mismatches). The code head
`234a159` (after `a8d82ed`, `42b5195`, `52d9c06`, `ee277bd`, `1d4ebfb`, `567efee` and `ba9ba8b`) pins the selected run folder
(never following the selected entry itself), opens every part relative to its folder's handle on Linux and Windows,
reads every run file once, only as a single-named regular file inside that pinned folder, parses the run from those bytes, and makes the donor proof also
compare the side metadata. Its Owner-PC re-run (exploratory, A = B) equals `e6d000f` in every class, arm and decision
value; on rc16 three donor records fail the side-metadata proof and are no longer replayed, which changes no E3–E5
number (run record §16). The current code head `a41ccef` lets only a proven broad answer make a byte-identical frame
in another duplicate group a shared copy; its Owner-PC re-run (exploratory) equals `234a159` in every class, arm and
decision value. The rc16 E3–E5 numbers here hold at `a41ccef`.

The rc16 E3–E5 numbers here were only ever a partial simulation, and the recommendation does not rest on them. The pre-registered E2 decision ("not
supported") does not use donor answers and stays the decision of record at `716e984`.

## Decision (proposed)

Do not implement script- and time-guided targeted acquisition (Option B) in the production planner now. A pre-registered
offline test on W3 REAL-DEV found that the planning-time signals available today (item trim, unit position, the
neighbours' chosen shots, the existing scores) did not put frames on the right shots significantly or consistently more
often than the current production rule or a shuffled control, and not more often than chance. On rc16 the primary arm
placed a frame inside the right range for 2 R3 units, against 1 for the production rule, 0 for the shuffled control and
2.95 expected by chance (exact McNemar p = 1.0 against the production rule); the pre-registered result is "not
supported". The current broad plus production-targeted acquisition stays as it is. A larger uniform budget (Option A)
gave no significant gain at up to 4x, on a partial simulation. A semantic model or embedding (Option C) is outside this
directive and would need its own ADR. Human-guided timestamps (Option D) are the only in-scope option whose location
source was not among the signals tested here (Option C is untested and outside this directive), but they are a product
and UX question that needs its own issue and evidence. A combined broad plus targeted design (Option E) inherits Option
B's unsupported localisation. Further evidence needs an owner-approved, bounded real-call experiment and/or a NEW
REAL-HOLDOUT; any implementation approval needs both (§22). The conclusion is **GENERALIZATION NOT VERIFIED**. Long-Form
Production Gate B is not claimed and stays FAIL.

## 1. Context

Stage 1 (#821, #816, #817, Release Safety Closure) is PASS within the verified scope by owner decision A of 2026-11-14
00:57 UTC. That scope is the Windows Desktop UI E2E of the existing journeys (CDP-driven DubFlow.exe 1.1.96-rc18
candidate, `publish=false`; CDP automation is not human review), the Windows packaged-engine integration tests (B1
fallback, same-run resume, unchanged-input rerun) and unit and CI tests. The UI action "Thử lại" creates a new run;
same-run resume is verified at engine level only. The limits stated in the PR #827 report are kept. Gate B and Gate D
are unchanged. There is no stable release and no Phase 4.

Stage 2 of the owner directive of 2026-11-13 (§14 to §26) asks whether vision can be sent to the parts of the source
that are likely to hold the right shot, instead of spreading frames almost evenly. It allows only this ADR and bounded
offline experiments. It does not allow a production planner change, a new model, a GPU, a new retrieval engine, a
large cloud AI budget increase or a default workflow change (§26).

The earlier feasibility study measured, on rc16 of the W3 set: recall@5 31/60 (51.7 %), 36 misses, 26 of them at
evidence acquisition (the right shot was never shown to vision). An oracle that only fixes interpretation of the shots
already seen reaches 34/60; an oracle that also supplies the right evidence on every right shot reaches 56/60. Both
are offline upper bounds that use ground truth, not achievable results.

The offline experiments live in `tests/mastering/longform/` (`targeting.py`, `targeted.py`, `targeted_prereg.json`,
`test_targeted.py`; PR #831, which supersedes #830; task #829). They read saved runs only, make no AI call, use no network and no key, and
import nothing into production. No file under `engine/`, `apps/`, `contracts/` or the component registry changes. They
ran on the owner's Windows PC, CPU only, Python 3.12, on worktree head `716e984` (the pre-registration commit
`c60ab02` is an ancestor); at that head the longform suite reported 71 tests OK (1 skipped). This is an offline PoC, not a desktop
E2E, and it says nothing about production readiness.

## 2. Current failure analysis

Dataset: W3 REAL-DEV (Why We Fight, long form, 60 sentence units with footage, ground truth sha256 `afa979d2…f66497`,
equal to its lock). Five fresh-cache runs, rc12 to rc16; rc16 is the primary run. The runs are not independent: they
share footage, ground truth and, for the replay experiments, four of five donors.

E1 baseline (saved runs, offline replay with the existing `w3.py`):

| Run | r@1 | r@3 | r@5 | Chosen hit | Silent misses (saved) | READY precision | Vision answers (budget 142) |
|---|---:|---:|---:|---:|---:|---:|---:|
| rc12 | 17 | 28 | 31 | 25 | 0 | 5/5 | 141 |
| rc13 | 20 | 31 | 33 | 26 | 2 | 5/7 | 139 |
| rc14 | 20 | 24 | 28 | 23 | 0 | 6/6 | 139 |
| rc15 | 21 | 27 | 29 | 25 | 3 | 5/8 | 139 |
| rc16 | 20 | 26 | 31 | 24 | 0 | 4/4 | 138 |

The silent misses of rc13 and rc15 are under the saved review states; the #802 replay brings them to 0.

Earliest losing stage per unit (`w3.py attribute`):

| Stage | rc12 | rc13 | rc14 | rc15 | rc16 |
|---|---:|---:|---:|---:|---:|
| OK | 25 | 26 | 23 | 25 | 24 |
| R2 (unusable right shot) | 1 | 1 | 1 | 1 | 1 |
| R3 (right shot never looked at) | 24 | 23 | 28 | 25 | 26 |
| R4 (looked, weak or no shared words) | 4 | 3 | 3 | 1 | 3 |
| R5 (ranking) | 3 | 3 | 2 | 5 | 3 |
| R6 (behind the cursor or taken) | 3 | 4 | 3 | 3 | 3 |

R1 (source outside the trim) and R2 discovery (no candidate covers the range) are 0 in every run. The single R2 unit
is i18.u2, whose only covering candidate is marked too dark and is therefore never shown to vision.

Oracle upper bounds (use ground truth), r@5 saved / replayed / interpretation only on shots already seen / acquisition
and interpretation on all right shots: rc12 31/32/37/56, rc13 33/33/33/56, rc14 28/28/33/56, rc15 29/30/34/55, rc16
31/31/34/56.

On rc16 the review-free replay used as baseline A in Stage 2 gives r@1 20/60, r@3 26/60, r@5 31/60, chosen 24/60,
READY precision 5/5, serious silent misses 0. Evidence-acquisition coverage (a unit counts when some observed frame or
candidate overlaps its ground-truth range) is 27/60 by frame and 29/60 by candidate.

Interpretation is not the main limit: when the right shot was seen, matching usually found it (R4 is 1 to 4 per run,
and the interpretation-only oracle adds 0 to 6). Acquisition is: in every run 23 to 28 of the 60 units lose because
vision never looked at the right shot. Earlier reports also show that a cold run's answers vary: recall@5 moves by
about two to three units between runs on the same footage, the five runs looked at 154 to 161 shots each with 143 in
common and a union of 184 of about 712 in-trim candidates, and no vision text was identical across runs.

## 3. Problem statement

A Long-Form Assisted Tổng hợp job shows vision about 139 frames out of about 710 candidates with a keyframe on W3 (710
on rc16). The broad pass spreads two thirds of the budget evenly; the targeted pass then gets a fixed share of 2
frames per part and starts at the beginning of each weak unit's forward window. For 23 to 28 of 60 units the right
shot is never shown. The question for #815 is whether information available at planning time, without ground truth,
can send the limited frames to the right shots, at a bounded and stated cost, without adding silent misses, false
READY units or needless warnings, and without changing the matching, ranking or allocation that already work once the
right evidence exists.

## 4. Goals

- Use the script and the sources' time information to choose where vision looks, for Long-Form Assisted only.
- Prefer regions likely to hold the right shot, inside a hard, stated budget that never grows without limit with
  duration.
- Reuse the vision cache and the existing keyframes wherever the frame is the same.
- Keep source provenance and the source timebase exact for every new piece of evidence.
- Add no silent miss and no false READY; report missing evidence precisely.
- Reuse the existing matching, ranking and allocation; do not rebuild the retrieval engine.
- Decide from pre-registered, GT-blind evidence, with oracle results kept apart.

## 5. Non-goals

- Any production change in this stage: planner, engine, app, contract, default, recipe or UI.
- A new model, embedding, OCR recognizer, GPU path or retrieval engine (§26 stop conditions).
- A large increase of the cloud AI budget, or any AI requirement for Mastering Off or for workflows that do not use
  AI.
- A recall@5 target such as 85 %, a Documentary planner, a timeline editor, a Mastering rewrite or a semantic scoring
  change across the system.
- Claiming Gate B, generalization, production readiness or desktop E2E from the offline PoC.
- Starting the Documentary Pilot or changing the Documentary Auto verdict.

## 6. Existing architecture

Paths are under `engine/dubflow/` unless stated.

| Stage | Code |
|---|---|
| Source and programme | `compilation/program.py` (source trim `start_ms`/`end_ms`, transcript, notes; `vision_budget` 0 to 400, default 80) |
| Scene detection | `mastering/analysis.py` (one pass: scene cuts, 5 fps grey thumbnails, silences; cached by content) |
| Candidate generation | `mastering/candidates.py` (shots up to 12 s are one candidate, longer shots are 6 s windows, `MAX_CANDIDATES` 400, one keyframe JPEG per candidate) |
| Evidence, non-vision | `mastering/planners/compilation.py` (`evidence_for`: transcript in span, source and item notes, file name) |
| Evidence, vision | `compilation/raw.py` (broad, targeted and multi-frame passes), `mastering/vision.py` (`Describer`, `allocate`, `targets`, `spread_order`), `mastering/frames.py` |
| Matching and ranking | `mastering/align.py` (`Scorer`, chosen shots plus 3 alternatives) |
| Allocation | `mastering/align.py` (forward-only cursor, coverage status) |
| Review | `mastering/review.py` (evidence and unit states) |

**Budget.** One budget per job: `auto_budget = max(base, min(400, 2 × sentences))` when the spec sets none (W3: 142
for 71 sentences). The broad pass gets `budget − budget // 3` when the pool is larger than the budget (W3: 95), shared
round-robin over the parts, each part's usable candidates in van der Corput order, one frame per dHash duplicate
group. What is left is split into an equal fixed share per part, intro and outro included (W3: 47 // 22 = 2 frames per
part). The targeted pass takes `min(room, max(8, room // 2))` of a part's share, so on W3 it takes both frames and the
multi-frame pass never runs in items. Frames a hook, intro or outro part spends are not visible to the items. Every
frame, cached or failed, counts against the budget.

**Frame time.** Each candidate's keyframe is its middle frame; if that frame is dark, flat or blurred, the 25 % and
then the 75 % frame is tried. The grab time is not stored, so a fallback grab cannot be told apart later. Extra frames
(multi-frame pass) are at 15 % and 85 % of the window.

Audit answers (§15 of the directive):

| # | Question | Answer on the current code and W3 |
|---|---|---|
| 1 | Does the candidate pool cover the right source ranges? | Yes for W3-style trims: R1 0 and R2 discovery 0 in all runs, 59/60 units have a usable covering candidate. Not guaranteed for untrimmed sources with more than 400 windows (evenly subsampled) or very short shots (under about 640 ms, i.e. under 400 ms once the 120 ms cut margins are removed). |
| 2 | Is the right shot in the pool but never seen by vision? | Yes, and it is the main failure: R3 23 to 28 per run, 26 of 36 misses on rc16. Causes: 2 targeted frames per part; the targeted pass runs only for units flagged partial or none and `needs_review`; unusable candidates are never shown; the targeted pass starts early in its window; a failed call is marked as looked. |
| 3 | Does sampling concentrate on early regions? | The broad pass does not (van der Corput starts in the middle). The targeted pass does: it takes the first unseen shot of each weak unit's forward window. On rc16 the production targeted picks fall in the four quarters of their windows as 20, 5, 5 and 9 (n 39, median at 0.201 of the window). |
| 4 | Is the budget split per source, scene or job? | Per job, then round-robin per part (broad), then an equal fixed share per part (targeted and multi-frame). Not per source, scene or sentence. |
| 5 | Does the script carry locating information? | No. Units carry text and narration-timeline milliseconds, which are output time, not source time. The only locators are the item trim, unit order, the neighbours' chosen shots, transcript overlap when cues exist, and scores after the broad pass. |
| 6 | Are user citation ranges used? | Only item-level trims (105 s per W3 item), which bound candidates and both passes. The Tổng hợp spec has no per-sentence citation field; the documentary path's `matched_source_refs` (widened by 6 s) is prior art only. Pins and exclusions act only after review. |
| 7 | Can transcript or OCR narrow the search? | Transcript can and already feeds `targets()` ranking, but only when cues exist and share words with the narration; W3 saved none. There is no OCR recognition before vision (on-screen text arrives inside vision answers), and adding one would be a new model. |
| 8 | How far can the vision cache be reused? | Only for byte-identical JPEG frames under the same prompt version, provider, model and label set, in the same output folder. Any new frame time is a miss and a paid call. All W3 runs started with a cold cache. |
| 9 | Can multi-pass analysis keep the timebase? | Mostly: all times are integer source milliseconds, candidate ids follow whole-source position and are stable across rc12 to rc16, extra frames are keyed by candidate and time. Hazards: the keyframe time is not stored, `inside_trim` keeps a keyframe that lies outside the trim, and grabs by `-ss` versus thumbnails at fixed 200 ms steps may drift on a non-zero stream start or variable frame rate (untested). |
| 10 | How is missing evidence recorded when the budget runs out? | As counts: a truncation warning without ids, call-failure warnings, the per-part `vision` report and `coverage_map` (which counts any content origin, so it is not a vision coverage map), and per-unit coverage and review states. The per-candidate reason (never selected, cut by budget, call failed, unusable, answered through a duplicate) is not recorded. |

**"Never looked at the right shot" versus "looked but did not understand".** The attribution separates them: R3 means
no candidate that hits the ground-truth range has content evidence; R4 means a hitting candidate has evidence but it
shares no words with the sentence or matches weakly; R5 and R6 are ranking and allocation. On rc16 that is R3 26
against R4 3. Three refinements matter for any future work. First, "looked" should mean vision evidence only;
`attribute` counts any content origin, which agrees with vision only because W3 had no notes or transcript. Second, R3
should split into never selected and selected but cut or failed; the log cannot tell these apart today. Third, a shot
can be seen at the wrong moment: i4.u2's candidate runs from 330.2 to 340.3 s with its keyframe at 335.25 s, while the
ground truth is 332.5 to 335 s, so a case like this counts as R4 although vision never saw the moment.

## 7. Proposed targeted acquisition flow

The flow below is the design that was evaluated. Section 16 and the report show that its Pass 2 did not localise
better than the existing rule on REAL-DEV, so it is not proposed for implementation now. It stays the shape any later
proposal must follow.

**Pass 1, broad coverage.** Unchanged from production: about two thirds of the job budget, evenly over time within
each part (van der Corput order), round-robin across parts, one frame per duplicate group, existing keyframes only,
cache first. No part of a source is left without a broad look when the budget allows it, the source and time mapping
is the candidate's, and whole videos are never sent frame by frame.

**Pass 2, script-guided targeting.** For each unit flagged weak after the first plan, rank the unseen usable
candidates of its forward window by planning-time signals only. The directive's signals and what W3 actually offers:

| Signal | Available on W3 | Used by the tested strategies |
|---|---|---|
| Citation ranges per sentence | No (item trim only) | Trim bounds every window |
| Source timestamps | Item trim, 105 s per item | `prior`: unit position in the narration mapped onto the trim |
| Transcript | No cues saved | Non-vision relevance first in `mixed` (empty on W3) |
| OCR | None before vision | Not used (a recognizer would be a new model) |
| User notes | Uniform per source, so no locating power | Not used |
| Scene metadata | Cuts, windows, dHash groups | Window bounds and duplicate exclusion |
| Current candidate scores | `Scorer` after the broad pass | `anchor`: centre on the best-scoring seen shot |

The strategies were `prior`, `anchor`, `gap` (spread over the unseen part of the forward window) and `mixed`
(non-vision relevance, then `anchor`, then `prior`, then `gap`, per unit). Controls were `uniform` (A+), the
production rule (P+) and `shuffled_prior` (each unit's prior centre moved to another unit of the same part), because
the trim was written by the person who wrote the ground truth. Signals only choose where to look; they never count as
evidence that a shot is right.

**Pass 3, targeted vision.** Look at the selected candidates' existing keyframes, inside `[centre ± W] ∩ forward
window ∩ trim`, nearest first. Several windows were tried on REAL-DEV, W = 5, 10, 15 and 30 s and the whole forward
window, and every one is reported; there is no "best" value and none is hardcoded. The ±15 s window of the earlier
study was a hypothesis and stays one. At the matched budget no window moved the pre-registered `mixed` arm off 2 R3
units with a hitting frame on rc16 (chance 2.95).

**Pass 4, ranking and validation.** Reuse `align.Scorer`, the existing ranking (chosen plus 3 alternatives) and the
forward-only allocation unchanged, and the existing READY thresholds. Validate every arm on: source range of each new
sample, candidate recall, READY safety (false READY, silent misses), extra warnings, and the per-unit attribution
change (R3 fixed, R3 moved to R4, R5 or R6, hits lost). A newly sampled shot is never assumed to be right. The harness
records, per fixed unit, whether the fix came from new evidence, a duplicate copy, or indirectly through the cursor or
ranking (spec fields `fixed_via_new_evidence`, `fixed_with_shared_only`, `fixed_indirect`); the Stage 2 record does not
quote this split, so it is not measured in Stage 2 and is required by §22.

## 8. Source/timebase contract

- Every sample carries `source_id`, a source identity digest, `candidate_id` and the frame time in integer source
  milliseconds, with its basis (`numbered_window`, `subshot_point` or `logged_mid`). Narration-timeline milliseconds
  (`Unit.start_ms`/`end_ms`) are output time and may only be used as a proportional prior, never as a source time.
- A new frame time inside an existing window becomes a new candidate row through the existing `frames.merge`
  mechanism, keyed by candidate and time, with times inside the parent window and at least 400 ms per sub-shot.
- The keyframe time and the fallback position actually used must be stored with the candidate; today they are not.
- A keyframe outside the part's trim must not be used as evidence for that part (today `inside_trim` clips the range
  but keeps the keyframe).
- Before any implementation, a test must show that grab times (`-ss`) and thumbnail times agree on sources with a
  non-zero stream start and with variable frame rate; this is untested today.

## 9. Evidence provenance

Each vision item added by a targeted look must record, beside the existing origin and `AI <provider>/<model>` detail:
the pass (broad, targeted, multi-frame), the unit that triggered it, the signal and window used, the frame time and
its basis, and whether the answer was asked, came from the cache or was copied from a duplicate. Today broad and
targeted answers carry the same detail and the cache record holds no source, time or candidate id. The offline harness
already keeps this record for every simulated sample (donor run, part, candidate, item index, match basis, own cache
or donor), and a test checks that each added item equals its donor item field by field. Provider and model names are
written to reports only as a 12-character digest.

## 10. Cache invalidation

- The cache key stays `sha256(PROMPT_VERSION | provider | model | labels | sha256(JPEG bytes))`. An answer is reused
  only for the same frame bytes; a new frame time, a new crop, a new encoder setting or a new prompt version is a
  miss.
- The context string sent with the frame (the programme title) and the other frames in the batch are not in the key. A
  targeted design that changes the prompt per unit (for example by adding the sentence) must bump `PROMPT_VERSION` or
  add that input to the key.
- The cache stays per output folder. Targeting changes which frames are asked, not what an answer means, so a change
  of targeting recipe does not invalidate answers; it does change the plan and must be part of the part's plan
  identity, so the plan is rebuilt.
- Answers are not merged across runs or providers. At `716e984` the harness matched donor answers by JPEG bytes and
  by the run-level provider and model only, which does not prove the production cache key (see the status note at the
  top). From `a558396` an answer is replayed only under its exact production cache key:
  - The target run's own cache must prove a single basis (prompt version, provider, model, label set). The basis
    counts only when its record reproduces an answer the run logged.
  - The answering run's cache must hold, under that basis's full key for the same JPEG bytes, a record whose answer
    is the logged item.
  - When `made_by` is present it must agree. A legacy record without `made_by` counts only through the computed full
    key.
  - A candidate basis is a recorded tuple only: the `made_by` of a record, or, for legacy records, the provider and
    model the run spec asked for as a pair (`468cb05`). Production keys by the model asked; the run reports keep the
    model that answered, which a fallback changes, so they never contribute a key and provider and model are never
    crossed across sources. A spec without both fields leaves a legacy run unproven.
  - Items whose `detail` names another provider or model are left out.
  - A record in the target's own cache for the same bytes wins over a donor.
  - The same proof decides which of the run's own looks count as targeted (and so what A0 removes): key presence alone
    never does, a byte-identical copy of a looked-at frame is shared, and a look without proof is kept as unproven
    (`7d90f35`).
  - Nothing is matched by time.

  Without that proof the frame is NOT_SIMULABLE, with a specific reason.

## 11. Budget control

Rules any implementation must meet. Where a number is a W3 observation it is marked as such; caps are stated as rules
so they are not tuned to W3.

| Item | Rule | Today and evidence |
|---|---|---|
| Max frames per job | The existing job budget is the hard cap: `vision_budget` 0 to 400, or `auto_budget` capped at 400. Targeted frames are spent inside it, never added on top, unless the user raises the budget. The cap does not grow with duration beyond 400. | W3: 142 per job, 95 broad and 44 targeted on rc16. |
| Max frames per source | A source gets at most its share of the targeted frames in proportion to its usable candidates, rounded up, with fair turns so no part gets 0 while another gets 2 or more. | Today there is no per-source cap; the share is per part (2 on W3). The harness's fair-turn rule is tested. |
| Max targeted retries | One targeted round per unit per run. A frame that was asked, answered or failed, is not asked again in the same run. A unit still weak after its round stays flagged for review. | Today failed frames are marked looked and never re-targeted. |
| Max vision calls | `calls ≤ Σ over parts of ceil(frames_part / 4)` (`BATCH` 4), plus the existing FORMAT retry in halves, which re-asks the same frames without adding frames. | Estimated calls per arm are in §17. |
| Cache | Cache first; a cached frame costs budget but no call. No new frame time is invented when an existing keyframe in the window is unseen. | §10. |
| Cancellation | Stop between batches. Answers already stored stay valid; no partial answer becomes evidence; the plan is not written as complete. | Requirement; not exercised in Stage 2. |
| Resume | A rerun reuses stored answers at zero calls and the same budget accounting, and the same inputs give the same selection (the selector is deterministic). | Selection determinism shown offline (byte-identical outputs). |
| Provider failure | §13. | Existing behaviour kept. |
| CPU | Selection is pure Python over existing candidates; no new decoding beyond existing keyframes. | Harness `select` about 4.4 s per run for all arms. Added production CPU not measured. |
| RAM | No new in-memory frame store. | Harness peak 350 MB. Production rc16 peak 3438 MB (earlier study). Added production RAM not measured. |
| Cloud AI cost | `cost = calls × price_per_call`, with the provider's price as a parameter. | No price per call was measured or given; the cost column is null. |
| No AI required | With no AI route, a budget of 0, a text-only model or Mastering Off, the job makes zero vision calls and targeting is skipped, not replaced. | `vision.factory` already returns no describer in these cases. |

## 12. Security

- Frames go only to the user's own AI route (the user's key, as today), as the existing 512 px keyframes. No new data
  type, endpoint or key is introduced, and nothing is sent when no route exists.
- No key, e-mail, provider or model name, absolute path, frame, narration or ground-truth sentence appears in logs,
  reports or the real-call batch. The batch holds candidate ids, times and JPEG digests only. The report JSON and
  markdown were checked to contain no absolute path; the raw `.perf.json` holds local paths and is kept off the
  repository.
- The ground truth is read only by the scoring and ORACLE code. The selector module has an import allowlist, never
  receives a ground-truth object, and is checked by a subprocess that re-runs every non-oracle selection without a
  ground-truth argument and must produce byte-identical samples (PASS on all five runs).
- Any per-sentence timestamp that a user supplies (Option D) is user content and must be stored like notes, inside the
  job folder, never sent anywhere except with the frame request.

## 13. Provider failure fallback

The existing fallback stays: when the provider reports a key, balance, quota, limit, model or refusal error, the job
stops asking for the rest of the job and plans on the evidence it has; a FORMAT error is retried in halves; a failed
frame is counted in the budget and not re-asked in the run. Targeting adds no new failure mode: if Pass 3 cannot run,
the plan is the Pass 1 plan, which is today's plan without its targeted frames. Missing evidence must then be reported
per candidate (never selected, cut by budget, call failed, unusable, duplicate) and per unit, so a unit whose right
shot was never seen is never stated as "source insufficient". Today a source with notes can make unseen footage count
as covered and lead to `SOURCE_INSUFFICIENT`; the existing test does not cover notes, and that gap must be closed
before any targeting change.

## 14. Human review behavior

- READY thresholds do not change (confidence at least 600, coverage at least 900 per mille). A shot found by a
  targeted look is shown for review like any other and never becomes READY because it was targeted.
- New evidence can lift a wrong shot over the READY threshold (rc13 i16.u1 went from 519 to 640 in an earlier study),
  so READY gains are tracked separately for hits and misses.
- The review shows, per unit, whether the right shot could have been seen: which shots were looked at, by which pass,
  and which were not looked at and why.
- Review edits (pins, exclusions, approvals) act only after review and are partial ground truth, so they are excluded
  from every experimental arm. A secondary regime with review edits exists in the harness (`--review-edits on`), but
  no result from it is part of the Stage 2 record; it is not measured.
- In every rc16 arm (the supporting-run rows were not reviewed for this ADR), ΔfalseREADY and Δwarnings were 0 and
  serious silent misses stayed 0; READY precision stayed 5/5 except A+ at 4x (6/6). These are partial simulations
  (§15).

## 15. Evaluation methodology

**Datasets.** W3 REAL-DEV is development data: it was used to design the harness and the arms, so it is never called a
holdout, and its results are **GENERALIZATION NOT VERIFIED**. The name "W3 REAL-DEV" keeps it apart from the Gate S
sets in `tests/mastering/real/`. A NEW REAL-HOLDOUT, needed before any quantitative general claim, must have: at least
40 sentences; at least 2 independent long videos (not W3 footage); real footage; easy and hard sentences; ground truth
locked by sha256 before the final evaluation; no use for choosing a budget, window or threshold; and no label change
after results are seen. Its arms, metrics and decision rule are pre-registered before the ground truth is used, in the
same way as below.

**Pre-registration.** `targeted_prereg.json` was committed at `c60ab02`, before any real-data run, with the selector
digest (`targeting_sha256` `fd884a26…`) and the analysis digest (`analysis_sha256` `4dff01c5…`). The harness labels a
report PREREGISTERED only when that commit is an ancestor of the run head and every digest and named value matches;
the rc16 report carries that label. Pre-registered: primary arm `open:mixed:W=fwd:+matched` (the whole forward window,
so no window value comes from an oracle), metric `r3_units_with_hitting_frame_selected` (R3 units for which some
selected frame lies inside a ground-truth range of that unit), comparators P+ and `shuffled_prior`, 5 required runs.
Rule: "supported" only if on rc16 the primary arm beats both comparators with exact McNemar p < 0.05 against P+, beats
both in each of the 5 runs, and exceeds analytic chance; otherwise "not supported". Recall and cost are decided only
by the owner-approved real-call batch.

**Decision of record.** The decision of record is the run at `716e984`. Review of PR #830 then hardened the harness at
`c1e97a5`: an unverifiable pre-registration (no git) now fails closed as PREREGISTRATION UNVERIFIED, the real-call
batch's `preregistered` flag is taken from the gate, and the perf record lists run names only. That commit leaves
`targeting.py` unchanged (`fd884a26…`) but changes `targeted.py`, so a re-run at `c1e97a5` or later is labelled
PREREGISTRATION MISMATCH (exploratory) by design, and its output digests differ from those below. The record is frozen
and pinned by a test; reproducing the decision of record needs a checkout of `716e984`. Any future data run, including
the real-call batch, needs a new pre-registration committed before it runs. Review of PR #831 changed `targeted.py`
again (`a558396`, `b17bb53`, `468cb05`: exact production cache key, restored matching counts, legacy keys only from
the spec's asked pair) and left `targeting.py` unchanged. The exact-key recompute ran at `468cb05` and is exploratory
(PREREGISTRATION MISMATCH). Later review fixes (`7d90f35`: classification and A0 need the same answer proof, and
replayed answers carry the record's product and hook fields; `4607031`: without an exact basis every non-broad answer
is unproven) and the later fixes `2b24f93` to `e6d000f` (run record §16) are all exploratory; the PC re-runs at
`4607031`, `4558305` and `669f298` change no rc16 arm and no decision value. Its exploratory PC re-run at
`e6d000f` also changes none. The code head `234a159` (run folder pinned and read by handle; run read once from checked bytes inside it;
donor side metadata) was re-run on the PC at that exact head (exploratory, A = B): no class, arm or decision value changes.
The current code head `a41ccef` (only a proven broad answer claims its JPEG bytes for a shared copy) was re-run on the
PC at that exact head (exploratory): no class, arm or decision value changes.

**Experiments.** E1 baseline replay; E2 GT-blind selection; E3 evidence replay from saved answers (a selected frame
gets an answer only if some run answered the byte-identical JPEG); E4 budgets matched (+40 frames on rc16, the
targeted frames the run paid in item parts), 2x and 4x, plus B0, the only same-total-budget arm (the run's own
targeted frames removed and re-spent); E5 per-unit attribution change. Regimes: open (production condition), closed
world (only shots some saved run answered; not a production condition) and the ceiling `ALL_DONOR_ITEMS`.

**Oracle separation.** Everything that uses ground truth is under "upper bounds", labelled ORACLE, in its own table,
and never presented as a result. Statistics are paired per unit (fixed, broken, exact McNemar). Runs are not
independent, so a claim needs sign consistency across all runs and a per-run paired test.

**Limits of the simulation.** Saved answers exist only for frames some run looked at, and the five runs looked at
almost the same frames. Across the open extra-budget arms at most 7.5 % of selected frames had a saved answer at the
matched budget and at most 5.6 % at 4x. Benefits and harms are both under-sampled, open-world recall rows are mostly
the unchanged baseline, and no E3 to E5 number is a lower bound. Donor replay also favours the production rule by
construction: B0-P+ re-spends the run's own frames with their own answers (40/40 simulated), while B0-mixed has answers
for only 9 to 10 of its 40 frames, so its recall mostly measures evidence removed in the simulation, not worse
localisation.

**Determinism.** Two full runs gave byte-identical outputs: report JSON `eeea89bb…7a04f`, markdown `c513db2c…f382`,
real-call batch `8d0bc9e3…ace`. These reproduce only at `716e984` (see Decision of record).

## 16. Expected benefits

On W3 REAL-DEV the pre-registered localisation test is **not supported**:

| Run | Primary arm | P+ | shuffled_prior | Chance | p vs P+ | Beats both | Above chance |
|---|---:|---:|---:|---:|---:|---|---|
| rc12 | 2 | 1 | 0 | 2.8006 | 1.0 | yes | no |
| rc13 | 3 | 1 | 1 | 2.8077 | 0.625 | yes | yes |
| rc14 | 2 | 3 | 2 | 3.1269 | 1.0 | no | no |
| rc15 | 2 | 2 | 1 | 3.1563 | 1.0 | no | no |
| rc16 | 2 | 1 | 0 | 2.9506 | 1.0 | yes | no |

The primary arm beat both comparators only in rc12, rc13 and rc16 and was above chance only in rc13. At 4x on rc16
`prior`, `anchor` and `mixed` put a hitting frame on 6 to 12 R3 units (chance 6.0 to 10.9); uniform A+ reached 13
(chance 10.05) and `gap`, the targeted strategy that spreads frames over the unseen forward window, reached 15 (chance
10.33). These secondary, single-run comparisons were not pre-registered and have no test; on them, script- and
time-based centring (`prior`, `anchor`, `mixed`) did no better than uniform frames.

Per-unit transitions on rc16 (E5, open, partial simulation): no extra-budget arm fixed an R3 unit at the matched budget;
at 2x only `anchor` W=5 s and `shuffled_prior` W=30 s fixed 1; at 4x A+ fixed 2 and P+, every `anchor` and `mixed`
window, `gap` and `shuffled_prior` W=30 s fixed 1 each, while `prior` fixed none. One R3 unit moved to R4, R5 or R6 in
every `prior` and `gap` arm and in P+, `mixed` W=5 s and `shuffled_prior` (forward window) at 4x; these arms, plus
`anchor` W=10 s at 4x, lost 1 hit each (`prior` W=5 s at 4x lost 2).

Recall on rc16 (partial simulation): the open matched arms give r@5 30 to 31/60 against A 31; 2x 30 to 32; 4x 29 to
33, the best being uniform A+ at 33/60 (fixed 2, broken 0, p 0.5, not significant). The prior arms are slightly
negative (29 to 30/60). B0 with `mixed` (same total budget, the run's 40 targeted frames re-spent) gives 28/60 (fixed
4, broken 7, p 0.5488); this comparison favours the production rule by construction (§15), so it shows no gain for
`mixed` rather than a measured harm. The closed world gives 27 to 32/60 and the ceiling 30/60.

What would be worth having, as ORACLE bounds that use ground truth and are not achievable results:

| Upper bound (ORACLE) | rc16 r@5 | Δ vs A |
|---|---:|---:|
| Acquisition and interpretation on all right shots | 56/60 | +25 |
| Acquisition at frames chosen at the ground-truth location, matched budget (W 10, 15, 30 s or forward window) | 42/60 | +11 (fixed 14, broken 3, p 0.0127) |
| The same at W 5 s | 41/60 | +10 |
| Interpretation only, shots already seen | 34/60 | +3 |
| Perfect interpretation of the frames chosen by A+ / P+ (matched) | 34/60 / 32/60 | +3 / +1 |
| Perfect interpretation of the frames chosen by `prior` / `mixed` / `anchor` (matched) | 32 to 34 / 30 / 29 to 30 | +1 to +3 / −1 / −2 to −1 |

So perfect knowledge of where to look would be worth about +11 r@5 on REAL-DEV at the matched budget, if what is seen
there were also understood perfectly. None of the non-oracle signals tested gets close: even perfectly understood, the
frames the `mixed` arm picks would not raise recall, and uniform frames would do as well as or better than any
targeted rule. The location-hint oracle (ranking only, inside a window around the ground truth, no new evidence) gives
hit@3 29/60 at ±30 s, 32/60 at ±15 s, 38/60 at ±10 s and 59/60 at ±5 s; a narrow window leaves few candidates, so this
measures how much a known location narrows the choice, not evidence quality.

## 17. Cost and performance

Frames are the primary cost unit. Calls are estimated as `Σ over parts of ceil(frames_part / 4)`, with FORMAT retries
unknown. No price per call was measured or given, so money is `calls × price_per_call` with the provider's price as a
parameter.

| rc16 arm family (open) | Extra frames | Estimated extra calls |
|---|---:|---:|
| Matched, arms that selected all 40 frames | 40 | 15 to 20, most arms 18 to 20 (A+ 20, P+ 19, `mixed` 19) |
| 2x, arms that selected all 80 frames | 80 | 20 to 30, A+ 20 and the others 24 to 30 (P+ 24, `mixed` 24) |
| 4x, arms that selected all 160 frames | 160 | 40 to 53 (A+ 40, P+ 51, `mixed` 51) |
| Arms capped by a smaller eligible set (`anchor` and `prior` W=5 s at 4x: 108 and 112 frames, 33 calls each; `shuffled_prior` W=5 s and W=10 s at every budget: 13 and 31 frames, 7 and 13 calls; W=15 s at 2x and 4x: 58 frames, 20 calls; W=30 s at 4x: 129 frames, 41 calls) | 13 to 129 | 7 to 41 |
| B0 (same total budget) | 40 re-spent | 18 to 19 for `mixed` |

For comparison, the rc16 production run had a budget of 142 and used 139 frames, 138 of them asked (95 broad, 44
targeted, 0 multi-frame); its number of calls was not recorded (the engine counts frames, not calls), so it is not measured; it took 676 s to the first draft and peaked at 3438 MB (earlier study, CPU only).

The offline harness ran all five runs with every arm in 591 s (per run 79 to 111 s), peak RAM 350 MB, CPU only, no AI
call. The repeat run took 612 s with the same peak. E1 tools alone take 0.4 to 5.6 s and 29 to 40 MB per run.

The real-call batch for the decisive E4 comparison lists the not-simulable frames of A+, P+, `mixed` (forward window)
and `shuffled_prior` (forward window) at the matched budget on rc16. Those four arms have 38, 39, 40 and 39
not-simulable frames, so the batch holds at most 156 frames; its exact frame and call counts are in the batch file. It
was written and **not executed**; it needs owner approval and a price per call.

## 18. Alternatives considered

**A. Keep current sampling, only increase the budget.** Uniform extra frames (A+) gave r@5 31, 31 and 33/60 at
matched, 2x and 4x (A 31); at 4x fixed 2, broken 0, p 0.5. Only 2/40, 2/80 and 6/160 selected frames had a saved
answer, so this is mostly untested rather than refuted. With perfect interpretation, A+'s matched frames reach 34/60
(ORACLE). Cost grows linearly with frames: about 20, 20 and 40 estimated calls.

**B. Targeted acquisition from script and time ranges.** The pre-registered decision is "not supported": 2 R3 units
with a hitting frame on rc16 against 1 (P+), 0 (shuffled) and 2.95 (chance), not sign-consistent over 5 runs. Recall
30 to 31/60 at matched; with perfect interpretation the `mixed` frames reach only 30/60 (ORACLE). Same frame cost as A
at the same budget.

**C. Add a semantic model or embedding.** Not tested: a new model is a §26 stop condition. On REAL-DEV the
interpretation-only oracle adds 3 (34/60), so a better reader of the frames already seen has little room; a model that
scans every frame to find the right shot would be a new localisation engine with its own GPU, cost and privacy
questions and needs its own ADR.

**D. More human-guided timestamp selection.** Not tested as a product feature. Within this directive's scope the user
is the only location source that was not among the signals tested here (Option C is untested and outside this
directive), and the ORACLE bound for frames at the true location is 42/60 (+11) at the
matched budget, assuming perfect interpretation. How close real users' timestamps get to that, how much effort it
takes, and how well vision then reads those frames are not measured. The Tổng hợp spec has no per-sentence citation
field, so this is a product, UX and contract change that needs its own issue, ADR and evidence.

**E. Broad plus bounded targeted.** This is what production already does (95 broad plus 2 targeted frames per part on
W3). Replacing the production targeted rule with `mixed` at the same total budget (B0) gave 28/60 against 31 (fixed 4,
broken 7, p 0.5488; 9 to 10 of 40 frames simulated), and re-spending with the production rule reproduces 31/60. This
comparison favours the production rule by construction (re-spending P+ reuses the run's own answers, 40/40 simulated),
so the 28/60 mostly measures lost evidence, not worse localisation; it shows no gain for `mixed`, not a measured harm.
With Option B's signals, E inherits B's unsupported localisation.

| Option | Evidence | Recall effect on REAL-DEV (partial simulation) | Cost | Risk | Verdict |
|---|---|---|---|---|---|
| A, more uniform budget | E4 A+ arms; ORACLE at A+ frames | 31 / 31 / 33 at matched / 2x / 4x vs 31, p 0.5 at best | Linear in frames, about 20 to 40 calls extra | Spends more without proven gain | No change now; may be a real-call arm |
| B, script/time targeting | Pre-registered E2; E3 to E5 | 30 to 31 matched, prior arms 29 to 30; ORACLE at `mixed` frames 30 | Same as A per frame, plus selection code | Overfitting to W3; early-window and trim biases | Do not implement |
| C, semantic model or embedding | Not tested (stop condition); ORACLE interpretation 34 | Not measured | New model, possible GPU and per-frame cost | Scope, privacy, cost | Out of scope; own ADR if ever |
| D, human-guided timestamps | ORACLE location bounds only | ORACLE 42 at the true location; real effect not measured | User time; frames as A | UX burden; timestamp accuracy unknown | Separate product issue with own evidence |
| E, broad plus bounded targeted | B0 respend arms; current production | Production rule 31; `mixed` re-spend 28 (fixed 4, broken 7; favours P+ by construction) | Same total budget | Inherits B's signals | Keep the current production rule (no improvement shown) |

## 19. Risks

- **Overfitting to W3.** The arms, the windows and the harness were designed on the same 60 units; any value chosen on
  W3 would not generalize. Mitigation: pre-registration and a NEW REAL-HOLDOUT.
- **Under-sampled simulation.** At most 7.5 % (matched) and 5.6 % (4x) of the open arms' selected frames had saved
  answers, so harms could be hidden as well as gains. Mitigation: the bounded real-call batch.
- **Run dependence and provider variance.** The five runs share footage, ground truth and donors, and recall@5 moves
  by about two to three units between cold runs.
- **Trim bias.** The item trim was written by the ground-truth author, which can favour trim-based priors; the
  shuffled control exists for this reason.
- **Early bias of the targeted pass** (20 of 39 production picks in the first quarter of their window on rc16) is a
  property of today's code and would remain.
- **READY safety.** New evidence can push a wrong shot over the READY threshold; missing evidence can be stated as
  `SOURCE_INSUFFICIENT` when notes are present.
- **Timebase.** Stored keyframe times are missing, out-of-trim keyframes are kept, and grab versus thumbnail drift is
  untested.
- **Cost growth.** Any design that scales frames with duration would break the budget policy; the cap in §11 prevents
  it.

## 20. Rollout proposal

No rollout is recommended now. Nothing in the planner, engine, app, contract or defaults changes in Stage 2.

A later rollout would be considered only after every condition in §22 holds, and then: as a separate implementation
issue and PR with its own review; behind a recipe version and an explicit Long-Form Assisted setting that is off by
default; first as a select-only diagnostic that records what it would look at without any call; then with real calls
inside the existing job budget; with Gate B evaluated separately and never inferred from this work.

Status of the gates at the time of this ADR: Targeted Evidence ADR PROPOSED; Targeted Evidence Feasibility PASS as a
study (finding: localisation not supported, do not implement), see the report; Long-Form Production Gate B FAIL; Human
Validation Gate D BLOCKED; Documentary Assisted Pilot Entry NOT READY; Documentary Auto Gate S FAIL (NO-GO). Phase 4
stays NO-GO and there is no stable release.

## 21. Rollback proposal

Today there is nothing to roll back in production. The harness is test tooling only: removing `targeting.py`,
`targeted.py`, `targeted_prereg.json` and `test_targeted.py` from `tests/mastering/longform/` removes it, and no
engine, app or contract code depends on it.

For any later implementation: turning the setting off must give today's plan byte for byte (a regression test compares
plans and the vision report with the setting off, as ADR-0055 does for Mastering Off); the targeting recipe is part of
the plan identity, so switching it off rebuilds the plan; cached answers stay valid because they are keyed by frame
bytes; review files are not touched.

## 22. Conditions for implementation approval

Implementation of any targeted acquisition may be proposed for approval only when all of these hold:

1. **Owner approval** of a bounded real-call experiment, with its frame cap, call estimate and a stated price per
   call. The prepared rc16 batch (at most 156 frames) is the first candidate.
2. **A NEW REAL-HOLDOUT** as in §15 (at least 40 sentences, at least 2 independent long videos, ground truth locked by
   sha256 before the final evaluation), with arms, metrics and the decision rule pre-registered by commit and digest
   before the ground truth is used.
3. **Localisation:** on the holdout, the candidate strategy places a hitting frame on more R3 units than both the
   production rule and the shuffled control, with exact McNemar p < 0.05 against the production rule, above analytic
   chance, and in the same direction on every holdout video.
4. **Recall with real calls:** at the same total frame budget as the production rule, hit@5 improves with exact
   McNemar p < 0.05 on paired units, chosen hits do not fall, and every fixed unit is attributed to new evidence, a
   duplicate copy or an indirect cursor or ranking effect (`fixed_via_new_evidence`, `fixed_with_shared_only`,
   `fixed_indirect`), with only the first counted as a gain from targeting.
5. **Safety:** zero new serious silent misses, ΔfalseREADY 0, READY precision not lower than the baseline, no increase
   in warnings per unit, and no unit stated as `SOURCE_INSUFFICIENT` whose right shot was never seen.
6. **Cost:** frames per job within the existing job budget cap (§11), calls and money reported with the formula of
   §17, and the gain per extra call stated.
7. **Provenance and timebase:** per-candidate acquisition status, pass, trigger unit and frame time recorded (§8, §9);
   the timebase test on non-zero stream start and variable frame rate passes.
8. **Determinism and blindness:** the selector stays GT-blind (subprocess check PASS) and repeated runs give
   byte-identical selections.
9. **No AI where not needed:** Mastering Off and workflows without AI make zero vision calls; no default workflow
   changes.
10. **Separate gates:** Gate B, Gate D and Gate S are judged on their own evidence; meeting these conditions does not
    change any of them.
