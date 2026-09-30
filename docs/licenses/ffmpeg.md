# FFmpeg redistribution

DubFlow's Windows runtime uses the LGPL build from the pinned BtbN
FFmpeg-Builds autobuild `autobuild-2026-09-29-13-10`, asset
`ffmpeg-n9.0.2-14-gebafaee10a-win64-lgpl-9.0.zip`.

The release workflow records and verifies the archive SHA-256 before copying
`ffmpeg.exe` and `ffprobe.exe` into the app-owned runtime. The corresponding
FFmpeg source and build instructions are available from the
[FFmpeg-Builds repository](https://github.com/BtbN/FFmpeg-Builds). This file
is included in every release bundle with the runtime binaries.
