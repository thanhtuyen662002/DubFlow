from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
required = [
    "README.md",
    "AGENTS.md",
    "docs/PRODUCT_SYSTEM_DESIGN.md",
    "docs/RISK_REGISTER.md",
    "docs/ENGINEERING_EXECUTION_PROTOCOL.md",
    "docs/PROJECT_STATE.yaml",
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
    "critical_path:",
    "direct_main_push_allowed:",
]:
    if key not in state:
        print("Missing project state key:", key)
        sys.exit(1)
if re.search(r"direct_main_push_allowed:\s*true", state, re.I):
    print("Direct main push must not be enabled by bootstrap state")
    sys.exit(1)

print("DubFlow governance validation passed.")
