"""``python -m engine.dubflow.ai`` answers one AI request for the desktop app.

stdin carries one JSON object; the key comes in the ``DUBFLOW_AI_KEY``
environment variable, so it never appears on a command line. stdout gets one
JSON line:

* ``{"action": "ask", "provider", "model"?, "base_url"?, "system", "schema", "parts", "max_tokens"?}``
  -> ``{"ok": true, "value": {...}, "model": "used-model"}``
* ``{"action": "models", "provider", "base_url"?}``
  -> ``{"ok": true, "models": [...], "suggested": "model-or-null"}``
* failures -> ``{"ok": false, "code": "KEY|BALANCE|MODEL|LIMIT|SERVER|TIMEOUT|NETWORK|REFUSED|FORMAT|INVALID", "message": "..."}``
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, TextIO

from .client import AiError, Transport, _urllib, ask, list_models, make_route, pick_model

MAX_REQUEST_BYTES = 32 * 1024 * 1024


def _parts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > 64:
        raise AiError("INVALID", "Yêu cầu AI không hợp lệ.")
    parts = []
    for part in value:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            parts.append({"text": part["text"]})
        elif isinstance(part, dict) and isinstance(part.get("image"), dict) and part["image"].get("mediaType") in {"image/jpeg", "image/png"} \
                and isinstance(part["image"].get("data"), str):
            parts.append({"image": {"mediaType": part["image"]["mediaType"], "data": part["image"]["data"]}})
        else:
            raise AiError("INVALID", "Yêu cầu AI không hợp lệ.")
    return parts


def handle(request: Any, key: str, transport: Transport = _urllib) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise AiError("INVALID", "Yêu cầu AI không hợp lệ.")
    route = make_route(str(request.get("provider", "")), key, model=request.get("model") or None, base_url=request.get("base_url") or None)
    if request.get("action") == "models":
        names = list_models(route, transport=transport)
        return {"ok": True, "models": names, "suggested": route.model if route.model in names else pick_model(route.provider, names)}
    if request.get("action") != "ask" or not isinstance(request.get("system"), str) or not isinstance(request.get("schema"), dict):
        raise AiError("INVALID", "Yêu cầu AI không hợp lệ.")
    max_tokens = request.get("max_tokens", 16000)
    if type(max_tokens) is not int or not 256 <= max_tokens <= 64000:
        raise AiError("INVALID", "Yêu cầu AI không hợp lệ.")
    value, used = ask(route, request["system"], request["schema"], _parts(request.get("parts")), transport=transport, max_tokens=max_tokens)
    return {"ok": True, "value": value, "model": used.model}


def main(stdin: TextIO | None = None, stdout: TextIO | None = None, transport: Transport = _urllib) -> int:
    key = os.environ.pop("DUBFLOW_AI_KEY", "")
    try:
        text = (stdin or sys.stdin).read(MAX_REQUEST_BYTES + 1)
        if len(text) > MAX_REQUEST_BYTES:
            raise AiError("INVALID", "Yêu cầu AI quá lớn.")
        result = handle(json.loads(text), key, transport)
    except AiError as error:
        result = {"ok": False, "code": error.code, "message": error.message}
    except ValueError:
        result = {"ok": False, "code": "INVALID", "message": "Yêu cầu AI không hợp lệ."}
    except Exception as error:  # noqa: BLE001 - the host always needs one JSON line.
        message = f"{type(error).__name__}: {str(error)[:200]}"
        result = {"ok": False, "code": "FAILED", "message": message.replace(key, "…") if key else message}
    print(json.dumps(result, ensure_ascii=True), file=stdout or sys.stdout)
    return 0


if __name__ == "__main__":
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    raise SystemExit(main())
