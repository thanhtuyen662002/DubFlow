DUBFLOW_PR_V1
Issue: #323
Lease-Owner: Claude/issue-323-gpu-separation
Lease-Heartbeat: 2026-10-29T16:12:02Z
Tested-Base-SHA: d2f330564df9c2a345773d8a2a08575e42bc53a3
Conflict-Domains: nvidia-accelerator-pack,separation-runtime
Expected-Paths: .claims/issue-323-gpu-separation.md,engine/dubflow/accelerator/onnx_cuda.py,engine/dubflow/tts/vieneu_native.py,engine/dubflow/separation/spleeter.py,engine/dubflow/separation/spleeter_native.py,engine/dubflow/worker/production_job.py,docs/adr/ADR-0039-nvidia-onnx-dubbing-pack.md,tests/separation/**,tests/production/worker/test_separation.py
Required-CI: PR Fast + Integration

## Status
Spleeter separation on the GPU; implementation in this PR.
