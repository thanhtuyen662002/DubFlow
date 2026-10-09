# ADR-0016: Real offline Vietnamese speech candidate

- Status: Proposed; Draft PR #195, Issue #166. No release qualification.
- Date: 2026-10-07
- Scope: B2 TTS adapter, model provisioning and provenance. No durable schema change.

## Context

The existing `BuiltinVietnameseTtsEngine` produces character-dependent sine
and noise signals. Its non-silent PCM and successful B2 smoke are not evidence
of Vietnamese speech. Issue #166 requires actual CPU dubbing, Chinese-language
translation, recovery, intelligibility and app-owned Windows runtime evidence.
This decision addresses the speech backend; the other acceptance criteria
remain open. The project continues to use B1 subtitle export as a usable fallback.

## Proposed decision

### Voice replacement after human feedback (2026-10-07)

The user rejected the VAIS1000 listening samples. New B2 jobs now select the
VieNeu v3 Turbo fp32 CPU ONNX candidate through a separate immutable
`production-vieneu-v1.json` manifest. Initial preset: Ngọc Huyền, subject to
the user's comparative listening preference. Preserve VAIS1000 as a historical
adapter/evidence profile; do not call the rejected voice quality-approved.

Producer becomes `3.0.0`, backend `vieneu-v3-turbo-onnx-v1`, native 48 kHz mono.
The SDK 3.8.3, sea-g2p 0.9.1, ORT 1.30.0, tokenizers 0.23.2 and NumPy 2.2.6
are pinned. Model/config/tokenizer/heads, codec and preset data each carry
exact size/hash and immutable upstream revisions. Provision into a separate
inventory-addressed app-owned root with existing resumable atomic downloads;
reject foreign, linked or tampered data before inference. No model Python
implementation is downloaded/executed. Retain upstream licensing evidence.

The private bridge accepts the selected fixed entrypoint/frontend/sample
rate and reuses bounded requests/replies, deadlines and OS process containment.
The entrypoint disables network access before SDK inference, forces CPU/fp32
and two threads, and disables hidden SDK retries. Preset enrollment/denoising
is unused. A generation that reaches its frame cap without EOS is rejected
instead of exporting cut speech. Natural PCM is cached only for the current
cue; one fit attempt changes FFmpeg `atempo` up to 1.3, preserving pitch.
Speech that still does not fit fails with a B1 downgrade; no audible samples
are trimmed. This is a conservative fit path; language-aware rewrite remains
part of broader acceptance.

The private B2 generation identity now includes the selected TTS recipe and
adapter/bridge source digests. Recipe/model/voice changes generate new audio
without mutating published private generations. Historical artifacts retain
their recorded producer and model identities. No public worker/artifact schema
or durable SQLite migration is introduced. Completed B2 reruns currently
regenerate/re-render; failed unpublished generations retain per-cue checkpoints.
This is not a claim of warm completed-B2 reuse or installed restart recovery.

Local back-ASR and actual Chinese-to-Vietnamese dubbed-video diagnostics are
recorded in `REAL_TTS_EVIDENCE.md`. Full human quality, long/batch/clean-machine,
all required CI lanes and runtime distribution obligations remain gates.

### Historical Mimic3 candidate

Implement a separate adapter for the Mimic3 `vi_VN/vais1000_low` VITS voice,
ONNX Runtime 1.30.0 and eSpeak from pinned Sherpa-ONNX 1.13.8. Keep native
libraries and model inference
behind the existing TTS adapter. Workers publish artifacts/events; the
supervisor retains ownership of durable job state.

Provision the model through the existing resumable downloader into the
supervisor-selected model root. Verify exact archive size/SHA-256 and the
digest of the complete extracted data inventory. Serialize provisioning
across processes. Reject archive traversal, links/junctions, special files,
portable-path violations, duplicate data and bounded-size violations. Stage
installation privately and replace only after validation; retain corrupt
previous data for diagnosis. Conversion scripts from the archive are omitted
and never executed. No weights or generated media enter ordinary Git history.

Use native 22,050 Hz mono PCM and source-derived integer time points. Generate
natural speech first. At most one additional generation changes the speaking
rate within the existing 1.3 ceiling. Reject speech that still cannot fit;
never trim audible samples to satisfy a cue. Pad short output with silence,
reject invalid/non-finite/silent output and preserve original/stem/final assets
through the existing AUD-0 mixer.

Pin producer version `2.1.0`, backend `mimic3-vits-onnx-v1`, runtime
`onnxruntime-1.30.0+espeak-sherpa-1.13.8`, model/tree hash, full voice manifest digest and inference
recipe in provenance. An archive integrity check, runtime health check or PCM
success is independent of speech-quality approval.

## Compatibility and migration

