"""Offline qualification of upstream SDK paging using a recorded extractor.

Run with an explicitly selected Python using -I -S -B. This is separate from
deterministic fixture tests and does not access a provider or qualify a release.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk-archive", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    helper_path = root / "engine/dubflow/download/authenticated_native.py"
    descriptor_path = root / "engine/dubflow/download/assets/yt-dlp-sdk-v1.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    # The reviewed wheel is code. Reject a substituted archive before import.
    expected = "1d57897e94c6665a0a6f9bc54b34e584284e32c034ffab3a7df25d8f7b24eedf"
    if descriptor.get("sha256") != expected or descriptor.get("version") != "2026.08.19":
        raise ValueError("reviewed descriptor changed; qualify the new producer explicitly")
    archive = args.sdk_archive.resolve(strict=True)
    if not 0 < archive.stat().st_size <= 16 * 1024 * 1024:
        raise ValueError("SDK archive exceeds qualification bound")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError("SDK archive differs from reviewed qualification pin")
    if not sys.flags.isolated or not sys.flags.no_site:
        raise ValueError("qualification requires isolated no-site Python")
    helper_bytes = helper_path.read_bytes()
    helper_hash = hashlib.sha256(helper_bytes).hexdigest()
    sys.path.insert(0, str(archive))
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.common import InfoExtractor
    from yt_dlp.globals import plugin_dirs
    from yt_dlp.utils import OnDemandPagedList
    from yt_dlp.version import __version__
    plugin_dirs.value = []
    spec = importlib.util.spec_from_file_location("reviewed_source_helper", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    pages, cases = [], []
    population, poison = 10002, False

    class RecordedSpaceIE(InfoExtractor):
        _VALID_URL = r"https://space\.bilibili\.com/(?P<id>[0-9]+)/video"

        def _real_extract(self, url):
            def page(index):
                pages.append(index)
                for n in range(index * 30, min((index + 1) * 30, population)):
                    if poison and n == 1:
                        yield None  # Preserve a deleted item's absolute slot.
                    else:
                        yield self.url_result(f"https://www.bilibili.com/video/BV{n:010d}",
                            ie="BiliBili", video_id=f"BV{n:010d}", video_title=f"Video {n}")
            return self.playlist_result(OnDemandPagedList(page, 30), self._match_id(url), "recorded creator")

    def downloader(options):
        actual = YoutubeDL(options, auto_init=False)
        actual.add_info_extractor(RecordedSpaceIE())
        return actual

    started = time.monotonic()
    for offset, size, population, expected_pages, expected_more, poison in (
            (0, 2, 10002, [0], True, False), (31, 2, 10002, [1], True, False),
            (900, 2, 10002, [30], True, False), (92, 2, 94, [3], False, False),
            (9998, 100, 10002, [333], True, False), (0, 2, 10002, [0], True, True)):
        pages.clear()
        result = helper.enumerate_page({"provider_id": "bilibili", "url": "https://space.bilibili.com/123/video",
                                      "offset": offset, "page_size": size}, downloader)
        count = min(size, 10000 - offset, population - offset)
        expected_ids = [None if poison and n == 1 else f"BV{n:010d}" for n in range(offset, offset + count)]
        observed_ids = [item["id"] if item is not None else None for item in result["entries"]]
        if observed_ids != expected_ids or result["has_more"] is not expected_more or pages != expected_pages:
            raise ValueError("actual SDK selected wrong IDs, page callbacks or completion")
        cases.append({"offset": offset, "requested_page_size": size, "returned": len(result["entries"]),
                      "has_more": result["has_more"], "sdk_page_callbacks": list(pages),
                      "deleted_slot": poison, "selected_ids": observed_ids})
    if helper_bytes != helper_path.read_bytes():
        raise ValueError("helper changed during qualification")
    report = {"schema_version": 1, "status": "passed",
              "scope": "actual pinned SDK with recorded offline paged extractor",
              "helper_sha256": helper_hash, "sdk_sha256": expected, "sdk_version": __version__,
              "qualification_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "descriptor_sha256": hashlib.sha256(descriptor_path.read_bytes()).hexdigest(),
              "python": sys.executable, "isolated": True, "no_site": True,
              "elapsed_seconds": time.monotonic() - started, "cases": cases,
              "live_bilibili": "NOT_RUN", "live_douyin": "NOT_RUN",
              "durable_desktop_scan": "NOT_RUN", "production_qualified": False}
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "cases": len(cases), "report": str(args.report),
                      "sha256": hashlib.sha256(args.report.read_bytes()).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
