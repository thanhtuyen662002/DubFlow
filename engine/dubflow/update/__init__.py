"""Signed release discovery and verified installer downloads."""


def _app_root_last() -> None:
    """Keep repo packaging/ behind installed third-party packaging imports."""

    from pathlib import Path
    import sys

    root = Path(__file__).resolve().parents[3]
    sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != root]
    sys.path.append(str(root))


class UpdateError(RuntimeError):
    """A bounded, machine-readable updater failure."""

    def __init__(self, code: str, message: str = "download failed integrity verification") -> None:
        self.code = code
        self.message = message[:4096]
        super().__init__(f"{code}: {self.message}")


__all__ = ["UpdateError"]
