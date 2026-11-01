import assert from "node:assert/strict";
import {
  createEdit, defaultExportName, EditHistory, estimateExportBytes, exportSize, formatClock, formatSpeed, HISTORY_LIMIT,
  isUnchanged, outputDuration, outputStarts, outputToSource, parseClock, parseProject, remapCues, remove, sameSegments,
  serializeProject, setSpeed, setVolume, sourceToOutput, split, thumbGrid, tickStep, trim, zoomScale,
} from "../src/features/edit/model.ts";

const close = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1e-6, `${message ?? ""} ${actual} != ${expected}`);

// A new edit is the whole video once.
const base = createEdit(100);
assert.equal(base.segments.length, 1);
assert.deepEqual(base.segments[0], { id: "s1", start: 0, end: 100, speed: 1, volume: 1 });
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
  schema_version: 1, duration: 100, next_id: 4, changes: 4, selected: "s3",
  segments: [{ id: "s1", start: 0, end: 40, speed: 1, volume: 1 }, { id: "s3", start: 70, end: 100, speed: 1.5, volume: 1 }],
});
const parsed = parseProject(saved, 100.2);
assert.equal(parsed.changes, 4);
assert.equal(parsed.selected, "s3");
assert.equal(parsed.edit.nextId, 4);
assert.equal(parsed.edit.duration, 100.2);
assert.ok(sameSegments(parsed.edit.segments, fast.segments));
assert.equal(parseProject(saved, 120), null, "a different render");
assert.equal(parseProject({ ...saved, schema_version: 2 }, 100), null);
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

console.log("edit tests passed");
