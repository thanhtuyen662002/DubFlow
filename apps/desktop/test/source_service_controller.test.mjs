import assert from "node:assert/strict";
import test from "node:test";
import { QueueController, MemorySnapshotStorage } from "../src/features/queue/model.ts";
import { createSourceServiceController } from "../src/features/source_intake/service_controller.ts";

const path = "D:/Dữ liệu nguồn/Cảnh, phần 1.mp4";
const receipt = (scan = "scan-1", identity = "generic:scene,1") => ({
  event: "source_downloaded", scan_id: scan, identity_key: identity,
  media_path: path, sha256: "a".repeat(64), size_bytes: 1621,
});
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture(queue = new QueueController(new MemorySnapshotStorage())) {
  const calls = [], events = [];
  let sequence = 0, scan = 0;
  const gates = new Map();
  const h = {
    queue, calls, events, gates,
    snapshot: { phase: "ready", failure: null, retired: false },
    push(value) { events.push({ sequence: ++sequence, value }); },
    count(command) { return calls.filter((call) => call.command === command).length; },
    sent(command) { return calls.filter((call) => call.command === "source_service_send_v1" && call.args.request.command === command).map((call) => call.args.request); },
    async invoke(command, args) {
      calls.push({ command, args });
      const gate = gates.get(command);
      if (gate) { gates.delete(command); return gate.promise; }
      if (command === "source_service_events_v1") return events.splice(0);
      if (command === "source_service_send_v1") return { delivery: "submitted" };
      if (command === "source_service_stop_v1") return { phase: "stopping", failure: null, retired: false };
      return structuredClone(h.snapshot);
    },
  };
  h.controller = createSourceServiceController(queue, h.invoke, { scanId: () => "scan-" + (++scan) });
  return h;
}
async function enumerate(h, scan = "scan-1", identity = "generic:scene,1") {
  h.push({ event: "source_preparing", scan_id: scan });
  h.push({ event: "source_status", scan_id: scan, status: "running", dispatch_revision: 1 });
  h.push({ event: "source_status", scan_id: scan, status: "completed", dispatch_revision: 2 });
  await h.controller.poll();
  h.push({ event: "source_items", scan_id: scan, offset: 0, total: 1, items: [{ identity_key: identity, status: "discovered" }] });
  await h.controller.poll();
}

test("stages exact URLs, isolates unsupported/private/invalid lines, and admits each local file first", async () => {
  const h = fixture();
  const result = await h.controller.submit([
    path, "bad", "https://example.test/scene,1", "https://example.test/scene//1",
    "https://example.test/scene/1/", "https://youtube.com/watch?v=abc",
    "https://example.test/private?sessionid=secret-value", "https://example.test/private?token=hidden",
    "https://example.test/" + "ệ".repeat(1400),
  ].join("\n"));
  assert.equal(result.local_added, 1);
  assert.equal(result.online_staged.length, 3);
  assert.equal(result.rejected.length, 5);
  assert.ok(result.rejected.some((value) => value.code === "SOURCE_PROVIDER_UNSUPPORTED"));
  assert.equal(h.sent("start_video").length, 1);
  assert.equal(h.sent("start_video")[0].source_ref, "https://example.test/scene,1");
  assert.equal(h.sent("start_video")[0].provider_id, "generic");
  assert.deepEqual(h.controller.getState().rows.map((row) => row.phase), ["submitted", "queued", "queued"]);
  assert.equal(h.queue.getSnapshot().jobs[0].sourcePath, path);
  const exposed = JSON.stringify(h.controller.getState());
  assert.ok(!exposed.includes("secret-value") && !exposed.includes("hidden"));
  const duplicate = await h.controller.submit("https://example.test/scene,1\nD:/another local.mp4");
  assert.equal(duplicate.local_added, 1);
  assert.equal(duplicate.rejected[0].code, "DUPLICATE_SOURCE");
  assert.equal(h.count("source_service_start_v1"), 1);
});

