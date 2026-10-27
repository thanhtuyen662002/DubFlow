import assert from "node:assert/strict";
import { MAX_LINKS, extractLinks } from "../src/features/links/extract.ts";
import { clock, fetchBadge, fetchMessage, shortPath } from "../src/features/links/status.ts";

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

// Links pasted back to back, or with " , " between them, are still separate.
const bili = ["https://www.bilibili.com/video/BV1B5xkzPEhx/", "https://www.bilibili.com/video/BV1FwY4zkEef/", "https://www.bilibili.com/video/BV1Lha9zCEJ4/"];
assert.deepEqual(extractLinks(bili.join("")).links, bili);
assert.deepEqual(extractLinks(bili.join(" , ")).links, bili);
assert.deepEqual(extractLinks(bili.join(",")).links, bili);

// Link rows: waiting, analysing with elapsed time, downloading, failed.
const base = { scan_id: "link-1", url: bili[0], provider: "bilibili", detail: null, queue_position: null, started_at_ms: null,
  downloaded_bytes: 0, total_bytes: null, media_path: null, error: null };
assert.equal(fetchMessage({ ...base, state: "queued", queue_position: 1 }, 0), "Đang chờ link trước tải xong");
assert.equal(fetchMessage({ ...base, state: "queued", queue_position: 3 }, 0), "Đang chờ: còn 2 link phía trước");
assert.equal(fetchMessage({ ...base, state: "preparing", detail: "Đang đọc thông tin video…", started_at_ms: 1_000 }, 84_000),
  "Đang đọc thông tin video… · 1:23");
assert.equal(fetchMessage({ ...base, state: "downloading", started_at_ms: 5_000, downloaded_bytes: 52_428_800, total_bytes: 209_715_200 }, 3_730_000),
  "Đang tải 50/200 MB · 1:02:05");
assert.equal(fetchMessage({ ...base, state: "failed", error: "Không tìm thấy video (có thể đã bị xóa)." }, 0), "Không tìm thấy video (có thể đã bị xóa).");
assert.equal(fetchBadge("queued"), "Đang chờ");
assert.equal(clock(59_999), "0:59");
assert.equal(shortPath("C:\\Users\\me\\Videos\\DubFlow Output\\BV1B5xkzPEhx - vi\\link-1"), "…\\DubFlow Output\\BV1B5xkzPEhx - vi\\link-1");
console.log("desktop link extraction tests passed");
