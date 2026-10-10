"""Actual pinned CPU VieNeu + back-ASR diagnostic; no quality approval."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(ROOT))
from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.tts.adapter import TtsConfig, TtsInput, TtsRequest
from engine.dubflow.tts.vieneu import load_vieneu_voice, VieNeuVietnameseTtsEngine, PRODUCER_VERSION, ENGINE_ID, RUNTIME_ID
from qualify_native import CASES, cer, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--asr-model-root", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.is_relative_to(ROOT):
        parser.error("generated media must stay outside Git")
    output.mkdir(parents=True, exist_ok=True)
    pack, voice = load_vieneu_voice(ROOT, args.model_root, ROOT / "models/manifests/production-cpu-v1.json")
    engine = VieNeuVietnameseTtsEngine(pack, ffmpeg_path=args.ffmpeg)
    rows = []
    try:
        config = TtsConfig(sample_rate=48000, requested_profile="cpu", max_attempts=1, max_text_chars=512)
        for identifier, group, text in CASES:
            before = time.perf_counter()
            segment = TtsInput(identifier, identifier, text, TimePoint(0, TimeBase(1, 1000)), TimePoint(12000, TimeBase(1, 1000)))
            request = TtsRequest(identifier, segment, voice, config, "sha256:" + hashlib.sha256(text.encode()).hexdigest(), 0, 12 * 48000)
            result = engine.synthesize(request)
            path = output / (identifier + ".wav")
            path.write_bytes(result.audio_bytes)
            rows.append({"case": identifier, "group": group, "reference": text, "wav_sha256": digest(path), "path": str(path), "inference_s": round(time.perf_counter() - before, 3), "fit_mode": result.fit_mode, "warnings": list(result.warnings)})
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    finally:
        engine.close()
    from faster_whisper import WhisperModel
    model = WhisperModel(str(args.asr_model_root / "asr/faster-whisper-small"), device="cpu", compute_type="int8", cpu_threads=4, local_files_only=True)
    for row in rows:
        segments, _ = model.transcribe(row["path"], language="vi", beam_size=5, vad_filter=True, condition_on_previous_text=False)
        row["recognized"] = " ".join(x.text.strip() for x in segments)
        row["cer"] = cer(row["reference"], row["recognized"])
        print(json.dumps(row, ensure_ascii=False), flush=True)
    sources = ("engine/dubflow/tts/vieneu.py", "engine/dubflow/tts/vieneu_native.py", "engine/dubflow/tts/native_process.py", "engine/dubflow/tts/windows_job.py", "engine/dubflow/tts/neural_vits.py", "models/manifests/production-vieneu-v1.json", "packaging/runtime/requirements-windows-x64.txt")
    report = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "producer_version": PRODUCER_VERSION, "backend": ENGINE_ID, "runtime": RUNTIME_ID, "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "working_tree": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True), "sources": {x: digest(ROOT / x) for x in sources}, "model_hash": pack.model_hash, "manifest_hash": pack.manifest_hash, "voice": voice.to_dict(), "cases": rows,
        "mean_common_cer": sum(x["cer"] for x in rows if x["group"] == "common") / 7,
        "release_qualified": False, "limits": ["Small back-ASR diagnostic is not human listening approval", "Development runtime is not a packaged clean-machine test"]}
    (output / "quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("mean_common_cer", report["mean_common_cer"], flush=True)


if __name__ == "__main__":
    main()
