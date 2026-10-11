"""Opt-in real HTTP + pinned FFmpeg source acquisition probe (no live site).

Run with explicit app-owned binary paths. This produces actual H.264/AAC
objects and bytes, unlike the deterministic injected muxer regressions.
"""

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from engine.dubflow.download.bilibili import BilibiliSourceAdapter
from engine.dubflow.download.generic import GenericUrlAdapter
from engine.dubflow.download.materializer import DownloadError, DownloadErrorCode, MediaMaterializer
from engine.dubflow.download.source_adapter import MediaCandidate, SourceError
from engine.dubflow.download.stream_materializer import FfmpegStreamMuxer, StreamMaterializer


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--ffprobe", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--video-encoder", choices=("libx264", "libopenh264", "h264_mf"), default="libx264")
    args = parser.parse_args()
    root = args.work_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    video, audio = root / "source-video.mp4", root / "source-audio.m4a"
    def generate(arguments, output):
        subprocess.run([str(args.ffmpeg), "-v", "error", "-nostdin", "-y", *arguments, str(output)], check=True, timeout=60)
    generate(["-f", "lavfi", "-i", "color=c=blue:s=160x90:r=25", "-t", "3", "-an", "-c:v", args.video_encoder, "-pix_fmt", "yuv420p"], video)
    generate(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "3", "-vn", "-c:a", "aac"], audio)
    contents = {"/v.m4s": video.read_bytes(), "/a.m4s": audio.read_bytes()}
    calls = []
    interrupt_audio = True
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal interrupt_audio
            route = self.path.split("?", 1)[0]
            if route not in contents:
                self.send_error(404)
                return
            data = contents[route]
            start = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
            calls.append({"route": route, "start": start, "if_range": self.headers.get("If-Range")})
            self.send_response(206 if start else 200)
            self.send_header("ETag", '"source-fixture-v1"')
            self.send_header("Content-Length", str(len(data) - start))
            if start:
                self.send_header("Content-Range", f"bytes {start}-{len(data)-1}/{len(data)}")
            self.end_headers()
            if route == "/a.m4s" and interrupt_audio:
                interrupt_audio = False
                self.wfile.write(data[:len(data)//2])
                self.wfile.flush()
                self.close_connection = True
            else:
                self.wfile.write(data[start:])
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    muxer = FfmpegStreamMuxer(args.ffmpeg, args.ffprobe, trusted_root=args.ffmpeg.parent,
        ffmpeg_sha256=digest(args.ffmpeg), ffprobe_sha256=digest(args.ffprobe))
    output = root / "bilibili.mp4"
    output.write_bytes(b"previous-good-artifact")
    payload = {"code": 0, "data": {"bvid": "BV1AbC2345", "title": "recorded native source", "duration": 3,
        "dash": {"video": [{"baseUrl": base + "/v.m4s?token=fixture", "width": 160, "height": 90}],
                 "audio": [{"baseUrl": base + "/a.m4s?token=fixture"}]}}}
    class BiliTransport:
        def fetch_video(self, source_ref):
            return payload
    def adapter():
        return BilibiliSourceAdapter(BiliTransport(), stream_materializer=StreamMaterializer(muxer, materializer=MediaMaterializer(chunk_bytes=1024)))
    try:
        first = adapter()
        item = first.inspect("BV1AbC2345")
        try:
            first.download(item, output, root=root)
            raise AssertionError("interrupted audio must not publish")
        except SourceError as error:
            assert output.read_bytes() == b"previous-good-artifact"
            interruption = type(error).__name__
        resumed = adapter().download(item, output, root=root)
        streams = muxer.probe(output)
        assert resumed.resumed and {stream.kind for stream in streams} == {"audio", "video"}
        assert sum(call["route"] == "/v.m4s" for call in calls) == 1
        assert any(call["route"] == "/a.m4s" and call["start"] > 0 and call["if_range"] for call in calls)
        class GenericTransport:
            def inspect_url(self, source_url):
                return {"id": "native", "formats": [
                    {"format_id": "audio", "url": base + "/a.m4s", "acodec": "aac", "vcodec": "none"},
                    {"format_id": "video", "url": base + "/v.m4s", "acodec": "none", "vcodec": "h264", "width": 160, "height": 90},
                ]}
        generic = GenericUrlAdapter(GenericTransport(), stream_materializer=StreamMaterializer(muxer))
        generic_result = generic.download(generic.inspect(base + "/recorded"), root / "generic.mp4", root=root)
        generic_streams = muxer.probe(generic_result.path)
        assert {stream.kind for stream in generic_streams} == {"audio", "video"}
        # Matroska does not necessarily publish per-stream duration metadata.
        # Actual source packet extents must validate both streams before mux.
        matroska = root / "source-matroska.mkv"
        generate(["-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-f", "matroska"], matroska)
        metadata = json.loads(subprocess.check_output([str(args.ffprobe), "-v", "error", "-show_streams", "-of", "json", str(matroska)], timeout=60))
        assert all(s.get("duration_ts") is None and s.get("duration") is None for s in metadata["streams"])
        matroska_info = muxer.probe(matroska)
        candidate = MediaCandidate("local-mkv", str(matroska), "local", "video/mp4", 160, 90, True)
        matroska_result = StreamMaterializer(muxer).download(candidate, root / "matroska.mp4", root=root)
        assert {stream.kind for stream in muxer.probe(matroska_result.path)} == {"audio", "video"}
        # A shorter companion must still fail even though its container ends
        # with the video. Container duration cannot stand in for audio extent.
        short_audio = root / "short-audio.m4a"
        generate(["-i", str(audio), "-t", "1", "-c", "copy"], short_audio)
        truncated = root / "source-short-audio.mkv"
        generate(["-i", str(video), "-i", str(short_audio), "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-f", "matroska"], truncated)
        preserved = root / "rejected-short-audio.mp4"
        preserved.write_bytes(b"previous-good-artifact")
        try:
            StreamMaterializer(muxer).download(MediaCandidate("short-mkv", str(truncated), "local", "video/mp4", has_audio=True), preserved, root=root)
            raise AssertionError("short Matroska companion must not be hidden by container duration")
        except DownloadError as error:
            assert error.code is DownloadErrorCode.SOURCE_CHANGED
            assert preserved.read_bytes() == b"previous-good-artifact"
        # Decode the selected audio and video, not merely their metadata.
        for path in (output, generic_result.path, matroska_result.path):
            subprocess.run([str(args.ffmpeg), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], check=True, timeout=60)
        report = {"schema_version": 1, "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"])),
            "fixture": "3-second generated color and sine, local HTTP; no live provider or installed GUI",
            "ffmpeg_sha256": digest(args.ffmpeg), "ffprobe_sha256": digest(args.ffprobe),
            "fixture_video_encoder": args.video_encoder,
            "interruption": interruption, "calls": calls, "resumed": resumed.to_dict(),
            "generic": generic_result.to_dict(), "streams": [vars(stream) for stream in streams], "decode": "passed",
            "matroska": {"source_info": [vars(s) for s in matroska_info], "publication": matroska_result.to_dict(), "short_companion_refusal": "passed"}}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"report": str(args.report), "sha256": digest(args.report), "decode": "passed"}))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
