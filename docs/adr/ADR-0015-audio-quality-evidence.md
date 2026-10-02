# ADR-0015: Evidence and compatibility for audio cleanup quality

- Status: Accepted for implementation in issue #171; production qualification remains pending.
- Date: 2026-10-02
- Issue: https://github.com/thanhtuyen662002/DubFlow/issues/171

## Context

The draft CPU energy gate inferred residual speech and background damage from the fraction of active PCM frames. Those formulas do not measure either quality property, but could select AUD-2. Averaging stereo amplitudes before measuring energy also incorrectly treated opposite-phase channels as silence. Passing an explicitly empty hardware environment accidentally inherited host GPU hints.

## Decision

Audio cleanup report schema 2 permits null residual_speech_millidb and background_artifact_milli values. Null means not measured, never zero, passing, or absent speech. AUD-2 is invalid if either required quality measurement is null. Activity fraction and clipping fraction remain measured signal statistics and cannot substitute for speech or background quality evidence.

The current energy gate always selects AUD-0 and preserves the usable existing mix until reference benchmarks qualify a production cleanup backend. It emits independent experimental stems and an explicit warning, but these artifacts do not establish production qualification. Real separation or attenuation, measured quality, resource pressure, GPU health, worker integration and all remaining acceptance criteria in #171 remain required.

Backend dubflow-cpu-gate-v2 computes per-frame energy across channels before taking window RMS. This preserves activity for opposite-phase stereo. Its manifest pins the v2 backend/profile and report schema 2, and records unqualified-no-reference-benchmark. A filename ending in v1 identifies the existing manifest envelope, not permission to reuse v1 backend results.

An explicit empty hardware environment must stay empty. Omission of the environment argument may use the host environment. Environment hints still do not prove GPU or encoder health; real probes and operational evidence remain required before production GPU claims.

## Compatibility and migration plan

These changes revise an unreleased Draft capability; they do not relabel a stable release as qualified. Preserve completed legacy exports and their historical producer pins. Do not upgrade a schema 1 cleanup report by changing its version or translating inferred scores into measured values. Schema 1 quality decisions are ineligible as evidence for a schema 2 cleanup selection.

Worker integration must pin and verify both cleanup producer identity and report schema before reusing a checkpoint. On incompatible cleanup evidence, retain valid upstream artifacts and the usable original mix, then rerun only the affected cleanup stage. Never silently reinterpret null as a passing threshold. Publish the complete report before recording its final hash in the checkpoint. These worker reuse and transaction rules remain implementation work under #171; this ADR does not claim they already run in the worker.

Consumers of schema 1 must reject unsupported schema 2 selection data and preserve standard export, or explicitly implement the nullable measurements and compatibility checks before using it. Review UI should show unmeasured quality separately from measured failures.

## Validation and release conditions

Regression tests use small deterministic PCM fixtures to reproduce opposite-phase cancellation and a high-activity input that previously qualified through inferred scores. They require AUD-0 fallback, null quality measurements, matching mono/stereo activity, and rejection of AUD-2 with missing measurements. A hardware test supplies host GPU hints while passing an empty mapping. These tests validate software behavior, not speech intelligibility or separation quality.

Production promotion still requires the reference corpus, measured residual speech and background damage thresholds, codec/duration/loudness/clipping/sync checks, resource and hardware evidence, integrated worker recovery, and every CI lane required by #171. Keep the PR Draft until those conditions and the exact-head/current-tested-base merge invariant are satisfied.
