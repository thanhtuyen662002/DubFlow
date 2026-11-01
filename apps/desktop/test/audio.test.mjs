import assert from "node:assert/strict";
import {
  addAudio, addedId, audioGain, audioLanes, audioLength, createEdit, EditHistory, exportPlan, insertAt, isUnchanged, mainLevel, matchesExport,
  MAX_AUDIO, outputDuration, parseProject, remove, removeAudio, serializeProject, setAudioProps, setAudioTime, setMainVolume, split,
} from "../src/features/edit/model.ts";
import { CATEGORIES, fold, parseCatalog, searchSounds, soundLength } from "../src/features/sfx/catalog.ts";
import { CATALOG } from "../src/features/sfx/bundled.ts";
import { buildSuggestionRequest, MAX_SUGGESTIONS, suggestEffects, validateSuggestions } from "../src/features/sfx/ai.ts";
import { isAudioPath, packSummary, sizeText } from "../src/features/sfx/library.ts";

const close = (actual, expected, message, tolerance = 1e-6) => assert.ok(Math.abs(actual - expected) < tolerance, `${message ?? ""} ${actual} != ${expected}`);

// ---------- The bundled catalog ----------
assert.equal(CATALOG.pack, "dubflow-sfx-v1");
assert.ok(CATALOG.sounds.length >= 120 && CATALOG.sounds.length <= 200, `${CATALOG.sounds.length} sounds`);
assert.equal(new Set(CATALOG.sounds.map((sound) => sound.id)).size, CATALOG.sounds.length, "unique ids");
for (const category of CATEGORIES) assert.ok(CATALOG.sounds.some((sound) => sound.category === category), category);
assert.ok(CATALOG.sounds.every((sound) => sound.license === "CC0-1.0" && sound.source.startsWith("Kenney ") && sound.tags.length > 0));
assert.ok(CATALOG.sounds.every((sound) => /[à-ỹ]/i.test(`${sound.name_vi} ${sound.tags.join(" ")}`) || /“/.test(sound.name_vi)), "Vietnamese names and tags");
// Bad entries are dropped, the rest kept.
const parsed = parseCatalog({ schema_version: 1, pack: "p", sounds: [
  { id: "ok-1", file: "ok-1.ogg", name_vi: "Vút", tags: ["vút", 3], category: "Chuyển cảnh", duration_ms: 500, source: "Kenney", license: "CC0-1.0" },
  { id: "ok-1", file: "ok-1.ogg", name_vi: "Trùng", tags: [], category: "Khác", duration_ms: 500 },
  { id: "../x", file: "../x.ogg", name_vi: "x", tags: [], category: "Khác", duration_ms: 500 },
  { id: "bad-cat", file: "bad-cat.ogg", name_vi: "x", tags: [], category: "Nhạc", duration_ms: 500 },
  { id: "bad-file", file: "other.ogg", name_vi: "x", tags: [], category: "Khác", duration_ms: 500 },
] });
assert.deepEqual(parsed.sounds.map((sound) => sound.id), ["ok-1"]);
assert.deepEqual(parsed.sounds[0].tags, ["vút"]);
assert.equal(parseCatalog({ schema_version: 2, sounds: [] }).sounds.length, 0);
// Search ignores Vietnamese marks and case, and filters by category.
assert.equal(fold("Vút Đấm"), "vut dam");
const whooshes = searchSounds(CATALOG.sounds, "vut");
assert.ok(whooshes.length > 0 && whooshes.every((sound) => fold([sound.name_vi, ...sound.tags, sound.category, sound.id].join(" ")).includes("vut")));
assert.ok(searchSounds(CATALOG.sounds, "đấm").some((sound) => sound.id === "punch-01"));
assert.ok(searchSounds(CATALOG.sounds, "", "Va chạm").every((sound) => sound.category === "Va chạm"));
assert.equal(searchSounds(CATALOG.sounds, "không có gì như thế này").length, 0);
assert.equal(soundLength(601), "0,6 giây");
assert.equal(soundLength(28), "0,1 giây");
assert.equal(sizeText(2_194_688), "2,2 MB");
assert.equal(packSummary({ installed: false, version: "", count: 0, size_bytes: 2_194_688, state: "idle", received: 0, error: null }), "Chưa tải · 2,2 MB");
assert.equal(packSummary({ installed: false, version: "", count: 0, size_bytes: 1000, state: "downloading", received: 500, error: null }), "Đang tải 50%");
assert.equal(packSummary({ installed: true, version: "v1", count: 199, size_bytes: 1000, state: "idle", received: 1000, error: null }), "Đã tải 199 âm thanh");
assert.ok(isAudioPath("D:\\Nhạc\\cười.MP3") && isAudioPath("C:/a/b.flac") && !isAudioPath("D:\\v\\clip.mp4") && !isAudioPath("cười.mp3"));

