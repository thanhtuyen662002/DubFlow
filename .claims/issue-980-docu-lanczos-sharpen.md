DUBFLOW_PR_V1
Issue: #980
Lease-Owner: Claude/issue-980-docu-lanczos-sharpen
Lease-Heartbeat: 2026-11-28T17:10:00Z
Tested-Base-SHA: 1401a664dfee769a55516d3093a05e0d0f50eb53
Conflict-Domains: documentary-render
Expected-Paths:
- .claims/issue-980-docu-lanczos-sharpen.md
- engine/dubflow/docu/roughcut.py
- tests/docu/test_roughcut.py
Required-CI: PR Fast + PR Integration

## Status
Documentary framing scales with lanczos and adds a light luma unsharp where the crop is upscaled (1080p footage zoomed for overscan or a caption band).
