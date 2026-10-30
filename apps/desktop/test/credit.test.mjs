import assert from "node:assert/strict";
import {
  appendCredit, emptySourceCredit, formatCreditLine, parseSourceCredit,
  platformFromUrl, sourceCreditForJob, sourceRightsHint, validateSourceCredit,
} from "../src/features/credit/model.ts";
import { mountSourceCredit } from "../src/features/credit/view.ts";
import { previewInvoke } from "../src/shell/preview_invoke.ts";
import { MemorySnapshotStorage, QueueController } from "../src/features/queue/model.ts";
import { createSessionKey, mountPostSuggestions } from "../src/features/seo/view.ts";

const empty = emptySourceCredit();
assert.deepEqual(parseSourceCredit(null), empty);
assert.equal(validateSourceCredit(empty), null);
assert.notEqual(parseSourceCredit(null), empty, "defaults are independent objects");
for (const value of [undefined, false, [], "{}", {}, { ...empty, schema_version: 2 }, { ...empty, rights: "licensed" }]) {
  assert.equal(parseSourceCredit(value), null);
  assert.equal(typeof validateSourceCredit(value), "string");
}
for (const rights of ["own", "permission", "cc", "unknown"]) {
  assert.deepEqual(parseSourceCredit({ ...empty, rights }), { ...empty, rights });
}
for (const [field, limit] of [["creator", 120], ["platform", 40], ["url", 500], ["note", 500]]) {
  const prefix = field === "url" ? "https://example.com/" : "";
  const atLimit = prefix + "🌱".repeat(limit - prefix.length);
  assert.equal(validateSourceCredit({ ...empty, [field]: atLimit }), null, `${field}: Unicode at limit`);
  assert.match(validateSourceCredit({ ...empty, [field]: atLimit + "x" }), new RegExp(`${limit}`));
  for (const control of ["\0", "\t", "\r", "\n", "\x7f", "\x85", "\x9f"]) {
    assert.match(validateSourceCredit({ ...empty, [field]: prefix + control }), /điều khiển/);
  }
  assert.equal(parseSourceCredit({ ...empty, [field]: null }), null);
  const missing = { ...empty };
  delete missing[field];
  assert.equal(parseSourceCredit(missing), null);
}
for (const url of ["ftp://example.com", "javascript:alert(1)", "https://", "example.com", " https://example.com"]) {
  assert.match(validateSourceCredit({ ...empty, url }), /Link/);
}
for (const url of ["", "http://example.com", "https://example.com/video?id=1&tag=vi"]) {
  assert.equal(validateSourceCredit({ ...empty, url }), null);
}

// Hosts, including share-link hosts, identify the platform; paths and suffix lookalikes do not.
for (const [url, platform] of [
  ["https://www.bilibili.com/video/BV1", "Bilibili"], ["https://b23.tv/abc", "Bilibili"],
  ["https://v.douyin.com/abc", "Douyin"], ["https://www.iesdouyin.com/share/video/1", "Douyin"],
  ["https://vm.tiktok.com/abc", "TikTok"], ["https://WWW.YOUTUBE.COM/watch?v=1", "YouTube"],
  ["http://youtu.be/abc", "YouTube"], ["https://m.youtube.com./watch?v=1", "YouTube"],
  ["https://www.kuaishou.com/short-video/1", "Kuaishou"], ["https://v.kuaishou.com/1", "Kuaishou"],
  ["https://v.kwai.com/abc", "Kuaishou"], ["https://chenzhongtech.com/abc", "Kuaishou"],
  ["", ""], ["bad url", ""], ["ftp://youtube.com/1", ""], ["https://notyoutube.com", ""],
  ["https://youtube.com.example.com", ""], ["https://example.com/youtube.com", ""],
  ["https://youtube.com@example.com", ""], ["https://you\ntube.com/1", ""],
]) assert.equal(platformFromUrl(url), platform, url);

const url = "https://www.bilibili.com/video/BV1";
assert.deepEqual(sourceCreditForJob(null, url), { ...empty, url, platform: "Bilibili" });
assert.deepEqual(sourceCreditForJob(null), empty);
assert.deepEqual(sourceCreditForJob(null, "https://example.com/" + "a".repeat(500)), empty, "never truncate a long source URL");
assert.deepEqual(sourceCreditForJob(empty, url), empty, "a saved empty credit suppresses prefill");
const saved = { ...empty, creator: "Tác giả", platform: "Douyin", rights: "permission", note: "Được phép đăng lại" };
assert.deepEqual(sourceCreditForJob(saved, url), saved);
assert.throws(() => sourceCreditForJob({ broken: true }, url), /không hợp lệ/);

