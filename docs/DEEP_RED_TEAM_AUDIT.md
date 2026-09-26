# DubFlow Deep Red-Team Audit — Architecture, GitHub Flow, CI and Delivery

Date: 2026-09-26

Purpose: deliberately try to kill DubFlow both as a product and as an engineering program. This document is not a feature wish list. It records failure modes that can silently corrupt output, stop autonomous development, create false-green evidence, or make one stalled worker a project-wide bottleneck.

## Roles used in the attack

### Hostile end user
Assumes:
- double-clicks the app repeatedly;
- closes/reboots the PC at arbitrary moments;
- fills the disk;
- imports a corrupt 6-hour VFR video;
- queues a whole channel;
- changes settings after a job is almost complete;
- expects one click and does not read technical logs.

### BA / product owner
Attacks ambiguity:
- two requirements that both sound correct but imply opposite implementations;
- “unlimited” promises with hidden resource limits;
- “local-first” with accidental cloud dependency;
- “editable” output tied too tightly to CapCut internals.

### Media pipeline engineer
Attacks:
- PTS/time-base drift;
- VFR;
- rotation/SAR/DAR;
- partial files;
- hardware codec failures;
- parser crashes;
- long-form chunk boundaries.

### AI/ML engineer
Attacks:
- confidence drift;
- ASR/OCR disagreement;
- diarization ≠ character identity;
- off-screen voice;
- animation identity;
- TTS timing;
- model update incompatibility.

### Desktop/runtime engineer
Attacks:
- two app instances;
- worker orphaning;
- SQLite ownership;
- updater races;
- model cleanup while mmap/inference is active.

### CI/CD engineer
Attacks:
- stale green CI;
- synthetic merge SHA confusion;
- queue starvation;
- two-hour jobs;
- flaky live services;
- workflow action deprecation;
- red main after individually green PRs.

### Autonomous-agent coordinator
Attacks:
- duplicate claim race;
- heartbeat with no useful work;
- local-only commits;
- Epic over-claim;
- dependency cycles;
- stale lease while CI is legitimately queued.

### Security engineer
Attacks:
- malicious media;
- path traversal;
- browser cookie leakage;
- unsigned model/runtime updates;
- release secret exposure;
- archive extraction;
- downloader/runtime supply chain.

### QA/release engineer
Attacks:
- “100%” progress with invalid output;
- stale QC after selective rerun;
- benchmark that is too small;
- passing functional tests while quality regresses.

---

# 1. Contradictions found and resolved

## C-001 Linear job state vs selective rerun
Original product documentation visually described one linear chain:

```text
CREATED → ... → TRANSLATED → DUB_READY → ... → DONE
```

But the product also requires:
- edit one voice and regenerate only that character;
- edit one subtitle and avoid re-running ASR/OCR;
- recover at stage boundaries;
- change inpaint mode without re-translating.

A literal linear state machine cannot express these requirements cleanly.

**Resolution:** the ordered stage list is UI/presentation. Internal execution is a versioned artifact DAG with provenance and invalidation.

## C-002 “Soft subtitle first” vs “ASR is meaning source”
The discovery section listed subtitle sources in a priority-looking order, while the subtitle intelligence section stated ASR is the meaning source when speech is clear.

Two agents could implement opposite rules.

**Resolution:** source list is discovery order only. Spoken dialogue uses evidence-based TranscriptCandidateSet arbitration with provenance.

## C-003 Exact-head CI vs pull_request synthetic merge ref
The engineering protocol correctly forbids stale-head evidence, but GitHub pull_request workflows commonly check out a synthetic merge ref.

If tooling compares the workflow tested SHA directly to PR head SHA, it can incorrectly call valid merge-ref evidence stale—or worse, use old PR-head evidence after a push.

**Resolution:** preserve two fields:
- `pr_head_sha`
- `tested_sha`

A run is valid only when it belongs to the current PR head, while its tested SHA/ref describes whether source-head or merge compatibility was actually exercised.

## C-004 timeout-minutes vs queued forever
Workflow job timeout starts after runner execution begins. It does not guarantee bounded queue wait.

**Resolution:** watchdog gets an independent queue-age policy and WAITING_INFRA state.

## C-005 Heartbeat vs progress
A worker can update timestamps forever and own an Issue without producing anything.

**Resolution:** lease liveness and useful progress are separate. Heartbeats require GitHub-visible durable checkpoints over time.

## C-006 App-owned SQLite vs multiple app processes
Single-writer SQLite inside one process is not enough if two DubFlow application instances open the same project.

**Resolution:** one writable supervisor/project lock, read-only/handoff behavior for additional instances, and validated stale-lock recovery.

