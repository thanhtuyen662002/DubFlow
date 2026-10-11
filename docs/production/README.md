# Production baseline workstream

Issue #163 owns the transition from the fixture-only desktop preview to a
supported local-file production profile. The profile is complete only when a
clean Windows machine can install the app-owned runtime, select a real local
video, resume a durable job after interruption, and receive a validated
playable MP4 plus standard editable assets.

The production path keeps the existing versioned timeline, worker protocol,
durable-state, provenance, security and fallback contracts. Fixture backends
remain test-only; they must never be selected by the packaged application.

The first supported profile is CPU-capable local-file Vietnamese subtitle
localization (B1). It includes real media probing/rendering, a pinned local
model/runtime profile, resumable model bootstrap, durable supervisor state,
truthful QC, batch isolation and an editable output pack. This profile
preserves the original audio and emits Vietnamese SRT/ASS. Opt-in B2 is now a
real single-voice upgrade: the worker verifies the app-owned `vi-builtin-v1`
voice pack, synthesizes per-cue Vietnamese PCM, and runs the non-destructive
AUD-0 source duck/mix stage before H.264/AAC render. The final manifest and
editable directory retain the original source WAV, dialogue stem, final mix,
TTS document, mix document and hashes. A missing or unhealthy voice pack,
source audio, TTS segment or mixer records `B2_AUDIO_FALLBACK_TO_B1` and keeps
the valid B1 output.
Live website acquisition, direct CapCut drafts and advanced visual cleanup are
additive capabilities and cannot be represented as available until their own
evidence exists.
