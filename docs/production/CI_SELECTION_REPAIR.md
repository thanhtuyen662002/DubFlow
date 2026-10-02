# Integration selection repair (#186)

## Scope and implementation

Baseline: `9271c2552331d43686940b939e8b1ad70bdc9863`. The repair owns only `scripts/ci/run_integration.py`, `tests/ci/test_integration_selection.py`, `.github/workflows/pr-integration.yml` and this note. It does not own product worker/model paths, release signing, or any other active PR branch.

The selector now reads NUL-delimited Git paths as bytes and uses filesystem decoding after splitting, preserving Unicode, control characters, whitespace and POSIX undecodable names. Disabling rename compression includes both the source and destination component of a move. Missing, zero or unresolvable bases retain the existing conservative all-component fallback.

Changes to Integration control code/registry, selector regression tests, governance validation, or fast/integration workflows select all registered deterministic components. Ordinary unrelated documents retain selective behavior. GPU/live-source/long-soak workflows are not added as universal gates. The Integration workflow runs the selector regressions before the component registry runner, with unchanged permissions and timeout.

## Executed evidence (2026-10-02)

The focused source snapshot was read through GitHub and tested on Linux with Python 3.13.5 and Git 2.47.3. This environment could not clone the repository because network/DNS was unavailable, and it had no Cargo. The snapshot is not a full production checkout.

Original source Git blob identities were verified before modification:
- `scripts/ci/run_integration.py`: `1a0a6a822101f3265d5cc0d417004e22504139ee`.
- `scripts/ci/component_registry.py` (unchanged): `43fc4cfa6fd4955d336af2cdf92a9b18c552619b`.
- `.github/workflows/pr-integration.yml`: `f7027f4571d73336fee695f529cb7ce44a15f2fb`.

`python -m unittest discover -s tests/ci -p 'test_*.py' -v` failed against the original selector and passed all 21 tests after the repair. The baseline emitted 14 failure records including subtests; these are not 14 separate test methods. Tests use real temporary local Git repositories and never access a network or model.

`python scripts/ci/component_registry.py` self-tests and `python -m compileall -q scripts/ci tests/ci` passed. The workflow was parsed and its read-only permissions, 45-minute timeout and regression-before-registry ordering were checked. The runner diff passed the Git whitespace check.

## Remaining gates

Full-repository Integration, hosted Python 3.12 checks, Rust/desktop builds, Windows execution, model/audio quality and release qualification were not run locally. Focused test success does not substitute for these. Record actual hosted run IDs and the final source HEAD/base in PR #187, keep it Draft while required CI/review is pending, and do not bypass inherited failures revealed by broader selection. Re-read live main before any merge; a moved base invalidates prior merge readiness.
