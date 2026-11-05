"""What a scene talks about and what a stretch of footage says, in shared concepts (ADR-0049, Tier-2).

The script's ``visual_search_tags`` and narration are English; the source lines
are usually machine-recognised Chinese. Comparing them word for word finds
nothing, so both sides are mapped onto a small fixed lexicon of concepts
(manure, biogas, elevator, feed, ventilation…), each with English word stems
and Chinese terms, plus the plain numbers they mention (``26`` in "26 floors"
and in "26层"). The matcher uses the overlap to pick filler footage that shows
what the narration is about instead of whatever comes next.

Everything here is a pure function of its text: the same script and sources
always give the same concepts, so the visual plan stays deterministic.
"""
from __future__ import annotations

import re
from typing import Iterable, Sequence

# concept: (English stems, Chinese terms). An English stem of 4+ letters matches words it starts;
# a shorter one matches the whole word only.
LEXICON: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "pig": (("pig", "hog", "swine", "pork", "piglet", "sow", "sows", "boar", "livestock"), ("猪", "生猪", "牲畜")),
    "breeding": (("breed", "pregnan", "farrow", "gestat", "birth", "litter", "insemin", "mating"),
                 ("产仔", "分娩", "怀孕", "繁殖", "配种", "妊娠", "母猪", "仔猪", "产房", "保育")),
    "manure": (("manure", "waste", "dung", "excrement", "sewage", "slurry", "faeces", "feces", "urine"),
               ("粪", "粪便", "污水", "排泄", "尿", "废水")),
    "biogas": (("biogas", "methane", "digest", "ferment", "anaerob"), ("沼气", "甲烷", "发酵", "沼液", "沼渣", "厌氧")),
    "energy": (("electric", "power", "energy", "generat", "kilowatt", "solar", "heat recovery"),
               ("发电", "用电", "电能", "能源", "电力", "光伏", "余热", "热能")),
    "fertilizer": (("fertili", "compost", "soil", "crop", "farmland"), ("肥料", "有机肥", "化肥", "土壤", "农田", "肥")),
    "elevator": (("elevator", "lift", "hoist"), ("电梯", "升降", "提升")),
    "feed": (("feed", "fodder", "grain", "corn", "maize", "soy", "trough", "nutrition", "diet", "meal"),
             ("饲料", "喂", "饲喂", "玉米", "豆粕", "料线", "料塔", "营养", "吃")),
    "air": (("air", "ventilat", "fan", "fans", "exhaust", "airflow", "odor", "odour", "smell", "deodor", "filter"),
            ("通风", "风机", "空气", "排风", "新风", "除臭", "气味", "臭", "过滤", "换气")),
    "climate": (("temperatur", "heat", "heating", "cooling", "climate", "warm", "humid", "degree"),
                ("温度", "恒温", "降温", "空调", "温控", "制冷", "加热", "湿度", "保温")),
    "water": (("water", "drink", "pipe", "pipes", "plumbing"), ("水", "饮水", "水管", "管道", "供水")),
    "tower": (("tower", "skyscraper", "floor", "floors", "storey", "story", "stories", "high-rise", "highrise", "building"),
              ("大楼", "楼", "层", "高楼", "楼房", "大厦", "建筑")),
    "structure": (("concrete", "steel", "structur", "beam", "column", "foundation", "construct", "rebar", "frame", "pillar"),
                  ("混凝土", "钢", "结构", "地基", "建设", "施工", "建造", "钢筋", "柱", "框架", "梁")),
    "load": (("weight", "load", "ton", "tons", "tonne", "heavy", "pound", "pounds"), ("重量", "承重", "吨", "荷载")),
    "control": (("control", "automat", "computer", "screen", "monitor", "sensor", "data", "digital", "algorithm",
                 "robot", "camera", "software"),
                ("控制", "中控", "自动", "智能", "电脑", "监控", "传感", "数据", "数字", "机器人", "摄像", "系统", "屏")),
    "disease": (("disease", "virus", "fever", "infect", "biosecur", "disinfect", "outbreak", "quarantin", "sick", "pathogen",
                 "epidemic", "vaccin"),
                ("病", "疫", "病毒", "猪瘟", "消毒", "防疫", "感染", "隔离", "生物安全", "疫苗")),
    "hygiene": (("shower", "wash", "clean", "steril"), ("洗澡", "淋浴", "清洗", "清洁", "冲洗", "洗消")),
    "staff": (("worker", "staff", "employee", "technician", "farmer", "people"), ("员工", "工人", "人员", "饲养员", "技术员")),
    "money": (("cost", "money", "dollar", "yuan", "invest", "price", "loss", "losing", "profit", "billion", "million",
               "revenue", "budget", "expens"),
              ("成本", "钱", "万元", "亿元", "投资", "价格", "亏", "利润", "亿", "万元", "收入", "资金", "盈利")),
    "meat": (("slaughter", "meat", "processing", "butcher"), ("屠宰", "肉", "加工")),
    "scale": (("acre", "area", "land", "square", "football field", "hectare", "size", "scale"),
              ("面积", "亩", "平方", "土地", "规模", "占地")),
    "transport": (("truck", "transport", "logistic", "vehicle", "shipping", "deliver", "road"),
                  ("运输", "卡车", "货车", "物流", "车辆", "运送", "公路")),
    "pollution": (("pollut", "environment", "emission", "carbon", "contaminat"), ("污染", "环境", "排放", "环保", "碳")),
    "dam": (("dam", "reservoir", "turbine", "hydro", "spillway"), ("大坝", "水库", "水电", "涡轮", "水轮机", "泄洪")),
    "bridge": (("bridge", "span", "cable-stayed", "suspension"), ("桥", "大桥", "斜拉", "悬索")),
    "tunnel": (("tunnel", "boring", "underground"), ("隧道", "盾构", "地下")),
    "rail": (("rail", "train", "railway", "track", "station"), ("铁路", "高铁", "列车", "轨道", "车站")),
    "crane": (("crane",), ("塔吊", "起重机", "吊车")),
    "city": (("city", "urban", "town"), ("城市", "市区")),
}
_WORD = re.compile(r"[a-z][a-z'-]*")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Numbers worth matching: big enough to be a fact ("26 floors"), not a year-free small count.
MIN_NUMBER, MAX_NUMBER = 10, 10_000_000
# A tag says what should be on screen; it counts more than a word in passing in the narration.
TAG_WEIGHT, NARRATION_WEIGHT = 2, 1


