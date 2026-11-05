import assert from "node:assert/strict";
import {
  addAudio, addedId, addOverlay, addText, append, createEdit, defaultExportName, EditHistory, estimateExportBytes, exportPlan, exportSize, formatClock,
  formatSpeed, HISTORY_LIMIT, insertAt, isUnchanged, matchesExport, moveOverlay, outputDuration, outputStarts, outputToSource,
  overlayHeight, overlayLength, overlayPreset, overlayPresetOf, parseClock, parseProject, remapCues, remove, removeOverlay,
  resizeOverlay, retargetEdit, sameEdit, sameSegments, serializeProject, setOverlayTime, setOverlayVolume, setSpeed, setTransition, setVolume,
  sourceToOutput, openingTime, resolveTarget, modeOffered, split, thumbGrid, tickStep, transitionAt, transitionRoom, trim, zoomScale,
} from "../src/features/edit/model.ts";

const close = (actual, expected, message, tolerance = 1e-6) => assert.ok(Math.abs(actual - expected) < tolerance, `${message ?? ""} ${actual} != ${expected}`);

// A new edit is the whole video once.
const base = createEdit(100);
assert.equal(base.segments.length, 1);
assert.deepEqual(base.segments[0], { id: "s1", mediaId: "main", start: 0, end: 100, speed: 1, volume: 1 });
assert.ok(isUnchanged(base, 100));
close(outputDuration(base), 100);

// Split at the playhead; too close to an edge is a no-op.
const halves = split(base, 40);
assert.deepEqual(halves.segments.map((s) => [s.id, s.start, s.end]), [["s1", 0, 40], ["s2", 40, 100]]);
assert.equal(halves.nextId, 3);
assert.ok(!isUnchanged(halves, 100));
assert.equal(split(base, 0.05), base);
assert.equal(split(base, 99.95), base);
assert.equal(split(halves, 40), halves, "a boundary is already split");
assert.notEqual(split(halves, 40.2), halves);
assert.deepEqual(base.segments[0].end, 100, "operations never mutate");

// Remove: the rest moves up; the last segment cannot go.
const three = split(halves, 70); // s1 0-40, s2 40-70, s3 70-100
const cut = remove(three, "s2");
assert.deepEqual(cut.segments.map((s) => s.id), ["s1", "s3"]);
close(outputDuration(cut), 70);
assert.equal(remove(createEdit(10), "s1").segments.length, 1);
assert.equal(remove(three, "nope"), three);

// Speed and volume are clamped per segment.
const fast = setSpeed(cut, "s3", 1.5);
close(outputDuration(fast), 40 + 30 / 1.5);
assert.equal(setSpeed(cut, "s3", 9).segments[1].speed, 2);
assert.equal(setSpeed(cut, "s3", 0.1).segments[1].speed, 0.5);
assert.equal(setSpeed(fast, "s3", 1.5), fast, "unchanged speed is a no-op");
assert.equal(setVolume(cut, "s1", 3).segments[0].volume, 2);
assert.equal(setVolume(cut, "s1", -1).segments[0].volume, 0);
assert.equal(setSpeed(cut, "s1", Number.NaN), cut);
assert.ok(!isUnchanged(setVolume(base, "s1", 0.5), 100));
assert.ok(!isUnchanged(setSpeed(base, "s1", 2), 100));

// Trim stays inside the source and keeps 0.1 s; neighbours do not constrain it.
assert.equal(trim(cut, "s3", "start", 20).segments[1].start, 20, "a segment may reuse an earlier source range");
assert.equal(trim(cut, "s3", "start", -5).segments[1].start, 0);
assert.equal(trim(cut, "s3", "start", 120).segments[1].start, 99.9);
assert.equal(trim(cut, "s1", "end", 0).segments[0].end, 0.1);
assert.equal(trim(cut, "s1", "end", 150).segments[0].end, 100);
assert.equal(trim(cut, "s1", "end", 40), cut);
assert.equal(trim(cut, "s1", "end", 40.00001), cut, "times are kept to the millisecond");
assert.equal(trim(cut, "s1", "start", Number.POSITIVE_INFINITY), cut);

