# Deterministic audio-mix tests

Tests in this directory will cover source-layout preservation, time-aligned
duck envelopes, final-mix and dialogue-stem artifacts, clipping/loudness
validation, missing or silent TTS handling, and the non-destructive baseline
fallback. They also exercise rational timeline identity, strict wire parsing,
segment metadata, and hash-bound checkpoint reuse. They must run with small
standard-library fixtures.
