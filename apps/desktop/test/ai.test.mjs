import assert from "node:assert/strict";
import { askJson, createAiSession, GEMINI_MODEL, isKeyError, keyHint, setKeyFor, toGeminiSchema } from "../src/features/ai/provider.ts";

// One key per provider, in memory only; switching keeps each key.
const session = createAiSession();
assert.equal(session.provider(), "claude");
session.set("  claude-key-not-real ");
session.setProvider("gemini");
assert.equal(session.read(), "", "Gemini starts without a key");
session.set("gemini-key-not-real");
session.setProvider("claude");
assert.equal(session.read(), "claude-key-not-real");
await session.clear();
assert.equal(session.read(), "");
session.setProvider("gemini");
assert.equal(session.read(), "gemini-key-not-real", "clearing one provider leaves the other");

// With the host store: keys are saved once and restored at the next start, with the provider choice.
const saved = new Map();
let savedProvider = null;
const store = {
  load: async (provider) => saved.get(provider) ?? null,
  save: async (provider, key) => { saved.set(provider, key); return `…${key.slice(-4)}`; },
  remove: async (provider) => { saved.delete(provider); },
  loadProvider: () => savedProvider,
  saveProvider: (provider) => { savedProvider = provider; },
};
const first = createAiSession(store);
first.setProvider("gemini");
await first.set("  AIzaSyExample1234 ");
await setKeyFor(first, "claude", "sk-ant-example-9876");
assert.equal(first.provider(), "gemini", "saving another provider's key keeps the choice");
assert.deepEqual([...saved.entries()].sort(), [["claude", "sk-ant-example-9876"], ["gemini", "AIzaSyExample1234"]]);
const next = createAiSession(store);
await next.restore();
assert.equal(next.provider(), "gemini");
assert.equal(next.read(), "AIzaSyExample1234");
assert.deepEqual([next.saved("claude"), next.saved("gemini")], ["…9876", "…1234"]);
await next.set("   ");
assert.equal(saved.has("gemini"), false, "a blank key removes the saved one");
const failing = createAiSession({ ...store, save: async () => { throw new Error("Không lưu được khóa"); } });
await assert.rejects(failing.set("sk-ant-new-key-0000"), /Không lưu được/);
assert.equal(failing.read(), "", "a key that was not saved is not kept");

// The prompt names where to get a key, where it is kept, who receives data and who bills.
const gemini = keyHint("gemini", "ảnh sản phẩm");
assert.match(gemini, /aistudio\.google\.com/);
assert.match(gemini, /Windows Credential Manager/);
assert.match(gemini, /ảnh sản phẩm được gửi tới Google/);
assert.match(gemini, /tính phí/);
assert.match(keyHint("claude"), /gửi tới Anthropic/);

// Gemini's schema subset: upper-case types, no additionalProperties, nested too.
assert.deepEqual(toGeminiSchema({
  type: "object", additionalProperties: false, required: ["a"],
  properties: { a: { type: "array", items: { type: "string" } }, type: { type: "string" } },
}), { type: "OBJECT", required: ["a"], properties: { a: { type: "ARRAY", items: { type: "STRING" } }, type: { type: "STRING" } } });

// A Gemini request: key in a header (never the URL), system prompt, image and text parts, JSON schema.
const calls = [];
const reply = (status, body) => async (url, init) => {
  calls.push({ url, init });
  return { ok: status < 300, status, json: async () => body };
};
const request = {
  system: "Hệ thống", effort: "low", schema: { type: "object", properties: { x: { type: "string" } }, required: ["x"], additionalProperties: false },
  parts: [{ image: { mediaType: "image/jpeg", data: "QUJD" } }, { text: "Đọc ảnh" }],
};
const value = await askJson("gemini", "gemini-key-not-real", request,
  reply(200, { candidates: [{ finishReason: "STOP", content: { parts: [{ text: "ignored", thought: true }, { text: "{\"x\":" }, { text: "\"ok\"}" }] } }] }));
assert.deepEqual(value, { x: "ok" });
const [{ url, init }] = calls;
assert.equal(url, `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent`);
assert.doesNotMatch(url, /key/);
assert.equal(init.headers["x-goog-api-key"], "gemini-key-not-real");
const body = JSON.parse(init.body);
assert.deepEqual(body.systemInstruction, { parts: [{ text: "Hệ thống" }] });
assert.deepEqual(body.contents[0].parts, [{ inlineData: { mimeType: "image/jpeg", data: "QUJD" } }, { text: "Đọc ảnh" }]);
assert.equal(body.generationConfig.responseMimeType, "application/json");
assert.equal(body.generationConfig.responseSchema.type, "OBJECT");

// Errors: a bad key is recognised; refusals and cut-off answers are explained.
await assert.rejects(askJson("gemini", "bad", request, reply(400, { error: { status: "INVALID_ARGUMENT", message: "API key not valid. Please pass a valid API key." } })),
  (error) => isKeyError(error));
await assert.rejects(askJson("gemini", "k", request, reply(403, { error: { status: "PERMISSION_DENIED", message: "denied" } })), (error) => isKeyError(error));
await assert.rejects(askJson("gemini", "k", request, reply(200, { promptFeedback: { blockReason: "SAFETY" } })), /từ chối/);
await assert.rejects(askJson("gemini", "k", request, reply(200, { candidates: [{ finishReason: "MAX_TOKENS", content: { parts: [{ text: "{" }] } }] })), /cắt ngắn/);
await assert.rejects(askJson("gemini", "k", request, reply(200, { candidates: [{ finishReason: "STOP", content: { parts: [] } }] })), /không trả về/);
assert.equal(isKeyError(new Error("500 INTERNAL: overloaded")), false);

console.log("desktop AI provider tests passed");
