# Real CPU Vietnamese TTS evidence — Issue #166 / PR #195

## Decision: not production-qualified

The current selected candidate is VieNeu v3 Turbo fp32 CPU, following the user's
rejection of the historical VITS voice. Local inference, back-ASR diagnostics
and a short Chinese-to-Vietnamese native dubbing probe have passed; human
selection, packaged recovery and full acceptance remain pending. These are
not release approval. Issue #166 and the full product gate #175 remain open.

## Reproducible local diagnostic

The initial tables below describe the historical Piper frontend checkpoint.
The subsequent word-blank and VieNeu measurements are recorded below;
do not attribute historical source digests/CI to the current implementation.

Run from the repo with a development environment containing the pinned runtime:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python tests/production/tts/qualify_native.py --model-root C:/model-cache --output-dir C:/tts-evidence --compare-upstream-recipe
```

The output directory must be outside this repository. Provision the pinned
`production-cpu-v1.json` ASR pack and `production-tts-v1.json` voice beforehand.
The command uses real CPU VITS and Faster-Whisper `small` (int8, four threads),
Vietnamese back-ASR, beam size 5, VAD, and no previous-text conditioning. CER
uses casefolded NFKC alphanumeric text with accents retained. It measures a
small diagnostic set, not human listening quality or translation accuracy.
All WAVs use a 12-second canonical slot; padding is included in the recorded
frame count. No sidecar or fixture synthesizer participates in this diagnostic.

Completed report SHA-256:
`862bd6ce8806d08730f173a1274eaee1deb7f86415ce81baf9762d9aaf97e7ba`.
It records local HEAD `4e0dd459ad79302c2006bc8d9ccae272761d5b7a` plus source
hashes because the implementation was then uncommitted. It is **not** green
CI evidence for that claim-only commit. Native adapter source at measurement:
`f2109d3b325038318b74880d112bad52d806463c846b409e6cc87a91219fb198`.
The subsequent silent-output validation does not change the inference recipe;
rerun diagnostics whenever that recipe/frontend/model changes.

## Pinned inputs

| Input | Identity |
| --- | --- |
| Native runtime | sherpa-onnx and sherpa-onnx-core 1.13.8; numpy 2.2.6 |
| ASR/decoder | Faster-Whisper 1.2.1, small int8 CPU; PyAV 16.1.0 |
| Voice archive | `8dcad0d5902bea5bd62994eac0275355ce2cc755232e6d1505bedaace49d2a1f` |
| Installed data inventory | `6a2cd9b2de6d07d06a7902c6a1766e060e46922fbbde50da647a1a36bfd86cf5` |
| ONNX model | `c0722964a453322220e2cceaef76d733ca7c4cba1e341868ec76fb962d878e79` |
| Voice manifest at measurement | `5ce14ea40fbe4412c7773b867aa2846a0e666cae6b5725f6fcb6987fc97eb29d` |

## Observed quality

| Recipe | Set | Count | Mean CER |
| --- | --- | ---: | ---: |
| Selected noise 0.0 / 0.0 | Common phrases | 7 | 0.319375 |
| Selected noise 0.0 / 0.0 | Foreign product name | 1 | 0.209302 |
| Experimental noise 0.667 / 0.8 | Common phrases | 7 | 0.345591 |
| Experimental noise 0.667 / 0.8 | Foreign product name | 1 | 0.279070 |

The experimental recipe is a diagnostic override, not a promoted model/default.

| Selected recipe reference | Actual back-ASR | CER |
| --- | --- | ---: |
| Xin chào Việt Nam. | chí chẳng Việt Nam. | 0.428571 |
| Hôm nay chúng ta cùng xem một video mới. | hôm nay chúng ta cùng sang một vệ mới. | 0.258065 |
| Bạn có khỏe không? Tôi rất vui được gặp bạn. | Bạn có quảy khúc thối sức phụ được gặp bạn. | 0.454545 |
| Tôi không biết. Bạn có thể nói lại được không? | Chứ không biết bạn có thể nóng ra được không? | 0.200000 |
| Mẹ đang đi chợ, còn bố đang ở nhà. | Mẹ đang đi chợt còn bốn đang ở nhà. | 0.083333 |
| Hãy mở ứng dụng và chọn video cần xử lý. | Hãy mở ứng dụng và chọc vêu cân xử lý. | 0.200000 |
| Cảm ơn bạn đã theo dõi. Hẹn gặp lại vào ngày mai. | Hẹn gặp lại và hẹn gặp lại và hẹn gặp lại. | 0.611111 |
| Xin chào, đây là giọng nói tiếng Việt thật của DubFlow. | Xin chàng, đây là dọng nói tiếng Việt thật của dụt lâu. | 0.209302 |

## Evidence boundaries and next work

Hermetic tests cover bounded archive extraction, integrity/tamper checks,
duration fitting without cutting speech, invalid/silent PCM, runtime pinning,
B2 adapter/AUD-0 wiring, and preservation of existing B1 assets when bootstrap
fails. The B2 wiring test deliberately uses a fixture backend: it cannot prove
speech intelligibility. The optional native test proves actual model execution
and PCM only. Separate production-worker tests exercise the existing B1 fallback.

The first native ASR attempt failed with unpinned PyAV 19.0.1 because
`metadata_errors` was rejected by `av.open`. Installing/pinning 16.1.0 restored
actual decoding. This is observed local compatibility evidence, not a clean
packaged Windows installation test.

The converter's token inventory omits original multi-codepoint phoneme `t̪`
(ID 30). Reconstructing the original sorted inventory with that entry yields
281 bytes and matches the upstream metadata SHA-256
`1070c88fd8459f584d3cc41a5d3d9bdf6161d545cfb532615fa64ab78b8869c0`.
Changing token aliases in a separate temporary file did not establish good
speech. Investigate the original Mimic3 word/blank/BOS/EOS encoder, rather than
silently modifying the checksum-pinned installed data.

Still required: intelligible Vietnamese with listening evidence, Chinese
no-sidecar input and truthful translation route, long-media/per-item recovery,
native-crash isolation, packaged Windows/clean-machine testing, runtime license
notices/corresponding source, and all Issue-required CI for exact HEAD and
current main. Do not mark the Draft ready, merge it or release from this report.

## Original-encoder experiment (not selected for production)

An additional native probe used the same immutable ONNX model and the eSpeak
API exported by the already pinned Sherpa Windows DLL. It reconstructed the
upstream-hash-verified phoneme inventory, kept `t̪` together, and inserted
Mimic3 word/token blanks with BOS/EOS. Common-phrase mean back-ASR CER improved
to **0.150931**; the foreign-name case was **0.279070**. This is a promising
frontend diagnosis, not a production-quality claim.

The completed temporary probe report has SHA-256
`6548b2d7074cf603d26ca7293901a7a2c0b00c6af59c2d7dbc4212b497e18422`.
The prototype source digest was
`2b696423885e9ca083084a7cdf5606ece6ec5f505ea0ccb781443e4543525464`.
The portable rerun harness below is derived from that prototype, with explicit
model/output arguments, runtime checks and bounds. Its own output records its
source digest, model/profile identity, unknown symbols and every measured case.

```powershell
python tests/production/tts/qualify_frontend.py --model-root C:/model-cache --output-dir C:/frontend-evidence
```

This experiment is Windows-only and requires ONNX Runtime 1.30.0 in the
development environment. It does not add or select a production runtime.
It approximates clause punctuation and does not yet remove eSpeak language
switch markers such as `(en)`/`(vi)`. Unknown phonemes are retained in report
data. These defects must be fixed before this encoding can be promoted.
The selected production adapter/profile and their prior failed-quality
measurement remain unchanged.

Primary algorithm references: [Mimic3 voice frontend](https://github.com/MycroftAI/mimic3/blob/master/mimic3_tts/voice.py),
[MIT phonemes2ids](https://github.com/rhasspy/phonemes2ids), and the pinned
[Sherpa Piper encoder](https://github.com/k2-fsa/sherpa-onnx/blob/v1.13.8/sherpa-onnx/csrc/piper-phonemize-lexicon.cc).

## Isolated word-blank frontend — producer 2.1.0

The production adapter now selects `mimic3-vits-onnx-v1`. Native model/eSpeak
loading runs in a private child with the same app-owned interpreter, isolated
imports, bounded JSON/stdout/stderr, verified temporary waveform bytes and a
120-second generation deadline (initialization capped at 30 seconds). Unit
faults exercise initialization crash, after-health crash, timeout and oversized
reply. These demonstrate controlled child failure, not real reboot or installed
Windows package recovery. Parent-death/process-tree cleanup is still open.

The new frontend preserves `t̪`, word/token blanks, clause punctuation and
strips native language-switch tags. Unknown symbols produce explicit synthesis
warnings. It uses one inference per cue; exact original per-clause pauses and
broader speech-quality validation remain pending.

Actual local CPU report SHA-256:
`7d60dbdd5a67ab668b9a779cb4fa7906bacda0ee0a1b55168805083d0869d4b6`.
The report records backend, immutable model tree, voice manifest and individual
source digests because code was still uncommitted at HEAD `7f9f94c` when measured.
Native entrypoint digest at measurement:
`48522fc6510a7115fed21fe93bbaedaaa7af3253b1c5a0dff9b867818f2fbd55`.
Subsequent bridge retirement/cleanup guards do not alter that inference recipe.

| Set | Cases | Mean CER |
| --- | ---: | ---: |
| Common phrases | 7 | 0.119329 |
| Foreign product name | 1 | 0.209302 |

| Reference | Actual back-ASR | CER | Warning |
| --- | --- | ---: | --- |
| Xin chào Việt Nam. | Xin chẳng Việt Nam. | 0.214286 | None |
| Hôm nay chúng ta cùng xem một video mới. | Hôm nay chúng ta cùng xem một việu mới. | 0.096774 | Unsupported U0064 |
| Bạn có khỏe không? Tôi rất vui được gặp bạn. | Bạn có khỏe không tôi rất vui được các bạn? | 0.090909 | None |
| Tôi không biết. Bạn có thể nói lại được không? | Tôi không biết bạn có thể nói lại được không? | 0.000000 | None |
| Mẹ đang đi chợ, còn bố đang ở nhà. | Nè đang đi trợn, con bố đang ở nhà. | 0.250000 | None |
| Hãy mở ứng dụng và chọn video cần xử lý. | Hãy mở ứng dụng và chọn veu cần xử lý. | 0.100000 | Unsupported U0064 |
| Cảm ơn bạn đã theo dõi. Hẹn gặp lại vào ngày mai. | Cảm ơn bạn đã theo dõi hẹn gặp lại vòng ngày mai. | 0.083333 | None |
| Xin chào, đây là giọng nói tiếng Việt thật của DubFlow. | Xin chào, đây là giọng nói đến việc thật của Dụt Lâu. | 0.209302 | None |

Same eight references, Whisper decoder/normalization and 12-second slots were
used as before. This improvement is measured, but remaining errors are visible.
The profile remains `qualification-pending`; no release, Chinese translation,
long-form, clean-machine or human listening success is claimed by these results.

## Native process lifetime correction

The Windows development fault suite now kills a parent worker while native
inference is deliberately busy and checks OS process handles for both the
native child and its descendant. Both terminate after the parent's hard kill.
A real kill-on-close Windows Job Object is assigned before initialization;
the test does not replace that API with a mock. Separate subprocess cases
confirm stdin EOF stops busy initialization and inference in the actual
entrypoint on POSIX. Windows uses Job containment without a concurrent stdin
reader; the first reader prototypes hung while importing NumPy and failed the
real-model health test. A control run using the preceding entrypoint with the
new Job Object initialized successfully, isolating the regression to the reader.
The corrected Windows hermetic command has 27 cases: 25 passed, the POSIX lease
case and optional real-model case skipped. Two B2 integration tests passed.
The separate native voice suite then passed all 14 cases, including actual
verified ONNX/eSpeak initialization and non-silent Vietnamese PCM with Job
containment enabled. The hard-death fixtures execute the actual entrypoint and
its job-join logic, replacing only the expensive model with a busy test model.

These observations address process lifetime under development fault fixtures.
They do not qualify native audio accuracy, installed Windows restart, temporary
staging reclamation, long-form or batch behavior. ADR-0016 records the persistent
private stdin lease and compatibility limits. Required CI on preceding HEADs
becomes stale when this correction is pushed; #196 remains a hard dependency.
# Voice replacement after human rejection — 2026-10-07

The user listened to the two VAIS1000 examples and requested a different voice.
Its previous quality result remains historical evidence, not listening approval.
The current Draft default is VieNeu v3 Turbo fp32 CPU, Ngọc Huyền preset,
pending the user's comparison with Trúc Ly and Đoan Trang. All three actual
generated examples were presented in chat; no preference reply has yet been
recorded for these new examples.

## New actual native evidence

- Model inventory: `30163bab69ff85db094314385a56510129293f600fc130e53d1c9b3b4b276113`.
- SDK 3.8.3 / sea-g2p 0.9.1 / ORT 1.30.0 / tokenizers 0.23.2 / NumPy 2.2.6.
- Producer `3.0.0`, backend `vieneu-v3-turbo-onnx-v1`; native 48 kHz mono.
- Warm local inference blocks network and uses the checksum-verified model,
  codec and official speaker/reference-code presets. No GPU or model code download.
- Nine comparative WAVs: three voices × greeting/video/foreign product names.
  Each generated 4.4–6.0 seconds of audio in 2.19–3.01 seconds on this machine,
  excluding initialization. These are machine-specific measurements.
- Same eight-phrase corpus as the rejected model: common mean back-ASR CER
  `0.0047619047619047615`, previous `0.1193289045823608`; foreign-name case
  `0.046511627906976744`, previous `0.20930232558139536`.
  One common phrase changed “và” to “vào” in back-ASR; one foreign-name phrase
  recognized “giọng” as “dọng”. Neither result is a broad quality benchmark.
- Diagnostic report: scratch `dubflow-vieneu-195/quality/quality.json`, SHA-256
  `f8a0bba511ea1bb2b295dff30f80e92bf1a1376125aa9894f7be967989059314`.
  Report includes actual source digests and dirty working-tree status at execution.
- Real native speech + app-owned FFmpeg tempo fit passed, including exact
  target-frame output and a bounded 1.2 tempo comparison. Generation-cap/EOS
  rejection and model inventory/tamper/offline cache cases have deterministic tests.
- Windows hard worker-death during initialization and inference executes both
  actual entrypoints with a busy model seam and real native descendant handles.
  This is OS containment evidence, not actual long-video installed crash recovery.

## Chinese video probe

Native worker ran without a subtitle sidecar and with network blocked after
provisioning: AISHELL human audio in generated landscape color frames → Whisper
language detection (`zh`, probability `0.9922882318496704`) → pinned zh-en-vi
translation → actual VieNeu speech → AUD-0 source/stem/final mix → H.264/AAC/QC.
There were zero TTS/mix failures. Video SHA-256:
`2ed13f1e6cd2990b47aca06f00e14448272ddd7ae0c0e041fce5ba98df5a7d9a`.
Receipt scratch `dubflow-vieneu-195/chinese-video-result.json`, SHA-256
`73352c710cef13436d24ffb1bc6742d72f6aa5a70a8468e24f02cf183563ff63`.

This proves the native pipeline wiring on a short human-audio fixture with
generated frames. The ASR/proper-name translation remains semantically weak;
the inherited cue confidence 0.85 is not measured speech/translation quality.
The mixer warns that output RMS is below its configured target. A repeated
completed B2 run produced identical video bytes on this machine but re-rendered
into a new immutable private generation; no warm render-reuse claim is made.
The initial harness mistakenly required B1-style unchanged render mtime;
the corrected receipt records B2 regeneration explicitly. The first development
probe omitted `DUBFLOW_WORKER_PROCESS=1` and hit repository `packaging` shadowing;
the proper worker import mode then completed the real route.

Still required: human voice selection/quality, broader material, portrait and
no-audio cases, long/batch/restart and staging reclamation, packaged/clean-machine
execution, full distribution notices, all Required-CI for current HEAD/main.
Issue #166 and #175 remain open; this evidence does not approve a release.

# Built-in preset choices — 2026-10-07

The owner requested many selectable voices. The installed manifest now contains
25 official licensed presets: 14 male/11 female; 15 northern, 8 southern and
2 central; natural, news and storytelling/reading styles. IDs are stable ASCII;
gender/region/description and style mapping are checked against the actual
checksum-pinned `voices.json`. License approval is separate from listening quality.

## Actual preset and pipeline evidence

- `qualify_presets.py` generated actual offline 48 kHz PCM for **all 25 presets**,
  using the same phrase and verified shared model/codec. All waveform hashes were
  distinct. End-to-end initialization/verification/synthesis took 4.62–6.34 seconds
  per preset on this development machine. One phrase is availability evidence,
  not a broad pronunciation/quality evaluation.
- Scratch receipt `dubflow-vieneu-195/preset-catalog/presets.json`, SHA-256
  `c0e0a84eee219233f651179529976123b1206ff0f8435992e352a3f94412f0fd`.
  The report records source digests and the dirty working tree before commit.
- Actual worker/video runs selected Trúc Ly then Thái Sơn using the same source
  and output directory. Both passed H.264/AAC QC, had zero TTS/mix failures,
  carried the requested ID in voice provenance and produced different video/audio
  identities. The earlier private TTS/mix data remained byte-identical. A third
  run with an unknown ID produced usable B1 and recorded `VOICE_ID_UNKNOWN`.
- Scratch receipt `dubflow-vieneu-195/voice-pipeline/voice-pipeline.json`, SHA-256
  `476a607bb05e68f848ec55c074a836eec83cb86850a44ae965b65dcdfe919324`.
  This probe uses generated color/sine source and an explicit Vietnamese sidecar;
  it does not test ASR or the installed desktop/supervisor. The first harness
  comparison omitted the manifest's `sha256:` prefix normalization and was
  corrected; no production private-audio mutation was found.
- Earlier clean HEAD `23fe826` also passed actual human-Chinese-audio portrait
  and a 60-second/14-cue loop, with zero TTS/mix failures. Silent video preserved
  B1. A poisoned item failed and later cases continued in the development harness.
  Receipt `media-cases-result.json`, SHA-256
  `c238cd2e1280a4643643aed2165866ca367a91b5a30fb86ef8596776900b78a5`.
  Generated frames and repeated human audio do not qualify long-form/batch execution.

## Desktop and transport

Per-video dubbing and voice selection are exposed with gender, region and style
filters. Filtering never silently changes the saved selection. Queue reload and
active/recovered-job locking preserve the choice. The native host reads the
installed `app/models/manifests` catalog and carries the ID through supervisor
CLI/JSON and worker start arguments to the real adapter. ADR-0019 defines the
optional-field/legacy-queue compatibility plan and exact component pairing.

Desktop unit tests and TypeScript/bundle build passed. A headless browser exercised
the built UI with mocked Tauri IPC: enable dubbing, filter, select Thùy Dung,
reload, switch between independent jobs, start with the exact selected ID and
lock active controls. No browser errors; screenshot was visually inspected.
Mocked IPC is not native-host execution. TTS catalog/identity tests, three B2
wiring/preservation cases, worker command validation and 17 release tests passed.
Rust sources parsed with rustfmt; the local MSVC linker is unavailable, so native
host/supervisor compile and new Rust tests require the Windows CI runner.

Windows run `37643703652` failed on HEAD `23fe826` when builder stdout emitted
Vietnamese into the runner's cp1252 console, after dependency installation and
16 release tests passed. The builder now uses escaped JSON on stdout; a real
cp1252 stream regression test round-trips Vietnamese metadata. Release workflow
adds desktop/catalog/supervisor tests before native qualification. No green
installed smoke is inferred from that failed run. Fresh exact-HEAD/base CI is
required for the new selection implementation; keep PR #195 Draft.

Remain open: human listening/quality, native packaged/restart execution, long-form
and durable batch qualification, private staging reclamation and full distribution
notices. None of this evidence qualifies the full #175 production capability gate.

# Real acquired Chinese video — 2026-10-08

Development production worker at source HEAD
`162a933b290f4a6823b8faf88fbd3f355368f363` processed the complete public Bilibili
`BV13x41117TL` acquired by #167's verified owned SDK/default transport. Source
MP4 SHA-256 `3f9a8f575443caca835c559a2890869f6d64d8e25b9fda5c2ed58bffe5917177`;
38,026,136 bytes, about 554 seconds, H.264/AAC at 640x360. There was no SRT/VTT
sidecar or generated speech input. This is actual acquired media, beyond the
earlier generated frames and repeated human-audio cases.

The CPU run completed in 906.687 seconds and emitted 272 cues. Whisper detected
Chinese (`zh`, probability 0.996829); pinned Argos routes `zh -> en -> vi`
translated the cues. Requested preset `vi-thuy-dung-vieneu3-v1` reached actual
VieNeu synthesis and AUD-0 mixing. The final 27,525,975-byte MP4 passed codec QC
(H.264/AAC, 553.921 seconds), with SHA-256
`f0967db92c908e5267bc0550ffe6cb84566d6e8dde891f6d2262a9cc3d9b65f2`.
Scratch receipt `dubflow-vieneu-195/live-bilibili-worker/report.json`, SHA-256
`bef7276b97f756769df78cb799abd70364df82dedae1d03fab45dab3eb442be8`.

**Quality gaps remain.** All 46 TTS failures were `DURATION_FIT_REQUIRED`:
natural speech exceeded the safe rate, or the tempo-adjusted output still
exceeded the slot. Original source audio was retained for these cues, and the
manifest/QC explicitly recorded `B2_AUDIO_DEGRADED` / downgrade. The 226 emitted
dialogue cues and playable output do not establish complete Vietnamese dubbing.
Reading the translated cues also exposes incorrect name/meaning preservation
(for example, a band name became a literal generic phrase). No human listening,
translation approval or broad intelligibility claim follows from codec success.

This used the development worker/model cache, not the native durable supervisor,
a new installer or an end-user GUI flow. It does not qualify installed live-source
intake, authenticated websites, long-form 2–6-hour video, durable large batches,
updater compatibility or #166/#175 release acceptance. Preserve this run while
improving duration fitting, translation review and native execution.

# Streaming mixer integration — 2026-10-09

#203 / PR #204 was accepted at main `404a429` after four exact-source CI lanes,
verified staged/installed producer2.0.1 execution and a complete six-hour PCM
capacity/restart rehearsal. Its actual cached272-cue/250-speech-file replay
matched historical PCM hashes/metrics while using63,328,256 bytes peak RSS.
These adapter-specific results are linked in PR204; they do not qualify the
combined B2 worker or the full product.

The #166 worker now selects that file-based producer and pins its code/native
recipe in generation identity. Local B2 wiring tests pass4 cases using actual
NumPy; they prove producer2.0.1 selection and preserve prior output when a
different producer creates a separate generation. Worker tests pass27 cases,
including an actual protocol failure envelope retaining MEDIA_PROBE_FAILED and
retryable false. Desktop voice/queue tests and build pass. CI selection passes26
cases with3 existing platform/tool skips. Three focused release qualification
guard tests pass: reject legacy mixer provenance, changed PCM bytes and repeated
or untyped corrupt-media failures. An earlier complete release suite had one
new-test import error; that missing import was fixed and the affected class
rerun. Current-source hosted full release evidence is still required.

Windows qualification now requires actual streaming mix provenance and WAV
hashes/headers in the real TTS worker output, plus exactly one typed corrupt
container failure. Previous a92b2e4 Windows evidence tested the older mixer and
older main2f1fb; it cannot qualify this integration. Keep Draft until current
source/current main required lanes and full #166 acceptance are proven.

The 25 existing real preset WAVs were also checked against the current catalog
and original receipt:14 male/11 female;15 North/8 South/2 Central;11 natural,
6 storytelling,4 news,4 story-reading. A local listening/filtering library lives
outside Git at `TEMP/dubflow-vieneu-195/preset-catalog/listen.html`; receipt
`voice-library-review.json` SHA256
`31dded9877ebd97059dbe1c4015c6aa5c3f87191b4651c7dcff943702a64aabc`.
This reuses recorded precommit samples for human comparison; it is not fresh
current-source synthesis or an installed preview feature. Voice quality,
translation meaning, full installed GUI/recovery and long-form/batch qualification
remain open.

## Fresh real-media worker and native retry rejection

Source `ac35465` completed the full production worker on the same acquired
554-second Bilibili video with no sidecar: fresh Whisper detected Chinese,
pinned Argos translated272 cues, VieNeu3.1 emitted250 speech artifacts and
streaming AUD-0 producer2.0.1 mixed/rendered a553.921-second H.264/AAC MP4.
Elapsed589.234seconds; measured parent worker peak916,459,520 bytes, explicitly
excluding native TTS children. This is not a full application memory bound.
All519 historical output files were preserved. Complete output decode passed.
MP4 SHA256 `df3e4d2d7f502a03f24fa641eee76d6d03ca31edc8814cb889d71d349645bfac`;
receipt `TEMP/dubflow-vieneu-195/live-bilibili-worker-stream-ac35465/report.json`
SHA256 `8d241a6f460181ebb197d8e7952fdf263a7a113a6b64adfb1e4f6ee84ee93eba`.
Twenty-two `DURATION_FIT_REQUIRED` cues still preserve source audio, with a
visible downgrade; complete/intelligible Vietnamese speech and translation
quality are not established by successful decode.

Windows37874749115 failed at its real CPU pipeline qualification guard:
the corrupt-media input repeated unchanged. The worker correctly retained
`MEDIA_PROBE_FAILED`, but the actual media adapter marks a nonzero ffprobe exit
retryable true. The earlier mocked boundary case used false and missed this.
The worker now overrides only that code to nonretryable at its protocol boundary,
retaining the exact condition. Its regression supplies the actual adapter's true
retryability; unknown errors remain nonretryable. There is no supervisor retry
policy or shared media-adapter change. All native predicates remain required.

An actual worker subprocess and real ffprobe against a corrupt container emitted
one `MEDIA_PROBE_FAILED`/retryable-false/attempt1 envelope, exit2 and failed shutdown,
without published output. Precommit repair receipt:
`TEMP/dubflow-vieneu-195/corrupt-probe-fixed-ac35465/report.json`, loaded worker
SHA256 `ac0c30eb21dba5cd477da9b17cf0172f44f02a71414b1da4db0fb2c7908e3740`.
Seven applicable worker tests passed after this correction. Failed native logs
and old success receipts are historical evidence; fresh exact repair-head lanes
must run before readiness. Full #166/#175 acceptance remains open.

## Film-dialogue choices and two-hour mux correction

The owner requested voices suitable for film dubbing. Source `17dbd82` prioritizes
the11 natural presets when browsing a new job; all25 presets remain selectable,
and a saved choice survives filtering/reload without being changed implicitly.
The compiled desktop UI passed a headless Edge exercise of browsing, persistence,
locked active controls and a separate processing version preserving old output.
That exercise uses mocked IPC; it is not native GUI execution.

At `9ed2ff9`, all11 natural presets generated two actual Vietnamese dialogue
lines each through the selected VieNeu3.1 CPU adapter. Receipt
`TEMP/dubflow-vieneu-195/preset-catalog/movie-dialogue-9ed2ff9/report.json`
SHA256 `818b46d05b1016bafdf3c964acea4f680186024a9fcd393c9365715e6e5a820e`.
The22 WAVs form a local human comparison library. Their optional back-ASR
diagnostic is not a judgment of acting, emotion, accent quality or human approval.
No default preset is changed from these two-line scores.

The first actual two-hour worker at `9ed2ff9` successfully synthesized120 Adam
cues, but B2 QC rejected a truncated render and safely preserved B1. Its receipt
remains failed, SHA256
`8fcda539640c79d90a39903345cd053e6e7b9e65a4ef5d0f0ca43e65b062794b`.
An actual minimal FFmpeg reproduction showed that `-shortest` treats an early
embedded subtitle ending as the output end. Source `1c16aaa` uses the original
video stream's integer duration/time base for a bounded mux instead. Four actual
small embedded/burn-in cases passed full decode after the correction.

The rerun at exact `1c16aaaea238c5081b5ac279808f6f5550e901f3`/main `404a429`
completed120 real Adam speech artifacts, zero failed cues and no final warnings.
The H.264/AAC output retains7200.093seconds although its embedded subtitle ends
at7146seconds. Full FFmpeg output decode and all three editable WAV hash parity
checks passed. Receipt
`TEMP/dubflow-vieneu-195/two-hour-worker-1c16aaa/report.json`, SHA256
`7f35eeb1ae3269508745a8230a2414ac12940d6e55deecc3f39443719d7fb9c6`.
Elapsed887.812seconds includes verification/decode. Parent peak71,311,360bytes
excludes native TTS and FFmpeg children; it is not whole-app memory qualification.

This is a two-hour loop of the retained real Bilibili video/audio with an authored
Vietnamese120-cue sidecar and four repeated phrases, using cached owned models.
Speech inference caching may reuse a repeated phrase. It does not establish a
unique two-hour film, fresh long-form ASR/translation, human listening quality,
installed GUI recovery, VFR/100–500-item batch or full #175 qualification.
Old outputs, code-bound receipts and failed evidence remain preserved.

## Native per-cue recovery qualification preparation

The Windows stage and installed production smoke now additionally prepares a
27-second/three-cue case with explicit English sidecar language and real local
translation/TTS. It observes a bounded, verified committed TTS record and WAV,
hard-kills the native supervisor tree before all three cues complete, and restarts
the exact same immutable job. Success requires the original record and WAV hash
and mtime to remain unchanged, an explicit same-cue reuse warning, three real
speech artifacts, durable SQLite completion, verified editable audio and full MP4
decode. A timed kill during model download does not satisfy this separate check.

Focused deterministic tests reject corrupted PCM/metadata, foreign paths,
oversized records, an already completed synthesis window, regenerated speech,
rewritten records, missing reuse evidence and incomplete durable state. Their
passing fixtures are not actual installed recovery evidence. Native qualification
must execute this guard on its exact next source/current base before acceptance.
The existing `1c16aaa` Windows run is preserved while it legitimately runs; it
does not contain or qualify this newly prepared test guard. No runtime/model,
public contract or supervisor durable mutation is introduced by this preparation.

## Bounded cue refusal isolation preparation

An actual child-process regression reproduced that a bounded native refusal
returned generic `TTS_NATIVE_INFERENCE_FAILED` and closed the child, poisoning
following cues. The prepared bridge now accepts only reviewed, sequence-bound
`TTS_TEXT_UNSUPPORTED`/`TTS_SPEECH_INCOMPLETE` cue errors and continues with the
next input. No failed cue is regenerated or its incomplete speech published.
All untyped/malformed/initialization/fatal failures retain containment and close.

Deterministic tests exercise both actual private entrypoints with a model fixture,
the same child PID across refusal/next cue, no implicit request repeat, and fatal
unknown/list-valued/missing-scope/invalid-condition/initialization replies. Direct
VieNeu bound tests require typed rejection before waveform publication. Existing
hard-worker-death tests still verify native process and descendant termination.
Actual model continuity and exact current-source native qualification remain
separate required evidence; these fixtures do not approve speech or film acting.

## Prepared visible dubbing downgrade qualification — 2026-10-09

The supervisor now derives the completion message from a bounded, exact-hash
committed QC snapshot, including durable reconciliation and completed replay.
Partial speech and total B1 fallback are visible through the existing desktop
status display. Changed/missing/invalid evidence yields an unverified message.
Private diagnostics, public schemas, models/voice pins and durable SQLite are
unchanged. The direct SHA dependency reuses locked 0.10.8 without an upgrade.

Six focused release qualification tests passed locally, covering native guard
rejection of hidden downgrade, repeated/retryable bad input, poisoned following
speech, ducking a failed cue, inconsistent QC, regenerated completed replay and
missing replay evidence. These use fixtures and do not qualify native speech.
Three supervisor tests cover exact committed bytes, bounded/corrupt/foreign QC,
partial/full/B1 status and durable replay; their execution is pending native CI.
Local `cargo check --locked -p dubflow-supervisor --tests` could not reach the
crate because the Windows MSVC linker `link.exe` is absent. Syntax parsing and
whitespace checks succeeded; no Rust compile/test pass is inferred.

The strengthened staged/installed smoke will require actual pinned content
refusal followed by valid speech, source-preserving partial/B1 exports, honest
status and completed replay with unchanged hashes/mtimes. The currently active
b9 native lane predates these changes; its eventual results cannot qualify this
prepared child. Full #166/#175, human listening, native GUI and release gates
remain open. No stable release or merge readiness is inferred here.

The prepared qualification additionally checks actual portrait dimensions and
a no-audio source with explicit VI sidecar. No-audio B1 completion states that
the source has no audio and must not claim original audio; absent AAC is allowed
only for that explicitly declared evidence. Seven focused release tests now
pass, including retained default AAC admission. An initial fixture-only replay
test lacked the new width/height fields and failed before its intended check;
the fixture was corrected and the full focused suite rerun successfully.
Four supervisor tests await native execution; local MSVC remains unavailable.
These additions do not change the production worker's media/QC behavior or
qualify real ASR/translation, GUI use or human film quality.
