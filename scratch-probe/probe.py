"""Live probe: unsigned SDK Douyin request vs. signed detail request.
Usage: <bundle>\\runtime\\python.exe probe.py <bundle root = ...\\versions\\X> <data root> <douyin video url>
Never prints cookie values."""
import importlib.util, sys, traceback
from pathlib import Path

bundle, data, url = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
here = Path(__file__).resolve().parent
sys.path.insert(0, str(bundle / "app"))
sdk = next((bundle / "runtime" / "source").glob("yt_dlp-*.whl"))
sys.path.insert(0, str(sdk))
import yt_dlp
from engine.dubflow.download.authenticated_native import scoped_cookies, downloader_options
from engine.dubflow.download.douyin import request_signature as signature


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

native = load("probe_native", here / "creator_native.py")
print("yt-dlp", yt_dlp.version.__version__)
headers = None
try:
    from engine.dubflow.download.sessions import ProtectedSessionBridge
    headers = ProtectedSessionBridge(data / "control" / "source-sessions").get_opaque_headers("douyin")
    names = [part.split("=", 1)[0] for part in headers["Cookie"].split("; ")]
    print("saved session:", len(names), "cookies; logged in:", "sessionid" in names, "; has msToken:", "msToken" in names)
except Exception as error:
    print("saved session unavailable:", type(error).__name__)

for label, use in (("with saved session", headers), ("visitor (no cookies)", None)):
    if label.startswith("with") and not use:
        continue
    print("\n==", label)
    with yt_dlp.YoutubeDL(downloader_options()) as d:
        for cookie in (scoped_cookies("douyin", use) if use else []):
            d.cookiejar.set_cookie(cookie)
        try:
            info = d.extract_info(url, download=False)
            print("UNSIGNED SDK: ok", info.get("id"), len(info.get("formats", [])), "formats")
        except Exception as error:
            print("UNSIGNED SDK: FAILED:", str(error)[:200])
        try:
            vid = native.aweme_id_from_url(url, d)
            detail = native.fetch_detail(d, vid, signer=signature.ABogus)
            ie = d.get_info_extractor("Douyin")
            info = ie._parse_aweme_video_app(detail)
            info.setdefault("webpage_url", f"https://www.douyin.com/video/{vid}")
            d.add_default_extra_info(info, ie, info["webpage_url"])
            info = d.process_ie_result(info, download=False)
            formats = info.get("formats", [])
            print("SIGNED DETAIL: ok", info.get("id"), repr(info.get("title"))[:60], len(formats), "formats")
            import urllib.request
            best = [f for f in formats if f.get("vcodec") not in (None, "none")][-1]
            req = urllib.request.Request(best["url"], headers={"User-Agent": native.USER_AGENT, "Referer": "https://www.douyin.com/", "Range": "bytes=0-65535"})
            with urllib.request.urlopen(req, timeout=30) as r:
                print("CDN media:", r.status, r.headers.get("Content-Type"), len(r.read()), "bytes")
        except Exception as error:
            print("SIGNED DETAIL: FAILED:", type(error).__name__, getattr(error, "native_exit_code", ""), str(error)[:200])
