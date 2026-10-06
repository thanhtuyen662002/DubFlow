from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

from engine.dubflow.tts.adapter import TtsError
from engine.dubflow.tts.mimic3_native import clause_words, encode_words
from engine.dubflow.tts.native_process import NativeProcess


class FrontendTests(unittest.TestCase):
    def test_compound_dental_symbol_and_language_metadata(self) -> None:
        self.assertEqual(clause_words("t̪_ˈiɛ6_ (en)_v_i_(vi)_", 0x80028), [["t̪", "ˈ", "i", "ɛ", "6"], ["v", "i", "."]])

    def test_clause_and_sentence_punctuation(self) -> None:
        for terminator, punctuation in ((0x41014, ","), (0x4001E, ","), (0x82028, "."), (0x8302D, "."), (0x94000, None)):
            with self.subTest(terminator=terminator):
                self.assertEqual(clause_words("a_", terminator), [["a", punctuation]] if punctuation else [["a"]])

    def test_word_blanks_and_unknown_symbols_are_explicit(self) -> None:
        tokens = {"^": 1, "$": 2, "_": 0, "#": 5, "t̪": 30, "a": 14, ".": 4}
        ids, unknown = encode_words([["t̪", "a"], ["d", "."]], tokens)
        self.assertEqual(ids, [1, 0, 30, 0, 14, 0, 5, 0, 4, 0, 5, 0, 2])
        self.assertEqual(unknown, ("U0064",))


class NativeFailureTests(unittest.TestCase):
    def start(self, code: str, timeout: float = 1.0):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "child.py"
            script.write_text(code, encoding="utf-8")
            pack = SimpleNamespace(path=Path(directory), noise_scale=0.0, noise_scale_w=0.0)
            return NativeProcess(pack, timeout=timeout, command=[sys.executable, "-I", "-u", str(script)])

    def test_native_initialization_crash_is_typed_and_parent_survives(self) -> None:
        with self.assertRaises(TtsError) as raised:
            self.start("import os; os._exit(77)\n")
        self.assertEqual(raised.exception.code, "TTS_NATIVE_EXITED")

    def test_native_initialization_timeout_is_bounded(self) -> None:
        with self.assertRaises(TtsError) as raised:
            self.start("import time; time.sleep(60)\n", timeout=0.1)
        self.assertEqual(raised.exception.code, "TTS_NATIVE_TIMEOUT")

    def test_oversized_native_reply_is_rejected(self) -> None:
        with self.assertRaises(TtsError):
            self.start("import sys; sys.stdout.write('x'*5000+'\\n'); sys.stdout.flush()\n")

    def test_runtime_crash_after_health_is_typed(self) -> None:
        code = "import sys,json,os\nsys.stdin.readline()\nprint(json.dumps({'schema_version':1,'sequence':0,'ok':True,'frontend':'mimic3-word-blanks-v1'}),flush=True)\nsys.stdin.readline()\nos._exit(78)\n"
        bridge = self.start(code)
        try:
            with self.assertRaises(TtsError) as raised:
                bridge.generate("Xin chào", sid=0, speed=1.0)
            self.assertEqual(raised.exception.code, "TTS_NATIVE_EXITED")
        finally:
            bridge.close()


if __name__ == "__main__":
    unittest.main()
