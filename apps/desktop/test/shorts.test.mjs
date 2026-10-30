import assert from "node:assert/strict";
import { buildRequest, firstLine, formatTime, normalizeSegments, parseCues, parseStatus, parseTime } from "../src/features/shorts/model.ts";
import { suggestSegments } from "../src/features/shorts/ai.ts";
import { previewInvoke } from "../src/shell/preview_invoke.ts";
import { mountShorts } from "../src/features/shorts/view.ts";

for (const [text, value] of [["00:00", 0], ["01:30", 90000], ["125:59", 7559000], ["00:01.2", 1200], ["00:01.023", 1023]]) {
  assert.equal(parseTime(text), value);
  assert.equal(parseTime(formatTime(value)), value);
}
for (const text of ["", "2", "1:2", "00:60", "-1:00", "01:00:01", "NaN:00", "0:01.1234", "1e2:00", "999999999999999999:00"]) assert.equal(parseTime(text), null);

const cues = Array.from({ length: 24 }, (_, i) => ({ start_ms: i * 5000 + 100, end_ms: (i + 1) * 5000 - 100, text: `Câu ${i + 1}` }));
assert.equal(parseCues([null, {}, { start_ms: -1, end_ms: 2, text: "X" }, ...cues]).length, cues.length);
assert.equal(firstLine(cues, 1000, 5000), "Câu 1");
assert.deepEqual(normalizeSegments(null, cues), []);
assert.deepEqual(normalizeSegments({ items: [{ start_ms: 0, end_ms: 35000, title: "Hay" }] }, []), []);
const normalized = normalizeSegments({ items: [
  { start_ms: 0, end_ms: 35020, title: " Hay " },
  { start_ms: 10000, end_ms: 45000, title: "Overlap" },
  { start_ms: 35000, end_ms: 70000, title: "Tiếp theo" },
  { start_ms: 0, end_ms: 4000, title: "Too short" },
  { start_ms: 70000, end_ms: 999999, title: "Final" },
] }, cues);
assert.deepEqual(normalized.map(({ start_ms, end_ms, title }) => ({ start_ms, end_ms, title })), [
  { start_ms: 100, end_ms: 34900, title: "Hay" },
  { start_ms: 35100, end_ms: 69900, title: "Tiếp theo" },
  { start_ms: 70100, end_ms: 119900, title: "Final" },
]);
assert.equal(normalized[0].preview, "Câu 1");
for (const item of [null, {}, { start_ms: true, end_ms: 35000, title: "X" }, { start_ms: 0, end_ms: 35000, title: "" },
  { start_ms: 0.1, end_ms: 35000, title: "X" }, { start_ms: -1, end_ms: 35000, title: "X" },
  { start_ms: 35000, end_ms: 0, title: "X" }, { start_ms: 0, end_ms: 100000, title: "Too long" },
  { start_ms: 0, end_ms: 35000, title: "đ".repeat(81) }, { start_ms: 0, end_ms: 35000, title: "X\0" }]) {
  assert.deepEqual(normalizeSegments({ items: [item] }, cues), []);
}
const request = buildRequest("preview/final_vi.mp4", normalized, "auto");
assert.equal(request.items.length, 3);
assert.equal(request.items[0].title, "Hay");
for (const [rows, layout] of [[[], "auto"], [normalized, "bad"], [[...normalized, normalized[0]], "auto"],
  [[normalized[0], normalized[0]], "auto"], [[{ ...normalized[0], title: "" }], "auto"],
  [[{ ...normalized[0], start_ms: NaN }], "auto"], [[{ ...normalized[0], end_ms: 1000 }], "auto"]]) {
  assert.equal(typeof buildRequest("preview/final_vi.mp4", rows, layout), "string");
}
assert.equal(buildRequest("preview/final_vi.mp4", [{ ...normalized[0], title: "🌱".repeat(80) }], "mo-nen").items.length, 1);
assert.equal(buildRequest("preview/final_vi.mp4", [normalized[0], { ...normalized[0], selected: false }], "auto").items.length, 1);

