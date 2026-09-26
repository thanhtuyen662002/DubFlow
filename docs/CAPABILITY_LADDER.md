# DubFlow Capability Ladder & Post-Foundation Delivery Strategy

## 1. Why this document exists

DubFlow contains several research-grade problems:

- subtitle detection/classification/removal in arbitrary layouts;
- ASR/OCR/platform-caption reconciliation;
- speaker diarization;
- audio ↔ visual character association;
- multi-character Vietnamese TTS;
- dialogue/music/effects separation;
- video inpainting;
- Douyin/Bilibili extraction under website change;
- CapCut draft compatibility;
- one-click packaging across diverse local hardware.

If the project defines success as "all of those must be near-perfect before anything is usable", development can remain in research indefinitely.

DubFlow therefore uses a **capability ladder**:

1. every subsystem has a minimum usable baseline;
2. advanced capability is additive;
3. each upgrade is benchmarked independently;
4. failure of an advanced tier falls back to a lower usable tier;
5. merging research code does not automatically promote it to product default;
6. only measured promotion changes the default capability profile.

The final product target remains one-click high-quality localization. The ladder is a delivery strategy, not a reduction of that target.

---

# 2. Three different gates

## 2.1. Merge gate

A merge gate answers:

> Is this change technically safe to add to the codebase?

Examples:

- deterministic unit/contract tests pass;
- no schema incompatibility;
- no invalid migration;
- no crash/resume regression;
- no path traversal;
- exact-current-head CI is green.

A prototype can pass a merge gate even if its AI quality is not yet good enough to become default.

## 2.2. Promotion gate

A promotion gate answers:

> Is this capability good enough to replace the current default for its supported profile?

Promotion requires:

- golden-set benchmark;
- latency/resource evidence;
- failure/fallback evidence;
- packaging/runtime eligibility;
- quality regression comparison;
- declared supported content/hardware scope.

Promotion is a separate PR/config/default change.

## 2.3. Release gate

A release gate answers:

> Is this complete product profile safe and usable for an end user?

Release gates include:

- clean-machine install;
- first-run bootstrap;
- representative end-to-end videos;
- crash/reboot resume;
- batch behavior;
- output QC;
- updater/rollback;
- no mandatory manual developer setup.

This separation prevents a difficult quality experiment from freezing ordinary engineering.

---

# 3. Product capability profiles

DubFlow must expose product behavior as a capability profile rather than a pile of unrelated feature flags.

Example internal profile:

```yaml
profile: auto_localize_vi
source:
  tier: best_available
transcript:
  tier: best_available
subtitle:
  tier: best_available
speaker:
  tier: best_available
dubbing:
  tier: best_available
audio_cleanup:
  tier: best_available
visual_cleanup:
  tier: best_available
editable_export:
  tier: standard
fallback_policy: preserve_usable_output
```

The supervisor resolves `best_available` based on:

- installed components;
- hardware;
- model compatibility;
- confidence;
- content type;
- benchmark-approved default;
- runtime failure.

A job records the exact resolved capabilities used.

---

# 4. Minimum viable fallback rule

For every optional/advanced capability:

```text
advanced attempt
→ validate output
→ if valid, continue
→ if confidence/health below policy, use lower tier
→ still finish a standard usable export when possible
→ record downgrade in QC
```

Downgrade is not silent. The user sees a simple message such as:

> Video completed. 3 scenes used the safe subtitle-cover fallback because automatic removal was uncertain.

Quick Mode still completes automatically.

---

# 5. Source acquisition ladder

## SRC-0 — Local media

Input:
- local video file;
- local folder.

Requirements:
- no network;
- dedup/content identity;
- durable queue.

This is the first baseline and must work even if every website extractor breaks.

## SRC-1 — Generic URL adapter

- yt-dlp/generic adapter behind SourceAdapter;
- one URL;
- deterministic error taxonomy.

Fallback:
- user can supply downloaded local file.

## SRC-2 — Bilibili supported adapter

- video metadata;
- selected stream;
- available subtitles;
- authentication only when required;
- stable provider identity.

## SRC-3 — Douyin supported adapter

- video URL;
- provider identity;
- cookie/session bridge where needed;
- site-change classification.

## SRC-4 — Playlist/channel enumeration

