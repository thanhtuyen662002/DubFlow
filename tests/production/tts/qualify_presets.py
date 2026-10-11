"""Generate every pinned preset offline; waveform availability is not listening approval."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(ROOT))
from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.tts.adapter import TtsConfig, TtsInput, TtsRequest
from engine.dubflow.tts.vieneu import load_vieneu_voice, voice_choices, VieNeuVietnameseTtsEngine


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.is_relative_to(ROOT):
        parser.error("generated media must remain outside Git")
    output.mkdir(parents=True, exist_ok=True)
    profile_path = ROOT / "models/manifests/production-vieneu-v1.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    voices = voice_choices(profile)
    text = "Xin chào Việt Nam, chúc bạn một ngày tốt lành."
    request_hash = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "head_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "working_tree": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True),
        "sources": {p: digest(ROOT / p) for p in ("engine/dubflow/tts/vieneu.py", "engine/dubflow/tts/vieneu_native.py", "engine/dubflow/tts/native_process.py", "models/manifests/production-vieneu-v1.json")},
        "reference": text, "cases": [], "release_qualified": False,
        "limits": ["One phrase per preset", "No human listening/quality approval", "Development runtime, not an installed clean-machine run"],
    }
    roster = json.loads((args.model_root / "tts/packs" / ("vieneu-" + profile["model_tree_sha256"]) / "voices.json").read_text(encoding="utf-8"))["presets"]
    for choice in voices:
        name = choice["name"]
        official = roster[name]
        # Display labels must describe the pinned preset rather than invented personas.
        gender = {"male": "Nam", "female": "Nữ"}[official["gender"]]
        styles = {"tu_nhien": {"tự nhiên"}, "tin_tuc": {"tin tức"}, "doc_truyen": {"kể chuyện", "đọc truyện"}}
        if choice["gender"] != gender or choice["accent"] != official["region"] or choice["style"] not in styles[official["style"]] or choice["description"] != official["description"]:
            raise ValueError("catalog metadata differs from pinned preset: " + name)
        before = time.perf_counter()
        pack, voice = load_vieneu_voice(ROOT, args.model_root, ROOT / "models/manifests/production-cpu-v1.json", voice_id=choice["voice_id"])
        engine = VieNeuVietnameseTtsEngine(pack, ffmpeg_path=args.ffmpeg)
        try:
            segment = TtsInput(voice.voice_id, voice.voice_id, text, TimePoint(0, TimeBase(1, 1000)), TimePoint(8000, TimeBase(1, 1000)))
            result = engine.synthesize(TtsRequest(voice.voice_id, segment, voice, TtsConfig(sample_rate=48000, max_attempts=1), request_hash, 0, 8 * 48000))
            path = output / (voice.voice_id + ".wav")
            path.write_bytes(result.audio_bytes)
            row = {"voice": choice, "voice_hash": voice.content_hash(), "wav_sha256": digest(path), "path": str(path), "seconds": round(time.perf_counter() - before, 3), "warnings": list(result.warnings)}
            report["cases"].append(row)
            print(json.dumps({"voice": name, "status": "generated", "seconds": row["seconds"]}, ensure_ascii=True), flush=True)
        finally:
            engine.close()
            (output / "presets.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if len({row["wav_sha256"] for row in report["cases"]}) != len(voices):
        raise ValueError("presets unexpectedly generated identical PCM")


if __name__ == "__main__":
    main()
