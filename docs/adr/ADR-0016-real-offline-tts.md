# ADR-0016: Real offline Vietnamese speech candidate

- Status: Proposed; Draft PR #195, Issue #166. No release qualification.
- Date: 2026-10-07
- Scope: B2 TTS adapter, model provisioning and provenance. No durable schema change.

## Context

The existing `BuiltinVietnameseTtsEngine` produces character-dependent sine
and noise signals. Its non-silent PCM and successful B2 smoke are not evidence
of Vietnamese speech. Issue #166 requires actual CPU dubbing, Chinese-language
translation, recovery, intelligibility and app-owned Windows runtime evidence.
This decision addresses the speech backend; the other acceptance criteria
remain open. The project continues to use B1 subtitle export as a usable fallback.

## Proposed decision

Implement a separate adapter for the Mimic3 `vi_VN/vais1000_low` VITS voice,
ONNX Runtime 1.30.0 and eSpeak from pinned Sherpa-ONNX 1.13.8. Keep native
libraries and model inference
behind the existing TTS adapter. Workers publish artifacts/events; the
supervisor retains ownership of durable job state.

Provision the model through the existing resumable downloader into the
supervisor-selected model root. Verify exact archive size/SHA-256 and the
digest of the complete extracted data inventory. Serialize provisioning
across processes. Reject archive traversal, links/junctions, special files,
portable-path violations, duplicate data and bounded-size violations. Stage
installation privately and replace only after validation; retain corrupt
previous data for diagnosis. Conversion scripts from the archive are omitted
and never executed. No weights or generated media enter ordinary Git history.

Use native 22,050 Hz mono PCM and source-derived integer time points. Generate
natural speech first. At most one additional generation changes the speaking
rate within the existing 1.3 ceiling. Reject speech that still cannot fit;
never trim audible samples to satisfy a cue. Pad short output with silence,
reject invalid/non-finite/silent output and preserve original/stem/final assets
through the existing AUD-0 mixer.

Pin producer version `2.1.0`, backend `mimic3-vits-onnx-v1`, runtime
`onnxruntime-1.30.0+espeak-sherpa-1.13.8`, model/tree hash, full voice manifest digest and inference
recipe in provenance. An archive integrity check, runtime health check or PCM
success is independent of speech-quality approval.

## Compatibility and migration

The current TTS/artifact schemas and canonical timeline contract are unchanged.
Historical jobs retain their recorded producer/model/contract versions and
artifacts; do not relabel historical tone output as neural speech. The legacy
voice loader remains available for compatibility tests. New B2 jobs select the
new profile; B1 remains independently usable. Existing voice data is not
overwritten or rewritten by a migration. A changed model or frontend recipe
requires a new recorded identity and selective regeneration, not silent reuse
of old TTS output.

The worker integration changes only the model-root argument at the B2 call.
No CapCut, review, OCR, speaker or hardware namespaces are claimed by this ADR.

## Qualification limits and alternatives

The selected voice's CC BY 4.0 weights permit attributed redistribution,
but the shipped native eSpeak-NG runtime has additional GPL/source obligations.
The license decision is documented separately and does not approve quality.
Models with noncommercial/research-only or unknown terms were not promoted.

The converted Piper frontend measured common-phrase mean back-ASR CER 0.3194.
It drops an original compound phoneme and uses different word encoding. The
new frontend reconstructs the upstream-hash-verified inventory, preserves
compound IPA symbols, applies original word/token blanks, retains clause
punctuation and removes native language-switch metadata. Its local diagnostic
measured 0.1193 common-phrase CER. Unsupported symbols remain explicit warnings.
This is not human listening approval or a stable publication decision. Original
per-clause pause/inference parity and unfamiliar words still need validation.

Native code now runs behind a private version-1 child protocol using the
app-owned interpreter with isolated imports. JSON requests/replies, stderr,
sample files and inference deadlines are bounded. Reply identity, waveform
digest/size and sample rate are verified before signed-16 conversion. Native
crash/timeout becomes a typed failure; it cannot terminate the job worker.
Normal teardown kills/waits for the child and verifies the private staging root
before cleanup. This changes no public worker protocol or durable schema.

Still unresolved: abrupt parent death/process-tree cleanup, packaged B1 recovery,
a human listening set, Chinese no-sidecar dubbing, long-form/batch evidence,
exact-head CI/current base, clean-machine Windows and release/soak qualification.
PR #195 remains Draft; this ADR proposes no exception to those gates.
