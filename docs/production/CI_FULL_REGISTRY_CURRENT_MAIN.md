# Full deterministic Integration registry — current main

Evidence for #250. This is a full, non-selective run of every registered integration component on one exact source SHA.

| Field | Value |
|---|---|
| Source SHA | `2fb02b58c7d6d9464e9aaae0237ec23f512629ee` (main after #326; includes #322, #324 and #325) |
| Runner | GitHub Actions `ubuntu-latest` (image `ubuntu-24.04`, version 20261025.353.1), workflow **PR Integration**, run [40828294459](https://github.com/thanhtuyen662002/DubFlow/actions/runs/40828294459), event `workflow_dispatch` on `main` |
| Python | CPython 3.12.15 (`actions/setup-python`, `python-version: "3.12"`) |
| Command | `python scripts/ci/run_integration.py --base-sha ""`. `BASE_SHA` is empty for `workflow_dispatch`, so `changed_paths` returns `None` and every registered component runs; there is no component filter. It is preceded by `python -m unittest discover -s tests/ci -p 'test_*.py' -v` (37 tests OK). |
| Pre-installed test imports | `cryptography==46.0.1` (workflow step). `production-b2-dubbing` installs its own hash-pinned `tests/audio_mix/requirements-ci.txt`. |
| Start / end (UTC) | 2026-10-29T06:18:08Z / 2026-10-29T06:21:29Z (job) |
| Registered components | 50 (`scripts/ci/component_registry.json` at the source SHA) |
| Selected / executed components | 50 / 50, in registry order; none missing, none extra |
| Python unittest totals | 953 tests run, 22 skipped, 0 failures |
| Overall exit code | 0. The log ends with `Integration passed for 50 component(s).` |

## Component results
Every component ran all of its registered commands, and every command exited 0. The runner stops at the first non-zero exit, so a final exit of 0 means all of them passed. "Python unittest tests run" counts the `Ran N tests` lines of that component's `unittest` commands. Components whose commands are only `cargo test` or `node` show "—".

| # | Component | Commands | Python unittest tests run | Skipped | Result |
|---|---|---|---|---|---|
| 1 | `supervisor-workspace` | 3 (python, cargo) | 3 | 0 | PASS |
| 2 | `media-contracts` | 2 (cargo, python) | — | 0 | PASS |
| 3 | `worker-protocol` | 2 (cargo, python) | 7 | 0 | PASS |
| 4 | `job-state` | 2 (cargo, python) | 5 | 0 | PASS |
| 5 | `storage-recovery` | 3 (cargo, python) | 2 | 0 | PASS |
| 6 | `security-boundary` | 2 (cargo, python) | 30 | 0 | PASS |
| 7 | `model-manifests` | 1 (python) | 4 | 0 | PASS |
| 8 | `job-liveness` | 2 (cargo, python) | 2 | 0 | PASS |
| 9 | `project-lock` | 3 (cargo, python) | 2 | 0 | PASS |
| 10 | `fair-scheduler` | 3 (cargo, python) | 2 | 0 | PASS |
| 11 | `artifact-provenance` | 3 (cargo, python) | 2 | 0 | PASS |
| 12 | `model-retention` | 2 (cargo, python) | 2 | 0 | PASS |
| 13 | `local-file-slice` | 1 (cargo) | — | 0 | PASS |
| 14 | `slice-b1-vietsub` | 2 (python) | 8 | 0 | PASS |
| 15 | `tts` | 1 (python) | 21 | 0 | PASS |
| 16 | `audio-mix` | 2 (python) | 22 | 0 | PASS |
| 17 | `asr` | 1 (python) | 16 | 0 | PASS |
| 18 | `accelerator` | 1 (python) | 20 | 0 | PASS |
| 19 | `discovery` | 1 (python) | 46 | 0 | PASS |
| 20 | `translation` | 1 (python) | 59 | 0 | PASS |
| 21 | `seo` | 1 (python) | 17 | 0 | PASS |
| 22 | `source-adapter` | 1 (python) | 54 | 1 | PASS |
| 23 | `bilibili-source` | 1 (python) | 23 | 0 | PASS |
| 24 | `douyin-source` | 1 (python) | 22 | 0 | PASS |
| 25 | `source-materializer` | 1 (python) | 51 | 1 | PASS |
| 26 | `source-generic` | 1 (python) | 35 | 2 | PASS |
| 27 | `speaker-diarization` | 1 (python) | 16 | 1 | PASS |
| 28 | `tts-multivoice` | 1 (python) | 4 | 0 | PASS |
| 29 | `text-intelligence` | 1 (python) | 5 | 0 | PASS |
| 30 | `text-orientation` | 1 (python) | 5 | 0 | PASS |
| 31 | `audio-cleanup` | 1 (python) | 21 | 1 | PASS |
| 32 | `spleeter-separation` | 1 (python) | 11 | 2 | PASS |
| 33 | `visual-cleanup` | 1 (python) | 12 | 1 | PASS |
| 34 | `character-speaker` | 1 (python) | 5 | 0 | PASS |
| 35 | `update` | 1 (python) | 24 | 1 | PASS |
| 36 | `updater-recovery` | 1 (cargo) | — | 0 | PASS |
| 37 | `installer-bootstrap` | 1 (python) | 16 | 0 | PASS |
| 38 | `native-source-controller` | 1 (cargo) | — | 0 | PASS |
| 39 | `source-queue` | 2 (cargo, python) | 47 | 1 | PASS |
| 40 | `release-soak` | 1 (python) | 8 | 0 | PASS |
| 41 | `capcut-import-pack` | 1 (python) | 3 | 0 | PASS |
| 42 | `capcut-direct-draft` | 1 (python) | 4 | 0 | PASS |
| 43 | `review-center` | 1 (node) | — | 0 | PASS |
| 44 | `benchmark-harness` | 1 (python) | 4 | 0 | PASS |
| 45 | `qc-provenance` | 1 (python) | 4 | 0 | PASS |
| 46 | `governance-watchdog` | 1 (python) | 6 | 0 | PASS |
| 47 | `render-export` | 1 (python) | 10 | 0 | PASS |
| 48 | `subtitle-render` | 1 (python) | 8 | 0 | PASS |
| 49 | `release-bundle` | 1 (python) | 71 | 4 | PASS |
| 50 | `production-b2-dubbing` | 4 (python) | 214 | 7 | PASS |

## Skips
The 22 skipped tests are reported by the components themselves, as listed above. They are environment-gated cases, such as Windows-only behaviour, live sites or optional native runtimes, and are not counted as passes.

## Scope
- This is deterministic registry evidence for one SHA. It is not Windows qualification, a release soak, a GPU run or a live-site run.
- The CI checks on this documentation PR do not replace the full-registry run above.
- The earlier 49-component report on `7f6bc8e` is superseded and remains in the git history of this file.
