import assert from "node:assert/strict";
import { MemorySnapshotStorage, QueueController, parseQueueSnapshot } from "../src/features/queue/model.ts";
import { toQueueViewModel } from "../src/features/queue/view_model.ts";
import { failureHint } from "../src/features/job_status/status.ts";

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

// A reviewed revision re-runs the completed job in its own output directory.
assert.throws(() => fresh.reviseJob(variantId, "D:/out"), /Only completed jobs/);
const revisionId = fresh.reviseJob(completed.id, "D:/media/DubFlow Output/run-1");
const revision = fresh.getSnapshot().jobs.find((job) => job.id === revisionId);
assert.equal(revision.outputDir, "D:/media/DubFlow Output/run-1");
assert.equal(revision.revisionOf, completed.id);
assert.equal(toQueueViewModel(fresh.getSnapshot()).rows.find((row) => row.id === revisionId).revision, true);
assert.equal(revision.status.state, "QUEUED");
assert.deepEqual(revision.dubbing, completed.dubbing);
assert.equal(new QueueController(emptyStorage, clock).getSnapshot().jobs.find((job) => job.id === revisionId).outputDir, "D:/media/DubFlow Output/run-1");
assert.throws(() => fresh.reviseJob(completed.id, " "), /output directory/);

// A downloaded link becomes a named local job with its own output folder.
const linkQueue = new QueueController(new MemorySnapshotStorage(), clock);
const linkId = linkQueue.addLinkJob("C:/data/source-media/abc.mp4", "https://archive.org/x/ed.mp4", "ed.mp4", "C:/Users/u/Videos/DubFlow Output/ed - vi/link-1");
const linkJob = linkQueue.getSnapshot().jobs.find((job) => job.id === linkId);
assert.equal(linkJob.displayName, "ed.mp4");
assert.equal(linkJob.originUrl, "https://archive.org/x/ed.mp4");
assert.equal(linkQueue.getSnapshot().selected_job_id, linkId);
assert.equal(linkQueue.addLinkJob("C:/data/source-media/abc.mp4", "https://archive.org/x/ed.mp4", "dup", "C:/o"), null);
assert.throws(() => linkQueue.addLinkJob("C:/b.mp4", "file:///x", "x", "C:/o"), /link and an output/);
const linkView = toQueueViewModel(linkQueue.getSnapshot()).rows[0];
assert.equal(linkView.fromLink, true);
assert.equal(linkView.revision, false);

// Subtitle burn-in is a per-job option, editable only before processing.
const burnQueue = new QueueController(new MemorySnapshotStorage(), clock);
const [burnId] = burnQueue.addPaths(["C:/media/burn.mp4"]);
burnQueue.setBurnIn(burnId, false);
assert.equal(burnQueue.getSnapshot().jobs[0].burnIn, false);
burnQueue.updateStatus(burnId, { ...burnQueue.getSnapshot().jobs[0].status, state: "RUNNING" });
assert.throws(() => burnQueue.setBurnIn(burnId, true), /before processing/);

// Common worker failures get actionable Vietnamese hints; unknown ones keep their message.
assert.match(failureHint("ASR_EMPTY: ASR produced no speech segments"), /Không tìm thấy lời thoại/);
assert.match(failureHint("SOURCE_LANGUAGE_UNRESOLVED: x"), /Ngôn ngữ gốc/);
assert.equal(failureHint("ASR_EMPTYISH: x"), null);
assert.equal(failureHint("SOMETHING_ELSE: x"), null);

// The host-chosen output folder is pinned once so resumes reuse it.
const pinQueue = new QueueController(new MemorySnapshotStorage(), clock);
const [pinId] = pinQueue.addPaths(["C:/media/pin.mp4"]);
pinQueue.pinOutputDir(pinId, "D:/Out/DubFlow Output/pin - vi/run-1");
pinQueue.pinOutputDir(pinId, "E:/Other");
assert.equal(pinQueue.getSnapshot().jobs[0].outputDir, "D:/Out/DubFlow Output/pin - vi/run-1");
assert.equal(toQueueViewModel(pinQueue.getSnapshot()).rows[0].revision, false);

// The subtitle box is a per-job option, editable only before processing.
const boxQueue = new QueueController(new MemorySnapshotStorage(), clock);
const [boxId] = boxQueue.addPaths(["C:/media/box.mp4"]);
boxQueue.setSubtitleBox(boxId, true);
assert.equal(boxQueue.getSnapshot().jobs[0].subtitleBox, true);

// A user cancellation reads as a neutral "Đã hủy", not a failure.
const cancelQueue = new QueueController(new MemorySnapshotStorage(), clock);
const [cancelId] = cancelQueue.addPaths(["C:/media/cancel.mp4"]);
cancelQueue.updateStatus(cancelId, { ...cancelQueue.getSnapshot().jobs[0].status, state: "FAILED", reason: "cancelled", message: "Đã hủy theo yêu cầu" });
const cancelRow = toQueueViewModel(cancelQueue.getSnapshot()).rows[0];
assert.equal(cancelRow.stateLabel, "Đã hủy");
assert.equal(cancelRow.cancelled, true);

// Source language is a per-job option; null returns to automatic detection.
const langQueue = new QueueController(new MemorySnapshotStorage(), clock);
const [langId] = langQueue.addPaths(["C:/media/zh.mp4"]);
langQueue.setSourceLanguage(langId, "zh");
assert.equal(langQueue.getSnapshot().jobs[0].sourceLanguage, "zh");
langQueue.setSourceLanguage(langId, null);
assert.equal(langQueue.getSnapshot().jobs[0].sourceLanguage, undefined);

// Processing time is recorded once: first active state, then first terminal state.
let tick = 0;
const timed = new QueueController(new MemorySnapshotStorage(), () => new Date(Date.UTC(2026, 9, 24, 12, 0, tick++)).toISOString());
const [timedId] = timed.addPaths(["C:/media/timed.mp4"]);
const timedStatus = timed.getSnapshot().jobs[0].status;
assert.equal(timed.getSnapshot().jobs[0].startedAt, undefined);
timed.updateStatus(timedId, { ...timedStatus, state: "RUNNING" });
const startedAt = timed.getSnapshot().jobs[0].startedAt;
timed.updateStatus(timedId, { ...timedStatus, state: "RUNNING", message: "still running" });
assert.equal(timed.getSnapshot().jobs[0].startedAt, startedAt);
timed.updateStatus(timedId, { ...timedStatus, state: "COMPLETED" });
assert.ok(Date.parse(timed.getSnapshot().jobs[0].finishedAt) > Date.parse(startedAt));

const colliding = new QueueController(new MemorySnapshotStorage(), clock, 100, () => "collision");
colliding.addPaths(["one"]);
assert.throws(() => colliding.addPaths(["two"]), /independent job ID/);
assert.equal(colliding.getSnapshot().jobs.length, 1);

console.log("desktop queue tests passed");
