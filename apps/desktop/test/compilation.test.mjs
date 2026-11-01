import assert from "node:assert/strict";
import { buildRequest, completedVideos, count, milliseconds, moveItem, newItem, parseRuns, parseStatus, runFraction, specToForm } from "../src/features/compilation/model.ts";
import { normalizeEditorial, writeEditorial } from "../src/features/compilation/ai.ts";
import { createAiSession } from "../src/features/ai/provider.ts";
import { previewInvoke } from "../src/shell/preview_invoke.ts";
import { mountCompilation } from "../src/features/compilation/view.ts";

const jobs = ["a", "b", "c"].map(id => ({ id, displayName: `${id}.mp4`, status: { state: "COMPLETED" }, outputPath: `preview/${id}/final_vi.mp4` }));
assert.equal(completedVideos([...jobs, { ...jobs[0], status: { state: "RUNNING" } }, { ...jobs[0], outputPath: null }]).length, 3);
const items = jobs.slice(0, 2).map(newItem);
assert.deepEqual(moveItem(items, 1, -1).map(item => item.id), ["b", "a"]);
assert.deepEqual(moveItem(items, 0, -1), items);
assert.equal(count("🌱đ"), 2);
assert.equal(milliseconds(""), null);
assert.equal(milliseconds("1.234"), 1234);
for (const value of ["-1", "NaN", "Infinity", "1e30"]) assert.equal(milliseconds(value), "invalid");
const form = { title: "Tổng hợp", layout: "bang-thong-tin", intro: "Xin chào.", outro: "", items, voiceId: "vi-test", speed: 1.3,
  music: null, musicVolume: 0.2, outputRoot: null, cpuOnly: true };
const request = buildRequest(form);
assert.equal(typeof request, "object");
assert.deepEqual(request.items.map(i => i.video), items.map(i => i.video));
assert.equal(request.items[0].score, null);
for (const change of [{ title: "đ".repeat(121) }, { intro: "a".repeat(801) }, { outro: "!!!" }, { intro: "\0" }, { items: [] },
  { items: [items[0]] }, { items: Array(31).fill(items[0]) }, { voiceId: "" }, { layout: "bad" }, { speed: NaN },
  { speed: 1.4 }, { musicVolume: Infinity }, { musicVolume: -1 }, { music: "x.mp4" }]) assert.equal(typeof buildRequest({ ...form, ...change }), "string");
for (const change of [{ title: "a".repeat(81) }, { lead: "a".repeat(401) }, { score: "8.5" }, { score: "11" }, { score: "NaN" },
  { pros: "a\nb\nc\nd" }, { cons: "a".repeat(61) }, { start: "-1" }, { start: "2", end: "1" }]) {
  assert.equal(typeof buildRequest({ ...form, items: [{ ...items[0], ...change }, items[1]] }), "string");
}
assert.equal(buildRequest({ ...form, items: [{ ...items[0], score: "0", start: "1.25", end: "3" }, items[1]] }).items[0].startMs, 1250);
assert.equal(parseStatus({ state: "COMPLETED", message: "Xong" }), null);
assert.equal(parseStatus({ state: "COMPLETED", message: "Xong", path: "preview/compilation.mp4" }).state, "COMPLETED");
assert.equal(parseStatus({ state: "bad", message: "X" }), null);
// Earlier compilations (Issue #426): the list, a run's progress and its request reused for a new one.
const runs = parseRuns([{ dir: "D:/o/run-1", created: 5, spec: { title: "T", layout: "toan-man-hinh", items: [{}, {}] },
  status: { state: "COMPLETED", message: "Xong", path: "D:/o/run-1/compilation.mp4" }, video: null }, { dir: "" }, null, { dir: "D:/o/run-2", spec: null, status: { state: "x" } }]);
assert.deepEqual(runs.map(run => [run.title, run.layout, run.parts, run.video, run.status?.state ?? null]),
  [["T", "toan-man-hinh", 2, "D:/o/run-1/compilation.mp4", "COMPLETED"], ["Video tổng hợp", "bang-thong-tin", 0, null, null]]);
assert.equal(runFraction({ state: "RUNNING", message: "", done: 1, total: 4, path: null }), 0.25);
assert.equal(runFraction({ state: "RUNNING", message: "", done: 0, total: 0, path: null }), null);
assert.equal(runFraction(null), null);
const draft = specToForm({ title: "T", layout: "x", intro: "I", outro: "", voice_id: "v", speed: 1.2, music: "D:/n.mp3", music_volume: 0.3,
  items: [{ video: jobs[0].outputPath, title: "A", lead: "L", score: 0, pros: ["p", 3], cons: [], start_ms: 1500, end_ms: 4000 }, { video: "" }, { video: "D:/x/final_vi.mp4", title: "X" }] }, jobs);
assert.equal(draft.layout, "bang-thong-tin");
assert.deepEqual(draft.items.map(item => [item.id, item.score, item.pros, item.start, item.end]), [["a", "0", "p", "1.5", "4"], ["D:/x/final_vi.mp4", "", "", "", ""]]);
assert.equal(specToForm({ items: "x" }), null);

