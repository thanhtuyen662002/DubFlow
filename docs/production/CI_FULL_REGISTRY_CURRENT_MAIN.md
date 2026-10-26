# Full deterministic Integration registry — current main

Evidence for #250. This is a full, non-selective run of every registered integration component on one exact source SHA.

| Field | Value |
|---|---|
| Source SHA | `15e1b2d836725a3021bdc75c9b9306d6dc28a4df` (main after #280) |
| Runner | GitHub Actions `ubuntu-latest`, workflow **PR Integration**, run [40255620223](https://github.com/thanhtuyen662002/DubFlow/actions/runs/40255620223) (job 121356725650), event `workflow_dispatch` |
| Python | CPython 3.12.15 (`actions/setup-python`, `python-version: "3.12"`) |
| Command | `python scripts/ci/run_integration.py --base-sha ""`. The base SHA is empty for `workflow_dispatch`, so `changed_paths` returns `None` and every registered component runs; there is no component filter. It is preceded by `python -m unittest discover -s tests/ci` (37 tests OK). |
| Pre-installed test imports | `cryptography==46.0.1` and `numpy==2.2.6` (same pins as the app runtime) |
| Start / end (UTC) | 2026-10-26T01:43:44Z / 2026-10-26T01:46:50Z (job) |
| Registered components | 49 (`scripts/ci/component_registry.json`) |
| Selected components | 49 (full registry; the executed list matches the registry exactly) |
| Overall exit code | 0. The log ends with `Integration passed for 49 component(s).` |

The governance validation (`scripts/validate_governance.py`) ran first and passed.

The previous report covered `60d92bc74005110bd37d06eba1547cd83aa27081` (run 40175033014) and remains valid only for that SHA.

## Component results
Every component below ran all of its registered commands, and every command exited 0. The runner stops on the first non-zero exit, so a final exit of 0 means all of them passed.

| # | Component | Result |
|---|---|---|
| 1 | `supervisor-workspace` | PASS |
| 2 | `media-contracts` | PASS |
| 3 | `worker-protocol` | PASS |
| 4 | `job-state` | PASS |
| 5 | `storage-recovery` | PASS |
| 6 | `security-boundary` | PASS |
| 7 | `model-manifests` | PASS |
| 8 | `job-liveness` | PASS |
| 9 | `project-lock` | PASS |
| 10 | `fair-scheduler` | PASS |
| 11 | `artifact-provenance` | PASS |
| 12 | `model-retention` | PASS |
| 13 | `local-file-slice` | PASS |
| 14 | `slice-b1-vietsub` | PASS |
| 15 | `tts` | PASS |
| 16 | `audio-mix` | PASS |
| 17 | `asr` | PASS |
| 18 | `discovery` | PASS |
| 19 | `translation` | PASS |
| 20 | `seo` | PASS |
| 21 | `source-adapter` | PASS |
| 22 | `bilibili-source` | PASS |
| 23 | `douyin-source` | PASS |
| 24 | `source-materializer` | PASS |
| 25 | `source-generic` | PASS |
| 26 | `speaker-diarization` | PASS |
| 27 | `tts-multivoice` | PASS |
| 28 | `text-intelligence` | PASS |
| 29 | `text-orientation` | PASS |
| 30 | `audio-cleanup` | PASS |
| 31 | `spleeter-separation` | PASS |
| 32 | `visual-cleanup` | PASS |
| 33 | `character-speaker` | PASS |
| 34 | `update` | PASS |
| 35 | `updater-recovery` | PASS |
| 36 | `installer-bootstrap` | PASS |
| 37 | `native-source-controller` | PASS |
| 38 | `source-queue` | PASS |
| 39 | `release-soak` | PASS |
| 40 | `capcut-import-pack` | PASS |
| 41 | `capcut-direct-draft` | PASS |
| 42 | `review-center` | PASS |
| 43 | `benchmark-harness` | PASS |
| 44 | `qc-provenance` | PASS |
| 45 | `governance-watchdog` | PASS |
| 46 | `render-export` | PASS |
| 47 | `subtitle-render` | PASS |
| 48 | `release-bundle` | PASS |
| 49 | `production-b2-dubbing` | PASS |

## Scope
This run is the Linux, deterministic integration registry. It is not Windows Release, Release / Soak, real-media or GPU evidence; those lanes stay separate under #175. The desktop host's Rust unit tests are not part of this registry.
