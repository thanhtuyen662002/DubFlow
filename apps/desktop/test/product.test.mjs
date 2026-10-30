import assert from "node:assert/strict";
import { describeProduct, normalizeInfo, normalizeScripts } from "../src/features/product/ai.ts";
import { addPaths, buildRequest, parseFeatures, parseStatus, readForm, toBase64 } from "../src/features/product/model.ts";

// What Claude read is trimmed, bounded and de-duplicated; an empty read is an error.
assert.deepEqual(normalizeInfo({ name: "  Nồi chiên  5L ", price: " 299.000đ", features: ["Ít dầu", "Ít dầu", "", 3, "Dễ rửa"], audience: null, notes: "x" }),
  { name: "Nồi chiên 5L", price: "299.000đ", features: ["Ít dầu", "Dễ rửa"], audience: "", notes: "x" });
assert.throws(() => normalizeInfo({ name: "", price: "1đ", features: [] }), /không đọc được/);
assert.equal(normalizeInfo({ name: "a", features: Array.from({ length: 12 }, (_, i) => `f${i}`) }).features.length, 8);

// Scripts are narration only: no emoji, hashtags or markdown, and at most `count`.
assert.deepEqual(normalizeScripts({ scripts: ["Chào cả nhà! 🔥 Mua ngay #sale **nhé**.", "  ", "🔥🔥", "Kịch bản hai.", "Ba."] }, 2),
  ["Chào cả nhà! Mua ngay nhé.", "Kịch bản hai."]);
assert.throws(() => normalizeScripts({ scripts: ["🔥"] }, 3), /không trả về/);
assert.equal(normalizeScripts({ scripts: ["a".repeat(5000)] }, 1)[0].length, 3000);

// The prompt's facts come only from the form; empty fields are left out.
assert.equal(describeProduct({ name: "Nồi", price: "", features: ["Ít dầu"], audience: "", notes: "" }), "Tên sản phẩm: Nồi\nĐiểm nổi bật:\n- Ít dầu");

// The form.
assert.deepEqual(parseFeatures("- Ít dầu\n• Dễ rửa\n\n1. Ít dầu\n  2) Bảo hành 12 tháng "), ["Ít dầu", "Dễ rửa", "Bảo hành 12 tháng"]);
const info = readForm({ name: " Nồi ", price: " 1đ ", features: "a\nb", audience: "", notes: "" });
assert.deepEqual(info, { name: "Nồi", price: "1đ", features: ["a", "b"], audience: "", notes: "" });
assert.deepEqual(addPaths(["a", "b"], ["b", "c", "d"], 3), ["a", "b", "c"]);

// The render request, or why it cannot start.
const base = { info, voiceId: "vi-a", scripts: [{ text: " Một. ", use: true }, { text: "Hai.", use: false }], visuals: ["D:/a.jpg"], music: null, volumePercent: 15, cpuOnly: false, outputRoot: null };
assert.deepEqual(buildRequest(base), { name: "Nồi", price: "1đ", voiceId: "vi-a", variants: ["Một."], visuals: ["D:/a.jpg"], music: null, musicVolume: 0.15, cpuOnly: false, outputRoot: null });
assert.match(buildRequest({ ...base, scripts: [{ text: "x", use: false }] }), /ít nhất một kịch bản/);
assert.match(buildRequest({ ...base, scripts: Array(6).fill({ text: "x", use: true }) }), /tối đa 5/);
assert.match(buildRequest({ ...base, visuals: [] }), /ảnh hoặc clip/);
assert.match(buildRequest({ ...base, voiceId: null }), /giọng/);

// Status from the engine.
assert.equal(parseStatus({ state: "WHAT", message: "" }), null);
assert.deepEqual(parseStatus({ state: "RUNNING", message: "m", done: 1, total: 3, videos: [{ index: 1, ok: true, path: "p" }, { bad: 1 }] }),
  { state: "RUNNING", message: "m", done: 1, total: 3, videos: [{ index: 1, ok: true, path: "p" }] });

// Base64 for the photos sent to Claude, including large buffers.
assert.equal(toBase64(new TextEncoder().encode("DubFlow")), "RHViRmxvdw==");
const large = new Uint8Array(100_000).map((_, i) => i % 251);
assert.equal(toBase64(large), Buffer.from(large).toString("base64"));

console.log("product tests passed");
