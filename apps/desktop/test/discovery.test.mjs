import assert from "node:assert/strict";
import {
  TOPICS, bilibiliMid, cleanTitle, discoveryError, formatCount, formatDuration, loadGlossCache, mergeVideos, needsTitleGloss, pageAddress, parseCollected,
  parseGlosses, parseTrends, needsChinese, topicChinese, parseChinese, parseVideos, previewSource, refreshVideos, rememberGloss, saveGlossCache, seenOnPage,
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

// While browsing, a video page adds only the video being watched; a list page adds every card.
const onVideoPage = parseCollected([
  { page_url: "https://www.bilibili.com/video/BV1VKyxQ6xE9", fetch_url: "https://www.bilibili.com/video/BV1VKyxQ6xE9", title: "Đang xem", current: true },
  { page_url: "https://www.bilibili.com/video/BV1aaaaaaaaa", fetch_url: "https://www.bilibili.com/video/BV1aaaaaaaaa", title: "Gợi ý" },
]);
assert.deepEqual(onVideoPage.map((video) => video.current), [true, false]);
assert.deepEqual(seenOnPage(onVideoPage).map((video) => video.title), ["Đang xem"]);
assert.equal("current" in seenOnPage(onVideoPage)[0], false);
assert.equal(seenOnPage(onVideoPage.slice(1)).length, 1);
assert.equal(parseCollected([{ page_url: "https://a.example/x", fetch_url: "https://a.example/x", current: "yes" }])[0].current, false);

// A card first read without a title takes the page's title later; known titles stay.
const untitled = { ...videos.items[1], cover: null };
const better = { ...videos.items[1], title: "Có tiêu đề", cover: "https://i0.hdslb.com/x.jpg" };
const refreshed = refreshVideos([videos.items[0], untitled], [better]);
assert.equal(refreshed[0], videos.items[0]);
assert.equal(refreshed[1].title, "Có tiêu đề");
assert.equal(refreshed[1].cover, "https://i0.hdslb.com/x.jpg");
assert.equal(refreshVideos([better], [{ ...better, title: "Khác" }])[0], better);

assert.equal(bilibiliMid("https://space.bilibili.com/546195/video"), "546195");
assert.equal(bilibiliMid("https://space.bilibili.com/546195"), "546195");
assert.equal(bilibiliMid("https://space.bilibili.com.evil/546195"), null);
assert.equal(bilibiliMid("https://www.bilibili.com/video/BV1"), null);

// The panel toolbar shows the address decoded, without its scheme or a trailing slash, at most 300 characters.
assert.equal(pageAddress("https://www.douyin.com/search/%E7%BE%8E%E9%A3%9F?type=video"), "www.douyin.com/search/美食?type=video");
assert.equal(pageAddress("https://a.example/%E7%ZZ"), "a.example/%E7%ZZ");
assert.equal(pageAddress("https://www.kuaishou.com/"), "www.kuaishou.com");
assert.equal(pageAddress(`https://a.example/${"x".repeat(400)}`).length, 300);

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

// Vietnamese search phrases are translated first; Chinese, links and empty text are not.
assert.equal(needsChinese("tháp nuôi lợn"), true);
assert.equal(needsChinese("am thuc"), true);
assert.equal(needsChinese("仙侠 剧"), false);
assert.equal(needsChinese("vlog 美食"), false);
assert.equal(needsChinese("https://www.douyin.com/user/abc"), false);
assert.equal(needsChinese("  123 "), false);
assert.equal(topicChinese("ẩm  THỰC"), "美食");
assert.equal(topicChinese("tien hiep"), "仙侠剧");
assert.equal(topicChinese("nuôi lợn"), null);
assert.equal(parseChinese({ ok: true, text: "  养猪  大楼 ", engine: "google" }), "养猪 大楼");
assert.equal(parseChinese({ ok: true, text: "tien hiep" }), null);
assert.equal(parseChinese({ ok: false, code: "NETWORK" }), null);
assert.equal(parseChinese(null), null);

// "Tất cả nguồn" (#978): suffix chips, per-source queries, mixed results and quality labels.
{
  const { ALL_SOURCES, PLATFORMS, SUFFIXES, interleave, joinQuery, needsEnglish, parseMedia, qualityLabel, splitSuffixes, toggleSuffix } =
    await import("../src/features/discovery/model.ts");
  const [broll, drone, noText] = SUFFIXES;
  assert.equal(PLATFORMS[0].id, "all");
  assert.deepEqual(ALL_SOURCES, ["bilibili", "youtube", "skypixel"]);
  // A chip goes at the end, a second click takes it away; other suffixes keep their order.
  assert.equal(toggleSuffix("白鹤滩水电站", broll, "zh"), "白鹤滩水电站 空镜");
  assert.equal(toggleSuffix("白鹤滩水电站 空镜", drone, "zh"), "白鹤滩水电站 空镜 航拍 4K");
  assert.equal(toggleSuffix("白鹤滩水电站 空镜 航拍 4K", broll, "zh"), "白鹤滩水电站 航拍 4K");
  assert.equal(toggleSuffix("Baihetan dam", noText, "en"), "Baihetan dam no text");
  // The phrase is translated without its suffixes, which come back in each source's language.
  const split = splitSuffixes("đập thủy điện Bạch Hạc Than 空镜  航拍 4K");
  assert.equal(split.base, "đập thủy điện Bạch Hạc Than");
  assert.deepEqual(split.suffixes, [broll, drone]);
  assert.equal(joinQuery("白鹤滩水电站", split.suffixes, "zh"), "白鹤滩水电站 空镜 航拍 4K");
  assert.equal(joinQuery("Baihetan Hydropower Station", split.suffixes, "en"), "Baihetan Hydropower Station B-roll 4K drone");
  // English forms count too; a word that merely ends like a suffix does not.
  assert.deepEqual(splitSuffixes("Qingdao port B-roll").suffixes, [broll]);
  assert.equal(splitSuffixes("Bigbroll").base, "Bigbroll");
  assert.equal(splitSuffixes("空镜").base, "");
  assert.equal(needsEnglish("白鹤滩"), true);
  assert.equal(needsEnglish("đập thủy điện"), true);
  assert.equal(needsEnglish("Baihetan dam 4K"), false);

  // Mixed one by one, never the same page twice.
  const video = (source, id) => ({ title: id, author: "", cover: null, durationS: null, plays: null, pageUrl: `https://x.test/${id}`, fetchUrl: `https://x.test/${id}`, source });
  assert.deepEqual(interleave([[video("bilibili", "b1"), video("bilibili", "b2"), video("bilibili", "b3")], [video("youtube", "y1")], [video("skypixel", "s1"), video("skypixel", "b2")]])
    .map((item) => item.title), ["b1", "y1", "s1", "b2", "b3"]);

  // Quality: the short side of the size the source reports, then its labels, then (unsure) the title.
  assert.deepEqual(qualityLabel({ title: "x", width: 3840, height: 2160 }), { label: "4K", sure: true });
  assert.deepEqual(qualityLabel({ title: "x", width: 2160, height: 3840 }), { label: "4K", sure: true });
  assert.deepEqual(qualityLabel({ title: "x", width: 3440, height: 1440 }), { label: "2K", sure: true });
  assert.deepEqual(qualityLabel({ title: "x", width: 1920, height: 1080 }), { label: "1080p", sure: true });
  assert.deepEqual(qualityLabel({ title: "x", badges: ["4K", "CC"] }), { label: "4K", sure: true });
  assert.deepEqual(qualityLabel({ title: "【4K】港珠澳大桥" }), { label: "4K", sure: false });
  assert.deepEqual(qualityLabel({ title: "超清航拍" }), { label: "4K", sure: false });
  assert.equal(qualityLabel({ title: "4Kids show 1080" }), null);

  // YouTube and SkyPixel results keep their source, size, labels and next-page token.
  const youtube = parseVideos({ ok: true, next_page: null, next_cursor: "EqoDEhVCYWloZXRh", items: [
    { platform: "youtube", title: "Dam", page_url: "https://www.youtube.com/watch?v=93kgTcANF1E", badges: ["4K", "CC", "<b>"] },
  ] });
  assert.equal(youtube.nextCursor, "EqoDEhVCYWloZXRh");
  assert.equal(youtube.items[0].source, "youtube");
  assert.deepEqual(youtube.items[0].badges, ["4K", "CC"]);
  assert.equal(parseVideos({ ok: true, next_page: null, next_cursor: "bad token!", items: [] }).nextCursor, null);
  const sky = parseVideos({ ok: true, next_page: 2, items: [{ platform: "skypixel", title: "桥", width: 3840, height: 2160, page_url: "https://www.skypixel.com/videos/a-b" }] });
  assert.deepEqual([sky.items[0].source, sky.items[0].width, sky.items[0].height], ["skypixel", 3840, 2160]);
  assert.equal(parseVideos({ ok: true, items: [{ platform: "evil", page_url: "https://x.test/1" }] }).items[0].source, undefined);

  // The SkyPixel rendition must sit on DJI's video CDN.
  assert.deepEqual(parseMedia({ ok: true, url: "https://cn-videos.dji.net/video_trans/a/1080.mp4", quality: "1080p", license: "SkyPixel Use" }),
    { url: "https://cn-videos.dji.net/video_trans/a/1080.mp4", quality: "1080p", license: "SkyPixel Use" });
  assert.equal(parseMedia({ ok: true, url: "https://evil.example/1080.mp4" }), null);
  assert.equal(parseMedia({ ok: false, code: "UNSUPPORTED" }), null);

  // Players: YouTube's no-cookie embed (muted for hover), SkyPixel's MP4 once looked up.
  assert.deepEqual(previewSource({ pageUrl: "https://www.youtube.com/watch?v=93kgTcANF1E", fetchUrl: "https://www.youtube.com/watch?v=93kgTcANF1E" }, { muted: true }),
    { kind: "iframe", vertical: false, src: "https://www.youtube-nocookie.com/embed/93kgTcANF1E?autoplay=1&mute=1&playsinline=1&rel=0" });
  assert.equal(previewSource({ pageUrl: "https://www.youtube.com/watch?v=bad", fetchUrl: "https://www.youtube.com/watch?v=bad" }, { muted: false }), null);
  assert.equal(previewSource({ pageUrl: "https://www.skypixel.com/videos/a", fetchUrl: "https://www.skypixel.com/videos/a" }, { muted: true }), null);
  assert.deepEqual(previewSource({ pageUrl: "https://www.skypixel.com/videos/a", fetchUrl: "https://cn-videos.dji.net/v/1080.mp4" }, { muted: false }),
    { kind: "video", vertical: false, src: "https://cn-videos.dji.net/v/1080.mp4" });
  // Sources without a page say so instead of offering "Mở trang".
  assert.doesNotMatch(discoveryError("BLOCKED", "youtube"), /Mở trang/);
}
console.log("discovery multi-source tests passed");

// Xinpianchang (#982): a page source offered under "Tìm thêm trên trang"; its MP4 is read when added.
{
  const { PAGE_ONLY_SOURCES, PLATFORMS, parseXinpianchangMedia } = await import("../src/features/discovery/model.ts");
  const xpc = PLATFORMS.find((entry) => entry.id === "xinpianchang");
  assert.deepEqual([xpc?.page, xpc?.apiSearch, xpc?.trends], [true, false, false]);
  assert.ok(PAGE_ONLY_SOURCES.includes("xinpianchang"));
  assert.match(discoveryError("BLOCKED", "xinpianchang"), /Mở trang/);
  assert.deepEqual(parseXinpianchangMedia({ url: "https://us-xpc5.xpccdn.com/a.mp4?e=1&s=2", width: 1280, height: 720, hd_locked: true }),
    { url: "https://us-xpc5.xpccdn.com/a.mp4?e=1&s=2", quality: "720p", hdLocked: true });
  assert.equal(parseXinpianchangMedia({ url: "https://us-xpc5.xpccdn.com/b.mp4", width: 3840, height: 2160 }).quality, "4K");
  assert.equal(parseXinpianchangMedia({ url: "https://evil.example/a.mp4" }), null);
  assert.equal(parseXinpianchangMedia({ url: "http://us-xpc5.xpccdn.com/a.mp4" }), null);
  assert.equal(parseXinpianchangMedia(null), null);
  const page = parseCollected([{ page_url: "https://www.xinpianchang.com/a13885930", fetch_url: "https://www.xinpianchang.com/a13885930", title: "工业" }]);
  assert.equal(page[0].fetchUrl, page[0].pageUrl);
  // Its page has no embed player: no preview, no hover.
  assert.equal(previewSource({ pageUrl: "https://www.xinpianchang.com/a13885930", fetchUrl: "https://www.xinpianchang.com/a13885930" }, { muted: false }), null);
}
console.log("discovery xinpianchang tests passed");

// #990: YouTube narrows with every word; its phrase drops quality words its filter covers, and repeats.
{
  const { SUFFIXES, youtubeQuery } = await import("../src/features/discovery/model.ts");
  const [broll, drone, noText, , uhd] = SUFFIXES;
  assert.equal(youtubeQuery("Baihetan Hydropower Station", [broll, drone], "4k"), "Baihetan Hydropower Station B-roll drone");
  assert.equal(youtubeQuery("Baihetan Hydropower Station", [broll, drone], "hd"), "Baihetan Hydropower Station B-roll drone");
  assert.equal(youtubeQuery("Baihetan Hydropower Station", [broll, drone], "any"), "Baihetan Hydropower Station B-roll 4K drone");
  assert.equal(youtubeQuery("Qingdao port 4K", [uhd, drone], "cc"), "Qingdao port 4K drone");
  assert.equal(youtubeQuery("Qingdao port", [uhd], "4k"), "Qingdao port");
  assert.equal(youtubeQuery("dam drone", [drone, noText], "4k"), "dam drone no text");
  assert.equal(youtubeQuery("", [broll], "4k"), "B-roll");
}
console.log("discovery youtube query tests passed");
