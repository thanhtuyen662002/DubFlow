from __future__ import annotations

import json
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "minimal-pipeline-wiring"))

from b1_pipeline import (  # noqa: E402
    B1Pipeline,
    B1PipelineInterrupted,
    SOURCE_BURNED_IN_TEXT_REMAINS,
    default_fixture_utterances,
)
from engine.dubflow.asr import (  # noqa: E402
    AsrBackendError,
    AsrStageError,
    DeterministicFixtureBackend,
)


FIXTURES = ROOT / "tests" / "integration" / "local_file_slice" / "fixtures"


def _run_hard_kill_child_if_requested() -> None:
    """Run the child half of the restart test before unittest discovery."""

    if os.environ.get("DUBFLOW_B1_CHILD") != "analysis":
        return
    pipeline = B1Pipeline(
        os.environ["DUBFLOW_B1_SOURCE"],
        os.environ["DUBFLOW_B1_OUTPUT"],
        video_width=640,
        video_height=360,
        checkpoint_path=os.environ["DUBFLOW_B1_CHECKPOINT"],
    )
    try:
        pipeline.run(hard_kill_after_analysis=True)
    except B1PipelineInterrupted:
        # Exit without running cleanup/finalization. The checkpoint was
        # atomically committed before this abrupt process boundary.
        os._exit(137)
    os._exit(1)


_run_hard_kill_child_if_requested()