- durable cursors;
- partial resume;
- dedup;
- hundreds/thousands of source items;
- poisoned item isolation.

## SRC-5 — Source health automation

- scheduled live smoke;
- source regression issue;
- adapter hotfix path.

Important: SRC-3/4/5 never become universal development CI dependencies.

---

# 6. Transcript and subtitle ladder

## TXT-0 — Audio transcript baseline

- VAD;
- ASR;
- timestamps;
- normalized transcript.

This gives DubFlow a semantic source even without visible subtitle understanding.

## TXT-1 — Existing/platform caption ingestion

- embedded subtitle;
- downloaded platform caption;
- canonical time mapping;
- TranscriptCandidateSet arbitration.

Fallback:
- ASR.

## TXT-2 — Basic burned-in text detection

- common horizontal subtitle regions;
- OCR polygons;
- temporal grouping;
- debug overlay.

This tier is useful for collecting/evaluating real video evidence but is not allowed to indiscriminately translate/remove all OCR text.

## TXT-3 — Text-role intelligence

Classifies:

- dialogue subtitle;
- narrator caption;
- watermark;
- username;
- signage;
- title;
- danmaku;
- UI;
- decorative;
- unknown.

Uses:

- ASR alignment;
- temporal behavior;
- geometry;
- semantics;
- region history.

This is the minimum tier before automatic burned-in subtitle removal becomes broadly enabled.

## TXT-4 — Arbitrary orientation and animated subtitles

- vertical;
- diagonal;
- perspective;
- karaoke/evolving text;
- moving subtitle tracks;
- multiple simultaneous text systems.

## TXT-5 — High-confidence selective visual cleanup

- removal masks derived from classified tracks;
- temporal stabilization;
- post-cleanup verification;
- inpaint or adaptive cover.

Fallback chain:

```text
temporal inpaint
→ adaptive cover
→ keep source text + place Vietnamese safely elsewhere
```

No removal failure may destroy the standard export.

---

# 7. Speaker/character ladder

## SPK-0 — One dubbing voice

All spoken dialogue can use one selected/default Vietnamese voice.

This is not the final UX, but it means translation/dubbing can be tested end-to-end before diarization research is complete.

## SPK-1 — Audio speaker clusters

- diarization;
- overlapping segment representation;
- stable voice cluster IDs within a job.

TTS can assign one Vietnamese voice per audio cluster.

Fallback:
- uncertain segments use narrator/default voice instead of guessing a character.

## SPK-2 — Stable voice casting

- cluster → TTS voice profile;
- voice consistency across the video;
- age/energy/style hints where evidence supports them.

## SPK-3 — Visual character identity

- shot detection;
- face/character tracks;
- cross-shot identity;
- animation-capable tracker boundary.

No requirement yet that the system knows who is speaking.

## SPK-4 — Audio ↔ visual active-speaker association

- mouth/activity evidence;
- voice/visual co-occurrence;
- continuity;
- narrator/off-screen identity;
- global association graph.

Fallback:
- keep SPK-1 audio cluster mapping where visual evidence is weak.

## SPK-5 — Cross-video/project character registry

- recurring character identity;
- persistent TTS voice;
- user corrections/constraints;
- scoped linking to avoid identity leakage.

SPK-4 quality must not block SPK-1 usable multi-voice dubbing.

---

# 8. Translation ladder

## TR-0 — Segment translation

- local translation backend;
- source transcript;
- Vietnamese output;
- deterministic segment IDs.

## TR-1 — Context-window translation

Adds:
- previous/next dialogue;
- speaker IDs;
- scene context.

## TR-2 — Glossary and identity-aware translation

Adds:
- names;
- places;
- terminology;
- do-not-translate;
- preferred wording.

## TR-3 — Duration-aware rewrite

The translation engine can produce a shorter natural alternative when the target speech slot is constrained.

Fallback:
- split subtitle;
- safe TTS-rate adjustment;
- use nearby silence.

TR-3 is important for polished dubbing but should not stop TR-0/1 integration.

---

# 9. Dubbing/TTS ladder

## DUB-0 — Valid single-voice synthesis

- local TTS;
- decode-valid waveform;
- target text coverage;
- normalized sample rate.

## DUB-1 — Multi-voice per speaker cluster

