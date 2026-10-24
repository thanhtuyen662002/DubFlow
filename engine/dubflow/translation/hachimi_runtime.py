"""Optional HachimiMT-60 Chinese→Vietnamese route (opt-in, see docs/licenses/hachimi-mt-60.md)."""
from __future__ import annotations

import importlib.metadata
from pathlib import Path
import re

from engine.dubflow.models import ensure_model_profile
from .adapter import TranslationError
from .argos_runtime import ArgosRuntime, child, digest, read_json

PROFILE_PATH = "models/manifests/production-translation-hachimi-v1.json"
COMPONENT_VERSIONS = {"ctranslate2": "4.8.2", "sentencepiece": "0.2.2"}
ENGINES = ("argos", "hachimi")
# Decode recipe evaluated in issue #244; the penalties stop the web-novel
# model from looping on short subtitle lines.
DECODE_OPTIONS = {"beam_size": 4, "length_penalty": 1, "max_decoding_length": 256, "return_scores": False, "repetition_penalty": 1.2, "no_repeat_ngram_size": 2}
SPECIAL_TOKENS = frozenset({"<s>", "</s>", "<pad>"})
MAX_SOURCE_TOKENS = 500
MAX_SENTENCES = 128
# The web-novel model respells Latin names (Twitterrific -> TWitherrific). Runs
# of Latin words are swapped for X1, X2, ... markers, which it copies reliably
# (7/7 test sentences vs 0-4/7 for numeric, <n>, [n] and @n markers), then
# restored. Pure numbers are left to the model.
LATIN_SPAN = re.compile(r"[A-Za-z][A-Za-z0-9.+#&'_-]*(?: [A-Za-z][A-Za-z0-9.+#&'_-]*)*")
MARKER = re.compile(r"X(\d+)")
LATIN_PASSTHROUGH = "latin-marker-x-v1"


def protect_latin(sentence: str) -> tuple[str, list[str]]:
    """Replace Latin runs with X1, X2, ... markers; returns the masked text and the runs."""
    spans: list[str] = []

    def mark(match: re.Match) -> str:
        value = match.group(0)
        core = value.rstrip(".'_-&+#")
        spans.append(core)
        return f"X{len(spans)}" + value[len(core):]

    return LATIN_SPAN.sub(mark, sentence), spans


def restore_latin(output: str, spans: list[str]) -> str | None:
    """Put the runs back, or None unless every marker appears exactly once."""
    if sorted(int(value) for value in MARKER.findall(output)) != list(range(1, len(spans) + 1)):
        return None
    # The model sometimes glues a marker to the preceding comma ("SMS,X2").
    output = re.sub(r"([,;:])(?=X\d)", r"\1 ", output)
    return MARKER.sub(lambda match: spans[int(match.group(1)) - 1], output)


def split_sentences(text: str) -> list[str]:
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?；;])|\n+", text) if part.strip()]
    if not sentences or len(text) > 16384 or len(sentences) > MAX_SENTENCES:
        raise TranslationError("TRANSLATION_INPUT_TOO_LONG", "cue exceeds the bounded sentence recipe")
    return sentences


