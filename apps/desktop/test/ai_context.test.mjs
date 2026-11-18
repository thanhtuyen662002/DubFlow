import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import {
  AI_CONTEXT_KEY, aiContextAnswered, aiContextHint, aiContextPrompt, decideAiContext, loadAiContext, providerReady, readAiContext,
  resetAiContextIfNoKey, saveAiContext, setKeysClosed,
} from "../src/features/ai/ai_context.ts";
import { translationJobArgs } from "../src/features/ai/translation_prefs.ts";

// "Dịch phụ đề bằng AI theo ngữ cảnh" is asked once before a saved key is used (Issue #934, Part L item 1).
// Everything here is fakes: no key, no network, no host call. Any fetch in this file is a bug.
const realFetch = globalThis.fetch;
globalThis.fetch = () => { throw new Error("no network in this test"); };

const LEGACY = "dubflow.translationPrefs.v1";
const memory = (entries = {}) => {
  const data = new Map(Object.entries(entries));
  const writes = [];
  return {
    data, writes,
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => { writes.push([key, value]); data.set(key, String(value)); },
  };
};
const fake = (provider, saved, options = {}) => ({ provider: () => provider, saved: () => saved, options: () => options });
const deepseek = fake("deepseek", "…1234");
const prefs = { sensitiveMode: "beep", words: [{ from: "chết", to: "chớt" }], beepSound: "beep", beepFile: null };
const words = { mode: "beep", words: [{ from: "chết", to: "chớt" }], sound: "beep" };

// 1. The stored choice, and the older settings when none is stored: only an explicit "false" was a choice.
assert.equal(readAiContext(null, null), "ask", "a fresh or sandboxed profile (the rc21 shape) asks");
assert.equal(readAiContext(null, JSON.stringify({ aiTranslation: false, sensitiveMode: "off" })), "off");
assert.equal(readAiContext(null, JSON.stringify({ aiTranslation: true, sensitiveMode: "beep", words: [] })), "ask", "a default is not consent");
assert.equal(readAiContext(null, JSON.stringify({ sensitiveMode: "off" })), "ask");
assert.equal(readAiContext("on", JSON.stringify({ aiTranslation: false })), "on");
assert.equal(readAiContext("off", JSON.stringify({ aiTranslation: true })), "off");
assert.equal(readAiContext("ask", JSON.stringify({ aiTranslation: false })), "ask");
assert.equal(readAiContext("yes", JSON.stringify({ aiTranslation: false })), "off", "a garbage value falls back to the older settings");
assert.equal(readAiContext("yes", null), "ask");
assert.equal(readAiContext(null, "{not json"), "ask");
assert.equal(readAiContext(null, "false"), "ask");
assert.equal(readAiContext(null, JSON.stringify({ aiTranslation: "false" })), "ask");

// 2. Loading: an older "off" is written to the new key once; "ask" writes nothing.
{
  const store = memory({ [LEGACY]: JSON.stringify({ aiTranslation: false }) });
  assert.equal(loadAiContext(store), "off");
  assert.deepEqual(store.writes, [[AI_CONTEXT_KEY, "off"]]);
  assert.equal(loadAiContext(store), "off");
  assert.equal(store.writes.length, 1, "written through once");
  // A later save of the other settings (which no longer carry the field) cannot undo it.
  store.data.set(LEGACY, JSON.stringify({ sensitiveMode: "off" }));
  assert.equal(loadAiContext(store), "off");
}
{
  const store = memory({ [LEGACY]: JSON.stringify({ aiTranslation: true }) });
  assert.equal(loadAiContext(store), "ask");
  assert.equal(loadAiContext(memory()), "ask");
  assert.deepEqual(store.writes, []);
}
assert.equal(loadAiContext(null), "ask", "no storage at all");

// 3. Job arguments: AI translation only with "on" and a ready provider; the rest never changes.
for (const choice of ["ask", "off", "on"]) {
  const args = translationJobArgs(prefs, deepseek, "Video", choice);
  assert.deepEqual(args.sensitiveWords, words, choice);
  assert.equal(args.title, "Video", choice);
  assert.deepEqual(args.aiTranslation, choice === "on" ? { provider: "deepseek", model: "deepseek-flash", baseUrl: null } : null, choice);
}
const customServer = fake("custom", null, { baseUrl: "http://localhost:1234/v1", model: "m" });
assert.equal(translationJobArgs(prefs, customServer, "", "ask").aiTranslation, null);
assert.equal(translationJobArgs(prefs, customServer, "", "off").aiTranslation, null);
assert.deepEqual(translationJobArgs(prefs, customServer, "", "on").aiTranslation, { provider: "custom", model: "m", baseUrl: "http://localhost:1234/v1" });
assert.equal(translationJobArgs(prefs, fake("deepseek", null), "", "on").aiTranslation, null);

