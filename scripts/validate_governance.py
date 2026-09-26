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