## C-007 Auto model cleanup vs resumable jobs
A paused job may require an exact old model version. Automatic cleanup can make resume impossible or behaviorally different.

**Resolution:** active/resumable jobs pin model/runtime versions; cleanup uses reference-aware retention.

---

# 2. Product kill scenarios

## P-001 Hard kill between file write and DB commit
Attack:
1. worker writes an artifact;
2. process dies before state commit;
3. app restarts.

Failure if naive:
- orphan files accumulate;
- rerun sees file and assumes valid;
- DB and filesystem disagree.

Required behavior:
```text
temp write
→ flush/validate/hash
→ atomic rename
→ DB provenance transaction
→ UI notification
```

Recovery scans orphans and validates by manifest/hash.

## P-002 DB says artifact exists but file is missing
Possible causes:
- user cleanup;
- antivirus quarantine;
- disk corruption;
- manual move;
- failed external tool.

Required behavior:
- artifact becomes invalid;
- downstream nodes become stale;
- only true dependency descendants rerun.

## P-003 User changes one character voice at 95%
Naive flow:
- restart translation, TTS, mix, render from scratch.

Correct:
- invalidate TTS for that character/segments;
- invalidate affected mix/render/QC;
- preserve ASR/OCR/translation/identity when unchanged.

## P-004 User edits one translated subtitle
Correct invalidation:
```text
translation segment
→ TTS segment if dubbed
→ subtitle render
→ mix if audio changed
→ final render
→ QC
```
No ASR/OCR rerun.

## P-005 6-hour VFR video
Attack:
- irregular frame timing;
- non-zero source PTS;
- scene cuts across chunks;
- audio drift.

Required:
- integer canonical ticks;
- rational time-base mapping;
- reversible proxy mapping;
- overlap merge;
- no frame number as identity.

## P-006 500 queued videos + one huge job
Naive scheduler gives GPU/render lock to one long job indefinitely.

Required:
- resource fairness/aging;
- bounded parallelism;
- chunk-safe checkpoints;
- priority/pause;
- one poisoned job quarantine.

## P-007 Disk estimate is initially correct, debug cache explodes later
Static preflight is insufficient.

Required:
- rolling free-space guard;
- retention classes;
- debug sampling;
- safe GC order.

## P-008 Two app instances
Attack:
- user double-clicks;
- updater relaunches before old process exits;
- old orphan still owns handles.

Required:
- project ownership lock;
- live-process validation;
- second instance handoff/read-only;
- no dual output directory mutation.

## P-009 Model update while job paused
Attack:
- job used model A v1;
- updater installs v2 and deletes v1;
- job resumes.

Forbidden:
- silent v2 substitution.

Allowed:
- reacquire v1;
- explicit migration with artifact invalidation;
- keep pinned v1 until job terminal.

## P-010 Cleanup while worker mmaps model
Windows can hold file handles; forced cleanup/update may fail or corrupt staged state.

Required:
- package lock/refcount;
- safe checkpoint;
- defer deletion.

---

# 3. Subtitle/OCR kill scenarios

## S-001 Five text systems in one frame
Frame contains:
- dialogue subtitle;
- username;
- watermark;
- shop sign;
- danmaku.

Naive OCR→translate fails.

Required:
- temporal text tracks;
- role classifier;
- speech/text alignment;
- removal only for authorized classes.

## S-002 Subtitle is vertical/diagonal
Axis-aligned bounding boxes cause oversized masks and destroy image regions.

Required:
- quadrilateral polygon;
- orientation;
- rectified OCR crop;
- polygon-preserving removal.

## S-003 Karaoke/animated per-word subtitles
Text may change every few frames.

Required:
- temporal grouping aware of stable region + evolving glyphs;
- do not treat every frame as a new independent subtitle.

## S-004 OCR says one sentence, ASR says another
Do not hide the disagreement.

Required:
- candidate provenance;
- confidence;
- semantic/timing evidence;
- QC flag if unresolved.

## S-005 No speech, but narrative caption exists
ASR cannot be semantic source.

Required:
- visual-text mode for narrator_caption/title classes;
- translate based on role/policy.

---

# 4. Speaker/character kill scenarios

## SP-001 Camera shows listener while off-screen speaker talks
Visible character cannot be selected merely because they are on screen.

Required:
- offscreen/narrator identity;
- audio continuity;
- active-speaker evidence.

## SP-002 Same actor voices two animated characters
Audio diarization may merge them.

Required:
- visual identity evidence can split usage into separate character mappings.

## SP-003 Two similar voices
Audio-only clustering may swap identities.

Required:
- global audiovisual association;
- continuity constraints;
- unresolved state allowed.

## SP-004 Two speakers overlap
Data model must allow overlapping utterances.

Do not serialize into fake non-overlapping turns.

