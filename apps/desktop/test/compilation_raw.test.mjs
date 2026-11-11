import assert from "node:assert/strict";
import {
  addSources, applySuggestedOrder, finalText, buildRawRequest, durationNote, emptyDraft, milliseconds, moveItem, numberRanks, openForExport, parseRawRun,
  partName, removeItem, removeSource, renderStats, sourceName, sourcesOf, specToDraft, supported,
} from "../src/features/compilation/raw_model.ts";
import { levelText, LEVELS } from "../src/features/mastering/model.ts";
import { parseRuns } from "../src/features/compilation/model.ts";

// Formats: only what the v2 engine reads; anything else is reported by name, never added.
assert.ok(supported("D:/a.MP4") && supported("C:\\x\\b.mkv") && supported("c.webm") && supported("d.m4v") && supported("e.avi") && supported("f.mov"));
assert.ok(!supported("a.flv") && !supported("a.ts") && !supported("noext") && !supported("a.mp4.txt"));

const draft = emptyDraft();
let added = addSources(draft, ["D:/v/one.mp4", "D:/v/two.MOV", "D:/v/three.flv", "D:/v/ONE.mp4", "D:/v/four_cat.webm"]);
assert.equal(added.added, 3);
assert.deepEqual(added.unsupported, ["three.flv"]);
assert.deepEqual(added.duplicate, ["ONE.mp4"]);
assert.deepEqual(draft.items.map((item) => item.title), ["one", "two", "four cat"], "each source is its own item, titled from its file");
added = addSources(draft, ["D:/v/extra.mkv"], draft.items[0].key);
assert.equal(sourcesOf(draft, draft.items[0].key).length, 2, "a source can join an existing item");
removeSource(draft, sourcesOf(draft, draft.items[0].key)[1].key);
assert.equal(sourcesOf(draft, draft.items[0].key).length, 1, "removed before the run");
const full = emptyDraft();
addSources(full, Array.from({ length: 40 }, (_, i) => `D:/v/${i}.mp4`));
assert.equal(full.items.length, 30);
assert.equal(sourcesOf(full, full.items[29].key).length, 8, "past 30 items, sources join the last item up to 8");
assert.equal(addSources(full, ["D:/v/more.mp4"]).full.length, 1);

// The user's order and ranks; never reordered by the model.
numberRanks(draft, true);
assert.deepEqual(draft.items.map((item) => item.rank), ["3", "2", "1"], "countdown: #N first");
moveItem(draft, draft.items[2].key, -1);
assert.deepEqual(draft.items.map((item) => item.title), ["one", "four cat", "two"]);
assert.deepEqual(draft.items.map((item) => item.rank), ["3", "1", "2"], "moving keeps each item's rank");

assert.equal(milliseconds(""), null);
assert.equal(milliseconds("1,5"), 1500);
for (const value of ["-1", "abc", "Infinity"]) assert.equal(milliseconds(value), "invalid");

// The request.
const options = { ai: null, outputRoot: null, cpuOnly: true };
assert.match(buildRawRequest(draft, options), /chủ đề/);
draft.title = "Top 3 khoảnh khắc";
draft.voiceId = "vi-test";
assert.match(buildRawRequest(draft, options), /Mục 1: chưa có lời bình/);
draft.items.forEach((item, index) => { item.narration = `Lời bình số ${index + 1}.`; });
sourcesOf(draft, draft.items[0].key)[0].start = "2"; sourcesOf(draft, draft.items[0].key)[0].end = "1";
assert.match(buildRawRequest(draft, options), /mốc cắt/);
sourcesOf(draft, draft.items[0].key)[0].end = "9,5";
draft.items[1].rank = "3";
assert.match(buildRawRequest(draft, options), /không trùng/);
draft.items[1].rank = "1";
const request = buildRawRequest(draft, options);
assert.equal(typeof request, "object", request);
assert.deepEqual(request.items.map((item) => [item.title, item.rank]), [["one", 3], ["four cat", 1], ["two", 2]], "sent in the user's order with the user's ranks");
assert.deepEqual(request.items[0].sources[0], { video: "D:/v/one.mp4", startMs: 2000, endMs: 9500, notes: null, rights: "unknown" });
assert.equal(request.ranking, "user");
assert.equal(request.ai, null);
assert.equal(request.mastering, "balanced");
assert.ok(!JSON.stringify(request).includes("key"), "no key in the request");
draft.ranking = "suggested";
assert.match(buildRawRequest(draft, options), /khóa/, "a suggestion needs an AI provider");
draft.useAi = true;
const suggested = buildRawRequest(draft, { ...options, ai: { provider: "gemini" } });
assert.equal(suggested.ranking, "suggested");
assert.equal(suggested.applySuggestion, false, "a suggestion is not applied unless the user asks");
assert.deepEqual(suggested.ai, { provider: "gemini" });
draft.ranking = "user"; draft.useAi = false;
const empty = emptyDraft();
empty.title = "x"; empty.voiceId = "v";
addSources(empty, ["D:/a.mp4", "D:/b.mp4"]);
empty.items.forEach((item) => { item.narration = "Có lời."; });
removeItem(empty, empty.items[1].key);
assert.match(buildRawRequest(empty, options), /2 đến 30/);

