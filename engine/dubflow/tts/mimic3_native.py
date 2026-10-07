"""Private native process entrypoint for the original Mimic3 VITS frontend.

This file is executed with the app-owned interpreter in isolated mode. It
does not import the application package, mutate SQLite, or execute model code.
The token/blank algorithm follows MIT phonemes2ids; data is verified against
the original Mycroft voice inventory digest before native inference begins.
"""
from __future__ import annotations

import ctypes
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from queue import Queue
import re
import sys
from threading import Thread
import unicodedata

INVENTORY_SHA256 = "1070c88fd8459f584d3cc41a5d3d9bdf6161d545cfb532615fa64ab78b8869c0"
FRONTEND_ID = "mimic3-word-blanks-v1"
ONNX_RUNTIME_VERSION = "1.30.0"
MAX_FRAMES = 8 * 1024 * 1024
LANGUAGE_MARKER = re.compile(r"\([a-z]{2,3}(?:-[a-z0-9]+)*\)")


def original_inventory(path: Path) -> dict[str, int]:
    with path.open("r", encoding="utf-8") as stream:
        payload = stream.read(4097)
    if len(payload) > 4096:
        raise ValueError("phoneme inventory exceeds its bound")
    tokens = {}
    for line in payload.splitlines():
        token, index = line.rsplit(" ", 1)
        if token.strip():
            tokens[token] = int(index)
    # The converter omits this compound symbol; reconstruction is authenticated.
    tokens["t\u032a"] = 30
    inventory = "".join(f"{index} {token}\n" for token, index in sorted(tokens.items(), key=lambda item: item[1])).encode()
    if len(inventory) != 281 or hashlib.sha256(inventory).hexdigest() != INVENTORY_SHA256:
        raise ValueError("original phoneme inventory checksum mismatch")
    return tokens


def clause_words(phonemes: str, terminator: int) -> list[list[str]]:
    """Keep IPA graphemes, strip native language metadata, preserve punctuation."""
    if len(phonemes) > 16384:
        raise ValueError("native phoneme output exceeds its bound")
    words = []
    for word in LANGUAGE_MARKER.sub("", phonemes).split():
        chars: list[str] = []
        for char in word.replace("_", ""):
            if unicodedata.combining(char) and chars:
                chars[-1] += char
            else:
                chars.append(char)
        if chars:
            words.append(chars)
    intonation = terminator & 0x7000
    if words and intonation != 0x4000:
        # phonemes2ids simplifies question/exclamation to '.', colon/semicolon
        # to ','. eSpeak exposes their clause/sentence and intonation bits.
        punctuation = "," if intonation == 0x1000 or terminator & 0xF0000 == 0x40000 else "."
        words[-1].append(punctuation)
    return words


def encode_words(words: list[list[str]], tokens: dict[str, int]) -> tuple[list[int], tuple[str, ...]]:
    ids = [tokens["^"], tokens["_"]]
    unknown = set()
    for word in words:
        word_ids = []
        for phoneme in word:
            if phoneme in tokens:
                word_ids.append(tokens[phoneme])
            else:
                unknown.add("+".join(f"U{ord(char):04X}" for char in phoneme))
        if word_ids:
            word_ids.append(tokens["#"])
            for index in word_ids:
                ids.extend((index, tokens["_"]))
    ids.append(tokens["$"])
    if len(ids) <= 3 or len(ids) > 4096:
        raise ValueError("phoneme sequence is empty or exceeds its bound")
    return ids, tuple(sorted(unknown))