// Output <-> source mapping, with speed.
const mapped = fast; // s1 0-40 @1, s3 70-100 @1.5 -> output 0-40, 40-60
assert.deepEqual(outputStarts(mapped), [0, 40]);
let point = outputToSource(mapped, 10);
assert.equal(point.segment.id, "s1");
close(point.sourceTime, 10);
point = outputToSource(mapped, 40);
assert.equal(point.segment.id, "s3", "a boundary belongs to the next segment");
close(point.sourceTime, 70);
point = outputToSource(mapped, 50);
close(point.sourceTime, 85);
assert.equal(point.index, 1);
close(point.outputStart, 40);
point = outputToSource(mapped, 500);
assert.equal(point.segment.id, "s3");
close(point.sourceTime, 100);
close(outputToSource(mapped, -3).sourceTime, 0);
close(sourceToOutput(mapped, 85), 50);
close(sourceToOutput(mapped, 10), 10);
assert.equal(sourceToOutput(mapped, 55), null, "deleted source time");
// A source time shared by two segments resolves to the preferred one.
const twice = { ...mapped, segments: [mapped.segments[0], { ...mapped.segments[1], start: 20 }] };
close(sourceToOutput(twice, 30), 30);
close(sourceToOutput(twice, 30, 1), 40 + 10 / 1.5);

// Cues: deleted ones go, partial ones are clipped, the rest shift and scale.
const cues = [
  { start_ms: 1000, end_ms: 3000, text: "một" },
  { start_ms: 38000, end_ms: 45000, text: "hai" }, // crosses the cut at 40
  { start_ms: 50000, end_ms: 60000, text: "ba" }, // deleted
  { start_ms: 69000, end_ms: 76000, text: "bốn" }, // starts before s3
  { start_ms: 94000, end_ms: 99000, text: "năm" },
  { start_ms: 39950, end_ms: 40050, text: "ngắn" }, // 0.1 s cue, half deleted -> 0.05 s piece dropped
];
assert.deepEqual(remapCues(cues, mapped), [
  { start_ms: 1000, end_ms: 3000, text: "một" },
  { start_ms: 38000, end_ms: 40000, text: "hai" },
  { start_ms: 40000, end_ms: 44000, text: "bốn" },
  { start_ms: 56000, end_ms: 59333, text: "năm" },
]);
// A split with nothing removed keeps a crossing cue whole.
assert.deepEqual(remapCues([{ start_ms: 38000, end_ms: 45000, text: "hai" }], halves), [{ start_ms: 38000, end_ms: 45000, text: "hai" }]);
// Reordered segments come back in output order; a whole short cue is kept.
const swapped = { ...three, segments: [three.segments[2], three.segments[0]] };
assert.deepEqual(remapCues([{ start_ms: 1000, end_ms: 1050, text: "a" }, { start_ms: 80000, end_ms: 81000, text: "b" }], swapped), [
  { start_ms: 10000, end_ms: 11000, text: "b" },
  { start_ms: 31000, end_ms: 31050, text: "a" },
]);
assert.deepEqual(remapCues([], mapped), []);

// History: undo/redo, no-op pushes, the change count against the last export.
const history = new EditHistory(base);
assert.equal(history.changes, 0);
assert.equal(history.push(base), false);
assert.ok(history.push(halves));
assert.ok(history.push(three));
assert.equal(history.changes, 2);
assert.equal(history.undo(), halves);
assert.equal(history.changes, 1);
assert.equal(history.redo(), three);
assert.equal(history.redo(), null);
history.markExported(three);
assert.equal(history.changes, 0);
history.undo();
assert.equal(history.changes, 1, "undo past the export is an unexported change");
history.redo();
assert.ok(history.push(cut));
assert.equal(history.changes, 1);
history.undo();
history.undo();
assert.ok(history.push(fast), "a new branch drops the exported state");
assert.ok(history.changes >= 1);
history.markExported(fast);
assert.equal(history.changes, 0);
// Exported by value: an equal edit from another object also counts.
history.push(setVolume(fast, "s1", 0.5));
history.markExported({ ...fast, segments: fast.segments.map((s) => ({ ...s })) });
assert.equal(history.changes, 1);

// A reopened draft keeps its count until undone to the start.
const reopened = new EditHistory(cut, 3);
assert.equal(reopened.changes, 3);
reopened.push(fast);
assert.equal(reopened.changes, 4);
reopened.undo();
assert.equal(reopened.changes, 3);
assert.equal(reopened.canUndo, false);

