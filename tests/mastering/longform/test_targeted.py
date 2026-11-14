"""The offline Stage 2 harness (#815) on run folders made here by the planner itself (no media, no network, no key).

The fixture extends ``test_w3.make_run``: shots c1…c10 of 6 s with small distinct JPEG files, a vision report and a
programme file. The run looked at shots 2, 3, 4, 5 and 7 (3, 5 and 7 in the broad look, 2 and 4 targeted); shot 5
shows smoke, so the tank sentence i1.u3 (ground truth 30.5–35.5 s, shot c6) has no matching footage, is flagged
(partial or none, needs review) and is R3: its shot was never looked at. A donor run with the same JPEG bytes answered
c6 with the tank sentence.
"""
from __future__ import annotations

import ast
import contextlib
import dataclasses
import hashlib
import io
import json
from pathlib import Path, PurePath
import random
import shutil
import socket
import subprocess
from tempfile import TemporaryDirectory
import os
import unittest
from unittest import mock

from engine.dubflow.mastering import align
from engine.dubflow.mastering import analysis as scene
from engine.dubflow.mastering import candidates as shots
from engine.dubflow.mastering import evidence as ev
from engine.dubflow.mastering import frames as more_frames
from engine.dubflow.mastering import plan as m
from engine.dubflow.mastering import review as reviewing
from engine.dubflow.mastering import vision
from engine.dubflow.mastering.planners import compilation as planner
from engine.dubflow.product import script
from tests.mastering.longform import targeted as tt
from tests.mastering.longform import targeting as tg
from tests.mastering.longform import w3
from tests.mastering.longform.test_w3 import SENTENCES, make_truth

HERE = Path(__file__).resolve().parent
PROVIDER, MODEL = "test-provider", "test-model"
DETAIL = f"AI {PROVIDER}/{MODEL}"
TEXTS = {2: "Bản đồ châu Âu có mũi tên, dòng chữ 1500 MILES",
         3: "Lá cờ phấp phới trong gió",
         4: "Đoàn binh sĩ đội mũ sắt hành quân trên con đường đất",
         5: "Khói đen bốc lên từ ống khói nhà máy",
         7: "Bầu trời có mây"}
BROAD, TARGETED = (3, 5, 7), (2, 4)
TANK = "Một chiếc xe tăng lăn bánh qua cánh đồng"
INTRO = [("Phần mở đầu kể về chiến dịch mùa đông.", 2200)]
UNUSABLE = (9, 10)


def jpeg_bytes(part: str, number: int, salt: str = "") -> bytes:
    return b"\xff\xd8\xff\xe0" + f"{part}-s1-c{number}{salt}".encode() + b"\xff\xd9"


