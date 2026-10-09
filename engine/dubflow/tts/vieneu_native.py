"""Private offline CPU entrypoint for pinned VieNeu v3 Turbo preset speech.

Only reviewed, installed Python modules are executed; model roots contain data.
The existing bounded protocol and OS process containment are reused unchanged.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys

FRONTEND_ID = "vieneu-sea-g2p-preset-v1"
VERSIONS = {"vieneu": "3.8.3", "sea-g2p": "0.9.1", "onnxruntime": "1.30.0", "numpy": "2.2.6", "tokenizers": "0.23.2"}
INFERENCE_RECIPE = {"seed": 20261007, "threads": 2, "max_new_frames": 300, "temperature": 0.8, "top_k": 25, "top_p": 0.95, "repetition_penalty": 1.2, "babble_retries": 0, "precision": "fp32", "duration_fit": "app-owned-ffmpeg-atempo-max1.3-measured3-pad5ms"}
SOURCE_FILES = {
    "vieneu/_v3_turbo_engine/onnx_runtime_lite.py": "7747ac18fb5b660a810a434461bd8386ba40b0b46d559c07907db0c19414084e",
    "vieneu/_v3_turbo_engine/rep_history.py": "2cfc52f9a860fb53450e5b3b364fa5955fba03acf558195c423b476535104a2b",
    "vieneu_utils/core_utils.py": "baff0fac1e3dfe38ff77e5401b289c5de17bcdc3b385640844e1846f45728bf9",
    "vieneu_utils/phonemize_text.py": "ce3c0ab79b1232cb9a62dc8360aedbdd7646318f46d59036603615a957dfe6a7",
}
MAX_FRAMES = 48_000 * 32


class NativeCueRejected(ValueError):
    """A bounded content failure, independent of the next cue's fresh decode."""
    def __init__(self, code: str, condition: str):
        if code not in {"TTS_SPEECH_INCOMPLETE", "TTS_TEXT_UNSUPPORTED"}:
            raise ValueError("unknown native cue refusal")
        self.code = code
        super().__init__(condition)


def _offline_environment():
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    # Fail any accidental SDK network access, including non-HF clients.
    import socket
    def denied(*args, **kwargs):
        raise RuntimeError("network is disabled in native TTS inference")
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied


