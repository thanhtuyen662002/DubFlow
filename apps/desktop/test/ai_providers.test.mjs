import assert from "node:assert/strict";
import {
  askJson, checkProvider, configureHostAi, createAiSession, hostRequest, isKeyError, isProvider, PROVIDER_IDS, PROVIDERS,
} from "../src/features/ai/provider.ts";
import { DEFAULT_SENSITIVE_WORDS, formatWordList, normalizePrefs, parseWordList, translationJobArgs } from "../src/features/ai/translation_prefs.ts";

// Every provider id is known, labelled and has a console to get a key from.
assert.equal(PROVIDER_IDS.length, 12);
for (const id of PROVIDER_IDS) {
  assert.ok(isProvider(id), id);
  assert.ok(PROVIDERS[id].label && PROVIDERS[id].console, id);
  assert.equal(PROVIDERS[id].host, id !== "claude" && id !== "gemini", id);
}
assert.equal(isProvider("evil"), false);

// The host request carries the chosen model and, for custom and Qwen, the server address.
assert.deepEqual(hostRequest("deepseek", {}, { action: "models" }), { provider: "deepseek", model: "deepseek-flash", action: "models" });
assert.deepEqual(hostRequest("openai", { model: "gpt-x" }, {}), { provider: "openai", model: "gpt-x" });
assert.deepEqual(hostRequest("qwen", {}, {}), { provider: "qwen", model: "qwen-plus", base_url: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1" });
assert.deepEqual(hostRequest("custom", { model: "llama3", baseUrl: "http://localhost:11434/v1" }, {}),
  { provider: "custom", model: "llama3", base_url: "http://localhost:11434/v1" });
assert.deepEqual(hostRequest("custom", {}, {}), { provider: "custom" });

// Host providers go through ai_ask with the key; failures keep the host's code.
const calls = [];
const session = createAiSession();
session.setOptions("deepseek", { model: "  deepseek-v9 ", baseUrl: " ignored/ " });
assert.deepEqual(session.options("deepseek"), { model: "deepseek-v9", baseUrl: "ignored" });
let reply = { ok: true, value: { x: "ok" }, model: "deepseek-v9" };
configureHostAi(async (command, args) => { calls.push({ command, args }); return reply; }, (provider) => session.options(provider));
const request = { system: "Hệ thống", effort: "low", schema: { type: "object" }, parts: [{ text: "xin chào" }] };
assert.deepEqual(await askJson("deepseek", " sk-deepseek-1234 ", request), { x: "ok" });
assert.equal(calls[0].command, "ai_ask");
assert.equal(calls[0].args.key, "sk-deepseek-1234");
assert.equal(calls[0].args.request.model, "deepseek-v9");
assert.equal(calls[0].args.request.action, "ask");
assert.match(calls[0].args.request.system, /dấu đầy đủ/);
reply = { ok: false, code: "KEY", message: "Khóa DeepSeek không hợp lệ (401)" };
await assert.rejects(askJson("deepseek", "sk-deepseek-1234", request), (error) => isKeyError(error) && /KEY:/.test(error.message));
await assert.rejects(askJson("openai", "", request), /Chưa có khóa API ChatGPT/);
await assert.rejects(askJson("groq", "gsk-1234567", { ...request, parts: [{ image: { mediaType: "image/jpeg", data: "QQ==" } }] }), /không đọc được ảnh/);
reply = { ok: true, models: ["a", "b"], suggested: "b" };
assert.deepEqual(await checkProvider("kimi", "sk-kimi-1234", {}), { models: ["a", "b"], suggested: "b" });
assert.equal(calls.at(-1).args.request.action, "models");

// Sensitive words: "từ = cách viết" per line, lower-cased, deduplicated, bounded.
assert.deepEqual(parseWordList("Chết = chớt\n\n giết→giớt \nchết = khác\ndao\n" + "x".repeat(41)), [
  { from: "chết", to: "chớt" }, { from: "giết", to: "giớt" }, { from: "dao", to: "dao" }]);
assert.equal(formatWordList([{ from: "chết", to: "chớt" }, { from: "dao", to: "dao" }]), "chết = chớt\ndao");
assert.deepEqual(parseWordList(formatWordList(DEFAULT_SENSITIVE_WORDS)), [...DEFAULT_SENSITIVE_WORDS]);
assert.deepEqual(normalizePrefs({ sensitiveMode: "loud", aiTranslation: false, words: "x" }),
  { aiTranslation: false, sensitiveMode: "off", words: [...DEFAULT_SENSITIVE_WORDS] });

// Job arguments: AI translation only with a saved key (or a custom server); words only when on.
const prefs = { aiTranslation: true, sensitiveMode: "beep", words: [{ from: "chết", to: "chớt" }] };
const fake = (provider, saved, options = {}) => ({ provider: () => provider, saved: () => saved, options: () => options });
assert.deepEqual(translationJobArgs(prefs, fake("deepseek", "…1234"), "Video"), {
  aiTranslation: { provider: "deepseek", model: "deepseek-flash", baseUrl: null },
  sensitiveWords: { mode: "beep", words: [{ from: "chết", to: "chớt" }] }, title: "Video" });
assert.equal(translationJobArgs(prefs, fake("deepseek", null), "").aiTranslation, null);
assert.equal(translationJobArgs({ ...prefs, aiTranslation: false }, fake("gemini", "…1"), "").aiTranslation, null);
assert.deepEqual(translationJobArgs(prefs, fake("custom", null, { baseUrl: "http://localhost:1234/v1", model: "m" }), "").aiTranslation,
  { provider: "custom", model: "m", baseUrl: "http://localhost:1234/v1" });
assert.equal(translationJobArgs({ ...prefs, sensitiveMode: "off" }, fake("gemini", "…1"), "").sensitiveWords, null);

console.log("ai providers ok");
