import assert from "node:assert/strict";
import {
  addAudio, addOverlay, addText, audioLength, audioPosition, createEdit, exportPlan, insertAt, isStillPath, matchesExport, outputDuration,
  overlayStyle, parseProject, serializeProject, setAudioProps, setAudioTime, setOverlayStyle, setTextProps, setTransition, split, MAX_LOOP_PART,
} from "../src/features/edit/model.ts";

const close = (actual, expected, message, tolerance = 1e-6) => assert.ok(Math.abs(actual - expected) < tolerance, `${message ?? ""} ${actual} != ${expected}`);
const base = createEdit(100, { path: "D:\\a.mp4", width: 1920, height: 1080 });
const roundTrip = (edit) => parseProject(JSON.parse(JSON.stringify(serializeProject(edit, 1, null))), 100, { path: "D:\\a.mp4", width: 1920, height: 1080 }).edit;

// ---------- Transitions: every kind but fade through black overlaps like a dissolve ----------
const cut = split(base, 50);
for (const type of ["fadewhite", "wipeleft", "wiperight", "slideleft", "slideright", "zoomin", "circleopen"]) {
  const joined = setTransition(cut, "s1", type, 1);
  close(outputDuration(joined), 99, type);
  assert.deepEqual(exportPlan(joined).transitions, [{ after: 0, type, duration: 1 }]);
  assert.equal(roundTrip(joined).transitions.s1.type, type, `${type} survives the draft`);
  assert.ok(matchesExport(joined, { segments: exportPlan(joined).segments, transitions: [{ after: 0, type, duration: 1 }] }));
  assert.ok(!matchesExport(joined, { segments: exportPlan(joined).segments, transitions: [{ after: 0, type: "dissolve", duration: 1 }] }));
}
close(outputDuration(setTransition(cut, "s1", "fade", 1)), 100, "fade through black keeps the length");

// ---------- Pictures: over the video only, for the whole video by default ----------
assert.ok(isStillPath("D:\\Logo\\kenh.PNG") && isStillPath("C:/x.jpeg") && isStillPath("a.webp") && !isStillPath("a.mp4"));
const logo = { path: "D:\\Logo\\kenh.png", title: "kenh", duration: 3600, width: 512, height: 256, fps: 30, hasAudio: false, kind: "file" };
assert.equal(insertAt(base, 10, logo), base, "a picture never becomes a segment");
const withLogo = addOverlay(base, logo, 0);
const [overlay] = withLogo.overlays;
assert.deepEqual([overlay.at, overlay.start, overlay.end], [0, 0, 100], "shown for the whole video");

// ---------- Overlay style: clamped, defaults left out, carried by drafts and exports ----------
assert.deepEqual(overlayStyle(overlay), { opacity: 1, radius: 0, border: 0, borderColor: "#FFFFFF" });
assert.equal(setOverlayStyle(withLogo, overlay.id, { opacity: 1, radius: 0 }), withLogo, "nothing changed");
const styled = setOverlayStyle(withLogo, overlay.id, { opacity: 0.02, radius: 0.9, border: 33, borderColor: "#ff0000" });
assert.deepEqual(overlayStyle(styled.overlays[0]), { opacity: 0.1, radius: 0.5, border: 20, borderColor: "#FF0000" });
assert.equal(setOverlayStyle(styled, overlay.id, { borderColor: "red" }), styled, "colours must be #RRGGBB");
const plain = setOverlayStyle(styled, overlay.id, { opacity: 1, radius: 0, border: 0 });
assert.deepEqual(Object.keys(plain.overlays[0]).sort(), Object.keys(overlay).sort(), "back to plain drops the style");
const plan = exportPlan(styled);
assert.deepEqual(plan.media, ["D:\\Logo\\kenh.png"]);
assert.deepEqual(plan.overlays[0], { media: 1, at: 0, start: 0, end: 100, x: overlay.x, y: overlay.y, w: overlay.w, volume: 1,
  opacity: 0.1, radius: 0.5, border: 20, border_color: "#FF0000" });
assert.deepEqual(Object.keys(exportPlan(withLogo).overlays[0]), ["media", "at", "start", "end", "x", "y", "w", "volume"], "plain overlays as before");
assert.deepEqual(roundTrip(styled).overlays, styled.overlays);
assert.ok(matchesExport(styled, { media: plan.media, segments: plan.segments, overlays: plan.overlays }));
assert.ok(!matchesExport(styled, { media: plan.media, segments: plan.segments, overlays: [{ ...plan.overlays[0], opacity: 0.5 }] }));
assert.ok(matchesExport(withLogo, { media: plan.media, segments: plan.segments, overlays: exportPlan(withLogo).overlays }));

// ---------- Lặp lại: background music repeats its part to the end ----------
const music = addAudio(base, { source: { kind: "file", path: "D:\\nhac.mp3" }, title: "nhạc", duration: 30 }, 10);
const clip = music.audio[0];
const looped = setAudioProps(setAudioTime(music, clip.id, { start: 5, end: 25 }), clip.id, { loop: true });
const loop = looped.audio[0];
assert.equal(loop.loop, true);
close(audioLength(loop, 100), 90, "plays to the end of the video");
close(audioPosition(loop, 10), 5);
close(audioPosition(loop, 34), 9, "wraps around its 20 s part");
close(audioPosition(clip, 34), 24, "a plain clip does not wrap");
assert.equal(exportPlan(looped).audio[0].loop, true);
assert.equal("loop" in exportPlan(music).audio[0], false);
assert.equal(roundTrip(looped).audio[0].loop, true);
assert.ok(!matchesExport(looped, { segments: exportPlan(looped).segments, audio: exportPlan(music).audio }));
const unlooped = setAudioProps(looped, clip.id, { loop: false });
assert.equal("loop" in unlooped.audio[0], false);
const long = addAudio(base, { source: { kind: "file", path: "D:\\dai.mp3" }, title: "dài", duration: MAX_LOOP_PART + 60 }, 0);
assert.equal(setAudioProps(long, long.audio[0].id, { loop: true }), long, "parts over 10 minutes do not loop");

// ---------- Bóng đổ ----------
const texted = addText(base, 5);
const text = texted.texts[0];
assert.equal(text.shadow, 0);
assert.equal("shadow" in exportPlan(texted).texts[0], false, "no shadow: as before");
const shadowed = setTextProps(texted, text.id, { shadow: 20 });
assert.equal(shadowed.texts[0].shadow, 12);
assert.equal(exportPlan(shadowed).texts[0].shadow, 12);
assert.equal(roundTrip(shadowed).texts[0].shadow, 12);
const textPlan = exportPlan(shadowed);
assert.ok(matchesExport(shadowed, { segments: textPlan.segments, texts: textPlan.texts }));
assert.ok(!matchesExport(shadowed, { segments: textPlan.segments, texts: exportPlan(texted).texts }));
assert.ok(matchesExport(texted, { segments: textPlan.segments, texts: exportPlan(texted).texts }));
const older = serializeProject(texted, 1, null);
delete older.texts[0].shadow;
assert.equal(parseProject(older, 100, { path: "D:\\a.mp4" }).edit.texts[0].shadow, 0, "older drafts have no shadow");

console.log("edit_assets: ok");
