# Reviewed source SDK distribution

Issue #167 ships the exact `yt_dlp-2026.8.19-py3-none-any.whl` from
[official PyPI](https://pypi.org/project/yt-dlp/2026.8.19/), SHA-256
`1d57897e94c6665a0a6f9bc54b34e584284e32c034ffab3a7df25d8f7b24eedf`.
The reviewed descriptor is `engine/dubflow/download/assets/yt-dlp-sdk-v1.json`.

Upstream identifies its PyPI wheel as Unlicense code. Redistribution of this
exact wheel is approved. Its embedded `licenses/LICENSE` is retained and checked
against SHA-256 `7e12e5df4bae12cb21581ba157ced20e1986a0508dd10d0e8a4ab9a4cf94e85c`.
The corresponding source is [tag 2026.08.19](https://github.com/yt-dlp/yt-dlp/tree/2026.08.19).
This decision covers the wheel only. The upstream PyInstaller executable,
optional third-party dependencies, EJS, plugins and downloaded helpers are
separate artifacts with separate distribution decisions.

The wheel is imported without extraction under isolated app-owned Python.
Preparation acquires pinned bytes into `runtime/source`; the existing release
manifest inventories those bytes and the descriptor/helper. An installed
application must obtain this code through its verified release/update policy,
not run pip or silently update the extractor. Provider breakage does not justify
changing an existing job's producer. A new approved SDK requires a new descriptor
and compatible release; retained releases remain responsible for pinned jobs.

Approved distribution is not proof of provider health. Installed desktop intake,
browser-session consent and source qualification remain separate acceptance.

## Extensions for TikTok and YouTube (Issue #335, ADR-0040)

The descriptor also pins these exact PyPI wheels, by URL, size and SHA-256. The versions are the ones yt-dlp 2026.08.19 itself pins in its `pin` and `pin-curl-cffi` extras, plus Deno:

| Package | Version | License | Why |
|---|---|---|---|
| curl_cffi | 0.16.0 | MIT (bundled libcurl-impersonate: curl and BoringSSL licenses) | Browser impersonation; TikTok refuses plain requests |
| cffi | 2.1.1 | MIT-0 | curl_cffi's C bridge |
| certifi | 2026.7.22 | MPL-2.0 (unmodified) | CA bundle for curl_cffi |
| pycparser | 3.0 | BSD-3-Clause | cffi dependency |
| yt-dlp-ejs | 0.8.0 | Unlicense AND MIT AND ISC | YouTube challenge scripts |
| deno | 2.9.8 | MIT | JS runtime that runs those scripts |

**How they are built:**
- The libraries are extracted unmodified into `runtime/source/ytdlp-lib`, with their `dist-info` license files. Wheel `.data` members are never installed.
- From the Deno wheel, only its pinned `deno.exe` and its `LICENSE` are extracted, into `runtime/source/deno`.

**How they are verified:** the signed release inventory pins every extracted file. Before each extractor run, the parent hashes every file again and refuses any file that is not in the inventory.

**How they are used:**
- The isolated extractor process imports the libraries only from that folder.
- Deno runs only from its verified absolute path.
- No system JS runtime, remote EJS component or plugin is used.
