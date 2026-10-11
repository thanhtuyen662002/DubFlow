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
