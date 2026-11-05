import assert from "node:assert/strict";
import { burned, burnedWords, layersFrom } from "../src/features/edit/docu_layers.ts";

const script = { project_title: "T", script_sha256: "abc", chapters: [{ chapter_type: "HOOK", scenes: [
  { scene_id: "sc_001", narration_text: "One.", on_screen_graphic: { type: "METRIC_CALLOUT", title: "TOTAL", value: "280", subtext: "" } }] }] };
const timeline = { duration: 10, scenes: [{ scene_id: "sc_001", chapter_type: "HOOK", start: 0, end: 7.5, slot_end: 8.3, exact_duration_ms: 7500 }] };
const open = (overlays, state = "COMPLETED") => ({ script, narration: { audio: null, timeline, status: null }, render: { video: "D:/run/documentary.mp4", status: { state, overlays }, options: {} } });

// What the last finished render burned in.
assert.deepEqual(burned(open({ graphics: true, captions: false, music: "generated" })), { graphics: true, captions: false, music: "generated" });
assert.equal(burned(open({ graphics: false, captions: false, music: "none" })), null, "a clean picture has nothing to split");
assert.equal(burned(open({ graphics: true, captions: true, music: "file" }, "RUNNING")), null, "not while rendering");
assert.equal(burned(open(undefined)), null, "an older render does not say");
assert.equal(burned(null), null);

assert.equal(burnedWords({ graphics: true, captions: true, music: "file" }), "số liệu, phụ đề và nhạc nền");
assert.equal(burnedWords({ graphics: true, captions: false, music: "generated" }), "số liệu và nhạc nền");
assert.equal(burnedWords({ graphics: false, captions: true, music: "none" }), "phụ đề");

// The layers put back: number cards only when the film drew them, captions burned again by Phụ đề, the kept bed.
const layers = layersFrom(open({ graphics: true, captions: true, music: "generated" }), { graphics: true, captions: true, music: "generated", bed: "D:/run/music_bed.wav" });
assert.deepEqual(layers.texts.map((text) => [text.at, text.text]), [[0.3, "280\nTOTAL"]]);
assert.equal(layers.burnCaptions, true);
assert.equal(layers.bed, "D:/run/music_bed.wav");
assert.deepEqual(layersFrom(open({}), { graphics: false, captions: false, music: "none", bed: null }), { texts: [], burnCaptions: false, bed: null });
console.log("docu layers ok");
