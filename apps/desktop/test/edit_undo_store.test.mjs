// Issue #896: Hoàn tác survives closing the editor and restarting DubFlow.
import assert from "node:assert/strict";
import { EditHistory, createEdit, sameEdit, serializeProject, split, parseProject } from "../src/features/edit/model.ts";
import { KEPT_BYTES, KEPT_FUTURE, KEPT_PAST, packHistory, unpackHistory } from "../src/features/edit/undo_store.ts";

const info = { path: "D:\\v.mp4", title: "v", width: 1920, height: 1080, fps: 30, hasAudio: true };
const base = createEdit(120, info);

// Ten edits, two undone: the draft keeps eight steps back and two forward.
const history = new EditHistory(base, 0);
let edit = base;
for (let cut = 1; cut <= 10; cut += 1) {
  edit = split(edit, cut * 10);
  history.push(edit);
}
history.undo();
history.undo();
assert.equal(history.index, 8);
const packed = packHistory(history.states, history.index);
assert.equal(packed.past.length, 8);
assert.equal(packed.future.length, 2);

// Saved through JSON with the draft, then read back on open (as after a restart).
const draft = JSON.parse(JSON.stringify({ ...serializeProject(history.current, 8, null), undo: packed }));
const opened = parseProject(draft, 120, info);
const kept = unpackHistory(draft.undo, 120, info);
const restored = new EditHistory(opened.edit, opened.changes);
restored.restore(kept.past, opened.edit, kept.future, opened.changes);
assert.equal(restored.index, 8);
assert.equal(restored.states.length, 11);
assert.equal(restored.changes, 8, "the change count is the draft's");
for (let step = 0; step < 11; step += 1) assert.ok(sameEdit(restored.states[step], history.states[step]), `step ${step} is the same edit`);
assert.equal(restored.current, opened.edit, "the draft's own edit stays current");
// Undo all the way back, then redo past the draft.
let undone = 0;
while (restored.undo()) undone += 1;
assert.equal(undone, 8);
assert.equal(restored.current.segments.length, 1, "back to the uncut video");
assert.equal(restored.changes, 1, "still unexported, never shown as exported");
for (let step = 0; step < 10; step += 1) restored.redo();
assert.equal(restored.current.segments.length, 11);
assert.equal(restored.redo(), null);

// No steps: nothing saved; a draft with nothing nor junk opens with an empty history.
assert.equal(packHistory([base], 0), null);
assert.deepEqual(unpackHistory(undefined, 120, info), { past: [], future: [] });
assert.deepEqual(unpackHistory({ past: "x", future: [3] }, 120, info), { past: [], future: [] });
// A step that no longer reads (another video's length) stops the chain there; the nearer ones stay.
const broken = { past: [packed.past[0], { ...packed.past[1], duration: 999 }, packed.past[2]], future: [] };
assert.equal(unpackHistory(broken, 120, info).past.length, 1);

// Limits: at most KEPT_PAST back, KEPT_FUTURE forward, and the byte budget.
const long = new EditHistory(base, 0);
edit = base;
for (let cut = 1; cut <= 60; cut += 1) {
  edit = split(edit, cut * 1.9);
  long.push(edit);
}
for (let step = 0; step < 15; step += 1) long.undo();
const kept2 = packHistory(long.states, long.index);
assert.ok(kept2.past.length <= KEPT_PAST && kept2.future.length <= KEPT_FUTURE);
assert.ok(JSON.stringify(kept2).length <= KEPT_BYTES + 100);
assert.ok(sameEdit(unpackHistory(kept2, 120, info).past.at(-1), long.states[long.index - 1]), "the nearest step back is kept first");

console.log("edit undo store tests passed");
