"""Run real CPU voice/back-ASR diagnostics outside the hermetic PR lane.

The report describes actual inference. It never declares release approval.
Model artifacts remain checksum verified; generated media stays outside Git.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unicodedata
import wave

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.tts.adapter import TtsConfig, TtsInput, TtsRequest
from engine.dubflow.tts.neural_vits import ENGINE_ID, NeuralVietnameseTtsEngine, load_neural_voice

CASES = (
    ("greeting", "common", "Xin chào Việt Nam."),
    ("video", "common", "Hôm nay chúng ta cùng xem một video mới."),
    ("question", "common", "Bạn có khỏe không? Tôi rất vui được gặp bạn."),
    ("dialogue", "common", "Tôi không biết. Bạn có thể nói lại được không?"),
    ("tone", "common", "Mẹ đang đi chợ, còn bố đang ở nhà."),
    ("instructions", "common", "Hãy mở ứng dụng và chọn video cần xử lý."),
    ("thanks", "common", "Cảm ơn bạn đã theo dõi. Hẹn gặp lại vào ngày mai."),
    ("foreign_name", "foreign_name", "Xin chào, đây là giọng nói tiếng Việt thật của DubFlow."),
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def normalize(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", text.casefold()) if char.isalnum())


def cer(reference: str, recognized: str) -> float:
    a, b = normalize(reference), normalize(recognized)
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1] / max(1, len(a))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--compare-upstream-recipe", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.is_relative_to(ROOT):
        parser.error("generated audio must be written outside the repository")
    output.mkdir(parents=True, exist_ok=True)
    from faster_whisper import WhisperModel
    import importlib.metadata
    if importlib.metadata.version("av") != "16.1.0":
        raise RuntimeError("qualification requires the packaged PyAV 16.1.0 decoder")
    model = WhisperModel(str(args.model_root / "asr/faster-whisper-small"), device="cpu", compute_type="int8", cpu_threads=4)
    # Preserve reproduction of the rejected historic model after the default changes.
    with tempfile.TemporaryDirectory() as directory:
        selector = Path(directory) / "legacy.json"
        selector.write_text(json.dumps({"tts_neural_profile": "models/manifests/production-tts-v1.json"}))
        pack, voice = load_neural_voice(ROOT, args.model_root, selector)
    recipes = [("configured", pack.noise_scale, pack.noise_scale_w)]
    if args.compare_upstream_recipe:
        recipes.append(("upstream_experiment", 0.667, 0.8))
    rows = []
    config = TtsConfig(sample_rate=pack.sample_rate, requested_profile="cpu", max_attempts=1, max_text_chars=512)
    for name, noise, noise_w in recipes:
        engine = NeuralVietnameseTtsEngine(replace(pack, noise_scale=noise, noise_scale_w=noise_w))
        health = engine.healthcheck(voice)
        if not health.ready:
            raise RuntimeError(f"{health.code}: {health.condition}")
        for case_id, group, text in CASES:
            start = time.perf_counter()
            segment = TtsInput(case_id, case_id, text, TimePoint(0, TimeBase(1, 1000)), TimePoint(12000, TimeBase(1, 1000)))
            request = TtsRequest(name + "-" + case_id, segment, voice, config, "sha256:" + hashlib.sha256(text.encode()).hexdigest(), 0, 12 * pack.sample_rate)
            result = engine.synthesize(request)
            audio_path = output / f"{name}-{case_id}.wav"
            audio_path.write_bytes(result.audio_bytes)
            segments, _info = model.transcribe(str(audio_path), language="vi", beam_size=5, vad_filter=True, condition_on_previous_text=False)
            recognized = " ".join(item.text.strip() for item in segments)
            with wave.open(str(audio_path)) as reader:
                frame_count = reader.getnframes()
            row = {"case": case_id, "group": group, "recipe": name, "noise_scale": noise, "noise_scale_w": noise_w, "reference": text, "recognized": recognized, "cer": cer(text, recognized), "wav_sha256": digest(audio_path), "frames": frame_count, "sample_rate": result.sample_rate, "fit_mode": result.fit_mode, "warnings": list(result.warnings), "seconds": round(time.perf_counter() - start, 3)}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    summaries = []
    for name, _noise, _noise_w in recipes:
        for group in ("common", "foreign_name"):
            values = [row["cer"] for row in rows if row["recipe"] == name and row["group"] == group]
            summaries.append({"recipe": name, "group": group, "cases": len(values), "mean_cer": sum(values) / len(values)})
    source_paths = ("engine/dubflow/tts/neural_vits.py", "engine/dubflow/tts/mimic3_native.py", "engine/dubflow/tts/native_process.py", "engine/dubflow/tts/windows_job.py", "models/manifests/production-tts-v1.json", "packaging/runtime/requirements-windows-x64.txt", "tests/production/tts/qualify_native.py")
    report = {"schema_version": 1, "scope": "local native CPU diagnostics; not packaged Windows or release approval", "production_qualified": False, "completed_at_utc": datetime.now(timezone.utc).isoformat(), "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "source_sha256": {path: digest(ROOT / path) for path in source_paths}, "backend": ENGINE_ID, "model_hash": pack.model_hash, "manifest_hash": pack.manifest_hash, "decoder": "av-16.1.0", "asr": "faster-whisper-small-int8", "summaries": summaries, "cases": rows}
    path = output / "native-tts-quality.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(path), "summaries": summaries}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
