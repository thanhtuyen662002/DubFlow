# Mastering contract (v1)

Owned by ADR-0052. Two documents, both `schema_version: 1` with a `kind`:

- `mastering_options` — the user's choice for one output: `level`
  (`off` | `light` | `balanced` | `dynamic`, default `off`) and `style`
  (`natural` | `dynamic` | `cinematic` | `technical` | `entertainment` |
  `sports` | `educational` | `storytelling`, default `natural`). Absent options
  mean `off`, and `off` keeps the workflow's previous behaviour exactly.
- `mastering_plan` — the planner's decisions, saved as
  `.dubflow-work/mastering_plan*.json` beside a run.

## Time

All times are integer ticks of the plan's one reduced `time_base`
(`{"numerator": "1", "denominator": "1000"}` by default), written as canonical
decimal strings (no sign, no leading zero, no exponent), as in the timeline
contract. `timeline` spans are half-open, ordered, contiguous from `0` to
`duration`. A segment's `source` span has the same length as its `timeline`
span (v1 has no retiming), except a `still`, whose source span is one point.
A photo source has `duration: "0"` and only `still` segments.

## Values without floats

`confidence_milli` is 0–1000. Zoom is milli (1000 = 100 %, at most 3000).
Centres and protected boxes are basis points (0–10000) of the source picture.

## Never automatic

`flip_h`, `flip_v`, `rotate`, `stretch` and `export_zoom` are rejected as
effects: a plan never alters what the footage shows (ADR-0052 §4). The
user can still flip or rotate a clip by hand in Chỉnh sửa.

## Compatibility

Consumers reject an unknown `schema_version` and plan again; they never
reinterpret a newer document. The executable reference is
`engine/dubflow/mastering/plan.py`; `tests/mastering` checks the fixtures in
`fixtures/mastering` against it and keeps this schema's key lists equal to it.
