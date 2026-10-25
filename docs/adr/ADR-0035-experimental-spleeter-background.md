# ADR-0035: Opt-in experimental Spleeter background bed

Status: Proposed — owner-requested local engine slice of #171. This does not
qualify the full production audio/hardware profile or wire desktop controls.

## Decision

The optional worker argument `separate_background` defaults to false and must
be a boolean. Only dubbing an audio-bearing source requests the pinned Spleeter
2-stem ONNX profile and its notice. The previous energy-gate backend stays
unwired. Native numpy/onnxruntime imports remain lazy; CPU inference uses two
threads. Failed download, decoding, inference, checkpoint publication or the
safety gate appends `SEPARATION_SKIPPED: code: condition` and retains AUD-0.
A failure reading/mixing the selected bed permits one retry with the original
bed in a separate B2 generation. Existing TTS/B1 recovery continues to apply.

Separation runs before B2, using the same app-owned `extract_audio` recipe:
the selected voice profile's PCM rate, stereo and the exact source-video
origin. This extra optional decode keeps analysis independently checkpointed
and avoids adding optional model lifecycle work inside TTS. B2 still extracts
and retains its own original source WAV. No source ticks or cue semantics change.

FFmpeg pipes 44.1 kHz stereo float PCM to a disk spool. Centered STFT uses a
4096-point periodic Hann and 1024 hop. Both models receive the first 1024
magnitude bins in globally anchored 512-frame splits. Two splits per batch
bound FFT/model working memory independently of duration. Accompaniment ratio
masks use squared estimates and epsilon, extend the high bins by per-frame
mean, and inverse STFT uses window-square normalization. The unnormalized
overlap-add tail crosses batch boundaries. Atomic hashed batch receipts retain
PCM and the tail/weights so restart skips completed inference. Their namespace
pins source bytes, both models, adapter code and batch recipe; an invalid
receipt is recomputed. Float PCM and stderr spool to disk, never duration-sized
RAM arrays. Storage errors use the same fallback.

FFmpeg converts the result back to the source WAV's rate and channels as
signed-16 PCM. Reject non-finite intermediate/output samples, converted peak
above 0.999, format changes or duration differences over 50 ms. Validate the
candidate before replacing `work_dir/separation/background.wav`. The stage
checkpoint binds source hash, audio decode identity, PCM format, media binary,
model manifest and adapter hash; verified output hashes authorize reuse.

## Artifacts and compatibility

The B2 generation identity includes the opt-in request, actual bed mode, stage
identity and background hash. Toggling the option invalidates mix/render/QC
without changing translation or the shared per-cue TTS synthesis cache. B2
copies the bed to its private `background_stem.wav`, protecting previous exports
if separation is regenerated. The original WAV, background, Vietnamese dialogue
and final mix are separately hashed and included in the editable pack.

QC and localization manifest gain additive `background` metadata: mode
`spleeter-2stems` or `original`, plus successful processing metrics and profile
identity. `seconds` is elapsed processing time; RMS and `removed_db` are dBFS,
with removed energy measured on source minus background. Silence floors at
-200 dBFS to keep JSON finite. These are signal/safety measurements, not
residual-speech or perceptual-quality scores. A later B1 fallback records
`original` as the actual exported background.

The existing manifest's audio section records `bed: spleeter-2stems|source`.
The strict v1 mix document remains unchanged; `source_layout` describes the
selected accompaniment and `original_audio` there is the mixer's retained input
bed. The localization manifest explicitly maps `source_audio` to the unmodified
source and `background_stem` to the accompaniment. `B2AudioResult` exposes both.
With `background_path=None`, B2 keeps existing paths and serialized bytes.

No durable DB, generic worker envelope, public mix contract or timeline schema
migration is needed. Old exports without these additive fields remain readable;
missing `background`/`bed` means the original bed. Existing jobs retain their
pinned runtime/model/contract versions; this adapter requires a new runtime pin.
Do not relabel old output or mutate a published generation. Downgrade preserves
existing exports and can regenerate with the option disabled.

## Verification limits

Offline fake-model tests cover STFT reconstruction, masks, split boundaries,
checkpoint resume/corruption, output safety, WAV conversion and optional worker
fallbacks. The environment-gated real-model synthetic smoke proves execution
and safety only. The measured owner spike is limited speech/music evidence;
human listening, residual speech, SFX damage and long-form resource/stability
qualification remain required for production promotion. This option remains
experimental and off by default.
