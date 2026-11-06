"""Voice removal with an MDX-Net vocal model (UVR's Kim_Vocal_2, ONNX).

Replaces Spleeter 2-stems, whose soft masks left the original voice audible
in the accompaniment and added a "watery" noise. The model estimates the
voice; the accompaniment is the source minus that voice, so music and sound
effects keep their own samples wherever nobody speaks.

The source spools to disk as float PCM and only one batch of overlapping
model chunks lives in RAM. Chunks are cross-faded with a Hann weight, so no
seam is audible. No scientific/native dependencies are imported until used.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from .production import SeparationError, SeparationConfig, _read_pcm_metadata
from .spleeter import LIMITER, _digest, _ffmpeg, _finite, _validate_background


BACKEND = "uvr-mdx-kim-vocal-2-onnx-v1"
MODEL_FILE = "Kim_Vocal_2.onnx"
# Kim_Vocal_2 in UVR's model_data.json: 7680-point STFT, 3072 bins, 256 frames.
RATE, N_FFT, HOP, DIM_F, DIM_T = 44100, 7680, 1024, 3072, 256
COMPENSATE = 1.009
CHUNK = HOP * (DIM_T - 1)
# A quarter of every chunk overlaps its neighbour: enough for a seamless fade.
STEP = CHUNK * 3 // 4


def _window():
    import numpy as np
    return np.hanning(N_FFT + 1)[:-1].astype(np.float32)


def _stft(chunks):
    """[B, 2, CHUNK] float32 → model input [B, 4, DIM_F, DIM_T] (torch.stft, center, reflect)."""
    import numpy as np
    padded = np.pad(chunks, ((0, 0), (0, 0), (N_FFT // 2, N_FFT // 2)), mode="reflect")
    frames = np.lib.stride_tricks.sliding_window_view(padded, N_FFT, axis=-1)[:, :, ::HOP]
    spec = np.fft.rfft(frames * _window(), axis=-1)[..., :DIM_F].transpose(0, 1, 3, 2)
    return np.stack((spec.real, spec.imag), 2).reshape(chunks.shape[0], 4, DIM_F, DIM_T).astype(np.float32)


def _istft(value):
    """Model output [B, 4, DIM_F, DIM_T] → [B, 2, CHUNK]; bins above DIM_F are silent."""
    import numpy as np
    count = value.shape[0]
    value = value.reshape(count, 2, 2, DIM_F, DIM_T)
    spec = np.zeros((count, 2, DIM_T, N_FFT // 2 + 1), np.complex64)
    spec[..., :DIM_F] = (value[:, :, 0] + 1j * value[:, :, 1]).transpose(0, 1, 3, 2)
    window = _window()
    frames = np.fft.irfft(spec, n=N_FFT, axis=-1).astype(np.float32) * window
    output = np.zeros((count, 2, (DIM_T - 1) * HOP + N_FFT), np.float32)
    norm = np.zeros(output.shape[-1], np.float32)
    for index in range(DIM_T):
        start = index * HOP
        output[:, :, start:start + N_FFT] += frames[:, :, index]
        norm[start:start + N_FFT] += window ** 2
    output /= np.maximum(norm, 1e-8)
    return output[:, :, N_FFT // 2:N_FFT // 2 + CHUNK]


def _fade():
    import numpy as np
    # Never exactly zero: the first and last samples of the whole file have one chunk only.
    return (np.hanning(CHUNK) + 1e-3).astype(np.float32)


def _threads() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _create_session(path):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = _threads()
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


def _voice_blocks(read, length, session_factory, *, batch=1, progress=None, cancelled=None):
    """Yield (source, voice) stereo blocks covering [0, length) in order.

    Chunk k covers padded samples [k·STEP, k·STEP + CHUNK); the padding of one
    CHUNK before the file keeps every real sample inside a full-weight region.
    A sample is final once no later chunk reaches it.
    """
    import numpy as np
    if type(batch) is not int or not 1 <= batch <= 8:
        raise ValueError("batch must be between one and eight")
    pad = CHUNK
    total = length + 2 * pad
    starts = list(range(0, max(1, total - CHUNK + STEP), STEP))
    fade = _fade()
    session = None
    origin = 0  # padded position of buffer index 0
    voice = np.zeros((2, CHUNK + STEP * batch), np.float32)
    weight = np.zeros(voice.shape[1], np.float32)
    emitted = pad  # next padded position to yield

    def padded(first, count):
        out = np.zeros((2, count), np.float32)
        low, high = max(first, pad), min(first + count, pad + length)
        if high > low:
            out[:, low - first:high - first] = read(low - pad, high - low)
        return out

    for index in range(0, len(starts), batch):
        if cancelled is not None and cancelled():
            raise SeparationError("SEPARATION_CANCELLED", "cancelled")
        group = starts[index:index + batch]
        chunks = np.stack([padded(start, CHUNK) for start in group])
        if session is None:
            session = session_factory()
        name = session.get_inputs()[0].name
        estimate = session.run(None, {name: _stft(chunks)})[0]
        if estimate.shape != (len(group), 4, DIM_F, DIM_T):
            raise SeparationError("SEPARATION_REJECTED", "unexpected model output dimensions")
        _finite(estimate)
        estimate = _istft(estimate) * COMPENSATE
        for offset, start in enumerate(group):
            position = start - origin
            voice[:, position:position + CHUNK] += estimate[offset] * fade
            weight[position:position + CHUNK] += fade
        following = starts[index + batch] if index + batch < len(starts) else total
        ready = min(following, pad + length)
        if ready > emitted:
            low, high = emitted - origin, ready - origin
            block = voice[:, low:high] / np.maximum(weight[low:high], 1e-8)
            _finite(block)
            yield padded(emitted, ready - emitted), block
            emitted = ready
        # Slide the buffers so index 0 is the next chunk's start.
        shift = following - origin
        if shift > 0 and index + batch < len(starts):
            voice = np.concatenate((voice[:, shift:], np.zeros((2, shift), np.float32)), axis=1)
            weight = np.concatenate((weight[shift:], np.zeros(shift, np.float32)))
            origin = following
        if progress:
            progress(min(1.0, (index + len(group)) / len(starts)))


def separate_background(source_wav: Path, background_wav: Path, *, model_dir: Path, ffmpeg_path: Path, progress=None,
                        device: str = "cpu", cancelled=None) -> dict:
    """Write the accompaniment (source minus the estimated voice) as gated PCM.

    Same contract and metrics as ``spleeter.separate_background``: the source
    must be signed-16 mono/stereo PCM; ``seconds`` is elapsed time.
    """
    started = time.monotonic()
    candidate = None
    try:
        import numpy as np
        source, target = Path(source_wav), Path(background_wav)
        if source.resolve() == target.resolve():
            raise SeparationError("SEPARATION_INPUT_INVALID", "background must not replace the original WAV")
        rate, channels, _ = _read_pcm_metadata(source, SeparationConfig())
        model = Path(model_dir) / MODEL_FILE
        source_hash, model_hash = _digest(source), _digest(model)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".background-", suffix=".wav", dir=target.parent)
        os.close(descriptor)
        candidate = Path(name)
        with tempfile.TemporaryFile() as decoded, tempfile.TemporaryFile() as errors:
            decoder = _ffmpeg([str(ffmpeg_path), "-v", "error", "-nostdin", "-i", str(source),
                "-ac", "2", "-ar", str(RATE), "-f", "f32le", "pipe:1"], stdout=subprocess.PIPE, stderr=errors)
            try:
                with decoder.stdout:
                    for block in iter(lambda: decoder.stdout.read(256 * 1024), b""):
                        decoded.write(block)
                if decoder.wait() != 0:
                    raise SeparationError("SEPARATION_DECODE_FAILED", "ffmpeg float PCM decode failed")
            finally:
                if decoder.poll() is None:
                    decoder.kill()
                decoder.wait()
            length, extra = divmod(decoded.tell(), 8)
            if extra or length <= 0:
                raise SeparationError("SEPARATION_DECODE_FAILED", "decoded float PCM is empty or truncated")

            def read(first, count):
                decoded.seek(first * 8)
                data = np.frombuffer(decoded.read(count * 8), dtype="<f4").reshape(-1, 2).T
                _finite(data)
                return data
            encoder = _ffmpeg([str(ffmpeg_path), "-v", "error", "-nostdin", "-y", "-f", "f32le", "-ar", str(RATE),
                "-ac", "2", "-i", "pipe:0", "-af", LIMITER, "-ar", str(rate), "-ac", str(channels), "-c:a", "pcm_s16le",
                str(candidate)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
            try:
                with encoder.stdin:
                    for mix, voice in _voice_blocks(read, length, lambda: _create_session(model),
                                                    batch=2 if device == "cuda" else 1, progress=progress,
                                                    cancelled=cancelled):
                        encoder.stdin.write((mix - voice).T.astype("<f4").tobytes())
                if encoder.wait() != 0:
                    raise SeparationError("SEPARATION_ENCODE_FAILED", "ffmpeg accompaniment conversion failed")
            finally:
                if encoder.poll() is None:
                    encoder.kill()
                encoder.wait()
        metrics = {"backend": BACKEND, "device": device, "seconds": time.monotonic() - started,
                   **_validate_background(source, candidate)}
        if _digest(source) != source_hash or _digest(model) != model_hash:
            raise SeparationError("SEPARATION_INPUT_CHANGED", "source or model changed during separation")
        with candidate.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(candidate, target)
        return metrics
    except SeparationError:
        raise
    except Exception as error:
        raise SeparationError("SEPARATION_FAILED", str(error)) from error
    finally:
        if candidate is not None:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass


def identity() -> str:
    """Changes whenever this adapter or its pinned recipe changes (for checkpoints)."""
    return sha256(json.dumps([BACKEND, _digest(__file__), N_FFT, DIM_F, DIM_T, STEP, COMPENSATE]).encode()).hexdigest()
