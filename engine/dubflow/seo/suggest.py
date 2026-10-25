"""Deterministic Vietnamese posting suggestions using only local text.

This is a small frequency-based helper, not a translation or language model.
No input is sent to a service and no timeline or job state is mutated.
"""

from __future__ import annotations

from collections import Counter
import re
import unicodedata


# Vietnamese syllables used as function words and conversational filler. Keep
# content-bearing genre words (including tu, tiên, hiệp, nấu, ăn) out of this set.
STOPWORDS = frozenset("""
là của và có không được này đó thì mà cho với một những các đã đang sẽ rất
cũng như khi nếu để ra vào lên xuống tôi bạn anh em chị nó họ chúng ta ngươi
mình tớ cậu hắn nàng ông bà cô chú bác dì dượng mợ thím cháu con bố mẹ cha
ấy đây kia kìa đấy đâu nào ai gì sao nào nhé nha nhỉ nhá ạ à ừ ờ ừm ôi ơi
vâng dạ hả hử hở ào ồ chao chà ê á ối úi ư ứ chứ thôi rồi còn lại vẫn
chỉ ngay nữa hết luôn đều từng mỗi mọi bất kỳ ít nhiều mấy bao nhiêu bấy
vài đôi dăm nửa cả tất hết thảy hết sức quá lắm khá hơi thật thực quả vốn
vốn dĩ hẳn chắc chắn có lẽ dường tựa tự tựa giống hệt hơn kém nhất bằng
theo từ đến tới tại ở trên dưới trong ngoài giữa bên cạnh quanh trước sau
qua cùng đối với về bởi vì nên do nhờ nhằm rằng hoặc hay nhưng tuy dù
dẫu mặc dầu song còn vậy thế tức nghĩa là nên cần phải hãy đừng chớ muốn
thích thể khả năng bị chịu khiến làm việc điều cái chiếc thứ sự sự đang
mỗi mọi toàn toàn bộ hầu gần tận mãi vừa mới chưa chẳng chả từng sắp
thường thỉnh thoảng bỗng đột nhiên liên tục cứ càng càng chẳng há sao vậy
tại vì tại sao thế nào thế nên cho nên thành thử miễn trừ ngoại trừ
bất cứ bất luận mọi người bọn tụi chúng tôi chúng ta chúng mình các bạn
nói bảo hỏi biết thấy nghe nghĩ tưởng hiểu nhớ quên gọi đáp rằng xin cảm
ơn chào tạm biệt được rồi đúng vâng dạ ok okay uh ah ha hihi haha
""".split())

_WORD_RUN = re.compile(r"[^\W_]+(?:\s+[^\W_]+)*", re.UNICODE)
_GENRES = (
    (("tiên hiệp", "tu tiên"), ("tienhiep", "phimtienhiep")),
    (("cổ trang",), ("cotrang",)),
    (("ẩm thực", "món ăn", "nấu"), ("amthuc", "nauan")),
    (("hài", "hài hước", "tấu hài"), ("haihuoc",)),
    (("phim",), ("reviewphim", "phimhay")),
    (("game", "trò chơi"), ("game",)),
    (("thú cưng", "mèo", "chó"), ("thucung",)),
)
_FALLBACK_TAGS = (
    "phudeviet", "videongan", "khampha", "giaitri", "videohay", "khoanhkhac",
)


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    # Subtitle line breaks and controls must not leak into titles/JSONL framing.
    text = "".join(" " if unicodedata.category(char).startswith("C") else char for char in text)
    return " ".join(text.split())


def _runs(text: str) -> list[tuple[str, ...]]:
    return [tuple(match.group().split()) for match in _WORD_RUN.finditer(text.lower())]


def _phrases(text: str):
    # Each punctuation-delimited run is separate: never bridge sentences/cues.
    for words in _runs(text):
        for size in (1, 2, 3):
            for start in range(len(words) - size + 1):
                phrase = words[start:start + size]
                if phrase[0] in STOPWORDS or phrase[-1] in STOPWORDS:
                    continue
                if not any(char.isalpha() for word in phrase for char in word):
                    continue
                yield phrase