let sent;
const suggestions = await suggestSegments("gemini", "test-key", cues, async (provider, key, input) => {
  assert.equal(provider, "gemini"); assert.equal(key, "test-key"); sent = input;
  return { items: [{ start_ms: 0, end_ms: 35000, title: "Mở đầu hay" }] };
});
assert.equal(suggestions[0].start_ms, 100);
assert.match(sent.system, /35–50/);
assert.match(sent.parts[0].text, /start_ms/);
assert.match(sent.parts[0].text, /Câu 1/);
assert.doesNotMatch(sent.parts[0].text, /test-key/);
await assert.rejects(suggestSegments("claude", "key", [], async () => ({})), /phụ đề/);
await assert.rejects(suggestSegments("claude", "key", cues, async () => ({})), /hợp lệ/);
const longCues = Array.from({ length: 600 }, (_, i) => ({ start_ms: i * 5000 + 100, end_ms: (i + 1) * 5000 - 100, text: `${i === 599 ? "LAST CUE" : "Subtitle"} ${"x".repeat(590)}` }));
let batches = 0, comparisons = 0;
const longInput = [];
const longResult = await suggestSegments("claude", "key", longCues, async (_provider, _key, input) => {
  const text = input.parts[0].text;
  longInput.push(text);
  if (text.startsWith("Chọn")) {
    comparisons += 1;
    return { items: [{ start_ms: 0, end_ms: 35000, title: "First" }, { start_ms: 2950000, end_ms: 2999900, title: "Last" }] };
  }
  batches += 1;
  const rows = text.split("\n").slice(1).map(line => JSON.parse(line));
  return { items: [{ start_ms: rows[0].start_ms, end_ms: rows[6].end_ms, title: "First" },
    ...(text.includes("LAST CUE") ? [{ start_ms: 2950000, end_ms: 2999900, title: "Last" }] : [])] };
});
assert.ok(batches > 1);
assert.equal(comparisons, batches - 1);
assert.ok(longInput.some(text => text.includes("LAST CUE")), "long videos include the final subtitles");
assert.ok(longResult.some(row => row.end_ms === 2999900), "final candidates can come from the end of a long video");

for (const state of ["RUNNING", "COMPLETED", "FAILED", "CANCELLED"]) {
  assert.equal(parseStatus({ state, message: "Test", done: 0, total: 1, outputs: [] }).state, state);
}
for (const value of [null, {}, { state: "OTHER" }, { state: "RUNNING", message: "Test", done: 2, total: 1, outputs: [] }]) assert.equal(parseStatus(value), null);

assert.equal(await previewInvoke("shorts_status", { outputPath: request.outputPath }), null);
await previewInvoke("shorts_render", { request });
assert.equal(parseStatus(await previewInvoke("shorts_status", { outputPath: request.outputPath })).state, "RUNNING");
await previewInvoke("shorts_cancel", { outputPath: request.outputPath });
assert.equal(parseStatus(await previewInvoke("shorts_status", { outputPath: request.outputPath })).state, "CANCELLED");
await previewInvoke("shorts_render", { request });
const now = Date.now;
try {
  Date.now = () => now() + 7000;
  const completed = parseStatus(await previewInvoke("shorts_status", { outputPath: request.outputPath }));
  assert.equal(completed.state, "COMPLETED");
  assert.equal(completed.outputs.length, 3);
  assert.equal(completed.outputs[0].path, "preview/shorts/short-1.mp4");
  assert.match(completed.outputs[0].text, /Xem bản đầy đủ/);
} finally { Date.now = now; }

