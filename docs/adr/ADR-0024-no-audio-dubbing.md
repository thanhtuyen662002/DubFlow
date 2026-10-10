# ADR0024 — Dubbing media without an audio stream

Status: accepted for implementation in leaf166; production qualification pending.

## Evidence and decision

Installed e96 processed a licensed181.623-second Sintel derivative with its
audio removed and eleven supplied English captions. B1 translation/render
completed, but B2 refused AUDIO_STREAM_MISSING before synthesizing any speech.
The exact completed result and scope declaration are preserved in PR195:
https://github.com/thanhtuyen662002/DubFlow/pull/195#issuecomment-6096400639

A valid caption/translation input is sufficient to request dubbing even when
the media has no audio stream. The owned media adapter creates a silent PCM
bed, and the existing real pinned TTS/mixer/render adapters process those cues.
The absence of audio never supplies dialogue semantics. Without usable
captions, the ASR path still reports AUDIO_STREAM_MISSING; OCR semantics remain
the separately qualified subtitle-intelligence capability.

## Timeline and resource rules

Source stream integer duration/time-base determines the PCM frame count:
ceil(duration_ticks * time_base.numerator * sample_rate / time_base.denominator).
When stream duration is absent, the probe's integer canonical duration may
be used. Unknown/nonpositive duration is a typed refusal before model loading.
No frame index, last caption, floating seconds or invented time chooses length.
The generated bed is48kHz stereo signed16-bit PCM. Writes use at most64KiB;
no full-duration PCM array is allocated. WAV RIFF overflow is refused before
writing. Six hours at this format fits the RIFF bound; that calculation is
not actual six-hour execution evidence.

Publish a private sibling WAV only after closing/flushing/fsync, validating
format/frame count and exact byte length, then atomically replacing the target.
A handled interruption or disk write failure removes its partial sibling and
preserves the previous artifact. Process kill may leave an uncommitted partial;
it never becomes a successful final artifact. Restart reconstructs this cheap
bounded PCM bed and reuses the existing independently verified per-cue TTS
checkpoints. Supervisor remains the only SQLite writer.

## Provenance and compatibility

Private mix source_layout is generated-silence-stereo. QC/job-manifest audio
records optional source_audio_origin=generated-silence (or decoded-source).
The standard source_audio.wav filename is retained for editable packs, with
explicit origin so it cannot be presented as speech decoded from the media.
Refused/missing TTS cues remain silent, with per-cue failures and a visible
partial-dub summary. The native summary reads supervisor-committed, bounded,
hash-verified QC source_probe.has_audio before describing fallback audio.

This is an additive private metadata extension; legacy readers ignore the
optional origin, and native summaries remain readable from old QC snapshots.
Public timeline/worker/status schemas and SQLite need no migration. The
existing generation identity hashes b2_audio.py; render identity hashes the
media adapter. New code cannot reuse an old B2 generation or render recipe.
The PCM mixer hashes the actual source WAV/layout and TTS inputs. Native
runtime/start binding keeps old jobs on their original coherent runtime.
Do not replay completed e96 jobs or relabel their exports. Qualification uses
a fresh candidate/job; rollback retains the old runtime/database/artifacts.

## Verification and remaining gates

Deterministic tests execute the real bounded WAV writer and mixer, including
fractional source time-base rounding, interruption, disk failure and overflow,
and test supplied-dialogue/no-duration/no-dialogue wiring. Substituted TTS or
media probes prove wiring only. Native tests verify truthful silent partial
status. All four required exact new HEAD/current-main CI lanes and installed
real no-audio + captions + selected-voice output remain required. Long-form,
batch/recovery, intelligibility and full166/175 qualification stay open.
