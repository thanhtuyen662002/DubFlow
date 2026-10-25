import assert from "node:assert/strict";
import { MAX_LINKS, extractLinks } from "../src/features/links/extract.ts";

// One per line, Windows line ends, duplicates dropped, URL commas preserved.
const lines = extractLinks("https://www.bilibili.com/video/BV1gKJ86JEuP\r\nhttps://archive.org/a,b.mp4\nhttps://www.bilibili.com/video/BV1gKJ86JEuP\n  http://x.example/scene//1  ");
assert.deepEqual(lines.links, ["https://www.bilibili.com/video/BV1gKJ86JEuP", "https://archive.org/a,b.mp4", "http://x.example/scene//1"]);

// Douyin share message: the link sits between Chinese text and full-width punctuation.
const share = extractLinks("7.15 复制打开抖音，看看【某某的作品】仙侠名场面 # 仙侠 https://v.douyin.com/iRNBho6u/ zGv:/ 07/11 Q@k.cA");
assert.deepEqual(share.links, ["https://v.douyin.com/iRNBho6u/"]);
assert.deepEqual(extractLinks("xem: https://example.com/a.mp4. Cảm ơn!").links, ["https://example.com/a.mp4"]);

// Paths that differ only by slashes stay distinct; nothing is normalised.
assert.equal(extractLinks("https://a.example/scene/1 https://a.example/scene/1/").links.length, 2);

// The byte limit counts UTF-8; oversized links are reported, valid ones kept.
const long = "https://a.example/" + "ả".repeat(1100);
const mixed = extractLinks(`${long}\nhttps://ok.example/v.mp4`);
assert.deepEqual(mixed.links, ["https://ok.example/v.mp4"]);
assert.equal(mixed.tooLong.length, 1);

// At most MAX_LINKS per paste; the rest are counted.
const many = extractLinks(Array.from({ length: 55 }, (_, i) => `https://a.example/${i}`).join("\n"));
assert.equal(many.links.length, MAX_LINKS);
assert.equal(many.dropped, 5);
assert.deepEqual(extractLinks("không có link ftp://x").links, []);
console.log("desktop link extraction tests passed");
