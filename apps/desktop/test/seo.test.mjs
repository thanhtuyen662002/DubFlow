import assert from "node:assert/strict";
import { normalizeSuggestion } from "../src/features/seo/ai.ts";
import { createSessionKey, KEY_CLEAR_LABEL, KEY_CLEARED, KEY_HINT, parseOffline } from "../src/features/seo/view.ts";

// Model output is cleaned: trimmed, de-duplicated, hashtags accent-free and lowercase.
const cleaned = normalizeSuggestion({
  titles: ["  Tiêu đề A ", "Tiêu đề A", "", 7, "Tiêu đề B"],
  description: "  Mô tả. ",
  hashtags: ["#Phim Tiên Hiệp", "đồ ăn", "##vietsub", "#vietsub", "#", "!!!"],
});
assert.deepEqual(cleaned, { titles: ["Tiêu đề A", "Tiêu đề B"], description: "Mô tả.", hashtags: ["#phimtienhiep", "#doan", "#vietsub"] });
assert.throws(() => normalizeSuggestion({ titles: [], description: "x", hashtags: [] }), /tiêu đề/);
assert.throws(() => normalizeSuggestion({ titles: ["a"], description: "", hashtags: [] }), /mô tả/);
assert.equal(normalizeSuggestion({ titles: Array(20).fill(0).map((_, i) => `T${i}`), description: "d", hashtags: [] }).titles.length, 8);

// Offline engine envelope.
assert.equal(parseOffline({ ok: false, code: "BAD_INPUT" }), null);
assert.equal(parseOffline({ ok: true, titles: [], description: "", hashtags: [] }), null);
assert.deepEqual(parseOffline({ ok: true, titles: ["T"], description: "D", hashtags: ["#a"], keywords: ["k"] }), { titles: ["T"], description: "D", hashtags: ["#a"] });
// The Claude API key lives only in memory: no persistent storage is ever touched.
const touched = [];
const spyStorage = (name) => new Proxy({}, { get: (_, prop) => { touched.push(`${name}.${String(prop)}`); return () => null; } });
globalThis.localStorage = spyStorage("localStorage");
globalThis.sessionStorage = spyStorage("sessionStorage");
globalThis.indexedDB = spyStorage("indexedDB");
Object.defineProperty(globalThis, "document", { configurable: true, value: { get cookie() { touched.push("document.cookie"); return ""; }, set cookie(_) { touched.push("document.cookie="); } } });
const key = createSessionKey();
assert.equal(key.read(), "");
key.set("  test-key-not-real  ");
assert.equal(key.read(), "test-key-not-real");
key.set("   ");
assert.equal(key.read(), "", "a blank entry leaves no key");
key.set("test-key-not-real");
key.clear();
assert.equal(key.read(), "");
assert.notEqual(createSessionKey().read(), "test-key-not-real", "each app session starts without a key");
assert.deepEqual(touched, [], "the key store never touches persistent storage");
delete globalThis.document;

// Copy says the key is session-only and keeps the data-sharing and billing warning.
assert.match(KEY_HINT, /phiên làm việc này/);
assert.match(KEY_HINT, /mất khi đóng hoặc tải lại app/);
assert.match(KEY_HINT, /gửi tới Anthropic/);
assert.match(KEY_HINT, /tính phí/);
assert.doesNotMatch(KEY_HINT, /lưu trên máy/);
assert.match(KEY_CLEAR_LABEL, /phiên/);
assert.match(KEY_CLEARED, /phiên/);
assert.doesNotMatch(KEY_CLEARED, /máy này/);
console.log("desktop posting suggestion tests passed");
