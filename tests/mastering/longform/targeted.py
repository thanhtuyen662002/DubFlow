"""Offline Stage 2 harness (#815): targeted evidence acquisition on saved long-form (W3) runs, E1 to E5.

Reads saved run folders and a locked ground truth only: no media, no network, no model, no key. Every number is a
replay of ``plan_part`` with the code checked out now (``w3.replay``), with evidence taken from the run itself, from
the other saved runs (the same JPEG bytes, provider and model: the production vision cache rule) or, in the labelled
upper bounds only, from the ground truth.

* E1 ``baseline``: what each run looked at, a review-free replay A, the broad pass reconstructed, A0 (A without the
  targeted and multi-frame answers), evidence-acquisition coverage.
* E2 ``select``: which extra frames each planning-time strategy picks (``targeting.py``, which never sees the ground
  truth), and how many R3 units ("the right shot was never looked at") get a frame inside their range.
* E3: the picked frames answered from the saved runs' own answers; partial simulation, never a lower bound.
* E4 ``budget``: arms at the matched budget and above, frames and estimated calls.
* E5 ``attribution``: per-unit stage changes against A.

The ground truth is checked against its lock before anything is scored (``w3.load_gt``). The scoring and attribution
rules are ``w3``'s, unchanged. ``W3 REAL-DEV`` is a development set: every report says GENERALIZATION NOT VERIFIED.

Usage (repository root, ``python -B``)::

    python -B -m tests.mastering.longform.targeted preflight <run>...
    python -B -m tests.mastering.longform.targeted baseline <gt> <lock> <run>... [--json OUT] [--review-edits off|on]
    python -B -m tests.mastering.longform.targeted select <run> (--all-arms --donors <run>... | --strategy S --budget N
                                                       [--window-s W|fwd]) [--json OUT|-]
    python -B -m tests.mastering.longform.targeted replay <gt> <lock> <run> --donors <run>... --strategy S --budget N
                                                       [--window-s W|fwd] [--regime open|closed_world]
    python -B -m tests.mastering.longform.targeted budget <gt> <lock> <run> --donors <run>... [--budgets ...]
                                                       [--windows-s ...] [--cost-per-call X --cost-unit U]
    python -B -m tests.mastering.longform.targeted attribution <gt> <lock> <run> --donors <run>... --arm <arm-id>
    python -B -m tests.mastering.longform.targeted all <gt> <lock> --primary <run> --runs <run>... --out-dir DIR [options]
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import dataclasses
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path, PurePath
import pickle
import subprocess
import sys
import time
from types import MappingProxyType
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
import zlib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.dubflow.mastering import align  # noqa: E402
from engine.dubflow.mastering import candidates as cands  # noqa: E402
from engine.dubflow.mastering import evidence as ev  # noqa: E402
from engine.dubflow.mastering import frames as more  # noqa: E402
from engine.dubflow.mastering import vision  # noqa: E402
from engine.dubflow.mastering.candidates import Candidate  # noqa: E402
from tests.mastering.longform import targeting as tg  # noqa: E402
from tests.mastering.longform import w3  # noqa: E402
from tests.mastering.real.capture import analysis_from, evidence_from  # noqa: E402

RECIPE = "w3-targeted-v2"
DATASET = "W3 REAL-DEV (development, not a holdout)"
CONCLUSION = "GENERALIZATION NOT VERIFIED; Gate B not claimed"
PARTIAL_NOTE = "partial simulation: benefits and harms both under-sampled (simulated {k} of {n})"
NOT_INTERPRETABLE = "not interpretable: no additional simulated evidence"
SAME_BUDGET_UNAVAILABLE = "same-budget comparison unavailable"
PREREG_PATH = HERE / "targeted_prereg.json"
MISSING_JPEG_LIMIT_MILLI = 50  # 5 %: above this a run fails its preflight
REGIMES = ("open", "closed_world")
REGIME_ORDER = {"open": 0, "closed_world": 1, "ceiling": 2}
KIND_ORDER = {"uniform": 0, "production": 1, "targeted": 2, "respend": 3, "ceiling": 4}
PRIMARY_ARM = "open:mixed:W=fwd:+matched"
PRIMARY_METRIC = "r3_units_with_hitting_frame_selected"
COMPARATORS = ("open:P+:-:+matched", "open:shuffled_prior:W=fwd:+matched")  # (production rule, shuffled control)
REQUIRED_RUNS = 5  # the decision rule's "each of the 5 runs"
BATCH_ARMS = ("open:A+:-:+matched", "open:P+:-:+matched", "open:mixed:W=fwd:+matched", "open:shuffled_prior:W=fwd:+matched")
CAVEATS = (
    "donor coverage: answers exist only for frames some saved run looked at; ALL_DONOR_ITEMS is the ceiling",
    "closed world (donor-covered shots only) is not a production condition",
    "partial simulation: benefits and harms both under-sampled",
    "review edits (pins, exclusions, approvals made after viewing) are excluded from every arm by default",
    "intro/outro looks are excluded from the item parts' observed set, as in production",
    "A0 keeps the logged sub-shots (the replay does not re-split them) with their non-vision evidence only",
    "runs not independent (shared footage, ground truth, 4/5 donors)",
    "an answer is replayed only under its exact production cache key (prompt version, provider, model, label set, JPEG "
    "bytes) with the logged answer equal to the cached record; no match by time or by run-level provider and model",
)
CODE_FILES = ("targeted.py", "targeting.py", "w3.py")


# --- small helpers ---

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def code_digest(path: Path) -> str:
    """sha256 of a source file with CRLF normalised to LF (the same on Windows and Linux checkouts)."""
    return sha256_bytes(path.read_bytes().replace(b"\r\n", b"\n"))


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=1) + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def r4(value: float) -> float:
    return round(float(value), 4)


def is_item(part: str) -> bool:
    return part.startswith("i") and part[1:].isdigit()


def vision_items(c: Mapping[str, Any]) -> list[dict]:
    return [i for i in (c.get("evidence") or {}).get("items") or () if i.get("origin") == "vision"]


def part_of_unit(unit_id: str) -> str:
    return unit_id.split(".")[0]


def mcnemar_exact(fixed: int, broken: int) -> float:
    """Two-sided exact binomial test on the discordant pairs (p = 1/2)."""
    n = fixed + broken
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(fixed, broken) + 1))
    return r4(min(1.0, 2 * tail / 2 ** n))


def chance_hits(eligible_frames: int, inside: Sequence[int], k: int) -> float:
    """Expected units with a frame inside their range when ``k`` frames are drawn uniformly without replacement from
    ``eligible_frames``, ``inside[u]`` of which lie inside unit u's ranges."""
    if eligible_frames <= 0 or k <= 0:
        return 0.0
    k = min(k, eligible_frames)
    total = math.comb(eligible_frames, k)
    return r4(sum(1 - math.comb(eligible_frames - h, k) / total for h in inside))


