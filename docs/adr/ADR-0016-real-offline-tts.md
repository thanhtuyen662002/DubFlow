# ADR-0016: Real offline Vietnamese speech candidate

- Status: Proposed; Draft PR #195, Issue #166. No release qualification.
- Date: 2026-10-07
- Scope: B2 TTS adapter, model provisioning and provenance. No durable schema change.

## One condition-changing EOS recovery — producer 3.2.0, 2026-10-10

Actual installed766 NgocHuyen speech refused17 film/vlogger intervals at its
reviewed frame cap. A private CPU diagnostic reproduced all17 with seed20261007.
The preselected changed seed20261008 reached EOS for9 intervals (4 distinct
texts, including6 repetitions of the same translation). This is mechanical
recovery evidence. Back-ASR differs on7 of those9; Sintel `Ngồi yên.` still
does not reach EOS. Neither seed nor this voice is human quality-approved.

New generations retain seed20261007 first. Only missing EOS permits one fresh
SDK decode with recipe-pinned `eos_retry_seed:20261008`, `eos_retries:1`.
Text, phonemes, preset, model, sampling parameters, phoneme/token limits and
the existing maximum300 plus SDK phoneme-based frame cap are unchanged. SDK
`babble_retries` remains0. Runtime errors, unsupported input and invalid
complete PCM do not trigger this recovery. Two missing-EOS results refuse
the cue and preserve the source-audio fallback; neither incomplete waveform
is cached or published. A successful primary result is never regenerated.

Successful reseeding returns optional private `warnings:[TTS_EOS_RESEEDED]`.
The bridge accepts only this bounded known warning; malformed/unreviewed
warnings terminate the child. Natural PCM and this warning stay together in
the one-text cache for all tempo passes. Existing TTS artifact warnings retain
this evidence through the existing document/checkpoint format. Reaching EOS
does not prove spoken-text coverage or film acting. Worker/adapter `attempt:1`
counts one synthesis request; the recipe and warning separately record the
at-most-two internal decodes. It does not consume or invent job retry history.

### Compatibility, versioning and qualification

Producer becomes3.2.0 and the manifest inference dictionary changes. Model
bytes, preset IDs/versions and licensed inventory are unchanged. Manifest and
adapter/bridge digests already separate private B2/checkpoint generations;
old audio cannot satisfy the changed recipe. No public TTS/worker/status
schema, SQLite, canonical timeline or artifact-format migration is required.
Old private readers ignore the optional warning; missing warnings retain
legacy behavior. Shipping still requires a coherent app/runtime/manifest.

Existing job IDs retain their immutable owned runtime/start binding and old
WAVs, exports and receipts. Use a fresh execution for3.2.0; never replay a766
job under the new runtime or relabel its metadata. An old manifest is rejected
by the new loader's exact recipe check. Rollback uses the retained coherent
old runtime/database; no historic artifact rewrite or database migration.
New-candidate native qualification requires3.2.0, explicitly refusing a3.1.0
receipt as proof of the changed producer.

Deterministic regressions cover the finite changed-seed bound, successful
primary preservation, incomplete/invalid/runtime refusal, cache-warning
retention and malformed private warning replies. Opt-in real CPU regressions
exercise observed reseeding, measured tempo cache and following-cue survival.
All4 exact new HEAD/current-base lanes and native candidate checks remain
required. Duration rewrite/intelligibility, ASR names/confidence, native GUI,
film acting, real long-form/batch and full166/175 acceptance stay open.

## Installer progress observer compatibility — 2026-10-09

Actual local setup840 failed with Windows error5 when a PowerShell diagnostic
reader held the progress JSON without DELETE sharing. Source unpacking and
inventory validation had completed; no version pointer was activated. This is
concurrent observer evidence, not an unattended-install failure claim.

Installer progress remains schema1 and advisory. The copy loop rehashes every
existing staged file, independently of the last completed-path snapshot, and
verifies the complete final tree before activation. Ordinary Windows permission
errors5/32/33 on progress snapshot replacement/cleanup are tolerated; other
errors and authoritative current/install-state/launcher writes remain fatal.
There is no lock retry loop. The next progress attempt describes new copy work,
after64 files or one second; initial/final/failure snapshots are forced. This
bounds whole-set serialization/fsync overhead on the measured35,782-file runtime.

