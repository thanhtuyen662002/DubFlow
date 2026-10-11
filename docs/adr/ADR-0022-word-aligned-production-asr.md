# ADR-0022: Word-aligned production dialogue and raw ASR evidence

- Status: Proposed, Issue #166 / Draft PR #195.
- Scope: pinned production ASR recipe, private transcript schema 3, additive
  optional ASR provenance in the localization job manifest.
- Public cue/timeline shape, worker protocol, SQLite and model packs: unchanged.

## Evidence and decision

Actual retained Sintel audio with the owned faster-whisper 1.2.1 small CPU int8
model places `Good night, skis.` in a segment from 122.02 to 138.66 seconds.
Its actual word alignment is 137.88 to 138.62 seconds. Using segment bounds
starts Vietnamese speech about 15.86 seconds early. Every prior segment also
received a fabricated constant confidence 0.85.

The new recipe `faster-whisper-1.2.1-word-gap-v2` requests word timestamps and
does not inject a glossary or known reference names. Normalize SDK seconds
once to positions in the actual owned 16K mono PCM decode, rounding starts
down and ends up. Map those source sample integers through the canonical
`map_sample_interval` helper into the existing 1/1000 time base. Cue identity
includes source sample endpoints and the text digest, never segment ordinal.
Split gaps greater than 16000 samples. Preserve zero-length punctuation when
its group contains a positive speech interval. Neither timing repair nor
voice selection proves lip synchronization or correct recognition.

Missing/malformed word alignment keeps the complete original segment text
and valid source interval with explicit fallback/review metadata. Never drop
unaligned words or invent a 50ms speech interval. Low confidence remains data.
The cue score is the minimum available word probability only when every word
has a valid probability. Missing scores use zero with an **unavailable** basis;
an actual zero probability retains its distinct model-score basis. These are
uncalibrated model scores, not measured sentence correctness. Preserve raw
word probabilities, sample positions and segment log probability, no-speech
probability, compression ratio and temperature for review.

## Compatibility, invalidation and rollback

Private transcript schema 3 records recipe, decoded audio digest/frame count,
sample rate, pause threshold, calibration=false and per-cue evidence. Resume
validates evidence against cue text/identity/interval/score. Schema 1/2 and a
different recipe cannot establish current word alignment; the input recipe
hash changes and recomputes transcript descendants. Translation chunks retain
schema 2; their existing input identities include new cue identities, times,
text and score. Changed cues select new translation/TTS/mix generations.

The localization manifest schema 1 gains an optional `asr` provenance object
containing this schema-1 evidence. Cue and editable timeline fields are
unchanged. Existing manifests without `asr` remain readable; consumers must
not interpret their historical constant score as current model evidence.
Sidecar captions retain their declared authority and do not fabricate ASR
word evidence. No new model weights, downloads, dependency or migrations.

Installed jobs retain their original immutable producer/runtime binding,
which includes the engine tree. Use a fresh job ID for the successor producer;
never overwrite an old installed job's outputs with the new recipe. Existing
validated exports remain available until a replacement passes QC/publication.
Rollback selects the retained old runtime/producer and its original evidence;
do not relabel new transcripts as schema 2 or waive producer admission.

## Qualification limits

Deterministic boundary/checkpoint tests and replay of recorded actual model
words are distinct from a fresh actual model/pipeline run. Pinned CPU native
qualification, real media, calibrated human recognition/film acting, long
inputs, batch isolation and all #166/#175 release requirements remain open
until their own evidence exists. Old TTS fit counts do not establish correct
film timing. This ADR does not approve a default voice or a stable release.