def quartiles(values: Sequence[int]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"n": 0, "q1_median_q3_milli": None, "histogram_4": [0, 0, 0, 0]}
    at = lambda f: ordered[min(len(ordered) - 1, (len(ordered) - 1) * f // 4)]  # noqa: E731
    hist = [0, 0, 0, 0]
    for v in ordered:
        hist[min(3, max(0, v * 4 // 1000))] += 1
    return {"n": len(ordered), "q1_median_q3_milli": [at(1), at(2), at(3)], "histogram_4": hist}


def vision_counts(report: Mapping[str, Any] | None) -> dict[str, Any]:
    """A saved vision report without who answered (no provider or model name leaves the PC: a short digest of the
    pair tells runs apart) and without its warning texts."""
    report = report or {}
    out = {k: v for k, v in report.items() if k not in ("provider", "model", "warnings")}
    if report.get("provider") or report.get("model"):
        out["provider_model_sha12"] = sha256_bytes(f"{report.get('provider')}|{report.get('model')}".encode("utf-8"))[:12]
    out["warnings"] = len(report.get("warnings") or ())
    return out


def peak_ram_mb() -> int | None:
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak // 1024 // (1024 if sys.platform == "darwin" else 1))
    except Exception:  # noqa: BLE001 - not on this platform
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return int(counters.PeakWorkingSetSize // (1024 * 1024))
    except Exception:  # noqa: BLE001
        pass
    return None


# --- loading ---

@dataclass
class Loaded:
    folder: Path
    run: w3.Run
    program: dict
    mdir: Path
    files: dict[str, str]                 # posix relpath -> sha256 of every file read
    jpeg: dict[str, str | None]           # keyframe file name -> sha256 (None: missing)
    cache: dict[str, dict] | None         # vision cache records by file stem (None: no cache folder)
    review_files: dict[str, dict]

    @property
    def name(self) -> str:
        return self.folder.name

    @property
    def run_sha256(self) -> str:
        return sha256_bytes("".join(f"{k}\t{v}\n" for k, v in sorted(self.files.items())).encode("utf-8"))

    @property
    def provider_model(self) -> tuple[str, str] | None:
        report = self.program.get("vision") or {}
        if report.get("provider") and report.get("model"):
            return str(report["provider"]), str(report["model"])
        return None


def _extra_names(keyframe: str) -> list[str]:
    stem = keyframe.rsplit(".", 1)[0]
    return [f"{stem}{more.SPLIT_MARK}1.jpg", f"{stem}{more.SPLIT_MARK}3.jpg"]


def load(folder: Path) -> Loaded:
    folder = Path(folder)
    run = w3.load_run(folder)
    mdir = w3.mastering_dir(folder)
    work = folder / ".dubflow-work"
    files: dict[str, str] = {}

    def read(path: Path) -> bytes:
        data = path.read_bytes()
        files[path.relative_to(folder).as_posix()] = sha256_bytes(data)
        return data

    for pattern in ("editorial-*.json", "plan-*.json", "analysis-*.json"):
        for path in sorted(mdir.glob(pattern)):
            read(path)
    program: dict = {}
    for name in ("program.json", "spec.json"):
        if (work / name).is_file():
            data = read(work / name)
            if name == "program.json":
                program = json.loads(data.decode("utf-8"))
    review_files = {}
    for path in sorted(work.glob("review-*.json")) if work.is_dir() else ():
        part = path.stem.removeprefix("review-")
        data = read(path)
        try:
            units = (json.loads(data.decode("utf-8")) or {}).get("units") or {}
        except ValueError:
            units = {}
        review_files[part] = {"present": True, "nonempty": bool(units),
                              "pins": sum(1 for d in units.values() if d.get("pin")),
                              "excludes": sum(len(d.get("exclude") or ()) for d in units.values()),
                              "approvals": sum(1 for d in units.values() if d.get("approved") is not None)}
    names = sorted({c["keyframe"] for log in run.logs.values() for c in log.get("candidates") or () if c.get("keyframe")})
    jpeg: dict[str, str | None] = {}
    for name in names:
        jpeg[name] = sha256_bytes(read(mdir / name)) if (mdir / name).is_file() else None
        for extra in _extra_names(name):
            if extra not in jpeg and (mdir / extra).is_file():
                jpeg[extra] = sha256_bytes(read(mdir / extra))
    cache = None
    if (mdir / "vision").is_dir():
        cache = {}
        for path in sorted((mdir / "vision").glob("*.json")):
            try:
                cache[path.stem] = json.loads(read(path).decode("utf-8"))
            except ValueError:
                continue
    return Loaded(folder, run, program, mdir, files, jpeg, cache, review_files)


# --- scoped replay patches ---

def no_review_edits(run: w3.Run, part: str, log: Mapping[str, Any]) -> tuple[align.Edits, str]:
    """The part's edits without what the user decided after viewing (review-<part>.json): the spec's own edits when
    the run saved its spec, else the log's edits less the review file's pins and exclusions."""
    if "editorial" in run.spec:
        given = ((run.spec.get("editorial") or {}).get("edits") or {}).get(part)
        return align.Edits.from_dict(given), "spec"
    edits = align.Edits.from_dict(log.get("edits") or {})
    path = run.folder / ".dubflow-work" / f"review-{part}.json"
    if not path.is_file():
        return edits, "log"
    try:
        units = (json.loads(path.read_text(encoding="utf-8")) or {}).get("units") or {}
    except (OSError, ValueError):
        return edits, "log_review_unreadable"
    for unit_id, decision in units.items():
        pin = decision.get("pin") or []
        if pin and edits.pin.get(unit_id) == list(pin):
            del edits.pin[unit_id]
        for i in decision.get("exclude") or ():
            edits.exclude.discard(i)
    return edits, "log_minus_review"


def _canon(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return {"__float__": repr(value)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"__dc__": type(value).__name__, **{f.name: _canon(getattr(value, f.name)) for f in dataclasses.fields(value)}}
    if isinstance(value, PurePath):
        return {"__path__": value.as_posix()}
    if isinstance(value, Mapping):
        if not all(isinstance(k, str) for k in value):
            raise TypeError("plan memo: a mapping with non-string keys")
        return {"__map__": [[k, _canon(value[k])] for k in sorted(value)]}
    if isinstance(value, (set, frozenset)):
        return {"__set__": sorted((_canon(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))}
    if isinstance(value, (list, tuple)):
        return [_canon(v) for v in value]
    raise TypeError(f"plan memo: cannot key a {type(value).__name__}")


def call_key(call: Mapping[str, Any]) -> str:
    return sha256_bytes(json.dumps(_canon(call), sort_keys=True, ensure_ascii=False).encode("utf-8"))


class PlanMemo:
    """``w3.plan_again`` remembered by the whole call; every caller gets its own copy of the plan and log."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.hits = 0
        self.misses = 0

    def wrap(self, original: Callable[[Mapping[str, Any]], tuple[dict, dict]]) -> Callable[[Mapping[str, Any]], tuple[dict, dict]]:
        def plan_again(call: Mapping[str, Any]) -> tuple[dict, dict]:
            key = call_key(call)
            if key in self.store:
                self.hits += 1
            else:
                self.misses += 1
                self.store[key] = zlib.compress(pickle.dumps(original(call), protocol=4))
            return pickle.loads(zlib.decompress(self.store[key]))
        return plan_again


@contextmanager
def replay_context(*, review: bool = False, memo: PlanMemo | None = None) -> Iterator[None]:
    """``w3.call_of`` without the review files' decisions and a remembered ``w3.plan_again``, for the block only."""
    saved_call, saved_plan = w3.call_of, w3.plan_again
    try:
        if not review:
            def call_of_no_review(run: w3.Run, part: str, shown: Sequence[str], **kwargs: Any) -> dict[str, Any]:
                call = saved_call(run, part, shown, **kwargs)
                call["edits"] = no_review_edits(run, part, kwargs.get("log") or run.logs[part])[0]
                return call
            w3.call_of = call_of_no_review
        if memo is not None:
            w3.plan_again = memo.wrap(saved_plan)
        yield
    finally:
        w3.call_of, w3.plan_again = saved_call, saved_plan


# --- run data for the planning view (no ground truth here) ---

def _trims(run: w3.Run, part: str, analyses: Mapping[str, Any]) -> tuple[tuple[str, int, int], ...]:
    plan = run.plans.get(part) or {}
    inputs = (plan.get("provenance") or {}).get("inputs") or {}
    given = w3.spec_part(run.spec, part)["trims"]
    out = []
    for source in plan.get("sources") or ():
        sid = source["source_id"]
        found = analyses.get(inputs.get(sid, "")) or (next(iter(analyses.values())) if len(analyses) == 1 else None)
        duration = found.duration_ms if found is not None else int(float(source.get("duration") or 0))
        start, end = given.get(sid, (0, None))
        out.append((sid, int(start or 0), int(end) if end is not None else duration))
    return tuple(sorted(out))


def source_keys(loaded: Loaded, part: str) -> tuple[dict[str, str], str]:
    """Each source's key: the content fingerprint the log saved, else the plan's identity digest."""
    log = loaded.run.logs.get(part) or {}
    plan = loaded.run.plans.get(part) or {}
    saved = log.get("sources") if isinstance(log.get("sources"), Mapping) else {}
    inputs = (plan.get("provenance") or {}).get("inputs") or {}
    out, basis = {}, set()
    for source in plan.get("sources") or ():
        sid = source["source_id"]
        if isinstance(saved.get(sid), str) and saved.get(sid):
            out[sid], _ = saved[sid], basis.add("content")
        else:
            out[sid], _ = str(inputs.get(sid) or ""), basis.add("identity_digest")
    return out, "+".join(sorted(basis)) or "none"


def run_data(replayed: w3.Run, loaded: Loaded, basis: str, restrict_to: frozenset[str] | None = None) -> tg.RunData:
    analyses = {k: analysis_from(v) for k, v in loaded.run.analyses.items()}
    evidence = {part: MappingProxyType({c["id"]: evidence_from(c["evidence"]) for c in log.get("candidates") or () if c.get("evidence")})
                for part, log in replayed.logs.items()}
    trims = {part: _trims(replayed, part, analyses) for part in replayed.order}
    keys = {part: MappingProxyType(source_keys(loaded, part)[0]) for part in replayed.order}
    return tg.RunData(loaded.name, basis, tuple(replayed.order), MappingProxyType(dict(replayed.logs)),
                      MappingProxyType(dict(replayed.plans)), MappingProxyType(dict(replayed.spec)), MappingProxyType(analyses),
                      MappingProxyType(evidence), MappingProxyType(trims), MappingProxyType(keys), restrict_to)


# --- broad pass, classification, A0 ---

def _sub_parent(c: Mapping[str, Any]) -> str:
    return tg.parent_id(c["id"], c["source_id"])


def broad_pools(run: w3.Run) -> list[list[Candidate]]:
    """Each part's pool as the broad look saw it: sub-shots folded back into their parent shot."""
    pools = []
    for part in run.order:
        listed = run.logs[part].get("candidates") or []
        plain = [Candidate.from_dict(c) for c in listed if _sub_parent(c) == c["id"]]
        subs: dict[str, list[dict]] = {}
        for c in listed:
            if _sub_parent(c) != c["id"]:
                subs.setdefault(_sub_parent(c), []).append(c)
        for parent, pieces in sorted(subs.items()):
            middle = next((p for p in pieces if p.get("keyframe") and not any(
                p["keyframe"].endswith(f"{more.SPLIT_MARK}{n}.jpg") for n in (1, 3))), pieces[len(pieces) // 2])
            made = Candidate.from_dict(middle)
            plain.append(dataclasses.replace(made, candidate_id=parent, start_ms=min(p["start_ms"] for p in pieces),
                                             end_ms=max(p["end_ms"] for p in pieces)))
        pools.append(plain)
    return pools


def job_groups(run: w3.Run) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in run.order:
        for c in run.logs[part].get("candidates") or ():
            out.setdefault(c["id"], c.get("group") or c["id"])
            out.setdefault(_sub_parent(c), c.get("group") or c["id"])
    return out


def observed_any(run: w3.Run, *, items_only: bool = False) -> set[str]:
    out = set()
    for part in run.order:
        if items_only and not is_item(part):
            continue
        for c in run.logs[part].get("candidates") or ():
            if vision_items(c):
                out.add(c["id"])
                out.add(_sub_parent(c))
    return out


def reconstruct_broad(loaded: Loaded) -> dict[str, Any]:
    run = loaded.run
    report = loaded.program.get("vision") or next((log.get("vision") for log in run.logs.values() if log.get("vision")), None) or {}
    budget = report.get("budget")
    if budget is None:
        return {"status": "NOT_RECONSTRUCTED", "reason": "no vision budget saved", "picked": []}
    pools = broad_pools(run)
    used = {c.candidate_id: c for pool in pools for c in pool}
    with_keyframe = sum(1 for c in used.values() if c.keyframe)
    room = budget - budget // 3 if with_keyframe > budget else budget
    picked = [cid for cid, _ in vision.allocate(pools, Path("."), room, job_groups(run))]
    seen = observed_any(run)
    observed = [i for i in picked if i in seen]
    missing = [i for i in picked if i not in seen]
    logged = report.get("broad")
    reasons = []
    if missing:
        reasons.append("picked not observed")
    if logged is None or len(picked) != int(logged):
        reasons.append("broad count mismatch")
    exact = cache_basis(loaded) == "exact"
    not_cached = [i for i in picked if not asked_in_cache(loaded, used[i].keyframe)] if exact else []
    if not_cached:
        reasons.append("picked not in cache")
    return {"status": "NOT_RECONSTRUCTED" if reasons else "RECONSTRUCTED", "reasons": reasons, "budget": budget,
            "room": room, "frames_with_keyframe": with_keyframe, "picked": sorted(picked), "picked_observed": len(observed),
            "picked_not_observed": len(missing), "logged_broad": logged,
            "picked_not_in_cache": len(not_cached) if exact else None}


def _cache_key(prompt: str, provider: str, model: str, labels: Sequence[str], digest_hex: str) -> str:
    return sha256_bytes(b"|".join([prompt.encode(), provider.encode(), model.encode(), ",".join(labels).encode(),
                                   bytes.fromhex(digest_hex)]))


def cache_combos(loaded: Loaded) -> list[tuple[str, str, str]]:
    """(prompt, provider, model asked) the run's cache keys can have been made with: what the records say (``made_by``,
    written since #808 only) and, for the older records, the pairs the programme file, the part logs and the spec name
    (the reports keep the model that answered, the spec the model that was asked)."""
    if loaded.cache is None:
        return []
    combos: set[tuple[str, str, str]] = set()
    for r in loaded.cache.values():
        made = r.get("made_by") if isinstance(r.get("made_by"), Mapping) else {}
        if made.get("provider"):
            combos.add((str(made.get("prompt") or vision.PROMPT_VERSION), str(made["provider"]), str(made.get("model") or "")))
    providers: set[str] = set()
    models: set[str] = set()
    reports = [loaded.program.get("vision") or {}, *((log.get("vision") or {}) for log in loaded.run.logs.values())]
    for report in reports:
        if report.get("provider") and report.get("model"):
            providers.add(str(report["provider"]))
            models.add(str(report["model"]))
    editorial = loaded.run.spec.get("editorial") if isinstance(loaded.run.spec.get("editorial"), Mapping) else {}
    ai = editorial.get("ai") if isinstance(editorial.get("ai"), Mapping) else {}
    if ai.get("provider"):
        providers.add(str(ai["provider"]))
    if ai.get("model"):
        models.add(str(ai["model"]))
    combos.update((vision.PROMPT_VERSION, provider, model) for provider in providers for model in models)
    return sorted(combos)


def cache_basis(loaded: Loaded) -> str:
    """How looks are classified: "exact" when the run's cache answers some observed keyframe by its production key,
    "group" without a cache folder, "group (cache keys unmatched)" when the folder exists but no key is found."""
    if loaded.cache is None:
        return "group"
    if not hasattr(loaded, "_cache_basis"):
        frames = sorted({c["keyframe"] for log in loaded.run.logs.values() for c in log.get("candidates") or ()
                         if c.get("keyframe") and vision_items(c)})
        loaded._cache_basis = "exact" if any(asked_in_cache(loaded, k) for k in frames) else "group (cache keys unmatched)"
    return loaded._cache_basis


def asked_in_cache(loaded: Loaded, keyframe: str | None) -> bool:
    """Whether the run's vision cache holds an answer for exactly these JPEG bytes (the production cache key)."""
    if loaded.cache is None or not keyframe or not loaded.jpeg.get(keyframe):
        return False
    if not hasattr(loaded, "_combos"):
        loaded._combos = cache_combos(loaded)
    for prompt, provider, model in loaded._combos:
        for labels in (ev.MOMENT_LABELS, ev.LABELS):
            key = _cache_key(prompt, provider, model, labels, loaded.jpeg[keyframe])
            record = loaded.cache.get(key[:32])
            if record is not None and record.get("key") == key:
                return True
    return False


def key_bases(loaded: Loaded) -> list[tuple[str, str, str, tuple[str, ...]]]:
    """The production cache-key bases (prompt version, provider, model asked, label set) this run's own cache proves:
    a basis counts only when, for one of the run's own answered keyframes, the record under the full key it gives
    (``vision.Describer._key``) reproduces the answer the run logged for that frame. A leftover record from another
    prompt version or model in the same folder proves nothing. Nothing is inferred from run-level fields alone."""
    if loaded.cache is None:
        return []
    if not hasattr(loaded, "_key_bases"):
        answered = sorted({(c["keyframe"], json.dumps(vision_items(c)[0], sort_keys=True, ensure_ascii=False))
                           for log in loaded.run.logs.values() for c in log.get("candidates") or ()
                           if c.get("keyframe") and vision_items(c) and loaded.jpeg.get(c["keyframe"])})
        found: set[tuple[str, str, str, tuple[str, ...]]] = set()
        for prompt, provider, model in cache_combos(loaded):
            for labels in (ev.MOMENT_LABELS, ev.LABELS):
                basis = (prompt, provider, model, tuple(labels))
                if any(exact_answer(loaded, basis, loaded.jpeg[name], json.loads(item)) == "exact" for name, item in answered):
                    found.add(basis)
        loaded._key_bases = sorted(found)
    return loaded._key_bases


def exact_answer(loaded: Loaded, basis: tuple[str, str, str, tuple[str, ...]], jpeg_hex: str | None,
                 item: Mapping[str, Any]) -> str:
    """"exact" when ``loaded``'s cache holds, under the full production key of ``basis`` for these JPEG bytes, a record
    whose validated answer is this logged item (text, labels, confidence); otherwise why that is not proven. A record
    without ``made_by`` (written before #808) is accepted only through this computed full key."""
    if not jpeg_hex:
        return "no_jpeg"
    if loaded.cache is None:
        return "no_cache"
    prompt, provider, model, labels = basis
    key = _cache_key(prompt, provider, model, labels, jpeg_hex)
    record = loaded.cache.get(key[:32])
    if record is None or record.get("key") != key:
        return "no_exact_key"
    made = record.get("made_by")
    if isinstance(made, Mapping) and (made.get("prompt"), made.get("provider"), made.get("model")) != (prompt, provider, model):
        return "made_by_mismatch"
    seen = vision.validate(record, labels)  # what production's _cached requires before it reuses a record
    if seen is None:
        return "answer_mismatch"
    logged = (item.get("text"), list(item.get("labels") or ()), item.get("confidence"))
    detail = str(item.get("detail") or "")
    # The run that asked logged item_of(the fresh answer), and the record stores that answer's fields as they were;
    # validating them again can differ at a clipped field ending in a space, so the stored fields count as they are.
    candidates = [seen]
    try:
        candidates.append(vision.Seen(str(record["description"]), tuple(record["objects"]), tuple(record["actions"]),
                                      str(record["visible_text"]), tuple(record["labels"]), int(record["confidence"]),
                                      str(record["clarity"]), record.get("product_box"), record.get("product_group"),
                                      int(record.get("hook") or 0)))
    except (KeyError, TypeError, ValueError):
        pass
    for answer in candidates:
        expected = vision.item_of(answer, detail)
        if (expected.text, list(expected.labels), expected.confidence) == logged:
            return "exact"
    return "answer_mismatch"


def classify(loaded: Loaded, broad: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    """Each looked-at shot: broad, broad_shared, targeted, targeted_shared or multi_frame (§3.4)."""
    run = loaded.run
    groups = job_groups(run)
    broad_ids = set(broad.get("picked") or ())
    first: dict[str, dict] = {}
    for part in run.order:
        for c in run.logs[part].get("candidates") or ():
            if vision_items(c):
                first.setdefault(c["id"], c)
    basis = cache_basis(loaded)
    exact = basis == "exact"
    answer = {i: json.dumps([(v.get("text"), v.get("labels")) for v in vision_items(c)[:1]], ensure_ascii=False)
              for i, c in first.items()}
    by_group: dict[tuple[str, str], list[str]] = {}
    for i in sorted(first):
        by_group.setdefault((groups.get(i, i), answer[i]), []).append(i)
    classes: dict[str, str] = {}
    for i, c in sorted(first.items()):
        if _sub_parent(c) != i:
            classes[i] = "multi_frame"
            continue
        if i in broad_ids:
            classes[i] = "broad"
            continue
        mates = by_group[(groups.get(i, i), answer[i])]
        if exact:
            if asked_in_cache(loaded, c.get("keyframe")):
                classes[i] = "targeted"
            else:
                classes[i] = "broad_shared" if any(m in broad_ids for m in mates) else "targeted_shared"
        else:
            if any(m in broad_ids for m in mates):
                classes[i] = "broad_shared"
            else:
                leader = next(m for m in mates if m not in broad_ids)
                classes[i] = "targeted" if i == leader else "targeted_shared"
    counts: dict[str, int] = {}
    for value in classes.values():
        counts[value] = counts.get(value, 0) + 1
    item_targeted = sorted(i for i, k in classes.items() if k == "targeted" and any(
        i in w3.candidates_of(run.logs[p]) and vision_items(w3.candidates_of(run.logs[p])[i]) for p in run.order if is_item(p)))
    multi_items = sum(max(0, len(vision_items(c)) - 1) for i, c in first.items() if classes[i] != "multi_frame")
    report = {"classification_basis": basis, "counts": {k: counts[k] for k in sorted(counts)},
              "targeted_frames_item_parts": len(item_targeted), "multi_frame_extra_items": multi_items,
              "a0_subshots_kept": sum(1 for p in run.order if is_item(p) for c in run.logs[p].get("candidates") or ()
                                      if _sub_parent(c) != c["id"])}
    return classes, report


def a0_evidence(classes: Mapping[str, str]) -> Callable[[str, Mapping[str, Any]], dict[str, ev.Evidence]]:
    """A without the targeted and multi-frame answers: broad answers keep their first frame only."""
    def evidence_for(part: str, log: Mapping[str, Any]) -> dict[str, ev.Evidence]:
        out = {}
        for c in log.get("candidates") or ():
            found = evidence_from(c["evidence"]) if c.get("evidence") else ev.Evidence(c["id"])
            kind = classes.get(c["id"])
            if kind in ("targeted", "targeted_shared", "multi_frame"):
                found.items = [i for i in found.items if i.origin != "vision"]
            elif kind in ("broad", "broad_shared"):
                kept, seen_vision = [], False
                for i in found.items:
                    if i.origin == "vision":
                        if seen_vision:
                            continue
                        seen_vision = True
                    kept.append(i)
                found.items = kept
            out[c["id"]] = found
        return out
    return evidence_for


def matched_budget(loaded: Loaded, classification: Mapping[str, Any]) -> tuple[int, str, dict[str, int]]:
    """Targeted frames the item parts paid for: the part logs' vision reports are cumulative over the job, so each
    part's own share is its report less the previous part's (intro/outro shares are left out)."""
    run = loaded.run
    per_part: dict[str, int] = {}
    previous = 0
    complete = True
    for part in run.order:
        report = run.logs[part].get("vision")
        if not report or "targeted" not in report:
            complete = False
            continue
        value = int(report["targeted"])
        per_part[part] = value - previous
        previous = value
    if complete and per_part:
        return sum(v for p, v in per_part.items() if is_item(p)), "log_vision_reports", per_part
    return int(classification.get("targeted_frames_item_parts") or 0), "classification", per_part


def respend_budget(loaded: Loaded, classification: Mapping[str, Any], matched: int, matched_basis: str) -> dict[str, Any]:
    """B0's budget: the item-part frames the run paid for and A0 removed (its targeted looks, ``matched``, and its
    multi-frame looks, both from the part logs' cumulative vision reports), set beside the classification's count."""
    run = loaded.run
    multi = 0
    previous = 0
    for part in run.order:
        report = run.logs[part].get("vision")
        if not report:
            continue
        value = int(report.get("multi_frame") or 0)
        if is_item(part):
            multi += value - previous
        previous = value
    classified = int(classification.get("targeted_frames_item_parts") or 0)
    if matched_basis == "log_vision_reports":
        budget, basis = matched + multi, "log_vision_reports"
        label = None if matched == classified else "budget basis differs from classification"
    else:
        budget, basis = classified, "classification"
        label = "budget basis: classification only (no log vision reports; multi-frame looks not counted)"
    return {"budget": budget, "basis": basis, "targeted_paid": matched if basis == "log_vision_reports" else None,
            "multi_frame_paid": multi if basis == "log_vision_reports" else None,
            "targeted_frames_item_parts": classified, "label": label}


# --- one run, prepared without the ground truth ---

@dataclass
class Prepared:
    loaded: Loaded
    review: bool
    A: w3.Run
    data_A: tg.RunData
    view_A: tg.PlanningView
    broad: dict[str, Any]
    classes: dict[str, str]
    classification: dict[str, Any]
    A0: w3.Run | None
    data_A0: tg.RunData | None
    view_A0: tg.PlanningView | None
    matched: int
    matched_basis: str
    targeted_per_part: dict[str, int]
    respend: dict[str, Any] = field(default_factory=dict)
    times: dict[str, float] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.loaded.name

    def basis(self, base: str) -> str:
        return f"{base}_replay_{'with' if self.review else 'no'}_review"

    def shot(self, part: str, candidate_id: str) -> tg.ShotView | None:
        if not hasattr(self, "_shots"):
            self._shots = {(p.part, s.candidate_id): s for p in self.view_A.parts for s in p.shots}
        return self._shots.get((part, candidate_id))


def prepare(loaded: Loaded, *, review: bool = False, memo: PlanMemo | None = None, light: bool = False) -> Prepared:
    """A run made ready for selection: A (a review-free replay), its planning view, the broad pass reconstructed, the
    looks classified and A0. ``light``: a donor only (its saved logs stand for A: a donor gives answers to frames,
    and which frames it answered does not depend on a replay)."""
    times: dict[str, float] = {}
    began = time.perf_counter()
    if light:
        A = loaded.run
    else:
        with replay_context(review=review, memo=memo):
            A = w3.replay(loaded.run)
    times["replay_A"] = time.perf_counter() - began
    basis_a = "saved_logs" if light else f"A_replay_{'with' if review else 'no'}_review"
    data_a = run_data(A, loaded, basis_a)
    view_a = tg.planning_view(data_a)
    broad = reconstruct_broad(loaded)
    classes, classification = classify(loaded, broad)
    a0 = data_a0 = view_a0 = None
    if broad["status"] == "RECONSTRUCTED" and not light:
        began = time.perf_counter()
        with replay_context(review=review, memo=memo):
            a0 = w3.replay(loaded.run, evidence_for=a0_evidence(classes))
        times["replay_A0"] = time.perf_counter() - began
        data_a0 = run_data(a0, loaded, f"A0_replay_{'with' if review else 'no'}_review")
        view_a0 = tg.planning_view(data_a0)
    matched, basis, per_part = matched_budget(loaded, classification)
    return Prepared(loaded, review, A, data_a, view_a, broad, classes, classification, a0, data_a0, view_a0, matched, basis,
                    per_part, respend_budget(loaded, classification, matched, basis), times)


# --- donor pool and matching (E3) ---

@dataclass(frozen=True)
class DonorSample:
    run: str
    part: str
    candidate_id: str
    item_index: int
    jpeg_sha256: str | None
    t_ms: int
    t_basis: str
    provider: str
    model: str
    item: Mapping[str, Any]
    source_key: str
    own_cache: bool


@dataclass(frozen=True)
class Replayed:
    sample: tg.Sample
    status: str                   # "SIMULATED" | "NOT_SIMULABLE"
    reason: str | None
    match_basis: str | None       # "exact_key": the same JPEG bytes under the proven production cache key
    donor_run: str | None
    donor_part: str | None
    donor_candidate_id: str | None
    donor_item_index: int | None
    own_cache: bool
    jpeg_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in dataclasses.asdict(self).items() if k != "sample"}
        out["sample"] = dataclasses.asdict(self.sample)
        return out


def donor_samples(prep: Prepared, *, parts: str, own: bool, only: Sequence[str] | None = None
                  ) -> tuple[list[DonorSample], dict[str, int]]:
    """Every answered frame of ``prep``'s saved logs: ``parts`` "items" or "all" (or "non_items", the run's own
    intro/outro looks); a shared copy of another frame's answer is left out (it says nothing of its own bytes), and so
    is an answer whose recorded ``detail`` names another provider or model than the run's."""
    loaded = prep.loaded
    pm = loaded.provider_model
    counts = {"donor_shared_excluded": 0, "detail_mismatch": 0}
    out: list[DonorSample] = []
    if pm is None:
        return out, counts
    provider, model = pm
    item_seen = observed_any(loaded.run, items_only=True)
    done: set[tuple[str, int]] = set()
    for part in loaded.run.order:
        if parts == "items" and not is_item(part) or parts == "non_items" and is_item(part):
            continue
        shots = {s.candidate_id: s for p in prep.view_A.parts if p.part == part for s in p.shots}
        for c in loaded.run.logs[part].get("candidates") or ():
            items = vision_items(c)
            if not items:
                continue
            if parts == "non_items" and c["id"] in item_seen:
                continue
            if prep.classes.get(c["id"]) in ("broad_shared", "targeted_shared"):
                counts["donor_shared_excluded"] += 1
                continue
            if only is not None and prep.classes.get(c["id"]) not in only:
                continue
            s = shots.get(c["id"])
            if s is None:
                continue
            extras = [n for n in _extra_names(c["keyframe"]) if loaded.jpeg.get(n)] if c.get("keyframe") else []
            lo, hi = s.window or (s.start_ms, s.end_ms)
            early, late = more.extra_times(dataclasses.replace(s.candidate, start_ms=lo, end_ms=hi))
            for index, item in enumerate(items):
                if (c["id"], index) in done:
                    continue
                done.add((c["id"], index))
                if item.get("detail") and item.get("detail") != f"AI {provider}/{model}":
                    counts["detail_mismatch"] += 1  # answered by another provider or model than the run asked: left out
                    continue
                if index == 0:
                    name, t, basis = c.get("keyframe"), s.keyframe_ms, s.keyframe_basis
                elif index - 1 < len(extras):
                    name = extras[index - 1]
                    t, basis = (early if name.endswith(f"{more.SPLIT_MARK}1.jpg") else late), "extra_frame"
                else:
                    continue
                out.append(DonorSample(loaded.name, part, c["id"], index, loaded.jpeg.get(name) if name else None, t, basis,
                                       provider, model, MappingProxyType(dict(item)), s.source_key, own))
    return out, counts


class DonorIndex:
    """Answers for selected frames: the run's own cached answers first (its intro/outro looks; for an arm that
    starts from A0, also the targeted answers A0 removed), then the donors' answers to the same JPEG bytes. An answer
    is admitted only when its exact production cache key is proven: the target run's own cache fixes one key basis
    (prompt version, provider, model, label set), and the answering run's cache must hold, under that basis's full key
    for the frame's JPEG bytes, a record whose answer is the logged item. Without that proof the frame is
    NOT_SIMULABLE; nothing is matched by time or by run-level provider and model alone."""

    def __init__(self, target: Prepared, donors: Sequence[Prepared], *, scope: str = "items", tolerance_ms: int = 500,
                 base: str = "A") -> None:
        self.target, self.donors, self.scope, self.base = target, list(donors), scope, base
        self.tolerance_ms = tolerance_ms
        self.pm = target.loaded.provider_model
        self.counts = {"donor_shared_excluded": 0, "donor_provider_mismatch": 0, "detail_mismatch": 0}
        self.provenance: dict[str, int] = {}
        self._a0: DonorIndex | None = None
        bases = key_bases(target.loaded)
        self.key_basis = bases[0] if len(bases) == 1 else None
        self.key_basis_status = "exact" if self.key_basis is not None else ("ambiguous" if bases else "unproven")
        own, counted = donor_samples(target, parts="non_items", own=True)
        self.counts["detail_mismatch"] += counted["detail_mismatch"]
        if base == "A0":
            extra, counted = donor_samples(target, parts="items", own=True, only=("targeted", "multi_frame"))
            own += extra
            self.counts["detail_mismatch"] += counted["detail_mismatch"]
        own = [d for d in own if self._proven(target.loaded, d)]
        pool = list(own)
        self.counts["own_cache_frames"] = len(own)
        mine = target.loaded.run_sha256
        used = set()
        for donor in sorted(donors, key=lambda d: d.name):
            if donor.loaded.run_sha256 == mine or donor.loaded.run_sha256 in used:
                continue
            used.add(donor.loaded.run_sha256)
            found, counted = donor_samples(donor, parts=scope, own=False)
            for k in ("donor_shared_excluded", "detail_mismatch"):
                self.counts[k] += counted[k]
            for d in found:
                if self.pm is None or (d.provider, d.model) != self.pm:
                    self.counts["donor_provider_mismatch"] += 1
                    continue
                if self._proven(donor.loaded, d):
                    pool.append(d)
        self.donor_runs = sorted({d.run for d in pool if not d.own_cache})
        self.pool = sorted(pool, key=self._order)
        self.by_jpeg: dict[str, list[DonorSample]] = {}
        for d in self.pool:
            if d.jpeg_sha256:
                self.by_jpeg.setdefault(d.jpeg_sha256, []).append(d)

    def _proven(self, loaded: Loaded, d: DonorSample) -> bool:
        reason = "no_key_basis" if self.key_basis is None else exact_answer(loaded, self.key_basis, d.jpeg_sha256, d.item)
        self.provenance[reason] = self.provenance.get(reason, 0) + 1
        return reason == "exact"

    def for_base(self, base: str) -> "DonorIndex":
        if base == self.base:
            return self
        if self._a0 is None:
            self._a0 = DonorIndex(self.target, self.donors, scope=self.scope, tolerance_ms=self.tolerance_ms, base=base)
        return self._a0

    @staticmethod
    def _order(d: DonorSample) -> tuple:
        return (not d.own_cache, d.run, d.candidate_id, d.item_index)

    def target_jpeg(self, part: str, candidate_id: str) -> str | None:
        s = self.target.shot(part, candidate_id)
        name = s.candidate.keyframe if s is not None else None
        return self.target.loaded.jpeg.get(name) if name else None

    def match(self, sample: tg.Sample) -> Replayed:
        tj = self.target_jpeg(sample.part, sample.candidate_id)

        def done(d: DonorSample | None, reason: str | None, basis: str | None) -> Replayed:
            if d is None:
                return Replayed(sample, "NOT_SIMULABLE", reason, None, None, None, None, None, False, tj)
            return Replayed(sample, "SIMULATED", None, basis, d.run, d.part, d.candidate_id, d.item_index, d.own_cache, tj)

        if self.pm is None:
            return done(None, "source_unknown", None)
        if self.key_basis is None:
            return done(None, f"target_key_{self.key_basis_status}", None)
        if tj is None:
            return done(None, "target_jpeg_unknown", None)  # no bytes, no production key: never matched by time
        found = self.by_jpeg.get(tj)
        if found:
            if not found[0].own_cache and self.target_record(tj) is not None:
                # production would reuse the target folder's own record for these bytes, not the donor's answer
                return done(None, "target_cache_conflict", None)
            return done(found[0], None, "exact_key")
        # diagnostic only: tolerance_ms never admits an answer, it only tells a near miss from no answer at all
        near = lambda d: abs(d.t_ms - sample.t_ms) <= self.tolerance_ms  # noqa: E731
        if any(d.source_key == sample.source_key and near(d) for d in self.pool):
            return done(None, "jpeg_mismatch", None)
        return done(None, "no_donor_answer", None)

    def target_record(self, jpeg_hex: str) -> Mapping[str, Any] | None:
        """The target folder's own cache record under the full production key for these bytes, if it has one."""
        if self.key_basis is None or self.target.loaded.cache is None:
            return None
        prompt, provider, model, labels = self.key_basis
        key = _cache_key(prompt, provider, model, labels, jpeg_hex)
        record = self.target.loaded.cache.get(key[:32])
        return record if record is not None and record.get("key") == key and vision.validate(record, labels) else None

    def covered(self, view: tg.PlanningView) -> frozenset[str]:
        """Open-world eligible shots some donor (or the run's own intro/outro look) answered."""
        out = set()
        for p in view.parts:
            for s in tg.eligible_shots(view, p):
                probe = tg.Sample(p.part, s.candidate_id, s.source_id, s.source_key, s.keyframe_ms, s.keyframe_basis, None, "probe", 0)
                if self.match(probe).status == "SIMULATED":
                    out.add(s.candidate_id)
        return frozenset(out)

    def item_of(self, r: Replayed) -> dict[str, Any] | None:
        if r.status != "SIMULATED":
            return None
        for d in self.pool:
            if (d.run, d.part, d.candidate_id, d.item_index, d.own_cache) == (r.donor_run, r.donor_part, r.donor_candidate_id,
                                                                             r.donor_item_index, r.own_cache):
                return dict(d.item)
        return None


def augmented_evidence(base: w3.Run, additions: Mapping[str, Mapping[str, Mapping[str, Any]]], *, share_duplicates: bool = True
                       ) -> tuple[Callable[[str, Mapping[str, Any]], dict[str, ev.Evidence] | None], set[str], set[str]]:
    """``evidence_for`` for ``w3.replay``: the base replay's evidence with each matched answer appended verbatim to the
    selected candidate of the selecting part (and, with ``share_duplicates``, to the same-group shots of that part that
    have no vision answer). Returns the function, the ids given an answer, and the ids given a shared copy."""
    new_ids: set[str] = set()
    shared_ids: set[str] = set()
    planned: dict[str, dict[str, ev.Evidence]] = {}
    for part, added in additions.items():
        log = base.logs[part]
        out = {}
        for c in log.get("candidates") or ():
            out[c["id"]] = evidence_from(c["evidence"]) if c.get("evidence") else ev.Evidence(c["id"])
        groups = {c["id"]: c.get("group") or c["id"] for c in log.get("candidates") or ()}
        for candidate_id, item in sorted(added.items()):
            made = ev.Item(item["origin"], int(item["confidence"]), item.get("text", ""), tuple(item.get("labels") or ()),
                           item.get("detail", ""))
            out.setdefault(candidate_id, ev.Evidence(candidate_id)).add(made)
            new_ids.add(candidate_id)
            if share_duplicates:
                for other, group in sorted(groups.items()):
                    if other != candidate_id and other not in added and group == groups.get(candidate_id) \
                            and not any(i.origin == "vision" for i in out[other].items):
                        out[other].add(made)
                        shared_ids.add(other)
        planned[part] = out

    def evidence_for(part: str, log: Mapping[str, Any]) -> dict[str, ev.Evidence] | None:
        return planned.get(part)
    return evidence_for, new_ids, shared_ids


def replay_parts(base: w3.Run, changed: Iterable[str]) -> list[str]:
    """The parts a change can move: the changed parts and every non-item part after the first of them (the outro
    avoids what the items showed)."""
    changed = set(changed)
    if not changed:
        return []
    first = min(base.order.index(p) for p in changed)
    return [p for k, p in enumerate(base.order) if p in changed or (k > first and not is_item(p))]


# --- evaluation context (ground truth from here on) ---

@dataclass(frozen=True)
class Arm:
    id: str
    regime: str
    kind: str
    strategy: str
    window_s: str                 # "-" | "5" | ... | "fwd"
    budget_label: str
    budget: int | None
    base: str                     # "A" | "A0"

    def params(self, target_units: str) -> tg.SelectParams:
        window = None if self.window_s in ("-", "fwd") else int(self.window_s) * 1000
        return tg.SelectParams(self.strategy, self.budget, window, target_units)

    def sort_key(self) -> tuple:
        w = 10 ** 9 if self.window_s in ("fwd", "-") else int(self.window_s)
        return (REGIME_ORDER[self.regime], KIND_ORDER[self.kind], self.strategy, w, self.budget if self.budget is not None else 10 ** 9,
                self.id)


def parse_budgets(text: str, matched: int) -> list[tuple[str, int]]:
    out = []
    for token in [t.strip() for t in text.split(",") if t.strip()]:
        if token == "matched":
            out.append(("+matched", matched))
        elif token.endswith("x") and token[:-1].isdigit():
            out.append((f"+{token}", matched * int(token[:-1])))
        elif token.isdigit():
            out.append((f"+{token}", int(token)))
        else:
            raise SystemExit(f"unknown budget {token!r}")
    return list(dict.fromkeys(out))


def parse_windows(text: str) -> list[str]:
    out = []
    for token in [t.strip() for t in text.split(",") if t.strip()]:
        if token != "fwd" and not token.isdigit():
            raise SystemExit(f"unknown window {token!r}")
        out.append(token)
    return list(dict.fromkeys(out))


TARGETED = ("prior", "shuffled_prior", "anchor", "mixed")


def arm_grid(prep: Prepared, *, budgets: str, windows: str, full: bool) -> list[Arm]:
    chosen = parse_budgets(budgets, prep.matched) if full else [("+matched", prep.matched)]
    arms = []
    for regime in REGIMES:
        for label, b in chosen:
            arms.append(Arm(f"{regime}:A+:-:{label}", regime, "uniform", "uniform", "-", label, b, "A"))
            arms.append(Arm(f"{regime}:P+:-:{label}", regime, "production", "production", "-", label, b, "A"))
            for strategy in TARGETED:
                for w in parse_windows(windows):
                    arms.append(Arm(f"{regime}:{strategy}:W={w if w == 'fwd' else w + 's'}:{label}", regime, "targeted",
                                    strategy, w, label, b, "A"))
            arms.append(Arm(f"{regime}:gap:W=fwd:{label}", regime, "targeted", "gap", "fwd", label, b, "A"))
        if prep.view_A0 is not None:
            removed = prep.respend["budget"]
            for w in parse_windows(windows):
                arms.append(Arm(f"{regime}:B0-mixed:W={w if w == 'fwd' else w + 's'}:=removed", regime, "respend", "mixed", w,
                                "=removed", removed, "A0"))
            arms.append(Arm(f"{regime}:B0-P+:-:=removed", regime, "respend", "production", "-", "=removed", removed, "A0"))
    arms.append(Arm("ceiling:ALL_DONOR_ITEMS:-:all", "ceiling", "ceiling", "all_eligible", "-", "all", None, "A"))
    return sorted(arms, key=Arm.sort_key)


class Views:
    """The planning views an arm starts from (per base and regime), built once; no ground truth."""

    def __init__(self, prep: Prepared, index: DonorIndex) -> None:
        self.prep, self.index = prep, index
        self._views: dict[tuple[str, str], tg.PlanningView] = {}

    def get(self, base: str, regime: str) -> tg.PlanningView:
        key = (base, "open" if regime == "open" else "closed_world")
        if key not in self._views:
            prep = self.prep
            data = prep.data_A if base == "A" else prep.data_A0
            open_view = tg.planning_view(data)
            if key[1] == "open":
                self._views[key] = open_view
            else:
                covered = self.index.for_base(base).covered(open_view)
                self._views[key] = tg.planning_view(dataclasses.replace(data, restrict_to=covered))
        return self._views[key]


def select_arms(prep: Prepared, index: DonorIndex, arms: Sequence[Arm], target_units: str
                ) -> dict[str, tuple[tg.Selection, tg.PlanningView]]:
    views = Views(prep, index)
    out = {}
    for arm in arms:
        if arm.base == "A0" and prep.view_A0 is None:
            continue
        view = views.get(arm.base, arm.regime)
        out[arm.id] = (tg.select_detail(view, arm.params(target_units)), view)
    return out


def samples_json(selected: Mapping[str, tuple[tg.Selection, tg.PlanningView]]) -> dict[str, list[dict]]:
    return {arm_id: [dataclasses.asdict(s) for s in sel.samples] for arm_id, (sel, _) in sorted(selected.items())}


@dataclass
class Context:
    gt: Mapping[str, Any]
    prep: Prepared
    index: DonorIndex
    memo: PlanMemo | None
    review: bool
    tolerance_ms: int
    share_duplicates: bool
    target_units: str
    cost_per_call: float | None
    cost_unit: str | None
    base_runs: dict[str, w3.Run] = field(default_factory=dict)
    scores: dict[str, dict] = field(default_factory=dict)
    attributions: dict[str, dict] = field(default_factory=dict)
    cache: dict[str, tuple] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.base_runs["A"] = self.prep.A
        if self.prep.A0 is not None:
            self.base_runs["A0"] = self.prep.A0
        for name, run in self.base_runs.items():
            self.scores[name] = w3.score(self.gt, run)
            self.attributions[name] = w3.attribute(self.gt, run)

    def replay_with(self, base: str, replayed: Sequence[Replayed]) -> tuple[w3.Run, dict, dict, set[str], set[str], dict[str, str]]:
        additions: dict[str, dict[str, dict]] = {}
        donor_of: dict[str, str] = {}
        index = self.index.for_base(base)
        for r in replayed:
            item = index.item_of(r)
            if item is None:
                continue
            additions.setdefault(r.sample.part, {})[r.sample.candidate_id] = item
            donor_of[r.sample.candidate_id] = "own_cache" if r.own_cache else str(r.donor_run)
        key = sha256_bytes(json.dumps([base, {p: {i: dict(v) for i, v in sorted(a.items())} for p, a in sorted(additions.items())},
                                       self.share_duplicates], sort_keys=True, ensure_ascii=False).encode("utf-8"))
        if key not in self.cache:
            run = self.base_runs[base]
            if not additions:
                self.cache[key] = (run, self.scores[base], self.attributions[base], set(), set())
            else:
                evidence_for, new_ids, shared_ids = augmented_evidence(run, additions, share_duplicates=self.share_duplicates)
                with replay_context(review=self.review, memo=self.memo):
                    again = w3.replay(run, evidence_for=evidence_for, parts=replay_parts(run, additions))
                self.cache[key] = (again, w3.score(self.gt, again), w3.attribute(self.gt, again), new_ids, shared_ids)
        again, scored, attributed, new_ids, shared_ids = self.cache[key]
        return again, scored, attributed, new_ids, shared_ids, donor_of


def gt_units(gt: Mapping[str, Any], run: w3.Run) -> dict[str, dict]:
    """unit id -> the ground-truth unit its sentence matches (footage units with ranges only)."""
    found = w3.units_by_text(run)
    out = {}
    for g in gt["units"]:
        match = found.get(w3.norm(g["text"]))
        if match is not None and g.get("ranges"):
            out.setdefault(match[1]["unit_id"], g)
    return out


def inside(t_ms: int, ranges: Sequence[Sequence[float]]) -> bool:
    return any(r0 * 1000 <= t_ms <= r1 * 1000 for r0, r1 in ranges)


def r3_units(attribution: Mapping[str, Any]) -> list[dict]:
    return [r for r in attribution["rows"] if r.get("stage") == "R3_NOT_LOOKED"]


def paired(before: Mapping[str, Any], after: Mapping[str, Any], key: str) -> dict[str, Any]:
    fixed = sum(1 for a, b in zip(before["rows"], after["rows"]) if not a[key] and b[key])
    broken = sum(1 for a, b in zip(before["rows"], after["rows"]) if a[key] and not b[key])
    return {"fixed": fixed, "broken": broken, "mcnemar_p_exact": mcnemar_exact(fixed, broken)}


def unit_paired(before: Mapping[str, bool], after: Mapping[str, bool]) -> dict[str, Any]:
    fixed = sum(1 for u in before if not before[u] and after.get(u))
    broken = sum(1 for u in before if before[u] and not after.get(u))
    return {"fixed": fixed, "broken": broken, "mcnemar_p_exact": mcnemar_exact(fixed, broken)}


def score_brief(scored: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("recall_at_1", "recall_at_3", "recall_at_5", "chosen_hit", "ready_precision", "ready_rate", "unmatched_footage")
    out = {k: scored[k] for k in keys}
    out["serious_silent_misses"] = len(scored["serious_silent_misses"])
    return out


def delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, int]:
    return {k: after[k][0] - before[k][0] for k in ("recall_at_1", "recall_at_3", "recall_at_5", "chosen_hit")}


def part_warnings(run: w3.Run) -> dict[str, dict[str, int]]:
    out = {}
    for part in run.order:
        counted: dict[str, int] = {}
        for w in run.logs[part].get("warnings") or ():
            counted[str(w)] = counted.get(str(w), 0) + 1
        out[part] = counted
    return out


def needs_review_count(run: w3.Run) -> int:
    return sum(1 for p in run.order for u in run.logs[p]["units"] if (u.get("coverage") or {}).get("needs_review"))


def attribution_delta(before: Mapping[str, Any], after: Mapping[str, Any], *, new_ids: Iterable[str], shared_ids: Iterable[str],
                      donor_of: Mapping[str, str], before_score: Mapping[str, Any] | None = None,
                      after_score: Mapping[str, Any] | None = None, warnings_before: Mapping[str, Mapping[str, int]] | None = None,
                      warnings_after: Mapping[str, Mapping[str, int]] | None = None, needs_review_delta: int = 0,
                      note: str = "") -> dict[str, Any]:
    """E5: how each unit's stage moved from ``before`` to ``after`` (both ``w3.attribute`` of the same ground truth)."""
    new_ids, shared_ids = set(new_ids), set(shared_ids)
    transitions: dict[str, int] = {}
    moved: dict[str, int] = {}
    fixed_r3 = still_r3 = 0
    via_new = shared_only = indirect = wrong_pick = 0
    by_donor: dict[str, int] = {}
    silent_new = []
    for a, b in zip(before["rows"], after["rows"]):
        if a.get("kind") == "missing":
            continue
        s0, s1 = a.get("stage"), b.get("stage")
        transitions[f"{s0}->{s1}"] = transitions.get(f"{s0}->{s1}", 0) + 1
        chosen = set(b.get("chosen") or ())
        if s0 == "R3_NOT_LOOKED":
            if s1 == "OK":
                fixed_r3 += 1
                if chosen & new_ids:
                    via_new += 1
                    for i in sorted(chosen & new_ids):
                        by_donor[donor_of.get(i, "unknown")] = by_donor.get(donor_of.get(i, "unknown"), 0) + 1
                        break
                elif chosen & shared_ids:
                    shared_only += 1
                else:
                    indirect += 1
            elif s1 == "R3_NOT_LOOKED":
                still_r3 += 1
            elif str(s1).startswith(("R4", "R5", "R6")):
                moved[str(s1)] = moved.get(str(s1), 0) + 1
        if chosen & (new_ids | shared_ids) and not b.get("chosen_hit"):
            wrong_pick += 1
        if b.get("r7") == "SILENT_MISS" and a.get("r7") != "SILENT_MISS":
            silent_new.append(b.get("unit"))
    out: dict[str, Any] = {"transitions": {k: transitions[k] for k in sorted(transitions)}, "fixed_r3": fixed_r3,
                           "moved_r3": {k: moved[k] for k in sorted(moved)}, "still_r3": still_r3,
                           "new_silent_misses": sorted(u for u in silent_new if u), "fixed_via_new_evidence": via_new,
                           "fixed_with_shared_only": shared_only, "fixed_indirect": indirect,
                           "new_evidence_wrong_pick": wrong_pick, "fixed_by_donor_run": {k: by_donor[k] for k in sorted(by_donor)},
                           "note": note}
    if before_score is not None and after_score is not None:
        rows = list(zip(before_score["rows"], after_score["rows"]))
        out["serious_silent_misses_delta"] = len(after_score["serious_silent_misses"]) - len(before_score["serious_silent_misses"])
        out["lost_hits"] = {"chosen_hit": sum(1 for a, b in rows if a["chosen_hit"] and not b["chosen_hit"]),
                            "hit_at_5": sum(1 for a, b in rows if a["hit_at_5"] and not b["hit_at_5"])}
        false_ready = lambda s: sum(1 for r in s["rows"] if r["state"] == "READY" and not r["chosen_hit"])  # noqa: E731
        out["false_ready_delta"] = false_ready(after_score) - false_ready(before_score)
        out["ready_gained_correct"] = sum(1 for a, b in rows if a["state"] != "READY" and b["state"] == "READY" and b["chosen_hit"])
        out["ready_precision"] = {"before": before_score["ready_precision"], "after": after_score["ready_precision"]}
    if warnings_before is not None and warnings_after is not None:
        changed = {}
        for part in sorted(set(warnings_before) | set(warnings_after)):
            a, b = warnings_before.get(part) or {}, warnings_after.get(part) or {}
            d = sum(b.values()) - sum(a.values())
            if d or a != b:
                changed[part] = {"added": sum(max(0, b.get(k, 0) - a.get(k, 0)) for k in b),
                                 "removed": sum(max(0, a.get(k, 0) - b.get(k, 0)) for k in a)}
        out["extra_warnings"] = {"per_part": changed, "needs_review_delta": needs_review_delta}
    return out


def acquisition(row: Mapping[str, Any], ranges: Sequence[Sequence[float]], replayed: Sequence[Replayed], before_row: Mapping[str, Any]) -> str:
    if before_row.get("hit_looked"):
        return "OBSERVED_BEFORE"
    part = part_of_unit(str(row.get("unit") or ""))
    mine = [r for r in replayed if r.sample.part == part]
    hitting = [r for r in mine if inside(r.sample.t_ms, ranges)]
    if hitting:
        return "SIMULATED_FRAME_INSIDE_GT" if any(r.status == "SIMULATED" for r in hitting) else "SELECTED_NOT_SIMULABLE"
    if any(r.status == "SIMULATED" and r.sample.unit_id == row.get("unit") for r in mine):
        return "SIMULATED_FRAME_OUTSIDE_GT"
    return "NOT_SELECTED"


def e2_metrics(ctx: Context, base: str, sel: tg.Selection, view: tg.PlanningView) -> dict[str, Any]:
    run = ctx.base_runs[base]
    truth = gt_units(ctx.gt, run)
    r3 = r3_units(ctx.attributions[base])
    samples = sel.samples
    cands_of = {p: w3.candidates_of(run.logs[p]) for p in run.order}
    with_frame = {r["unit"]: any(s.part == part_of_unit(r["unit"]) and inside(s.t_ms, r["ranges"]) for s in samples) for r in r3}
    own_frame = sum(1 for r in r3 if any(s.unit_id == r["unit"] and inside(s.t_ms, r["ranges"]) for s in samples))
    own_cand = sum(1 for r in r3 if any(s.unit_id == r["unit"] and s.candidate_id in cands_of[s.part]
                                        and w3.hits(cands_of[s.part][s.candidate_id], r["ranges"]) for s in samples))
    with_unit = [s for s in samples if s.unit_id is not None]
    precise = sum(1 for s in with_unit if s.unit_id in truth and inside(s.t_ms, truth[s.unit_id]["ranges"]))
    times = {(p.part, s.candidate_id): s.keyframe_ms for p in view.parts for s in p.shots}
    eligible = [(pid, cid, times.get((pid, cid))) for pid, cid in _eligible_parts(view, sel.eligible)]
    inside_counts = [sum(1 for pid, _, t in eligible if pid == part_of_unit(r["unit"]) and t is not None and inside(t, r["ranges"]))
                     for r in r3]
    per_part: dict[str, int] = {}
    per_source: dict[str, int] = {}
    basis: dict[str, int] = {}
    for s in samples:
        per_part[s.part] = per_part.get(s.part, 0) + 1
        per_source[s.source_id] = per_source.get(s.source_id, 0) + 1
        basis[s.t_basis] = basis.get(s.t_basis, 0) + 1
    positions = []
    for s in with_unit:
        part = next(p for p in view.parts if p.part == s.part)
        unit = next((u for u in part.units if u.unit_id == s.unit_id), None)
        if unit is None:
            continue
        low, high = tg.forward_bounds(part, unit).get(s.source_id, (0, 0))
        if high > low:
            positions.append(max(0, min(999, (s.t_ms - low) * 1000 // (high - low))))
    return {"selected": len(samples), "eligible": len(sel.eligible), "per_part": dict(sorted(per_part.items())),
            "per_source": dict(sorted(per_source.items())), "keyframe_basis_counts": dict(sorted(basis.items())),
            "r3_units": len(r3), "r3_units_with_hitting_frame_selected": sum(with_frame.values()),
            "loc_hit_units_frame": own_frame, "loc_hit_units_candidate": own_cand,
            "loc_precision_frame": [precise, len(with_unit)] if with_unit else None,
            "chance_r3_units": chance_hits(len(eligible), inside_counts, len(samples)),
            "early_bias": quartiles(positions), "notes": {k: sel.notes[k] for k in sorted(sel.notes)},
            "_r3_with_frame": with_frame}


def _eligible_parts(view: tg.PlanningView, ids: Sequence[str]) -> list[tuple[str, str]]:
    wanted = set(ids)
    out = []
    for p in view.parts:
        if p.kind != "item":
            continue
        for s in p.shots:
            if s.candidate_id in wanted:
                out.append((p.part, s.candidate_id))
                wanted.discard(s.candidate_id)
    return out


def e4_cost(replayed: Sequence[Replayed], cost_per_call: float | None, cost_unit: str | None) -> dict[str, Any]:
    per_part: dict[str, int] = {}
    paid: dict[str, int] = {}
    per_source: dict[str, int] = {}
    for r in replayed:
        per_part[r.sample.part] = per_part.get(r.sample.part, 0) + 1
        per_source[r.sample.source_id] = per_source.get(r.sample.source_id, 0) + 1
        if not r.own_cache:
            paid[r.sample.part] = paid.get(r.sample.part, 0) + 1
    calls = sum(math.ceil(n / vision.BATCH) for n in paid.values())
    return {"frames_job": len(replayed), "frames_per_part": dict(sorted(per_part.items())),
            "frames_per_source": dict(sorted(per_source.items())), "calls_est": calls,
            "calls_label": "estimate; FORMAT retries unknown",
            "cost": None if cost_per_call is None else r4(calls * cost_per_call),
            "cost_unit": cost_unit if cost_per_call is not None else None,
            "cost_basis": "given per call" if cost_per_call is not None else "not given"}


def evaluate_arm(ctx: Context, arm: Arm, sel: tg.Selection, view: tg.PlanningView) -> dict[str, Any]:
    replayed = [ctx.index.for_base(arm.base).match(s) for s in sel.samples]
    again, scored, attributed, new_ids, shared_ids, donor_of = ctx.replay_with(arm.base, replayed)
    before_score, before_attr = ctx.scores[arm.base], ctx.attributions[arm.base]
    simulated = [r for r in replayed if r.status == "SIMULATED"]
    reasons: dict[str, int] = {}
    bases: dict[str, int] = {}
    for r in replayed:
        if r.status != "SIMULATED":
            reasons[str(r.reason)] = reasons.get(str(r.reason), 0) + 1
        else:
            bases[str(r.match_basis)] = bases.get(str(r.match_basis), 0) + 1
    note = PARTIAL_NOTE.format(k=len(simulated), n=len(replayed))
    e2 = e2_metrics(ctx, arm.base, sel, view)
    truth = gt_units(ctx.gt, ctx.base_runs[arm.base])
    rows = []
    for a, b, sa, sb in zip(before_attr["rows"], attributed["rows"], before_score["rows"], scored["rows"]):
        if a.get("kind") == "missing":
            continue
        ranges = (truth.get(a.get("unit")) or {}).get("ranges") or a.get("ranges") or []
        rows.append({"unit": a.get("unit"), "stage_before": a.get("stage"), "stage_after": b.get("stage"),
                     "chosen_hit": [bool(sa["chosen_hit"]), bool(sb["chosen_hit"])],
                     "hit_at_5": [bool(sa["hit_at_5"]), bool(sb["hit_at_5"])],
                     "acquisition": acquisition(b, ranges, replayed, a) if a.get("unit") else "UNMATCHED"})
    e5 = attribution_delta(before_attr, attributed, new_ids=new_ids, shared_ids=shared_ids, donor_of=donor_of,
                           before_score=before_score, after_score=scored, warnings_before=part_warnings(ctx.base_runs[arm.base]),
                           warnings_after=part_warnings(again),
                           needs_review_delta=needs_review_count(again) - needs_review_count(ctx.base_runs[arm.base]), note=note)
    acq: dict[str, int] = {}
    for r in rows:
        acq[r["acquisition"]] = acq.get(r["acquisition"], 0) + 1
    e5["acquisition"] = dict(sorted(acq.items()))
    return {"id": arm.id, "regime": arm.regime, "kind": arm.kind, "strategy": arm.strategy, "window_s": arm.window_s,
            "budget_label": arm.budget_label, "budget": arm.budget, "view_basis": view.basis,
            "eligible_restriction": view.eligible_restriction, "oracle": False, "e2": e2,
            "e3": {"selected": len(replayed), "simulated": {"own_cache": sum(r.own_cache for r in simulated),
                                                            "donor": sum(not r.own_cache for r in simulated)},
                   "not_simulable": dict(sorted(reasons.items())), "simulable_fraction": [len(simulated), len(replayed)],
                   "shared": len(shared_ids), "match_basis_counts": dict(sorted(bases.items())), "note": note},
            "e4": e4_cost(replayed, ctx.cost_per_call, ctx.cost_unit),
            "score": score_brief(scored), "delta_vs_A": delta(ctx.scores["A"], scored),
            "delta_vs_base": delta(before_score, scored),
            "paired": {"chosen_hit": paired(ctx.scores["A"], scored, "chosen_hit"),
                       "hit_at_5": paired(ctx.scores["A"], scored, "hit_at_5")},
            "e5": e5, "selection": [dataclasses.asdict(s) for s in sel.samples],
            "samples": [r.to_dict() for r in replayed], "unit_rows": rows}


# --- upper bounds (use the ground truth; reported apart, never as a selector) ---

def ORACLE_select_gt_window(view: tg.PlanningView, gt: Mapping[str, Any], run: w3.Run, params: tg.SelectParams) -> tg.Selection:
    """Upper bound: the ``prior`` machinery centred on the middle of each ground-truth range."""
    truth = gt_units(gt, run)
    centres: dict[str, dict[str, int]] = {}
    for p in view.parts:
        for u in p.units:
            g = truth.get(u.unit_id)
            if g is None:
                continue
            r0, r1 = g["ranges"][0]
            centres[u.unit_id] = {sid: int((r0 + r1) / 2 * 1000) for sid, _, _ in p.trims}
    return tg.select_centred(view, params, centres)


def ORACLE_acq_at_selected(gt: Mapping[str, Any], run: w3.Run, samples: Sequence[tg.Sample], review: bool,
                           memo: PlanMemo | None) -> w3.Run:
    """Upper bound: each selected frame is told the sentence of the ground-truth unit whose range it lies in (the unit
    it was selected for when that range holds it, else the first unit of the part, by id, whose range does), the same
    rule for every arm, as a real look at that frame would describe it; every other selected frame gets an empty
    answer. Separates localisation from interpretation without donors."""
    truth = gt_units(gt, run)
    additions: dict[str, dict[str, ev.Item]] = {}
    for s in samples:
        g = truth.get(s.unit_id) if s.unit_id else None
        if g is None or not inside(s.t_ms, g["ranges"]):
            g = next((t for uid, t in sorted(truth.items()) if part_of_unit(uid) == s.part and inside(s.t_ms, t["ranges"])), None)
        if g is not None:
            item = ev.Item("vision", 900, g["text"], (), "oracle")
        else:
            item = ev.Item("vision", 0, "", (), "oracle-empty")
        additions.setdefault(s.part, {})[s.candidate_id] = item

    def evidence_for(part: str, log: Mapping[str, Any]) -> dict[str, ev.Evidence] | None:
        if part not in additions:
            return None
        out = {}
        for c in log.get("candidates") or ():
            found = evidence_from(c["evidence"]) if c.get("evidence") else ev.Evidence(c["id"])
            if c["id"] in additions[part]:
                found.add(additions[part][c["id"]])
            out[c["id"]] = found
        return out

    if not additions:
        return run
    with replay_context(review=review, memo=memo):
        return w3.replay(run, evidence_for=evidence_for, parts=replay_parts(run, additions))


def upper_bounds(ctx: Context, arms: Sequence[dict], windows: Sequence[str], selected_matched: Sequence[tuple[dict, tg.Selection]]
                 ) -> list[dict]:
    prep = ctx.prep
    out = []
    a_score = ctx.scores["A"]

    def entry(uid: str, kind: str, scored: Mapping[str, Any], **extra: Any) -> dict:
        return {"id": uid, "oracle": True, "kind": kind, "score": score_brief(scored), "delta_vs_A": delta(a_score, scored),
                "paired": {"chosen_hit": paired(a_score, scored, "chosen_hit"), "hit_at_5": paired(a_score, scored, "hit_at_5")},
                **extra}

    views = Views(prep, ctx.index)
    for regime in REGIMES:
        for w in windows:
            arm = Arm(f"ORACLE_select_gt_window:{regime}:W={w if w == 'fwd' else w + 's'}:+matched", regime, "targeted",
                      "oracle_window", w, "+matched", prep.matched, "A")
            view = views.get("A", regime)
            sel = ORACLE_select_gt_window(view, ctx.gt, prep.A, arm.params(ctx.target_units))
            result = evaluate_arm(ctx, arm, sel, view)
            result.update({"oracle": True})
            out.append(result)
            if regime == "open":
                acq = ORACLE_acq_at_selected(ctx.gt, prep.A, sel.samples, ctx.review, ctx.memo)
                out.append(entry(f"ORACLE_ACQ_at_selected[{arm.id}]", "acq_at_selected", w3.score(ctx.gt, acq),
                                 selected=len(sel.samples)))
    for arm, sel in selected_matched:
        acq = ORACLE_acq_at_selected(ctx.gt, prep.A, sel.samples, ctx.review, ctx.memo)
        out.append(entry(f"ORACLE_ACQ_at_selected[{arm['id']}]", "acq_at_selected", w3.score(ctx.gt, acq), selected=len(sel.samples)))
    for w in windows:
        if w == "fwd":
            continue
        found = w3.hint(ctx.gt, prep.A, float(w))
        out.append({"id": f"ORACLE_LOCATION_hint_±{w}s", "oracle": True, "kind": "hint", "hint": w3._brief(found)})
    with replay_context(review=ctx.review, memo=ctx.memo):
        looked = w3.oracle_evidence(ctx.gt, prep.loaded.run, only_looked=True)
        every = w3.oracle_evidence(ctx.gt, prep.loaded.run, only_looked=False)
    out.append(entry("ORACLE_INTERPRETATION_looked_only", "oracle_evidence", w3.score(ctx.gt, looked)))
    out.append(entry("ORACLE_ACQ+INTERP_all_hits", "oracle_evidence", w3.score(ctx.gt, every)))
    for item in out:
        item["id"] = item["id"] if item["id"].startswith("ORACLE_") else f"ORACLE_{item['id']}"
    return sorted(out, key=lambda e: e["id"])


# --- E1 ---

def e1_baseline(gt: Mapping[str, Any], prep: Prepared, index: DonorIndex, ctx: Context | None = None) -> dict[str, Any]:
    loaded, run, A = prep.loaded, prep.loaded.run, prep.A
    saved = w3.score(gt, run)
    replayed_with_review = w3.score(gt, w3.replay(run))
    scored_a = ctx.scores["A"] if ctx else w3.score(gt, A)
    attributed = ctx.attributions["A"] if ctx else w3.attribute(gt, A)
    equal = {p: w3.same_plan(run.plans[p], A.plans[p]) for p in run.order}
    seen_items = tg.observed_ids(prep.data_A)
    frames: dict[str, Any] = {"per_part": {}, "per_source": {}}
    for p in prep.view_A.parts:
        log = run.logs[p.part]
        own = [c for c in log.get("candidates") or () if vision_items(c)]
        shared = sum(1 for c in own if prep.classes.get(c["id"]) in ("broad_shared", "targeted_shared"))
        frames["per_part"][p.part] = {
            "pool": len(p.shots), "usable": sum(1 for s in p.shots if s.usable and s.has_keyframe),
            "observed_item_jobwide": sum(1 for s in p.shots if s.observed), "observed_own_log": len(own),
            "vision_items": sum(len(vision_items(c)) for c in own), "shared_duplicates_est": shared,
            "log_vision": vision_counts(log.get("vision")),
            "coverage_map": {k: v for k, v in (log.get("coverage_map") or {}).items() if k != "unseen"},
            "targeted_frames_part": prep.targeted_per_part.get(p.part)}
        if p.kind == "item":
            for s in p.shots:
                row = frames["per_source"].setdefault(s.source_key[:16] or s.source_id, {"pool": 0, "observed": 0})
                row["pool"] += 1
                row["observed"] += int(s.observed)
    frames["job"] = {"pool_item_parts": sum(len(p.shots) for p in prep.view_A.parts if p.kind == "item"),
                     "observed_item_jobwide": len(seen_items),
                     "program_vision": vision_counts(loaded.program.get("vision"))}
    truth = gt_units(gt, A)
    footage = [g for g in gt["units"] if g.get("kind") != "missing"]
    by_text = w3.units_by_text(A)
    shots_of = {p.part: p.shots for p in prep.view_A.parts}
    eac = {"candidate": 0, "frame": 0, "ranges": 0, "range_candidate": 0, "range_frame": 0}
    for g in footage:
        ranges = g.get("ranges") or []
        eac["ranges"] += len(ranges)
        match = by_text.get(w3.norm(g["text"]))
        if match is None or not ranges:
            continue
        part, _ = match
        seen = [s for s in shots_of.get(part, ()) if s.observed]
        logged = w3.candidates_of(A.logs[part])
        eac["candidate"] += any(w3.hits(logged[s.candidate_id], ranges) for s in seen if s.candidate_id in logged)
        eac["frame"] += any(inside(s.keyframe_ms, ranges) for s in seen)
        for r in ranges:
            eac["range_candidate"] += any(w3.hits(logged[s.candidate_id], [r]) for s in seen if s.candidate_id in logged)
            eac["range_frame"] += any(inside(s.keyframe_ms, [r]) for s in seen)
    disagree = 0
    for row in attributed["rows"]:
        if row.get("stage") in (None, "UNMATCHED") or row.get("kind") == "missing":
            continue
        part = part_of_unit(row["unit"])
        logged = w3.candidates_of(A.logs[part])
        by_vision = [i for i in row.get("hit_ids") or () if i in logged and vision_items(logged[i])]
        disagree += bool(row.get("hit_looked")) != bool(by_vision)
    flag_view = prep.view_A0 or prep.view_A
    positions = []
    for p in flag_view.parts:
        if p.kind != "item":
            continue
        for s in p.shots:
            if prep.classes.get(s.candidate_id) != "targeted":
                continue
            for u in tg.wanted_units(p, "flagged"):
                low, high = tg.forward_bounds(p, u).get(s.source_id, (0, 0))
                if high > low and low <= s.keyframe_ms <= high:
                    positions.append((s.keyframe_ms - low) * 1000 // (high - low))
                    break
    recall = {1: 0, 3: 0, 5: 0}
    scorers: dict[str, align.Scorer] = {}
    for g in footage:
        match = by_text.get(w3.norm(g["text"]))
        if match is None or not g.get("ranges"):
            continue
        part, u = match
        p = next(x for x in prep.view_A.parts if x.part == part)
        if part not in scorers:
            scorers[part] = align.Scorer([s.candidate for s in p.shots if s.usable] or [s.candidate for s in p.shots], p.evidence)
        scorer = scorers[part]
        unit = align.Unit(u["unit_id"], u["text"], int(u["start_ms"]), int(u["end_ms"]), "narration")
        ranked = sorted(p.shots, key=lambda s: (-tg.relevance(scorer, unit, s), s.start_ms, s.candidate_id))
        logged = w3.candidates_of(A.logs[part])
        for k in recall:
            recall[k] += any(w3.hits(logged[s.candidate_id], g["ranges"]) for s in ranked[:k] if s.candidate_id in logged)
    basis_counts: dict[str, int] = {}
    for p in prep.view_A.parts:
        for s in p.shots:
            basis_counts[s.keyframe_basis] = basis_counts.get(s.keyframe_basis, 0) + 1
    closed = Views(prep, index).get("A", "closed_world")
    reachable = len(tg.select_detail(closed, tg.SelectParams("all_eligible", None, None)).eligible)
    n = len(footage)
    return {"saved": score_brief(saved), "replayed_with_review": score_brief(replayed_with_review), "A": score_brief(scored_a),
            "equal_plans": sum(equal.values()), "parts": len(equal), "different": [p for p, v in equal.items() if not v],
            "review_files": {p: loaded.review_files.get(p, {"present": False, "nonempty": False, "pins": 0, "excludes": 0,
                                                              "approvals": 0}) for p in run.order},
            "edits_basis": {p: no_review_edits(run, p, run.logs[p])[1] for p in run.order},
            "frames": frames, "matched_budget": prep.matched, "matched_budget_basis": prep.matched_basis,
            "respend_budget": dict(prep.respend),
            "broad_reconstruction": {k: v for k, v in prep.broad.items() if k != "picked"} | {"picked_count": len(prep.broad.get("picked") or [])},
            "classification": prep.classification,
            "a0_status": "RECONSTRUCTED" if prep.A0 is not None else "NOT_RECONSTRUCTED",
            "same_budget": "available" if prep.A0 is not None else SAME_BUDGET_UNAVAILABLE,
            "eac_candidate": [eac["candidate"], n], "eac_frame": [eac["frame"], n],
            "eac_range_candidate": [eac["range_candidate"], eac["ranges"]], "eac_range_frame": [eac["range_frame"], eac["ranges"]],
            "looked_basis_disagreements": disagree,
            "early_bias": {**quartiles(positions), "basis": "A0 view flags" if prep.view_A0 is not None else "A view flags"},
            "retrieval_recall_at_k": {"label": "unconstrained retrieval, supplementary",
                                      **{f"recall_at_{k}": [v, n] for k, v in recall.items()}},
            "keyframe_basis_counts": dict(sorted(basis_counts.items())), "donor_supply_reachable": reachable,
            "donor_shared_excluded": index.counts["donor_shared_excluded"],
            "donor_provider_mismatch": index.counts["donor_provider_mismatch"],
            "own_cache_frames": index.counts["own_cache_frames"], "donor_runs": index.donor_runs,
            "attribution": {"stages": attributed["stages"], "review": attributed["review"], "footage_units": attributed["footage_units"]},
            "signals_unavailable": list(prep.view_A.signals_unavailable)}


# --- preflight ---

def preflight(loaded: Loaded) -> dict[str, Any]:
    run = loaded.run
    work = loaded.folder / ".dubflow-work"
    failures = []
    if not (work / "spec.json").is_file():
        failures.append("MISSING_SPEC")
    single = False
    for part in run.order:
        plan = run.plans.get(part)
        if plan is None:
            failures.append(f"MISSING_PLAN:{part}")
            continue
        inputs = (plan.get("provenance") or {}).get("inputs") or {}
        for source in plan.get("sources") or ():
            sid = source["source_id"]
            if inputs.get(sid) not in run.analyses:
                if len(run.analyses) == 1:
                    single = True
                else:
                    failures.append(f"MISSING_ANALYSIS:{sid}")
    referenced = sorted({c["keyframe"] for log in run.logs.values() for c in log.get("candidates") or () if c.get("keyframe")})
    missing = [n for n in referenced if not loaded.jpeg.get(n)]
    if referenced and len(missing) * 1000 > MISSING_JPEG_LIMIT_MILLI * len(referenced):
        failures.append(f"MISSING_KEYFRAMES:{len(missing)}")
    if loaded.provider_model is None:
        failures.append("MISSING_VISION_REPORT")
    keys = {source_keys(loaded, p)[1] for p in run.order}
    prompt = "recorded" if loaded.cache and any((r.get("made_by") or {}).get("prompt") for r in loaded.cache.values()) else "unknown"
    return {"status": "FAIL" if failures else "PASS", "failures": sorted(dict.fromkeys(failures)), "parts": len(run.order),
            "spec": (work / "spec.json").is_file(), "analysis_basis": "single_analysis" if single else "provenance",
            "keyframes_referenced": len(referenced), "keyframes_missing": len(missing),
            "program_vision_report": loaded.provider_model is not None,
            "part_logs_without_vision_report": sum(1 for p in run.order if not run.logs[p].get("vision")),
            "vision_cache_folder": loaded.cache is not None,
            "classification_basis": cache_basis(loaded),
            "source_key_basis": "+".join(sorted(keys)), "prompt_version_basis": prompt}


# --- the whole study ---

PREREGISTERED = "PREREGISTERED"
PREREG_MISMATCH = "PREREGISTRATION MISMATCH: results exploratory"
NOT_PREREGISTERED = "NOT PREREGISTERED: results exploratory"
PREREG_UNVERIFIED = "PREREGISTRATION UNVERIFIED: results exploratory"


def git_is_ancestor(commit: str) -> bool | None:
    """Whether ``commit`` is an ancestor of (or is) the checked-out HEAD; None when git or the repository is not there."""
    try:
        head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=str(ROOT), capture_output=True, timeout=60)
        if head.returncode != 0:
            return None
        done = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=str(ROOT), capture_output=True,
                              timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.returncode == 0


def prereg_status(path: Path | None) -> dict[str, Any]:
    """The pre-registration record against the code that runs: the selector (targeting.py) and the analysis
    (targeted.py: the metric, the arms, the matched budget and the decision rule) by digest, the primary arm, metric,
    comparators and run count by value, and the record's commit as an ancestor of HEAD. It fails closed: without git
    (a source archive, no repository) the ancestry cannot be shown, so matching digests alone give PREREG_UNVERIFIED."""
    current = code_digest(HERE / "targeting.py")
    analysis = code_digest(HERE / "targeted.py")
    try:
        record = json.loads((path or PREREG_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    mismatches = []
    if record.get("targeting_sha256") != current:
        mismatches.append("targeting_sha256")
    if record.get("analysis_sha256") != analysis:
        mismatches.append("analysis_sha256")
    for key, value in (("primary_arm", PRIMARY_ARM), ("primary_metric", PRIMARY_METRIC), ("comparators", list(COMPARATORS)),
                       ("required_runs", REQUIRED_RUNS)):
        if record.get(key) != value:
            mismatches.append(key)
    commit = record.get("commit")
    if not commit:
        commit_check = "no_commit"
    else:
        found = git_is_ancestor(str(commit))
        commit_check = "git_unavailable" if found is None else ("ancestor" if found else "not_ancestor")
    if mismatches:
        label = PREREG_MISMATCH
    elif commit_check == "ancestor":
        label = PREREGISTERED
    elif commit_check == "git_unavailable":
        label = PREREG_UNVERIFIED
    else:
        label = NOT_PREREGISTERED
    return {"commit": commit, "commit_check": commit_check, "targeting_sha256": record.get("targeting_sha256"),
            "current_targeting_sha256": current, "analysis_sha256": record.get("analysis_sha256"),
            "current_analysis_sha256": analysis, "match": not mismatches, "mismatches": mismatches,
            "primary_arm": record.get("primary_arm"), "primary_metric": record.get("primary_metric"), "label": label}


def code_sha256() -> dict[str, str]:
    out = {name: code_digest(HERE / name) for name in CODE_FILES}
    out["vision.PROMPT_VERSION"] = vision.PROMPT_VERSION
    out["candidates.RECIPE"] = cands.RECIPE
    return out


@dataclass
class Options:
    budgets: str = "matched,2x,4x"
    windows: str = "5,10,15,30,fwd"
    tolerance_ms: int = 500
    donor_scope: str = "items"
    share_duplicates: bool = True
    review: bool = False
    target_units: str = "flagged"
    cost_per_call: float | None = None
    cost_unit: str | None = None
    grid_supporting: str = "matched"
    blindness: str = "subprocess"
    prereg: Path | None = None
    memo: bool = True


def all_arm_selection(target: Prepared, donors: Sequence[Prepared], options: Options, full: bool) -> dict[str, list[dict]]:
    """Every non-oracle arm's selection for ``target``: no ground truth is read on this path."""
    index = DonorIndex(target, donors, scope=options.donor_scope, tolerance_ms=options.tolerance_ms)
    arms = arm_grid(target, budgets=options.budgets, windows=options.windows, full=full)
    return samples_json(select_arms(target, index, arms, options.target_units))


def blindness_subprocess(target: Prepared, donors: Sequence[Prepared], options: Options, full: bool) -> str:
    argv = [sys.executable, "-B", "-m", "tests.mastering.longform.targeted", "select", str(target.loaded.folder), "--all-arms",
            "--donors", *[str(d.loaded.folder) for d in donors], "--budgets", options.budgets, "--windows-s", options.windows,
            "--tolerance-ms", str(options.tolerance_ms), "--donor-scope", options.donor_scope,
            "--target-units", options.target_units, "--review-edits", "on" if options.review else "off",
            "--grid", "full" if full else "matched", "--json", "-"]
    # the child's stdout is UTF-8 bytes (_stdout); its stderr may be in the locale code page on Windows
    done = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    if done.returncode != 0:
        return f"ERROR {done.returncode}: {done.stderr[-400:]}"
    return done.stdout


def interpretability(arms: list[dict]) -> None:
    """A budget above matched whose simulated answers do not grow over the next lower budget says nothing new."""
    series: dict[tuple, list[dict]] = {}
    for a in arms:
        if a["budget_label"].startswith("+") and a["kind"] in ("uniform", "production", "targeted"):
            series.setdefault((a["regime"], a["strategy"], a["window_s"]), []).append(a)
    for _, items in series.items():
        items.sort(key=lambda a: a["budget"] or 0)
        for lower, higher in zip(items, items[1:]):
            grew = sum(higher["e3"]["simulated"].values()) > sum(lower["e3"]["simulated"].values())
            if not grew or higher["e3"].get("evidence_limited"):
                higher["delta_vs_A"] = NOT_INTERPRETABLE


def evaluate_run(gt: Mapping[str, Any], target: Prepared, donors: Sequence[Prepared], options: Options, *, full: bool,
                 memo: PlanMemo | None) -> tuple[dict[str, Any], dict[str, list[dict]], dict[str, float]]:
    times: dict[str, float] = dict(target.times)
    began = time.perf_counter()
    index = DonorIndex(target, donors, scope=options.donor_scope, tolerance_ms=options.tolerance_ms)
    ctx = Context(gt, target, index, memo, options.review, options.tolerance_ms, options.share_duplicates, options.target_units,
                  options.cost_per_call, options.cost_unit)
    times["context"] = time.perf_counter() - began
    began = time.perf_counter()
    e1 = e1_baseline(gt, target, index, ctx)
    times["e1"] = time.perf_counter() - began
    began = time.perf_counter()
    arms = arm_grid(target, budgets=options.budgets, windows=options.windows, full=full)
    selected = select_arms(target, index, arms, options.target_units)
    times["select"] = time.perf_counter() - began
    began = time.perf_counter()
    results = []
    for arm in arms:
        if arm.id not in selected:
            results.append({"id": arm.id, "regime": arm.regime, "kind": arm.kind, "strategy": arm.strategy,
                            "window_s": arm.window_s, "budget_label": arm.budget_label, "budget": arm.budget, "oracle": False,
                            "status": SAME_BUDGET_UNAVAILABLE})
        else:
            sel, view = selected[arm.id]
            results.append(evaluate_arm(ctx, arm, sel, view))
        if arm.kind == "respend":
            results[-1]["respend_budget"] = dict(target.respend)
    times["arms"] = time.perf_counter() - began
    evaluated = [a for a in results if "e3" in a]
    reachable = e1["donor_supply_reachable"]
    ceiling = next((a for a in evaluated if a["kind"] == "ceiling"), None)
    for a in evaluated:
        a["e3"]["donor_supply_reachable"] = reachable
        a["e3"]["evidence_limited"] = sum(a["e3"]["simulated"].values()) >= reachable
        if ceiling is not None:
            d = ceiling["delta_vs_A"]
            a["e3"]["gain_fraction_of_ceiling"] = {"recall_at_5": [a["delta_vs_A"]["recall_at_5"], d["recall_at_5"]],
                                                   "chosen_hit": [a["delta_vs_A"]["chosen_hit"], d["chosen_hit"]]}
    # the primary metric against the production rule and the shuffled control, per R3 unit
    by_id = {a["id"]: a for a in evaluated}
    for a in evaluated:
        if a["kind"] not in ("targeted", "uniform", "production"):
            continue
        mine = a["e2"]["_r3_with_frame"]
        w = "fwd" if a["window_s"] in ("fwd", "-") else a["window_s"] + "s"
        for name, other_id in (("vs_production", f"{a['regime']}:P+:-:{a['budget_label']}"),
                               ("vs_shuffled_prior", f"{a['regime']}:shuffled_prior:W={w}:{a['budget_label']}")):
            other = by_id.get(other_id)
            if other is not None and other is not a:
                a["e2"][name] = {"arm": other_id, **unit_paired(other["e2"]["_r3_with_frame"], mine),
                                 "comparator_r3_with_frame": other["e2"]["r3_units_with_hitting_frame_selected"]}
    interpretability(evaluated)
    began = time.perf_counter()
    matched_open = [(a, selected[a["id"]][0]) for a in evaluated if a["regime"] == "open" and a["budget_label"] == "+matched"]
    bounds = upper_bounds(ctx, evaluated, parse_windows(options.windows), matched_open)
    times["upper_bounds"] = time.perf_counter() - began
    for a in results + bounds:
        if "e2" in a:
            a["e2"].pop("_r3_with_frame", None)
    out = {"e1": e1, "arms": results, "upper_bounds": bounds,
           "matching": {"tolerance_ms": options.tolerance_ms,  # labels near misses only; never admits an answer "donor_runs": index.donor_runs, **index.counts,
                        "key_basis_status": index.key_basis_status,
                        # the basis without names: prompt version, a digest of provider|model, and which label set
                        "key_basis": {"prompt": index.key_basis[0],
                                      "provider_model_sha12": sha256_bytes(f"{index.key_basis[1]}|{index.key_basis[2]}".encode("utf-8"))[:12],
                                      "labels": "moment" if tuple(index.key_basis[3]) == tuple(ev.MOMENT_LABELS) else "all"}
                        if index.key_basis else None,
                        "provenance": dict(sorted(index.provenance.items()))}}
    return out, samples_json(selected), times


def decision(runs: Mapping[str, dict], primary: str, required_runs: int = REQUIRED_RUNS) -> dict[str, Any]:
    rule = ("localisation supported on REAL-DEV only if, on the primary run, the primary arm has more R3 units with a hitting "
            f"frame than both P+ and shuffled_prior (exact McNemar p < 0.05 against P+), beats both in each of the {required_runs} "
            "runs, and exceeds chance_r3_units; recall and cost are decided only by the approved real-call batch")
    per_run = []
    ok_all = True
    for name in sorted(runs):
        arm = next((a for a in runs[name]["arms"] if a["id"] == PRIMARY_ARM and "e2" in a), None)
        e2 = arm["e2"] if arm is not None else {}
        vs_prod, vs_shuf = e2.get("vs_production") or {}, e2.get("vs_shuffled_prior") or {}
        if arm is None or (vs_prod.get("arm"), vs_shuf.get("arm")) != COMPARATORS:
            per_run.append({"run": name, "result": "unavailable"})
            continue
        mine = e2[PRIMARY_METRIC]
        prod, shuf = vs_prod.get("comparator_r3_with_frame"), vs_shuf.get("comparator_r3_with_frame")
        beats = mine > prod and mine > shuf
        row = {"run": name, "r3_with_frame": mine, "production": prod, "shuffled_prior": shuf, "chance": e2["chance_r3_units"],
               "p_vs_production": vs_prod.get("mcnemar_p_exact"), "beats_both": beats,
               "above_chance": mine > e2["chance_r3_units"]}
        per_run.append(row)
        ok_all = ok_all and beats
    main = next((r for r in per_run if r["run"] == primary), None)
    if len(runs) < required_runs:
        result, reason = "unavailable", f"{len(runs)} of the {required_runs} runs the rule names"
    elif any("beats_both" not in r for r in per_run):
        result, reason = "unavailable", "a run has no primary-arm or comparator result"
    elif main is None:
        result, reason = "unavailable", "the primary run has no result"
    else:
        p = main.get("p_vs_production")
        reason = None
        result = "supported" if (main["beats_both"] and p is not None and p < 0.05 and ok_all and main["above_chance"]) else "not supported"
    return {"primary_arm": PRIMARY_ARM, "metric": PRIMARY_METRIC, "comparators": list(COMPARATORS), "rule": rule,
            "required_runs": required_runs, "runs_given": len(runs), "result": result, "reason": reason, "per_run": per_run}


def gate_decision(found: dict[str, Any], blind: Mapping[str, str], prereg_label: str) -> dict[str, Any]:
    """The verdict only stands with every run's blindness check passed and the code pre-registered; otherwise it is
    replaced (the computed one is kept apart as ``result_before_gates``)."""
    computed = found["result"]
    found["result_before_gates"] = computed
    found["gates"] = {"blindness_check": "PASS" if blind and all(v == "PASS" for v in blind.values()) else
                      ("FAIL" if any(v == "FAIL" for v in blind.values()) else "NOT_RUN"),
                      "preregistration": prereg_label}
    if found["gates"]["blindness_check"] == "FAIL":
        found["result"] = "BLINDNESS_CHECK_FAILED"
    elif found["gates"]["blindness_check"] != "PASS":
        found["result"] = "unavailable: blindness check not run"
    elif prereg_label != PREREGISTERED and computed in ("supported", "not supported"):
        found["result"] = "exploratory"
    if prereg_label != PREREGISTERED:
        found["label"] = prereg_label
    return found


def across_runs(runs: Mapping[str, dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    ids = sorted({a["id"] for r in runs.values() for a in r["arms"] if "delta_vs_A" in a})
    for arm_id in ids:
        rows = [(name, next((a for a in runs[name]["arms"] if a["id"] == arm_id), None)) for name in sorted(runs)]
        rows = [(n, a) for n, a in rows if a is not None and isinstance(a.get("delta_vs_A"), dict)]
        if not rows:
            continue
        d5 = [a["delta_vs_A"]["recall_at_5"] for _, a in rows]
        out[arm_id] = {"runs": [n for n, _ in rows], "delta_r5": d5,
                       "fixed": [a["paired"]["hit_at_5"]["fixed"] for _, a in rows],
                       "broken": [a["paired"]["hit_at_5"]["broken"] for _, a in rows],
                       "sign_consistent_all_runs": len(rows) == len(runs) and (all(d > 0 for d in d5) or all(d < 0 for d in d5)),
                       "label": "runs not independent (shared footage, GT, 4/5 donors)"}
    return out


def real_call_batch(primary: str, arms: Sequence[dict], options: Options, prereg_label: str) -> dict[str, Any]:
    """The frames the named arms would send for real; ``preregistered`` is true only when the pre-registration gate of
    this same study passed (ancestry, digests and named values)."""
    frames: dict[tuple, dict] = {}
    for a in arms:
        if a["id"] not in BATCH_ARMS:
            continue
        for r in a.get("samples") or ():
            if r["status"] == "NOT_SIMULABLE":
                s = r["sample"]
                frames.setdefault((s["part"], s["candidate_id"]), {"part": s["part"], "candidate_id": s["candidate_id"],
                                                                   "t_ms": s["t_ms"], "jpeg_sha256": r["jpeg_sha256"]})
    listed = [frames[k] for k in sorted(frames)]
    per_part: dict[str, int] = {}
    for f in listed:
        per_part[f["part"]] = per_part.get(f["part"], 0) + 1
    calls = sum(math.ceil(n / vision.BATCH) for n in per_part.values())
    return {"run": primary, "arms": list(BATCH_ARMS), "regime": "open", "budget": "+matched", "frames": len(listed),
            "calls_est": calls, "calls_label": "estimate; FORMAT retries unknown",
            "cost": None if options.cost_per_call is None else r4(calls * options.cost_per_call),
            "cost_unit": options.cost_unit if options.cost_per_call is not None else None,
            "cost_basis": "given per call" if options.cost_per_call is not None else "not given",
            "preregistered": prereg_label == PREREGISTERED, "preregistration": prereg_label,
            "execution": "none; owner approval required", "list": listed}


def run_all(gt: Mapping[str, Any], primary: Path, runs: Sequence[Path], options: Options) -> tuple[dict, dict, dict]:
    perf: dict[str, Any] = {"phases": {}, "runs": {}}
    began_all = time.perf_counter()
    memo = PlanMemo() if options.memo else None
    folders: dict[str, Path] = {}
    for f in [primary, *runs]:
        folders.setdefault(Path(f).resolve().as_posix(), Path(f))
    loaded_all, seen_sha = [], set()
    for f in folders.values():
        loaded = load(f)
        if loaded.run_sha256 in seen_sha:
            continue
        seen_sha.add(loaded.run_sha256)
        loaded_all.append(loaded)
    loaded_all.sort(key=lambda x: x.name)
    primary_sha = load(primary).run_sha256
    checks = {x.name: preflight(x) for x in loaded_all}
    failed = {k: v["failures"] for k, v in checks.items() if v["status"] != "PASS"}
    if failed:
        raise SystemExit(f"PREFLIGHT_FAILED {json.dumps(failed, sort_keys=True)}")
    began = time.perf_counter()
    preps = {x.name: prepare(x, review=options.review, memo=memo) for x in loaded_all}
    perf["phases"]["prepare"] = time.perf_counter() - began
    primary_name = next(x.name for x in loaded_all if x.run_sha256 == primary_sha)
    results, blind = {}, {}
    for name, prep in sorted(preps.items()):
        donors = [p for n, p in sorted(preps.items()) if n != name]
        full = name == primary_name or options.grid_supporting == "full"
        began = time.perf_counter()
        result, embedded, times = evaluate_run(gt, prep, donors, options, full=full, memo=memo)
        perf["runs"][name] = {"phases": times, "total": time.perf_counter() - began}
        result["role"] = "primary" if name == primary_name else "supporting"
        results[name] = result
        mine = dumps(embedded)
        if options.blindness == "off":
            blind[name] = "NOT_RUN"
        else:
            if options.blindness == "subprocess":
                other = blindness_subprocess(prep, donors, options, full)
            else:
                fresh = prepare(load(prep.loaded.folder), review=options.review)
                other = dumps(all_arm_selection(fresh, [prepare(load(d.loaded.folder), light=True) for d in donors], options, full))
            blind[name] = "PASS" if other == mine else "FAIL"
    prereg = prereg_status(options.prereg)
    saved_r5 = [r["e1"]["saved"]["recall_at_5"][0] for r in results.values()]
    report = {
        "recipe": RECIPE, "code_sha256": code_sha256(), "prompt_version": vision.PROMPT_VERSION, "candidates_recipe": cands.RECIPE,
        "preregistration": {k: prereg[k] for k in ("commit", "commit_check", "targeting_sha256", "current_targeting_sha256",
                                                    "analysis_sha256", "current_analysis_sha256", "match", "mismatches",
                                                    "label")},
        "dataset": DATASET, "conclusion": "GENERALIZATION NOT VERIFIED; Gate B not claimed",
        "inputs": {"ground_truth_sha256": gt.get("sha256"),
                   "runs": [{"name": x.name, "run_sha256": x.run_sha256, "role": results[x.name]["role"]} for x in loaded_all]},
        "params": {"tolerance_ms": options.tolerance_ms, "budgets": options.budgets, "windows_s": options.windows,
                   "target_units": options.target_units, "frames": "mid", "donor_scope": options.donor_scope,
                   "share_duplicates": options.share_duplicates, "review_edits": "on" if options.review else "off",
                   "regimes": [*REGIMES, "ceiling"], "cost_per_call": options.cost_per_call, "cost_unit": options.cost_unit,
                   "grid_supporting": options.grid_supporting, "seed": None},
        "preflight": checks, "blindness_check": blind,
        "noise_floor_r5_descriptive": (max(saved_r5) - min(saved_r5)) if saved_r5 else 0,
        "runs": results, "across_runs": across_runs(results),
        "decision": gate_decision(decision(results, primary_name), blind, prereg["label"]),
        "real_call_batch": f"real-call-batch-{primary_name}.json", "caveats": list(CAVEATS)}
    batch = real_call_batch(primary_name, results[primary_name]["arms"], options, prereg["label"])
    batch["targeted_report"] = f"targeted-{primary_name}.json"
    perf["total"] = time.perf_counter() - began_all
    perf["peak_ram_mb"] = peak_ram_mb()
    perf["memo"] = {"hits": memo.hits, "misses": memo.misses} if memo else None
    perf["runs_loaded"] = [x.name for x in loaded_all]  # run names only: no local folder paths leave the machine
    return report, batch, perf


# --- markdown (from the JSON only) ---

def _pair(v: Any) -> str:
    return f"{v[0]}/{v[1]}" if isinstance(v, list) and len(v) == 2 else ("–" if v is None else str(v))


def markdown(report: Mapping[str, Any]) -> str:
    lines = [f"# Stage 2 (#815) targeted evidence: {report['decision'].get('label', report['preregistration']['label'])}", "",
             f"- Dataset: {report['dataset']}", f"- Conclusion: **{report['conclusion']}**",
             f"- Pre-registration: {report['preregistration']['label']} (commit {report['preregistration']['commit_check']}; "
             f"targeting sha256 {str(report['preregistration']['current_targeting_sha256'])[:12]}, analysis sha256 "
             f"{str(report['preregistration']['current_analysis_sha256'])[:12]})",
             f"- Decision ({report['decision']['primary_arm']}, {report['decision']['metric']}): **{report['decision']['result']}**"
             + (f" ({report['decision']['reason']})" if report['decision'].get('reason') else ""),
             f"- Blindness check: {', '.join(f'{k} {v}' for k, v in sorted(report['blindness_check'].items()))}",
             f"- Real-call batch: {report['real_call_batch']} (not executed; owner approval required)", ""]
    for name, run in sorted(report["runs"].items()):
        e1 = run["e1"]
        lines += [f"## {name} ({run['role']})", "",
                  f"saved r@5 {_pair(e1['saved']['recall_at_5'])}, replayed with review {_pair(e1['replayed_with_review']['recall_at_5'])}, "
                  f"A r@5 {_pair(e1['A']['recall_at_5'])}; matched budget {e1['matched_budget']} ({e1['matched_budget_basis']}); "
                  f"A0 {e1['a0_status']}; B0 budget {e1['respend_budget']['budget']} ({e1['respend_budget']['basis']}"
                  f"{'; ' + e1['respend_budget']['label'] if e1['respend_budget']['label'] else ''}); "
                  f"EAC frame {_pair(e1['eac_frame'])}; donor supply {e1['donor_supply_reachable']}.", ""]
        header = ("| arm | kind | B | W | selected | eligible | simulated | not-sim | r3 hit-frame (chance) | r@1 | r@3 | r@5 | "
                  "chosen | fixed/broken (p) | silent | READY prec | fixed R3 | R3→R4/R5/R6 | lost | ΔfalseREADY | Δwarn | frames | "
                  "calls est | cost |")
        for regime in ("open", "closed_world", "ceiling"):
            rows = [a for a in run["arms"] if a["regime"] == regime]
            if not rows:
                continue
            lines += [f"### {regime}" + (" (closed world, donor-covered shots only; not a production condition)"
                                          if regime == "closed_world" else ""), "", header, "|" + "---|" * 24]
            for a in rows:
                if "e3" not in a:
                    lines.append(f"| {a['id']} | {a['kind']} | {a['budget']} | {a['window_s']} | {a.get('status')} |" + " |" * 19)
                    continue
                e2, e3, e4, e5, s = a["e2"], a["e3"], a["e4"], a["e5"], a["score"]
                p = a["paired"]["hit_at_5"]
                moved = sum(e5["moved_r3"].values())
                warn = sum(v["added"] - v["removed"] for v in e5.get("extra_warnings", {}).get("per_part", {}).values())
                lines.append(
                    f"| {a['id']} | {a['kind']} | {a['budget']} | {a['window_s']} | {e2['selected']} | {e2['eligible']} | "
                    f"{_pair(e3['simulable_fraction'])} | {sum(e3['not_simulable'].values())} | "
                    f"{e2['r3_units_with_hitting_frame_selected']} ({e2['chance_r3_units']}) | {_pair(s['recall_at_1'])} | "
                    f"{_pair(s['recall_at_3'])} | {_pair(s['recall_at_5'])} | {_pair(s['chosen_hit'])} | "
                    f"{p['fixed']}/{p['broken']} ({p['mcnemar_p_exact']}) | {s['serious_silent_misses']} | "
                    f"{_pair(s['ready_precision'])} | {e5['fixed_r3']} | {moved} | {e5.get('lost_hits', {}).get('hit_at_5', 0)} | "
                    f"{e5.get('false_ready_delta', 0)} | {warn} | {e4['frames_job']} | {e4['calls_est']} | "
                    f"{'–' if e4['cost'] is None else e4['cost']} |")
            lines.append("")
        lines += ["### Upper bounds (use ground truth)", "", "| id | kind | r@5 | chosen | Δr@5 vs A |", "|---|---|---|---|---|"]
        for b in run["upper_bounds"]:
            if "score" in b:
                lines.append(f"| {b['id']} | {b['kind']} | {_pair(b['score']['recall_at_5'])} | {_pair(b['score']['chosen_hit'])} | "
                             f"{b['delta_vs_A']['recall_at_5'] if isinstance(b['delta_vs_A'], dict) else b['delta_vs_A']} |")
            else:
                lines.append(f"| {b['id']} | {b['kind']} | hit@3 {_pair(b['hint'].get('hit_at_3'))} | – | – |")
        lines.append("")
    lines += ["## Caveats", "", *[f"- {c}" for c in report["caveats"]], "",
              f"{DATASET}. **GENERALIZATION NOT VERIFIED**; Gate B not claimed. Every E3/E5 number is a partial simulation: "
              "benefits and harms both under-sampled.", ""]
    return "\n".join(lines)


# --- command line ---

def _options(args: argparse.Namespace) -> Options:
    return Options(budgets=getattr(args, "budgets", "matched,2x,4x"), windows=getattr(args, "windows_s", "5,10,15,30,fwd"),
                   tolerance_ms=getattr(args, "tolerance_ms", 500), donor_scope=getattr(args, "donor_scope", "items"),
                   share_duplicates=getattr(args, "share_duplicates", "on") == "on",
                   review=getattr(args, "review_edits", "off") == "on", target_units=getattr(args, "target_units", "flagged"),
                   cost_per_call=getattr(args, "cost_per_call", None), cost_unit=getattr(args, "cost_unit", None),
                   grid_supporting=getattr(args, "grid_supporting", "matched"), blindness=getattr(args, "blindness_check", "subprocess"),
                   prereg=Path(args.prereg) if getattr(args, "prereg", None) else None, memo=not getattr(args, "no_memo", False))


def _stdout(text: str) -> None:
    """UTF-8 with LF on every platform (a Windows console or pipe would otherwise re-encode and add CR)."""
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        sys.stdout.write(text)
        return
    sys.stdout.flush()
    buffer.write(text.encode("utf-8"))
    buffer.flush()


def _emit(value: Any, out: str | None) -> None:
    text = dumps(value)
    if out in (None, "-"):
        _stdout(text)
    else:
        write_text(Path(out), text)


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--budgets", default="matched,2x,4x")
    p.add_argument("--windows-s", default="5,10,15,30,fwd")
    p.add_argument("--tolerance-ms", type=int, default=500)
    p.add_argument("--donor-scope", choices=("items", "all"), default="items")
    p.add_argument("--share-duplicates", choices=("on", "off"), default="on")
    p.add_argument("--review-edits", choices=("off", "on"), default="off")
    p.add_argument("--target-units", choices=tg.TARGET_UNITS, default="flagged")
    p.add_argument("--cost-per-call", type=float, default=None)
    p.add_argument("--cost-unit", default=None)
    p.add_argument("--no-memo", action="store_true")
    p.add_argument("--json", default=None)


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="targeted", description="Offline Stage 2 harness (#815)")
    sub = top.add_subparsers(dest="command", required=True)
    p = sub.add_parser("preflight")
    p.add_argument("runs", nargs="+")
    p.add_argument("--json", default=None)
    p = sub.add_parser("baseline")
    p.add_argument("gt")
    p.add_argument("lock")
    p.add_argument("runs", nargs="+")
    _common(p)
    p = sub.add_parser("select")
    p.add_argument("run")
    p.add_argument("--all-arms", action="store_true")
    p.add_argument("--donors", nargs="*", default=[])
    p.add_argument("--strategy", choices=tg.STRATEGIES)
    p.add_argument("--budget", type=int)
    p.add_argument("--window-s", default="fwd")
    p.add_argument("--grid", choices=("full", "matched"), default="full")
    _common(p)
    for name in ("replay", "budget", "attribution"):
        p = sub.add_parser(name)
        p.add_argument("gt")
        p.add_argument("lock")
        p.add_argument("run")
        p.add_argument("--donors", nargs="*", default=[])
        p.add_argument("--strategy", choices=tg.STRATEGIES, default="mixed")
        p.add_argument("--budget", type=int, default=None)
        p.add_argument("--window-s", default="fwd")
        p.add_argument("--regime", choices=REGIMES, default="open")
        p.add_argument("--arm", default=PRIMARY_ARM)
        _common(p)
    p = sub.add_parser("all")
    p.add_argument("gt")
    p.add_argument("lock")
    p.add_argument("--primary", required=True)
    p.add_argument("--runs", nargs="*", default=[])
    p.add_argument("--out-dir", required=True)
    p.add_argument("--grid-supporting", choices=("matched", "full"), default="matched")
    p.add_argument("--blindness-check", choices=("subprocess", "inprocess", "off"), default="subprocess")
    p.add_argument("--prereg", default=None)
    _common(p)
    return top


def main(argv: Sequence[str]) -> int:
    args = parser().parse_args(list(argv))
    options = _options(args)
    if args.command == "preflight":
        result = {Path(r).name: preflight(load(Path(r))) for r in args.runs}
        _emit(result, args.json)
        return 0 if all(v["status"] == "PASS" for v in result.values()) else 3
    if args.command == "select":  # no ground truth on this path
        memo = PlanMemo() if options.memo else None
        target = prepare(load(Path(args.run)), review=options.review, memo=memo)
        donors = [prepare(load(Path(d)), light=True) for d in args.donors]
        if args.all_arms:
            _emit(all_arm_selection(target, donors, options, args.grid == "full"), args.json)
            return 0
        if args.strategy is None or (args.budget is None and args.strategy != "all_eligible"):
            raise SystemExit("select needs --all-arms, or --strategy and --budget")
        window = None if args.window_s == "fwd" else int(args.window_s) * 1000
        found = tg.select(target.view_A, tg.SelectParams(args.strategy, args.budget, window, options.target_units))
        _emit([dataclasses.asdict(s) for s in found], args.json)
        return 0
    gt = w3.load_gt(Path(args.gt), Path(args.lock))
    if args.command == "baseline":
        memo = PlanMemo() if options.memo else None
        preps = [prepare(load(Path(r)), review=options.review, memo=memo) for r in args.runs]
        result = {}
        for prep in preps:
            index = DonorIndex(prep, [p for p in preps if p is not prep], scope=options.donor_scope, tolerance_ms=options.tolerance_ms)
            result[prep.name] = e1_baseline(gt, prep, index)
        _emit({"dataset": DATASET, "conclusion": CONCLUSION, "runs": result}, args.json)
        return 0
    if args.command in ("replay", "budget", "attribution"):
        memo = PlanMemo() if options.memo else None
        target = prepare(load(Path(args.run)), review=options.review, memo=memo)
        donors = [prepare(load(Path(d)), review=options.review, memo=memo) for d in args.donors]
        if args.command == "replay":
            index = DonorIndex(target, donors, scope=options.donor_scope, tolerance_ms=options.tolerance_ms)
            ctx = Context(gt, target, index, memo, options.review, options.tolerance_ms, options.share_duplicates,
                          options.target_units, options.cost_per_call, options.cost_unit)
            budget = args.budget if args.budget is not None else target.matched
            arm = Arm(f"{args.regime}:{args.strategy}:W={args.window_s}:+{budget}", args.regime,
                      "targeted", args.strategy, args.window_s, f"+{budget}", budget, "A")
            view = Views(target, index).get("A", args.regime)
            result = evaluate_arm(ctx, arm, tg.select_detail(view, arm.params(options.target_units)), view)
            result["e2"].pop("_r3_with_frame", None)
            _emit({"dataset": DATASET, "conclusion": CONCLUSION, "arm": result}, args.json)
            return 0
        found, _, _ = evaluate_run(gt, target, donors, options, full=True, memo=memo)
        if args.command == "budget":
            _emit({"dataset": DATASET, "conclusion": CONCLUSION,
                   "arms": [{k: v for k, v in a.items() if k not in ("samples", "unit_rows", "selection")} for a in found["arms"]]},
                  args.json)
            return 0
        arm = next((a for a in found["arms"] if a["id"] == args.arm), None)
        if arm is None:
            raise SystemExit(f"unknown arm {args.arm!r}")
        _emit({"dataset": DATASET, "conclusion": CONCLUSION, "arm": arm["id"], "e5": arm.get("e5"),
               "unit_rows": arm.get("unit_rows")}, args.json)
        return 0
    if args.command == "all":
        report, batch, perf = run_all(gt, Path(args.primary), [Path(r) for r in args.runs], options)
        out = Path(args.out_dir)
        primary = report["real_call_batch"].removeprefix("real-call-batch-").removesuffix(".json")
        write_text(out / f"targeted-{primary}.json", dumps(report))
        write_text(out / f"targeted-{primary}.md", markdown(report))
        write_text(out / f"real-call-batch-{primary}.json", dumps(batch))
        write_text(out / f"targeted-{primary}.perf.json", dumps(perf))
        _stdout(dumps({"decision": report["decision"]["result"], "blindness_check": report["blindness_check"],
                                "preregistration": report["preregistration"]["label"],
                                "files": sorted(p.name for p in out.glob(f"*{primary}*"))}))
        return 0 if all(v != "FAIL" for v in report["blindness_check"].values()) else 4
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
