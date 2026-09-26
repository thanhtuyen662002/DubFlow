# Canonical Timeline Contract

This directory owns the versioned cross-language timeline wire contract for Issue #3.

Normative rules:
- durable temporal identity is integer ticks plus an explicit rational time base;
- decimal seconds and frame indices are presentation/analysis helpers, never durable IDs;
- signed 64-bit tick values serialize on JSON wires as base-10 strings to preserve exact values across JavaScript/TypeScript;
- non-zero source PTS is preserved;
- VFR media is represented by presentation timestamps, not assumed frame cadence;
- proxy/source mappings use explicit integer anchors and exact rational interpolation;
- geometry records coded dimensions plus clockwise display rotation; transforms must be reversible.

The concrete v1 schema, fixtures and reference implementation are added by the active Issue #3 Draft PR.
