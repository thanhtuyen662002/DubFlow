import assert from "node:assert/strict";
import {
  addMedia, countMedia, dubJobFor, readMedia, filterMedia, finishedDubFor, mediaUse, originLabel, parseMedia, removeMedia, seedFromJobs, serializeMedia,
} from "../src/features/media/model.ts";

let next = 0;
const id = () => `m${++next}`;

// Nothing stored yet, or something unreadable: never an empty list (both are rebuilt from Lồng tiếng).
assert.equal(parseMedia(null), null);
assert.equal(parseMedia("not json"), null);
assert.deepEqual(readMedia(null), { state: "missing" });
assert.deepEqual(readMedia("not json"), { state: "corrupt" });
assert.deepEqual(readMedia('{"items":"x"}'), { state: "corrupt" });
assert.deepEqual(readMedia('{"schema_version":1,"items":[]}'), { state: "ok", entries: [], dropped: 0 });
// Bad rows are counted, so the user can be told.
assert.deepEqual(readMedia('{"items":[{"id":"a","path":"C:/a.mp4","title":"a","addedAt":"t"},{"id":"b"}]}').dropped, 1);
assert.deepEqual(parseMedia('{"items":[{"id":"a","path":" ","title":"x","addedAt":"t"},{"id":"b","path":"C:\\\\v.mp4","title":"v","addedAt":"t","originUrl":"javascript:x"}]}'), []);

// Added videos go on top; the same file (any case or slash) moves up and keeps its id and link.
let { entries, ids } = addMedia([], [{ path: "C:\\Videos\\Clip.mp4" }], "2026-11-07T01:00:00Z", id);
assert.deepEqual(entries.map((entry) => [entry.id, entry.title]), [["m1", "Clip"]]);
({ entries, ids } = addMedia(entries, [{ path: "D:\\dl\\a.mp4", title: "Lợn nhà cao tầng", originUrl: "https://www.douyin.com/video/1", outputDir: "D:\\Out\\a" }], "2026-11-07T02:00:00Z", id));
assert.deepEqual(ids, ["m2"]);
({ entries, ids } = addMedia(entries, [{ path: "c:/videos/clip.MP4" }], "2026-11-07T03:00:00Z", id));
assert.deepEqual(ids, ["m1"]);
assert.deepEqual(entries.map((entry) => entry.id), ["m1", "m2"]);
assert.equal(entries[1].originUrl, "https://www.douyin.com/video/1");
assert.equal(entries[1].outputDir, "D:\\Out\\a");

// Stored and read back the same; a duplicate path in storage is kept once.
const stored = serializeMedia([...entries, { ...entries[0], id: "dup" }]);
assert.deepEqual(parseMedia(stored), entries);

// Filters, counts, search without Vietnamese marks, labels.
assert.deepEqual(countMedia(entries), { all: 2, link: 1, file: 1 });
assert.deepEqual(filterMedia(entries, "link", "").map((entry) => entry.id), ["m2"]);
assert.deepEqual(filterMedia(entries, "file", "").map((entry) => entry.id), ["m1"]);
assert.deepEqual(filterMedia(entries, "all", "lon nha").map((entry) => entry.id), ["m2"]);
assert.equal(originLabel(entries[0]), "Từ máy");
assert.equal(originLabel(entries[1]), "Douyin");
assert.deepEqual(removeMedia(entries, ["m1"]).map((entry) => entry.id), ["m2"]);

// What each video has been used for, from the dub jobs (re-runs share the source).
const job = (jobId, sourcePath, state, createdAt, extra = {}) => ({
  id: jobId, sourcePath, displayName: "a.mp4", createdAt, outputPath: state === "COMPLETED" ? `D:\\Out\\${jobId}\\final_vi.mp4` : null, status: { state }, ...extra,
});
const jobs = [
  job("j1", "D:\\dl\\a.mp4", "COMPLETED", "2026-11-07T04:00:00Z"),
  job("j2", "D:\\dl\\a.mp4", "QUEUED", "2026-11-07T05:00:00Z", { revisionOf: "j1" }),
  job("j3", "C:\\Videos\\Clip.mp4", "FAILED", "2026-11-07T04:00:00Z"),
];
assert.equal(finishedDubFor(entries[1], jobs)?.id, "j1");
assert.equal(dubJobFor(entries[1], jobs)?.id, "j2");
assert.equal(mediaUse(entries[1], jobs), "dubbed");
assert.equal(mediaUse(entries[0], jobs), "failed");
assert.equal(mediaUse(entries[0], []), "none");
assert.equal(mediaUse(entries[0], [job("j4", "c:/videos/clip.mp4", "RUNNING", "t")]), "dubbing");

// First run after the update: Lồng tiếng's videos become Hàng đợi, newest first, once each, re-runs skipped.
const seeded = seedFromJobs([
  job("old", "C:\\v\\old.mp4", "COMPLETED", "2026-11-01T00:00:00Z"),
  job("link", "D:\\dl\\a.mp4", "QUEUED", "2026-11-02T00:00:00Z", { displayName: "Video hay", originUrl: "https://b23.tv/x", outputDir: "D:\\Out\\x" }),
  job("rerun", "D:\\dl\\a.mp4", "QUEUED", "2026-11-03T00:00:00Z", { revisionOf: "link" }),
]);
assert.deepEqual(seeded.map((entry) => [entry.id, entry.title, entry.originUrl ?? null]), [["media-link", "Video hay", "https://b23.tv/x"], ["media-old", "a", null]]);
assert.equal(originLabel(seeded[0]), "Bilibili");

// A full list refuses new videos (and says how many) instead of dropping old ones; a known path still moves up.
const full = addMedia([], [{ path: "C:\\1.mp4" }, { path: "C:\\2.mp4" }], "t", id, 2).entries;
const over = addMedia(full, [{ path: "C:\\3.mp4" }, { path: "c:/1.MP4" }], "t2", id, 2);
assert.equal(over.refused, 1);
assert.deepEqual(over.entries.map((entry) => entry.path), ["c:/1.MP4", "C:\\2.mp4"]);

console.log("media ok");