// Duration guide only.
draft.targetMinutes = "10";
assert.match(durationNote(draft), /ngắn hơn mục tiêu 10 phút.*không tự cắt/);
draft.targetMinutes = "";
assert.match(durationNote(draft), /^Lời bình đọc khoảng 0 phút/);

// Honest level texts for the raw path.
for (const level of LEVELS) assert.ok(levelText("compilation", level).length > 20, level);
assert.match(levelText("compilation", "off"), /như Nhẹ/);
assert.match(levelText("compilation", "light"), /không mờ dần/);
assert.match(levelText("compilation", "balanced"), /mờ dần.*6 giây/);
assert.match(levelText("compilation", "dynamic"), /Không đổi thứ hạng/);
assert.doesNotMatch(Object.values(LEVELS.map((level) => levelText("compilation", level))).join(" "), /zoom|phóng to|hiệu ứng âm thanh/i,
  "no claim of what the raw renderer does not do");

// A run's spec back into a draft: ids kept, so review decisions stay with their item.
const spec = { schema_version: 2, title: "Top", layout: "toan-man-hinh", voice_id: "vi-test", speed: 1.1, music: null, music_volume: 0.1,
  program: { kind: "top_n", ranking: "suggested", apply_suggestion: false }, hook: "Bạn sẽ bất ngờ.", intro: "Chào.", outro: "",
  mastering: { schema_version: 1, level: "dynamic", style: "entertainment" }, editorial: { ai: { provider: "gemini" }, draft_narration: false },
  items: [
    { item_id: "i1", rank: 2, title: "Mèo", narration: "Mèo nhảy.", notes: "", keep_source_audio: "on",
      sources: [{ video: "D:/v/cat.mp4", start_ms: 1000, end_ms: null, notes: "", rights: "owned" }] },
    { item_id: "i2", rank: 1, title: "Chó", narration: "Chó chạy.", notes: "", keep_source_audio: "auto",
      sources: [{ video: "D:/v/dog.mkv", start_ms: null, end_ms: null, notes: "", rights: "weird" }] },
  ] };
const back = specToDraft(spec);
assert.deepEqual(back.items.map((item) => [item.itemId, item.rank, item.keepAudio]), [["i1", "2", "on"], ["i2", "1", "auto"]]);
assert.equal(back.mastering, "dynamic");
assert.equal(back.sources[0].start, "1");
assert.equal(back.sources[1].rights, "unknown");
assert.equal(specToDraft({ schema_version: 1, items: [] }), null);
// The user applies the AI's suggested order: items move, each place keeps its rank number.
assert.equal(applySuggestedOrder(back, ["i2", "i1"]), true);
assert.deepEqual(back.items.map((item) => [item.itemId, item.rank]), [["i2", "2"], ["i1", "1"]]);
assert.equal(applySuggestedOrder(back, ["i1", "i9"]), false, "a stale suggestion changes nothing");
assert.deepEqual(back.items.map((item) => item.itemId), ["i2", "i1"]);
const edited = buildRawRequest(back, { ...options, ai: { provider: "gemini" } });
assert.deepEqual(edited.items.map((item) => item.itemId), ["i2", "i1"]);

