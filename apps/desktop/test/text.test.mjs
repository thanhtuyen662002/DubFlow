import assert from "node:assert/strict";
import {
  addedId, addText, animSpan, cleanTextValue, createEdit, DEFAULT_TEXT_LOOK, duplicateText, EditHistory, exportPlan, isUnchanged, matchesExport,
  MAX_TEXT_CHARS, MAX_TEXTS, moveText, parseProject, remove, removeText, resizeText, serializeProject, setTextProps, setTextTime, split,
  textLength, textsAt,
} from "../src/features/edit/model.ts";
import { POP_FROM, POP_OVER, revealAt, SLIDE, snapText, TEXT_PRESETS, textFrame, textMetrics, textUnits } from "../src/features/edit/text.ts";
import { SUBTITLE_FONTS } from "../src/features/edit/subtitles.ts";

const close = (actual, expected, message, tolerance = 1e-6) => assert.ok(Math.abs(actual - expected) < tolerance, `${message ?? ""} ${actual} != ${expected}`);

// ---------- Adding ----------
const film = createEdit(100, { width: 1920, height: 1080 });
assert.deepEqual(film.texts, []);
const one = addText(film, 12.3456);
assert.equal(one.texts.length, 1);
assert.deepEqual(one.texts[0], { ...DEFAULT_TEXT_LOOK, id: "t2", text: "Chữ của bạn", at: 12.346, duration: 3 });
assert.equal(addedId(film, one), "t2");
assert.equal(one.nextId, 3);
assert.ok(!isUnchanged(one, 100), "a text is something to export");
// Near the end it is pulled back to keep 0.2 s, and stops at the end of the video.
const late = addText(film, 99.95).texts[0];
assert.deepEqual([late.at, late.duration], [99.8, 0.2]);
assert.deepEqual([addText(film, 98).texts[0].at, addText(film, 98).texts[0].duration], [98, 2]);
assert.equal(addText(film, Number.NaN), film);
assert.equal(addText(film, 1, "   \n "), film, "empty words");
let crowded = film;
for (let index = 0; index < MAX_TEXTS; index++) crowded = addText(crowded, index);
assert.equal(crowded.texts.length, MAX_TEXTS);
assert.equal(addText(crowded, 1), crowded, "at most 50 texts");
assert.equal(duplicateText(crowded, crowded.texts[0].id), crowded);

// Words render as typed: composed, one kind of line break, no control characters, 300 characters.
assert.equal(cleanTextValue("Pha\u0302\u0300n 2\r\nhai\rba\u0007\t"), "Phần 2\nhai\nba");
assert.equal([...cleanTextValue("đ".repeat(400))].length, MAX_TEXT_CHARS);
assert.equal(cleanTextValue("😀".repeat(301)), "😀".repeat(300), "characters, not UTF-16 units");

// Presets: every look is valid, so a preset text has exactly its look.
assert.deepEqual(TEXT_PRESETS.map((preset) => preset.label), ["Tiêu đề lớn", "Chú thích", "Tên kênh góc", "Phụ đề nhấn", "Nhãn hộp"]);
for (const preset of TEXT_PRESETS) {
  const added = addText(film, 5, preset.text, preset.look, preset.seconds === "end" ? 95 : preset.seconds).texts[0];
  for (const [key, value] of Object.entries(preset.look)) assert.equal(added[key], value, `${preset.id}.${key}`);
  assert.equal(added.text, preset.text);
  assert.ok(SUBTITLE_FONTS.includes(added.font));
}
const channel = TEXT_PRESETS.find((preset) => preset.id === "channel");
const corner = addText(film, 0, channel.text, channel.look, 100).texts[0];
close(corner.x - corner.w / 2, 0.03, "the channel name keeps a 3 % margin");
assert.equal(corner.duration, 100);

// ---------- Changing ----------
const id = "t2";
const styled = setTextProps(one, id, { text: "Phần 2\nĐường về", font: "Candara", size: 300, color: "#fde047", outline_width: 12.3, box: "#000000",
  box_opacity: 0.63, bold: false, italic: true, align: "left", animation: "pop", anim_duration: 2.54 });
const text = styled.texts[0];
assert.deepEqual([text.text, text.font, text.size, text.color, text.outline_width, text.box, text.box_opacity, text.italic, text.align, text.animation, text.anim_duration],
  ["Phần 2\nĐường về", "Candara", 240, "#FDE047", 12, "#000000", 0.65, true, "left", "pop", 2]);
