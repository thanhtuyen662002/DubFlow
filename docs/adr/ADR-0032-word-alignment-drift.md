# ADR-0032: Reviewable timing fallback for orphaned ASR sentence tails

- Status: Proposed, Issue #227.
- Scope: production ASR cue grouping and private evidence. Public timeline,
  worker, SQLite and model contracts are unchanged.

## Evidence and decision

Recorded Elephants Dream word alignment puts `Are` at 27.43–27.97 seconds,
`you` at 27.97–33.19 and `hurt?` at 47.17–47.47 inside one segment. Splitting
every pause preserves erroneous word alignment as two translation fragments.
Using the entire word envelope instead would create a twenty-second cue.

Recipe `faster-whisper-1.2.1-bounded-word-gap-v4` flags words longer than
40000 decoded 16K samples (2.5 seconds). After the existing one-second gap
split, a terminal tail of at most three lexical word entries continues the
previous group when that group lacks terminal punctuation and the tail starts
with a lowercase or uncased letter. Terminal punctuation includes `. ! ? 。
！ ？ …` and may precede a closing quote/bracket. Chinese has no capitalization
signal, so an uncased continuation qualifies. An explicit capital, a completed
previous sentence, an unterminated tail or a longer tail preserves the split.
An orphan signal can trigger fallback without a stretched word. These are
heuristics for review, not a claim that the sentence or timing is correct.

For an orphan pair, intersect its observed word envelope with the original
segment interval. Use that interval if positive and at most 240000 samples
(15 seconds). Otherwise use the observed words up to and including the first
stretched word (stopping at an excessive gap), intersected with the segment
bounds, only if that meets the same limit. The stretched word's own aligned
span is observed data and covers where the sentence was spoken; only the
orphaned tail's late timing is discarded. A bare pre-drift prefix (0.54 s for
the example) is too short to read or dub. Never extrapolate or invent an
interval. The recorded example with segment bounds 27.43–47.47 uses
27.43–33.19; the complete sentence remains one translation unit. Its timing
and duration require review. If no bounded positive prefix exists, keep both
groups split and flag both. A stretched word without an orphan flags its
existing cue without changing its observed word timings or model scores.

A merged fallback has `review_reason: word-alignment-drift`,
`timing_basis: drift-fallback`, unavailable confidence and one segment-like
text/timing entry. Additive private `alignment_drift` evidence retains every
original word, probability, integer sample endpoint, segment bounds and the
selected fallback basis. These observations are never rewritten as corrected
word alignments. Checkpoint admission validates those observations and
recomputes the fallback interval. Chunk reconciliation owns the aggregate by
the existing positive-entry midpoint rule and preserves its evidence, so it
cannot split the sentence again using the rejected timestamps. Distinct
fallback hypotheses retain their existing boundary-disagreement review data.

## Compatibility, invalidation and rollback

The recipe participates in transcript inputs, chunk binding and checkpoint
admission. Older recipe artifacts cannot establish this evidence and are
recomputed into new generations; existing retained artifacts are not edited.
Private schema numbers remain unchanged because recipe admission is exact and
the additional evidence is specific to the new recipe. Translation/TTS/mix
descendants continue to use their existing cue/input identities. Existing
installed jobs retain their immutable producer/runtime binding and exports;
successor producers use fresh jobs. Rollback selects the retained producer and
its original recipe and artifacts. No database migration, model download or
dependency change is required.

## Validation

Offline recorded-word regressions cover the reported drift, segment clipping,
integer window offsets, unusable prefixes, legitimate pauses, capitalization,
English/Chinese terminal punctuation, the strict word-duration threshold and
normal word-aligned speech. Mocked production decoding covers reconciliation,
private chunk reuse, transcript checkpoint admission, evidence tampering and
recipe invalidation. These tests do not establish actual-model timing quality.
