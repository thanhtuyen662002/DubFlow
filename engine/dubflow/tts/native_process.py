"""Bounded native TTS child bridge; no native inference in the job worker."""
from __future__ import annotations

from array import array
import hashlib
import json
import os
from pathlib import Path
from queue import Queue, Empty, Full
import subprocess
import sys
import tempfile
from threading import Thread, Lock
from types import SimpleNamespace

from .adapter import TtsBackendError
from .mimic3_native import FRONTEND_ID, MAX_FRAMES
from .windows_job import WindowsJob

CUE_REJECTION_CODES = {"TTS_SPEECH_INCOMPLETE", "TTS_TEXT_UNSUPPORTED"}
CUE_SYNTHESIS_WARNINGS = {"TTS_EOS_RESEEDED", "TTS_EOS_FRAME_BUDGET_EXTENDED"}


class NativeProcess:
    def __init__(self, pack, *, timeout: float = 120.0, command: list[str] | None = None,
                 entrypoint: Path | None = None, frontend: str = FRONTEND_ID,
                 sample_rate: int = 22050, initialization: dict | None = None) -> None:
        self.timeout = timeout
        self.sample_rate = sample_rate
        self.sequence = 0
        self.lock = Lock()
        self.responses = Queue(maxsize=8)
        self.errors = bytearray()
        self.directory = tempfile.TemporaryDirectory(prefix="dubflow-native-tts-")
        self.root = Path(self.directory.name)
        self.process = None
        self.job = None
        try:
            self.process = subprocess.Popen(
                command or [sys.executable, "-I", "-B", "-u", str(entrypoint or Path(__file__).with_name("mimic3_native.py"))],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            # No native initialization request is sent before containment. If
            # assignment fails, close the child and return a typed B1 fallback.
            self.job = WindowsJob(self.process)
            Thread(target=self._read_output, daemon=True).start()
            Thread(target=self._read_errors, daemon=True).start()
            request = dict(initialization or {})
            request.update({"sequence": 0, "pack_path": str(pack.path), "output_root": str(self.root), "noise_scale": pack.noise_scale, "noise_scale_w": pack.noise_scale_w, "windows_job_name": self.job.name})
            ready = self._request(request, timeout=min(timeout, 120.0 if entrypoint else 30.0))
            if ready.get("frontend") != frontend:
                raise TtsBackendError("TTS_NATIVE_PROTOCOL_INVALID", "native frontend identity differs")
        except BaseException:
            self.close()
            raise

    def _read_output(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while line := self.process.stdout.readline(4097):
                if len(line) > 4096 or not line.endswith(b"\n"):
                    raise ValueError("native response exceeds protocol bound")
                self.responses.put(line, timeout=1)
        except (OSError, ValueError, Full):
            pass
        finally:
            try:
                self.responses.put(None, timeout=1)
            except Full:
                pass

    def _read_errors(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        try:
            while chunk := self.process.stderr.read(4096):
                self.errors.extend(chunk[:max(0, 65536 - len(self.errors))])
        except OSError:
            pass

    def _request(self, request: dict, *, timeout: float | None = None) -> dict:
        cue_rejected = False
        try:
            if self.process is None or self.process.poll() is not None:
                raise TtsBackendError("TTS_NATIVE_EXITED", "native process is no longer running")
            payload = json.dumps(request, ensure_ascii=True, allow_nan=False).encode() + b"\n"
            if len(payload) > 8192:
                raise TtsBackendError("TTS_NATIVE_PROTOCOL_INVALID", "native request exceeds protocol bound")
            self.process.stdin.write(payload)
            self.process.stdin.flush()
            try:
                line = self.responses.get(timeout=timeout or self.timeout)
            except Empty as error:
                raise TtsBackendError("TTS_NATIVE_TIMEOUT", "native inference exceeded its deadline") from error
            if line is None:
                raise TtsBackendError("TTS_NATIVE_EXITED", "native process exited before completing the request")
            reply = json.loads(line)
            if type(reply) is not dict or type(reply.get("schema_version")) is not int or reply["schema_version"] != 1 or type(reply.get("sequence")) is not int or reply["sequence"] != request["sequence"] or type(reply.get("ok")) is not bool:
                raise TtsBackendError("TTS_NATIVE_PROTOCOL_INVALID", "native reply identity differs")
            if not reply["ok"]:
                if (request["sequence"] > 0 and reply.get("scope") == "cue" and
                        type(reply.get("code")) is str and
                        reply.get("code") in CUE_REJECTION_CODES and
                        type(reply.get("condition")) is str and 0 < len(reply["condition"]) <= 1024):
                    # The reviewed model resets decoding for each text. A cue
                    # that hit its content bound is refused once; retain this
                    # healthy child for the next cue. Crashes, initialization,
                    # timeout, malformed replies and all other errors close it.
                    cue_rejected = True
                    raise TtsBackendError(reply["code"], reply["condition"], retryable=False)
                raise TtsBackendError("TTS_NATIVE_INFERENCE_FAILED", str(reply.get("condition", "native inference failed"))[:1024])
            return reply
        except TtsBackendError:
            if not cue_rejected:
                self.close()
            raise
        except (OSError, ValueError) as error:
            self.close()
            raise TtsBackendError("TTS_NATIVE_PROTOCOL_INVALID", "native process communication failed") from error

    def generate(self, text: str, sid: int, speed: float):
        with self.lock:
            self.sequence += 1
            reply = self._request({"sequence": self.sequence, "text": text, "speed": speed})
            name = f"samples-{self.sequence}.f32"
            path = self.root / name
            try:
                frames = reply.get("frames")
                if reply.get("file") != name or type(frames) is not int or not 0 < frames <= MAX_FRAMES or reply.get("sample_rate") != self.sample_rate or path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                    raise TtsBackendError("TTS_NATIVE_PROTOCOL_INVALID", "native waveform metadata differs")
                with path.open("rb") as stream:
                    payload = stream.read(frames * 4 + 1)
                if len(payload) != frames * 4 or hashlib.sha256(payload).hexdigest() != reply.get("sha256"):
                    raise TtsBackendError("TTS_NATIVE_PROTOCOL_INVALID", "native waveform digest/size differs")
                samples = array("f")
                samples.frombytes(payload)
                if sys.byteorder != "little":
                    samples.byteswap()
                unknown = reply.get("unknown", [])
                if type(unknown) is not list or len(unknown) > 512 or any(type(value) is not str or len(value) > 128 for value in unknown):
                    raise TtsBackendError("TTS_NATIVE_PROTOCOL_INVALID", "native phoneme warnings differ")
                warnings = reply.get("warnings", [])
                if (type(warnings) is not list or len(warnings) > 2 or
                        any(type(value) is not str or value not in CUE_SYNTHESIS_WARNINGS for value in warnings) or
                        len(set(warnings)) != len(warnings) or
                        ("TTS_EOS_FRAME_BUDGET_EXTENDED" in warnings and "TTS_EOS_RESEEDED" not in warnings)):
                    raise TtsBackendError("TTS_NATIVE_PROTOCOL_INVALID", "native synthesis warnings differ")
                phoneme_warnings = ("UNSUPPORTED_PHONEMES: " + ",".join(unknown),) if unknown else ()
                return SimpleNamespace(samples=samples, sample_rate=self.sample_rate, warnings=phoneme_warnings + tuple(warnings))
            except TtsBackendError:
                self.close()
                raise
            except OSError as error:
                self.close()
                raise TtsBackendError("TTS_NATIVE_PROTOCOL_INVALID", "native waveform is unavailable") from error
            finally:
                path.unlink(missing_ok=True)

    def close(self) -> None:
        process = self.process
        if self.job is not None:
            job, self.job = self.job, None
            job.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        if self.root.resolve().parent != Path(tempfile.gettempdir()).resolve() or not self.root.name.startswith("dubflow-native-tts-"):
            raise TtsBackendError("TTS_NATIVE_PATH_UNSAFE", "refusing cleanup outside the native staging root")
        self.directory.cleanup()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
