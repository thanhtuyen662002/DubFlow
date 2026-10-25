# ADR-0036: Bounded dubbing fit ladder and separated-bed source restoration

Status: Proposed, Issue #276. Extends ADR-0025/0026/0035.

## Decision

Keep the existing natural/atempo <=1.3 route, 2000 ms postroll and 120 ms
next-cue guard as the first attempt. Only DURATION_FIT_REQUIRED advances one
cue through these finite steps: widen postroll to at most 4000 ms, condense
Vietnamese once, then allow <=1.5 tempo. Windows remain bounded by media end
and the next source cue minus guard; overlap on either side forbids extension.
Tempo correction retains the existing three measured passes, complete-speech
checks and refusal to cut spoken samples. Other failures stop the ladder.
VieNeu reuses its natural PCM for window/tempo changes; condensation permits
at most one additional text inference (with the existing bounded EOS policy).

Condensation applies an ordered lexical rule set to fillers, redundant
connectives/adverbs and final particles. Quoted/control-containing text and
potential capitalized names/titles are declined. Numeric classifiers and
locative/demonstrative complements are protected; names, numbers and negation
are never rewritten. Original translation/subtitles and canonical source ticks
remain authoritative. These rules can remove emphasis: the warning names all
applied rules and asks for spoken-text review, without claiming calibrated
semantic fidelity. No new model or dependency is introduced.

The engine boundary carries an optional selected render limit internally.
The outer adapter checks it against independently allocated fallback limits,
checks neighboring intervals, validates PCM, and records the selected limit in
the existing TTS2 artifact. Fast fitting requires explicit adapter opt-in and
a cue/speed advisory, and cannot exceed 1500 milli. The default config remains
1300 to preserve successful first-route request/artifact identities and bytes.
Widening, rewrite and fast-fit warnings plus selected speed/window are bound
to the spoken-text receipt and checkpoint. Recovery revalidates these limits
and waveform bytes, including when no new inference runs.

When accompaniment is selected, build a separate `mix_bed.wav` from that stem
with source PCM in failed canonical slots. Union overlapping failed windows
before applying 20 ms linear crossfades inside their edges. Process bounded
PCM blocks and publish atomically. Never add source over accompaniment: replace
samples, preserving source music gain. Keep `background_stem.wav` pure and keep
the original source separately. A TTS file rejected at mix time permits one
bed rebuild for the newly observed failures. Emit
`SEPARATION_SOURCE_RESTORED: <cue>` for every restored slot. All-TTS failure
continues to the existing B1/source export fallback. A wholly refused voice
group remains a per-cue failure document so other voice groups can still dub.
Job manifest/QC warnings retain the fit and restoration advisories.

## Identity and compatibility

No public contract, timeline, worker protocol or SQLite migration is required.
TTS1/2 and mix1 serialization remain compatible. The private spoken receipt
recipe changes and binds selected fit evidence; old receipts cannot authorize
reuse under it. Generation/checkpoint/cache identities pin the updated parent,
native, rewrite and B2 allocator sources; mix recipe identity also pins the bed
builder. Affected render limits participate in artifact hashes; changed
text/config/window participate in synthesis-cache keys. Healthy first-route
requests retain their previous config, placement input, producer and artifact
identity. Existing generations may be recomputed in new private generations;
published exports and pinned old runtimes are never relabelled or migrated.

The native recipe now declares a 1.3 primary/1.5 fallback limit. The exact
previous data-only model manifest recipe remains accepted so its model/voice
hashes do not change; both parent and child code hashes distinguish the new
capability. No other inference/manifest differences are accepted. Rollback
uses the retained previous runtime and outputs.

## Evidence limits

Fake-engine tests cover each rung, protected text, strict window/rate bounds,
unchanged successful identities, receipt validation, resume and source-restored
PCM/fades across block boundaries. These tests do not measure real-model
intelligibility or the reported 45/124 undubbed rate. A before/after count for
the original 246 s video requires that authorized source and its installed
models; it remains NOT_RUN without those inputs. No models/media are downloaded
or committed for deterministic qualification.
