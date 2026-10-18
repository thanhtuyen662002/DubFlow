# ADR-0031: Complete audio generation publication

Status: Proposed — Issue #209, narrow follow-up to PR182.

Codex 3 owns only separation generation publication and its regressions.
Candidate lineage: PR182 head 7f39ed5e89394852543a01adca8c78691a3923d4.
Base: main 2bd7f51129d19dc63ea1dc82f20f77e193da6fcb.

The implementation and recovery/compatibility decision will be recorded here
with fault-injection and process-interruption evidence. Existing worker wiring,
AUD-0 fallback and unknown speech-quality measurements remain unchanged.