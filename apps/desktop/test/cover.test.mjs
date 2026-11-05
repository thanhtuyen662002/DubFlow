import assert from "node:assert/strict";
import { createEdit, remove, split } from "../src/features/edit/model.ts";
import {
  activeRegions, COVER_MODES, coverDocument, coverSummary, DEFAULT_COVER_STYLE, describeCoverStyle, groupAuto, LEGACY_COVER_STYLE, loadCover,
  mosaicCells, nextMoment, normalizeCoverStyle, parseCoverStyle, rectBetween, regionOn, sameCover, sameCoverStyle, storedCoverStyle,
  stretchDraws, uncoveredMoments, plainCover,
} from "../src/features/edit/cover.ts";
import { cueOutputTime } from "../src/features/edit/subtitles.ts";
import { startJobRequest } from "../src/features/translation_review/flow.ts";

let ids = 0;
const makeId = () => `m${++ids}`;

// New videos stretch the edge above the caption; the setup choices read as the owner named them.
assert.deepEqual(DEFAULT_COVER_STYLE, { mode: "stretch", direction: "top", strip_px: 4, color: "#000000", block: 24, feather: 0 });
assert.deepEqual(COVER_MODES.map((mode) => mode.label), ["Kéo dãn mép (mặc định)", "Làm mờ", "Màu đặc", "Khảm"]);
assert.deepEqual(COVER_MODES.map((mode) => mode.short), ["Kéo mép", "Làm mờ", "Màu", "Khảm"]);
assert.equal(describeCoverStyle(DEFAULT_COVER_STYLE), "kéo dãn mép");

// Styles are parsed strictly like the engine's; missing options take their defaults.
assert.deepEqual(parseCoverStyle({ mode: "mosaic", block: 32 }), { ...DEFAULT_COVER_STYLE, mode: "mosaic", block: 32 });
assert.deepEqual(parseCoverStyle({ mode: "color", color: "#a1b2c3" }).color, "#A1B2C3");
for (const bad of [null, [], {}, { mode: "smear" }, { mode: "stretch", direction: "left" }, { mode: "stretch", strip_px: 1 }, { mode: "stretch", strip_px: 4.5 },
  { mode: "mosaic", block: 65 }, { mode: "color", color: "red" }, { mode: "stretch", feather: 9 }, { mode: "blur", sigma: 4 }]) {
  assert.equal(parseCoverStyle(bad), null, JSON.stringify(bad));
}
// The remembered style (dubflow.coverStyle.v1) is the default for new videos; nothing or junk means edge stretch.
assert.deepEqual(storedCoverStyle(null), DEFAULT_COVER_STYLE);
assert.deepEqual(storedCoverStyle("{oops"), DEFAULT_COVER_STYLE);
assert.deepEqual(storedCoverStyle(JSON.stringify({ mode: "blur" })), LEGACY_COVER_STYLE);
assert.deepEqual(normalizeCoverStyle({ ...DEFAULT_COVER_STYLE, strip_px: 40, block: 3, feather: -1, color: "#abcdef" }),
  { ...DEFAULT_COVER_STYLE, strip_px: 12, block: 8, feather: 0, color: "#ABCDEF" });
assert.ok(sameCoverStyle({ ...DEFAULT_COVER_STYLE, color: "#000000" }, DEFAULT_COVER_STYLE));

// start_job carries the remembered style; the host writes it as cover.json for a new folder.
const job = { id: "j1", sourcePath: "D:/v.mp4", dubbing: { enabled: false, voiceId: null }, status: { state: "QUEUED" } };
assert.ok(!("coverStyle" in startJobRequest(job, null)));
assert.deepEqual(startJobRequest(job, null, false, null, DEFAULT_COVER_STYLE).coverStyle, DEFAULT_COVER_STYLE);

