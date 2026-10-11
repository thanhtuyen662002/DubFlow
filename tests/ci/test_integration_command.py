from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/ci"))
from run_integration import run_metadata


class MetadataCommandTests(unittest.TestCase):
    def test_real_success_keeps_machine_json_out_of_log_and_emits_result(self):
        output = io.StringIO()
        with redirect_stdout(output):
            run_metadata([sys.executable, "-c", "import sys; sys.stdout.write('metadata-json-' * 100000)"])
        self.assertIn("Cargo metadata exit=0", output.getvalue())
        self.assertIn("stdout_bytes=1400000", output.getvalue())
        self.assertNotIn("metadata-json-", output.getvalue())

    def test_real_failure_retains_bounded_diagnostics_and_nonzero_exit(self):
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaises(subprocess.CalledProcessError) as context:
                run_metadata([sys.executable, "-c", r"import sys; sys.stderr.buffer.write(b'x' * 100000 + b'\xff\nactual failure'); sys.stdout.write('partial JSON'); sys.exit(7)"])
        self.assertEqual(context.exception.returncode, 7)
        self.assertIn("actual failure", output.getvalue())
        self.assertIn("partial JSON", output.getvalue())
        self.assertIn("Cargo metadata exit=7", output.getvalue())
        self.assertLess(len(output.getvalue()), 19000)


if __name__ == "__main__":
    unittest.main()
