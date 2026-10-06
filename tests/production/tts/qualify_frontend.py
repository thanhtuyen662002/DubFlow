"""Diagnose original Mimic3 token encoding; never approve production quality.

This deliberately incomplete experiment preserves compound IPA symbols and
word blanks. Clause punctuation and eSpeak language-switch markers need a
proper frontend before promotion. Unknown symbols are reported, not hidden.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import time
import unicodedata
import wave

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from engine.dubflow.tts.neural_vits import RUNTIME_VERSION, load_neural_voice
from tests.production.tts.qualify_native import CASES, cer

INVENTORY_SHA256 = "1070c88fd8459f584d3cc41a5d3d9bdf6161d545cfb532615fa64ab78b8869c0"


def original_inventory(path: Path) -> dict[str, int]:
    tokens = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        token, index = line.rsplit(" ", 1)
        if token.strip():
            tokens[token] = int(index)
    tokens["t\u032a"] = 30
    inventory = "".join(f"{index} {token}\n" for token, index in sorted(tokens.items(), key=lambda item: item[1])).encode()
    if len(inventory) != 281 or hashlib.sha256(inventory).hexdigest() != INVENTORY_SHA256:
        raise RuntimeError("reconstructed inventory differs from upstream metadata")
    return tokens


def diagnostic_ids(phrases: list[str], tokens: dict[str, int]) -> tuple[list[int], list[str]]:
    words = []
    for phrase in phrases:
        for word in phrase.split():
            chars: list[str] = []
            for char in word.replace("_", ""):
                if unicodedata.combining(char) and chars:
                    chars[-1] += char
                else:
                    chars.append(char)
            words.append(chars)
    # Approximation only: the original keeps clause-specific punctuation.
    words.append(["."])
    ids = [tokens["^"], tokens["_"]]
    unknown = []
    for word in words:
        word_ids = []
        for phoneme in word:
            if phoneme in tokens:
                word_ids.append(tokens[phoneme])
            else:
                unknown.append(phoneme)
        if word_ids:
            word_ids.append(tokens["#"])
            for index in word_ids:
                ids.extend((index, tokens["_"]))
    ids.append(tokens["$"])
    return ids, unknown


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.is_relative_to(ROOT):
        parser.error("diagnostic media must remain outside the repository")
    output.mkdir(parents=True, exist_ok=True)
    import numpy as np
    import onnxruntime as ort
    import sherpa_onnx
    from faster_whisper import WhisperModel
    for name, expected in (("sherpa-onnx", RUNTIME_VERSION), ("sherpa-onnx-core", RUNTIME_VERSION), ("onnxruntime", "1.30.0"), ("av", "16.1.0"), ("numpy", "2.2.6")):
        if importlib.metadata.version(name) != expected:
            raise RuntimeError(f"diagnostic requires {name}=={expected}")
    pack, _voice = load_neural_voice(ROOT, args.model_root, ROOT / "models/manifests/production-cpu-v1.json")
    tokens = original_inventory(pack.path / "tokens.txt")
    # Windows-only diagnosis using the already app-owned pinned wheel DLL.
    library = ctypes.CDLL(str(Path(sherpa_onnx.__file__).parent / "lib/sherpa-onnx-c-api.dll"))
    library.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    library.espeak_Initialize.restype = ctypes.c_int
    library.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
    library.espeak_SetVoiceByName.restype = ctypes.c_int
    library.espeak_TextToPhonemes.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int, ctypes.c_int]
    library.espeak_TextToPhonemes.restype = ctypes.c_char_p
    if library.espeak_Initialize(2, 0, str(pack.path).encode(), 0) != pack.sample_rate or library.espeak_SetVoiceByName(b"vi") != 0:
        raise RuntimeError("pinned native phonemizer initialization failed")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(pack.path / "vi_VN-vais1000_low.onnx"), options, providers=["CPUExecutionProvider"])
    asr = WhisperModel(str(args.model_root / "asr/faster-whisper-small"), device="cpu", compute_type="int8", cpu_threads=4)
    rows = []
    for case, group, text in CASES:
        buffer = ctypes.create_string_buffer(text.encode())
        pointer = ctypes.c_void_p(ctypes.addressof(buffer))
        phrases = []
        for _ in range(32):
            if not pointer.value:
                break
            previous = pointer.value
            phonemes = library.espeak_TextToPhonemes(ctypes.byref(pointer), 1, 2 | (ord("_") << 8))
            if phonemes:
                phrases.append(phonemes.decode())
            if pointer.value == previous:
                raise RuntimeError("native phonemizer made no progress")
        if pointer.value:
            raise RuntimeError("native phonemizer exceeded clause budget")
        ids, unknown = diagnostic_ids(phrases, tokens)
        start = time.perf_counter()
        audio = session.run(None, {"input": np.array([ids], dtype=np.int64), "input_lengths": np.array([len(ids)], dtype=np.int64), "scales": np.array([0.0, 1.0, 0.0], dtype=np.float32)})[0].reshape(-1)
        if not 0 < len(audio) <= 16 * 1024 * 1024 // 2 or not np.isfinite(audio).all():
            raise RuntimeError("native diagnostic returned invalid or oversized audio")
        pcm = (np.clip(audio, -0.94, 0.94) * 32767).round().astype("<i2")
        path = output / f"{case}.wav"
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(pack.sample_rate)
            writer.writeframes(pcm.tobytes())
        segments, _info = asr.transcribe(str(path), language="vi", beam_size=5, vad_filter=True, condition_on_previous_text=False)
        recognized = " ".join(segment.text.strip() for segment in segments)
        row = {"case": case, "group": group, "reference": text, "recognized": recognized, "cer": cer(text, recognized), "phonemes": phrases, "unknown": unknown, "frames": len(audio), "seconds": round(time.perf_counter() - start, 3), "wav_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    summaries = {group: sum(row["cer"] for row in rows if row["group"] == group) / sum(row["group"] == group for row in rows) for group in ("common", "foreign_name")}
    report = {"schema_version": 1, "production_qualified": False, "scope": "Windows development diagnostic; original encoder approximation, punctuation and language markers incomplete", "completed_at_utc": datetime.now(timezone.utc).isoformat(), "phoneme_inventory_sha256": INVENTORY_SHA256, "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "model_hash": pack.model_hash, "manifest_hash": pack.manifest_hash, "summaries": summaries, "cases": rows}
    (output / "quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summaries": summaries}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
