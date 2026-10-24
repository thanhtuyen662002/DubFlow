"""Child fixture stopped externally by the parent at real publication boundaries."""
from pathlib import Path
import json
import os
import sys
import threading
from unittest.mock import patch

from engine.dubflow.separation import CpuAttenuationBackend, load_current
from engine.dubflow.separation import publication


def run(source, output, marker, milestone):
    path_open, sync, replace = Path.open, os.fsync, os.replace
    descriptors = {}

    def gate(point):
        if point == milestone:
            Path(marker).write_text(point)
            threading.Event().wait(30)
            raise RuntimeError("parent failed to terminate child")

    def tracked_open(path, *args, **kwargs):
        stream = path_open(path, *args, **kwargs)
        descriptors[stream.fileno()] = path.name
        return stream

    def tracked_sync(fd):
        sync(fd)
        name = descriptors.get(fd, "")
        gate("flushed-pointer" if name.startswith(".current-audio.") else "flushed-" + name)

    def tracked_replace(source_path, target):
        stage = "current" if Path(target).name == publication.CURRENT else "seal"
        gate("before-" + stage)
        replace(source_path, target)
        gate("after-" + stage)

    with patch.object(Path, "open", tracked_open), patch("os.fsync", tracked_sync), patch("os.replace", tracked_replace):
        CpuAttenuationBackend().process(source, output)


if __name__ == "__main__":
    if sys.argv[1] == "recover":
        result = load_current(sys.argv[2])
        print(json.dumps(None if result is None else result.to_dict()))
    else:
        run(*sys.argv[2:])