const aiValue = { intro: "  Xin chào 🌱 #tag ", outro: "Tạm biệt.", items: [
  { id: "b", title: "B", lead: "*Nhận xét*", score: 8, pros: ["x".repeat(80), "b", "c", "d"], cons: [] },
  { id: "unknown", title: "bad" }, { id: "b", title: "duplicate" }, { id: "a", title: "A", lead: "a".repeat(500), score: "10", cons: [1, "x"] },
] };
const normalized = normalizeEditorial(aiValue, items);
assert.equal(normalized.items.size, 2);
assert.equal(normalized.intro, "Xin chào");
assert.equal(normalized.items.get("b").score, "8");
assert.equal(normalized.items.get("a").score, "");
assert.equal(normalized.items.get("a").lead.length, 400);
assert.equal(normalized.items.get("b").pros.split("\n").length, 3);
for (const provider of ["claude", "gemini"]) {
  let sent;
  await writeEditorial(provider, "test-key", "Tổng hợp", items.map(item => ({ ...item, subtitles: Array(90).fill("Nội dung thật") })), async (p, key, prompt) => {
    assert.equal(p, provider); assert.equal(key, "test-key"); sent = prompt;
    return aiValue;
  });
  const payload = JSON.parse(sent.parts[0].text);
  assert.equal(payload.items[0].subtitles.length, 60);
  assert.equal(payload.items[0].video, undefined, "local paths are never sent to AI");
  assert.match(sent.system, /Không bịa/);
}

// A small DOM adapter exercises the list → selection → reorder → request → cancel → result actions → editor → reuse.
const nodes = new Map();
class Node {
  listeners = {}; children = []; value = ""; hidden = false; disabled = false; textContent = ""; options = []; dataset = {}; style = {}; attributes = {};
  classList = { names: new Set(), toggle: (name, on) => { if (on ?? !this.classList.names.has(name)) this.classList.names.add(name); else this.classList.names.delete(name); },
    add: name => this.classList.names.add(name), remove: name => this.classList.names.delete(name), contains: name => this.classList.names.has(name) };
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; if (this.tag === "select") this.options = children; }
  querySelector(selector) { return nodes.get(selector) ?? null; }
  querySelectorAll() { return []; }
  pause() {} load() {} play() { return Promise.resolve(); }
  set innerHTML(html) {
    for (const match of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)) {
      const node = new Node(); node.tag = match[1]; nodes.set(`#${match[2]}`, node);
    }
  }
}
class Input extends Node {}
globalThis.HTMLInputElement = Input;
const root = new Node(); nodes.set("#compilation-view", root);
globalThis.document = { querySelector: s => nodes.get(s), createElement: tag => tag === "input" ? new Input() : Object.assign(new Node(), { tag }) };
const timers = new Map(); let timer = 0;
globalThis.window = { setInterval: fn => { timers.set(++timer, fn); return timer; }, clearInterval: id => timers.delete(id) };
const pastDir = "D:/Videos/DubFlow Output/Tong hop - Cũ/run-00000000a1b2";
const past = { dir: pastDir, created: 1_793_500_000, video: `${pastDir}/compilation.mp4`,
  spec: { schema_version: 1, title: "Bản cũ", layout: "toan-man-hinh", voice_id: "vi-test", speed: 1.2, music: null, music_volume: 0.3, intro: "Chào.", outro: "",
    items: [{ video: jobs[2].outputPath, title: "C", lead: "", score: 7, pros: ["Hay"], cons: [], start_ms: 1500, end_ms: null },
      { video: "D:/Videos/DubFlow Output/khac/final_vi.mp4", title: "Khác", lead: "", score: null, pros: [], cons: [], start_ms: null, end_ms: null }] },
  status: { state: "COMPLETED", message: "Đã tạo video tổng hợp.", done: 4, total: 4, path: `${pastDir}/compilation.mp4` } };