class NativeModel:
    def __init__(self, config: dict) -> None:
        for name, version in (("onnxruntime", ONNX_RUNTIME_VERSION), ("sherpa-onnx", "1.13.8"), ("sherpa-onnx-core", "1.13.8"), ("numpy", "2.2.6")):
            if importlib.metadata.version(name) != version:
                raise ValueError("native runtime version mismatch: " + name)
        import numpy as np
        import onnxruntime as ort
        import sherpa_onnx
        self.np = np
        self.pack = Path(config["pack_path"])
        self.output = Path(config["output_root"])
        self.tokens = original_inventory(self.pack / "tokens.txt")
        self.noise = float(config["noise_scale"])
        self.noise_w = float(config["noise_scale_w"])
        self.library = ctypes.CDLL(str(Path(sherpa_onnx.__file__).parent / "lib/sherpa-onnx-c-api.dll"))
        self.library.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        self.library.espeak_Initialize.restype = ctypes.c_int
        self.library.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
        self.library.espeak_SetVoiceByName.restype = ctypes.c_int
        self.library.espeak_TextToPhonemesWithTerminator.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.library.espeak_TextToPhonemesWithTerminator.restype = ctypes.c_char_p
        if self.library.espeak_Initialize(2, 0, str(self.pack).encode(), 0) != 22050 or self.library.espeak_SetVoiceByName(b"vi") != 0:
            raise ValueError("native phonemizer initialization failed")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(self.pack / "vi_VN-vais1000_low.onnx"), options, providers=["CPUExecutionProvider"])

    def generate(self, request: dict) -> dict:
        text, speed, sequence = request["text"], request["speed"], request["sequence"]
        if type(text) is not str or not 0 < len(text) <= 512 or "\x00" in text:
            raise ValueError("native cue text is invalid")
        if type(sequence) is not int or not 1 <= sequence <= 1_000_000 or type(speed) not in (int, float) or not 1 <= speed <= 1.3:
            raise ValueError("native request identity/rate is invalid")
        buffer = ctypes.create_string_buffer(text.encode())
        pointer = ctypes.c_void_p(ctypes.addressof(buffer))
        words = []
        for _ in range(32):
            if not pointer.value:
                break
            previous = pointer.value
            terminator = ctypes.c_int()
            phonemes = self.library.espeak_TextToPhonemesWithTerminator(ctypes.byref(pointer), 1, 2 | (ord("_") << 8), ctypes.byref(terminator))
            if phonemes:
                words.extend(clause_words(phonemes.decode(), terminator.value))
            if pointer.value == previous:
                raise ValueError("native phonemizer made no progress")
        if pointer.value:
            raise ValueError("native phonemizer exceeded clause budget")
        ids, unknown = encode_words(words, self.tokens)
        np = self.np
        audio = self.session.run(None, {"input": np.array([ids], dtype=np.int64), "input_lengths": np.array([len(ids)], dtype=np.int64), "scales": np.array([self.noise, 1 / speed, self.noise_w], dtype=np.float32)})[0].reshape(-1)
        if not 0 < len(audio) <= MAX_FRAMES or not np.isfinite(audio).all():
            raise ValueError("native waveform is invalid or oversized")
        payload = audio.astype("<f4").tobytes()
        name = f"samples-{sequence}.f32"
        with (self.output / name).open("xb") as stream:
            stream.write(payload)
        return {"file": name, "frames": len(audio), "sample_rate": 22050, "sha256": hashlib.sha256(payload).hexdigest(), "unknown": unknown}


def _join_windows_job(request: dict) -> None:
    if os.name != "nt":
        return
    name = request.get("windows_job_name")
    if not isinstance(name, str) or not re.fullmatch(r"DubFlowNativeTts-[0-9a-f]{32}", name):
        raise ValueError("native Windows containment identity is missing")
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    api.OpenJobObjectW.restype = wintypes.HANDLE
    api.GetCurrentProcess.restype = wintypes.HANDLE
    api.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    api.IsProcessInJob.restype = wintypes.BOOL
    api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    api.AssignProcessToJobObject.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    job = api.OpenJobObjectW(0x0001 | 0x0004, False, name)  # ASSIGN_PROCESS | QUERY
    if not job:
        raise ValueError("native worker containment is unavailable")
    try:
        member = wintypes.BOOL()
        process = api.GetCurrentProcess()
        if not api.IsProcessInJob(process, job, ctypes.byref(member)) or (not member.value and not api.AssignProcessToJobObject(job, process)):
            raise ValueError("native interpreter cannot join worker containment")
    finally:
        api.CloseHandle(job)
    # Only the parent retains a job handle. Keeping one here would defeat
    # kill-on-close when the worker dies. Redirector-launched interpreters
    # must join before importing native model libraries.


def _read_requests(requests: Queue) -> None:
    """Stdin stays open for the bridge lifetime, including busy inference."""
    try:
        pending = b""
        while chunk := os.read(sys.stdin.fileno(), 8193):
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                if len(line) >= 8192:
                    raise ValueError("native request exceeds protocol bound")
                requests.put(line + b"\n", timeout=1)
            if len(pending) > 8192:
                raise ValueError("native request exceeds protocol bound")
    except Exception:
        os._exit(2)
    # The parent closes its pipe or dies. A daemon reader can terminate native
    # initialization/inference even while the main thread is inside ONNX.
    os._exit(0)


def main() -> None:
    if os.name == "nt":
        # Windows Job containment owns lifetime. A concurrent pipe reader
        # during NumPy DLL loading caused a reproduced initialization hang.
        lines = iter(lambda: sys.stdin.buffer.readline(8193), b"")
    else:
        requests = Queue(maxsize=2)
        Thread(target=_read_requests, args=(requests,), daemon=True).start()
        lines = iter(requests.get, None)
    model = None
    for line in lines:
        sequence = -1
        try:
            if len(line) > 8192 or not line.endswith(b"\n"):
                raise ValueError("native request exceeds protocol bound")
            request = json.loads(line)
            sequence = request["sequence"]
            if model is None:
                if sequence != 0:
                    raise ValueError("native initialization must be first")
                _join_windows_job(request)
                model = NativeModel(request)
                result = {"frontend": FRONTEND_ID}
            else:
                result = model.generate(request)
            reply = {"schema_version": 1, "sequence": sequence, "ok": True, **result}
        except Exception as error:
            reply = {"schema_version": 1, "sequence": sequence, "ok": False, "condition": str(error)[:1024]}
        sys.stdout.write(json.dumps(reply, ensure_ascii=True, allow_nan=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