// Anything invalid is ignored; empty words keep the old ones; no change is the same edit.
assert.equal(setTextProps(styled, id, { font: "Impact", color: "yellow", box: "#12345", align: "justify", animation: "spin", size: Number.NaN }), styled);
assert.equal(setTextProps(styled, id, { text: "  " }), styled);
assert.equal(setTextProps(styled, id, { size: 240 }), styled);
assert.equal(setTextProps(styled, "t99", { size: 50 }), styled);
assert.equal(setTextProps(styled, id, { box: null }).texts[0].box, null);
assert.equal(setTextProps(styled, id, { size: 3 }).texts[0].size, 16);
// Times: at least 0.2 s, inside the output.
assert.deepEqual(pick(setTextTime(one, id, { at: 99, duration: 5 }).texts[0], "at", "duration"), { at: 99, duration: 1 });
assert.deepEqual(pick(setTextTime(one, id, { duration: 0.01 }).texts[0], "at", "duration"), { at: 12.346, duration: 0.2 });
assert.deepEqual(pick(setTextTime(one, id, { at: -4 }).texts[0], "at", "duration"), { at: 0, duration: 3 });
assert.equal(setTextTime(one, id, { at: Number.POSITIVE_INFINITY }), one);
// Position: the box stays inside the frame sideways; its width is 10–100 %.
assert.deepEqual(pick(moveText(one, id, 0.95, 1.4).texts[0], "x", "y"), { x: 0.6, y: 1 });
assert.deepEqual(pick(moveText(one, id, 0.1, -0.2).texts[0], "x", "y"), { x: 0.4, y: 0 });
assert.deepEqual(pick(resizeText(one, id, 0.02).texts[0], "w", "x"), { w: 0.1, x: 0.5 });
assert.deepEqual(pick(resizeText(one, id, 0.5, 0.3).texts[0], "w", "x"), { w: 0.5, x: 0.3 });
assert.deepEqual(pick(resizeText(one, id, 1.5, 0.3).texts[0], "w", "x"), { w: 1, x: 0.5 });
// Duplicate: right after when it fits, else a little lower at the same time.
const copied = duplicateText(one, id);
assert.deepEqual(pick(copied.texts[1], "id", "at", "duration", "y", "text"), { id: "t3", at: 15.346, duration: 3, y: 0.5, text: "Chữ của bạn" });
const tail = setTextTime(one, id, { at: 97, duration: 3 });
assert.deepEqual(pick(duplicateText(tail, id).texts[1], "at", "y"), { at: 97, y: 0.58 });
assert.deepEqual(removeText(copied, "t3").texts, one.texts);
assert.equal(removeText(copied, "t9"), copied);
assert.deepEqual(textsAt(copied, 14).map((item) => item.id), ["t2"]);
assert.deepEqual(textsAt(copied, 15.5).map((item) => item.id), ["t3"]);
// Texts stay at their output time when segments are cut or split (like sounds).
const cutFirst = remove(split(one, 10), "s1");
assert.equal(cutFirst.texts[0].at, 12.346);
assert.equal(textLength(cutFirst.texts[0], 90), 3);
assert.equal(textLength({ at: 89, duration: 3 }, 90), 1, "cut at the end of the output");
// Undo/redo carries texts like any other change.
const history = new EditHistory(film);
history.push(one);
history.push(styled);
assert.equal(history.undo(), one);
assert.equal(history.undo(), film);
assert.equal(history.redo(), one);

// ---------- Export ----------
const plan = exportPlan(styled);
assert.deepEqual(plan.texts, [{ text: "Phần 2\nĐường về", at: 12.346, duration: 3, x: 0.5, y: 0.5, w: 0.8, font: "Candara", size: 240,
  color: "#FDE047", outline: "#000000", outline_width: 12, box: "#000000", box_opacity: 0.65, bold: false, italic: true, align: "left",
  animation: "pop", anim_duration: 2 }]);
// Shown length and animation span are fitted: a fade in and out takes half; texts past the end are left out.
const fading = setTextProps(setTextTime(one, id, { at: 99.5, duration: 3 }), id, { animation: "fade", anim_duration: 2 });
assert.deepEqual(pick(exportPlan(fading).texts[0], "duration", "anim_duration"), { duration: 0.5, anim_duration: 0.25 });
assert.equal(animSpan({ animation: "pop", anim_duration: 2 }, 0.5), 0.5);
assert.equal(animSpan({ animation: "none", anim_duration: 2 }, 0.5), 0);
const shortened = { ...fading, segments: [{ ...fading.segments[0], end: 90 }] };
assert.deepEqual(exportPlan(shortened).texts, []);
// exports.json keeps the texts as sent; an older export without texts does not match.
const exported = { ...JSON.parse(JSON.stringify(plan)), main_volume: plan.mainVolume };
assert.ok(matchesExport(styled, exported));
assert.ok(!matchesExport(setTextProps(styled, id, { bold: true }), exported));
assert.ok(!matchesExport(moveText(styled, id, 0.4, 0.5), exported));
assert.ok(!matchesExport(styled, { ...exported, texts: undefined }));
assert.ok(matchesExport(film, { segments: [{ start: 0, end: 100, speed: 1, volume: 1 }] }), "a 4a export has no texts");

