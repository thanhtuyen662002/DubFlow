import assert from "node:assert/strict";
import {
  TOPICS, bilibiliMid, cleanTitle, discoveryError, formatCount, formatDuration, loadGlossCache, mergeVideos, needsTitleGloss, parseCollected,
  parseGlosses, parseTrends, parseVideos, previewSource, rememberGloss, saveGlossCache,
} from "../src/features/discovery/model.ts";

// Trends: keyword required, unknown heat stays null, ranks default to order.
const trends = parseTrends({ ok: true, next_page: null, items: [
  { keyword: "  仙侠  剧 ", rank: 1, heat: 9800000 }, { keyword: "", rank: 2 }, { keyword: "美食", heat: null }, null,
] });
assert.equal(trends.ok, true);
assert.deepEqual(trends.items, [{ keyword: "仙侠 剧", rank: 1, heat: 9800000 }, { keyword: "美食", rank: 3, heat: null }]);

// Failures carry the adapter code; a malformed answer is never an empty success.
assert.deepEqual(parseTrends({ ok: false, code: "BLOCKED", message: "412" }), { ok: false, code: "BLOCKED", message: "412" });
assert.equal(parseTrends(null).ok, false);
assert.equal(parseTrends({ items: [] }).ok, false);

// Videos: only https pages; counts are null unless real numbers.
const videos = parseVideos({ ok: true, next_page: 2, items: [
  { title: "A", author: "UP", duration_s: 95, plays: 12000, cover_url: "https://i0.hdslb.com/a.jpg", page_url: "https://www.bilibili.com/video/BV1" },
  { title: "B", page_url: "http://www.bilibili.com/video/BV2" },
  { title: "", duration_s: -1, plays: "5", cover_url: "javascript:alert(1)", page_url: "https://www.bilibili.com/video/BV3" },
] });
assert.equal(videos.ok, true);
assert.equal(videos.nextPage, 2);
assert.equal(videos.items.length, 2);
assert.deepEqual(videos.items[1], { title: "(không có tiêu đề)", author: "", cover: null, durationS: null, plays: null,
  pageUrl: "https://www.bilibili.com/video/BV3", fetchUrl: "https://www.bilibili.com/video/BV3" });

// Page collection keeps the host's fetch URL (Kuaishou media) separate from the page.
const collected = parseCollected([
  { page_url: "https://www.kuaishou.com/short-video/3x1", fetch_url: "https://v2.kwaicdn.com/a.mp4", title: "t", cover_url: null },
  { page_url: "https://www.kuaishou.com/short-video/3x2" },
]);
assert.equal(collected.length, 1);
assert.equal(collected[0].fetchUrl, "https://v2.kwaicdn.com/a.mp4");
assert.deepEqual(parseCollected({ items: [] }), []);

// Merging keeps order and drops pages already listed.
const merged = mergeVideos(videos.items, [collected[0], videos.items[0]]);
assert.deepEqual(merged.map((video) => video.pageUrl), ["https://www.bilibili.com/video/BV1", "https://www.bilibili.com/video/BV3", "https://www.kuaishou.com/short-video/3x1"]);

assert.equal(bilibiliMid("https://space.bilibili.com/546195/video"), "546195");
assert.equal(bilibiliMid("https://space.bilibili.com/546195"), "546195");
assert.equal(bilibiliMid("https://space.bilibili.com.evil/546195"), null);
assert.equal(bilibiliMid("https://www.bilibili.com/video/BV1"), null);

assert.equal(formatCount(null), "");
assert.equal(formatCount(9999), "9999");
assert.equal(formatCount(12_000), "1,2 vạn");
assert.equal(formatCount(9_800_000), "980 vạn");
assert.equal(formatCount(250_000_000), "2,5 tỷ");
assert.equal(formatDuration(95), "1:35");
assert.equal(formatDuration(null), "");
assert.match(discoveryError("BLOCKED", "bilibili"), /Mở trang/);
assert.match(discoveryError("UNSUPPORTED", "kuaishou"), /Kuaishou/);

console.log("desktop discovery model tests passed");

// Keyword glosses must line up with the terms sent; topics map to Chinese searches.
const glossed = parseGlosses({ ok: true, items: ["Tiên hiệp", ""] }, ["仙侠", "坏"]);
assert.deepEqual([...glossed], [["仙侠", "Tiên hiệp"], ["坏", ""]]);
assert.equal(parseGlosses({ ok: true, items: ["a"] }, ["x", "y"]), null);
assert.equal(parseGlosses({ ok: false, code: "TRANSLATION_PACKAGE_MISSING" }, ["x"]), null);
assert.ok(TOPICS.length >= 20 && TOPICS.every((topic) => topic.vi && /[\u4e00-\u9fff]/.test(topic.zh)));
assert.equal(new Set(TOPICS.map((topic) => topic.zh)).size, TOPICS.length);
console.log("desktop discovery gloss and topic tests passed");

// Preview sources: official embeds with strictly checked ids, Kuaishou media only on its CDNs.
const page = (pageUrl, fetchUrl = pageUrl) => ({ pageUrl, fetchUrl });
assert.deepEqual(previewSource(page("https://www.bilibili.com/video/BV1GJ411x7h7"), { muted: true }), { kind: "iframe", vertical: false,
  src: "https://player.bilibili.com/player.html?bvid=BV1GJ411x7h7&autoplay=1&muted=1&danmaku=0&high_quality=0&as_wide=1&page=1" });
