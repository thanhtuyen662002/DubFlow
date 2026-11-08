import assert from "node:assert/strict";
import {
  activeTrack, addExtracted, addTrack, burnRequests, MAX_TRACKS, plainLayers, removeTrack, serializeLayers, serializePlain, srtLines,
} from "../src/features/edit/plain_subs.ts";
import { DEFAULT_STYLE } from "../src/features/edit/subtitles.ts";

// A draft from before layers is one layer, "Phụ đề", with its lines.
const old = { subtitles: serializePlain({ burn: true, style: DEFAULT_STYLE, lines: [{ id: "c1", start_ms: 0, end_ms: 1000, text: "Một" }] }) };
const legacy = plainLayers(old, [], null);
assert.equal(legacy.tracks.length, 1);
assert.equal(legacy.active, "t1");
assert.equal(activeTrack(legacy).lines[0].text, "Một");
assert.equal(activeTrack(legacy).burn, true);

// Tách phụ đề: a lone empty layer is replaced by the original words (above, smaller) and the Vietnamese.
const empty = plainLayers(null, [], null);
const source = [{ start_ms: 1000, end_ms: 2000, text: "大家好" }, { start_ms: 3000, end_ms: 4000, text: "谢谢" }];
const vi = [{ start_ms: 1000, end_ms: 2000, text: "Chào mọi người" }, { start_ms: 3000, end_ms: 4000, text: "Cảm ơn" }];
const made = addExtracted(empty, "zh", source, vi, DEFAULT_STYLE);
assert.deepEqual(empty.tracks.map((track) => [track.name, track.lang]), [["Tiếng Trung", "zh"], ["Tiếng Việt", "vi"]]);
assert.equal(empty.active, made.vi.id);
assert.ok(made.source.style.size < made.vi.style.size, "the original words are smaller");
assert.equal(made.source.style.y, 0.8);
assert.equal(made.vi.style.y, undefined, "the Vietnamese keeps the usual place");

// Both burn, the Vietnamese over the original; the .srt is the Vietnamese.
const burns = burnRequests(empty);
assert.deepEqual(burns.burn.lines.map((line) => line.text), ["大家好", "谢谢"]);
assert.deepEqual(burns.burnExtra.map((burn) => burn.lines.map((line) => line.text)), [["Chào mọi người", "Cảm ơn"]]);
assert.deepEqual(srtLines(empty).map((line) => line.text), ["Chào mọi người", "Cảm ơn"]);

// A hidden layer is neither burned nor in the .srt.
made.vi.hidden = true;
assert.deepEqual(burnRequests(empty), { burn: burns.burn });
assert.deepEqual(srtLines(empty).map((line) => line.text), ["大家好", "谢谢"]);
made.vi.hidden = false;

// Saved and read back, with the edited layer at the top level for older versions.
const saved = JSON.parse(JSON.stringify(serializeLayers(empty)));
assert.deepEqual(saved.lines.map((line) => line.text), ["Chào mọi người", "Cảm ơn"]);
const back = plainLayers({ subtitles: saved }, [], null);
assert.deepEqual(back.tracks.map((track) => [track.id, track.name, track.lang, track.hidden, track.lines.length]),
  empty.tracks.map((track) => [track.id, track.name, track.lang, track.hidden, track.lines.length]));
assert.equal(back.active, empty.active);

// Layers with words stay when extracting again; at most MAX_TRACKS.
const full = plainLayers({ subtitles: saved }, [], null);
addExtracted(full, "en", source, vi, DEFAULT_STYLE);
assert.equal(full.tracks.length, MAX_TRACKS);
const over = addExtracted(full, "zh", source, vi, DEFAULT_STYLE);
assert.equal(over.vi, null);
assert.equal(addTrack(full, { name: "x", style: DEFAULT_STYLE }), null);

// Removing: the edited one moves to a neighbour; the last one left is emptied, not removed.
const two = plainLayers({ subtitles: saved }, [], null);
removeTrack(two, two.active);
assert.equal(two.tracks.length, 1);
assert.equal(two.active, two.tracks[0].id);
removeTrack(two, two.active);
assert.equal(two.tracks.length, 1);
assert.equal(two.tracks[0].lines.length, 0);
assert.deepEqual(burnRequests(two), {});
console.log("plain_layers ok");
