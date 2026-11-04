import assert from "node:assert/strict";
import {
  ageGroup, countByKind, fileTitle, filterEntries, fold, folderOf, forgetFile, isVideoPath, MAX_RECENT, mergeEntries, parseLibrary, parseRecent,
  pathKey, rememberFile, sizeLabel, distinctTitles,
} from "../src/features/edit/library.ts";

// The host's list keeps only the other pages' videos, well formed.
const library = parseLibrary([
  { path: "D:\\Out\\Ban hang - Noi\\run-1\\video-1.mp4", kind: "product", title: "Nồi · video 1", modified: 300, size_bytes: 9_800_000 },
  { path: "D:\\Out\\Tong hop - Top\\run-1\\compilation.mp4", kind: "compilation", title: " ", modified: 200 },
  { path: "D:\\Out\\x\\final_vi.mp4", kind: "job", title: "queue videos come from the page", modified: 1 },
  { path: "", kind: "docu" }, null, "x",
]);
assert.deepEqual(library.map((entry) => [entry.kind, entry.title, entry.sizeBytes]), [["product", "Nồi · video 1", 9_800_000], ["compilation", "compilation", undefined]]);
assert.deepEqual(parseLibrary({}), []);

// Remembered files: newest first, once each whatever the case, at most MAX_RECENT; bad storage is empty.
assert.deepEqual(parseRecent("not json"), []);
assert.deepEqual(parseRecent('[{"path":"C:\\\\a.txt"},{"path":"C:\\\\b.MOV","modified":5}]').map((entry) => [entry.title, entry.kind, entry.modified]), [["b", "file", 5]]);
let recent = rememberFile([], "C:\\Videos\\Clip.mp4", 10);
recent = rememberFile(recent, "D:\\Other.mkv", 20);
recent = rememberFile(recent, "c:/videos/clip.MP4", 30);
assert.deepEqual(recent.map((entry) => [entry.path, entry.modified]), [["c:/videos/clip.MP4", 30], ["D:\\Other.mkv", 20]]);
assert.deepEqual(forgetFile(recent, "d:\\other.MKV").map((entry) => entry.path), ["c:/videos/clip.MP4"]);
let many = [];
for (let index = 0; index < MAX_RECENT + 5; index += 1) many = rememberFile(many, `C:\\v${index}.mp4`, index);
assert.equal(many.length, MAX_RECENT);
assert.equal(many[0].path, `C:\\v${MAX_RECENT + 4}.mp4`);

// One list, newest first; a remembered file that is a DubFlow video shows as that video.
const job = { path: "D:\\Out\\Phim - vi\\run-1\\final_vi.mp4", kind: "job", title: "Phim", modified: 250, jobId: "j1" };
const merged = mergeEntries([job], library, [{ path: "d:/out/ban hang - noi/run-1/VIDEO-1.mp4", kind: "file", title: "video-1", modified: 400 }, ...recent]);
assert.deepEqual(merged.map((entry) => [entry.kind, entry.modified]), [["product", 400], ["file", 30], ["job", 250], ["compilation", 200], ["file", 20]].sort((a, b) => b[1] - a[1]));
assert.deepEqual(countByKind(merged), { all: 5, job: 1, product: 1, compilation: 1, docu: 0, file: 2 });

// Filter by source; search ignores case and Vietnamese marks, and matches folders.
assert.deepEqual(filterEntries(merged, "file", "").map((entry) => entry.kind), ["file", "file"]);
assert.deepEqual(filterEntries(merged, "all", "noi video").map((entry) => entry.kind), ["product"]);
assert.deepEqual(filterEntries(merged, "all", "NỒI").map((entry) => entry.kind), ["product"]);
assert.deepEqual(filterEntries(merged, "all", "tong hop").map((entry) => entry.kind), ["compilation"]);
assert.deepEqual(filterEntries(merged, "job", "noi"), []);
assert.equal(fold("Đập Tam Hiệp"), "dap tam hiep");

// Headings by age, sizes, names.
const now = new Date(2026, 10, 4, 15, 0, 0);
const at = (days, hours = 12) => new Date(2026, 10, 4 - days, hours).getTime() / 1000;
assert.deepEqual([at(0, 1), at(1), at(5), at(9), 0].map((seconds) => ageGroup(seconds, now)), ["Hôm nay", "Hôm qua", "7 ngày qua", "Trước đó", "Trước đó"]);
assert.deepEqual([sizeLabel(9_800_000), sizeLabel(2_500_000_000), sizeLabel(undefined), sizeLabel(10)], ["9 MB", "2,3 GB", "", "1 MB"]);
assert.equal(fileTitle("D:\\Videos\\Phim hay.final.mp4"), "Phim hay.final");
assert.equal(folderOf("D:\\Videos\\a.mp4"), "D:\\Videos");
assert.equal(pathKey("C:/A/b.MP4"), "c:\\a\\b.mp4");
assert.ok(isVideoPath("x.MKV") && !isVideoPath("x.srt"));

// Same titles get their date; unique ones stay as they are.
const twins = distinctTitles([{ title: "Nồi · video 1", modified: new Date(2026, 10, 2, 0, 14).getTime() / 1000 },
  { title: "Nồi · video 1", modified: new Date(2026, 10, 1, 23, 11).getTime() / 1000 }, { title: "Khác", modified: 5 }]);
assert.deepEqual(twins.map((entry) => entry.title), ["Nồi · video 1 · 02/11 00:14", "Nồi · video 1 · 01/11 23:11", "Khác"]);

console.log("edit library: ok");
