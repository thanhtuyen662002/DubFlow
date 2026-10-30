import assert from "node:assert/strict";
import { buildRequest, charCount, parseInfo, parseStatus } from "../src/features/package/model.ts";
import { mountPackage } from "../src/features/package/view.ts";
import { previewInvoke } from "../src/shell/preview_invoke.ts";

const info = { has_background_stem: true, voice_id: "vi-test" };
const form = { outputPath: "preview/final_vi.mp4", intro: "Xin chào.", outro: "", voiceId: "vi-test", speed: 1,
  music: null, musicVolume: 0.15, background: "keep", cpuOnly: true };
assert.deepEqual(buildRequest(form, info), form);
assert.equal(charCount("🌱đ"), 2);
assert.equal(typeof buildRequest({ ...form, intro: "đ".repeat(600) }, info), "object");
for (const changes of [{ intro: "đ".repeat(601) }, { outro: "x".repeat(601) }, { intro: "" }, { intro: "!!!" },
  { intro: "\0" }, { voiceId: "" }, { speed: NaN }, { speed: 0.9 }, { speed: 1.4 }, { musicVolume: Infinity },
  { musicVolume: -1 }, { background: "other" }, { background: "replace" }, { music: "a.mp4" }]) {
  assert.equal(typeof buildRequest({ ...form, ...changes }, info), "string");
}
for (const background of ["keep", "replace", "duck"]) {
  assert.equal(typeof buildRequest({ ...form, intro: "", music: "preview/music.FLAC", background }, info), "object");
}
assert.match(buildRequest({ ...form, music: "a.wav", background: "duck" }, { ...info, has_background_stem: false }), /nền gốc/);
assert.deepEqual(parseInfo(info), info);
for (const value of [null, {}, { ...info, has_background_stem: 1 }, { ...info, voice_id: "" }]) assert.equal(parseInfo(value), null);
for (const state of ["RUNNING", "FAILED", "CANCELLED"]) assert.equal(parseStatus({ state, message: "Test" }).state, state);
assert.equal(parseStatus({ state: "COMPLETED", message: "Xong" }), null);
assert.equal(parseStatus({ state: "COMPLETED", message: "Xong", path: "preview/final_vi_packaged.mp4" }).state, "COMPLETED");
assert.equal(parseStatus({ state: "other", message: "Test" }), null);

class Node {
  listeners = {}; hidden = false; disabled = false; value = ""; textContent = "";
  addEventListener(name, handler) { this.listeners[name] = handler; }
  reset() {
    nodes.get("#package-intro").value = nodes.get("#package-outro").value = "";
    nodes.get("#package-background").value = "keep";
    nodes.get("#package-speed").value = "1.0";
    nodes.get("#package-volume").value = "15";
  }
}
const nodes = new Map(["section", "form", "fields", "intro", "outro", "background", "speed", "volume", "cancel",
  "state", "intro-count", "outro-count", "music-name", "music-clear", "background-row", "volume-row", "results",
  "music-pick", "volume-value", "open", "folder", "duck"].map(id => [`#package-${id}`, new Node()]));
