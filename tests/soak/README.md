# Release soak and chaos rehearsal

`runner.py` is a deterministic rehearsal for the failure modes that are hard
to cover in a short pull-request clip: VFR long-form progress, 100/500-item
queues, hard-kill/reboot recovery at stage boundaries, disk pressure, GPU OOM
with CPU fallback, dependency outages, corrupt-input quarantine, dead-worker
replacement, updater rollback, and output hash verification.  It writes a
versioned `soak-state.json` after every work unit using an atomic replacement,
so a second process can resume without losing a completed unit or corrupting
JSON.  Job identifiers are validated before they can be used in state, which
also exercises the application-owned path boundary.

The CI profile uses 100 synthetic queue items and 16 small units per item.  The unit is a
deterministic checkpoint, not a wall-clock second; this keeps CI bounded while
preserving the same resume and isolation invariants needed by a two-to-six-hour
VFR media run.  The test suite also runs a 500-item one-unit queue.  A release
operator can point the runner at a fixture-backed profile with the same evidence
schema when real long-form media is available.

Run the rehearsal locally:

```powershell
python tests/soak/runner.py --release-evidence --output .\artifacts\release-evidence.json
python -m unittest discover -s tests\chaos -p "test_*.py"
```

The evidence fields are deliberately reviewable: `crash_recovered`,
`resumed_steps`, `disk_pressure_observed`, `dependency_outage_isolated`,
`updater_rollback_observed`, `gpu_oom_recovered`,
`corrupt_input_quarantined`, `dead_worker_recovered`,
`artifact_hashes_verified`, `resumability_verified`,
`security_boundary_checked`, terminal job lists, and a resource profile.  The
GPU event is a deterministic adapter simulation; a real GPU OOM and clean-machine
run still belong in the scheduled/manual release lane before calling a build
one-click ready.
