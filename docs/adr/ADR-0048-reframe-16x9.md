# ADR-0048: Optional 16:9 derivatives of completed vertical exports

Status: Proposed. Issue #382.

## Decision

The inspector exposes “Đổi khung 16:9” after Shorts only for completed vertical
`final_vi.mp4` exports. The host validates the path through `review_dir`, probes
geometry off the UI thread and launches the app-owned Python/media runtime.
The original video is read-only. No model, voice or system Python is needed.

The private v1 JSON spec requires `schema_version`, absolute `video` below
`DubFlow Output`, and `mode`. Optional fields are `title` (≤80 Unicode characters),
`score` (integer 0–10 or null), `pros`/`cons` (up to three nonempty strings each,
≤60 characters), `summary` (≤120), `focus_start`/`focus_end` (finite 0–100,
default 50), `start_ms` (default 0), and `end_ms` (null means the source end).
Unknown/duplicate fields, control characters and invalid types are rejected.
Trim ranges are integer milliseconds within the finished export, not canonical
timeline identities. The engine checks their bounds against the source duration.

`bang-thong-tin` reuses compilation's right-hand fitted clip, blurred fill and
dark left panel with escaped ASS title, score/bar and pros/cons. The shared ASS
helper adds an optional summary near the panel bottom, reserving room by using
smaller bullet text only when a summary is present. Existing calls are unchanged.
`cat-ngang` crops full width × width·9/16, interpolating the vertical position
linearly over the trimmed clip using FFmpeg's `t`, then scales to 1920×1080.
Focus is percent of free vertical travel: 0 top, 50 centre, 100 bottom. A nonempty
title adds a small top-left ASS box. Frames remain chronological and retain the
clip's own audio. Both modes encode 30 fps H.264 Media Foundation around 8 Mb/s,
with stereo AAC at 48 kHz / 192 kb/s; tests inject `mpeg4`.

Spec, RUNNING/COMPLETED/FAILED/CANCELLED status, cancel marker and work files live
in `.dubflow-work/reframe/`. The shared runner kills FFmpeg on cancellation; the
host prevents overlapping renders and watches unexpected child termination.
Only after duration (±300 ms), 1920×1080 and 30 fps checks does an atomic rename
publish `final_vi_16x9.mp4`. Failure/cancel preserves previous published output.
A receipt binds source, recipe/helper code, tool bytes, encoder and settings to
the output hash. A retry reuses a verified final artifact; an interrupted render
restarts this single segment. Memory is bounded by FFmpeg streaming.

“AI điền bảng” explicitly sends only up to 60 Vietnamese subtitle lines (≤300
characters each) to the saved provider through `askJson`. The view names that
provider; compilation's normalization bounds and cleans the editable response.
The prompt forbids invented facts and allows a null score when evidence is weak.

## Compatibility and migration

This adds private versioned spec/status/receipt files and an optional derivative.
Canonical timelines, SQLite, worker protocols, manifests and standard/editable
exports need no migration. Older clients ignore these files. Producer code hashes
invalidate receipts after recipe changes; incompatible schemas require a new
version. No credentials, media or model artifacts enter Git history.
