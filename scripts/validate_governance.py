from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
required = [
    "README.md",
    "AGENTS.md",
    "docs/PRODUCT_SYSTEM_DESIGN.md",
    "docs/RISK_REGISTER.md",
    "docs/DEEP_RED_TEAM_AUDIT.md",
    "docs/ENGINEERING_EXECUTION_PROTOCOL.md",
    "docs/PARALLEL_EXECUTION.md",
    "docs/PROJECT_STATE.yaml",
    "docs/ROADMAP.md",
    "docs/AUDIT_BOOTSTRAP.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/workflows/pr-integration.yml",
    "scripts/ci/component_registry.json",
    "scripts/ci/component_registry.py",
    "scripts/ci/run_integration.py",
    "docs/adr/ADR-0001-local-first-and-time.md",
]
missing = [p for p in required if not (ROOT / p).is_file()]
if missing:
    print("Missing required files:")
    for p in missing:
        print(" -", p)
    sys.exit(1)

agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
for invariant in [
    "CapCut is an adapter",
    "OCR alone never defines dialogue semantics",
    "Speaker diarization never equals visual-character identity",
    "All expensive stages are chunkable/checkpointable/resumable",
]:
    if invariant not in agents:
        print("Missing architecture invariant:", invariant)
        sys.exit(1)

state = (ROOT / "docs/PROJECT_STATE.yaml").read_text(encoding="utf-8")
for key in [
    "source_of_truth:",
    "phase:",
    "product_foundation_implemented:",
    "critical_path:",
    "work_graph_source:",
    "roadmap:",
    "parallel_execution:",
    "direct_main_push_allowed:",
    "ci_infrastructure_issue:",
]:
    if key not in state:
        print("Missing project state key:", key)
        sys.exit(1)

if re.search(r"direct_main_push_allowed:\s*true", state, re.I):
    print("Direct main push must not be enabled by project state")
    sys.exit(1)

if re.search(r"product_foundation_implemented:\s*true", state, re.I):
    product_roots = [ROOT / "apps", ROOT / "crates", ROOT / "engine"]
    if not any(p.exists() for p in product_roots):
        print("Project state claims product foundation is implemented but no product code roots exist")
        sys.exit(1)

deprecated_keys = [
    "independent_claimable_lanes:",
    "critical_contract_tasks:",
]
for key in deprecated_keys:
    if key in state:
        print("Deprecated duplicated work inventory in PROJECT_STATE.yaml:", key)
        sys.exit(1)

roadmap = (ROOT / "docs/ROADMAP.md").read_text(encoding="utf-8")
parallel = (ROOT / "docs/PARALLEL_EXECUTION.md").read_text(encoding="utf-8")
if "Immediate parallel lanes" not in roadmap or "Parallel groups" not in parallel:
    print("Dynamic work graph / ownership map is missing expected control-plane sections")
    sys.exit(1)

print("DubFlow governance validation passed.")


# Deterministic issue-metadata parser contract must remain executable in PR Fast.
from issue_graph.task_metadata import run_self_tests
run_self_tests()


pr_template = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
protocol_text = (ROOT / "docs/ENGINEERING_EXECUTION_PROTOCOL.md").read_text(encoding="utf-8")
agents_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
for marker in ["DUBFLOW_PR_V1", "Tested-Base-SHA:"]:
    if marker not in pr_template:
        print("Missing PR evidence marker in template:", marker)
        sys.exit(1)
if "STALE_BASE" not in protocol_text or "Tested-Base-SHA" not in protocol_text:
    print("Engineering protocol is missing tested-base freshness rules")
    sys.exit(1)
if "STALE_BASE" not in agents_text or "Tested-Base-SHA" not in agents_text:
    print("AGENTS.md is missing tested-base merge invariant")
    sys.exit(1)


m = re.search(r"(?m)^ci_infrastructure_issue:\s*(\d+)\s*$", state)
if not m or int(m.group(1)) != 10:
    print("PROJECT_STATE must identify Issue #10 as CI infrastructure owner during bootstrap")
    sys.exit(1)
if "WAITING_CI_INFRA" not in protocol_text:
    print("Engineering protocol must define WAITING_CI_INFRA")
    sys.exit(1)
if "Required-CI" not in agents_text or "WAITING_CI_INFRA" not in agents_text:
    print("AGENTS.md must enforce required CI lane availability")
    sys.exit(1)


from ci.component_registry import run_self_tests as run_ci_registry_self_tests
from ci.component_registry import validate_current_repository
run_ci_registry_self_tests()
ci_registry_errors = validate_current_repository(ROOT)
if ci_registry_errors:
    print("Integration component registry validation failed:")
    for error in ci_registry_errors:
        print(" -", error)
    sys.exit(1)

integration_workflow = (ROOT / ".github/workflows/pr-integration.yml").read_text(encoding="utf-8")
if "name: integration" not in integration_workflow:
    print("PR Integration must emit stable check name: integration")
    sys.exit(1)
if "timeout-minutes: 45" not in integration_workflow:
    print("PR Integration must keep a bounded 45-minute job timeout")
    sys.exit(1)
