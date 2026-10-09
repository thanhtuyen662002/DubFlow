# ADR-0021: File-based streaming PCM mixing and private recovery journals

## Status

Proposed — 2026-10-09. Leaf #203 / Draft PR #204; selected by #166 only after
this adapter is accepted and its own integration is freshly qualified.

## Evidence and decision

An actual 554-second / 272-cue B2 dubbing run reached 12,750,622,720 bytes peak
working set in the legacy mixer. Whole-track Python frame objects and several
simultaneous copies made its declared 512MiB resource profile ineffective.
Its 128MiB source limit also excluded six-hour PCM. Increasing that limit alone
does not address resource or restart requirements.

Keep the historical byte/fixture `LocalAudioMixer` unchanged. Add a separate
file-reference `StreamingAudioMixer` with producer `dubflow-aud-0/2.0.1`, backend
`pcm-stream-duck-v1`, and recipe runtime `owned-python/numpy-2.2.6`. Production
callers select it explicitly. Pin NumPy2.2.6 in the owned runtime, matching the
voice lane's existing pin. File hashes, ordered cue identities, confidence,
failure conditions, source ID/layout/ticks, DSP policy, block/resource limits,
input hash, producer and numeric runtime identify one immutable generation.

Integer source ticks/time-base map to samples; signed timeline positions remain
supported. Resampling uses the legacy nearest-lower sample rule and channel
mapping. Dialogue is clamped before its global normalization, then added to the
ducked source and normalized globally. Separate passes preserve whole-track
peak/RMS decisions without keeping whole tracks in memory. No boosting or
cutting spoken material is introduced. Missing/corrupt individual TTS files
produce cue failures and preserve source audio. All public documents remain
`audio_mix_document` schema-v1; no SQLite schema or worker protocol changes.

## Bounds and storage

Default blocks hold 262144 frames. At most two stage readers, two stage writers
and one short-lived TTS reader are active. Numeric buffer admission counts
twelve stereo int64 block buffers plus four times the maximum single TTS file
against the stage budget; pinned runtime/journal overhead is separately measured
in qualification. Default stage budget is 512MiB. Segment count is at most10000,
block count at most16384, journal/document bytes at most32MiB. Source capacity
is RIFF PCM16 mono/stereo up to2^32-1 total bytes, covering six-hour48K stereo
(4,147,200,044 bytes). Unsupported formats or oversized duck integer arithmetic
fail with typed conditions. Absolute regular files without traversal, links,
reparse points, hard links or named streams are required.

Private work uses an exact source snapshot, S16 raw dialogue, S32 raw combined
and two S16 WAV outputs. New-generation admission checks source size plus five
PCM payloads, WAV headers and a256MiB disk reserve. Individual writes also check
free disk. Six-hour stereo needs about25GB of additional workspace; sources,
models and other job artifacts require extra capacity. Private generations are
retained for caller-managed recovery/retention; this leaf adds no deletion policy.

## Recovery and publication

The caller serializes a mixer/generation. A private version1 JSON journal owns
four contiguous stages: snapshot copy, dialogue, combined, output. Each block's
bytes are flushed/fsynced before its length/hash is atomically checkpointed.
On restart, all committed prefixes are read and hash-verified. Only uncommitted
tails of owned private files may be truncated. Invalid identity, phase, shape,
headers, missing blocks or corrupted committed bytes fail without promotion.
Missing ownership journal cannot authorize reuse or overwriting existing data.

Copy preserves original WAV bytes including metadata. Artifact promotion can
resume after any rename. Source/TTS inputs are rehashed before final publication;
all final metrics/header/hash evidence is recomputed in bounded passes. The
four complete journal prefixes are verified again before promotion, including
the source snapshot, intermediate spools and output WAVs. Original artifact hash
must equal the pinned source hash. QC is measured on candidates before rename.
The schema-validated document is the completion sentinel and is atomically written
last. Existing complete documents are reused only after current identity and
actual artifact metadata/metrics/hashes match. New inputs/configuration produce
a distinct generation; previous valid outputs are not overwritten.

Journal hashes are integrity evidence, not authentication against an attacker
who can rewrite the journal and every recorded hash. The checkpoint callback
provides phase/count/hash to the supervisor; workers never mutate durable SQLite.
Future integration must anchor the relevant identity/evidence in supervisor
state and preserve immutable job admission. Runtime files are verified against
the complete release manifest before the installed qualification executes them.

## Compatibility and qualification

Historical producer1.0.0 artifacts/readers remain usable and are never relabeled
as2.0.1. The premerge2.0.0 candidate failed review: active private-file mutation
could be blessed by recomputed artifact metrics/hash without comparing committed
block hashes. Producer2.0.1 creates a distinct generation even for identical
input/DSP settings;2.0.0 caches/journals are never automatically promoted or
relabeled under the repaired producer. Existing candidate bytes are retained,
and readers can still inspect/export their schema-v1 documents. They cannot
serve as qualified2.0.1 evidence. No public migration is required. Private journal version1 is specific
to this backend; future incompatible layouts require a new recipe/version and
generation. Rolling back uses the older producer's existing artifacts; an older
mixer is not asked to consume this private journal. #166 must pin/select the new
producer in its pipeline identity and rerun downstream QC/export evidence.

Small native tests compare PCM bytes and metrics with legacy AUD-0 across layout,
rate, overlap, signed starts and block-boundary ramps. They exercise actual child
exit, I/O failure, mutation, corrupted checkpoints and completed-output corruption.
Fast and Integration execute them with hash-pinned NumPy. Release/Soak runs an
actual generated short PCM restart rehearsal. Windows Release executes the new
adapter through verified staged and installed owned Python. A separate actual
six-hour generated PCM run measures resource/frame/hash/QC/restart capacity.
Synthetic PCM cannot qualify real ASR/translation/model speech/GUI/batch/update
or the full #175 release gate. Keep Draft while any #203 acceptance is missing.
