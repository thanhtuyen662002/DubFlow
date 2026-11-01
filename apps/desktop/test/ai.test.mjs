import assert from "node:assert/strict";
import {
  askJson, createAiSession, GEMINI_FALLBACK_MODEL, GEMINI_MAX_RETRY_WAIT_MS, GEMINI_MODEL, GEMINI_TIMEOUT_MS, isKeyError, keyHint,
  lacksDiacritics, parseRetryAfter, parseRetryDelay, setKeyFor, toGeminiSchema,
} from "../src/features/ai/provider.ts";
import { fillPanel } from "../src/features/reframe/ai.ts";
import { suggestSegments } from "../src/features/shorts/ai.ts";

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
assert.match(body.systemInstruction.parts[0].text, /^Hệ thống\nLuôn viết tiếng Việt có dấu/);
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
// A busy model is retried once, then the larger model answers; waits are injected, not slept.
const waits = [];
const busyThenOk = (() => {
  const urls = [];
  const send = async (url) => {
    urls.push(url);
    if (urls.length < 3) return { ok: false, status: 503, json: async () => ({ error: { status: "UNAVAILABLE", message: "high demand" } }) };
    return { ok: true, status: 200, json: async () => ({ candidates: [{ finishReason: "STOP", content: { parts: [{ text: "{\"x\":\"ok\"}" }] } }] }) };
  };
  return { urls, send };
})();
assert.deepEqual(await askJson("gemini", "k", request, busyThenOk.send, async (ms) => { waits.push(ms); }), { x: "ok" });
assert.deepEqual(busyThenOk.urls.map((url) => url.match(/models\/([^:]+)/)[1]), [GEMINI_MODEL, GEMINI_MODEL, GEMINI_FALLBACK_MODEL]);
assert.deepEqual(waits, [4000, 2000]);
// Still busy after every attempt: a plain Vietnamese message, after exactly three requests.
let busyCalls = 0;
await assert.rejects(askJson("gemini", "k", request, async () => { busyCalls += 1; return { ok: false, status: 503, json: async () => ({ error: { status: "UNAVAILABLE", message: "x" } }) }; }, async () => {}), /quá tải/);
assert.equal(busyCalls, 3);
await assert.rejects(askJson("gemini", "k", request, async () => ({ ok: false, status: 429, json: async () => ({ error: { status: "RESOURCE_EXHAUSTED", message: "quota" } }) }), async () => {}), /giới hạn số lần gọi Gemini/);
// A time-out counts as busy; a bad key is never retried.
let timeoutCalls = 0;
await assert.rejects(askJson("gemini", "k", request, async () => { timeoutCalls += 1; throw Object.assign(new Error("aborted"), { name: "TimeoutError" }); }, async () => {}), /quá lâu/);
assert.equal(timeoutCalls, 3);
let keyCalls = 0;
await assert.rejects(askJson("gemini", "bad", request, async () => { keyCalls += 1; return { ok: false, status: 400, json: async () => ({ error: { status: "INVALID_ARGUMENT", message: "API key not valid." } }) }; }, async () => {}), (error) => isKeyError(error));
assert.equal(keyCalls, 1);

