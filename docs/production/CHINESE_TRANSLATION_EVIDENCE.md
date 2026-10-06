# Production source language / Chinese translation evidence

Issue #196, Draft PR #197. This is development evidence, not release approval.
The branch starts at main `5ab7670beb2c33a8462494304a7f66f98102d7b2`.

## Actual local inference

Run on Windows using the development venv outside Git. Argos Translate 1.11.0,
CTranslate2 4.8.2, SentencePiece 0.2.2; CPU int8, beam 4. Both archives match the
SHA256/byte counts and extracted-tree pins in
`models/manifests/production-translation-v1.json`. No weights/media enter Git.
Final profile SHA256: `18e321aaa409f365af3d4b5066184709095994eee32430d9b5ff0d057c219775`.

The package index provides zh-en 1.9 and en-vi 1.9; Chinese is explicitly routed
through English. A declared zh-CN/zh-TW tag remains in transcript/provenance;
the underlying model is generic zh. Vietnamese has an explicit identity route.

| Source | Observed Vietnamese output |
| --- | --- |
| 你好，世界。 | Chào cả thế giới. |
| 请在下一站下车。 | Xin hãy xuống ở trạm kế tiếp. |
| 这个视频很有意思。 | Đoạn phim này thật thú vị. |
| 我们明天早上八点见。 | Hẹn gặp anh lúc 8 giờ sáng mai. |

These are authored text probes, not a speech/media corpus. Successful inference
does not establish translation accuracy, pronoun fidelity or dialect coverage.
The last example introduces a pronoun absent from Chinese, a concrete quality
limitation that must be evaluated with the real corpus.

A separate fresh process exercised the en-vi route offline: “Please get off at
the next station.” produced “Xin hãy xuống ở trạm kế tiếp.”, with its installed
tree still verified. Development console output required UTF-8; the native
probe's JSON file is written explicitly with UTF-8. This is not evidence for
the packaged worker's process/encoding behavior.

During inference `socket.socket.connect` was patched to raise in the native
process. All four examples completed, and both extracted model trees still
matched their pins afterward. This establishes that this warmed development
route did not require network access; first-use download/packaged offline
recovery requires separate evidence. Raw logs/results remain in development
scratch `dubflow-language-196`, outside Git.

## Auxiliary model discovery and adapter decision

The initial unmodified Argos path translated a sentence but the default Stanza
loader could update package resources/tokenizers. Enforcing offline loading
exposed incompatible legacy resource/checkpoint fields (`packages`,
`feat_dropout`, `all_caps`). A configuration-only conversion probe failed and
is not part of the product.

The final adapter uses unchanged pinned Argos translation/SentencePiece data,
with an injected deterministic `cue-punctuation-v1` sentence boundary recipe.
It loads no auxiliary sentence model. Input is bounded at 16,384 characters,
128 sentence parts and 512 tokens per sentence; excessive input returns a typed
error before silent model truncation. The recipe/runtime component identities
are recorded and invalidate cached translation when changed. See ADR-0017.

## Deterministic regression evidence

Tests cover language authority/probability, explicit Chinese aliases, mismatch,
unresolved auto/sidecar, unchanged source cue IDs/integer ticks, package and
runtime pins, extraction paths/links/special entries/resource limits, model
quarantine, per-cue interruption/tamper recovery and derived-output invalidation.
The worker regression executes the worker flow with test media/model adapters:
same inputs reuse render, changed model or edited sidecar regenerates output.
Those test outputs are fixtures, not playable video evidence.

Run from repo root with bytecode disabled:

```powershell
python -m unittest discover -s tests/production/worker -p 'test_*.py'
python -m unittest discover -s tests/translation -p 'test_*.py'
python scripts/validate_governance.py
git diff --check
```

## Qualification still required

- Exact HEAD/current-main PR Fast and applicable Integration evidence.
- Windows Release and Release / Soak evidence for the packaged candidate.
- Real Chinese local video with no sidecar: detection, ASR, translation, mux,
  editable artifacts and provenance inspected together.
- Long media, interrupted/restarted processing, mixed-success batch, cold
  first-use bootstrap and clean-machine recovery.
- Corpus/human quality review and distribution attribution/license inventory.

The full #166/#175 user-value gate remains open. This leaf does not qualify
TTS, source acquisition, OCR, speaker identity, CapCut or GPU capabilities.
