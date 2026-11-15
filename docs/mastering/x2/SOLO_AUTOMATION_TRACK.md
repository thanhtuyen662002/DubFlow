# SOLO_AUTOMATION_ENGINEERING_EVALUATION

Owner directive 2026-11-15 (Solo-Founder Autonomous Execution V2). DubFlow is a one-person project. No narration writer B, labeler, adjudicator or independent reviewer exists or will be recruited.

This track sits **beside** the X2 protocol (`README.md`, `X2_EVALUATION_DATASET_PROTOCOL.md`, `X2_POC_DESIGN_V2.md`) and does not replace it.

## 1. Two levels of evidence

| Level | Name | What it may claim | What it never claims |
|---|---|---|---|
| 1 | **Engineering Evaluation** (this track) | A defect exists and is reproduced. A fix changes a deterministic metric on fixtures whose answers are known by construction. A safety invariant holds | Quality on real, unseen video. Human Validation. REAL-HOLDOUT. A Gate B/D or READY-threshold change |
| 2 | **Formal Evaluation** (X2 protocol) | Quality on independent data, measured against locked human labels | — |

- Level 2 is **FORMAL_EVALUATION_BLOCKED** while there are no human labels. It stays blocked until valid evidence exists or the Owner approves a new protocol with its own standard and limits.
- Level 1 never silently stands in for level 2.

Every level-1 artefact is tagged `ENGINEERING_FIXTURE_ONLY = TRUE` (synthetic fixture), or `PROXY_ONLY` (AI or heuristic judge). These never enter REAL-DEV or REAL-HOLDOUT.

## 2. READY_FOR_EVALUATION conditions by kind

Kinds: (1) deterministic and automatic, (2) automatic but proxy only, (3) needs a person under the current protocol, (4) needs a data-rights or cost approval.

| Condition (`validate_manifest.py --stage eval`, README §3.1) | Kind | Solo status |
|---|---|---|
| Everything of READY FOR PAID SCRIPT DRAFT (rights, sha256, scope, local-ASR cues) | 1 (rights already given by the Owner) | **PASS** for REAL-DEV |
| Narration/cues locked (sha256, `locked_at`, method, writer) | 1 for the lock; the writing needs (4) for an AI draft or (3) for a human writer | Lock tool exists. AI draft waits on the 1a cost approval |
| `writer_saw_footage: false` | 1 for an AI draft fed text only (the harness sends no frame). For a human it is (3) self-declared | Holds by construction for the 1a harness |
| `cue_rejections` recorded | 1 (`script_draft.py` records invented cue ids) | Ready |
| 20–30 sentences | 1 for the count | — |
| No-footage sentences and hard negatives by a writer B who is not writer A | **3** | **BLOCKED**. An AI may not write them and present them as writer B's |
| Human labeler, labels not AI-judged, labels locked | **3** | **BLOCKED** |
| Scoring rules locked to a commit | 1 | Possible at any time |
| HOLDOUT: two labelers, separate adjudicator, κ, parameters locked | **3** | **BLOCKED** |
| Sending frames to a Vision API (arm measurement, 1b) | **4** (scope is `transcript_text_only`) | **BLOCKED** for d-s1 and d-s2 |
| Paid text draft (1a) | **4** (cost) | Prepared, not approved |
| Picture-level "this shot shows what the sentence says" on real video | 3 (human) or 2 (AI judge, proxy only; needs frames, so also 4) | **No level-1 substitute** |

## 3. The engineering benchmark (`tests/mastering/x2/solo_bench.py`)

The fixture is a synthetic video, made by ffmpeg, with no download and no model. It has 12 textured coloured shots of 6 s each, and one transcript line per shot at a fixed offset inside the shot.

Each line names a thing only that shot "shows". This is a fixture assumption: the speech describes its own shot. Real sources do not guarantee it.

There are 16 narration units:
- 8 match;
- 1 montage;
- 7 hard negatives with the known answer "no footage": 2 entity, 2 action, 1 time, 2 unrelated.

Each unit is scored against the construction:

| Metric | Answer from |
|---|---|
| Top-1 shot in the right segment; recall@5 | Shot boundaries |
| Played span overlaps the cue span; start offset | Cue span |
| READY count | Must be 0: transcript is not picture evidence |
| False assignment of a hard negative (footage claimed) | "No footage" by construction |
| Final QC on the whole part; wall time | — |

Run it with `python tests/mastering/x2/solo_bench.py <work dir>`. It takes about 5 s. Without ffmpeg, the end-to-end tests skip.

### Baseline on main `d91a5a2` (2026-11-15)

| | With cues | Without cues (silent or empty ASR) |
|---|---|---|
| Shots detected | 12/12 | 12/12 |
| READY | 0 | 0 |
| Match top-1 in the right segment | **9/9** | 1/9 |
| Played span overlaps the cue | **4/9** | 1/9 |
| Median start offset (played start − cue start) | **−3,180 ms** | — |
| Simulated cue-aligned in-point: played over the cue | **9/9** | — |
| Simulated cue-aligned in-point: median offset | **−420 ms** | — |
| Hard negatives with footage claimed | **5/7** (entity 2/2, action 2/2, time 1/1, unrelated 0/2) | 0/7 |
| Final QC on the unreviewed part | Blocks: decisions, claims, and timeline (held frames once the footage runs out) | Blocks: decisions, claims |

Reading:
- **Retrieval works when the source says the words.** The thing the planner does wrong is *where* in the shot it plays: it plays from the start of the shot (finding D1).
- The cue-aligned in-point is a **simulation** of a proposed planner rule, not production code (`cue_aligned`).
- **Lexical retrieval cannot tell a hard negative from a match** when the two share content words (entity, action, time). Safety holds because such a unit stays NEEDS_REVIEW and never READY. Telling them apart needs picture evidence or claims.

## 4. Same metric on the real d-s1 source (where the words are said)

The ground truth here is the local ASR cue. It says where the words are spoken, not what the picture shows. The probe sentences were written by the agent, so they are not human labels.

| | Main |
|---|---|
| 6 English positives: played span over the matching cue | 3/6 (3 play before their cue) |
| Simulated cue-aligned in-point | 6/6 |
| 3 Vietnamese probes of the same facts (narration language ≠ source language) | 2/3 find footage, only through names and numbers (relevance 72–116 of 1000); 1/3 none |

A Vietnamese narration over an English source is close to blind for lexical retrieval. This is the case X2's cue provenance (arms B/C) is meant to fix.

## 5. SOLO-QA (lower evidence level, proposed)

When the Owner has a few minutes, this is the procedure. It is optional and never a gate for engineering work.

1. The harness writes a short list (≤ 10) of the units where a decision matters most: a contradicted claim, a cue-aligned shot that differs from the baseline, a hard negative that got footage.
2. The Owner looks at each list item once. The answer is recorded as `owner_spot_check` with a timestamp, and **is not** a label in the X2 sense: there is one person, who knows the system, and nothing is adjudicated.
3. Spot checks may support or refute an engineering finding. They never make a formal result.

## 6. What this track does not do

- It does not change the READY threshold, Gate B/D, #844, or the production planner, retrieval or verification policy. A production change found here goes to a proposal with its evidence and stops at the Owner's gate.
- It does not send frames, audio or video to any API. It does not call a paid API without approval.
- It does not put media in the repository. It does not use W3 as independent data.
