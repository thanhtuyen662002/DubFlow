import assert from "node:assert/strict";
import { clampSize, dragResult, growSign, keyResult, parsePanelState, SNAP_FOLD_PX } from "../src/shell/panels.ts";

// Stored state: missing or broken values fall back to the default.
assert.deepEqual(parsePanelState(null), { size: null, collapsed: false });
assert.deepEqual(parsePanelState("{not json"), { size: null, collapsed: false });
assert.deepEqual(parsePanelState('{"size":300.4,"collapsed":true}'), { size: 300, collapsed: true });
assert.deepEqual(parsePanelState('{"size":-5,"collapsed":"yes"}'), { size: null, collapsed: false });

// Sizes stay within the limits; a max under the min keeps the min.
assert.equal(clampSize(100, 200, 500), 200);
assert.equal(clampSize(900, 200, 500), 500);
assert.equal(clampSize(300, 200, 150), 200);

// A handle on the right edge grows with the pointer moving right; on the left edge, moving left.
assert.equal(growSign("right"), 1);
assert.equal(growSign("bottom"), 1);
assert.equal(growSign("left"), -1);
assert.equal(growSign("top"), -1);
assert.deepEqual(dragResult("right", 284, 40, 200, 560, true), { size: 324, collapsed: false });
assert.deepEqual(dragResult("left", 344, 40, 280, 640, true), { size: 304, collapsed: false });
assert.deepEqual(dragResult("top", 170, -100, 110, 500, true), { size: 270, collapsed: false });
// Pulled well under the smallest size: folds; not foldable: stops at the smallest size.
assert.deepEqual(dragResult("right", 284, -(284 - 200 + SNAP_FOLD_PX + 1), 200, 560, true), { size: null, collapsed: true });
assert.deepEqual(dragResult("right", 284, -400, 200, 560, false), { size: 200, collapsed: false });
// A small pull under the minimum just holds the minimum.
assert.deepEqual(dragResult("right", 284, -100, 200, 560, true), { size: 200, collapsed: false });

// Keyboard: arrows step 16 (64 with Shift) the way the panel grows; Home/End; Enter folds.
assert.equal(keyResult("ArrowRight", false, "right", 284, 200, 560), 300);
assert.equal(keyResult("ArrowRight", false, "left", 344, 280, 640), 328);
assert.equal(keyResult("ArrowLeft", true, "left", 344, 280, 640), 408);
assert.equal(keyResult("ArrowUp", false, "top", 170, 110, 500), 186);
assert.equal(keyResult("ArrowDown", false, "bottom", 260, 140, 400), 276);
assert.equal(keyResult("Home", false, "right", 284, 200, 560), 200);
assert.equal(keyResult("End", false, "right", 284, 200, 560), 560);
assert.equal(keyResult("Enter", false, "right", 284, 200, 560), "toggle");
assert.equal(keyResult("ArrowUp", false, "right", 284, 200, 560), null);
assert.equal(keyResult("a", false, "top", 284, 200, 560), null);

console.log("panels tests passed");
