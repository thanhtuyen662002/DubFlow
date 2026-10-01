# App-owned media adapter

`MediaProbe` and `FfmpegMediaAdapter` are the production worker boundary for
local media. They require absolute executable paths supplied by the app-owned
runtime manifest plus an explicit trusted runtime root; a command found
through `PATH`, a symlink, or a binary outside that root is rejected. Every
process is started with an argv tuple and `shell=False`, with stdin closed and
bounded diagnostic output.

`MediaProbe` consumes FFprobe JSON and preserves rational time-bases, signed
start PTS and integer durations. Its timeline API performs explicit integer
rescaling with named rounding modes; it never converts durable identity to
floating seconds. `FfmpegMediaAdapter` provides audio extraction, H.264/AAC
rendering with optional subtitle/audio inputs, and video/audio muxing. The
Windows production profile selects FFmpeg's `h264_mf` Media Foundation
encoder because the redistributed LGPL build does not contain GPL-only
`libx264`; the resulting stream remains H.264 and does not require a GPU. All
outputs are created as private sibling `.partial` files and atomically
published only after FFmpeg exits successfully and writes non-empty bytes.

Burn-in subtitles are copied to a private safe basename and FFmpeg runs from
the output directory. This avoids passing a drive-qualified or user-named path
through the subtitles filter grammar while retaining shell-free argv.

The module intentionally has no fixture backend or model selection. ASR,
translation and TTS workers inject their own app-owned model adapters and use
these operations for media I/O.

Optional text-intelligence stages use `sample_frames` to publish a bounded,
atomic directory of real PNG frames for an app-owned detector adapter. Visual
cleanup uses `apply_visual_masks` with generated integer-tick `delogo` ranges;
the worker probes and validates that output before it can replace the source
for rendering, and retains the source on any failure.
