import assert from "node:assert/strict";
import {
  createEdit, exportPlan, freezeFrame, isUnchanged, matchesExport, outputDuration, outputToSource, parseProject, remapCues, serializeProject,
  setLook, setSpeed, split, sourceToOutput, copySegment, pasteSegment, trim, MAX_SPEED, MIN_SPEED,
} from "../src/features/edit/model.ts";
import { colorPresetOf, COLOR_PRESETS, cropForRatio, cropRatioOf, cssFilter, dragCrop, normalizeLook, planLook } from "../src/features/edit/look.ts";

const close = (actual, expected, message, tolerance = 1e-6) => assert.ok(Math.abs(actual - expected) < tolerance, `${message ?? ""} ${actual} != ${expected}`);
const base = createEdit(100, { path: "D:\\a.mp4", width: 1920, height: 1080 });

// Wider speed range.
assert.equal(MIN_SPEED, 0.25);
assert.equal(MAX_SPEED, 4);
close(outputDuration(setSpeed(base, "s1", 4)), 25);
close(outputDuration(setSpeed(base, "s1", 0.1)), 400, "clamped to 0.25");

// A neutral look is no look; values are clamped.
assert.equal(setLook(base, "s1", { rotate: 0, color: { brightness: 0, contrast: 1, saturation: 1, temperature: 0 } }), base);
const styled = setLook(base, "s1", { rotate: 90, flipH: true, color: { brightness: 2, contrast: 1.2, saturation: 1, temperature: 0 } });
assert.deepEqual(styled.segments[0].look.color, { brightness: 0.5, contrast: 1.2, saturation: 1, temperature: 0 });
assert.ok(!isUnchanged(styled, 100));
assert.equal(setLook(styled, "s1", { rotate: 0, flipH: false, color: null }).segments[0].look, undefined, "back to plain");
assert.equal(normalizeLook({ crop: { x: 0, y: 0, w: 1, h: 1 } }), null);
assert.deepEqual(normalizeLook({ crop: { x: 0.9, y: 0, w: 0.5, h: 0.5 } }).crop, { x: 0.5, y: 0, w: 0.5, h: 0.5 }, "kept inside the picture");

// Plan: only what changes something, snake case; matchesExport sees look changes.
const plan = exportPlan(styled);
assert.deepEqual(plan.segments[0], { media: 0, start: 0, end: 100, speed: 1, volume: 1, rotate: 90, flip_h: true, color: { brightness: 0.5, contrast: 1.2, saturation: 1, temperature: 0 } });
const exported = JSON.parse(JSON.stringify(plan));
assert.ok(matchesExport(styled, exported));
assert.ok(!matchesExport(setLook(styled, "s1", { flipV: true }), exported));
assert.ok(!matchesExport(base, exported));
assert.ok(matchesExport(base, JSON.parse(JSON.stringify(exportPlan(base)))), "plain plans have no look keys");

// Reverse: the output runs from the end; split keeps the later half first; long parts refused.
const short = split(split(base, 20), 50); // s1 0-20, s2 20-50, s3 50-100
const reversed = setLook(short, "s2", { reverse: true });
close(outputToSource(reversed, 21).sourceTime, 49);
close(sourceToOutput(reversed, 49, 1), 21);
const halves = split(reversed, 30);
assert.deepEqual(halves.segments.slice(1, 3).map((s) => [s.start, s.end, s.look.reverse]), [[40, 50, true], [20, 40, true]]);
assert.equal(setLook(createEdit(300), "s1", { reverse: true }).segments[0].look, undefined, "over two minutes");
assert.equal(remapCues([{ start_ms: 25000, end_ms: 27000, text: "x" }], reversed).length, 0, "no subtitles on reversed parts");

// Fades stay on the outer ends of a split.
const faded = setLook(base, "s1", { fadeIn: 1, fadeOut: 2 });
const cut = split(faded, 40);
assert.deepEqual(cut.segments.map((s) => [s.look.fadeIn, s.look.fadeOut]), [[1, 0], [0, 2]]);

// Freeze: holds a frame for its own length, no sound; splits into two holds; cannot be trimmed.
const frozen = freezeFrame(base, 30, 3);
assert.equal(frozen.segments.length, 3);
const hold = frozen.segments[1];
assert.equal(hold.look.freeze, 3);
assert.equal(hold.volume, 0);
close(hold.start, 30);
close(outputDuration(frozen), 103);
close(outputToSource(frozen, 31.5).sourceTime, 30, "held frame");
assert.equal(trim(frozen, hold.id, "end", 60), frozen);
const twoHolds = split(frozen, 31);
assert.deepEqual(twoHolds.segments.slice(1, 3).map((s) => s.look.freeze), [1, 2]);
assert.equal(split(frozen, 30.1), frozen, "too close to the edge");
assert.equal(remapCues([{ start_ms: 29000, end_ms: 31000, text: "x" }], frozen).length, 2, "the cue on both sides of the hold, not on it");

// Draft round trip and copy/paste carry the look.
const back = parseProject(serializeProject(frozen, 1, null), 100, { path: "D:\\a.mp4" });
assert.deepEqual(back.edit.segments, frozen.segments);
const clip = copySegment(styled, "s1");
const pasted = pasteSegment(base, 0, clip);
assert.equal(pasted.segments[0].look.rotate, 90);

// Helpers.
assert.equal(colorPresetOf(COLOR_PRESETS[2].color), "warm");
assert.equal(colorPresetOf(null), "none");
assert.equal(cssFilter(null), "");
assert.equal(cssFilter({ brightness: 0, contrast: 1.2, saturation: 0, temperature: 0 }), "contrast(1.2) saturate(0)");
const portrait = cropForRatio(1920, 1080, 9 / 16);
close(portrait.w, (9 / 16) / (16 / 9), "9:16 from 16:9", 1e-3);
assert.equal(portrait.h, 1);
assert.equal(cropRatioOf(portrait, 1920, 1080), "9:16");
assert.deepEqual(planLook(null), {});
// Cắt khung dragged on the preview: sides and corners move only their edges, the body keeps its size, never below 10 %.
assert.deepEqual(dragCrop(null, "w", 0.2, 0), { x: 0.2, y: 0, w: 0.8, h: 1 });
assert.deepEqual(dragCrop(null, "se", -0.25, -0.5), { x: 0, y: 0, w: 0.75, h: 0.5 });
assert.deepEqual(dragCrop({ x: 0.1, y: 0.1, w: 0.5, h: 0.5 }, "move", 0.9, -0.3), { x: 0.5, y: 0, w: 0.5, h: 0.5 });
assert.deepEqual(dragCrop({ x: 0.1, y: 0.1, w: 0.5, h: 0.5 }, "n", 0, 0.9), { x: 0.1, y: 0.5, w: 0.5, h: 0.1 });
assert.equal(dragCrop({ x: 0.2, y: 0, w: 0.8, h: 1 }, "w", -0.5, 0), null, "back to the whole picture is no crop");
console.log("edit look tests passed");