// Ready: a saved key, or for a custom server its address and model.
assert.equal(providerReady(deepseek, "deepseek"), true);
assert.equal(providerReady(fake("claude", null), "claude"), false);
assert.equal(providerReady(customServer, "custom"), true);
assert.equal(providerReady(fake("custom", "…1234", { baseUrl: "http://localhost:1234/v1" }), "custom"), false);

// 5. The question: once, the answer kept; nothing ready or already decided asks nothing.
const asker = (answers) => {
  const calls = [];
  const confirm = async (options) => { calls.push(options); return answers.shift(); };
  return { calls, confirm };
};
{
  const store = memory();
  const { calls, confirm } = asker([true]);
  assert.equal(await decideAiContext(deepseek, "start", confirm, store), "on");
  assert.equal(store.getItem(AI_CONTEXT_KEY), "on");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].initialFocus, "cancel");
  assert.equal(await decideAiContext(deepseek, "start", confirm, store), "on");
  assert.equal(calls.length, 1, "asked once");
}
{
  // "Không", and Esc (the dialog resolves false for both; a click outside does not answer, see confirm.test.mjs).
  const store = memory();
  const { calls, confirm } = asker([false]);
  const answer = await decideAiContext(deepseek, "start", confirm, store);
  assert.equal(answer, "off");
  assert.equal(store.getItem(AI_CONTEXT_KEY), "off");
  assert.equal(translationJobArgs(prefs, deepseek, "Video", answer).aiTranslation, null);
  assert.equal(await decideAiContext(deepseek, "saved", confirm, store), "off");
  assert.equal(calls.length, 1);
}
{
  // A batch or an auto-start: callers at the same time share one dialog and its answer.
  const store = memory();
  let release;
  const calls = [];
  const confirm = (options) => { calls.push(options); return new Promise((resolve) => { release = resolve; }); };
  const first = decideAiContext(deepseek, "start", confirm, store);
  const second = decideAiContext(deepseek, "start", confirm, store);
  const third = decideAiContext(deepseek, "saved", confirm, store);
  await Promise.resolve();
  assert.equal(calls.length, 1);
  release(true);
  assert.deepEqual(await Promise.all([first, second, third]), ["on", "on", "on"]);
  assert.equal(calls.length, 1);
  assert.deepEqual(store.writes, [[AI_CONTEXT_KEY, "on"]]);
}
{
  // Nothing ready (no key, custom server unset or keys closed by DUBFLOW_NO_SAVED_KEYS): no question, no AI.
  const store = memory();
  const { calls, confirm } = asker([true, true, true]);
  assert.equal(await decideAiContext(fake("claude", null), "start", confirm, store), "ask");
  assert.equal(await decideAiContext(fake("custom", null, { model: "m" }), "start", confirm, store), "ask");
  assert.equal(await decideAiContext(fake("custom", null, { baseUrl: "http://localhost:1234/v1" }), "saved", confirm, store), "ask");
  assert.equal(calls.length, 0);
  assert.deepEqual(store.writes, []);
}
{
  // DUBFLOW_NO_SAVED_KEYS (ai_key_status says keys are closed): no question and no AI for this run, whatever is stored
  // and even for a custom server, which needs no key; nothing is kept, so the next normal run is as before.
  setKeysClosed(true);
  const { calls, confirm } = asker([true, true, true, true]);
  const asked = memory();
  const on = memory({ [AI_CONTEXT_KEY]: "on" });
  for (const [session, store] of [[customServer, asked], [deepseek, asked], [customServer, on]]) {
    const answer = await decideAiContext(session, "start", confirm, store);
    assert.equal(answer, "off");
    assert.equal(translationJobArgs(prefs, session, "Video", answer).aiTranslation, null);
  }
  assert.equal(calls.length, 0);
  assert.deepEqual([asked.writes, on.writes], [[], []]);
  setKeysClosed(false);
  assert.equal(await decideAiContext(customServer, "start", confirm, on), "on");
  assert.equal(await decideAiContext(customServer, "start", confirm, asked), "on");
  assert.equal(calls.length, 1);
}
{
  // Already decided, in either direction (an older "off" included): no question.
  const { calls, confirm } = asker([true, true, true]);
  assert.equal(await decideAiContext(deepseek, "start", confirm, memory({ [AI_CONTEXT_KEY]: "on" })), "on");
  assert.equal(await decideAiContext(deepseek, "start", confirm, memory({ [AI_CONTEXT_KEY]: "off" })), "off");
  assert.equal(await decideAiContext(deepseek, "start", confirm, memory({ [LEGACY]: JSON.stringify({ aiTranslation: false }) })), "off");
  assert.equal(calls.length, 0);
}
{
  // Storage that throws: the answer still counts for this run, without an exception.
  const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
  const { calls, confirm } = asker([true]);
  assert.equal(await decideAiContext(deepseek, "start", confirm, broken), "on");
  assert.equal(calls.length, 1);
  assert.equal(loadAiContext(broken), "on");
  assert.equal(await decideAiContext(deepseek, "start", confirm, broken), "on");
  assert.equal(calls.length, 1);
}
{
  // A write that fails after an earlier one succeeded: the newer answer wins for this run.
  const store = memory({ [AI_CONTEXT_KEY]: "on" });
  store.setItem = () => { throw new Error("quota"); };
  saveAiContext("off", store);
  assert.equal(loadAiContext(store), "off");
}
{
  // A dialog that fails to open: offline this time, asked again next time.
  const store = memory();
  assert.equal(await decideAiContext(deepseek, "start", async () => { throw new Error("no dialog"); }, store), "off");
  assert.equal(store.getItem(AI_CONTEXT_KEY), null);
  const { calls, confirm } = asker([true]);
  assert.equal(await decideAiContext(deepseek, "start", confirm, store), "on");
  assert.equal(calls.length, 1);
}

