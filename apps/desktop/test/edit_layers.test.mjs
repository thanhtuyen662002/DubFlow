import assert from "node:assert/strict";
import {
  addOverlay, createEdit, exportPlan, matchesExport, moveOverlayLayer, overlayLanes, overlayStyle, parseProject, serializeProject, setLook, setOverlayStyle,
  setOverlayTime, split as splitAt,
} from "../src/features/edit/model.ts";
import { isLutPath, lookBadges, lutName, normalizeLook } from "../src/features/edit/look.ts";

const info = { path: "D:\\a.mp4", width: 1920, height: 1080 };
const base = createEdit(100, info);
const roundTrip = (edit) => parseProject(JSON.parse(JSON.stringify(serializeProject(edit, 1, null))), 100, info).edit;
const still = (name) => ({ path: `D:\\${name}.png`, title: name, duration: 3600, width: 512, height: 512, fps: 30, hasAudio: false, kind: "file" });

// ---------- Overlay rows and layers ----------
let edit = addOverlay(addOverlay(addOverlay(base, still("a"), 0), still("b"), 0), still("c"), 0);
const [a, b, c] = edit.overlays.map((overlay) => overlay.id);
edit = setOverlayTime(setOverlayTime(setOverlayTime(edit, a, { end: 10 }), b, { at: 5, end: 10 }), c, { at: 20, end: 5 });
const lanes = overlayLanes(edit);
assert.deepEqual([lanes.get(a), lanes.get(b), lanes.get(c)], [1, 0, 0], "overlapping overlays take another row, the top layer highest");
assert.deepEqual([...overlayLanes(moveOverlayLayer(edit, a, Infinity)).values()].sort(), [0, 0, 1]);
assert.equal(overlayLanes(moveOverlayLayer(edit, a, Infinity)).get(a), 0, "brought to the top, it moves up a row");
const upped = moveOverlayLayer(edit, a, 1);
assert.deepEqual(upped.overlays.map((overlay) => overlay.id), [b, a, c], "one layer up");
assert.deepEqual(moveOverlayLayer(edit, a, Infinity).overlays.map((overlay) => overlay.id), [b, c, a], "to the top");
assert.equal(moveOverlayLayer(edit, c, 1), edit, "already on top");
assert.equal(moveOverlayLayer(edit, "o999", 1), edit);
assert.equal(matchesExport(upped, exportPlan(edit)), false, "the order is part of the export");
assert.deepEqual(roundTrip(upped).overlays.map((overlay) => overlay.id), [b, a, c], "kept in the draft");

// ---------- Hình tròn ----------
const round = setOverlayStyle(edit, a, { shape: "ellipse", radius: 0.3, border: 6 });
assert.equal(round.overlays[0].shape, "ellipse");
assert.equal("radius" in round.overlays[0], false, "an ellipse needs no corner radius");
assert.deepEqual(overlayStyle(round.overlays[0]).shape, "ellipse");
assert.equal(exportPlan(round).overlays[0].shape, "ellipse");
assert.equal(roundTrip(round).overlays[0].shape, "ellipse");
assert.equal(setOverlayStyle(round, a, { shape: "star" }), round, "unknown shapes are ignored");
assert.equal("shape" in setOverlayStyle(round, a, { shape: "rect" }).overlays[0], false);
assert.equal(matchesExport(round, exportPlan(edit)), false);
assert.equal(matchesExport(round, exportPlan(round)), true);

// ---------- LUT màu ----------
assert.ok(isLutPath("D:\\LUT\\Teal.cube") && isLutPath("/home/a/b.CUBE") && isLutPath("\\\\nas\\luts\\x.cube"));
assert.ok(!isLutPath("Teal.cube") && !isLutPath("D:\\LUT\\Teal.png") && !isLutPath(3));
assert.equal(lutName("D:\\LUT\\Teal Orange.cube"), "Teal Orange");
assert.equal(normalizeLook({ lut: "relative.cube" }), null, "only absolute .cube paths");
const split = splitAt(base, 30);
const [first, second] = split.segments;
let graded = setLook(split, first.id, { lut: "D:\\LUT\\Teal.cube" });
graded = setLook(graded, second.id, { lut: "D:\\LUT\\Teal.cube" });
assert.deepEqual(lookBadges(graded.segments[0].look), ["LUT"]);
const plan = exportPlan(graded);
assert.deepEqual(plan.luts, ["D:\\LUT\\Teal.cube"], "each file once");
assert.deepEqual(plan.segments.map((segment) => segment.lut), [0, 0]);
assert.equal("lut" in exportPlan(split).segments[0], false);
assert.deepEqual(exportPlan(split).luts, []);
assert.equal(matchesExport(graded, { ...plan, main_volume: plan.mainVolume }), true);
assert.equal(matchesExport(graded, { ...plan, main_volume: plan.mainVolume, luts: ["D:\\LUT\\Other.cube"] }), false, "a different file");
assert.equal(matchesExport(graded, { ...exportPlan(split), main_volume: 1 }), false, "no LUT before");
assert.equal(roundTrip(graded).segments[0].look.lut, "D:\\LUT\\Teal.cube");
assert.equal(setLook(graded, first.id, { lut: null }).segments[0].look, undefined, "removing the LUT leaves the plain look");

console.log("edit_layers: ok");
