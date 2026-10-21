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

// Persisted output-root choices are independent of committed outputPath.
const legacyOutput = structuredClone(beforeDosDuplicate);
delete legacyOutput.jobs[0].outputRoot;
const legacyOutputStorage = new MemorySnapshotStorage(JSON.stringify(legacyOutput));
const restoredOutput = new QueueController(legacyOutputStorage, clock, 100, dosIds);
assert.equal(restoredOutput.getSnapshot().schema_version, 1);
assert.deepEqual(restoredOutput.getSnapshot(), beforeDosDuplicate);
assert.equal(JSON.parse(legacyOutputStorage.read()).jobs[0].outputRoot, undefined);

const rootStorage = new MemorySnapshotStorage();
const roots = new QueueController(rootStorage, clock, 100, dosIds);
const [rootId] = roots.addPaths(["C:/media/output-choice.mp4"]);
assert.equal(roots.getSnapshot().jobs[0].outputRoot, null);
for (const root of [
  "C:\\Đầu ra, bản 1\\", "d:/Mixed/Out ", "/tmp/output",
  "\\\\server\\share\\Đầu ra", "//server/share/out", "\\\\?\\C:\\Out",
]) {
  roots.setOutputRoot(rootId, root);
  assert.equal(roots.getSnapshot().jobs[0].outputRoot, root);
  assert.equal(new QueueController(rootStorage, clock).getSnapshot().jobs[0].outputRoot, root);
  assert.equal(roots.getSnapshot().jobs[0].outputPath, null);
}
roots.setOutputRoot(rootId, null);
assert.equal(new QueueController(rootStorage, clock).getSnapshot().jobs[0].outputRoot, null);
for (const bad of [
  "", " ", "relative/out", "C:relative", "\\rooted-only", "https://example.test/out",
  " C:/out", "C:/bad\u0000path", "C:/bad\npath", "C:/bad\u007fpath", "C:/bad\u0085path",
  "C:/" + "x".repeat(32766), 4, false, {}, [],
]) {
  const before = roots.getSnapshot(), stored = rootStorage.read();
  assert.throws(() => roots.setOutputRoot(rootId, bad), /Invalid output root/);
  assert.deepEqual(roots.getSnapshot(), before);
  assert.equal(rootStorage.read(), stored);
  const malformed = structuredClone(before);
  malformed.jobs[0].outputRoot = bad;
  assert.equal(parseQueueSnapshot(JSON.stringify(malformed)), null);
}
for (const state of [
  "RUNNING", "RETRYING", "RECOVERED", "WORKER_LOST", "WAITING_RESOURCE",
  "WAITING_EXTERNAL", "BLOCKED_NEEDS_ACTION", "COMPLETED", "FAILED",
]) {
  const blockedStorage = new MemorySnapshotStorage();
  const blocked = new QueueController(blockedStorage, clock, 100, dosIds);
  const [id] = blocked.addPaths(["C:/media/blocked-" + state + ".mp4"]);
  blocked.setOutputRoot(id, "D:/old-root");
  blocked.updateStatus(id, { ...blocked.getSnapshot().jobs[0].status, state });
  const before = blocked.getSnapshot(), stored = blockedStorage.read();
  assert.throws(() => blocked.setOutputRoot(id, "D:/new-root"), /untouched queued/);
  assert.deepEqual(blocked.getSnapshot(), before);
  assert.equal(blockedStorage.read(), stored);
}
for (const taint of [
  (s) => { s.checkpoint_id = "prior-checkpoint"; },
  (s) => { s.progress.completed_units = "1"; },
  (s) => { s.progress.total_units = "10"; },
  (s) => { s.progress.heartbeat_sequence = "1"; },
  (s) => { s.retry.attempt = "1"; },
  (s) => { s.retry.condition_fingerprint = "prior-condition"; },
  (s) => { s.resource.kind = "CPU"; },
  (s) => { s.resource.held = true; },
  (s) => { s.resource.release_requested = true; },
  (s) => { s.reason = "starting"; },
]) {
  const blocked = new QueueController(new MemorySnapshotStorage(), clock, 100, dosIds);
  const [id] = blocked.addPaths(["C:/media/queued-prior-work.mp4"]);
  const status = blocked.getSnapshot().jobs[0].status;
  taint(status);
  blocked.updateStatus(id, status);
  const before = blocked.getSnapshot();
  assert.throws(() => blocked.setOutputRoot(id, "D:/new-root"), /untouched queued/);
  assert.deepEqual(blocked.getSnapshot(), before);
}
const [committedId] = roots.addPaths(["C:/media/prior-result.mp4"]);
roots.updateStatus(committedId, roots.getSnapshot().jobs[1].status, "D:/committed/final.mp4");
const committedBefore = roots.getSnapshot();
assert.throws(() => roots.setOutputRoot(committedId, "D:/new-root"), /untouched queued/);
assert.deepEqual(roots.getSnapshot(), committedBefore);

