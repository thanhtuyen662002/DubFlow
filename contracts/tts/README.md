# Local TTS contract boundary

Issue #41 owns the versioned DUB-0 local single-voice Vietnamese synthesis
boundary. The contract preserves translated segment identity and canonical
integer tick slots while making waveform/sample-rate/duration validation
explicit.

`TtsEngine` implementations are replaceable adapters. The deterministic
fixture engine uses only the Python standard library and emits a small,
non-silent PCM WAV for PR/Integration evidence. Approved stock voices and
production model runtimes remain behind the same interface; no model weights,
network access, GPU, or system executable are required by the contract.

Every accepted segment carries producer/model/config/voice/input provenance.
The document records canonical slot and actual end points, exact PCM sample
metadata, RMS/peak/clipping metrics, and whether a fallback engine produced an
artifact. Failures are structured and bounded, low confidence is retained as
data, and one failed segment does not erase successful segments. A checkpoint
is reusable only when its content/config/model/voice/engine hashes and the
validated artifact descriptor still match.
