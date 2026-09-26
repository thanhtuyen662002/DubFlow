from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

SCHEMA = "DUBFLOW_TASK_V1"
REQUIRED_KEYS = (
    "Hard-Dependencies",
    "Soft-Dependencies",
    "Conflict-Domains",
    "Expected-Paths",
    "Required-CI",
)

@dataclass(frozen=True)
class TaskMetadata:
    hard_dependencies: tuple[int, ...]
    soft_dependencies: tuple[int, ...]
    conflict_domains: tuple[str, ...]
    expected_paths: tuple[str, ...]
    required_ci: str

def _csv(value: str) -> tuple[str, ...]:
    value = value.strip()
    if not value or value.lower() == "none":
        return ()
    return tuple(x.strip() for x in value.split(",") if x.strip())

def _deps(value: str) -> tuple[int, ...]:
    items = _csv(value)
    deps: list[int] = []
    for item in items:
        m = re.fullmatch(r"#?(\d+)", item)
        if not m:
            raise ValueError(f"invalid dependency token: {item!r}")
        deps.append(int(m.group(1)))
    if len(set(deps)) != len(deps):
        raise ValueError("duplicate dependency")
    return tuple(deps)

def parse_task_metadata(body: str) -> TaskMetadata:
    lines = body.replace("\r\n", "\n").split("\n")
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == SCHEMA)
    except StopIteration as exc:
        raise ValueError(f"missing {SCHEMA}") from exc

    values: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if not line.strip():
            break
        if ":" not in line:
            raise ValueError(f"invalid metadata line: {line!r}")
        key, value = line.split(":", 1)
        key = key.strip()
        if key in values:
            raise ValueError(f"duplicate metadata key: {key}")
        values[key] = value.strip()

    missing = [k for k in REQUIRED_KEYS if k not in values]
    if missing:
        raise ValueError("missing metadata keys: " + ", ".join(missing))

    hard = _deps(values["Hard-Dependencies"])
    soft = _deps(values["Soft-Dependencies"])
    overlap = set(hard) & set(soft)
    if overlap:
        raise ValueError(f"dependency cannot be both hard and soft: {sorted(overlap)}")

    return TaskMetadata(
        hard_dependencies=hard,
        soft_dependencies=soft,
        conflict_domains=_csv(values["Conflict-Domains"]),
        expected_paths=_csv(values["Expected-Paths"]),
        required_ci=values["Required-CI"].strip(),
    )

def validate_hard_graph(
    tasks: dict[int, TaskMetadata],
    epic_ids: Iterable[int] = (),
) -> list[str]:
    errors: list[str] = []
    epics = set(epic_ids)

    for issue, meta in tasks.items():
        if issue in meta.hard_dependencies:
            errors.append(f"#{issue} hard-depends on itself")
        for dep in meta.hard_dependencies:
            if dep in epics:
                errors.append(f"#{issue} hard-depends on Epic #{dep}")

    visiting: set[int] = set()
    visited: set[int] = set()

    def dfs(node: int, stack: list[int]) -> None:
        if node in visited:
            return
        if node in visiting:
            try:
                i = stack.index(node)
                cycle = stack[i:] + [node]
            except ValueError:
                cycle = stack + [node]
            errors.append("hard dependency cycle: " + " -> ".join(f"#{x}" for x in cycle))
            return

        visiting.add(node)
        stack.append(node)
        meta = tasks.get(node)
        if meta:
            for dep in meta.hard_dependencies:
                if dep in tasks:
                    dfs(dep, stack)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in tasks:
        dfs(node, [])

    return sorted(set(errors))

def run_self_tests() -> None:
    ok = """DUBFLOW_TASK_V1
Hard-Dependencies: #3,#4
Soft-Dependencies: #6
Conflict-Domains: timeline,worker-protocol
Expected-Paths: tests/integration/**,crates/example/**
Required-CI: PR Fast + Integration

## Outcome
fixture
"""
    meta = parse_task_metadata(ok)
    assert meta.hard_dependencies == (3, 4)
    assert meta.soft_dependencies == (6,)
    assert meta.conflict_domains == ("timeline", "worker-protocol")

    try:
        parse_task_metadata(ok.replace("Soft-Dependencies: #6", "Soft-Dependencies: #4"))
        raise AssertionError("hard/soft overlap was not rejected")
    except ValueError:
        pass

    tasks = {
        10: TaskMetadata((11,), (), (), (), "PR Fast"),
        11: TaskMetadata((12,), (), (), (), "PR Fast"),
        12: TaskMetadata((10,), (), (), (), "PR Fast"),
    }
    assert any("cycle" in e for e in validate_hard_graph(tasks))

    no_hard_cycle = {
        20: TaskMetadata((), (21,), (), (), "PR Fast"),
        21: TaskMetadata((), (20,), (), (), "PR Fast"),
    }
    assert not validate_hard_graph(no_hard_cycle)

    epic_edge = {30: TaskMetadata((2,), (), (), (), "PR Fast")}
    assert validate_hard_graph(epic_edge, epic_ids={2}) == ["#30 hard-depends on Epic #2"]

if __name__ == "__main__":
    run_self_tests()
    print("Issue graph metadata self-tests passed.")
