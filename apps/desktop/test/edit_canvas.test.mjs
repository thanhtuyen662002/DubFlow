import assert from "node:assert/strict";
import {
  ASPECT_PRESETS, aspectRatio, canvasSize, createEdit, exportPlan, isUnchanged, matchesExport, normalizeCanvas, parseAspect, parseProject,
  rangeEdit, serializeProject, setCanvas, split,
} from "../src/features/edit/model.ts";

// Tỉ lệ khung hình (Issue #626): presets, custom shapes, what the export takes.
assert.deepEqual(ASPECT_PRESETS.map((preset) => preset.aspect), ["16:9", "9:16", "1:1", "4:5", "4:3", "3:4", "21:9"]);
for (const preset of ASPECT_PRESETS) assert.equal(parseAspect(preset.aspect), preset.aspect, "every preset is a valid shape");
assert.equal(parseAspect("16x9"), "16:9");
assert.equal(parseAspect(" 32 : 18 "), "16:9", "reduced");
assert.equal(parseAspect("5/4"), "5:4");
assert.equal(parseAspect("7:3"), "21:9", "named as the preset");
assert.equal(parseAspect("1920:1080"), "16:9", "pixel sizes reduce too");
for (const bad of ["", "16", "0:9", "9:0", "1:5", "5:1", "101:1", "16:9:1", "a:b", "-1:2", "1.5:1", null, 16]) {
  assert.equal(parseAspect(bad), null, String(bad));
}
assert.equal(parseAspect("101:100"), null, "101:100 cannot be reduced under 100");
assert.equal(aspectRatio("9:16"), 9 / 16);
assert.equal(aspectRatio(""), 1);

// The export frame keeps the short side, as the host's aspect_size does.
assert.deepEqual(canvasSize(1920, 1080, { aspect: "9:16", fill: "blur" }), { width: 1080, height: 1920 });
assert.deepEqual(canvasSize(1080, 1920, { aspect: "16:9", fill: "blur" }), { width: 1920, height: 1080 });
assert.deepEqual(canvasSize(1280, 720, { aspect: "1:1", fill: "black" }), { width: 720, height: 720 });
assert.deepEqual(canvasSize(1280, 720, { aspect: "4:5", fill: "fill" }), { width: 720, height: 900 });
assert.deepEqual(canvasSize(1920, 1080, { aspect: "4:3", fill: "blur" }), { width: 1440, height: 1080 });
assert.deepEqual(canvasSize(1920, 1080, { aspect: "21:9", fill: "blur" }), { width: 2520, height: 1080 });
assert.deepEqual(canvasSize(3840, 2160, { aspect: "21:9", fill: "blur" }), { width: 4096, height: 1756 }, "the long side stops at 4096");
assert.deepEqual(canvasSize(1280, 720, { aspect: "5:4", fill: "blur" }), { width: 900, height: 720 });
assert.deepEqual(canvasSize(1280, 720, null), { width: 1280, height: 720 });

// The edit keeps it; null goes back to the source's own frame.
const base = createEdit(100, { path: "D:\\a.mp4", width: 1920, height: 1080 });
assert.equal(base.canvas, undefined);
const tall = setCanvas(base, { aspect: "9:16", fill: "blur" });
assert.deepEqual(tall.canvas, { aspect: "9:16", fill: "blur" });
assert.ok(!isUnchanged(tall, 100), "a new frame is a change to export");
assert.equal(setCanvas(tall, { aspect: "18:32", fill: "blur" }), tall, "the same shape is no change");
assert.deepEqual(setCanvas(tall, { aspect: "9:16", fill: "fill" }).canvas, { aspect: "9:16", fill: "fill" });
assert.equal(setCanvas(tall, { aspect: "bad", fill: "blur" }).canvas, undefined, "an invalid shape clears it");
const back = setCanvas(tall, null);
assert.ok(!("canvas" in back));
assert.ok(isUnchanged(back, 100));
assert.equal(setCanvas(base, null), base);
assert.deepEqual(normalizeCanvas({ aspect: "1:1", fill: "nope" }), { aspect: "1:1", fill: "blur" });
assert.equal(normalizeCanvas({ aspect: "1:9" }), null);

// Other edits keep the frame.
assert.deepEqual(split(tall, 40).canvas, tall.canvas);
assert.deepEqual(rangeEdit(tall, 10, 20).canvas, tall.canvas);

// The export plan carries aspect and fill only when set.
const plan = exportPlan(tall);
assert.equal(plan.aspect, "9:16");
assert.equal(plan.fill, "blur");
assert.ok(!("aspect" in exportPlan(base)) && !("fill" in exportPlan(base)));
const exported = JSON.parse(JSON.stringify({ ...plan, main_volume: plan.mainVolume }));
assert.ok(matchesExport(tall, exported));
assert.ok(!matchesExport(base, exported), "a frame change is unexported");
assert.ok(!matchesExport(setCanvas(tall, { aspect: "9:16", fill: "black" }), exported), "a fill change is unexported");
const plainExport = JSON.parse(JSON.stringify({ ...exportPlan(base), main_volume: 1 }));
assert.ok(matchesExport(base, plainExport));
assert.ok(!matchesExport(tall, plainExport));

// Saved with the draft and read back; an old draft has none.
const saved = JSON.parse(JSON.stringify(serializeProject(setCanvas(base, { aspect: "21:9", fill: "black" }), 1, null)));
assert.deepEqual(saved.canvas, { aspect: "21:9", fill: "black" });
assert.deepEqual(parseProject(saved, 100).edit.canvas, { aspect: "21:9", fill: "black" });
assert.equal(parseProject({ ...saved, canvas: { aspect: "0:0", fill: "blur" } }, 100).edit.canvas, undefined);
const plain = JSON.parse(JSON.stringify(serializeProject(base, 0, null)));
assert.ok(!("canvas" in plain));
assert.equal(parseProject(plain, 100).edit.canvas, undefined);

console.log("edit_canvas tests passed");