// At most 200 steps back; the count keeps going.
const long = new EditHistory(base);
let state = base;
for (let i = 0; i < HISTORY_LIMIT + 25; i++) {
  state = setVolume(state, "s1", i % 2 ? 1 : 0.5);
  long.push(state);
}
let undone = 0;
while (long.undo()) undone += 1;
assert.equal(undone, HISTORY_LIMIT);
assert.ok(long.changes >= 1);

// Projects round-trip and reject other renders or bad data.
const saved = JSON.parse(JSON.stringify(serializeProject(fast, 4, "s3")));
assert.deepEqual(saved, {
  schema_version: 4, duration: 100, next_id: 4, changes: 4, selected: "s3",
  media: [{ id: "main", path: "", title: "", duration: 100, width: 0, height: 0, fps: 30, hasAudio: true, kind: "main" }],
  segments: [{ id: "s1", mediaId: "main", start: 0, end: 40, speed: 1, volume: 1 }, { id: "s3", mediaId: "main", start: 70, end: 100, speed: 1.5, volume: 1 }],
  transitions: {}, overlays: [], audio: [], main_volume: 1, texts: [],
});
const parsed = parseProject(saved, 100.2);
assert.equal(parsed.changes, 4);
assert.equal(parsed.selected, "s3");
assert.equal(parsed.edit.nextId, 4);
assert.equal(parsed.edit.duration, 100.2);
assert.ok(sameSegments(parsed.edit.segments, fast.segments));
assert.equal(parseProject(saved, 120), null, "a different render");
assert.equal(parseProject({ ...saved, schema_version: 5 }, 100), null);
assert.equal(parseProject({ ...saved, media: undefined }, 100), null, "schema 2 lists its media");
// A 4a (schema 1) draft migrates: every segment plays the main video.
const legacy = parseProject({ schema_version: 1, duration: 100, next_id: 4, changes: 2, selected: "s3",
  segments: [{ id: "s1", start: 0, end: 40, speed: 1, volume: 1 }, { id: "s3", start: 70, end: 100, speed: 1.5, volume: 1 }] }, 100, { path: "D:\\o\\final_vi.mp4" });
assert.deepEqual(legacy.edit.segments.map((s) => s.mediaId), ["main", "main"]);
assert.deepEqual(legacy.edit.media.map((m) => [m.id, m.path]), [["main", "D:\\o\\final_vi.mp4"]]);
assert.deepEqual(legacy.edit.transitions, {});
assert.deepEqual(legacy.edit.overlays, []);
assert.equal(legacy.changes, 2);
assert.equal(parseProject({ schema_version: 1, duration: 100, segments: [{ id: "s1", start: 0, end: 10, speed: 1, volume: 1, mediaId: "m9" }] }, 100).edit.segments[0].mediaId, "main", "schema 1 ignores media ids");
assert.equal(parseProject({ ...saved, segments: [] }, 100), null);
assert.equal(parseProject({ ...saved, segments: [{ ...saved.segments[0], speed: 3 }] }, 100), null);
assert.equal(parseProject({ ...saved, segments: [saved.segments[0], saved.segments[0]] }, 100), null, "duplicate ids");
assert.equal(parseProject({ ...saved, segments: [{ ...saved.segments[0], end: 0.05 }] }, 100), null);
assert.equal(parseProject({ ...saved, segments: [{ ...saved.segments[0], id: "x" }] }, 100), null);
assert.equal(parseProject(null, 100), null);
assert.equal(parseProject({ ...saved, next_id: 1, selected: "zz" }, 100).edit.nextId, 4);
assert.equal(parseProject({ ...saved, selected: "zz" }, 100).selected, null);
// A slightly shorter render clamps the last end.
assert.equal(parseProject(saved, 99.8).edit.segments[1].end, 99.8);