def make_run_k(root: Path, texts: dict[int, str] | None = None, name: str = "run-test", parts: tuple[str, ...] = ("i1",),
               spec: dict | None = None, review: dict | None = None, jpegs: bool = True, salt: str = "",
               intro: dict[int, str] | None = None, budget: int = 4, targeted: tuple[int, ...] = TARGETED,
               provider: tuple[str, str] = (PROVIDER, MODEL), cache: bool = True, missing_jpegs: tuple[int, ...] = (),
               broad: tuple[int, ...] = BROAD, twins: dict[int, int] | None = None,
               asked: tuple[str, str] | None | bool = True) -> Path:
    """A raw run of item parts (and an optional intro) as ``run_program`` saves it, with keyframes and vision reports.

    ``provider`` is the pair that answered (reports, item details, cache keys); ``asked`` the spec's ``editorial.ai``
    pair (True: the same as ``provider``; None: no ``editorial`` in the spec)."""
    texts = TEXTS if texts is None else texts
    detail = f"AI {provider[0]}/{provider[1]}"
    length = 60_000
    folder = root / name / ".dubflow-work" / "mastering"
    folder.mkdir(parents=True)
    work = folder.parent
    sources, pools, analyses = {}, {}, {}
    quality = shots.Quality(120, 50, 300)
    bad = shots.Quality(10, 2, 1, ("quá tối",))
    for part in [p for p in parts if p.startswith("i")]:
        sid = f"{part}-s1"
        identity = hashlib.sha256(sid.encode()).hexdigest()
        found = scene.Analysis(length, tuple(range(6000, length, 6000)),
                               tuple(scene.Shot(a, a + 6000, 40) for a in range(0, length, 6000)), (), True, identity)
        analyses[sid] = found
        sources[sid] = planner.Footage(sid, Path("film.webm"), found, 640, 480, (24000, 1001))
        rng = random.Random(7 + len(pools))
        pools[sid] = [shots.Candidate(f"{sid}c{n + 1}", sid, n * 6000 + 120, n * 6000 + 5880, n, 30,
                                      bad if n + 1 in UNUSABLE else quality, rng.getrandbits(64), f"{sid}-c{n + 1}.jpg")
                      for n in range(length // 6000)]
        for copy, leader in (twins or {}).items():  # the same picture twice: one duplicate group
            pools[sid][copy - 1] = dataclasses.replace(pools[sid][copy - 1], dhash=pools[sid][leader - 1].dhash)
        for n in range(1, 11):
            if jpegs and n not in missing_jpegs:
                (folder / f"{sid}-c{n}.jpg").write_bytes(jpeg_bytes(part, n, salt))
        (folder / f"analysis-{identity[:8]}.json").write_text(json.dumps(found.to_dict()), encoding="utf-8")
    groups = shots.duplicate_groups([c for pool in pools.values() for c in pool])
    base = planner.evidence_for(list(sources.values()), pools, {})

    def answer(evidence: dict, sid: str, number: int, text: str) -> None:
        # the production look shares an answer with the duplicates of the frame it asked about (vision.share)
        for n in [number, *[c for c, leader in (twins or {}).items() if leader == number]]:
            if not any(i.origin == "vision" for i in evidence[f"{sid}c{n}"].items):
                # what vision.apply writes: the item and the side metadata of the answer (no box, hook or group)
                vision.apply({"x": vision.Seen(text, (), (), "", ("explanation",), 850, "clear", None, None, 0)},
                             {"x": evidence[f"{sid}c{n}"]}, detail)

    for part in [p for p in parts if p.startswith("i")]:
        sid = f"{part}-s1"
        for number in broad:
            if number in texts:
                answer(base, sid, number, texts[number])
    cache_dir = folder / "vision"
    report = {"provider": provider[0], "model": provider[1], "frames": 0, "asked": 0, "broad": len(broad) * sum(p.startswith("i") for p in parts),
              "targeted": 0, "warnings": [], "budget": budget * sum(p.startswith("i") for p in parts), "budget_rule": "spec",
              "shared_duplicates": 0}
    report["frames"] = report["broad"]

    def remember(part: str, number: int, text: str | None) -> None:
        # what vision.Describer._store writes: the full production key, the validated answer and who made it
        if not cache or text is None:
            return
        digest = hashlib.sha256(jpeg_bytes(part, number, salt)).digest()
        key = hashlib.sha256(b"|".join([vision.PROMPT_VERSION.encode(), provider[0].encode(), provider[1].encode(),
                                        ",".join(ev.MOMENT_LABELS).encode(), digest])).hexdigest()
        cache_dir.mkdir(exist_ok=True)
        seen = vision.Seen(text, (), (), "", ("explanation",), 850, "clear", None, None, 0)
        (cache_dir / f"{key[:32]}.json").write_text(json.dumps({"key": key, **seen.to_dict(),
                                                                 "made_by": {"prompt": vision.PROMPT_VERSION, "provider": provider[0],
                                                                             "model": provider[1], "answered_by": provider[1]}},
                                                                ensure_ascii=False), encoding="utf-8")

    program_parts = []
    for part in parts:
        if part.startswith("i"):
            for number in broad:
                remember(part, number, texts.get(number))
    for part in parts:
        if part == "intro":
            evidence = {k: dataclasses.replace(v, items=list(v.items)) for k, v in base.items()}
            for number, text in (intro or {}).items():
                answer(evidence, "i1-s1", number, text)
                remember("i1", number, text)
            report["targeted"] += len(intro or {})
            report["frames"] = report["broad"] + report["targeted"]
            spoken = INTRO
            footage, candidates, kind = list(sources.values()), pools, "intro"
        else:
            sid = f"{part}-s1"
            for number in targeted:
                if number in texts:
                    answer(base, sid, number, texts[number])
                    remember(part, number, texts[number])
            report["targeted"] += len([n for n in targeted if n in texts])
            report["frames"] = report["broad"] + report["targeted"]
            evidence, spoken = base, SENTENCES
            footage, candidates, kind = [sources[sid]], {sid: pools[sid]}, "item"
        _, narration_ms = script.captions(spoken)
        inputs = {f.source_id: f.analysis.identity for f in footage}
        inputs["part"] = "b" * 64
        made = planner.plan_part(part, kind, spoken, narration_ms, m.Options("balanced", "entertainment"), lead_ms=script.LEAD_MS,
                                 gap_ms=script.GAP_MS, footage=footage, candidates=candidates, evidence=evidence, groups=groups,
                                 inputs=inputs, title="")
        reviewing.annotate(made.log, made.plan.to_dict())
        made.log["vision"] = dict(report)
        (folder / f"editorial-{part}.json").write_text(json.dumps(made.log, ensure_ascii=False), encoding="utf-8")
        (folder / f"plan-{part}.json").write_text(json.dumps(made.plan.to_dict(), ensure_ascii=False), encoding="utf-8")
        program_parts.append({"id": part})
    (work / "program.json").write_text(json.dumps({"parts": program_parts, "vision": dict(report)}), encoding="utf-8")
    items = [{"item_id": p, "sources": [{"start_ms": 0, "end_ms": length}]} for p in parts if p.startswith("i")]
    if spec is None:
        spec = {"items": items}
        if asked is not None:
            pair = provider if asked is True else asked
            spec["editorial"] = {"ai": {"provider": pair[0], "model": pair[1]}}
    (work / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    if review is not None:
        (work / "review-i1.json").write_text(json.dumps(review), encoding="utf-8")
    return root / name


def donor_texts() -> dict[int, str]:
    return {**TEXTS, 6: TANK, 2: "Bầu trời xám có mây trắng"}


def make_pair(root: Path, **kwargs) -> tuple[Path, Path, Path, Path]:
    run = make_run_k(root, name="run-a", **kwargs)
    donor = make_run_k(root, name="run-b", texts=donor_texts(), targeted=(2, 6), **kwargs)
    gt, lock = make_truth(root)
    return run, donor, gt, lock


def quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def all_args(root: Path, run: Path, donor: Path, gt: Path, lock: Path, out: str, *extra: str) -> list[str]:
    return ["all", str(gt), str(lock), "--primary", str(run), "--runs", str(donor), "--out-dir", str(root / out),
            "--windows-s", "10,fwd", "--budgets", "matched,2x", "--blindness-check", "inprocess", *extra]




_TMP: TemporaryDirectory | None = None
STUDY: dict = {}
FORBIDDEN = {"gt", "truth", "ground", "ranges", "hits", "lock", "oracle", "review", "load_gt"}


def setUpModule() -> None:  # one study on the fixture, read by the tests that only look at its output
    global _TMP
    _TMP = TemporaryDirectory()
    root = Path(_TMP.name)
    run, donor, gt, lock = make_pair(root)
    quiet(tt.main, all_args(root, run, donor, gt, lock, "out"))
    STUDY.update(root=root, run=run, donor=donor, gt=gt, lock=lock,
                 json=(root / "out" / "targeted-run-a.json").read_text(encoding="utf-8"),
                 md=(root / "out" / "targeted-run-a.md").read_text(encoding="utf-8"),
                 batch=json.loads((root / "out" / "real-call-batch-run-a.json").read_text(encoding="utf-8")),
                 perf=(root / "out" / "targeted-run-a.perf.json").read_text(encoding="utf-8"))
    STUDY["report"] = json.loads(STUDY["json"])


def tearDownModule() -> None:
    if _TMP is not None:
        _TMP.cleanup()


def arms_of(report: dict, run: str = "run-a") -> dict[str, dict]:
    return {a["id"]: a for a in report["runs"][run]["arms"]}


def tokens(text: str) -> set[str]:
    out = set()
    for word in "".join(ch if ch.isalnum() or ch == "_" else " " for ch in text.replace("needs_review", " ")).split():
        out.add(word.lower())
        out.update(part.lower() for part in word.split("_") if part)
    return out


def prepared(root: Path, **kwargs) -> tuple[tt.Prepared, tt.Prepared, dict]:
    run, donor, gt, lock = make_pair(root, **kwargs)
    return tt.prepare(tt.load(run)), tt.prepare(tt.load(donor)), w3.load_gt(gt, lock)


class FixtureTests(unittest.TestCase):
    def test_the_fixture_is_what_the_scenario_says(self):
        with TemporaryDirectory() as tmp:
            run, donor, gt_path, lock = make_pair(Path(tmp))
            loaded = tt.load(run)
            unit = {u["unit_id"]: u for u in loaded.run.logs["i1"]["units"]}["i1.u3"]
            self.assertTrue(unit["coverage"]["needs_review"])
            self.assertIn(unit["coverage"]["status"], ("partial", "none"))
            gt = w3.load_gt(gt_path, lock)
            prep = tt.prepare(loaded)
            self.assertEqual({r["unit"]: r["stage"] for r in w3.attribute(gt, prep.A)["rows"]}["i1.u3"], "R3_NOT_LOOKED")
            self.assertEqual(prep.broad["status"], "RECONSTRUCTED")
            self.assertEqual(prep.broad["picked"], ["i1-s1c3", "i1-s1c5", "i1-s1c7"])
            self.assertEqual(prep.matched, 2)
            self.assertEqual(tt.preflight(loaded)["status"], "PASS")


class BlindnessTests(unittest.TestCase):
    ALLOWED = {"__future__": None, "dataclasses": None, "typing": None, "types": {"MappingProxyType"},
               "engine.dubflow.mastering.vision": {"allocate", "spread_order", "targets"},
               "engine.dubflow.mastering.align": {"Scorer", "Unit"},
               "engine.dubflow.mastering.candidates": {"Candidate", "numbered", "_all_windows", "WITHIN_BASE", "MAX_CANDIDATES"},
               "engine.dubflow.mastering.frames": {"extra_times", "SPLIT_MARK"},
               "engine.dubflow.mastering.evidence": {"Evidence"}}

    def tree(self) -> ast.Module:
        return ast.parse((HERE / "targeting.py").read_text(encoding="utf-8"))

    def test_the_selector_imports_only_the_allowlist(self):
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Import):
                self.assertEqual([a.name for a in node.names], ["math"])
            elif isinstance(node, ast.ImportFrom):
                self.assertIn(node.module, self.ALLOWED)
                allowed = self.ALLOWED[node.module]
                if allowed is not None:
                    self.assertLessEqual({a.name for a in node.names}, allowed)

    def test_the_selector_has_no_file_network_or_scoring_words(self):
        banned_names = {"open", "json", "pathlib", "io", "os", "subprocess", "factory", "Describer", "tests", "random"}
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                self.assertNotIn(node.value.id, ("vision", "frames", "candidates", "w3"))
            names = []
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names.append(node.name)
            elif isinstance(node, ast.arg):
                names.append(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                names.append(node.arg)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.assertFalse(tokens(node.value) & FORBIDDEN, node.value[:80])
            for name in names:
                self.assertNotIn(name, banned_names)
                self.assertFalse(tokens(name) & FORBIDDEN, name)

    def test_signatures_and_fields(self):
        import inspect
        self.assertEqual(list(inspect.signature(tg.select).parameters), ["view", "params"])
        self.assertEqual(list(inspect.signature(tg.planning_view).parameters), ["data"])
        for cls in (tg.RunData, tg.PlanningView, tg.PartView, tg.ShotView, tg.UnitView, tg.SelectParams, tg.Sample):
            for f in dataclasses.fields(cls):
                self.assertFalse(tokens(f.name) & FORBIDDEN, f"{cls.__name__}.{f.name}")
        with self.assertRaises(TypeError):
            tg.select({"parts": []}, tg.SelectParams("mixed", 2, None))
        with self.assertRaises(TypeError):
            tg.planning_view({"logs": {}})

    def test_the_selector_works_with_the_scoring_poisoned(self):
        def boom(*args, **kwargs):
            raise AssertionError("the selection path read the ground truth")
        with TemporaryDirectory() as tmp, mock.patch.object(w3, "load_gt", boom), mock.patch.object(w3, "hits", boom), \
                mock.patch.object(w3, "score", boom), mock.patch.object(w3, "attribute", boom):
            run, donor, _, _ = make_pair(Path(tmp))
            target, other = tt.prepare(tt.load(run)), tt.prepare(tt.load(donor))
            found = tt.all_arm_selection(target, [other], tt.Options(windows="10,fwd", budgets="matched,2x"), True)
            self.assertIn(tt.PRIMARY_ARM, found)
            for strategy in tg.STRATEGIES:
                tg.select(target.view_A, tg.SelectParams(strategy, None if strategy == "all_eligible" else 2, None))

    def test_another_ground_truth_changes_only_the_upper_bounds(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, donor, gt, lock = STUDY["run"], STUDY["donor"], STUDY["gt"], STUDY["lock"]
            other = root / "other"
            other.mkdir()
            gt2, lock2 = make_truth(other, third=[[42.5, 47.5]])
            quiet(tt.main, all_args(root, run, donor, gt2, lock2, "out2"))
            second = json.loads((root / "out2" / "targeted-run-a.json").read_text(encoding="utf-8"))
        first = STUDY["report"]
        self.assertNotEqual(first["inputs"]["ground_truth_sha256"], second["inputs"]["ground_truth_sha256"])
        for run_name in first["runs"]:
            a, b = arms_of(first, run_name), arms_of(second, run_name)
            self.assertEqual(sorted(a), sorted(b))
            for arm_id in a:
                self.assertEqual(a[arm_id].get("selection"), b[arm_id].get("selection"), arm_id)
        oracle = lambda r: {u["id"]: u.get("selection") for u in r["runs"]["run-a"]["upper_bounds"]  # noqa: E731
                            if u["id"].startswith("ORACLE_select_gt_window:open")}
        self.assertNotEqual(oracle(first), oracle(second))

    def test_a_review_file_changes_no_selection(self):
        decisions = reviewing.Review(units={"i1.u3": reviewing.UnitDecision(pin=["i1-s1c8"]),
                                            "i1.u1": reviewing.UnitDecision(approved=["i1-s1c2"])}).to_dict()
        options = tt.Options(windows="10,fwd", budgets="matched,2x")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = make_run_k(root, name="run-a")
            reviewed = make_run_k(root / "r", name="run-a", review=decisions)
            donor = make_run_k(root, name="run-b", texts=donor_texts(), targeted=(2, 6))
            other = tt.prepare(tt.load(donor))
            loaded = tt.load(reviewed)
            self.assertEqual(loaded.review_files["i1"], {"present": True, "nonempty": True, "pins": 1, "excludes": 0, "approvals": 1})
            a = tt.all_arm_selection(tt.prepare(tt.load(plain)), [other], options, True)
            b = tt.all_arm_selection(tt.prepare(loaded), [other], options, True)
            self.assertEqual(tt.dumps(a), tt.dumps(b))
            # the review is real: merged in (the old w3 behaviour), the pin moves the tank sentence
            merged = tt.prepare(tt.load(reviewed), review=True)
            self.assertEqual({u["unit_id"]: u["chosen"] for u in merged.A.logs["i1"]["units"]}["i1.u3"], ["i1-s1c8"])
            self.assertNotEqual({u["unit_id"]: u["chosen"] for u in tt.prepare(loaded).A.logs["i1"]["units"]}["i1.u3"], ["i1-s1c8"])

    def test_the_blindness_subprocess_gives_the_embedded_samples(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, donor, gt, lock = STUDY["run"], STUDY["donor"], STUDY["gt"], STUDY["lock"]
            args = all_args(root, run, donor, gt, lock, "sub")
            args[args.index("inprocess")] = "subprocess"
            quiet(tt.main, args)
            report = json.loads((root / "sub" / "targeted-run-a.json").read_text(encoding="utf-8"))
        self.assertEqual(report["blindness_check"], {"run-a": "PASS", "run-b": "PASS"})
        self.assertEqual(report["runs"], STUDY["report"]["runs"])


class BlindnessDecodingTests(unittest.TestCase):
    def test_a_child_error_in_another_code_page_is_reported_not_raised(self):
        with TemporaryDirectory() as tmp:
            target, donor, _ = prepared(Path(tmp))
        seen = {}

        def run(argv, **kwargs):
            seen.update(kwargs)
            raw = "Traceback: C:\\Phim tài\\Đá x".encode("cp1258")  # the Vietnamese Windows code page
            return subprocess.CompletedProcess(argv, 1, "", raw.decode(kwargs["encoding"], kwargs["errors"]))
        with mock.patch.object(subprocess, "run", run):
            found = tt.blindness_subprocess(target, [donor], tt.Options(), True)
        self.assertTrue(found.startswith("ERROR 1: "))
        self.assertEqual((seen["encoding"], seen["errors"], seen["env"]["PYTHONUTF8"], seen["env"]["PYTHONIOENCODING"]),
                         ("utf-8", "replace", "1", "utf-8"))


class ViewBasisTests(unittest.TestCase):
    def test_b0_starts_from_a0_and_a0_flags_what_the_targeted_answers_covered(self):
        with TemporaryDirectory() as tmp:
            target, _, _ = prepared(Path(tmp))
            flagged = lambda view: {u.unit_id for p in view.parts for u in tg.wanted_units(p, "flagged")}  # noqa: E731
            self.assertNotIn("i1.u2", flagged(target.view_A))  # the soldiers' shot c4 was a targeted look
            self.assertIn("i1.u2", flagged(target.view_A0))
            self.assertEqual(target.view_A.basis, "A_replay_no_review")
            self.assertEqual(target.view_A0.basis, "A0_replay_no_review")
        for arm in arms_of(STUDY["report"]).values():
            if "e2" in arm:
                self.assertEqual(arm["view_basis"], "A0_replay_no_review" if "B0" in arm["id"] else "A_replay_no_review")


class MatchingTests(unittest.TestCase):
    def sample_of(self, prep: tt.Prepared, candidate_id: str, t_ms: int | None = None) -> tg.Sample:
        s = prep.shot("i1", candidate_id)
        return tg.Sample("i1", s.candidate_id, s.source_id, s.source_key, s.keyframe_ms if t_ms is None else t_ms,
                         s.keyframe_basis, "i1.u3", "test", 1)

    def test_the_same_jpeg_bytes_are_simulated(self):
        arm = arms_of(STUDY["report"])[tt.PRIMARY_ARM]
        tank = next(r for r in arm["samples"] if r["sample"]["candidate_id"] == "i1-s1c6")
        self.assertEqual((tank["status"], tank["match_basis"], tank["donor_run"], tank["own_cache"]),
                         ("SIMULATED", "exact_key", "run-b", False))

    def test_other_bytes_another_provider_or_a_shared_answer_are_not(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = make_run_k(root, name="run-a")
            target = tt.prepare(tt.load(run))
            salted = tt.prepare(tt.load(make_run_k(root, name="salted", texts=donor_texts(), targeted=(2, 6), salt="x")))
            index = tt.DonorIndex(target, [salted])
            self.assertEqual(index.match(self.sample_of(target, "i1-s1c6")).reason, "jpeg_mismatch")
            other = tt.prepare(tt.load(make_run_k(root, name="other", texts=donor_texts(), targeted=(2, 6),
                                                  provider=("other-provider", MODEL))))
            index = tt.DonorIndex(target, [other])
            self.assertGreater(index.counts["donor_provider_mismatch"], 0)
            self.assertEqual(index.match(self.sample_of(target, "i1-s1c6")).status, "NOT_SIMULABLE")
            shared = tt.prepare(tt.load(make_run_k(root, name="shared", texts={**TEXTS, 2: "Bầu trời xám"}, targeted=(2,),
                                                   twins={6: 2})))
            self.assertEqual(shared.classes["i1-s1c6"], "targeted_shared")
            index = tt.DonorIndex(target, [shared])
            self.assertGreater(index.counts["donor_shared_excluded"], 0)
            self.assertEqual(index.match(self.sample_of(target, "i1-s1c6")).status, "NOT_SIMULABLE")

    def test_a_missing_jpeg_is_never_matched_by_time(self):
        # no bytes, no production key: a donor answer near the same time is not proof of the same cached answer
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = tt.prepare(tt.load(make_run_k(root, name="run-a", missing_jpegs=(6,))))
            donor = tt.prepare(tt.load(make_run_k(root, name="run-b", texts=donor_texts(), targeted=(2, 6))))
            index = tt.DonorIndex(target, [donor])
            self.assertIsNotNone(index.key_basis)
            found = index.match(self.sample_of(target, "i1-s1c6"))
            self.assertEqual((found.status, found.reason), ("NOT_SIMULABLE", "target_jpeg_unknown"))

    def donor_with(self, root: Path, change) -> tuple[tt.DonorIndex, tt.Prepared]:
        """The fixture pair (c6 answered only by the donor) with ``change`` applied to the donor's c6 cache record."""
        target = tt.prepare(tt.load(make_run_k(root, name="run-a")))
        run = make_run_k(root, name="run-b", texts=donor_texts(), targeted=(2, 6))
        loaded = tt.load(run)
        c6 = next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == "i1-s1c6")
        key = tt._cache_key(vision.PROMPT_VERSION, PROVIDER, MODEL, ev.MOMENT_LABELS, loaded.jpeg[c6["keyframe"]])
        path = run / ".dubflow-work" / "mastering" / "vision" / f"{key[:32]}.json"
        change(path, json.loads(path.read_text(encoding="utf-8")), loaded)
        return tt.DonorIndex(target, [tt.prepare(tt.load(run))]), target

    def rekeyed(self, prompt: str = vision.PROMPT_VERSION, labels=ev.MOMENT_LABELS, model: str = MODEL):
        def change(path: Path, record: dict, loaded: tt.Loaded) -> None:
            c6 = next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == "i1-s1c6")
            key = tt._cache_key(prompt, PROVIDER, model, labels, loaded.jpeg[c6["keyframe"]])
            path.unlink()
            record["key"] = key
            record["made_by"] = {**record["made_by"], "prompt": prompt, "model": model}
            (path.parent / f"{key[:32]}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return change

    @staticmethod
    def side_change(box: dict, log_too: bool):
        """A donor change: the c6 record gets a hook, a box and a product group; with ``log_too`` the donor's log
        carries the side metadata vision.apply wrote for that record (the donor really answered so)."""
        def change(path: Path, record: dict, loaded: tt.Loaded) -> None:
            changed = {**record, "hook": 900, "product_box": box, "product_group": "g1"}
            path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
            if not log_too:
                return
            applied = ev.Evidence("x")
            vision.apply({"x": vision.validate(changed, ev.MOMENT_LABELS)}, {"x": applied}, DETAIL)
            log_path = loaded.folder / ".dubflow-work" / "mastering" / "editorial-i1.json"
            log = json.loads(log_path.read_text(encoding="utf-8"))
            for c in log["candidates"]:
                if c["id"] == "i1-s1c6":
                    c["evidence"].update({k: applied.to_dict()[k] for k in tt.SIDE_FIELDS})
            log_path.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
        return change

    def test_a_replayed_answer_carries_what_vision_apply_sets(self):
        box = {"x": 100, "y": 100, "w": 300, "h": 300}
        with TemporaryDirectory() as tmp:
            index, target = self.donor_with(Path(tmp), self.side_change(box, log_too=True))
            found = index.match(self.sample_of(target, "i1-s1c6"))
            self.assertEqual(found.status, "SIMULATED")
            item = index.item_of(found)
            self.assertEqual(item["side"], {"product_box": box, "hook": 900, "product_visible": True, "same_product": "g1"})
            evidence_for, new_ids, _ = tt.augmented_evidence(target.A, {"i1": {"i1-s1c6": item}})
            got = evidence_for("i1", target.A.logs["i1"])["i1-s1c6"]
            self.assertEqual((got.hook, got.product_visible, got.same_product, got.product_box is not None),
                             (900, True, "g1", True))
            self.assertIn("i1-s1c6", new_ids)

    def test_the_replayed_side_is_the_one_of_the_answer_that_proved_the_log(self):
        # production logs a product group clipped at 40 characters with its trailing space and stores it so;
        # validating the record again strips the space: the stored answer proves the log, and its side is replayed
        group = "k" * 39 + " "
        with TemporaryDirectory() as tmp:
            def change(path: Path, record: dict, loaded: tt.Loaded) -> None:
                changed = {**record, "product_group": group}
                path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
                log_path = loaded.folder / ".dubflow-work" / "mastering" / "editorial-i1.json"
                log = json.loads(log_path.read_text(encoding="utf-8"))
                for c in log["candidates"]:
                    if c["id"] == "i1-s1c6":
                        c["evidence"]["same_product"] = group
                log_path.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
            index, target = self.donor_with(Path(tmp), change)
            found = index.match(self.sample_of(target, "i1-s1c6"))
            self.assertEqual(found.status, "SIMULATED")
            self.assertEqual(index.item_of(found)["side"]["same_product"], group)

    def test_a_donor_record_whose_side_metadata_the_donor_did_not_log_is_not_replayed(self):
        # same key and the same item, but the record's box, hook and group are not what the donor's log carries:
        # replaying it would add a whole answer the donor never logged
        box = {"x": 100, "y": 100, "w": 300, "h": 300}
        with TemporaryDirectory() as tmp:
            index, target = self.donor_with(Path(tmp), self.side_change(box, log_too=False))
            found = index.match(self.sample_of(target, "i1-s1c6"))
            self.assertEqual(found.status, "NOT_SIMULABLE")
            self.assertGreaterEqual(index.provenance.get("side_mismatch", 0), 1)
            self.assertIsNone(index.item_of(found))

    def test_a_record_production_would_not_validate_is_not_exact(self):
        # vision.Describer._cached reuses only validate(record): its raw fields must not prove an answer
        with TemporaryDirectory() as tmp:
            loaded = tt.load(make_run_k(Path(tmp), name="run-a"))
            c2 = next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == "i1-s1c2")
            basis = (vision.PROMPT_VERSION, PROVIDER, MODEL, tuple(ev.MOMENT_LABELS))
            key = tt._cache_key(*basis, loaded.jpeg[c2["keyframe"]])
            loaded.cache[key[:32]] = {"key": key, "description": "", "objects": ["Xe tăng"], "actions": [], "visible_text": "",
                                      "labels": ["not-a-label"], "confidence": 850, "clarity": "clear"}
            self.assertIsNone(vision.validate(loaded.cache[key[:32]], ev.MOMENT_LABELS))
            item = {"origin": "vision", "text": "Xe tăng", "labels": ["not-a-label"], "confidence": 850, "detail": DETAIL}
            self.assertEqual(tt.exact_answer(loaded, basis, loaded.jpeg[c2["keyframe"]], item), "answer_mismatch")

    def test_made_by_and_the_spec_pair_are_never_crossed(self):
        # made_by names (provider, m1), the spec (other-provider, m2): only those two tuples, never a mix of them
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp), name="run-a", asked=("other-provider", "m2"))
            loaded = tt.load(run)
            self.assertEqual(tt.cache_combos(loaded), sorted([(vision.PROMPT_VERSION, PROVIDER, MODEL),
                                                              (vision.PROMPT_VERSION, "other-provider", "m2")]))
            # a stale record under a crossed tuple that reproduces a logged answer proves nothing
            c2 = next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == "i1-s1c2")
            item = tt.vision_items(c2)[0]
            for provider, model in ((PROVIDER, "m2"), ("other-provider", MODEL)):
                key = tt._cache_key(vision.PROMPT_VERSION, provider, model, ev.MOMENT_LABELS, loaded.jpeg[c2["keyframe"]])
                seen = vision.Seen(item["text"], (), (), "", tuple(item["labels"]), item["confidence"], "clear", None, None, 0)
                (run / ".dubflow-work" / "mastering" / "vision" / f"{key[:32]}.json").write_text(
                    json.dumps({"key": key, **seen.to_dict()}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(tt.key_bases(tt.load(run)), [(vision.PROMPT_VERSION, PROVIDER, MODEL, tuple(ev.MOMENT_LABELS))])

    def test_the_report_keeps_every_matching_count(self):
        matching = STUDY["report"]["runs"]["run-a"]["matching"]
        for key in ("tolerance_ms", "donor_runs", "donor_shared_excluded", "donor_provider_mismatch", "detail_mismatch",
                    "own_cache_frames", "key_basis_status", "key_basis", "provenance"):
            self.assertIn(key, matching, key)
        self.assertEqual(matching["key_basis_status"], "exact")
        self.assertEqual(matching["key_basis"]["source"], "made_by+spec")
        self.assertEqual(matching["donor_runs"], ["run-b"])

    def test_an_exact_production_key_is_simulated(self):
        with TemporaryDirectory() as tmp:
            index, target = self.donor_with(Path(tmp), lambda path, record, loaded: None)
            found = index.match(self.sample_of(target, "i1-s1c6"))
            self.assertEqual((found.status, found.match_basis, found.donor_run), ("SIMULATED", "exact_key", "run-b"))
            self.assertGreater(index.provenance["exact"], 0)

    def test_another_prompt_version_label_set_or_model_in_the_key_is_not(self):
        for change, label in ((self.rekeyed(prompt="vision-v2"), "prompt"), (self.rekeyed(labels=ev.LABELS), "labels"),
                              (self.rekeyed(model="another-model"), "model")):
            with self.subTest(label), TemporaryDirectory() as tmp:
                index, target = self.donor_with(Path(tmp), change)
                found = index.match(self.sample_of(target, "i1-s1c6"))
                self.assertEqual((found.status, found.reason), ("NOT_SIMULABLE", "no_donor_answer"))
                self.assertGreater(index.provenance["no_exact_key"], 0)

    def test_a_record_whose_made_by_or_answer_disagrees_is_not(self):
        def edit(field: str, key: str, value) -> object:
            def change(path: Path, record: dict, loaded: tt.Loaded) -> None:
                (record if field == "" else record[field])[key] = value
                path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            return change

        cases = ((edit("made_by", "prompt", "vision-v2"), "made_by_mismatch"),
                 (edit("made_by", "provider", "other-provider"), "made_by_mismatch"),
                 (edit("made_by", "model", "another-model"), "made_by_mismatch"),
                 (edit("", "description", "Một cảnh khác"), "answer_mismatch"),
                 (edit("", "labels", ["reaction"]), "answer_mismatch"),
                 (edit("", "confidence", 300), "answer_mismatch"),
                 (edit("", "clarity", "blurry"), "answer_mismatch"))
        for change, reason in cases:
            with self.subTest(reason), TemporaryDirectory() as tmp:
                index, target = self.donor_with(Path(tmp), change)
                self.assertEqual(index.match(self.sample_of(target, "i1-s1c6")).status, "NOT_SIMULABLE")
                self.assertGreater(index.provenance[reason], 0)

    def test_a_legacy_record_without_made_by_counts_only_through_its_full_key(self):
        def legacy(path: Path, record: dict, loaded: tt.Loaded) -> None:
            record.pop("made_by")
            path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

        with TemporaryDirectory() as tmp:
            index, target = self.donor_with(Path(tmp), legacy)
            self.assertEqual(index.match(self.sample_of(target, "i1-s1c6")).status, "SIMULATED")

        def legacy_other_prompt(path: Path, record: dict, loaded: tt.Loaded) -> None:
            self.rekeyed(prompt="vision-v2")(path, record, loaded)
            for other in path.parent.glob("*.json"):
                data = json.loads(other.read_text(encoding="utf-8"))
                data.pop("made_by", None)
                other.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        with TemporaryDirectory() as tmp:
            index, target = self.donor_with(Path(tmp), legacy_other_prompt)
            self.assertEqual(index.match(self.sample_of(target, "i1-s1c6")).status, "NOT_SIMULABLE")

        def legacy_wrong_stored_key(path: Path, record: dict, loaded: tt.Loaded) -> None:
            # the file keeps its v3 name (the first 32 hex digits): only the stored full key can tell it apart
            c6 = next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == "i1-s1c6")
            record.pop("made_by")
            record["key"] = tt._cache_key("vision-v2", PROVIDER, MODEL, ev.MOMENT_LABELS, loaded.jpeg[c6["keyframe"]])
            path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

        with TemporaryDirectory() as tmp:
            index, target = self.donor_with(Path(tmp), legacy_wrong_stored_key)
            self.assertEqual(index.match(self.sample_of(target, "i1-s1c6")).status, "NOT_SIMULABLE")
            # and the proof itself compares the stored full key, not only the file name
            donor = tt.load(Path(tmp) / "run-b")
            c6 = next(c for c in donor.run.logs["i1"]["candidates"] if c["id"] == "i1-s1c6")
            basis = (vision.PROMPT_VERSION, PROVIDER, MODEL, tuple(ev.MOMENT_LABELS))
            self.assertEqual(tt.exact_answer(donor, basis, donor.jpeg[c6["keyframe"]], tt.vision_items(c6)[0]), "no_exact_key")

    def test_a_clipped_answer_ending_in_a_space_still_matches_its_record(self):
        # vision.validate is not idempotent at a clipped field that ends in a space; the run logged the fresh answer
        fresh = vision.validate({"description": "Xe tăng", "visible_text": "chữ " * 60, "labels": ["explanation"],
                                 "confidence": 850}, ev.MOMENT_LABELS)
        self.assertTrue(fresh.visible_text.endswith(" "))
        logged = vision.item_of(fresh, DETAIL).to_dict()
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp), name="run-a")
            loaded = tt.load(run)
            c2 = next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == "i1-s1c2")
            basis = (vision.PROMPT_VERSION, PROVIDER, MODEL, tuple(ev.MOMENT_LABELS))
            key = tt._cache_key(*basis, loaded.jpeg[c2["keyframe"]])
            loaded.cache[key[:32]] = {"key": key, **fresh.to_dict()}
            self.assertEqual(tt.exact_answer(loaded, basis, loaded.jpeg[c2["keyframe"]], logged), "exact")

    def test_a_target_record_for_the_same_bytes_wins_over_a_donor(self):
        # production reads the target folder's own cache first: a donor answer there is not what it would reuse
        def target_also(path: Path, record: dict, loaded: tt.Loaded) -> None:
            pass

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            index, target = self.donor_with(root, target_also)
            c6 = target.shot("i1", "i1-s1c6").candidate.keyframe
            jpeg = target.loaded.jpeg[c6]
            key = tt._cache_key(vision.PROMPT_VERSION, PROVIDER, MODEL, ev.MOMENT_LABELS, jpeg)
            target.loaded.cache[key[:32]] = {"key": key, **vision.Seen("Cảnh cũ khác hẳn", (), (), "", ("reaction",), 300,
                                                                       "clear", None, None, 0).to_dict()}
            found = index.match(self.sample_of(target, "i1-s1c6"))
            self.assertEqual((found.status, found.reason), ("NOT_SIMULABLE", "target_cache_conflict"))

    def test_the_target_basis_is_proven_by_its_own_logged_answers(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = make_run_k(root, name="run-a")
            donor = tt.prepare(tt.load(make_run_k(root, name="run-b", texts=donor_texts(), targeted=(2, 6))))
            loaded = tt.load(run)
            answered = [c for c in loaded.run.logs["i1"]["candidates"] if tt.vision_items(c) and c.get("keyframe")]
            # a leftover record from another model, with another answer, proves nothing
            stale = tt._cache_key(vision.PROMPT_VERSION, PROVIDER, "old-model", ev.MOMENT_LABELS, loaded.jpeg[answered[0]["keyframe"]])
            (run / ".dubflow-work" / "mastering" / "vision" / f"{stale[:32]}.json").write_text(json.dumps(
                {"key": stale, **vision.Seen("cũ", (), (), "", ("reaction",), 100, "clear", None, None, 0).to_dict(),
                 "made_by": {"prompt": vision.PROMPT_VERSION, "provider": PROVIDER, "model": "old-model"}}), encoding="utf-8")
            with mock.patch.object(tt, "cache_combos", lambda _: [(vision.PROMPT_VERSION, PROVIDER, "old-model"),
                                                                  (vision.PROMPT_VERSION, PROVIDER, MODEL)]):
                index = tt.DonorIndex(tt.prepare(tt.load(run)), [donor])
                self.assertEqual(index.key_basis, (vision.PROMPT_VERSION, PROVIDER, MODEL, tuple(ev.MOMENT_LABELS)))
            # the same answer under the full label set too: two bases reproduce it, so nothing is simulated
            item = tt.vision_items(answered[0])[0]
            both = tt._cache_key(vision.PROMPT_VERSION, PROVIDER, MODEL, ev.LABELS, loaded.jpeg[answered[0]["keyframe"]])
            (run / ".dubflow-work" / "mastering" / "vision" / f"{both[:32]}.json").write_text(json.dumps(
                {"key": both, **vision.Seen(item["text"], (), (), "", tuple(item["labels"]), item["confidence"], "clear", None, None,
                                            0).to_dict()}, ensure_ascii=False), encoding="utf-8")
            target = tt.prepare(tt.load(run))
            index = tt.DonorIndex(target, [donor])
            self.assertEqual((index.key_basis, index.key_basis_status), (None, "ambiguous"))
            self.assertEqual(index.match(self.sample_of(target, "i1-s1c6")).reason, "target_key_ambiguous")

    def fallback_run(self, root: Path, keyed: str, asked: tuple[str, str] | None | bool = (PROVIDER, "m1")) -> Path:
        """A pre-#808 run that asked for m1 and was answered by m2 (a fallback): reports and item details say m2, no
        record has made_by, and every record is keyed under ``keyed`` with the answer the run logged."""
        run = make_run_k(root, name="run-a", provider=(PROVIDER, "m2"), asked=asked)
        folder = run / ".dubflow-work" / "mastering" / "vision"
        for path in sorted(folder.glob("*.json")):
            path.unlink()
        loaded = tt.load(run)
        for c in loaded.run.logs["i1"]["candidates"]:
            if not (tt.vision_items(c) and c.get("keyframe")):
                continue
            item = tt.vision_items(c)[0]
            key = tt._cache_key(vision.PROMPT_VERSION, PROVIDER, keyed, ev.MOMENT_LABELS, loaded.jpeg[c["keyframe"]])
            seen = vision.Seen(item["text"], (), (), "", tuple(item["labels"]), item["confidence"], "clear", None, None, 0)
            (folder / f"{key[:32]}.json").write_text(json.dumps({"key": key, **seen.to_dict()}, ensure_ascii=False),
                                                     encoding="utf-8")
        return run

    def test_a_stale_record_under_the_answering_model_proves_no_basis(self):
        # production keys by the model asked (Describer.model), the reports keep the model that answered: a legacy
        # record under the answering model reproduces the logged answer but is not what production would read
        with TemporaryDirectory() as tmp:
            loaded = tt.load(self.fallback_run(Path(tmp), keyed="m2"))
            self.assertEqual(tt.cache_combos(loaded), [(vision.PROMPT_VERSION, PROVIDER, "m1")])
            self.assertEqual(tt.key_bases(loaded), [])
            index = tt.DonorIndex(tt.prepare(loaded), [])
            self.assertEqual((index.key_basis, index.key_basis_status), (None, "unproven"))
            self.assertEqual(tt.cache_basis(loaded), "group (cache keys unmatched)")

    def test_the_record_under_the_asked_model_proves_the_basis(self):
        with TemporaryDirectory() as tmp:
            loaded = tt.load(self.fallback_run(Path(tmp), keyed="m1"))
            self.assertEqual(tt.key_bases(loaded), [(vision.PROMPT_VERSION, PROVIDER, "m1", tuple(ev.MOMENT_LABELS))])
            index = tt.DonorIndex(tt.prepare(loaded), [])
            self.assertEqual((index.key_basis_status, index.key_basis_source), ("exact", ["spec"]))
            self.assertEqual(tt.cache_basis(loaded), "exact")

    def test_without_the_asked_pair_in_the_spec_a_legacy_cache_is_unproven(self):
        # no editorial.ai, or a provider without a model (a preset default the run's own version chose): nothing is
        # guessed from the reports, so even the asked-model records prove nothing
        for asked, label in ((None, "no editorial"), ((PROVIDER, ""), "no model"), (("", "m1"), "no provider")):
            with self.subTest(label), TemporaryDirectory() as tmp:
                loaded = tt.load(self.fallback_run(Path(tmp), keyed="m1", asked=asked))
                self.assertIsNone(tt.asked_pair(loaded))
                self.assertEqual((tt.cache_combos(loaded), tt.key_bases(loaded)), ([], []))
                self.assertEqual(tt.DonorIndex(tt.prepare(loaded), []).key_basis_status, "unproven")

    def test_made_by_records_give_their_own_full_tuple_only(self):
        # a made_by record names the asked tuple itself; it is never crossed with the spec or the reports
        with TemporaryDirectory() as tmp:
            run = self.fallback_run(Path(tmp), keyed="m1", asked=None)
            folder = run / ".dubflow-work" / "mastering" / "vision"
            for path in sorted(folder.glob("*.json")):
                record = json.loads(path.read_text(encoding="utf-8"))
                record["made_by"] = {"prompt": vision.PROMPT_VERSION, "provider": PROVIDER, "model": "m1", "answered_by": "m2"}
                path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            loaded = tt.load(run)
            self.assertEqual(tt.cache_combos(loaded), [(vision.PROMPT_VERSION, PROVIDER, "m1")])
            index = tt.DonorIndex(tt.prepare(loaded), [])
            self.assertEqual((index.key_basis_status, index.key_basis_source), ("exact", ["made_by"]))

    def test_a_target_cache_that_proves_no_basis_simulates_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = make_run_k(root, name="run-a")
            for path in sorted((run / ".dubflow-work" / "mastering" / "vision").glob("*.json")):
                record = json.loads(path.read_text(encoding="utf-8"))
                record["key"] = "0" * 64
                path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            target = tt.prepare(tt.load(run))
            donor = tt.prepare(tt.load(make_run_k(root, name="run-b", texts=donor_texts(), targeted=(2, 6))))
            index = tt.DonorIndex(target, [donor])
            self.assertEqual((index.key_basis, index.key_basis_status, index.pool), (None, "unproven", []))
            self.assertEqual(index.match(self.sample_of(target, "i1-s1c6")).reason, "target_key_unproven")

    def test_an_item_answered_by_another_model_is_left_out(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = tt.prepare(tt.load(make_run_k(root, name="run-a")))
            run = make_run_k(root, name="run-b", texts=donor_texts(), targeted=(2, 6))
            log_path = run / ".dubflow-work" / "mastering" / "editorial-i1.json"
            log = json.loads(log_path.read_text(encoding="utf-8"))
            for c in log["candidates"]:
                if c["id"] == "i1-s1c6":
                    for item in tt.vision_items(c):
                        item["detail"] = f"AI {PROVIDER}/another-model"
            log_path.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
            index = tt.DonorIndex(target, [tt.prepare(tt.load(run))])
            self.assertGreater(index.counts["detail_mismatch"], 0)
            self.assertFalse(any(d.candidate_id == "i1-s1c6" for d in index.pool))
            self.assertEqual(index.match(self.sample_of(target, "i1-s1c6")).status, "NOT_SIMULABLE")

    def test_a_target_without_a_proven_key_basis_simulates_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = tt.prepare(tt.load(make_run_k(root, name="run-a", cache=False)))
            donor = tt.prepare(tt.load(make_run_k(root, name="run-b", texts=donor_texts(), targeted=(2, 6))))
            index = tt.DonorIndex(target, [donor])
            self.assertIsNone(index.key_basis)
            self.assertEqual(index.pool, [])
            found = index.match(self.sample_of(target, "i1-s1c6"))
            self.assertEqual((found.status, found.reason), ("NOT_SIMULABLE", "target_key_unproven"))

    def test_the_runs_own_intro_answer_is_its_cache(self):
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp), name="run-a", parts=("intro", "i1"), intro={8: "Xe tải chở lính trên đường"})
            target = tt.prepare(tt.load(run))
            self.assertFalse(target.shot("i1", "i1-s1c8").observed)  # an intro look stays the intro's
            found = tt.DonorIndex(target, []).match(self.sample_of(target, "i1-s1c8"))
            self.assertEqual((found.status, found.own_cache, found.donor_part), ("SIMULATED", True, "intro"))
            cost = tt.e4_cost([found], None, None)
            self.assertEqual((cost["frames_job"], cost["calls_est"]), (1, 0))
            # the run's own answer needs its own exact record too: a record under the right key with another answer
            c8 = target.loaded.jpeg[target.shot("i1", "i1-s1c8").candidate.keyframe]
            key = tt._cache_key(vision.PROMPT_VERSION, PROVIDER, MODEL, ev.MOMENT_LABELS, c8)
            path = run / ".dubflow-work" / "mastering" / "vision" / f"{key[:32]}.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["description"] = "Một cảnh khác"
            path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            target = tt.prepare(tt.load(run))
            index = tt.DonorIndex(target, [])
            self.assertEqual(index.match(self.sample_of(target, "i1-s1c8")).status, "NOT_SIMULABLE")
            self.assertGreater(index.provenance["answer_mismatch"], 0)