def _english(stem: str, words: Sequence[str], text: str) -> bool:
    if " " in stem or "-" in stem:
        return stem in text
    if len(stem) >= 4:
        return any(word.startswith(stem) for word in words)
    return stem in words


def numbers(text: str) -> set[str]:
    found = set()
    for match in _NUMBER.finditer(text):
        try:
            value = float(match.group(0).replace(",", ""))
        except ValueError:
            continue
        if MIN_NUMBER <= value <= MAX_NUMBER and value == int(value):
            found.add(f"#{int(value)}")
    return found


def concepts(text: str) -> frozenset[str]:
    """The lexicon concepts and ``#number`` tokens a text (English or Chinese) mentions."""
    lowered = text.casefold()
    words = _WORD.findall(lowered)
    found = {concept for concept, (stems, terms) in LEXICON.items()
             if any(term in text for term in terms) or any(_english(stem, words, lowered) for stem in stems)}
    return frozenset(found | numbers(text))


def scene_terms(tags: Iterable[str], narration: str) -> tuple[tuple[str, int], ...]:
    """(concept, weight) for one scene, sorted: its visual tags count double, the narration once."""
    weights: dict[str, int] = {}
    for concept in concepts(" ".join(tags)):
        weights[concept] = max(weights.get(concept, 0), TAG_WEIGHT)
    for concept in concepts(narration):
        weights[concept] = max(weights.get(concept, 0), NARRATION_WEIGHT)
    return tuple(sorted(weights.items()))


def score(terms: Sequence[tuple[str, int]], found: frozenset[str]) -> int:
    return sum(weight for concept, weight in terms if concept in found)
