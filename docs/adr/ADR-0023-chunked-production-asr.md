# ADR-0023: Bounded production ASR and per-chunk recovery

- Status: Proposed, Issue #166 / Draft PR #195.
- Scope: production worker ASR recipe, private chunk schema 1, private transcript
  schema 4 and optional localization-manifest ASR evidence schema 2.
- Public ASR/timeline/worker contracts, SQLite, model packs and default voice:
  unchanged.

## Evidence and decision

The actual nine-minute ea5 source-worker run accumulates the entire Whisper
generator before publishing a transcript checkpoint. An interruption therefore
loses all recognition work. This violates the long-input stage invariant.
An actual seven-window Sintel probe through the existing public LocalAsrAdapter
also rejects a complete chunk because one genuine word has zero sample duration.
Private word evidence must retain that word with its positive speech group;
inventing a positive word interval would misrepresent the model observation.

The production recipe becomes `faster-whisper-1.2.1-bounded-word-gap-v3`.
Consume the existing ChunkPlanner with a 1/16000 source-sample time base.
Balance non-overlapping cores of at most 120 seconds, with up to ten seconds
of context on each side. Balancing avoids a tiny final core that contains only
earlier context and silence. A decode window is at most 140 seconds. Stream
its PCM from the verified mono16K analysis WAV in blocks of at most64000 frames
into an owned temporary window WAV; Whisper receives that bounded file.
Neither the complete long-form PCM nor its float array enters the model call.

Normalize SDK seconds to window-relative integer samples before adding the
integer window offset. Canonical cue identity and millisecond mapping continue
to derive from those source samples. Own words by their midpoint in a core.
Zero-duration words follow a nearby positive word in the same model group;
their original zero endpoints remain in evidence. Context hypotheses remain
in their original chunk artifacts. At adjacent boundaries, reconcile matching
normalized words with overlapping source intervals, retaining the earlier
owner's original probability rather than selecting a more confident score.
Preserve distinct overlapping hypotheses and explicit boundary-review data.
The reconciliation frontier contains only the previous chunk's groups, avoiding
a whole-transcript pairwise scan. None of these rules proves correct names,
sentence semantics, calibrated confidence, dubbing intelligibility or lip sync.

Auto language is detected at the first chunk with source-owned positive speech,
then pinned for subsequent decode calls. A silent/context-only chunk does not
pin its guessed language. Persist original per-chunk detection scores, decode
language, source-owned counts and the selected language/probability. Explicit
language and a pinned continuation reject a backend language disagreement.
This preserves one-source-language routing; it is not multilingual detection
or speaker/character casting.

## Checkpoint and producer compatibility

Each private chunk record schema1 contains its complete aligned hypotheses,
original scores, decode/detected language and a canonical payload digest.
Bind identity to actual analysis-audio SHA/frame count, requested language,
caller model/profile identity, recipe and core/overlap policy. Validate digest,
expected chunk/core/window, language chain and full word/cue evidence before
reuse. Missing/corrupt/different records regenerate that chunk; successful
records are not rewritten. A silent successful chunk is reusable. There is no
unchanged-condition retry inside this adapter. A failed invocation keeps all
earlier atomic records and emits a typed worker failure. The supervisor alone
owns durable state; the worker publishes private artifacts and existing JSONL
progress/checkpoint events after an atomic chunk commit.

Private transcript schema4/ASR evidence schema2 records the balanced plan,
chunk digests, source/model/recipe binding, language pin and boundary reviews.
Full checkpoint admission validates plan coverage and word evidence together.
Schemas1–3 and the earlier recipe cannot establish bounded recovery and are
not reused as current evidence. The recipe input identity invalidates transcript
descendants; existing translation/TTS/mix identities select new generations.
The public localization manifest remains schema1 with optional additive `asr`.
Consumers of that optional object must dispatch on its own evidence version.
Existing exports without it remain readable and usable.

Existing installed jobs retain their original immutable engine/runtime/model
binding. Successor actual runs use fresh job IDs; never overwrite their outputs
or relabel old tests/native candidates as current. A rollback selects the retained
producer and its original private artifacts. A previous published export remains
available until the successor passes QC and atomic publication. No migration,
dependency/lockfile, new weights, runtime download or stable release is introduced.

## Qualification

Focused deterministic regressions cover balanced six-hour plan bounds (planning
only), offset rounding, atomic interruption/resume, cache/model/language binding,
corrupt-chunk isolation, silent language admission, boundary duplicate/disagreement
and zero-duration words. Actual model hard-exit/restart, source-worker and installed
producer evidence are separate requirements. Planning six hours is not processing
six hours. Existing ea5 four-lane/native results are historical after a new source
commit. New exact-head/current-base CI, actual installed recovery, native GUI,
2–6h VFR,100/500 batches and all original166/175 gates remain required.