const calls = []; let state = { state: "RUNNING", message: "Đang chạy", done: 0, total: 2 };
const edited = [];
const session = createAiSession();
const view = mountCompilation(async (command, args) => {
  calls.push([command, args]);
  if (command === "voice_catalog") return { schema_version: 1, default_voice_id: "vi-test", voices: [] };
  if (command === "compilation_runs") return [past];
  if (command === "compilation_render") return "preview/output";
  if (command === "compilation_status") return state;
  if (command === "compilation_description") return "00:00 Phần 1\nNguồn: Tác giả";
  if (command === "compilation_cancel") state = { state: "CANCELLED", message: "Đã hủy" };
  if (command === "edit_status") return { status: null, exports: [{ path: "D:/Videos/Bản cũ (sửa 1).mp4", created: 1_793_600_000, duration: 120 }] };
}, session, { jobs: () => jobs, outputRoot: () => null, cpuOnly: () => true, roots: () => ["D:/Videos"], edit: video => edited.push(video) });
const node = name => nodes.get(`#compilation-${name}`);
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
view.show();
await flush(); await flush();
assert.ok(calls.some(([cmd, args]) => cmd === "compilation_runs" && args.roots[0] === "D:/Videos"));
assert.equal(node("runs").children.length, 1, "earlier compilations are listed");
assert.equal(node("fields").hidden, false, "a new compilation starts on its setup");
for (const row of node("available").children.slice(0, 2)) {
  const check = row.children[0].children[0]; check.checked = true; check.listeners.change();
}
assert.equal(node("order").children.length, 2);
node("order").children[1].children[1].listeners.click();
assert.match(node("order").children[0].children[0].textContent, /b/);
node("title").value = "Tổng hợp"; node("voice").value = "vi-test"; node("layout").value = "bang-thong-tin";
node("speed").value = "1.1"; node("volume").value = "15";
node("render").listeners.click();
await flush();
const submitted = calls.find(([cmd]) => cmd === "compilation_render")[1].request;
assert.deepEqual(submitted.items.map(i => i.video), [jobs[1].outputPath, jobs[0].outputPath]);
assert.equal(node("fields").disabled, true);
assert.equal(node("cancel").hidden, false);
assert.equal(node("runs").children.length, 2, "the new compilation joins the list");
assert.equal(node("busy").hidden, false, "its progress shows over the player");
assert.match(node("runs").children[0].children[1].children[1].children[1].textContent, /Đang chạy/);
await node("cancel").listeners.click();
await [...timers.values()][0]();
assert.equal(node("fields").disabled, false);
assert.equal(timers.size, 0);
assert.equal(node("failed").hidden, false, "a cancelled compilation says so over the player");
state = { state: "RUNNING", message: "Đang chạy", done: 0, total: 2 };
node("new").listeners.click();
node("render").listeners.click(); await flush();
state = { state: "COMPLETED", message: "Xong", path: "preview/output/compilation.mp4", done: 2, total: 2 };
await [...timers.values()][0]();
assert.equal(node("results").hidden, false);
assert.equal(node("edit").hidden, false, "a finished compilation opens in the editor");
node("open").listeners.click(); node("folder").listeners.click();
assert.ok(calls.some(([cmd, args]) => cmd === "open_path" && args.path === state.path));
assert.ok(calls.some(([cmd, args]) => cmd === "open_folder" && args.path === "preview/output"));
node("edit").listeners.click();
assert.deepEqual(edited, [{ outputPath: "preview/output/compilation.mp4", displayName: "Tổng hợp" }]);
await flush();
assert.ok(calls.some(([cmd, args]) => cmd === "edit_status" && args.outputPath === state.path), "Kết quả lists the edited versions");
assert.equal(node("export-list").children.length, 1);
const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
let copied = "";
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async text => { copied = text; } } } });
await node("copy").listeners.click();
assert.match(copied, /Nguồn: Tác giả/);
assert.ok(calls.some(([cmd, args]) => cmd === "compilation_description" && args.outputDir === "preview/output"));
if (originalNavigator) Object.defineProperty(globalThis, "navigator", originalNavigator); else delete globalThis.navigator;
// An earlier compilation: its setup read-only, reused for a new one.
node("runs").children.at(-1).listeners.click();
assert.equal(node("fields").hidden, true);
assert.equal(node("setup-past").hidden, false);
assert.equal(node("past-parts").children.length, 2);
node("reuse").listeners.click();
assert.equal(node("fields").hidden, false);
assert.equal(node("title").value, "Bản cũ");
assert.equal(node("layout").value, "toan-man-hinh");
assert.deepEqual(node("order").children.map(row => row.children[0].textContent), ["Phần 1: C", "Phần 2: Khác"]);
const reused = calls.length;
node("render").listeners.click(); await flush();
const again = calls.slice(reused).find(([cmd]) => cmd === "compilation_render")[1].request;
assert.deepEqual(again.items.map(i => [i.video, i.startMs, i.score]), [[jobs[2].outputPath, 1500, 7], ["D:/Videos/DubFlow Output/khac/final_vi.mp4", null, null]],
  "a part whose video left the queue is still sent; the host checks it");
delete globalThis.document; delete globalThis.window; delete globalThis.HTMLInputElement;

const outputDir = await previewInvoke("compilation_render", { request });
assert.equal((await previewInvoke("compilation_status", { outputDir })).state, "RUNNING");
await previewInvoke("compilation_cancel", { outputDir });
assert.equal((await previewInvoke("compilation_status", { outputDir })).state, "CANCELLED");
const completedDir = await previewInvoke("compilation_render", { request });
const now = Date.now;
try {
  Date.now = () => now() + 100000;
  assert.equal((await previewInvoke("compilation_status", { outputDir: completedDir })).state, "COMPLETED");
} finally { Date.now = now; }
assert.match(await previewInvoke("compilation_description", { outputDir: completedDir }), /Nguồn:/);
console.log("compilation tests passed");
