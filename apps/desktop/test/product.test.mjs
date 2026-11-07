import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describeProduct, MAX_SOURCE_CHARS, normalizeInfo, normalizeScripts, readParts } from "../src/features/product/ai.ts";
import {
  addMedia, addPaths, backgroundBox, buildRequest, coverOverflow, dragFocus, focusKey, moveItem, parseBackgroundMemory, parseBackgrounds,
  keepsVoice, parseFeatures, parseProductSpec, parseRuns, parseStatus, photosToRead, readForm, runRow, TEMPLATES, toBase64, videoMedia,
} from "../src/features/product/model.ts";
import { backgroundHint, captionAt, templateButtonsHtml, templateNote } from "../src/features/product/look.ts";
import { anchorStyle, ASS_EM, captionChunks, cardTexts, cardTimes, inkStyle, LAYOUT, mediaHeight } from "../src/features/product/layout.ts";
import { escapeHtml, previewLayers } from "../src/features/product/preview.ts";

// What Claude read is trimmed, bounded and de-duplicated; an empty read is an error.
assert.deepEqual(normalizeInfo({ name: "  Nồi chiên  5L ", price: " 299.000đ", features: ["Ít dầu", "Ít dầu", "", 3, "Dễ rửa"], audience: null, notes: "x" }),
  { name: "Nồi chiên 5L", price: "299.000đ", features: ["Ít dầu", "Dễ rửa"], audience: "", notes: "x" });
assert.throws(() => normalizeInfo({ name: "", price: "1đ", features: [] }), /không đọc được/);

// Reading works from pasted text alone, photos alone, or both; the text is tidied and bounded.
const photo = { mediaType: "image/jpeg", data: "QUJD" };
const textOnly = readParts({ text: "  Nồi chiên 5L\r\n\r\n\r\n\r\nGiá 299k   \n", photos: [] });
assert.equal(textOnly.length, 1);
assert.match(textOnly[0].text, /^Đọc thông tin sản phẩm trong văn bản dưới đây\.\n\n<van_ban_nguoi_ban>\nNồi chiên 5L\n\nGiá 299k\n<\/van_ban_nguoi_ban>$/);
const photosOnly = readParts({ text: " ", photos: [photo, photo] });
assert.deepEqual(photosOnly.map((part) => "image" in part), [true, true, false]);
assert.equal(photosOnly[2].text, "Đọc thông tin sản phẩm trong các ảnh trên.");
assert.match(readParts({ text: "Mô tả", photos: [photo] })[1].text, /văn bản dưới đây và các ảnh trên/);
assert.ok(readParts({ text: "a".repeat(MAX_SOURCE_CHARS + 500), photos: [] })[0].text.length < MAX_SOURCE_CHARS + 120);
assert.throws(() => readParts({ text: "  ", photos: [] }), /dán mô tả/);
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
assert.equal(parseFeatures("x".repeat(300))[0].length, 200);

// Photos and clips: added once, reordered, some only for the AI to read.
let media = addMedia([], ["D:/a.jpg", "D:/b.mp4", "D:/a.jpg", "D:/c.png"], 30);
assert.deepEqual(media.map((item) => item.path), ["D:/a.jpg", "D:/b.mp4", "D:/c.png"]);
media = moveItem(media, 2, 0);
assert.deepEqual(media.map((item) => item.path), ["D:/c.png", "D:/a.jpg", "D:/b.mp4"]);
assert.deepEqual(moveItem([1, 2, 3], 0, 9), [2, 3, 1]);
assert.deepEqual(moveItem([1, 2, 3], 5, 0), [1, 2, 3]);
media[0].video = false;
assert.deepEqual(videoMedia(media), ["D:/a.jpg", "D:/b.mp4"]);
assert.deepEqual(photosToRead(media), ["D:/c.png", "D:/a.jpg"], "clips are not read; photos kept out of the video still are");
assert.equal(addMedia(media, ["D:/x.jpg", "D:/y.jpg"], 4).length, 4);

