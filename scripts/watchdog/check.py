from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any

from classifier import Check, PRSnapshot, classify_snapshot


ROOT = Path(__file__).resolve().parents[2]


def gh_json(*args: str) -> Any:
    result = subprocess.run(["gh", "api", *args], cwd=ROOT, check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def load_snapshot(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("prs"), list) or not isinstance(value.get("commits"), list):
        raise ValueError("snapshot must contain prs and commits arrays")
    return value


def snapshot_from_github(repo: str) -> dict[str, Any]:
    main = gh_json(f"repos/{repo}/branches/main")
    main_sha = main["commit"]["sha"]
    prs_raw = gh_json(f"repos/{repo}/pulls?state=open&per_page=100")
    prs: list[dict[str, Any]] = []
    for raw in prs_raw:
        head_sha = raw["head"]["sha"]
        checks_raw = gh_json(f"repos/{repo}/commits/{head_sha}/check-runs")
        checks = [{"name": item.get("name", ""), "status": item.get("status", ""), "conclusion": item.get("conclusion")} for item in checks_raw.get("check_runs", [])]
        prs.append({
            "number": raw["number"],
            "title": raw.get("title", ""),
            "body": raw.get("body") or "",
            "head_sha": head_sha,
            "base_sha": raw["base"]["sha"],
            "checks": checks,
        })
    commits_raw = gh_json(f"repos/{repo}/commits?sha=main&per_page=20")
    commits: list[dict[str, Any]] = []
    for raw in commits_raw:
        associated = gh_json(f"repos/{repo}/commits/{raw['sha']}/pulls")
        commits.append({"sha": raw["sha"], "message": raw.get("commit", {}).get("message", ""), "associated_pr": bool(associated)})
    return {"main_sha": main_sha, "main_protected": bool(main.get("protected")), "prs": prs, "commits": commits}


def to_prs(data: dict[str, Any]) -> list[PRSnapshot]:
    main_sha = str(data.get("main_sha", ""))
    result: list[PRSnapshot] = []
    for raw in data["prs"]:
        checks = tuple(Check(str(item.get("name", "")), str(item.get("status", "")), item.get("conclusion")) for item in raw.get("checks", []))
        result.append(PRSnapshot(int(raw["number"]), str(raw.get("title", "")), str(raw.get("body", "")), str(raw["head_sha"]), str(raw["base_sha"]), main_sha, checks))
    return result


def write_warning_issue(repo: str, warnings: list[dict[str, object]]) -> None:
    if not warnings:
        return
    body = "\n".join([
        "Automated DubFlow governance watchdog warning.",
        "",
        "This report is advisory and does not auto-merge, delete branches or revert commits.",
        "",
        "WARNING DATA",
        json.dumps(warnings, indent=2, sort_keys=True),
    ])
    existing = subprocess.run(["gh", "issue", "list", "--repo", repo, "--state", "open", "--search", "[watchdog] DubFlow governance warning in:title", "--json", "number"], cwd=ROOT, check=True, text=True, capture_output=True)
    found = json.loads(existing.stdout)
    if found:
        number = str(found[0]["number"])
        subprocess.run(["gh", "issue", "comment", number, "--repo", repo, "--body", body], cwd=ROOT, check=True)
    else:
        subprocess.run(["gh", "issue", "create", "--repo", repo, "--title", "[watchdog] DubFlow governance warning", "--body", body], cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--repo")
    parser.add_argument("--write-issue", action="store_true")
    args = parser.parse_args()
    if bool(args.snapshot) == bool(args.repo):
        parser.error("provide exactly one of --snapshot or --repo")
    data = load_snapshot(args.snapshot) if args.snapshot else snapshot_from_github(args.repo)
    warnings = classify_snapshot(
        prs=to_prs(data),
        commits=data["commits"],
        main_protected=bool(data.get("main_protected")),
        now=datetime.now(timezone.utc),
    )
    encoded = [warning.to_dict() for warning in warnings]
    print(json.dumps({"warning_count": len(encoded), "warnings": encoded}, indent=2, sort_keys=True))
    if args.write_issue and args.repo:
        write_warning_issue(args.repo, encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