test("local input stays usable while startup and stream delivery are pending or fail", async () => {
  const h = fixture();
  const start = deferred(); h.gates.set("source_service_start_v1", start);
  const online = h.controller.submit("https://example.test/one");
  assert.equal(h.controller.getState().service, "admitting");
  assert.equal((await h.controller.submit(path)).local_added, 1);
  start.reject("SOURCE_SETUP_MISSING: private-root secret-token");
  await online;
  assert.equal(h.controller.getState().failure, "SOURCE_SETUP_MISSING");
  assert.equal(h.sent("start_video").length, 0);
  assert.equal((await h.controller.submit("D:/local after error.mp4")).local_added, 1);
  await h.controller.submit("https://example.test/two");
  assert.equal(h.count("source_service_start_v1"), 1); // no auto restart
  assert.ok(!JSON.stringify(h.controller.getState()).includes("secret-token"));

  const next = fixture(), send = deferred();
  next.gates.set("source_service_send_v1", send);
  const pending = next.controller.submit("https://example.test/one");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal((await next.controller.submit(path)).local_added, 1);
  send.reject(new Error("raw cookie=private diagnostic"));
  await pending;
  assert.equal(next.controller.getState().rows[0].delivery, "unknown");
  assert.equal(next.controller.getState().rows[0].error, "SOURCE_IPC_FAILED");
  assert.equal(next.sent("start_video").length, 1);
});

test("admission and completed enumeration never imply download; explicit exact identity is required", async () => {
  const h = fixture();
  h.snapshot.phase = "admitting";
  await h.controller.submit("https://example.test/one");
  assert.equal(h.sent("start_video").length, 0);
  h.snapshot.phase = "ready";
  h.push({ event: "source_ready", schema_version: 1, single_writer: true });
  await h.controller.poll();
  assert.equal(h.sent("start_video").length, 1);
  await enumerate(h);
  assert.equal(h.controller.getState().rows[0].phase, "enumerated");
  assert.equal(h.sent("items").length, 1);
  assert.equal(h.sent("download").length, 0);
  assert.equal(h.queue.getSnapshot().jobs.length, 0);
  assert.equal(await h.controller.download("scan-1", "generic:other"), false);
  assert.equal(await h.controller.download("scan-1", "generic:scene,1"), true);
  assert.equal(await h.controller.download("scan-1", "generic:scene,1"), false);
  assert.deepEqual(h.sent("download"), [{ command: "download", scan_id: "scan-1", identity_key: "generic:scene,1", resume: false }]);
  assert.equal(h.controller.getState().rows[0].phase, "download_submitted");
  assert.equal(h.controller.getState().rows[0].delivery, "submitted");
  assert.equal(h.queue.getSnapshot().jobs.length, 0);
  h.push({ event: "source_download_preparing", scan_id: "scan-1", identity_key: "generic:scene,1" });
  h.push({ event: "source_download_progress", scan_id: "scan-1", identity_key: "generic:scene,1" });
  await h.controller.poll();
  assert.equal(h.controller.getState().rows[0].phase, "downloading");
  h.push(receipt()); h.push(receipt()); h.push(receipt("unknown-scan"));
  await h.controller.poll();
  assert.equal(h.queue.getSnapshot().jobs.length, 1);
  assert.equal(h.queue.getSnapshot().jobs[0].sourcePath, path);
  assert.equal(h.controller.getState().rows[0].phase, "downloaded");
  h.push(receipt()); await h.controller.poll();
  assert.equal(h.queue.getSnapshot().jobs.length, 1);
});

test("one drain is shared, new sources remain staged until observed download completion", async () => {
  const h = fixture();
  await h.controller.submit("https://example.test/one\nhttps://bilibili.com/video/BVfixture");
  await enumerate(h);
  await h.controller.download("scan-1", "generic:scene,1");
  const gate = deferred(); h.gates.set("source_service_events_v1", gate);
  const before = h.count("source_service_events_v1");
  const a = h.controller.poll(), b = h.controller.poll();
  assert.equal(a, b); assert.equal(h.count("source_service_events_v1"), before + 1);
  assert.equal((await h.controller.submit(path)).local_added, 1);
  gate.resolve([{ sequence: 100, value: receipt() }]);
  await a;
  assert.equal(h.sent("start_video").length, 2);
  assert.equal(h.sent("start_video")[1].provider_id, "bilibili");
  assert.equal(h.controller.getState().rows[1].phase, "submitted");
});

test("cancels one queued or preparing scan; late events cannot resurrect it", async () => {
  const h = fixture();
  await h.controller.submit("https://example.test/one\nhttps://example.test/two\nhttps://example.test/three");
  assert.equal(await h.controller.cancel("scan-2"), true);
  assert.equal(h.sent("cancel").length, 0);
  h.push({ event: "source_preparing", scan_id: "scan-1" }); await h.controller.poll();
  assert.equal(await h.controller.cancel("scan-1"), true);
  assert.equal(h.controller.getState().rows[0].phase, "cancelling");
  assert.equal(h.sent("cancel").length, 1);
  h.push({ event: "source_preparing", scan_id: "scan-1" }); await h.controller.poll();
  assert.equal(h.controller.getState().rows[0].phase, "cancelling");
  h.push({ event: "source_preparation_stopped", scan_id: "scan-1", cancelled: true });
  h.push({ event: "source_status", scan_id: "scan-1", status: "completed", dispatch_revision: 2 });
  h.push(receipt()); await h.controller.poll();
  assert.equal(h.controller.getState().rows[0].phase, "cancelled");
  assert.equal(h.queue.getSnapshot().jobs.length, 0);
  assert.deepEqual(h.sent("start_video").map((value) => value.scan_id), ["scan-1", "scan-3"]);
  assert.equal(h.count("source_service_stop_v1"), 0);
});

