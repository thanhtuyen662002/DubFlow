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

// #616: opening the stored list never overwrites what it cannot read or back up.
{
  const { openMedia, MEDIA_STORAGE_KEY, MEDIA_CORRUPT_KEY } = await import("../src/features/media/model.ts");
  const fake = (initial, faults = {}) => {
    const data = new Map(Object.entries(initial));
    const writes = [];
    return {
      data, writes,
      getItem(key) { if (faults.get?.(key)) throw new Error("SecurityError"); return data.has(key) ? data.get(key) : null; },
      setItem(key, value) { writes.push(key); if (faults.set?.(key)) throw new Error("QuotaExceededError"); data.set(key, value); },
    };
  };
  const seed = () => [{ id: "media-j1", path: "D:\\dl\\a.mp4", title: "a", addedAt: "t" }];
  const curated = serializeMedia([{ id: "m-own", path: "C:\\mine.mp4", title: "của tôi", addedAt: "t" }]);

  // 1. A read that throws is "unavailable", not "missing": nothing is written, the list is session-only.
  let store = fake({ [MEDIA_STORAGE_KEY]: curated }, { get: () => true });
  let opened = openMedia(store, seed);
  assert.equal(opened.state, "unavailable");
  assert.equal(opened.sessionOnly, true);
  assert.equal(opened.saved, false);
  assert.deepEqual(opened.entries.map((entry) => entry.id), ["media-j1"]);
  assert.deepEqual(store.writes, []);
  assert.equal(store.data.get(MEDIA_STORAGE_KEY), curated);
  assert.match(opened.notice, /Không đọc được danh sách Hàng đợi/);

  // 2. Corrupt list whose backup write fails, while the primary seed write would succeed: primary bytes stay unchanged.
  const broken = '{"items": [ {"id": "m-own", "path": "C:\\\\mine.mp4"'; // truncated JSON
  store = fake({ [MEDIA_STORAGE_KEY]: broken }, { set: (key) => key === MEDIA_CORRUPT_KEY });
  opened = openMedia(store, seed);
  assert.equal(opened.state, "corrupt");
  assert.equal(opened.backup, "failed");
  assert.equal(opened.sessionOnly, true);
  assert.equal(store.data.get(MEDIA_STORAGE_KEY), broken);
  assert.equal(store.writes.includes(MEDIA_STORAGE_KEY), false);
  assert.match(opened.notice, /chưa sao lưu được bản cũ/);

  // A backup that does not read back the same bytes is not confirmed either.
  store = fake({ [MEDIA_STORAGE_KEY]: broken });
  const setItem = store.setItem.bind(store);
  store.setItem = (key, value) => setItem(key, key === MEDIA_CORRUPT_KEY ? value.slice(0, 5) : value);
  opened = openMedia(store, seed);
  assert.equal(opened.backup, "failed");
  assert.equal(store.data.get(MEDIA_STORAGE_KEY), broken);

  // Corrupt list with a confirmed backup: the old bytes are kept aside, the rebuilt list is saved, the usual message is shown.
  store = fake({ [MEDIA_STORAGE_KEY]: broken });
  opened = openMedia(store, seed);
  assert.equal(opened.backup, "saved");
  assert.equal(store.data.get(MEDIA_CORRUPT_KEY), broken);
  assert.equal(opened.saved, true);
  assert.equal(opened.sessionOnly, false);
  assert.deepEqual(parseMedia(store.data.get(MEDIA_STORAGE_KEY)).map((entry) => entry.id), ["media-j1"]);
  assert.equal(opened.notice, "Danh sách Hàng đợi đã lưu bị hỏng nên được dựng lại từ các video ở Lồng tiếng. Bản cũ được giữ riêng; file video trên máy không bị ảnh hưởng.");

  // 3. First run whose seed write fails: the user is told the list is memory-only, never that it was saved.
  store = fake({}, { set: (key) => key === MEDIA_STORAGE_KEY });
  opened = openMedia(store, seed);
  assert.equal(opened.state, "missing");
  assert.equal(opened.saved, false);
  assert.equal(opened.sessionOnly, false);
  assert.equal(store.data.has(MEDIA_STORAGE_KEY), false);
  assert.match(opened.notice, /chỉ nằm trong bộ nhớ/);

  // Ordinary paths are unchanged: a first run saves the seed quietly, a stored list is read as is (bad rows counted).
  store = fake({});
  opened = openMedia(store, seed);
  assert.deepEqual([opened.state, opened.saved, opened.notice], ["missing", true, null]);
  assert.deepEqual(parseMedia(store.data.get(MEDIA_STORAGE_KEY)).map((entry) => entry.id), ["media-j1"]);
  const withBad = '{"items":[{"id":"a","path":"C:/a.mp4","title":"a","addedAt":"t"},{"id":"b"}]}';
  store = fake({ [MEDIA_STORAGE_KEY]: withBad });
  opened = openMedia(store, seed);
  assert.deepEqual([opened.state, opened.saved, opened.sessionOnly, opened.dropped], ["ok", true, false, 1]);
  assert.deepEqual(store.writes, []);
  assert.equal(opened.notice, "1 mục hỏng trong danh sách Hàng đợi đã được bỏ qua.");
  store = fake({ [MEDIA_STORAGE_KEY]: curated });
  assert.deepEqual([openMedia(store, seed).notice, store.writes], [null, []]);
  console.log("media storage faults ok");
}

const { clock } = await import("../src/features/media/player_bar.ts");
assert.equal(clock(0), "0:00");
assert.equal(clock(75.9), "1:15");
assert.equal(clock(3725), "1:02:05");
assert.equal(clock(Number.NaN), "0:00");
assert.equal(clock(-3), "0:00");
