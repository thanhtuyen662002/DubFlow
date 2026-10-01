DUBFLOW_PR_V1

Issue: #171
Lease-Owner: /root
Lease-Heartbeat: 2026-10-01T02:15:00Z
Tested-Base-SHA: 8a5ffcab15d4bed56f293596c903a0366b6a21ed
Conflict-Domains: production-audio-cleanup,gpu-profile,hardware-render
Expected-Paths: engine/dubflow/separation/**,engine/dubflow/mix/**,engine/dubflow/media/**,engine/dubflow/models/**,engine/dubflow/worker/**,models/manifests/**,packaging/runtime/**,tests/audio_cleanup/**,tests/production/media/**,tests/integration/production_local_file/**
Required-CI: PR Fast + Integration + Windows Release + Release / Soak

## Outcome

Promote conservative source-dialogue attenuation and hardware-aware execution to production profiles. CPU remains a complete fallback; stronger separation or GPU acceleration can never invalidate B1/B2 output.

## Scope

- app-owned PCM separation/attenuation boundary with AUD-0 fallback and independent hashes;
- hardware/VRAM/encoder resolver with deterministic CPU/software fallback and resource evidence;
- QC for loudness, clipping, duration, sync and downgrade provenance;
- worker integration after the B2 audio boundary is rebased to the exact main SHA.

This Draft PR claims Issue #171 from the exact current main SHA.