test("download cancellation suppresses receipts observed after native confirmation", async () => {
  const h = fixture();
  await h.controller.submit("https://example.test/one");
  await enumerate(h); await h.controller.download("scan-1", "generic:scene,1");
  await h.controller.cancel("scan-1");
  h.push({ event: "source_download_cancelled", scan_id: "scan-1", identity_key: "generic:scene,1" });
  h.push(receipt()); await h.controller.poll();
  assert.equal(h.controller.getState().rows[0].phase, "cancelled");
  assert.equal(h.queue.getSnapshot().jobs.length, 0);
});

test("native errors are not invented RPC correlation or an automatic retry", async () => {
  const h = fixture();
  await h.controller.submit("https://example.test/one\nhttps://example.test/two");
  h.push({ event: "source_error", code: "SOURCE_REQUEST_REJECTED", retryable: false });
  await h.controller.poll();
  assert.equal(h.controller.getState().failure, "SOURCE_ERROR_UNCORRELATED");
  assert.equal(h.controller.getState().rows[0].phase, "submitted");
  assert.equal(h.sent("start_video").length, 1);
  assert.equal((await h.controller.submit(path)).local_added, 1);
  const other = fixture();
  await other.controller.submit("https://example.test/one\nhttps://example.test/two");
  other.push({ event: "source_error", scan_id: "scan-1", code: "AUTH_REQUIRED", condition: "cookie=private", retryable: true });
  await other.controller.poll();
  assert.equal(other.controller.getState().rows[0].phase, "failed");
  assert.equal(other.sent("start_video").length, 2); // a different source, no retry
  assert.ok(!JSON.stringify(other.controller.getState()).includes("cookie"));
});

test("storage rejection and malformed receipt report uncertainty without faking a local job", async () => {
  const accepted = [];
  const q = { addPaths(paths) { if (paths[0] === path) throw new Error("private storage detail"); accepted.push(paths[0]); return ["job"]; } };
  const h = fixture(q);
  const local = await h.controller.submit(path + "\nD:/valid neighbor.mp4");
  assert.equal(local.local_added, 1); assert.equal(local.rejected[0].code, "LOCAL_QUEUE_REJECTED");
  assert.deepEqual(accepted, ["D:/valid neighbor.mp4"]);
  await h.controller.submit("https://example.test/one"); await enumerate(h);
  await h.controller.download("scan-1", "generic:scene,1");
  h.push(receipt()); await h.controller.poll();
  assert.equal(h.controller.getState().rows[0].phase, "queue_error");
  assert.equal(h.controller.getState().rows[0].local_queue, "error");
  assert.equal(h.controller.getState().rows[0].error, "LOCAL_QUEUE_REJECTED");
  assert.ok(!JSON.stringify(h.controller.getState()).includes("private storage"));
  const malformed = fixture();
  await malformed.controller.submit("https://example.test/one"); await enumerate(malformed);
  await malformed.controller.download("scan-1", "generic:scene,1");
  malformed.push({ ...receipt(), sha256: "not-a-hash" }); await malformed.controller.poll();
  assert.equal(malformed.controller.getState().rows[0].error, "SOURCE_MATERIALIZED_INVALID");
  assert.equal(malformed.queue.getSnapshot().jobs.length, 0);
});

test("downloaded item snapshots cannot close the crash/restart reconciliation gap", async () => {
  const h = fixture();
  await h.controller.submit("https://example.test/one");
  await enumerate(h);
  h.push({ event: "source_items", scan_id: "scan-1", offset: 0, total: 1,
    items: [{ identity_key: "generic:scene,1", status: "downloaded", media_path: path, content_hash: "a".repeat(64) }] });
  await h.controller.poll();
  assert.equal(h.controller.getState().rows[0].error, "SOURCE_RECONCILIATION_REQUIRED");
  assert.equal(await h.controller.download("scan-1", "generic:scene,1"), false);
  assert.equal(h.queue.getSnapshot().jobs.length, 0);
});