// 6. The words of the question.
for (const provider of ["claude", "deepseek"]) {
  const prompt = aiContextPrompt(provider, "start");
  const name = provider === "claude" ? "Claude" : "DeepSeek";
  const company = provider === "claude" ? "Anthropic" : "DeepSeek";
  assert.equal(prompt.title, "Dịch phụ đề bằng AI theo ngữ cảnh?");
  assert.ok(prompt.message.includes(name), provider);
  assert.ok(prompt.message.includes(`gửi tới ${company}`), provider);
  assert.match(prompt.message, new RegExp(`tính phí vào tài khoản ${name} của bạn`));
  assert.match(prompt.message, /offline/);
  assert.match(prompt.message, /Cài đặt › Dịch phụ đề/);
  assert.doesNotMatch(prompt.message, /Google có thể dùng/);
  assert.equal(prompt.confirmLabel, "Bật dịch AI");
  assert.equal(prompt.cancelLabel, "Không, dịch offline");
  assert.equal(prompt.initialFocus, "cancel");
  assert.equal(prompt.backdropCancels, false, "a click beside the question is no answer");
  assert.ok(!prompt.danger);
}
assert.match(aiContextPrompt("gemini", "start").message, /Khóa miễn phí của Gemini không mất tiền nhưng giới hạn lượt gọi, và Google có thể dùng lời thoại/);
{
  const custom = aiContextPrompt("custom", "start").message;
  assert.match(custom, /máy chủ bạn đã nhập; nếu máy chủ đó tính phí/);
  assert.doesNotMatch(custom, /tính phí vào tài khoản .* của bạn\./, "no definite charge for the user's own server");
  assert.doesNotMatch(custom, /Mỗi video tốn nhiều lượt gọi AI/);
}
assert.match(aiContextPrompt("deepseek", "saved").message, /^Bạn vừa lưu khóa DeepSeek\./);
assert.match(aiContextPrompt("deepseek", "start").message, /^Máy này đã lưu khóa DeepSeek, nhưng DubFlow chưa hỏi/);
assert.notEqual(aiContextPrompt("custom", "saved").message.split("\n")[0], aiContextPrompt("custom", "start").message.split("\n")[0]);