If an observer holds a snapshot through activation, it can remain stale until
the next installation/self-recovery cleans it. Consumers must use the verified
current pointer and install state as activation authority. Existing interrupted
schema1 snapshots remain readable; staged artifact reuse still requires exact
hashes. No durable job/schema/model/timeline migration is introduced. Real
Windows deny-delete handle tests cover advisory contention and retained pointer
failure. Full new-candidate installation/GUI and #166 acceptance remain required.

## Private native cue rejection compatibility — 3.1.0 history, 2026-10-09

A reviewed VieNeu context/frame bound can refuse one cue while its native model
remains healthy. The former bridge treated every `ok:false` as process failure,
closed the child, and caused later independent cues to fail `TTS_NATIVE_EXITED`.
The private v1 reply now optionally includes `scope:cue` and one of two bounded
codes: `TTS_TEXT_UNSUPPORTED` or `TTS_SPEECH_INCOMPLETE`. Only the reviewed typed
exception for phoneme/context bounds or missing EOS emits this marker. It never
publishes the incomplete waveform. The pinned SDK creates fresh decode caches
and repetition history for each text; the next cue can use the same model safely.

The bridge accepts that marker only after initialization, for the exact sequence,
with a known string code and a non-empty bounded string condition. It raises a
nonretryable cue error, keeping the healthy child for the next changed input.
There is no hidden regeneration, frame-cap relaxation or changed voice/model.
Initialization, crashes, timeout, malformed/unknown replies, invalid PCM,
runtime/FFmpeg errors and all untyped failures still close the contained child.
The existing adapter records the failed cue and source-audio fallback.

This is an additive private bridge field, not a change to the supervisor/worker
public protocol, canonical timeline, SQLite schema or published TTS document.
An old bridge conservatively closes a child on the new refusal; a new bridge
conservatively closes on an old untyped refusal. A job pins its owned runtime;
adapter/entrypoint source digests already create a separate B2 generation when
this recipe changes. Old jobs, WAVs and exports remain immutable and unrelabelled.
No public migration or existing-artifact rewrite is needed. Production
qualification requires actual speech after a refusal and fresh native checks;
fixture subprocess continuity alone does not satisfy #166/#175 quality gates.

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

## Visible completion after speech degradation

The desktop already presents the supervisor's `status.message`. The supervisor
now distinguishes a complete dub, partial speech/source fallback and a B1
Vietsub/original-audio export using the existing QC audio metadata. It reads at
most 1 MiB plus one byte from the latest committed QC artifact for the exact
job/stage/attempt/path. SHA-256 covers the same snapshot that is parsed, and
must match the supervisor-owned artifact record and size. A missing, modified,
oversized, incompatible or invalid report yields an explicit unverified-quality
message, while retaining the committed job's successful export state.

Both completion events and final durable reconciliation derive that summary.
An already completed job replay therefore retains its downgrade without
regenerating speech or mutating outputs. Generic warnings remain visible and
are not relabeled as missing speech. A full dub message requires valid zero
TTS/mix failure counts; source-only output requested with dubbing receives the
explicit B1 fallback message. No private model diagnostic is shown in that
product message.

Public worker/status/artifact schemas, SQLite, TTS/mix producer versions and
timeline identity are unchanged. `reason` and `message` already permit strings;
older readers continue showing the message, and older QC can conservatively
remain unverified. The supervisor directly reuses the existing locked
`sha2 = 0.10.8` to hash bounded parsed bytes; no dependency version is upgraded.
ADR-0020 runtime fingerprints still prevent rebinding historic executable job
IDs to a changed supervisor. Previous installed runtimes and exports retain
their original bytes; no data migration rewrites past completion evidence.

Native staged and installed qualification now additionally sends one genuinely
unsupported numeric cue through the pinned phonemizer followed by a normal
Vietnamese line. It requires one nonretryable refusal, actual following speech,
no ducking of the failed cue, verified playable/editable outputs and an honest
partial-dub status. Replaying that completed job must retain every output hash
and mtime and the same limitation. A separate all-refused job must export B1
with original audio and a visible fallback. Authored Vietnamese sidecars and
generated source media qualify these failure paths; they do not establish real
ASR/translation, native GUI or human film-dialogue quality. All required lanes
and the complete #166/#175 acceptance remain required before promotion.

