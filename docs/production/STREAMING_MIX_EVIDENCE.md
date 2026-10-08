# Streaming PCM evidence — leaf #203

## Current scope

Draft PR #204 enables #166's measured mixer resource gap. It retains all public
schema-v1 contracts and the legacy byte adapter. No product readiness, #166/#175
closure or stable publication is claimed by this document.

## Executed development checks

2026-10-09 Windows, development venv with actual NumPy2.2.6:

- 17 audio-mix tests passed, including six layout/signed-start numerical parity
cases, overlap/resampling/duck ramps across127-frame blocks, normalization and
PCM hashes/metrics. No native numeric test is skipped or replaced by a mock.
- Actual child process exits42 after a committed combined block; restart
verifies saved prefixes and completes. Every stage also exercises interrupted
private tails, checkpoint corruption and artifact reuse/corruption.
- Disk refusal, injected real write/fsync exception boundary and TTS mutation
fail with typed conditions, without creating the completion document.
- Actual generated12-second stereo48K WAV rehearsal completed576000 frames,
source/final hashes and QC, hard exit42 and restart. Peak working set61452288
bytes. Receipt outside Git: `TEMP/dubflow-streaming-203/smoke-1.json`.

The current expanded suite passes19 tests, including asymmetric stereo rounding,
WAV metadata preservation and ticks above2^53. A separate real cached PCM run
used all272 cues /250 actual VieNeu3.1 speech artifacts from the554-second Bili
source. All original/stem/final PCM hashes and metrics matched legacy AUD-0;
257 historical files were byte-hash preserved. Mixer elapsed21.609seconds,
peak working set64430080 bytes. Receipt:
`TEMP/dubflow-streaming-203/real-cached-1/report.json`, SHA256
`cd700c74d416a7de8c2b9319d3d8ad08161cc9b6e9bdc18aa8ed36faddc0f978`.
This precommit run does not establish fresh ASR/TTS/native GUI behavior.

The six-hour stereo48K generated rehearsal is running; there is no completed
resource result yet. It uses full non-zero PCM writes and actual mixing, not
sparse files or synthetic counts. Its intended receipt is
`TEMP/dubflow-streaming-203/six-hour-stereo-1.json`.

## Required evidence pending

Current-head/current-main Fast, Integration, Windows Release and Release/Soak
must all pass. The Windows lane must execute the actual new adapter in both
verified staging and installed runtime; baseline B2 smoke is insufficient.
Both #195/#198 native runs terminated SUCCESS before the shared release workflow
was edited. Complete six-hour frame/hash/QC/memory/
disk/restart results and exact source evidence remain required. Tests/rehearsals
do not establish human voice quality or real-media/native GUI qualification.