// The line after the answer, and the Cài đặt hint for each choice.
assert.equal(aiContextAnswered("deepseek", "on"), "Đã bật dịch phụ đề bằng DeepSeek. Tắt trong Cài đặt › Dịch phụ đề.");
assert.equal(aiContextAnswered("deepseek", "off"), "Phụ đề vẫn dịch offline. Bật dịch AI trong Cài đặt › Dịch phụ đề khi cần.");
assert.equal(aiContextHint("deepseek", "on", "Video mới: …"), "Video mới: … Mỗi video tốn nhiều lượt gọi AI, tính phí vào tài khoản DeepSeek của bạn.");
assert.match(aiContextHint("deepseek", "ask", "x"), /^Chưa bật\. Trước video đầu tiên, DubFlow sẽ hỏi bạn có dùng khóa DeepSeek .*phụ đề dịch offline\.$/);
assert.match(aiContextHint("deepseek", "off", "x"), /^Đang tắt: phụ đề dịch offline \(dịch từng câu\), không dùng khóa DeepSeek\./);
assert.doesNotMatch(aiContextHint("custom", "on", "x"), /tính phí vào tài khoản .* của bạn\./);

// 7. Deleting the last ready key puts "on" back to "ask"; another key, a custom server, or "off" keeps the choice.
const none = { saved: () => null, options: () => ({}) };
{
  const store = memory({ [AI_CONTEXT_KEY]: "on" });
  resetAiContextIfNoKey(none, store);
  assert.equal(loadAiContext(store), "ask");
  // And the next first key asks again.
  const { calls, confirm } = asker([false]);
  assert.equal(await decideAiContext(deepseek, "saved", confirm, store), "off");
  assert.equal(calls.length, 1);
}
{
  const store = memory({ [AI_CONTEXT_KEY]: "on" });
  resetAiContextIfNoKey({ saved: (id) => (id === "gemini" ? "…1" : null), options: () => ({}) }, store);
  resetAiContextIfNoKey({ saved: () => null, options: (id) => (id === "custom" ? { baseUrl: "http://localhost:1/v1", model: "m" } : {}) }, store);
  assert.equal(loadAiContext(store), "on");
  const off = memory({ [AI_CONTEXT_KEY]: "off" });
  resetAiContextIfNoKey(none, off);
  assert.equal(loadAiContext(off), "off");
}

