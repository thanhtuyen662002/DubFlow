"""Execute streaming PCM in a verified owned bundle or a scoped development rehearsal.

The generated audio capacity test is not real video/ASR/TTS/GUI qualification.
Reports and media must be outside the release tree and normal Git history.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import wave


def peak_memory():
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                *[(name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]]
        counters = Counters(); counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.PeakWorkingSetSize
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)


def child(args):
    # -I -S excludes environment/user site/custom startup. Only these two
    # explicit roots are added after the parent verifies the entire bundle.
    sys.path[:0] = [str(args.app_root), str(args.site_root)]
    import numpy as np
    from engine.dubflow.asr import TimeBase, TimePoint
    from engine.dubflow.mix import FileSource, FileSegment, StreamingAudioMixer, MixConfig, ResourceProfile
    from engine.dubflow.mix.streaming import file_hash
    if np.__version__ != "2.2.6": raise RuntimeError("numeric runtime pin differs")
    import engine.dubflow.mix.streaming as module
    adapter_digest = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
    if args.root:
        owned = args.root.resolve()
        if Path(sys.prefix).resolve() != owned / "runtime" or Path(sys.base_prefix).resolve() != owned / "runtime":
            raise RuntimeError("interpreter prefix is not owned")
        for path in (Path(np.__file__), Path(module.__file__)):
            path.resolve().relative_to(owned)
        for path in sys.path:
            if path: Path(path).resolve().relative_to(owned)
    work = args.work.resolve(); work.mkdir(parents=True, exist_ok=True)
    frames = args.seconds * 48000
    source_path = work / "source.wav"; tts_path = work / "speech.wav"
    before = shutil.disk_usage(work).free
    needed = frames * args.channels * 2 * 6 + 512 * 1024 * 1024
    if not source_path.exists() and before < needed: raise RuntimeError("not enough disk for generated PCM capacity rehearsal")
    if not source_path.exists():
        # Full non-zero PCM writes, not sparse files or faked frame counts.
        block = np.array([[1000, -2000][:args.channels]] * 262144, dtype="<i2")
        with wave.open(str(source_path), "wb") as writer:
            writer.setnchannels(args.channels); writer.setsampwidth(2); writer.setframerate(48000)
            for offset in range(0, frames, len(block)):
                writer.writeframesraw(block[:min(len(block), frames - offset)].tobytes())
    if not tts_path.exists():
        with wave.open(str(tts_path), "wb") as writer:
            writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(16000)
            writer.writeframes(np.full(16000, 5000, dtype="<i2").tobytes())
    base = TimeBase(1, 48000); point = lambda tick: TimePoint(tick, base)
    source = FileSource(source_path, point(-48000), point(frames - 48000), "synthetic-capacity", "mono" if args.channels == 1 else "stereo")
    pinned_source, pinned_tts = file_hash(source_path), file_hash(tts_path)
    receipt = work / "inputs.json"
    pins = {"source": pinned_source, "tts": pinned_tts, "frames": frames, "channels": args.channels}
    if receipt.exists() and json.loads(receipt.read_text()) != pins: raise RuntimeError("rehearsal input identity changed")
    receipt.write_text(json.dumps(pins, sort_keys=True), encoding="utf-8")
    stride = max(2, args.seconds // 50)
    segments = [FileSegment("speech-" + str(second), "u-" + str(second), point(second * 48000 - 48000),
                point((second + 1) * 48000 - 48000), tts_path, pinned_tts)
                for second in range(1, args.seconds - 1, stride)]
    events = []
    def checkpoint(phase, done, total, digest):
        if len(events) < 16: events.append({"phase": phase, "done": done, "total": total, "hash": digest})
        if args.kill and phase == "combined" and done == 2:
            (work / "hard-kill.json").write_text(json.dumps({"phase": phase, "done": done, "total": total,
                "hash": digest, "peak_memory_bytes": peak_memory(), "pid": os.getpid()}), encoding="utf-8")
            os._exit(42)
    started = time.monotonic()
    result = StreamingAudioMixer(output_dir=work / "mix", config=MixConfig(resource=ResourceProfile(max_memory_mb=512)),
        checkpoint=checkpoint).mix(source, segments, input_hash="sha256:" + hashlib.sha256(json.dumps(pins, sort_keys=True).encode()).hexdigest())
    if result.original_audio.content_hash != pinned_source or result.failures: raise RuntimeError("source preservation/segment qualification failed")
    for artifact in (result.original_audio, result.dialogue_stem, result.final_mix):
        if artifact.frame_count != frames or artifact.content_hash != file_hash(Path(artifact.path)):
            raise RuntimeError("artifact frame/hash qualification failed")
    if result.final_mix.metrics.clipped_samples: raise RuntimeError("generated mix clipped")
    memory = peak_memory()
    if memory > 512 * 1024 * 1024: raise RuntimeError("measured PCM process exceeds 512MiB qualification budget")
    result_path = work / "child-report.json"
    report = {"schema_version": 1, "scope": "generated-streaming-pcm", "seconds": args.seconds, "channels": args.channels,
        "source_frames": frames, "source_bytes": source_path.stat().st_size, "elapsed_seconds": round(time.monotonic() - started, 3),
        "peak_memory_bytes": memory, "disk_payload_bytes": sum(path.stat().st_size for path in work.rglob("*") if path.is_file()),
        "free_disk_before_bytes": before, "free_disk_after_bytes": shutil.disk_usage(work).free,
        "numpy_version": np.__version__, "python": sys.executable, "python_prefix": sys.prefix,
        "adapter_path": str(Path(module.__file__)), "numpy_path": str(Path(np.__file__)),
        "adapter_sha256": adapter_digest,
        "isolated": bool(sys.flags.isolated), "no_site": bool(sys.flags.no_site), "events": events,
        "mix": result.to_dict(), "real_media_asr_tts_gui": "not_run", "production_qualified": False}
    result_path.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")
    if hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() != adapter_digest:
        raise RuntimeError("adapter source changed while rehearsal was running")
    print(json.dumps({"report": str(result_path), "peak_memory_bytes": memory, "frames": frames}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--expected-source-sha")
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--seconds", type=int, default=12)
    parser.add_argument("--channels", type=int, choices=(1, 2), default=2)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--kill", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--app-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--site-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 6 <= args.seconds <= 21600: parser.error("seconds must be in [6,21600]")
    if args.child: return child(args)
    if args.report is None: parser.error("report is required")
    repository = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository))
    manifest_digest = None
    if args.development:
        if args.root or args.expected_source_sha: parser.error("development rehearsal has no release identity")
        python = Path(sys.executable); app = repository
        import site
        packages = [Path(value) for value in site.getsitepackages() if Path(value).is_dir()]
        site_root = next(value for value in packages if (value / "numpy").exists())
    else:
        if not args.root or not args.expected_source_sha or not re.fullmatch(r"[0-9a-f]{40}", args.expected_source_sha): parser.error("bundle root and exact source SHA required")
        from packaging.release.bootstrap import verify_bundle
        from packaging.release.manifest import ReleaseManifest
        root = args.root.resolve()
        for path in (args.work.resolve(), args.report.resolve()):
            try: path.relative_to(root)
            except ValueError: pass
            else: raise ValueError("qualification work/report must be outside verified bundle")
        with (root / "release-manifest.json").open("rb") as handle: raw = handle.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024: raise ValueError("manifest exceeds bound")
        manifest = ReleaseManifest.from_mapping(json.loads(raw))
        if manifest.source_sha != args.expected_source_sha: raise ValueError("bundle source SHA differs")
        verify_bundle(root, manifest)
        manifest_digest = hashlib.sha256(raw).hexdigest()
        python, app, site_root = root / "runtime/python.exe", root / "app", root / "runtime/Lib/site-packages"
    args.work.mkdir(parents=True, exist_ok=True)
    command = [str(python), "-I", "-S", "-B", str(Path(__file__).resolve()), "--child", "--app-root", str(app),
        "--site-root", str(site_root), "--work", str(args.work.resolve()), "--seconds", str(args.seconds), "--channels", str(args.channels)]
    if args.root: command += ["--root", str(args.root.resolve())]
    started = time.monotonic()
    for killed in (True, False):
        process = subprocess.run(command + (["--kill"] if killed else []), capture_output=True, text=True, timeout=3600)
        expected = 42 if killed else 0
        if process.returncode != expected: raise RuntimeError(f"PCM child returned {process.returncode}, expected {expected}: {process.stderr[-4000:]}")
    report = json.loads((args.work / "child-report.json").read_text(encoding="utf-8"))
    report.update({"source_sha": args.expected_source_sha, "manifest_sha256": manifest_digest,
        "verified_release_tree": not args.development, "development_rehearsal": args.development,
        "hard_process_exit": json.loads((args.work / "hard-kill.json").read_text()),
        "total_elapsed_seconds": round(time.monotonic() - started, 3), "restart": "passed"})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(args.report.resolve()), "peak_memory_bytes": report["peak_memory_bytes"],
        "frames": report["source_frames"], "restart": "passed", "scope": report["scope"]}))
    return 0


if __name__ == "__main__": raise SystemExit(main())
