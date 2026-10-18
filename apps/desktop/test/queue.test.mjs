import assert from "node:assert/strict";
import { MemorySnapshotStorage, QueueController, parseQueueSnapshot } from "../src/features/queue/model.ts";
import { toQueueViewModel } from "../src/features/queue/view_model.ts";
import { importPackMessage, parseImportPackStatus } from "../src/features/import_pack/model.ts";

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

// A finished export can be voiced again without modifying its existing row.
fresh.updateStatus(retryId, { ...fresh.getSnapshot().jobs[1].status, state: "COMPLETED" }, "C:/outputs/truc-ly.mp4");
const completed = structuredClone(fresh.getSnapshot().jobs[1]);
const variantId = fresh.retryJob(retryId);
fresh.setDubbing(variantId, { enabled: true, voiceId: "vi-thai-son-vieneu3-v1" });
assert.deepEqual(fresh.getSnapshot().jobs[1], completed);
const loadedVariants = new QueueController(emptyStorage, clock).getSnapshot();
assert.deepEqual(loadedVariants.jobs[1], completed);
assert.equal(loadedVariants.jobs[2].dubbing.voiceId, "vi-thai-son-vieneu3-v1");
assert.equal(loadedVariants.selected_job_id, variantId);
assert.equal(loadedVariants.jobs[2].sourcePath, completed.sourcePath);
assert.equal(loadedVariants.jobs[2].outputPath, null);

const colliding = new QueueController(new MemorySnapshotStorage(), clock, 100, () => "collision");
colliding.addPaths(["one"]);
assert.throws(() => colliding.addPaths(["two"]), /independent job ID/);
assert.equal(colliding.getSnapshot().jobs.length, 1);

const verifiedPack = parseImportPackStatus({ schema_version: 1, state: "available", path: "D:/output/portable pack",
  manifest_sha256: `sha256:${"a".repeat(64)}`, capcut: { reason: "CAPCUT_VERSION_UNSUPPORTED" } });
assert.equal(importPackMessage(verifiedPack), "D:/output/portable pack");
assert.equal(verifiedPack.capcutReason, "CAPCUT_VERSION_UNSUPPORTED");
assert.throws(() => parseImportPackStatus({ schema_version: 1, state: "available", path: "../other", manifest_sha256: `sha256:${"a".repeat(64)}` }));
assert.throws(() => parseImportPackStatus({ schema_version: 1, state: "available", path: "D:/output", manifest_sha256: "not verified" }));
assert.throws(() => parseImportPackStatus({ schema_version: 2, state: "unavailable", reason: "unknown" }));
assert.equal(parseImportPackStatus({ schema_version: 1, state: "unavailable", reason: "LEGACY_EXPORT_WITHOUT_IMPORT_PACK" }).state, "unavailable");
assert.match(importPackMessage({ state: "unavailable", reason: "corrupt" }), /Video và phụ đề/);

// Comparison only: no filesystem read/resolve or stored-path rewrite.
const pastedDos = "D:/DubFlowQA/real-film-portrait-input-20261010/sintel-dialogue-portrait.mp4";
const pickedDos = "D:\\DubFlowQA\\real-film-portrait-input-20261010\\sintel-dialogue-portrait.mp4";
let dosId = 0;
const dosIds = () => "dos-memory-" + (++dosId);
const dosStorage = new MemorySnapshotStorage();
const dosQueue = new QueueController(dosStorage, clock, 100, dosIds);
const [dosOriginalId] = dosQueue.addPaths([pastedDos]);
dosQueue.setDubbing(dosOriginalId, { enabled: true, voiceId: "vi-truc-ly-vieneu3-v1" });
dosQueue.selectJob(dosOriginalId);
dosQueue.updateStatus(dosOriginalId, { ...dosQueue.getSnapshot().jobs[0].status, state: "COMPLETED" }, "D:/memory/prior-output.mp4");
const beforeDosDuplicate = dosQueue.getSnapshot();
const beforeDosStorage = dosStorage.read();
assert.deepEqual(dosQueue.addPaths([pickedDos]), [], "same DOS-drive path with alternate separators must not add a row");
assert.deepEqual(dosQueue.getSnapshot(), beforeDosDuplicate);
assert.equal(dosStorage.read(), beforeDosStorage);

