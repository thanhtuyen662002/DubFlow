# Integration selection repair (#186)

## Scope

Repair the deterministic Integration selector before relying on it to advance the production PR queue. The baseline inspected is `9271c2552331d43686940b939e8b1ad70bdc9863`.

Owned paths are `scripts/ci/run_integration.py`, `tests/ci/test_integration_selection.py`, `.github/workflows/pr-integration.yml` and this note. No product worker, voice/model, release/signing, or other active PR branch is owned by this task.

## Failure cases

Git's quoted line-based filename output can prevent a Unicode or control-character path from matching its component root. Rename detection can omit the former component path. Changes to the selector, registry or Integration workflow must invalidate selection globally rather than select no components.

## Validation plan

Use temporary local Git repositories and Python unittest to exercise NUL-delimited path handling, both sides of renames, deletions, missing/invalid base fallback, namespace boundaries and CI-control invalidation. Run the selector regression suite explicitly before selective Integration. Preserve unrelated-doc selectivity and keep GPU/live-site/long-soak lanes separate.

This is a claim checkpoint, not test or release evidence. Exact-head/base results belong in the linked PR after execution. The PR must remain Draft until acceptance, hosted required CI and review are satisfied.
