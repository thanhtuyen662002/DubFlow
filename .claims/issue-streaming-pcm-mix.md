DUBFLOW_PR_V1
Issue: #203
Lease-Owner: codex/01a11204/streaming-pcm-mix
Lease-Heartbeat: 2026-10-08T16:57:53Z
Tested-Base-SHA: 2f1fb764b78489020dec42ad670ed13ad3121737
Conflict-Domains: production-streaming-pcm-mix,audio-mix-runtime-pin,streaming-mix-ci-evidence
Expected-Paths: engine/dubflow/mix/streaming.py,engine/dubflow/mix/__init__.py,tests/audio_mix/test_streaming.py,tests/audio_mix/requirements-ci.txt,tests/audio_mix/qualify_streaming.py,scripts/ci/component_registry.json,packaging/runtime/requirements-windows-x64.txt,docs/adr/ADR-0021-streaming-pcm-mix.md,docs/production/STREAMING_MIX_EVIDENCE.md,.claims/issue-streaming-pcm-mix.md,.github/workflows/release.yml
Required-CI: PR Fast + Integration + Windows Release + Release / Soak
