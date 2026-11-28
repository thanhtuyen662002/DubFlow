DUBFLOW_PR_V1
Issue: #980
Lease-Owner: Claude/issue-980-docu-lanczos-sharpen
Lease-Heartbeat: 2026-11-28T17:10:00Z
Tested-Base-SHA: 468fd0359dd8a65e5a58dd2f2806fc62f7e4b7ff
Conflict-Domains: documentary-render
Expected-Paths:
- .claims/issue-980-docu-lanczos-sharpen.md
- engine/dubflow/docu/roughcut.py
- tests/docu/test_roughcut.py
Required-CI: PR Fast + PR Integration

## Status
Documentary framing scales with lanczos and adds a light luma unsharp where the crop is upscaled (1080p footage zoomed for overscan or a caption band).
