# Targeted Evidence Acquisition (#815), Stage 2: offline E1–E5 run record

- Written 2026-11-14. Design issue #815, task issue #829, harness PR #831 (supersedes #830). ADR:
  [ADR-0057](../../../adr/ADR-0057-targeted-evidence-acquisition.md) (Proposed, not approved). Report:
  [TARGETED_EVIDENCE_FEASIBILITY_REPORT.md](../../TARGETED_EVIDENCE_FEASIBILITY_REPORT.md).
- Environment: the owner's Windows PC, CPU only, Python 3.12.10, offline. The app was not started. The harness made no
  AI or vision calls, used no network and no key, and wrote nothing to GitHub.
- Inputs: the five saved W3 runs of 1.1.96-rc12 to rc16 (fresh vision cache each) and the locked W3 ground truth. The
  harness reads only the run folders and the ground truth. This record is redacted: it holds counts, unit ids, run
  folder names and digests only. It contains no narration or ground-truth sentences, no frames, no vision answer text,
  no provider or model names, no keys and no local paths.
- Labels: **Offline** = `tests.mastering.longform.targeted` (E2–E5) and `tests.mastering.longform.w3` (E1 baseline) run on
  saved evidence. This is not desktop E2E and says nothing about production readiness. **ORACLE** = uses the ground
  truth; an upper bound, never a production result.
- Dataset: W3 REAL-DEV (development, not a holdout), 60 hash-locked sentence units. The five runs are not independent
  (shared footage, shared ground truth, 4 of 5 runs act as donors for the fifth).

## Summary

| Item | Result |
|---|---|
| Pre-registered decision (primary arm `open:mixed:W=fwd:+matched`, metric R3 units with a hitting frame selected) | **not supported** |
| rc16 primary arm vs comparators | 2 R3 units vs P+ 1 vs shuffled_prior 0; chance 2.9506; exact McNemar p vs P+ 1.0 |
| Across the 5 runs | beats both comparators in rc12, rc13, rc16 only; above chance in rc13 only |
| rc16 r@5, open world (E3/E4, partial simulation) | matched 30–31/60 (A 31); 2x 30–32/60; 4x 29–33/60 (best `open:A+` uniform 33/60, fixed 2 / broken 0, p 0.5) |
| Silent misses, ΔfalseREADY, Δwarn | rc16: 0 in every arm (partial simulation: harms under-sampled; in the open extra-budget arms at most 7.5 % of selected frames were simulated at matched budget and at most 5.6 % at 4x) |
| Cache coverage of selected frames (open arms) | at most 7.5 % at matched budget, at most 5.6 % at 4x |
| Determinism | run A = run B, byte-identical on all three outputs |
| Harness cost | 591.1 s for all 5 runs, peak RAM 350 MB, CPU only |
| Real-call batch | written, **not executed** (owner approval required) |

## 1. Code, tests and inputs

| Item | Value |
|---|---|
| Harness head | `716e984f7a0fbb3f9a8499167e42c55f8e02c644` on `agent/issue-815-targeted-evidence`, clean worktree |
| Pre-registration commit | `c60ab029e55ced81ebf8122ec1cfcd76f98ff0c7` (named in `targeted_prereg.json`), **ancestor** of the head |
| Tests (at `716e984`) | `Ran 71 tests in 11.610s` / `OK (skipped=1)`. The skipped test is the #815 PR scope check, which runs only when `DUBFLOW_SCOPE_CHECK=815` is set |
| Ground truth | sha256 `afa979d239b6a68be03349308e233809efddc84cd2ed13e0d869913c51f66497` = lock |
| E1 baseline tool | `tests.mastering.longform.w3` on main `cf0c2a9` (a separate worktree); the E1 block inside the `targeted` report was computed at `716e984` |
| PR head after review | `c1e97a5`: review fixes on PR #830 (an unverifiable pre-registration fails closed, the batch's `preregistered` flag comes from the gate, the perf record lists run names only). `targeting.py` is unchanged; `targeted.py` changed, so its digest no longer equals the record. A re-run at `c1e97a5` or later is labelled PREREGISTRATION MISMATCH (exploratory) by design and does not reproduce the §5 digests. **The decision of record is the run at `716e984`.** The record itself is unchanged and pinned by a test |

