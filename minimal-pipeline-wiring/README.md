# Minimal B1 pipeline wiring

This directory is the Issue #56 integration boundary. The executable test
under `tests/integration/slice-b1-vietsub` composes the already versioned ASR,
translation, subtitle, and export adapters through their public interfaces.

It is intentionally an adapter-level harness: canonical contracts remain in
their owning namespaces, and production media/encoder implementations remain
behind the render boundary. The deterministic path runs offline on CPU for
PR Fast/Integration evidence.
