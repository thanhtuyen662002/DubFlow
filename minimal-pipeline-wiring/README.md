# Minimal B1 pipeline wiring

This directory is the Issue #56 integration boundary. `b1_pipeline.py` and
the executable test under `tests/integration/slice-b1-vietsub` compose the
already versioned ASR, translation, subtitle, and export adapters through
their public interfaces.

The B1 config consumes normalized local-file metadata supplied at the #14
probe boundary. It owns the supervisor-side checkpoint file and output sidecars, while
backend workers return only ephemeral results. Checkpoints are content- and
configuration-bound, written atomically, and safely invalidated when the
source or producer configuration changes.

It is intentionally an adapter-level harness: canonical contracts remain in
their owning namespaces, and production media/encoder implementations remain
behind the render boundary. The deterministic path runs offline on CPU for
PR Fast/Integration evidence.

Slice B enables `B1PipelineConfig(enable_dubbing=True)` to consume the
validated translation output through the local TTS and AUD-0 mix adapters. The
original source WAV is published unchanged, and any TTS/mix failure selects the
existing #56 original-audio Vietsub output while keeping downgrade evidence in
the report. The default remains the proven B1 Vietsub-only mode for callers
that have not opted into the new stage.
