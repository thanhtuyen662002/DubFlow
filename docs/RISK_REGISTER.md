# DubFlow — Unified Risk & Failure Register

This register covers both the **product runtime** and the **GitHub engineering system**. A failure is important even when no process crashes: a pipeline that returns the wrong speaker, translates a watermark, or lets engineering silently stop is also a production failure.

## Severity
- S5: project/release/install can be bricked or durable data lost.
- S4: job/batch/repository progress can stop or major work is discarded.
- S3: one feature/job/PR fails and needs recovery.
- S2: output is materially wrong but looks successful.
- S1: degraded UX/performance.
- S0: cosmetic.

## Product kill paths

### Media timeline
- **VFR / non-zero PTS / broken timestamps / rotation metadata** can make OCR coordinates, subtitle timing and TTS drift while every component independently reports success. Canonical time must use integer timeline ticks derived from source time bases; frame indexes and floating-point seconds are display values, not identity.
- **HDR/10-bit/unusual codecs/multi-audio tracks** must be probed and preserved deliberately; hardware decode/encode is an optimization with software fallback.
- **Long video** must never imply whole-video frames/audio/model state held in RAM. Every expensive stage must be chunkable, checkpointable and overlap-mergeable.

### Download/source
- Douyin/Bilibili/site changes, login expiry, 429, CAPTCHA and extractor regressions are normal operating conditions, not exceptional architecture events.
- yt-dlp must remain behind a source adapter. Live-site tests cannot be mandatory PR gates because third-party instability would halt development.
- Channel enumeration must checkpoint cursor + canonical source IDs so partial scans resume.
- A single poisoned source must be quarantined and must not stop a batch.

### Subtitle intelligence
- OCR text is not automatically dialogue. Logo, watermark, danmaku, signage, UI, title and decorative text must be classified before translation/removal.
- Bounding boxes alone are insufficient; use polygons/orientation and temporal text tracks.
- Multi-frame consensus is mandatory for moving/occluded/stylized text.
- Dialogue meaning should prefer ASR when speech is clear; OCR supplies geometry and corroboration. Conflicts must preserve provenance and confidence.
- Never remove a region that has not been classified as removable dialogue/caption text.
- Post-removal OCR must detect ghost glyphs and trigger repair/fallback.

### Speaker/character intelligence
- Diarization identity != visual character identity != active speaker.
- Camera may show the listener while another person speaks; visual presence cannot override audio continuity.
- Offscreen/narrator speech needs its own identity class.
- Two simultaneous speakers require overlapping utterance data, not forced serialization.
- Same actor/multiple animated characters can require splitting one audio cluster by visual evidence.
- Similar voices require audiovisual evidence; animation may not provide human facial landmarks, so character tracking cannot depend on a human-only face detector.
- Global identity reconciliation is required across chunk boundaries to prevent label swaps.

### Translation/TTS
- Translation must be context-windowed by speaker/scene/glossary, not cue-by-cue.
- A translation longer than the slot must first be rewritten/resegmented; extreme time-stretch is forbidden as the primary fix.
- TTS engines are plugins. "Supports Vietnamese" is not a quality gate; local benchmark data decides the default.
- Every generated waveform must be validated for duration, silence, NaN/corruption and target-text coverage.
- Back-ASR and voice-embedding consistency checks should catch missing words, bad names and character voice drift.
- Existing jobs pin model versions; a model update cannot silently change a resumed job.

### Audio separation
- Dialogue/music/effects separation can remove explosions/music together with speech. Prefer confidence-aware attenuation over destructive removal when artifacts are high.
- Never collapse final stereo merely because analysis ASR is mono.
- Final mix requires clipping and loudness validation.

### Inpainting/subtitle removal
- Per-frame masks cause flicker; masks must come from temporally smoothed tracks.
- Inpainting can damage faces/textures. Risk scoring must allow an adaptive-cover fallback.
- Shadow/outline/glow must be included in the mask.
- Failure of inpainting must not prevent a usable final export.

### Render/output
- Hardware encoders need runtime capability tests and software fallback.
- Partial outputs use a temporary suffix and become final only after ffprobe/QC validation.
- Disk-full detection must happen before corruption; render can restart without redoing AI analysis.
- CapCut is never the canonical project format. Stable video/audio/SRT/ASS/timeline assets must survive CapCut schema changes.

