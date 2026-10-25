import assert from "node:assert/strict";
import { normalizeSuggestion } from "../src/features/seo/ai.ts";
import { parseOffline } from "../src/features/seo/view.ts";

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
console.log("desktop posting suggestion tests passed");
