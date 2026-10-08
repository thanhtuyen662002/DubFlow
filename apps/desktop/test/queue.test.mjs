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
assert.equal(selected, added[0]);
queue.selectJob(added[249]);
assert.equal(queue.getSnapshot().selected_job_id, added[249]);

const restored = new QueueController(storage, clock);
assert.equal(restored.getSummary().total, 500);
assert.equal(restored.getSnapshot().selected_job_id, added[249]);

const view = toQueueViewModel(restored.getSnapshot());
assert.equal(view.rows.length, 500);
assert.equal(view.rows.filter((row) => row.selected).length, 1);
assert.equal(view.rows[249].progress, "0 units");

assert.equal(parseQueueSnapshot("{bad json"), null);
assert.equal(parseQueueSnapshot(JSON.stringify({ schema_version: 2, selected_job_id: null, jobs: [] })), null);
assert.equal(parseQueueSnapshot(JSON.stringify(restored.getSnapshot())).jobs.length, 500);

// Removing every row and reloading must not reuse an ID still owned by SQLite.
const emptyStorage = new MemorySnapshotStorage();
const original = new QueueController(emptyStorage, clock);
const [oldId] = original.addPaths(["C:/media/old.mp4"]);
original.removeJob(oldId);
const fresh = new QueueController(emptyStorage, clock);
const [newId] = fresh.addPaths(["C:/media/new.mp4"]);
assert.notEqual(newId, oldId);
assert.match(newId, /^job-[0-9a-f]{32}$/);
assert.equal(new QueueController(emptyStorage, clock).getSnapshot().jobs[0].id, newId);

// Historic IDs survive loading; new IDs do not derive from their row count.
const legacy = original.getSnapshot();
legacy.jobs = [{ ...fresh.getSnapshot().jobs[0], id: "job-1" }];
legacy.selected_job_id = "job-1";
const migrated = new QueueController(new MemorySnapshotStorage(JSON.stringify(legacy)), clock);
assert.equal(migrated.getSnapshot().jobs[0].id, "job-1");
assert.notEqual(migrated.addPaths(["C:/media/second.mp4"])[0], "job-1");

fresh.setDubbing(newId, { enabled: true, voiceId: "vi-truc-ly-vieneu3-v1" });
fresh.updateStatus(newId, { ...fresh.getSnapshot().jobs[0].status, state: "FAILED" }, "C:/outputs/original.mp4");
const retryId = fresh.retryJob(newId);
assert.notEqual(retryId, newId);
assert.equal(fresh.getSnapshot().jobs[0].status.state, "FAILED");
assert.equal(fresh.getSnapshot().jobs[0].outputPath, "C:/outputs/original.mp4");
assert.deepEqual(fresh.getSnapshot().jobs[1].dubbing, { enabled: true, voiceId: "vi-truc-ly-vieneu3-v1" });
assert.equal(fresh.getSnapshot().jobs[1].status.state, "QUEUED");
assert.equal(fresh.getSnapshot().jobs[1].outputPath, null);

const colliding = new QueueController(new MemorySnapshotStorage(), clock, 100, () => "collision");
colliding.addPaths(["one"]);
assert.throws(() => colliding.addPaths(["two"]), /independent job ID/);
assert.equal(colliding.getSnapshot().jobs.length, 1);

console.log("desktop queue tests passed");