### Installer/updater/model supply chain
- Do not use system Python.
- Model downloads are resumable and checksum-verified.
- App/engine/model versions use explicit compatibility contracts.
- Updates are staged, signed/verified, applied only at safe checkpoints, health-checked and rollback-capable.
- Database migrations are transactional with backup/validation.
- Release signing credentials must never be accessible to ordinary PR code or untrusted fork workflows.
- Every redistributed binary/model/codec dependency requires a recorded license/distribution decision before packaging.

### State/recovery
- Rust supervisor is the single durable-state writer; workers communicate by protocol and must not mutate SQLite independently.
- Artifact completion order: write/flush -> validate/hash -> DB transaction -> UI event.
- Orphan files after crash are reconciled by content hash/manifests.
- Cancellation is cooperative and preserves reusable checkpoints.
- Retry budgets are finite and each retry must materially alter conditions.

## Engineering/GitHub kill paths

### G-001 Unprotected main — S5
Current repository started with no branch protection/ruleset. Any agent with push can bypass review/CI.

Mitigation:
- no direct main pushes by policy;
- PR-only workflow;
- repository ruleset/branch protection must require stable named checks once CI exists;
- force-push/delete main prohibited.

### G-002 Mega critical path — S5
If every task depends on one giant foundation PR, one stalled worker stops the project.

Mitigation:
- vertical foundation slice kept small;
- split work into independent lanes after contracts are established;
- dependencies must be explicit and minimal;
- mocked adapters allow downstream work before real integrations are complete.

### G-003 One dead agent owns an Issue forever — S4
Assignee is not a lease.

Mitigation:
- immediate Draft PR is the claim;
- PR body carries machine-readable lease metadata;
- heartbeat/checkpoint required;
- stale lease may be reclaimed without deleting prior branch/work.

### G-004 Two agents claim same task — S4
Mitigation:
- preflight reads all open Draft PRs and issue state;
- earliest valid lease wins;
- loser stops before material edits;
- shared-path conflict check before claim.

### G-005 Hidden path overlap — S4
Two unrelated Issues both edit workspace manifests, DB migrations, schemas, lockfiles or state-machine contracts.

Mitigation:
- Issue must declare expected paths and conflict domains;
- high-contention files are serialized by a coordination lease;
- lockfile-only overlap is still material when dependency changes differ.

### G-006 Stale CI accepted — S5
Green CI from an older commit must never authorize merge of a newer HEAD.

Mitigation:
- all merge/review decisions use exact PR head SHA;
- check runs are re-read after the final push.

### G-007 CI run hangs for hours — S4
Mitigation:
- every job has timeout-minutes;
- same-PR superseded runs are cancelled by concurrency groups;
- PR-fast tests are short;
- long GPU/live-site/soak benchmarks are not blocking every PR;
- watchdog distinguishes queued/running/hung/infra failure/test failure.

### G-008 CI runner starvation — S4
Long integration jobs can occupy all runners.

Mitigation:
- tiered workflows: fast required, selective integration, scheduled soak;
- path-based execution;
- matrix sharding;
- self-hosted GPU runners have dedicated labels and concurrency limits;
- no live-site smoke in the required fast lane.

### G-009 Flaky external tests halt merges — S4
Douyin/Bilibili/CapCut/network availability cannot be a required deterministic gate.

Mitigation:
- fixtures/recorded contracts for required CI;
- live smoke scheduled or manually dispatched;
- live failures open/update an operational issue, not invalidate unrelated code.

### G-010 PR too large — S4
A 5,000-line cross-stack PR becomes impossible to review and merge without conflict.

Mitigation:
- one executable Issue -> one focused PR;
- PR declares changed components and contracts;
- split foundational contracts from implementations;
- large generated/model assets never belong in normal Git history.

### G-011 Epic treated as executable work — S4
Agents may claim an Epic and implement arbitrary scope.

Mitigation:
- Epics track outcomes only;
- agents claim leaf tasks containing scope, dependencies, acceptance and evidence requirements.

### G-012 Dependency deadlock — S4
A waits on B, B waits on C, C waits on A.

