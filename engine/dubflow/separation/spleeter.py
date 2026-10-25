"""Experimental CPU Spleeter adapter; original-bed AUD-0 remains the fallback.

Only fixed-size STFT batches live in RAM. Decoded float PCM spools to disk;
completed batches retain their overlap-add tails in atomic, hashed receipts.
No scientific/native dependencies are imported until this adapter is used.
"""

from __future__ import annotations

from hashlib import sha256
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
import wave
import zipfile

from .production import SeparationError, SeparationConfig, _read_pcm_metadata


BACKEND = "spleeter-2stems-onnx-v1"
RATE, N_FFT, HOP, BINS, SPLIT = 44100, 4096, 1024, 1024, 512
# Two splits bound both FFT storage and the native model's intermediate tensors.
BATCH_SPLITS = 2


def _digest(path):
    digest = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _window():
    import numpy as np
    return np.hanning(N_FFT + 1)[:-1].astype(np.float32)


def _stft_frames(samples):
    import numpy as np
    frames = np.lib.stride_tricks.sliding_window_view(samples, N_FFT, axis=-1)[:, ::HOP]
    return np.fft.rfft(frames * _window(), axis=-1)


def _stft(samples):
    """Centered, zero-padded STFT, also used by the deterministic round trip."""
    import numpy as np
    return _stft_frames(np.pad(samples, ((0, 0), (N_FFT // 2, N_FFT // 2))))


def _overlap_add(spec, tail=None, norm_tail=None):
    import numpy as np
    count = spec.shape[1]
    output = np.zeros((2, count * HOP + N_FFT - HOP), np.float32)
    norm = np.zeros(output.shape[1], np.float32)
    if tail is not None:
        output[:, :tail.shape[1]] = tail
        norm[:len(norm_tail)] = norm_tail
    window = _window()
    chunks = np.fft.irfft(spec, n=N_FFT, axis=-1) * window
    for index in range(count):
        start = index * HOP
        output[:, start:start + N_FFT] += chunks[:, index]
        norm[start:start + N_FFT] += window ** 2
    return output, norm


def _istft(spec, length):
    import numpy as np
    output, norm = _overlap_add(spec)
    output /= np.maximum(norm, 1e-8)
    return output[:, N_FFT // 2:N_FFT // 2 + length]


def _finite(samples):
    import numpy as np
    if not np.isfinite(samples).all():
        raise SeparationError("SEPARATION_REJECTED", "non-finite accompaniment or model output")


def _accompaniment_mask(vocals, accompaniment):
    import numpy as np
    _finite(vocals)
    _finite(accompaniment)
    with np.errstate(over="ignore", invalid="ignore"):
        mask = (accompaniment ** 2 + 5e-11) / (vocals ** 2 + accompaniment ** 2 + 1e-10)
    _finite(mask)
    return mask


def _create_session(path):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


def _save_batch(path, pcm, tail, norm):
    import numpy as np
    temporary = path.with_suffix(".partial")
    with temporary.open("wb") as stream:
        np.savez(stream, pcm=pcm, tail=tail, norm=norm)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    receipt = path.with_suffix(".sha256")
    temporary = receipt.with_suffix(".partial")
    temporary.write_text(_digest(path), encoding="ascii")
    os.replace(temporary, receipt)


def _load_batch(path, count):
    import numpy as np
    try:
        if path.stat().st_size > count * HOP * 8 + 128 * 1024:
            return None
        with path.with_suffix(".sha256").open("r", encoding="ascii") as receipt:
            if receipt.read(65) != _digest(path):
                return None
        with np.load(path, allow_pickle=False) as saved:
            pcm, tail, norm = saved["pcm"], saved["tail"], saved["norm"]
        if (pcm.shape != (2, count * HOP) or tail.shape != (2, N_FFT - HOP)
                or norm.shape != (N_FFT - HOP,) or any(x.dtype != np.float32 for x in (pcm, tail, norm))):
            return None
        for value in (pcm, tail, norm):
            _finite(value)
        return pcm, tail, norm
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
        return None


def _separated_blocks(read, length, session_factory, *, batch_splits=BATCH_SPLITS, checkpoint_dir=None, progress=None):
    """Yield contiguous stereo PCM; read(start, count) reads unpadded samples.

    Model splits stay anchored to global frame zero. The unnormalised OLA tail
    and window-square weights cross every batch boundary, including on resume.
    """
    import numpy as np
    if type(batch_splits) is not int or not 1 <= batch_splits <= 8:
        raise ValueError("batch_splits must be between one and eight")
    frames = 1 + length // HOP
    sessions = None
    tail = norm_tail = None
    if checkpoint_dir is not None:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
    for first in range(0, frames, batch_splits * SPLIT):
        count = min(batch_splits * SPLIT, frames - first)
        path = checkpoint_dir / f"{first:012d}.npz" if checkpoint_dir is not None else None
        saved = _load_batch(path, count) if path is not None else None
        if saved is not None:
            pcm, tail, norm_tail = saved
        else:
            start = first * HOP - N_FFT // 2
            stop = start + (count - 1) * HOP + N_FFT
            samples = np.zeros((2, stop - start), np.float32)
            low, high = max(0, start), min(length, stop)
            samples[:, low - start:high - start] = read(low, high - low)
            spec = _stft_frames(samples)
            magnitude = np.abs(spec[:, :, :BINS]).astype(np.float32)
            padded = ((count + SPLIT - 1) // SPLIT) * SPLIT
            model_input = np.pad(magnitude, ((0, 0), (0, padded - count), (0, 0))).reshape(2, -1, SPLIT, BINS)
            if sessions is None:
                sessions = session_factory()
            estimates = []
            for session in sessions:
                value = session.run(None, {"x": model_input})[0]
                if value.shape != model_input.shape:
                    raise SeparationError("SEPARATION_REJECTED", "unexpected model output dimensions")
                estimates.append(value.reshape(2, padded, BINS)[:, :count])
            mask = _accompaniment_mask(*estimates)
            full = np.concatenate((mask, np.repeat(mask.mean(-1, keepdims=True), spec.shape[2] - BINS, axis=-1)), axis=-1)
            output, norm = _overlap_add(spec * full, tail, norm_tail)
            cut = count * HOP
            pcm = output[:, :cut] / np.maximum(norm[:cut], 1e-8)
            tail, norm_tail = output[:, cut:].copy(), norm[cut:].copy()
            _finite(pcm)
            if path is not None:
                _save_batch(path, pcm, tail, norm_tail)
        # Discard only the global center padding, never padding per batch.
        offset = first * HOP - N_FFT // 2
        low, high = max(0, -offset), min(pcm.shape[1], length - offset)
        if high > low:
            yield pcm[:, low:high]
        if progress:
            progress(min(1.0, (first + count) / frames))
    offset = frames * HOP - N_FFT // 2
    if offset < length:
        yield (tail / np.maximum(norm_tail, 1e-8))[:, :length - offset]


# Ratio masks on loud, already-limited sources can push the float accompaniment
# past full scale; a lookahead limiter keeps it below the clipping gate instead
# of rejecting separation for exactly the loud videos users dub most.
LIMITER = "alimiter=limit=0.95:attack=5:release=50:level=0"


def _validate_background(source, background):
    """AUD-2 safety gate and dBFS metrics measured on the converted PCM."""
    import numpy as np
    rate, channels, frames = _read_pcm_metadata(source, SeparationConfig())
    try:
        out_rate, out_channels, out_frames = _read_pcm_metadata(background, SeparationConfig())
    except SeparationError as error:
        raise SeparationError("SEPARATION_REJECTED", error.condition) from error
    if (rate, channels) != (out_rate, out_channels) or abs(out_frames - frames) * 1000 > rate * 50:
        raise SeparationError("SEPARATION_REJECTED", "accompaniment format/duration differs from source by more than 50 ms")
    source_energy = background_energy = removed_energy = peak = 0.0
    count = 0
    with wave.open(str(source), "rb") as original, wave.open(str(background), "rb") as bed:
        for first in range(0, max(frames, out_frames), 16384):
            size = min(16384, max(frames, out_frames) - first)
            arrays = []
            for reader in (original, bed):
                raw = reader.readframes(min(size, max(0, reader.getnframes() - first)))
                expected = min(size, max(0, reader.getnframes() - first)) * channels * 2
                if len(raw) != expected:
                    raise SeparationError("SEPARATION_REJECTED", "truncated WAV samples")
                data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768
                arrays.append(np.pad(data, (0, size * channels - len(data))))
            x, y = arrays
            _finite(y)
            peak = max(peak, float(np.max(np.abs(y))))
            source_energy += float(x @ x)
            background_energy += float(y @ y)
            delta = x - y
            removed_energy += float(delta @ delta)
            count += size * channels
    if peak > 0.999:
        raise SeparationError("SEPARATION_REJECTED", "accompaniment clips after PCM conversion (peak > 0.999)")
    def db(energy):
        return 10 * math.log10(max(energy / count, 1e-20))
    return {"source_rms_db": db(source_energy), "background_rms_db": db(background_energy),
            "removed_db": db(removed_energy), "peak": peak}


def _ffmpeg(argv, *, stdin=None, stdout=None, stderr=None):
    return subprocess.Popen(argv, stdin=stdin, stdout=stdout, stderr=stderr,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def separate_background(source_wav: Path, background_wav: Path, *, model_dir: Path, ffmpeg_path: Path, progress=None) -> dict:
    """Separate with the pinned pair of models and atomically publish gated PCM.

    ``seconds`` is elapsed processing time. RMS and removed signal energy are
    dBFS (removed = source minus accompaniment); they are not quality scores.
    Source PCM must be signed-16 mono/stereo, as produced by the B2 decoder.
    """
    started = time.monotonic()
    candidate = None
    try:
        import numpy as np
        source, target = Path(source_wav), Path(background_wav)
        if source.resolve() == target.resolve():
            raise SeparationError("SEPARATION_INPUT_INVALID", "background must not replace the original WAV")
        rate, channels, _ = _read_pcm_metadata(source, SeparationConfig())
        models = [Path(model_dir) / f"spleeter-2stems-{stem}.onnx" for stem in ("vocals", "accompaniment")]
        source_hash, model_hashes = _digest(source), list(map(_digest, models))
        identity = sha256(json.dumps([source_hash, *model_hashes, _digest(__file__), BATCH_SPLITS]).encode()).hexdigest()
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".background-", suffix=".wav", dir=target.parent)
        os.close(descriptor)
        candidate = Path(name)
        # Stderr spools too: a noisy/broken native process cannot fill a pipe.
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
                "-ac", "2", "-i", "pipe:0", "-af", LIMITER, "-ar", str(rate), "-ac", str(channels), "-c:a", "pcm_s16le", str(candidate)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
            try:
                with encoder.stdin:
                    for block in _separated_blocks(read, length, lambda: tuple(_create_session(path) for path in models),
                            checkpoint_dir=target.parent / ".spleeter-chunks" / identity, progress=progress):
                        _finite(block)
                        encoder.stdin.write(block.T.astype("<f4").tobytes())
                if encoder.wait() != 0:
                    raise SeparationError("SEPARATION_ENCODE_FAILED", "ffmpeg accompaniment conversion failed")
            finally:
                if encoder.poll() is None:
                    encoder.kill()
                encoder.wait()
        metrics = {"backend": BACKEND, "seconds": time.monotonic() - started, **_validate_background(source, candidate)}
        if _digest(source) != source_hash or list(map(_digest, models)) != model_hashes:
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
                pass  # Retain an uncommitted candidate if the OS denies cleanup.
