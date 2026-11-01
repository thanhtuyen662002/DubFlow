import assert from "node:assert/strict";
import { createEdit, remove, setSpeed, split } from "../src/features/edit/model.ts";
import {
  applyPreset, changeSummary, cueAt, cueOutputTime, DEFAULT_STYLE, dragPosition, normalizeStyle, parseStyle, presetOf, PRESETS,
  previewMetrics, regionOn, rgba, sameStyle, storedStyle, SUBTITLE_FONTS, subtitleChanges, withoutPosition,
} from "../src/features/edit/subtitles.ts";
import { startJobRequest } from "../src/features/translation_review/flow.ts";

const close = (actual, expected, message, tolerance = 1e-6) => assert.ok(Math.abs(actual - expected) < tolerance, `${message ?? ""} ${actual} != ${expected}`);

// Twelve Vietnamese-capable Windows fonts, the same list the worker accepts.
assert.equal(SUBTITLE_FONTS.length, 12);
for (const missing of ["Georgia", "Trebuchet MS", "Arial Black", "Impact", "Bahnschrift"]) assert.ok(!SUBTITLE_FONTS.includes(missing), missing);

// Styles are parsed strictly; colours are stored upper-case; position goes in pairs.
const yellow = { ...DEFAULT_STYLE, primary: "#fde047", bold: true };
assert.deepEqual(parseStyle(yellow), { ...yellow, primary: "#FDE047" });
assert.deepEqual(parseStyle({ ...DEFAULT_STYLE, x: 0.5, y: 0.2 }), { ...DEFAULT_STYLE, x: 0.5, y: 0.2 });
for (const bad of [null, [], { ...DEFAULT_STYLE, font: "Impact" }, { ...DEFAULT_STYLE, size: 2.5 }, { ...DEFAULT_STYLE, primary: "white" },
  { ...DEFAULT_STYLE, box: "#000" }, { ...DEFAULT_STYLE, outline_width: 9 }, { ...DEFAULT_STYLE, align: "left" }, { ...DEFAULT_STYLE, x: 0.5 },
  { ...DEFAULT_STYLE, bold: "yes" }, { ...DEFAULT_STYLE, extra: 1 }]) {
  assert.equal(parseStyle(bad), null, JSON.stringify(bad));
}
assert.equal(storedStyle(null), null);
assert.equal(storedStyle("{not json"), null);
assert.deepEqual(storedStyle(JSON.stringify(DEFAULT_STYLE)), DEFAULT_STYLE, "the last applied style is the default for new videos");

// Normalizing rounds to the control steps; equal looks compare equal.
const rough = normalizeStyle({ ...DEFAULT_STYLE, size: 1.234, outline_width: 2.3, box_opacity: 0.612, x: 0.123456, y: 0.9 });
assert.deepEqual([rough.size, rough.outline_width, rough.box_opacity, rough.x], [1.25, 2.5, 0.6, 0.1235]);
assert.ok(sameStyle({ ...DEFAULT_STYLE, primary: "#ffffff" }, DEFAULT_STYLE));
assert.ok(!sameStyle({ ...DEFAULT_STYLE, x: 0.5, y: 0.5 }, DEFAULT_STYLE));

// Presets change the look and keep the position.
assert.equal(PRESETS.length, 5);
assert.deepEqual(PRESETS.map((preset) => preset.label), ["Mặc định", "Hộp tối", "Vàng nổi", "Viền dày", "Tối giản"]);
const placed = { ...DEFAULT_STYLE, align: "top", x: 0.3, y: 0.2 };
const boxed = applyPreset(placed, "dark-box");
assert.equal(boxed.box, "#000000");
assert.deepEqual([boxed.align, boxed.x, boxed.y], ["top", 0.3, 0.2]);
assert.equal(presetOf(boxed), "dark-box");
assert.equal(presetOf(DEFAULT_STYLE), "default", "today's look is the Mặc định preset");
assert.equal(presetOf({ ...DEFAULT_STYLE, primary: "#123456" }), null);
assert.deepEqual(withoutPosition(placed), { ...DEFAULT_STYLE, align: "top" });

// The preview scales like libass: sizes with the picture height, side margins with its width.
const metrics = previewMetrics({ ...DEFAULT_STYLE, size: 1.5, box: "#000000" }, { width: 960, height: 540 });
close(metrics.fontPx, 72 * 0.5 * 0.895, "Arial em of a 72 px ASS size");
close(metrics.outlinePx, 1.5);
close(metrics.paddingPx, 14 * 0.5);
close(metrics.marginX, 20);
close(metrics.marginV, 24);
assert.equal(previewMetrics(DEFAULT_STYLE, { width: 960, height: 540 }).paddingPx, 0, "no box, no padding");
assert.equal(rgba("#FF8000", 0.5), "rgba(255, 128, 0, 0.5)");

