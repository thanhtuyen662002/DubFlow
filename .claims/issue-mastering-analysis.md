# Issue #641 mastering scene analysis

Owner request in the project thread (2026-11-10), Epic #637, ADR-0052 §6. Scope: `engine/dubflow/mastering/analysis.py` (one FFmpeg pass: shots with docu/shots.py's algorithm, per-shot motion, silences; integer ms; cached), its tests, and `engine/dubflow/docu/shots.py` as a root of the `mastering` CI component. Stacked on #639.
