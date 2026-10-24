import assert from "node:assert/strict";
import { MemorySnapshotStorage, QueueController } from "../src/features/queue/model.ts";
import { createSourceEventPump, enqueueMaterializedSources, materializedSource } from "../src/features/source_intake/materialized.ts";
const path = "\\\\?\\D:\\Dữ liệu nguồn\\Cảnh, phần 1.mp4";
const receipt = { sequence: 9, value: { event: "source_downloaded", scan_id: "scan-1", identity_key: "generic:scene,1",
  media_path: path, sha256: "a".repeat(64), size_bytes: 1621 } };
const storage = new MemorySnapshotStorage();
const queue = new QueueController(storage);
const first = enqueueMaterializedSources([
  { sequence: 1, value: { event: "source_ready", schema_version: 1, single_writer: true } },
  { sequence: 2, value: { event: "source_preparing", scan_id: "scan-1" } },
  { sequence: 3, value: { event: "source_status", status: "completed", media_path: path, sha256: "a".repeat(64) } },
  { sequence: 4, value: { event: "source_items", items: [{ status: "downloaded", media_path: path }] } },
  { sequence: 5, value: { event: "source_download_progress", media_path: path } }, receipt,
], queue);
assert.equal(first.queued.length, 1); assert.equal(first.already_present.length, 0); assert.equal(first.rejected.length, 0);
assert.equal(queue.getSnapshot().jobs.length, 1); assert.equal(queue.getSnapshot().jobs[0].sourcePath, path);
assert.equal(materializedSource(receipt).receipt_key, JSON.stringify(["scan-1", "generic:scene,1", "a".repeat(64), path]));
assert.equal(enqueueMaterializedSources([receipt, receipt], queue).already_present.length, 2);
const reopened = new QueueController(storage);
assert.equal(enqueueMaterializedSources([receipt], reopened).already_present.length, 1);
assert.equal(reopened.getSnapshot().jobs.length, 1);

const bad = [
  { ...receipt, value: { ...receipt.value, event: "source_preparing" } }, // ignored
  { ...receipt, value: { ...receipt.value, sha256: "not-a-hash" } },
  { ...receipt, value: { ...receipt.value, size_bytes: 0 } },
  { ...receipt, value: { ...receipt.value, media_path: "relative.mp4" } },
  { ...receipt, value: { ...receipt.value, media_path: " " + path } },
  { ...receipt, value: { ...receipt.value, identity_key: "ệ".repeat(342) } },
  { ...receipt, sequence: 0 },
];
const secondReceipt = { ...receipt, sequence: 10, value: { ...receipt.value, media_path: "D:/Dữ liệu/cảnh khác.mp4" } };
const mixed = enqueueMaterializedSources([...bad, secondReceipt], reopened);
assert.equal(mixed.rejected.length, 6); assert.equal(mixed.queued.length, 1); assert.equal(reopened.getSummary().total, 2);

const limited = new QueueController(new MemorySnapshotStorage(), undefined, 1);
const capacity = enqueueMaterializedSources([receipt, secondReceipt], limited);
assert.equal(capacity.queued.length, 1); assert.equal(capacity.rejected[0].code, "LOCAL_QUEUE_REJECTED");
limited.removeJob(limited.getSnapshot().jobs[0].id);
assert.equal(enqueueMaterializedSources([secondReceipt], limited).queued.length, 1); // no poisoned delivered flag
let calls = 0, finish;
const pumping = new QueueController(new MemorySnapshotStorage());
const pump = createSourceEventPump(pumping, (command) => {
  assert.equal(command, "source_service_events_v1"); calls++;
  return new Promise((resolve) => { finish = resolve; });
});
const waitA = pump.poll(), waitB = pump.poll();
assert.equal(waitA, waitB); assert.equal(calls, 1);
pumping.addPaths(["D:/local still available.mp4"]);
assert.equal(pumping.getSummary().total, 1);
finish([receipt]);
assert.equal((await waitA).materialized.queued.length, 1);
assert.equal(pumping.getSummary().total, 2);
const replay = pump.poll(); assert.equal(calls, 2); finish([receipt]);
assert.equal((await replay).materialized.already_present.length, 1);
assert.equal(pumping.getSummary().total, 2);
if (!process.argv.includes("--wire")) console.log("source materialized hook: PASS (native-event-only, preserved path, durable path dedup, per-item queue errors)");
