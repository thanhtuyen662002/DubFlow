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

For a source without audio, `create_silent_audio` provides a bounded stereo
PCM bed when the worker already has valid dialogue captions. Its sample count
comes from integer source duration/time-base, and it validates the WAV before
atomic publication. It never supplies dialogue text. QC and mix provenance
identify this generated silence; see ADR0024 for compatibility and recovery.