// ---------- Sound clips in the edit ----------
const whoosh = { source: { kind: "sfx", id: "whoosh-01" }, title: "Vút nhanh", duration: 0.601 };
const song = { source: { kind: "file", path: "D:\\Nhạc\\nhạc nền.mp3" }, title: "nhạc nền", duration: 30 };
const film = createEdit(100);
assert.equal(film.mainVolume, 1);
assert.deepEqual(film.audio, []);
const one = addAudio(film, whoosh, 12.3456);
assert.equal(one.audio.length, 1);
assert.deepEqual(one.audio[0], { id: "a2", source: { kind: "sfx", id: "whoosh-01" }, title: "Vút nhanh", duration: 0.601, at: 12.346, start: 0,
  end: 0.601, volume: 1, fadeIn: 0, fadeOut: 0, duckDb: 0 });
assert.equal(addedId(film, one), "a2");
assert.ok(!isUnchanged(one, 100), "a sound is a change");
assert.equal(addAudio(film, { ...whoosh, duration: 0.01 }, 1), film, "too short");
assert.equal(addAudio(film, { ...whoosh, source: { kind: "sfx", id: "" } }, 1), film);
// Kept inside the output; the end of the output cuts what plays.
const late = addAudio(film, song, 500);
assert.equal(late.audio[0].at, 99.98);
close(audioLength(late.audio[0], outputDuration(late)), 0.02);
// Move and trim; a trim keeps at least 20 ms and the file's bounds.
let edit = addAudio(one, song, 40);
const songId = edit.audio[1].id;
edit = setAudioTime(edit, songId, { at: 41.5, start: 2, end: 12 });
assert.deepEqual([edit.audio[1].at, edit.audio[1].start, edit.audio[1].end], [41.5, 2, 12]);
assert.equal(setAudioTime(edit, songId, { end: 1 }).audio[1].end, 2.02);
assert.equal(setAudioTime(edit, songId, { end: 99 }).audio[1].end, 30);
assert.equal(setAudioTime(edit, songId, { at: -4 }).audio[1].at, 0);
assert.equal(setAudioTime(edit, songId, { at: 41.5 }), edit, "a no-op returns the same edit");
assert.equal(setAudioTime(edit, songId, { at: Number.NaN }), edit);
// Volume 0–200 %, fades 0–3 s in 0.1 s steps that fit the clip, ducking 0–24 dB.
edit = setAudioProps(edit, songId, { volume: 1.555, fadeIn: 1.24, fadeOut: 9, duckDb: 10.4 });
assert.deepEqual([edit.audio[1].volume, edit.audio[1].fadeIn, edit.audio[1].fadeOut, edit.audio[1].duckDb], [1.56, 1.2, 3, 10]);
assert.equal(setAudioProps(edit, songId, { volume: 5 }).audio[1].volume, 2);
assert.equal(setAudioProps(edit, songId, { duckDb: 99 }).audio[1].duckDb, 24);
const shortClip = setAudioProps(setAudioTime(edit, songId, { end: 3 }), songId, { fadeIn: 3, fadeOut: 3 }).audio[1];
assert.ok(shortClip.fadeIn + shortClip.fadeOut <= 1 + 1e-9, "fades fit a 1 s clip");
// What the preview hears: the clip's gain with its fades, the original sound dipped under it.
const total = outputDuration(edit);
const clip = edit.audio[1];
assert.equal(audioGain(clip, 41, total), 0, "before the clip");
close(audioGain(clip, 41.5 + 0.6, total), 1.56 * 0.5, "half way through the fade-in");
close(audioGain(clip, 46, total), 1.56);
close(audioGain(clip, 41.5 + 10 - 1.5, total), 1.56 * 0.5, "half way through the fade-out");
assert.equal(audioGain(clip, 52, total), 0, "after the clip");
assert.equal(mainLevel(edit, 30), 1);
close(mainLevel(edit, 45), Math.pow(10, -10 / 20));
const louder = setMainVolume(edit, 1.5);
close(mainLevel(louder, 30), 1.5);
close(mainLevel(louder, 45), 1.5 * Math.pow(10, -10 / 20));
assert.equal(setMainVolume(edit, 9).mainVolume, 2);
assert.equal(setMainVolume(edit, 1), edit);
assert.ok(!isUnchanged(setMainVolume(film, 0.5), 100), "a main volume change is a change");
// Rows for the timeline: overlapping clips stack.
const lanes = audioLanes([{ id: "a", at: 0, start: 0, end: 5 }, { id: "b", at: 2, start: 0, end: 1 }, { id: "c", at: 5, start: 0, end: 1 }, { id: "d", at: 2.5, start: 0, end: 1 }], 100);
assert.deepEqual([...lanes.entries()].sort(), [["a", 0], ["b", 1], ["c", 0], ["d", 2]]);
// Clips stay at their output time when segments are inserted, split or removed (overlays ripple, sounds do not).
const intro = { path: "D:\\c\\intro.mp4", title: "intro", duration: 10, width: 1280, height: 720, fps: 25, hasAudio: true, kind: "file" };
const inserted = insertAt(edit, 5, intro);
assert.equal(inserted.audio[1].at, 41.5);
assert.equal(remove(split(edit, 20), "s1").audio[1].at, 41.5);
assert.equal(removeAudio(edit, songId).audio.length, 1);
assert.equal(removeAudio(edit, "a999"), edit);
// At most 100 sounds.
let crowded = film;
for (let i = 0; i < MAX_AUDIO + 5; i++) crowded = addAudio(crowded, whoosh, i);
assert.equal(crowded.audio.length, MAX_AUDIO);
// Undo covers sounds and the main volume.
const history = new EditHistory(film);
history.push(one);
history.push(setMainVolume(one, 0.5));
assert.equal(history.undo().mainVolume, 1);
assert.equal(history.undo().audio.length, 0);
assert.equal(history.redo().audio.length, 1);