class NativeModel:
    def __init__(self, config: dict):
        for name, version in VERSIONS.items():
            if importlib.metadata.version(name) != version:
                raise ValueError("native runtime version mismatch: " + name)
        package = importlib.metadata.distribution("vieneu")
        for relative, expected in SOURCE_FILES.items():
            path = Path(package.locate_file(relative))
            if hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != expected:
                raise ValueError("native SDK source mismatch: " + relative)
        _offline_environment()
        import numpy as np
        from vieneu._v3_turbo_engine.onnx_runtime_lite import OnnxV3LiteEngine
        from vieneu_utils.phonemize_text import phonemize_text
        from vieneu_utils.core_utils import strip_encoder_pad_frame
        self.np = np
        self.phonemize = phonemize_text
        self.pack = Path(config["pack_path"])
        self.output = Path(config["output_root"])
        self.ffmpeg = config.get("ffmpeg_path")
        self.voice_name = config["voice_name"]
        with (self.pack / "voices.json").open("rb") as f:
            payload = f.read(256 * 1024 + 1)
        if len(payload) > 256 * 1024:
            raise ValueError("preset metadata exceeds its bound")
        preset = json.loads(payload)["presets"][self.voice_name]
        self.speaker = np.asarray(preset["speaker_emb"], dtype=np.float32)
        self.codes = strip_encoder_pad_frame(np.asarray(preset["codes"], dtype=np.int64))
        if self.speaker.shape != (192,) or not np.isfinite(self.speaker).all() or self.codes.ndim != 2 or not 1 <= len(self.codes) <= 100 or self.codes.shape[1] != 16 or (self.codes < 0).any() or (self.codes >= 1024).any():
            raise ValueError("invalid bounded preset speaker/reference data")

        class OfflineEngine(OnnxV3LiteEngine):
            def _fetch(self, *args, **kwargs):
                raise RuntimeError("all model files must be provisioned by DubFlow")
            def _load_denoiser(self):
                return None  # Preset synthesis never enrolls/clones new audio.
            def _resolve_root_file(self, *args, **kwargs):
                raise RuntimeError("voice enrollment is not part of this adapter")
            def _acoustic_frame(self, *args, **kwargs):
                codes, eos = super()._acoustic_frame(*args, **kwargs)
                self.ended = bool(eos)
                return codes, eos

        self.engine = OfflineEngine(checkpoint_path=str(self.pack), onnx_dir=str(self.pack / "tts"), codec_dir=str(self.pack / "codec"), threads=2)
        self.engine.babble_retries = 0  # DubFlow owns bounded, condition-changing retries.
        self.last_text = None
        self.last_samples = None

    def generate(self, request: dict) -> dict:
        text, speed = request["text"], request["speed"]
        if type(text) is not str or not text.strip() or len(text) > 512 or isinstance(speed, bool) or not isinstance(speed, (float, int)) or not math.isfinite(speed) or not 1.0 <= speed <= 1.3:
            raise ValueError("invalid bounded native speech request")
        if text != self.last_text:
            phones = self.phonemize(text)
            if not phones or len(phones) > 4096:
                raise NativeCueRejected("TTS_TEXT_UNSUPPORTED", "phoneme sequence exceeds its bound")
            if len(self.engine.tokenizer.encode(phones).ids) > 1024:
                raise NativeCueRejected("TTS_TEXT_UNSUPPORTED", "phoneme token count exceeds the bounded model context")
            # Fixed seed is a reproducible recipe, not a cross-platform bit-exact claim.
            self.np.random.seed(20261007)
            self.engine.ended = False
            samples = self.engine.infer(phonemes=phones, ref_codes=self.codes, speaker_emb=self.speaker, max_new_frames=300, frame_cap=True)
            if not self.engine.ended:
                raise NativeCueRejected("TTS_SPEECH_INCOMPLETE", "speech reached its generation bound before end-of-speech; output rejected")
            samples = self.np.asarray(samples, dtype=self.np.float32).reshape(-1)
            if not 0 < len(samples) <= MAX_FRAMES or not self.np.isfinite(samples).all():
                raise ValueError("native waveform exceeds its finite size bound")
            self.last_text, self.last_samples = text, samples
        samples = self.last_samples
        if speed != 1.0:
            if not self.ffmpeg or not Path(self.ffmpeg).is_absolute():
                raise ValueError("duration fitting requires app-owned FFmpeg")
            source, target = self.output / "tempo-source.f32", self.output / "tempo-fit.f32"
            try:
                source.write_bytes(samples.astype("<f4").tobytes())
                completed = subprocess.run([self.ffmpeg, "-nostdin", "-v", "error", "-y", "-f", "f32le", "-ar", "48000", "-ac", "1", "-i", str(source), "-af", "atempo=" + str(speed), "-f", "f32le", str(target)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                if completed.returncode or not target.is_file() or target.stat().st_size > MAX_FRAMES * 4:
                    raise ValueError("app-owned speech tempo fit failed")
                samples = self.np.fromfile(target, dtype="<f4")
            finally:
                source.unlink(missing_ok=True)
                target.unlink(missing_ok=True)
        name = "samples-" + str(request["sequence"]) + ".f32"
        payload = samples.astype("<f4").tobytes()
        (self.output / name).write_bytes(payload)
        return {"file": name, "frames": len(samples), "sample_rate": 48000, "sha256": hashlib.sha256(payload).hexdigest(), "unknown": []}


def main():
    spec = importlib.util.spec_from_file_location("dubflow_native_protocol", Path(__file__).with_name("mimic3_native.py"))
    protocol = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(protocol)
    protocol.NativeModel = NativeModel
    protocol.NativeCueRejected = NativeCueRejected
    protocol.FRONTEND_ID = FRONTEND_ID
    protocol.main()


if __name__ == "__main__":
    main()
