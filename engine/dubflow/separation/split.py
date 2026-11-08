"""Nhạc and SFX: the accompaniment (everything but the voice) in two parts.

``python -B -m engine.dubflow.separation.split --background X --out-dir D``
writes ``D/music.wav`` and ``D/sfx.wav`` beside ``D/split.json`` (and, given
``--source``, ``D/voice.wav``: the source minus the accompaniment, which the
editor draws as the Lời lane). No model is
needed: a harmonic–percussive split (median filtering of the spectrogram,
Fitzgerald 2010) keeps what holds a pitch over time (melody, chords, pads) as
music and what is short or broadband (hits, impacts, whooshes, steps, clicks)
as sound effects. The two masks add up to one, so ``music + sfx`` equals the
accompaniment and the editor's weights stay exact: the host mixes
``source·o + music·(m−o) + sfx·(s−o)``. Drums in a song land in SFX; the
editor says so.

The file is read in 20 s blocks with 1 s of context on each side, so memory
stays flat on long videos. ``split.json`` names the accompaniment it came
from (path, size, modification time) and the recipe, so the app can tell when
the parts are stale.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import wave

from engine.dubflow.separation.outdir import OutDir, UnsafeFolder

RECIPE = "hpss-median-v1"
N_FFT = 2048
HOP = 512
TIME_KERNEL = 17
FREQ_KERNEL = 17
BLOCK_SECONDS = 20.0
CONTEXT_SECONDS = 1.0
SPLIT_FILE = "split.json"


class SplitError(ValueError):
    pass


def identity_of(background: Path) -> dict:
    stat = background.stat()
    return {"recipe": RECIPE, "background": str(background), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _median(values, kernel: int, axis: int):
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view

    half = kernel // 2
    pad = [(0, 0)] * values.ndim
    pad[axis] = (half, half)
    padded = np.pad(values, pad, mode="reflect" if values.shape[axis] > half else "edge")
    return np.median(sliding_window_view(padded, kernel, axis=axis), axis=-1)


def _stft(signal, window):
    import numpy as np

    padded = np.pad(signal, (N_FFT // 2, N_FFT // 2), mode="reflect" if signal.size > N_FFT // 2 else "constant")
    count = 1 + max(0, (padded.size - N_FFT) // HOP)
    frames = np.lib.stride_tricks.as_strided(padded, shape=(count, N_FFT), strides=(padded.strides[0] * HOP, padded.strides[0]))
    return np.fft.rfft(frames * window, axis=-1).T


def _istft(spec, window, length: int):
    import numpy as np

    frames = np.fft.irfft(spec.T, n=N_FFT, axis=-1) * window
    total = N_FFT + HOP * (frames.shape[0] - 1)
    out = np.zeros(total)
    norm = np.zeros(total)
    for index, frame in enumerate(frames):
        out[index * HOP:index * HOP + N_FFT] += frame
        norm[index * HOP:index * HOP + N_FFT] += window ** 2
    out /= np.maximum(norm, 1e-8)
    start = N_FFT // 2
    return out[start:start + length]


def split_block(block):
    """``block``: float [channels, samples]. Returns (music, sfx) of the same shape, music + sfx == block."""
    import numpy as np

    window = np.hanning(N_FFT + 1)[:-1]
    length = block.shape[1]
    if length < N_FFT:
        return block.copy(), np.zeros_like(block)
    specs = [_stft(channel, window) for channel in block]
    # One mask for every channel, from their mean magnitude: stereo keeps its image.
    magnitude = np.mean([np.abs(spec) for spec in specs], axis=0).astype(np.float32)
    harmonic = _median(magnitude, TIME_KERNEL, axis=1)
    percussive = _median(magnitude, FREQ_KERNEL, axis=0)
    h2, p2 = harmonic.astype(np.float64) ** 2, percussive.astype(np.float64) ** 2
    mask = h2 / np.maximum(h2 + p2, 1e-12)
    music = np.stack([_istft(spec * mask, window, length) for spec in specs])
    # SFX is the rest, so the two always add back to the accompaniment.
    return music, block - music


def _voice(source: Path, background: Path, folder: OutDir, *, cancelled=None) -> None:
    """``voice.wav``: the source minus the accompaniment (what the Lời lane draws), when both line up."""
    import numpy as np

    with wave.open(str(source), "rb") as left, wave.open(str(background), "rb") as right:
        shape = (left.getnchannels(), left.getsampwidth(), left.getframerate())
        if shape != (right.getnchannels(), right.getsampwidth(), right.getframerate()) or shape[1] != 2:
            return
        frames = min(left.getnframes(), right.getnframes())
        temp = folder.create(".voice-", ".wav")
        try:
            with wave.open(temp.file, "wb") as writer:
                writer.setnchannels(shape[0])
                writer.setsampwidth(2)
                writer.setframerate(shape[2])
                step = shape[2] * 10
                for _ in range(0, frames, step):
                    if cancelled and cancelled():
                        raise SplitError("cancelled")
                    count = min(step, frames - left.tell())
                    a = np.frombuffer(left.readframes(count), dtype="<i2").astype(np.int32)
                    b = np.frombuffer(right.readframes(count), dtype="<i2").astype(np.int32)
                    size = min(a.size, b.size)
                    writer.writeframes(np.clip(a[:size] - b[:size], -32768, 32767).astype("<i2").tobytes())
            temp.publish("voice.wav")
        finally:
            temp.discard()


def plain_out_dir(out_dir: Path, *, make: bool = False) -> None:
    """Refuse ``out_dir`` unless it and the two folders above it are real folders (no link or reparse
    point), each opened relative to the one above. With ``make``, a missing ``out_dir`` is created on
    its own (never its parents). A preflight: the writes themselves go through ``OutDir``."""
    try:
        OutDir.open(out_dir, make=make).close()
    except UnsafeFolder as error:
        raise SplitError(str(error)) from None


def open_out_dir(out_dir: Path, *, make: bool = False) -> OutDir:
    try:
        return OutDir.open(out_dir, make=make)
    except UnsafeFolder as error:
        raise SplitError(str(error)) from None


def _publish(folder: OutDir, target: str, text: str) -> None:
    """Write ``target`` in the held folder through a temporary file and a rename relative to the folder
    (a link named ``target`` is replaced, not followed)."""
    temp = folder.create(f".{Path(target).stem}-", Path(target).suffix)
    try:
        temp.file.write(text.encode("utf-8"))
        temp.publish(target)
    finally:
        temp.discard()


def split(background: Path, out_dir: Path, *, source: Path | None = None, progress=None, cancelled=None,
          folder: OutDir | None = None) -> dict:
    """Split into ``out_dir``. Every write, publication and removal is relative to the folder as opened
    (``folder``, or opened here): a path swapped for a link meanwhile is never followed."""
    import numpy as np

    with wave.open(str(background), "rb") as reader:
        channels, width, rate, frames = reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getnframes()
        if width != 2 or reader.getcomptype() != "NONE" or not 1 <= channels <= 2 or frames <= 0:
            raise SplitError("nhạc nền phải là WAV PCM 16-bit")
        identity = identity_of(background)
    own = folder is None
    folder = folder or open_out_dir(out_dir, make=True)
    try:
        return _split(background, folder, channels, rate, frames, identity, source, progress, cancelled, np)
    finally:
        if own:
            folder.close()


def _split(background, folder, channels, rate, frames, identity, source, progress, cancelled, np) -> dict:
    # The old marker goes first: until the new one is written, no music/SFX pair counts as current
    # (an interrupted run may leave one new part beside an old one).
    folder.unlink(SPLIT_FILE)
    block, context = int(BLOCK_SECONDS * rate), int(CONTEXT_SECONDS * rate)
    temps = []
    try:
        with wave.open(str(background), "rb") as reader:
            writers = []
            try:
                for name in ("music", "sfx"):
                    temps.append(folder.create(f".{name}-", ".wav"))
                    writer = wave.open(temps[-1].file, "wb")
                    writer.setnchannels(channels)
                    writer.setsampwidth(2)
                    writer.setframerate(rate)
                    writers.append(writer)
                for first in range(0, frames, block):
                    if cancelled and cancelled():
                        raise SplitError("cancelled")
                    begin, end = max(0, first - context), min(frames, first + block + context)
                    reader.setpos(begin)
                    raw = np.frombuffer(reader.readframes(end - begin), dtype="<i2").astype(np.float64) / 32768.0
                    data = raw.reshape(-1, channels).T
                    music, sfx = split_block(data)
                    keep = slice(first - begin, first - begin + min(block, frames - first))
                    for writer, part in zip(writers, (music, sfx)):
                        pcm = np.clip(np.round(part[:, keep].T * 32768.0), -32768, 32767).astype("<i2")
                        writer.writeframes(pcm.tobytes())
                    if progress:
                        progress(min(1.0, (first + block) / frames))
            finally:
                for writer in writers:
                    writer.close()
        temps[0].publish("music.wav")
        temps[1].publish("sfx.wav")
    finally:
        for temp in temps:
            temp.discard()
    folder.unlink("voice.wav")
    if source is not None and source.is_file():
        try:
            _voice(source, background, folder, cancelled=cancelled)
        except (OSError, EOFError, wave.Error):
            pass  # Only the Lời lane's picture needs it.
    document = {"schema_version": 1, **identity}
    _publish(folder, SPLIT_FILE, json.dumps(document, ensure_ascii=True))
    return document


def main(argv: list[str] | None = None) -> int:
    from engine.dubflow.separation.__main__ import Status

    parser = argparse.ArgumentParser(prog="python -m engine.dubflow.separation.split")
    parser.add_argument("--background", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args(argv)
    try:
        folder = open_out_dir(args.out_dir, make=True)
    except (SplitError, OSError) as error:
        # Nothing is written there, not even a status: the host reads this line instead.
        print(json.dumps({"schema_version": 1, "state": "FAILED", "progress": 0,
                          "message": f"Không tách được nhạc và tiếng động ({error})."}, ensure_ascii=True))
        return 1
    with folder:
        status = Status(args.out_dir / "split-status.json", folder=folder)
        status.write(state="RUNNING", progress=0, message="Đang tách nhạc và tiếng động…")
        try:
            split(args.background, args.out_dir, source=args.source, folder=folder,
                  progress=lambda fraction: status.write(force=False, progress=round(100 * fraction, 1), message="Đang tách nhạc và tiếng động…"),
                  cancelled=lambda: folder.exists("split-cancel"))
            status.write(state="COMPLETED", progress=100, message="Đã tách nhạc và tiếng động.")
        except Exception as error:  # noqa: BLE001 - every failure becomes a readable status.
            if str(error) == "cancelled":
                status.write(state="CANCELLED", progress=0, message="Đã hủy tách nhạc và tiếng động.")
            else:
                status.write(state="FAILED", progress=0, message=f"Không tách được nhạc và tiếng động ({' '.join(str(error).split())[:200]}).")
    print(json.dumps(status.value, ensure_ascii=True))
    return 0 if status.value["state"] == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