// ---------- Export plan ----------
const mixed = setMainVolume(setAudioProps(edit, "a2", { fadeOut: 0.3 }), 0.8);
const plan = exportPlan(mixed);
assert.equal(plan.mainVolume, 0.8);
assert.deepEqual(plan.audio, [
  { source: { kind: "sfx", id: "whoosh-01" }, at: 12.346, start: 0, end: 0.601, volume: 1, fade_in: 0, fade_out: 0.3, duck_db: 0 },
  { source: { kind: "file", path: "D:\\Nhạc\\nhạc nền.mp3" }, at: 41.5, start: 2, end: 12, volume: 1.56, fade_in: 1.2, fade_out: 3, duck_db: 10 },
]);
// exports.json keeps them as the host received them (main volume as main_volume).
const exported = { ...JSON.parse(JSON.stringify(plan)), main_volume: plan.mainVolume };
assert.ok(matchesExport(mixed, exported));
assert.ok(!matchesExport(setMainVolume(mixed, 1), exported));
assert.ok(!matchesExport(setAudioProps(mixed, songId, { duckDb: 0 }), exported));
assert.ok(!matchesExport(removeAudio(mixed, "a2"), exported));
assert.ok(matchesExport(film, { segments: [{ start: 0, end: 100, speed: 1, volume: 1 }] }), "a 4a export has no sounds and full volume");
// A clip pushed past the shortened output is left out.
const cut = { ...mixed, segments: [{ ...mixed.segments[0], end: 30 }] };
assert.deepEqual(exportPlan(cut).audio.map((item) => item.at), [12.346]);

// ---------- Draft schema 3 (now 4), and v2 drafts migrate ----------
const saved = JSON.parse(JSON.stringify(serializeProject(mixed, 3, "s1")));
assert.equal(saved.schema_version, 4);
assert.equal(saved.main_volume, 0.8);
assert.deepEqual(saved.audio[1], { id: songId, source: { kind: "file", path: "D:\\Nhạc\\nhạc nền.mp3" }, title: "nhạc nền", duration: 30, at: 41.5, start: 2,
  end: 12, volume: 1.56, fade_in: 1.2, fade_out: 3, duck_db: 10 });
const reloaded = parseProject(saved, 100);
assert.deepEqual(reloaded.edit.audio, mixed.audio);
assert.equal(reloaded.edit.mainVolume, 0.8);
assert.ok(reloaded.edit.nextId > Math.max(...mixed.audio.map((item) => Number(item.id.slice(1)))), "new ids never reuse a sound's");
// Broken sounds are dropped, the rest of the draft kept.
const broken = parseProject({ ...saved, audio: [...saved.audio, { ...saved.audio[0], id: "a77", source: { kind: "sfx", id: "../x" } },
  { ...saved.audio[0], id: "a78", volume: 3 }, { ...saved.audio[0], id: "s1" }, "x"], main_volume: 7 }, 100);
assert.equal(broken.edit.audio.length, 2);
assert.equal(broken.edit.mainVolume, 2);
// A 4d (schema 2) draft: no sounds, original sound at 100 %.
const v2 = { ...saved, schema_version: 2 };
delete v2.audio;
delete v2.main_volume;
const migrated = parseProject(v2, 100);
assert.deepEqual(migrated.edit.audio, []);
assert.equal(migrated.edit.mainVolume, 1);
assert.equal(parseProject({ ...saved, schema_version: 2 }, 100).edit.audio.length, 0, "schema 2 ignores audio");

