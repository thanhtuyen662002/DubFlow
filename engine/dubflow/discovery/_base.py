"""Shared response validation and unsupported capability behavior."""

from .http import DiscoveryError, HttpClient


def object_at(payload: dict, *path: str) -> dict:
    value = payload
    for key in path:
        value = value.get(key)
        if not isinstance(value, dict):
            raise DiscoveryError("BAD_RESPONSE", "Missing or malformed discovery response object.")
    return value


def rows_at(payload: dict, *path: str) -> list[dict]:
    parent = object_at(payload, *path[:-1])
    rows = parent.get(path[-1])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise DiscoveryError("BAD_RESPONSE", "Missing or malformed discovery item list.")
    return rows


def require_success(payload: dict, key: str = "code", allowed: tuple = (0,)) -> None:
    if isinstance(payload.get(key), bool) or payload.get(key) not in allowed:
        code = "LOGIN_REQUIRED" if payload.get(key) == -101 else "BAD_RESPONSE"
        raise DiscoveryError(code, "Discovery endpoint did not report success.")


def text_at(row: dict, key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DiscoveryError("BAD_RESPONSE", f"Missing or malformed discovery field: {key}.")
    return value


class DiscoveryAdapter:
    platform: str

    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client if client is not None else HttpClient()

    def _unsupported(self):
        raise DiscoveryError("UNSUPPORTED", f"{self.platform} does not support this discovery action; use its browser.")

    def popular(self, page: int = 1):
        return self._unsupported()

    def search(self, query: str, page: int = 1):
        return self._unsupported()

    def creator(self, mid: str, page: int = 1):
        return self._unsupported()

    def suggest(self, query: str):
        return self._unsupported()