Consumes SPK-1.

## DUB-2 — Duration fitting

- regenerate/rewrite;
- safe rate range;
- segment timing validation;
- light stretch only as last resort.

## DUB-3 — Source-style prosody

- energy;
- pause;
- speaking rate;
- supported emotion/style hints.

## DUB-4 — Voice/reference cloning mode

Optional engine capability only when packaging/license/security rules allow it.

It must never become the only way the product can dub a video.

Fallback:
- stock/local approved voice.

---

# 10. Audio cleanup ladder

## AUD-0 — Preserve source audio + overlay dub

Lowest-risk path:
- keep original mix;
- duck it during Vietnamese speech;
- add TTS clearly.

This can leave audible original dialogue, so it is baseline/debug quality, but it keeps music/effects intact.

## AUD-1 — Speech attenuation

Use speech estimate/center/spectral strategy to reduce original dialogue conservatively.

Fallback:
- AUD-0.

## AUD-2 — Speech/music/effects separation

- dialogue stem;
- background/M&E;
- artifact validation.

Fallback:
- less aggressive attenuation rather than destroying background audio.

## AUD-3 — Local background repair

Repair separation holes/artifacts around dialogue removal.

No separation model can be a universal correctness dependency.

---

# 11. Subtitle visual cleanup ladder

## VIS-0 — Keep original text

Place Vietnamese subtitle in a safe readable area.

## VIS-1 — Adaptive cover

Cover only classified dialogue subtitle regions with stable boxes/gradients/background treatment.

## VIS-2 — Image inpaint per stable scene/text track

Requires temporal mask consistency.

## VIS-3 — Temporal video inpaint

Higher quality and higher compute.

Fallback chain always remains VIS-3 → VIS-2 → VIS-1 → VIS-0.

---

# 12. Render/QC ladder

## RND-0 — Standard compatible export

- H.264/AAC MP4;
- source aspect ratio;
- validated duration;
- software encoder fallback.

## RND-1 — Hardware-aware encode

NVENC/QSV/AMF/other supported acceleration after runtime test.

## RND-2 — Advanced profiles

HEVC/AV1/HDR-preserving paths where explicitly supported.

QC is mandatory at every tier; codec sophistication is not.

---

# 13. Editable export ladder

## EDT-0 — Standard editable pack

Always available:

- clean/processed video;
- dub mix;
- speaker stems where available;
- SRT;
- ASS;
- timeline JSON;
- character/voice mapping;
- QC report.

## EDT-1 — CapCut import-friendly pack

- paths/layout optimized for manual import;
- SRT/assets ready.

## EDT-2 — Tested direct CapCut project

Only for explicitly tested CapCut versions.

Fallback:
- EDT-1/EDT-0.

Direct CapCut draft is never a release blocker for standard export.

---

# 14. Packaging/update ladder

## PKG-0 — Developer reproducible environment

For engineering only.

## PKG-1 — App-owned runtime package

User does not install Python/FFmpeg manually.

## PKG-2 — Hardware/model first-run resolver

- CPU/GPU detection;
- model pack selection;
- resumable downloads;
- hash validation.

## PKG-3 — Signed staged update + rollback

App/engine/model compatibility and DB migration safety.

## PKG-4 — Self-healing update/source hotfix channel

Still signed/versioned/rollbackable; never arbitrary remote pip install.

A user-facing release cannot be called one-click before PKG-1/2 are proven on a clean machine.

---

# 15. User-visible release slices

## Slice A — Foundation proof

Goal:
- local file;
- durable job;
- canonical timeline;
- fake analysis;
- passthrough render;
- hard-kill resume.

Primary Issue:
- #14 after #3/#4/#5.

This slice proves architecture, not localization quality.

## Slice B — Baseline Vietnamese localization

User can:

```text
select local video
→ ASR
→ local translate
→ single Vietnamese TTS voice
→ Vietnamese subtitle
→ source ducking
→ final MP4
```

Minimum capabilities:

- SRC-0
- TXT-0
- TR-0/1
- DUB-0
- AUD-0
- VIS-0
- RND-0
- EDT-0

No OCR text-role, character association, inpainting, live websites or CapCut direct project is required.

This is the first genuinely useful product slice.