assert.match(previewSource(page("https://www.bilibili.com/video/BV1GJ411x7h7/?spm_id_from=333"), { muted: false }).src, /bvid=BV1GJ411x7h7&autoplay=1&muted=0&/);
for (const bad of ["https://www.bilibili.com/video/BV1GJ411x7h", "https://www.bilibili.com/video/BV1GJ411x7h77", "https://www.bilibili.com/video/av170001",
  "https://www.bilibili.com/video/BV1GJ411x7h%26", "https://www.bilibili.com.evil/video/BV1GJ411x7h7", "http://www.bilibili.com/video/BV1GJ411x7h7",
  "https://www.bilibili.com/list/BV1GJ411x7h7", "https://www.bilibili.com:444/video/BV1GJ411x7h7"]) {
  assert.equal(previewSource(page(bad), { muted: false }), null, bad);
}
assert.deepEqual(previewSource(page("https://www.douyin.com/video/7431852694117028610"), { muted: false }),
  { kind: "iframe", vertical: true, src: "https://open.douyin.com/player/video?vid=7431852694117028610&autoplay=1" });
// No documented mute flag: Douyin never plays as a muted hover preview.
assert.equal(previewSource(page("https://www.douyin.com/video/7431852694117028610"), { muted: true }), null);
for (const bad of ["https://www.douyin.com/video/1234567", "https://www.douyin.com/video/1234567890123456789012345", "https://www.douyin.com/video/74318526x4",
  "https://www.douyin.com/user/7431852694117028610", "https://douyin.com.evil/video/7431852694117028610"]) {
  assert.equal(previewSource(page(bad), { muted: false }), null, bad);
}
const ks = "https://www.kuaishou.com/short-video/3xk8f2abcd";
assert.deepEqual(previewSource(page(ks, "https://v2.kwaicdn.com/ksc2/a.mp4?pkey=x"), { muted: true }),
  { kind: "video", vertical: true, src: "https://v2.kwaicdn.com/ksc2/a.mp4?pkey=x" });
assert.equal(previewSource(page(ks, "https://txmov2.a.yximgs.com/a.mp4"), { muted: true })?.kind, "video");
assert.equal(previewSource(page(ks), { muted: true }), null);
for (const bad of ["https://kwaicdn.com/a.mp4", "https://v2.kwaicdn.com.evil/a.mp4", "http://v2.kwaicdn.com/a.mp4", "https://evil.com/kwaicdn.com/a.mp4"]) {
  assert.equal(previewSource(page(ks, bad), { muted: true }), null, bad);
}
assert.equal(previewSource(page("https://example.com/video/BV1GJ411x7h7"), { muted: false }), null);

// Titles: hashtags, mentions and emoji go; long text is cut at a word when it can be.
assert.equal(cleanTitle("男人抠出一块骨头，鼻子里竟藏着活物😱 #悬疑 #电影解说"), "男人抠出一块骨头，鼻子里竟藏着活物");
assert.equal(cleanTitle("1939年，穷女孩混进上流舞会🔥🔥 @电影小助手  "), "1939年，穷女孩混进上流舞会");
assert.equal(cleanTitle("萌宠日常：猫咪第一次见到雪❄️#萌宠#猫"), "萌宠日常：猫咪第一次见到雪");
assert.equal(cleanTitle("👨‍👩‍👧 一家人 🇨🇳 旅行"), "一家人 旅行");
const longHan = "仙".repeat(100);
assert.equal(cleanTitle(longHan), "仙".repeat(80));
const words = Array.from({ length: 30 }, (_, i) => `词${i}`).join(" ");
const cut = cleanTitle(words);
assert.ok(Array.from(cut).length <= 80 && words.startsWith(cut) && words[cut.length] === " ", cut);
assert.ok(Array.from(cleanTitle("𠀀".repeat(90))).length === 80);
assert.equal(needsTitleGloss("男人抠出一块骨头"), true);
assert.equal(needsTitleGloss("农村生活vlog｜奶奶做的手工面条"), true);
assert.equal(needsTitleGloss("Vlog du lịch Trùng Khánh 4K"), false);
assert.equal(needsTitleGloss("Phim hay 仙侠"), false);
assert.equal(needsTitleGloss("2026 !!!"), false);
assert.equal(needsTitleGloss(""), false);

// Gloss cache: most recently used last, oldest dropped past the limit, junk ignored.
const cache = new Map();
rememberGloss(cache, "a", "A", 3);
rememberGloss(cache, "b", "B", 3);
rememberGloss(cache, "c", "C", 3);
rememberGloss(cache, "a", "A", 3);
rememberGloss(cache, "d", "D", 3);
assert.deepEqual([...cache.keys()], ["c", "a", "d"]);
assert.deepEqual(JSON.parse(saveGlossCache(new Map([["x", ""], ["y", "Y"], ["z", "Z"]]), 1)), [["z", "Z"]]);
const stored = loadGlossCache(JSON.stringify([["a", "A"], ["b", ""], [1, "x"], ["c"], "junk", ["d", "D"], ["a", "A2"]]), 800);
assert.deepEqual([...stored], [["d", "D"], ["a", "A2"]]);
assert.equal(loadGlossCache("{not json").size, 0);
assert.equal(loadGlossCache(null).size, 0);
assert.equal(loadGlossCache(JSON.stringify({ a: "A" })).size, 0);
const big = loadGlossCache(JSON.stringify(Array.from({ length: 900 }, (_, i) => [`t${i}`, `v${i}`])));
assert.equal(big.size, 800);
assert.equal([...big.keys()][0], "t100");
console.log("desktop discovery preview and title tests passed");
