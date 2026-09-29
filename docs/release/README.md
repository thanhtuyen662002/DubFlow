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

## Build locally

Packaging smoke needs an app-owned runtime directory containing `python.exe`.
The release workflow supplies that directory from the pinned `setup-python`
toolchain. A local smoke build can use a small fixture runtime:

```text
python -m packaging.release.builder \
  --source-root . \
  --output-dir dist \
  --version 0.1.0-rc1 \
  --source-sha <40-or-64-lowercase-git-sha> \
  --runtime-root <directory-containing-python.exe> \
  --source-date-epoch <unix-seconds>
```

The resulting ZIP contains `setup.cmd` and `setup.ps1`. The Windows release
workflow additionally compiles `packaging/release/windows_setup.rs` into a
self-extracting `*-setup.exe`. The Rust bootstrapper embeds the exact ZIP,
uses inbox PowerShell only for extraction, launches `setup.cmd` under the
current user token, and does not request elevation. It uploads the ZIP,
executable, checksums, and release evidence. The executable is unsigned while
the repository has no configured release signing service; stable publication
therefore fails closed until signing is provided.
