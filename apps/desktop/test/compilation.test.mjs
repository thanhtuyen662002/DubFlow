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
// One-shot timers (re-reading the edited versions while their Kiểm tra kỹ thuật runs) are kept apart from the polls.
const timeouts = new Map(); let timeout = 0;
globalThis.window = { setInterval: fn => { timers.set(++timer, fn); return timer; }, clearInterval: id => timers.delete(id),
  setTimeout: fn => { timeouts.set(++timeout, fn); return timeout; }, clearTimeout: id => timeouts.delete(id) };
const pastDir = "D:/Videos/DubFlow Output/Tong hop - Cũ/run-00000000a1b2";
const past = { dir: pastDir, created: 1_793_500_000, video: `${pastDir}/compilation.mp4`,
  spec: { schema_version: 1, title: "Bản cũ", layout: "toan-man-hinh", voice_id: "vi-test", speed: 1.2, music: null, music_volume: 0.3, intro: "Chào.", outro: "",
    items: [{ video: jobs[2].outputPath, title: "C", lead: "", score: 7, pros: ["Hay"], cons: [], start_ms: 1500, end_ms: null },
      { video: "D:/Videos/DubFlow Output/khac/final_vi.mp4", title: "Khác", lead: "", score: null, pros: [], cons: [], start_ms: null, end_ms: null }] },
  status: { state: "COMPLETED", message: "Đã tạo video tổng hợp.", done: 4, total: 4, path: `${pastDir}/compilation.mp4` } };
const calls = []; let state = { state: "RUNNING", message: "Đang chạy", done: 0, total: 2 };
let listed = [past];
// A compilation this page did not start (a re-render, or one still running from before a restart).
const otherDir = "D:/Videos/DubFlow Output/Tong hop - Khác/run-00000000c3d4";
let otherState = { state: "RUNNING", message: "Đang dựng phần 1/2", done: 1, total: 2 };
const edited = [];
// Kiểm tra kỹ thuật (Issue #935) of the edited version, as edit_status attaches it.
const checkBase = { schema_version: 1, kind: "export_check", advisory: true, video: "D:/Videos/Bản cũ (sửa 1).mp4", warnings: [], unmeasured: [], black: [], frozen: [] };
let editCheck = { ...checkBase, state: "RUNNING", percent: 40, checks: [] };
const session = createAiSession();
const view = mountCompilation(async (command, args) => {
  calls.push([command, args]);
  if (command === "voice_catalog") return { schema_version: 1, default_voice_id: "vi-test", voices: [] };
  if (command === "compilation_runs") return listed;
  if (command === "compilation_render") return "preview/output";
  if (command === "compilation_status") return args.outputDir === otherDir ? otherState : state;
  if (command === "compilation_description") return "00:00 Phần 1\nNguồn: Tác giả";
  if (command === "compilation_cancel") { if (args.outputDir === otherDir) otherState = { state: "CANCELLED", message: "Đã hủy" }; else state = { state: "CANCELLED", message: "Đã hủy" }; }
  if (command === "edit_status") return { status: null, exports: [{ path: "D:/Videos/Bản cũ (sửa 1).mp4", created: 1_793_600_000, duration: 120, check: editCheck }] };
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
const checkLine = () => node("export-list").children[0].children[0].children[2];
assert.equal(checkLine().textContent, "Đang kiểm tra kỹ thuật… 40% — video đã lưu và xem được ngay; muốn đổi tên, di chuyển hay xóa tệp thì bấm Dừng kiểm tra trước.", "a running check shows its percent");
assert.equal(timeouts.size, 1, "and the list is read again while it runs");
editCheck = { ...checkBase, state: "DONE", percent: 100, warnings: ["black"], black: [[4, 7]], checks: [
  { id: "decode", label: "Giải mã", ok: true, detail: "đọc hết 12,0 giây, không có lỗi" },
  { id: "black", label: "Hình đen", ok: false, detail: "đen 3,0 giây ở 0:04–0:07" },
  { id: "subtitle_frame", label: "Phụ đề trong khung", ok: null, detail: "không có phụ đề in vào hình" }] };
await [...timeouts.values()][0](); await flush();
// a warning does not hide what was not measured, in the line or its tooltip
assert.equal(checkLine().textContent, "Kiểm tra kỹ thuật: 1 điểm cần xem. Chưa đo: Phụ đề trong khung.");
assert.ok(checkLine().className.includes("mqc-warn"));
assert.match(checkLine().title, /Hình đen: đen 3,0 giây ở 0:04–0:07/);
assert.match(checkLine().title, /Chưa đo — Phụ đề trong khung: không có phụ đề in vào hình/);
assert.match(checkLine().title, /Chỉ để tham khảo/);
assert.equal(timeouts.size, 0, "a finished check is not read again");
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
state = { state: "RUNNING", message: "Đang chạy", done: 0, total: 2 };
node("render").listeners.click(); await flush();
const again = calls.slice(reused).find(([cmd]) => cmd === "compilation_render")[1].request;
assert.deepEqual(again.items.map(i => [i.video, i.startMs, i.score]), [[jobs[2].outputPath, 1500, 7], ["D:/Videos/DubFlow Output/khac/final_vi.mp4", null, null]],
  "a part whose video left the queue is still sent; the host checks it");
// Issue #919: the run's end first seen by choosing its card, before the next poll, still unlocks the page.
assert.equal(node("fields").disabled, true);
const statusReads = () => calls.filter(([cmd]) => cmd === "compilation_status").length;
state = { state: "COMPLETED", message: "Xong", path: "preview/output/compilation.mp4", done: 2, total: 2 };
let reads = statusReads();
node("runs").children[0].listeners.click(); await flush(); await flush();
assert.equal(statusReads(), reads + 1, "choosing a running card reads its status");
assert.equal(node("fields").disabled, false, "the read that saw the end unlocks the page");
assert.equal(node("cancel").hidden, true);
assert.equal(node("cancel").disabled, false);
assert.equal(timers.size, 0, "nothing is left to poll");
reads = statusReads();
node("runs").children[0].listeners.click(); await flush(); await flush();
assert.equal(statusReads(), reads, "a finished run is not read again when chosen");
// A running compilation this page did not start: listed by ⟳, cancelled from its own overlay.
listed = [{ dir: otherDir, created: 1_793_700_000, spec: { ...past.spec, title: "Khác" }, status: otherState }, past];
await node("refresh").listeners.click(); await flush(); await flush();
node("runs").children.find((item) => item.children[1].children[0].textContent === "Khác").listeners.click(); await flush(); await flush();
assert.equal(node("busy").hidden, false);
assert.equal(node("busy-cancel").hidden, false, "Hủy ghép is offered for a run this page did not start");
const stateLine = node("state").textContent;
await node("busy-cancel").listeners.click();
assert.ok(calls.some(([cmd, args]) => cmd === "compilation_cancel" && args.outputDir === otherDir));
assert.equal(node("cancel").disabled, false, "the setup's Hủy, for the page's own run, is left as it was");
assert.equal(node("state").textContent, stateLine, "the setup's state line is not about the other run");
await [...timers.values()][0]();
assert.equal(node("failed").hidden, false, "the other run shows cancelled");
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