Mitigation:
- dependency graph validator;
- Issues may depend only on stable IDs;
- cycles are release-blocking governance errors;
- use interface-first/mock tasks to break unnecessary sequencing.

### G-013 Architecture drift — S4
Two agents implement incompatible timeline units, worker protocols or artifact schemas.

Mitigation:
- architecture invariants in AGENTS.md;
- versioned schemas in contracts/;
- compatibility tests;
- architecture changes require ADR plus migration plan.

### G-014 Database migration collision — S5
Parallel workers allocate conflicting migrations.

Mitigation:
- migration changes are a conflict domain;
- sequence IDs allocated only after claim;
- CI detects duplicate/down migration problems;
- migrations never edited after release except with corrective migration.

### G-015 Contract change silently breaks downstream — S5
Mitigation:
- schema compatibility check;
- producer/consumer version matrix;
- changed contract PR must identify dependent modules;
- compatibility shim/deprecation window when feasible.

### G-016 Lockfile/dependency churn — S3
Multiple branches update Rust/npm/Python lockfiles and create repeated conflicts.

Mitigation:
- dependency upgrades isolated from feature work unless required;
- workspace dependency policy;
- merge/update branch before final CI.

### G-017 Agent "status-only loop" — S4
Automation repeatedly comments/checks without advancing code.

Mitigation:
- a run must produce one of: code/test/docs change, concrete CI repair, review resolution, merge/release action, or explicit blocked evidence;
- repeated no-progress checkpoints become stale and reclaimable.

### G-018 Waiting CI freezes useful work — S3
Agent has nothing to do while two-hour CI runs.

Mitigation:
- agent may only do non-overlapping work that cannot invalidate the running evidence;
- avoid pushing unrelated commits to the same PR because that restarts CI;
- other workstreams remain independent.

### G-019 Failed CI owner disappears — S4
Mitigation:
- implementation owner gets first right to fix while lease active;
- after stale threshold watchdog can reclaim;
- infra/flaky failure may be rerun without taking code ownership.

### G-020 Merge train breakage — S4
Two individually green PRs conflict semantically after first merge.

Mitigation:
- update branch/rebase to current main before final merge for high-risk shared contracts;
- post-merge main CI;
- revert/hotfix policy;
- queue shared-contract PRs serially.

### G-021 Docs conflict with code — S4
Documentation becomes aspirational while code behaves differently.

Mitigation:
- invariant/contract docs are versioned with code;
- PR changing behavior must update docs or explicitly state no doc change;
- CI checks required governance files and contract versions.

### G-022 Release pipeline blocks development — S4
Packaging/signing failures should not make unit PRs impossible.

Mitigation:
- release validation is separate from PR-fast;
- unsigned packaging smoke can run on PR;
- signing only in protected release context.

### G-023 Massive AI models in repository — S5
Git history/repo cloning becomes unusable.

Mitigation:
- models fetched by manifests; Git stores hashes/metadata only;
- small deterministic fixtures only;
- large golden media stored externally or via controlled artifact mechanism.

### G-024 Secrets exfiltration via PR CI — S5
Mitigation:
- fork/untrusted PRs never receive release/auth secrets;
- live-site credentials absent from normal CI;
- release jobs use protected environments and least privilege.

### G-025 Watchdog becomes single point of failure — S4
Mitigation:
- repository state is sufficient for any authorized agent to resume;
- watchdog owns no exclusive hidden state;
- lease expiry is time/state based, not dependent on a specific chat.

## Required chaos tests for engineering flow

1. Stop an agent after opening a Draft PR but before first commit.
2. Stop after code push while CI is queued.
3. Stop while CI is running.
4. Stop after CI failure.
5. Push a new head while old CI is green.
6. Open two claims for one Issue simultaneously.
7. Open two different Issues that overlap a conflict-domain file.
8. Make a required CI job hang until timeout.
9. Exhaust self-hosted runner capacity.
10. Fail a live-site smoke while PR-fast is green.
11. Introduce a dependency cycle.
12. Introduce incompatible contract schema change.
13. Make main fail after merging two individually green PRs.
14. Lose the watchdog process entirely and prove another agent can reconstruct state.

A stable release requires product chaos tests and engineering-flow chaos tests. The project is not autonomous if recovery depends on one particular chat, workstation, runner, or agent.
