## Current main checkpoint - 2026-10-27T13:42:00Z - NOT_RUN, not acceptance

- **Source SHA:** `b4714892565789b1569663b35fa2d544a392f545` (current main at claim creation).
- **Status:** `NOT_RUN`. The Lead runtime has GitHub connector access but no complete checkout/local shell/Cargo runner. No `python -m unittest discover -s tests/ci` or unfiltered Integration command was executed by this Lead run.
- **Historical report below:** source SHA `7f6bc8e98f5d6069b6d4bc2bfbb2d247f7cf112e`; it is not evidence for the current main SHA above.
- **Required next action (Coder 1):** on a complete checkout of exactly this source SHA, run `python -m unittest discover -s tests/ci`, then exactly `python scripts/ci/run_integration.py` with no `--base-sha` and no component filter. Record runner/OS/Python, UTC start/end, registered/selected/executed component counts, each result, skips separately, run URL and exit code.
- If the checkout or runner is unavailable to the assigned coder, keep `NOT_RUN` and name that exact missing capability. Replace this checkpoint only with evidence actually produced on the recorded source; do not infer success from selective CI or this documentation PR's checks.

---

# Full deterministic Integration registry â€” current main

Evidence for #250. This is a full, non-selective run of every registered integration component on one exact source SHA.

| Field | Value |
|---|---|
| Source SHA | `7f6bc8e98f5d6069b6d4bc2bfbb2d247f7cf112e` (main after #304; source of release 1.1.5) |
| Runner | GitHub Actions `ubuntu-latest` (image `ubuntu-24.04`, version 20261012.334.1), workflow **PR Integration**, run [40444565855](https://github.com/thanhtuyen662002/DubFlow/actions/runs/40444565855), event `workflow_dispatch` |
| Python | CPython 3.12.15 (`actions/setup-python`, `python-version: "3.12"`) |
| Command | `python scripts/ci/run_integration.py --base-sha ""`. The base SHA is empty for `workflow_dispatch`, so `changed_paths` returns `None` and every registered component runs; there is no component filter. It is preceded by `python -m unittest discover -s tests/ci -p 'test_*.py' -v` (37 tests OK). |
| Pre-installed test imports | `cryptography==46.0.1` (workflow step). `production-b2-dubbing` installs its own hash-pinned `tests/audio_mix/requirements-ci.txt`. |
| Start / end (UTC) | 2026-10-27T03:31:13Z / 2026-10-27T03:33:08Z (job log) |
| Registered components | 49 (`scripts/ci/component_registry.json` at the source SHA) |
| Selected / executed components | 49 / 49, in registry order; none missing, none extra |
| Python unittest totals | 911 tests run, 22 skipped, 0 failures |
| Overall exit code | 0. The log ends with `Integration passed for 49 component(s).` |

## Component results
Every component ran all of its registered commands, and every command exited 0. The runner stops at the first non-zero exit, so a final exit of 0 means all of them passed. "Python unittest tests run" counts the `Ran N tests` lines of that component's `unittest` commands. Components whose commands are only `cargo test` or `node` show "â€”".

| # | Component | Commands | Python unittest tests run | Skipped | Result |
|---|---|---|---|---|---|
| 1 | `supervisor-workspace` | 3 (cargo, python) | 3 | 0 | PASS |
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
| 18 | `discovery` | 1 (python) | 46 | 0 | PASS |
| 19 | `translation` | 1 (python) | 59 | 0 | PASS |
| 20 | `seo` | 1 (python) | 17 | 0 | PASS |
| 21 | `source-adapter` | 1 (python) | 52 | 1 | PASS |
| 22 | `bilibili-source` | 1 (python) | 23 | 0 | PASS |
| 23 | `douyin-source` | 1 (python) | 22 | 0 | PASS |
| 24 | `source-materializer` | 1 (python) | 50 | 1 | PASS |
| 25 | `source-generic` | 1 (python) | 35 | 2 | PASS |
| 26 | `speaker-diarization` | 1 (python) | 16 | 1 | PASS |
| 27 | `tts-multivoice` | 1 (python) | 4 | 0 | PASS |
| 28 | `text-intelligence` | 1 (python) | 5 | 0 | PASS |
| 29 | `text-orientation` | 1 (python) | 5 | 0 | PASS |
| 30 | `audio-cleanup` | 1 (python) | 21 | 1 | PASS |
| 31 | `spleeter-separation` | 1 (python) | 11 | 2 | PASS |
| 32 | `visual-cleanup` | 1 (python) | 12 | 1 | PASS |
| 33 | `character-speaker` | 1 (python) | 5 | 0 | PASS |
| 34 | `update` | 1 (python) | 24 | 1 | PASS |
| 35 | `updater-recovery` | 1 (cargo) | — | 0 | PASS |
| 36 | `installer-bootstrap` | 1 (python) | 16 | 0 | PASS |
| 37 | `native-source-controller` | 1 (cargo) | — | 0 | PASS |
| 38 | `source-queue` | 2 (cargo, python) | 47 | 1 | PASS |
| 39 | `release-soak` | 1 (python) | 8 | 0 | PASS |
| 40 | `capcut-import-pack` | 1 (python) | 3 | 0 | PASS |
| 41 | `capcut-direct-draft` | 1 (python) | 4 | 0 | PASS |
| 42 | `review-center` | 1 (node) | — | 0 | PASS |
| 43 | `benchmark-harness` | 1 (python) | 4 | 0 | PASS |
| 44 | `qc-provenance` | 1 (python) | 4 | 0 | PASS |
| 45 | `governance-watchdog` | 1 (python) | 6 | 0 | PASS |
| 46 | `render-export` | 1 (python) | 10 | 0 | PASS |
| 47 | `subtitle-render` | 1 (python) | 8 | 0 | PASS |
| 48 | `release-bundle` | 1 (python) | 71 | 4 | PASS |
| 49 | `production-b2-dubbing` | 4 (python) | 195 | 7 | PASS |

## Skips
22 tests were skipped across 11 components: `source-adapter` 1, `source-materializer` 1, `source-generic` 2, `speaker-diarization` 1, `audio-cleanup` 1, `spleeter-separation` 2, `visual-cleanup` 1, `update` 1, `source-queue` 1, `release-bundle` 4 and `production-b2-dubbing` 7.

The runner calls `unittest` without `-v`, so the log gives each command's skip count (`OK (skipped=N)`) but not the names of the skipped tests. The skips come from skip guards inside the suites. One example is the `release-bundle` suite's Windows-only installer tests, which need real Windows file handles. Skipped tests are not failures, and they are not evidence for the platforms they need.

## Scope
This run is the Linux, deterministic integration registry. It is not Windows Release, Release / Soak, real-media or GPU evidence; those lanes stay separate under #175. The desktop host's Rust unit tests are not part of this registry.