// A version without cover.json was rendered with the blur; cover.json gives the style, drawn and fixed regions and switched-off detections.
const legacy = loadCover(null, makeId);
assert.deepEqual([legacy.style, legacy.manual, legacy.fixed, [...legacy.disabled]], [LEGACY_COVER_STYLE, [], null, []]);
const loaded = loadCover({ style: { mode: "stretch", direction: "both" }, regions: [{ x: 0.1, y: 0.1, w: 0.2, h: 0.1, start_ms: 1000, end_ms: 2000 },
  { x: 0.5, y: 0.5, w: 0.9, h: 0.1 }, { x: 0.1, y: 0.1, w: 0.2, h: 0.1, start_ms: 5 }], fixed: { x: 0, y: 0.9, w: 1, h: 0.1 }, disabled_auto: [2, -1, 1.5, 0] }, makeId);
assert.equal(loaded.style.direction, "both");
assert.equal(loaded.manual.length, 1, "regions outside the frame or with half a time range are dropped");
assert.deepEqual([...loaded.disabled].sort(), [0, 2]);
assert.deepEqual(coverDocument(loaded), {
  style: { ...DEFAULT_COVER_STYLE, direction: "both" }, regions: [{ x: 0.1, y: 0.1, w: 0.2, h: 0.1, start_ms: 1000, end_ms: 2000 }],
  disabled_auto: [0, 2], fixed: { x: 0, y: 0.9, w: 1, h: 0.1 },
});

// What changed reads in the footer; the same cover is no change.
const copy = loadCover(coverDocument(loaded), makeId);
assert.ok(sameCover(loaded, copy), "region ids do not count");
assert.deepEqual(coverSummary(loaded, { ...copy, style: { ...copy.style, mode: "mosaic" } }), ["kiểu che mới"]);
assert.deepEqual(coverSummary(loaded, { ...copy, disabled: new Set([0]) }), ["vùng che mới"]);
assert.deepEqual(coverSummary(loaded, { ...copy, style: LEGACY_COVER_STYLE, fixed: null }), ["kiểu che mới", "vùng che mới"]);

// Detected captions group per caption line: the lower line at four widths, and a short upper line.
const detected = [
  ...Array.from({ length: 6 }, (_, index) => ({ x: 0.22 + (index % 3) * 0.01, y: 0.84, w: 0.56 - (index % 3) * 0.02, h: 0.085,
    start_ms: 4000 * index + 1000, end_ms: 4000 * index + 3000 })),
  { x: 0.3, y: 0.74, w: 0.4, h: 0.07, start_ms: 9000, end_ms: 11000 },
].map((region, index) => ({ ...region, index }));
const groups = groupAuto(detected);
assert.equal(groups.length, 2);
assert.deepEqual(groups[0].indices, [0, 1, 2, 3, 4, 5]);
assert.deepEqual(groups[0].rect, { x: 0.22, y: 0.84, w: 0.56, h: 0.085 }, "the union of the line's widths");
assert.deepEqual(groups[0].spans[0], [1000, 3000]);
assert.equal(groups[1].first, 9000);

// The render covers the job's regions, drawn ones, the fixed one and the detections left on.
const state = { style: DEFAULT_COVER_STYLE, manual: [{ id: "a", x: 0.1, y: 0.1, w: 0.2, h: 0.1, start_ms: 0, end_ms: 500 }], fixed: null, disabled: new Set([1, 6]) };
const active = activeRegions(state, detected, [{ x: 0, y: 0, w: 0.1, h: 0.1 }]);
assert.equal(active.length, 1 + 1 + 5);
assert.ok(regionOn(active[1], 499) && !regionOn(active[1], 500) && regionOn(active[0], 10_000_000));

// "Còn sót chữ?": switched-off detections nothing else covers, and the next one after the playhead (wrapping).
assert.deepEqual(uncoveredMoments(state, detected), [5000, 9000]);
assert.deepEqual(uncoveredMoments({ ...state, fixed: { x: 0.2, y: 0.83, w: 0.6, h: 0.1 } }, detected), [9000], "a fixed band covers the lower line");
assert.deepEqual(uncoveredMoments({ ...state, manual: [{ id: "b", x: 0.25, y: 0.7, w: 0.5, h: 0.15, start_ms: 8000, end_ms: 12000 }] }, detected), [5000]);
assert.deepEqual(uncoveredMoments({ ...state, manual: [{ id: "c", x: 0.25, y: 0.7, w: 0.5, h: 0.15, start_ms: 9500, end_ms: 12000 }] }, detected), [5000, 9000],
  "a region on for only part of the time leaves the caption showing");
