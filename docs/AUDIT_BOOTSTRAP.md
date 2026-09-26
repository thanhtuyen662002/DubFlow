# Architecture & Delivery Audit — Bootstrap

Date: 2026-09-26

This audit deliberately attacks both the product design and the development process. Findings are classified as **conflict**, **ambiguity**, **single point of failure**, or **missing gate**.

## Resolved/high-priority findings

### A-001 Local-first vs unspecified translation backend
The product promise says local-first, but the original design did not explicitly guarantee a local translation path.

**Resolution:** Translation is an adapter. A local-capable backend is the baseline path; cloud providers are optional and disabled unless the user explicitly configures them. No core job may require a cloud API to reach a standard usable export.

### A-002 Canonical time represented by decimal seconds in examples
Several examples use `12.520` seconds. If implemented literally across long VFR media, repeated float conversion can drift.

**Resolution:** canonical identity is integer ticks plus source time-base/rational mapping. Seconds/frame indexes are presentation fields only. Proxy timelines must store reversible mappings.

### A-003 "Unlimited duration" vs disk-heavy editable artifacts
Long videos can produce stems, proxies, OCR caches, inpaint caches and debug media many times larger than the source.

**Resolution:** no software hard limit, but bounded-memory/chunked processing, preflight storage estimate, rolling disk guard, artifact retention classes and resumable GC are mandatory.

### A-004 One-click install vs model/license/credential requirements
Some candidate models may require account tokens, click-through licenses or redistribution restrictions. Bundling them blindly would violate the zero-manual-setup promise or make packaging impossible.

**Resolution:** every model pack carries provenance, license, redistribution status, download source, required credential/acceptance and fallback. Default one-click profile may only depend on redistributable or automatically downloadable components compatible with the intended distribution. Candidate model names are not architecture commitments.

### A-005 One-click install vs GPU dependency
The original hardware tiers could be misread as "8 GB NVIDIA required".

**Resolution:** hardware capability is detected dynamically. GPU is an accelerator, not a correctness dependency. CPU/alternate backend fallback must preserve a usable pipeline, even if slower or lower-quality.

### A-006 Downloader hot updates vs supply-chain security
Rapid extractor updates are useful when sites change, but arbitrary downloader self-update can become remote-code execution.

**Resolution:** source adapters/extractor bundles are versioned, signed/checksummed, rollbackable and capability-scoped. No unsigned remote Python package install on user machines.

### A-007 Candidate libraries vs packaging stability
WhisperX, pyannote, TTS/inpaint/audio candidates can change APIs, licenses or model-access rules.

**Resolution:** all are behind internal contracts. CI tests adapters against fixtures. Default engine selection comes from benchmark + packaging/license evidence, not documentation claims.

### A-008 FFmpeg "bundle everything" ambiguity
Codec builds differ by platform/licensing and hardware support.

**Resolution:** packaging must pin an approved FFmpeg build profile and record codec/license manifest. Hardware codec availability is probed; no specific hardware encoder is mandatory.

### A-009 Speaker intelligence can become an impossible global blocker
Perfect character assignment is not always observable (offscreen speech, animation, crowd, occlusion).

**Resolution:** confidence-based best effort + narrator/offscreen/unknown identity is valid output. Low confidence cannot block final export by default.

### A-010 Smart inpaint as default can destroy frames
Subtitle removal quality can be worse than leaving/covering text.

**Resolution:** inpaint risk scoring and adaptive-cover fallback are part of normal flow, not exceptional recovery.

### A-011 CapCut direct-project output can become a release blocker
Internal draft formats can change independently.

**Resolution:** CapCut is an adapter. Standard editable assets are release-critical; direct project generation is compatibility-tested but cannot block the standard export.

## Engineering-flow findings

### E-001 Repository main is currently unprotected
At bootstrap inspection, `main` had `protected=false` and no repository rulesets.

**Risk:** S5. Any authorized agent could bypass CI/PR policy.

**Action:** establish stable required check names first, then enable a branch/ruleset requiring PR + exact-head checks. Until then AGENTS.md forbids direct main pushes.

### E-002 No CI existed
A stalled or broken PR would have no deterministic evidence.

**Action:** bootstrap `PR Fast / governance` now. Later extend stable lanes rather than continuously renaming required checks.

### E-003 No Issue execution contract existed
Agents could claim broad epics, duplicate work or silently stop.

**Action:** leaf Issue template + Draft-PR lease + heartbeat + stale reclaim rules.

### E-004 A single foundation PR could stop every downstream stream
The original milestone order was largely sequential.

**Action:** minimize the true critical path to durable job engine + canonical timeline + worker/contracts. Then use mock/adapter contracts so desktop, downloader, AI experiments and packaging can proceed in parallel.

### E-005 Long AI/live-site CI could destroy throughput
GPU benchmarks, live Douyin/Bilibili smoke and long-video soak are slow/flaky.

**Action:** five CI lanes. Only deterministic fast/selective integration checks are universal PR gates. Quality-critical changes explicitly opt into benchmark gates.

### E-006 CI itself can hang
A workflow without timeout can occupy a runner indefinitely.

**Action:** every job has a timeout; superseded same-PR runs cancel; self-hosted GPU capacity is isolated.

### E-007 Green CI can be stale
A new push after green checks invalidates old evidence.

**Action:** merge protocol requires exact HEAD SHA and exact-head checks.

### E-008 Shared files create hidden serialization
Timeline schemas, worker protocol, migrations, compatibility manifests and lockfiles can conflict even when Issues look unrelated.

**Action:** declare conflict domains and expected paths in every executable Issue and Draft PR.

### E-009 Watchdog can become another single point of failure
If watchdog state exists only in one chat, losing that chat loses orchestration.

**Action:** watchdog owns no hidden state. Any agent reconstructs leases, dependency state and CI from GitHub.

## Remaining decisions to validate by prototype

1. Canonical tick/time-base schema and proxy mapping.
2. Worker protocol framing/backpressure/cancellation semantics.
3. SQLite single-writer durability under hard-kill/reboot.
4. Subtitle-role fusion benchmark.
5. Audio↔visual speaker association benchmark for live action and animation.
6. Vietnamese TTS engine benchmark and packaging/license choice.
7. Speech/music/effects separation benchmark.
8. Subtitle inpainting fallback threshold.
9. CapCut version compatibility strategy.
10. Windows runtime/model pack size and first-run bootstrap experience.

No coding task may treat these unresolved candidates as already-proven facts.
