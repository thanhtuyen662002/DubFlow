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

## Hardware probe and compatibility

The hardware resolver no longer treats launcher environment variables as health evidence. An explicit NVIDIA probe queries device 0 for current free VRAM and runs a one-frame H.264 NVENC encode using the app-owned FFmpeg path. Both subprocesses have bounded timeouts, use no shell and suppress a console on Windows. Missing drivers, malformed inventory, timeouts, insufficient free VRAM or encoder failure select the usable CPU/software path. CPU and software requests do not initialize a GPU. Other GPU providers and model-specific CUDA health still require their own adapters.

The serialized HardwareSnapshot and ExecutionProfile field sets stay unchanged. Existing environment inputs remain accepted as unverified diagnostics, but they cannot select GPU. This is a conservative behavior change: callers requiring hardware encoding must provide a real probe. Existing pinned jobs must retain their producer version; recorded selection is not new health evidence. No durable schema migration is introduced. Encoder smoke evidence does not qualify model execution, runtime resource pressure or a long render, and model-specific GPU integration remains acceptance work under #171.

## Worker rendering and compatibility

The production worker accepts an optional render_profile argument with cpu, auto and gpu values. Omission preserves the legacy CPU path. The worker envelope already permits command-specific arguments, so its schema does not change. CPU requests do not locate a driver or launch a GPU probe. Other requests obtain the NVIDIA tool from the OS system directory or fixed driver location; a job cannot provide an executable or PATH override.

The media adapter exposes only software and h264_nvenc selections. Software retains the shipped LGPL Windows Media Foundation encoder, quality setting, mappings and atomic output. NVENC targets the same device 0 as the smoke probe. A retryable encoder command failure, encoder timeout or empty output changes the condition once to software while preserving the chosen B2 audio and subtitle options. Cancellation, invalid input, process-start and storage errors are not treated as GPU encoder failures.

Before the CPU attempt, GPU retirement is atomically written to render-policy.json and its digest is checkpointed. Policy schema 1 identifies hardware-render-policy-v1, the job, source digest, selected CPU/software path and encoder failure code. Restore checks the digest over a bounded 64 KiB payload and validates the schema, producer contract, job and source. A retired encoder stays disabled for that job across worker restart. Missing, malformed, oversized, mismatched or unknown policy data selects CPU with explicit unverified evidence. No worker writes durable SQLite state.

Rendering checkpoints carry optional profile evidence. B1 cache reuse requires a matching output digest; reused video reports reused_checkpoint and preserves recorded evidence only when that digest matches. An unused renderer does not perform a fresh hardware probe or claim a successful command. Existing checkpoints without profile evidence remain readable and produce unmeasured cached diagnostics. Existing jobs retain their pinned producer/model/contract versions; this change does not upgrade their producer in place.

The namespaced hardware_render block uses hardware-render-evidence-v1 inside the existing producer QC summary. It describes executed or reused rendering and is not model GPU qualification, measured separation quality or a canonical QC pass. The legacy local-file summary remains distinct from contracts/qc/schema-v1.json; migrating and verifying the full pipeline against that canonical QC contract remains required for the full production gate. No canonical QC schema is relaxed to accommodate this diagnostic block.

Deterministic tests simulate processes and use temporary files to validate preserved audio, sticky fallback, retirement persistence before retry, restore, policy integrity and provenance. They do not establish physical GPU, codec quality, model health, resource-pressure or long-soak qualification. Full worker separation integration, chunked long rendering and the release qualification lanes remain acceptance work under #171 and #175.

## Validation and release conditions

Regression tests use small deterministic PCM fixtures to reproduce opposite-phase cancellation and a high-activity input that previously qualified through inferred scores. They require AUD-0 fallback, null quality measurements, matching mono/stereo activity, and rejection of AUD-2 with missing measurements. Hardware tests require CPU fallback for both inherited and explicitly provided environment hints. Additional deterministic tests cover free-VRAM policy, explicit paths/device selection, CPU isolation, malformed inventory, unavailable drivers, encoder failures and timeouts through a mocked process adapter. Physical GPU evidence is separate. These tests validate software behavior, not speech intelligibility or separation quality.

Production promotion still requires the reference corpus, measured residual speech and background damage thresholds, codec/duration/loudness/clipping/sync checks, resource and hardware evidence, integrated worker recovery, and every CI lane required by #171. Keep the PR Draft until those conditions and the exact-head/current-tested-base merge invariant are satisfied.