**E3–E5 provenance (Lead review, 2026-11-14).** At `716e984` donor answers were matched by JPEG bytes and by the run-level provider and model. That does not prove the full production cache key: prompt version, provider, model, label set and JPEG bytes, with the logged answer equal to the cached record. The harness now requires that proof (`a558396`, PR #831).

Every E3–E5 number in this record (§10 onwards: simulated counts, open-world recall, E5 deltas) is therefore **UNVERIFIED** until it is recomputed from the saved runs with the exact-key rule. That recompute is exploratory by design. The E2 decision of record does not use donor answers and is unchanged.

Timeline (UTC, 2026-11-14, from the output files' write times on the PC):

| Step | Time |
|---|---|
| E1 baseline outputs (`w3` score, attribute, oracle for rc12 to rc16) | 00:58:07 to 00:58:39 |
| Harness head `716e984` committed | 00:59:27 |
| Preflight output | 01:00:48 |
| Run A outputs (report JSON, markdown, real-call batch, perf) | 01:11:16 |
| Run B outputs (same four files, same sizes) | 01:21:40 |

Experiment status:

| Experiment | Status | Scope of the result |
|---|---|---|
| E1 baseline replay | **RAN** (exit 0, rc12 to rc16) | full: recall@1/3/5, attribution, READY precision, oracle bounds |
| E2 targeted sampling simulation | **RAN** (exit 0, 5 runs, blindness PASS on all 5) | full for selection; pre-registered decision "not supported" |
| E3 evidence replay | **RAN** at `716e984` as a partial simulation under the old matching rule, now **UNVERIFIED** (no exact production cache-key proof; see below); real vision calls **NOT_RUN** | only frames with a saved answer could be replayed (open arms: at most 7.5 % matched, 5.6 % at 4x) |
| E4 budget comparison | **RAN**; frames and call estimates stand, the recall replay is **UNVERIFIED** (as E3); real-call cost **NOT_RUN** | frames and estimated calls per arm; recall replay partial; no price given, cost null |
| E5 failure attribution | **RAN** at `716e984`, now **UNVERIFIED** (as E3) | per-arm R3 fixed, moves to R4/R5/R6, lost hits, silent misses, false READY, warnings |
| Owner-approved real-call batch | **NOT_RUN** | batch file written with `execution: none`; needs the owner's approval and a new pre-registration at the head that runs it |

Runs:

| rc | run | Role |
|---|---|---|
| rc12 | `run-6206f16daf09` | supporting run, donor |
| rc13 | `run-76080efb6da4` | supporting run, donor |
| rc14 | `run-b3e7c7967a6e` | supporting run, donor |
| rc15 | `run-8f64b0a50e04` | supporting run, donor |
| rc16 | `run-f6919b04ccb3` | primary run |

## 2. Pre-registration

The primary decision was fixed in `targeted_prereg.json` at commit `c60ab02`, before any real-data run. The harness
checks that this commit is an ancestor of the checked-out head and that the digests and named values match the code
that runs.

| Field | Value |
|---|---|
| `label` | **PREREGISTERED** |
| `commit` / `commit_check` | `c60ab029e55ced81ebf8122ec1cfcd76f98ff0c7` / `ancestor` |
| `targeting_sha256` (`targeting.py`, LF) | `fd884a26e7f70e8405364c9309f0129d41abe1c21e51cc053a991b701427ee92` = current at `716e984` (and still at `c1e97a5`) |
| `analysis_sha256` (`targeted.py`, LF) | `4dff01c5fd1a80b9298c77439e2eb763056894e4dfecae6134cbf437d6711a7f` = current at `716e984` (not at `c1e97a5`, see §1) |
| `match` / `mismatches` | `true` / `[]` |
| Primary run | rc16 |
| Primary arm | `open:mixed:W=fwd:+matched` (strategy mixed, forward window, extra budget equal to the run's own targeted spend, flagged units, open condition) |
| Primary metric | `r3_units_with_hitting_frame_selected` |
| Comparators | `open:P+:-:+matched`, `open:shuffled_prior:W=fwd:+matched` |
| Required runs | 5 |
| Rule | Localisation is supported on REAL-DEV only if, on rc16, the primary arm has more R3 units with a hitting frame than both P+ and shuffled_prior, with exact McNemar p < 0.05 against P+; and it beats both comparators in each of the 5 runs; and it exceeds chance_r3_units. Otherwise not supported. Recall and cost (E4) are decided only by the owner-approved real-call batch |

## 3. Preflight (no ground truth)

Exit 0; all 5 runs **PASS**. For each run: 22 parts, spec present, program vision report present, vision cache folder
present, 0 part logs without a vision report, 0 keyframes missing (712 keyframes in rc12, 710 in each of the others),
`source_key_basis` content, `classification_basis` exact, `analysis_basis` provenance, `prompt_version_basis` unknown,
failures none.

## 4. Commands

Run from the repository root with `716e984` checked out (the decision-of-record version), with `PYTHONUTF8=1`. On the PC the launcher was `py -3.12`; the
module form is shown here. Angle brackets are placeholders for the local ground-truth files and run folders, which are
not recorded.

```text
python -B -m unittest discover -s tests/mastering/longform -t . -p "test_*.py"
python -B -m tests.mastering.longform.targeted preflight <rc12> <rc13> <rc14> <rc15> <rc16>
python -B -m tests.mastering.longform.targeted all <ground-truth.json> <ground-truth.lock> \
  --primary <rc16> --runs <rc12> <rc13> <rc14> <rc15> \
  --out-dir <out> --windows-s "5,10,15,30,fwd" --budgets "matched,2x,4x"
```

No `--cost-per-call` was given, so every cost field is null ("–"). Review edits are excluded (the default), and the
subprocess blindness check is on (the default). Run B repeated the `all` command with a different output folder.

Outputs (file names only): `targeted-run-f6919b04ccb3.json` (25.3 MB), `targeted-run-f6919b04ccb3.md` (97.6 KB),
`real-call-batch-run-f6919b04ccb3.json`, and a `.perf.json`. The JSON and the markdown contain 0 absolute paths. The raw
`.perf.json` holds local paths and stayed on the PC; only its summary is reported here.

## 5. Determinism

Both runs exited 0 (run A in 592 s, run B in 612 s). All three outputs are byte-identical.

| File | sha256 (run A = run B) |
|---|---|
| `targeted-run-f6919b04ccb3.json` | `eeea89bb52b6d3a0634b0cd92cd53111f890d87339ef23c993e4adee78e7a04f` |
| `targeted-run-f6919b04ccb3.md` | `c513db2c3b9d3ec0b9d16490d7f0835cd34c63483d402943a1178bf47dbff382` |
| `real-call-batch-run-f6919b04ccb3.json` | `8d0bc9e38c9940e36f3736a91294ea76b93c4831198ef657d227b22ebc894ace` |

Blindness check (subprocess): `run-6206f16daf09` PASS, `run-76080efb6da4` PASS, `run-8f64b0a50e04` PASS,
`run-b3e7c7967a6e` PASS, `run-f6919b04ccb3` PASS.

## 6. Performance (run A, CPU only)

| Item | Value |
|---|---|
| Total | 591.1 s (run B: 611.7 s) |
| Peak RAM | 350 MB (run B: 350 MB) |
| Prepare | 35.3 s |
| Per run | rc12 98.8 s, rc13 96.8 s, rc14 101.5 s, rc15 79.3 s, rc16 110.6 s |
| Typical phases per run | arms about 38–45 s, upper bounds about 42–45 s, E1 about 4.5 s, selection about 4.4 s, replay A and A0 about 3.5 s each, context about 1–1.5 s |
| E1 tool (`w3`), per run | score 0.4–0.7 s, about 29 MB; attribute 0.8–0.9 s, about 33 MB; oracle 4.7–5.6 s, about 40 MB |

## 7. E1 baseline across runs

`w3` tool on main `cf0c2a9`, saved plans (review edits included). This is the existing baseline, not a new result.

| Run | r@1 | r@3 | r@5 | chosen hit | silent misses (saved) | READY precision | vision answers (budget 142) |
|---|---|---|---|---|---|---|---|
| rc12 | 17 | 28 | 31 | 25 | 0 | 5/5 | 141 |
| rc13 | 20 | 31 | 33 | 26 | 2 | 5/7 | 139 |
| rc14 | 20 | 24 | 28 | 23 | 0 | 6/6 | 139 |
| rc15 | 21 | 27 | 29 | 25 | 3 | 5/8 | 139 |
| rc16 | 20 | 26 | 31 | 24 | 0 | 4/4 | 138 |

The silent misses of rc13 (2) and rc15 (3) are counted under the saved states; the #802 replay brings both to 0.

Failure attribution (OK / R3 / R5 / R4 weak / R4 no shared words / R6 behind cursor / R6 taken / R2):

| Run | OK | R3 | R5 | R4 weak | R4 no shared | R6 cursor | R6 taken | R2 |
|---|---|---|---|---|---|---|---|---|
| rc12 | 25 | 24 | 3 | 3 | 1 | 2 | 1 | 1 |
| rc13 | 26 | 23 | 3 | 2 | 1 | 3 | 1 | 1 |
| rc14 | 23 | 28 | 2 | 2 | 1 | 3 | 0 | 1 |
| rc15 | 25 | 25 | 5 | 0 | 1 | 3 | 0 | 1 |
| rc16 | 24 | 26 | 3 | 2 | 1 | 3 | 0 | 1 |

rc16 review buckets (`w3`): READY_HIT 4, FLAGGED_HIT 20, FLAGGED_MISS 36, SILENT_MISS 0.

ORACLE r@5 per run (uses the ground truth; saved / replayed / looked-only / all hits): rc12 31/32/37/56, rc13
33/33/33/56, rc14 28/28/33/56, rc15 29/30/34/55, rc16 31/31/34/56.

## 8. Decision

- Primary arm `open:mixed:W=fwd:+matched`, metric `r3_units_with_hitting_frame_selected`.
- Comparators `open:P+:-:+matched` and `open:shuffled_prior:W=fwd:+matched`.
- Gates: blindness check PASS; pre-registration PREREGISTERED; required runs 5.
- **Result: not supported** (`result_before_gates` not supported, `reason` null).

| Run | Primary arm | P+ | shuffled_prior | Chance | p vs P+ | Beats both | Above chance |
|---|---|---|---|---|---|---|---|
| rc12 | 2 | 1 | 0 | 2.8006 | 1.0 | yes | no |
| rc13 | 3 | 1 | 1 | 2.8077 | 0.625 | yes | yes |
| rc14 | 2 | 3 | 2 | 3.1269 | 1.0 | no | no |
| rc15 | 2 | 2 | 1 | 3.1563 | 1.0 | no | no |
| rc16 | 2 | 1 | 0 | 2.9506 | 1.0 | yes | no |

On the primary run the primary arm does not reach p < 0.05 against P+, and it is below chance. It fails the
"every run" condition in rc14 and rc15. The non-oracle script and time signals did not localise the right shots
significantly or consistently better than the existing priority, and not better than chance.

## 9. rc16 (primary run): E1 inside the harness

Arm A replays the saved run with review edits excluded, so it can differ from the `w3` figures in §7 (READY precision
5/5 here, 4/4 there). Both are reported.

| Item | Value |
|---|---|
| A | r@1 20/60, r@3 26/60, r@5 31/60, chosen 24/60, READY precision 5/5, ready rate 5/64, serious silent misses 0, unmatched footage 0/60 |
| Saved / replayed with review / A | r@5 31/60 / 31/60 / 31/60 |
| Matched budget | 40 (from the log vision reports); B0 budget 40 |
| A0 (broad pass only) | RECONSTRUCTED: budget 142, logged broad 95, picked 95, picked and observed 95, not in cache 0, frames with keyframe 710 |
| Classification (exact basis) | broad 95, broad_shared 5, targeted 53, targeted_shared 4, targeted frames in item parts 40, multi-frame extra 0, A0 sub-shots kept 0 |
| Attribution | OK 24, R3 26, R5 3, R6 behind cursor 3, R4 weak 2, R4 no words 1, R2 1 |
| Review buckets | READY_HIT 5, FLAGGED_HIT 19, FLAGGED_MISS 36 |
| Evidence acquisition coverage | frame 27/60, candidate 29/60 |
| Donor supply | reachable 20, shared and excluded 29, provider mismatch 0; donors rc12–rc15 |
| Early bias (A0 view, flagged units, n 39) | quartile histogram [20, 5, 5, 9]; q1 / median / q3 (thousandths) 40 / 201 / 642 |
| Parts that differ between the saved plan and A | i9 |

## 10. rc16 per-arm summary (E2 selection, E3 evidence replay, E4 budget, E5 attribution)

Open condition (production-like). Rows shown: the uniform (A+) and production (P+) arms, every targeted strategy at the
forward window, and the respend arms. The 5 s, 10 s, 15 s and 30 s windows are in the generated report and stay inside
the ranges given below the table. "Simulated" is the number of selected frames for which some saved run has an answer
for the byte-identical keyframe JPEG from the same provider and model (the production cache-key basis); the other
selected frames keep the baseline answer. "Fixed R3", "R3→R4/R5/R6" and "Lost" are the E5 columns of the generated
report, copied as printed. The generated report does not split the fixed units by cause (new evidence, a duplicate
copy, or indirectly through the cursor or ranking), so that split is not reported here.

B0 favours the production rule by construction: `B0-P+` re-spends the run's own 40 targeted frames with their own
answers (40/40 simulated), while `B0-mixed` has answers for only 9–10 of its 40 frames. Its 28/60 and broken 7 therefore
mostly measure evidence that was removed and not replaced in the simulation, not worse localisation.

| Arm | B | Selected | Simulated | R3 hit-frame (chance) | r@1 / r@3 / r@5 | Chosen | Fixed / broken (p) | Silent | READY | Fixed R3 | R3→R4/R5/R6 | Lost | ΔfalseREADY / Δwarn | Calls est |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `open:A+:-:+matched` | 40 | 40 | 2/40 | 4 (2.7952) | 20 / 26 / 31 | 24/60 | 0/0 (1.0) | 0 | 5/5 | 0 | 0 | 0 | 0/0 | 20 |
| `open:A+:-:+2x` | 80 | 80 | 2/80 | 5 (5.3936) | 20 / 26 / 31 | 24/60 | 0/0 (1.0) | 0 | 5/5 | 0 | 0 | 0 | 0/0 | 20 |
| `open:A+:-:+4x` | 160 | 160 | 6/160 | 13 (10.0477) | 22 / 28 / 33 | 26/60 | 2/0 (0.5) | 0 | 6/6 | 2 | 0 | 0 | 0/0 | 40 |
| `open:P+:-:+matched` | 40 | 40 | 1/40 | 1 (2.9506) | 20 / 26 / 31 | 24/60 | 0/0 (1.0) | 0 | 5/5 | 0 | 0 | 0 | 0/0 | 19 |
| `open:P+:-:+2x` | 80 | 80 | 3/80 | 4 (5.642) | 20 / 26 / 31 | 24/60 | 0/0 (1.0) | 0 | 5/5 | 0 | 0 | 0 | 0/0 | 24 |
| `open:P+:-:+4x` | 160 | 160 | 6/160 | 11 (10.3258) | 20 / 26 / 31 | 24/60 | 1/1 (1.0) | 0 | 5/5 | 1 | 1 | 1 | 0/0 | 51 |
| `open:anchor:W=fwd:+matched` | 40 | 40 | 0/40 | 2 (2.9506) | 20 / 26 / 31 | 24/60 | 0/0 (1.0) | 0 | 5/5 | 0 | 0 | 0 | 0/0 | 19 |
| `open:anchor:W=fwd:+2x` | 80 | 80 | 1/80 | 4 (5.642) | 20 / 26 / 31 | 24/60 | 0/0 (1.0) | 0 | 5/5 | 0 | 0 | 0 | 0/0 | 24 |
| `open:anchor:W=fwd:+4x` | 160 | 160 | 2/160 | 10 (10.3258) | 21 / 27 / 32 | 25/60 | 1/0 (1.0) | 0 | 5/5 | 1 | 0 | 0 | 0/0 | 51 |
| `open:gap:W=fwd:+matched` | 40 | 40 | 3/40 | 3 (2.9506) | 20 / 25 / 30 | 23/60 | 0/1 (1.0) | 0 | 5/5 | 0 | 1 | 1 | 0/0 | 19 |
| `open:gap:W=fwd:+2x` | 80 | 80 | 3/80 | 8 (5.642) | 20 / 25 / 30 | 23/60 | 0/1 (1.0) | 0 | 5/5 | 0 | 1 | 1 | 0/0 | 24 |
| `open:gap:W=fwd:+4x` | 160 | 160 | 6/160 | 15 (10.3258) | 21 / 26 / 31 | 24/60 | 1/1 (1.0) | 0 | 5/5 | 1 | 1 | 1 | 0/0 | 51 |
| **`open:mixed:W=fwd:+matched`** (primary) | 40 | 40 | 0/40 | 2 (2.9506) | 20 / 26 / 31 | 24/60 | 0/0 (1.0) | 0 | 5/5 | 0 | 0 | 0 | 0/0 | 19 |
| `open:mixed:W=fwd:+2x` | 80 | 80 | 1/80 | 4 (5.642) | 20 / 26 / 31 | 24/60 | 0/0 (1.0) | 0 | 5/5 | 0 | 0 | 0 | 0/0 | 24 |
| `open:mixed:W=fwd:+4x` | 160 | 160 | 2/160 | 10 (10.3258) | 21 / 27 / 32 | 25/60 | 1/0 (1.0) | 0 | 5/5 | 1 | 0 | 0 | 0/0 | 51 |
| `open:prior:W=fwd:+matched` | 40 | 40 | 2/40 | 3 (2.9506) | 20 / 25 / 30 | 23/60 | 0/1 (1.0) | 0 | 5/5 | 0 | 1 | 1 | 0/0 | 19 |
| `open:prior:W=fwd:+2x` | 80 | 80 | 2/80 | 4 (5.642) | 20 / 25 / 30 | 23/60 | 0/1 (1.0) | 0 | 5/5 | 0 | 1 | 1 | 0/0 | 24 |
| `open:prior:W=fwd:+4x` | 160 | 160 | 4/160 | 9 (10.3258) | 20 / 25 / 30 | 23/60 | 0/1 (1.0) | 0 | 5/5 | 0 | 1 | 1 | 0/0 | 51 |
| `open:shuffled_prior:W=fwd:+matched` | 40 | 40 | 1/40 | 0 (2.9506) | 20 / 26 / 31 | 24/60 | 0/0 (1.0) | 0 | 5/5 | 0 | 0 | 0 | 0/0 | 19 |
| `open:shuffled_prior:W=fwd:+2x` | 80 | 80 | 3/80 | 1 (5.642) | 20 / 26 / 31 | 24/60 | 0/0 (1.0) | 0 | 5/5 | 0 | 0 | 0 | 0/0 | 24 |
| `open:shuffled_prior:W=fwd:+4x` | 160 | 160 | 9/160 | 7 (10.3258) | 20 / 25 / 30 | 23/60 | 0/1 (1.0) | 0 | 5/5 | 0 | 1 | 1 | 0/0 | 51 |
| `open:B0-mixed:W=fwd:=removed` (respend) | 40 | 40 | 10/40 | 3 (3.3246) | 16 / 25 / 28 | 21/60 | 4/7 (0.5488) | 0 | 5/5 | 0 | 0 | 0 | 0/0 | 19 |
| `open:B0-P+:-:=removed` (respend) | 40 | 40 | 40/40 | 4 (3.3246) | 20 / 26 / 31 | 24/60 | 0/0 (1.0) | 0 | 5/5 | 4 | 2 | 4 | 0/0 | 0 |

Ranges over every open arm and window on rc16:

| Condition / budget | r@5 | R3 hit-frame vs chance (all arms except shuffled_prior) | R3 hit-frame vs chance (shuffled_prior, all W) | Silent | ΔfalseREADY / Δwarn |
|---|---|---|---|---|---|
| Open, matched (B 40) | 30–31/60 (A 31) | 1–4 vs chance 2.37–4.05 (primary 2 vs 2.95) | 0–1 vs chance 0–2.95 | 0 | 0 / 0 |
| Open, 2x (B 80) | 30–32/60 | 3–8 vs chance 4.49–6.75 | 0–4 vs chance 0–5.64 | 0 | 0 / 0 |
| Open, 4x (B 160) | 29–33/60 | 6–15 vs chance 6.0–10.93 | 0–7 vs chance 0–10.33 | 0 | 0 / 0 |
| Respend `B0-mixed` (all W; favours P+ by construction, see above) | 28/60, fixed 4 / broken 7 (p 0.5488) | 3 vs chance 3.3246 | – | 0 | 0 / 0 |
| Closed world (donor-covered shots only, **not a production condition**) | 27–32/60; highest 32/60, reached by `anchor W=5s` (1 frame selected) and `shuffled_prior W=30s` (7 frames) | – | – | 0 | 0 / 0 |
| Ceiling `ALL_DONOR_ITEMS` | 30/60, fixed 3 / broken 4 (p 1.0) | 3 vs chance 3.0 | – | 0 | 0 / 0 |

With the 5 s and 10 s windows, shuffled_prior has only 13 and 31 eligible shots, so it selects fewer frames than its
budget and its chance value falls to 0 at those windows.

- Open 4x: the best arm is `open:A+` uniform, 33/60, fixed 2 / broken 0, p 0.5 (not significant).
- The prior arms are slightly negative: 29–30/60, losing 1 or 2 units.
- Calls (estimates from the harness, not measured calls), arms that selected their full budget: matched 15–20 (most
  arms 18–20), 2x 20–30 (A+ 20, the others 24–30), 4x 40–53. Arms capped by a smaller eligible set: anchor and prior
  at W=5s at 4x (108 and 112 frames, 33 calls each); shuffled_prior at W=5s and W=10s at every budget (13 and 31
  frames, 7 and 13 calls), at W=15s at 2x and 4x (58 frames, 20 calls) and at W=30s at 4x (129 frames, 41 calls).

Cost. No price per vision call was measured or given, so the harness cost column is null. The cost of an arm is
`estimated calls × P`, where `P` is the provider's price per vision call at the time of a run. For example the primary
arm at matched budget is `19 × P` and `open:A+` at 4x is `40 × P`. No price is assumed here.

## 11. Cache coverage (rc16, open condition)

Almost nothing the arms select has a saved answer, because the five runs looked at nearly the same frames.

| Arm (matched / 2x / 4x selected) | Simulated (not simulable) |
|---|---|
| `open:mixed` (primary family) W=5s | 0/40 (40), 0/80 (80), 4/160 (156) |
| `open:mixed`, other W | 0/40, 1/80, 2/160 |
| `open:anchor` | 0/40, 1/80, 1–4 of 108–160 |
| `open:A+` | 2/40, 2/80, 6/160 |
| `open:P+` | 1/40, 3/80, 6/160 |
| `open:gap` | 3/40, 3/80, 6/160 |
| `open:prior` | 2/40, 2/80, 3–4 of 112–160 |
| `open:shuffled_prior` | 0–3 of 13–40, up to 7 of 80–129, 9/160 |
| respend `B0-mixed` | 9–10/40 |
| `B0-P+` | 40/40 |
| closed world, ceiling | 100 % simulated by construction, but tiny: 0–20 frames |

Across the open arms, at most 7.5 % of selected frames are simulable at matched budget and at most 5.6 % at 4x. The
open-world E3/E5 figures are therefore almost entirely the unchanged baseline: a partial simulation in which benefits
and harms are both under-sampled.

## 12. Upper bounds on rc16 (ORACLE, use the ground truth)

These rows use the ground truth to place or interpret frames. They bound what perfect localisation or perfect
interpretation could be worth on REAL-DEV. They are not production results; no non-oracle arm reaches the 42/60 and
56/60 bounds.

| Bound (ORACLE) | r@5 | Chosen | Δr@5 vs A (31) |
|---|---|---|---|
| `ORACLE_ACQ+INTERP_all_hits` | 56/60 | 55/60 | +25 (hit@5 fixed 26 / broken 1, p 0.0) |
| `ORACLE_ACQ_at_selected[ORACLE_select_gt_window]`, W = 10 s, 15 s, 30 s, fwd | 42/60 | 37/60 | +11 (hit@5 fixed 14 / broken 3, p 0.0127) |
| `ORACLE_ACQ_at_selected[ORACLE_select_gt_window]`, W = 5 s | 41/60 | 38/60 | +10 |
| `ORACLE_ACQ_at_selected[open:A+ matched]` | 34/60 | 29/60 | +3 |
| `ORACLE_ACQ_at_selected[open:P+ matched]` | 32/60 | 25/60 | +1 |
| `ORACLE_ACQ_at_selected[open:anchor / open:mixed matched]` | 29–30/60 | 25/60 | −2 to −1 |
| `ORACLE_ACQ_at_selected[open:prior matched]` | 32–34/60 | 25–27/60 | +1 to +3 |
| `ORACLE_INTERPRETATION_looked_only` | 34/60 | 30/60 | +3 |
| `ORACLE_LOCATION_hint` ±5 s / ±10 s / ±15 s / ±30 s | hit@3 59 / 38 / 32 / 29 of 60 | – | – |
| `ORACLE_select_gt_window` (open and closed world, all W), replayed with cached answers only | 31–32/60 | 25–26/60 | 0 to +1 |

Reading: with the ground-truth window chosen and a correct answer at every selected frame, r@5 would reach 42/60
(+11). The non-oracle arms, given the same oracle answers at their own selected frames, reach 29–34/60. The gap is
in where to look, and no non-oracle signal tested here closes it.

## 13. Real-call batch

`real-call-batch-run-f6919b04ccb3.json` (sha256 `8d0bc9e3…894ace`) lists the pre-registered frames for a later real-call
run: candidate ids, times and keyframe digests only. It was **not executed**; running it needs the owner's approval.

## 14. Caveats (verbatim from the generated report)

- donor coverage: answers exist only for frames some saved run looked at; ALL_DONOR_ITEMS is the ceiling
- closed world (donor-covered shots only) is not a production condition
- partial simulation: benefits and harms both under-sampled
- review edits (pins, exclusions, approvals made after viewing) are excluded from every arm by default
- intro/outro looks are excluded from the item parts' observed set, as in production
- A0 keeps the logged sub-shots (the replay does not re-split them) with their non-vision evidence only
- runs not independent (shared footage, ground truth, 4/5 donors)

The four supporting-run sections of the generated report are summarised by the decision table in §8; their full rows
are not reproduced here.

W3 REAL-DEV (development, not a holdout). GENERALIZATION NOT VERIFIED; Gate B not claimed.