// Export names and sizes.
assert.equal(defaultExportName("Sinh vật lạ", []), "Sinh vật lạ (sửa 1)");
assert.equal(defaultExportName("Sinh vật lạ.mp4", ["D:\\x\\Sinh vật lạ (sửa 1).mp4", "D:/x/sinh vật lạ (sửa 2).mp4"]), "Sinh vật lạ (sửa 3)");
assert.equal(defaultExportName("  ", []), "video (sửa 1)");
assert.deepEqual(exportSize(1024, 576, "source"), { width: 1024, height: 576 });
assert.deepEqual(exportSize(1024, 576, "1080p"), { width: 1920, height: 1080 });
assert.deepEqual(exportSize(1024, 576, "720p"), { width: 1280, height: 720 });
assert.deepEqual(exportSize(1080, 1920, "720p"), { width: 720, height: 1280 });
assert.deepEqual(exportSize(853, 480, "720p"), { width: 1280, height: 720 });
close(estimateExportBytes(100_000_000, 100, 50, 1024, 576, "source"), 50_000_000);
assert.ok(estimateExportBytes(100_000_000, 100, 50, 1920, 1080, "720p") < 50_000_000);
assert.equal(estimateExportBytes(0, 100, 50, 1, 1, "source"), 0);

// Clock text, speed labels.
assert.equal(formatClock(0), "0:00");
assert.equal(formatClock(52.04, true), "0:52.0");
assert.equal(formatClock(107.96, true), "1:48.0");
assert.equal(formatClock(3725.25, true), "1:02:05.3");
assert.equal(formatClock(3725), "1:02:05");
assert.equal(parseClock("0:52.5"), 52.5);
assert.equal(parseClock("01:47,0"), 107);
assert.equal(parseClock("83.4"), 83.4);
assert.equal(parseClock("1:02:05"), 3725);
assert.equal(parseClock("1:75"), null);
assert.equal(parseClock("abc"), null);
assert.equal(parseClock(""), null);
assert.equal(formatSpeed(1.5), "1,5×");
assert.equal(formatSpeed(0.75), "0,75×");
assert.equal(formatSpeed(2), "2×");

// Timeline: ruler spacing, zoom, thumbnail grid.
assert.equal(tickStep(20), 5);
assert.equal(tickStep(1), 120);
assert.equal(tickStep(0.001), 3600);
close(zoomScale(2, 0), 2);
close(zoomScale(2, 1), 20);
close(zoomScale(30, 0.5), 30, "a short video already fits above 20 px/s");
assert.ok(zoomScale(2, 0.5) > 2 && zoomScale(2, 0.5) < 20);
const grid = thumbGrid(600);
assert.equal(grid.times.length, 60);
assert.equal(grid.step, 10);
assert.equal(grid.times[0], 5);
assert.equal(thumbGrid(20).times.length, 20, "at least a second apart");
assert.deepEqual(thumbGrid(0).times, []);

// ---------- Chèn (4b): other media, transitions, overlays ----------
const film = createEdit(100, { path: "D:\\o\\final_vi.mp4", title: "Phim", width: 1920, height: 1080, fps: 25 });
assert.deepEqual(film.media.map((m) => [m.id, m.kind, m.path]), [["main", "main", "D:\\o\\final_vi.mp4"]]);
const intro = { path: "D:\\c\\intro.mp4", title: "intro", duration: 10, width: 1280, height: 720, fps: 30, hasAudio: true, kind: "file" };
const silent = { path: "D:\\c\\cảnh.mov", title: "cảnh", duration: 6, width: 720, height: 1280, fps: 30, hasAudio: false, kind: "job" };

// Insert at the playhead: the segment there splits, the clip goes after its left part, the rest ripples.
const inserted = insertAt(film, 40, intro);
assert.deepEqual(inserted.segments.map((s) => [s.id, s.mediaId, s.start, s.end]), [["s1", "main", 0, 40], ["s4", "m2", 0, 10], ["s3", "main", 40, 100]]);
assert.deepEqual(inserted.media.map((m) => m.id), ["main", "m2"]);
assert.equal(addedId(film, inserted), "s4");
close(outputDuration(inserted), 110);
assert.deepEqual(outputStarts(inserted), [0, 40, 50]);
point = outputToSource(inserted, 45);
assert.equal(point.segment.id, "s4");
close(point.sourceTime, 5);
close(sourceToOutput(inserted, 45), 55, "main times skip the inserted clip");
close(sourceToOutput(inserted, 5, -1, "m2"), 45);
assert.equal(insertAt(film, Number.NaN, intro), film);
assert.equal(insertAt(film, 10, { ...intro, duration: 0.05 }), film, "too short to insert");
// Within 0.1 s of a segment edge there is no split; the same file is one media.
const twiceIn = insertAt(inserted, 50.05, intro, { start: 2, end: 4 });
assert.deepEqual(twiceIn.segments.map((s) => [s.id, s.mediaId, s.start, s.end]),
  [["s1", "main", 0, 40], ["s4", "m2", 0, 10], ["s5", "m2", 2, 4], ["s3", "main", 40, 100]]);
