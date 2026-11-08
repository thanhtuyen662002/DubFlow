import assert from "node:assert/strict";
import {
  changedCuts, changedFlags, changedScenes, cutAt, cutEnd, cutLength, cutsFrom, edgeRange, firstOfScene, removeCut, replaceFootage, setCutCover,
  slicesRequest, swapCuts, trimEdge,
} from "../src/features/edit/docu_cuts.ts";

// Issue #630: every planned slice of a documentary as its own cut.
const status = { plan: { duration: 12, sources: [{ id: "src_01", duration: 20 }, { id: "src_02", duration: 5 }], scenes: [
  { scene_id: "sc_001", start: 0, end: 8, slices: [
    { source_id: "src_01", start: 1, end: 5, at: 0, tier: 0 },
    { source_id: "src_02", start: 0, end: 4, at: 4, tier: 0, cover: { band: { top: 0.8, bottom: 0.9, share: 1 }, logos: [[0.1, 0.1, 0.2, 0.2]] } }] },
  { scene_id: "sc_002", start: 8, end: 12, slices: [{ source_id: "src_01", start: 10, end: 14, at: 8, tier: 0, kind: "map", ref: "g1" }] },
] } };
const plan = cutsFrom(status);
assert.equal(plan.cuts.length, 3);
assert.deepEqual(plan.cuts[0], { scene: "sc_001", from: ["sc_001", 0], source: "src_01", start: 1, at: 0, kind: null, cover: null });
assert.deepEqual(plan.cuts[1].cover, { band: { top: 0.8, bottom: 0.9 }, logos: [[0.1, 0.1, 0.2, 0.2]] });
assert.equal(plan.cuts[2].kind, "map");
assert.equal(cutsFrom({ plan: null }), null);
assert.equal(cutsFrom({ plan: { sources: [], scenes: [] } }), null);
assert.equal(cutEnd(plan, 0), 4);
assert.equal(cutEnd(plan, 1), 8, "the last cut of a scene ends with it");
assert.equal(cutLength(plan, 2), 4);
assert.equal(cutAt(plan, 5), 1);
assert.equal(cutAt(plan, 8), 2);
assert.ok(firstOfScene(plan, 0) && !firstOfScene(plan, 1) && firstOfScene(plan, 2));

// Trim: only inside the scene, neither cut shorter than 0.5 s or longer than its footage (src_02 is 5 s).
assert.equal(edgeRange(plan, 0), null);
assert.equal(edgeRange(plan, 2), null, "a scene's first cut follows the narration");
assert.deepEqual(edgeRange(plan, 1), { min: 3, max: 7.5 });
const trimmed = trimEdge(plan, 1, 6);
assert.equal(trimmed.cuts[1].at, 6);
assert.equal(trimmed.cuts[1].start, 2, "the footage stays where it was in the film");
assert.equal(cutLength(trimmed, 0), 6);
assert.equal(trimEdge(plan, 1, 1).cuts[1].at, 3, "kept in range");
assert.equal(trimEdge(plan, 0, 3), plan, "no left edge to move");
assert.equal(plan.cuts[1].at, 4, "the plan itself is not changed");

// Swap: each keeps its place and length; the footage (and its own cover) moves.
const swapped = swapCuts(plan, 0, 1);
assert.equal(swapped.cuts[0].source, "src_02");
assert.equal(swapped.cuts[0].at, 0);
assert.deepEqual(swapped.cuts[0].from, ["sc_001", 1]);
assert.ok(swapped.cuts[0].cover && !swapped.cuts[1].cover);
assert.equal(swapCuts(trimmed, 0, 1), null, "6 s of slot is longer than the 5 s source");
assert.equal(swapCuts(plan, 1, 1), null);
const map = swapCuts(plan, 0, 2);
assert.equal(map.cuts[0].kind, "map");

// Remove: the cut before takes the time; a scene's first cut gives it to the next; a scene's only cut stays.
const removed = removeCut(plan, 1);
assert.equal(removed.cuts.length, 2);
assert.equal(cutLength(removed, 0), 8);
const first = removeCut(plan, 0);
assert.equal(first, null, "src_02 (5 s) cannot fill the 8 s scene");
const longer = removeCut(replaceFootage(plan, 1, "src_01", 12), 0);
assert.equal(longer.cuts[0].at, 0);
assert.equal(longer.cuts[0].start, 8, "starts earlier in its footage");
assert.equal(removeCut(plan, 2), null);

// Replace: other footage, kept inside its source; a generated picture becomes footage.
const replaced = replaceFootage(plan, 0, "src_01", 19);
assert.equal(replaced.cuts[0].start, 16);
assert.equal(replaceFootage(plan, 2, "src_01", 0).cuts[2].kind, null);
assert.equal(replaceFootage(plan, 0, "src_09", 0), null);
assert.equal(replaceFootage(trimmed, 0, "src_02", 0), null);

// Own cover, the changed scenes and the request.
const covered = setCutCover(trimmed, 0, { band: null, logos: [[0.5, 0.5, 0.1, 0.1]] });
assert.deepEqual(changedScenes(plan, covered), ["sc_001"]);
assert.equal(changedCuts(plan, covered), 2);
assert.equal(changedCuts(plan, removed), 2, "one cut removed, the one before it longer");
assert.deepEqual(changedFlags(plan, removed), [true, false]);
assert.deepEqual(changedScenes(plan, plan), []);
assert.deepEqual(slicesRequest(covered, ["sc_001"]), [{ scene_id: "sc_001", slices: [
  { from: ["sc_001", 0], source_id: "src_01", start: 1, at: 0, cover: { band: null, logos: [[0.5, 0.5, 0.1, 0.1]] } },
  { from: ["sc_001", 1], source_id: "src_02", start: 2, at: 6, cover: { band: { top: 0.8, bottom: 0.9 }, logos: [[0.1, 0.1, 0.2, 0.2]] } }] }]);
assert.equal(setCutCover(covered, 0, null).cuts[0].cover, null);

console.log("docu_cuts tests passed");
