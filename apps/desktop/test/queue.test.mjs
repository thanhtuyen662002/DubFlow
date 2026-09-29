import assert from "node:assert/strict";
import { MemorySnapshotStorage, QueueController, parseQueueSnapshot } from "../src/features/queue/model.ts";
import { toQueueViewModel } from "../src/features/queue/view_model.ts";

const clock = () => "2026-09-29T02:30:00.000Z";
const storage = new MemorySnapshotStorage();
const queue = new QueueController(storage, clock);

const paths = Array.from({ length: 500 }, (_, index) => `C:/media/video-${index}.mp4`);
const added = queue.addPaths(paths);
assert.equal(added.length, 500);
assert.equal(queue.getSummary().total, 500);
assert.equal(queue.getSummary().queued, 500);
assert.equal(queue.addPaths(paths).length, 0);

const selected = queue.getSnapshot().selected_job_id;
assert.equal(selected, "job-1");
queue.selectJob("job-250");
assert.equal(queue.getSnapshot().selected_job_id, "job-250");

const restored = new QueueController(storage, clock);
assert.equal(restored.getSummary().total, 500);
assert.equal(restored.getSnapshot().selected_job_id, "job-250");

const view = toQueueViewModel(restored.getSnapshot());
assert.equal(view.rows.length, 500);
assert.equal(view.rows.filter((row) => row.selected).length, 1);
assert.equal(view.rows[249].progress, "0 units");

assert.equal(parseQueueSnapshot("{bad json"), null);
assert.equal(parseQueueSnapshot(JSON.stringify({ schema_version: 2, selected_job_id: null, jobs: [] })), null);
assert.equal(parseQueueSnapshot(JSON.stringify(restored.getSnapshot())).jobs.length, 500);

console.log("desktop queue tests passed");