class ProvenanceAndSimulationTests(unittest.TestCase):
    def test_each_added_item_is_the_donor_item_verbatim(self):
        with TemporaryDirectory() as tmp:
            target, donor, gt = prepared(Path(tmp))
            index = tt.DonorIndex(target, [donor])
            ctx = tt.Context(gt, target, index, None, False, 500, True, "flagged", None, None)
            sel = tg.select_detail(target.view_A, tg.SelectParams("mixed", 2, None))
            replayed = [index.match(s) for s in sel.samples]
            again, *_ = ctx.replay_with("A", replayed)
            self.assertTrue(any(r.status == "SIMULATED" for r in replayed))
            for r in replayed:
                if r.status != "SIMULATED":
                    continue
                source = next(c for c in donor.loaded.run.logs[r.donor_part]["candidates"] if c["id"] == r.donor_candidate_id)
                expected = tt.vision_items(source)[r.donor_item_index]
                used = next(c for c in again.logs[r.sample.part]["candidates"] if c["id"] == r.sample.candidate_id)
                self.assertEqual(tt.vision_items(used)[-1], expected)

    def test_three_selected_one_answered(self):
        arm = arms_of(STUDY["report"])["open:A+:-:+2x"]
        self.assertEqual((arm["e2"]["selected"], arm["e3"]["simulable_fraction"]), (3, [1, 3]))
        self.assertEqual(sum(arm["e3"]["not_simulable"].values()), 2)
        with TemporaryDirectory() as tmp:
            target, donor, gt = prepared(Path(tmp))
            index = tt.DonorIndex(target, [donor])
            ctx = tt.Context(gt, target, index, None, False, 500, True, "flagged", None, None)
            replayed = [index.match(s) for s in tg.select(target.view_A, tg.SelectParams("uniform", 3, None))]
            again, *_ = ctx.replay_with("A", replayed)
            for r in replayed:
                c = next(c for c in again.logs["i1"]["candidates"] if c["id"] == r.sample.candidate_id)
                self.assertEqual(bool(tt.vision_items(c)), r.status == "SIMULATED")

    def test_the_real_call_batch_is_the_union_of_the_named_arms(self):
        arms = arms_of(STUDY["report"])
        union = {(r["sample"]["part"], r["sample"]["candidate_id"]) for i in tt.BATCH_ARMS for r in arms[i]["samples"]
                 if r["status"] == "NOT_SIMULABLE"}
        batch = STUDY["batch"]
        self.assertEqual({(f["part"], f["candidate_id"]) for f in batch["list"]}, union)
        self.assertEqual((batch["frames"], batch["execution"]), (len(union), "none; owner approval required"))
        label = STUDY["report"]["preregistration"]["label"]
        self.assertEqual((batch["preregistered"], batch["preregistration"]), (label == tt.PREREGISTERED, label))

    def test_the_batch_is_labelled_preregistered_only_when_the_gate_passed(self):
        arms = arms_of(STUDY["report"])["open:mixed:W=fwd:+matched"]
        options = tt.Options()
        for label in (tt.PREREGISTERED, tt.PREREG_UNVERIFIED, tt.PREREG_MISMATCH, tt.NOT_PREREGISTERED):
            batch = tt.real_call_batch("run-a", [arms], options, label)
            self.assertEqual((batch["preregistered"], batch["preregistration"]), (label == tt.PREREGISTERED, label), label)


class KeyframeTimeTests(unittest.TestCase):
    def analysis(self, shot_ms: int, count: int) -> scene.Analysis:
        return scene.Analysis(shot_ms * count, tuple(range(shot_ms, shot_ms * count, shot_ms)),
                              tuple(scene.Shot(a, a + shot_ms, 40) for a in range(0, shot_ms * count, shot_ms)), (), True, "x" * 64)

    def cand(self, cid: str, start: int, end: int, keyframe: str = "k.jpg") -> shots.Candidate:
        return shots.Candidate(cid, "s1", start, end, 0, 30, shots.Quality(120, 50, 300), 1, keyframe)

    def test_a_clipped_window_of_a_long_trimmed_source(self):
        found = self.analysis(1000, 500)  # 500 windows: more than the cap, numbered from WITHIN_BASE inside a trim
        windows = tg.windows_by_number(found, (100_500, 200_000))
        number = shots.WITHIN_BASE + 101
        self.assertEqual(windows[number], (100_120, 100_880))
        t, basis = tg.keyframe_time(self.cand(f"s1c{number}", 100_500, 100_880), windows)
        self.assertEqual((t, basis), (100_500, "numbered_window"))  # the unclipped window's middle, not the logged one

    def test_a_short_trimmed_source_is_numbered_over_the_whole_source(self):
        found = self.analysis(6000, 20)
        windows = tg.windows_by_number(found, (31_000, 60_000))
        t, basis = tg.keyframe_time(self.cand("s1c6", 31_000, 35_880), windows)
        self.assertEqual((t, basis), (33_000, "numbered_window"))
        self.assertEqual(tg.keyframe_time(self.cand("s1c99", 1000, 3000), windows), (2000, "logged_mid"))

    def test_sub_shots(self):
        windows = tg.windows_by_number(self.analysis(12_000, 1), None)
        self.assertEqual(windows[1], (0, 12_000))
        three = [self.cand("s1c1f1", 0, 3900, "t-c1f1.jpg"), self.cand("s1c1f2", 3900, 8100, "t-c1.jpg"),
                 self.cand("s1c1f3", 8100, 12_000, "t-c1f3.jpg")]
        self.assertEqual([tg.keyframe_time(c, windows, three) for c in three],
                         [(1800, "subshot_point"), (6000, "subshot_point"), (10_200, "subshot_point")])
        two = [self.cand("s1c1f1", 0, 8100, "t-c1.jpg"), self.cand("s1c1f2", 8100, 12_000, "t-c1f3.jpg")]
        self.assertEqual([tg.keyframe_time(c, windows, two)[0] for c in two], [6000, 10_200])


