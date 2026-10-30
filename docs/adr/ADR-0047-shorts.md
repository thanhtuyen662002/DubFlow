# ADR-0047: Vertical Shorts from finished dubbing exports

Status: Proposed. Issue #379.

## Decision

Shorts are an optional post-export adapter over a completed `final_vi.mp4` under
`DubFlow Output`. A strict version 1 spec contains `schema_version`, `video`,
`items` (1–3 integer millisecond ranges, each 5–60 seconds, with a nonempty
title of at most 80 Unicode characters), and `layout` (`auto`, `ban-tin`,
`mo-nen`). The engine checks ranges against the probed export duration before
rendering. These ranges are selections within this export, not canonical
timeline identities. The source and its burned subtitles remain read-only.

Each excerpt preserves chronological frames and its own audio. Output is
1080×1920 at 30 fps, H.264 Media Foundation around 6 Mb/s and stereo AAC at
48 kHz / 192 kb/s. Tests can override the encoder. Portrait sources resize to
the canvas without removing subtitles. Landscape `auto` uses the product
template's 1080×1200 media area above a green title panel; `mo-nen` centers
the fitted source on its blurred fill. ASS renders wrapped, escaped titles.

The installed app-owned Python/media runtime needs no voice or model. The host
uses `review_dir`, validates requests, prevents concurrent Shorts renders per
job, and watches worker termination. Status and the cancellation marker live in
`.dubflow-work/shorts/`. Cancellation kills FFmpeg and retains published clips.
Each clip is a checkpoint: receipts bind source bytes, recipe code, FFmpeg,
encoder, geometry, layout and selection to the rendered content hash. A retry
reuses verified clips; changed or damaged clips render again. Partial files are
published atomically only after duration (±300 ms), dimensions and fps checks.
Outputs are `shorts/short-N.mp4` with a UTF-8 `short-N.txt` title, full-video
description and optional attribution matching `formatCreditLine`. The video
title comes from the manifest's source filename, with the job folder name as
fallback; directory paths never enter the description.

The inspector offers manual rows and optional AI suggestions. Only Vietnamese
cue text and its times go to the saved provider through `askJson`; the view
names that provider before sending. Subtitle batches are bounded to 180,000
characters; very long videos are scanned in batches, comparing at most six
candidates at a time to keep the best 1–3 across the video. Suggestions snap to cue
boundaries, reject ranges outside 5–60 seconds, and drop overlaps. Users review
and edit times/titles before rendering. Credits, private notes and media are
excluded from provider input.

## Compatibility and migration

This adds private versioned spec/status/receipt artifacts beside existing
exports. Canonical timelines, manifests, SQLite, dubbing worker contracts and
editable exports do not change. Existing jobs need no migration; old clients
ignore the new directory. Future recipe changes invalidate receipts via code
hashes; incompatible spec/status changes require a new schema version.
