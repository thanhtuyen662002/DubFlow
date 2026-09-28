# Local ASR Contract

This directory owns the versioned local automatic-speech-recognition boundary
for Issue #39. The adapter emits `asr_transcript` schema version 1 documents.

ASR output uses the canonical timeline identity from Issue #3: every utterance
and word boundary is a signed integer tick paired with a reduced rational time
base. Decimal seconds, frame indexes and array positions are presentation or
implementation details; they are never durable transcript identity.

The contract is backend-neutral. A local model, a CPU fallback, and a small
deterministic fixture backend all produce the same validated transcript shape.
The `AsrBackend` receives a bounded `AudioChunk` carrying the source artifact
reference, optional sample rate, canonical decode window and non-overlapping
core interval; it never receives the whole long-form recording in memory.
Backends that report sample-relative offsets must call the explicit integer
`map_sample_interval` helper (Floor for starts and Ceil for ends) before
returning hypotheses, so negative/non-zero anchors and overflow remain typed
timeline errors.
Producer/model/config/input provenance is required so a later model change can
invalidate only the transcript descendants that depend on it. Low confidence
is retained as data. Structured backend failures carry a stable code,
retryability and the condition that changed before a retry.

Long inputs are processed as bounded chunks with explicit overlap. The adapter
merges duplicate boundary hypotheses by canonical word timing and text while
preserving overlapping utterances when they represent distinct hypotheses.
Chunks are independently checkpointable and a failed chunk does not erase
successful chunk results.

No model weights or network access are required by the deterministic CI
fixture. Production engines implement the adapter interface behind their own
runtime/model manifest and remain replaceable without changing this contract.