// ---------- Draft schema 4, and v3 drafts migrate ----------
const saved = JSON.parse(JSON.stringify(serializeProject(copied, 2, "s1")));
assert.equal(saved.schema_version, 4);
assert.equal(saved.texts.length, 2);
assert.deepEqual(saved.texts[0], { ...DEFAULT_TEXT_LOOK, id: "t2", text: "Chữ của bạn", at: 12.346, duration: 3 });
const reloaded = parseProject(saved, 100);
assert.deepEqual(reloaded.edit.texts, copied.texts);
assert.equal(reloaded.edit.nextId, 4);
const ahead = parseProject({ ...saved, next_id: 2 }, 100);
assert.equal(ahead.edit.nextId, 4, "new ids never reuse a text's");
const broken = parseProject({ ...saved, texts: [...saved.texts, { ...saved.texts[0], id: "t8", font: "Impact" }, { ...saved.texts[0], id: "t9", text: " " },
  { ...saved.texts[0], id: "t2" }, { ...saved.texts[0], id: "a5" }, { ...saved.texts[0], id: "t10", box: "red" }, "x",
  { ...saved.texts[0], id: "t11", size: 999, x: 0 }] }, 100);
assert.deepEqual(broken.edit.texts.map((item) => item.id), ["t2", "t3", "t11"]);
assert.deepEqual(pick(broken.edit.texts[2], "size", "x"), { size: 240, x: 0.4 }, "out-of-range values are fitted");
const v3 = { ...saved, schema_version: 3 };
delete v3.texts;
assert.deepEqual(parseProject(v3, 100).edit.texts, []);
assert.deepEqual(parseProject({ ...saved, schema_version: 3 }, 100).edit.texts, [], "schema 3 ignores texts");

// ---------- Drawing and animation (text.ts) ----------
const metrics = textMetrics({ font: "Arial", size: 90, outline_width: 3, box: "#000000", w: 0.8 }, { width: 960, height: 540 });
close(metrics.fontPx, 45 * 0.895);
close(metrics.lineHeight, 1 / 0.895);
close(metrics.outlinePx, 1.5);
close(metrics.paddingPx, 9);
close(metrics.widthPx, 768);
close(textMetrics({ font: "Arial", size: 90, outline_width: 0, box: null, w: 0.5 }, { width: 540, height: 960 }).fontPx, 45 * 0.895, "portrait: the short side");

const at = (look, time) => textFrame({ ...one.texts[0], ...look }, time, 100);
assert.equal(at({}, 12), null, "before it starts");
assert.equal(at({}, 15.346), null, "after it ends");
assert.deepEqual(at({}, 13), { opacity: 1, scale: 1, dy: 0, shown: Number.POSITIVE_INFINITY });
close(at({ animation: "fade", anim_duration: 1 }, 12.846).opacity, 0.5);
close(at({ animation: "fade", anim_duration: 1 }, 15.096).opacity, 0.25, "fades out at the end");
const pop = (time) => at({ animation: "pop", anim_duration: 0.5 }, 12.346 + time);
close(pop(0).scale, POP_FROM);
close(pop(0.3).scale, POP_OVER);
close(pop(0.4).scale, (POP_OVER + 1) / 2);
close(pop(0.1).opacity, 0.5);
assert.equal(pop(0.6).scale, 1);
const slide = at({ animation: "slide-up", anim_duration: 0.4 }, 12.546);
close(slide.dy, SLIDE / 2);
close(slide.opacity, 0.5);
// Typewriter: as the export's \ko tags reveal it (edit.rs typewriter_text).
assert.deepEqual(textUnits("a\u0301b\nc").map((unit) => [unit.text, unit.index]), [["a\u0301", 0], ["b", 1], ["\n", null], ["c", 2]]);
assert.deepEqual([0, 1, 2, 3].map((index) => revealAt(index, 4, 0.25)), [0, 0.06, 0.13, 0.19]);
const typed = (time) => at({ text: "Gõ từng chữ\nỞ đây", animation: "typewriter", anim_duration: 1 }, 12.346 + time).shown;
assert.deepEqual([typed(0), typed(0.05), typed(0.07), typed(0.45), typed(0.99)], [1, 1, 2, 8, 16]);

// Snapping: the centre to the middle and thirds, the box edges to the 5 % margins.
const snap = { x: 0.01, y: 0.01 };
assert.deepEqual(snapText({ x: 0.505, y: 0.4 }, { w: 0.4, h: 0.1 }, snap), { x: 0.5, y: 0.4, guides: [{ axis: "x", at: 0.5 }] });
assert.deepEqual(snapText({ x: 0.3, y: 0.338 }, { w: 0.2, h: 0.1 }, snap), { x: 0.3, y: 0.3333, guides: [{ axis: "y", at: 1 / 3 }] });
assert.deepEqual(snapText({ x: 0.155, y: 0.9 }, { w: 0.2, h: 0.1 }, snap), { x: 0.15, y: 0.9, guides: [{ axis: "x", at: 0.05 }, { axis: "y", at: 0.95 }] });
assert.deepEqual(snapText({ x: 0.155, y: 0.9 }, { w: 0.2, h: 0.1 }, snap, true), { x: 0.155, y: 0.9, guides: [] }, "Alt: no snapping");
assert.deepEqual(snapText({ x: 0.99, y: 0.5 }, { w: 0.4, h: 0.1 }, snap).x, 0.8, "kept inside sideways");

function pick(value, ...keys) {
  return Object.fromEntries(keys.map((key) => [key, value[key]]));
}

console.log("text tests passed");