// The run the host reads back.
const run = parseRawRun({
  dir: "D:/Videos/DubFlow Output/Tong hop - Top/run-0123456789ab", spec, video: "D:/x/compilation.mp4", final: null,
  status: { state: "COMPLETED", message: "Xong", done: 4, total: 4, unreviewed: 3 },
  program: {
    kind: "compilation_program", ranking: { ranking: "suggested", suggestion: { order: ["i2", "i1"], reasons: { i2: "Cao trào hơn", i1: 5 } } },
    parts: [{ id: "hook", kind: "hook", title: "Top", rank: null, narration: "Bạn sẽ bất ngờ.", sources: [], start_ms: 0, duration_ms: 3000 },
      { id: "i1", kind: "item", title: "Mèo", rank: 2, narration: "Mèo nhảy.", sources: ["i1-s1"], start_ms: 3000, duration_ms: 8000 },
      { id: "i2", kind: "item", title: "Chó", rank: 1, narration: "Chó chạy.", sources: ["i2-s1"], start_ms: 11000, duration_ms: 7000 }],
    review: { i1: { unreviewed: 2, accept_unreviewed: false, summary: { READY: 1 } }, i2: { unreviewed: 1, accept_unreviewed: true } },
    rendered_parts: ["i1"], reused_parts: ["hook", "i2"], warnings: ["Mục 1: x"], credits: ["Tác giả (Douyin)"],
  },
});
assert.equal(run.parts.length, 3);
assert.equal(run.parts[1].startMs, 3000);
assert.deepEqual(run.suggestion.reasons, { i2: "Cao trào hơn" });
assert.equal(run.applied, false);
assert.equal(partName(run.parts[0], "top_n"), "Câu mở màn");
assert.equal(partName(run.parts[1], "top_n"), "#2 · Mèo");
assert.equal(partName(run.parts[1], "list"), "Mèo");
const open = openForExport(run);
assert.deepEqual(open.parts.map((part) => part.id), ["i1"], "an accepted part is not open");
assert.equal(open.sentences, 2);
assert.match(renderStats(run), /dựng 1 phần, dùng lại 2 phần/);
assert.equal(sourceName("i2-s1", spec), "Mục 2 · nguồn 1 (dog.mkv)");
assert.equal(sourceName("v1", spec), "v1");
assert.equal(parseRawRun({ spec }), null);

// P2-6: "Bản cuối" says whether the final export is still the programme
assert.equal(run.finalState, null);
assert.equal(finalText(run), "");
const sha = "a".repeat(64);
const current = parseRawRun({ dir: "d", spec, final: "D:/x/final.mp4",
  final_state: { path: "D:/x/final.mp4", stale: false, reason: null, revision: 2, sha256: sha, exported_at_ms: 5 } });
assert.equal(current.finalState.stale, false);
assert.equal(current.finalState.sha256, sha);
assert.equal(finalText(current), "Bản cuối (lần xuất 2): D:/x/final.mp4");
const rerendered = parseRawRun({ dir: "d", spec, final: "D:/x/final.mp4", final_state: { stale: true, reason: "rerendered", revision: 1 } });
assert.match(finalText(rerendered), /^Bản cuối cũ: .*đã được dựng lại.*xuất bản cuối lại/);
assert.match(finalText(parseRawRun({ dir: "d", spec, final: "D:/x/final.mp4", final_state: { stale: true, reason: "rendering" } })), /đang được dựng lại/);
// an older host (no final_state) or a malformed one: never shown as the current final
const older = parseRawRun({ dir: "d", spec, final: "D:/x/final.mp4" });
assert.equal(older.finalState, null);
assert.match(finalText(older), /^Bản cuối cũ: .*không xác định/);
assert.equal(parseRawRun({ dir: "d", spec, final: "D:/x/final.mp4", final_state: { reason: "rerendered" } }).finalState.stale, true);

// The page's list tells the versions apart.
const listed = parseRuns([{ dir: "a", spec: { schema_version: 1, items: [] } }, { dir: "b", schema_version: 2, spec: { schema_version: 2, items: [] } },
  { dir: "c", schema_version: 9, spec: { title: "Sau" } }]);
assert.deepEqual(listed.map((item) => item.version), [1, 2, 9]);

