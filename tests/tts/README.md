# TTS deterministic tests

The tests in this directory cover the contract validator, deterministic fake
engine, sample-rate/channel/duration validation, silence and corrupt waveform
rejection, bounded retry/fallback evidence, canonical timeline identity, and
checkpoint reuse and invalidation. They also exercise strict duplicate-field
and structural wire rejection, fallback provenance, resource profiles, and
partial-batch failure isolation. They run offline with the Python standard
library.