// 8. The call sites that send AI translation (source guard: a later edit that skips the question, or passes "on" or a
// stale answer, fails here even though tsc only checks that some AiContext is passed).
{
  const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8").replace(/\r\n/g, "\n")
    .replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  const between = (text, start, end) => {
    const from = text.indexOf(start);
    assert.ok(from >= 0, `missing: ${start}`);
    const to = text.indexOf(end, from + start.length);
    assert.ok(to > from, `no end after: ${start}`);
    return text.slice(from, to);
  };
  const inOrder = (text, parts, what) => {
    let at = -1;
    for (const part of parts) {
      const next = text.indexOf(part, at + 1);
      assert.ok(next > at, `${what}: "${part}" missing or out of order`);
      at = next;
    }
  };
  // assert.match would print the whole file on a miss.
  const has = (text, pattern, what) => assert.ok(pattern.test(text), `${what}: ${pattern}`);
  const main = read("main.ts");
  // A dubbing job: the saved keys (and whether they are closed) are loaded, the answer awaited, then start_job with it.
  const startJob = between(main, "async function startJob(job: QueueJob): Promise<void> {", "\n}\n");
  inOrder(startJob, [
    "await aiRestored;",
    "const aiContext = await decideAiContext(claudeKey, \"start\");",
    "await invoke<{ output_dir: string } | null>(\"start_job\", {",
    "...translationJobArgs(loadPrefs(), claudeKey, job.displayName, aiContext),",
  ], "startJob");
  assert.equal(startJob.match(/\baiContext\b/g).length, 2, "startJob: the answer is only declared and passed on");
  has(main, /const aiRestored = Promise\.all\(\[\s*claudeKey\.restore\(\)\.catch\(\(\) => undefined\),\s*invoke<Record<string, unknown> \| null>\("ai_key_status"\)\.then\(\(status\) => setKeysClosed\(status\?\.keysClosed === true\)/, "aiRestored");
  // Tách phụ đề: the editor host asks the same way, after the same load.
  has(main, /decideAi: async \(\) => \{\s*await aiRestored;\s*return decideAiContext\(claudeKey, "start"\);\s*\},/, "editor host");
  const view = read("features/edit/view.ts");
  has(view, /extractArgs: \(aiContext: AiContext = "off"\) => session \? \{[^}]*\.\.\.translationJobArgs\(loadPrefs\(\), host\.ai, [^\n]*, aiContext\),\s*\} : null,\s*decideAi: \(\) => host\.decideAi\(\),/, "editor extractArgs");
  const subs = read("features/edit/subtitles_view.ts");
  const extract = between(subs, "extractStart.addEventListener(\"click\", async () => {", "\n  });\n");
  inOrder(extract, [
    "const aiContext = await context.decideAi();",
    "const args = context.extractArgs(aiContext);",
    "await invoke(\"edit_subs_extract\", { ...args, language: extractLang.value });",
  ], "Tách phụ đề");
  assert.equal(extract.match(/\baiContext\b/g).length, 2, "Tách phụ đề: the answer is only declared and passed on");
  // Every other extractArgs call (cancel, status) carries no answer, so no AI.
  assert.deepEqual([...subs.matchAll(/context\.extractArgs\(([^)]*)\)/g)].map((match) => match[1]).sort(), ["", "", "", "aiContext"]);

  // Nothing else builds translation job arguments (not even under another name: only these two files import it) or
  // starts a job or a Tách phụ đề (the browser preview's stand-in for the host only answers them).
  const files = (dir) => readdirSync(new URL(`../src/${dir}`, import.meta.url), { withFileTypes: true })
    .flatMap((entry) => entry.isDirectory() ? files(`${dir}${entry.name}/`) : /\.ts$/.test(entry.name) ? [`${dir}${entry.name}`] : []);
  const uses = { translationJobArgs: [], start_job: [], edit_subs_extract: [] };
  for (const file of files("")) {
    const text = read(file);
    if (file !== "features/ai/translation_prefs.ts") for (const _ of text.matchAll(/\btranslationJobArgs\b/g)) uses.translationJobArgs.push(file);
    for (const _ of text.matchAll(/["'`]start_job["'`]/g)) uses.start_job.push(file);
    for (const _ of text.matchAll(/["'`]edit_subs_extract["'`]/g)) uses.edit_subs_extract.push(file);
  }
  assert.deepEqual(uses, {
    // each: the import and the one call
    translationJobArgs: ["features/edit/view.ts", "features/edit/view.ts", "main.ts", "main.ts"],
    start_job: ["main.ts", "shell/preview_invoke.ts"],
    edit_subs_extract: ["features/edit/subtitles_view.ts", "shell/preview_invoke.ts"],
  });
}

// 9. A job left RUNNING ("starting") while the question waited, then DubFlow closed: on the next launch it goes back to
// QUEUED unless the host did receive it (then it is polled), so it is never stuck. And the media picker, the other
// dialog on .confirm-backdrop, takes Esc only when it is on top, so one Esc never also answers the question.
{
  const strip = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8").replace(/\r\n/g, "\n")
    .replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  const main = strip("main.ts");
  assert.match(main, /if \(job\.status\.state === "RUNNING" && job\.status\.reason === "starting"\) void restoreStartingJob\(job\.id\);\s*else if/);
  const restore = main.slice(main.indexOf("async function restoreStartingJob"), main.indexOf("for (const job of queue.getSnapshot().jobs) {\n  if (job.status.state === \"RUNNING\""));
  assert.match(restore, /if \(await invoke<StatusResponse \| null>\("job_status", \{ jobId \}\)\) return beginPolling\(jobId\);/);
  assert.match(restore, /catch \{\s*return beginPolling\(jobId\);/, "a failed status read keeps the job polled, never re-queued");
  assert.match(restore, /job\.status\.state !== "RUNNING" \|\| job\.status\.reason !== "starting"\) return;/);
  assert.match(restore, /state: "QUEUED", reason: "not_started"/);
  assert.ok(main.indexOf('reason: "starting"') < main.indexOf("await decideAiContext(claudeKey, \"start\")"), "the job is RUNNING while it waits, so no second start");
  const picker = strip("features/media/picker.ts");
  assert.match(picker, /if \(event\.key !== "Escape" \|\| !isTopmostDialog\(backdrop, document\.body\.children\)\) return;\s*event\.preventDefault\(\);\s*event\.stopImmediatePropagation\(\);/);
}

globalThis.fetch = realFetch;
console.log("ai context ok");
