import assert from "node:assert/strict";
import { addLine, asCues, burnRequest, exportLines, mainMs, MIN_LINE_MS, plainSubtitles, retime, serializePlain } from "../src/features/edit/plain_subs.ts";
import { createEdit } from "../src/features/edit/model.ts";
import { DEFAULT_STYLE } from "../src/features/edit/subtitles.ts";

// Seeded from the video's captions, not burned (they are usually in the picture already).
const cues = [{ start_ms: 3000, end_ms: 4000, text: "Hai" }, { start_ms: 0, end_ms: 1500, text: "Một" }];
const seeded = plainSubtitles(null, cues, null);
assert.equal(seeded.burn, false);
assert.deepEqual(seeded.lines.map((line) => line.text), ["Một", "Hai"]);
assert.deepEqual(seeded.style, DEFAULT_STYLE);
// No captions: the user writes them, to burn in.
assert.equal(plainSubtitles({}, [], null).burn, true);

// The draft wins over the captions; bad lines and duplicate ids are dropped.
const saved = plainSubtitles({ subtitles: { burn: true, style: { ...DEFAULT_STYLE, size: 1.5 }, lines: [
  { id: "a", start_ms: 500, end_ms: 900, text: "x" }, { id: "a", start_ms: 0, end_ms: 100, text: "dup" },
  { id: "b", start_ms: 900, end_ms: 800, text: "backwards" }, { id: "c", start_ms: 0, end_ms: 400, text: "first" }, "junk",
] } }, cues, null);
assert.equal(saved.burn, true);
assert.equal(saved.style.size, 1.5);
assert.deepEqual(saved.lines.map((line) => line.id), ["c", "a"]);
// A bad saved look falls back to the remembered one.
assert.equal(plainSubtitles({ subtitles: { lines: [], style: { font: "Comic" } } }, [], { ...DEFAULT_STYLE, bold: true }).style.bold, true);

// Adding: at the playhead, up to the next line, never shorter than MIN_LINE_MS.
const lines = structuredClone(seeded.lines);
const added = addLine(lines, 2000, 60_000);
assert.deepEqual([added.start_ms, added.end_ms, added.text], [2000, 3000, ""]);
assert.deepEqual(lines.map((line) => line.id), ["c1", "n3", "c2"]);
assert.equal(addLine(lines, 2900, 60_000), null, "no room before the next line");
assert.equal(addLine(lines, 59_900, 60_000), null, "no room at the end");

// Retiming keeps a line at least MIN_LINE_MS long.
assert.deepEqual(retime(lines[0], { end_ms: 1200 }), { start_ms: 0, end_ms: 1200 });
assert.equal(retime(lines[0], { start_ms: 1500 - MIN_LINE_MS + 1 }), null);

// The playhead on the edited timeline as a time of the main video.
const edit = createEdit(10, { path: "D:\\v.mp4", title: "v", width: 1920, height: 1080, fps: 30, hasAudio: true });
assert.equal(mainMs(edit, 2.5), 2500);

// Export: empty lines are left out; burned only when on.
const subtitles = { ...seeded, lines };
assert.deepEqual(exportLines(subtitles).map((line) => line.text), ["Một", "Hai"]);
assert.equal(burnRequest(subtitles), null);
assert.deepEqual(burnRequest({ ...subtitles, burn: true }).lines.length, 2);
assert.equal(asCues(lines)[1].text, "");
assert.deepEqual(plainSubtitles({ subtitles: serializePlain({ ...subtitles, burn: true }) }, [], null).lines, lines);
console.log("plain_subs ok");