// Unaccented Vietnamese is detected; accented text, short text and English-free JSON shapes are not flagged.
assert.equal(lacksDiacritics({ scripts: ["Ban co bao gio tu hoi anh ban nay dang lam gi khong. Mot hinh anh rat quen thuoc."] }), true);
assert.equal(lacksDiacritics({ scripts: ["Bạn có bao giờ tự hỏi anh bạn này đang làm gì không. Một hình ảnh rất quen thuộc."] }), false);
assert.equal(lacksDiacritics({ title: "OK" }), false);
const okReply = (text) => ({ ok: true, status: 200, json: async () => ({ candidates: [{ finishReason: "STOP", content: { parts: [{ text }] } }] }) });
const unaccented = JSON.stringify({ x: "Ban co bao gio tu hoi anh ban nay dang lam gi khong, mot hinh anh quen thuoc" });
const accented = JSON.stringify({ x: "Bạn có bao giờ tự hỏi anh bạn này đang làm gì không, một hình ảnh quen thuộc" });
// The fast model answers unaccented: it is asked once more, and the accented answer is used.
const asked = [];
const fixedByFallback = await askJson("gemini", "k", request, async (url) => {
  asked.push(url.match(/models\/([^:]+)/)[1]);
  return okReply(asked.length === 1 ? unaccented : accented);
}, async () => {});
assert.deepEqual(asked, [GEMINI_MODEL, GEMINI_MODEL]);
assert.match(fixedByFallback.x, /Bạn có/);
// The second try fails: the unaccented answer is still returned rather than an error.
let tries = 0;
const kept = await askJson("gemini", "k", request, async () => {
  tries += 1;
  return tries === 1 ? okReply(unaccented) : { ok: false, status: 503, json: async () => ({ error: { status: "UNAVAILABLE", message: "busy" } }) };
}, async () => {});
assert.equal(tries, 2);
assert.match(kept.x, /^Ban co/);

// ---- Resilience (#386): model order, the retryable set, waits Google asks for, and what is never retried. ----
assert.equal(GEMINI_MODEL, "gemini-3.5-flash-lite", "the fast model is tried first");
assert.equal(GEMINI_FALLBACK_MODEL, "gemini-3.8-flash", "the larger model is the only fallback");
assert.equal(GEMINI_TIMEOUT_MS, 90_000, "each attempt has a 90-second timeout");
assert.ok(GEMINI_MAX_RETRY_WAIT_MS > 4000 && GEMINI_MAX_RETRY_WAIT_MS <= 60_000, "the honoured wait is bounded");
assert.ok(calls[0].init.signal instanceof AbortSignal, "every request carries a timeout signal");

const answer = (value) => ({ ok: true, status: 200, json: async () => ({ candidates: [{ finishReason: "STOP", content: { parts: [{ text: JSON.stringify(value) }] } }] }) });
const failure = (status, error = {}, headers = {}) => ({ ok: false, status, headers: new Headers(headers), json: async () => ({ error }) });
/** Plays `replies` in order (a function reply is called, so it can throw) and records models and waits. */
async function play(replies, key = "test-key-not-real") {
  const models = [];
  const waits = [];
  let error = null;
  let value;
  try {
    value = await askJson("gemini", key, request, async (url) => {
      models.push(url.match(/models\/([^:]+)/)[1]);
      const next = replies[Math.min(models.length - 1, replies.length - 1)];
      return typeof next === "function" ? next() : next;
    }, async (ms) => { waits.push(ms); });
  } catch (caught) {
    error = caught;
  }
  return { models, waits, error, value };
}
const ALL_MODELS = [GEMINI_MODEL, GEMINI_MODEL, GEMINI_FALLBACK_MODEL];

// Every server error 500–599 is retried once on the same model, then on the larger one, with a Vietnamese final message.
for (const status of [500, 501, 502, 503, 504, 599]) {
  const run = await play([failure(status, { status: "UNAVAILABLE", message: "busy" })]);
  assert.deepEqual(run.models, ALL_MODELS, `${status}: three bounded attempts in order`);
  assert.deepEqual(run.waits, [4000, 2000]);
  assert.match(run.error.message, new RegExp(`^${status}: Gemini đang quá tải`));
  assert.equal(isKeyError(run.error), false);
  const recovered = await play([failure(status), answer({ x: "được" })]);
  assert.deepEqual(recovered.models, [GEMINI_MODEL, GEMINI_MODEL], `${status}: the same-model retry can succeed`);
  assert.deepEqual(recovered.value, { x: "được" });
}

