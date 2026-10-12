import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from engine.dubflow.mix.adapter import MixError
from engine.dubflow.review.mix_io import MAX_ATTEMPTS, PROBE_DELAYS, ReviewStreamingAudioMixer, recover_mix_io


class MixIoRecoveryTests(unittest.TestCase):
    def fixture(self, root, *, code=5, outside=False):
        generation = Path(root) / ('mix-' + 'a' * 64)
        stream = (Path(root) / 'foreign' if outside else generation) / ('stream-' + 'b' * 64)
        stream.mkdir(parents=True)
        journal = stream / 'checkpoint.json'; journal.write_bytes(b'prior valid checkpoint')
        temporary = journal.with_name('checkpoint.json.partial'); temporary.write_bytes(b'new private checkpoint')
        cause = PermissionError(13, 'sharing refusal', str(temporary)); cause.winerror = code
        cause.filename2 = str(journal)
        error = MixError('MIX_IO_FAILED', 'private mix I/O failed'); error.__cause__ = cause
        return generation, journal, temporary, error

    def test_changed_delete_sharing_resumes_without_repairing_files(self):
        with TemporaryDirectory(prefix='dubflow-review-mix-io-') as root:
            generation, journal, temporary, error = self.fixture(root)
            before = {p: p.read_bytes() for p in (journal, temporary)}
            operation = Mock(side_effect=[error, 'verified result'])
            probe = Mock(side_effect=[False, True, True])
            with patch('engine.dubflow.review.mix_io.time.sleep'):
                self.assertEqual(recover_mix_io(operation, generation_root=generation, ready_probe=probe), 'verified result')
            self.assertEqual(operation.call_count, 2)
            self.assertEqual(probe.call_args.args, (journal,))
            self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_blocked_delete_sharing_never_retries_or_changes_permissions(self):
        with TemporaryDirectory(prefix='dubflow-review-mix-io-') as root:
            generation, journal, temporary, error = self.fixture(root)
            operation = Mock(side_effect=error); probe = Mock(return_value=False)
            before = {p: (p.read_bytes(), p.stat().st_mode) for p in (journal, temporary)}
            with patch('engine.dubflow.review.mix_io.time.sleep'):
                with self.assertRaises(MixError): recover_mix_io(operation, generation_root=generation, ready_probe=probe)
            self.assertEqual(operation.call_count, 1)
            self.assertEqual(probe.call_count, len(PROBE_DELAYS))
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mode) for p in before})

    def test_recurrent_sharing_refusal_has_a_hard_attempt_bound(self):
        with TemporaryDirectory(prefix='dubflow-review-mix-io-') as root:
            generation, _, _, error = self.fixture(root, code=32)
            operation = Mock(side_effect=error); probe = Mock(return_value=True)
            with self.assertRaises(MixError): recover_mix_io(operation, generation_root=generation, ready_probe=probe)
            self.assertEqual(operation.call_count, MAX_ATTEMPTS)
            self.assertEqual(probe.call_count, 2 * (MAX_ATTEMPTS - 1))

    def test_foreign_non_journal_missing_and_non_windows_errors_do_not_retry(self):
        with TemporaryDirectory(prefix='dubflow-review-mix-io-') as root:
            for case in range(4):
                case_root = Path(root) / str(case)
                generation, journal, temporary, error = self.fixture(case_root, code=112 if case == 0 else 5, outside=case == 1)
                if case == 2: temporary.unlink()
                if case == 3: error.__cause__.filename2 = str(journal.with_name('other.json'))
                operation = Mock(side_effect=error); probe = Mock(return_value=True)
                with self.assertRaises(MixError): recover_mix_io(operation, generation_root=generation, ready_probe=probe)
                self.assertEqual(operation.call_count, 1)
                probe.assert_not_called()

    def test_normal_success_and_unrelated_failures_are_not_retried(self):
        with TemporaryDirectory(prefix='dubflow-review-mix-io-') as root:
            generation = Path(root) / ('mix-' + 'a' * 64)
            probe = Mock()
            operation = Mock(return_value='ok')
            self.assertEqual(recover_mix_io(operation, generation_root=generation, ready_probe=probe), 'ok')
            operation.assert_called_once(); probe.assert_not_called()
            operation = Mock(side_effect=RuntimeError('inference refusal'))
            with self.assertRaises(RuntimeError): recover_mix_io(operation, generation_root=generation, ready_probe=probe)
            operation.assert_called_once(); probe.assert_not_called()

    def test_journal_retry_keeps_the_same_fsynced_block_state(self):
        from engine.dubflow.mix import MixConfig
        with TemporaryDirectory(prefix='dubflow-review-mix-io-') as root:
            generation, journal, _, error = self.fixture(root)
            mixer = ReviewStreamingAudioMixer(config=MixConfig(), output_dir=generation, generation_root=generation)
            state = {'phase': 'dialogue', 'committed_block': 'unchanged'}
            with (patch('engine.dubflow.mix.streaming.StreamingAudioMixer._save', side_effect=[error.__cause__, None]) as save,
                  patch('engine.dubflow.review.mix_io._delete_available', return_value=True)):
                # Bind the native probe through the existing recovery default;
                # mocks observe the exact same state, never another mixer run.
                with patch('engine.dubflow.review.mix_io.recover_mix_io', side_effect=lambda operation, **kw:
                           recover_mix_io(operation, ready_probe=lambda path: True, **kw)):
                    mixer._save(journal.parent, state)
            self.assertEqual(save.call_count, 2)
            self.assertTrue(all(call.args == (journal.parent, state) for call in save.call_args_list))

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing semantics')
    def test_windows_saver_recovers_after_actual_reader_closes(self):
        import threading
        from engine.dubflow.mix import MixConfig
        with TemporaryDirectory(prefix='dubflow-review-mix-io-') as root:
            generation, journal, _, _ = self.fixture(root)
            mixer = ReviewStreamingAudioMixer(config=MixConfig(), output_dir=generation, generation_root=generation)
            reader = journal.open('rb')
            release = threading.Timer(0.05, reader.close); release.start()
            state = {'phase': 'dialogue', 'block_count': 1, 'stages': {'dialogue': []}}
            try: mixer._save(journal.parent, state)
            finally: release.join(); reader.close()
            import json
            self.assertEqual(json.loads(journal.read_bytes()), state)

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing semantics')
    def test_windows_read_handle_release_is_observed_before_retry(self):
        from engine.dubflow.review.mix_io import _delete_available
        with TemporaryDirectory(prefix='dubflow-review-mix-io-') as root:
            _, journal, _, _ = self.fixture(root)
            with journal.open('rb'):
                self.assertFalse(_delete_available(journal))
            self.assertTrue(_delete_available(journal))
            journal.chmod(0o400)
            try: self.assertFalse(_delete_available(journal))
            finally: journal.chmod(0o600)


if __name__ == '__main__': unittest.main()
