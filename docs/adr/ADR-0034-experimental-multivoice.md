# ADR-0034: Opt-in experimental audio-speaker casting

Status: Proposed — owner-requested local engine implementation under #170.
This slice does not qualify visual character association or the full production
speaker capability. No desktop/supervisor wiring or release is included.

## Decision

New worker arguments `multi_voice` (default false) and `speaker_voices`
(default empty, at most S1–S8) opt new requests into audio-only CAM++ clustering.
The existing generic command args envelope and SQLite schema do not change.
Old requests retain their existing one-voice behavior. Admission from the
desktop and immutable supervisor bindings are a separate integration task.

The optional profile pins the unchanged Apache-2.0 ONNX export and notice by
size and SHA-256. Download it only on opt-in. Use the app-owned FFmpeg adapter
and source-video origin mapping to obtain mono 16 kHz signed-16 analysis PCM;
cue spans remain source-derived integer milliseconds. The analysis uses
Kaldi-style fbank, per-utterance mean normalization, normalized CAM++ embeddings
and deterministic average-linkage cosine similarity at 0.3. Long cues use
bounded 10-second feature windows. Model inference is CPU-only with two threads.

Cues below 800 ms borrow their nearest embedded cue. When none can be embedded,
retain one unknown cluster. Cap clusters at eight by merging the smallest by
speech duration into the most similar cluster; number by total speech time.
Median autocorrelation pitch suggests a casting preference, with an unknown
band. It is neither a calibrated gender determination nor a visual identity.
S1 receives the selected primary voice. Reserve valid overrides first, then
consume distinct approved catalog voices with a gender preference. Catalog order
and source order resolve ties. Explicit overrides may intentionally reuse voices.

Stage `speakers` writes private `speakers.json` containing labels and summaries.
Its checkpoint binds source hash, aligned audio recipe, cue IDs/spans, model
profile hash, adapter code hash and threshold. Cue embeddings/pitch have atomic
receipts under that identity so interruption resumes completed cue work. A
changed voice override recasts the stored analysis without downloading or
embedding again. Any optional-stage failure emits `MULTI_VOICE_SKIPPED` and
continues one-voice B2. A grouped TTS bootstrap/stage failure permits one retry
with casting removed in a separate generation; ordinary B2-to-B1 fallback remains.

## Artifacts and compatibility

Compute source dubbing windows across **all** cues before grouping by voice.
Each group uses the existing bounded TTS/rewrite/checkpoint/cache path, its own
voice provenance and `tts/<voice_id>/`. The synthesis cache omits the global
input digest but retains voice, recipe, text, source slot and render window, so
recasting one speaker preserves reusable lines. B2 generation identity includes
the casting and cue-to-voice maps. AUD-0 mixes the union of all group artifacts,
and QC aggregates all group failures and warnings.

Each voice retains a separate TTS document with consistent provenance. The
primary group uses `tts_document.json`; others use
`tts_document.<voice_id>.json`. If overrides assign every cue away from the
requested primary, the first used voice owns the base document and result's
primary voice. No empty or mixed-provenance TTS document is fabricated. Each
voice also has its own spoken-text receipt; additional documents are hashed in
the job manifest and additional spoken-text receipts are copied into editable.

Only opted-in successful analysis adds `speaker` to editable timeline cues and
a `speakers` list (id, gender heuristic, actual voice_id, cue IDs) to the job
manifest and QC. Source cue IDs, start/end ticks, transcript, translation and
subtitle semantics are unchanged. These are additive experimental fields in
the localization export, separate from `contracts/speakers` v1. Readers unaware
of these fields can retain their existing subtitle/audio behavior. Overlapping
speakers inside one cue are not resolved by this experimental per-cue model.

No migration of old artifacts is performed. `voice_by_cue=None` retains the
flat `tts/` layout and existing serialized documents. Existing job/runtime pins
must remain unchanged; enable this adapter only in a newly pinned runtime.
Downgrade keeps old exports and can regenerate a one-voice job with the option
disabled. New grouped receipts must not be relabeled as old one-voice evidence.

## Verification limits

Offline tests cover planning without numpy, synthetic-vector clustering,
short-cue borrowing, time ordering, cap, pitch signals, casting, resumability,
grouped TTS, cross-voice cache isolation, optional failures and exported mapping.
A separate model smoke requires `DUBFLOW_TEST_SPEAKER_MODEL`,
`DUBFLOW_TEST_FFMPEG` and `DUBFLOW_TEST_SPEAKER_AUDIO` with recorded speech. It
asserts execution only; synthetic tones are not diarization-quality evidence.
Human listening, real-speaker purity/coverage, long-form resource qualification
and installed Windows admission remain separate gates for #170/#175.