// Inspector polling and late cue/status responses must remain attached to their job.
class Node {
  listeners = {}; children = []; hidden = false; disabled = false; value = ""; textContent = "";
  addEventListener(name, handler) { this.listeners[name] = handler; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  reset() { nodes.get("#shorts-layout").value = "auto"; }
}
const nodes = new Map(["section", "form", "fields", "cancel", "ai", "add", "layout", "state", "rows", "results", "provider"].map(id => [`#shorts-${id}`, new Node()]));
const timers = new Map(), calls = [], pending = [];
let timer = 0, status = null, deferStatus = false, resolveOldStatus;
globalThis.document = { querySelector: selector => nodes.get(selector), createElement: () => new Node() };
globalThis.window = { setInterval: fn => { timers.set(++timer, fn); return timer; }, clearInterval: id => timers.delete(id) };
const node = id => nodes.get(`#shorts-${id}`);
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
const view = mountShorts(async (command, args) => {
  calls.push([command, args]);
  if (command === "review_cues") return new Promise(resolve => pending.push(resolve));
  if (command === "shorts_status") {
    if (deferStatus) { deferStatus = false; return new Promise(resolve => { resolveOldStatus = resolve; }); }
    return status;
  }
  if (command === "shorts_render") { status = { state: "RUNNING", message: "Đang chạy", done: 0, total: 1, outputs: [] }; return; }
  if (command === "shorts_cancel") { status = { state: "CANCELLED", message: "Đã hủy", done: 0, total: 1, outputs: [] }; return; }
}, { provider: () => "gemini", read: () => "" });
const job = id => ({ id, outputPath: `${id}/final_vi.mp4`, status: { state: "COMPLETED" } });
view.select({ ...job("queued"), status: { state: "RUNNING" } });
assert.equal(node("section").hidden, true);
view.select(job("a"));
view.select(job("b"));
pending[1]([{ start_ms: 0, end_ms: 50000, text: "Video B" }]);
await flush();
pending[0]([{ start_ms: 0, end_ms: 50000, text: "Video A" }]);
await flush();
assert.match(node("provider").textContent, /Gemini \(Google\)/);
await node("ai").listeners.click();
assert.match(node("state").textContent, /khóa Gemini/);
node("add").listeners.click();
assert.equal(node("rows").children[0].children[4].textContent, "Video B", "old job's subtitles cannot replace current preview");
const titleInput = node("rows").children[0].children[1].children[0];
titleInput.value = "Tiêu đề riêng"; titleInput.listeners.input();
view.select(job("b"));
assert.equal(node("rows").children[0].children[1].children[0].value, "Tiêu đề riêng", "inspector refresh keeps edits");
await node("form").listeners.submit({ preventDefault() {} });
const rendered = calls.find(([cmd]) => cmd === "shorts_render")[1].request;
assert.equal(rendered.outputPath, "b/final_vi.mp4");
assert.equal(rendered.items[0].title, "Tiêu đề riêng");
assert.equal(node("fields").disabled, true);
assert.equal(node("cancel").hidden, false);
await node("cancel").listeners.click();
await [...timers.values()][0]();
assert.equal(node("fields").disabled, false);
assert.equal(timers.size, 0);
view.select(job("c"));
status = { state: "COMPLETED", message: "Xong", done: 1, total: 1, outputs: [{ path: "c/shorts/short-1.mp4", text_path: "c/shorts/short-1.txt", title: "Hay", text: "Nội dung" }] };
// Force a new read to pick up completed output after selection's initial null/cancelled read.
await [...timers.values()][0]();
assert.equal(node("results").children.length, 1);
node("results").children[0].children[1].listeners.click();
assert.ok(calls.some(([cmd, args]) => cmd === "open_path" && args.path === "c/shorts/short-1.mp4"));
const oldNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
let copied = "";
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async text => { copied = text; } } } });
node("results").children[0].children[2].listeners.click();
await flush();
assert.equal(copied, "Nội dung");
if (oldNavigator) Object.defineProperty(globalThis, "navigator", oldNavigator); else delete globalThis.navigator;
deferStatus = true;
view.select(job("d"));
node("add").listeners.click();
await node("form").listeners.submit({ preventDefault() {} });
resolveOldStatus(status = { ...status, state: "COMPLETED", message: "Old result", outputs: [] });
await flush();
assert.equal(node("fields").disabled, true, "status read from before submit cannot finish the current render");
view.select(null);
assert.equal(node("section").hidden, true);
assert.equal(timers.size, 0);
delete globalThis.document;
delete globalThis.window;
console.log("shorts tests passed");
