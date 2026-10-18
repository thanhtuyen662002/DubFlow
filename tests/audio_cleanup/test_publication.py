"""Generation integrity regressions (synthetic PCM, not speech quality)."""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave

from engine.dubflow.separation.production import CpuAttenuationBackend


def write_pcm(path: Path, amplitude: int = 1000, frames: int = 8000, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setparams((channels, 2, 8000, 0, "NONE", "not compressed"))
        for start in range(0, frames, 4096):
            block = b"".join(struct.pack("<" + "h" * channels, *([amplitude if i % 2 else -amplitude] * channels))
                             for i in range(start, min(frames, start + 4096)))
            writer.writeframesraw(block)


def result_hashes(result):
    return tuple("sha256:" + sha256(path.read_bytes()).hexdigest()
                 for path in (result.source_path, result.dialogue_path, result.background_path))


class PublicationTests(unittest.TestCase):
    def test_every_replace_failure_preserves_previous_result_hashes(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.wav"
            write_pcm(source)
            backend = CpuAttenuationBackend()
            replace = os.replace
            with patch("os.replace", wraps=replace) as calls:
                backend.process(source, root / "count")
            for failed_call in range(1, calls.call_count + 1):
                with self.subTest(replace=failed_call):
                    write_pcm(source, 1000)
                    previous = backend.process(source, root / str(failed_call))
                    hashes = result_hashes(previous)
                    write_pcm(source, 9000)
                    seen = 0

                    def fail_at(source_path, target):
                        nonlocal seen
                        seen += 1
                        if seen == failed_call:
                            raise PermissionError("injected replace failure")
                        return replace(source_path, target)

                    with patch("os.replace", side_effect=fail_at):
                        with self.assertRaises((OSError, ValueError)):
                            backend.process(source, root / str(failed_call))
                    self.assertEqual(result_hashes(previous), hashes)


if __name__ == "__main__":
    unittest.main()