## SP-005 Character disappears for multiple scenes
Chunk-local IDs can swap after return.

Required:
- global identity reconciliation;
- cross-shot embeddings/continuity;
- uncertainty preserved.

## SP-006 Cross-video Character Registry over-merges
Two similar people/characters can accidentally share one voice profile.

Required:
- identity scope;
- threshold + evidence;
- unresolved state;
- explicit correction constraints.

---

# 5. Translation/TTS kill scenarios

## T-001 Long Vietnamese translation cannot fit original slot
Forbidden first response:
- 1.6×/1.8× speedup until intelligibility collapses.

Preferred:
1. context-aware shorter rewrite;
2. cue segmentation;
3. safe speaking-rate change;
4. consume nearby silence if policy allows;
5. light high-quality stretch last.

## T-002 TTS engine “supports Vietnamese” but reads names/numbers badly
Required:
- benchmark;
- back-ASR;
- glossary pronunciation;
- human-auditable sample set.

## T-003 Model update changes confidence calibration
Same numeric confidence does not mean same reliability across versions.

Required:
- versioned calibration;
- golden-set thresholds;
- model provenance on scores.

## T-004 User changes glossary after translation
Glossary hash belongs in translation provenance and invalidates affected downstream nodes.

## T-005 TTS emits silence/corrupt waveform but returns success
Validate:
- duration;
- non-zero energy where expected;
- decode validity;
- NaN/inf;
- target coverage via back-ASR where appropriate.

---

# 6. Download/input kill scenarios

## D-001 Douyin changes extractor behavior
Required CI must remain green because live source smoke is isolated.

Operational result:
- source adapter reports DOWNLOAD_SITE_CHANGED;
- live-source Issue/watchdog updates;
- local-file/Bilibili/unrelated development continues.

## D-002 Whole-channel enumeration dies halfway
Persist:
- scan ID;
- cursor;
- discovered canonical IDs;
- item states.

Do not restart from page 1 blindly.

## D-003 Platform returns duplicate URL variants
Dedup cannot depend only on URL text.

Use:
- provider;
- stable source ID where available;
- content hash after download.

## D-004 Malicious filenames
Reject/sanitize:
- `../`;
- absolute paths;
- Windows device names;
- trailing dot/space problems;
- invalid characters;
- overlong names.

Never let remote metadata choose arbitrary output paths.

---

# 7. Security kill scenarios

## SEC-001 Malicious media targets FFmpeg/parser
DubFlow processes untrusted native formats.

Controls:
- patched pinned builds;
- normal-user privileges;
- scoped directories;
- no unnecessary network access from media worker;
- bounded process control where practical.

## SEC-002 Model pack points to executable payload
No arbitrary remote pip/package install.

Controls:
- signed/checksummed manifest;
- package type;
- approved origins;
- staging/rollback.

## SEC-003 Zip Slip in runtime/model archive
Every archive member must resolve under staging root after canonicalization.

## SEC-004 Cookie leaks into diagnostics
Diagnostic exporter must redact:
- cookies;
- Authorization headers;
- session files;
- browser profiles;
- sensitive source URLs when configured.

## SEC-005 PR exfiltrates release secrets
Normal PR workflows receive no release signing/live-site secrets.

Signing uses protected release context and least privilege.

## SEC-006 Untrusted project file triggers arbitrary path reads
Editable manifests/timeline imports must validate paths and permissions; project-relative resources cannot escape approved roots without explicit user action.

---

# 8. GitHub/autonomous-development kill scenarios

## G-001 Two agents claim #3 simultaneously
Protocol:
1. both may create branches;
2. each opens Draft PR immediately;
3. each re-reads open PRs;
4. earliest valid claim wins;
5. later claimant stops.

Without post-claim recheck, both could advance for hours.

## G-002 Agent works locally for one hour before Draft PR
All that work can be duplicated/lost.

Rule:
- no material implementation before GitHub lease exists.

## G-003 Agent keeps refreshing heartbeat
Rule:
- heartbeat is liveness;
- pushed commit/review/CI fix/blocker evidence is progress;
- no-progress can be reclaimed.

## G-004 CI run green on old PR head
Current PR head is authoritative. Old run is evidence only for old head.

## G-005 pull_request run checks synthetic merge ref
Watchdog stores both source head and tested ref/SHA.

## G-006 Runner never allocated
Job `timeout-minutes` is insufficient.

Need:
- queue age;
- WAITING_INFRA classification;
- no unnecessary lease expiry.

## G-007 Live Douyin test fails during unrelated UI PR
Live-source smoke is non-universal. UI PR must not be blocked.

## G-008 GPU benchmark takes two hours
Benchmark lane is targeted/scheduled and cannot consume normal PR-fast runner capacity.