def _contains(words: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    return any(words[start:start + len(phrase)] == phrase for start in range(len(words) - len(phrase) + 1))


def _overlaps(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if _contains(left, right) or _contains(right, left):
        return True
    # Suppress sliding windows and fragments such as "đậu phụ" / "phụ sốt".
    # A shared syllable inside otherwise separate phrases is still allowed.
    return any(left[-size:] == right[:size] or right[-size:] == left[:size]
               for size in range(1, min(len(left), len(right)) + 1))


def _keywords(cues: list[str], title: str, tags: list[str]) -> list[str]:
    counts = Counter(phrase for text in [title, *cues, *tags] for phrase in _phrases(text))
    title_phrases = set(_phrases(title))

    def rank(phrase: tuple[str, ...]):
        # A compound such as "tiên hiệp" should beat its frequent syllables.
        score = counts[phrase] * (1, 3, 4)[len(phrase) - 1]
        if phrase in title_phrases:
            score *= 2.5
        if any(word in STOPWORDS for word in phrase[1:-1]):
            score *= 0.5
        return (-score, -len(phrase), phrase)

    selected: list[tuple[str, ...]] = []
    for phrase in sorted(counts, key=rank):
        if not any(_overlaps(phrase, previous) for previous in selected):
            selected.append(phrase)
            if len(selected) == 8:
                break
    return [" ".join(phrase) for phrase in selected]


def _clip(text: str, limit: int) -> str:
    """Fit complete whitespace-delimited words; an oversized word is omitted."""
    if len(text) <= limit:
        return text
    words: list[str] = []
    used = 0
    for word in text.split():
        size = len(word) + bool(words)
        if used + size > limit:
            break
        words.append(word)
        used += size
    return " ".join(words).rstrip(" ,;:|-")


def _capitalise(text: str) -> str:
    for index, char in enumerate(text):
        if char.isalpha():
            return text[:index] + char.upper() + text[index + 1:]
    return text


def _meaningful(text: str) -> bool:
    words = [word for run in _runs(text) for word in run]
    return len(words) >= 4 and any(word not in STOPWORDS and any(char.isalpha() for char in word) for word in words)


def _titles(cues: list[str], title: str, keywords: list[str]) -> list[str]:
    topic = _clip(keywords[0], 45) if keywords else _clip(title, 45)
    topic = topic or "Video tiếng Trung"
    candidates: list[str] = []
    if title:
        short_title = _clip(title, 90)
        if short_title:
            candidates.append(f"{short_title} (Vietsub)")

    def hook_score(cue: str) -> int:
        return 4 * cue.count("?") + 3 * cue.count("!") + 2 * len(re.findall(r"\d+", cue))

    meaningful = [cue for cue in cues if _meaningful(cue)]
    if meaningful:
        hook = max(meaningful, key=hook_score)
        if hook_score(hook):
            candidates.append(hook)
    candidates.append(f"{topic} khiến ai cũng bất ngờ")
    review = f"Review {topic}"
    short_title = _clip(title, 100 - len(review) - 3)
    candidates.append(f"{review} | {short_title}" if short_title else f"{review} (Vietsub)")
    candidates.append(f"Khám phá {topic} qua từng khoảnh khắc")
    if len(keywords) > 1:
        candidates.append(f"{_clip(keywords[1], 60)}: Câu chuyện phía sau")
    candidates.extend(("Video tiếng Trung với phụ đề Việt", "Cùng xem những khoảnh khắc đáng chú ý", "Khám phá câu chuyện qua bản Vietsub"))

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = _clip(_capitalise(candidate), 100)
        key = candidate.casefold()
        if candidate and key not in seen:
            result.append(candidate)
            seen.add(key)
            if len(result) == 5:
                break
    return result


def _sentence(text: str, limit: int = 144) -> str:
    text = _clip(_capitalise(text), limit - 1)
    if text and text[-1] not in ".!?…":
        text += "."
    return text


def _description(cues: list[str], title: str) -> str:
    sentences: list[str] = []
    for cue in cues:
        for part in re.split(r"(?<=[.!?…])\s+", cue):
            if not _meaningful(part):
                continue
            sentence = _sentence(part)
            if sentence and sentence.casefold() not in {item.casefold() for item in sentences}:
                sentences.append(sentence)
            if len(sentences) == 2:
                return " ".join(sentences) + "\n#vietsub"
    if not sentences:
        sentences.append(_sentence(title) or "Khám phá video tiếng Trung với phụ đề tiếng Việt.")
    sentences.append("Cùng theo dõi video với phụ đề tiếng Việt.")
    return " ".join(sentences) + "\n#vietsub"


def _hashtag(text: str) -> str:
    folded = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    body = "".join(char for char in folded if not unicodedata.combining(char))
    body = re.sub(r"[^a-z0-9]", "", body)
    # The bound includes '#'. Skip, rather than chop, oversized keywords.
    return "#" + body if 2 <= len(body) <= 29 else ""


def _hashtags(cues: list[str], title: str, tags: list[str], keywords: list[str]) -> list[str]:
    runs = [run for text in [title, *cues, *tags] for run in _runs(text)]
    source_tag_set = {_hashtag(tag) for tag in tags}
    genres: list[str] = []
    for markers, genre_tags in _GENRES:
        if any(any(_contains(run, tuple(marker.split())) for run in runs)
               or _hashtag(marker) in source_tag_set for marker in markers):
            genres.extend("#" + tag for tag in genre_tags)

    candidates = [*map(_hashtag, keywords[:3]), *genres, "#vietsub", "#trungquoc",
                  *map(_hashtag, keywords[3:]), *map(_hashtag, tags)]
    result = list(dict.fromkeys(tag for tag in candidates if tag))[:15]
    for tag in _FALLBACK_TAGS:
        if len(result) >= 8:
            break
        hashtag = "#" + tag
        if hashtag not in result:
            result.append(hashtag)
    return result


def suggest_post(
    cues: list[str],
    *,
    source_title: str = "",
    source_tags: list[str] = (),
    duration_s: float | None = None,
) -> dict:
    """Return titles, description, hashtags and ranked Vietnamese keywords.

    ``duration_s`` is optional caller metadata; duration does not change the
    text ranking or imply a genre. Output order is deterministic.
    """
    lines = [line for cue in cues if (line := _clean(cue))]
    title = _clean(source_title)
    tags = [_clean(tag) for tag in source_tags]
    keywords = _keywords(lines, title, tags)
    return {
        "titles": _titles(lines, title, keywords),
        "description": _description(lines, title),
        "hashtags": _hashtags(lines, title, tags, keywords),
        "keywords": keywords,
    }
