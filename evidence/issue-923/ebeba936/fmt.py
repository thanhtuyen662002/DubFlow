# usage: python3 -I fmt.py <outdir> <sha> <base_url>  -> markdown evidence sections on stdout
import json, sys
out, sha, base = sys.argv[1], sys.argv[2], sys.argv[3]
r = json.load(open(f"{out}/result.json"))
rows = []
for key, n in (("t45", "45"), ("t107", "107")):
    for lay in ("left-column", "band", "big-bottom", "two-lines"):
        cell = r[f"{key}-{lay}"]
        for fmt, ext in (("png", "png"), ("jpeg", "jpg")):
            c = cell[fmt]; e = c["edgeBright"]
            edges = f"{e['left']} / {e['right']} / {e['top']} / {e['bottom']}"
            if key == "t107" and lay == "big-bottom": edges += " **expected exception**"
            elif any(e.values()): edges += " **UNEXPECTED**"
            match = "identical (0 px differ)" if c["pixelsDifferingFromWindow"] == 0 else f"JPEG loss: mean {c['meanAbsDiff']}, max {c['maxDiff']}"
            if fmt == "png" and c["pixelsDifferingFromWindow"]: match += " **UNEXPECTED**"
            rows.append(f"| {n} | {lay} | [{fmt.upper()}]({base}/{key}-{lay}.{ext}) | {c['width']}×{c['height']} | {c['bytes']:,} | `{c['sha256']}` | {edges} | {match} |")
print("| Letters | Layout | Saved file | Size | Bytes | SHA-256 | Bright edge px L / R / T / B | Saved vs window canvas |")
print("|---|---|---|---|---|---|---|---|")
print("\n".join(rows))
print()
print("| Edge | Ink after the push (x, y) | Drag back (thumbnail px) | Ink after | Moved by |")
print("|---|---|---|---|---|")
for name, d in r["drag"].items():
    p, a = d["pushed"], d["after"]
    print(f"| {name} | x {p['left']}–{p['right']}, y {p['top']}–{p['bottom']} | {d['back']} | x {a['left']}–{a['right']}, y {a['top']}–{a['bottom']} | {d['movedBy']} |")
print()
s = r["scenarios"]
A, B, C, D, Dp, E = s["A_twoStrokes120WithWobble4px"], s["B_outAndBack300OneStroke"], s["C_twoStrokes200EndingTwoPxHigher"], s["D_sideHeldPushAndJitter"], s["D_pulledAway40px"], s["E_stretchUpBack"]
print("| Real-mouse scenario | Result |")
print("|---|---|")
print(f"| A. big-bottom 45: 120 px right, release, 120 px back, each stroke with a 4 screen px up/down wobble (> DRAG_START_PX) | middle ink y {A['middleInk']['top']}–{A['middleInk']['bottom']} (re-wrapped and lifted from {A['startInk']['top']}–{A['startInk']['bottom']}); end canvas hash {'equals' if A['same'] else '**differs from**'} start (`{A['start']}`) |")
print(f"| B. big-bottom 45: one stroke 300 px right and straight back, 4 px wobble | end canvas hash {'equals' if B['same'] else '**differs from**'} start (`{B['start']}`) |")
print(f"| C. big-bottom 45: 200 px right ending 2 screen px higher (< DRAG_START_PX), release, back ending 2 px higher | end canvas hash {'equals' if C['same'] else '**differs from**'} start |")
print(f"| D. left-column 107 (held at the left edge): 100 screen px into the edge, 30 px up/down with 2 px sideways jitter, back | ink x {D['startAcross'][0]}–{D['startAcross'][1]} at every one of {len(D['seen'])} steps: {D['sameAcrossEveryStep']}; end hash {'equals' if D['same'] else '**differs from**'} start |")
print(f"| D2. then pulled 40 screen px away from the edge | ink x {Dp['before'][0]}–{Dp['before'][1]} → {Dp['after'][0]}–{Dp['after'][1]} (leaves the edge, re-wrapped wider to its new room) |")
print(f"| E. big-bottom 45: 200 px right (stretched), 20 px up, back down, back across, one stroke | up moved {E['upMovedPx']} px (pointer 20); back down equals stretched: {E['backDownSameAsStretched']}; back across equals start: {E['backAcrossSameAsStart']} |")
F = s["F_sideHeldRoomDependent"]
print(f"| F. 'NGHIÊNG AB CD' held at the left edge where its wrap depends on its room (x {F['targetX']:.4f}): 4 strokes of 40 screen px down/up each ending 2 px toward the edge, then 5 × ArrowLeft | ink x {F['placed'][0]}–{F['placed'][1]}, height {F['placed'][2]} px before; after each stroke {[h for _, _, h in F['afterStrokes']]}, after the keys {F['afterKeys'][2]} (same x every time: {F['sameLinesThroughout']}); on the previous head 8e53eda the same run re-wraps to one more line (height 312) from the second stroke |")
print()
print(f"Browser: {r['browser']} (Playwright, headless), canvas stage {r['stageWidthPx']} screen px wide, source `{sha}`.")
