# Real CPU Vietnamese TTS evidence — Issue #166 / PR #195

## Decision: not production-qualified

The candidate executes a real native VITS model. It does not yet provide
acceptable Vietnamese dubbing. Local inference, non-silent WAVs and hermetic
tests are not release approval. Issue #166 and the full product gate #175
remain open. Chinese auto-language/NMT routing is also still unfinished.

## Reproducible local diagnostic

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
