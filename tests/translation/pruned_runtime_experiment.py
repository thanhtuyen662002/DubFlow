"""Opt-in real-model comparison; not part of deterministic unit test discovery.

Run with --help. All writes go beneath --work-dir, which must be in this checkout.
The supplied runtimes and source model cache are read-only inputs. Create/prune a
runtime copy first; this runner checks absence and forbids network connections.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = {
    "en": [
        "Hello, how are you?",
        "This video was translated offline.",
        "The train leaves at 3.14. Please wait!",
        "I don't know. Can you help me?",
        'She said, "See you tomorrow."',
        "First paragraph.\nSecond paragraph!",
        "  Thank you very much!  ",
        "We have 12 apples and 2 oranges.",
    ],
    "zh": [
        "你好，你今天怎么样？",
        "这个视频是在本地翻译的。",
        "火车三点出发。请等一下！",
        "我不知道。你能帮我吗？",
        "她说：“明天见。”",
        "第一段。\n第二段！",
        "  非常感谢！  ",
        "我们有十二个苹果和两个橙子。",
    ],
}
HEAVY = ("torch", "stanza", "spacy")


def deny_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        raise AssertionError("offline experiment attempted network: " + event)


def worker(args):
    sys.addaudithook(deny_network)
    # Never put the repository's packaging/ ahead of the installed packaging.
    sys.path.append(str(ROOT))
    from engine.dubflow.translation import argos_runtime as runtime

    if args.mode == "original":
        # Optional pre-change snapshot proves the unmodified adapter as well.
        spec = importlib.util.spec_from_file_location(
            "engine.dubflow.translation._experiment_original", args.snapshot
        )
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
    elif args.mode in {"upstream", "stubs"}:
        if args.mode == "stubs":
            sys.path.append(str(args.work_dir / "stubs"))

        def upstream(pkg, sentencizer, settings):
            from argostranslate import translate
            backend = translate.PackageTranslation(
                translate.Language(pkg.from_code, pkg.from_code),
                translate.Language(pkg.to_code, pkg.to_code), pkg,
            )
            backend.sentencizer = sentencizer
            return backend

        runtime.PinnedPackageTranslation = upstream
    elif args.mode == "chunk":
        # Import the real Argos module with a different chunk setting, in isolation.
        for name in ("DATA", "CONFIG", "CACHE"):
            os.environ["XDG_" + name + "_HOME"] = str(args.work_dir / "chunk" / name)
        os.environ["ARGOS_CHUNK_TYPE"] = args.chunk
        try:
            from argostranslate import translate
        except ModuleNotFoundError as error:
            print(json.dumps({"chunk": args.chunk, "missing_import": error.name}))
            return
        raise AssertionError("unexpected successful import without Stanza")

    absent = {name: importlib.util.find_spec(name) is None for name in HEAVY}
    if args.mode == "direct" and all(absent.values()):
        # A hidden conditional import must fail even if caught by dependency code.
        from importlib.abc import MetaPathFinder

        class NoSBD(MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in HEAVY or fullname in {
                    "argostranslate.translate", "argostranslate.sbd"
                }:
                    raise AssertionError("unexpected import: " + fullname)

        sys.meta_path.insert(0, NoSBD())

    backend = runtime.ArgosRuntime(ROOT, args.work_dir / "models", args.language)
    backend.prepare(args.work_dir / "models")
    hop_outputs = []
    for hop in backend._translators:
        translate_hop = hop.translate

        def record_hop(text, translate_hop=translate_hop):
            value = translate_hop(text)
            hop_outputs.append(value)
            return value

        hop.translate = record_hop
    records = []
    for source in [*SAMPLES[args.language], "", "first\n\nlast"]:
        hop_outputs.clear()
        try:
            output = backend.translate_text(source)
            records.append({"source": source, "output": output,
                            "hop_outputs": list(hop_outputs),
                            "utf8_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest()})
        except runtime.TranslationError as error:
            records.append({"source": source, "error": error.code})
    result = {"language": args.language, "records": records,
              "provenance": backend.provenance(), "absent": absent,
              "loaded_heavy": [n for n in HEAVY if n in sys.modules],
              "translate_loaded": "argostranslate.translate" in sys.modules,
              "executable": sys.executable,
              "runtime_prefix": sys.prefix,
              "module_paths": {name: str(sys.modules[name].__file__) for name in (
                  "argostranslate.package", "argostranslate.settings", "ctranslate2", "sentencepiece")},
              "versions": {n: importlib.metadata.version(n) for n in (
                  "argostranslate", "ctranslate2", "sentencepiece")}}
    if args.mode == "direct":
        assert not result["translate_loaded"] and not result["loaded_heavy"], result
    if args.mode == "stubs":
        import stanza
        import spacy
        for call in (stanza.Pipeline, spacy.load):
            try:
                call()
            except RuntimeError as error:
                assert "disabled" in str(error)
            else:
                raise AssertionError("stub did not fail closed")
        result["stub_guards_verified"] = True
    print(json.dumps(result, ensure_ascii=True))


def main(args):
    args.work_dir.mkdir(parents=True, exist_ok=True)
    profile = json.loads((ROOT / "models/manifests/production-translation-v1.json").read_text())
    for artifact in profile["artifacts"]:
        destination = args.work_dir / "models" / artifact["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(args.model_source / artifact["path"], destination)
    stub_dir = args.work_dir / "stubs"
    stub_dir.mkdir(exist_ok=True)
    (stub_dir / "stanza.py").write_text(
        'def Pipeline(*args, **kwargs):\n    raise RuntimeError("Stanza disabled: use CueSentencizer")\n'
    )
    (stub_dir / "spacy.py").write_text(
        'def load(*args, **kwargs):\n    raise RuntimeError("spaCy disabled: use CueSentencizer")\n'
    )
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
           "PYTHONPATH": "", "PYTHONUTF8": "1"}
    temporary = args.work_dir / "tmp"
    temporary.mkdir(exist_ok=True)
    env.update(TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary))
    results = {}

    def run(executable, mode, language="en", chunk="MINISBD"):
        command = [str(executable), "-B", "-s", str(Path(__file__).resolve()),
                   "--worker", "--mode", mode, "--language", language,
                   "--chunk", chunk, "--work-dir", str(args.work_dir)]
        if args.snapshot:
            command.extend(["--snapshot", str(args.snapshot)])
        completed = subprocess.run(command, env=env, cwd=args.work_dir,
                                   capture_output=True, text=True)
        if completed.returncode:
            raise RuntimeError(f"Worker failed: {command}\n{completed.stdout}\n{completed.stderr}")
        result = json.loads(completed.stdout)
        label = mode + "-" + language + "-" + ("pruned" if executable == args.pruned_runtime else "full")
        if mode == "chunk":
            label += "-" + chunk
        (args.work_dir / (label + ".json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        results[label] = result
        print(label + ": " + str(len(result.get("records", []))) + " cases", flush=True)
        return result

    for language in SAMPLES:
        baseline = run(args.full_runtime, "upstream", language)
        if args.snapshot:
            original = run(args.full_runtime, "original", language)
            assert original["records"] == baseline["records"]
            assert original["provenance"] == baseline["provenance"]
        for executable, mode in [(args.full_runtime, "direct"),
                                 (args.pruned_runtime, "stubs"),
                                 (args.pruned_runtime, "direct")]:
            result = run(executable, mode, language)
            assert result["records"] == baseline["records"], (mode, language)
            assert result["provenance"] == baseline["provenance"], (mode, language)
            if executable == args.pruned_runtime and mode == "direct":
                assert all(result["absent"].values()), result["absent"]
                assert Path(result["runtime_prefix"]).resolve() == executable.parent
                for path in result["module_paths"].values():
                    Path(path).resolve().relative_to(executable.parent)
    for chunk in ("MINISBD", "NONE"):
        result = run(args.pruned_runtime, "chunk", chunk=chunk)
        assert result["missing_import"] == "stanza", result
    summary = {"identical": True, "successful_samples_per_route": 8,
               "matching_error_cases_per_route": 2, "results": results}
    (args.work_dir / "comparison.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS: both routes byte-identical; provenance/errors match; network forbidden.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-runtime", type=Path)
    parser.add_argument("--pruned-runtime", type=Path)
    parser.add_argument("--model-source", type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", default="direct", choices=("direct", "upstream", "original", "stubs", "chunk"))
    parser.add_argument("--language", default="en", choices=SAMPLES)
    parser.add_argument("--chunk", default="MINISBD")
    args = parser.parse_args()
    args.work_dir = args.work_dir.resolve()
    args.work_dir.relative_to(ROOT)
    if args.snapshot:
        args.snapshot = args.snapshot.resolve()
    if args.worker:
        worker(args)
    else:
        if not all((args.full_runtime, args.pruned_runtime, args.model_source)):
            parser.error("full/pruned runtimes and model source are required")
        args.full_runtime = args.full_runtime.resolve()
        args.pruned_runtime = args.pruned_runtime.resolve()
        main(args)
