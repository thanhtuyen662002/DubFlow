# Benchmark metric registry

Metrics are recorded as versioned machine-readable evidence, not as a single
human score. Required reports should retain dataset/catalog revision, producer
and model hashes, resource profile, latency, confidence calibration and the
fallback decision. Large benchmark outputs belong in CI artifacts or release
storage; this directory contains only schemas and small deterministic metadata.