// ---------- Gợi ý AI ----------
const lines = [
  { start_ms: 1000, end_ms: 3500, text: "Anh ta bất ngờ\nđấm một cú!" },
  { start_ms: 4000, end_ms: 6000, text: "  Cả lớp cười ồ.  " },
  { start_ms: 200_000, end_ms: 201_000, text: "Sau khi cắt, câu này nằm ngoài video." },
  { start_ms: 7000, end_ms: 8000, text: "   " },
];
const sounds = CATALOG.sounds.slice(0, 3);
const request = buildSuggestionRequest(lines, sounds, 60_000);
assert.equal(request.effort, "low");
assert.match(request.system, /Chỉ dùng sfx_id có trong danh sách/);
assert.match(request.system, /tối đa 30/);
assert.equal(request.schema.properties.items.maxItems, MAX_SUGGESTIONS);
const text = request.parts[0].text;
assert.match(text, /Video dài 60000 ms \(time_ms hợp lệ: 0 đến 59999\)/);
assert.ok(text.includes(`${sounds[0].id} | ${sounds[0].name_vi} | ${sounds[0].category} | ${sounds[0].tags.join(", ")} | ${sounds[0].duration_ms}ms`));
assert.ok(text.includes(JSON.stringify({ start_ms: 1000, end_ms: 3500, text: "Anh ta bất ngờ đấm một cú!" })), "one line per cue, whitespace folded");
assert.ok(text.includes(JSON.stringify({ start_ms: 4000, end_ms: 6000, text: "Cả lớp cười ồ." })));
assert.ok(!text.includes("ngoài video") && !text.includes('"start_ms":7000'), "lines outside the video or empty are left out");
assert.ok(!text.includes(CATALOG.sounds[5].id + " |"), "only the given catalog");
assert.match(buildSuggestionRequest([], sounds, 60_000).parts[0].text, /Video chưa có lời thoại/);
// A long video stays one bounded request.
const many = Array.from({ length: 5000 }, (_, i) => ({ start_ms: i * 1000, end_ms: i * 1000 + 900, text: "Một câu thoại khá dài ".repeat(20) }));
assert.ok(buildSuggestionRequest(many, CATALOG.sounds, 6_000_000).parts[0].text.length < 200_000);

const id = (index) => sounds[index].id;
const answer = { items: [
  { time_ms: 3000.4, sfx_id: id(1), reason_vi: "  Cú đấm\nbất ngờ  " },
  { time_ms: 1000, sfx_id: id(0), reason_vi: "Mở đầu" },
  { time_ms: 1200, sfx_id: id(0), reason_vi: "Trùng chỗ" },
  { time_ms: 5000, sfx_id: "made-up-sound", reason_vi: "Bịa" },
  { time_ms: 60_000, sfx_id: id(2), reason_vi: "Ngoài video" },
  { time_ms: -5, sfx_id: id(2), reason_vi: "Âm" },
  { time_ms: "7000", sfx_id: id(2), reason_vi: "Sai kiểu" },
  { time_ms: 7000, sfx_id: id(2) },
  null,
] };
assert.deepEqual(validateSuggestions(answer, sounds, 60_000), [
  { time_ms: 1000, sfx_id: id(0), reason_vi: "Mở đầu" },
  { time_ms: 3000, sfx_id: id(1), reason_vi: "Cú đấm bất ngờ" },
  { time_ms: 7000, sfx_id: id(2), reason_vi: "" },
]);
assert.deepEqual(validateSuggestions({ nope: [] }, sounds, 60_000), []);
assert.deepEqual(validateSuggestions(null, sounds, 60_000), []);
const flood = { items: Array.from({ length: 80 }, (_, i) => ({ time_ms: i * 700, sfx_id: id(i % 3), reason_vi: "x" })) };
const capped = validateSuggestions(flood, sounds, 60_000);
assert.equal(capped.length, MAX_SUGGESTIONS);
assert.ok(capped.every((item, i) => i === 0 || item.time_ms >= capped[i - 1].time_ms), "in time order");

// suggestEffects asks once with the built request and returns only checked picks.
let asked = null;
const fake = async (provider, key, req) => { asked = { provider, key, req }; return answer; };
const picks = await suggestEffects("gemini", "test-key-not-real", lines, sounds, 60_000, fake);
assert.equal(asked.provider, "gemini");
assert.equal(asked.req.parts[0].text, request.parts[0].text);
assert.equal(picks.length, 3);
await assert.rejects(suggestEffects("claude", "k", lines, sounds, 60_000, async () => ({ items: [{ time_ms: 1, sfx_id: "nope", reason_vi: "" }] })), /AI chưa tìm được chỗ hợp/);
await assert.rejects(suggestEffects("claude", "k", lines, sounds, 500, fake), /quá ngắn/);

console.log("audio tests passed");
