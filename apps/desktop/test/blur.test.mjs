import assert from "node:assert/strict";
import { DEFAULT_REGION, MIN_SIZE, clampRegion, describeRegions, dragRegion, isBlurRegion, isBlurRegionList } from "../src/features/blur/region.ts";
import { MemorySnapshotStorage, QueueController, parseQueueSnapshot } from "../src/features/queue/model.ts";

const near = (actual, expected) => {
  for (const key of ["x", "y", "w", "h"]) assert.ok(Math.abs(actual[key] - expected[key]) < 1e-9, `${key}: ${actual[key]} != ${expected[key]}`);
};
const start = { x: 0.1, y: 0.8, w: 0.8, h: 0.1 };

// Pulling the two side edges toward the middle narrows the box symmetrically.
let region = dragRegion(start, "w", 0.2, 0);
region = dragRegion(region, "e", -0.2, 0);
near(region, { x: 0.3, y: 0.8, w: 0.4, h: 0.1 });

// Moving keeps the size and stops at the frame border.
near(dragRegion(start, "move", 0.5, 0.5), { x: 0.2, y: 0.9, w: 0.8, h: 0.1 });
near(dragRegion(start, "move", -1, -1), { x: 0, y: 0, w: 0.8, h: 0.1 });

// An edge never crosses the opposite one or leaves the frame.
near(dragRegion(start, "n", 0.5, 0.5), { x: 0.1, y: 0.9 - MIN_SIZE, w: 0.8, h: MIN_SIZE });
near(dragRegion(start, "se", 1, 1), { x: 0.1, y: 0.8, w: 0.9, h: 0.2 });
near(dragRegion(start, "nw", -1, -1), { x: 0, y: 0, w: 0.9, h: 0.9 });
near(clampRegion({ x: 0.95, y: -0.2, w: 0.3, h: 0.001 }), { x: 0.7, y: 0, w: 0.3, h: MIN_SIZE });

assert.ok(isBlurRegion(DEFAULT_REGION));
assert.ok(!isBlurRegion({ x: 0.5, y: 0.5, w: 0.6, h: 0.1 }));
assert.ok(!isBlurRegion({ x: "0.1", y: 0.5, w: 0.2, h: 0.1 }));
assert.ok(!isBlurRegionList([]));
assert.ok(!isBlurRegionList(Array(17).fill(DEFAULT_REGION)));
assert.equal(describeRegions(undefined), "Tắt");
assert.equal(describeRegions([start]), "Vùng dưới, rộng 80% × cao 10%");
assert.equal(describeRegions([start, { x: 0, y: 0, w: 0.2, h: 0.1 }]), "2 vùng");

// Queue: per job, for every waiting job, persisted, copied to revisions, locked once processing starts.
const clock = () => "2026-10-25T00:00:00.000Z";
const queue = new QueueController(new MemorySnapshotStorage(), clock);
const [first, second, third] = queue.addPaths(["C:/a.mp4", "C:/b.mp4", "C:/c.mp4"]);
queue.setBlurRegions(first, [start]);
assert.deepEqual(queue.getSnapshot().jobs[0].blurRegions, [start]);
assert.throws(() => queue.setBlurRegions(first, [{ x: 0.9, y: 0.9, w: 0.5, h: 0.5 }]), /Invalid blur/);
queue.updateStatus(third, { ...queue.getSnapshot().jobs[2].status, state: "RUNNING" });
assert.equal(queue.setBlurRegionsForQueued([region]), 2);
assert.deepEqual(queue.getSnapshot().jobs.map((job) => job.blurRegions), [[region], [region], undefined]);
assert.throws(() => queue.setBlurRegions(third, [start]), /before processing/);
assert.deepEqual(parseQueueSnapshot(JSON.stringify(queue.getSnapshot())).jobs[1].blurRegions, [region]);
assert.equal(parseQueueSnapshot(JSON.stringify({ ...queue.getSnapshot(), jobs: [{ ...queue.getSnapshot().jobs[0], blurRegions: [{ x: 2 }] }] })), null);
queue.setBlurRegions(second, null);
assert.equal(queue.getSnapshot().jobs[1].blurRegions, undefined);
queue.updateStatus(first, { ...queue.getSnapshot().jobs[0].status, state: "COMPLETED" });
const revision = queue.reviseJob(first, "D:/Out/rev");
assert.deepEqual(queue.getSnapshot().jobs.find((job) => job.id === revision).blurRegions, [region]);

console.log("desktop blur region geometry and queue tests passed");
