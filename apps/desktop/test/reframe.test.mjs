import assert from "node:assert/strict";
import { buildRequest, defaults, parseInfo, parseStatus, previewBand } from "../src/features/reframe/model.ts";
import { fillPanel, normalizePanel } from "../src/features/reframe/ai.ts";
import { mountReframe } from "../src/features/reframe/view.ts";
import { previewInvoke } from "../src/shell/preview_invoke.ts";

const request = buildRequest("preview/final_vi.mp4", defaults());
assert.deepEqual(request, { outputPath: "preview/final_vi.mp4", mode: "bang-thong-tin", title: "", score: null,
  pros: [], cons: [], summary: "", focusStart: 50, focusEnd: 50, startMs: 0, endMs: null });
for (const changes of [{ mode: "bad" }, { title: "x".repeat(81) }, { title: "x\n" }, { summary: "x".repeat(121) },
  { score: "11" }, { score: "1.5" }, { score: "1e0" }, { pros: "a\nb\nc\nd" }, { cons: "x".repeat(61) },
  { cons: "x\0" }, { focusStart: -1 }, { focusEnd: 101 }, { focusStart: NaN }, { focusEnd: Infinity },
  { start: "1" }, { end: "00:60" }, { start: "00:02", end: "00:01" }, { start: "00:01", end: "00:01" }]) {
  assert.equal(typeof buildRequest(request.outputPath, { ...defaults(), ...changes }), "string");
}
assert.equal(typeof buildRequest("", defaults()), "string");
assert.equal(buildRequest(request.outputPath, { ...defaults(), title: "🌱".repeat(80) }).title.length, 160);
assert.equal(buildRequest(request.outputPath, { ...defaults(), score: "0" }).score, 0);
assert.deepEqual(buildRequest(request.outputPath, { ...defaults(), pros: " a \r\nb\n\nc", start: "00:01.250", end: "01:30" }).pros, ["a", "b", "c"]);
assert.equal(buildRequest(request.outputPath, { ...defaults(), start: "00:01.250", end: "01:30" }).startMs, 1250);
assert.equal(buildRequest(request.outputPath, { ...defaults(), mode: "cat-ngang", score: "invalid" }).score, null);
for (const focus of [0, 50, 100]) {
  const band = previewBand(90, 160, focus);
  assert.equal(band.height, 90 * 9 / 16 / 160);
  assert.equal(band.top, (1 - band.height) * focus / 100);
}
for (const value of [null, {}, { width: 0, height: 100, vertical: true }, { width: 90, height: 160, vertical: false }]) assert.equal(parseInfo(value), null);
assert.equal(parseInfo({ width: 90, height: 160, vertical: true }).vertical, true);
assert.equal(parseInfo({ width: 160, height: 90, vertical: false }).vertical, false);
for (const state of ["RUNNING", "FAILED", "CANCELLED"]) assert.equal(parseStatus({ state, message: "Test" }).state, state);
for (const value of [null, {}, { state: "OTHER", message: "Test" }, { state: "COMPLETED", message: "Test" }]) assert.equal(parseStatus(value), null);

const normalized = normalizePanel({ title: " **Tiêu đề** 🌱 #tag ", score: 8, pros: ["a", "b", "c", "d"],
  cons: [false, "Nhược\nđiểm", "x".repeat(100)], summary: "Tóm tắt 🌱 " + "đ".repeat(130) });
assert.equal(normalized.title, "Tiêu đề");
assert.equal(normalized.score, "8");
assert.equal(normalized.pros, "a\nb\nc");
assert.equal(normalized.cons.split("\n")[0], "Nhược điểm");
assert.equal([...normalized.cons.split("\n")[1]].length, 60);
assert.equal([...normalized.summary].length, 120);
assert.equal(normalizePanel(null).score, "");
assert.equal(normalizePanel({ score: 1.5 }).score, "");
let sent;
const cues = Array.from({ length: 80 }, (_, i) => ({ start_ms: i * 1000, end_ms: i * 1000 + 900, text: `Câu ${i} ${"x".repeat(400)}`, privateNote: "DO NOT SEND" }));
const panel = await fillPanel("gemini", "test-key", cues, async (provider, key, input) => {
  assert.equal(provider, "gemini"); assert.equal(key, "test-key"); sent = input;
  return { title: "Bảng", score: null, pros: [], cons: [], summary: "Tóm tắt" };
});
assert.equal(panel.summary, "Tóm tắt");
const payload = JSON.parse(sent.parts[0].text);
assert.deepEqual(Object.keys(payload), ["subtitles"]);
assert.equal(payload.subtitles.length, 60);
assert.ok(payload.subtitles.every(line => [...line].length <= 300));
assert.doesNotMatch(sent.parts[0].text, /DO NOT SEND|start_ms|test-key/);
await assert.rejects(fillPanel("claude", "key", [], async () => ({})), /phụ đề/);
await assert.rejects(fillPanel("claude", "key", cues, async () => ({})), /nội dung/);

assert.equal((await previewInvoke("reframe_info", { outputPath: request.outputPath })).vertical, true);
assert.equal(await previewInvoke("reframe_status", { outputPath: request.outputPath }), null);
await previewInvoke("reframe_render", { request });
assert.equal(parseStatus(await previewInvoke("reframe_status", { outputPath: request.outputPath })).state, "RUNNING");
await previewInvoke("reframe_cancel", { outputPath: request.outputPath });
assert.equal(parseStatus(await previewInvoke("reframe_status", { outputPath: request.outputPath })).state, "CANCELLED");
await previewInvoke("reframe_render", { request });
const now = Date.now;
try {
  Date.now = () => now() + 7000;
  const completed = parseStatus(await previewInvoke("reframe_status", { outputPath: request.outputPath }));
  assert.equal(completed.state, "COMPLETED");
  assert.equal(completed.path, "preview/final_vi_16x9.mp4");
} finally { Date.now = now; }