// Dragging moves vertically; Shift frees the horizontal; edges snap to the safe margins and the centre line.
const picture = { width: 1000, height: 500 };
const block = { width: 400, height: 50 };
const down = dragPosition({ x: 0.5, y: 0.5 }, { dx: 120, dy: 50 }, block, picture, false);
assert.deepEqual([down.x, down.y], [0.5, 0.6]);
const free = dragPosition({ x: 0.5, y: 0.5 }, { dx: 120, dy: 0 }, block, picture, true);
assert.equal(free.x, 0.62);
const snapped = dragPosition({ x: 0.5, y: 0.5 }, { dx: 4, dy: 172 }, block, picture, true);
assert.deepEqual([snapped.x, snapped.y, snapped.guides], [0.5, 0.85, ["center", "bottom"]], "bottom edge on the 90% line");
const top = dragPosition({ x: 0.5, y: 0.5 }, { dx: 0, dy: -172 }, block, picture, false);
assert.deepEqual([top.y, top.guides], [0.15, ["center", "top"]]);
const clamped = dragPosition({ x: 0.5, y: 0.5 }, { dx: -2000, dy: 2000 }, block, picture, true);
assert.deepEqual([clamped.x, clamped.y], [0.2, 0.95], "the block stays inside the picture");

// Lines map through the edit: a cut line has no output time; later lines move up and follow speed.
const edit = setSpeed(remove(split(split(createEdit(100), 10), 20), "s2"), "s3", 2); // s1 0–10, (cut 10–20), s3 20–100 at 2×
assert.equal(cueOutputTime(edit, { start_ms: 12_000, end_ms: 15_000 }), null);
close(cueOutputTime(edit, { start_ms: 30_000, end_ms: 32_000 }), 10 + 10 / 2);
close(cueOutputTime(edit, { start_ms: 8_000, end_ms: 12_000 }), 8);
close(cueOutputTime(edit, { start_ms: 18_000, end_ms: 22_000 }), 10, "a line partly cut shows from where it is kept");
const lines = [{ start_ms: 0, end_ms: 1000 }, { start_ms: 2000, end_ms: 3000 }, { start_ms: 3000, end_ms: 4000 }];
assert.equal(cueAt(lines, 2.5), lines[1]);
assert.equal(cueAt(lines, 3), lines[2]);
assert.equal(cueAt(lines, 1.5), null);
assert.equal(cueAt([], 1), null);
assert.ok(regionOn({ x: 0 }, 5) && regionOn({ x: 0, start_ms: 0, end_ms: 10 }, 5) && !regionOn({ x: 0, start_ms: 0, end_ms: 10 }, 10));

// "Áp dụng": edited lines keep their earlier speech unless marked; marked lines speak their edited text.
const cue = (cue_id, machine_text, text = machine_text, voice_text = text) =>
  ({ cue_id, start_ms: 0, end_ms: 1000, source_text: "源", machine_text, text, voice_text, edited: text !== machine_text, dub_failed: false });
const cues = [
  cue("a", "Xin chào"),
  cue("b", "Cảm ơn"),
  cue("c", "Tạm biệt", "Hẹn gặp lại", "Tạm biệt"), // edited earlier, voice kept earlier
  cue("d", "Một"),
];
const drafts = { a: "  Chào   bạn ", b: "Cảm ơn nhé", d: "Một" };
const changes = subtitleChanges(cues, (id) => drafts[id] ?? cues.find((item) => item.cue_id === id).text, new Set(["b", "d"]));
assert.deepEqual(changes.overrides, { a: "Chào bạn", b: "Cảm ơn nhé", c: "Hẹn gặp lại" });
assert.deepEqual(changes.voice, { a: "Xin chào", c: "Tạm biệt" });
assert.deepEqual(changes.revoice, ["b", "d"], "a line can be voiced again with its text unchanged");
assert.deepEqual([changes.edited, changes.empty, changes.tooLong], [2, 0, 0]);
const emptied = subtitleChanges(cues, (id) => (id === "a" ? "   " : cues.find((item) => item.cue_id === id).text), new Set());
assert.equal(emptied.empty, 1);
assert.ok(!("a" in emptied.overrides));
const untouched = subtitleChanges(cues, (id) => cues.find((item) => item.cue_id === id).text, new Set());
assert.deepEqual([untouched.edited, untouched.revoice.length], [0, 0]);
assert.deepEqual(untouched.overrides, { c: "Hẹn gặp lại" }, "earlier edits stay in the whole set the engine reads");
assert.deepEqual(untouched.voice, { c: "Tạm biệt" });
assert.equal(changeSummary(2, 1, true), "2 câu đã sửa · 1 câu đọc lại giọng · kiểu chữ mới");
assert.equal(changeSummary(0, 0, false), "Chưa có thay đổi");
assert.equal(changeSummary(2, 1, true, true), "2 câu sửa · 1 đọc lại · kiểu mới");

// New jobs carry the remembered style only when there is one.
const job = { id: "j", sourcePath: "C:/a.mp4", dubbing: { enabled: false, voiceId: null } };
assert.ok(!("subtitleStyle" in startJobRequest(job, null)));
assert.deepEqual(startJobRequest(job, null, false, DEFAULT_STYLE).subtitleStyle, DEFAULT_STYLE);

console.log("subtitles tests passed");
