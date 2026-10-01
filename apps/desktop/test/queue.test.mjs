import assert from "node:assert/strict";
import { MemorySnapshotStorage, QueueController, parseQueueSnapshot } from "../src/features/queue/model.ts";
import { toQueueViewModel } from "../src/features/queue/view_model.ts";
import { parseSourceInput, supervisorSourceItems, sourceIntakeLabel } from "../src/features/source_intake/model.ts";

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

const sources = parseSourceInput([
  "C:\\Media\\Demo Video.mp4",
  "https://www.youtube.com/watch?v=video-id&token=secret-token",
  "https://www.youtube.com/watch?v=video-id&token=secret-token",
  "https://user:password@example.com/private.mp4",
  "notaurl",
].join("\n"));
assert.equal(sources.items.length, 2);
assert.equal(sources.items[0].kind, "local");
assert.equal(sources.items[1].provider, "youtube");
assert.equal(sources.items[1].url?.includes("secret-token"), false);
assert.equal(sources.items[1].transport_url?.includes("secret-token"), true);
assert.equal(sources.rejected.filter((item) => item.code === "DUPLICATE_SOURCE").length, 1);
assert.equal(sources.rejected.some((item) => item.code === "URL_INVALID"), true);
assert.equal(supervisorSourceItems(sources)[1].source_url.includes("secret-token"), false);
assert.equal(sourceIntakeLabel(sources.items[1]), "Youtube");

console.log("desktop source intake tests passed");
