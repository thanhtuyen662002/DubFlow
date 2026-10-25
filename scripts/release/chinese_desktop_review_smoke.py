"""Real Chinese local-media smoke through an installed DubFlow and the desktop review contract.

The production run uses the app-owned supervisor with the same arguments the
desktop host's ``start_job`` passes, with explicit Chinese routing. The review
step replays the host's ``review_cues``/``save_review`` file contract (#273: a
new sibling folder "<name> (sửa N)" holding a copy of the work cache, the
reviewed text and ``revision_of.json``) and then starts the revision the way
the desktop does: a new job id with the folder ``save_review`` returned. The
GUI itself is not driven, and the report says so.

The script never downloads media or models and never writes into the
repository. Reports keep only placeholders for local paths.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from typing import Any

REPORT_SCHEMA = "dubflow-chinese-desktop-review-smoke-v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
VIDEO_SUFFIXES = frozenset({".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"})
SIDECAR_SUFFIXES = frozenset({".srt", ".ass", ".ssa", ".vtt", ".lrc"})
OVERRIDES_FILE = "translation_overrides.json"
REVISION_OF_FILE = "revision_of.json"
REVISION_SKIP = frozenset({"b2-audio", "export-pending"})
REVISION_NAME = re.compile(r"^(?P<base>.*) \(sửa (?P<number>\d+)\)$")
CJK = re.compile(r"[㐀-鿿]")
VIETNAMESE = re.compile(r"[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]", re.IGNORECASE)
ABSOLUTE_PATH = re.compile(r"(?i)(?:\b[a-z]:[\\/]|^\\\\|/(?:users|home|mnt)/)")
PRIMARY_OUTPUTS = ("final_vi.mp4", "captions_vi.srt", "captions_vi.ass", "job_manifest.json", "qc_report.json")


class SmokeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_sidecars(source: Path) -> list[Path]:
    """Subtitle files that belong to the source: ``name.srt`` or ``name.<lang>.srt``."""
    stem = source.stem.casefold()
    sidecars = []
    for candidate in source.parent.iterdir():
        if candidate == source or candidate.suffix.casefold() not in SIDECAR_SUFFIXES:
            continue
        name = candidate.stem.casefold()
        if name == stem or (name.startswith(stem + ".") and "." not in name[len(stem) + 1:]):
            sidecars.append(candidate)
    return sorted(sidecars, key=lambda path: path.name.casefold())


def validate_source(source: Path, repo_root: Path = REPO_ROOT) -> Path:
    if not source.is_absolute():
        raise SmokeError("SOURCE_INVALID", "source must be an absolute path")
    if source.is_symlink() or not source.is_file():
        raise SmokeError("SOURCE_INVALID", "source must be an existing regular file")
    if source.suffix.casefold() not in VIDEO_SUFFIXES:
        raise SmokeError("SOURCE_INVALID", "source must be a local video file")
    resolved = source.resolve()
    if resolved.is_relative_to(repo_root.resolve()):
        raise SmokeError("SOURCE_IN_REPOSITORY", "real media must live outside the repository")
    if find_sidecars(resolved):
        raise SmokeError("SOURCE_HAS_SIDECAR", "source has a subtitle sidecar; this smoke needs ASR on real speech")
    return resolved


def version_root(install_root: Path) -> Path:
    pointer = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    relative = pointer.get("version_path")
    if not isinstance(relative, str) or not re.fullmatch(r"versions/[A-Za-z0-9._+-]{1,64}", relative):
        raise SmokeError("INSTALL_INVALID", "current.json does not name an installed version")
    root = install_root / relative
    for required in ("release-manifest.json", "runtime/python.exe", "app/bin/dubflow-supervisor.exe", "runtime/media/ffprobe.exe"):
        if not (root / required).is_file():
            raise SmokeError("INSTALL_INVALID", f"installed version is missing {required}")
    return root


class Redactor:
    """Replace local absolute paths with placeholders before anything is reported."""

    def __init__(self, **roots: Path) -> None:
        pairs = []
        for name, root in roots.items():
            for spelling in {str(root), str(root.resolve()), root.as_posix(), root.resolve().as_posix()}:
                pairs.append((spelling, f"<{name}>"))
        self.pairs = sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)

    def __call__(self, value: Any) -> Any:
        if isinstance(value, Path):
            value = str(value)
        if isinstance(value, str):
            for spelling, placeholder in self.pairs:
                value = value.replace(spelling, placeholder)
            return value
        if isinstance(value, list):
            return [self(item) for item in value]
        if isinstance(value, dict):
            return {key: self(item) for key, item in value.items()}
        return value


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in [str(key), *_strings(item)]]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def validate_report(report: dict[str, Any]) -> list[str]:
    """Problems that make a report untrustworthy or unsafe to commit."""
    problems = []
    for key in ("schema", "verdict", "real_media", "gui", "source", "runtime", "stages", "checks"):
        if key not in report:
            problems.append(f"missing {key}")
    if report.get("schema") != REPORT_SCHEMA:
        problems.append("unknown schema")
    checks = report.get("checks", [])
    if report.get("verdict") == "pass":
        if report.get("real_media") is not True:
            problems.append("a pass needs real media")
        if not checks or not all(check.get("passed") is True for check in checks):
            problems.append("a pass needs every check to pass")
    elif report.get("verdict") not in {"fail", "error"}:
        problems.append("verdict must be pass, fail or error")
    source = report.get("source", {})
    if not re.fullmatch(r"[0-9a-f]{64}", str(source.get("sha256", ""))):
        problems.append("source sha256 missing")
    if any(ABSOLUTE_PATH.search(text) for text in _strings(report)):
        problems.append("report contains an unredacted absolute path")
    return problems


def review_cues(output_dir: Path) -> list[dict[str, Any]]:
    """Python replay of the desktop host's ``review_cues``."""
    translation = json.loads((output_dir / ".dubflow-work" / "translation.json").read_text(encoding="utf-8"))
    overrides_path = output_dir / OVERRIDES_FILE
    overrides = json.loads(overrides_path.read_text(encoding="utf-8"))["cues"] if overrides_path.is_file() else {}
    cues = []
    for cue in translation["cues"]:
        machine = cue.get("translated_text", "")
        edited = overrides.get(cue["cue_id"])
        cues.append({"cue_id": cue["cue_id"], "start_ms": cue["start_ms"], "end_ms": cue["end_ms"],
                     "source_text": cue.get("source_text", ""), "machine_text": machine,
                     "text": edited if edited is not None else machine, "edited": edited is not None})
    return cues