class HachimiRuntime:
    def __init__(self, app_root: Path, model_root: Path, source_language: str, *, progress=None):
        self.profile_path = child(app_root.absolute(), PROFILE_PATH)
        try:
            self.profile = read_json(self.profile_path)
        except FileNotFoundError as error:
            raise TranslationError("TRANSLATION_PROFILE_MISSING", "app-owned Hachimi profile is missing") from error
        except (OSError, ValueError) as error:
            raise TranslationError("TRANSLATION_PROFILE_INVALID", "unable to read app-owned Hachimi profile") from error
        if self.profile.get("schema_version") != 1 or self.profile.get("runtime_components") != COMPONENT_VERSIONS or type(self.profile.get("model_dir")) is not str:
            raise TranslationError("TRANSLATION_PROFILE_INVALID", "unsupported pinned Hachimi profile")
        if source_language not in self.profile.get("source_languages", ()):
            raise TranslationError("TRANSLATION_ROUTE_UNAVAILABLE", "HachimiMT-60 translates Chinese only")
        self.source_language = source_language
        self.profile_hash = digest(self.profile_path)
        self.model_dir = child(model_root.absolute(), self.profile["model_dir"])
        self.progress = progress
        self._translator = None
        self._source = None
        self._target = None

    def provenance(self) -> dict:
        return {
            "backend": "hachimi-mt-60-ct2-int8-v1",
            "runtime": "ctranslate2-" + COMPONENT_VERSIONS["ctranslate2"],
            "components": dict(COMPONENT_VERSIONS),
            "requested_source_language": self.source_language,
            "backend_route": ["zh", "vi"],
            "profile_sha256": self.profile_hash,
            "settings": {"device": "cpu", "compute_type": "int8", "sentence_boundary": "cue-punctuation-zh-v1", "latin_passthrough": LATIN_PASSTHROUGH, **DECODE_OPTIONS},
            "license": self.profile.get("license"),
            "upstream": dict(self.profile.get("upstream") or {}),
            "packages": [{key: item[key] for key in ("id", "sha256")} for item in self.profile["artifacts"]],
        }

    def prepare(self, model_root: Path) -> None:
        if self._translator is not None:
            return
        try:
            for name, version in COMPONENT_VERSIONS.items():
                if importlib.metadata.version(name) != version:
                    raise TranslationError("TRANSLATION_RUNTIME_MISMATCH", "translation runtime differs from its component pins")
        except importlib.metadata.PackageNotFoundError as error:
            raise TranslationError("TRANSLATION_RUNTIME_MISSING", "app-owned translation component is missing") from error
        # Downloads happen only here, i.e. only for jobs that opted in.
        ensure_model_profile(self.profile_path, model_root, progress=self.progress)
        import ctranslate2
        import sentencepiece
        self._translator = ctranslate2.Translator(str(self.model_dir), device="cpu", compute_type="int8", inter_threads=1, intra_threads=4)
        self._source = sentencepiece.SentencePieceProcessor(model_file=str(self.model_dir / "source.spm"))
        self._target = sentencepiece.SentencePieceProcessor(model_file=str(self.model_dir / "target.spm"))

    def _decode(self, sentences: list[str]) -> list[str]:
        batch = []
        for sentence in sentences:
            tokens = self._source.encode(sentence, out_type=str)
            if len(tokens) > MAX_SOURCE_TOKENS:
                raise TranslationError("TRANSLATION_INPUT_TOO_LONG", "sentence exceeds the safe inference token bound")
            batch.append(tokens + ["</s>"])
        results = self._translator.translate_batch(batch, **DECODE_OPTIONS) if batch else []
        return [self._target.decode([token for token in result.hypotheses[0] if token not in SPECIAL_TOKENS]).strip() for result in results]

    def translate_text(self, text: str) -> str:
        if self._translator is None:
            raise TranslationError("TRANSLATION_RUNTIME_MISSING", "route has not been initialized")
        sentences = split_sentences(text)
        protected = [protect_latin(sentence) for sentence in sentences]
        pieces: list[str | None] = [None] * len(sentences)
        pending = []
        for index, (masked, spans) in enumerate(protected):
            # Nothing but Latin text and punctuation: keep it as written.
            if spans and not re.search(r"[^\W\d_]", MARKER.sub("", masked)):
                pieces[index] = sentences[index]
            else:
                pending.append(index)
        for index, output in zip(pending, self._decode([protected[index][0] for index in pending])):
            pieces[index] = restore_latin(output, protected[index][1]) if protected[index][1] else output
        # A dropped or duplicated marker falls back to translating the sentence as is.
        fallback = [index for index in pending if pieces[index] is None]
        for index, output in zip(fallback, self._decode([sentences[index] for index in fallback])):
            pieces[index] = output
        if not all(pieces):
            raise TranslationError("TRANSLATION_EMPTY", "HachimiMT-60 produced no translated text")
        return " ".join(" ".join(pieces).split())


def create_runtime(engine: str, app_root: Path, model_root: Path, source_language: str, *, progress=None):
    """Pick the translator for one job. Hachimi covers Chinese only; other
    languages keep the default Argos route so the option never blocks a job."""
    if engine not in ENGINES:
        raise TranslationError("TRANSLATION_ENGINE_UNKNOWN", "unknown translation engine")
    if engine == "hachimi" and source_language in {"zh", "zh-CN", "zh-TW"}:
        return HachimiRuntime(app_root, model_root, source_language, progress=progress)
    return ArgosRuntime(app_root, model_root, source_language, progress=progress)
