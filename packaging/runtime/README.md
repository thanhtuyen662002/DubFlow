# App-owned production runtime

The Windows release workflow installs the pinned requirements into the
portable Python runtime copied into `runtime/`.  The shipped worker starts
only this interpreter and the FFmpeg/FFprobe binaries under `runtime/media/`.
The worker places the app source after runtime site-packages on `sys.path` so
the repository's release-policy package cannot shadow Argos Translate's
third-party `packaging` distribution.

The CPU local-file profile uses Faster-Whisper `small` and Argos Translate
`en→vi`. Their model bytes are downloaded on first use from
`models/manifests/production-cpu-v1.json`, written to the user-owned model
root, and accepted only after the manifest's exact byte count and SHA-256
match. Interrupted downloads retain a private partial file and resume with a
validated HTTP range request; a CDN that ignores ranges is restarted safely
from byte zero. A model download failure is surfaced as a typed job failure
and never silently replaced by fixture text.

The production-qualified scope is deliberately explicit: a user selects local
Windows media, the supervisor launches the app-owned worker, captions are
obtained from a matching `.srt`/`.vtt` sidecar or CPU ASR, and English text is
translated locally to Vietnamese. The default B1 result emits a playable
H.264/AAC MP4 plus Vietnamese SRT/ASS, QC, editable timeline, and provenance
manifest while preserving source audio. Opt-in B2 additionally loads the
app-owned `vi-builtin-v1` voice pack selected by the same manifest, synthesizes
per-cue signed-16 PCM on CPU, and runs the non-destructive AUD-0 mixer. The
voice pack is pinned by byte count, SHA-256, model/version ID and the approved
`dubflow-builtin-voice-1.0` license; no network or credential is required.
If model health, TTS, source decoding or mixing fails, B2 records an actionable
`B2_AUDIO_FALLBACK_TO_B1` downgrade and emits the already-valid B1 result.