## G-009 One contract PR becomes massive
Break into:
- schema/contract;
- reference implementation;
- integration;
- migration/compatibility.

Do not make every lane wait for a giant PR.

## G-010 Main changes while high-risk PR waits
For timeline/worker/migration/shared contracts:
- update against main;
- re-run merge compatibility;
- avoid stacking incompatible green PRs.

## G-011 Watchdog dies
No effect on correctness of protocol.

Any authorized agent reconstructs:
- leases;
- heads;
- CI;
- dependencies;
- conflicts
from GitHub.

## G-012 Watchdog becomes required PR check
Forbidden. It would become the very single point of failure it is meant to detect.

---

# 9. CI kill scenarios

## CI-001 Monolithic workflow
If Rust, Python, desktop, GPU benchmark, live-source smoke and packaging all become one universal workflow, CI becomes the project bottleneck.

Required separation:
- PR Fast;
- selective Integration;
- AI/GPU Benchmark;
- Live Source Smoke;
- Soak/Chaos/Release.

## CI-002 Stable check renamed after branch protection
Branch protection can wait forever for a check that no longer exists.

Rule:
- stable required check names;
- migration plan before rename.

## CI-003 Action runtime deprecation
Bootstrap runner already emitted warnings that current action majors target deprecated Node.js 20 and are forced onto Node.js 24.

Tracked in #10.

## CI-004 Cancel-in-progress abuse
Constant small pushes repeatedly cancel expensive runs and starve evidence.

Rule:
- push coherent checkpoints;
- do not “save” every tiny edit to expensive validation branches.

## CI-005 Flaky test rerun until green
Forbidden for deterministic failures.

Classify:
- deterministic code/test;
- infra;
- external;
- known flaky.

Only infra/known-flaky gets selective rerun without code change.

---

# 10. Release/installer kill scenarios

## R-001 Clean Windows machine has no Python/CUDA/FFmpeg
Expected path still works because product owns runtime and FFmpeg.

## R-002 CPU-only machine
Performance degrades but correctness path remains available.

## R-003 Updater changes engine API while old app/job exists
Compatibility manifest blocks unsafe switch.

## R-004 DB migration fails halfway
Transactional migration + backup + validation + rollback.

## R-005 App update succeeds but model manifest is incompatible
Healthcheck fails; atomic switch does not commit.

## R-006 Signing service unavailable
Normal development/PR CI continues; release is blocked, not engineering.

---

# 11. Progress-kill analysis

The biggest schedule risks are not “hard AI research” alone. They are hidden serialization points.

## Highest serialization risks
1. canonical timeline contract;
2. worker protocol;
3. SQLite migrations/job state;
4. shared workspace/lockfiles;
5. CI workflow ownership;
6. release/update compatibility manifests.

These must have:
- narrow Issues;
- explicit conflict domains;
- versioned contracts;
- mock-first downstream escape paths.

## Work that must stay parallel
- desktop UX against mocks;
- source adapter fixtures;
- golden benchmark harness;
- model/license inventory;
- CI lane construction;
- AI prototypes against versioned interfaces.

## Work that must not become universal dependencies
- direct CapCut draft;
- live Douyin/Bilibili access;
- GPU runners;
- a single TTS model;
- inpainting quality;
- release signing;
- watchdog availability.

---

# 12. Current finding ownership

Already addressed in Issue #16 / this PR:
- linear state vs DAG;
- source arbitration;
- claim race;
- durable progress;
- merge-ref/head semantics;
- queue-age semantics;
- multi-instance DB;
- pinned model retention.

Existing Issues:
- #3 canonical timeline.
- #4 worker protocol.
- #5 durable state/recovery.
- #6 desktop UX.
- #7 source adapter.
- #8 fixture/benchmark harness.
- #9 runtime/model/license manifest.
- #10 CI lanes and action runtime deprecation.
- #11 branch/ruleset protection.
- #14 first vertical slice.
- #15 lease/stuck-CI watchdog.

Concrete follow-up leaf Issues created by this audit:
- #17 — artifact DAG/provenance/cache invalidation implementation;
- #18 — project/process/resource locking and multi-instance recovery;
- #19 — input/path/archive/media-processing security boundary;
- #20 — fair batch resource scheduler and rolling disk guard;
- #21 — model/runtime retention + safe garbage collection;
- #22 — semantic QC provenance and confidence calibration.

---

# 13. Exit criterion for this audit

The audit is successful only if future agents cannot reasonably implement two contradictory interpretations while both claiming to follow the docs.

The remaining uncertainties are allowed to exist only when they are:
- explicit;
- versioned behind interfaces;
- assigned to a prototype/benchmark Issue;
- unable to freeze unrelated work.