The partial-dub native case also preserves a 180x320 portrait source. A separate
source with no audio stream and an explicitly authored VI sidecar must retain
its playable video and subtitles with `AUDIO_STREAM_MISSING` B1 fallback. The
completion message uses verified source-probe audio availability and never
claims to preserve nonexistent original audio. No synthetic speech/audio is
inserted to hide this limitation. The qualification helper permits absent AAC
only for this explicitly declared no-audio case; all existing dubbed/audio
cases retain the AAC gate. Standard worker media/QC behavior is unchanged.

## Local-file status reconstruction and coherent pipeline progress

An installed real nine-minute no-sidecar run reproduced 440 completed units
over a stale 272-cue denominator during translation. Its completed replay
preserved all 789 output files but reset durable checkpoint `qc` to null and
actual start count 1 to 0. These are status defects, not media regeneration.

The supervisor now reads the existing stage columns through a bounded read-only
state accessor. Reconciliation precedes its initial projection; completion and
failure preserve the latest durable checkpoint, actual starts, maximum attempts
and retry condition. A scheduled replacement does not count before it starts.
No previous status file or worker-owned SQLite writes establish this history.
If a later read fails, observed history is retained rather than erased.

Overall worker fractions project onto paired 1000-unit progress. Stage cue counts
remain in the raw event. Running progress caps at 999, including rounding near
one; completed progress requires validated durable success. The current one-shot
projected event/heartbeat sequence restarts per invocation and is not recoverable
cumulative history; synthetic durable reconciliation does not advance it.

Status schema 1, SQLite migration 2, worker protocol, artifact/model versions and
producer recipes are unchanged. Old readers accept the existing fields. Older
supervisors retain their original status behavior; rollback must use their
coherent retained runtime/database as in ADR-0020. Immutable producer pins forbid
replaying an existing ID under this changed supervisor. Historic outputs and
their failure receipts are not rewritten. No migration is needed for the
read-only projection repair.

Reopen/progress/failure regressions and native staged/installed completed-replay
guards must prove checkpoint/actual-start/progress equivalence alongside all
output hashes and mtimes. Only the invocation counter may differ. Prior f3 CI
and real-video receipts remain historical for that source; required exact new
HEAD/current-base lanes and real-media evidence must rerun. These repairs do not
qualify film acting, bounded semantic rewrite or the full #166/#175 release.

## Per-job execution ownership before restart recovery

An isolated installed f3 real-media concurrency test found that starting a
corrupt neighboring job recovered the healthy live job's SQL stage. A second
process then ran the same job ID concurrently, reaching actual duplicate
worker starts and `STATE_WRITE_FAILED`. Process startup alone cannot establish
that all running rows belong to a dead process.

Each one-shot or IPC start now acquires an exclusive OS file lock keyed by the
canonical database path and job ID, before admission or durable mutation. The
file is an empty retained handle under `.execution-locks` beside the database;
its name is a SHA-256 digest, never raw user path text. The file is never
unlinked or cloned. Handle closure/process death releases authority, so a
hard-killed supervisor can restart without time-based stale-lock reclamation.
The one-shot holds it through final status publication. IPC retains it through
worker execution and failure/panic handling. A server without the owning
in-process worker also requires this lock before cancellation.

Live duplicates return existing nonretryable `JOB_ALREADY_RUNNING` without
changing the original job, status or artifacts. Long-lived server startup performs no
global recovery; `ready.recovered_stages` remains zero. After immutable
admission, recovery touches only the locked requested job, preserves checkpoint
and attempt history, and never revives a cancelled job. The legacy global state
recovery API is retained for callers with exclusive whole-store ownership; the
production per-job supervisor no longer calls it.

The supervisor MSRV becomes Rust 1.89 for stable `File::try_lock`. No crate,
lockfile, SQLite migration, worker/status schema, model or timeline format
changes. Previous runtimes have no lock interoperability guarantee: immutable
ADR-0020 producer pins continue refusing cross-version job replay. Rollback
uses the retained coherent runtime/database, never relabels existing job IDs
or rewrites outputs. Empty lock files are operational coordination, not durable
project/artifact authority or evidence of live ownership.

