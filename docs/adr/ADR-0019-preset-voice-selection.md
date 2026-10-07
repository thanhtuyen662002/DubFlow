# ADR-0019: Explicit preset voice selection for CPU dubbing

Status: Proposed by Issue #166 / PR #195. Full production qualification remains open.

## Decision

The installed checksum-pinned VieNeu manifest is the single catalog for the
desktop and the TTS adapter. It lists 25 official presets with stable ASCII IDs,
display names, gender, accent and style copied from the pinned upstream roster.
`approved` records license approval; it does not mean human listening approval.
All presets share the same immutable model inventory and require no separate
download per voice. No enrollment or automatic speaker casting is introduced.

The desktop offers per-video dubbing and voice controls plus gender/accent/style
filters. Browsing a filter never changes the saved voice. A selected voice ID is
stored in the queue before start and carried through the native host, supervisor
CLI/JSON start request, worker command and TTS adapter. Active/recovered jobs keep
their saved choice. Unknown voices fail explicitly and preserve the B1 export;
they never silently become another voice.

The optional `tts_voice_id` is a bounded ASCII identifier. The native host checks
it against its installed catalog; the worker independently checks the approved
manifest before downloading or initializing models. Existing voice ID/version,
model/manifest hashes and producer identity stay in TTS/AUD-0 provenance. The
requested choice is also included in the job manifest, including a B1 downgrade.
Private B2 generation identity includes the requested voice and recipe digests;
an explicit choice change cannot reuse another voice's generated audio.

## Compatibility and migration

This is an additive extension of start options inside the existing version-1
worker command's `args_json`, not a change to envelope/timeline/SQLite schemas.
The desktop, supervisor and worker must ship together in one verified release;
older supervisors cannot accept the new CLI flag. Do not mix component versions.
No database migration is required; the supervisor still owns durable SQLite.

Queue snapshot version 1 accepts an optional `dubbing` extension. Missing options
from older queues restore to disabled dubbing and no selected voice, preserving
their prior B1 behavior. Malformed stored IDs/options are rejected. Newly enabled
jobs store an explicit ID; recovered jobs never fill a missing/unavailable ID
with a different preset. An unavailable voice prevents dubbing start and requires
an explicit new queued job with a supported choice.

Legacy non-desktop callers may omit the option and retain the manifest default.
Historic Mimic3 recipes retain their own single ID; they reject foreign preset
IDs. Existing artifacts are not relabeled or rewritten by a catalog update.
The current production supervisor treats completed/failed jobs as terminal;
regeneration after an explicit choice change requires a new executable job ID.
Worker-level targeted regeneration continues to use distinct B2 generations.

## Evidence and remaining qualification

Deterministic checks cover the actual catalog, independent per-job persistence,
legacy queue loading, locked recovered choices, unknown IDs, CLI/JSON transport,
distinct voice provenance and B1 preservation. Actual CPU waveform generation
is verified separately from fixture tests. Accent/style labels describe upstream
preset metadata and are not speaker/character identity or a quality score.

No full-release claim follows from catalog availability or a short waveform.
Human listening, packaged Windows execution/recovery and all exact-HEAD/current
base CI requirements remain part of #166 and the full #175 integration gate.
