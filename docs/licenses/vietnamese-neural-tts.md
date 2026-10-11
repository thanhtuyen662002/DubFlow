# Vietnamese neural TTS distribution decision

## Voice data

Selected voice: MycroftAI Mimic3 `vi_VN/vais1000_low` 0.1.0, converted to
Sherpa-ONNX VITS by k2-fsa. The model is trained on VAIS1000 and uses one
speaker at 22,050 Hz. It is distinct from the Piper VAIS1000 voice fine-tuned
from Lessac and from VIVOS/InfoRe candidates with restricted/unknown terms.

Primary metadata: [MycroftAI voice manifest](https://github.com/MycroftAI/mimic3/blob/master/mimic3_tts/voices.json).
It pins the original LICENSE to 45 bytes with SHA-256
`0792914b42cbac29cb2ca5e729be0e7f959786ead4c1c31c027aca9a0a108c02`.
Those exact bytes are `https://creativecommons.org/licenses/by/4.0/` plus a
newline; their digest was verified. Decision: weights may be redistributed
under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) with VAIS1000,
MycroftAI and k2-fsa attribution, change disclosure and the license link.

The selected archive and the installed data tree are separately pinned in
`models/manifests/production-tts-v1.json`. Two conversion scripts in the
upstream archive are deliberately omitted from installation and never run.
Weights and generated media are kept outside normal Git history.

## Native runtime and phonemizer

Sherpa-ONNX and sherpa-onnx-core are pinned to 1.13.8. Sherpa's own code has
[Apache-2.0 terms](https://github.com/k2-fsa/sherpa-onnx/blob/v1.13.8/LICENSE).
Its VITS phonemizer incorporates eSpeak-NG; **the complete shipped runtime
must not be described as Apache-only**. eSpeak-NG code/data has GPL terms.
The upstream build pins [eSpeak-NG for Piper](https://github.com/k2-fsa/sherpa-onnx/blob/v1.13.8/cmake/espeak-ng-for-piper.cmake)
to `ed530aa113046142eb5115cf2fc9157854d0ffe1`.

Before stable publication, bundle applicable copyright/license notices and
the exact corresponding source/build material or compliant source offer for
the native runtime and phonemizer. Verify these obligations against the exact
Windows wheels/build, not only the Python package metadata. This remains a
release qualification gate under #175; local inference is not release approval.

The neural adapter uses local inference only. Provisioning is app-owned and
checksum verified. No cloud credential, system Python installation, user
voice cloning or additional media upload is involved.
