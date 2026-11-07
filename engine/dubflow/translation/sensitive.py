"""Words that platforms' community rules often flag (chết, giết, dao, ...).

The user keeps a list of words, each with the spelling to use instead
("chết" -> "chớt"), and picks how a match is shown:

* ``variant``: subtitles and the Vietnamese voice both use the replacement
  ("chớt", "giớt"), the way creators already write around the filters;
* ``beep``: subtitles show the word masked ("ch*t") and the voice gets a beep
  over it (the voice says the replacement underneath, so nothing flagged is
  ever spoken even where the beep is short).

Matching is whole words only, case-insensitive, on NFC text, and keeps the
first letter's case ("Chết" -> "Chớt").
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
import unicodedata
from typing import Any

MODES = ("variant", "beep")
MAX_WORDS = 300
MAX_WORD_CHARS = 40

# A starting list; the user edits it in "Cài đặt AI" → "Từ nhạy cảm".
DEFAULT_WORDS: tuple[tuple[str, str], ...] = (
    ("chết", "chớt"), ("giết", "giớt"), ("dao", "dzao"), ("súng", "sún"), ("máu", "máo"),
    ("tự tử", "tự tữ"), ("tự sát", "tự xát"), ("đâm", "đăm"), ("bắn", "bắng"),
    ("ma túy", "ma thúy"), ("ma tuý", "ma thuý"), ("đánh bom", "đánh bôm"), ("khủng bố", "khủng bớ"),
    ("thi thể", "thi thễ"), ("xác chết", "xác chớt"), ("cờ bạc", "cờ bặc"),
)


# Everyday words that contain a listed word and must stay as they are.
KEEP_PHRASES: tuple[str, ...] = ("dao động", "dao cạo", "chết máy", "bắn tin", "máu mặt", "máu lửa", "đâm chồi", "đâm ra")


class SensitiveWordsError(ValueError):
    pass


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _mask(word: str) -> str:
    """Keep the first and last letter of each syllable, star the rest: "chết" -> "ch*t", "dao" -> "d*o"."""
    def syllable(part: str) -> str:
        if len(part) <= 2:
            return part[0] + "*"
        head = 2 if part[:2].lower() in {"ch", "gh", "gi", "kh", "ng", "nh", "ph", "qu", "th", "tr"} else 1
        return part[:head] + "*" * max(1, len(part) - head - 1) + part[-1]
    return " ".join(syllable(part) for part in word.split())


def _keep_case(original: str, replacement: str) -> str:
    if original.isupper() and len(original) > 1:
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


SOUNDS = ("beep", "bird", "dog", "custom")
MAX_SOUND_PATH = 1024


@dataclass(frozen=True)
class SensitivePolicy:
    mode: str
    words: tuple[tuple[str, str], ...]
    # What is laid over a flagged word in beep mode: the tone, a bird, a dog, or the user's file.
    sound: str = "beep"
    sound_file: str | None = None

    @classmethod
    def from_mapping(cls, value: Any) -> "SensitivePolicy":
        if not isinstance(value, Mapping) or value.get("mode") not in MODES:
            raise SensitiveWordsError("sensitive.mode must be variant or beep")
        raw = value.get("words")
        if not isinstance(raw, list) or len(raw) > MAX_WORDS:
            raise SensitiveWordsError("sensitive.words must be a bounded list")
        words: dict[str, str] = {}
        for item in raw:
            if not isinstance(item, Mapping) or not isinstance(item.get("from"), str) or not isinstance(item.get("to"), str):
                raise SensitiveWordsError("each sensitive word needs from and to")
            source = " ".join(_nfc(item["from"]).split()).lower()
            target = " ".join(_nfc(item["to"]).split())
            if not source or len(source) > MAX_WORD_CHARS or len(target) > MAX_WORD_CHARS or re.search(r"[\x00-\x1f]", source + target):
                raise SensitiveWordsError("sensitive words must be short single-line text")
            words[source] = target or source
        sound = value.get("sound", "beep")
        if sound not in SOUNDS:
            raise SensitiveWordsError("sensitive.sound must be beep, bird, dog or custom")
        sound_file = value.get("sound_file")
        if sound == "custom":
            if not isinstance(sound_file, str) or not sound_file.strip() or len(sound_file) > MAX_SOUND_PATH or re.search(r"[\x00-\x1f]", sound_file):
                raise SensitiveWordsError("sensitive.sound_file must name the chosen sound file")
        else:
            sound_file = None
        # Longest first, so "xác chết" wins over "chết".
        return cls(value["mode"], tuple(sorted(words.items(), key=lambda pair: -len(pair[0]))), sound, sound_file)

    def _pattern(self) -> re.Pattern[str] | None:
        if not self.words:
            return None
        alternatives = "|".join(re.escape(source).replace(r"\ ", r"\s+") for source, _ in self.words)
        keep = "|".join(re.escape(phrase).replace(r"\ ", r"\s+") for phrase in KEEP_PHRASES)
        # A kept phrase matches first and is put back unchanged.
        return re.compile(rf"(?<![\w])(?:(?P<keep>{keep})|(?:{alternatives}))(?![\w])", re.IGNORECASE)

    def matches(self, text: str) -> list[tuple[int, int, str]]:
        """(start, end, matched word) for each flagged word in ``text`` (NFC)."""
        pattern = self._pattern()
        return [(match.start(), match.end(), match.group(0)) for match in pattern.finditer(_nfc(text)) if not match.group("keep")] if pattern else []

    def _replace(self, text: str, choose) -> str:
        pattern = self._pattern()
        if pattern is None:
            return text
        table = dict(self.words)
        return pattern.sub(lambda match: match.group(0) if match.group("keep") else
                           _keep_case(match.group(0), choose(" ".join(match.group(0).lower().split()), table)), _nfc(text))

    def subtitle(self, text: str) -> str:
        """What the subtitle shows."""
        if self.mode == "beep":
            return self._replace(text, lambda source, _table: _mask(source))
        return self._replace(text, lambda source, table: table[source])

    def spoken(self, text: str) -> str:
        """What the Vietnamese voice says (the beep, if any, is laid over it later)."""
        return self._replace(text, lambda source, table: table[source])


def apply_to_cues(cues: Sequence[Any], policy: SensitivePolicy | None, *, spoken: bool) -> tuple[tuple[Any, ...], int]:
    """Cues with flagged words replaced, and how many cues changed."""
    from dataclasses import replace

    if policy is None or not policy.words:
        return tuple(cues), 0
    changed = 0
    result = []
    for cue in cues:
        text = cue.translated_text
        if not text:
            result.append(cue)
            continue
        new = policy.spoken(text) if spoken else policy.subtitle(text)
        if new != text and new.strip():
            changed += 1
            result.append(replace(cue, translated_text=new))
        else:
            result.append(cue)
    return tuple(result), changed


def default_policy_mapping(mode: str = "variant") -> dict[str, Any]:
    return {"mode": mode, "words": [{"from": source, "to": target} for source, target in DEFAULT_WORDS]}
