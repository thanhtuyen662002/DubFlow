"""One request/action per host invocation; exactly one JSON line on stdout."""

import argparse
import json
import sys
from typing import TextIO

from .bilibili import Bilibili
from .douyin import Douyin
from .http import DiscoveryError, HttpClient
from .kuaishou import Kuaishou


def _positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv: list[str] | None = None, *, client: HttpClient | None = None,
         stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=("douyin", "bilibili", "kuaishou"))
    parser.add_argument("action", choices=("trends", "suggest", "popular", "ranking", "search", "creator"))
    parser.add_argument("--query")
    parser.add_argument("--page", type=_positive, default=1)
    parser.add_argument("--mid")
    args = parser.parse_args(argv)
    if args.action in ("suggest", "search") and not (args.query and args.query.strip()):
        parser.error("--query is required for suggest/search")
    if args.action == "creator" and not args.mid:
        parser.error("--mid is required for creator")
    if args.platform == "bilibili" and args.action == "creator":
        if not args.mid.isascii() or not args.mid.isdigit() or int(args.mid) < 1:
            parser.error("--mid must be a positive numeric Bilibili UID")
    adapter = {"douyin": Douyin, "bilibili": Bilibili, "kuaishou": Kuaishou}[args.platform](client)
    try:
        if args.action == "ranking":
            if not isinstance(adapter, Bilibili):
                raise DiscoveryError("UNSUPPORTED", "Ranking is only available for Bilibili.")
            page = adapter.ranking()
        elif args.action == "trends":
            page = adapter.trends()
        elif args.action == "suggest":
            page = adapter.suggest(args.query)
        elif args.action == "popular":
            page = adapter.popular(args.page)
        elif args.action == "search":
            page = adapter.search(args.query, args.page)
        else:
            page = adapter.creator(args.mid, args.page)
        result = {"ok": True, **page.to_dict()}
    except DiscoveryError as error:
        result = {"ok": False, "code": error.code, "message": error.message}
    except Exception as error:  # noqa: BLE001 - the host must always receive one JSON line.
        result = {"ok": False, "code": "BAD_RESPONSE", "message": f"{type(error).__name__}: {str(error)[:200]}"}
    # ASCII escapes also work under the Windows host's legacy stdout encoding.
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")), file=stdout or sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
