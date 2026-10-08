"""Numerical compatibility and real filesystem/restart tests for production PCM."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.mix import (FileSource, FileSegment, StreamLimits, StreamingAudioMixer,
    LocalAudioMixer, SourceAudio, MixSegment, MixConfig, MixError, ResourceProfile)
from engine.dubflow.mix.streaming import file_hash
from test_adapter import make_wave

BASE = TimeBase(1, 1000)
INPUT = "sha256:" + "1" * 64
CONFIG = MixConfig(resource=ResourceProfile(max_memory_mb=512))
LIMITS = StreamLimits(block_frames=127, disk_reserve_bytes=0)


def point(ticks): return TimePoint(ticks, BASE)


def fixture(root, *, channels=2, start=0, value=3000):
    source = root / "source.wav"
    payload = make_wave(1000, channels, 1000, [(-1 if index % 7 == 0 else 1) * value for index in range(1000)])
    source.write_bytes(payload)
    files, legacy = [], []
    for index, (lo, hi, rate, layout) in enumerate(((103, 513, 1500, 1), (391, 777, 500, 2), (810, 910, 2000, 1))):
        identifier = "s" + str(index)
        # All chosen durations are exact in their native rate and timeline.
        audio = make_wave(rate, layout, (hi - lo) * rate // 1000, -15001 if index == 1 else 28000)
        path = root / (identifier + ".wav"); path.write_bytes(audio)
        files.append(FileSegment(identifier, "u" + identifier, point(start + lo), point(start + hi), path, file_hash(path)))
        legacy.append(MixSegment(identifier, "u" + identifier, point(start + lo), point(start + hi), audio))
    return FileSource(source, point(start), point(start + 1000), "source", "stereo" if channels == 2 else "mono"), files, SourceAudio("source", payload, point(start), point(start + 1000), "stereo" if channels == 2 else "mono"), legacy


class StreamingTests(unittest.TestCase):
    def test_source_metadata_and_asymmetric_stereo_rounding_are_preserved(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source_pcm = make_wave(1000, 1, 1000, 1000)
            # A valid extra RIFF chunk is part of the exact original snapshot.
            payload = source_pcm + b"JUNK" + struct.pack("<I", 4) + b"meta"
            payload = payload[:4] + struct.pack("<I", len(payload) - 8) + payload[8:]
            source_path = root / "metadata.wav"; source_path.write_bytes(payload)
            tts = make_wave(1000, 2, 200, 0)
            tts = tts[:44] + struct.pack("<hh", -32768, 32767) * 200
            tts_path = root / "odd.wav"; tts_path.write_bytes(tts)
            source = FileSource(source_path, point(0), point(1000))
            segment = FileSegment("odd", "uodd", point(300), point(500), tts_path, file_hash(tts_path))
            old = LocalAudioMixer(output_dir=root / "old", config=CONFIG).mix(
                SourceAudio(source.source_id, payload, source.start, source.end),
                [MixSegment("odd", "uodd", point(300), point(500), tts)], input_hash=INPUT)
            result = StreamingAudioMixer(output_dir=root / "new", config=CONFIG, limits=LIMITS).mix(source, [segment], input_hash=INPUT)
            self.assertEqual(Path(result.original_audio.path).read_bytes(), payload)
            self.assertEqual(Path(result.dialogue_stem.path).read_bytes(), Path(old.dialogue_stem.path).read_bytes())
            self.assertEqual(Path(result.final_mix.path).read_bytes(), Path(old.final_mix.path).read_bytes())

    def test_tick_identity_above_float_safe_integer_is_exact(self):
        with TemporaryDirectory() as folder:
            root = Path(folder); base = TimeBase(1, 48000)
            start = 9007199254741001
            p = lambda delta: TimePoint(start + delta, base)
            source_path = root / "source.wav"; source_path.write_bytes(make_wave(48000, 2, 4096, 1234))
            tts_path = root / "tts.wav"; tts_path.write_bytes(make_wave(16000, 1, 512, -5679))
            result = StreamingAudioMixer(output_dir=root / "new", config=CONFIG, limits=LIMITS).mix(
                FileSource(source_path, p(0), p(4096)), [FileSegment("s", "u", p(1023), p(2559), tts_path)], input_hash=INPUT)
            self.assertEqual(result.duck_windows[0].start_sample, start + 1023)
            self.assertEqual(result.duck_windows[0].end_sample, start + 2559)
            self.assertEqual(result.source_start.ticks, start)
            self.assertEqual(result.final_mix.frame_count, 4096)

    def test_native_numeric_parity_across_blocks_layouts_and_signed_ticks(self):
        for channels in (1, 2):
            for start in (-503, 0, 1077):
                with self.subTest(channels=channels, start=start), TemporaryDirectory() as folder:
                    root = Path(folder); source, files, old_source, old_segments = fixture(root, channels=channels, start=start)
                    config = replace(CONFIG, max_peak=19000, max_rms_milli=450)
                    old = LocalAudioMixer(output_dir=root / "old", config=config).mix(old_source, old_segments, input_hash=INPUT)
                    new = StreamingAudioMixer(output_dir=root / "new", config=config, limits=LIMITS).mix(source, files, input_hash=INPUT)
                    for before, after in zip((old.original_audio, old.dialogue_stem, old.final_mix), (new.original_audio, new.dialogue_stem, new.final_mix)):
                        self.assertEqual(Path(before.path).read_bytes(), Path(after.path).read_bytes())
                        self.assertEqual(before.metrics, after.metrics)
                    self.assertEqual(old.duck_windows, new.duck_windows)
                    self.assertEqual(old.warnings, new.warnings)
                    self.assertEqual(new.provenance.producer_version, "2.0.0")
                    self.assertEqual(new.provenance.backend_id, "pcm-stream-duck-v1")

    def test_resume_every_phase_verifies_prefix_and_discards_only_private_tail(self):
        for phase in ("copy", "dialogue", "combined", "output"):
            with self.subTest(phase=phase), TemporaryDirectory() as folder:
                root = Path(folder); source, segments, _, _ = fixture(root)
                events = []
                def stop(current, done, total, digest):
                    events.append((current, done, digest))
                    if current == phase and done == 2: raise RuntimeError("power interruption")
                mixer = StreamingAudioMixer(output_dir=root / "new", config=CONFIG, limits=LIMITS, checkpoint=stop)
                with self.assertRaisesRegex(RuntimeError, "power interruption"): mixer.mix(source, segments, input_hash=INPUT)
                generation = next((root / "new").glob("stream-*"))
                self.assertFalse((generation / "mix_document.json").exists())
                name = {"copy": "original.wav.part", "dialogue": "dialogue.pcm", "combined": "combined.pcm", "output": "final.wav.part"}[phase]
                with (generation / name).open("ab") as handle: handle.write(b"uncommitted interrupted tail")
                resumed_events = []
                result = StreamingAudioMixer(output_dir=root / "new", config=CONFIG, limits=LIMITS,
                    checkpoint=lambda *args: resumed_events.append(args)).mix(source, segments, input_hash=INPUT)
                self.assertTrue((generation / "mix_document.json").is_file())
                self.assertEqual(result.original_audio.content_hash, file_hash(source.path))
                self.assertTrue(any(item[0] == phase and item[1] == 3 for item in resumed_events))
                self.assertFalse(any(item[0] == phase and item[1] == 1 for item in resumed_events))
                # Reuse validates actual complete artifact metrics, not only paths.
                self.assertEqual(StreamingAudioMixer(output_dir=root / "new", config=CONFIG, limits=LIMITS).mix(source, segments, input_hash=INPUT), result)

    def test_actual_process_exit_then_restart(self):
        with TemporaryDirectory() as folder:
            root = Path(folder); source, segments, _, _ = fixture(root)
            script = root / "kill_child.py"
            script.write_text("""import os,sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom engine.dubflow.asr import TimeBase,TimePoint\nfrom engine.dubflow.mix import *\nfrom engine.dubflow.mix.streaming import file_hash\nr=Path(sys.argv[2]); p=lambda x:TimePoint(x,TimeBase(1,1000))\ns=FileSource(r/'source.wav',p(0),p(1000),'source','stereo')\nf=[FileSegment('s'+str(i),'us'+str(i),p(lo),p(hi),r/('s'+str(i)+'.wav'),file_hash(r/('s'+str(i)+'.wav'))) for i,(lo,hi) in enumerate(((103,513),(391,777),(810,910)))]\ndef checkpoint(phase,done,total,digest):\n if phase=='combined' and done==2: os._exit(42)\nStreamingAudioMixer(output_dir=r/'new',config=MixConfig(resource=ResourceProfile(max_memory_mb=512)),limits=StreamLimits(block_frames=127,disk_reserve_bytes=0),checkpoint=checkpoint).mix(s,f,input_hash='sha256:'+'1'*64)\n""", encoding="utf-8")
            process = subprocess.run([sys.executable, str(script), str(Path(__file__).resolve().parents[2]), str(root)], capture_output=True, text=True, timeout=60)
            self.assertEqual(process.returncode, 42, process.stderr)
            generation = next((root / "new").glob("stream-*"))
            self.assertFalse((generation / "mix_document.json").exists())
            result = StreamingAudioMixer(output_dir=root / "new", config=CONFIG, limits=LIMITS).mix(source, segments, input_hash=INPUT)
            self.assertEqual(result.final_mix.frame_count, 1000)

    def test_corrupt_committed_block_or_journal_never_promotes(self):
        for corruption in ("bytes", "json", "identity", "counts"):
            with self.subTest(corruption=corruption), TemporaryDirectory() as folder:
                root = Path(folder); source, segments, _, _ = fixture(root)
                def stop(phase, done, *args):
                    if phase == "dialogue" and done == 2: raise RuntimeError("stop")
                with self.assertRaises(RuntimeError):
                    StreamingAudioMixer(output_dir=root / "new", config=CONFIG, limits=LIMITS, checkpoint=stop).mix(source, segments, input_hash=INPUT)
                generation = next((root / "new").glob("stream-*")); journal = generation / "checkpoint.json"
                if corruption == "bytes":
                    with (generation / "dialogue.pcm").open("r+b") as handle: handle.write(b"XX")
                elif corruption == "json": journal.write_text("{broken", encoding="utf-8")
                else:
                    data = json.loads(journal.read_text())
                    if corruption == "identity": data["identity"] = INPUT
                    else: data["stages"]["output"].append({})
                    journal.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(MixError) as raised:
                    StreamingAudioMixer(output_dir=root / "new", config=CONFIG, limits=LIMITS).mix(source, segments, input_hash=INPUT)
                self.assertEqual(raised.exception.code, "MIX_CHECKPOINT_INVALID")
                self.assertFalse((generation / "mix_document.json").exists())

    def test_changed_inputs_and_recipe_preserve_previous_generation(self):
        with TemporaryDirectory() as folder:
            root = Path(folder); source, segments, _, _ = fixture(root)
            mixer = StreamingAudioMixer(output_dir=root / "new", config=CONFIG, limits=LIMITS)
            original = mixer.mix(source, segments, input_hash=INPUT)
            pinned = {Path(item.path): file_hash(Path(item.path)) for item in (original.original_audio, original.dialogue_stem, original.final_mix)}
            changed = replace(segments[0], status="failed", condition="voice changed", content_hash=None)
            second = mixer.mix(source, [changed, *segments[1:]], input_hash="sha256:" + "2" * 64)
            third = StreamingAudioMixer(output_dir=root / "new", config=replace(CONFIG, duck_gain_milli=500), limits=LIMITS).mix(source, segments, input_hash=INPUT)
            self.assertEqual(len({original.final_mix.path, second.final_mix.path, third.final_mix.path}), 3)
            self.assertTrue(all(file_hash(path) == digest for path, digest in pinned.items()))

    def test_invalid_tts_is_isolated_and_source_is_preserved(self):
        with TemporaryDirectory() as folder:
            root = Path(folder); source, segments, _, _ = fixture(root)
            bad = root / "bad.wav"; bad.write_bytes(b"not wav")
            segments = [replace(segments[0], path=bad, content_hash=None), replace(segments[1], content_hash=INPUT), replace(segments[2], status="missing", path=None)]
            result = StreamingAudioMixer(output_dir=root / "new", config=CONFIG, limits=LIMITS).mix(source, segments, input_hash=INPUT)
            self.assertEqual({item.code for item in result.failures}, {"AUDIO_CORRUPT", "AUDIO_HASH_MISMATCH", "TTS_MISSING"})
            self.assertEqual(result.original_audio.content_hash, file_hash(source.path))
            self.assertEqual(Path(result.final_mix.path).read_bytes(), source.path.read_bytes())
            self.assertEqual(result.duck_windows, ())

    def test_bound_disk_io_and_input_mutation_fail_without_document(self):
        with TemporaryDirectory() as folder:
            root = Path(folder); source, segments, _, _ = fixture(root)
            with patch("engine.dubflow.mix.streaming.shutil.disk_usage", return_value=type("Disk", (), {"free": 0})()):
                with self.assertRaises(MixError) as raised:
                    StreamingAudioMixer(output_dir=root / "disk", config=CONFIG, limits=LIMITS).mix(source, segments, input_hash=INPUT)
                self.assertEqual(raised.exception.code, "AUDIO_DISK_REQUIRED")
            with patch("engine.dubflow.mix.streaming.os.fsync", side_effect=OSError("disk disconnected")):
                with self.assertRaises(MixError) as raised:
                    StreamingAudioMixer(output_dir=root / "io", config=CONFIG, limits=LIMITS).mix(source, segments, input_hash=INPUT)
                self.assertEqual(raised.exception.code, "MIX_IO_FAILED")
            def mutate(phase, done, *args):
                if phase == "output" and done == 1:
                    with segments[0].path.open("r+b") as handle: handle.seek(44); handle.write(b"XX")
            with self.assertRaises(MixError) as raised:
                StreamingAudioMixer(output_dir=root / "mutation", config=CONFIG, limits=LIMITS, checkpoint=mutate).mix(source, segments, input_hash=INPUT)
            self.assertEqual(raised.exception.code, "AUDIO_INPUT_CHANGED")
            self.assertFalse(any((root / "mutation").glob("*/mix_document.json")))

    def test_completed_artifact_corruption_is_rejected(self):
        with TemporaryDirectory() as folder:
            root = Path(folder); source, segments, _, _ = fixture(root)
            mixer = StreamingAudioMixer(output_dir=root / "new", config=CONFIG, limits=LIMITS)
            result = mixer.mix(source, segments, input_hash=INPUT)
            with Path(result.final_mix.path).open("r+b") as handle: handle.seek(44); handle.write(b"XX")
            with self.assertRaises(MixError) as raised: mixer.mix(source, segments, input_hash=INPUT)
            self.assertEqual(raised.exception.code, "MIX_CHECKPOINT_INVALID")

    def test_runtime_and_memory_and_path_bounds(self):
        with TemporaryDirectory() as folder:
            root = Path(folder); source, segments, _, _ = fixture(root)
            with patch("engine.dubflow.mix.streaming.importlib.metadata.version", return_value="0.0.0"):
                with self.assertRaises(MixError) as raised:
                    StreamingAudioMixer(output_dir=root / "new", config=CONFIG, limits=LIMITS).mix(source, segments, input_hash=INPUT)
                self.assertEqual(raised.exception.code, "AUDIO_RUNTIME_VERSION_MISMATCH")
            with self.assertRaises(MixError): StreamingAudioMixer(output_dir=root / "new", config=replace(CONFIG, resource=ResourceProfile(max_memory_mb=64)))
            with self.assertRaises(MixError): FileSource(Path("relative.wav"), point(0), point(1000))
            with self.assertRaises(MixError): StreamingAudioMixer(output_dir=root / "bound", config=CONFIG, limits=replace(LIMITS, max_blocks=1)).mix(source, segments, input_hash=INPUT)


if __name__ == "__main__": unittest.main()
