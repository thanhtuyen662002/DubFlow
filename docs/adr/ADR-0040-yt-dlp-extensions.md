# ADR-0040: Pinned yt-dlp extensions for TikTok and YouTube links

Status: Proposed, Issue #335. Amends the source SDK decision (Issue #167) that shipped the yt-dlp wheel alone.

## Context
Partners send product videos as TikTok and YouTube links. DubFlow sends any non-Bilibili, non-Douyin link to the generic adapter, which runs the pinned yt-dlp 2026.08.19 in an isolated `python -I -S -B` process with `js_runtimes: {}` and no optional dependencies. Checked against that exact yt-dlp:
- **TikTok:** fails ("Unexpected response from webpage request"). Its extractor needs browser impersonation (curl_cffi).
- **YouTube:** works for some videos, but warns that extraction without a JavaScript runtime is deprecated. Formats depend on solving YouTube's JS challenges (EJS plus a JS runtime).

## Decision
1. **Pin the extras yt-dlp itself pins.** The SDK descriptor (`yt-dlp-sdk-v1.json`, producer `yt-dlp-sdk-2026.08.19-v2`) gains an `extensions` list:
   - curl_cffi 0.16.0, cffi 2.1.1 (CPython 3.12), certifi 2026.7.22, pycparser 3.0 and yt-dlp-ejs 0.8.0, which are yt-dlp 2026.08.19's own `pin` and `pin-curl-cffi` versions;
   - Deno 2.9.8, the JS runtime yt-dlp enables by default.

   Each is a PyPI wheel pinned by URL, size and SHA-256. Deno's executable member is also pinned by size and SHA-256. Exactly one JS runtime is allowed.
2. **Build time only.** `provision_sdk` downloads each wheel with the resumable, verified materializer.
   - The libraries are extracted into `runtime/source/ytdlp-lib`, and wheel `.data` members are never installed.
   - Only `deno.exe` and its license are extracted into `runtime/source/deno`.
   - The folders are rebuilt from verified wheels on every build, and the wheels are removed.
   - The release builder copies the runtime, so the signed inventory pins every extracted file.
3. **Verified on every run.**
   - `provider_from_verified_bundle` takes the extension files from the verified inventory, and requires Deno's hash to equal its pin.
   - Before each extractor run, the transport hashes every extension file again and refuses any file in those folders that is not in the inventory, so nothing extra can be imported or run.
4. **Isolated use.**
   - The child gets the library folder and Deno's absolute path as arguments.
   - It puts the library folder after the SDK on `sys.path`, still under `-I -S`, and sets `js_runtimes: {"deno": {"path": …}}`.
   - `remote_components` stays empty, because EJS comes from the pinned package.
   - Health reports `impersonation` and `js_runtime`.
5. **Older bundles keep working.** A descriptor without `extensions` behaves exactly as before.

## Consequences
- **Bigger installer:** the installer grows by about 44 MB compressed. Deno is 98 MB on disk and the libraries about 5.5 MB.
- **Checked in real runs:** with this layout on Windows, a public TikTok video resolves (11 formats, up to 1280p), and YouTube watch and `youtu.be` links resolve, including 2160p formats.
- **Not permanent:** providers change. A future yt-dlp release that changes these pins needs a new descriptor and release, as the SDK itself does.
- **Not covered:** logged-in, private and age-restricted videos, and any discovery or reupload of other people's content.