Actual Rust tests must exercise two processes and hard-kill release. Native
staged/installed qualification must overlap a healthy pinned-TTS job, a corrupt
neighbor and a duplicate, including idle-server startup. It checks actual SQL
running state and start count, typed refusal, committed speech, playable export
and durable completion. Generated media/authored VI cues exercise execution
isolation; they do not qualify real ASR/translation, GUI, film acting or the
full #166/#175 gates. All new source/base required lanes must rerun.

## Windows status reader contention

The installed f2 NgocHuyen real-media run reached 272 actual ASR cues, then
status publication failed with Windows error 5. Controlled actual Win32 and
installed-supervisor tests reproduce this for a live reader of an ordinary
target, including FILE_SHARE_DELETE and Python readers. Releasing the handle
allows replacement without deleting the target. The producer previously
retried only error 32, so the observed reader condition bypassed that bound.

The existing eight-attempt/15 ms filesystem wait now also permits error 5 only
when the destination remains an ordinary writable file. Readonly, invalid,
unavailable and persistent errors remain failures. Atomic replacement and
previous bytes are preserved; there is no delete gap, permission change or
worker retry. Required Windows Rust tests exercise reader release, a persistent
reader and readonly refusal. Older f2 receipts remain historical, including
the failed real run; no completed voice/export approval is inferred.

Native qualification uses the existing flag-only server CLI. Its erroneous
literal `serve` argument caused 16cb5cf Windows smoke failure before the server
could exercise execution exclusion. Correcting this harness is not evidence
that the previously unexecuted path passed. Required exact successor/head/base
lanes and staged/installed actual process overlap must rerun. Public schemas,
SQLite, models and artifact formats remain unchanged; immutable job/runtime
pins and coherent rollback apply to this changed supervisor as above.

## Native browser data and immutable release inventory

Actual e50 installation completed, but normal desktop startup created
`app/bin/DubFlow.exe.WebView2/EBWebView` caches under the immutable release.
The strict installed inventory correctly rejected these unmanifested files
before real-film qualification. The older profile and failed receipt remain
evidence; startup success alone did not establish a trusted runtime.

Before Tauri initializes windows or threads, the Windows host now sets the
documented `WEBVIEW2_USER_DATA_FOLDER` override to
`LOCALAPPDATA/DubFlow/control/webview2/<hex-release-version>`. Absolute writable
roots are required; the canonical browser profile must be outside the runtime.
Hexadecimal UTF-8 version bytes preserve case-sensitive version identity on
Windows. Each version retains its own disposable browser data. This directory
is neither canonical project storage nor supervisor-owned durable job state.

Windows release qualification must observe actual WebView2 files there and
run the unchanged full manifest verifier with the installed owned interpreter
after normal startup. It records inventory, source and interpreter bindings;
this is initialization evidence, not interactive GUI or film-acting approval.
The exact installed executable owns the process check and shutdown.

No public schema, migration, model, dependency, job producer or artifact format
changes. Existing jobs keep their original runtime pins. No old profile is
copied, rewritten or deleted. Rollback keeps the coherent retained runtime and
its corresponding profile; older runtimes retain their original startup
behavior. No cache whitelist or trust waiver is permitted. All required
successor HEAD/current-base lanes and actual installed qualification must rerun.

WebView2 override semantics and writable user-data placement:
https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/webview2-idl
and https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder.

## Preserve stereo in the B2 source bed

The retained installed e96 actual Sintel trailer run generated two real TrucLy
cues and passed its existing QC, but decoded the stereo source to mono and
exported mono editable PCM and AAC. Current c055 had the same explicit mono
decode request. Those historical receipts do not establish stereo preservation.

B2 now decodes the source bed as stereo and records `stereo-source`. Mono
sources are upmixed; mono TTS remains centered by the existing streaming mixer.
No-audio jobs retain their generated stereo silence. Source and dialogue assets
remain separate. A distinct opposite-phase L/R regression exercises actual PCM
mixing with fixture decoding/TTS. Packaged qualification must require two
channels in every editable mix WAV and the final AAC stream, in addition to
its existing hashes, producer pins and decode checks. Fixture tests do not
approve film acting, semantic accuracy or actual installed release behavior.

No public schema, durable migration, model, dependency or artifact format changes.
The existing private B2 generation hashes this worker source, separating the
changed decode policy from previous generations. Existing jobs retain their
original producer/runtime; rollback uses that coherent retained release.
All successor HEAD/current-base required lanes and actual packaged stereo
qualification must rerun. Full #166/#175 acceptance remains required.