assert.equal(twiceIn.media.length, 2);
assert.deepEqual(insertAt(inserted, 0, silent).segments.map((s) => s.mediaId), ["m5", "main", "m2", "main"]);
// Append, and the main video itself inserted again.
const appended = append(inserted, silent);
assert.deepEqual(appended.segments.map((s) => [s.mediaId, s.start, s.end]).at(-1), ["m5", 0, 6]);
close(outputDuration(appended), 116);
assert.equal(append(film, { ...intro, path: "d:/o/FINAL_VI.mp4", duration: 100 }).segments[1].mediaId, "main");
// Trim stays within the clip's own length; speed and volume work as on any segment.
assert.equal(trim(inserted, "s4", "end", 50).segments[1].end, 10);
assert.equal(trim(inserted, "s4", "start", 9.95).segments[1].start, 9.9);
close(outputDuration(setSpeed(inserted, "s4", 2)), 105);
// Removing the clip drops its media.
assert.deepEqual(remove(inserted, "s4").media.map((m) => m.id), ["main"]);
// Cues come from the main video only and skip the inserted part.
assert.deepEqual(remapCues([{ start_ms: 38000, end_ms: 45000, text: "hai" }, { start_ms: 41000, end_ms: 42000, text: "ba" }], inserted), [
  { start_ms: 38000, end_ms: 40000, text: "hai" },
  { start_ms: 50000, end_ms: 55000, text: "hai" },
  { start_ms: 51000, end_ms: 52000, text: "ba" },
]);
assert.ok(!isUnchanged(append(film, intro), 100));

// Transitions: a dissolve overlaps its neighbours, fade through black keeps the length.
const dissolved = setTransition(inserted, "s1", "dissolve", 1);
assert.deepEqual(dissolved.transitions, { s1: { type: "dissolve", duration: 1 } });
close(outputDuration(dissolved), 109);
assert.deepEqual(outputStarts(dissolved), [0, 39, 49]);
point = outputToSource(dissolved, 39.5);
assert.equal(point.segment.id, "s4", "the dissolve belongs to the incoming segment");
close(point.sourceTime, 0.5);
assert.deepEqual(remapCues([{ start_ms: 45000, end_ms: 46000, text: "x" }], dissolved), [{ start_ms: 54000, end_ms: 55000, text: "x" }], "1 s earlier than without the dissolve");
const faded = setTransition(dissolved, "s4", "fade", 3);
assert.deepEqual(faded.transitions.s4, { type: "fade", duration: 2 }, "at most 2 s");
close(outputDuration(faded), 109);
assert.equal(setTransition(inserted, "s1", "fade", 0.05).transitions.s1.duration, 0.2, "at least 0.2 s");
assert.equal(setTransition(inserted, "s3", "fade", 1), inserted, "no joint after the last segment");
assert.equal(setTransition(inserted, "nope", "fade", 1), inserted);
assert.equal(setTransition(dissolved, "s1", "dissolve", 1), dissolved);
assert.deepEqual(setTransition(dissolved, "s1", null).transitions, {});
assert.equal(setTransition(inserted, "s1", null), inserted);
// At most half of each neighbour: a 1 s clip allows 0.5 s, a 0.3 s one none.
assert.deepEqual(transitionAt(trim(dissolved, "s4", "end", 1), 0), { type: "dissolve", duration: 0.5 });
assert.equal(setTransition(inserted, "s1", "fade", 2).transitions.s1.duration, 2);
assert.equal(setTransition(trim(inserted, "s4", "end", 1), "s1", "fade", 2).transitions.s1.duration, 0.5);
const tiny = trim(dissolved, "s4", "end", 0.3);
assert.equal(transitionAt(tiny, 0), null);
assert.equal(setTransition(tiny, "s4", "fade", 1), tiny);
close(transitionRoom(inserted, 0), 2);
// A split keeps the joint's transition on the right part; removing a segment drops its own.
const splitDissolve = split(dissolved, 20);
assert.deepEqual(Object.keys(splitDissolve.transitions), ["s5"]);
assert.deepEqual(splitDissolve.segments.map((s) => s.id), ["s1", "s5", "s4", "s3"]);
assert.deepEqual(remove(dissolved, "s1").transitions, {});