// 429 with no hint: retried with DubFlow's own pauses, then a Vietnamese rate-limit message.
{
  const run = await play([failure(429, { status: "RESOURCE_EXHAUSTED", message: "quota" })]);
  assert.deepEqual(run.models, ALL_MODELS);
  assert.deepEqual(run.waits, [4000, 2000]);
  assert.match(run.error.message, /^429: Đã vượt giới hạn số lần gọi Gemini/);
  assert.equal(isKeyError(run.error), false);
}
// 429 with a Retry-After header in seconds: that wait is honoured before the retry.
{
  const run = await play([failure(429, {}, { "Retry-After": "12" }), answer({ x: "ok" })]);
  assert.deepEqual(run.models, [GEMINI_MODEL, GEMINI_MODEL]);
  assert.deepEqual(run.waits, [12_000]);
}
// 429 with a Retry-After HTTP date about 20 s ahead.
{
  const run = await play([failure(429, {}, { "Retry-After": new Date(Date.now() + 20_500).toUTCString() }), answer({ x: "ok" })]);
  assert.equal(run.waits.length, 1);
  assert.ok(run.waits[0] >= 18_000 && run.waits[0] <= 20_500, `HTTP-date wait ${run.waits[0]}`);
}
// 429 with Google's RetryInfo metadata in the error details.
const retryInfo = (retryDelay) => ({ status: "RESOURCE_EXHAUSTED", message: "quota", details: [
  { "@type": "type.googleapis.com/google.rpc.QuotaFailure", violations: [] },
  { "@type": "type.googleapis.com/google.rpc.RetryInfo", retryDelay },
] });
{
  const run = await play([failure(429, retryInfo("7.5s")), answer({ x: "ok" })]);
  assert.deepEqual(run.waits, [7500]);
  const both = await play([failure(429, retryInfo("9s"), { "retry-after": "5" }), answer({ x: "ok" })]);
  assert.deepEqual(both.waits, [9000], "the longer of the two hints wins");
  const short = await play([failure(429, retryInfo("1s")), answer({ x: "ok" })]);
  assert.deepEqual(short.waits, [4000], "a shorter hint never shortens DubFlow's own pause");
  const beforeFallback = await play([failure(503), failure(429, retryInfo("10s")), answer({ x: "ok" })]);
  assert.deepEqual(beforeFallback.models, ALL_MODELS);
  assert.deepEqual(beforeFallback.waits, [4000, 10_000], "the latest failure's hint applies to the next attempt");
  const serverHint = await play([failure(503, {}, { "Retry-After": "6" }), answer({ x: "ok" })]);
  assert.deepEqual(serverHint.waits, [6000], "Retry-After on a 5xx is honoured too");
}
// Invalid hints are ignored rather than trusted.
for (const headers of [{ "Retry-After": "soon" }, { "Retry-After": "-5" }, { "Retry-After": "1.5" }, { "Retry-After": "" }]) {
  const run = await play([failure(429, retryInfo("later"), headers), answer({ x: "ok" })]);
  assert.deepEqual(run.waits, [4000], JSON.stringify(headers));
}
// A requested wait above the safe cap stops at once with a Vietnamese "try again later" message; nothing is retried early.
{
  const header = await play([failure(429, {}, { "Retry-After": "120" }), answer({ x: "too early" })]);
  assert.deepEqual(header.models, [GEMINI_MODEL]);
  assert.deepEqual(header.waits, []);
  assert.equal(header.error.message, "429: Gemini yêu cầu chờ khoảng 2 phút trước khi gọi lại; hãy thử lại sau.");
  assert.equal(isKeyError(header.error), false);
  const metadata = await play([failure(429, retryInfo("3600s")), answer({ x: "too early" })]);
  assert.deepEqual(metadata.models, [GEMINI_MODEL]);
  assert.match(metadata.error.message, /khoảng 60 phút.*thử lại sau/);
  const date = await play([failure(429, {}, { "Retry-After": new Date(Date.now() + 3 * 3600_000).toUTCString() })]);
  assert.deepEqual(date.models, [GEMINI_MODEL]);
  assert.match(date.error.message, /hơn một giờ/);
  const later = await play([failure(503), failure(503, {}, { "Retry-After": String(GEMINI_MAX_RETRY_WAIT_MS / 1000 + 1) })]);
  assert.deepEqual(later.models, [GEMINI_MODEL, GEMINI_MODEL], "the fallback model is not tried before the requested time either");
  assert.match(later.error.message, /^503: Gemini yêu cầu chờ khoảng 1 phút/);
  const atCap = await play([failure(429, {}, { "Retry-After": String(GEMINI_MAX_RETRY_WAIT_MS / 1000) }), answer({ x: "ok" })]);
  assert.deepEqual(atCap.waits, [GEMINI_MAX_RETRY_WAIT_MS], "a wait exactly at the cap is honoured");
}
// Time-outs (request or answer body) are retried; the final message is Vietnamese.
{
  const timeout = () => { throw Object.assign(new Error("signal timed out"), { name: "TimeoutError" }); };
  const run = await play([timeout]);
  assert.deepEqual(run.models, ALL_MODELS);
  assert.equal(run.error.message, "Gemini trả lời quá lâu; hãy thử lại sau ít phút.");
  const aborted = await play([() => { throw Object.assign(new Error("aborted"), { name: "AbortError" }); }, answer({ x: "ok" })]);
  assert.deepEqual(aborted.models, [GEMINI_MODEL, GEMINI_MODEL]);
  const slowBody = { ok: true, status: 200, json: async () => { throw Object.assign(new Error("body timed out"), { name: "TimeoutError" }); } };
  const body = await play([slowBody, answer({ x: "ok" })]);
  assert.deepEqual(body.models, [GEMINI_MODEL, GEMINI_MODEL], "a stalled answer body times out and is retried");
  assert.deepEqual(body.value, { x: "ok" });
}
// Never retried: missing key, invalid key/auth, other 4xx, refusals, malformed or unusable answers, network errors.
for (const key of ["", "   "]) {
  const run = await play([answer({ x: "ok" })], key);
  assert.deepEqual(run.models, [], "a missing key never reaches Google");
  assert.ok(isKeyError(run.error));
  assert.match(run.error.message, /Chưa có khóa API Gemini/);
}
for (const [status, error, isKey] of [
  [400, { status: "INVALID_ARGUMENT", message: "API key not valid. Please pass a valid API key." }, true],
  [401, { status: "UNAUTHENTICATED", message: "Request had invalid authentication credentials." }, true],
  [403, { status: "PERMISSION_DENIED", message: "Permission denied." }, true],
  [400, { status: "INVALID_ARGUMENT", message: "Invalid JSON payload: responseSchema" }, false],
  [404, { status: "NOT_FOUND", message: "model not found" }, false],
  [408, { status: "DEADLINE_EXCEEDED", message: "request timeout" }, false],
  [409, { status: "ABORTED", message: "conflict" }, false],
  [413, { status: "INVALID_ARGUMENT", message: "too large" }, false],
  [422, { status: "INVALID_ARGUMENT", message: "unprocessable" }, false],
]) {
  const run = await play([failure(status, { ...error, details: [{ "@type": "type.googleapis.com/google.rpc.RetryInfo", retryDelay: "1s" }] }, { "Retry-After": "1" }), answer({ x: "ok" })]);
  assert.deepEqual(run.models, [GEMINI_MODEL], `${status} ${error.status} is not retried`);
  assert.deepEqual(run.waits, []);
  assert.match(run.error.message, new RegExp(`^${status} `));
  assert.equal(isKeyError(run.error), isKey, `${status} ${error.message}`);
}
for (const [reply, pattern] of [
  [{ ok: true, status: 200, json: async () => ({ promptFeedback: { blockReason: "SAFETY" } }) }, /từ chối/],
  [{ ok: true, status: 200, json: async () => ({ candidates: [{ finishReason: "SAFETY", content: { parts: [] } }] }) }, /từ chối/],
  [{ ok: true, status: 200, json: async () => ({ candidates: [{ finishReason: "PROHIBITED_CONTENT" }] }) }, /từ chối/],
  [{ ok: true, status: 200, json: async () => ({ candidates: [{ finishReason: "STOP", content: { parts: [{ text: "{\"x\": \"thiếu" }] } }] }) }, /sai định dạng JSON/],
  [{ ok: true, status: 200, json: async () => ({ candidates: [{ finishReason: "STOP", content: { parts: [{ text: "503 not json" }] } }] }) }, /sai định dạng JSON/],
  [{ ok: true, status: 200, json: async () => { throw new SyntaxError("Unexpected token <"); } }, /không trả về nội dung/],
  [{ ok: true, status: 200, json: async () => ({ candidates: [{ finishReason: "MAX_TOKENS", content: { parts: [{ text: "{" }] } }] }) }, /cắt ngắn/],
  [() => { throw new TypeError("Failed to fetch"); }, /Failed to fetch/],
]) {
  const run = await play([reply, answer({ x: "ok" })]);
  assert.deepEqual(run.models, [GEMINI_MODEL], String(pattern));
  assert.deepEqual(run.waits, []);
  assert.match(run.error.message, pattern);
}