// ---------- The page: raw mode beside the legacy flow ----------
const nodes = new Map();
class Node {
  listeners = {}; children = []; value = ""; hidden = false; disabled = false; textContent = ""; options = []; dataset = {}; style = {}; attributes = {};
  checked = false;
  classList = { names: new Set(), toggle: (name, on) => { if (on ?? !this.classList.names.has(name)) this.classList.names.add(name); else this.classList.names.delete(name); },
    add: (name) => this.classList.names.add(name), remove: (name) => this.classList.names.delete(name), contains: (name) => this.classList.names.has(name) };
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  append(...children) { this.children.push(...children); if (this.tag === "select") this.options.push(...children); }
  replaceChildren(...children) { this.children = children; if (this.tag === "select") this.options = children; }
  querySelector(selector) { return nodes.get(selector) ?? null; }
  querySelectorAll(selector) { return selector.includes("data-mode") ? [nodes.get("#compilation-mode-dubbed"), nodes.get("#compilation-mode-raw")] : []; }
  closest() { return null; }
  pause() {} load() {} play() { return Promise.resolve(); }
  set innerHTML(html) {
    for (const match of html.matchAll(/<(\w+)([^>]*)\bid="([^"]+)"([^>]*)>/g)) {
      const node = new Node(); node.tag = match[1]; nodes.set(`#${match[3]}`, node);
      const mode = /data-mode="(\w+)"/.exec(match[2] + match[4]);
      if (mode) node.dataset.mode = mode[1];
    }
  }
}
class Input extends Node {}
globalThis.HTMLInputElement = Input;
const root = new Node(); nodes.set("#compilation-view", root);
globalThis.document = { querySelector: (s) => nodes.get(s), createElement: (tag) => tag === "input" ? Object.assign(new Input(), { tag }) : Object.assign(new Node(), { tag }) };
const timers = new Map(); let timer = 0;
globalThis.window = { setInterval: (fn) => { timers.set(++timer, fn); return timer; }, clearInterval: (id) => timers.delete(id) };
const { mountCompilation } = await import("../src/features/compilation/view.ts");
const { createAiSession } = await import("../src/features/ai/provider.ts");
const rawDir = "D:/Videos/DubFlow Output/Tong hop - Top/run-0123456789ab";
const calls = [];
let rawStatus = { state: "RUNNING", message: "Đang dựng", done: 0, total: 3 };
const view = mountCompilation(async (command, args) => {
  calls.push([command, args]);
  if (command === "voice_catalog") return { schema_version: 1, default_voice_id: "vi-test", voices: [{ voice_id: "vi-test", approved: true, name: "Thử",
    gender: "Nữ", accent: "Nam", style: "tự nhiên", description: "Giọng thử", voice_version: "1" }] };
  if (command === "compilation_runs") return [{ dir: "D:/Videos/DubFlow Output/Tong hop - Moi/run-00000000000f", schema_version: 7, spec: { title: "Mới" }, created: 1 }];
  if (command === "pick_files") return ["D:/v/a.mp4", "D:/v/b.mov", "D:/v/c.flv"];
  if (command === "compilation_raw_render") return rawDir;
  if (command === "compilation_status") return { ...rawStatus, path: rawStatus.state === "COMPLETED" ? `${rawDir}/compilation.mp4` : null };
  if (command === "compilation_raw_open") return { dir: rawDir, spec, status: rawStatus, program: null, video: null, final: null };
}, createAiSession(), { jobs: () => [], outputRoot: () => null, cpuOnly: () => true, roots: () => ["D:/Videos"], fromQueueRaw: () => calls.push(["queue-raw"]) });
const node = (name) => nodes.get(`#compilation-${name}`);
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
view.show();
await flush(); await flush();
assert.equal(node("raw-setup").hidden, true, "the legacy flow stays the default");
assert.equal(node("fields").hidden, false);
node("mode-raw").listeners.click();
assert.equal(node("raw-setup").hidden, false);
assert.equal(node("fields").hidden, true, "the legacy form is hidden in raw mode, not changed");
assert.equal(node("render").hidden, true);
node("from-queue").listeners.click();
assert.ok(calls.some(([cmd]) => cmd === "queue-raw"), "Chọn từ Hàng đợi asks for raw sources in raw mode");
assert.match(view.addSources(["D:/v/x.mp4", "D:/v/y.avi", "D:/v/z.wmv"]), /Đã thêm 2 video.*Chưa hỗ trợ định dạng: z\.wmv/);
const [form, foot] = node("raw-setup").children;
const renderButton = foot.children[0], stateLine = foot.children[2];
await renderButton.listeners.click();
assert.match(stateLine.textContent, /chủ đề/, "the raw form checks before the host");
const titleInput = form.children[2].children[1].children[0].children[1];
titleInput.value = "Top 2";
titleInput.listeners.input();
const itemsList = form.children[3].children[3];
for (const item of itemsList.children) {
  const narration = item.children[1].children[1].children[1];
  narration.value = "Lời bình."; narration.listeners.input();
}
form.children[4].children[1].children[0].children[1].value = "";  // hook
const voiceSelect = form.children[5].children[1].children[0].children[1];
voiceSelect.value = "vi-test";
await renderButton.listeners.click();
await flush();
const sent = calls.find(([cmd]) => cmd === "compilation_raw_render");
assert.ok(sent, stateLine.textContent);
assert.equal(sent[1].request.title, "Top 2");
assert.deepEqual(sent[1].request.items.map((item) => item.sources[0].video), ["D:/v/x.mp4", "D:/v/y.avi"]);
assert.equal(sent[1].request.mastering, "balanced");
assert.equal(node("runs").children.length, 2, "the new raw run joins the list beside other runs");
assert.equal(node("raw-result").hidden, false, "Kết quả of a raw run is the raw panel");
assert.equal(node("result-card").hidden, true);
await flush();
assert.ok(calls.some(([cmd, args]) => cmd === "compilation_raw_open" && args.outputDir === rawDir));
// A run of an unknown version says why it cannot open.
node("runs").children.at(-1).listeners.click();
assert.match(node("result-note").textContent, /phiên bản DubFlow mới hơn \(định dạng 7\)/);
assert.match(node("past-note").textContent, /cập nhật DubFlow/);
delete globalThis.document; delete globalThis.window; delete globalThis.HTMLInputElement;
console.log("compilation raw tests passed");
