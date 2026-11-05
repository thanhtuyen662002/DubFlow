import assert from "node:assert/strict";
import {
  addMarker, addOverlay, copySegment, createEdit, duplicateSegment, keepRange, moveSegment, nearbyPoint, outputDuration, outputStarts,
  parseProject, pasteSegment, removeMarker, removeRange, serializeProject, setMarker, setSpeed, setTransition, setVolume, snapPoints, snapTime, split,
} from "../src/features/edit/model.ts";

const close = (actual, expected, message, tolerance = 1e-6) => assert.ok(Math.abs(actual - expected) < tolerance, `${message ?? ""} ${actual} != ${expected}`);
const ids = (edit) => edit.segments.map((segment) => segment.id);
const clipInfo = { path: "D:\\b.mp4", title: "b", duration: 10, width: 1920, height: 1080, fps: 30, hasAudio: true, kind: "file" };

// Reorder: s1 0-40, s2 40-70, s3 70-100.
const three = split(split(createEdit(100, { path: "D:\\a.mp4" }), 40), 70);
const withJoints = setTransition(setTransition(three, "s1", "fade"), "s2", "dissolve");
const moved = moveSegment(withJoints, "s3", 0);
assert.deepEqual(ids(moved), ["s3", "s1", "s2"]);
assert.deepEqual(Object.keys(moved.transitions), ["s1"], "only the joints that now join other pictures lose transitions");
assert.deepEqual(Object.keys(moveSegment(withJoints, "s1", 1).transitions), [], "moving s1 after s2 touches both joints");
assert.equal(moveSegment(three, "s2", 1), three, "same place is a no-op");
assert.equal(moveSegment(three, "nope", 0), three);
assert.deepEqual(ids(moveSegment(three, "s1", 99)), ["s2", "s3", "s1"], "clamped to the end");
close(outputDuration(moved), 100);

// Duplicate: the copy follows; overlays after it ripple; the joint moves to the copy.
const overlaid = addOverlay(setTransition(three, "s2", "fade"), clipInfo, 80);
const doubled = duplicateSegment(overlaid, "s2");
assert.equal(doubled.segments.length, 4);
assert.deepEqual(doubled.segments[2].start, 40);
assert.deepEqual(doubled.segments[2].end, 70);
assert.ok(!doubled.transitions.s2 && doubled.transitions[doubled.segments[2].id], "transition moves to the copy");
close(outputDuration(doubled), 130);
close(doubled.overlays[0].at, 110, "overlay after the joint ripples");

// Copy and paste: speed and volume come along; a main part pastes as main.
const styled = setVolume(setSpeed(three, "s2", 2), "s2", 0.5);
const clip = copySegment(styled, "s2");
assert.equal(clip.speed, 2);
const pasted = pasteSegment(styled, 0, clip);
assert.equal(pasted.segments[0].mediaId, "main");
assert.equal(pasted.segments[0].speed, 2);
assert.equal(pasted.segments[0].volume, 0.5);
assert.equal(pasted.media.length, 1, "no extra media for the main video");
close(outputDuration(pasted), outputDuration(styled) + 15);
// Into another video: the main part becomes an inserted clip.
const other = createEdit(50, { path: "D:\\other.mp4" });
const across = pasteSegment(other, 50, { ...clip, media: { ...clip.media, path: "D:\\a.mp4", duration: 100 } }, "D:\\other.mp4");
assert.equal(across.segments.length, 2);
assert.notEqual(across.segments[1].mediaId, "main");
assert.equal(across.media[1].kind, "file");

// Range: cut 30–80 out of 0–100 (three segments): 50 s left.
const cutRange = removeRange(three, 30, 80);
close(outputDuration(cutRange), 50);
assert.deepEqual(cutRange.segments.map((segment) => [segment.start, segment.end]), [[0, 30], [80, 100]]);
assert.equal(removeRange(three, 0, 100), three, "something must stay");
assert.equal(removeRange(three, 10, 10.05), three, "too short");
const kept = keepRange(three, 30, 80);
close(outputDuration(kept), 50);
assert.deepEqual(kept.segments.map((segment) => [segment.start, segment.end]), [[30, 40], [40, 70], [70, 80]]);

// Markers: sorted, one per spot, renamed, moved, saved and parsed back.
let marked = addMarker(three, 50, "Hook");
marked = addMarker(marked, 10);
assert.deepEqual(marked.markers.map((marker) => marker.at), [10, 50]);
assert.equal(addMarker(marked, 50.01), marked, "one marker per spot");
const renamed = setMarker(marked, marked.markers[1].id, { label: "Cao\ntrào", at: 5 });
assert.deepEqual(renamed.markers.map((marker) => [marker.at, marker.label]), [[5, "Cao trào"], [10, ""]]);
assert.equal(removeMarker(renamed, renamed.markers[0].id).markers.length, 1);
const back = parseProject(serializeProject(renamed, 3, null), 100, { path: "D:\\a.mp4" });
assert.deepEqual(back.edit.markers, renamed.markers);
assert.ok(back.edit.nextId > Math.max(...renamed.markers.map((marker) => Number(marker.id.slice(1)))));
const old = serializeProject(three, 0, null);
delete old.markers;
assert.deepEqual(parseProject(old, 100).edit.markers, [], "drafts without markers still open");

// Snapping and edit points.
const points = snapPoints(marked);
assert.deepEqual(points, [0, 10, 40, 50, 70, 100]);
assert.equal(snapTime(41, points, 1.5), 40);
assert.equal(snapTime(45, points, 1.5), 45);
assert.equal(nearbyPoint(marked, 41, 1), 50);
assert.equal(nearbyPoint(marked, 41, -1), 40);
assert.equal(nearbyPoint(marked, 0, -1), 0);
close(outputStarts(three)[2], 70);
console.log("edit timeline tests passed");
