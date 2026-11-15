DUBFLOW_PR_V1
Issue: #848
Lease-Owner: Claude/issue-848-tests-package-shadow
Lease-Heartbeat: 2026-11-15T10:07:00Z
Tested-Base-SHA: 1d9e996b97da256e82dec41f0447553cf4a308c3
Depends-On: none
Conflict-Domains: test-harness
Expected-Paths: .claims/issue-848-tests-package-shadow.md,tests/__init__.py,tests/ci/test_tests_package_shadow.py
Required-CI: PR Fast + PR Integration

## Scope
Owner sprint directive 2026-11-15 09:41Z: fix how tests are imported on the packaged Python **without changing the product runtime**.
So this PR does not prune `site-packages/tests` in release.yml, which the issue proposed. Instead it makes the repository's `tests` a regular package, so the repository root (first on sys.path) wins over argostranslate's bundled `tests`.