// Overlays: placed top right at 35 % of the width, kept inside the frame.
const covered = addOverlay(film, intro, 20);
assert.deepEqual(covered.overlays, [{ id: "o3", mediaId: "m2", at: 20, start: 0, end: 10, x: 0.62, y: 0.03, w: 0.35, volume: 1 }]);
assert.equal(addedId(film, covered), "o3");
close(overlayHeight(covered, covered.overlays[0]), 0.35);
assert.ok(!isUnchanged(covered, 100));
let moved = moveOverlay(covered, "o3", 0.9, -1);
assert.deepEqual([moved.overlays[0].x, moved.overlays[0].y], [0.65, 0]);
assert.equal(moveOverlay(covered, "o3", 0.62, 0.03), covered);
assert.equal(resizeOverlay(covered, "o3", 0.1).overlays[0].w, 0.2);
moved = resizeOverlay(covered, "o3", 1.5);
assert.deepEqual([moved.overlays[0].x, moved.overlays[0].y, moved.overlays[0].w], [0, 0, 1]);
assert.deepEqual(overlayPreset(covered, "o3", "br").overlays[0], { ...covered.overlays[0], x: 0.62, y: 0.62 });
assert.deepEqual(overlayPreset(covered, "o3", "center").overlays[0], { ...covered.overlays[0], x: 0.325, y: 0.325 });
assert.equal(overlayPresetOf(covered, covered.overlays[0]), "tr");
const offPreset = moveOverlay(covered, "o3", 0.4, 0.1);
assert.equal(overlayPresetOf(offPreset, offPreset.overlays[0]), null);
// A portrait clip over a landscape video is as wide as fits the height.
const tallEdit = addOverlay(film, silent, 0);
assert.equal(tallEdit.overlays[0].w, 0.3164);
assert.equal(tallEdit.overlays[0].y, 0);
close(overlayHeight(tallEdit, tallEdit.overlays[0]), 1, "", 0.001);
// Times: within the clip and the output; an overlay near the end moves back to fit.
assert.equal(addOverlay(film, intro, 95).overlays[0].at, 90);
let timed = setOverlayTime(covered, "o3", { at: 500, start: -2, end: 50 });
assert.deepEqual([timed.overlays[0].at, timed.overlays[0].start, timed.overlays[0].end], [99.9, 0, 10]);
timed = setOverlayTime(covered, "o3", { start: 9.95 });
assert.deepEqual([timed.overlays[0].start, timed.overlays[0].end], [9.9, 10]);
assert.equal(setOverlayTime(covered, "o3", { at: Number.NaN }), covered);
assert.equal(setOverlayVolume(covered, "o3", 5).overlays[0].volume, 2);
assert.deepEqual(removeOverlay(covered, "o3").media.map((m) => m.id), ["main"]);
assert.equal(removeOverlay(covered, "o9"), covered);
close(overlayLength(covered, { ...covered.overlays[0], at: 95 }), 5);
// Inserting before an overlay moves it with the video after it.
assert.equal(insertAt(covered, 10, silent).overlays[0].at, 26);
assert.equal(insertAt(covered, 30, silent).overlays[0].at, 20);
// At most 20 overlays.
let crowded = film;
for (let i = 0; i < 25; i++) crowded = addOverlay(crowded, intro, i);
assert.equal(crowded.overlays.length, 20);

