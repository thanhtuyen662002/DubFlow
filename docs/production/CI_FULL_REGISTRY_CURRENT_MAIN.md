# Full deterministic Integration registry — current main

Evidence for #250. This is a full, non-selective run of every registered integration component on one exact source SHA. #416 and #417, its hard dependencies, are both merged at this SHA.

| Field | Value |
|---|---|
| Source SHA | `713bd2365db313f0f4ffb662e2190e5c6a5b2394` (main after #467 / #417; includes #465 / #416, #466 / #464 and #469 / #468) |
| Runner | GitHub Actions `ubuntu-latest` (image `ubuntu-24.04`, version 20261021.618), workflow **PR Integration**, run [41780693790](https://github.com/thanhtuyen662002/DubFlow/actions/runs/41780693790), job 126411316531, event `workflow_dispatch` on `main` |
| Python / Node | CPython 3.12.15 (`actions/setup-python`, `python-version: "3.12"`); Node 22.14.0 (`actions/setup-node`) |
| Checkout | Complete (`fetch-depth: 0`) at the exact source SHA |
| Command | `python scripts/ci/run_integration.py --base-sha ""`. `BASE_SHA` is empty for `workflow_dispatch`, so `changed_paths` returns `None` and every registered component runs. There is no component filter. It is preceded by `python -m unittest discover -s tests/ci -p 'test_*.py' -v` (38 tests OK). |
| Pre-installed test imports | `cryptography==46.0.1` (workflow step). `production-b2-dubbing` installs its own hash-pinned `tests/audio_mix/requirements-ci.txt`. |
| Start / end (UTC) | 2026-11-04T05:40:38Z / 2026-11-04T05:45:11Z (job) |
| Registered components | 59 (`scripts/ci/component_registry.json` at the source SHA) |
| Selected / executed components | 59 / 59, in registry order; none missing, none extra |
| Totals | Python unittest: 1,318 tests run, 38 skipped, 0 failures. Rust `cargo test`: 328 passed, 0 failed, 4 ignored. Node: 63 desktop suites passed (31 in each of the two npm components, plus `review-center`). |
| Overall exit code | 0. The log ends with `Integration passed for 59 component(s).` |

## Component results
Every component ran all of its registered commands, and every command exited 0. The runner stops at the first non-zero exit, so a final exit of 0 means all of them passed.
- "Python tests (skipped)" sums the `Ran N tests` and `OK (skipped=K)` lines of that component's unittest commands.
- "Rust passed (ignored)" sums its `cargo test` result lines.
- "Node suites passed" counts the desktop suite lines that end in `tests passed`.

| # | Component | Commands | Python tests (skipped) | Rust passed (ignored) | Node suites passed | Result |
|---|---|---|---|---|---|---|
| # | Component | Commands | Python tests (skipped) | Rust passed (ignored) | Node suites passed | Result |
|---|---|---|---|---|---|---|
| 1 | `supervisor-workspace` | 3 | 3 (0) | 148 (2) | – | PASS |
| 2 | `media-contracts` | 2 | – | 8 (0) | – | PASS |
| 3 | `worker-protocol` | 2 | 7 (0) | 7 (0) | – | PASS |
| 4 | `job-state` | 2 | 5 (0) | 20 (0) | – | PASS |
| 5 | `storage-recovery` | 3 | 2 (0) | 15 (0) | – | PASS |
| 6 | `security-boundary` | 2 | 30 (0) | 5 (0) | – | PASS |
| 7 | `model-manifests` | 1 | 4 (0) | – | – | PASS |
| 8 | `job-liveness` | 2 | 2 (0) | 7 (0) | – | PASS |
| 9 | `project-lock` | 3 | 2 (0) | 4 (0) | – | PASS |
| 10 | `fair-scheduler` | 3 | 2 (0) | 6 (0) | – | PASS |
| 11 | `artifact-provenance` | 3 | 2 (0) | 5 (0) | – | PASS |
| 12 | `model-retention` | 2 | 2 (0) | 5 (0) | – | PASS |
| 13 | `local-file-slice` | 1 | – | 6 (0) | – | PASS |
| 14 | `slice-b1-vietsub` | 2 | 8 (0) | – | – | PASS |
| 15 | `tts` | 1 | 22 (0) | – | – | PASS |
| 16 | `audio-mix` | 2 | 22 (0) | – | – | PASS |
| 17 | `asr` | 1 | 16 (0) | – | – | PASS |
| 18 | `accelerator` | 1 | 32 (2) | – | – | PASS |
| 19 | `discovery` | 1 | 46 (0) | – | – | PASS |
| 20 | `translation` | 1 | 59 (0) | – | – | PASS |
| 21 | `product-video` | 1 | 40 (4) | – | – | PASS |
| 22 | `compilation-video` | 1 | 9 (1) | – | – | PASS |
| 23 | `documentary` | 1 | 60 (4) | – | – | PASS |
| 24 | `package-video` | 1 | 13 (2) | – | – | PASS |
| 25 | `reframe` | 1 | 8 (1) | – | – | PASS |
| 26 | `shorts` | 1 | 7 (1) | – | – | PASS |
| 27 | `seo` | 1 | 17 (0) | – | – | PASS |
| 28 | `support-report` | 1 | 9 (0) | – | – | PASS |
| 29 | `source-adapter` | 1 | 60 (1) | – | – | PASS |
| 30 | `bilibili-source` | 1 | 23 (0) | – | – | PASS |
| 31 | `douyin-source` | 1 | 22 (0) | – | – | PASS |
| 32 | `source-materializer` | 1 | 68 (1) | – | – | PASS |
| 33 | `source-generic` | 1 | 35 (2) | – | – | PASS |
| 34 | `speaker-diarization` | 1 | 16 (1) | – | – | PASS |
| 35 | `tts-multivoice` | 1 | 4 (0) | – | – | PASS |
| 36 | `text-intelligence` | 1 | 5 (0) | – | – | PASS |
| 37 | `text-orientation` | 1 | 5 (0) | – | – | PASS |
| 38 | `audio-cleanup` | 1 | 21 (1) | – | – | PASS |
| 39 | `spleeter-separation` | 1 | 15 (3) | – | – | PASS |
| 40 | `visual-cleanup` | 1 | 12 (1) | – | – | PASS |
| 41 | `character-speaker` | 1 | 5 (0) | – | – | PASS |
| 42 | `update` | 1 | 24 (1) | – | – | PASS |
| 43 | `updater-recovery` | 1 | – | 5 (0) | – | PASS |
| 44 | `installer-bootstrap` | 1 | 16 (0) | – | – | PASS |
| 45 | `native-source-controller` | 1 | – | 65 (2) | – | PASS |
| 46 | `source-queue` | 2 | 53 (1) | 22 (0) | – | PASS |
| 47 | `release-soak` | 1 | 8 (0) | – | – | PASS |
| 48 | `capcut-import-pack` | 1 | 3 (0) | – | – | PASS |
| 49 | `capcut-direct-draft` | 1 | 4 (0) | – | – | PASS |
| 50 | `review-center` | 1 | – | – | 1 suite | PASS |
| 51 | `benchmark-harness` | 1 | 4 (0) | – | – | PASS |
| 52 | `qc-provenance` | 1 | 4 (0) | – | – | PASS |
| 53 | `governance-watchdog` | 1 | 6 (0) | – | – | PASS |
| 54 | `render-export` | 1 | 10 (0) | – | – | PASS |
| 55 | `subtitle-render` | 1 | 14 (0) | – | – | PASS |
| 56 | `release-bundle` | 1 | 71 (4) | – | – | PASS |
| 57 | `production-b2-dubbing` | 4 | 234 (7) | – | – | PASS |
| 58 | `desktop-ai-provider` | 3 | – | – | 31 suites | PASS |
| 59 | `desktop-editor-subtitles` | 6 | 147 (0) | – | 31 suites | PASS |

## Skips
The 38 skipped Python tests and 4 ignored Rust tests come from the components themselves, as listed above. They are environment-gated cases that do not run on this Linux runner, such as Windows-only handles and paths, live sites, real FFmpeg or optional native runtimes. They are not counted as passes.

The Windows-only parts run on their own Windows PR lanes, not in this registry:
- the desktop host `cargo test` and the real-FFmpeg media adapter tests (`desktop-editor-windows`, #417);
- the documentary host tests (`documentary-windows`, #464);
- the release suite (`release-tests-windows`, #416).

## Scope
- This is deterministic registry evidence for one SHA. It is not Windows qualification, a release soak, a GPU run or a live-site run, and it does not accept #175.
- The CI checks on this documentation PR do not replace the full-registry run above.
- The earlier 50-component report on `2fb02b5` is superseded and remains in the git history of this file.