roots.setOutputRoot(rootId, "D:\\Short Output");
roots.setDubbing(rootId, { enabled: true, voiceId: "vi-truc-ly-vieneu3-v1" });
roots.updateStatus(rootId, { ...roots.getSnapshot().jobs[0].status, state: "COMPLETED" }, "D:/original/final.mp4");
const oldRootRow = structuredClone(roots.getSnapshot().jobs[0]);
const rootRetry = roots.retryJob(rootId);
assert.notEqual(rootRetry, rootId);
assert.equal(roots.getSnapshot().selected_job_id, rootRetry);
assert.deepEqual(roots.getSnapshot().jobs[0], oldRootRow);
const retryRootRow = roots.getSnapshot().jobs.find((job) => job.id === rootRetry);
assert.equal(retryRootRow.outputRoot, "D:\\Short Output");
assert.equal(retryRootRow.outputPath, null);
assert.equal(retryRootRow.sourcePath, oldRootRow.sourcePath);
assert.deepEqual(retryRootRow.dubbing, oldRootRow.dubbing);
roots.setOutputRoot(rootRetry, "E:/another root");
assert.deepEqual(roots.getSnapshot().jobs[0], oldRootRow);
assert.deepEqual(new QueueController(rootStorage, clock).getSnapshot().jobs[0], oldRootRow);
assert.throws(() => roots.setOutputRoot("missing-job", null), /Unknown job/);
console.log("output-root queue regressions passed (legacy/persistence/validation/untouched guard/retry isolation)");

await import("./output_location.test.mjs");

// Appended to queue.test.mjs; public API/schema remain unchanged.
import test from "node:test";

function storageAtomicityFixture() {
  let serialized = null, fail = false, writes = 0, successful = 0, next = 0;
  const storage = {
    read: () => serialized,
    write(value) {
      writes++;
      if (fail) throw new Error("CONTROLLED_STORAGE_WRITE_FAILED");
      serialized = value;
      successful++;
    },
  };
  const queue = new QueueController(storage, () => "2026-10-21T00:00:00.000Z", 100,
    () => `storage-job-${++next}`);
  return { storage, queue, fail: (value) => { fail = value; },
    counts: () => ({ writes, successful }), reopen: () => new QueueController(storage) };
}

test("storage atomicity: failed add stays absent; changed-condition explicit retry persists exactly once", () => {
  const f = storageAtomicityFixture(), before = f.queue.getSnapshot();
  const path = "D:/nguồn, video.mp4";
  let notifications = 0;
  f.queue.subscribe(() => { notifications++; });
  f.fail(true);
  assert.throws(() => f.queue.addPaths([path]), /CONTROLLED_STORAGE_WRITE_FAILED/);
  assert.deepEqual(f.queue.getSnapshot(), before);
  assert.equal(f.storage.read(), null);
  assert.equal(notifications, 1);
  f.fail(false); // Explicit changed condition, never an automatic retry.
  const ids = f.queue.addPaths([path]);
  assert.equal(ids.length, 1);
  assert.equal(f.queue.getSnapshot().jobs[0].sourcePath, path);
  assert.deepEqual(f.reopen().getSnapshot(), f.queue.getSnapshot());
  assert.deepEqual(f.counts(), { writes: 2, successful: 1 });
  assert.deepEqual(f.queue.addPaths([path]), []);
  assert.deepEqual(f.counts(), { writes: 2, successful: 1 });
});

