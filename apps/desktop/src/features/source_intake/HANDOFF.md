# Source intake hardening handoff (#207)

Codex 2 owns the source_intake parser/controller, source_transport module,
native per-item validator and their dedicated tests. Follow-up to #168 / #184.
Shared entry points, manifests, lockfiles and CI workflows remain owned by the
integrator on #185. Wiring will be delivered as preimage-checked patches here.

This module-only Draft is not integrated into the running app. No merge or
release is authorized. Acceptance and deterministic test evidence: Issue #207.