// Existing snapshots retain fetched URLs, including a new run of a linked job.
const storage = new MemorySnapshotStorage();
const queue = new QueueController(storage);
const linkedId = queue.addLinkJob("preview/source.mp4", url, "Video", "preview/output");
const linked = queue.getSnapshot().jobs[0];
queue.updateStatus(linkedId, { ...linked.status, state: "COMPLETED" }, "preview/final_vi.mp4");
const retryId = queue.retryJob(linkedId);
const restored = new QueueController(storage).getSnapshot();
assert.equal(restored.jobs.find((job) => job.id === retryId).originUrl, url);
assert.equal(sourceCreditForJob(null, restored.jobs[0].originUrl).platform, "Bilibili");

assert.equal(formatCreditLine(empty), "");
assert.equal(formatCreditLine({ ...empty, note: "Ghi chú riêng", rights: "own" }), "");
assert.equal(formatCreditLine({ ...saved, url }), `Nguồn: Tác giả (Douyin) – ${url}`);
assert.equal(formatCreditLine({ ...empty, creator: "  Tác giả  " }), "Nguồn: Tác giả");
assert.equal(formatCreditLine({ ...empty, platform: "TikTok" }), "Nguồn: TikTok");
assert.equal(formatCreditLine({ ...empty, url }), `Nguồn: ${url}`);
assert.equal(formatCreditLine({ ...empty, creator: "Tác giả", url }), `Nguồn: Tác giả – ${url}`);
assert.equal(formatCreditLine({ ...empty, platform: "Bilibili", url }), `Nguồn: Bilibili – ${url}`);
assert.equal(formatCreditLine({ ...empty, creator: " ", platform: " " }), "");
assert.equal(appendCredit("Mô tả", saved), "Mô tả\n\nNguồn: Tác giả (Douyin)");
assert.equal(appendCredit("Mô tả", empty), "Mô tả");
assert.equal(appendCredit("", saved), "Nguồn: Tác giả (Douyin)");
assert.equal(sourceRightsHint(empty), "Chưa ghi quyền sử dụng video này; hãy điền mục “Nguồn & quyền” trước khi đăng.");
for (const rights of ["own", "permission", "cc"]) assert.equal(sourceRightsHint({ ...saved, rights }), "");
assert.notEqual(sourceRightsHint(sourceCreditForJob(null, url)), "", "a fetched URL does not establish rights");

// Browser preview persists by output path, returns copies, and validates saves.
const path = "preview/DubFlow Output/one/final_vi.mp4";
assert.equal(await previewInvoke("source_credit_get", { outputPath: path }), null);
await previewInvoke("source_credit_set", { outputPath: path, credit: saved });
const retrieved = await previewInvoke("source_credit_get", { outputPath: path });
assert.deepEqual(retrieved, saved);
retrieved.creator = "changed";
assert.deepEqual(await previewInvoke("source_credit_get", { outputPath: path }), saved);
assert.equal(await previewInvoke("source_credit_get", { outputPath: path + "-other" }), null);
await assert.rejects(previewInvoke("source_credit_set", { outputPath: path, credit: { ...saved, note: "\n" } }), /không hợp lệ/);
await previewInvoke("source_credit_set", { outputPath: path, credit: empty });
assert.deepEqual(sourceCreditForJob(await previewInvoke("source_credit_get", { outputPath: path }), url), empty);

// An inspector load/save may finish after the user has selected a different job.
const nodes = new Map();
for (const id of ["source-credit", "credit-fields", "credit-state", "credit-creator", "credit-platform", "credit-url", "credit-rights", "credit-note"]) {
  nodes.set(`#${id}`, { hidden: true, disabled: false, value: "", textContent: "", listeners: {}, addEventListener(name, handler) { this.listeners[name] = handler; }, reset() {} });
}
globalThis.document = { querySelector: (selector) => nodes.get(selector) };
const pending = [];
const calls = [];
const inspector = mountSourceCredit((command, args) => {
  calls.push([command, args]);
  return new Promise((resolve, reject) => pending.push({ resolve, reject }));
});
const job = (id, originUrl) => ({ id, originUrl, outputPath: id, status: { state: "COMPLETED" } });
const flush = () => new Promise((resolve) => setImmediate(resolve));
inspector.select(job("a", url));
inspector.select(job("a", url));
assert.equal(calls.length, 1, "polling does not reload the form");
inspector.select(job("b"));
pending[1].resolve(saved);
await flush();
assert.equal(nodes.get("#credit-creator").value, saved.creator);
pending[0].resolve(null);
await flush();
assert.equal(nodes.get("#credit-creator").value, saved.creator, "late load cannot replace another job's fields");
nodes.get("#credit-note").value = "\n";
await nodes.get("#source-credit").listeners.submit({ preventDefault() {} });
assert.equal(calls.length, 2, "invalid form is never saved");
nodes.get("#credit-note").value = "Ghi chú";
const save = nodes.get("#source-credit").listeners.submit({ preventDefault() {} });
assert.equal(calls[2][0], "source_credit_set");
assert.equal(calls[2][1].outputPath, "b");
inspector.select(job("c"));
pending[2].reject(new Error("disk full"));
await save;
assert.equal(nodes.get("#credit-fields").disabled, true, "late save cannot enable a loading job");
pending[3].resolve(null);
await flush();
const failedSave = nodes.get("#source-credit").listeners.submit({ preventDefault() {} });
pending[4].reject(new Error("disk full"));
await failedSave;
assert.match(nodes.get("#credit-state").textContent, /Không lưu được:.*disk full/);
inspector.select(null);
assert.equal(nodes.get("#source-credit").hidden, true);
delete globalThis.document;