assert.equal(nextMoment([5000, 9000], 0), 5000);
assert.equal(nextMoment([5000, 9000], 5000), 9000);
assert.equal(nextMoment([5000, 9000], 9500), 5000);
assert.equal(nextMoment([], 0), null);

// A region's first appearance maps through cuts to the edited video.
const edit = remove(split(createEdit(60), 10), "s1"); // (cut 0–10), s2 10–60
assert.equal(cueOutputTime(edit, { start_ms: 12_000, end_ms: 13_000 }), 2);
assert.equal(cueOutputTime(edit, { start_ms: 4000, end_ms: 5000 }), null);

// Dragging out a rectangle in any direction stays inside the frame.
assert.deepEqual(rectBetween({ x: 0.9, y: 0.95 }, { x: 0.6, y: 1.4 }), { x: 0.6, y: 0.95, w: 0.3, h: 0.05 });

// The preview draws the strips exactly where the engine crops them.
assert.deepEqual(stretchDraws({ x: 0.1, y: 0.8, w: 0.8, h: 0.1 }, DEFAULT_COVER_STYLE, 1920, 1080),
  [{ sx: 192, sy: 860, sw: 1536, sh: 4, dx: 192, dy: 864, dw: 1536, dh: 108 }]);
assert.equal(stretchDraws({ x: 0.1, y: 0, w: 0.8, h: 0.1 }, DEFAULT_COVER_STYLE, 1920, 1080)[0].sy, 108, "no room above: the strip below");
assert.equal(stretchDraws({ x: 0.1, y: 0.9, w: 0.8, h: 0.1 }, { direction: "bottom", strip_px: 4 }, 1920, 1080)[0].sy, 968, "no room below: the strip above");
const both = stretchDraws({ x: 0.1, y: 0.8, w: 0.8, h: 0.1 }, { direction: "both", strip_px: 6 }, 1280, 720);
assert.deepEqual(both.map((part) => [part.sy, part.sh, part.dy, part.dh]), [[572, 4, 576, 36], [648, 4, 612, 36]]);
assert.equal(stretchDraws({ x: 0.1, y: 0.8, w: 0.8, h: 0.1 }, DEFAULT_COVER_STYLE, 480, 270)[0].sh, 2, "at least two rows");
// Mosaic blocks: the same rows at any resolution, square across.
assert.deepEqual(mosaicCells({ x: 0.1, y: 0.8, w: 0.8, h: 0.1 }, 16, 1920, 1080), { rows: 7, columns: 100 });
assert.deepEqual(mosaicCells({ x: 0.1, y: 0.8, w: 0.8, h: 0.1 }, 16, 640, 360), { rows: 7, columns: 100 });

console.log("cover tests passed");

// Issue #510: a video exported from Chỉnh sửa keeps its cover with the draft; a fresh one starts empty in the last style.
{
  const saved = { style: { ...DEFAULT_COVER_STYLE, mode: "color" }, regions: [{ x: 0.1, y: 0.8, w: 0.5, h: 0.1 }], disabled_auto: [], fixed: null };
  assert.equal(plainCover({ schema_version: 4, cover: saved }, null), saved);
  const fresh = plainCover({ schema_version: 4 }, { ...DEFAULT_COVER_STYLE, mode: "mosaic" });
  assert.equal(fresh.style.mode, "mosaic");
  assert.deepEqual(fresh.regions, []);
  assert.equal(plainCover(null, "rubbish").style.mode, "stretch");
  const state = loadCover(plainCover(null, null), makeId);
  assert.equal(state.style.mode, "stretch");
  assert.equal(state.manual.length, 0);
}
