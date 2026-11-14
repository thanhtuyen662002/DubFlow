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
| Decision-of-record run | `716e984`: the PREREGISTERED run of §2–§14. **The decision of record is this run.** The pre-registration record is unchanged and pinned by a test |
| Review fixes after the run | `c1e97a5` (PR #830 review: an unverifiable pre-registration fails closed, the batch's `preregistered` flag comes from the gate, the perf record lists run names only); `a558396`, `b17bb53` and `468cb05` (PR #831 review: exact production cache key, restored matching counts, legacy keys only from the spec's asked pair). Each changes `targeted.py` and none changes `targeting.py`, so a re-run at any of them is labelled PREREGISTRATION MISMATCH (exploratory) by design and does not reproduce the §5 digests |
| Last recompute head | `468cb05`: the exploratory exact-key recompute of §15 ran at this head |
| Classification fix (historical) | `7d90f35`: classification and A0 need the same answer proof as donor matching, A0 clears the product and hook fields of removed answers, replayed answers carry the record's; exploratory PC re-run changes no arm (see §15) |
| Fail-closed fallback (historical) | `4607031`: without a single answer-proven basis every non-broad answer is unproven and kept in A0; exploratory PC re-run changes no rc16 arm and no decision value (see §15) |
| Later review fixes | `2b24f93`, `a061581`, `4558305`, `c2c8e26`, `669f298`, `e6d000f`, `a8d82ed`, `42b5195`, `52d9c06`, `ee277bd`, `1d4ebfb`, `567efee`, `ba9ba8b`, `234a159` (Lead and independent reviews of classification, A0, donor proof and file reads; see §16). Each changes `targeted.py` only |
| Earlier PC re-runs after `4607031` | `4558305` and `669f298` (exploratory, A = B; §16): no decision value changes |
| Earlier completed PC re-run | `e6d000f` (own-record proof also covers the side metadata; an unanswered broad pick proves no shared copy). Exploratory, A = B (§16): E1 classification/A0 and every arm equal `669f298`; no decision value changes |
| Current code head | `234a159` (the selected run folder is pinned and its selected entry is never followed; every part is opened relative to its folder's handle on Linux and Windows; the run is read once, from checked bytes inside it, and never reopened by path; the donor proof also covers the side metadata) |
| Latest completed PC re-run | `234a159`, the current code head. Exploratory, A = B (§16): E1 classification/A0 and every arm equal `e6d000f`; on rc16 3 donor records fail the side-metadata proof and are no longer replayed, which changes no E3–E5 number; no decision value changes |
| PR head | the harness code at the PR head is identical to `234a159`; later commits change documents only |

**E3–E5 provenance (Lead review, 2026-11-14).** At `716e984` donor answers were matched by JPEG bytes and by the run-level provider and model. That does not prove the full production cache key: prompt version, provider, model, label set and JPEG bytes, with the logged answer equal to the cached record. The harness now requires that proof (`a558396`, PR #831).

The E3–E5 numbers in this record (§10 onwards: simulated counts, open-world recall, E5 deltas) were therefore recomputed from the same saved runs with the exact-key rule; see §15. The recompute is exploratory by design. It changes no rc16 metric (rc16 is the run behind every E3–E5 number here): only two simulated-frame counts drop by one. The supporting runs rc12–rc15 record no asked model, so no cache key can be proven for them and they simulate nothing. The E2 decision of record does not use donor answers and is unchanged.

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
| E3 evidence replay | **RAN** at `716e984` as a partial simulation; **recomputed** with the exact production cache key at `468cb05`, `7d90f35` and `4607031` (exploratory, §15), rc16 metrics unchanged, rc12–rc15 not simulable (no provable key); real vision calls **NOT_RUN** | only frames with a saved answer could be replayed (open arms: at most 7.5 % matched, 5.6 % at 4x) |
| E4 budget comparison | **RAN**; frames and call estimates stand, the recall replay was recomputed as E3 at `468cb05`, `7d90f35` and `4607031` (rc16 unchanged); real-call cost **NOT_RUN** | frames and estimated calls per arm; recall replay partial; no price given, cost null |
| E5 failure attribution | **RAN** at `716e984`; recomputed as E3 at `468cb05`, `7d90f35` and `4607031`, rc16 unchanged | per-arm R3 fixed, moves to R4/R5/R6, lost hits, silent misses, false READY, warnings |
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
| `targeting_sha256` (`targeting.py`, LF) | `fd884a26e7f70e8405364c9309f0129d41abe1c21e51cc053a991b701427ee92` = current at `716e984`, and unchanged at `c1e97a5`, `468cb05`, `7d90f35` and `4607031` |
| `analysis_sha256` (`targeted.py`, LF) | `4dff01c5fd1a80b9298c77439e2eb763056894e4dfecae6134cbf437d6711a7f` = current at `716e984` only; the later exploratory code at `c1e97a5`, `468cb05` and `7d90f35` has other digests (`468cb05`: `f370e3376bccf5f0aa150f559c0fe0b736f969e3a428bcba399a56d8d4d16e8a`; `7d90f35`: `5948625f9ca82c997db32735c747b9955ead11e8e4653e34afa1bb7e88765d1d`; `4607031`: `f10650dba4b88df30a7ea8a1182b28d34cdad61b11768d2af62e86173cc0ccc4`), see §1 and §15 |
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
| Classification (exact basis) | at `7d90f35` and `4607031`: broad 95, broad_shared 5, targeted 43, targeted_shared 14, unproven 0, targeted frames in item parts 31 (B0 budget note: "budget basis differs from classification"); at `716e984`: broad 95, broad_shared 5, targeted 53, targeted_shared 4, targeted frames in item parts 40, multi-frame extra 0, A0 sub-shots kept 0 |
| Attribution | OK 24, R3 26, R5 3, R6 behind cursor 3, R4 weak 2, R4 no words 1, R2 1 |
| Review buckets | READY_HIT 5, FLAGGED_HIT 19, FLAGGED_MISS 36 |
| Evidence acquisition coverage | frame 27/60, candidate 29/60 |
| Donor supply | reachable 20, shared and excluded 29, provider mismatch 0; donors rc12–rc15 |
| Early bias (A0 view, flagged units) | at `7d90f35` and `4607031`: n 35, quartile histogram [16, 5, 5, 9]; q1 / median / q3 (thousandths) 67 / 308 / 666. At `716e984`: n 39, [20, 5, 5, 9]; 40 / 201 / 642 |
| Parts that differ between the saved plan and A | i9 |

## 10. rc16 per-arm summary (E2 selection, E3 evidence replay, E4 budget, E5 attribution)

Open condition (production-like). Rows shown: the uniform (A+) and production (P+) arms, every targeted strategy at the
forward window, and the respend arms. The 5 s, 10 s, 15 s and 30 s windows are in the generated report and stay inside
the ranges given below the table. "Simulated" is the number of selected frames that receive a saved answer; the other
selected frames keep the baseline answer. In the initial `716e984` run this used a pre-review approximation (an answer
for the byte-identical keyframe JPEG from the same run-level provider and model), which is **not** the production
cache key. The exploratory recompute (§15, final rule `468cb05`, PREREGISTRATION MISMATCH) admits an answer only under
the exact production key (prompt version, provider, model asked, label set, JPEG bytes) with the logged answer equal to
the cached record; for rc16 it reproduces every metric in this section; only the donor-supply and simulated-frame counts listed in §15 differ. "Fixed R3", "R3→R4/R5/R6" and "Lost" are the E5 columns of the generated
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

At `716e984` "simulated" meant a donor answer for the same JPEG bytes from the same run-level provider and model. The
exact-key recompute (§15) also requires the prompt version, the label set and the logged answer to equal the cached
record, and adds the caveat line: "an answer is replayed only under its exact production cache key (prompt version,
provider, model, label set, JPEG bytes) with the logged answer equal to the cached record; no match by time or by
run-level provider and model".

The four supporting-run sections of the generated report are summarised by the decision table in §8; their full rows
are not reproduced here.

## 15. Exact-key recompute of E3–E5 (exploratory)

Lead review of PR #830 found that §10–§11 matched donor answers without proving the full production cache key. PR #831
admits an answer only when its exact key (prompt version, provider, model, label set, JPEG bytes) is proven and the
logged answer equals the cached record (`a558396`). A second Lead review found that, for legacy records without
`made_by`, the candidate keys still crossed every provider with every model named by the run reports (the model that
answered) and the spec (the model asked). From `468cb05` a legacy basis comes only from the provider and model the run
spec records as a pair; a `made_by` record gives its own full tuple; the reports never contribute a key. The same five
saved runs were recomputed on the owner's PC, CPU only, offline, with the same command as §4, at each of these heads.

| Item | `a558396` | `b17bb53` | `468cb05` (final rule) |
|---|---|---|---|
| Tests | `OK (skipped=1)`, 82 tests | `OK (skipped=1)`, 83 tests | `OK (skipped=1)`, 87 tests |
| Label | PREREGISTRATION MISMATCH: results exploratory (`targeting_sha256` equals the record; `analysis_sha256` differs, as designed) | same | same |
| `result_before_gates` | not supported | not supported | not supported |
| Run A = run B | yes, byte-identical on all three outputs | yes | yes (JSON `28ca66ba…`, markdown `ad6140b5…`, batch `ccfc6deb…` unchanged) |
| Perf (run A) | 568.4 s, peak RAM 350 MB | 615.6 s (run B 568.6 s), 350 MB | 364.4 s (run B 363.0 s), 340 MB |
| Leak scans (key, e-mail, local paths) | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |

`b17bb53` only restores the donor-run and match counts that a comment had dropped from the report's `matching` object
at `a558396`. At `a558396` and `b17bb53` all five runs showed an `exact` key basis, but for rc12–rc15 that basis rested
on the crossed reports, which `468cb05` no longer accepts.

What the saved runs record (counts only):

| Run | Spec `editorial.ai`: provider / model | Cache records | With `made_by` |
|---|---|---|---|
| rc12 | yes / **no** | 141 | 0 |
| rc13 | yes / **no** | 139 | 0 |
| rc14 | yes / **no** | 139 | 0 |
| rc15 | yes / **no** | 139 | 0 |
| rc16 | yes / **no** | 138 | 138 |

Matching under the final rule (`468cb05`):

| Run | Key basis | Basis source | Admitted (`exact`) | Refused | Donor runs |
|---|---|---|---|---|---|
| rc12 | none (`unproven`) | – | 0 | 528 `no_key_basis` | none |
| rc13 | none (`unproven`) | – | 0 | 529 `no_key_basis` | none |
| rc14 | none (`unproven`) | – | 0 | 529 `no_key_basis` | none |
| rc15 | none (`unproven`) | – | 0 | 529 `no_key_basis` | none |
| rc16 (primary) | `exact` (prompt `vision-v3`, one provider and model, moment labels) | `made_by` | 521 | 1 `no_exact_key` | rc12–rc15 |

For rc16 the donors' legacy records are admitted only under rc16's proven full key, with the logged answer equal to
the record. No item detail or provider mismatch was found in any run.

Result against `716e984`:

- E2: the per-run decision table (§8) is identical at all three heads; it is a selection metric and uses no donor
  answer.
- rc16, the run behind every E3–E5 number in §10–§12: its whole markdown section is byte-identical between
  `b17bb53` and `468cb05`. Against `716e984` it differs only in: donor supply 19 frames (was 20);
  `open:A+:-:+4x` simulated 5/160 (was 6/160) with recall, fixed/broken and READY unchanged (r@5 33/60, fixed 2 /
  broken 0, p 0.5); closed-world `A+` and the ceiling `ALL_DONOR_ITEMS` 19 frames (was 20) with metrics unchanged
  (closed-world `A+` r@1/3/5 21/26/30, fixed 3 / broken 4, READY 5/5; ceiling r@5 30/60, fixed 3 / broken 4); and the
  new caveat line in §14. Every rc16 frame that could not be simulated lacks a donor answer (`no_donor_answer`).
- rc12–rc15 (supporting runs): their specs name no model and their records have no `made_by`, so no cache key can be
  proven. Under the final rule they simulate nothing (`target_key_unproven`), and their E3, E4 recall and E5 values
  equal each run's own replayed baseline. Earlier simulated values for them (up to 40 frames in some respend arms) are
  withdrawn. This record and the report never used those values; the supporting runs serve only the E2 decision.
- So, at `468cb05`, §10–§12 stand as printed (rc16 only), as an exact-key-verified partial simulation. The
  recommendation does not rest on these numbers.

**Classification fix after the recompute (`7d90f35`).** A further Lead review found that the frame
classification (E1: which looks count as targeted, and so what A0 removes) and the broad reconstruction still took the
presence of a key as proof. At `7d90f35` the classification basis is exact only from a single answer-proven basis; a
frame is targeted only when its own record under that key is the answer it logged and no broad or earlier frame holds
the same JPEG bytes; it is shared only when a proven look in its group gave its answer; otherwise it is `unproven` and
A0 keeps its answer. A0 also clears the product box, hook, product visibility and group of the answers it removes, and
replayed answers set them from the validated record, as `vision.apply` does. The same five runs were re-run on the
owner's PC at `7d90f35` (exploratory, PREREGISTRATION MISMATCH):

| Item | `7d90f35` |
|---|---|
| Tests | `Ran 98 tests` / `OK (skipped=1)`; scope check (`DUBFLOW_SCOPE_CHECK=815`) `Ran 83 tests` / `OK` |
| Preflight | 5/5 PASS; classification basis `exact` for rc16, `group (cache keys unmatched)` for rc12–rc15 |
| `result_before_gates` | not supported; per-run decision table (§8) identical |
| Run A = run B | yes (JSON `7bf1a369…`, markdown `c485e55f…`, batch `ccfc6deb…` unchanged) |
| Perf (run A) | 368.3 s (run B 365.3 s), peak RAM 340 MB |
| Leak scans (key, e-mail, local paths) | 0 / 0 / 0 |

- Every arm in every run (rc16 151, rc12–rc15 59 each) has the same E2, E3, score, delta and E5 values as at
  `468cb05`. A scores and attribution are unchanged in all five runs (the report keeps A0's status, RECONSTRUCTED,
  not a separate A0 score).
- rc16 classification: 10 frames that hold the same JPEG bytes as an earlier targeted frame move from targeted to
  targeted_shared (targeted 43, targeted_shared 14; targeted frames in item parts 31, was 40). No frame is unproven.
  The broad pass is RECONSTRUCTED with 0 picks not proven in the cache.
- The matched and B0 budgets stay 40 (from the log vision reports), now noted "budget basis differs from
  classification"; the early-bias sample has 35 units (was 39); own-cache frames 12 (was 13). The rc16 markdown
  section differs from `468cb05` only in that budget note.
- rc12–rc15 keep no provable key (matching `unproven`, classification by group).

**Fail-closed fallback (`4607031`, historical; the code changed again after it, §16).** A further Lead review found that without an exact basis the
classification still inferred targeted and shared looks from grouping alone. At `4607031`, with no cache, no proven
basis or more than one, every shot outside the reconstructed broad pass (and the logged sub-shots) is `unproven` and A0
keeps its answer. PC re-run at `4607031` (exploratory, PREREGISTRATION MISMATCH):

| Item | `4607031` |
|---|---|
| Tests | `Ran 98 tests` / `OK (skipped=1)`; scope check `DUBFLOW_SCOPE_CHECK=815 python -B -m unittest -v tests.mastering.longform.test_targeted`: `Ran 83 tests` / `OK`, `test_no_product_path_is_changed ... ok` |
| Preflight | 5/5 PASS; classification basis `exact` for rc16, `group (cache keys unmatched)` for rc12–rc15 |
| `result_before_gates` | not supported; per-run decision table (§8) identical |
| Run A = run B | yes (JSON `1d1b864f…`, markdown `d43c62fc…`, batch `ccfc6deb…` unchanged) |
| Perf (run A) | 357.0 s (run B 357.8 s), peak RAM 341 MB |
| Leak scans (key, e-mail, local paths) | 0 / 0 / 0 |

Per-run classification and arms against `7d90f35`:

| Run | Classification at `7d90f35` | Classification at `4607031` | Targeted frames in item parts | Differing arms |
|---|---|---|---|---|
| rc12 | broad 95, broad_shared 5, multi_frame 3, targeted 44, targeted_shared 14 | broad 95, multi_frame 3, unproven 63 | 31 → 0 | 12 of 59 |
| rc13 | broad 95, broad_shared 5, targeted 44, targeted_shared 14 | broad 95, unproven 63 | 32 → 0 | 12 of 59 |
| rc14 | broad 95, broad_shared 5, targeted 42, targeted_shared 13 | broad 95, unproven 60 | 30 → 0 | 12 of 59 |
| rc15 | broad 95, broad_shared 5, targeted 42, targeted_shared 12 | broad 95, unproven 59 | 31 → 0 | 12 of 59 |
| rc16 | broad 95, broad_shared 5, targeted 43, targeted_shared 14 | unchanged | 31 | 0 of 151 |

- The 12 differing arms in each of rc12–rc15 are exactly the B0/respend arms (open and closed world, B0-mixed at every
  window and B0-P+). With no targeted frame to remove they equal the run's baseline: fixed/broken 0/0 and r@5 equal to
  A (rc12 32, rc13 33, rc14 28, rc15 30; before: 29/30/28/27 with fixed/broken 2/5, 3/6, 4/4, 5/8). These arms were
  never used for a conclusion.
- The primary arm and both comparators (`open:mixed:W=fwd:+matched`, `open:P+:-:+matched`,
  `open:shuffled_prior:W=fwd:+matched`) are unchanged in every run. A scores and attribution are unchanged in all
  five runs; the broad pass is RECONSTRUCTED everywhere; matched budget 40 everywhere.
- rc12–rc15 early-bias samples are now empty (no targeted frame); rc16 stays n 35.
- rc16 matching: the donors' answers are now all offered (none excluded as shared), and each is still admitted only
  under rc16's exact key: 559 admitted, 30 refused (`no_exact_key`), against 520 and 1 before; no rc16 arm, score or
  markdown line changes.

So the E3–E5 numbers in §10–§12 (rc16) held at `4607031`. That head is now historical: the code changed again after
it (§16). The E2 decision of record (`716e984`) is unchanged.


## 16. Review fixes after `4607031` and their PC re-runs (exploratory)

Further Lead and independent reviews of PR #831 changed only `targeted.py` (classification of looks and what A0 keeps).
`targeting.py` (`fd884a26…`), the frozen pre-registration and the E2 decision of record (`716e984`) are unchanged.
Every re-run below is labelled PREREGISTRATION MISMATCH (exploratory) by design.

| Commit | Change |
|---|---|
| `2b24f93` | a shared copy must carry its proven mate's whole answer (confidence, detail and the side metadata `vision.apply` writes); a split shot's middle piece is classed like the shot, only extra-frame pieces are multi_frame |
| `a061581` | a split parent needs its own middle piece; an item part's copy and group represent an id before a hook or intro copy |
| `4558305` | own-record proof for a copy re-asked from the cache in another part; under an exact basis a logged split is `NOT_RECONSTRUCTED`; the A0 caveat line now reads "A0 keeps the logged sub-shots (the replay does not re-split them): a split shot's middle piece is classed like the shot, the extra frames' pieces keep their non-vision evidence only" |
| `c2c8e26` | a copy in a broad pick's duplicate group is never targeted (broad_shared with the pick's answer, otherwise unproven) |
| `669f298` | that rule applies only to a broad pick the broad pass answered |
| `234a159` | Lead review: the selected run-folder entry itself is never followed (only the folders above it are resolved); a selected entry that is a link or junction fails before any handle is opened. Independent review of `567efee`: a link or junction at `.dubflow-work`, `mastering` or `vision` fails the load with no outside bytes; the review snapshot is replaced only by a load that succeeded |
| `ba9ba8b` | each file is read as the size its handle reports plus one byte (then 1 MiB pieces if it grew), not one 256 MiB buffer per file: the Owner-PC profile at `1d4ebfb` (historical) put 56 of 58 s of one rc16 load (895 files) in those reads |
| `567efee` | Windows opens every part relative to its folder's handle (NtCreateFile with FILE_OPEN_REPARSE_POINT; names matched without regard to case, as Windows paths are) and lists folders from their handles; during a load every folder handle is opened once and kept until the load ends; the path-based fallback is removed. Owner-PC unit tests at `567efee` (historical): 138 OK (skipped=3: ScopeTests and two POSIX-only tests), the three Windows-only tests ran and passed |
| `1d4ebfb` | from an independent review of `ee277bd`: a part Windows would read as another name (ending in a space or a dot, or a device name such as `CON` or `nul.jpg`) is refused on every platform, since Windows trims a trailing space or dot from the last part of a path only; on Windows a listing enumerates the held folder handle itself; the final file is opened non-blocking (a file swapped for a named pipe fails instead of hanging) |
| `ee277bd` | the selected run folder is pinned when a load starts (resolved path, device and file id). Where the platform opens a name relative to an open folder without following a link (Linux), every read and listing starts from a handle on the pinned folder and opens each part from the handle before it; on Windows the run folder and every folder on the way are held open without delete sharing until the read ends, the opened file must be the checked one and the whole way is checked again; both ways check the run folder against its pin before and after, so a run folder renamed and replaced by a link, a junction or another folder fails (`UnsafeRunFile`) |
| `52d9c06` | the checks walk each part with `lstat` from the root resolved once (no per-file `resolve`, which opened every file on Windows and slowed the harness about fivefold); a file used as a folder on the way is refused on every platform (Windows reports it as not found) |
| `42b5195` | `load()` reads every input once through `read_run_file` and builds the run from those bytes (no `w3.load_run`, no later reopen by path; the replay's review files come from that snapshot); a name with `:` (NTFS stream) or NUL, a hard link, a folder swapped for a link around the open, or any other unusable path fails with `UnsafeRunFile` naming the relative path only; the replayed side is the side of the answer that proved the donor's log |
| `a8d82ed` | `load()` reads every file (logs, plans, analyses, program/spec, review files, keyframes named in the logs, extra frames, cache records) only as a regular file inside the resolved run folder, with no symbolic link or reparse point on the way and at most 256 MiB, or fails (`UnsafeRunFile`); the donor proof (E3) also requires the record's side metadata to equal the donor's log |
| `e6d000f` | own-record proof also requires the record's side metadata (product box, hook, product visible, product group) to equal the logged evidence (`side_mismatch` otherwise, kept in A0); an unanswered broad pick is not the source of a shared copy |

Owner-PC re-runs (same commands as §4, two runs each, offline, 0 AI calls; leak scans 0):

| Item | `4558305` | `669f298` | `e6d000f` | `234a159` (current code head) |
|---|---|---|---|---|
| Tests (`test_targeted` + `test_w3`) | 109 OK (skipped=1) | 111 OK (skipped=1) | 113 OK (skipped=1) | 142 OK (skipped=3: ScopeTests and two POSIX-only tests); every Windows-only test ran and passed |
| Scope check (`DUBFLOW_SCOPE_CHECK=815`) | 94 OK | 96 OK | 98 OK | 127 OK (skipped=2: the two POSIX-only tests) |
| Run A = run B (except perf) | yes: JSON `0ef48d23…`, markdown `e5696a98…` | yes: JSON `9f06d85a…`, markdown `317f76df…` | yes: JSON `10d95a2c…`, markdown `e7b59b90…` | yes: JSON `891b3723…`, markdown `caa580df…` |
| Real-call batch | `ccfc6deb…` (unchanged) | `ccfc6deb…` (unchanged) | `ccfc6deb…` (unchanged) | `ccfc6deb…` (unchanged) |
| `analysis_sha256` | `2eb3f425…` | `99af3157…` | `5b103661…` | `19370c23…` |
| Perf (run A / B), peak RAM | 462.2 s / 452.4 s, 341 MB | 510.0 s / 354.0 s, 340 MB | 359.7 s / 350.5 s, 341 MB | 445.2 s / 473.9 s, 342 MB (one rc16 load 0.19 s, 895 files) |
| `result_before_gates`, per-run decision table | not supported; identical | not supported; identical | not supported; identical | not supported; identical |
| Broad pass and A0 | RECONSTRUCTED in all five runs | RECONSTRUCTED in all five runs | RECONSTRUCTED in all five runs (rc16: 0 picks not in cache) | RECONSTRUCTED in all five runs |
| Classification | rc12 broad 95, multi_frame 2, unproven 64; rc13 95/63; rc14 95/60; rc15 95/59; rc16 broad 95, broad_shared 5, targeted 43, targeted_shared 14 (item-part targeted frames 31) | identical to `4558305` (0 ids change in any run) | identical to `669f298` (0 ids change in any run) | identical to `e6d000f` (0 ids change in any run) |
| Ids whose class changes vs `4607031` | rc12: 1 (multi_frame to unproven); rc13–rc16: 0 | same | same | same |
| Differing arms vs `4607031` | rc12 6 of 59 (the open B0 arms: r3hit at W=10 s/15 s/30 s/fwd 1 to 2, chance 2.7884 to 2.8006; r@5 32/60 and fixed/broken 0/0 unchanged); rc13–rc15 0 of 59; rc16 0 of 151 | 0 of 59 / 0 of 151 vs `4558305` | 0 of 59 / 0 of 151 vs `669f298` | 0 of 59 / 0 of 151 vs `e6d000f` |
| Donor matching provenance (rc16) | | | exact 559, no_exact_key 30 | exact 556, no_exact_key 30, side_mismatch 3 (own cache 12); rc12–rc15 no key basis (572/571/572/573), as before |

The primary arm and both comparators, A scores, attribution and the matched budget (40) are unchanged in every run at
all four heads. At `e6d000f` the own-record proof also compares the side metadata; on rc16 the first copy of each of
the 157 answered ids gives `exact` 148 and `no_exact_key` 9 (shared copies without a record of their own), and no
record fails on its side metadata (`side_mismatch` 0, `answer_mismatch` 0). So the E1 classification/A0 and the E3–E5
numbers in §9–§12 (rc16) held at `e6d000f`, as exploratory results; the E2 decision of record (`716e984`) is
unchanged.

**Current code head `234a159`: measured (exploratory).** It pins the selected run folder (never following the selected entry),
opens every part relative to its folder's handle, reads the run once from checked bytes inside it and makes the donor
proof also compare the side metadata. Its Owner-PC re-run (A = B, leak scans 0, no `UnsafeRunFile`) equals `e6d000f`
in every class, every arm and every decision value. The only result differences in the whole JSON are the
code/analysis digest and the rc16 donor matching provenance: 3 donor records whose side metadata differs from the
donor's log are now `side_mismatch` and no longer replayed (exact 559 to 556), and no E3–E5 number changes. The
markdown differs only in the pre-registration header line (the analysis digest). So the E1 classification/A0 and the
E3–E5 numbers in §9–§12 (rc16) hold at `234a159`, as exploratory results; the E2 decision of record (`716e984`) is
unchanged. Owner-PC history at the intermediate heads: `52d9c06` and `567efee`/`ba9ba8b` were stopped before run A
for later findings (tests passed); the `1d4ebfb` profile (58.2 s per rc16 load, 56 s in cap-sized reads) led to
`ba9ba8b`.

Known fail-closed losses (accepted; answers are kept or not replayed, never replayed or removed without proof):

- An extra frame's answer (item index 1 or 2, or a split shot's extra-frame piece) is never given the side metadata by
  production (`frames.look_again`/`frames.merge`), so its record's side cannot be proven against the log: such donor
  and own answers are NOT_SIMULABLE whenever the record carries a hook, box or product label.
- A file under any NTFS reparse point, including compression (WOF) or deduplication tags, fails the load: run folders
  must sit on a plain folder.

W3 REAL-DEV (development, not a holdout). GENERALIZATION NOT VERIFIED; Gate B not claimed.
