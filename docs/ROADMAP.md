# DubFlow Foundation Roadmap

## Current phase
Foundation is ready to be claimed through leaf Issues. Main CI is green.

**Live snapshot, observed 2026-11-04 at Main `bc801585` (#463).** Re-check GitHub before acting on it; GitHub Issues remain the live work graph.
- Main is protected by the active ruleset `protect-main-dubflow`: PR-only, no deletion or force-push, strict required check `governance`, 0 required approvals, no bypass actors. Issue #11 is completed. PR Fast and PR Integration also run on every PR, but only `governance` is ruleset-required.
- Full deterministic Integration registry: 59/59 components passed with no base and no filter on `713bd23` (run 41780693790, #250). Main push Integration is selective and can select zero components; it is not full-registry evidence.
- Windows-only suites run on their own read-only PR lanes: `release-tests-windows` (#416), `documentary-windows` (#464) and `desktop-editor-windows` (#417).
- Latest published stable release: v1.1.49 (2026-11-03), built from an earlier Main.
- Still open: #175 (final production qualification, not accepted) and #291 (one continuous 2–6 h real recording on a current-Main build; earlier 1.1.6 evidence needed a manual state reset after a shutdown, so it is not accepted). Also open: #323 (NVIDIA GPU; only the real-hardware run remains) and #382 (16:9 reframe; see below).
- #439 accepted one Chinese→Vietnamese desktop review/revision leaf only; it does not accept #175.
- Residual risk, owner decision: accepted ADR-0008 describes supervisor-owned project DB/artifact/temp locking. #384 was closed with a narrower per-work-folder lease (`work_lease.rs`), so reframe/package/shorts still spawn their Python workers from the desktop host. This is a scope gap against the ADR, not a confirmed data-loss incident; #382's acceptance text still names the stronger supervisor-owned API.

Capability tiers, promotion gates, and fallback semantics are defined in [`docs/CAPABILITY_LADDER.md`](CAPABILITY_LADDER.md). This roadmap keeps the delivery gates and dependency order; GitHub Issues remain the authoritative live work graph.

## Foundation Epic
- #2 — Foundation: resumable one-click vertical slice. Tracking only; do not claim directly.

## Immediate parallel lanes
The following may begin concurrently **only inside the narrow ownership map in `docs/PARALLEL_EXECUTION.md`**:

- #3 — Canonical media timeline contract.
- #4 — Supervisor ↔ Python worker protocol.
- #6 — Desktop shell and queue UX against mocks.
- #7 — Source adapter contract and deterministic downloader fixtures.
- #8 — Media fixture and golden benchmark harness.
- #9 — Model/runtime/license manifest and one-click eligibility rules.
- #10 — Stable fast and selective integration CI lanes.
- #17 — Artifact DAG provenance and selective invalidation.
- #18 — Project ownership locks / multi-instance recovery.
- #19 — Untrusted input/path/archive/media security boundary.
- #20 — Fair batch resource scheduler and rolling disk guard.
- #21 — Model/runtime retention graph and safe GC.
- #22 — QC provenance and confidence calibration.

These Issues are not all allowed to edit their broad roots at once. Their namespace ownership is explicitly split in `docs/PARALLEL_EXECUTION.md`.

## Durable job state
- #5 — Durable job state and crash-resume skeleton.

#5 has a **soft integration dependency** on #4. It may implement against the fake/mock worker boundary and reconcile before final integration.

## First hard integration gate
- #14 — First resumable local-file passthrough vertical slice.

Hard dependencies:
1. #3 canonical timeline accepted/merged;
2. #4 worker protocol accepted/merged;
3. #5 durable state/recovery accepted/merged.

#6 is not a hard dependency for #14; backend proof may use a minimal harness. This prevents desktop UX from becoming a backend critical-path blocker.

## Resilience lane
- #15 — GitHub lease and stuck-CI watchdog.
- #24 — Parallel path ownership / hidden contention elimination.

The watchdog is never a universal required check or product dependency.

## Administrative control
- #11 — Protect main with PR + required status checks.

Completed. The ruleset `protect-main-dubflow` (id 24152712) is active, PR-only and strict, with required check `governance` and no bypass actors (observed 2026-11-04).

## Hard dependency graph

```text
#3 ─┐
#4 ─┼──> #14 first local-file slice
#5 ─┘

#5  soft-consumes #4 mock before final integration
#17 integrates later with #3/#5
#18 integrates later with #5
#19 integrates later with #7/#9
#20 integrates later with #5
#21 integrates later with #9/#5
#22 integrates later with #8/#17
#15 aligns with #10 conventions
```

There is no hard dependency cycle.

## Hidden contention graph

The dangerous graph is path ownership, not only Issue dependency.

High-contention roots are split:

```text
contracts/
  timeline/   -> #3
  worker/     -> #4
  source/     -> #7
  artifacts/  -> #17
  qc/         -> #22

crates/job-supervisor/
  protocol/      -> #4
  state/         -> #5
  invalidation/  -> #17
  locking/       -> #18
  scheduler/     -> #20
  resources/     -> #20
  model_refs/    -> #21

.github/workflows/
  core CI lanes  -> #10
  watchdog       -> #15
```

Shared root exports, lockfiles, migrations and required check names are short serialized coordination points.

## Merge ordering rule
Two green PRs sharing a conflict domain are not independently merge-safe forever.

For shared-domain PRs:
1. merge the owner/earlier contract PR;
2. update the second PR to current main;
3. rerun current-head/merge-compatibility checks;
4. merge only after fresh green evidence.

## Rules for future decomposition
- Epics are never implementation leases.
- Add hard dependency edges only when mocks/interfaces cannot preserve progress.
- Narrow namespaces before declaring tasks independent.
- Every critical-path Issue must include recovery/decomposition.
- A stale worker blocks only its leaf/owned namespace.
- Live Douyin/Bilibili availability, GPU runners, CapCut direct draft, release signing and watchdog availability never become universal product/PR dependencies.


## Staged user-value delivery

The first useful localization result is intentionally split from dubbing so TTS/audio work cannot hold Vietsub hostage.

### #56 — Slice B1 Vietsub-only
Hard dependencies:
- #14 foundation local-file/resume;
- #39 baseline ASR;
- #40 baseline local translation;
- #42 subtitle compositor;
- #44 export/render in subtitle-only mode.

Produces a usable MP4 + SRT/ASS/editable pack while preserving original audio. It does not wait for TTS, audio mixing, speaker intelligence, OCR cleanup, live-source adapters, GPU or CapCut direct draft.

### #45 — Slice B baseline dubbing
Hard dependencies after this decomposition:
- #56 proven Vietsub path;
- #41 baseline single-voice TTS;
- #43 baseline source-audio ducking/dub mix.

If TTS/mix fails, the job degrades to the already-valid #56 Vietsub output rather than failing the whole localization result.

The capability ladder treats #56 as the first useful localization slice and #45 as its optional dubbing upgrade. Speaker clustering, arbitrary-orientation subtitle intelligence, visual identity, source separation, inpainting, CapCut direct drafts, source acquisition and installer/update work remain additive lanes with their own merge, promotion and release evidence.

This staged path is a progress invariant: future work must not re-collapse #56 and #45 into one all-or-nothing mega-gate without an ADR explaining why.


## Gate-driven claim order

Autonomous workers should derive claim priority from machine-readable Issue dependencies rather than treating this roadmap as a static queue.

Current intended delivery order:
1. prove the foundation integration gate;
2. unlock the Vietsub-only first user-value gate;
3. upgrade the proven path to baseline dubbing;
4. add source acquisition, installer/update, advanced subtitle/speaker quality and CapCut capabilities in parallel according to their own gates;
5. qualify release with soak/chaos evidence.

For each gate, workers should claim only READY root hard-dependencies not already protected by a valid Draft PR lease. Soft dependencies are compatibility relationships, not reasons to wait.

This keeps roadmap text descriptive while GitHub Issues remain the authoritative live dependency graph.