def revision_dir(original: Path) -> Path:
    match = REVISION_NAME.match(original.name)
    base = match.group("base") if match else original.name
    for number in range(1, 1000):
        candidate = original.parent / f"{base} (sửa {number})"
        if not candidate.exists():
            return candidate
    raise SmokeError("SAVE_REJECTED", "too many revisions")


def save_review(output_dir: Path, overrides: dict[str, str]) -> Path:
    """Python replay of the desktop host's ``save_review``: same validation, a new sibling folder."""
    if len(overrides) > 10_000 or any(not cue_id or len(cue_id) > 128 or not text.strip() or len(text) > 2000
                                      for cue_id, text in overrides.items()):
        raise SmokeError("SAVE_REJECTED", "invalid reviewed text: empty or longer than 2000 characters")
    target = revision_dir(output_dir)
    partial = target.with_name(target.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    try:
        shutil.copytree(output_dir / ".dubflow-work", partial / ".dubflow-work", symlinks=True,
                        ignore=lambda directory, names: [name for name in names if Path(directory) == output_dir / ".dubflow-work"
                                                         and name in REVISION_SKIP])
        if overrides:
            (partial / OVERRIDES_FILE).write_text(json.dumps({"schema_version": 1, "cues": overrides}, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
        manifest = output_dir / "job_manifest.json"
        link = {"schema_version": 1, "original_output_dir": str(output_dir),
                "original_manifest_sha256": "sha256:" + sha256_file(manifest) if manifest.is_file() else None,
                "created_unix": int(time.time())}
        (partial / REVISION_OF_FILE).write_text(json.dumps(link, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(partial, target)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return target


def supervisor_command(version: Path, install_root: Path, job_id: str, source: Path, output_dir: Path,
                       voice_id: str | None) -> list[str]:
    """The arguments the desktop host's ``start_job`` passes for a Chinese job (Argos, no blur)."""
    command = [str(version / "app" / "bin" / "dubflow-supervisor.exe"), "run", "--root", str(version),
               "--job-id", job_id, "--source", str(source), "--output-dir", str(output_dir),
               "--data-root", str(install_root), "--model-root", str(install_root / "models"),
               "--status-path", str(install_root / "control" / "jobs" / f"{job_id}.json")]
    if voice_id:
        command += ["--enable-dubbing", "--tts-voice-id", voice_id]
    return command + ["--source-language", "zh"]


def probe(version: Path, media: Path) -> dict[str, Any]:
    completed = subprocess.run([str(version / "runtime" / "media" / "ffprobe.exe"), "-v", "error", "-show_entries",
                                "format=duration:stream=codec_type,codec_name,width,height,sample_rate,channels",
                                "-of", "json", str(media)], capture_output=True, text=True, timeout=120)
    if completed.returncode != 0:
        raise SmokeError("PROBE_FAILED", completed.stderr.strip()[:300])
    value = json.loads(completed.stdout)
    return {"duration_s": round(float(value["format"]["duration"]), 3),
            "streams": [{key: stream[key] for key in sorted(stream) if key != "disposition"} for stream in value["streams"]]}


def dubbing_summary(output_dir: Path, reason: str | None) -> dict[str, Any]:
    """How many cues were dubbed; failed cues keep the original audio by design."""
    manifest = json.loads((output_dir / "job_manifest.json").read_text(encoding="utf-8"))
    cues = json.loads((output_dir / ".dubflow-work" / "translation.json").read_text(encoding="utf-8")).get("cues", [])
    failed: list[str] = []
    checkpoint = output_dir / ".dubflow-work" / "checkpoint.json"
    if checkpoint.is_file():
        document = json.loads(checkpoint.read_text(encoding="utf-8")).get("stages", {}).get("b2-audio", {}).get("tts_document")
        if document and Path(document).is_file():
            failed = [item.get("segment_id") for item in json.loads(Path(document).read_text(encoding="utf-8")).get("failures", [])]
    return {"completion_reason": reason, "cues": len(cues), "failed_cues": len(failed),
            "warnings": [warning.get("code", warning) if isinstance(warning, dict) else warning
                         for warning in manifest.get("warnings", [])][:20]}


def output_hashes(output_dir: Path) -> dict[str, str | None]:
    return {name: sha256_file(output_dir / name) if (output_dir / name).is_file() else None for name in PRIMARY_OUTPUTS}


class Smoke:
    def __init__(self, source: Path, install_root: Path, work_root: Path, voice_id: str | None, timeout: float) -> None:
        self.source, self.install_root, self.work_root = source, install_root, work_root
        self.voice_id, self.timeout = voice_id, timeout
        self.redact = Redactor(source=source, install=install_root, work=work_root, repository=REPO_ROOT, home=Path.home())
        self.stages: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, passed: bool, detail: Any = None) -> bool:
        self.checks.append({"name": name, "passed": bool(passed), "detail": self.redact(detail)})
        return bool(passed)

    def run_job(self, name: str, version: Path, job_id: str, output_dir: Path) -> dict[str, Any]:
        command = supervisor_command(version, self.install_root, job_id, self.source, output_dir, self.voice_id)
        stage = {"name": name, "job_id": job_id, "command": self.redact(command), "started_at_utc": utc_now()}
        began = time.monotonic()
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=self.timeout)
        stage.update(ended_at_utc=utc_now(), seconds=round(time.monotonic() - began, 1), exit_code=completed.returncode)
        status_path = self.install_root / "control" / "jobs" / f"{job_id}.json"
        document = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
        status = document.get("status", {})
        stage.update(state=status.get("state"), reason=status.get("reason"),
                     stderr_tail=self.redact(completed.stderr.strip()[-600:]) if completed.returncode else "")
        self.stages.append(stage)
        return stage

    def verify_output(self, prefix: str, output_dir: Path, version: Path) -> dict[str, Any]:
        manifest = json.loads((output_dir / "job_manifest.json").read_text(encoding="utf-8"))
        translation = json.loads((output_dir / ".dubflow-work" / "translation.json").read_text(encoding="utf-8"))
        cues = translation.get("cues", [])
        chinese = sum(1 for cue in cues if CJK.search(cue.get("source_text", "")))
        vietnamese = sum(1 for cue in cues if VIETNAMESE.search(cue.get("translated_text", "")))
        self.check(f"{prefix}: Chinese transcript", len(cues) > 0 and chinese >= 0.8 * len(cues), {"cues": len(cues), "with_cjk": chinese})
        self.check(f"{prefix}: Vietnamese translation", vietnamese >= max(1, len(cues) // 2), {"with_vietnamese_marks": vietnamese})
        language = manifest.get("language", {})
        self.check(f"{prefix}: explicit Chinese routing", language.get("source_language") == "zh"
                   and language.get("language_authority") == "requested", language)
        provenance = manifest.get("translation", {})
        self.check(f"{prefix}: translation provenance", bool(provenance.get("backend")) and bool(provenance.get("packages")),
                   {"backend": provenance.get("backend"), "route": provenance.get("backend_route"),
                    "packages": [package.get("id") for package in provenance.get("packages", [])]})
        self.check(f"{prefix}: source linkage", manifest.get("source", {}).get("path") in {str(self.source), self.source.as_posix()},
                   {"source_path_matches": manifest.get("source", {}).get("path") in {str(self.source), self.source.as_posix()}})
        mismatched = []
        for name, artifact in manifest.get("artifacts", {}).items():
            path = Path(artifact["path"])
            if not path.is_file() or "sha256:" + sha256_file(path) != artifact["sha256"]:
                mismatched.append(name)
        self.check(f"{prefix}: manifest artifact hashes", not mismatched and bool(manifest.get("artifacts")),
                   {"artifacts": len(manifest.get("artifacts", {})), "mismatched": mismatched})
        for required in ("captions_vi.srt", "captions_vi.ass", "editable/timeline.json"):
            self.check(f"{prefix}: {required} present", (output_dir / required).is_file() and (output_dir / required).stat().st_size > 0)
        qc = json.loads((output_dir / "qc_report.json").read_text(encoding="utf-8"))
        self.check(f"{prefix}: QC passed", qc.get("status") == "passed", {"status": qc.get("status"), "warnings": len(qc.get("warnings", []))})
        video = probe(version, output_dir / "final_vi.mp4")
        kinds = {stream.get("codec_type") for stream in video["streams"]}
        self.check(f"{prefix}: playable output", {"video", "audio"} <= kinds and video["duration_s"] > 0, video)
        return {"manifest": manifest, "cues": cues, "probe": video,
                "models": {"asr_model_binding": manifest.get("asr", {}).get("chunk_binding", {}).get("model_binding"),
                           "asr_recipe": manifest.get("asr", {}).get("chunk_binding", {}).get("recipe"),
                           "translation_packages": [{key: package.get(key) for key in ("id", "package_version", "sha256")}
                                                    for package in provenance.get("packages", [])],
                           "production_profile": manifest.get("production_profile"),
                           "dubbing_voice": manifest.get("dubbing", {}).get("voice_id") if isinstance(manifest.get("dubbing"), dict) else None}}

    def run(self) -> dict[str, Any]:
        report: dict[str, Any] = {"schema": REPORT_SCHEMA, "started_at_utc": utc_now(), "real_media": True,
                                  "gui": "NOT_RUN: the desktop GUI was not driven; the host review_cues/save_review file contract was replayed",
                                  "stages": self.stages, "checks": self.checks}
        version = version_root(self.install_root)
        release = json.loads((version / "release-manifest.json").read_text(encoding="utf-8"))
        report["runtime"] = {key: release.get(key) for key in ("version", "source_sha", "release_channel")}
        report["runtime"]["signature"] = release.get("security", {}).get("algorithm")
        report["source"] = {"sha256": sha256_file(self.source), "size_bytes": self.source.stat().st_size,
                            "suffix": self.source.suffix.casefold(), "sidecars": 0, "probe": probe(version, self.source)}
        output_dir = self.work_root / f"run-{secrets.token_hex(4)}"
        output_dir.mkdir(parents=True)
        first = self.run_job("production", version, f"smoke271-{secrets.token_hex(8)}", output_dir)
        if not self.check("production: job completed", first["exit_code"] == 0 and first["state"] == "COMPLETED",
                          {"exit_code": first["exit_code"], "state": first["state"], "reason": first["reason"]}):
            return self.finish(report)
        original = self.verify_output("production", output_dir, version)
        report["models"] = original["models"]
        report["dubbing"] = dubbing_summary(output_dir, first["reason"])
        report["output_probe"] = original["probe"]
        if not all(check["passed"] for check in self.checks):
            return self.finish(report)

        # Review: open cues, try an invalid save (must change nothing), then save one real edit.
        cues = review_cues(output_dir)
        before = output_hashes(output_dir)
        target = cues[0]
        try:
            save_review(output_dir, {target["cue_id"]: "   "})
            rejected = False
        except SmokeError:
            rejected = True
        self.check("review: invalid save rejected without changes",
                   rejected and revision_dir(output_dir) == output_dir.parent / f"{output_dir.name} (sửa 1)"
                   and not (output_dir / OVERRIDES_FILE).exists()
                   and output_hashes(output_dir) == before and not any(path.name.endswith(".partial") for path in output_dir.parent.iterdir()))
        edited_text = target["machine_text"].rstrip(" .") + " (đã duyệt)"
        returned = save_review(output_dir, {target["cue_id"]: edited_text})
        reopened = {cue["cue_id"]: cue for cue in review_cues(returned)}
        self.check("review: retried save persisted the edit", reopened[target["cue_id"]]["text"] == edited_text
                   and reopened[target["cue_id"]]["edited"] is True, {"cue_id": target["cue_id"]})
        report["review"] = {"cues": len(cues), "edited_cue": target["cue_id"], "save_review_returned_same_folder": returned == output_dir}

        second = self.run_job("revision", version, f"smoke271-{secrets.token_hex(8)}", returned)
        if not self.check("revision: job completed", second["exit_code"] == 0 and second["state"] == "COMPLETED",
                          {"exit_code": second["exit_code"], "state": second["state"], "reason": second["reason"]}):
            return self.finish(report)
        revised = self.verify_output("revision", returned, version)
        after = output_hashes(output_dir)
        srt = (returned / "captions_vi.srt").read_text(encoding="utf-8")
        self.check("revision: edited text in new captions", edited_text in srt)
        self.check("revision: provenance records reviewed overrides",
                   bool(revised["manifest"].get("translation", {}).get("reviewed_overrides")))
        self.check("revision: output separate from the original", returned != output_dir,
                   {"revision_folder_is_original_folder": returned == output_dir})
        preserved = [name for name in PRIMARY_OUTPUTS if before[name] is not None and before[name] == after[name]]
        replaced = [name for name in PRIMARY_OUTPUTS if before[name] is not None and before[name] != after[name]]
        self.check("revision: original validated output still available", not replaced and len(preserved) == len(PRIMARY_OUTPUTS),
                   {"unchanged": preserved, "replaced_in_place": replaced})
        link_path = returned / REVISION_OF_FILE
        link = json.loads(link_path.read_text(encoding="utf-8")) if link_path.is_file() else {}
        self.check("revision: links back to the original output",
                   Path(link.get("original_output_dir", "")) == output_dir
                   and link.get("original_manifest_sha256") == "sha256:" + str(before["job_manifest.json"]),
                   {"revision_of": bool(link)})
        return self.finish(report)

    def finish(self, report: dict[str, Any]) -> dict[str, Any]:
        report["ended_at_utc"] = utc_now()
        report["verdict"] = "pass" if self.checks and all(check["passed"] for check in self.checks) else "fail"
        return self.redact(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True, help="authorized real Chinese video, outside the repository")
    parser.add_argument("--install-root", type=Path, required=True, help="installed DubFlow root (the folder holding current.json)")
    parser.add_argument("--work-root", type=Path, required=True, help="empty folder for outputs, outside the repository")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--tts-voice-id", help="enable Vietnamese dubbing with this voice")
    parser.add_argument("--timeout", type=float, default=3 * 3600)
    args = parser.parse_args(argv)
    try:
        source = validate_source(args.source)
        for path in (args.install_root, args.work_root):
            if not path.is_absolute() or path.resolve().is_relative_to(REPO_ROOT):
                raise SmokeError("PATH_INVALID", "install and work roots must be absolute and outside the repository")
        report = Smoke(source, args.install_root.resolve(), args.work_root.resolve(), args.tts_voice_id, args.timeout).run()
    except SmokeError as error:
        report = {"schema": REPORT_SCHEMA, "verdict": "error", "error": error.code, "real_media": False}
        print(json.dumps(report, ensure_ascii=False), file=sys.stderr)
        return 2
    problems = validate_report(report)
    if problems:
        report["report_problems"] = problems
        report["verdict"] = "error"
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "failed": [check["name"] for check in report["checks"] if not check["passed"]]},
                     ensure_ascii=False))
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
