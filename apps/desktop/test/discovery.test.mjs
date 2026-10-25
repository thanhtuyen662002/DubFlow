import assert from "node:assert/strict";
import {
  bilibiliMid, discoveryError, formatCount, formatDuration, mergeVideos, parseCollected, parseTrends, parseVideos,
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