const reopenedDos = new QueueController(dosStorage, clock, 100, dosIds);
assert.deepEqual(reopenedDos.addPaths([pickedDos, pastedDos]), []);
assert.deepEqual(reopenedDos.getSnapshot(), beforeDosDuplicate);
assert.equal(dosStorage.read(), beforeDosStorage);
const dosRetry = reopenedDos.retryJob(dosOriginalId); // Explicit retry intentionally remains a new job.
assert.notEqual(dosRetry, dosOriginalId);
assert.deepEqual(reopenedDos.getSnapshot().jobs[0], beforeDosDuplicate.jobs[0]);
assert.equal(reopenedDos.getSnapshot().selected_job_id, dosRetry);
assert.equal(reopenedDos.getSnapshot().jobs[1].sourcePath, pastedDos);
assert.equal(reopenedDos.getSnapshot().jobs[1].outputPath, null);
assert.deepEqual(reopenedDos.getSnapshot().jobs[1].dubbing, beforeDosDuplicate.jobs[0].dubbing);
assert.deepEqual(reopenedDos.addPaths([pickedDos]), []);
assert.equal(reopenedDos.getSnapshot().jobs.length, 2);

for (const [firstPath, secondPath] of [
  [pastedDos, pickedDos], [pickedDos, pastedDos],
  ["D:/Dữ liệu nguồn/Cảnh, phần 1.mp4", "D:\\Dữ liệu nguồn\\Cảnh, phần 1.mp4"],
  ["E:\\mixed/parts\\clip.mp4", "E:/mixed\\parts/clip.mp4"],
]) {
  const batch = new QueueController(new MemorySnapshotStorage(), clock, 100, dosIds);
  assert.equal(batch.addPaths([firstPath, secondPath]).length, 1);
  assert.equal(batch.getSnapshot().jobs[0].sourcePath, firstPath);
}
const fullDos = new QueueController(new MemorySnapshotStorage(), clock, 1, dosIds);
fullDos.addPaths([pickedDos]);
assert.deepEqual(fullDos.addPaths([pastedDos]), []); // Duplicate is harmless even at capacity.

// Pre-existing duplicates are historical rows, never silently merged/migrated.
const legacyDos = structuredClone(beforeDosDuplicate);
legacyDos.jobs.push({ ...structuredClone(legacyDos.jobs[0]), id: "legacy-dos-duplicate",
  sourcePath: pickedDos, outputPath: "D:/memory/other-prior-output.mp4" });
const legacyDosStorage = new MemorySnapshotStorage(JSON.stringify(legacyDos));
const preservedDos = new QueueController(legacyDosStorage, clock, 100, dosIds);
assert.deepEqual(preservedDos.getSnapshot(), legacyDos);
assert.deepEqual(preservedDos.addPaths([pastedDos, pickedDos]), []);
assert.deepEqual(preservedDos.getSnapshot(), legacyDos);
assert.deepEqual(JSON.parse(legacyDosStorage.read()), legacyDos);

// No authority over case, relative/dot paths, UNC/verbatim, URLs or POSIX aliases.
for (const distinct of [
  ["D:/one.mp4", "D:/two.mp4"],
  ["D:/one.mp4", "E:/one.mp4"],
  ["D:/Case/one.mp4", "D:/case/one.mp4"],
  ["D:/one.mp4", "d:/one.mp4"],
  ["D:relative/one.mp4", "D:relative\\one.mp4"],
  ["D:/folder/./one.mp4", "D:/folder/one.mp4"],
  ["D:/folder/../one.mp4", "D:/one.mp4"],
  ["D:/folder/./one.mp4", "D:\\folder\\.\\one.mp4"],
  ["D:/folder//one.mp4", "D:\\folder\\\\one.mp4"],
  ["D:/folder/one.mp4/", "D:\\folder\\one.mp4\\"],
  ["/media/folder/one.mp4", "\\media\\folder\\one.mp4"],
  ["\\\\server\\share\\one.mp4", "//server/share/one.mp4"],
  ["\\\\?\\D:\\folder\\one.mp4", "//?/D:/folder/one.mp4"],
  ["\\\\?\\D:\\folder\\one.mp4", "D:/folder/one.mp4"],
  ["https://example.test/scene/1", "https://example.test/scene//1"],
  ["https://example.test/scene/1", "https://example.test/scene/1/"],
  ["https://example.test/scene/1", "https://example.test/scene\\1"],
]) {
  const separate = new QueueController(new MemorySnapshotStorage(), clock, 100, dosIds);
  assert.equal(separate.addPaths(distinct).length, 2, JSON.stringify(distinct));
  assert.deepEqual(separate.getSnapshot().jobs.map((job) => job.sourcePath), distinct);
}
console.log("DOS-drive separator comparison regressions passed (calls/reload/batch; preserved completed/selected/voice/output; retry; narrow negative boundaries)");
console.log("desktop queue and import pack tests passed");
