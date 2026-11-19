"""A $0 stand-in for an OpenAI-compatible AI server on this computer (Issue #953).

DubFlow's "Tùy chỉnh (tương thích OpenAI)" provider may point at http://127.0.0.1 with no key (a local
LM Studio / Ollama). This server answers there with deterministic Vietnamese so the real app can be driven
end to end without a key, a paid call or any byte leaving the machine:

* the consent question before AI translation (Issue #934) can be answered both ways on a real install;
* the translation's AI pass and "Viết lại kiểu thuyết minh" send their real payload, which is logged to
  ``--log`` (one JSON object per request: path, model, system prompt, user text), so the glossary and
  context they carry can be checked on the wire.

Answers: a request whose text lists cue ids gets one line per id (``{"lines": [...], "ghi_chu": ""}``);
any other gets an empty brief. A line whose source holds a term from the request's "Thuật ngữ BẮT BUỘC"
list carries that term's Vietnamese, unless ``--drop-terms`` is given (then the source spelling is kept,
to exercise the app's own check).

    python tests/ai/mock_openai_server.py --port 8765 --log mock-ai.jsonl
    # Cài đặt › Tài khoản › Tùy chỉnh: Base URL http://127.0.0.1:8765/v1, model mock
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
from typing import Any

_ROW = re.compile(r'^\{.*"id":\s*"([^"]+)".*\}$', re.M)
_MUST = re.compile(r"Thuật ngữ BẮT BUỘC, luôn viết đúng như sau: (.+)")
_LOCK = threading.Lock()


def _terms(text: str) -> list[tuple[str, str]]:
    found = _MUST.search(text)
    if not found:
        return []
    pairs = []
    for item in found.group(1).split("; "):
        source, _, target = item.partition(" → ")
        if source and target:
            pairs.append((source.strip(), target.strip()))
    return pairs


def answer(system: str, text: str, *, drop_terms: bool = False) -> dict[str, Any]:
    """The JSON content the server returns for one request."""
    terms = _terms(text)
    rows = []
    for line in text.splitlines():
        match = _ROW.match(line.strip())
        if not match:
            continue
        try:
            row = json.loads(line.strip())
        except ValueError:
            continue
        source = str(row.get("goc", ""))
        words = [f"Câu thử {len(rows) + 1} đã dịch"]
        for term_source, target in terms:
            if term_source in source:
                words.append(term_source if drop_terms else target)
        rows.append({"id": match.group(1), "text": " ".join(words)})
    if rows:
        return {"lines": rows, "ghi_chu": ""}
    return {"tom_tat": "Tóm tắt thử nghiệm.", "the_loai": "thử nghiệm", "nhan_vat": [], "thuat_ngu": []}


class Handler(BaseHTTPRequestHandler):
    log_path: str = "mock-ai.jsonl"
    drop_terms = False

    def log_message(self, *_: Any) -> None:  # quiet console
        return

    def _reply(self, status: int, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        if self.path.rstrip("/").endswith("/models"):
            return self._reply(200, {"data": [{"id": "mock"}]})
        return self._reply(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            request = json.loads(self.rfile.read(min(length, 8 * 1024 * 1024)).decode("utf-8"))
        except ValueError:
            return self._reply(400, {"error": {"message": "bad json"}})
        messages = request.get("messages") or []
        system = next((str(m.get("content")) for m in messages if m.get("role") == "system"), "")
        user = "\n".join(str(m.get("content")) if isinstance(m.get("content"), str)
                         else "\n".join(str(part.get("text", "")) for part in m.get("content") or [] if isinstance(part, dict))
                         for m in messages if m.get("role") == "user")
        with _LOCK, open(self.log_path, "a", encoding="utf-8") as log:
            log.write(json.dumps({"path": self.path, "model": request.get("model"), "system": system, "user": user,
                                  "authorization": bool(self.headers.get("Authorization"))}, ensure_ascii=False) + "\n")
        content = json.dumps(answer(system, user, drop_terms=self.drop_terms), ensure_ascii=False)
        return self._reply(200, {"id": "mock", "object": "chat.completion", "model": request.get("model"),
                                 "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
                                 "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log", default="mock-ai.jsonl")
    parser.add_argument("--drop-terms", action="store_true")
    args = parser.parse_args()
    Handler.log_path, Handler.drop_terms = args.log, args.drop_terms
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)  # this computer only
    print(f"mock AI server on http://127.0.0.1:{args.port}/v1, log {args.log}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
