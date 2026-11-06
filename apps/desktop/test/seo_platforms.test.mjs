import assert from "node:assert/strict";
import { fitTags, normalizePlatformPost, normalizePlatformPosts, PLATFORM_RULES, platformPostsFromBase, postText, toHashtag, writePlatformPosts } from "../src/features/seo/platforms.ts";
import { offlineKeywords } from "../src/features/seo/posts.ts";

// Hashtags: accent-free, lowercase, no spaces; junk dropped.
assert.equal(toHashtag("#Phim Tiên Hiệp"), "#phimtienhiep");
assert.equal(toHashtag("Đồ ăn"), "#doan");
assert.equal(toHashtag("!!"), "");

// YouTube tags fit the 500-character box (commas included) and are de-duplicated.
const many = Array.from({ length: 80 }, (_, index) => `từ khóa số ${index}`);
const fitted = fitTags([...many, "Từ khóa số 1"]);
assert.ok(fitted.join(",").length <= 500);
assert.equal(new Set(fitted).size, fitted.length);
assert.deepEqual(fitTags(["#vietsub", " a ", "Lồng tiếng"]), ["vietsub", "lồng tiếng"]);

// AI output is clipped to each platform's limits.
const youtube = normalizePlatformPost("youtube", {
  titles: ["x".repeat(140), "Tiêu đề #hot", "Tiêu đề #hot", "", 3],
  description: "  Dòng 1\r\n\r\n\r\nDòng 2  ",
  hashtags: ["#Vietsub", "vietsub", "Phim hay", "a", "#b1", "#c1", "#d1", "#e1"],
  tags: ["phim hay", "#vietsub"],
});
assert.equal([...youtube.titles[0]].length <= PLATFORM_RULES.youtube.titleMax, true);
assert.equal(youtube.titles[1], "Tiêu đề", "hashtags never stay in a title");
assert.equal(youtube.titles.length, 2);
assert.equal(youtube.description, "Dòng 1\n\nDòng 2");
assert.deepEqual(youtube.hashtags, ["#vietsub", "#phimhay", "#b1", "#c1", "#d1"]);
assert.deepEqual(youtube.tags, ["phim hay", "vietsub"]);
assert.deepEqual(normalizePlatformPost("tiktok", { titles: ["Hook"], description: "Câu", hashtags: [], tags: ["x y"] }).tags, [], "only YouTube has tags");
assert.throws(() => normalizePlatformPost("facebook", { titles: [], description: "x" }), /Facebook/);
assert.throws(() => normalizePlatformPosts({ youtube: youtube, facebook: youtube }), /TikTok/);

// Offline drafts per platform from the subtitle-based suggestion.
const base = { titles: ["Cô gái nấu món lạ khiến cả làng bất ngờ", "Bí quyết nấu ăn"], description: "Cô gái nấu một món ăn lạ. Cả làng kéo đến xem.\n#vietsub", hashtags: ["#nauan", "#amthuc", "#vietsub"] };
const offline = platformPostsFromBase(base, ["nấu ăn", "món lạ"]);
assert.ok(offline.youtube.description.startsWith("Cô gái nấu một món ăn lạ."));
assert.ok(offline.youtube.description.includes("Đăng ký"));
assert.ok(!offline.youtube.description.includes("#"), "hashtags live in their own box");
assert.ok(offline.youtube.hashtags.includes("#vietsub") && offline.youtube.hashtags.length <= 5);
assert.deepEqual(offline.youtube.tags.slice(0, 2), ["nấu ăn", "món lạ"]);
assert.ok(offline.facebook.description.includes("?"), "Facebook ends with a question for comments");
assert.equal(offline.tiktok.description, "Cô gái nấu một món ăn lạ.");
assert.ok(offline.tiktok.hashtags.includes("#xuhuong") && offline.tiktok.hashtags.length >= 4);
for (const post of Object.values(offline)) assert.ok(!/(^|\s)#vietsub\n/.test(post.description));

// "Sao chép bài đăng" never repeats the hashtag line.
const text = postText("youtube", offline.youtube, offline.youtube.titles[0]);
assert.equal(text.split(offline.youtube.hashtags.join(" ")).length, 2);
assert.equal(postText("tiktok", { titles: ["Hook"], description: "Câu", hashtags: ["#a1"], tags: [] }, "Hook"), "Hook\nCâu\n\n#a1");
assert.equal(normalizePlatformPost("youtube", { titles: ["T"], description: "Nội dung\n\n#a1 #b2\nCuối", hashtags: [] }).description, "Nội dung\n\nCuối");

// Offline keywords come through when the engine sends them.
assert.deepEqual(offlineKeywords({ ok: true, keywords: ["a", 1, "b"] }), ["a", "b"]);
assert.deepEqual(offlineKeywords(null), []);

// One Gemini request returns all three platforms, in Vietnamese.
const answer = { youtube: { titles: ["Món lạ khiến cả làng sốc"], description: "Mô tả có dấu tiếng Việt đầy đủ cho video nấu ăn ở làng quê.", hashtags: ["#nauan"], tags: ["nấu ăn"] },
  facebook: { titles: ["Ai đã từng ăn món này chưa?"], description: "Cả làng bất ngờ.", hashtags: ["#amthuc"], tags: [] },
  tiktok: { titles: ["Món này lạ quá"], description: "Xem đến cuối nhé", hashtags: ["#xuhuong"], tags: [] } };
let sent = null;
const posts = await writePlatformPosts("gemini", "test-key", ["Câu một", "Câu hai"], "Tiêu đề", async (url, init) => {
  sent = JSON.parse(init.body);
  return new Response(JSON.stringify({ candidates: [{ content: { parts: [{ text: JSON.stringify(answer) }] }, finishReason: "STOP" }] }), { status: 200, headers: { "content-type": "application/json" } });
});
assert.equal(posts.tiktok.titles[0], "Món này lạ quá");
assert.deepEqual(posts.youtube.tags, ["nấu ăn"]);
assert.ok(JSON.stringify(sent).includes("Câu hai"));
console.log("seo platforms ok");
