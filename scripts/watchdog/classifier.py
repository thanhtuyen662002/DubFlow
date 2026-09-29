from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class Lease:
    issue: int
    owner: str
    heartbeat: datetime
    tested_base_sha: str
    conflict_domains: frozenset[str]
    expected_paths: tuple[str, ...]


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    conclusion: str | None


@dataclass(frozen=True)
class PRSnapshot:
    number: int
    title: str
    body: str
    head_sha: str
    base_sha: str
    main_sha: str
    checks: tuple[Check, ...]


@dataclass(frozen=True)
class Warning:
    code: str
    subject: str
    detail: str
    pr_numbers: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "subject": self.subject, "detail": self.detail, "pr_numbers": list(self.pr_numbers)}


def _field(body: str, name: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(name)}:\s*(.+?)\s*$", body)
    return match.group(1).strip() if match else None


def parse_lease(body: str) -> Lease:
    if not isinstance(body, str) or "DUBFLOW_PR_V1" not in body.splitlines():
        raise ValueError("missing DUBFLOW_PR_V1 marker")
    issue_text = _field(body, "Issue")
    owner = _field(body, "Lease-Owner")
    heartbeat_text = _field(body, "Lease-Heartbeat")
    tested_base = _field(body, "Tested-Base-SHA")
    conflicts = _field(body, "Conflict-Domains")
    paths = _field(body, "Expected-Paths")
    if not issue_text or not issue_text.startswith("#") or not issue_text[1:].isdigit():
        raise ValueError("Issue must be a numeric #id")
    if not owner or not heartbeat_text or not tested_base or not conflicts or not paths:
        raise ValueError("lease metadata is incomplete")
    try:
        heartbeat = datetime.fromisoformat(heartbeat_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Lease-Heartbeat must be ISO-8601") from exc
    if heartbeat.tzinfo is None:
        raise ValueError("Lease-Heartbeat must include a timezone")
    if not re.fullmatch(r"[0-9a-f]{40}", tested_base):
        raise ValueError("Tested-Base-SHA must be a lowercase Git SHA")
    conflict_domains = frozenset(item.strip() for item in conflicts.split(",") if item.strip())
    expected_paths = tuple(item.strip() for item in paths.split(",") if item.strip())
    if not conflict_domains or not expected_paths:
        raise ValueError("lease conflict/path metadata cannot be empty")
    return Lease(int(issue_text[1:]), owner, heartbeat.astimezone(timezone.utc), tested_base, conflict_domains, expected_paths)


def classify_pr(snapshot: PRSnapshot, *, now: datetime, stale_after: timedelta = timedelta(hours=2), required_checks: Sequence[str] = ("governance",)) -> list[Warning]:
    warnings: list[Warning] = []
    try:
        lease = parse_lease(snapshot.body)
    except ValueError as exc:
        return [Warning("INVALID_LEASE", f"PR #{snapshot.number}", str(exc), (snapshot.number,))]
    if now.astimezone(timezone.utc) - lease.heartbeat > stale_after:
        warnings.append(Warning("STALE_HEARTBEAT", f"PR #{snapshot.number}", f"lease heartbeat is older than {stale_after}", (snapshot.number,)))
    if lease.tested_base_sha != snapshot.main_sha or snapshot.base_sha != snapshot.main_sha:
        warnings.append(Warning("STALE_BASE", f"PR #{snapshot.number}", f"tested base {lease.tested_base_sha[:12]} and PR base {snapshot.base_sha[:12]} do not match main {snapshot.main_sha[:12]}", (snapshot.number,)))
    checks = {check.name: check for check in snapshot.checks}
    for name in required_checks:
        check = checks.get(name)
        if check is None or check.status != "completed":
            warnings.append(Warning("WAITING_CI", f"PR #{snapshot.number}", f"required check {name!r} has not completed", (snapshot.number,)))
        elif (check.conclusion or "").lower() != "success":
            warnings.append(Warning("FAILED_CI", f"PR #{snapshot.number}", f"required check {name!r} concluded {(check.conclusion or 'unknown')!r}", (snapshot.number,)))
    return warnings


def classify_claims(prs: Sequence[PRSnapshot]) -> list[Warning]:
    by_issue: dict[int, list[int]] = {}
    by_domain: dict[str, list[int]] = {}
    warnings: list[Warning] = []
    for pr in prs:
        try:
            lease = parse_lease(pr.body)
        except ValueError:
            continue
        by_issue.setdefault(lease.issue, []).append(pr.number)
        for domain in lease.conflict_domains:
            by_domain.setdefault(domain, []).append(pr.number)
    for issue, numbers in by_issue.items():
        if len(numbers) > 1:
            warnings.append(Warning("DUPLICATE_CLAIM", f"Issue #{issue}", f"multiple live leases: {', '.join(f'PR #{number}' for number in numbers)}", tuple(sorted(numbers))))
    for domain, numbers in by_domain.items():
        if len(numbers) > 1:
            warnings.append(Warning("CONFLICT_OVERLAP", domain, f"live leases overlap conflict domain: {', '.join(f'PR #{number}' for number in numbers)}", tuple(sorted(numbers))))
    return warnings


def classify_main_integrity(commits: Iterable[Mapping[str, object]]) -> list[Warning]:
    warnings: list[Warning] = []
    for commit in commits:
        sha = str(commit.get("sha", ""))
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or bool(commit.get("associated_pr")):
            continue
        message = str(commit.get("message", "")).splitlines()[0][:256]
        warnings.append(Warning("DIRECT_MAIN_COMMIT", sha[:12], f"main commit has no associated merged PR: {message}"))
    return warnings


def classify_snapshot(*, prs: Sequence[PRSnapshot], commits: Iterable[Mapping[str, object]], main_protected: bool, now: datetime, required_checks: Sequence[str] = ("governance",)) -> list[Warning]:
    warnings: list[Warning] = []
    if not main_protected:
        warnings.append(Warning("MAIN_UNPROTECTED", "main", "main has no active branch protection or ruleset"))
    for pr in prs:
        warnings.extend(classify_pr(pr, now=now, required_checks=required_checks))
    warnings.extend(classify_claims(prs))
    warnings.extend(classify_main_integrity(commits))
    return warnings
