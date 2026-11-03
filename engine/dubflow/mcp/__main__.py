"""python -B -m engine.dubflow.mcp: DubFlow's MCP server on stdin/stdout."""
from __future__ import annotations

import io
import sys

from engine.dubflow.docu.mcp_tools import Install
from .server import Server, serve


def main() -> int:
    # Protocol messages are UTF-8 whatever the console code page is.
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
    stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n")
    serve(Server(Install.here()), stdin, stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