for (const [name, mutate] of [
  ["selection", (q, first, second) => q.selectJob(second)],
  ["status/output", (q, first, second) => q.updateStatus(second,
    { ...q.getSnapshot().jobs[1].status, state: "RUNNING" }, "D:/new-output.mp4")],
  ["retry/history", (q, first) => q.retryJob(first)],
  ["output root", (q, first, second) => q.setOutputRoot(second, "E:/new output")],
  ["voice", (q, first, second) => q.setDubbing(second, { enabled: true, voiceId: "vi-truc-ly-vieneu3-v1" })],
  ["remove/history", (q, first) => q.removeJob(first)],
]) {
  test("storage atomicity: failed " + name + " preserves committed rows, order, selection and history", () => {
    const f = storageAtomicityFixture();
    const [first, second] = f.queue.addPaths(["D:/old, A.mp4", "D:/pending B.mp4"]);
    f.queue.setOutputRoot(first, "D:/original output");
    f.queue.setDubbing(first, { enabled: true, voiceId: "vi-truc-ly-vieneu3-v1" });
    f.queue.updateStatus(first, { ...f.queue.getSnapshot().jobs[0].status, state: "COMPLETED" }, "D:/completed/final.mp4");
    const before = f.queue.getSnapshot(), persisted = f.storage.read(), counts = f.counts();
    let notifications = 0;
    f.queue.subscribe(() => { notifications++; });
    f.fail(true);
    assert.throws(() => mutate(f.queue, first, second), /CONTROLLED_STORAGE_WRITE_FAILED/);
    assert.deepEqual(f.queue.getSnapshot(), before);
    assert.equal(f.storage.read(), persisted);
    assert.deepEqual(f.reopen().getSnapshot(), before);
    assert.equal(notifications, 1);
    assert.deepEqual(f.counts(), { writes: counts.writes + 1, successful: counts.successful });
  });
}

test("storage atomicity: a capacity failure after the first batch item publishes no prefix", () => {
  const storage = new MemorySnapshotStorage();
  let next = 0;
  const queue = new QueueController(storage, () => "2026-10-21T00:00:00.000Z", 1, () => `capacity-${++next}`);
  const before = queue.getSnapshot();
  assert.throws(() => queue.addPaths(["D:/first.mp4", "D:/second.mp4"]), /Queue limit/);
  assert.deepEqual(queue.getSnapshot(), before);
  assert.equal(storage.read(), null);
  assert.deepEqual(new QueueController(storage).getSnapshot(), before);
});

test("storage atomicity: ID failure/collision after the first batch item publishes no prefix", () => {
  for (const collision of [false, true]) {
    const storage = new MemorySnapshotStorage();
    let next = 0;
    const queue = new QueueController(storage, () => "2026-10-21T00:00:00.000Z", 100,
      () => collision || next++ === 0 ? "same-id" : "invalid id");
    const before = queue.getSnapshot();
    assert.throws(() => queue.addPaths(["D:/first.mp4", "D:/second.mp4"]), /Invalid generated|independent job ID/);
    assert.deepEqual(queue.getSnapshot(), before);
    assert.equal(storage.read(), null);
  }
});

test("storage atomicity: observers cannot turn a durable success into a failed add or change another view", () => {
  const f = storageAtomicityFixture(), seen = [];
  f.queue.subscribe((view) => {
    if (view.jobs.length) { view.jobs.length = 0; throw new Error("CONTROLLED_OBSERVER_FAILED"); }
  });
  f.queue.subscribe((view) => { seen.push(view.jobs.length); });
  let ids;
  assert.doesNotThrow(() => { ids = f.queue.addPaths(["D:/committed.mp4"]); });
  assert.equal(ids.length, 1);
  assert.deepEqual(seen, [0, 1]);
  assert.deepEqual(f.reopen().getSnapshot(), f.queue.getSnapshot());
  assert.deepEqual(f.counts(), { writes: 1, successful: 1 });
});

test("storage atomicity: published RAM remains old until synchronous persistence succeeds", () => {
  let stored = null, queue, before;
  const storage = { read: () => stored, write(value) {
    assert.deepEqual(queue.getSnapshot(), before);
    stored = value;
  } };
  queue = new QueueController(storage, () => "2026-10-21T00:00:00.000Z", 100, () => "publication-job");
  before = queue.getSnapshot();
  const [id] = queue.addPaths(["D:/publication.mp4"]);
  assert.equal(queue.getSnapshot().jobs[0].id, id);
  assert.deepEqual(new QueueController(storage).getSnapshot(), queue.getSnapshot());
});