// Hint parsing: whole seconds or an HTTP date; Google's Duration string "<n>s".
const now = Date.parse("Sun, 01 Nov 2026 10:00:00 GMT");
assert.equal(parseRetryAfter("0", now), 0);
assert.equal(parseRetryAfter(" 12 ", now), 12_000);
assert.equal(parseRetryAfter("Sun, 01 Nov 2026 10:00:30 GMT", now), 30_000);
assert.equal(parseRetryAfter("Sunday, 01-Nov-26 10:00:30 GMT", now), 30_000);
assert.equal(parseRetryAfter("Sun, 01 Nov 2026 09:59:00 GMT", now), 0, "a past date means no wait");
for (const value of [null, undefined, "", "soon", "-5", "1.5", "1e3", "12s", "Sun, 99 Nov 2026 10:00:30 GMT", "2026-11-01"]) {
  assert.equal(parseRetryAfter(value, now), null, String(value));
}
assert.equal(parseRetryDelay([{ "@type": "type.googleapis.com/google.rpc.RetryInfo", retryDelay: "37s" }]), 37_000);
assert.equal(parseRetryDelay([{ "@type": "type.googleapis.com/google.rpc.RetryInfo", retryDelay: "0.25s" }]), 250);
for (const details of [undefined, null, {}, [], [{ "@type": "type.googleapis.com/google.rpc.Help", retryDelay: "5s" }],
  [{ "@type": "type.googleapis.com/google.rpc.RetryInfo", retryDelay: "-5s" }], [{ "@type": "type.googleapis.com/google.rpc.RetryInfo", retryDelay: { seconds: 5 } }],
  [{ "@type": "type.googleapis.com/google.rpc.RetryInfo", retryDelay: "5m" }]]) {
  assert.equal(parseRetryDelay(details), null, JSON.stringify(details));
}