// The export plan: media 0 is the main video, other files by path, effective transitions.
const full = setTransition(addOverlay(append(dissolved, silent), intro, 5), "s3", "fade", 0.8);
const plan = exportPlan(full);
assert.deepEqual(plan, {
  media: ["D:\\c\\intro.mp4", "D:\\c\\cảnh.mov"],
  segments: [
    { media: 0, start: 0, end: 40, speed: 1, volume: 1 }, { media: 1, start: 0, end: 10, speed: 1, volume: 1 },
    { media: 0, start: 40, end: 100, speed: 1, volume: 1 }, { media: 2, start: 0, end: 6, speed: 1, volume: 1 },
  ],
  transitions: [{ after: 0, type: "dissolve", duration: 1 }, { after: 2, type: "fade", duration: 0.8 }],
  overlays: [{ media: 1, at: 5, start: 0, end: 10, x: 0.62, y: 0.03, w: 0.35, volume: 1 }],
  audio: [], mainVolume: 1, texts: [],
});
assert.ok(matchesExport(full, JSON.parse(JSON.stringify(plan))));
assert.ok(!matchesExport(setOverlayVolume(full, full.overlays[0].id, 0.5), plan));
assert.ok(!matchesExport(setTransition(full, "s3", null), plan));
assert.ok(matchesExport(halves, { segments: [{ start: 0, end: 40, speed: 1, volume: 1 }, { start: 40, end: 100, speed: 1, volume: 1 }] }), "a 4a export");
assert.ok(!matchesExport(inserted, { segments: [{ start: 0, end: 40, speed: 1, volume: 1 }, { start: 0, end: 10, speed: 1, volume: 1 }, { start: 40, end: 100, speed: 1, volume: 1 }] }));
assert.ok(sameEdit(full, JSON.parse(JSON.stringify(full))));

// Undo covers inserts, transitions and overlays.
const chain = new EditHistory(film);
chain.push(inserted);
chain.push(dissolved);
chain.push(full);
assert.equal(chain.undo(), dissolved);
assert.equal(chain.undo(), inserted);
assert.equal(chain.changes, 1);
assert.equal(chain.redo(), dissolved);

// Schema 2 round trip, with recently picked files kept.
const other = { ...intro, id: "m77", path: "D:\\c\\khác.mp4", title: "khác" };
const draft = JSON.parse(JSON.stringify(serializeProject(full, 3, "s4", [{ ...intro, id: "x" }, other])));
assert.deepEqual(draft.media.map((m) => [m.id, m.path]), [["main", "D:\\o\\final_vi.mp4"], ["m2", "D:\\c\\intro.mp4"], ["m5", "D:\\c\\cảnh.mov"], ["r1", "D:\\c\\khác.mp4"]]);
assert.deepEqual(draft.transitions, { s1: { type: "dissolve", duration: 1 }, s3: { type: "fade", duration: 0.8 } });
const reread = parseProject(draft, 100, { path: "D:\\o\\final_vi.mp4", title: "Phim", width: 1920, height: 1080, fps: 25 });
assert.ok(sameEdit(reread.edit, full));
assert.deepEqual(reread.edit.media.map((m) => m.id), ["main", "m2", "m5"]);
assert.deepEqual(reread.recent.map((m) => m.path), ["D:\\c\\intro.mp4", "D:\\c\\khác.mp4"]);
assert.equal(reread.selected, "s4");
assert.equal(reread.edit.nextId, full.nextId);
assert.equal(parseProject({ ...draft, segments: [{ ...draft.segments[1], mediaId: "m99" }] }, 100), null, "unknown media");
assert.equal(parseProject({ ...draft, segments: [{ ...draft.segments[1], mediaId: "r1" }] }, 100), null, "a recent file is not part of the edit");
assert.equal(parseProject({ ...draft, segments: [{ ...draft.segments[1], end: 30 }] }, 100).edit.segments[0].end, 10, "clamped to the clip");
assert.equal(parseProject({ ...draft, media: [...draft.media, draft.media[1]] }, 100), null, "duplicate media ids");
assert.equal(parseProject({ ...draft, media: [{ ...draft.media[0], kind: "file" }, ...draft.media.slice(1)] }, 100), null);
const loose = parseProject({ ...draft, transitions: { s3: { type: "wipe", duration: 1 }, s99: { type: "fade", duration: 1 }, s1: { type: "fade", duration: 9 } },
  overlays: [...draft.overlays, { ...draft.overlays[0], id: "o99", x: 4 }] }, 100);
assert.deepEqual(loose.edit.transitions, { s1: { type: "fade", duration: 2 } }, "bad transitions are dropped, durations clamped");
assert.equal(loose.edit.overlays.length, 1, "a bad overlay is dropped");

