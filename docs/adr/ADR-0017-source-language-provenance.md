# ADR-0017: Resolved source language and pinned production translation

- Status: Proposed, Issue #196 / Draft PR #197.
- Scope: transcription/translation metadata and private checkpoint compatibility.
- Public timeline/worker/SQLite contracts: unchanged.

## Context and decision

The production worker discards Whisper language evidence and treats `auto` as
English. The merged translation resolver already rejects unresolved language;
the worker must use that boundary before selecting a real local package.

Persist concrete source language, requested/detected authority and available
language probability alongside transcript cues. Preserve all cue IDs and integer
time points. Explicit Chinese variants remain declared variants; a generic `zh`
package and the actual `zh -> en -> vi` route are recorded separately. The
official package index contains no direct Chinese-to-Vietnamese package.

Use app-owned, version/hash-pinned Argos packages and an explicit route behind
the translation adapter. Verify package language/version and installed data
integrity before inference. An unavailable or incompatible route is a typed
per-item failure, never a silent English fallback. Vietnamese identity export
is an explicit route. Publication requires separate license/quality evidence.

The selected archives contain legacy Stanza tokenizer checkpoints incompatible
with the current SDK. Its default sentence initialization may download newer
resources/models into the package tree. The adapter therefore injects the
bounded `cue-punctuation-v1` sentence boundary implementation into each Argos
PackageTranslation. It uses the unchanged pinned SentencePiece/model data, CPU
int8 and beam 4. No auxiliary tokenizer model is loaded or downloaded. Reject
sentences over 512 input tokens rather than silently truncating them. Record
this recipe and exact CTranslate2/SentencePiece component versions in route
identity. This is a qualified adapter recipe, not a claim of upstream default
Argos behavior or release translation quality.

## Compatibility and migration plan

Transcript/translation private artifacts gain version-2 language and route
metadata. Existing public cue/timeline identity remains unchanged. A legacy
checkpoint may supply cues with an explicit requested language; an automatic
request without recorded evidence must recompute detection or fail with an
actionable unresolved-language error. Cached text is not language evidence.

Translation reuse requires the current input/language/model identity. A changed
route or transcript invalidates derived subtitles/render checkpoints, even if
their file hashes remain valid. Retain source media and previous validated
output until replacement generation succeeds. Existing jobs retain their
recorded engine/model versions; this introduces no SQLite migration or updater
exception. The supervisor remains the durable job-state writer.

Private per-cue translation checkpoints include input and output hashes. A
native failure leaves completed chunks reusable under the same language,
recipe, runtime and package identities; changed identity selects new chunks.
Malformed/tampered chunks are recomputed. No worker writes supervisor SQLite.

## Qualification boundaries

Deterministic tests protect language fences, compatibility and identity; they
cannot establish Chinese speech/translation quality or installed Windows
behavior. Required exact-head/current-main CI, no-sidecar real media, long-form,
batch/recovery, package license and clean-machine evidence remain mandatory.
The leaf remains Draft while any acceptance or required lane is incomplete.