test("stop reports stopping until observed retirement; disposal ignores a late destructive drain", async () => {
  const h = fixture();
  await h.controller.submit("https://example.test/one\nhttps://example.test/two");
  const stopped = await h.controller.stop();
  assert.equal(stopped.service, "stopping"); assert.equal(stopped.retired, false);
  h.snapshot = { phase: "stopped", failure: null, retired: true };
  await h.controller.poll();
  assert.equal(h.controller.getState().retired, true);
  assert.equal(h.controller.getState().rows[0].phase, "interrupted");
  assert.equal(h.sent("start_video").length, 1);
  assert.equal((await h.controller.submit(path)).local_added, 1);
  const next = fixture();
  await next.controller.submit("https://example.test/one"); await enumerate(next);
  await next.controller.download("scan-1", "generic:scene,1");
  const gate = deferred(); next.gates.set("source_service_events_v1", gate);
  const pending = next.controller.poll();
  const result = await next.controller.dispose();
  assert.equal(result.disposed, true); assert.equal(result.retired, false);
  gate.resolve([{ sequence: 100, value: receipt() }]); await pending;
  assert.equal(next.queue.getSnapshot().jobs.length, 0);
  assert.equal(next.count("source_service_stop_v1"), 1);
  assert.equal((await next.controller.submit(path)).rejected[0].code, "SOURCE_CONTROLLER_DISPOSED");
});

test("late submitted delivery and stale statuses cannot regress native progress; views get copies", async () => {
  const h = fixture(), gate = deferred();
  h.gates.set("source_service_send_v1", gate);
  const pending = h.controller.submit("https://example.test/one");
  await new Promise((resolve) => setImmediate(resolve));
  h.push({ event: "source_preparing", scan_id: "scan-1" }); await h.controller.poll();
  assert.equal(h.controller.getState().rows[0].phase, "preparing");
  gate.resolve({ delivery: "submitted" }); await pending;
  assert.equal(h.controller.getState().rows[0].phase, "preparing");
  h.push({ event: "source_status", scan_id: "scan-1", status: "completed", dispatch_revision: 2 }); await h.controller.poll();
  h.push({ event: "source_status", scan_id: "scan-1", status: "running", dispatch_revision: 1 }); await h.controller.poll();
  assert.equal(h.controller.getState().rows[0].phase, "enumerated");
  let count = 0;
  const unsubscribe = h.controller.subscribe((state) => { count++; state.rows[0].phase = "failed"; });
  assert.equal(h.controller.getState().rows[0].phase, "enumerated");
  unsubscribe(); await h.controller.submit(path); assert.equal(count, 1);
});

test("pending download delivery is not submitted and cannot regress a native preparation event", async () => {
  const h = fixture(), gate = deferred();
  await h.controller.submit("https://example.test/one"); await enumerate(h);
  h.gates.set("source_service_send_v1", gate);
  const pending = h.controller.download("scan-1", "generic:scene,1");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.controller.getState().rows[0].phase, "download_sending");
  assert.equal(h.controller.getState().rows[0].delivery, "pending");
  h.push({ event: "source_download_preparing", scan_id: "scan-1", identity_key: "generic:scene,1" });
  await h.controller.poll();
  gate.resolve({ delivery: "submitted" }); await pending;
  assert.equal(h.controller.getState().rows[0].phase, "download_preparing");
  assert.equal(h.queue.getSnapshot().jobs.length, 0);
});

test("disposal during pending startup cannot revive readiness or dispatch queued URLs", async () => {
  const h = fixture(), gate = deferred();
  h.gates.set("source_service_start_v1", gate);
  const pending = h.controller.submit("https://example.test/one");
  const stopped = await h.controller.dispose();
  assert.equal(stopped.service, "stopping"); assert.equal(stopped.disposed, true);
  gate.resolve({ phase: "ready", failure: null, retired: false }); await pending;
  assert.equal(h.controller.getState().service, "stopping");
  assert.equal(h.sent("start_video").length, 0);
});

test("native public-reference spelling refusals remain per-item before using the stream", async () => {
  const h = fixture();
  const result = await h.controller.submit([
    "https://example.test/one#", "https://ex%61mple.test/one",
    "https://example.test/one?%ff=x", "https://example.test/one?%zz=x",
    "https://example.test/good,1?name=tiếng-Việt",
  ].join("\n"));
  assert.equal(result.rejected.length, 4);
  assert.equal(result.online_staged.length, 1);
  assert.equal(h.sent("start_video")[0].source_ref, "https://example.test/good,1?name=tiếng-Việt");
});
