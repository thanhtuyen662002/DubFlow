# Independent check of the saved files outside Chromium: ffprobe size/codec, Pillow decode, PNG vs JPEG of the same
# composition, and bright pixels in the outer 2 px (R+G+B > 600), as in the in-browser count.
import json, subprocess, sys, hashlib
from PIL import Image
out = sys.argv[1]
rows = []
for key in ("t45", "t107"):
    for lay in ("left-column", "band", "big-bottom", "two-lines"):
        png = Image.open(f"{out}/{key}-{lay}.png").convert("RGB"); jpg = Image.open(f"{out}/{key}-{lay}.jpg").convert("RGB")
        probe = {}
        for ext in ("png", "jpg"):
            j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height", "-of", "json", f"{out}/{key}-{lay}.{ext}"], capture_output=True, text=True).stdout)["streams"][0]
            probe[ext] = f"{j['codec_name']} {j['width']}x{j['height']}"
        def edges(im):
            w, h = im.size; px = im.load(); b = lambda x, y: 1 if sum(px[x, y]) > 600 else 0
            return [sum(b(x, y) for y in range(h) for x in (0, 1)), sum(b(w - 1 - x, y) for y in range(h) for x in (0, 1)), sum(b(x, y) for x in range(w) for y in (0, 1)), sum(b(x, h - 1 - y) for x in range(w) for y in (0, 1))]
        a, c = png.tobytes(), jpg.tobytes()
        mean = sum(abs(a[i] - c[i]) for i in range(0, len(a), 7)) / (len(a) / 7)
        rows.append({"case": f"{key}-{lay}", "ffprobe": probe, "pngEdges": edges(png), "jpgEdges": edges(jpg), "jpgVsPngMeanAbs": round(mean, 2)})
json.dump(rows, open(f"{out}/verify_pillow_ffprobe.json", "w"), indent=1)
for r in rows: print(r["case"], r["ffprobe"], r["pngEdges"], r["jpgEdges"], r["jpgVsPngMeanAbs"])
