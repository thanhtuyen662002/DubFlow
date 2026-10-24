"""Argos 1.11.0's single-hypothesis package recipe without its SBD imports.

Only used after ArgosRuntime verifies the SDK/component/model pins and settings.
Keep paragraph handling, native decode arguments and detokenization equivalent to
PackageTranslation.translate + apply_packaged_translation in that pinned SDK.

Adapted from argostranslate/translate.py (Argos Translate 1.11.0).
MIT License
Copyright (c) 2020 Argos Open Technologies, LLC

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from __future__ import annotations


class PinnedPackageTranslation:
    def __init__(self, pkg, sentencizer, settings):
        self.pkg = pkg
        self.sentencizer = sentencizer
        self.settings = settings
        self.translator = None

    def translate(self, input_text: str) -> str:
        if self.translator is None:
            from ctranslate2 import Translator

            self.translator = Translator(
                str(self.pkg.package_path / "model"),
                device=self.settings.device,
                inter_threads=self.settings.inter_threads,
                intra_threads=self.settings.intra_threads,
                compute_type=self.settings.compute_type,
            )

        paragraphs = []
        for paragraph in input_text.split("\n"):
            sentences = self.sentencizer.split_sentences(paragraph)
            tokenized = [self.pkg.tokenizer.encode(sentence) for sentence in sentences]
            prefix = self.pkg.target_prefix
            batches = self.translator.translate_batch(
                tokenized,
                target_prefix=[[prefix]] * len(tokenized) if prefix != "" else None,
                replace_unknowns=True,
                max_batch_size=self.settings.batch_size,
                batch_type="tokens",
                beam_size=max(1, self.settings.beam_size),
                num_hypotheses=1,
                length_penalty=0.2,
                return_scores=True,
            )
            # Argos decodes the concatenated sentence tokens once per paragraph.
            tokens = [token for batch in batches for token in batch.hypotheses[0]]
            value = self.pkg.tokenizer.decode(tokens)
            if prefix != "" and value.startswith(prefix):
                value = value[len(prefix):]
            if value.startswith(" "):
                value = value[1:]
            paragraphs.append(value)
        return "\n".join(paragraphs).lstrip("\n")