// Mẫu (Issue #426): the draft moves to a new render of a Bán hàng video.
{
  const info = { path: "D:/o/run-1/video-1.mp4", title: "Nồi · video 1", width: 1080, height: 1920, fps: 30, hasAudio: true };
  const base = split(createEdit(30, info), 10);
  const same = retargetEdit(base, 30.02, { path: "D:/o/run-2/video-1.mp4" });
  assert.equal(same.trimmed, 0);
  assert.equal(same.edit.media[0].path, "D:/o/run-2/video-1.mp4");
  assert.equal(same.edit.media[0].width, 1080, "what is not given is kept");
  assert.deepEqual(same.edit.segments.map(segment => [segment.start, segment.end]), [[0, 10], [10, 30.02]]);
  const longer = retargetEdit(base, 34);
  assert.equal(longer.trimmed, 0);
  assert.equal(outputDuration(longer.edit), 34, "a part that reached the end reaches the new end");
  const shorter = retargetEdit(base, 8);
  assert.equal(shorter.trimmed, 2, "the first part is cut, the second is past the end");
  assert.deepEqual(shorter.edit.segments.map(segment => [segment.start, segment.end]), [[0, 8]]);
  const added = addText(addAudio(base, { source: { kind: "sfx", id: "pop" }, title: "Pop", duration: 1 }, 25), 25);
  const cut = retargetEdit(added, 20);
  assert.equal(cut.trimmed, 2, "a sound and a text past the new end go");
  assert.deepEqual([cut.edit.audio.length, cut.edit.texts.length], [0, 0]);
  const kept = retargetEdit(added, 28);
  assert.deepEqual([kept.trimmed, kept.edit.audio.length, kept.edit.texts.length], [0, 1, 1]);
  const late = trim(createEdit(30, info), "s1", "start", 25);
  const gone = retargetEdit(late, 12);
  assert.equal(gone.trimmed, 1);
  assert.deepEqual(gone.edit.segments.map(segment => [segment.mediaId, segment.start, segment.end]), [["main", 0, 12]], "an empty edit becomes the whole new video");
  const saved = parseProject(JSON.parse(JSON.stringify(serializeProject(shorter.edit, 3, null))), 8, info);
  assert.ok(saved, "the carried draft loads for the new video");
  assert.equal(saved.changes, 3);
}

console.log("edit tests passed");

// Opening: a queue video goes on from its player's time; another video starts at 0 (Issue #497).
close(openingTime("job", createEdit(100), 45), 45);
assert.equal(openingTime("job", createEdit(100), null), 0);
for (const kind of ["file", "product", "compilation", "docu"]) assert.equal(openingTime(kind, createEdit(100), 45), 0, kind);

// The host's kind wins (Issue #497): a run DubFlow kept no record of opens as a plain file.
const seed = (edit) => edit;
for (const kind of ["product", "compilation", "docu", "job"]) {
  const requested = { id: "D:\\v.mp4", kind, outputPath: "D:\\v.mp4", displayName: "Video", seed, job: kind === "job" ? { id: "j" } : undefined };
  const opened = resolveTarget(requested, "file");
  assert.equal(opened.kind, "file", kind);
  assert.equal(opened.seed, undefined, `${kind}: no documentary first draft`);
  assert.equal(opened.job, undefined, `${kind}: no queue job`);
  assert.equal(opened.displayName, "Video");
  for (const mode of ["template", "subs"]) assert.equal(modeOffered(mode, opened.kind, true), false, `${kind} as a file: no ${mode}`);
  for (const mode of ["cut", "insert", "audio", "text", "cover"]) assert.equal(modeOffered(mode, opened.kind, false), true, `${kind} as a file: ${mode}`);
  // A recorded run keeps its kind.
  assert.equal(resolveTarget(requested, kind), requested);
}
assert.equal(modeOffered("template", "product", false), true);
assert.equal(modeOffered("subs", "job", true), true);
assert.equal(modeOffered("subs", "job", false), false);
assert.equal(modeOffered("subs", "docu", true), false);
// Issue #514: Tạo thêm (đóng gói, Shorts, đổi khung) for dubbing jobs only.
assert.equal(modeOffered("more", "job", false), true);
for (const kind of ["docu", "product", "compilation", "file"]) assert.equal(modeOffered("more", kind, true), false, `${kind}: no more`);
// Issue #510: Che for every video.
for (const kind of ["job", "docu", "product", "compilation", "file"]) assert.equal(modeOffered("cover", kind, false), true, `${kind}: cover`);
console.log("edit target tests passed");
