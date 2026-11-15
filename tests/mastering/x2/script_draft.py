"""Step 1a of X2: an AI text model drafts the REAL-DEV narration from the transcript cues only (README §3.2, §8).

usage:
  python -I script_draft.py estimate <manifest.json> --base <sandbox dir> [--split REAL-DEV]
  python -I script_draft.py dry-run  <manifest.json> --base <sandbox dir> --out <dir>      (mock transport, $0)
  python -I script_draft.py live     <manifest.json> --base <sandbox dir> --out <dir>
                                     --approval "<Owner 1a approval: who, when, where>" --cap-usd <x> --max-calls <n>

Nothing in this file spends money unless ``live`` is given an Owner approval, a cap and a call limit. Tests and
``dry-run`` use a mock transport and never touch the network.

Order of checks for every request (any failure stops the run; nothing is retried around it):
1. the split is READY FOR PAID SCRIPT DRAFT (``validate_manifest --stage draft --verify-files``);
2. each cue file is the hashed one the manifest names, and was made from the hashed media;
3. ``api_scope.preflight``: only transcript text leaves the machine (no frame, audio, video, file, URL, base64);
4. ``Ledger.before``: spent + the worst case of this request stays under the cap, and the call limit holds;
5. after the response: ``Ledger.after`` reads ``usage`` (missing usage = charged at worst case, run stops);
6. the answer is validated: counts per type, only cue ids that exist for that source, no no-footage sentences.

Output (on the test machine only, never in the repo): ``narration-draft.json`` and ``ledger.jsonl``. The draft is
``status: DRAFT_NOT_LOCKED``. It is AI-written (``method: ai-draft-with-cues``), never a human label, and holds no
no-footage sentence or hard negative: README §3.2 step 3 keeps those for a separate human writer B.
"""
from __future__ import annotations

import hashlib, json, os, sys, time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import api_scope  # noqa: E402
import validate_manifest as vm  # noqa: E402

MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_TOKENS = 16_000
NARRATION_LANGUAGE = "vi"  # DubFlow narrates in Vietnamese; the narration language is a fixed DEV parameter
# USD per million tokens (Anthropic API price list, checked 2026-11-15). Thinking is billed as output.
PRICES = {"claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20}}
TYPES = ("direct", "general", "illustrative")
# README §3.2: about 9 direct (>= 4 factual), 5 general, 5 illustrative footage sentences for DEV; the 6 no-footage
# sentences (>= 3 hard negatives) come from human writer B, never from this step.
QUOTA = {"d-s1": {"direct": 5, "general": 2, "illustrative": 3, "min_factual": 2},
         "d-s2": {"direct": 4, "general": 3, "illustrative": 2, "min_factual": 2}}
MAX_ATTEMPTS = 2  # per source: one more ask when an answer is invalid; each attempt is a counted, capped call

SYSTEM = """You write narration sentences for a short video, in {language_name}, from the source's speech transcript only.
You never see the video. Each transcript cue has an id, a start and end time in milliseconds, and its text.

Write exactly the number of sentences of each type that the request asks for:
- "direct": says what the speech says at a given moment (a fact, an event, a step). Cite at least one cue.
- "general": sums up a stretch of the source in one sentence. Cite the cues it draws on (may be several).
- "illustrative": a sentence that a fitting shot of this source could illustrate. Cite cues if it draws on them.
Mark "factual": true when the sentence holds a proper name, a number, a date or a time.
Write at least the asked number of factual "direct" sentences.

Rules:
- Use only facts present in the cues. Do not invent names, numbers or events.
- Cite only cue ids from this request, exactly as written. Never invent a cue id.
- Do not write sentences that have no footage in the source, and do not write trick or negative sentences.
- One sentence per item, at most 220 characters, natural spoken {language_name}.
- Answer with the JSON object only."""
LANGUAGE_NAMES = {"vi": "Vietnamese", "en": "English"}

SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "planned_type": {"type": "string", "enum": list(TYPES)},
                    "factual": {"type": "boolean"},
                    "cues": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "planned_type", "factual", "cues"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sentences"],
    "additionalProperties": False,
}


class StopRun(RuntimeError):
    """A condition under which no further request may be sent (budget, missing usage, refusal, scope)."""


class BudgetError(StopRun):
    pass


# ---------------------------------------------------------------- inputs