// Through the real Gemini path, the reframe panel keeps Vietnamese diacritics and its normalizer limits,
// and sends only bounded Vietnamese subtitle lines.
{
  const panelCues = Array.from({ length: 75 }, (_, i) => ({ start_ms: i * 1000, end_ms: i * 1000 + 900, text: `Câu số ${i}: Sản phẩm nhỏ gọn, dễ vệ sinh ${"ữ".repeat(400)}` }));
  const bodies = [];
  const replies = [failure(503), answer({
    title: `Đánh giá máy xay sinh tố ${"ố".repeat(120)}`, score: 8,
    pros: ["Nhỏ gọn, dễ vệ sinh", `Xay nhuyễn ${"đá".repeat(60)}`, "Giá hợp lý", "Ý thứ tư bị bỏ"],
    cons: ["Hơi ồn khi xay đá", "", "Dây điện ngắn", "Nắp khó mở", "Thêm ý"],
    summary: `Tóm tắt: máy dùng tốt cho gia đình nhỏ ${"ư".repeat(200)}`,
  })];
  const panel = await fillPanel("gemini", "test-key-not-real", panelCues, (provider, key, input) => askJson(provider, key, input, async (url, init) => {
    bodies.push(JSON.parse(init.body));
    return replies[bodies.length - 1];
  }, async () => {}));
  assert.equal(bodies.length, 2, "one same-model retry after a 503");
  const subtitles = JSON.parse(bodies[0].contents[0].parts[0].text).subtitles;
  assert.equal(subtitles.length, 60);
  assert.ok(subtitles.every((line) => [...line].length <= 300));
  assert.match(subtitles[0], /^Câu số 0: Sản phẩm nhỏ gọn, dễ vệ sinh/, "subtitle diacritics reach Gemini unchanged");
  assert.match(bodies[0].systemInstruction.parts[0].text, /tiếng Việt có dấu đầy đủ/);
  assert.equal([...panel.title].length, 80);
  assert.match(panel.title, /^Đánh giá máy xay sinh tố ố/);
  assert.equal(panel.score, "8");
  assert.deepEqual(panel.pros.split("\n").map((line) => [...line].length <= 60), [true, true, true]);
  assert.equal(panel.pros.split("\n")[0], "Nhỏ gọn, dễ vệ sinh");
  assert.deepEqual(panel.cons.split("\n"), ["Hơi ồn khi xay đá", "Dây điện ngắn", "Nắp khó mở"]);
  assert.equal([...panel.summary].length, 120);
  assert.match(panel.summary, /^Tóm tắt: máy dùng tốt cho gia đình nhỏ/);
  const scoreOutOfRange = await fillPanel("gemini", "k", panelCues, (p, k, input) => askJson(p, k, input, async () => answer({ title: "Tiêu đề", score: 12, pros: [], cons: [], summary: "" }), async () => {}));
  assert.equal(scoreOutOfRange.score, "");
}
// Shorts suggestions through Gemini: at most three valid, non-overlapping 5–60 s segments with Vietnamese titles intact.
{
  const shortCues = Array.from({ length: 30 }, (_, i) => ({ start_ms: i * 5000 + 100, end_ms: (i + 1) * 5000 - 100, text: `Đoạn ${i + 1}: chuyện thú vị ${"ễ".repeat(700)}` }));
  const texts = [];
  const rows = await suggestSegments("gemini", "test-key-not-real", shortCues, (provider, key, input) => askJson(provider, key, input, async (url, init) => {
    texts.push(JSON.parse(init.body).contents[0].parts[0].text);
    return answer({ items: [
      { start_ms: 100, end_ms: 40_000, title: "Mở đầu bất ngờ khiến ai cũng tò mò" },
      { start_ms: 20_000, end_ms: 50_000, title: "Chồng lấn đoạn đầu" },
      { start_ms: 50_100, end_ms: 51_000, title: "Quá ngắn" },
      { start_ms: 50_100, end_ms: 130_000, title: "Quá dài" },
      { start_ms: 50_100, end_ms: 95_000, title: `Tiêu đề quá dài ${"ạ".repeat(80)}` },
      { start_ms: 50_100, end_ms: 95_000, title: "Bí quyết giữ đồ ăn tươi lâu" },
      { start_ms: 100_100, end_ms: 140_000, title: "Kết thúc đầy cảm xúc" },
      { start_ms: 140_100, end_ms: 149_900, title: "Đoạn thứ tư bị bỏ" },
    ] });
  }, async () => {}));
  assert.equal(texts.length, 1);
  const lines = texts[0].split("\n").slice(1).map((line) => JSON.parse(line));
  assert.ok(lines.every((line) => [...line.text].length <= 600), "each cue sent is bounded");
  assert.match(lines[0].text, /^Đoạn 1: chuyện thú vị/);
  assert.deepEqual(rows.map(({ start_ms, end_ms, title }) => [start_ms, end_ms, title]), [
    [100, 39_900, "Mở đầu bất ngờ khiến ai cũng tò mò"],
    [50_100, 94_900, "Bí quyết giữ đồ ăn tươi lâu"],
    [100_100, 139_900, "Kết thúc đầy cảm xúc"],
  ]);
  assert.ok(rows.every((row) => row.end_ms - row.start_ms >= 5000 && row.end_ms - row.start_ms <= 60_000 && [...row.title].length <= 80));
}

console.log("desktop AI provider tests passed");