globalThis.document = { querySelector: selector => nodes.get(selector) };
const timers = new Map();
let timer = 0;
globalThis.window = { setInterval: fn => { timers.set(++timer, fn); return timer; }, clearInterval: id => timers.delete(id) };
const calls = [], pending = [];
let status = null, picked = ["preview/music.mp3"];
let deferStatus = false, resolveOldStatus;
const view = mountPackage(async (command, args) => {
  calls.push([command, args]);
  if (command === "package_info") return new Promise((resolve, reject) => pending.push({ resolve, reject }));
  if (command === "package_status") {
    if (deferStatus) { deferStatus = false; return new Promise(resolve => { resolveOldStatus = resolve; }); }
    return status;
  }
  if (command === "pick_product_files") return picked;
  if (command === "package_video") { status = { state: "RUNNING", message: "Đang chạy" }; return; }
  if (command === "package_cancel") { status = { state: "CANCELLED", message: "Đã hủy" }; return; }
}, { cpuOnly: () => true });
const job = id => ({ id, outputPath: `${id}/final_vi.mp4`, status: { state: "COMPLETED" } });
const flush = async () => { await new Promise(resolve => setTimeout(resolve, 0)); };
const node = name => nodes.get(`#package-${name}`);
view.select({ ...job("queued"), status: { state: "RUNNING" } });
assert.equal(node("section").hidden, true);
view.select(job("a"));
view.select(job("b"));
pending[1].resolve({ ...info, has_background_stem: false });
await flush();
assert.equal(node("duck").disabled, true);
pending[0].resolve(info);
await flush();
assert.equal(node("duck").disabled, true, "late info cannot enable another job's duck option");
node("intro").value = "  Xin chào.  ";
node("intro").listeners.input();
assert.equal(node("intro-count").textContent, "13/600");
view.select(job("b"));
assert.equal(node("intro").value, "  Xin chào.  ", "inspector polls preserve typed text");
await node("music-pick").listeners.click();
assert.equal(node("background").value, "replace");
assert.equal(node("background-row").hidden, false);
node("music-clear").listeners.click();
assert.equal(node("background").value, "keep");
assert.equal(node("background-row").hidden, true);
await node("form").listeners.submit({ preventDefault() {} });
const request = calls.find(([command]) => command === "package_video")[1].request;
assert.equal(request.outputPath, "b/final_vi.mp4");
assert.equal(request.intro, "Xin chào.");
assert.equal(request.cpuOnly, true);
assert.equal(node("fields").disabled, true);
assert.equal(node("cancel").hidden, false);
await node("cancel").listeners.click();
await [...timers.values()][0]();
assert.equal(node("state").textContent, "Đã hủy");
assert.equal(node("fields").disabled, false);
assert.equal(timers.size, 0);
view.select(job("c"));
status = { state: "COMPLETED", message: "Xong", path: "c/final_vi_packaged.mp4" };
pending[2].resolve(info);
await flush();
assert.equal(node("results").hidden, false);
node("open").listeners.click();
node("folder").listeners.click();
assert.ok(calls.some(([cmd, args]) => cmd === "open_path" && args.path === status.path));
assert.ok(calls.some(([cmd, args]) => cmd === "reveal_path" && args.path === status.path));
view.select(job("d"));
deferStatus = true;
pending[3].resolve(info);
await flush();
node("intro").value = "Lần mới.";
await node("form").listeners.submit({ preventDefault() {} });
resolveOldStatus({ state: "COMPLETED", message: "Old result", path: "d/old.mp4" });
await flush();
assert.equal(node("fields").disabled, true, "a status read started before a new render cannot finish that render");
assert.equal(node("results").hidden, true);
view.select(null);
assert.equal(node("section").hidden, true);
assert.equal(timers.size, 0);
delete globalThis.document;
delete globalThis.window;

assert.equal((await previewInvoke("package_info", { outputPath: form.outputPath })).has_background_stem, true);
assert.equal(await previewInvoke("package_status", { outputPath: form.outputPath }), null);
await previewInvoke("package_video", { request: form });
assert.equal((await previewInvoke("package_status", { outputPath: form.outputPath })).state, "RUNNING");
await previewInvoke("package_cancel", { outputPath: form.outputPath });
assert.equal((await previewInvoke("package_status", { outputPath: form.outputPath })).state, "CANCELLED");
await previewInvoke("package_video", { request: form });
const now = Date.now;
try {
  Date.now = () => now() + 7000;
  const completed = await previewInvoke("package_status", { outputPath: form.outputPath });
  assert.equal(completed.state, "COMPLETED");
  assert.equal(completed.path, "preview/final_vi_packaged.mp4");
} finally { Date.now = now; }
console.log("package video tests passed");
