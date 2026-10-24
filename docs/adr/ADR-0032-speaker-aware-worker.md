# ADR-0032: Opt-in speaker voices in the production worker

Status: Proposed, Issue #170. Worker-only integration; no supervisor or desktop change.

## Decision

The existing command args accept optional `speaker_voices`, a list of 2 to 64
distinct approved VieNeu preset IDs. Omission (or null) retains the single-voice
route and every existing recipe, generation and cue identity. `b2_audio.py` and
the existing TTS/native/cache sources remain unchanged because their source
digests participate in those identities.

Enabled jobs analyze the owned source-clock-aligned 16 kHz mono WAV, including
explicit-language sidecar jobs which previously needed no analysis audio.
The imported CPU signal-feature backend emits job-scoped audio clusters, never
visual character identities. Rank clusters by union of speaking ticks, breaking
ties by earliest occurrence then cluster ID; assign the supplied voices in
order and cycle when necessary. Each original cue takes its strongest temporal
overlap, breaking ties by confidence and segment ID. Keep source IDs, source
slots, translation and subtitle text unchanged. Preserve uncalibrated speaker
confidence as evidence. Unresolved cues use the selected primary voice. Missing
audio, backend failure or fewer than two detected speakers use that primary
voice for every cue and emit an explicit fallback warning.

VieNeu binds a native engine to one verified preset. The new stage groups cues
by assigned voice and runs one existing LocalTtsAdapter/engine at a time, closing
it before loading the next. Compute placement windows across all cues before
grouping so another speaker still limits the previous cue. Each group retains
its own existing TTS2 document, voice/model provenance, validated waveform and
per-cue checkpoints. One refused group does not discard successful groups.
Merge the accepted artifacts through the existing AUD-0 mixer. The aggregate
TTS2 document uses `voice_id=composite` and a hash of the actual voice profiles
and cue memberships; per-voice documents contain the exact synthesis provenance.
Spoken-text evidence and bounded lexical rewrites retain the existing contract.

## Identity, evidence and recovery

Only enabled generations extend the legacy B2 identity with the ordered setting,
primary voice, source audio hash, backend/model/config/code recipe, diarization
segments and cue assignments. Runtime elapsed/peak-memory measurements do not
affect identity. Each voice's checkpoints bind that whole plan and its TTS
recipe; the adapter independently validates the cue's voice and waveform hashes.
Speech cache requests include the actual per-cue voice, enabled producer recipe
and ordered setting, so changes cannot replay a different voice's waveform.

The completed speaker plan is atomically checkpointed with its source/settings/
cue digest and file checksum. A restart can reuse it and completed synthesis
cues. An interruption during diarization restarts that streaming analysis; no
partially analyzed plan authorizes reuse. Native engines remain sequential and
PCM mixing remains file-based. Analysis retains segment observations and is not
a constant-memory or long-form qualification claim.

`job_manifest.json` adds `dubbing.speaker_voices` and `audio.speaker_dubbing`
only when enabled. The latter includes speaker-to-voice and cue-to-voice maps,
cue confidence, complete diarization provenance/warnings, fallback reason and
per-voice document paths/hashes. Evidence survives a later B1 downgrade. Existing
standard video/subtitle/audio exports remain usable without these diagnostics.

This is an additive worker args/manifest extension, not an envelope, timeline,
SQLite or TTS schema migration. Existing jobs keep their pinned runtime/options
and artifacts. New enabled runs get distinct private B2 generations. Rollback
retains previous outputs/runtime; older workers must not receive the new option.
The supervisor remains the sole durable SQLite writer. UI/CLI transport and
immutable start-option binding must be integrated separately before exposure.

## Limits and evidence

The stdlib energy/ZCR/autocorrelation clusterer has no learned speaker embedding,
cannot infer overlapping speakers or visual identities, and can confuse similar
voices, background music/noise or microphone changes. Defaults cap clusters at
eight and audio at 24 hours. Confidence is uncalibrated. A cue spanning multiple
turns receives one voice; it is not split. Synthetic PCM and fixture TTS tests
prove routing, identity, provenance, fallback and recovery, not real-film
diarization accuracy, listening quality or packaged production qualification.
