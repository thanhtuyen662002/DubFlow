# Kokoro v1.0 English narration voices: distribution inventory

These voices are used only by "Phim tài liệu" (ADR-0049, phase 2a). The pack is
not in the installer: the app downloads it on first use, after the user starts a
narration, and verifies every file against `production-kokoro-en-v1.json`.

## Pinned upstream data

| Component | Immutable upstream identity | Terms |
| --- | --- | --- |
| Kokoro-82M v1.0 fp32 ONNX model and voice table (`model.onnx`, `voices.bin`, `tokens.txt`) | [sherpa-onnx export](https://huggingface.co/csukuangfj/kokoro-multi-lang-v1_0/tree/f7b96bb6bef5c5da4d3aa4f4e0498fbbf62dc78b) of [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | Apache-2.0 (the `LICENSE` file is downloaded with the pack) |
| US and UK English lexicons (`lexicon-us-en.txt`, `lexicon-gb-en.txt`) | same revision | Apache-2.0, as distributed with the export |
| English espeak-ng data subset (`phontab`, `phonindex`, `phondata`, `phondata-manifest`, `intonations`, `en_dict`, `lang/gmw/en`, `lang/gmw/en-US`, `lang/gmw/en-GB-x-rp`) | same revision | GPL-3.0-or-later ([espeak-ng](https://github.com/espeak-ng/espeak-ng)) |
| Runtime | sherpa-onnx 1.13.8 and ONNX Runtime 1.30.0, already in the runtime | Apache-2.0 / MIT; preserve each distribution's notices |

The espeak-ng files are data read by sherpa-onnx's own phonemizer, used only for
words missing from the lexicons. They are downloaded unmodified from the pinned
revision, kept as separate files in the model folder, and never linked into
DubFlow code. The source of that data is the espeak-ng project linked above.

The manifest inventories 15 files totalling 366,933,826 bytes. Only the nine US
and UK English speakers listed in the manifest are offered; the other languages
in the voice table are not used.

## Synthetic voice notice

Kokoro voices are synthetic. Finished documentaries should not present the
narrator as a real person.
