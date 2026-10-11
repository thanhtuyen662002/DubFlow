DUBFLOW_PR_V1

Issue: #170
Lease-Owner: /root
Lease-Heartbeat: 2026-10-01T00:00:00Z
Tested-Base-SHA: 8a5ffcab15d4bed56f293596c903a0366b6a21ed
Conflict-Domains: production-speaker-diarization,multivoice-tts,visual-identity
Expected-Paths: contracts/speakers/**,contracts/characters/**,engine/dubflow/diarization/**,engine/dubflow/tts/casting/**,engine/dubflow/tts/duration/**,engine/dubflow/visual_identity/**,engine/dubflow/active_speaker/**,engine/dubflow/worker/**,tests/diarization/**,tests/tts_multivoice/**,tests/character_speaker/**,tests/integration/production_local_file/**

## Outcome

Promote speaker-aware dubbing and conservative character association to production-safe optional capabilities. The implementation will derive stable audio clusters from real app-owned PCM media, cast cues to approved local Vietnamese voices, fit duration within bounded limits, and retain the audio-cluster fallback whenever visual evidence is weak.

## Scope and evidence

- production CPU audio feature/diarization boundary with stable within-job cluster IDs, overlap/offscreen/narrator states and provenance;
- deterministic multi-voice casting and bounded duration fitting routed through the real B2 TTS adapter;
- conservative visual tracking/active-speaker association with explicit identity-switch and confidence downgrade evidence;
- production integration fixtures use real WAV/frames and preserve B1/B2 artifacts on advanced-stage failure.

This Draft PR claims Issue #170 from the exact current main SHA. It remains additive and optional; canonical timeline and CapCut format are unchanged.