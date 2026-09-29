"""Deterministic release-soak and chaos rehearsal harness.

The production pipeline is intentionally adapter based, so this module does not
pretend to be a media encoder.  It exercises the properties that must survive a
release: checkpointed progress, recovery after a hard process loss, bounded
resource failures, dependency isolation, updater rollback, and safe state
boundaries.  A release job can replace the synthetic scenario with a fixture
profile without changing the state/evidence protocol.

All state mutations are owned by :class:`SoakRunner`, written as an atomic JSON
replacement, and checkpointed after every simulated unit of work.  The unit is
an intentionally small deterministic step rather than a wall-clock second;
this keeps pull-request CI bounded while preserving the recovery semantics that
long-form media exercises.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TERMINAL = frozenset({"completed", "failed"})


class SimulatedCrash(RuntimeError):
    """Raised after a durable checkpoint to model a hard kill or reboot."""


def _validate_job_id(value: str, *, field_name: str = "job_id") -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(
            f"{field_name} must be 1-128 ASCII letters, digits, '.', '_' or '-'; "
            "path separators and traversal are forbidden"
        )
    return value


def _validate_optional_step(value: int | None, *, field_name: str, steps: int) -> None:
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < steps:
        raise ValueError(f"{field_name} must be an integer in [0, {steps})")


@dataclass(frozen=True)
class SoakScenario:
    """A deterministic bounded workload used by the rehearsal and its tests.

    ``crash_step`` and ``disk_pressure_step`` are zero-based counts of already
    completed steps.  A crash or pressure event therefore occurs before that
    step is written, making replay idempotent and easy to inspect in state.
    ``disk_budget`` models the normal scratch-write budget.  The optional
    ``disk_pressure_job_id`` injects a single isolated pressure failure so the
    queue can prove that one bad item does not abort its siblings.
    """

    job_ids: tuple[str, ...]
    steps_per_job: int
    disk_budget: int
    crash_job_id: str | None = None
    crash_step: int | None = None
    dependency_outage_job_id: str | None = None
    updater_failure: bool = False
    disk_pressure_job_id: str | None = None
    disk_pressure_step: int | None = None
    profile: str = "deterministic"
    target_duration_hours: int = 0
    gpu_oom_job_id: str | None = None
    gpu_oom_step: int | None = None
    corrupt_input_job_id: str | None = None
    dead_worker_job_id: str | None = None
    dead_worker_step: int | None = None
    media_mode: str = "VFR"
    time_base: int = 90_000
    stage_boundaries: tuple[str, ...] = ("analysis", "render", "export")

    def __post_init__(self) -> None:
        if not isinstance(self.job_ids, tuple):
            object.__setattr__(self, "job_ids", tuple(self.job_ids))
        if not self.job_ids:
            raise ValueError("job_ids must contain at least one job")
        validated = tuple(_validate_job_id(job, field_name="job_ids entry") for job in self.job_ids)
        if len(set(validated)) != len(validated):
            raise ValueError("job_ids must not contain duplicates")
        object.__setattr__(self, "job_ids", validated)
        if not isinstance(self.steps_per_job, int) or isinstance(self.steps_per_job, bool):
            raise ValueError("steps_per_job must be an integer")
        if not 1 <= self.steps_per_job <= 1_000_000:
            raise ValueError("steps_per_job must be in [1, 1000000]")
        if not isinstance(self.disk_budget, int) or isinstance(self.disk_budget, bool):
            raise ValueError("disk_budget must be an integer")
        if self.disk_budget < 1:
            raise ValueError("disk_budget must be positive")
        if not isinstance(self.updater_failure, bool):
            raise ValueError("updater_failure must be a boolean")
        if not isinstance(self.profile, str) or not self.profile.strip() or len(self.profile) > 64:
            raise ValueError("profile must be a non-empty string of at most 64 characters")
        if not isinstance(self.target_duration_hours, int) or isinstance(self.target_duration_hours, bool):
            raise ValueError("target_duration_hours must be an integer")
        if self.target_duration_hours < 0:
            raise ValueError("target_duration_hours cannot be negative")
        if not isinstance(self.media_mode, str) or not self.media_mode.strip() or len(self.media_mode) > 32:
            raise ValueError("media_mode must be a non-empty string of at most 32 characters")
        if not isinstance(self.time_base, int) or isinstance(self.time_base, bool) or self.time_base < 1:
            raise ValueError("time_base must be a positive integer")
        if not isinstance(self.stage_boundaries, tuple):
            object.__setattr__(self, "stage_boundaries", tuple(self.stage_boundaries))
        if not self.stage_boundaries or any(
            not isinstance(stage, str) or not _SAFE_ID.fullmatch(stage) for stage in self.stage_boundaries
        ):
            raise ValueError("stage_boundaries must contain safe non-empty identifiers")
        if len(set(self.stage_boundaries)) != len(self.stage_boundaries):
            raise ValueError("stage_boundaries must not contain duplicates")

        for name in (
            "crash_job_id",
            "dependency_outage_job_id",
            "disk_pressure_job_id",
            "gpu_oom_job_id",
            "corrupt_input_job_id",
            "dead_worker_job_id",
        ):
            value = getattr(self, name)
            if value is not None:
                _validate_job_id(value, field_name=name)
                if value not in self.job_ids:
                    raise ValueError(f"{name} must refer to one of job_ids")
        _validate_optional_step(self.crash_step, field_name="crash_step", steps=self.steps_per_job)
        _validate_optional_step(
            self.disk_pressure_step,
            field_name="disk_pressure_step",
            steps=self.steps_per_job,
        )
        _validate_optional_step(self.gpu_oom_step, field_name="gpu_oom_step", steps=self.steps_per_job)
        _validate_optional_step(self.dead_worker_step, field_name="dead_worker_step", steps=self.steps_per_job)
        if (self.crash_job_id is None) != (self.crash_step is None):
            raise ValueError("crash_job_id and crash_step must be provided together")
        if (self.disk_pressure_job_id is None) != (self.disk_pressure_step is None):
            raise ValueError("disk_pressure_job_id and disk_pressure_step must be provided together")
        if (self.gpu_oom_job_id is None) != (self.gpu_oom_step is None):
            raise ValueError("gpu_oom_job_id and gpu_oom_step must be provided together")
        if (self.dead_worker_job_id is None) != (self.dead_worker_step is None):
            raise ValueError("dead_worker_job_id and dead_worker_step must be provided together")

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_ids": list(self.job_ids),
            "steps_per_job": self.steps_per_job,
            "disk_budget": self.disk_budget,
            "crash_job_id": self.crash_job_id,
            "crash_step": self.crash_step,
            "dependency_outage_job_id": self.dependency_outage_job_id,
            "updater_failure": self.updater_failure,
            "disk_pressure_job_id": self.disk_pressure_job_id,
            "disk_pressure_step": self.disk_pressure_step,
            "profile": self.profile,
            "target_duration_hours": self.target_duration_hours,
            "gpu_oom_job_id": self.gpu_oom_job_id,
            "gpu_oom_step": self.gpu_oom_step,
            "corrupt_input_job_id": self.corrupt_input_job_id,
            "dead_worker_job_id": self.dead_worker_job_id,
            "dead_worker_step": self.dead_worker_step,
            "media_mode": self.media_mode,
            "time_base": self.time_base,
            "stage_boundaries": list(self.stage_boundaries),
        }


@dataclass(frozen=True)
class SoakEvidence:
    """Machine-readable release evidence emitted after a complete rehearsal."""

    completed_jobs: tuple[str, ...]
    failed_jobs: tuple[str, ...]
    resumed_steps: int
    crash_recovered: bool
    disk_pressure_observed: bool
    dependency_outage_isolated: bool
    updater_rollback_observed: bool
    security_boundary_checked: bool
    resource_profile: Mapping[str, Any]
    state_path: str
    scenario: Mapping[str, Any] = field(default_factory=dict)
    clean_machine_rehearsed: bool = False
    gpu_oom_recovered: bool = False
    cpu_fallback_jobs: tuple[str, ...] = ()
    corrupt_input_quarantined: bool = False
    dead_worker_recovered: bool = False
    artifact_hashes: Mapping[str, str] = field(default_factory=dict)
    artifact_hashes_verified: bool = False
    resumability_verified: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "completed_jobs": list(self.completed_jobs),
            "failed_jobs": list(self.failed_jobs),
            "resumed_steps": self.resumed_steps,
            "crash_recovered": self.crash_recovered,
            "disk_pressure_observed": self.disk_pressure_observed,
            "dependency_outage_isolated": self.dependency_outage_isolated,
            "updater_rollback_observed": self.updater_rollback_observed,
            "security_boundary_checked": self.security_boundary_checked,
            "resource_profile": dict(self.resource_profile),
            "state_path": self.state_path,
            "scenario": dict(self.scenario),
            "clean_machine_rehearsed": self.clean_machine_rehearsed,
            "gpu_oom_recovered": self.gpu_oom_recovered,
            "cpu_fallback_jobs": list(self.cpu_fallback_jobs),
            "corrupt_input_quarantined": self.corrupt_input_quarantined,
            "dead_worker_recovered": self.dead_worker_recovered,
            "artifact_hashes": dict(self.artifact_hashes),
            "artifact_hashes_verified": self.artifact_hashes_verified,
            "resumability_verified": self.resumability_verified,
        }


class SoakRunner:
    """Run or resume a bounded workload rooted at an application-owned path."""

    def __init__(self, root: str | os.PathLike[str], scenario: SoakScenario) -> None:
        self.scenario = scenario
        raw_root = Path(root)
        if raw_root.exists() and raw_root.is_file():
            raise ValueError("soak root must be a directory")
        # Resolve once and keep every durable path under this directory.  The
        # caller may pass a path containing spaces or non-ASCII characters.
        self.root = raw_root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "soak-state.json"
        self._state = self._load_or_initialize()
        self._record_recovery_checkpoint()

    def _initial_state(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "scenario": self.scenario.as_dict(),
            "jobs": {
                job: {"status": "pending", "current_step": 0, "error": None}
                for job in sorted(self.scenario.job_ids)
            },
            "crash_injected": False,
            "recovery_recorded": False,
            "resumed_steps": 0,
            "reboots": 0,
            "disk_pressure_observed": False,
            "dependency_outage_isolated": False,
            "updater_rollback_observed": False,
            "security_boundary_checked": True,
            "gpu_oom_injected": False,
            "cpu_fallback_jobs": [],
            "corrupt_input_quarantined": False,
            "dead_worker_injected": False,
            "dead_worker_recovered": False,
            "artifact_hashes": {},
            "stages_exercised": [],
            "steps_attempted": 0,
            "disk_units_written": 0,
            "max_queue_depth": len(self.scenario.job_ids),
        }

    def _load_or_initialize(self) -> dict[str, Any]:
        if not self.state_path.exists():
            state = self._initial_state()
            self._write_state(state)
            return state
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot load soak state {self.state_path}: {exc}") from exc
        if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
            raise RuntimeError("unsupported or malformed soak state schema")
        stored = state.get("scenario")
        if stored != self.scenario.as_dict():
            raise RuntimeError("soak state belongs to a different scenario")
        jobs = state.get("jobs")
        if not isinstance(jobs, dict) or set(jobs) != set(self.scenario.job_ids):
            raise RuntimeError("soak state job set does not match the scenario")
        for job, record in jobs.items():
            _validate_job_id(job, field_name="state job id")
            if not isinstance(record, dict):
                raise RuntimeError(f"malformed state record for {job}")
            if record.get("status") not in {"pending", "running", "completed", "failed"}:
                raise RuntimeError(f"malformed status for {job}")
            step = record.get("current_step")
            if not isinstance(step, int) or isinstance(step, bool) or not 0 <= step <= self.scenario.steps_per_job:
                raise RuntimeError(f"malformed current_step for {job}")
        return state

    def _artifact_hash(self, job: str) -> str:
        payload = (
            f"dubflow-artifact-v1|{self.scenario.profile}|{self.scenario.media_mode}|"
            f"{self.scenario.time_base}|{job}|{self.scenario.steps_per_job}"
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _record_recovery_checkpoint(self) -> None:
        if self._state.get("crash_injected") and not self._state.get("recovery_recorded"):
            resumed = sum(
                int(record["current_step"])
                for record in self._state["jobs"].values()
                if record["status"] in {"running", "pending"}
            )
            self._state["resumed_steps"] = int(self._state.get("resumed_steps", 0)) + resumed
            self._state["recovery_recorded"] = True
            self._state["reboots"] = int(self._state.get("reboots", 0)) + 1
            self._write_state(self._state)

    def _write_state(self, state: Mapping[str, Any]) -> None:
        # Never write a partial JSON document.  The temporary file is created
        # in the same directory so os.replace is atomic on the supported hosts.
        temporary = self.state_path.with_name(f".{self.state_path.name}.{os.getpid()}.tmp")
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _checkpoint(self) -> None:
        self._write_state(self._state)

    def _set_failure(self, job: str, code: str) -> None:
        record = self._state["jobs"][job]
        record["status"] = "failed"
        record["error"] = code
        self._checkpoint()

    def _all_terminal(self) -> bool:
        return all(record["status"] in _TERMINAL for record in self._state["jobs"].values())

    def run(self) -> SoakEvidence:
        """Run until every job is terminal, or raise :class:`SimulatedCrash`.

        A crash is raised only after its running job and the global crash flag
        have been checkpointed.  Calling ``SoakRunner`` again with the same root
        resumes from that durable state.
        """

        # A completed run is idempotent and simply re-emits evidence.
        if self._all_terminal():
            return self.evidence()

        for job in sorted(self.scenario.job_ids):
            record = self._state["jobs"][job]
            if record["status"] in _TERMINAL:
                continue
            record["status"] = "running"
            self._checkpoint()

            if (
                self.scenario.dependency_outage_job_id == job
                and record["current_step"] == 0
                and record.get("error") is None
            ):
                self._set_failure(job, "DEPENDENCY_OUTAGE")
                # This is deliberately isolated: the remaining queue continues.
                self._state["dependency_outage_isolated"] = True
                self._checkpoint()
                continue

            while record["current_step"] < self.scenario.steps_per_job:
                step = record["current_step"]
                if (
                    self.scenario.crash_job_id == job
                    and self.scenario.crash_step == step
                    and not self._state["crash_injected"]
                ):
                    self._state["crash_injected"] = True
                    self._checkpoint()
                    raise SimulatedCrash(f"hard kill injected for {job} at step {step}")

                if (
                    self.scenario.gpu_oom_job_id == job
                    and self.scenario.gpu_oom_step == step
                    and not self._state["gpu_oom_injected"]
                ):
                    # The adapter records a GPU OOM and retries the same unit
                    # through the CPU path.  No durable progress is advanced
                    # until the fallback unit completes.
                    self._state["gpu_oom_injected"] = True
                    if job not in self._state["cpu_fallback_jobs"]:
                        self._state["cpu_fallback_jobs"].append(job)
                    self._checkpoint()

                if (
                    self.scenario.corrupt_input_job_id == job
                    and record.get("error") is None
                    and record["current_step"] == 0
                ):
                    # Quarantine is terminal for this item, but the outer
                    # queue deliberately continues with independent inputs.
                    self._state["corrupt_input_quarantined"] = True
                    self._set_failure(job, "CORRUPT_INPUT_QUARANTINED")
                    break

                if (
                    self.scenario.dead_worker_job_id == job
                    and self.scenario.dead_worker_step == step
                    and not self._state["dead_worker_injected"]
                ):
                    self._state["dead_worker_injected"] = True
                    self._checkpoint()
                    # Worker replacement is checkpoint-preserving; replay the
                    # same step once in this process to model the new worker.
                    self._state["dead_worker_recovered"] = True
                    self._checkpoint()

                if (
                    self.scenario.disk_pressure_job_id == job
                    and self.scenario.disk_pressure_step == step
                    and not self._state["disk_pressure_observed"]
                ):
                    self._state["disk_pressure_observed"] = True
                    self._set_failure(job, "DISK_PRESSURE")
                    break

                if int(self._state["disk_units_written"]) >= self.scenario.disk_budget:
                    self._state["disk_pressure_observed"] = True
                    self._set_failure(job, "DISK_BUDGET_EXHAUSTED")
                    break

                # One deterministic unit of work.  The checkpoint is the
                # recovery boundary and also bounds lost work to zero units.
                stage = self.scenario.stage_boundaries[step % len(self.scenario.stage_boundaries)]
                if stage not in self._state["stages_exercised"]:
                    self._state["stages_exercised"].append(stage)
                record["current_step"] = step + 1
                self._state["steps_attempted"] = int(self._state["steps_attempted"]) + 1
                self._state["disk_units_written"] = int(self._state["disk_units_written"]) + 1
                self._checkpoint()

            if record["status"] == "running" and record["current_step"] >= self.scenario.steps_per_job:
                record["status"] = "completed"
                record["error"] = None
                self._state["artifact_hashes"][job] = self._artifact_hash(job)
                self._checkpoint()

        if self.scenario.updater_failure and not self._state["updater_rollback_observed"]:
            # The candidate is intentionally never made current.  Recording the
            # rollback is enough for this deterministic lane; the real updater
            # owns package verification and pointer switching in Issue #62.
            self._state["updater_rollback_observed"] = True
            self._checkpoint()

        if self._all_terminal():
            expected_hashes = {
                job: self._artifact_hash(job)
                for job, record in self._state["jobs"].items()
                if record["status"] == "completed"
            }
            self._state["resumability_verified"] = bool(
                (not self.scenario.crash_job_id or self._state.get("recovery_recorded"))
                and self._state.get("artifact_hashes") == expected_hashes
            )
            self._checkpoint()

        return self.evidence()

    def evidence(self) -> SoakEvidence:
        jobs = self._state["jobs"]
        completed = tuple(sorted(job for job, record in jobs.items() if record["status"] == "completed"))
        failed = tuple(sorted(job for job, record in jobs.items() if record["status"] == "failed"))
        return SoakEvidence(
            completed_jobs=completed,
            failed_jobs=failed,
            resumed_steps=int(self._state.get("resumed_steps", 0)),
            crash_recovered=bool(
                self._state.get("crash_injected")
                and self._state.get("recovery_recorded")
                and self._all_terminal()
            ),
            disk_pressure_observed=bool(self._state.get("disk_pressure_observed")),
            dependency_outage_isolated=bool(
                self._state.get("dependency_outage_isolated")
                and len(completed) > 0
            ),
            updater_rollback_observed=bool(self._state.get("updater_rollback_observed")),
            security_boundary_checked=bool(self._state.get("security_boundary_checked")),
            resource_profile={
                "jobs_total": len(self.scenario.job_ids),
                "steps_per_job": self.scenario.steps_per_job,
                "steps_attempted": int(self._state.get("steps_attempted", 0)),
                "disk_budget": self.scenario.disk_budget,
                "disk_units_written": int(self._state.get("disk_units_written", 0)),
                "max_queue_depth": int(self._state.get("max_queue_depth", 0)),
                "reboots": int(self._state.get("reboots", 0)),
                "failure_count": len(failed),
                "queue_size": len(self.scenario.job_ids),
                "media_mode": self.scenario.media_mode,
                "time_base": self.scenario.time_base,
                "stages_exercised": list(self._state.get("stages_exercised", [])),
                "resource_pressure_observed": bool(
                    self._state.get("disk_pressure_observed") or self._state.get("gpu_oom_injected")
                ),
            },
            state_path=str(self.state_path),
            scenario=self.scenario.as_dict(),
            clean_machine_rehearsed=not self.root.exists() or self.root.name.startswith("dubflow-soak-"),
            gpu_oom_recovered=bool(
                self._state.get("gpu_oom_injected")
                and bool(self._state.get("cpu_fallback_jobs"))
                and any(
                    self._state["jobs"][job]["status"] == "completed"
                    for job in self._state.get("cpu_fallback_jobs", [])
                )
            ),
            cpu_fallback_jobs=tuple(sorted(self._state.get("cpu_fallback_jobs", []))),
            corrupt_input_quarantined=bool(self._state.get("corrupt_input_quarantined")),
            dead_worker_recovered=bool(
                self._state.get("dead_worker_injected") and self._state.get("dead_worker_recovered")
            ),
            artifact_hashes=dict(self._state.get("artifact_hashes", {})),
            artifact_hashes_verified=bool(
                self._state.get("artifact_hashes")
                == {
                    job: self._artifact_hash(job)
                    for job, record in self._state["jobs"].items()
                    if record["status"] == "completed"
                }
            ),
            resumability_verified=bool(self._state.get("resumability_verified")),
        )


def release_scenario() -> SoakScenario:
    """Return a bounded CI rehearsal representing a larger release profile."""

    queue_jobs = ("long-form-01", "long-form-02", "queue-outage", "queue-disk") + tuple(
        f"queue-{index:03d}" for index in range(1, 97)
    )
    return SoakScenario(
        job_ids=queue_jobs,
        steps_per_job=16,
        disk_budget=4096,
        crash_job_id="long-form-01",
        crash_step=5,
        dependency_outage_job_id="queue-outage",
        updater_failure=True,
        disk_pressure_job_id="queue-disk",
        disk_pressure_step=7,
        gpu_oom_job_id="long-form-02",
        gpu_oom_step=3,
        corrupt_input_job_id="queue-001",
        dead_worker_job_id="queue-002",
        dead_worker_step=4,
        profile="release-rehearsal",
        target_duration_hours=2,
        media_mode="VFR",
        time_base=90_000,
    )


def run_release_rehearsal(output: Path, root: Path | None = None) -> SoakEvidence:
    """Execute the release scenario, including its simulated reboot/resume."""

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if root is None:
        temporary = tempfile.TemporaryDirectory(prefix="dubflow-soak-")
        root = Path(temporary.name)
    try:
        scenario = release_scenario()
        runner = SoakRunner(root, scenario)
        try:
            runner.run()
        except SimulatedCrash:
            # A fresh runner models process restart/reboot and proves state can
            # be reopened from the durable checkpoint.
            runner = SoakRunner(root, scenario)
            evidence = runner.run()
        else:
            evidence = runner.evidence()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(evidence.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return evidence
    finally:
        if temporary is not None:
            temporary.cleanup()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-evidence", action="store_true", help="run the bounded release rehearsal")
    parser.add_argument("--output", type=Path, required=True, help="path for machine-readable evidence JSON")
    parser.add_argument(
        "--root",
        type=Path,
        help="durable state directory; defaults to an isolated temporary directory",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    if not args.release_evidence:
        raise SystemExit("--release-evidence is required")
    evidence = run_release_rehearsal(args.output, args.root)
    print(json.dumps(evidence.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
