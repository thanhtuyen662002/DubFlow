"""sha256 lock of the files a measurement depends on ($0, offline). Write once before the run; verify before scoring.

usage: python -I lock.py write <lockfile.json> <file>...   |   python -I lock.py verify <lockfile.json>
"""
import hashlib, json, sys, datetime
from pathlib import Path

def digest(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()

if sys.argv[1] == "write":
    lock = Path(sys.argv[2])
    if lock.exists(): sys.exit("lock exists: a lock is written once")
    lock.write_text(json.dumps({"locked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                                "files": {p: digest(p) for p in sys.argv[3:]}}, indent=1))
    print("locked", len(sys.argv) - 3, "files")
else:
    data = json.loads(Path(sys.argv[2]).read_text()); bad = [p for p, d in data["files"].items() if not Path(p).exists() or digest(p) != d]
    print("\n".join("CHANGED " + p for p in bad) or "OK"); sys.exit(1 if bad else 0)
