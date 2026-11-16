import assert from "node:assert/strict";
import { createEdit, lookOf, outputDuration, segmentLength, addText, addAudio, outputStarts } from "../src/features/edit/model.ts";
import { planLook, tiltCover, MAX_TILT } from "../src/features/edit/look.ts";
import { applyMotion, clearMotion, motionFor, splitEvery, DEFAULT_MOTION, MOTION_PRESETS, generator } from "../src/features/edit/motion.ts";

const info = { path: "D:\\a.mp4", width: 1920, height: 1080 };

// ---------- The generator is the same everywhere ----------
const g = generator(42);
assert.deepEqual([g(), g(), g()].map((v) => Math.round(v * 1e6)), (() => { const h = generator(42); return [h(), h(), h()].map((v) => Math.round(v * 1e6)); })());

// ---------- Split every 3 s: pieces cover the same time; nothing placed on the output clock moves ----------
let edit = createEdit(20, info);
edit = addText(edit, 7.5, "Giữ nguyên chỗ");
edit = addAudio(edit, { source: { kind: "sfx", id: "whoosh-01" }, title: "Vút", duration: 0.6 }, 11.2);
const before = { duration: outputDuration(edit), texts: JSON.stringify(edit.texts), audio: JSON.stringify(edit.audio) };
const cut = splitEvery(edit, edit.segments[0].id, 3);
assert.equal(cut.ids.length, 7, "20 s in 3 s steps: six cuts, the last piece 2 s");
assert.deepEqual(outputStarts(cut.edit).map((t) => Math.round(t * 1000)), [0, 3000, 6000, 9000, 12000, 15000, 18000]);
assert.equal(outputDuration(cut.edit), before.duration, "the length stays");
assert.equal(JSON.stringify(cut.edit.texts), before.texts, "texts stay at their times");
assert.equal(JSON.stringify(cut.edit.audio), before.audio, "sounds stay at their times");
const small = splitEvery(createEdit(4, info), "s1", 3);  // 1 s left is not half a step: no sliver
assert.equal(splitEvery(createEdit(10, info), "s1", 0.2).ids.length, 1, "a step under 0.5 s is refused");
assert.ok(small.ids.length <= 2);

// ---------- Seeded motion: same seed, same values; different seed, different values; retry stable ----------
const a = applyMotion(cut.edit, cut.ids, 12345);
const b = applyMotion(cut.edit, cut.ids, 12345);
const c = applyMotion(cut.edit, cut.ids, 999);
assert.equal(JSON.stringify(a.segments.map((s) => s.look)), JSON.stringify(b.segments.map((s) => s.look)));
assert.notEqual(JSON.stringify(a.segments.map((s) => s.look)), JSON.stringify(c.segments.map((s) => s.look)));
assert.equal(JSON.stringify(a.segments.map((s) => planLook(s.look))), JSON.stringify(b.segments.map((s) => planLook(s.look))),
  "what the export reads is stored in the draft, so a retry renders the same");
assert.equal(outputDuration(a), before.duration, "motion never changes timing");
assert.deepEqual(a.segments.map(segmentLength), cut.edit.segments.map(segmentLength));
for (const piece of a.segments) {
  const look = lookOf(piece);
  assert.ok(Math.abs(look.tilt ?? 0) <= DEFAULT_MOTION.maxTilt + 1e-9 && Math.abs(look.tilt ?? 0) >= 0.3 * DEFAULT_MOTION.maxTilt - 0.01, `${look.tilt}`);
  assert.ok(look.zoom && Math.max(look.zoom.from, look.zoom.to) <= DEFAULT_MOTION.maxZoom + 1e-9 && Math.min(look.zoom.from, look.zoom.to) === 1);
}
const tilts = a.segments.map((s) => Math.sign(lookOf(s).tilt ?? 0));
for (let i = 1; i < tilts.length; i += 1) assert.notEqual(tilts[i], tilts[i - 1], "neighbours lean opposite ways (no crooked run)");

// only the chosen pieces; their other looks stay; clear removes only zoom and tilt
const some = applyMotion(cut.edit, [cut.ids[1], cut.ids[3]], 7);
assert.equal(some.segments.filter((s) => s.look).length, 2);
const cleared = clearMotion(some, cut.ids);
assert.ok(cleared.segments.every((s) => !s.look));

// limits: none means none; presets stay within the cap
assert.ok(applyMotion(cut.edit, cut.ids, 1, { maxZoom: 1, maxTilt: 0 }).segments.every((s) => !s.look));
assert.ok(MOTION_PRESETS.every((p) => p.limits.maxTilt <= MAX_TILT && p.limits.maxZoom <= 1.15));
const wild = motionFor(3, 0, { maxZoom: 9, maxTilt: 40 });
assert.ok(Math.abs(wild.tilt) <= MAX_TILT && Math.max(wild.zoom.from, wild.zoom.to) <= 1.15, "limits are capped");

// ---------- No empty corner: the cover factor for a tilt ----------
assert.equal(tiltCover(0, 1920, 1080), 1);
const k = tiltCover(1, 1920, 1080);
const r = Math.PI / 180;
assert.ok(1920 * k >= 1920 * Math.cos(r) + 1080 * Math.sin(r) - 1e-6 && 1080 * k >= 1920 * Math.sin(r) + 1080 * Math.cos(r) - 1e-6);
assert.equal(planLook({ ...lookOf(a.segments[0]) }).tilt, lookOf(a.segments[0]).tilt);

console.log("edit seeded motion tests passed");