## Slice C — Multi-speaker localization

Adds:

- SPK-1/2
- DUB-1/2
- context translation;
- per-speaker tracks.

Still no visual character association required.

## Slice D — Smart burned-in subtitle localization

Adds:

- TXT-2/3;
- VIS-1;
- transcript arbitration;
- safe automatic handling of source subtitles.

## Slice E — Character-aware dubbing

Adds:

- SPK-3/4;
- narrator/offscreen handling;
- audiovisual association.

Fallback remains speaker-cluster voices.

## Slice F — High-quality cleanup

Adds:

- TXT-4/5;
- AUD-2/3;
- VIS-2/3;
- stronger semantic QC.

## Slice G — One-click source acquisition

Adds:

- SRC-2/3/4;
- whole-channel durable queue.

Local-file functionality remains independent.

## Slice H — One-click distribution

Adds:

- PKG-1/2/3;
- clean Windows validation;
- updater rollback;
- diagnostics.

## Slice I — Advanced editor bridge

Adds:

- EDT-2 tested direct CapCut drafts.

EDT-0/1 remain permanent fallbacks.

---

# 16. Research timeboxing rule

AI research tasks are bounded by a decision checkpoint, not by a promise of perfection.

Each research/prototype Issue must define:

```text
baseline
candidate(s)
dataset
metric(s)
resource budget
supported content scope
decision checkpoint
fallback if not promoted
```

At the decision checkpoint the result is one of:

1. **PROMOTE** — candidate becomes approved default for a declared profile;
2. **KEEP EXPERIMENTAL** — code may remain available but not default;
3. **REJECT** — remove/disable candidate and preserve fallback;
4. **SPLIT** — evidence shows different defaults are needed for different content/hardware profiles.

There is no state “keep researching forever because quality is not perfect.”

---

# 17. Benchmark promotion policy

A model/algorithm default change requires comparison against the current default on the same benchmark revision.

Promotion evidence includes:

- quality metric;
- failure rate;
- latency;
- RAM/VRAM peak;
- model/runtime size;
- CPU fallback behavior;
- long-form behavior;
- packaging eligibility;
- known regression categories.

A candidate may be better on average but still not replace the default if it catastrophically fails an important content class.

Profiles can choose different defaults:

```text
live_action_gpu
animation_gpu
cpu_low_memory
text_heavy
long_form
```

---

# 18. Quality budget vs compute budget

Quick Mode must optimize for **usable completion**, not maximum model size.

The planner has a hardware/profile budget:

- VRAM;
- RAM;
- expected latency;
- free disk;
- video duration;
- active queue load.

If a quality tier exceeds the safe resource budget:

```text
choose lower model/tier
→ keep same semantic contract
→ record downgrade
```

The app must not repeatedly OOM while insisting on the highest tier.

---

# 19. Capability health contract

Each adapter/engine exposes:

```text
available
version
supported_inputs
hardware_requirements
healthcheck
quality_profile
fallback
```

A capability can be:

- AVAILABLE
- DEGRADED
- UNAVAILABLE
- EXPERIMENTAL
- BLOCKED_BY_RESOURCE
- BLOCKED_BY_AUTH
- INCOMPATIBLE_VERSION

The supervisor chooses only approved combinations.

---

# 20. No-false-success rule

A lower-tier fallback is allowed only if the output remains truthful.

Examples:

Allowed:
- inpaint uncertain → adaptive cover;
- AV speaker uncertain → diarization voice cluster;
- hardware encode fails → software encode;
- CapCut version unknown → editable import pack.

Not allowed:
- OCR uncertain → delete arbitrary text anyway;
- speaker uncertain → confidently label visible listener as speaker;
- model missing → silently use incompatible model and claim same provenance;
- QC stale → report PASS;
- source download partial → mark channel complete.

---

# 21. Post-foundation Epic graph

Epics are tracking-only.

## EPIC A — Baseline Vietnamese Localization

Outcome:
local file → ASR → translate → subtitle → single-voice TTS → source duck → render → QC.

This is the first useful product path.

## EPIC B — Source Acquisition

Outcome:
reliable local/generic/Bilibili/Douyin/channel ingestion behind SourceAdapter.

## EPIC C — Subtitle Intelligence

