import assert from "node:assert/strict";
import {
  addAudio, addOverlay, addText, createEdit, exportPlan, matchesExport, moveOverlay, outputDuration, overlayAt, overlayPreset, overlayPresetOf,
  parseProject, placementOf, rangeEdit, resizeOverlay, serializeProject, setOverlayFades, setOverlayMotion, addMarker,
} from "../src/features/edit/model.ts";

const close = (actual, expected, message, tolerance = 1e-6) => assert.ok(Math.abs(actual - expected) < tolerance, `${message ?? ""} ${actual} != ${expected}`);
const info = { path: "D:\\a.mp4", width: 1920, height: 1080 };
const base = createEdit(100, info);
const roundTrip = (edit) => parseProject(JSON.parse(JSON.stringify(serializeProject(edit, 1, null))), 100, info).edit;
const logo = { path: "D:\\logo.png", title: "logo", duration: 3600, width: 512, height: 512, fps: 30, hasAudio: false, kind: "file" };

// ---------- Overlay fades ----------
const withLogo = addOverlay(base, logo, 10);
const id = withLogo.overlays[0].id;
const faded = setOverlayFades(withLogo, id, { fadeIn: 1.04, fadeOut: 9 });
assert.deepEqual([faded.overlays[0].fadeIn, faded.overlays[0].fadeOut], [1, 3]);
close(overlayAt(faded, faded.overlays[0], 0.5).alpha, 0.5, "half way into the fade in");
close(overlayAt(faded, faded.overlays[0], 50).alpha, 1);
close(overlayAt(faded, faded.overlays[0], 98.5).alpha, 0.5, "half way out");
assert.equal("fadeIn" in setOverlayFades(faded, id, { fadeIn: 0 }).overlays[0], false, "0 leaves the key out");
assert.deepEqual(pick(exportPlan(faded).overlays[0], "fade_in", "fade_out"), { fade_in: 1, fade_out: 3 });

// ---------- Chuyển động: an end placement, edited as its own key ----------
const moving = setOverlayMotion(withLogo, id, true);
assert.deepEqual(moving.overlays[0].to, placementOf(withLogo.overlays[0]), "starts where it is");
assert.equal("to" in exportPlan(moving).overlays[0], false, "no motion until the end moves");
const ended = resizeOverlay(overlayPreset(moving, id, "tl", "end"), id, 0.5, "end");
const overlay = ended.overlays[0];
assert.equal(overlayPresetOf(ended, overlay, "start"), "tr", "the start stays top-right");
assert.deepEqual(placementOf(overlay, "end"), { x: 0.03, y: 0.03, w: 0.5 });
const middle = overlayAt(ended, overlay, 50);
close(middle.w, (overlay.w + 0.5) / 2, "half way through it is half way in size");
close(middle.x, (overlay.x + 0.03) / 2);
assert.deepEqual(exportPlan(ended).overlays[0].to, { x: 0.03, y: 0.03, w: 0.5 });
assert.deepEqual(roundTrip(ended).overlays, ended.overlays);
const plan = exportPlan(ended);
assert.ok(matchesExport(ended, { media: plan.media, segments: plan.segments, overlays: plan.overlays }));
assert.ok(!matchesExport(ended, { media: plan.media, segments: plan.segments, overlays: exportPlan(withLogo).overlays }));
assert.equal(moveOverlay(withLogo, id, 0.2, 0.2, "end").overlays[0].x, 0.2, "without motion the end key moves the start");
assert.equal("to" in setOverlayMotion(ended, id, false).overlays[0], false);

// ---------- Xuất đoạn I–O ----------
let full = addAudio(addText(addOverlay(base, logo, 5), 30), { source: { kind: "sfx", id: "whoosh-01" }, title: "w", duration: 2 }, 41);
full = addMarker(full, 45, "giữa");
const part = rangeEdit(full, 40, 60);
close(outputDuration(part), 20);
assert.deepEqual([part.segments[0].start, part.segments[0].end], [40, 60]);
assert.deepEqual([part.overlays[0].at, part.overlays[0].start], [0, 40], "the overlay is cut to the range");
assert.equal(part.texts.length, 0, "a text that ended before the range is left out");
assert.equal(part.audio[0].at, 1);
assert.deepEqual(part.markers.map((marker) => marker.at), [5]);
assert.equal(rangeEdit(full, 10, 10.05), null);
assert.equal(outputDuration(rangeEdit(full, 0, 100)), 100);

function pick(object, ...keys) {
  return Object.fromEntries(keys.map((key) => [key, object[key]]));
}
console.log("edit_motion: ok");
