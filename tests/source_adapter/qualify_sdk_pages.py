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
from unittest.mock import patch


def bounded_bytes(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if not data or len(data) > maximum:
        raise ValueError("qualification input exceeds its bound")
    return data


def qualify_sdk_pages(archive: Path, helper_path: Path, descriptor_path: Path,
                      *, helper_sha256: str, descriptor_sha256: str) -> dict:
    # Even a future SDK change must not turn this required recorded lane into
    # a live-site check. Restore the process guard on both success and failure.
    def forbidden(*args, **kwargs):
        raise RuntimeError("network forbidden during recorded SDK qualification")
    with patch("socket.socket.connect", forbidden), patch("socket.socket.connect_ex", forbidden), \
            patch("socket.getaddrinfo", forbidden), patch("socket.create_connection", forbidden):
        return _qualify(archive, helper_path, descriptor_path,
                        helper_sha256=helper_sha256, descriptor_sha256=descriptor_sha256)


def _qualify(archive: Path, helper_path: Path, descriptor_path: Path,
             *, helper_sha256: str, descriptor_sha256: str) -> dict:
    descriptor_bytes = bounded_bytes(descriptor_path, 16 * 1024)
    if hashlib.sha256(descriptor_bytes).hexdigest() != descriptor_sha256:
        raise ValueError("descriptor differs from verified bundle inventory")
    descriptor = json.loads(descriptor_bytes)
    # The reviewed wheel is code. Reject a substituted archive before import.
    expected = "1d57897e94c6665a0a6f9bc54b34e584284e32c034ffab3a7df25d8f7b24eedf"
    if descriptor.get("sha256") != expected or descriptor.get("version") != "2026.08.19":
        raise ValueError("reviewed descriptor changed; qualify the new producer explicitly")
    helper_bytes = bounded_bytes(helper_path, 1024 * 1024)
    helper_hash = hashlib.sha256(helper_bytes).hexdigest()
    if helper_hash != helper_sha256:
        raise ValueError("helper differs from verified bundle inventory")
    archive = archive.resolve(strict=True)
    if not 0 < archive.stat().st_size <= 16 * 1024 * 1024:
        raise ValueError("SDK archive exceeds qualification bound")
    if hashlib.sha256(bounded_bytes(archive, 16 * 1024 * 1024)).hexdigest() != expected:
        raise ValueError("SDK archive differs from reviewed qualification pin")
    if not sys.flags.isolated or not sys.flags.no_site:
        raise ValueError("qualification requires isolated no-site Python")
    if any(name == "yt_dlp" or name.startswith("yt_dlp.") for name in sys.modules):
        raise ValueError("qualification cannot reuse an already imported SDK")
    sys.path.insert(0, str(archive))
    import yt_dlp
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.common import InfoExtractor
    from yt_dlp.globals import plugin_dirs
    from yt_dlp.utils import OnDemandPagedList
    from yt_dlp.version import __version__
    if __version__ != descriptor["version"] or Path(yt_dlp.__file__).parents[1].resolve() != archive:
        raise ValueError("imported SDK version differs from reviewed descriptor")
    plugin_dirs.value = []
    spec = importlib.util.spec_from_file_location("reviewed_source_helper", helper_path)
    helper = importlib.util.module_from_spec(spec)
    # Execute the exact bytes just verified rather than reopening a mutable
    # helper path in the import loader.
    exec(compile(helper_bytes, str(helper_path), "exec"), helper.__dict__)
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

    # Exercise the same real SDK's flat URL entries and full video resolution.
    # The recorded extractors never contact a site or call another downloader.
    generic_pages, generic_cases, resolved_videos = [], [], []
    missing_identity = False

    class RecordedPlaylistIE(InfoExtractor):
        _VALID_URL = r"https://video\.example\.test/list/(?P<id>[0-9]+)"

        def _real_extract(self, url):
            def page(index):
                generic_pages.append(index)
                for n in range(index * 30, min((index + 1) * 30, population)):
                    if poison and n == 1:
                        yield None
                    else:
                        yield self.url_result(f"https://video.example.test/watch/{n}",
                            ie="RecordedVideo", video_id=None if missing_identity else str(n),
                            video_title=None if missing_identity else f"Video {n}")
            return self.playlist_result(OnDemandPagedList(page, 30), self._match_id(url), "recorded playlist")

    class RecordedVideoIE(InfoExtractor):
        _VALID_URL = r"https://video\.example\.test/watch/(?P<id>[0-9]+)"

        def _real_extract(self, url):
            identifier = self._match_id(url)
            resolved_videos.append(identifier)
            return {"id": identifier, "title": "Recorded public video", "description": "First line\nSecond line",
                    "duration": 1.25, "formats": [{"format_id": "av", "url": "https://cdn.example.test/file.mp4",
                        "vcodec": "h264", "acodec": "aac", "ext": "mp4", "protocol": "https"}],
                    "subtitles": {"en": [{"url": "https://cdn.example.test/sub.vtt", "ext": "vtt"}]}}

    def generic_downloader(options):
        actual = YoutubeDL(options, auto_init=False)
        actual.add_info_extractor(RecordedPlaylistIE())
        actual.add_info_extractor(RecordedVideoIE())
        if (options["cachedir"] is not False or options["js_runtimes"] != {}
                or options["remote_components"] or list(actual.cookiejar)):
            raise ValueError("generic SDK acquired an unapproved runtime/session capability")
        return actual

    for offset, size, population, expected_pages, expected_more, poison in (
            (0, 2, 10002, [0], True, False), (31, 2, 10002, [1], True, False),
            (900, 2, 10002, [30], True, False), (92, 2, 94, [3], False, False),
            (9998, 100, 10002, [333], True, False), (0, 2, 10002, [0], True, True)):
        generic_pages.clear()
        result = helper.enumerate_page({"provider_id": "generic", "url": "https://video.example.test/list/123",
                                       "offset": offset, "page_size": size}, generic_downloader)
        count = min(size, 10000 - offset, population - offset)
        expected_ids = [None if poison and n == 1 else str(n) for n in range(offset, offset + count)]
        observed_ids = [item["id"] if item is not None else None for item in result["entries"]]
        if (observed_ids != expected_ids or result["has_more"] is not expected_more
                or generic_pages != expected_pages or resolved_videos):
            raise ValueError("actual generic SDK selected wrong IDs/page callbacks or eagerly resolved videos")
        if any(item is not None and (item.get("ie_key") != "RecordedVideo"
                or item.get("url") != f"https://video.example.test/watch/{item['id']}") for item in result["entries"]):
            raise ValueError("actual SDK did not preserve generic flat entry identity/URL")
        generic_cases.append({"offset": offset, "requested_page_size": size, "returned": count,
                              "has_more": result["has_more"], "sdk_page_callbacks": list(generic_pages),
                              "selected_ids": observed_ids, "deleted_slot": poison, "eager_video_resolutions": 0})
    video = helper.inspect({"provider_id": "generic", "url": "https://video.example.test/watch/31"}, generic_downloader)
    if (video["id"] != "31" or video["extractor_key"] != "RecordedVideo"
            or video["webpage_url"] != "https://video.example.test/watch/31" or resolved_videos != ["31"]
            or len(video["formats"]) != 1 or video["subtitles"]["en"][0]["ext"] != "vtt"):
        raise ValueError("actual generic SDK full video resolution did not preserve identity/media/subtitle candidates")
    try:
        helper.provider_request({"provider_id": "generic", "url": "https://video.example.test/watch/31",
                                 "headers": {"Cookie": "synthetic-secret"}})
    except ValueError:
        pass
    else:
        raise ValueError("generic helper accepted a provider session")
    from engine.dubflow.download.generic.sdk import ENUMERATION_RECIPE, GenericSdkTransport, sdk_identity
    class RecordedBoundary:
        pins = {"python": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                "helper": helper_hash, "sdk_archive": expected}
        def enumerate_url(self, url, *, provider_id, offset, page_size):
            return helper.enumerate_page({"provider_id":provider_id,"url":url,"offset":offset,"page_size":page_size},generic_downloader)
        def inspect_url(self, url, *, provider_id):
            return helper.inspect({"provider_id":provider_id,"url":url},generic_downloader)
    missing_identity, population, poison = True, 10002, False
    generic_pages.clear()
    resolved_videos.clear()
    transport = GenericSdkTransport(RecordedBoundary())
    first = transport.enumerate_playlist("https://video.example.test/list/123",cursor=None,page_size=2)
    if resolved_videos != ["0","1"] or generic_pages != [0] or first.completed:
        raise ValueError("missing-ID generic page did not resolve only selected slots")
    second = transport.enumerate_playlist("https://video.example.test/list/123",cursor=first.next_cursor,page_size=2)
    selected = [*first.items,*second.items]
    if (resolved_videos != ["0","1","2","3"] or generic_pages != [0,0] or first.failures or second.failures
            or any(item.media_candidates for item in selected)
            or [item.identity for item in selected] != [sdk_identity({"id":str(n),"ie_key":"RecordedVideo",
                "url":f"https://video.example.test/watch/{n}"}) for n in range(4)]):
        raise ValueError("missing-ID generic page lost stable identity, bounded resolution or cursor progression")
    missing_report = {"status":"passed","page_count":2,"selected_ids":["0","1","2","3"],
        "resolved_videos":list(resolved_videos),"lookahead_resolved":False,
        "source_identity_preserved":True,"mapping_recipe":ENUMERATION_RECIPE}
    if helper_bytes != bounded_bytes(helper_path, 1024 * 1024):
        raise ValueError("helper changed during qualification")
    if descriptor_bytes != bounded_bytes(descriptor_path, 16 * 1024):
        raise ValueError("descriptor changed during qualification")
    report = {"schema_version": 1, "status": "passed",
              "scope": "actual pinned SDK with recorded offline paged extractor",
              "helper_sha256": helper_hash, "sdk_sha256": expected, "sdk_version": __version__,
              "qualification_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "descriptor_sha256": descriptor_sha256,
              "helper_path": str(helper_path.resolve()), "descriptor_path": str(descriptor_path.resolve()),
              "sdk_archive": str(archive), "network": "forbidden",
              "sdk_import_origin": yt_dlp.__file__,
              "python": sys.executable, "isolated": True, "no_site": True,
              "elapsed_seconds": time.monotonic() - started, "cases": cases,
              "generic_playlist_cases": generic_cases,
              "generic_inspection": {"status": "passed", "extractor_key": video["extractor_key"],
                                     "source_id": video["id"], "resolved_videos": list(resolved_videos),
                                     "provider_session_refusal": "passed"},
              "generic_missing_identity": missing_report,
              "live_bilibili": "NOT_RUN", "live_douyin": "NOT_RUN",
              "durable_desktop_scan": "NOT_RUN", "production_qualified": False}
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk-archive", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    helper = root / "engine/dubflow/download/authenticated_native.py"
    descriptor = root / "engine/dubflow/download/assets/yt-dlp-sdk-v1.json"
    report = qualify_sdk_pages(args.sdk_archive, helper, descriptor,
        helper_sha256=hashlib.sha256(bounded_bytes(helper, 1024 * 1024)).hexdigest(),
        descriptor_sha256=hashlib.sha256(bounded_bytes(descriptor, 16 * 1024)).hexdigest())
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "cases": len(report["cases"]), "generic_cases": len(report["generic_playlist_cases"]), "report": str(args.report),
                      "sha256": hashlib.sha256(args.report.read_bytes()).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