Outcome:
burned-in subtitle detection, text-role understanding, arbitrary orientation, safe removal.

## EPIC D — Speaker & Character Intelligence

Outcome:
audio speakers → stable voices → visual identities → active speaker association → project registry.

## EPIC E — Translation & Dubbing

Outcome:
context/glossary/duration-aware Vietnamese translation and multi-voice TTS.

## EPIC F — Audio & Visual Cleanup

Outcome:
safe speech attenuation/separation and source-subtitle cleanup with fallback.

## EPIC G — Render, QC & Editable Export

Outcome:
validated final video + standard editable pack + targeted regeneration.

## EPIC H — Local Runtime, Installer & Updates

Outcome:
clean-machine one-click install, model resolver, signed update/rollback.

## EPIC I — CapCut Bridge

Outcome:
standard import pack always available, direct draft only for tested versions.

## EPIC J — Release Hardening

Outcome:
batch/long-form/chaos/security/soak evidence for user release.

No Epic may be claimed directly.

---

# 22. What is allowed to block what

## Can block the first useful local localization slice

Only hard correctness dependencies such as:

- durable job execution;
- canonical timing;
- valid worker protocol;
- baseline ASR;
- baseline local translation;
- baseline TTS;
- valid render/QC.

## Cannot block the first useful local localization slice

- Douyin live access;
- whole-channel enumeration;
- OCR arbitrary-orientation perfection;
- active-speaker visual association;
- cross-video character registry;
- high-quality separation;
- video inpainting;
- direct CapCut draft;
- GPU;
- release signing;
- watchdog.

## Can block promotion of a specific advanced tier

Its own benchmark/quality evidence.

This isolates risk.

---

# 23. Architecture rule for experimental engines

Experimental engines live behind the same stable contract as approved engines.

They may not:

- add special-case state directly to UI;
- bypass artifact provenance;
- bypass model manifest;
- write SQLite directly;
- invent their own time representation;
- change final output semantics silently.

If an experiment needs a contract change, that is an architecture task/ADR before promotion.

---

# 24. Failure containment matrix

| Failure | Required fallback |
|---|---|
| Douyin unavailable | local file / other source adapters still work |
| Bilibili unavailable | local/generic/Douyin paths unaffected |
| ASR large model OOM | lower-memory ASR / CPU profile |
| Platform subtitle missing | ASR/OCR arbitration |
| OCR role uncertain | do not remove; safe subtitle placement |
| Active speaker uncertain | audio speaker cluster / narrator voice |
| TTS chosen engine fails | approved fallback TTS voice/engine |
| Separation artifacts | conservative attenuation / source duck |
| Inpaint artifacts | adaptive cover / keep original |
| HW encoder fails | software encoder |
| CapCut schema unknown | standard editable pack |
| Model update fails | rollback/pinned old version |
| Live source test fails | operational issue, unrelated PRs continue |
| GPU runner unavailable | benchmark delayed, product CI continues |

---

# 25. Delivery success metrics

Project velocity must not be measured only by merged PR count.

Track:

- time from READY Issue to first Draft PR;
- time waiting for CI vs active coding;
- stale lease count;
- conflict/rebase rate;
- percentage of PRs touching shared roots;
- critical-path age;
- baseline end-to-end success rate;
- fallback frequency by capability;
- benchmark promotion rate;
- regression rate after promotion;
- install success on clean machines;
- job resume success;
- batch completion rate.

A rising shared-root conflict rate means the architecture/governance needs decomposition, not simply more agents.

---

# 26. Definition of a product-ready capability

A capability is product-ready only when all are true:

1. versioned internal contract;
2. deterministic functional tests;
3. representative benchmark;
4. known failure modes;
5. fallback path;
6. resource envelope;
7. packaging/license eligibility;
8. provenance recorded;
9. resume behavior defined;
10. diagnostics/QC visibility;
11. upgrade/rollback behavior defined;
12. does not create a new single point of failure.

---

# 27. Final rule

DubFlow should continuously move along two tracks:

```text
PRODUCT TRACK:
  always preserve the best currently usable one-click path

QUALITY TRACK:
  independently improve subtitle/speaker/TTS/audio/inpaint/source quality
  and promote only after evidence
```

The quality track may fail experiments.

The product track must keep moving.
