# FFmpeg redistribution

DubFlow's Windows runtime uses the LGPL build from the pinned BtbN
FFmpeg-Builds autobuild `autobuild-2026-09-30-13-08` (monthly build, retained two years), asset
`ffmpeg-n9.0.2-17-g2a571b6068-win64-lgpl-9.0.zip`.

The release workflow records and verifies the archive SHA-256 before copying
`ffmpeg.exe` and `ffprobe.exe` into the app-owned runtime. The corresponding
FFmpeg source and build instructions are available from the
[FFmpeg-Builds repository](https://github.com/BtbN/FFmpeg-Builds). This file
is included in every release bundle with the runtime binaries.