// The render request, or why it cannot start.
const base = { info, voiceId: "vi-a", scripts: [{ text: " Một. ", use: true }, { text: "Hai.", use: false }], visuals: ["D:/a.jpg"], music: null, volumePercent: 15, cpuOnly: false, outputRoot: null };
assert.deepEqual(buildRequest(base), { name: "Nồi", price: "1đ", voiceId: "vi-a", variants: ["Một."], visuals: ["D:/a.jpg"], music: null, musicVolume: 0.15, background: null, template: "ban-tin", features: [], focus: { x: 0.5, y: 0.5 }, speed: 1, cpuOnly: false, outputRoot: null });
assert.deepEqual([buildRequest({ ...base, template: "toan-man-hinh", speed: 1.2 }).template, buildRequest({ ...base, template: "toan-man-hinh", speed: 1.2 }).speed], ["toan-man-hinh", 1.2]);
assert.deepEqual([buildRequest({ ...base, template: "khac", speed: 3 }).template, buildRequest({ ...base, template: "khac", speed: 3 }).speed], ["ban-tin", 1]);
assert.equal(buildRequest({ ...base, background: "D:/nen.png" }).background, "D:/nen.png");
assert.match(buildRequest({ ...base, scripts: [{ text: "x", use: false }] }), /ít nhất một kịch bản/);
assert.match(buildRequest({ ...base, scripts: Array(6).fill({ text: "x", use: true }) }), /tối đa 5/);
assert.match(buildRequest({ ...base, visuals: [] }), /ảnh hoặc clip/);
// Chỉ chữ needs no media, sends the highlights, and keeps the background with its focus.
const text = buildRequest({ ...base, visuals: ["D:/a.jpg"], template: "chi-chu", background: "C:/nen.png", focus: { x: 2, y: 0.25 } });
assert.deepEqual([text.template, text.visuals, text.features, text.background, text.focus], ["chi-chu", [], ["a", "b"], "C:/nen.png", { x: 1, y: 0.25 }]);
assert.equal(typeof buildRequest({ ...base, visuals: [], template: "chi-chu" }), "object");
assert.match(buildRequest({ ...base, visuals: [], template: "chi-chu", info: { ...info, name: "", features: [] } }), /Chỉ chữ/);
assert.equal(buildRequest({ ...base, template: "toan-man-hinh", background: "C:/nen.png" }).background, null, "full screen shows no background");
assert.match(buildRequest({ ...base, voiceId: null }), /giọng/);

// Status from the engine.
assert.equal(parseStatus({ state: "WHAT", message: "" }), null);
assert.equal(parseStatus({ state: "CANCELLED", message: "Đã hủy. Giữ lại 1 video đã xong.", videos: [] }).state, "CANCELLED");
assert.deepEqual(parseStatus({ state: "RUNNING", message: "m", done: 1, total: 3, videos: [{ index: 1, ok: true, path: "p" }, { bad: 1 }] }),
  { state: "RUNNING", message: "m", done: 1, total: 3, videos: [{ index: 1, ok: true, path: "p" }] });

// Base64 for the photos sent to Claude, including large buffers.
assert.equal(toBase64(new TextEncoder().encode("DubFlow")), "RHViRmxvdw==");
const large = new Uint8Array(100_000).map((_, i) => i % 251);
assert.equal(toBase64(large), Buffer.from(large).toString("base64"));

// The background library and the remembered choice per template.
const id = "a".repeat(64);
assert.deepEqual(parseBackgrounds([{ id, extension: "png", name: "n.png", width: 1080, height: 720, path: "C:/b/x.png" }, { id: "../x", path: "p", name: "", width: 1, height: 1 }, null]).map((item) => item.id), [id]);
assert.deepEqual(parseBackgrounds("x"), []);
assert.deepEqual(parseBackgroundMemory(JSON.stringify({ choice: { "ban-tin": id, "chi-chu": "bad", khac: id }, focus: { [focusKey("ban-tin", id)]: { x: 3, y: -1 }, junk: { x: 0, y: 0 } } })),
  { choice: { "ban-tin": id }, focus: { [`ban-tin:${id}`]: { x: 1, y: 0 } } });
assert.deepEqual(parseBackgroundMemory("{broken"), { choice: {}, focus: {} });
assert.deepEqual(TEMPLATES.map((item) => item.id), ["ban-tin", "toan-man-hinh", "chi-chu"]);

// Which box the background fills, and dragging the focus like the engine's crop.
assert.equal(backgroundBox("ban-tin", { width: 1080, height: 720 }, LAYOUT.fullFrameRatio), "panel");
assert.equal(backgroundBox("ban-tin", { width: 1080, height: 1920 }, LAYOUT.fullFrameRatio), "frame");
assert.equal(backgroundBox("chi-chu", { width: 1080, height: 720 }, LAYOUT.fullFrameRatio), "frame");
assert.equal(backgroundBox("toan-man-hinh", { width: 1080, height: 1920 }, LAYOUT.fullFrameRatio), null);
assert.deepEqual(coverOverflow({ width: 1600, height: 900 }, { width: 1080, height: 720 }), { x: 200, y: 0 });
assert.deepEqual(dragFocus({ x: 0.5, y: 0.5 }, { x: 200, y: 0 }, 50, 30), { x: 0.25, y: 0.5 });
assert.deepEqual(dragFocus({ x: 0.5, y: 0.5 }, { x: 200, y: 0 }, -500, 0), { x: 1, y: 0.5 });