class ClassificationTests(unittest.TestCase):
    def test_broad_copies_are_kept_targeted_copies_removed(self):
        for cache in (False, True):
            with TemporaryDirectory() as tmp:
                prep = tt.prepare(tt.load(make_run_k(Path(tmp), twins={8: 3, 1: 4}, cache=cache)))
                classes = prep.classes
                self.assertEqual(prep.classification["classification_basis"], "exact" if cache else "group")
                kept = {c["id"]: bool(tt.vision_items(c)) for c in prep.A0.logs["i1"]["candidates"]}
                if cache:  # the cache says which frame was asked
                    self.assertEqual((classes["i1-s1c3"], classes["i1-s1c8"]), ("broad", "broad_shared"))
                    self.assertEqual((classes["i1-s1c4"], classes["i1-s1c1"]), ("targeted", "targeted_shared"))
                    self.assertEqual(prep.classification["counts"]["targeted_shared"], 1)
                    self.assertEqual(prep.classification["targeted_frames_item_parts"], 2)  # c2 and c4: c1 is a copy
                    self.assertTrue(kept["i1-s1c3"] and kept["i1-s1c8"])
                    self.assertFalse(kept["i1-s1c4"] or kept["i1-s1c1"] or kept["i1-s1c2"])
                else:  # without a proven key nothing outside the broad pass is inferred: unproven, kept in A0
                    self.assertEqual(classes["i1-s1c3"], "broad")
                    self.assertEqual({classes[i] for i in ("i1-s1c8", "i1-s1c4", "i1-s1c1", "i1-s1c2")}, {"unproven"})
                    self.assertEqual(prep.classification["targeted_frames_item_parts"], 0)
                    self.assertTrue(all(kept[i] for i in ("i1-s1c3", "i1-s1c8", "i1-s1c4", "i1-s1c1", "i1-s1c2")))

    @staticmethod
    def rewrite_record(run: Path, candidate: str, change, labels=ev.MOMENT_LABELS) -> Path:
        """Apply ``change`` to the record under ``candidate``'s full production key (and write it back)."""
        loaded = tt.load(run)
        c = next(c for log in loaded.run.logs.values() for c in log.get("candidates") or () if c["id"] == candidate)
        key = tt._cache_key(vision.PROMPT_VERSION, PROVIDER, MODEL, labels, loaded.jpeg[c["keyframe"]])
        path = run / ".dubflow-work" / "mastering" / "vision" / f"{key[:32]}.json"
        record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"key": key}
        change(record)
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return path

    def test_a_record_with_other_side_metadata_under_the_same_key_is_not_the_logged_answer(self):
        # vision.apply writes the box, hook, product flag and product group beside the item: a record under the frame's
        # own key with the same text but other side metadata is not the whole logged answer, so A0 keeps the answer
        # and its metadata (fail closed)
        box = {"x": 100, "y": 100, "w": 500, "h": 500}
        changes = [("product box", {"product_box": box}), ("hook", {"hook": 900}), ("product group", {"product_group": "nhóm-khác"})]
        for label, change in changes:
            with self.subTest(label), TemporaryDirectory() as tmp:
                run = make_run_k(Path(tmp))
                self.rewrite_record(run, "i1-s1c2", lambda r: r.update(change))
                loaded = tt.load(run)
                c2 = next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == "i1-s1c2")
                self.assertEqual(tt.own_answer(loaded, c2), "side_mismatch")
                prep = tt.prepare(loaded)
                self.assertEqual(prep.classification["classification_basis"], "exact")
                self.assertEqual(prep.classes["i1-s1c2"], "unproven")
                self.assertEqual(prep.classes["i1-s1c4"], "targeted")
                kept = next(c for c in prep.A0.logs["i1"]["candidates"] if c["id"] == "i1-s1c2")
                self.assertEqual(tt.vision_items(kept), tt.vision_items(c2))
                self.assertEqual({k: kept["evidence"].get(k) for k in tt.SIDE_FIELDS},
                                 {k: c2["evidence"].get(k) for k in tt.SIDE_FIELDS})

    def test_an_unanswered_broad_pick_is_not_the_source_of_a_shared_copy(self):
        # another frame's record proves the run's key basis, but neither the broad pick c3 nor its same-answer mate c8
        # has a record of its own: nothing proves the answer they share, so c8 is unproven, not targeted_shared
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp), twins={8: 3})
            self.rewrite_record(run, "i1-s1c3", lambda r: None).unlink()
            loaded = tt.load(run)
            c3, c8 = (next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == f"i1-s1c{n}") for n in (3, 8))
            self.assertEqual((tt.own_answer(loaded, c3), tt.own_answer(loaded, c8)), ("no_exact_key", "no_exact_key"))
            self.assertEqual(tt.answer_identity(c3), tt.answer_identity(c8))
            self.assertEqual(tt.cache_basis(loaded), "exact")
            broad = tt.reconstruct_broad(loaded)
            self.assertEqual(broad["status"], "NOT_RECONSTRUCTED")
            classes, _ = tt.classify(loaded, {"picked": broad["picked"]})
            self.assertEqual((classes["i1-s1c3"], classes["i1-s1c8"]), ("broad", "unproven"))

    def test_a_correct_key_with_another_answer_is_not_a_targeted_look(self):
        # the record sits under the frame's own full key, but its answer is not the one the run logged
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp))
            self.rewrite_record(run, "i1-s1c2", lambda r: r.update(description="Một cảnh khác hẳn"))
            loaded = tt.load(run)
            c2 = next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == "i1-s1c2")
            self.assertEqual(tt.own_answer(loaded, c2), "answer_mismatch")
            prep = tt.prepare(loaded)
            self.assertEqual(prep.classification["classification_basis"], "exact")  # other frames still prove it
            self.assertEqual(prep.classes["i1-s1c2"], "unproven")
            self.assertEqual(prep.classes["i1-s1c4"], "targeted")
            self.assertEqual(prep.classification["counts"]["unproven"], 1)
            self.assertEqual(prep.classification["targeted_frames_item_parts"], 1)
            kept = {c["id"]: bool(tt.vision_items(c)) for c in prep.A0.logs["i1"]["candidates"]}
            self.assertTrue(kept["i1-s1c2"])  # not dropped from A0 as a proven targeted answer
            self.assertFalse(kept["i1-s1c4"])

    def test_key_presence_alone_never_gives_the_exact_basis(self):
        # every record is under its correct full key, but none holds the answer the run logged
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp))
            for path in sorted((run / ".dubflow-work" / "mastering" / "vision").glob("*.json")):
                record = json.loads(path.read_text(encoding="utf-8"))
                record["description"] = "Không phải câu trả lời đã ghi"
                path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            loaded = tt.load(run)
            self.assertEqual(tt.key_bases(loaded), [])
            prep = tt.prepare(loaded)
            self.assertEqual(prep.classification["classification_basis"], "group (cache keys unmatched)")
            # fail closed: no answer-proven key, so the non-broad answers are unproven and stay in A0
            self.assertEqual((prep.classes["i1-s1c2"], prep.classes["i1-s1c4"]), ("unproven", "unproven"))
            self.assertNotIn("targeted", prep.classification["counts"])
            kept = {c["id"]: bool(tt.vision_items(c)) for c in prep.A0.logs["i1"]["candidates"]}
            self.assertTrue(kept["i1-s1c2"] and kept["i1-s1c4"])
            self.assertIsNone(prep.broad["picked_not_in_cache"])

    def test_an_ambiguous_basis_keeps_every_non_broad_answer(self):
        # the same answers under the full label set too: two bases reproduce them, so neither is proven
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp), twins={8: 3, 1: 4})
            loaded = tt.load(run)
            for c in loaded.run.logs["i1"]["candidates"]:
                if not (tt.vision_items(c) and c.get("keyframe")):
                    continue
                item = tt.vision_items(c)[0]
                seen = vision.Seen(item["text"], (), (), "", tuple(item["labels"]), item["confidence"], "clear", None, None, 0)
                self.rewrite_record(run, c["id"], lambda r, seen=seen: r.update(seen.to_dict()), labels=ev.LABELS)
            loaded = tt.load(run)
            self.assertEqual(len(tt.key_bases(loaded)), 2)
            c4 = next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == "i1-s1c4")
            self.assertEqual(tt.own_answer(loaded, c4), "no_key_basis")
            prep = tt.prepare(loaded)
            self.assertEqual(prep.classification["classification_basis"], "group (cache basis ambiguous)")
            self.assertEqual({prep.classes[i] for i in ("i1-s1c4", "i1-s1c1", "i1-s1c2")}, {"unproven"})
            self.assertNotIn("targeted", prep.classification["counts"])
            self.assertIsNone(prep.broad["picked_not_in_cache"])
            kept = {c["id"]: bool(tt.vision_items(c)) for c in prep.A0.logs["i1"]["candidates"]}
            self.assertTrue(kept["i1-s1c4"] and kept["i1-s1c1"] and kept["i1-s1c2"])
            # mixed: c4 also loses both of its records (no matching answer under either basis); still kept
            for labels in (ev.MOMENT_LABELS, ev.LABELS):
                self.rewrite_record(run, "i1-s1c4", lambda r: None, labels=labels).unlink()
            loaded = tt.load(run)
            self.assertEqual(len(tt.key_bases(loaded)), 2)
            prep = tt.prepare(loaded)
            self.assertEqual(prep.classes["i1-s1c4"], "unproven")
            kept = {c["id"]: bool(tt.vision_items(c)) for c in prep.A0.logs["i1"]["candidates"]}
            self.assertTrue(kept["i1-s1c4"])

    def test_a_broad_pick_whose_record_is_another_answer_is_not_cached(self):
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp))
            self.rewrite_record(run, "i1-s1c3", lambda r: r.update(confidence=120))
            prep = tt.prepare(tt.load(run))
            self.assertEqual(prep.classification["classification_basis"], "exact")
            self.assertEqual((prep.broad["status"], prep.broad["picked_not_in_cache"]), ("NOT_RECONSTRUCTED", 1))
            self.assertIn("picked not in cache", prep.broad["reasons"])

    def test_a_byte_identical_copy_of_a_broad_frame_is_shared_not_targeted(self):
        # the copy's full key is the broad frame's key: the record there proves the broad look, not a look at the copy
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp), twins={8: 3})
            folder = run / ".dubflow-work" / "mastering"
            (folder / "i1-s1-c8.jpg").write_bytes((folder / "i1-s1-c3.jpg").read_bytes())
            prep = tt.prepare(tt.load(run))
            self.assertEqual(prep.classification["classification_basis"], "exact")
            self.assertEqual((prep.classes["i1-s1c3"], prep.classes["i1-s1c8"]), ("broad", "broad_shared"))
            self.assertEqual(prep.classification["targeted_frames_item_parts"], 2)
            kept = {c["id"]: bool(tt.vision_items(c)) for c in prep.A0.logs["i1"]["candidates"]}
            self.assertTrue(kept["i1-s1c8"])

    def test_a_broad_picks_duplicate_with_its_own_record_is_still_shared_not_targeted(self):
        # the broad look answered the whole group and the targeted look skips answered shots: a record of the copy's
        # own frame (an earlier run's look kept in the job's cache) does not make the copy a targeted look
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp), twins={8: 3})
            loaded = tt.load(run)
            c3, c8 = (next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == f"i1-s1c{n}") for n in (3, 8))
            cache = run / ".dubflow-work" / "mastering" / "vision"
            mine = tt._cache_key(vision.PROMPT_VERSION, PROVIDER, MODEL, ev.MOMENT_LABELS, loaded.jpeg[c3["keyframe"]])
            record = json.loads((cache / f"{mine[:32]}.json").read_text(encoding="utf-8"))
            key = tt._cache_key(vision.PROMPT_VERSION, PROVIDER, MODEL, ev.MOMENT_LABELS, loaded.jpeg[c8["keyframe"]])
            self.assertNotEqual(key, mine)
            (cache / f"{key[:32]}.json").write_text(json.dumps({**record, "key": key}, ensure_ascii=False), encoding="utf-8")
            loaded = tt.load(run)
            self.assertEqual(tt.own_answer(loaded, c8), "exact")
            prep = tt.prepare(loaded)
            self.assertEqual(prep.classification["classification_basis"], "exact")
            self.assertEqual((prep.classes["i1-s1c3"], prep.classes["i1-s1c8"]), ("broad", "broad_shared"))
            self.assertEqual(prep.classification["targeted_frames_item_parts"], 2)
            kept = {c["id"]: bool(tt.vision_items(c)) for c in prep.A0.logs["i1"]["candidates"]}
            self.assertTrue(kept["i1-s1c8"])
            # another answer in a broad pick's group was not a targeted look either: it is unproven and kept
            self.rewrite_logged(run, "i1-s1c8", text="Một câu trả lời khác")
            path = cache / f"{key[:32]}.json"
            path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "description": "Một câu trả lời khác"},
                                       ensure_ascii=False), encoding="utf-8")
            prep = tt.prepare(tt.load(run))
            self.assertEqual(prep.classes["i1-s1c8"], "unproven")
            kept = {c["id"]: bool(tt.vision_items(c)) for c in prep.A0.logs["i1"]["candidates"]}
            self.assertTrue(kept["i1-s1c8"])

    def test_a_targeted_look_in_the_group_of_an_unanswered_broad_pick_stays_targeted(self):
        # the broad pass got no answer for c3 (a dropped or rejected frame): nothing was shared to c8, so the targeted
        # look could ask c8 and share its answer back to c3; c8 is the targeted look, c3 its shared copy
        with TemporaryDirectory() as tmp:
            texts = {k: v for k, v in TEXTS.items() if k != 3}
            texts[8] = "Cờ đỏ trên tháp"
            run = make_run_k(Path(tmp), texts=texts, twins={3: 8}, targeted=(2, 4, 8))
            loaded = tt.load(run)
            c3 = next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == "i1-s1c3")
            self.assertEqual(tt.own_answer(loaded, c3), "no_exact_key")
            broad = tt.reconstruct_broad(loaded)
            self.assertEqual(broad["status"], "NOT_RECONSTRUCTED")
            self.assertIn("picked not in cache", broad["reasons"])
            classes, report = tt.classify(loaded, {"picked": broad["picked"]})
            self.assertEqual((classes["i1-s1c3"], classes["i1-s1c8"]), ("broad", "targeted"))
            self.assertEqual(report["targeted_frames_item_parts"], 3)

    def test_only_a_proven_broad_answer_makes_a_byte_identical_frame_elsewhere_a_shared_copy(self):
        # c4 (a targeted look, its own duplicate group) holds the same JPEG bytes as the broad pick c3, so both frames
        # share one production key. When c3's own logged answer is that key's record, c4's matching answer is a copy of
        # the broad look (broad_shared); when c3's logged answer is not the record (another answer, or other side
        # metadata), the broad pick proves nothing and the record is c4's own targeted look, which A0 removes
        folder = lambda run: run / ".dubflow-work" / "mastering"
        cases = [("proven broad copy", None, "broad_shared"),
                 ("broad answer mismatch", {}, "targeted"),
                 ("broad side mismatch", {"side": {"hook": 900}}, "targeted")]
        for label, broken, expected in cases:
            with self.subTest(label), TemporaryDirectory() as tmp:
                run = make_run_k(Path(tmp))
                if broken is None:
                    (folder(run) / "i1-s1-c4.jpg").write_bytes((folder(run) / "i1-s1-c3.jpg").read_bytes())
                    self.rewrite_logged(run, "i1-s1c4", text=TEXTS[3])
                else:
                    (folder(run) / "i1-s1-c3.jpg").write_bytes((folder(run) / "i1-s1-c4.jpg").read_bytes())
                    if broken:
                        self.rewrite_logged(run, "i1-s1c3", text=TEXTS[4], **broken)
                loaded = tt.load(run)
                c3, c4 = (next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == f"i1-s1c{n}") for n in (3, 4))
                self.assertEqual(loaded.jpeg[c3["keyframe"]], loaded.jpeg[c4["keyframe"]])
                self.assertNotEqual(tt.job_groups(loaded.run).get("i1-s1c3"), tt.job_groups(loaded.run).get("i1-s1c4"))
                self.assertEqual(tt.own_answer(loaded, c4), "exact")
                if broken is not None:
                    self.assertNotEqual(tt.own_answer(loaded, c3), "exact")
                prep = tt.prepare(loaded)
                self.assertEqual(prep.classification["classification_basis"], "exact")
                self.assertEqual((prep.classes["i1-s1c3"], prep.classes["i1-s1c4"]), ("broad", expected))
                # an unproven broad pick leaves the broad pass NOT_RECONSTRUCTED, so prepare builds no A0 (fail closed);
                # the A0 evidence these classes give is checked directly, as prepare would build it
                self.assertEqual(prep.A0 is None, broken is not None)
                answers = {i: tt.answer_identity(c) for i, c in tt.first_answers(loaded.run).items()}
                evidence = tt.a0_evidence(prep.classes, answers, lambda c: tt.own_answer(loaded, c) == "exact")(
                    "i1", loaded.run.logs["i1"])
                kept = {i: any(x.origin == "vision" for x in found.items) for i, found in evidence.items()}
                self.assertTrue(kept["i1-s1c3"])  # the broad answer stays in A0, proven or not
                self.assertEqual(kept["i1-s1c4"], expected == "broad_shared")
                if prep.A0 is not None:
                    kept = {c["id"]: bool(tt.vision_items(c)) for c in prep.A0.logs["i1"]["candidates"]}
                    self.assertEqual((kept["i1-s1c3"], kept["i1-s1c4"]), (True, True))

    def test_byte_identical_targeted_copies_count_one_look(self):
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp), twins={1: 4})
            folder = run / ".dubflow-work" / "mastering"
            (folder / "i1-s1-c1.jpg").write_bytes((folder / "i1-s1-c4.jpg").read_bytes())
            prep = tt.prepare(tt.load(run))
            self.assertEqual(sorted([prep.classes["i1-s1c1"], prep.classes["i1-s1c4"]]), ["targeted", "targeted_shared"])
            self.assertEqual(prep.classification["targeted_frames_item_parts"], 2)  # c2 and one of the copies

    @staticmethod
    def rewrite_logged(run: Path, candidate: str, side: dict | None = None, **fields) -> None:
        """Change the first logged vision item of ``candidate`` (and its evidence ``side`` fields) in every saved
        editorial log (no cache record moves)."""
        for path in sorted((run / ".dubflow-work" / "mastering").glob("editorial-*.json")):
            log = json.loads(path.read_text(encoding="utf-8"))
            for c in log.get("candidates") or ():
                if c["id"] == candidate and c.get("evidence"):
                    item = next(i for i in c["evidence"]["items"] if i["origin"] == "vision")
                    item.update(fields)
                    c["evidence"].update(side or {})
            path.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")

    def test_a_shared_copy_must_be_the_whole_answer_its_proven_mate_gave(self):
        # same duplicate group, same text and labels, no record of its own, but another confidence or detail: that is
        # not the Seen production's vision.share() handed on, so it is unproven and A0 keeps it
        cases = [("targeted mate", {1: 4}, "i1-s1c1", "i1-s1c4", "targeted_shared"),
                 ("broad mate", {8: 3}, "i1-s1c8", "i1-s1c3", "broad_shared")]
        box = {"x": 100, "y": 100, "w": 500, "h": 500, "label": "subject"}
        changes = [("confidence", {"confidence": 600}), ("detail", {"detail": "AI other-provider/other-model"}),
                   ("both", {"confidence": 600, "detail": "AI other-provider/other-model"}),
                   ("hook", {"side": {"hook": 900}}), ("product box", {"side": {"product_box": box, "product_visible": True}}),
                   ("product group", {"side": {"same_product": "nhóm-khác"}})]
        for label, twins, copy, mate, shared in cases:
            with self.subTest(label, change="none"), TemporaryDirectory() as tmp:
                # the positive case: the exact copy of the mate's logged answer is still a shared copy
                loaded = tt.load(make_run_k(Path(tmp), twins=twins))
                c = next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == copy)
                self.assertEqual(tt.own_answer(loaded, c), "no_exact_key")
                prep = tt.prepare(loaded)
                self.assertEqual(prep.classification["classification_basis"], "exact")
                self.assertEqual(prep.classes[copy], shared)
            for name, fields in changes:
                with self.subTest(label, change=name), TemporaryDirectory() as tmp:
                    run = make_run_k(Path(tmp), twins=twins)
                    self.rewrite_logged(run, copy, **fields)
                    loaded = tt.load(run)
                    c = next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == copy)
                    m = next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == mate)
                    self.assertEqual((tt.vision_items(c)[0]["text"], tt.vision_items(c)[0]["labels"]),
                                     (tt.vision_items(m)[0]["text"], tt.vision_items(m)[0]["labels"]))
                    self.assertEqual(tt.own_answer(loaded, c), "no_exact_key")
                    prep = tt.prepare(loaded)
                    self.assertEqual(prep.classification["classification_basis"], "exact")
                    self.assertEqual(prep.classes[copy], "unproven")
                    self.assertEqual(prep.classes[mate], "targeted" if shared == "targeted_shared" else "broad")
                    self.assertNotIn(shared, prep.classification["counts"])
                    kept = {x["id"]: tt.vision_items(x) for x in prep.A0.logs["i1"]["candidates"]}
                    self.assertTrue(kept[copy])  # A0 retains the answer
                    for key, value in fields.items():
                        if key != "side":
                            self.assertEqual(kept[copy][0][key], value)
                    evidence = next(x for x in prep.A0.logs["i1"]["candidates"] if x["id"] == copy)["evidence"]
                    for key, value in (fields.get("side") or {}).items():
                        self.assertEqual(evidence[key], value)  # and the metadata set with it

    @staticmethod
    def a0_direct(loaded, classes) -> dict[str, dict]:
        """A0's evidence for part i1 as ``prepare`` builds it (proven answers, own-record proof), by candidate id."""
        answers = {i: tt.answer_identity(c) for i, c in tt.first_answers(loaded.run).items()}
        a0 = tt.a0_evidence(classes, answers, lambda c: tt.own_answer(loaded, c) == "exact")
        return {i: e.to_dict() for i, e in a0("i1", loaded.run.logs["i1"]).items()}

    @staticmethod
    def split_shot(run: Path, number: int, keep: tuple[int, ...] = (1, 3)) -> list[str]:
        """Split shot ``number`` of part i1 the way ``frames.merge`` does: the extra frames ``keep`` (1 early, 3 late; a
        frame ``look_again`` could not use is left out), with their own JPEGs, dhashes and, when the run has a cache,
        records under the full key, that show something else. Returns the piece ids in log order."""
        folder = run / ".dubflow-work" / "mastering"
        path = folder / "editorial-i1.json"
        log = json.loads(path.read_text(encoding="utf-8"))
        index = next(n for n, c in enumerate(log["candidates"]) if c["id"] == f"i1-s1c{number}")
        shot = log["candidates"][index]
        found = tt.evidence_from(shot["evidence"])
        made = shots.Candidate.from_dict(shot)
        texts = {1: "Một người lính đứng gác", 3: "Chiếc thuyền trên sông"}
        extra = []
        for n, at in zip((1, 3), more_frames.extra_times(made)):
            if n not in keep:
                continue
            name = f"i1-s1-c{number}{more_frames.SPLIT_MARK}{n}.jpg"
            data = b"\xff\xd8" + f"extra{number}-{n}".encode() + b"\xff\xd9"
            (folder / name).write_bytes(data)
            seen = vision.Seen(texts[n], (), (), "", ("explanation",), 850, "clear", None, None, 0)
            if (folder / "vision").is_dir():
                key = tt._cache_key(vision.PROMPT_VERSION, PROVIDER, MODEL, ev.MOMENT_LABELS, hashlib.sha256(data).hexdigest())
                (folder / "vision" / f"{key[:32]}.json").write_text(json.dumps(
                    {"key": key, **seen.to_dict(), "made_by": {"prompt": vision.PROMPT_VERSION, "provider": PROVIDER,
                                                               "model": MODEL, "answered_by": MODEL}}, ensure_ascii=False),
                    encoding="utf-8")
            extra.append((at, vision.item_of(seen, DETAIL), name, 12345 + n))
        pieces = []
        for part, evidence in more_frames.merge(made, found.summary(), extra, found):
            piece = dict(shot, id=part.candidate_id, start_ms=part.start_ms, end_ms=part.end_ms, keyframe=part.keyframe,
                         evidence=evidence.to_dict())
            pieces.append(piece)
        log["candidates"][index:index + 1] = pieces
        path.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
        return [piece["id"] for piece in pieces]

    def test_a_split_shots_middle_piece_is_its_shot_and_only_extra_frames_are_multi_frame(self):
        # frames.merge keeps the shot's own keyframe and original answer on the middle piece: a broad pick's answer
        # stays in A0, a proven targeted look's goes, an unproven one stays; the extra frames' pieces are multi_frame.
        # The middle piece is the one with the shot's own keyframe, wherever merge numbered it (one extra frame: f1/f2).
        # Under an exact basis a logged split means the groups were recomputed: the broad pass is not claimed, so the
        # classes are checked against the broad picks of the same run before the split
        for keep in ((1, 3), (3,), (1,)):
            for cache, number, middle_class in ((True, 3, "broad"), (False, 3, "broad"), (True, 4, "targeted"),
                                                (False, 4, "unproven")):
                with self.subTest(keep=keep, cache=cache, shot=number), TemporaryDirectory() as tmp:
                    run = make_run_k(Path(tmp), cache=cache)
                    picks = {"picked": tt.reconstruct_broad(tt.load(run))["picked"]}
                    pieces = self.split_shot(run, number, keep)
                    loaded = tt.load(run)
                    listed = {c["id"]: c for c in loaded.run.logs["i1"]["candidates"]}
                    middle = next(i for i in pieces if listed[i]["keyframe"] == f"i1-s1-c{number}.jpg")
                    extras = [i for i in pieces if i != middle]
                    self.assertEqual(len(extras), len(keep))
                    broad = tt.reconstruct_broad(loaded)
                    if cache:
                        self.assertEqual(broad["status"], "NOT_RECONSTRUCTED")
                        self.assertEqual(broad["reasons"], ["duplicate groups recomputed after a split"])
                        self.assertEqual(broad["picked_not_in_cache"], 0)  # checked on the middle piece's answer
                    else:
                        self.assertEqual(broad["status"], "RECONSTRUCTED", broad.get("reasons"))
                    self.assertEqual(broad["picked"], picks["picked"])
                    classes, _ = tt.classify(loaded, picks)
                    self.assertEqual(classes[middle], middle_class)
                    self.assertEqual({classes[i] for i in extras}, {"multi_frame"})
                    kept = self.a0_direct(loaded, classes)
                    middle_kept = [i["text"] for i in kept[middle]["items"] if i["origin"] == "vision"]
                    self.assertEqual(middle_kept, [] if middle_class == "targeted" else [TEXTS[number]])
                    for i in extras:
                        self.assertFalse([x for x in kept[i]["items"] if x["origin"] == "vision"])

    def test_a_log_with_only_extra_frame_pieces_has_no_middle_piece(self):
        # the trimmed log can hold an extra frame's piece alone: it is never taken for the shot's own answer
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp))
            f1, f2, f3 = self.split_shot(run, 3)
            path = run / ".dubflow-work" / "mastering" / "editorial-i1.json"
            log = json.loads(path.read_text(encoding="utf-8"))
            log["candidates"] = [c for c in log["candidates"] if c["id"] not in (f1, f2)]
            path.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
            loaded = tt.load(run)
            classes, _ = tt.classify(loaded, {"picked": ["i1-s1c3", "i1-s1c5", "i1-s1c7"]})
            self.assertEqual(classes[f3], "multi_frame")
            found = tt.a0_evidence(classes)("i1", loaded.run.logs["i1"])[f3]
            self.assertFalse(any(i.origin == "vision" for i in found.items))

    def test_a_broad_pick_split_without_its_middle_piece_is_not_reconstructed(self):
        # with only extra-frame pieces logged, no piece holds the picture the broad look saw: the parent is not
        # folded from an extra frame and the broad pass is not claimed as reconstructed (fail closed)
        for cache in (True, False):
            with self.subTest(cache=cache), TemporaryDirectory() as tmp:
                run = make_run_k(Path(tmp), cache=cache)
                f1, f2, f3 = self.split_shot(run, 3)
                path = run / ".dubflow-work" / "mastering" / "editorial-i1.json"
                log = json.loads(path.read_text(encoding="utf-8"))
                log["candidates"] = [c for c in log["candidates"] if c["id"] != f2]
                path.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
                loaded = tt.load(run)
                self.assertEqual(tt.split_without_middle(loaded.run), ["i1-s1c3"])
                folded = next(c for pool in tt.broad_pools(loaded.run) for c in pool if c.candidate_id == "i1-s1c3")
                self.assertIsNone(folded.keyframe)
                broad = tt.reconstruct_broad(loaded)
                self.assertEqual(broad["status"], "NOT_RECONSTRUCTED")
                self.assertIn("split shot without its original keyframe", broad["reasons"])
                prep = tt.prepare(loaded)
                self.assertIsNone(prep.A0)
                self.assertEqual({prep.classes[f1], prep.classes[f3]}, {"multi_frame"})

    def test_an_item_parts_copy_and_group_represent_an_id_before_a_hook_or_intro_copy(self):
        # w3 orders parts hook, intro, items, outro: the intro's copy of an item shot must not decide its proof or
        # group; the intro copy is removed from A0 only when it carries the same whole answer
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp))
            clean = tt.prepare(tt.load(run))
            self.assertEqual(clean.classes["i1-s1c4"], "targeted")
            picks = {"picked": clean.broad["picked"]}
            folder = run / ".dubflow-work" / "mastering"
            program = run / ".dubflow-work" / "program.json"
            if program.is_file():  # the programme lists the intro before the items
                value = json.loads(program.read_text(encoding="utf-8"))
                value["parts"] = [{"id": "intro"}] + list(value.get("parts") or ())
                program.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            i1 = json.loads((folder / "editorial-i1.json").read_text(encoding="utf-8"))
            c4 = next(c for c in i1["candidates"] if c["id"] == "i1-s1c4")
            for label, change in (("same answer", {}), ("stale answer", {"text": "Câu trả lời cũ khác hẳn"}),
                                  ("other hook", {"hook": 777})):
                with self.subTest(label):
                    copy = json.loads(json.dumps(c4))
                    copy["group"] = "nhóm-intro"
                    item = next(i for i in copy["evidence"]["items"] if i["origin"] == "vision")
                    item.update({k: v for k, v in change.items() if k != "hook"})
                    if "hook" in change:
                        copy["evidence"]["hook"] = change["hook"]
                    intro = dict(i1, candidates=[copy], units=[])
                    (folder / "editorial-intro.json").write_text(json.dumps(intro, ensure_ascii=False), encoding="utf-8")
                    loaded = tt.load(run)
                    self.assertEqual(loaded.run.order[0], "intro")
                    self.assertEqual(tt.job_groups(loaded.run)["i1-s1c4"], c4.get("group") or "i1-s1c4")
                    self.assertIs(tt.first_answers(loaded.run)["i1-s1c4"], next(
                        c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == "i1-s1c4"))
                    classes, report = tt.classify(loaded, picks)
                    self.assertEqual(report["classification_basis"], "exact")
                    self.assertEqual(classes["i1-s1c4"], "targeted")  # proven on the item part's copy
                    answers = {i: tt.answer_identity(c) for i, c in tt.first_answers(loaded.run).items()}
                    a0 = tt.a0_evidence(classes, answers)
                    has = {part: any(i.origin == "vision" for i in a0(part, loaded.run.logs[part])["i1-s1c4"].items)
                           for part in ("intro", "i1")}
                    self.assertEqual(has, {"intro": bool(change), "i1": False})
                    if change.get("hook"):
                        self.assertEqual(a0("intro", loaded.run.logs["intro"])["i1-s1c4"].hook, 777)

    def test_an_extra_frames_answer_is_never_the_source_of_a_shared_copy(self):
        # frames.look_again asks the describer directly and shares nothing: a duplicate with no record of its own
        # that carries an extra frame's answer is not a shared copy of it
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp), twins={6: 4})
            picks = {"picked": tt.reconstruct_broad(tt.load(run))["picked"]}
            self.rewrite_logged(run, "i1-s1c6", text="Một người lính đứng gác")
            self.split_shot(run, 4)
            loaded = tt.load(run)
            classes, _ = tt.classify(loaded, picks)
            self.assertEqual(classes["i1-s1c4f1"], "multi_frame")
            self.assertEqual(classes["i1-s1c6"], "unproven")
            kept = self.a0_direct(loaded, classes)
            self.assertTrue([x for x in kept["i1-s1c6"]["items"] if x["origin"] == "vision"])

    def test_a_copy_re_asked_from_the_cache_in_another_part_is_proven_on_its_own(self):
        # a later part that re-asks a frame gets it from the cache: its copy can carry another detail (a model
        # fallback in between); its own record proves it, so A0 removes it like the first copy
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp), parts=("i1", "i2"))
            clean = tt.prepare(tt.load(run))
            picks = {"picked": clean.broad["picked"]}
            folder = run / ".dubflow-work" / "mastering"
            c4 = next(c for c in json.loads((folder / "editorial-i1.json").read_text(encoding="utf-8"))["candidates"]
                      if c["id"] == "i1-s1c4")
            other = json.loads(json.dumps(c4))
            next(i for i in other["evidence"]["items"] if i["origin"] == "vision")["detail"] = f"AI {PROVIDER}/fallback-model"
            path = folder / "editorial-i2.json"
            log = json.loads(path.read_text(encoding="utf-8"))
            log["candidates"] = [c for c in log["candidates"] if c["id"] != "i1-s1c4"] + [other]
            path.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
            loaded = tt.load(run)
            classes, _ = tt.classify(loaded, picks)
            self.assertEqual(classes["i1-s1c4"], "targeted")
            answers = {i: tt.answer_identity(c) for i, c in tt.first_answers(loaded.run).items()}
            copy = next(c for c in loaded.run.logs["i2"]["candidates"] if c["id"] == "i1-s1c4")
            self.assertEqual(tt.own_answer(loaded, copy), "exact")
            for proven, kept in ((None, True), (lambda c: tt.own_answer(loaded, c) == "exact", False)):
                found = tt.a0_evidence(classes, answers, proven)("i2", loaded.run.logs["i2"])["i1-s1c4"]
                self.assertEqual(any(i.origin == "vision" for i in found.items), kept)

    def test_under_an_exact_basis_a_logged_split_is_not_claimed_as_a_reconstruction(self):
        # production recomputes the duplicate groups after a split; the logged groups may not be the broad pass's
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp))
            self.split_shot(run, 2)
            broad = tt.reconstruct_broad(tt.load(run))
            self.assertEqual(broad["status"], "NOT_RECONSTRUCTED")
            self.assertIn("duplicate groups recomputed after a split", broad["reasons"])
            self.assertIsNone(tt.prepare(tt.load(run)).A0)

    def test_a_split_parents_group_comes_from_its_middle_piece(self):
        # production recomputes the duplicate groups after a split: the extra frame's piece leads its own group, and
        # the broad pass grouped the picture that the middle piece kept
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp))
            f1, f2, f3 = self.split_shot(run, 5)
            path = run / ".dubflow-work" / "mastering" / "editorial-i1.json"
            log = json.loads(path.read_text(encoding="utf-8"))
            for c in log["candidates"]:
                if c["id"] in (f1, f3):
                    c["group"] = c["id"]
                elif c["id"] == f2:
                    c["group"] = "i1-s1c5"
            path.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
            groups = tt.job_groups(tt.load(run).run)
            self.assertEqual(groups["i1-s1c5"], "i1-s1c5")
            self.assertEqual(groups[f1], f1)

    def test_a0_removes_only_the_copies_that_carry_the_proven_answer(self):
        # A0 removes answers by id in every part log: one part's proven answer proves nothing of another part's, so a
        # copy with another answer is kept, while the proven copy (and an equal copy) still goes
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp), parts=("i1", "i2"), twins={1: 4})
            clean = tt.prepare(tt.load(run))
            self.assertEqual((clean.classes["i1-s1c4"], clean.classes["i1-s1c1"]), ("targeted", "targeted_shared"))
            picks = {"picked": clean.broad["picked"]}
            folder = run / ".dubflow-work" / "mastering"
            c4 = next(c for c in json.loads((folder / "editorial-i1.json").read_text(encoding="utf-8"))["candidates"]
                      if c["id"] == "i1-s1c4")
            for label, change in (("same answer", {}), ("stale answer", {"text": "Câu trả lời cũ khác hẳn"}),
                                  ("other detail", {"detail": "AI test-provider/other-model"}), ("other confidence", {"confidence": 600})):
                with self.subTest(label):
                    other = json.loads(json.dumps(c4))
                    next(i for i in other["evidence"]["items"] if i["origin"] == "vision").update(change)
                    path = folder / "editorial-i2.json"
                    log = json.loads(path.read_text(encoding="utf-8"))
                    log["candidates"] = [c for c in log["candidates"] if c["id"] != "i1-s1c4"] + [other]
                    path.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
                    loaded = tt.load(run)
                    classes, report = tt.classify(loaded, picks)
                    self.assertEqual(report["classification_basis"], "exact")
                    # proven on its first copy (i1); its duplicate shares that proven answer
                    self.assertEqual((classes["i1-s1c4"], classes["i1-s1c1"]), ("targeted", "targeted_shared"))
                    answers = {i: tt.answer_identity(c) for i, c in tt.first_answers(loaded.run).items()}
                    a0 = tt.a0_evidence(classes, answers)
                    has = {part: any(i.origin == "vision" for i in a0(part, loaded.run.logs[part])["i1-s1c4"].items)
                           for part in ("i1", "i2")}
                    self.assertEqual(has, {"i1": False, "i2": bool(change)})
                    self.assertFalse(any(i.origin == "vision" for i in a0("i1", loaded.run.logs["i1"])["i1-s1c1"].items))
                    prep = tt.prepare(loaded)  # the replay passes the proven answers and the own-record proof
                    if prep.A0 is not None:
                        kept = {c["id"]: bool(tt.vision_items(c)) for c in prep.A0.logs["i2"]["candidates"]}
                        # another model in the detail is still proven by the copy's own record (a cache re-ask)
                        self.assertEqual(kept["i1-s1c4"], bool(change) and "detail" not in change)

    def test_a_logged_answer_naming_another_provider_is_not_proven_by_the_key(self):
        # the provider is part of the cache key; the model part of the detail may differ after a fallback
        for detail, expected in (("AI other-provider/test-model", "detail_mismatch"), (f"AI {PROVIDER}-b/{MODEL}", "detail_mismatch"),
                                 (f"AI x/{PROVIDER}", "detail_mismatch"), (f"AI {PROVIDER}/fallback-model", "exact")):
            with self.subTest(detail), TemporaryDirectory() as tmp:
                run = make_run_k(Path(tmp))
                self.rewrite_logged(run, "i1-s1c2", detail=detail)
                loaded = tt.load(run)
                c2 = next(c for c in loaded.run.logs["i1"]["candidates"] if c["id"] == "i1-s1c2")
                self.assertEqual(tt.own_answer(loaded, c2), expected)
                prep = tt.prepare(loaded)
                self.assertEqual(prep.classification["classification_basis"], "exact")
                self.assertEqual(prep.classes["i1-s1c2"], "unproven" if expected != "exact" else "targeted")
                kept = {c["id"]: bool(tt.vision_items(c)) for c in prep.A0.logs["i1"]["candidates"]}
                self.assertEqual(kept["i1-s1c2"], expected != "exact")

    def test_no_record_and_no_proven_look_to_share_from_is_unproven(self):
        for label in ("record deleted", "keyframe missing"):
            with self.subTest(label), TemporaryDirectory() as tmp:
                if label == "record deleted":
                    run = make_run_k(Path(tmp))
                    self.rewrite_record(run, "i1-s1c4", lambda r: None).unlink()
                else:
                    run = make_run_k(Path(tmp), missing_jpegs=(4,))
                prep = tt.prepare(tt.load(run))
                self.assertEqual(prep.classification["classification_basis"], "exact")
                self.assertEqual(prep.classes["i1-s1c4"], "unproven")
                self.assertNotIn("targeted_shared", prep.classification["counts"])
                kept = {c["id"]: bool(tt.vision_items(c)) for c in prep.A0.logs["i1"]["candidates"]}
                self.assertTrue(kept["i1-s1c4"])

    def test_a0_clears_what_the_removed_answers_set(self):
        evidence = {"candidate_id": "x", "items": [{"origin": "vision", "confidence": 850, "text": "Xe tăng",
                                                     "labels": ["explanation"], "detail": DETAIL}],
                    "product_box": {"x": 100, "y": 100, "w": 300, "h": 300}, "hook": 900, "product_visible": True,
                    "same_product": "g1"}
        log = {"candidates": [{"id": "x", "evidence": evidence}, {"id": "y", "evidence": {**evidence, "candidate_id": "y"}}]}
        out = tt.a0_evidence({"x": "targeted", "y": "broad"})("i1", log)
        self.assertEqual((out["x"].items, out["x"].product_box, out["x"].hook, out["x"].product_visible, out["x"].same_product),
                         ([], None, 0, None, None))
        self.assertEqual((len(out["y"].items), out["y"].hook, out["y"].product_visible, out["y"].same_product), (1, 900, True, "g1"))

    def test_cache_records_without_made_by_still_give_the_exact_basis(self):
        # rc12-rc15 were made before #808: their cache records carry no made_by
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp), cache=True)
            for path in sorted((run / ".dubflow-work" / "mastering" / "vision").glob("*.json")):
                record = json.loads(path.read_text(encoding="utf-8"))
                record.pop("made_by")
                path.write_text(json.dumps(record), encoding="utf-8")
            prep = tt.prepare(tt.load(run))
            self.assertEqual(prep.classification["classification_basis"], "exact")
            self.assertEqual((prep.classes["i1-s1c2"], prep.classes["i1-s1c4"]), ("targeted", "targeted"))
            self.assertEqual(prep.classification["targeted_frames_item_parts"], 2)
            self.assertEqual(prep.broad["status"], "RECONSTRUCTED")

    def test_cache_keys_that_match_nothing_leave_every_non_broad_answer_unproven(self):
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp), cache=True)
            for path in sorted((run / ".dubflow-work" / "mastering" / "vision").glob("*.json")):
                record = json.loads(path.read_text(encoding="utf-8"))
                record["key"], record["made_by"]["model"] = "0" * 64, "another-model"
                path.write_text(json.dumps(record), encoding="utf-8")
            loaded = tt.load(run)
            prep = tt.prepare(loaded)
            self.assertEqual(prep.classification["classification_basis"], "group (cache keys unmatched)")
            self.assertEqual(tt.preflight(loaded)["classification_basis"], "group (cache keys unmatched)")
            self.assertEqual((prep.classes["i1-s1c2"], prep.classes["i1-s1c4"]), ("unproven", "unproven"))
            kept = {c["id"]: bool(tt.vision_items(c)) for c in prep.A0.logs["i1"]["candidates"]}
            self.assertTrue(kept["i1-s1c2"] and kept["i1-s1c4"])
            self.assertEqual(prep.broad["status"], "RECONSTRUCTED")

    def test_a_broad_count_other_than_the_logged_one_is_not_reconstructed(self):
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp))
            program = run / ".dubflow-work" / "program.json"
            data = json.loads(program.read_text(encoding="utf-8"))
            data["vision"]["broad"] = 4
            program.write_text(json.dumps(data), encoding="utf-8")
            prep = tt.prepare(tt.load(run))
            self.assertEqual((prep.broad["status"], prep.broad["reasons"]), ("NOT_RECONSTRUCTED", ["broad count mismatch"]))
            self.assertIsNone(prep.A0)

    def test_b0_spends_what_the_run_paid_for(self):
        with TemporaryDirectory() as tmp:
            prep = tt.prepare(tt.load(make_run_k(Path(tmp))))
            self.assertEqual(prep.respend, {"budget": 2, "basis": "log_vision_reports", "targeted_paid": 2, "multi_frame_paid": 0,
                                            "targeted_frames_item_parts": 2, "label": None})
            for log in prep.loaded.run.logs.values():  # three multi-frame looks paid, one more targeted look logged
                log["vision"] = {**log["vision"], "multi_frame": 3, "targeted": 3}
            matched, basis, _ = tt.matched_budget(prep.loaded, prep.classification)
            found = tt.respend_budget(prep.loaded, prep.classification, matched, basis)
            self.assertEqual((found["budget"], found["targeted_paid"], found["multi_frame_paid"], found["label"]),
                             (6, 3, 3, "budget basis differs from classification"))
            prep.respend = found
            b0 = [a for a in tt.arm_grid(prep, budgets="matched", windows="fwd", full=True) if a.kind == "respend"]
            self.assertTrue(b0)
            self.assertEqual({a.budget for a in b0}, {6})
        for arm in arms_of(STUDY["report"]).values():
            if arm["kind"] == "respend":
                self.assertEqual(arm["respend_budget"]["budget"], arm["budget"])

    def test_the_matched_budget_leaves_the_intro_out(self):
        with TemporaryDirectory() as tmp:
            prep = tt.prepare(tt.load(make_run_k(Path(tmp), parts=("intro", "i1"), intro={8: "Xe tải chở lính"})))
            self.assertEqual(prep.targeted_per_part, {"intro": 1, "i1": 2})
            self.assertEqual((prep.matched, prep.matched_basis), (2, "log_vision_reports"))


