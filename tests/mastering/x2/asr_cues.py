"""Run LOCAL ASR on one rights-cleared source and write its cue file in the README §3.3 format ($0, no network).

usage: python -I asr_cues.py <media> --source-id d-s1 --language en --model-dir <faster-whisper model dir>
                             --base <sandbox dir> --cues <cue file, relative to base>
                             [--manifest <manifest.json> --split REAL-DEV]

- The model loads with ``local_files_only=True``: nothing is downloaded and no API is called.
- ASR that completes with no segments is ``asr_status: ok`` and ``cues: []``: no speech was DETECTED. That is a
  measurement, not proof the source is silent (speech_ratio_note in the file).
- ASR that raises is ``asr_status: failed``. The file keeps the error and has no ``cues`` key, so it can never pass
  as a no-speech source.
- Cue times are copied from the model (seconds -> whole ms), never edited. A cue that is impossible for this
  file (end <= start, start < 0, end after the measured duration) goes to ``rejected`` with its reason; it is kept
  for audit and not counted (README §3.3: cue rejections are recorded, never deleted).
- ``--manifest`` writes the technical fields only. Rights fields are never written (README §2).
"""
import hashlib, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_source  # noqa: E402
from validate_manifest import speech_ratio  # noqa: E402

RIGHTS_FIELDS = frozenset({"rights_confirmed_by", "rights_confirmed_at", "third_party_processing_allowed", "license",
                           "license_evidence"})
TECH_FIELDS = frozenset({"path", "sha256", "duration_ms", "speech_ratio", "cues_path", "cues_sha256", "cues_method",
                         "asr_status", "asr_model", "cue_count", "cue_rejected_count"})
PARAMS = {"device": "cpu", "compute_type": "int8", "beam_size": 5, "vad_filter": True,
          "condition_on_previous_text": False, "word_timestamps": True, "local_files_only": True}
NOTE = "speech_ratio = union of accepted cue spans / duration_ms, not rounded; 0 means no speech detected by this run"


def _versions():
    from importlib import metadata
    out = {}
    for name in ("faster-whisper", "ctranslate2"):
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def faster_whisper_transcriber(model_dir, language):
    """The real transcriber: local faster-whisper, CPU int8, never downloads."""
    def transcribe(media):
        from faster_whisper import WhisperModel  # imported lazily so tests and CI need no ASR runtime
        model = WhisperModel(str(model_dir), device=PARAMS["device"], compute_type=PARAMS["compute_type"],
                             local_files_only=True)
        segments, _info = model.transcribe(str(media), language=language, beam_size=PARAMS["beam_size"],
                                           vad_filter=PARAMS["vad_filter"],
                                           condition_on_previous_text=PARAMS["condition_on_previous_text"],
                                           word_timestamps=PARAMS["word_timestamps"])
        return list(segments)  # the generator does the work; materialise it inside the try of the caller
    return transcribe


def _ms(seconds):
    return round(float(seconds) * 1000)


def build_cues(segments, duration_ms):
    """Cue spans follow the spoken words: a Whisper segment's own start/end often runs over the silence before or
    after it (Track A 2026-11-15: 25.9 s of cues over 12.4 s of speech), so the first and last word bound the cue.
    The segment span is kept for audit. Without word times the segment span is used and marked ``timing: segment``."""
    accepted, rejected = [], []
    for seg in segments:
        words = [w for w in (getattr(seg, "words", None) or ()) if w.end is not None and w.start is not None]
        if words:
            start, end, timing = _ms(words[0].start), _ms(words[-1].end), "words"
        else:
            start, end, timing = _ms(seg.start), _ms(seg.end), "segment"
        cue = {"start_ms": start, "end_ms": end, "text": (seg.text or "").strip(), "timing": timing,
               "segment_start_ms": _ms(seg.start), "segment_end_ms": _ms(seg.end)}
        reason = None
        if cue["start_ms"] < 0 or cue["end_ms"] <= cue["start_ms"]:
            reason = "invalid_span"
        elif cue["end_ms"] > duration_ms:
            reason = "after_source_end"
        (rejected if reason else accepted).append(dict(cue, reason=reason) if reason else cue)
    return accepted, rejected