The current TTS/artifact schemas and canonical timeline contract are unchanged.
Historical jobs retain their recorded producer/model/contract versions and
artifacts; do not relabel historical tone output as neural speech. The legacy
voice loader remains available for compatibility tests. New B2 jobs select the
new profile; B1 remains independently usable. Existing voice data is not
overwritten or rewritten by a migration. A changed model or frontend recipe
requires a new recorded identity and selective regeneration, not silent reuse
of old TTS output.

The worker integration changes only the model-root argument at the B2 call.
No CapCut, review, OCR, speaker or hardware namespaces are claimed by this ADR.

## Qualification limits and alternatives

The selected voice's CC BY 4.0 weights permit attributed redistribution,
but the shipped native eSpeak-NG runtime has additional GPL/source obligations.
The license decision is documented separately and does not approve quality.
Models with noncommercial/research-only or unknown terms were not promoted.

The converted Piper frontend measured common-phrase mean back-ASR CER 0.3194.
It drops an original compound phoneme and uses different word encoding. The
new frontend reconstructs the upstream-hash-verified inventory, preserves
compound IPA symbols, applies original word/token blanks, retains clause
punctuation and removes native language-switch metadata. Its local diagnostic
measured 0.1193 common-phrase CER. Unsupported symbols remain explicit warnings.
This is not human listening approval or a stable publication decision. Original
per-clause pause/inference parity and unfamiliar words still need validation.

Native code now runs behind a private version-1 child protocol using the
app-owned interpreter with isolated imports. JSON requests/replies, stderr,
sample files and inference deadlines are bounded. Reply identity, waveform
digest/size and sample rate are verified before signed-16 conversion. Native
crash/timeout becomes a typed failure; it cannot terminate the job worker.
Normal teardown kills/waits for the child and verifies the private staging root
before cleanup. This changes no public worker protocol or durable schema.

Before sending initialization, the bridge assigns the child to a non-inherited
Windows Job Object with `KILL_ON_JOB_CLOSE`. Closing the bridge or abruptly
killing its worker closes the last job handle and terminates native descendants.
The actual interpreter verifies/joins that randomly named job before model
imports, covering a Windows venv redirector that may have spawned the interpreter
before the launcher was assigned. Its temporary query/assignment handle closes
immediately; only the worker retains an ownership handle. Parent and child must
ship together; the new containment field is private initialization metadata.
Containment failure is typed and aborts initialization so B1 fallback remains
available. On POSIX, an independent bounded stdin reader in the native entrypoint
treats EOF as lease loss and exits while the main thread is busy. Windows uses
the Job Object without a concurrent stdin reader: both buffered and raw pipe
reader prototypes reproduced a NumPy DLL initialization hang. The private
bridge keeps stdin open for its complete lifetime.
Subprocess tests use the actual entrypoint/containment with a deliberate busy
model fixture and a real descendant under hard
worker death on Windows, and EOF during both initialization and inference on
POSIX. These are development fault tests, separate from installed media recovery.

The waveform recipe and producer/model identities remain unchanged by this
process lifetime correction. Abrupt termination can leave the bounded private
temporary directory behind; supervisor storage reclamation and packaged
restart evidence remain qualification work. No worker SQLite mutation is added.

Still unresolved: packaged parent-death/restart and staging reclamation, B1 recovery,
a human listening set, Chinese no-sidecar dubbing, long-form/batch evidence,
exact-head CI/current base, clean-machine Windows and release/soak qualification.
PR #195 remains Draft; this ADR proposes no exception to those gates.

## Measured VieNeu duration fitting — producer 3.1.0

Actual acquired media exposed residual frame-count overshoot after FFmpeg
`atempo`, even when the natural waveform/rate calculation predicts a fit.
VieNeu now measures that output and may make at most two additional tempo
passes on the same cached natural waveform (three total tempo passes). Each
pass strictly increases the speed using measured frame count plus a 5 ms
margin. Both the requested configuration and the engine cap of 1.3 times
normal speech bound every pass. Natural output that already requires more
than that rate, or residual spoken samples after the final bounded pass,
remains `DURATION_FIT_REQUIRED`; preserve source/B1 instead of cutting speech.
The historic Mimic3 adapter keeps its original single-fit behavior.

This changes the VieNeu producer to `3.1.0` and the checksum-pinned profile's
duration recipe to `app-owned-ffmpeg-atempo-max1.3-measured3-pad5ms`. Model
weights, presets, public TTS/artifact schemas and source timeline are unchanged.
The new manifest/producer/source digests invalidate private B2 generation
reuse. Historic jobs and exports retain their recorded 3.0.0 recipe and runtime;
updated components must not be bound to those executable IDs. Create a new
execution or retain the compatible installed runtime as required by ADR-0020.
No migration relabels or overwrites historic audio. Required lanes must rerun
on the new complete source before readiness.

The development diagnostic fits all 24 residual-overshoot cues from the
9-minute real Chinese video without cutting samples, while 22 initially
over-rate cues still need fallback. That diagnostic precedes this production
implementation and is independent of listening/translation approval. Tests
must cover actual selected code, bounded/nonrepeating rates, source preservation
and the packaged producer identity; this is not a full-release exception.