class BudgetTests(unittest.TestCase):
    def test_fair_turns_over_three_parts(self):
        with TemporaryDirectory() as tmp:
            prep = tt.prepare(tt.load(make_run_k(Path(tmp), parts=("i1", "i2", "i3"))))
            for strategy in ("uniform", "production", "prior", "shuffled_prior", "anchor", "gap", "mixed"):
                found = tg.select(prep.view_A, tg.SelectParams(strategy, 4, None))
                per = [sum(1 for s in found if s.part == p) for p in ("i1", "i2", "i3")]
                self.assertEqual(sum(per), 4, strategy)
                self.assertFalse(min(per) == 0 and max(per) >= 2, (strategy, per))

    def test_every_arm_spends_its_budget_or_its_eligible_shots(self):
        for arm in arms_of(STUDY["report"]).values():
            if "e2" not in arm:
                continue
            e2, e4 = arm["e2"], arm["e4"]
            if arm["budget"] is not None:
                self.assertEqual(e2["selected"], min(arm["budget"], e2["eligible"]), arm["id"])
            self.assertEqual(e4["frames_job"], sum(e4["frames_per_part"].values()))
            self.assertEqual(e4["frames_job"], sum(e4["frames_per_source"].values()))
            paid: dict[str, int] = {}
            for r in arm["samples"]:
                if not r["own_cache"]:
                    paid[r["sample"]["part"]] = paid.get(r["sample"]["part"], 0) + 1
            self.assertEqual(e4["calls_est"], sum(-(-n // vision.BATCH) for n in paid.values()))
            self.assertIsNone(e4["cost"])
            self.assertEqual(e4["cost_basis"], "not given")

    def test_a_given_price_is_the_only_price(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            quiet(tt.main, all_args(root, STUDY["run"], STUDY["donor"], STUDY["gt"], STUDY["lock"], "priced",
                                    "--cost-per-call", "0.5", "--cost-unit", "unit"))
            report = json.loads((root / "priced" / "targeted-run-a.json").read_text(encoding="utf-8"))
        for arm in arms_of(report).values():
            if "e4" in arm:
                self.assertEqual(arm["e4"]["cost"], arm["e4"]["calls_est"] * 0.5)
        for node in ast.walk(ast.parse((HERE / "targeted.py").read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, (int, float)) and not isinstance(node.value.value, bool):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    name = getattr(target, "id", getattr(target, "attr", "")).lower()
                    self.assertFalse("cost" in name or "price" in name, name)


class CeilingAndRegimeTests(unittest.TestCase):
    def test_the_ceiling_and_the_closed_world(self):
        report = STUDY["report"]
        arms = arms_of(report)
        ceiling = arms["ceiling:ALL_DONOR_ITEMS:-:all"]
        reachable = report["runs"]["run-a"]["e1"]["donor_supply_reachable"]
        self.assertEqual(sum(ceiling["e3"]["simulated"].values()), reachable)
        self.assertTrue(ceiling["e3"]["evidence_limited"])
        with TemporaryDirectory() as tmp:
            target, donor, _ = prepared(Path(tmp))
            index = tt.DonorIndex(target, [donor])
            covered = {"A": index.covered(target.view_A), "A0": index.for_base("A0").covered(target.view_A0)}
        for arm in arms.values():
            if arm["regime"] == "closed_world" and "selection" in arm:
                base = "A0" if "B0" in arm["id"] else "A"
                self.assertLessEqual({s["candidate_id"] for s in arm["selection"]}, covered[base], arm["id"])
                self.assertEqual(arm["eligible_restriction"], "closed_world_donor_covered")
        self.assertEqual(arms["open:A+:-:+2x"]["delta_vs_A"], tt.NOT_INTERPRETABLE)


class StrategyTests(unittest.TestCase):
    """A part whose flagged sentences have 23 eligible shots in their forward window, so the strategies differ.

    30 shots of 4 s (keyframes at 2 s + 4 s·n); u1 (0-20 s) chose c2 and u4 (90-120 s) chose c27, so the forward
    window of the flagged u2 (20-50 s) and u3 (50-90 s) is 7880-104120 ms. c21 (keyframe 82 s) was looked at and its
    answer says u2's words; c13 has an OCR transcript with some of them."""

    WORDS = "Một chiếc xe tăng lăn bánh qua cánh đồng"

    def view(self) -> tg.PlanningView:
        from types import MappingProxyType
        quality = shots.Quality(120, 50, 300)
        listed, evidence = [], {}
        for n in range(30):
            cid = f"s1c{n + 1}"
            c = shots.Candidate(cid, "s1", n * 4000 + 120, n * 4000 + 3880, n, 30, quality, n, f"{cid}.jpg")
            evidence[cid] = ev.Evidence(cid)
            listed.append(tg.ShotView(cid, "s1", "key", c.start_ms, c.end_ms, n * 4000 + 2000, "numbered_window", True, True, cid,
                                      n + 1 == 21, c, (c.start_ms, c.end_ms)))
        evidence["s1c21"].add(ev.Item("vision", 850, self.WORDS, ("action",), DETAIL))
        evidence["s1c13"].add(ev.Item("transcript", 700, "xe tăng lăn bánh", (), "ocr"))

        def unit(uid, text, start, end, chosen, flagged):
            return tg.UnitView(uid, text, "narration", start, end, chosen, (), "none" if flagged else "full", flagged, 0)
        units = (unit("i1.u1", "Mở đầu bản tin hôm nay", 0, 20_000, ("s1c2",), False),
                 unit("i1.u2", self.WORDS, 20_000, 50_000, (), True),
                 unit("i1.u3", "Đoàn binh sĩ đội mũ sắt hành quân", 50_000, 90_000, (), True),
                 unit("i1.u4", "Kết thúc bản tin", 90_000, 120_000, ("s1c27",), False))
        part = tg.PartView("i1", "item", 120_000, (("s1", 0, 120_000),), (("s1", ()),), (), units, tuple(listed),
                           MappingProxyType(evidence))
        return tg.PlanningView("run-s", "test", (part,), (), "open")

    def test_forward_bounds_come_from_the_neighbours_choices(self):
        part = self.view().parts[0]
        self.assertEqual(tg.forward_bounds(part, part.units[1]), {"s1": (7880, 104120)})  # after c2, before c27
        self.assertEqual(tg.forward_bounds(part, part.units[0]), {"s1": (0, 104120)})
        self.assertEqual(tg.forward_bounds(part, part.units[3]), {"s1": (7880, 120_000)})

    def test_each_strategy_picks_its_own_shot(self):
        view = self.view()
        part = view.parts[0]
        u2 = part.units[1]
        self.assertEqual(tg.prior_centres(part, u2), {"s1": 35_000})
        self.assertEqual(tg.anchor_centres(part, u2, tg._scorer(view, part), tg.forward_bounds(part, u2)), {"s1": 82_000})
        picked = {}
        for strategy in ("prior", "shuffled_prior", "anchor", "gap", "mixed"):
            found = tg.select(view, tg.SelectParams(strategy, 1, 5000))
            self.assertEqual(len(found), 1, strategy)
            picked[strategy] = (found[0].candidate_id, found[0].unit_id)
        self.assertEqual(picked, {"prior": ("s1c9", "i1.u2"),            # keyframe 34 s, nearest the prior centre 35 s
                                  "shuffled_prior": ("s1c18", "i1.u2"),  # u3's centre 70 s
                                  "anchor": ("s1c20", "i1.u2"),          # beside c21, the looked-at shot that matches
                                  "gap": ("s1c14", "i1.u2"),             # the middle of the window's fresh shots
                                  "mixed": ("s1c13", "i1.u2")})          # the words first
        self.assertGreaterEqual(len({c for c, _ in picked.values()}), 3)

    def test_the_window_leaves_out_shots_farther_than_w(self):
        view = self.view()
        near = {s.candidate_id for s in tg.select(view, tg.SelectParams("prior", 100, 5000))}
        self.assertEqual(near, {"s1c8", "s1c9", "s1c10", "s1c17", "s1c18", "s1c19"})  # within 5 s of 35 s and of 70 s
        every = {s.candidate_id for s in tg.select(view, tg.SelectParams("prior", 100, None))}
        self.assertEqual(len(every), 23)
        self.assertIn("s1c11", every - near)  # keyframe 42 s: 7 s from the centre
        self.assertNotIn("s1c21", every)      # looked at already


class ShuffleTests(unittest.TestCase):
    def test_the_shuffled_prior_is_a_fixed_derangement(self):
        with TemporaryDirectory() as tmp:
            prep = tt.prepare(tt.load(make_run_k(Path(tmp))))
            part = next(p for p in prep.view_A0.parts if p.part == "i1")
            units = tg.wanted_units(part, "flagged")
            self.assertGreaterEqual(len(units), 2)
            own = {u.unit_id: tg.prior_centres(part, u) for u in units}
            moved = tg.shuffled_centres(part, units)
            self.assertEqual(moved, tg.shuffled_centres(part, units))
            self.assertEqual(sorted(map(str, moved.values())), sorted(map(str, own.values())))
            for u in units:
                self.assertNotEqual(moved[u.unit_id], own[u.unit_id])
            self.assertEqual(tg.shuffled_centres(part, units[:1]), {units[0].unit_id: own[units[0].unit_id]})


class MemoTests(unittest.TestCase):
    def test_the_memo_changes_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            quiet(tt.main, all_args(root, STUDY["run"], STUDY["donor"], STUDY["gt"], STUDY["lock"], "nomemo", "--no-memo"))
            self.assertEqual((root / "nomemo" / "targeted-run-a.json").read_text(encoding="utf-8"), STUDY["json"])

    def test_an_arm_again_after_another_is_the_same(self):
        with TemporaryDirectory() as tmp:
            target, donor, gt = prepared(Path(tmp))
            memo = tt.PlanMemo()
            index = tt.DonorIndex(target, [donor])
            ctx = tt.Context(gt, target, index, memo, False, 500, True, "flagged", None, None)
            views = tt.Views(target, index)
            x = tt.Arm("open:mixed:W=fwd:+matched", "open", "targeted", "mixed", "fwd", "+matched", 2, "A")
            y = tt.Arm("open:A+:-:+2x", "open", "uniform", "uniform", "-", "+2x", 3, "A")

            def e5(arm: tt.Arm) -> str:
                view = views.get(arm.base, arm.regime)
                return tt.dumps(tt.evaluate_arm(ctx, arm, tg.select_detail(view, arm.params("flagged")), view)["e5"])
            first = e5(x)
            e5(y)
            self.assertEqual(e5(x), first)

    def test_the_memo_hands_out_copies_and_refuses_unknown_types(self):
        memo = tt.PlanMemo()
        planned = memo.wrap(lambda call: ({"plan": 1}, {"candidates": [1]}))
        _, log = planned({"part": "i1"})
        log["candidates"].append(2)
        _, again = planned({"part": "i1"})
        self.assertEqual(again, {"candidates": [1]})
        self.assertIsNot(again, log)
        self.assertEqual((memo.hits, memo.misses), (1, 1))
        with self.assertRaises(TypeError):
            tt.call_key({"part": object()})


class PatchTests(unittest.TestCase):
    def assert_original(self):
        self.assertEqual((w3.call_of.__module__, w3.call_of.__qualname__), (w3.__name__, "call_of"))
        self.assertEqual((w3.plan_again.__module__, w3.plan_again.__qualname__), (w3.__name__, "plan_again"))
        self.assertIs(w3.compiler.plan_part, planner.plan_part)

    def test_nothing_is_patched_outside_a_replay(self):
        self.assert_original()  # after import
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            def refuse(*args, **kwargs):
                raise AssertionError("no network and no process on this path")
            # the pre-registration ancestry check is the one sanctioned git call; it is stubbed so that any other process fails
            with mock.patch.object(socket, "socket", refuse), mock.patch.object(subprocess, "Popen", refuse), \
                    mock.patch.object(subprocess, "run", refuse), mock.patch.object(tt, "git_is_ancestor", lambda commit: None):
                code = quiet(tt.main, all_args(root, STUDY["run"], STUDY["donor"], STUDY["gt"], STUDY["lock"], "offline"))
            self.assertEqual(code, 0)
        self.assert_original()
        with tt.replay_context(memo=tt.PlanMemo()):
            self.assertNotEqual(w3.call_of.__qualname__, "call_of")
        self.assert_original()


class RunFileSafetyTests(unittest.TestCase):
    """Every file the harness reads stays inside the selected run folder: no traversal from a logged keyframe name, no
    symbolic link or reparse point, only regular files, and a size cap (``tt.load`` fails instead of reading)."""

    def run_with(self, tmp: str) -> tuple[Path, Path]:
        run = make_run_k(Path(tmp) / "runs")
        outside = Path(tmp) / "outside.jpg"
        outside.write_bytes(b"\xff\xd8 private bytes outside the run \xff\xd9")
        return run, outside

    @staticmethod
    def set_keyframe(run: Path, candidate: str, name: str) -> None:
        path = run / ".dubflow-work" / "mastering" / "editorial-i1.json"
        log = json.loads(path.read_text(encoding="utf-8"))
        for c in log["candidates"]:
            if c["id"] == candidate:
                c["keyframe"] = name
        path.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")

    def link(self, target: Path, at: Path) -> None:
        try:
            at.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are not available here")

    def test_a_clean_run_loads(self):
        with TemporaryDirectory() as tmp:
            run, _ = self.run_with(tmp)
            loaded = tt.load(run)
            self.assertTrue(all(loaded.jpeg.values()))
            self.assertIsNone(tt.read_run_file(run, ".dubflow-work/review-i1.json"))

    def test_a_logged_keyframe_name_cannot_leave_the_run_folder(self):
        with TemporaryDirectory() as tmp:
            run, outside = self.run_with(tmp)
            for name in ("../../../outside.jpg", str(outside), "sub/../../../../outside.jpg"):
                with self.subTest(name):
                    self.set_keyframe(run, "i1-s1c2", name)
                    with self.assertRaises(tt.UnsafeRunFile) as caught:
                        tt.load(run)
                    self.assertNotIn(str(Path(tmp)), str(caught.exception))

    def test_a_symbolic_link_anywhere_on_the_way_is_refused(self):
        with TemporaryDirectory() as tmp:
            run, outside = self.run_with(tmp)
            mdir = run / ".dubflow-work" / "mastering"
            frame = mdir / "i1-s1-c2.jpg"
            frame.unlink()
            self.link(outside, frame)
            with self.assertRaises(tt.UnsafeRunFile):
                tt.load(run)
        cases = [("editorial log", "editorial-i1.json"), ("cache record", None), ("review file", "review")]
        for label, name in cases:
            with self.subTest(label), TemporaryDirectory() as tmp:
                run, _ = self.run_with(tmp)
                mdir = run / ".dubflow-work" / "mastering"
                if name is None:
                    path = next((mdir / "vision").glob("*.json"))
                elif name == "review":
                    path = run / ".dubflow-work" / "review-i1.json"
                    path.write_text("{}", encoding="utf-8")
                else:
                    path = mdir / name
                copy = Path(tmp) / "elsewhere.json"
                copy.write_bytes(path.read_bytes())
                path.unlink()
                self.link(copy, path)
                with self.assertRaises(tt.UnsafeRunFile):
                    tt.load(run)

    def test_a_link_or_junction_below_the_run_folder_is_never_followed(self):
        # .dubflow-work, mastering or vision is already a link (POSIX) or a junction (Windows, no privilege needed) to
        # an outside folder when the load starts: the load fails, and no outside bytes are ever returned
        for which in (".dubflow-work", ".dubflow-work/mastering", ".dubflow-work/mastering/vision"):
            with self.subTest(which=which), TemporaryDirectory() as tmp:
                run, _ = self.run_with(tmp)
                copy = self.outside_run(tmp, run)
                at, kept = run / which, Path(tmp) / "kept"
                os.rename(at, kept)
                try:
                    if os.name == "nt":
                        import _winapi
                        _winapi.CreateJunction(str(copy / which), str(at))
                    else:
                        os.symlink(copy / which, at, target_is_directory=True)
                except (OSError, NotImplementedError, AttributeError):
                    self.fail("a junction (Windows) or a symbolic link (POSIX) could not be made")
                real = tt.read_run_file
                returned: list[bytes] = []

                def reading(root, relative):
                    data = real(root, relative)
                    if data is not None:
                        returned.append(data)
                    return data

                with mock.patch.object(tt, "read_run_file", reading), self.assertRaises(tt.UnsafeRunFile) as caught:
                    tt.load(run)
                self.assertIn("symbolic link or reparse point", str(caught.exception))
                self.assertFalse([d for d in returned if b"OUTSIDE-BYTES" in d])

    def test_a_load_that_fails_leaves_the_review_snapshot_of_the_last_good_load(self):
        decisions = reviewing.Review(units={"i1.u3": reviewing.UnitDecision(pin=["i1-s1c8"])}).to_dict()
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp) / "runs", review=decisions)
            loaded = tt.load(run)
            before = tt._review_bytes(loaded.run.folder, "i1")
            (run / ".dubflow-work" / "review-i1.json").write_text("{}", encoding="utf-8")
            frame = run / self.FRAME
            frame.unlink()
            self.link(Path(tmp) / "nowhere.jpg", frame)
            with self.assertRaises(tt.UnsafeRunFile):
                tt.load(run)
            self.assertEqual(tt._review_bytes(loaded.run.folder, "i1"), before)

    def test_a_linked_mastering_folder_is_refused(self):
        with TemporaryDirectory() as tmp:
            run, _ = self.run_with(tmp)
            mdir = run / ".dubflow-work" / "mastering"
            moved = Path(tmp) / "moved-mastering"
            mdir.rename(moved)
            self.link(moved, mdir)
            with self.assertRaises(tt.UnsafeRunFile):
                tt.load(run)

    def test_a_stream_name_is_refused(self):
        # frame.jpg:hidden names an NTFS alternate data stream on Windows: never a plain file inside the run folder
        with TemporaryDirectory() as tmp:
            run, _ = self.run_with(tmp)
            self.set_keyframe(run, "i1-s1c2", "i1-s1-c2.jpg:hidden")
            with self.assertRaises(tt.UnsafeRunFile):
                tt.load(run)

    def test_the_run_is_parsed_from_the_checked_bytes_and_never_reopened(self):
        # every input is read once through read_run_file (w3.load_run is never called), and nothing reads the run
        # folder by path afterwards: swapping the files after load changes no replay, with or without review files
        decisions = reviewing.Review(units={"i1.u3": reviewing.UnitDecision(pin=["i1-s1c8"])}).to_dict()
        other = reviewing.Review(units={"i1.u3": reviewing.UnitDecision(pin=["i1-s1c5"])}).to_dict()
        with TemporaryDirectory() as tmp:
            run = make_run_k(Path(tmp) / "runs", review=decisions)
            calls: dict[str, int] = {}
            real = tt.read_run_file

            def counted(root, relative):
                key = PurePath(relative).as_posix()
                calls[key] = calls.get(key, 0) + 1
                return real(root, relative)

            with mock.patch.object(tt, "read_run_file", counted), \
                    mock.patch.object(w3, "load_run", side_effect=AssertionError("path-based reopen")):
                loaded = tt.load(run)
            self.assertLessEqual(set(loaded.files), set(calls))  # every digested file came from read_run_file
            self.assertEqual(max(calls.values()), 1)
            self.assertIn(".dubflow-work/review-i1.json", loaded.files)
            baseline = {review: tt.prepare(tt.load(run), review=review) for review in (False, True)}
            swapped = {review: tt.load(run) for review in (False, True)}
            work = run / ".dubflow-work"
            outside = Path(tmp) / "other-review.json"
            outside.write_text(json.dumps(other), encoding="utf-8")
            (work / "review-i1.json").unlink()
            try:
                (work / "review-i1.json").symlink_to(outside)
            except (OSError, NotImplementedError):
                (work / "review-i1.json").write_text(json.dumps(other), encoding="utf-8")
            for path in (work / "mastering").glob("editorial-*.json"):
                path.write_text("not json", encoding="utf-8")
            (work / "spec.json").write_text("not json", encoding="utf-8")
            for review in (False, True):
                with self.subTest(review=review):
                    prep = tt.prepare(swapped[review], review=review)
                    self.assertEqual(tt.dumps(prep.A.plans), tt.dumps(baseline[review].A.plans))
                    self.assertEqual(tt.dumps(prep.A.logs), tt.dumps(baseline[review].A.logs))
            self.assertNotEqual(tt.dumps(baseline[False].A.plans), tt.dumps(baseline[True].A.plans))

    def test_a_replay_takes_the_review_file_from_the_snapshot_never_the_live_path(self):
        # w3.call_of looks the review file up by path: a review file in the snapshot still counts when the live file
        # is removed after the load, a review file that appears only after the load never counts, and the replay
        # makes no live lookup of either (Path.is_file is not called while the parts are re-planned)
        decisions = reviewing.Review(units={"i1.u3": reviewing.UnitDecision(pin=["i1-s1c8"])}).to_dict()
        for had_review in (True, False):
            with self.subTest(had_review=had_review), TemporaryDirectory() as tmp:
                run = make_run_k(Path(tmp) / "runs", review=decisions if had_review else None)
                live = run / ".dubflow-work" / "review-i1.json"
                baseline = {review: tt.prepare(tt.load(run), review=review) for review in (False, True)}
                loaded = {review: tt.load(run) for review in (False, True)}
                self.assertEqual(".dubflow-work/review-i1.json" in loaded[True].files, had_review)
                if had_review:
                    live.unlink()
                else:
                    live.write_text(json.dumps(decisions), encoding="utf-8")
                for review in (False, True):
                    with mock.patch.object(Path, "is_file", side_effect=AssertionError("live path lookup")):
                        prep = tt.prepare(loaded[review], review=review)
                    self.assertEqual(tt.dumps(prep.A.plans), tt.dumps(baseline[review].A.plans))
                    self.assertEqual(tt.dumps(prep.A.logs), tt.dumps(baseline[review].A.logs))
                chosen = {u["unit_id"]: u["chosen"] for u in baseline[True].A.logs["i1"]["units"]}["i1.u3"]
                self.assertEqual(chosen == ["i1-s1c8"], had_review)  # the review is real when the snapshot has it

    def test_a_later_load_with_other_review_files_fails_and_leaves_the_earlier_snapshot(self):
        # review bytes are kept by run folder for the replay: a second load of the folder that read other review
        # files (written, changed or removed after the first load) fails, and the first snapshot replays as before
        decisions = reviewing.Review(units={"i1.u3": reviewing.UnitDecision(pin=["i1-s1c8"])}).to_dict()
        for had_review, change in ((False, "written"), (True, "changed"), (True, "removed")):
            with self.subTest(change=change), TemporaryDirectory() as tmp:
                run = make_run_k(Path(tmp) / "runs", review=decisions if had_review else None)
                live = run / ".dubflow-work" / "review-i1.json"
                first = tt.load(run)
                before = tt.prepare(first, review=True)
                self.assertEqual(tt.dumps(tt.load(run).files), tt.dumps(first.files))  # an unchanged reload is fine
                if change == "removed":
                    live.unlink()
                else:
                    other = reviewing.Review(units={"i1.u3": reviewing.UnitDecision(pin=["i1-s1c5"])}).to_dict()
                    live.write_text(json.dumps(other), encoding="utf-8")
                with self.assertRaises(tt.UnsafeRunFile) as caught:
                    tt.load(run)
                self.assertIn("review files changed", str(caught.exception))
                again = tt.prepare(first, review=True)
                self.assertEqual(tt.dumps(again.A.plans), tt.dumps(before.A.plans))
                self.assertEqual(tt.dumps(again.A.logs), tt.dumps(before.A.logs))

    def test_a_nested_replay_context_replays_with_the_inner_review_choice(self):
        decisions = reviewing.Review(units={"i1.u3": reviewing.UnitDecision(pin=["i1-s1c8"])}).to_dict()
        with TemporaryDirectory() as tmp:
            loaded = tt.load(make_run_k(Path(tmp) / "runs", review=decisions))
            run = dataclasses.replace(loaded.run, spec={k: v for k, v in loaded.run.spec.items() if k != "editorial"})
            for outer in (False, True):
                for inner in (False, True):
                    with self.subTest(outer=outer, inner=inner):
                        with tt.replay_context(review=inner):
                            alone = w3.call_of(run, "i1", [])["edits"]
                        with tt.replay_context(review=outer), tt.replay_context(review=inner):
                            nested = w3.call_of(run, "i1", [])["edits"]
                        self.assertEqual(nested.to_dict(), alone.to_dict())

    def test_a_hard_link_and_an_unusable_name_are_refused_without_naming_the_machine(self):
        with TemporaryDirectory() as tmp:
            run, outside = self.run_with(tmp)
            frame = run / ".dubflow-work" / "mastering" / "i1-s1-c2.jpg"
            frame.unlink()
            try:
                os.link(outside, frame)
            except (OSError, NotImplementedError):
                self.skipTest("hard links are not available here")
            with self.assertRaises(tt.UnsafeRunFile):
                tt.load(run)
        for name in ("editorial-i1.json/x.jpg", "x" * 300 + ".jpg", "nul\x00.jpg"):
            with self.subTest(name=name[:20]), TemporaryDirectory() as tmp:
                run, _ = self.run_with(tmp)
                self.set_keyframe(run, "i1-s1c2", name)
                with self.assertRaises(tt.UnsafeRunFile) as caught:
                    tt.load(run)
                self.assertNotIn(str(Path(tmp)), str(caught.exception))

    FRAME = ".dubflow-work/mastering/i1-s1-c2.jpg"

    @staticmethod
    def outside_run(tmp: str, run: Path) -> Path:
        # a copy of the run elsewhere whose files say they are outside: the same names, other bytes
        copy = Path(tmp) / "outside-run"
        shutil.copytree(run, copy)
        (copy / RunFileSafetyTests.FRAME).write_bytes(b"OUTSIDE-BYTES")
        (copy / ".dubflow-work" / "mastering" / "outside-only.json").write_text("{}", encoding="utf-8")
        log = copy / ".dubflow-work" / "mastering" / "editorial-i1.json"
        data = json.loads(log.read_text(encoding="utf-8"))
        data["outside"] = "OUTSIDE-BYTES"
        log.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return copy

    @staticmethod
    def kinds() -> list[str]:
        import importlib.util
        found = ["symbolic link", "another folder"]
        if os.name == "nt" and importlib.util.find_spec("_winapi") is not None:
            import _winapi
            if hasattr(_winapi, "CreateJunction"):
                found.append("junction")
        return found

    def assert_kinds_ran(self, ran: set[str]) -> None:
        # a kind that cannot be made here is passed over (never skipping the kinds after it); the folder swap always
        # runs, and on Windows the junction (no privilege needed) must have run too
        self.assertIn("another folder", ran)
        if os.name == "nt" and "junction" in self.kinds():
            self.assertIn("junction", ran)

    @staticmethod
    def replace(at: Path, target: Path, kind: str, kept: Path) -> None:
        """``at`` renamed to ``kept`` and its name given to ``target`` (a link or junction to it, or ``target``
        itself moved there). Raises OSError when the rename is refused (Windows: a held folder)."""
        os.rename(at, kept)
        try:
            if kind == "symbolic link":
                os.symlink(target, at, target_is_directory=True)
            elif kind == "junction":
                import _winapi
                _winapi.CreateJunction(str(target), str(at))
            else:
                os.rename(target, at)
        except BaseException:
            os.rename(kept, at)
            raise

    @staticmethod
    def put_back(at: Path, target: Path, kind: str, kept: Path) -> None:
        if kind == "another folder":
            os.rename(at, target)
        elif os.name == "nt":
            os.rmdir(at)  # a directory link or junction, never its target
        else:
            os.unlink(at)
        os.rename(kept, at)

    def test_a_run_folder_replaced_after_it_was_pinned_is_never_followed(self):
        # the run folder is pinned (load pins it), then renamed and its old name given to a link, a junction or
        # another folder holding the same names: every later read or listing fails, none returns outside bytes
        ran: set[str] = set()
        for kind in self.kinds():
            with self.subTest(kind=kind), TemporaryDirectory() as tmp:
                run, _ = self.run_with(tmp)
                inside = (run / self.FRAME).read_bytes()
                copy = self.outside_run(tmp, run)
                tt.load(run)
                try:
                    self.replace(run, copy, kind, Path(tmp) / "kept-run")
                except (OSError, NotImplementedError):
                    continue  # this kind cannot be made here
                ran.add(kind)
                self.assertEqual((run / self.FRAME).read_bytes(), b"OUTSIDE-BYTES")  # the name now leads out
                for call in (lambda: tt.read_run_file(run, self.FRAME),
                             lambda: tt.list_run_dir(run, ".dubflow-work/mastering"),
                             lambda: tt.read_run_file(run, ".dubflow-work/mastering/editorial-i1.json")):
                    with self.assertRaises(tt.UnsafeRunFile) as caught:
                        call()
                    self.assertNotIn(str(Path(tmp)), str(caught.exception))
                self.put_back(run, copy, kind, Path(tmp) / "kept-run")
                self.assertEqual(tt.read_run_file(run, self.FRAME), inside)  # the pinned folder again
        self.assert_kinds_ran(ran)

    def test_preflight_reads_the_spec_from_the_loaded_snapshot_never_the_live_path(self):
        # preflight reports what load() read: a run loaded without a spec stays MISSING_SPEC when its folder or its
        # .dubflow-work is then replaced by a link, a junction or another folder holding a spec, and a run loaded with
        # a spec keeps it when the live spec is then removed or its folder replaced by one without a spec
        ran: set[str] = set()
        for kind in self.kinds():
            for where in (".", ".dubflow-work"):
                for had_spec in (False, True):
                    with self.subTest(kind=kind, where=where, had_spec=had_spec), TemporaryDirectory() as tmp:
                        run, _ = self.run_with(tmp)
                        copy = self.outside_run(tmp, run)
                        spec, outside_spec = run / ".dubflow-work" / "spec.json", copy / ".dubflow-work" / "spec.json"
                        (outside_spec if had_spec else spec).unlink()  # the outside tree says the opposite
                        loaded = tt.load(run)
                        self.assertEqual(".dubflow-work/spec.json" in loaded.files, had_spec)
                        before = tt.preflight(loaded)
                        self.assertEqual(("MISSING_SPEC" in before["failures"], before["spec"]), (not had_spec, had_spec))
                        at, target = (run, copy) if where == "." else (run / where, copy / where)
                        try:
                            self.replace(at, target, kind, Path(tmp) / "kept")
                        except (OSError, NotImplementedError):
                            continue  # this kind cannot be made here
                        ran.add(kind)
                        self.assertEqual(spec.is_file(), not had_spec)  # the live path now says the opposite
                        self.assertEqual(tt.preflight(loaded), before)
                        self.put_back(at, target, kind, Path(tmp) / "kept")
                        if had_spec:
                            spec.unlink()  # removed after a valid load: the snapshot still has it
                            self.assertEqual(tt.preflight(loaded), before)
        self.assert_kinds_ran(ran)

    def test_a_run_folder_replaced_during_a_load_fails_the_load(self):
        # the swap is tried between two reads of one load: the load fails and no read returned outside bytes. On
        # Windows the load holds the run folder open, so the rename is refused and the load reads the pinned folder.
        for kind in self.kinds():
            with self.subTest(kind=kind), TemporaryDirectory() as tmp:
                run, _ = self.run_with(tmp)
                copy = self.outside_run(tmp, run)
                real = tt.read_run_file
                returned: list[bytes] = []
                state = {"calls": 0, "made": False, "refused": False}

                def swap_midway(root, relative):
                    state["calls"] += 1
                    if state["calls"] == 3:
                        try:
                            self.replace(run, copy, kind, Path(tmp) / "kept-run")
                            state["made"] = True
                        except (OSError, NotImplementedError):
                            state["refused"] = True
                    data = real(root, relative)
                    if data is not None:
                        returned.append(data)
                    return data

                with mock.patch.object(tt, "read_run_file", swap_midway):
                    try:
                        loaded = tt.load(run)
                    except tt.UnsafeRunFile:
                        loaded = None
                self.assertGreaterEqual(state["calls"], 3)
                self.assertFalse([d for d in returned if b"OUTSIDE-BYTES" in d])
                if state["made"]:
                    self.assertIsNone(loaded)
                else:
                    self.assertTrue(state["refused"])
                    if os.name == "nt":
                        self.assertIsNotNone(loaded)  # Windows: the held run folder could not be renamed

    def test_a_folder_swapped_around_the_open_never_gives_outside_bytes(self):
        # right before the file is opened, the run folder or the mastering folder becomes a link, junction or another
        # folder holding the same names; it is put back right after (the hardest case) or left. The file is opened
        # from the pinned folders' handles, so the read returns the run's own bytes or fails, never outside ones
        for which in ("run folder", "mastering folder"):
            for kind in self.kinds():
                for back in (True, False):
                    with self.subTest(which=which, kind=kind, back=back), TemporaryDirectory() as tmp:
                        run, _ = self.run_with(tmp)
                        inside = (run / self.FRAME).read_bytes()
                        copy = self.outside_run(tmp, run)
                        at = run if which == "run folder" else run / ".dubflow-work" / "mastering"
                        target = copy if which == "run folder" else copy / ".dubflow-work" / "mastering"
                        kept = Path(tmp) / "kept"
                        tt.pin_root(run)
                        real = tt._open_child
                        state = {"swapped": False, "refused": False}

                        def swapping(folder_fd, part, *, folder, shown):
                            if not state["swapped"] and part == "i1-s1-c2.jpg":
                                state["swapped"] = True
                                try:
                                    self.replace(at, target, kind, kept)
                                except (OSError, NotImplementedError):
                                    state["refused"] = True
                                try:
                                    return real(folder_fd, part, folder=folder, shown=shown)
                                finally:
                                    if back and not state["refused"]:
                                        self.put_back(at, target, kind, kept)
                            return real(folder_fd, part, folder=folder, shown=shown)

                        with mock.patch.object(tt, "_open_child", swapping):
                            try:
                                data = tt.read_run_file(run, self.FRAME)
                            except tt.UnsafeRunFile:
                                data = None
                        self.assertTrue(state["swapped"])
                        self.assertIn(data, (None, inside))
                        if not state["refused"] and (back or which == "mastering folder"):
                            self.assertEqual(data, inside)  # opened from the pinned folders' handles
                        if not back and not state["refused"] and which == "run folder":
                            self.assertIsNone(data)  # the run folder no longer the pinned one: fails after

    def test_the_opened_run_folder_must_be_the_pinned_one(self):
        # the run folder's name is given to a link, junction or another folder just for the open of the run folder
        # itself and put back right after: the handle is not the pinned folder, so nothing below it is read
        ran: set[str] = set()
        for kind in self.kinds():
            with self.subTest(kind=kind), TemporaryDirectory() as tmp:
                run, _ = self.run_with(tmp)
                copy = self.outside_run(tmp, run)
                tt.pin_root(run)
                kept = Path(tmp) / "kept"
                real = tt._open_root_fd
                state = {"swapped": False}

                def swapping(pinned):
                    if not state["swapped"]:
                        state["swapped"] = True
                        try:
                            self.replace(run, copy, kind, kept)
                        except (OSError, NotImplementedError):
                            state["unavailable"] = True
                            raise tt.UnsafeRunFile("not swapped") from None
                        try:
                            return real(pinned)
                        finally:
                            self.put_back(run, copy, kind, kept)
                    return real(pinned)

                for call in (lambda: tt.read_run_file(run, self.FRAME),
                             lambda: tt.list_run_dir(run, ".dubflow-work/mastering")):
                    state["swapped"] = False
                    with mock.patch.object(tt, "_open_root_fd", swapping), self.assertRaises(tt.UnsafeRunFile):
                        call()
                    self.assertTrue(state["swapped"])
                if state.get("unavailable"):
                    continue  # this kind cannot be made here
                ran.add(kind)
        self.assert_kinds_ran(ran)

    def test_a_folder_swapped_while_it_is_listed_never_lists_outside_names(self):
        # right before the listing, the mastering folder (or the run folder) becomes a link, junction or another
        # folder whose files are not the run's: the listing comes from the pinned folder's handle, or fails
        for which in ("run folder", "mastering folder"):
            for kind in self.kinds():
                for back in (True, False):
                    with self.subTest(which=which, kind=kind, back=back), TemporaryDirectory() as tmp:
                        run, _ = self.run_with(tmp)
                        copy = self.outside_run(tmp, run)
                        inside = sorted(os.listdir(run / ".dubflow-work" / "mastering"))
                        at = run if which == "run folder" else run / ".dubflow-work" / "mastering"
                        target = copy if which == "run folder" else copy / ".dubflow-work" / "mastering"
                        kept = Path(tmp) / "kept"
                        tt.pin_root(run)
                        real = tt._list_fd
                        state = {"swapped": False, "refused": False}

                        def swapping(folder_fd, shown):
                            if not state["swapped"]:
                                state["swapped"] = True
                                try:
                                    self.replace(at, target, kind, kept)
                                except (OSError, NotImplementedError):
                                    state["refused"] = True
                                try:
                                    return real(folder_fd, shown)
                                finally:
                                    if back and not state["refused"]:
                                        self.put_back(at, target, kind, kept)
                            return real(folder_fd, shown)

                        with mock.patch.object(tt, "_list_fd", swapping):
                            try:
                                names = tt.list_run_dir(run, ".dubflow-work/mastering")
                            except tt.UnsafeRunFile:
                                names = None
                        self.assertTrue(state["swapped"])
                        self.assertIn(None if names is None else sorted(names), (None, inside))
                        if not state["refused"] and (back or which == "mastering folder"):
                            self.assertEqual(sorted(names), inside)  # listed from the pinned folder's handle
                        if not back and not state["refused"] and which == "run folder":
                            self.assertIsNone(names)  # the run folder no longer the pinned one: fails after

    def test_a_run_folder_replaced_before_a_read_is_refused_even_if_put_back_before_the_last_check(self):
        # the run folder's name leads out when a read or a listing starts and is put back right before the check
        # against the pin after it: only the handle on the pinned folder stands between the read and outside bytes
        ran: set[str] = set()
        for kind in self.kinds():
            for call in ("read", "list"):
                with self.subTest(kind=kind, call=call), TemporaryDirectory() as tmp:
                    run, _ = self.run_with(tmp)
                    copy = self.outside_run(tmp, run)
                    kept = Path(tmp) / "kept"
                    tt.pin_root(run)
                    real_check = tt._check_root
                    state = {"back": False}

                    def check_then_put_back(pinned):
                        if not state["back"]:
                            state["back"] = True
                            self.put_back(run, copy, kind, kept)
                        return real_check(pinned)

                    try:
                        self.replace(run, copy, kind, kept)
                    except (OSError, NotImplementedError):
                        continue  # this kind cannot be made here
                    ran.add(kind)
                    try:
                        with mock.patch.object(tt, "_check_root", check_then_put_back):
                            if call == "read":
                                result = tt.read_run_file(run, self.FRAME)
                            else:
                                result = tt.list_run_dir(run, ".dubflow-work/mastering")
                    except tt.UnsafeRunFile:
                        result = None
                    finally:
                        if not state["back"]:
                            self.put_back(run, copy, kind, kept)
                    self.assertIsNone(result)
        self.assert_kinds_ran(ran)

    def test_a_load_whose_run_folder_is_replaced_after_its_last_read_fails(self):
        with TemporaryDirectory() as tmp:
            run, _ = self.run_with(tmp)
            copy = self.outside_run(tmp, run)
            real_read, real_list = tt.read_run_file, tt.list_run_dir
            count = {"n": 0, "refused": False}

            def counted(real):
                def call(root, relative):
                    result = real(root, relative)
                    count["n"] += 1
                    if count["n"] == count.get("swap_at"):
                        try:
                            self.replace(run, copy, "another folder", Path(tmp) / "kept")
                        except OSError:
                            count["refused"] = True  # Windows: the load holds the run folder open
                    return result
                return call

            with mock.patch.object(tt, "read_run_file", counted(real_read)), \
                    mock.patch.object(tt, "list_run_dir", counted(real_list)):
                tt.load(run)
                count["swap_at"], count["n"] = count["n"], 0  # the load's last read or listing
                if os.name == "nt":
                    tt.load(run)
                    self.assertTrue(count["refused"])
                else:
                    with self.assertRaises(tt.UnsafeRunFile):
                        tt.load(run)
            self.assertEqual(count["n"], count["swap_at"])

    @unittest.skipUnless(tt._POSIX_HANDLES and not tt._NT_HANDLES, "the check before an open is POSIX only")
    def test_a_folder_swapped_between_its_check_and_its_open_is_refused(self):
        for which in (".dubflow-work", "mastering"):
            for kind in ("symbolic link", "another folder"):
                with self.subTest(which=which, kind=kind), TemporaryDirectory() as tmp:
                    run, _ = self.run_with(tmp)
                    copy = self.outside_run(tmp, run)
                    relative = ".dubflow-work" if which == ".dubflow-work" else ".dubflow-work/mastering"
                    at, target = run / relative, copy / relative
                    tt.pin_root(run)
                    real = tt._lstat_at
                    state = {"swapped": False}

                    def swap_after_check(folder_fd, part, shown):
                        found = real(folder_fd, part, shown)
                        if part == which and not state["swapped"]:
                            state["swapped"] = True
                            self.replace(at, target, kind, Path(tmp) / "kept")
                        return found

                    with mock.patch.object(tt, "_lstat_at", swap_after_check), self.assertRaises(tt.UnsafeRunFile):
                        tt.read_run_file(run, self.FRAME)
                    self.assertTrue(state["swapped"])

    def test_a_run_folder_reached_through_a_linked_parent_stays_pinned_under_that_name(self):
        # a link above the run folder is the user's: it is resolved once and the run folder below it pinned
        with TemporaryDirectory() as tmp:
            run, _ = self.run_with(tmp)
            copy = self.outside_run(tmp, run)
            above = Path(tmp) / "above"
            try:
                os.symlink(run.parent, above, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are not available here")
            named = above / run.name
            loaded = tt.load(named)
            self.assertEqual(tt._ROOTS[os.fspath(named)].path, run.resolve())
            self.assertTrue(loaded.jpeg)
            self.replace(run, copy, "another folder", Path(tmp) / "kept")
            with self.assertRaises(tt.UnsafeRunFile):
                tt.read_run_file(named, self.FRAME)

    def test_a_selected_run_folder_that_is_already_a_link_is_refused_before_anything_is_read(self):
        # the selected entry itself is a symbolic link or junction to an outside folder when the load starts: it is
        # never followed, so the load fails before a single file (inside or outside) is read or listed
        ran: set[str] = set()
        for kind in self.kinds():
            if kind == "another folder":
                continue
            with self.subTest(kind=kind), TemporaryDirectory() as tmp:
                run, _ = self.run_with(tmp)
                copy = self.outside_run(tmp, run)
                selected = Path(tmp) / "selected"
                try:
                    if kind == "symbolic link":
                        os.symlink(copy, selected, target_is_directory=True)
                    else:
                        import _winapi
                        _winapi.CreateJunction(str(copy), str(selected))
                except (OSError, NotImplementedError):
                    continue  # this kind cannot be made here
                ran.add(kind)
                touched: list[str] = []
                real_root = tt._open_root_fd

                def opened(pinned):
                    touched.append("run folder")
                    return real_root(pinned)

                with mock.patch.object(tt, "_open_root_fd", opened):
                    for call in (lambda: tt.load(selected), lambda: tt.pin_root(selected)):
                        with self.assertRaises(tt.UnsafeRunFile) as caught:
                            call()
                        self.assertNotIn(str(Path(tmp)), str(caught.exception))
                self.assertEqual(touched, [])
                self.assertNotIn(os.fspath(selected), tt._ROOTS)
        if os.name == "nt" and "junction" in self.kinds():
            self.assertIn("junction", ran)
        if os.name != "nt":
            self.assertIn("symbolic link", ran)

    @unittest.skipUnless(hasattr(os, "mkfifo") and not tt._NT_HANDLES, "named pipes are not available here")
    def test_a_file_swapped_for_a_named_pipe_after_its_check_fails_instead_of_blocking(self):
        import signal
        with TemporaryDirectory() as tmp:
            run, _ = self.run_with(tmp)
            tt.pin_root(run)
            real = tt._checked
            state = {"swapped": False}

            def swap_for_pipe(st, shown, *, folder):
                real(st, shown, folder=folder)
                if not folder and not state["swapped"]:
                    state["swapped"] = True
                    (run / self.FRAME).unlink()
                    os.mkfifo(run / self.FRAME)

            def blocked(*_):
                raise AssertionError("the open blocked on a named pipe")

            previous = signal.signal(signal.SIGALRM, blocked)
            signal.alarm(10)
            try:
                with mock.patch.object(tt, "_checked", swap_for_pipe), self.assertRaises(tt.UnsafeRunFile):
                    tt.read_run_file(run, self.FRAME)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, previous)
            self.assertTrue(state["swapped"])

    def test_a_name_windows_would_read_as_another_is_refused_everywhere(self):
        # Windows trims a trailing space or dot from the last part of a path only: "sub /x.jpg" would be checked as
        # "sub" and opened through "sub " (which may be a junction). Device names open a device, not a file.
        names = ("sub /i1-s1-c2.jpg", "i1-s1-c2.jpg.", "i1-s1-c2.jpg ", "sub./i1-s1-c2.jpg", "CON", "nul.jpg",
                 "Com1.json", "lpt9", "aux .jpg", "conin$")
        for name in names:
            with self.subTest(name=name), TemporaryDirectory() as tmp:
                run, _ = self.run_with(tmp)
                self.set_keyframe(run, "i1-s1c2", name)
                with self.assertRaises(tt.UnsafeRunFile) as caught:
                    tt.load(run)
                self.assertIn("Windows would read as another", str(caught.exception))
                self.assertNotIn(str(Path(tmp)), str(caught.exception))
        for fine in ("console.json", "nullable.jpg", "com10.jpg", "i1-s1-c2.jpg"):
            self.assertEqual(tt._rel_parts(fine)[0], (fine,))

    def test_names_are_matched_as_the_platform_matches_them(self):
        # Windows matches names without regard to case (as before, through paths); POSIX file systems as they do
        with TemporaryDirectory() as tmp:
            run, _ = self.run_with(tmp)
            inside = (run / self.FRAME).read_bytes()
            upper = self.FRAME.upper()
            expected = inside if os.path.lexists(run / upper) else None
            if os.name == "nt":
                self.assertEqual(expected, inside)
            self.assertEqual(tt.read_run_file(run, upper), expected)

    def test_a_load_walks_each_folder_once(self):
        # the folder handles are kept for the whole load: the run folder and each folder below it are opened once
        with TemporaryDirectory() as tmp:
            run, _ = self.run_with(tmp)
            real_root, real_child = tt._open_root_fd, tt._open_child
            opened: list[str] = []

            def root(pinned):
                opened.append("<run>")
                return real_root(pinned)

            def child(folder_fd, part, *, folder, shown):
                if folder:
                    opened.append(part)
                return real_child(folder_fd, part, folder=folder, shown=shown)

            with mock.patch.object(tt, "_open_root_fd", root), mock.patch.object(tt, "_open_child", child):
                loaded = tt.load(run)
            self.assertTrue(loaded.jpeg)
            self.assertEqual(sorted(opened), sorted(set(opened)))
            self.assertIn("<run>", opened)
            self.assertIsNone(tt._HELD)  # released when the load ends

    @unittest.skipUnless(os.name == "nt", "folder handles are opened through ntdll only on Windows")
    def test_a_held_folder_is_listed_from_its_handle_on_windows(self):
        with TemporaryDirectory() as tmp:
            run, _ = self.run_with(tmp)
            mdir = run / ".dubflow-work" / "mastering"
            for index in range(1000):  # more than one 64 KiB buffer of entries
                (mdir / f"extra-{index:04d}-{'x' * 60}.bin").write_bytes(b"")
            pinned = tt.pin_root(run)
            fd, close = tt._folder_fd(pinned, (".dubflow-work", "mastering"), "mastering")
            try:
                self.assertEqual(sorted(tt._list_fd(fd, "mastering")), sorted(os.listdir(mdir)))
            finally:
                if close:
                    os.close(fd)
            self.assertEqual(sorted(tt.list_run_dir(run, ".dubflow-work/mastering")), sorted(os.listdir(mdir)))

    @unittest.skipUnless(os.name == "nt", "folder handles are opened through ntdll only on Windows")
    def test_a_held_folder_cannot_be_renamed_on_windows(self):
        with TemporaryDirectory() as tmp:
            run, _ = self.run_with(tmp)
            pinned = tt.pin_root(run)
            fd = tt._open_root_fd(pinned)
            try:
                with self.assertRaises(OSError):
                    os.rename(run, Path(tmp) / "kept-run")
            finally:
                os.close(fd)
            os.rename(run, Path(tmp) / "kept-run")  # free again once the handle is closed

    def test_a_file_is_read_whole_without_a_buffer_of_the_cap_size(self):
        # reading asks for the reported size (plus one byte), then 1 MiB pieces if the file grew while it was
        # read; never one read of the cap's size (a 256 MiB buffer per file made Windows loads ~60 ms per file)
        class Reader:
            def __init__(self, data: bytes) -> None:
                self.data, self.asked = data, []

            def read(self, n: int) -> bytes:
                self.asked.append(n)
                out, self.data = self.data[:n], self.data[n:]
                return out

        reader = Reader(b"x" * 10)
        self.assertEqual(tt._read_capped(reader, 10, "f"), b"x" * 10)
        self.assertEqual(reader.asked, [11])
        grown = Reader(b"y" * (3 << 20))  # grew from 5 bytes after the size was taken
        self.assertEqual(tt._read_capped(grown, 5, "f"), b"y" * (3 << 20))
        self.assertLessEqual(max(grown.asked), 1 << 20)
        with mock.patch.object(tt, "MAX_INPUT_BYTES", 8):
            with self.assertRaises(tt.UnsafeRunFile):
                tt._read_capped(Reader(b"z" * 20), 5, "f")  # grew past the cap after its check
        with TemporaryDirectory() as tmp:
            run, _ = self.run_with(tmp)
            asked: list[int] = []
            real_fdopen = os.fdopen

            def fdopen(fd, *args, **kwargs):
                handle = real_fdopen(fd, *args, **kwargs)
                read = handle.read

                class Spy:
                    def __enter__(self):
                        return self

                    def __exit__(self, *exc):
                        handle.close()

                    def fileno(self):
                        return handle.fileno()

                    def read(self, n=-1):
                        asked.append(n)
                        return read(n)
                return Spy()

            with mock.patch.object(tt.os, "fdopen", fdopen):
                tt.load(run)
            self.assertTrue(asked)
            self.assertLess(max(asked), 1 << 20)

    def test_only_regular_files_under_the_size_cap_are_read(self):
        with TemporaryDirectory() as tmp:
            run, _ = self.run_with(tmp)
            (run / ".dubflow-work" / "mastering" / "a-folder.jpg").mkdir()
            self.set_keyframe(run, "i1-s1c2", "a-folder.jpg")
            with self.assertRaises(tt.UnsafeRunFile):
                tt.load(run)
        with TemporaryDirectory() as tmp:
            run, _ = self.run_with(tmp)
            with mock.patch.object(tt, "MAX_INPUT_BYTES", 64):
                with self.assertRaises(tt.UnsafeRunFile):
                    tt.load(run)


class DeterminismTests(unittest.TestCase):
    def test_twice_reversed_and_with_the_primary_listed_again(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, donor, gt, lock = STUDY["run"], STUDY["donor"], STUDY["gt"], STUDY["lock"]
            quiet(tt.main, all_args(root, run, donor, gt, lock, "again"))
            args = all_args(root, run, donor, gt, lock, "listed")
            at = args.index("--runs")
            args[at + 1:at + 2] = [str(donor), str(run)]
            quiet(tt.main, args)
            for out in ("again", "listed"):
                self.assertEqual((root / out / "targeted-run-a.json").read_text(encoding="utf-8"), STUDY["json"], out)
                self.assertEqual((root / out / "targeted-run-a.md").read_text(encoding="utf-8"), STUDY["md"], out)
        self.assertEqual(STUDY["report"]["runs"]["run-a"]["e1"]["donor_runs"], ["run-b"])
        self.assertEqual(STUDY["report"]["runs"]["run-b"]["e1"]["donor_runs"], ["run-a"])

    def test_paths_line_ends_and_no_randomness(self):
        loaded = tt.load(STUDY["run"])
        for path in loaded.files:
            self.assertNotIn("\\", path)
            self.assertTrue(path.startswith(".dubflow-work/"), path)
        self.assertNotIn(str(STUDY["root"]), STUDY["json"])
        # the performance record leaves the machine too: run names only, no local folder (user name, home) paths
        root = Path(STUDY["root"])
        for form in {str(root), root.as_posix(), str(root.resolve()), root.resolve().as_posix(), str(Path.home()),
                     Path.home().as_posix()}:
            self.assertNotIn(form, STUDY["perf"])
            self.assertNotIn(form.replace("\\", "\\\\"), STUDY["perf"])
        perf = json.loads(STUDY["perf"])
        self.assertNotIn("paths", perf)
        self.assertEqual(perf["runs_loaded"], ["run-a", "run-b"])
        with TemporaryDirectory() as tmp:
            lf, crlf = Path(tmp) / "lf.py", Path(tmp) / "crlf.py"
            lf.write_bytes(b"a = 1\nb = 2\n")
            crlf.write_bytes(b"a = 1\r\nb = 2\r\n")
            self.assertEqual(tt.code_digest(lf), tt.code_digest(crlf))
        for name in ("targeted.py", "targeting.py"):
            for node in ast.walk(ast.parse((HERE / name).read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    self.assertNotIn("random", [a.name for a in node.names])
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "random")
        self.assertIsNone(STUDY["report"]["params"]["seed"])

    def test_golden_fixture_digest(self):
        """The fixture study, its code digests and pre-registration masked, is the same on Linux and Windows. After a
        deliberate change of the harness or the planner, print the new value with ``DUBFLOW_PRINT_GOLDEN=1``."""
        import os
        report = json.loads(STUDY["json"])
        report["code_sha256"] = report["preregistration"] = None
        report["decision"].pop("label", None)  # the record's commit and the code digests, not the study
        report["decision"]["gates"]["preregistration"] = None
        digest = hashlib.sha256(tt.dumps(report).encode("utf-8")).hexdigest()
        if os.environ.get("DUBFLOW_PRINT_GOLDEN"):
            print("golden", digest)
        self.assertEqual(digest, GOLDEN)


def prereg_record(**changes) -> dict:
    record = {"commit": "a" * 40, "targeting_sha256": tt.code_digest(HERE / "targeting.py"),
              "analysis_sha256": tt.code_digest(HERE / "targeted.py"), "primary_arm": tt.PRIMARY_ARM,
              "primary_metric": tt.PRIMARY_METRIC, "comparators": list(tt.COMPARATORS), "required_runs": tt.REQUIRED_RUNS}
    record.update(changes)
    return record


FROZEN_RECORD = {"commit": "c60ab029e55ced81ebf8122ec1cfcd76f98ff0c7",
                 "targeting_sha256": "fd884a26e7f70e8405364c9309f0129d41abe1c21e51cc053a991b701427ee92",
                 "analysis_sha256": "4dff01c5fd1a80b9298c77439e2eb763056894e4dfecae6134cbf437d6711a7f"}


class PreregistrationTests(unittest.TestCase):
    def status(self, ancestor: bool | None = True, **changes) -> dict:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.json"
            path.write_text(json.dumps(prereg_record(**changes)), encoding="utf-8")
            with mock.patch.object(tt, "git_is_ancestor", return_value=ancestor):
                return tt.prereg_status(path)

    def test_a_changed_selector_makes_the_results_exploratory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrong, right = root / "wrong.json", root / "right.json"
            wrong.write_text(json.dumps(prereg_record(targeting_sha256="0" * 64)), encoding="utf-8")
            right.write_text(json.dumps(prereg_record()), encoding="utf-8")
            with mock.patch.object(tt, "git_is_ancestor", return_value=True):
                for name, path in (("wrong", wrong), ("right", right)):
                    quiet(tt.main, all_args(root, STUDY["run"], STUDY["donor"], STUDY["gt"], STUDY["lock"], name, "--prereg", str(path)))
            bad = json.loads((root / "wrong" / "targeted-run-a.json").read_text(encoding="utf-8"))
            good = json.loads((root / "right" / "targeted-run-a.json").read_text(encoding="utf-8"))
            self.assertEqual(bad["preregistration"]["label"], "PREREGISTRATION MISMATCH: results exploratory")
            self.assertEqual(bad["preregistration"]["mismatches"], ["targeting_sha256"])
            self.assertIn("PREREGISTRATION MISMATCH", (root / "wrong" / "targeted-run-a.md").read_text(encoding="utf-8"))
            self.assertEqual(good["preregistration"]["label"], "PREREGISTERED")
            self.assertNotIn("label", good["decision"])
            self.assertNotIn("PREREGISTRATION MISMATCH", (root / "right" / "targeted-run-a.md").read_text(encoding="utf-8"))

    def test_the_analysis_and_the_named_arms_are_registered_too(self):
        self.assertEqual(self.status()["label"], tt.PREREGISTERED)
        for key, value in (("analysis_sha256", "0" * 64), ("primary_arm", "open:prior:W=fwd:+matched"),
                           ("primary_metric", "loc_hit_units_frame"), ("comparators", ["open:A+:-:+matched"]),
                           ("required_runs", 2)):
            found = self.status(**{key: value})
            self.assertEqual((found["label"], found["mismatches"]), (tt.PREREG_MISMATCH, [key]), key)

    def test_no_commit_or_a_commit_outside_the_history_is_not_preregistered(self):
        self.assertEqual((self.status(commit=None)["label"], self.status(commit=None)["commit_check"]),
                         (tt.NOT_PREREGISTERED, "no_commit"))
        self.assertEqual((self.status(False)["label"], self.status(False)["commit_check"]), (tt.NOT_PREREGISTERED, "not_ancestor"))
        # fail closed: without git the ancestry cannot be shown, so matching digests are not enough
        self.assertEqual((self.status(None)["label"], self.status(None)["commit_check"]), (tt.PREREG_UNVERIFIED, "git_unavailable"))
        self.assertTrue(self.status(None)["match"])
        found = tt.gate_decision({"result": "not supported"}, {"run-a": "PASS"}, self.status(None)["label"])
        self.assertEqual((found["result"], found["result_before_gates"], found["label"]),
                         ("exploratory", "not supported", tt.PREREG_UNVERIFIED))
        # until the commit after the pre-registration commit fills it in, the committed record has no commit
        if json.loads(tt.PREREG_PATH.read_text(encoding="utf-8"))["commit"] is None:
            self.assertEqual(STUDY["report"]["preregistration"]["commit_check"], "no_commit")
            self.assertEqual(STUDY["report"]["preregistration"]["label"], tt.NOT_PREREGISTERED)

    def test_the_committed_record_names_the_primary_arm(self):
        record = json.loads(tt.PREREG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(record["primary_arm"], tt.PRIMARY_ARM)
        self.assertEqual(record["primary_metric"], tt.PRIMARY_METRIC)
        self.assertEqual(record["comparators"], list(tt.COMPARATORS))
        self.assertEqual(record["required_runs"], tt.REQUIRED_RUNS)
        # The record is frozen: the real-data study ran at 716e984 with these digests and the commit as an ancestor
        # (PREREGISTERED). It is never rewritten afterwards.
        self.assertEqual({k: record[k] for k in FROZEN_RECORD}, FROZEN_RECORD)
        # The selector is still the registered one.
        self.assertEqual(record["targeting_sha256"], tt.code_digest(HERE / "targeting.py"),
                         "targeting.py changed after the real-data run: the registered selector must not change")
        # targeted.py was hardened after that run (review of PR #830: fail-closed ancestry, batch label, no local paths
        # in the perf record). A re-run at this head is therefore exploratory by construction, never PREREGISTERED.
        self.assertNotEqual(record["analysis_sha256"], tt.code_digest(HERE / "targeted.py"))
        with mock.patch.object(tt, "git_is_ancestor", return_value=True):
            found = tt.prereg_status(tt.PREREG_PATH)
        self.assertEqual((found["label"], found["mismatches"]), (tt.PREREG_MISMATCH, ["analysis_sha256"]))


class DecisionTests(unittest.TestCase):
    def runs(self, n: int, mine: int = 3, prod: int = 1, shuf: int = 1, p: float = 0.01) -> dict:
        arm = {"id": tt.PRIMARY_ARM, "e2": {tt.PRIMARY_METRIC: mine, "chance_r3_units": 0.5,
                                            "vs_production": {"arm": tt.COMPARATORS[0], "comparator_r3_with_frame": prod,
                                                              "mcnemar_p_exact": p},
                                            "vs_shuffled_prior": {"arm": tt.COMPARATORS[1], "comparator_r3_with_frame": shuf}}}
        return {f"run-{k}": {"arms": [json.loads(json.dumps(arm))]} for k in range(n)}

    def test_every_run_the_rule_names(self):
        self.assertEqual(tt.decision(self.runs(5), "run-0")["result"], "supported")
        self.assertEqual(tt.decision(self.runs(5, prod=3), "run-0")["result"], "not supported")
        for n in (1, 2, 4):
            found = tt.decision(self.runs(n), "run-0")
            self.assertEqual((found["result"], found["runs_given"], found["required_runs"]), ("unavailable", n, 5))
        missing = self.runs(5)
        missing["run-3"]["arms"] = []
        self.assertEqual(tt.decision(missing, "run-0")["result"], "unavailable")
        other = self.runs(5)
        other["run-2"]["arms"][0]["e2"]["vs_shuffled_prior"]["arm"] = "open:shuffled_prior:W=10s:+matched"
        self.assertEqual(tt.decision(other, "run-0")["result"], "unavailable")

    def test_the_gates(self):
        passed = {f"run-{k}": "PASS" for k in range(5)}
        gate = lambda blind, label, runs=None: tt.gate_decision(tt.decision(runs or self.runs(5), "run-0"), blind, label)  # noqa: E731
        self.assertEqual(gate(passed, tt.PREREGISTERED)["result"], "supported")
        self.assertEqual(gate({**passed, "run-1": "NOT_RUN"}, tt.PREREGISTERED)["result"], "unavailable: blindness check not run")
        self.assertEqual(gate({**passed, "run-1": "FAIL"}, tt.PREREGISTERED)["result"], "BLINDNESS_CHECK_FAILED")
        found = gate(passed, tt.NOT_PREREGISTERED)
        self.assertEqual((found["result"], found["result_before_gates"], found["label"]),
                         ("exploratory", "supported", tt.NOT_PREREGISTERED))
        self.assertEqual(gate(passed, tt.PREREG_MISMATCH, self.runs(5, prod=3))["result"], "exploratory")
        self.assertEqual(gate(passed, tt.NOT_PREREGISTERED, self.runs(2))["result"], "unavailable")

    def test_the_fixture_study_is_two_runs_so_no_verdict(self):
        found = STUDY["report"]["decision"]
        self.assertEqual((found["result"], found["runs_given"], found["gates"]["blindness_check"]), ("unavailable", 2, "PASS"))
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            quiet(tt.main, all_args(root, STUDY["run"], STUDY["donor"], STUDY["gt"], STUDY["lock"], "off",
                                    "--blindness-check", "off"))
            off = json.loads((root / "off" / "targeted-run-a.json").read_text(encoding="utf-8"))
        self.assertEqual(off["decision"]["result"], "unavailable: blindness check not run")


class StatisticsTests(unittest.TestCase):
    def test_exact_mcnemar_and_chance(self):
        self.assertEqual(tt.mcnemar_exact(5, 0), 0.0625)
        self.assertEqual(tt.mcnemar_exact(0, 0), 1.0)
        self.assertEqual(tt.mcnemar_exact(3, 3), 1.0)
        # 4 eligible frames, 2 drawn: a unit with 1 frame inside is hit with 1 - C(3,2)/C(4,2) = 1/2, with 2: 5/6
        self.assertEqual(tt.chance_hits(4, [1, 2], 2), round(0.5 + 5 / 6, 4))
        self.assertEqual(tt.chance_hits(4, [0], 2), 0.0)


class OracleTests(unittest.TestCase):
    def test_upper_bounds_are_apart_and_labelled(self):
        run = STUDY["report"]["runs"]["run-a"]
        for arm in run["arms"]:
            self.assertFalse(arm["oracle"])
            self.assertNotIn("ORACLE", arm["id"])
        self.assertTrue(run["upper_bounds"])
        for bound in run["upper_bounds"]:
            self.assertTrue(bound["oracle"])
            self.assertTrue(bound["id"].startswith("ORACLE_"))
        section = None
        for line in STUDY["md"].splitlines():
            if line.startswith("#"):
                section = line
            if "ORACLE_" in line:
                self.assertEqual(section, "### Upper bounds (use ground truth)")

    def test_acquisition_oracle_tells_only_frames_inside_the_range(self):
        with TemporaryDirectory() as tmp:
            target, _, gt = prepared(Path(tmp))
            samples = [tg.Sample("i1", "i1-s1c6", "i1-s1", "", 33000, "numbered_window", "i1.u3", "test", 1),
                       tg.Sample("i1", "i1-s1c8", "i1-s1", "", 45000, "numbered_window", "i1.u3", "test", 1)]
            told = tt.ORACLE_acq_at_selected(gt, target.A, samples, False, None)
            items = {c["id"]: tt.vision_items(c) for c in told.logs["i1"]["candidates"]}
            self.assertEqual([(i["text"], i["detail"]) for i in items["i1-s1c6"]], [(SENTENCES[2][0], "oracle")])
            self.assertEqual([(i["text"], i["confidence"], i["detail"]) for i in items["i1-s1c8"]], [("", 0, "oracle-empty")])
            # one rule for every arm: a frame selected for u2 that lies in u3's range is told u3's sentence, as a
            # uniform (no unit) frame there is
            for unit_id in ("i1.u2", None):
                moved = [tg.Sample("i1", "i1-s1c6", "i1-s1", "", 33000, "numbered_window", unit_id, "test", 1)]
                told = tt.ORACLE_acq_at_selected(gt, target.A, moved, False, None)
                c6 = next(c for c in told.logs["i1"]["candidates"] if c["id"] == "i1-s1c6")
                self.assertEqual([(i["text"], i["detail"]) for i in tt.vision_items(c6)], [(SENTENCES[2][0], "oracle")], unit_id)
            empty = ev.Evidence("i1-s1c8", [ev.Item("vision", 0, "", (), "oracle-empty")])
            c8 = target.shot("i1", "i1-s1c8").candidate
            unit = align.Unit("i1.u3", SENTENCES[2][0], 0, 1000, "narration")
            self.assertEqual(align.Scorer([c8], {"i1-s1c8": empty}).relevance(unit, c8)[0], 0)


class W3UnchangedTests(unittest.TestCase):
    def test_the_old_replay_and_the_rules_are_w3s(self):
        gt = w3.load_gt(STUDY["gt"], STUDY["lock"])
        run = w3.load_run(STUDY["run"])
        self.assertEqual(STUDY["report"]["runs"]["run-a"]["e1"]["replayed_with_review"],
                         tt.score_brief(w3.score(gt, w3.replay(run))))
        defined = {n.name for n in ast.walk(ast.parse((HERE / "targeted.py").read_text(encoding="utf-8")))
                   if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        self.assertFalse(defined & {"score", "hits", "top", "overlap", "norm", "attribute", "replay"})
        with TemporaryDirectory() as tmp, mock.patch.object(w3, "score", wraps=w3.score) as scored, \
                mock.patch.object(w3, "attribute", wraps=w3.attribute) as attributed:
            target, donor, gt = prepared(Path(tmp))
            ctx = tt.Context(gt, target, tt.DonorIndex(target, [donor]), None, False, 500, True, "flagged", None, None)
            ctx.replay_with("A", [])
            self.assertTrue(scored.called and attributed.called)


class AttributionDeltaTests(unittest.TestCase):
    def test_hand_made_transitions(self):
        def rows(*stages):
            return {"rows": [{"unit": f"i1.u{k}", "kind": "footage", "stage": s, "r7": r7, "chosen": chosen,
                              "chosen_hit": hit} for k, (s, r7, chosen, hit) in enumerate(stages, 1)]}
        before = rows(("R3_NOT_LOOKED", "FLAGGED_MISS", ["a"], False), ("R3_NOT_LOOKED", "FLAGGED_MISS", ["b"], False),
                      ("OK", "READY_HIT", ["c"], True), ("OK", "READY_HIT", ["d"], True))
        after = rows(("OK", "FLAGGED_HIT", ["x"], True), ("R4_WEAK_MATCH", "FLAGGED_MISS", ["b"], False),
                     ("R5_RANKING", "FLAGGED_MISS", ["c"], False), ("R5_RANKING", "SILENT_MISS", ["y"], False))
        found = tt.attribution_delta(before, after, new_ids={"x", "y"}, shared_ids=set(), donor_of={"x": "run-b"})
        self.assertEqual(found["transitions"], {"OK->R5_RANKING": 2, "R3_NOT_LOOKED->OK": 1, "R3_NOT_LOOKED->R4_WEAK_MATCH": 1})
        self.assertEqual((found["fixed_r3"], found["moved_r3"], found["still_r3"]), (1, {"R4_WEAK_MATCH": 1}, 0))
        self.assertEqual((found["fixed_via_new_evidence"], found["fixed_by_donor_run"]), (1, {"run-b": 1}))
        self.assertEqual((found["new_silent_misses"], found["new_evidence_wrong_pick"]), (["i1.u4"], 1))

    def test_the_primary_arm_on_the_fixture(self):
        e5 = arms_of(STUDY["report"])[tt.PRIMARY_ARM]["e5"]
        self.assertEqual((e5["fixed_r3"], e5["fixed_via_new_evidence"], e5["fixed_by_donor_run"]), (1, 1, {"run-b": 1}))
        self.assertEqual(e5["lost_hits"], {"chosen_hit": 0, "hit_at_5": 0})
        self.assertIn("partial simulation", e5["note"])


class LabelTests(unittest.TestCase):
    def test_labels_windows_and_no_best(self):
        text, md = STUDY["json"], STUDY["md"]
        for label in ("GENERALIZATION NOT VERIFIED", "REAL-DEV", "partial simulation"):
            self.assertIn(label, text)
            self.assertIn(label, md)
        self.assertNotIn("lower bound", text.lower())
        self.assertNotIn("lower bound", md.lower())
        ids = set(arms_of(STUDY["report"]))
        for window in ("W=10s", "W=fwd"):
            self.assertTrue(any(window in i for i in ids), window)

        def keys(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    yield k
                    yield from keys(v)
            elif isinstance(value, list):
                for v in value:
                    yield from keys(v)
        self.assertFalse([k for k in keys(STUDY["report"]) if k.lower().startswith("best")])


class ScopeTests(unittest.TestCase):
    def test_no_product_path_is_changed(self):
        """A check of the #815 pull request's scope, not of the harness: run it for that PR only."""
        import os
        if os.environ.get("DUBFLOW_SCOPE_CHECK") != "815":
            self.skipTest("PR-scope check of #815; set DUBFLOW_SCOPE_CHECK=815")
        root = HERE.parents[2]
        try:
            base = subprocess.run(["git", "merge-base", "origin/main", "HEAD"], cwd=root, capture_output=True, text=True, check=True)
            changed = subprocess.run(["git", "diff", "--name-only", base.stdout.strip()], cwd=root, capture_output=True, text=True,
                                     check=True).stdout.split()
            changed += subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=root, capture_output=True,
                                      text=True, check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git or origin/main is not available here")
        for path in changed:
            self.assertFalse(path.startswith(("engine/", "contracts/", ".github/workflows/")), path)
            self.assertNotEqual(path, "scripts/ci/component_registry.json")


GOLDEN = "f202f1087383b8d8f3bf05ea1dec2c7ce699688471ce8f9e545084b5ac882d08"  # #837: the fixture logs carry recipe v24 and the per-piece notes (study numbers unchanged)


if __name__ == "__main__":
    unittest.main()
