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
manifest while preserving source audio.

## B2 neural speech candidate (not production-qualified)

The former `vi-builtin-v1` implementation generates character tones. It is
retained for compatibility tests and must not be counted as intelligible
Vietnamese speech. This Draft change selects the Mimic3 VAIS1000 VITS voice,
ONNX Runtime 1.30.0 and the eSpeak API from the pinned Sherpa-ONNX 1.13.8
wheel through `production-tts-v1.json`. It uses the app-owned
model root, a resumable first-use download, archive and extracted-tree hashes,
and 22,050 Hz mono signed-16 PCM. Inference itself is offline. The AUD-0
mixer publishes original audio, dialogue stem and final mix as before.

PyAV is explicitly pinned to 16.1.0: the tested Faster-Whisper 1.2.1 decoder
uses `av.open(metadata_errors=...)`, which failed with the unpinned 19.0.1
wheel. This is a decoder compatibility pin, not evidence of a packaged build.

Local native execution and back-ASR diagnostics are recorded in
`docs/production/REAL_TTS_EVIDENCE.md`. The previous Piper frontend measured
31.94% common-phrase mean CER; the new word-blank frontend measured 11.93%
on that small diagnostic set. The voice remains `qualification-pending`.
A working native WAV or green hermetic
test does not make this candidate release-ready. Before publication, require
speech-quality evidence, actual packaged Windows runs, required CI lanes and
the native phonemizer's license/source obligations described in
`docs/licenses/vietnamese-neural-tts.md`.

If model health, TTS, source decoding or mixing fails, B2 records an actionable
`B2_AUDIO_FALLBACK_TO_B1` downgrade and emits the already-valid B1 result.
Native initialization/inference now runs in a separate child using the same
app-owned interpreter in isolated mode. Bounded protocol, inference timeout,
initialization/after-health crash tests and normal cleanup protect the worker.
Abrupt parent death/process-tree cleanup and packaged B1 recovery still need
qualification; process separation is not a security sandbox.
