"""Issue #337: every path a job writes stays within Windows' 260-character limit."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from engine.dubflow.worker import production_job as worker
from tests.integration.production_local_file import test_disk_usage  # A module import: its tests run once, in their own module.


class OutputPathTests(unittest.TestCase):
    def test_work_below_the_output_folder_stays_within_its_budget(self):
        for separate in (True, False):
            with self.subTest(separate=separate), test_disk_usage.DubbedJobStorageTests().job(separate=separate) as job:
                worker.run_local_file(job.config, Mock())
                output = job.config.output_dir
                deepest = max(len(str(path.relative_to(output))) for path in output.rglob("*"))
                # Leave room for a multi-voice folder (about 25) and a temporary ".partial" suffix.
                self.assertLessEqual(deepest + 33, worker.WORK_PATH_DEPTH, deepest)

    def test_too_long_an_output_folder_stops_before_any_work(self):
        with test_disk_usage.DubbedJobStorageTests().job() as job:
            base = job.root.absolute()
            def folder(length):
                return base / ("x" * (length - len(str(base)) - 1))
            limit = worker.WINDOWS_MAX_PATH - worker.WORK_PATH_DEPTH
            for length, refused in ((limit, False), (limit + 1, True)):
                with self.subTest(length=length):
                    config = replace(job.config, output_dir=folder(length))
                    self.assertEqual(len(str(config.output_dir.absolute())), length)
                    if refused:
                        with self.assertRaises(worker.ProductionJobError) as raised:
                            worker._path_admission(config, windows=True, long_paths=False)
                        self.assertEqual(raised.exception.code, "OUTPUT_PATH_TOO_LONG")
                        self.assertIn(f"dài {length} ký tự", raised.exception.condition)
                    else:
                        worker._path_admission(config, windows=True, long_paths=False)
            # With long paths on, only FFmpeg's working directory still limits the folder.
            worker._path_admission(replace(job.config, output_dir=folder(200)), windows=True, long_paths=True)
            worker._path_admission(replace(job.config, output_dir=folder(400)), windows=False)
            config = replace(job.config, output_dir=folder(250))
            original = worker._path_admission
            with patch.object(worker, "_path_admission", side_effect=lambda value: original(value, windows=True, long_paths=False)), \
                    patch.object(worker.export_publication, "prepare") as prepare:
                with self.assertRaises(worker.ProductionJobError):
                    worker.run_local_file(config, Mock())
                prepare.assert_not_called()
                job.media.extract_audio.assert_not_called()

if __name__ == "__main__":
    unittest.main()