// The actual posting dialog displays/copies credit for both offline and AI results.
class Node {
  children = []; listeners = {}; hidden = false; disabled = false; value = ""; textContent = "";
  addEventListener(name, handler) { this.listeners[name] = handler; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  querySelector() { return this.hint ??= new Node(); }
  setAttribute() {}
}
const postNodes = new Map(["post-dialog", "post-body", "post-state", "post-credit-hint", "post-ai", "post-key-row", "post-key", "post-key-clear", "post-provider", "post-key-save", "post-close"].map((id) => [`#${id}`, new Node()]));
globalThis.document = { querySelector: (selector) => postNodes.get(selector), createElement: () => new Node() };
globalThis.window = { localStorage: { removeItem() {} }, setTimeout() {} };
const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
let copied = "";
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async (text) => { copied = text; } } } });
const session = createSessionKey();
session.setProvider("gemini");
await session.set("test-placeholder");
let storedCredit = { ...empty, creator: "Credit author", platform: "YouTube", url: "https://youtu.be/credit", note: "Private credit note" };
const hostCalls = [];
const post = mountPostSuggestions(async (command, args) => {
  hostCalls.push([command, args]);
  if (command === "source_credit_get") return storedCredit;
  if (command === "post_suggestions") return { ok: true, titles: ["Tiêu đề"], description: "Mô tả offline", hashtags: ["#vietsub"] };
  if (command === "review_cues") return [{ text: "Nội dung phụ đề" }];
  throw new Error(`Unexpected ${command}`);
}, session);
post.open("one", "Tên video.mp4");
await flush();
const description = () => postNodes.get("#post-body").children[2];
assert.equal(description().children[1].textContent, appendCredit("Mô tả offline", storedCredit));
await description().children[0].children[1].listeners.click();
assert.equal(copied, appendCredit("Mô tả offline", storedCredit));
assert.equal(postNodes.get("#post-credit-hint").textContent, sourceRightsHint(empty));
assert.equal(postNodes.get("#post-credit-hint").hidden, false);
const originalFetch = globalThis.fetch;
let providerBody;
globalThis.fetch = async (_, init) => {
  providerBody = init.body;
  return { ok: true, json: async () => ({ candidates: [{ content: { parts: [{ text: JSON.stringify({ titles: ["Tiêu đề AI"], description: "Mô tả AI", hashtags: [] }) }] } }] }) };
};
postNodes.get("#post-ai").listeners.click();
await flush();
assert.match(providerBody, /Nội dung phụ đề/);
for (const field of ["Credit author", "https://youtu.be/credit", "Private credit note"]) assert.equal(providerBody.includes(field), false);
assert.equal(description().children[1].textContent, appendCredit("Mô tả AI", storedCredit));
await description().children[0].children[1].listeners.click();
assert.equal(copied, appendCredit("Mô tả AI", storedCredit));
assert.deepEqual(hostCalls.filter(([command]) => command === "post_suggestions")[0][1], { outputPath: "one", sourceTitle: "Tên video" });
storedCredit = { ...storedCredit, rights: "permission", creator: "Updated author" };
post.open("one", "Tên video.mp4");
await flush();
assert.match(description().children[1].textContent, /Updated author/);
assert.equal(postNodes.get("#post-credit-hint").hidden, true, "each opening reloads saved rights");
storedCredit = null;
post.open("two", "Video", url);
await flush();
assert.equal(description().children[1].textContent, appendCredit("Mô tả offline", sourceCreditForJob(null, url)));
assert.equal(postNodes.get("#post-credit-hint").hidden, false);
globalThis.fetch = originalFetch;
if (navigatorDescriptor) Object.defineProperty(globalThis, "navigator", navigatorDescriptor);
else delete globalThis.navigator;
delete globalThis.document;
delete globalThis.window;

console.log("source credit tests passed");
