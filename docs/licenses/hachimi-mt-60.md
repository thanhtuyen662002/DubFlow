# HachimiMT-60 (optional Chinese → Vietnamese translator)

DubFlow can optionally translate Chinese dialogue with **HachimiMT-60**
instead of the default Argos Translate route (zh → en → vi). The option is
**off by default**; the model is downloaded only after a user turns it on.

## Attribution (CC-BY-4.0)

- Model: HachimiMT-60, "Chinese→Vietnamese Web-Novel Translation Model"
- Author: ngocdang83 — <https://huggingface.co/ngocdang83/HachimiMT-60-zh-vi>
- Revision used: `50cd988cefa7773c2663337e54829df8bb036659`
- License: Creative Commons Attribution 4.0 International
  (<https://creativecommons.org/licenses/by/4.0/>)
- Changes made by DubFlow: the Marian checkpoint was converted to CTranslate2
  4.8.2 with int8 quantization. The converter keeps every vocabulary row and
  uses the model's own `<pad>` start token and its trained start embedding
  (the stock converter assumes a trailing padding row). The SentencePiece
  tokenizer files are redistributed unchanged.

## Licensing caveat — read before relying on it

The upstream model card states that its training data includes **Gemini API
teacher distillation** and asks downstream users to verify the current Gemini
API terms for derivative-work training. Google's current Gemini API terms
restrict using outputs to develop competing models. The upstream training
data also lists `chi-vi/hirashiba-mt-zh2vi-b-filtered`, whose terms were not
established. DubFlow has **not** established that commercial use of this model
is permitted. It is offered as an **experimental, opt-in** option; the
default translator remains Argos Translate. Do not use it for commercial work
until you have verified these terms yourself.

## Known quality limits

Trained mainly on web novels: it often uses a literary register (`ta`/`ngươi`),
can mistranslate gaming or e-commerce terms, and in evaluation reversed one
negation. Review its output with *Xem & sửa bản dịch* before publishing.
Evaluation (30 short-video sentences, chrF): HachimiMT-60 42.5 vs. Argos
pivot 22.6 (issue #244).
