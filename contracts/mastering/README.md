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

## Review (`editorial_review`, Phase 5 MVP)

The decision log `editorial-<n>.json` carries, per unit, the planner's own
`review`: `evidence` (`MATCH_AVAILABLE` | `MATCH_NOT_FOUND` |
`SOURCE_INSUFFICIENT` | `NOT_NEEDED`) and `state` (`READY` | `NEEDS_REVIEW` |
`MATCH_NOT_FOUND` | `SOURCE_INSUFFICIENT`). `SOURCE_INSUFFICIENT` is only
written when every usable shot has content evidence and none matches;
otherwise a missing match is `MATCH_NOT_FOUND`.

The user's decisions live apart in `review-<n>.json` (`schema_version: 3`; 1 and 2 still read,
`kind: editorial_review`): per unit `approved` (the chosen shot ids the user
saw), `pin`, `exclude` (shot ids from the log) and `invalidated` (why a
decision no longer holds), plus `acknowledged` warning ids,
`accept_unreviewed` and a bounded undo `history`. Pins and exclusions reach
the planner as `editorial.edits`; an approval never changes a verdict or a
confidence. A new plan invalidates decisions whose shots are gone and
approvals of footage that changed. The executable reference is
`engine/dubflow/mastering/review.py`; the desktop changes the file only
through `python -m engine.dubflow.mastering.review`.

Version 2 (Assisted Editorial Production) adds, per unit, `later` (the user
puts it off: still unreviewed) and `source_missing` (the user found no
suitable footage: settled, the planner's verdict unchanged, nothing reaches
the planner); `position` (the sentence the user was on); and `records`, the
Human Decision Contract: one entry per decision with `type` (`APPROVE`,
`PIN`, `EXCLUDE`, `REPLACE`, `REVIEW_LATER`, `MARK_SOURCE_MISSING`, `UNDO`,
or `INVALIDATED` written by the system), `run_id` (the run folder's name),
`plan_revision`, `speech_ref`, `source_id`, `shot_id`, `source_ms`,
`decision_revision`, `approval_state`, `input_fingerprint` (the sentence and
the shots it showed), `decided_at` (UTC) and `invalidation_reason`.
`USER_APPROVED` is never `SEMANTIC_CORRECT`, a pin is re-checked on every
plan, and `READY` is not `USER_APPROVED`. The panel's view adds per unit
`priority` (`critical` | `review` | `optional` | null when settled) with
`queue_reasons`, and `queue` (most serious first), `queue_calibrated: false`,
`position` and `shots` (every usable shot).

Version 3 (#805, whole-shot exclusion) adds, per unit, `ranges`: for each
excluded id, the footage it named when the user left it out (`[source id,
start ms, end ms]`; a whole shot split into pieces may be excluded by its own
id, and its range spans the pieces). The planner leaves out a shot named, every
piece of a whole shot named, and any shot at least half inside a recorded
range, whatever it is called now; an id renumbered onto other footage is not
left out, and the decision says the footage is gone. An exclusion that would
leave a part no usable footage is refused; footage used anyway is listed in the
log's `exclusions.reused` and the sentence gets an `all_excluded` conflict.
The action `include` ("Bỏ loại") removes every exclusion that leaves the
given shots out, as one change (record `UNDO`, `detail: include`, `shots`). An
`EXCLUDE` record of a whole shot has `detail: whole_shot` and `affected`
(each shot it leaves out, with its range). The view adds per shot `parent`,
`excluded` and `excluded_by`, plus `families` (each split shot with its pieces
and the sentences that use them) and `exclusions` (each exclusion, the
sentence that made it, whether it is a whole shot, what it covers now and
what was reused). Versions 1 and 2 still read.