// The preview shares the engine's layout (tests/product keeps layout.json equal to the engine).
assert.deepEqual(JSON.parse(readFileSync(new URL("../src/features/product/layout.json", import.meta.url), "utf8")), LAYOUT);
assert.deepEqual([mediaHeight("ban-tin"), mediaHeight("toan-man-hinh"), mediaHeight("chi-chu")], [1200, 1920, 0]);
assert.equal(LAYOUT.templates["ban-tin"].title.y, 1244);
const title = anchorStyle(LAYOUT.templates["ban-tin"].title);
assert.match(title, /^left: 60px; top: 1244px; width: 960px; transform: translateY\(0\); text-align: center; /);
assert.match(title, new RegExp(`font: 700 ${(60 * ASS_EM).toFixed(2)}px/60px Arial`));
assert.match(anchorStyle(LAYOUT.templates["ban-tin"].price), /top: 1850px; .*translateY\(-100%\)/);
assert.match(anchorStyle(LAYOUT.templates["chi-chu"].counter), /^left: 162px; top: 612px; transform: translateY\(0\); text-align: left/);
assert.match(inkStyle(LAYOUT.templates["ban-tin"].price), /^background: rgba\(216, 28, 28, 1\); padding: 16px; /);
assert.match(inkStyle(LAYOUT.templates["ban-tin"].caption), /text-shadow: 3px 3px 0 rgba\(0, 0, 0, 0.608\)$/);
assert.equal(inkStyle(LAYOUT.templates["ban-tin"].outlined.caption).split("px 0 #000").length - 1, 8, "an outline all round");

// Cards share the video like the engine's layout.card_times.
assert.deepEqual(cardTimes(3, 9000), [[0, 3000], [3000, 6000], [6000, 9000]]);
assert.equal(cardTimes(8, 5000).length, 2);
assert.deepEqual(cardTimes(2, 1000), [[0, 1000]]);
assert.deepEqual(cardTimes(0, 9000), []);
assert.deepEqual(cardTexts(" Nồi ", []), { title: "", cards: ["Nồi"] });
assert.deepEqual(cardTexts("Nồi", [" Ít  dầu ", ""]), { title: "Nồi", cards: ["Ít dầu"] });
assert.deepEqual(captionChunks("Chào cả nhà! Nồi chiên không dầu dung tích lớn, nấu được cho cả gia đình bốn người ăn."),
  ["Chào cả nhà!", "Nồi chiên không dầu dung tích lớn, nấu được cho", "cả gia đình bốn người ăn."]);

