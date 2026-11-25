# DubFlow Foundation Roadmap

## Current phase
The live Project State remains `production_qualified_cpu_b1`; first user-value gate #56 and baseline-dubbing gate #45 are unchanged. Main is green/protected. GitHub Issues and PRs remain the authoritative live work graph.

**Live snapshot, observed 2026-11-26 03:55 ICT at Main `f2e67098af3422f1865f3554de8029f2555b1914`.** Re-check GitHub before acting on this snapshot.
- Main ruleset `protect-main-dubflow` (#24152712) is ACTIVE, strict PR-only, no bypass actors; required context `governance`. Exact-Main checks: 26 total, 25 SUCCESS, 1 SKIPPED, 0 FAILURE. A skipped publish check is not test evidence.
- Current registry has 61 components. Main Integration run #44540904280 selected only `desktop-editor-subtitles` (1/61), with BASE_SHA `a37d2aee339a7a8ccb7288c1cc01d9c88de40903`; Worker skipped=6, TTS skipped=2, and `VOICE_MANIFEST_UNAVAILABLE`. There is no current-Main 61/61 full-registry evidence.
- Last known full-registry evidence remains 59/59 on source SHA `713bd2365db313f0f4ffb662e2190e5c6a5b2394` (run #41780693790 / #250 / PR #470). Later product merges mean this is historical, not current Main.
- Current-Main Windows source-session #45017439019: 65 tests, 12 skipped (pinned curl_cffi / yt-dlp runtime unavailable). Current-Main Release/Soak on the same run: 89 tests, 1 skipped (Windows Job lifetime). These skips are not passes and do not establish complete release qualification. The previous Release/Soak run/SHA remains historical in Project State.
- Live OPEN/DRAFT PRs are exactly #463, #973 and #975. #463 is this Lead-owned control-doc reconciliation; #973 is Claude's #439 evidence leaf; #975 is Codex's #489 security leaf. No PR is merged in this snapshot.
- #439 is OPEN/reopened for one current-Main Chinese-to-Vietnamese review/revision/output evidence leaf only. Historical PR #462 was accepted on old Main `99edc85d...`; it does not accept current Main or Epic #175. Current #439 lease is PR #973, still metadata-only: no Claude-authored commit/report update, real Windows Desktop/Tauri/media evidence NOT_RUN. Claude collaborator permission is `read`; execution endpoint and authorized Windows host session are unavailable to this Lead runtime.
- #489 is OPEN for MCP documentary project-creation confinement outside reserved `source-media`. Codex's sole current assignment is PR #975, currently a claim-only Draft (no product code). Source review identifies a potential source-store integrity/eviction risk, not a reproduced exploit or data-loss incident. Codex collaborator permission is `read`; Owner-provisioned Codex Cloud Environment / write-capable execution is unavailable.
- #818 remains OPEN but deferred/not active while Codex's single assignment is #489. Preserve its historical branch/closed PR; do not run #818 concurrently.
- #490 is CLOSED/COMPLETED. #884 remains OPEN as the peer-binding/public-egress follow-up and has no active lease.
- The historical `.claims/issue-490-generic-url-public-egress.md` file still exists on Main, but #490 is closed and no matching open Draft PR/lease exists; it is not a valid lease. That path is outside #463's allowlist and is left untouched for separate authorized cleanup.
- #175, #291 and #323 are CLOSED/NOT_PLANNED (not accepted as final production qualification); #382 is CLOSED/COMPLETED. Do not reopen these based on the 2026-11-04 snapshot.
- #416, #417 and #464 are CLOSED/COMPLETED; their old claim metadata is being marked released in PR #463. #487 is CLOSED/COMPLETED, but its claim file is absent on Main; do not recreate it. #250 is CLOSED/COMPLETED and its 59/59 result remains historical.
- #463 is OPEN/reopened for this bounded control-state reconciliation. Its previous lease was completed at PR #471 / merge `f76b602ea879cfc41712c1b0879ce821c8975d79`; the new Lead-owned PR #463 r2 is separate and touches only the allowed claim metadata plus Project State/Roadmap.
- Residual architecture note: ADR-0008 describes supervisor-owned project DB/artifact/temp locking. #384 delivered a narrower per-work-folder lease (`work_lease.rs`); reframe/package/shorts still spawn Python workers from the desktop host. This remains a scope gap against ADR-0008, not a confirmed data-loss incident.

Capability tiers, promotion gates, and fallback semantics are defined in [`docs/CAPABILITY_LADDER.md`](CAPABILITY_LADDER.md). This roadmap keeps delivery gates and dependency order; GitHub Issues remain the authoritative live work graph.

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