## Accepted streaming mixer selected by B2

After #203 / PR #204 was accepted at main `404a429`, B2 explicitly selects
`FileSource`, `FileSegment` and `StreamingAudioMixer` instead of reading whole
source/TTS WAVs into Python frame objects. AUD-0 producer `2.0.1`, backend
`pcm-stream-duck-v1` and NumPy `2.2.6` follow ADR-0021. The selected adapter's
code digest and producer/backend/numeric recipe join the B2 generation identity,
alongside source/translation/TTS/voice pins. A recipe change creates separate
generations; historical outputs retain their recorded producer and bytes.
Immutable supervisor admission still requires the original installed runtime
for existing job IDs or an explicit new execution (ADR-0020).

The public timeline, TTS/mix documents, worker envelope and durable schema do
not change. All 25 preset choices and B1 preservation remain available. A mixer
recipe that cannot be verified is a typed B1 downgrade. Media adapter failures
retain their code/retryability at the worker boundary; an unexpected exception
requires action instead of authorizing unchanged-input retries. This fixes the
observed corrupt-container retry without changing the supervisor retry policy.

Applicable production Integration installs the same hash-pinned native NumPy
dependency as the mixer lane. Native qualification must prove real selected TTS
and streaming mix provenance plus actual source/stem/final WAV hashes, and a
corrupt source must report `MEDIA_PROBE_FAILED`, retryable false, attempt 1.
The #203 synthetic six-hour resource result is enabling evidence only; updated
#166 full-worker/native/media/recovery/batch and all declared CI still need fresh
qualification on this combined source/current main. No stable promotion follows
from selecting the adapter.

## Per-cue TTS recovery and bounded editable publication

Production now passes verified per-cue checkpoints into `LocalTtsAdapter` and
commits a worker-private JSON record immediately after each successful, fsynced
WAV. Records are limited to 64 KiB, atomically replaced, and bind exact input,
configuration, voice, producer/model/runtime provenance and adapter recipe.
Their metadata checksum and private generation paths are validated before the
adapter independently checks waveform hash, PCM metrics and canonical timing.
Fitted audio retains its original fit mode/rate when checked; padded and safely
speed-adjusted cues can be reused without silently rewriting completed speech.
An incomplete record reprocesses its cue; corruption is visible as a warning.
Failure to commit a checkpoint requires action and preserves the B1 path.

The recipe digest now includes the common TTS adapter and checkpoint store.
Public TTS/timeline/worker formats, model weights and VieNeu producer `3.1.0`
remain unchanged. The new private record format is version 1, with no SQLite
mutation or durable schema migration. Older private generations without these
records are never relabeled as resumed: changed generation identity selects a
new directory, and immutable existing job IDs still require their pinned
runtime under ADR-0020. Published generations and prior outputs stay immutable.

Editable source/stem/final WAVs are copied in 1 MiB blocks, with space admission,
source-size/change checks, expected mixer hashes, fsync and destination hash
verification before atomic replacement. Completed verified copies can be reused
after interruption. The enclosing recoverable export transaction protects the
preceding validated export. This removes the remaining whole-WAV allocation in
the production publication path; it is separate from mixer resource evidence.

Native qualification additionally requires each successful real TTS cue's
committed record and actual WAV hash, and byte parity between editable WAVs and
the mixer receipts. Deterministic tests cover abrupt process exit, fitted cue
reuse, corrupt/oversized/foreign records and copy/promotion/storage failures.
Real full-worker hard-kill recovery and large-file memory evidence still need
source-bound receipts; this decision alone does not qualify #166 or #175.

## Source video bounds dubbed mux duration

The two-hour actual-video-loop rehearsal completed 120 real Adam TTS cues and
streaming mixing, but B2 QC detected truncation and preserved B1. A minimal
actual FFmpeg reproduction shortened an 8.08-second source to 0.48 seconds when
an embedded subtitle ended at 0.5 seconds: output `-shortest` considered that
sparse stream as well as the full-duration video/audio.

Production passes an integer `MediaTimeline` containing the video stream's
source time-base and duration to the media render adapter, using the existing
canonical format-duration fallback only when the stream duration is absent.
The output limit is formatted with integer arithmetic, rounding upward to a
microsecond; it cannot depend on the last subtitle or dialogue slot. A render
with this explicit limit omits `-shortest`. Embedded sparse subtitles never
authorize `-shortest`, even for callers without a known duration. Existing
codec/audio QC and B1 preservation still apply.

The private render identity changes to `h264-aac-source-duration-v2` and pins
the exact source duration and media adapter digest. Previous runtime/job pins
and validated exports retain their original bytes; an older render checkpoint
is not relabeled with the new recipe. Public timelines, artifact schemas,
worker envelopes, models and durable schema do not change. This fixes source
video preservation, with no hardware-profile or speaker-identity changes.
Required native CI and long-media evidence must rerun against this source.
