"""Measure a REAL-DEV/HOLDOUT source file offline ($0, no network): streaming sha256, size, and ffprobe duration/codecs.

usage: python -I probe_source.py <media file> [--ffprobe PATH]

Prints JSON. Every value is measured from the file; nothing is typed. Run it only on a source whose rights a named
person has confirmed (README §2); the tool itself does not check rights.
"""
import hashlib, json, subprocess, sys


def sha256_file(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def probe(path, ffprobe="ffprobe", run=subprocess.run):
    """Return the measured technical facts of ``path``. Raises RuntimeError when ffprobe cannot read the file."""
    done = run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
               capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({done.returncode}): {done.stderr.strip()[:300]}")
    info = json.loads(done.stdout)
    fmt, streams = info.get("format") or {}, info.get("streams") or []
    if fmt.get("duration") in (None, "N/A"):
        raise RuntimeError("ffprobe reported no duration")
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    with open(path, "rb"):
        pass  # fail early and clearly if the file vanished between ffprobe and hashing
    return {
        "sha256": sha256_file(path),
        "bytes": int(fmt.get("size") or 0) or None,
        "duration_ms": round(float(fmt["duration"]) * 1000),
        "container": fmt.get("format_name"),
        "video_codec": video.get("codec_name"),
        "width": video.get("width"),
        "height": video.get("height"),
        "audio_codec": audio.get("codec_name"),
        "audio_channels": audio.get("channels"),
        "audio_sample_rate": int(audio["sample_rate"]) if audio.get("sample_rate") else None,
        "has_audio": bool(audio),
    }


if __name__ == "__main__":
    args = sys.argv[1:]
    tool = args[args.index("--ffprobe") + 1] if "--ffprobe" in args else "ffprobe"
    print(json.dumps(probe(args[0], tool), indent=2))