def load_inputs(manifest: Mapping[str, Any], split: str, base: str) -> list[tuple[dict, list[dict]]]:
    """The split's sources with their cues, after the draft gate and the cue-file hashes are checked."""
    problems = vm.problems(manifest, "draft", split, base)
    if problems:
        raise StopRun("not READY FOR PAID SCRIPT DRAFT: " + "; ".join(problems))
    out = []
    for source in manifest["splits"][split]["sources"]:
        raw = Path(base, source["cues_path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["cues_sha256"]:
            raise StopRun(f"{source['source_id']}: cue file differs from the manifest's cues_sha256")
        doc = json.loads(raw.decode("utf-8"))
        if doc.get("media_sha256") != source["sha256"] or doc.get("asr_status") != "ok":
            raise StopRun(f"{source['source_id']}: cue file is not an ok ASR run of this media")
        cues = [{"id": f"{source['source_id']}#{i}", "start_ms": c["start_ms"], "end_ms": c["end_ms"],
                 "text": c["text"]} for i, c in enumerate(doc["cues"])]
        out.append((source, cues))
    return out


def build_request(source: Mapping[str, Any], cues: Sequence[Mapping[str, Any]], quota: Mapping[str, int], *,
                  model: str = MODEL, language: str = NARRATION_LANGUAGE, max_tokens: int = MAX_TOKENS,
                  effort: str = EFFORT) -> dict:
    """One Messages API request: text only (title, language, cues). No path, no hash, no media."""
    name = LANGUAGE_NAMES[language]
    ask = {
        "source_id": source["source_id"],
        "source_title": source.get("candidate_title") or "",
        "source_language": source.get("language"),
        "narration_language": language,
        "write": {t: quota[t] for t in TYPES},
        "min_factual_direct": quota["min_factual"],
        "cues": [dict(c) for c in cues],
    }
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM.format(language_name=name),
        "messages": [{"role": "user", "content": [{"type": "text", "text": json.dumps(ask, ensure_ascii=False)}]}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
    }


# ---------------------------------------------------------------- cost


def worst_input_tokens(request: Mapping[str, Any]) -> int:
    """An upper bound on input tokens: a token is never shorter than one UTF-8 byte, plus room for framing."""
    return len(json.dumps(request, ensure_ascii=False).encode("utf-8")) + 1_000


def estimate_input_tokens(request: Mapping[str, Any]) -> int:
    """A typical count (about 3 bytes of mixed English/Vietnamese JSON per token). For planning only."""
    return len(json.dumps(request, ensure_ascii=False).encode("utf-8")) // 3


class Ledger:
    """Layer 2 of POC v2 §6: every response's usage is read and priced; the next call waits on the cap."""

    def __init__(self, cap_usd: float, max_calls: int, path: Path | None = None, prices: Mapping = PRICES):
        if cap_usd <= 0 or max_calls <= 0:
            raise BudgetError("a run needs a positive cap and call limit")
        self.cap, self.max_calls, self.path, self.prices = float(cap_usd), int(max_calls), path, prices
        self.spent, self.calls, self.rows = 0.0, 0, []

    def price(self, model: str) -> Mapping[str, float]:
        if model not in self.prices:
            raise BudgetError(f"no price for model {model!r}")
        return self.prices[model]

    def worst(self, request: Mapping[str, Any]) -> float:
        p = self.price(request["model"])
        return (worst_input_tokens(request) * p["input"] + request["max_tokens"] * p["output"]) / 1e6

    def before(self, request: Mapping[str, Any]) -> float:
        worst = self.worst(request)
        if self.calls >= self.max_calls:
            raise BudgetError(f"call limit {self.max_calls} reached")
        if self.spent + worst > self.cap:
            raise BudgetError(f"spent ${self.spent:.4f} + worst case ${worst:.4f} would pass the cap ${self.cap:.2f}")
        return worst

    def after(self, request: Mapping[str, Any], response: Mapping[str, Any] | None, note: str = "") -> float:
        self.calls += 1
        usage = (response or {}).get("usage")
        ok = isinstance(usage, Mapping) and all(isinstance(usage.get(k), int) for k in ("input_tokens", "output_tokens"))
        if ok:
            p = self.price((response or {}).get("model") or request["model"])
            cost = (usage["input_tokens"] * p["input"] + usage["output_tokens"] * p["output"]
                    + (usage.get("cache_creation_input_tokens") or 0) * p["cache_write"]
                    + (usage.get("cache_read_input_tokens") or 0) * p["cache_read"]) / 1e6
        else:
            cost = self.worst(request)
        self.spent += cost
        row = {"call": self.calls, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model": request["model"],
               "usage": usage if ok else None, "cost_usd": round(cost, 6), "spent_usd": round(self.spent, 6),
               "cap_usd": self.cap, "measured": ok, "note": note}
        self.rows.append(row)
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        if not ok:
            raise StopRun("response without usage: charged at worst case, run stopped")
        return cost


# ---------------------------------------------------------------- answers


def answer_text(response: Mapping[str, Any]) -> str:
    if response.get("stop_reason") == "refusal":
        raise StopRun(f"model refused: {response.get('stop_details')}")
    if response.get("stop_reason") == "max_tokens":
        raise ValueError("answer cut at max_tokens")
    texts = [b.get("text", "") for b in response.get("content") or () if b.get("type") == "text"]
    if not texts:
        raise ValueError("no text block in the answer")
    return "".join(texts)


def validate_answer(answer: Any, source_id: str, cue_ids: set[str], quota: Mapping[str, int]) -> tuple[list[dict], list[dict]]:
    """Kept sentences and cue rejections, or ValueError when the answer as a whole cannot be used.

    A cited id that does not exist is never repaired: the sentence is dropped and the rejection recorded
    (README §8 1a.2: the harness refuses cues that do not exist). Then the counts must still hold.
    """
    if not isinstance(answer, Mapping) or not isinstance(answer.get("sentences"), list):
        raise ValueError("answer is not {sentences: [...]}")
    kept, rejected = [], []
    for n, s in enumerate(answer["sentences"]):
        if not isinstance(s, Mapping) or set(s) != {"text", "planned_type", "factual", "cues"}:
            raise ValueError(f"sentence {n}: wrong fields")
        text = s["text"].strip() if isinstance(s["text"], str) else ""
        if not text or len(text) > 220:
            raise ValueError(f"sentence {n}: empty or longer than 220 characters")
        if s["planned_type"] not in TYPES or not isinstance(s["factual"], bool) or not isinstance(s["cues"], list):
            raise ValueError(f"sentence {n}: bad type, factual or cues")
        missing = [c for c in s["cues"] if c not in cue_ids]
        if missing:
            rejected.append({"source_id": source_id, "sentence": n, "reason": "cue id does not exist", "cues": missing})
            continue
        if s["planned_type"] == "direct" and not s["cues"]:
            rejected.append({"source_id": source_id, "sentence": n, "reason": "direct sentence without a cue", "cues": []})
            continue
        kept.append({"text": text, "planned_type": s["planned_type"], "factual": s["factual"],
                     "cue_ids": list(dict.fromkeys(s["cues"]))})
    counts = {t: sum(1 for s in kept if s["planned_type"] == t) for t in TYPES}
    if counts != {t: quota[t] for t in TYPES}:
        raise ValueError(f"counts {counts} differ from the quota {dict((t, quota[t]) for t in TYPES)}")
    if sum(1 for s in kept if s["planned_type"] == "direct" and s["factual"]) < quota["min_factual"]:
        raise ValueError("too few factual direct sentences")
    return kept, rejected


# ---------------------------------------------------------------- run

Transport = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def run(manifest: Mapping[str, Any], split: str, base: str, out_dir: Path, transport: Transport, ledger: Ledger, *,
        quota: Mapping[str, Mapping[str, int]] = QUOTA, synthetic: bool, approval: str | None,
        model: str = MODEL) -> dict:
    """Draft every source in turn (never in parallel). Returns the draft document (also written to ``out_dir``)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs = load_inputs(manifest, split, base)
    sentences, rejections, notes = [], [], []
    for source, cues in inputs:
        sid = source["source_id"]
        if sid not in quota:
            raise StopRun(f"{sid}: no sentence quota")
        request = build_request(source, cues, quota[sid], model=model)
        api_scope.preflight(request, [source])  # raises ScopeError (BLOCKED) before any money is at stake
        ids = {c["id"] for c in cues}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            ledger.before(request)
            response = transport(request)
            ledger.after(request, response, note=f"{sid} attempt {attempt}")
            try:
                kept, rejected = validate_answer(json.loads(answer_text(response)), sid, ids, quota[sid])
            except (ValueError, json.JSONDecodeError) as error:
                notes.append(f"{sid} attempt {attempt}: invalid answer: {error}")
                continue
            rejections.extend(rejected)
            for s in kept:
                sentences.append(dict(s, sentence_id=f"{sid}-{len(sentences) + 1:03d}", source_ids=[sid],
                                      text_sha256=hashlib.sha256(s["text"].encode("utf-8")).hexdigest(),
                                      hard_negative=False, ai_generated=True, synthetic=synthetic))
            break
        else:
            raise StopRun(f"{sid}: no valid answer in {MAX_ATTEMPTS} attempts ({'; '.join(notes[-MAX_ATTEMPTS:])})")
    draft = {"schema": "dubflow-x2-narration-draft-v1", "split": split, "status": "DRAFT_NOT_LOCKED",
             "method": "ai-draft-with-cues", "writer": f"ai:{model}", "writer_saw_footage": False,
             "synthetic": synthetic, "approval": approval, "narration_language": NARRATION_LANGUAGE,
             "model": model, "effort": EFFORT, "max_tokens": MAX_TOKENS,
             "sources": [{"source_id": s["source_id"], "sha256": s["sha256"], "cues_sha256": s["cues_sha256"]} for s, _ in inputs],
             "sentences": sentences, "cue_rejections": rejections, "notes": notes,
             "cost": {"spent_usd": round(ledger.spent, 6), "calls": ledger.calls, "cap_usd": ledger.cap}}
    (out_dir / "narration-draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    return draft


def mock_transport(usage: Mapping[str, int] | None = None, answers: Sequence[Any] | None = None) -> Transport:
    """A $0 stand-in for the API: a valid answer built from the request's own cues (or the given answers in turn).

    Its sentences are placeholders, flagged synthetic by ``run``; they can never pass the eval gate."""
    queue = list(answers or ())

    def send(request: Mapping[str, Any]) -> Mapping[str, Any]:
        ask = json.loads(request["messages"][0]["content"][0]["text"])
        if queue:
            body = queue.pop(0)
        else:
            ids = [c["id"] for c in ask["cues"]]
            items, k = [], 0
            for kind in TYPES:
                for j in range(ask["write"][kind]):
                    items.append({"text": f"[mock {kind} {j}]", "planned_type": kind,
                                  "factual": kind == "direct" and j < ask["min_factual_direct"],
                                  "cues": [ids[k % len(ids)]] if ids else []})
                    k += 1
            body = {"sentences": items}
        text = body if isinstance(body, str) else json.dumps(body)
        return {"model": request["model"], "stop_reason": "end_turn", "content": [{"type": "text", "text": text}],
                "usage": dict(usage) if usage is not None else {"input_tokens": estimate_input_tokens(request),
                                                                 "output_tokens": 3_000}}
    return send


def anthropic_transport() -> Transport:  # pragma: no cover - real API, only for an approved 1a run
    """The real call: official SDK, no automatic retries (each retry would be an uncounted paid call)."""
    import anthropic  # imported lazily: tests and CI never need it
    client = anthropic.Anthropic(max_retries=0)

    def send(request: Mapping[str, Any]) -> Mapping[str, Any]:
        return client.messages.create(**request).model_dump()
    return send


def estimate(manifest: Mapping[str, Any], split: str, base: str, quota=QUOTA) -> dict:
    rows = []
    prices = PRICES[MODEL]
    for source, cues in load_inputs(manifest, split, base):
        request = build_request(source, cues, quota[source["source_id"]])
        api_scope.preflight(request, [source])
        typical_in, worst_in = estimate_input_tokens(request), worst_input_tokens(request)
        rows.append({"source_id": source["source_id"], "cues": len(cues), "request_bytes": worst_in - 1_000,
                     "typical_usd": round((typical_in * prices["input"] + 4_000 * prices["output"]) / 1e6, 4),
                     "worst_usd": round((worst_in * prices["input"] + MAX_TOKENS * prices["output"]) / 1e6, 4)})
    return {"model": MODEL, "effort": EFFORT, "max_tokens": MAX_TOKENS, "requests": rows,
            "typical_usd": round(sum(r["typical_usd"] for r in rows), 4),
            "worst_one_attempt_usd": round(sum(r["worst_usd"] for r in rows), 4),
            "worst_all_attempts_usd": round(MAX_ATTEMPTS * sum(r["worst_usd"] for r in rows), 4)}


if __name__ == "__main__":
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    mode, manifest_path = a[0], a[1]
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    split, base = opt("--split", "REAL-DEV"), opt("--base")
    if mode == "estimate":
        print(json.dumps(estimate(data, split, base), indent=2))
        sys.exit(0)
    out = Path(opt("--out"))
    if mode == "dry-run":
        ledger = Ledger(cap_usd=float(opt("--cap-usd", "3.0")), max_calls=int(opt("--max-calls", "4")), path=out / "ledger.mock.jsonl")
        out.mkdir(parents=True, exist_ok=True)
        draft = run(data, split, base, out, mock_transport(), ledger, synthetic=True, approval=None)
    elif mode == "live":
        approval, cap, calls = opt("--approval"), opt("--cap-usd"), opt("--max-calls")
        if not (approval and cap and calls):
            sys.exit("live needs --approval, --cap-usd and --max-calls (Owner 1a cost approval)")
        out.mkdir(parents=True, exist_ok=True)
        ledger = Ledger(cap_usd=float(cap), max_calls=int(calls), path=out / "ledger.jsonl")
        draft = run(data, split, base, out, anthropic_transport(), ledger, synthetic=False, approval=approval)
    else:
        sys.exit(f"unknown mode {mode!r}")
    print(json.dumps({"sentences": len(draft["sentences"]), "cue_rejections": len(draft["cue_rejections"]),
                      **draft["cost"]}, indent=2))