// Selection races, stale polls, mode controls and the native frame signature.
class Node {
  listeners = {}; hidden = false; disabled = false; value = ""; textContent = ""; style = {};
  addEventListener(name, handler) { this.listeners[name] = handler; }
  removeAttribute(name) { delete this[name]; }
  reset() {
    for (const id of ["title", "score", "pros", "cons", "summary", "start", "end"]) node(id).value = "";
    node("mode").value = "bang-thong-tin"; node("focus-start").value = node("focus-end").value = "50";
  }
}
const ids = ["section", "form", "fields", "mode", "title", "score", "pros", "cons", "summary", "start", "end", "focus-start", "focus-end",
  "ai", "cancel", "state", "image", "band", "preview", "provider", "panel", "crop", "focus-start-value", "focus-end-value", "preview-title", "preview-state", "results", "open", "folder"];
const nodes = new Map(ids.map(id => [`#reframe-${id}`, new Node()]));
const node = id => nodes.get(`#reframe-${id}`);
const calls = [], timers = new Map(), pending = new Map();
let timer = 0, status = null, deferStatus = false, resolveOldStatus;
globalThis.document = { querySelector: selector => nodes.get(selector) };
globalThis.window = { setInterval: fn => { timers.set(++timer, fn); return timer; }, clearInterval: id => timers.delete(id) };
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
const view = mountReframe(async (command, args) => {
  calls.push([command, args]);
  if (command === "reframe_info") return new Promise(resolve => pending.set(args.outputPath, resolve));
  if (command === "reframe_status") {
    if (deferStatus) { deferStatus = false; return new Promise(resolve => { resolveOldStatus = resolve; }); }
    return status;
  }
  if (command === "video_frame") return new Uint8Array([1, 2, 3]).buffer;
  if (command === "reframe_render") { status = { state: "RUNNING", message: "Đang chạy" }; return; }
  if (command === "reframe_cancel") { status = { state: "CANCELLED", message: "Đã hủy" }; return; }
}, { provider: () => "gemini", read: () => "" });
const job = id => ({ id, outputPath: `${id}/final_vi.mp4`, status: { state: "COMPLETED" } });
const ready = async (id, info = { width: 90, height: 160, vertical: true }) => {
  pending.get(job(id).outputPath)(info); await flush();
};
view.select({ ...job("queued"), status: { state: "RUNNING" } });
assert.equal(node("section").hidden, true);
view.select(job("a")); view.select(job("b"));
await ready("b"); await ready("a", { width: 160, height: 90, vertical: false });
assert.equal(node("section").hidden, false, "late landscape probe cannot hide selected portrait");
assert.match(node("provider").textContent, /Gemini \(Google\)/);
await node("ai").listeners.click();
assert.match(node("state").textContent, /khóa Gemini/);
node("mode").value = "cat-ngang"; node("mode").listeners.change(); await flush();
const frameCall = calls.find(([command]) => command === "video_frame");
assert.deepEqual(frameCall[1], { sourcePath: "b/final_vi.mp4", position: 0 });
assert.equal(node("panel").hidden, true);
assert.equal(node("preview").hidden, false);
node("focus-start").value = "100"; node("focus-start").listeners.input();
assert.equal(node("band").style.top, `${(1 - 90 * 9 / 16 / 160) * 100}%`);
node("title").value = "Tiêu đề riêng"; node("title").listeners.input();
assert.equal(node("preview-title").textContent, "Tiêu đề riêng");
view.select(job("b")); assert.equal(node("title").value, "Tiêu đề riêng", "inspector refresh preserves form edits");
await node("form").listeners.submit({ preventDefault() {} });
const rendered = calls.find(([command]) => command === "reframe_render")[1].request;
assert.equal(rendered.outputPath, "b/final_vi.mp4");
assert.equal(rendered.focusStart, 100); assert.equal(rendered.title, "Tiêu đề riêng");
assert.equal(node("fields").disabled, true); assert.equal(node("cancel").hidden, false);
await node("cancel").listeners.click(); await [...timers.values()][0]();
assert.equal(node("fields").disabled, false); assert.equal(timers.size, 0);
view.select(job("c"));
status = { state: "COMPLETED", message: "Xong", path: "c/final_vi_16x9.mp4", duration_ms: 1500 };
await ready("c");
assert.equal(node("results").hidden, false);
node("open").listeners.click(); node("folder").listeners.click();
assert.ok(calls.some(([command, args]) => command === "open_path" && args.path === status.path));
assert.ok(calls.some(([command, args]) => command === "reveal_path" && args.path === status.path));
view.select(job("d")); deferStatus = true; await ready("d");
await node("form").listeners.submit({ preventDefault() {} });
resolveOldStatus({ state: "COMPLETED", message: "Old result", path: "d/final_vi_16x9.mp4", duration_ms: 1500 });
await flush(); assert.equal(node("fields").disabled, true, "a poll from before submit cannot finish a new render");
view.select(job("landscape")); await ready("landscape", { width: 160, height: 90, vertical: false });
assert.equal(node("section").hidden, true);
view.select(null); assert.equal(timers.size, 0);
delete globalThis.document; delete globalThis.window;
console.log("reframe tests passed");
