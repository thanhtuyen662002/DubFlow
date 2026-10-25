"""One JSON response per updater command, for the desktop supervisor."""

from . import UpdateError, _app_root_last

_app_root_last()

import argparse
import json
from pathlib import Path
from typing import Sequence

from .client import check, download


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "download"):
        command = commands.add_parser(name)
        command.add_argument("--current", required=True)
        command.add_argument("--channel", choices=("stable", "candidate"), required=True)
        if name == "download":
            command.add_argument("--dest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = check(args.current, args.channel)
        if args.command == "download" and result["available"]:
            path = download(result["setup"], args.dest)
            result = {"path": str(path), "version": result["version"]}
        response = {"ok": True, **result}
    except UpdateError as exc:
        response = {"ok": False, "code": exc.code, "message": exc.message}
    except OSError:
        response = {"ok": False, "code": "IO_ERROR", "message": "unable to write update files"}
    except Exception:
        response = {"ok": False, "code": "INTERNAL", "message": "unexpected updater failure"}
    print(json.dumps(response, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
