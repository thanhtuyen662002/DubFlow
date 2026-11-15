"""Offline tests for api_scope.preflight: the transcript-only scope blocks every non-text payload ($0, no network)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from api_scope import ScopeError, TRANSCRIPT_TEXT_ONLY, preflight  # noqa: E402

SOURCE = {"source_id": "d-s1", "path": "d-s1/lava-flows-2025-09.mp4", "third_party_processing_allowed": True,
          "third_party_processing_scope": "transcript_text_only: Owner decision 2026-11-15T15:39:49Z"}


def request(*blocks, system="Write narration from cues."):
    return {"model": "m", "max_tokens": 10, "system": system, "messages": [{"role": "user", "content": list(blocks)}]}


TEXT = {"type": "text", "text": '{"cues": [{"id": "d-s1#0", "text": "Lava flows are common."}]}'}
B64 = "iVBORw0KGgo" + "A" * 300


class TranscriptTextOnly(unittest.TestCase):
    def blocked(self, req, source=SOURCE):
        with self.assertRaises(ScopeError):
            preflight(req, [source])

    def test_transcript_text_is_allowed(self):
        self.assertEqual(preflight(request(TEXT), [SOURCE]), TRANSCRIPT_TEXT_ONLY)

    def test_a_frame_is_blocked(self):
        self.blocked(request(TEXT, {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": B64}}))

    def test_audio_is_blocked(self):
        self.blocked(request({"type": "audio", "source": {"type": "base64", "media_type": "audio/wav", "data": B64}}))
        self.blocked(request({"type": "input_audio", "input_audio": {"data": B64, "format": "wav"}}))

    def test_video_is_blocked(self):
        self.blocked(request({"type": "video", "source": {"type": "url", "url": "https://x/v.mp4"}}))

    def test_mixed_text_and_image_is_blocked(self):
        self.blocked(request(TEXT, {"type": "image", "source": {"type": "url", "url": "https://x/f.jpg"}}))

    def test_documents_and_files_are_blocked(self):
        self.blocked(request({"type": "document", "source": {"type": "file", "file_id": "file_1"}}))

    def test_missing_or_unknown_scope_is_blocked(self):
        for broken in ({"third_party_processing_scope": None}, {"third_party_processing_scope": ""},
                       {"third_party_processing_scope": "frames_ok"}, {"third_party_processing_allowed": None},
                       {"third_party_processing_allowed": "true"}):
            with self.subTest(broken=broken):
                self.blocked(request(TEXT), dict(SOURCE, **broken))
        with self.assertRaises(ScopeError):
            preflight(request(TEXT), [])

    def test_media_smuggled_into_text_is_blocked(self):
        for text in ("see data:image/png;base64,AAAA", "frame at https://cdn.example/f/12.jpg",
                     "clip https://example.org/a/b/video.mp4?x=1", B64, "the file lava-flows-2025-09.mp4",
                     "D:\\Projects\\DubFlow\\_sandbox\\x2-real-dev\\d-s1\\lava-flows-2025-09.mp4", "audio track.wav"):
            with self.subTest(text=text[:40]):
                self.blocked(request({"type": "text", "text": text}))

    def test_media_in_the_system_prompt_is_blocked(self):
        self.blocked(request(TEXT, system=[{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": B64}}]))
        self.blocked(request(TEXT, system="frame: data:image/jpeg;base64,/9j/"))

    def test_tools_and_mcp_servers_are_blocked(self):
        self.blocked(dict(request(TEXT), tools=[{"type": "web_fetch_20260209", "name": "web_fetch"}]))
        self.blocked(dict(request(TEXT), mcp_servers=[{"type": "url", "url": "https://x", "name": "m"}]))

    def test_one_source_without_clearance_blocks_the_whole_request(self):
        other = {"source_id": "d-s2", "third_party_processing_allowed": False}
        with self.assertRaises(ScopeError):
            preflight(request(TEXT), [SOURCE, other])

    def test_preflight_is_not_a_spending_approval(self):
        # the scope answer carries no budget: it returns the scope name and nothing else
        self.assertIsInstance(preflight(request(TEXT), [SOURCE]), str)


if __name__ == "__main__":
    unittest.main()