def run(media, *, source_id, language, model_dir, base, cues_rel, transcriber=None, probe=probe_source.probe,
        versions=_versions):
    """Probe ``media``, run ASR, write the cue file under ``base`` and return the technical manifest patch."""
    base = os.path.abspath(base)
    media_abs = os.path.abspath(media)
    if os.path.commonpath([base, media_abs]) != base:
        raise ValueError("the media file must be inside --base so the manifest path can be verified")
    model_name = os.path.basename(os.path.normpath(str(model_dir)))
    doc = {"source_id": source_id, "asr_model": model_name, "language": language, "params": PARAMS,
           "versions": versions(), "speech_ratio_note": NOTE}
    accepted, rejected = None, []
    try:
        facts = probe(media_abs)
    except Exception as error:  # an unreadable file is a failed run with a record, never a traceback only
        facts = {"sha256": probe_source.sha256_file(media_abs), "duration_ms": None}
        doc.update(asr_status="failed", error=f"probe: {type(error).__name__}: {error}"[:500])
    doc.update(media_sha256=facts["sha256"], duration_ms=facts["duration_ms"])
    if "asr_status" not in doc and facts.get("has_audio") is False:
        doc.update(asr_status="failed", error="no audio stream: ASR cannot run (this is not a no-speech result)")
    if "asr_status" not in doc:
        transcribe = transcriber or faster_whisper_transcriber(model_dir, language)
        try:
            segments = list(transcribe(media_abs))
            accepted, rejected = build_cues(segments, facts["duration_ms"])
            doc.update(asr_status="ok", cues=accepted, rejected=rejected)
        except Exception as error:  # any failure is recorded as failed, never as "no speech"
            accepted, rejected = None, []
            doc.update(asr_status="failed", error=f"{type(error).__name__}: {error}"[:500])
    cues_abs = os.path.join(base, cues_rel)
    os.makedirs(os.path.dirname(cues_abs) or ".", exist_ok=True)
    raw = json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")
    with open(cues_abs, "wb") as fh:
        fh.write(raw)
    patch = {"path": os.path.relpath(media_abs, base).replace(os.sep, "/"), "sha256": facts["sha256"],
             "duration_ms": facts["duration_ms"], "cues_path": cues_rel.replace(os.sep, "/"),
             "cues_sha256": hashlib.sha256(raw).hexdigest(), "cues_method": "local-asr",
             "asr_status": doc["asr_status"], "asr_model": model_name,
             "cue_count": len(accepted) if accepted is not None else None,
             "cue_rejected_count": len(rejected),
             "speech_ratio": speech_ratio(accepted, facts["duration_ms"]) if accepted is not None else None}
    return patch


def apply_patch(manifest_path, split, source_id, patch):
    """Write technical fields into one source of the manifest. Refuses rights fields and unknown keys."""
    bad = set(patch) - TECH_FIELDS
    if bad & RIGHTS_FIELDS:
        raise ValueError("refusing to write rights fields: " + ", ".join(sorted(bad & RIGHTS_FIELDS)))
    if bad:
        raise ValueError("unknown fields: " + ", ".join(sorted(bad)))
    with open(manifest_path, encoding="utf-8") as fh:
        m = json.load(fh)
    matches = [s for s in m["splits"][split]["sources"] if s["source_id"] == source_id]
    if len(matches) != 1:
        raise ValueError(f"{split} has {len(matches)} sources with id {source_id}")
    matches[0].update(patch)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=2)
    return matches[0]


if __name__ == "__main__":
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    patch = run(a[0], source_id=opt("--source-id"), language=opt("--language"), model_dir=opt("--model-dir"),
                base=opt("--base"), cues_rel=opt("--cues"))
    if opt("--manifest"):
        apply_patch(opt("--manifest"), opt("--split", "REAL-DEV"), opt("--source-id"), patch)
    print(json.dumps(patch, indent=2))
    sys.exit(0 if patch["asr_status"] == "ok" else 1)
