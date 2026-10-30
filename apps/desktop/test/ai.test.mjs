import assert from "node:assert/strict";
import { askJson, createAiSession, GEMINI_MODEL, isKeyError, keyHint, toGeminiSchema } from "../src/features/ai/provider.ts";

// One key per provider, in memory only; switching keeps each key.
const session = createAiSession();
assert.equal(session.provider(), "claude");
session.set("  claude-key-not-real ");
session.setProvider("gemini");
assert.equal(session.read(), "", "Gemini starts without a key");
session.set("gemini-key-not-real");
session.setProvider("claude");
assert.equal(session.read(), "claude-key-not-real");
session.clear();
assert.equal(session.read(), "");
session.setProvider("gemini");
assert.equal(session.read(), "gemini-key-not-real", "clearing one provider leaves the other");

// The prompt names where to get a key, the session-only rule, who receives data and who bills.
const gemini = keyHint("gemini", "ảnh sản phẩm");
assert.match(gemini, /aistudio\.google\.com/);
assert.match(gemini, /phiên làm việc này/);
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