// The preview layers: escaped text at the template's places, the background box, the current card.
const state = { template: "ban-tin", name: "Nồi <5L>", price: "299.000đ", features: ["Ít dầu", "Dễ rửa"], caption: "Giá tốt", media: { url: "blob:a", label: "a.jpg" }, background: null, timeMs: 0, totalMs: 9000 };
const news = previewLayers(state);
assert.match(news.base, /background: #0E7A3B/);
assert.match(news.base, /top: 1200px; height: 10px; background: #FFD400/);
assert.match(news.base, /Nồi &lt;5L&gt;/);
assert.equal(news.card, "");
assert.match(news.caption, /top: 1560px/);
const panel = previewLayers({ ...state, background: { url: "blob:b", width: 1600, height: 900, focus: { x: 0.25, y: 1 }, box: "panel" } });
assert.match(panel.base, /class="pv-bg" data-box="panel" style="top: 1200px; height: 720px"/);
assert.match(panel.base, /object-position: 25.00% 100.00%/);
assert.ok(panel.base.indexOf("pv-bg") < panel.base.indexOf("pv-media"), "the media sits over the background");
const full = previewLayers({ ...state, template: "toan-man-hinh" });
assert.match(full.base, /class="pv-media" style="height: 1920px"/);
assert.match(full.caption, /top: 1660px/);
const cards = previewLayers({ ...state, template: "chi-chu", media: null, timeMs: 5000 });
assert.match(cards.base, /linear-gradient\(180deg, #14213D, #0E7A3B\)/);
assert.doesNotMatch(cards.base, /pv-media/);
assert.match(cards.card, /data-card="1"/);
assert.match(cards.card, /2\/2/);
assert.match(cards.card, />Dễ rửa</);
assert.match(previewLayers({ ...state, template: "chi-chu", background: { url: null, width: 1080, height: 1920, focus: { x: 0.5, y: 0.5 }, box: "frame" } }).base, /pv-scrim/);
assert.equal(previewLayers({ ...state, template: "chi-chu", name: "", features: [] }).card, "");
assert.equal(previewLayers({ ...state, price: " " }).base.includes("299"), false);
assert.equal(escapeHtml(`<a href="x">'&`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;");

// Mẫu (Issue #426): a product video's request, earlier runs, and whether a new render keeps the narration.
{
  const raw = { spec: { schema_version: 1, name: "Nồi", price: "299k", voice_id: "vi-a", variants: ["Một.", "Hai."], visuals: ["D:/a.jpg", 3], music: null,
    music_volume: 0.2, background: "C:/bg.png", template: "chi-chu", speed: 1.1, features: ["x", 2], focus: { x: 2, y: 0.25 } },
    variant: 2, run: "D:/o/run-1", duration_ms: 31000, narration: true };
  const info = parseProductSpec(raw);
  assert.equal(info.spec.script, "Hai.");
  assert.deepEqual([info.spec.visuals, info.spec.features, info.spec.focus, info.spec.template, info.spec.speed], [["D:/a.jpg"], ["x"], { x: 1, y: 0.25 }, "chi-chu", 1.1]);
  assert.equal(info.durationMs, 31000);
  for (const bad of [{ ...raw, variant: 3 }, { ...raw, run: "" }, { ...raw, variant: 0 }, null]) assert.equal(parseProductSpec(bad), null);
  assert.equal(keepsVoice(info, { script: " Hai. ", voiceId: "vi-a", speed: 1.1 }), true);
  for (const change of [{ script: "Ba." }, { voiceId: "vi-b" }, { speed: 1 }]) {
    assert.equal(keepsVoice(info, { script: "Hai.", voiceId: "vi-a", speed: 1.1, ...change }), false);
  }
  assert.equal(keepsVoice({ ...info, narration: false }, { script: "Hai.", voiceId: "vi-a", speed: 1.1 }), false, "no narration left to reuse");
  const runs = parseRuns([{ dir: "D:/o/run-2", name: "Nồi", created: 1_793_500_000, status: { state: "COMPLETED", message: "x", videos: [
    { index: 1, ok: true, path: "D:/o/run-2/video-1.mp4", duration_ms: 30400 }, { index: 2, ok: false, message: "lỗi" }] } }, { dir: 5 }, { dir: "D:/o/run-1", status: null }]);
  assert.equal(runs.length, 2);
  const rows = runs.map(runRow);
  assert.equal(rows[0].videos.length, 1);
  assert.equal(rows[0].videos[0].displayName, "Nồi · video 1");
  assert.equal(rows[0].dir, runs[0].dir);
  assert.match(rows[0].text, /^1 video · \d\d\/\d\d \d\d:\d\d$/);
  assert.equal((templateButtonsHtml().match(/data-template=/g) ?? []).length, 3);
  assert.match(templateNote("chi-chu"), /thẻ chữ/);
  assert.match(backgroundHint("ban-tin", { id: "a".repeat(64), path: "x", name: "n", extension: "png", width: 1600, height: 900 }), /1600×900 được cắt cho vừa nền dưới 1080×720/);
  assert.equal(captionAt("", 0, 1000), "Phụ đề lời đọc hiện ở đây");
}

// The Bán hàng list: one row per run, like the queue, with its finished videos and progress.
{
  const done = runRow({ dir: "D:\\a", name: "Nồi", template: "ban-tin", created: 0, status: { state: "COMPLETED", done: 2, total: 2, message: "x",
    videos: [{ index: 1, ok: true, path: "D:\\a\\1.mp4", duration_ms: 31_400 }, { index: 2, ok: false, message: "lỗi" }] } });
  assert.equal(done.title, "Nồi");
  assert.equal(done.text, "1 video");
  assert.equal(done.fraction, null);
  assert.deepEqual(done.videos, [{ index: 1, outputPath: "D:\\a\\1.mp4", displayName: "Nồi · video 1", seconds: 31 }]);
  const busy = runRow({ dir: "b", name: "", template: "ban-tin", created: 0, status: { state: "RUNNING", done: 1, total: 4, message: "Đang render video 2/4…", videos: [] } });
  assert.equal(busy.title, "Video bán hàng");
  assert.equal(busy.text, "Đang render video 2/4…");
  assert.equal(busy.fraction, 0.25);
  assert.equal(runRow({ dir: "c", name: "x", template: "ban-tin", created: 0, status: { state: "CANCELLED", done: 0, total: 2, message: "", videos: [] } }).text, "Đã hủy");
  assert.equal(runRow({ dir: "d", name: "x", template: "ban-tin", created: 0, status: null }).state, null);
}

console.log("product tests passed");
