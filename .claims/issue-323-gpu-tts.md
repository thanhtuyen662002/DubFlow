DUBFLOW_PR_V1
Issue: #323
Lease-Owner: Claude/issue-323-gpu-tts
Lease-Heartbeat: 2026-10-29T07:45:02Z
Tested-Base-SHA: 78026b7e768a87399a3b6255e9dff5aa8f802f5b
Conflict-Domains: nvidia-accelerator-pack,tts-native-runtime,b2-audio-work-layout
Expected-Paths: .claims/issue-323-gpu-tts.md,engine/dubflow/accelerator/**,engine/dubflow/tts/vieneu.py,engine/dubflow/tts/vieneu_native.py,engine/dubflow/tts/native_process.py,engine/dubflow/tts/mimic3_native.py,engine/dubflow/worker/b2_audio.py,engine/dubflow/worker/production_job.py,models/manifests/accelerator-nvidia-onnx-cuda13-v1.json,docs/adr/ADR-0039-*.md,docs/licenses/nvidia-cuda-runtime.md,tests/accelerator/**,tests/production/tts/**,tests/integration/production_local_file/**,crates/job-supervisor/app/src/main.rs,scripts/ci/component_registry.json,packaging/runtime/README.md
Required-CI: PR Fast + Integration

## Status
#323 leaf 2: VieNeu dubbing on an NVIDIA GPU through ONNX Runtime CUDA, CPU otherwise. Implementation in this PR.
