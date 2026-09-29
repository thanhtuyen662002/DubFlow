# Windows release bundle

This directory defines the downloadable Windows release contract. The release
builder produces a versioned x64 bundle from one source commit and records the
source SHA, package manifest version, runtime version, file hashes, and build
metadata in a machine-readable release manifest.

The bootstrap entrypoint is app-owned. It stages files below the user-owned
DubFlow installation root, verifies size, SHA-256, and signature metadata before
activation, and writes state atomically. It never asks the user to install
system Python, FFmpeg, CUDA, `pip`, or `conda`.

Pull requests run unsigned packaging smoke only. Signing and publication are
restricted to the protected release workflow. A published artifact remains a
release candidate until the clean-machine and hardware evidence required by
Issues #36 and #38 is attached.
