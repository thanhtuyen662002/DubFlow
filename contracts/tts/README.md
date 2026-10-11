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

## Explicit render windows (document 2)

Document 2 keeps canonical `slot_start`/`slot_end` and adds the required
`render_window_end` TimePoint to every artifact. `actual_end` is measured PCM
placement, bounded by that explicit limit; no overrun tolerance permits speech
to cross it. The limit is at least the original source end. TimePoint and the
canonical timeline retain schema1. Source timestamps/IDs are never rewritten
to describe a longer dubbing waveform. Mixers use `actual_end` for document2.

New readers accept both strict versions; old readers refuse2. No-window callers
continue to emit unchanged document1. Private checkpoint2 and a source-bound
placement1 receipt carry the versioned production allocation described in
ADR-0025. Existing jobs keep their original compatible runtime and artifacts;
rollback does not convert or relabel prior speech. A derived ASR inter-cue gap
does not prove silence or human film quality.