class B1PipelineIntegrationTests(unittest.TestCase):
    def test_portrait_and_landscape_deliver_complete_offline_vietsub_pack(self) -> None:
        cases = (("portrait.mp4", 360, 640, "portrait"), ("landscape.mp4", 640, 360, "landscape"))
        with tempfile.TemporaryDirectory(prefix="dubflow-b1-orientation-") as directory:
            root = Path(directory)
            for fixture_name, width, height, aspect in cases:
                with self.subTest(aspect=aspect):
                    output = root / f"{aspect}.mp4"
                    result = B1Pipeline(
                        FIXTURES / fixture_name,
                        output,
                        video_width=width,
                        video_height=height,
                    ).run()

                    self.assertEqual(result.report["orientation"], aspect)
                    self.assertEqual(result.export.audio_mode, "original")
                    self.assertEqual(result.export.output["width"], width)
                    self.assertEqual(result.export.output["height"], height)
                    self.assertEqual(len(result.transcript.utterances), 2)
                    self.assertEqual(len(result.translation.translations), 2)
                    self.assertEqual(
                        [item.utterance_id for item in result.transcript.utterances],
                        [item.source_utterance_id for item in result.translation.translations],
                    )
                    self.assertEqual(
                        [
                            (item.start.ticks, item.end.ticks)
                            for item in result.transcript.utterances
                        ],
                        [
                            (item.start.ticks, item.end.ticks)
                            for item in result.translation.translations
                        ],
                    )
                    self.assertEqual(
                        [item.source_utterance_id for item in result.translation.translations],
                        [item.source_utterance_id for item in result.subtitles.cues],
                    )
                    self.assertTrue(output.is_file())
                    self.assertTrue(result.srt_path.is_file())
                    self.assertTrue(result.ass_path.is_file())
                    self.assertTrue(result.report_path.is_file())
                    self.assertTrue(Path(result.export.editable_pack["path"]).is_file())

                    srt = result.srt_path.read_text(encoding="utf-8")
                    ass = result.ass_path.read_text(encoding="utf-8")
                    self.assertIn("Xin chào mọi người", srt)
                    self.assertIn("Chào mừng trở lại", ass)

                    pack = json.loads(Path(result.export.editable_pack["path"]).read_text(encoding="utf-8"))
                    self.assertEqual(pack["audio_mode"], "original")
                    self.assertEqual([item["format"] for item in pack["assets"]], ["ass"])
                    self.assertEqual(
                        result.report["artifacts"],
                        {
                            "mp4": str(output),
                            "srt": str(result.srt_path),
                            "ass": str(result.ass_path),
                            "editable_pack": result.export.editable_pack["path"],
                        },
                    )
                    self.assertEqual(result.report["audio"]["policy"], "preserve_original")
                    self.assertEqual(result.report["audio"]["mode"], "original")
                    self.assertEqual(
                        result.report["source"]["metadata_source"],
                        "caller-supplied normalized local-file metadata (#14 boundary)",
                    )
                    self.assertEqual(
                        result.report["subtitle_hashes"]["srt"],
                        "sha256:" + sha256(srt.encode("utf-8")).hexdigest(),
                    )
                    self.assertEqual(
                        result.report["subtitle_hashes"]["ass"],
                        "sha256:" + sha256(ass.encode("utf-8")).hexdigest(),
                    )
                    self.assertEqual(
                        result.report["capability_downgrades"],
                        [SOURCE_BURNED_IN_TEXT_REMAINS],
                    )
                    self.assertEqual(result.report["manual_steps_required"], [])

            self.assertEqual(list(root.glob("*.partial")), [])

    def test_hard_kill_boundary_reuses_persisted_analysis_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dubflow-b1-restart-") as directory:
            root = Path(directory)
            output = root / "resumed.mp4"
            checkpoint = root / "analysis.checkpoint.json"
            child_environment = os.environ.copy()
            child_environment.update(
                {
                    "DUBFLOW_B1_CHILD": "analysis",
                    "DUBFLOW_B1_SOURCE": str(FIXTURES / "landscape.mp4"),
                    "DUBFLOW_B1_OUTPUT": str(output),
                    "DUBFLOW_B1_CHECKPOINT": str(checkpoint),
                }
            )
            child = subprocess.run(
                [sys.executable, str(Path(__file__))],
                cwd=ROOT,
                env=child_environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(child.returncode, 137, child.stderr)
            self.assertTrue(checkpoint.is_file())
            self.assertFalse(output.exists())

            # A fresh worker backend proves that the restart consumes the
            # durable checkpoint instead of invoking ASR again.
            resumed_backend = DeterministicFixtureBackend(default_fixture_utterances())
            resumed = B1Pipeline(
                FIXTURES / "landscape.mp4",
                output,
                video_width=640,
                video_height=360,
                checkpoint_path=checkpoint,
                asr_backend=resumed_backend,
            )
            result = resumed.run()
            self.assertEqual(resumed.analysis_calls, ())
            self.assertGreater(len(result.checkpoint_reused_chunks), 0)
            self.assertTrue(output.is_file())
            self.assertEqual(result.report["analysis"]["reused_chunks"], list(result.checkpoint_reused_chunks))

    def test_corrupt_checkpoint_is_invalidated_and_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dubflow-b1-checkpoint-") as directory:
            root = Path(directory)
            checkpoint = root / "analysis.checkpoint.json"
            checkpoint.write_text("{truncated", encoding="utf-8")
            pipeline = B1Pipeline(
                FIXTURES / "portrait.mp4",
                root / "rebuilt.mp4",
                video_width=360,
                video_height=640,
                checkpoint_path=checkpoint,
            )
            result = pipeline.run()
            self.assertGreater(len(pipeline.analysis_calls), 0)
            self.assertEqual(result.checkpoint_reused_chunks, ())
            self.assertTrue(Path(result.export.output["path"]).is_file())

    def test_one_failed_job_does_not_stop_a_second_job(self) -> None:
        class FailingBackend:
            def transcribe(self, chunk):
                raise AsrBackendError("FIXTURE_FAILURE", "unreadable analysis fixture", retryable=False)

        with tempfile.TemporaryDirectory(prefix="dubflow-b1-isolation-") as directory:
            root = Path(directory)
            failed_output = root / "failed.mp4"
            failed = B1Pipeline(
                FIXTURES / "landscape.mp4",
                failed_output,
                video_width=640,
                video_height=360,
                asr_backend=FailingBackend(),
            )
            with self.assertRaises(AsrStageError):
                failed.run()
            self.assertFalse(failed_output.exists())

            successful_output = root / "successful.mp4"
            successful = B1Pipeline(
                FIXTURES / "portrait.mp4",
                successful_output,
                video_width=360,
                video_height=640,
            )
            result = successful.run()
            self.assertTrue(successful_output.is_file())
            self.assertEqual(result.export.audio_mode, "original")


if __name__ == "__main__":
    unittest.main()
