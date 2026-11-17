// Thumbnail (Issue #901): the pure model of the editor's thumbnail maker.
import assert from "node:assert/strict";
import {
  THUMB_HEIGHT, THUMB_WIDTH, THUMB_TEMPLATES, applyTemplate, coverRect, dragPan, emptyThumb, hasThumb, makeText,
  DRAG_START_PX, MAX_LINE_WIDTH, MAX_THUMB_TEXT, centreOn, fitBox, inkMargin, layoutText, leavesEdge, nudgeText, moveText, normalizeThumb, textAt, textBox, textRoom, thumbName, wrapLines,
} from "../src/features/edit/thumbnail.ts";

const covers = (rect) => rect.x <= 1e-9 && rect.y <= 1e-9 && rect.x + rect.width >= THUMB_WIDTH - 1e-9 && rect.y + rect.height >= THUMB_HEIGHT - 1e-9;

// The picture always covers the 1280×720 thumbnail: wide, tall, small, at any zoom and pan.
for (const [width, height] of [[1920, 1080], [1080, 1920], [640, 360], [4000, 1000], [300, 300]]) {
  for (const zoom of [1, 1.5, 3, 9, 0.2]) {
    for (const pan of [-1, -0.4, 0, 0.7, 1, -5, 5]) {
      const rect = coverRect(width, height, zoom, pan, pan);
      assert.ok(covers(rect), `${width}x${height} zoom ${zoom} pan ${pan}: ${JSON.stringify(rect)}`);
      assert.ok(Math.abs(rect.width / rect.height - width / height) < 1e-9, "never stretched");
    }
  }
}
// A 16:9 frame at zoom 1 fills exactly; a portrait one is centred, cropped top and bottom.
assert.deepEqual(coverRect(1920, 1080, 1, 0, 0), { x: 0, y: 0, width: 1280, height: 720 });
const tall = coverRect(1080, 1920, 1, 0, 0);
assert.equal(tall.width, 1280);
assert.ok(Math.abs(tall.y + (tall.height - 720) / 2) < 1e-9);
// pan -1 shows the top / left edge, 1 the bottom / right.
assert.equal(coverRect(1080, 1920, 1, 0, -1).y, 0);
assert.ok(Math.abs(coverRect(1080, 1920, 1, 0, 1).y + (tall.height - 720)) < 1e-9);

// Dragging moves the picture with the pointer, never past its edge; no slack means no pan.
let doc = { ...emptyThumb(), zoom: 2 };
const dragged = dragPan(doc, 1920, 1080, 100, 0);
assert.ok(dragged.panX < 0, "dragged right shows more of the left");
assert.equal(dragPan(doc, 1920, 1080, 1e6, -1e6).panX, -1);
assert.equal(dragPan(doc, 1920, 1080, 1e6, -1e6).panY, 1);
assert.deepEqual(dragPan(emptyThumb(), 1920, 1080, 50, 50), { panX: 0, panY: 0 });
{
  const before = coverRect(1920, 1080, 2, 0, 0);
  const after = coverRect(1920, 1080, 2, dragged.panX, dragged.panY);
  assert.ok(Math.abs(after.x - before.x - 100) < 1e-6, "the picture moved exactly the pointer's distance");
}

// A saved thumbnail is checked and clamped; anything else is no thumbnail.
assert.equal(normalizeThumb(null), null);
assert.equal(normalizeThumb({ version: 2 }), null);
assert.equal(normalizeThumb("x"), null);
const saved = normalizeThumb({
  version: 1, background: { kind: "frame", path: "D:\\Videos\\a.mp4", time: -3 }, zoom: 20, panX: -9, panY: 0.25, darken: 5, format: "png",
  texts: [
    { id: "a", text: "Tháp nuôi lợn 26 tầng\r\n海拔", x: 2, y: -1, size: 5000, font: "Comic Sans", color: "red", stroke: "#00FF00", strokeWidth: 99, shadow: true, band: "#123456" },
    { text: 3 }, null,
    ...Array.from({ length: 10 }, (_, index) => ({ text: `dòng ${index}` })),
  ],
});
assert.deepEqual(saved.background, { kind: "frame", path: "D:\\Videos\\a.mp4", time: 0 });
assert.equal(saved.zoom, 3);
assert.equal(saved.panX, -1);
assert.equal(saved.panY, 0.25);
assert.equal(saved.darken, 0.7);
assert.equal(saved.format, "png");
assert.equal(saved.texts.length, 6, "at most six texts; broken rows dropped");
const first = saved.texts[0];
assert.equal(first.text, "Tháp nuôi lợn 26 tầng\n海拔", "Vietnamese and Chinese kept, line ends unified");
assert.equal(first.x, 1);
assert.equal(first.y, 0);
assert.equal(first.size, 220);
assert.equal(first.font, "Segoe UI Black", "an unknown font falls back to one with every Vietnamese letter");
assert.equal(first.color, "#ffffff");
assert.equal(first.stroke, "#00ff00");
assert.equal(first.strokeWidth, 24);
assert.equal(first.band, "#123456");
assert.equal(normalizeThumb({ version: 1, background: { kind: "image", path: "" } }).background, null);
assert.equal(normalizeThumb({ version: 1, format: "gif" }).format, "jpeg");
// A round trip through JSON (the draft file) keeps it whole.
assert.deepEqual(normalizeThumb(JSON.parse(JSON.stringify(saved))), saved);

// A blank thumbnail is not kept.
assert.equal(hasThumb(null), false);
assert.equal(hasThumb(emptyThumb()), false);
assert.equal(hasThumb({ ...emptyThumb(), texts: [makeText("  ")] }), false);
assert.equal(hasThumb({ ...emptyThumb(), texts: [makeText("Hay")] }), true);
assert.equal(hasThumb({ ...emptyThumb(), background: { kind: "image", path: "D:\\a.png" } }), true);

// Text wraps at word boundaries; a long word stays whole; blank lines inside are kept.
const measure = (line) => line.length * 10;
assert.deepEqual(wrapLines("một hai ba bốn năm", 70, measure), ["một hai", "ba bốn", "năm"]);
assert.deepEqual(wrapLines("siêuuuuuuuuuuudài x", 50, measure), ["siêuuuuuuuuuuudài", "x"]);
assert.deepEqual(wrapLines("a\n\nb\n", 100, measure), ["a", "", "b"]);

// rc23: every layout keeps a long title on the thumbnail. Lines wrap to the room around the text's centre,
// wider where that would be taller than the thumbnail, and a box that would still be cut at an edge is drawn moved in.
const inside = (rect) => rect.x >= -1e-9 && rect.y >= -1e-9 && rect.x + rect.width <= THUMB_WIDTH + 1e-9 && rect.y + rect.height <= THUMB_HEIGHT + 1e-9;
const glyphs = (text) => (line) => line.length * 0.62 * text.size; // about Segoe UI Black
const drawnTop = (text) => layoutText(text, glyphs(text)).box.y;
const longTitle = "RC23 THÁP NUÔI LỢN 26 TẦNG Ở HỒ BẮC";
const longerTitle = "Bên trong tòa tháp nuôi lợn 26 tầng lớn nhất thế giới ở Hồ Bắc Trung Quốc mỗi năm xuất chuồng 1,2 triệu con";
const longestTitle = `${longerTitle} và hơn thế nữa`.slice(0, MAX_THUMB_TEXT);
assert.equal(longestTitle.length, MAX_THUMB_TEXT);
const checked = { inside: 0, tooLong: 0 };
for (const title of [longTitle, longerTitle, longestTitle]) {
  for (const template of THUMB_TEMPLATES) {
    const laid = applyTemplate({ ...emptyThumb(), texts: [] }, template.id, title);
    for (const text of laid.texts) {
      const { lines, widths, box } = layoutText(text, glyphs(text));
      assert.equal(lines.join(" "), text.text.split(/\s+/).join(" "), `${template.id}: every word drawn`);
      assert.ok(widths.every((width) => width <= MAX_LINE_WIDTH), `${template.id}: no line over 90 %`);
      // Inside whenever it fits top to bottom at 90 % (a title too long for its size even then is left as it is, below).
      const fits = textBox(text, wrapLines(text.text, MAX_LINE_WIDTH, glyphs(text)).map(glyphs(text))).height <= THUMB_HEIGHT;
      if (fits) assert.ok(inside(box), `${template.id} "${text.text.slice(0, 20)}…" (${title.length}): drawn inside the thumbnail ${JSON.stringify(box)}`);
      else assert.deepEqual(lines, wrapLines(text.text, MAX_LINE_WIDTH, glyphs(text)), `${template.id} (${title.length}): the fewest lines`);
      checked[fits ? "inside" : "tooLong"] += 1;
    }
  }
}
assert.deepEqual(checked, { inside: 10, tooLong: 5 }, "the 35-letter title fits every layout; the 107/120-letter ones only some at their size");
const left = applyTemplate({ ...emptyThumb(), texts: [] }, "left-column", longTitle).texts[0];
assert.equal(textRoom(left), 2 * 0.3 * THUMB_WIDTH - 16, "left-column: the room around 30 % across, less the outline");
assert.ok(textBox(left, [glyphs(left)(longTitle)]).x < 0, "on one line, as on rc23, its start would be cut");
assert.ok(layoutText(left, glyphs(left)).widths.every((width) => width <= textRoom(left)), "a title that fits top to bottom wraps to its room");
// Too tall in its room: wrapped wider (as narrow as still fits), then moved in sideways, instead of losing its first and last lines.
const tallTitle = applyTemplate({ ...emptyThumb(), texts: [] }, "left-column", longerTitle).texts[0];
assert.ok(textBox(tallTitle, wrapLines(tallTitle.text, textRoom(tallTitle), glyphs(tallTitle)).map(glyphs(tallTitle))).height > THUMB_HEIGHT, "in its room it would be taller than the thumbnail");
const tallLaid = layoutText(tallTitle, glyphs(tallTitle));
assert.ok(inside(tallLaid.box) && Math.max(...tallLaid.widths) > textRoom(tallTitle), "wrapped wider and drawn inside");
const oneLineMore = textBox(tallTitle, wrapLines(tallTitle.text, Math.max(...tallLaid.widths) - 2, glyphs(tallTitle)).map(glyphs(tallTitle)));
assert.ok(oneLineMore.height > THUMB_HEIGHT, "no narrower wrap fits top to bottom");
const nearEdge = { ...left, x: 0.08 };
assert.ok(inside(layoutText(nearEdge, glyphs(nearEdge)).box), "dragged near the edge: not one word per line off the top and bottom");
assert.deepEqual(layoutText(makeText("x", { x: 0.5 }), glyphs({ size: 96 })).lines, ["x"]);
assert.equal(textRoom(makeText("x", { x: 0.5, strokeWidth: 0 })), MAX_LINE_WIDTH, "centred: 90 % of the width as before");
assert.equal(textRoom(makeText("x", { x: 0, size: 50 })), 100, "at the edge: still two letters' worth");
const centred = makeText(longerTitle, { x: 0.5, y: 0.5, size: 92 });
assert.deepEqual(layoutText(centred, glyphs(centred)).lines, wrapLines(longerTitle, MAX_LINE_WIDTH, glyphs(centred)), "centred: wrapped as before");
// Taller than the thumbnail even at 90 %: the fewest lines, left where it is.
const huge = makeText(longestTitle, { x: 0.5, y: 0.5, size: 200, strokeWidth: 0 });
const hugeLaid = layoutText(huge, glyphs(huge));
assert.deepEqual(hugeLaid.lines, wrapLines(longestTitle, MAX_LINE_WIDTH, glyphs(huge)));
assert.deepEqual(hugeLaid.box, textBox(huge, hugeLaid.widths));
// A box is moved in only where it would be cut, and only as far as needed.
assert.deepEqual(fitBox({ x: 100, y: 100, width: 200, height: 100 }), { x: 100, y: 100, width: 200, height: 100 });
assert.deepEqual(fitBox({ x: -40, y: 650, width: 200, height: 100 }), { x: 0, y: 620, width: 200, height: 100 });
assert.deepEqual(fitBox({ x: 1200, y: -5, width: 200, height: 100 }), { x: 1080, y: 0, width: 200, height: 100 });
assert.deepEqual(fitBox({ x: -10, y: 0, width: 1400, height: 100 }), { x: -10, y: 0, width: 1400, height: 100 }, "too big: as it is");
// Accents stacked on capitals and the outline reach past the box: at the top or bottom edge that room is kept too.
const topWord = makeText("NGHIÊNG Ể", { x: 0.5, y: 0, size: 96, strokeWidth: 8 });
assert.deepEqual(inkMargin(topWord), { top: 96 * 0.25 + 8, bottom: 96 * 0.05 + 8 });
assert.equal(drawnTop(topWord), 32, "at the top: its accents and outline stay on the image");
const bottomWord = { ...topWord, y: 1 };
const bottomBox = layoutText(bottomWord, glyphs(bottomWord)).box;
assert.ok(Math.abs(bottomBox.y + bottomBox.height - (THUMB_HEIGHT - 96 * 0.05 - 8)) < 1e-6, "at the bottom: its descenders and outline too");
assert.deepEqual(inkMargin(makeText("x", { band: "#e11d48", size: 64, strokeWidth: 0 })), { top: 64 * 0.1, bottom: 0 }, "a band holds part of it");
assert.deepEqual(fitBox({ x: 0, y: 0, width: 100, height: 100 }, { top: 20, bottom: 10 }), { x: 0, y: 20, width: 100, height: 100 });
assert.deepEqual(fitBox({ x: 0, y: 650, width: 100, height: 100 }, { top: 20, bottom: 10 }), { x: 0, y: 610, width: 100, height: 100 });
assert.deepEqual(fitBox({ x: 0, y: -50, width: 100, height: 700 }, { top: 20, bottom: 10 }), { x: 0, y: 0, width: 100, height: 700 }, "no room for the margin: the box itself still fits");
// A move away from an edge the text is held against starts where it is drawn, so it comes back at the first step;
// a move toward or along the edge, or a jitter, keeps the saved place, so the text stays held there.
const drawnAt = (text) => layoutText(text, glyphs(text)).box;
let pushed = makeText("Chữ mới", { x: 0.5, y: 0.5 });
for (let step = 0; step < 100; step += 1) pushed = nudgeText(pushed, drawnAt(pushed), -10, 0);
const atEdge = drawnAt(pushed);
assert.equal(atEdge.x, 0, "pushed to the left edge");
assert.equal(pushed.x, 0, "its saved centre went on to the edge itself");
const back = nudgeText(pushed, atEdge, 8, 0);
assert.ok(Math.abs(drawnAt(back).x - 8) < 1e-6, `one step right moves it 8 px at once (${drawnAt(back).x})`);
assert.equal(nudgeText(pushed, atEdge, -8, 0).x, 0, "toward the edge: still held");
assert.equal(nudgeText(pushed, atEdge, 0, 8).x, 0, "along the edge: still held");
const big = applyTemplate({ ...emptyThumb(), texts: [] }, "big-bottom", longTitle).texts[0];
const bigBox = drawnAt(big);
assert.ok(bigBox.y + bigBox.height / 2 < big.y * THUMB_HEIGHT - 1, "big-bottom: a 3-line title is drawn moved up");
assert.ok(Math.abs(drawnAt(nudgeText(big, bigBox, 0, -10)).y - (bigBox.y - 10)) < 1e-6, "and moves up at the first move");
assert.equal(nudgeText(big, bigBox, 10, 0).y, big.y, "a sideways move keeps it held at the bottom");
assert.equal(leavesEdge(0.5, 0.5, 50, THUMB_WIDTH), false, "not held: the saved place");
assert.equal(leavesEdge(0, 0.1, 1, THUMB_WIDTH), true, "a key move away: leaves");
assert.equal(leavesEdge(0, 0.1, -8, THUMB_WIDTH), false, "toward the edge: still held");
assert.equal(leavesEdge(0, 0.1, 0, THUMB_WIDTH), false, "no move on this axis: as it is");
assert.equal(centreOn(big, undefined), big, "no box drawn: as it is");
const unmoved = makeText("ngắn", { x: 0.5, y: 0.5 });
assert.equal(centreOn(unmoved, drawnAt(unmoved)), unmoved, "a box that was not moved in: the same text");

// Picking and dragging text.
const title = makeText("Tiêu đề", { x: 0.5, y: 0.5, size: 100, strokeWidth: 0 });
const box = textBox(title, [400]);
assert.equal(box.width, 400);
assert.ok(Math.abs(box.x - (640 - 200)) < 1e-9);
const label = makeText("Nhãn", { x: 0.5, y: 0.5, size: 40 });
const boxes = [{ id: title.id, box }, { id: label.id, box: textBox(label, [100]) }];
assert.equal(textAt(boxes, 640, 360), label.id, "the top text wins");
assert.equal(textAt(boxes, 460, 360), title.id);
assert.equal(textAt(boxes, 10, 10), null);
const moved = moveText(title, 128, -72);
assert.ok(Math.abs(moved.x - 0.6) < 1e-9 && Math.abs(moved.y - 0.4) < 1e-9);
assert.deepEqual([moveText(title, 1e5, 1e5).x, moveText(title, 1e5, 1e5).y], [1, 1], "kept on the thumbnail");

// Templates keep the background and lay the texts out again; a thumbnail with no text takes the video's title.
doc = { ...emptyThumb(), background: { kind: "image", path: "D:\\bg.jpg" }, texts: [] };
for (const template of THUMB_TEMPLATES) {
  const next = applyTemplate(doc, template.id, "Lợn ở tòa nhà 26 tầng");
  assert.deepEqual(next.background, doc.background);
  assert.equal(next.texts.at(-1).text, "Lợn ở tòa nhà 26 tầng", `${template.id}: the title is the last text, chosen after the change`);
  assert.equal(next.texts.at(-1).role, "title");
  assert.deepEqual(next.texts.map((text) => text.role), template.labelled ? ["label", "title"] : ["title"]);
  assert.deepEqual(normalizeThumb(next).texts, next.texts, `${template.id} texts (with their roles) are valid as saved`);
}
assert.ok(applyTemplate(doc, "big-bottom", "  ").texts[0].text.length > 0, "an empty title still gives text to edit");
assert.equal(normalizeThumb({ ...doc, texts: [{ ...makeText("x"), role: "boss" }] }).texts[0].role, undefined, "an unknown role is dropped");
assert.ok(!("role" in normalizeThumb({ ...doc, texts: [makeText("x")] }).texts[0]), "a text the user adds has no role");

// rc22 (#921): a change of layout never drops what the user typed (it replaced the typed title with "MỚI").
const typed = (thumb, words) => ({ ...thumb, texts: thumb.texts.map((text, index) => (words[index] === undefined ? text : { ...text, text: words[index] })) });
const words = (thumb) => thumb.texts.map((text) => text.text);
const twice = applyTemplate(applyTemplate(doc, "two-lines", "Tên video"), "two-lines", "Tên video");
assert.deepEqual(words(twice), ["MỚI", "Tên video"], "two-lines twice: the title is not taken from the label");
let two = typed(applyTemplate(doc, "two-lines", "Tên video"), [undefined, "Tháp nuôi lợn 26 tầng"]);
assert.deepEqual(words(applyTemplate(applyTemplate(two, "two-lines", "x"), "two-lines", "x")), ["MỚI", "Tháp nuôi lợn 26 tầng"], "rc22's exact steps");
for (const template of THUMB_TEMPLATES.filter((item) => !item.labelled)) {
  const next = applyTemplate(two, template.id, "Tên video");
  assert.deepEqual(words(next), ["Tháp nuôi lợn 26 tầng"], `two-lines → ${template.id}: the typed title stays, the untouched label goes`);
  assert.deepEqual(words(applyTemplate(next, "two-lines", "Tên video")), ["MỚI", "Tháp nuôi lợn 26 tầng"], `${template.id} → two-lines`);
}
// A typed label goes round every layout and comes back to its place.
two = typed(two, ["SIÊU TO"]);
let round = two;
for (const id of ["band", "big-bottom", "left-column", "two-lines", "two-lines", "band", "two-lines"]) {
  round = applyTemplate(round, id, "Tên video");
  assert.deepEqual([...words(round)].sort(), ["SIÊU TO", "Tháp nuôi lợn 26 tầng"], `after ${id}: both typed texts are kept`);
  assert.equal(round.texts.at(-1).text, "Tháp nuôi lợn 26 tầng", `after ${id}: the title stays the title`);
}
assert.deepEqual(words(round), ["SIÊU TO", "Tháp nuôi lợn 26 tầng"], "back on two-lines, the typed label is the label again");
assert.equal(round.texts[0].role, "label");
const band = applyTemplate(two, "band", "Tên video");
assert.deepEqual(band.texts.find((text) => text.text === "SIÊU TO"), two.texts[0], "a label a layout has no place for stays as it was");
// A "MỚI" the user moved or restyled is theirs: kept.
const movedLabel = { ...twice, texts: [{ ...twice.texts[0], x: 0.86, band: "#2563eb" }, twice.texts[1]] };
assert.deepEqual(applyTemplate(movedLabel, "band", "x").texts[0], movedLabel.texts[0], "a moved, recoloured MỚI is kept");
// A "MỚI" the user typed is theirs, even placed and styled exactly like the layout's label.
const lookalike = makeText("MỚI", { x: 0.16, y: 0.16, size: 64, band: "#e11d48", strokeWidth: 0, shadow: false });
const withOwn = { ...twice, texts: [...twice.texts, lookalike] };
assert.deepEqual(applyTemplate(withOwn, "band", "x").texts.filter((text) => text.text === "MỚI"), [lookalike], "band: the layout's MỚI goes, the user's stays");
assert.deepEqual(words(applyTemplate(withOwn, "two-lines", "x")), ["MỚI", "MỚI", "Tên video"], "two-lines: the user's MỚI and a fresh label");
assert.deepEqual(applyTemplate(withOwn, "two-lines", "x").texts[0], lookalike);
// The label is the text the layout made as its label, whatever the order or sizes of the others.
const caption = makeText("Phần 2", { y: 0.5 });
const shuffled = { ...two, texts: [caption, two.texts[1], two.texts[0]] };
for (const id of ["two-lines", "band", "two-lines"]) {
  const next = applyTemplate(shuffled, id, "x");
  assert.equal(next.texts.find((text) => text.role === "label")?.text, "SIÊU TO", `${id}: the typed label is still the label`);
  assert.equal(next.texts.at(-1).text, "Tháp nuôi lợn 26 tầng");
  assert.deepEqual(next.texts[0], caption, `${id}: the caption is as it was`);
}
// Texts the user adds are never promoted or restyled by a layout.
const mark = makeText("@kenhcuatoi", { x: 0.9, y: 0.95, size: 32 });
let marked = { ...twice, texts: [...twice.texts, mark] };
for (let index = 0; index < 4; index += 1) {
  marked = applyTemplate(marked, "two-lines", "Tên video");
  assert.deepEqual(marked.texts.find((text) => text.text === "@kenhcuatoi"), mark, `two-lines ×${index + 1}: the watermark is untouched`);
  assert.deepEqual(words(marked).filter((text) => text !== "@kenhcuatoi"), ["MỚI", "Tên video"]);
}
const added = makeText("Phần 2", { y: 0.5 });
const onBand = { ...applyTemplate(doc, "band", "Tên video"), texts: [...applyTemplate(doc, "band", "Tên video").texts, added] };
assert.equal(added.size > onBand.texts[0].size, true, "the added text is larger than the band title");
for (const template of THUMB_TEMPLATES) {
  const next = applyTemplate(onBand, template.id, "x");
  assert.equal(next.texts.at(-1).text, "Tên video", `band → ${template.id}: a larger added text does not take the title`);
  assert.deepEqual(next.texts.find((text) => text.text === "Phần 2"), added, `band → ${template.id}: the added text is as it was`);
  assert.equal(next.texts.length, template.labelled ? 3 : 2);
}
// A thumbnail saved before layouts marked their texts: the largest text is the title, the next one the label,
// and nothing is dropped (nothing is known to be the layout's).
const strip = (thumb) => ({ ...thumb, texts: thumb.texts.map(({ role, ...text }) => text) });
const old = strip(typed(applyTemplate(doc, "two-lines", "x"), [undefined, "Tiêu đề cũ"]));
assert.deepEqual(words(applyTemplate(old, "two-lines", "x")), ["MỚI", "Tiêu đề cũ"], "its MỚI is reused as the label, not doubled");
assert.deepEqual(applyTemplate(old, "band", "x").texts[0], old.texts[0], "on a layout without a label, its MỚI stays as it was");
assert.deepEqual(words(applyTemplate(old, "band", "x")), ["MỚI", "Tiêu đề cũ"]);
const oldLabel = strip(typed(applyTemplate(doc, "two-lines", "x"), ["HOT", "Tiêu đề cũ"]));
const relaid = applyTemplate(oldLabel, "two-lines", "x");
assert.deepEqual(words(relaid), ["HOT", "Tiêu đề cũ"], "an old typed label is the label");
assert.deepEqual(relaid.texts.map((text) => text.role), ["label", "title"], "and the result now has roles");
const lone = strip({ ...doc, texts: [twice.texts[0]] });
assert.deepEqual(words(applyTemplate(lone, "band", "Tên video")), ["MỚI", "Tên video"], "a MỚI-looking old text is never the title, and is kept");
assert.deepEqual(words(applyTemplate(lone, "two-lines", "Tên video")), ["MỚI", "Tên video"]);
// Six texts: all kept; a fresh MỚI is left out rather than drop one; no room at all leaves the thumbnail as it is.
const five = ["một", "hai", "ba", "bốn", "năm"].map((text, index) => makeText(text, { size: 40 + index }));
const full = { ...doc, texts: [...five, applyTemplate(doc, "big-bottom", "Tên video").texts[0]] };
for (const template of THUMB_TEMPLATES) {
  const next = applyTemplate(full, template.id, "x");
  assert.deepEqual([...words(next)].sort(), [...words(full)].sort(), `${template.id} keeps all 6 texts`);
  assert.equal(next.texts.at(-1).text, "Tên video");
  assert.deepEqual(next.texts.slice(0, 5), five, `${template.id}: the added texts are as they were`);
}
const crowded = { ...doc, texts: [...five, { ...makeText("sáu"), role: "label" }] };
assert.equal(applyTemplate(crowded, "two-lines", "x"), crowded, "no room for a title beside a typed label: the thumbnail stays as it is");
// Texts typed before any layout: the largest becomes the title.
const plain = applyTemplate({ ...doc, texts: [...five, makeText("sáu", { size: 50 })] }, "big-bottom", "x");
assert.deepEqual([...words(plain)].sort(), ["ba", "bốn", "hai", "một", "năm", "sáu"].sort());
assert.equal(plain.texts.at(-1).text, "sáu");
assert.deepEqual(words(applyTemplate({ ...doc, texts: [makeText("   "), makeText("cũ")] }, "band", "x")), ["cũ"], "blank texts are not kept");
assert.equal(applyTemplate(doc, "nope", "x"), doc);
assert.equal(new Set(THUMB_TEMPLATES.map((template) => template.id)).size, THUMB_TEMPLATES.length);

// File name.
assert.equal(thumbName("Phim hay.mp4"), "Phim hay - thumbnail");
assert.equal(thumbName(""), "video - thumbnail");

// The view itself (#923): mounted on a small fake DOM whose canvas records what is drawn, with the same glyph model.
// It checks the drawn composition, dragging from the drawn place, and that the saved image is that composition without the frame.
class FakeElement {
  constructor(id) { this.id = id; this.value = ""; this.checked = false; this.hidden = false; this.disabled = false; this.open = false; this.textContent = ""; this.title = ""; this.listeners = {}; this.options = []; }
  addEventListener(type, listener) { (this.listeners[type] ??= []).push(listener); }
  fire(type, event = {}) { for (const listener of this.listeners[type] ?? []) listener({ preventDefault() {}, stopPropagation() {}, shiftKey: false, ...event }); }
  append(option) { this.options.push(option); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  focus() { globalThis.document.activeElement = this; }
  select() {}
}
class FakeContext {
  constructor() { this.ops = []; this.font = ""; this.fillStyle = ""; this.strokeStyle = ""; this.lineWidth = 1; this.stack = []; }
  size() { return Number(/(\d+(?:\.\d+)?)px/.exec(this.font)[1]); }
  measureText(line) { return { width: line.length * 0.62 * this.size() }; }
  save() {} restore() {} clearRect() {} setLineDash() {} drawImage() {}
  fillRect(x, y, width, height) { this.ops.push({ op: "fillRect", x, y, width, height, style: this.fillStyle }); }
  strokeRect(x, y, width, height) { this.ops.push({ op: "frame", x, y, width, height }); }
  fillText(line, x, y) { this.ops.push({ op: "fill", line, x, y, size: this.size() }); }
  strokeText(line, x, y) { this.ops.push({ op: "stroke", line, x, y, size: this.size(), lineWidth: this.lineWidth }); }
}
const elements = new Map();
const element = (selector) => {
  if (!elements.has(selector)) elements.set(selector, new FakeElement(selector));
  return elements.get(selector);
};
const canvasContext = new FakeContext();
const canvasElement = element("#thumb-canvas");
canvasElement.getContext = () => canvasContext;
canvasElement.getBoundingClientRect = () => ({ left: 0, top: 0, width: THUMB_WIDTH, height: THUMB_HEIGHT });
canvasElement.setPointerCapture = () => {};
let blobOps = null;
canvasElement.toBlob = (done, type) => {
  blobOps = { type, ops: canvasContext.ops.map((op) => ({ ...op })) };
  setTimeout(() => done({ arrayBuffer: async () => new ArrayBuffer(8) }), 0);
};
globalThis.document = { activeElement: null, querySelector: element, createElement: (tag) => new FakeElement(tag) };
globalThis.Option = class { constructor(label, value) { this.label = label; this.value = value; } };
const { mountThumbnail } = await import("../src/features/edit/thumbnail_view.ts");
const savedFiles = [];
let changes = 0;
const view = mountThumbnail(async () => { throw new Error("no host calls expected"); }, {
  context: () => ({ name: `${longTitle} TRUNG QUỐC.mp4`, frame: null, folder: "D:\\out" }),
  changed: () => { changes += 1; },
  save: async (bytes, folder, name, format) => { savedFiles.push({ bytes: bytes.byteLength, folder, name, format }); return `${folder}\\${name}.${format}`; },
});
view.begin(null);
const fresh = () => { canvasContext.ops = []; };
/** The drawn ink of each line: its centre, half width (with outline) and half height, from the recorded calls. */
const inks = () => canvasContext.ops.filter((op) => op.op === "fill").map((op) => {
  const outline = canvasContext.ops.find((other) => other.op === "stroke" && other.line === op.line && other.x === op.x && other.y === op.y);
  const half = op.line.length * 0.62 * op.size / 2 + (outline ? outline.lineWidth / 2 : 0);
  return { line: op.line, left: op.x - half, right: op.x + half, top: op.y - op.size * 0.575, bottom: op.y + op.size * 0.575, x: op.x, y: op.y };
});
const allInside = (list) => list.every((ink) => ink.left >= -1e-6 && ink.right <= THUMB_WIDTH + 1e-6 && ink.top >= -1e-6 && ink.bottom <= THUMB_HEIGHT + 1e-6);
const templateSelect = element("#thumb-template");
for (const template of THUMB_TEMPLATES) {
  view.begin(null);
  fresh();
  templateSelect.value = template.id;
  templateSelect.fire("change");
  const drawn = inks();
  const title = `${longTitle} TRUNG QUỐC`;
  assert.ok(drawn.map((ink) => ink.line).join(" ").includes(title), `${template.id}: the whole title is drawn (${drawn.map((ink) => ink.line).join(" | ")})`);
  assert.ok(allInside(drawn), `${template.id}: every line drawn inside the image ${JSON.stringify(drawn)}`);
}
// Dragging a text that the drawing moved in: the first small drag moves it by the same distance and it stays under the pointer.
const at = (x, y) => ({ clientX: x, clientY: y, pointerId: 1 });
const dragCheck = (saved, dx, dy, label) => {
  fresh();
  view.begin({ ...emptyThumb(), texts: [saved] });
  const before = inks()[0];
  assert.ok(before, `${label}: drawn`);
  const pointer = { x: before.x, y: before.y };
  canvasElement.fire("pointerdown", at(pointer.x, pointer.y));
  fresh();
  canvasElement.fire("pointermove", at(pointer.x + dx, pointer.y + dy));
  const after = inks()[0];
  assert.ok(Math.abs(after.x - before.x - dx) < 1e-6 && Math.abs(after.y - before.y - dy) < 1e-6, `${label}: the first drag moves the drawn text by (${dx}, ${dy}): ${JSON.stringify({ before, after })}`);
  assert.ok(pointer.x + dx >= after.left && pointer.x + dx <= after.right && pointer.y + dy >= after.top && pointer.y + dy <= after.bottom, `${label}: still under the pointer`);
  canvasElement.fire("pointerup", at(pointer.x + dx, pointer.y + dy));
  return after;
};
const word = (over) => ({ ...makeText("NGHIÊNG", over) });
dragCheck(word({ x: 0, y: 0.5 }), 10, 0, "moved in from the left edge, dragged right");
dragCheck(word({ x: 1, y: 0.5 }), -10, 0, "moved in from the right edge, dragged left");
dragCheck(word({ x: 0.5, y: 1 }), 0, -10, "moved up from the bottom, dragged up");
dragCheck(word({ x: 0.5, y: 0 }), 0, 10, "moved down from the top, dragged down");
dragCheck(word({ x: 0, y: 1 }), 12, -12, "moved in from a corner, dragged out of it");
// Arrow keys too: one press moves the drawn text one step.
fresh();
view.begin({ ...emptyThumb(), texts: [word({ x: 0, y: 0.5 })] });
const keyBefore = inks()[0];
canvasElement.fire("pointerdown", at(keyBefore.x, keyBefore.y));
canvasElement.fire("pointerup", at(keyBefore.x, keyBefore.y));
fresh();
canvasElement.fire("keydown", { key: "ArrowRight" });
assert.ok(Math.abs(inks()[0].x - keyBefore.x - 8) < 1e-6, "ArrowRight moves the drawn text 8 px at the first press");
// A drag is measured from where the text was grabbed: going out and back leaves it where it was, even if it was
// re-wrapped and lifted off the bottom on the way, and the same pointer place gives the same result however it got there.
const titleOf = () => view.document().texts.find((text) => text.role === "title");
view.begin(null);
templateSelect.value = "two-lines";
templateSelect.fire("change");
const grabbedTitle = titleOf();
const grabbedInk = inks().filter((ink) => grabbedTitle.text.includes(ink.line));
const grip = { x: grabbedInk[0].x, y: grabbedInk[0].y };
canvasElement.fire("pointerdown", at(grip.x, grip.y));
let lifted = false;
for (let step = 1; step <= 40; step += 1) {
  fresh();
  canvasElement.fire("pointermove", at(grip.x - step * 10, grip.y));
  lifted ||= inks().some((ink) => ink.y < grip.y - 1);
}
for (let step = 39; step >= 0; step -= 1) canvasElement.fire("pointermove", at(grip.x - step * 10, grip.y));
canvasElement.fire("pointerup", at(grip.x, grip.y));
assert.ok(lifted, "on the way it was re-wrapped and lifted off the bottom");
assert.ok(Math.abs(titleOf().x - grabbedTitle.x) < 1e-9 && Math.abs(titleOf().y - grabbedTitle.y) < 1e-9, `out and back: where it was ${JSON.stringify([titleOf().x, titleOf().y, grabbedTitle.x, grabbedTitle.y])}`);
const endAfter = (steps) => {
  fresh();
  view.begin({ ...emptyThumb(), texts: [makeText("Trường học ở Bắc Kinh", { x: 0.2, y: 0.5 })] });
  const start = inks()[0];
  canvasElement.fire("pointerdown", at(start.x, start.y));
  for (let step = 1; step <= steps; step += 1) canvasElement.fire("pointermove", at(start.x - 400 * step / steps, start.y));
  canvasElement.fire("pointerup", at(start.x - 400, start.y));
  fresh();
  view.begin(view.document());
  const { x, y } = view.document().texts[0];
  return { x, y, lines: inks().map((ink) => ink.line) };
};
assert.deepEqual(endAfter(1), endAfter(40), "one long step or forty short ones: the same place and lines");
// Arrow keys sideways never keep an upward shift: 12 presses left and 12 right leave the title where it was.
view.begin(null);
templateSelect.value = "big-bottom";
templateSelect.fire("change");
element("#thumb-text").value = "Tháp nuôi lợn 26 tầng ở Hồ Bắc";
element("#thumb-text").fire("input");
const keyTitle = titleOf();
assert.equal(keyTitle.text, "Tháp nuôi lợn 26 tầng ở Hồ Bắc");
for (let press = 0; press < 12; press += 1) canvasElement.fire("keydown", { key: "ArrowLeft", shiftKey: true });
for (let press = 0; press < 12; press += 1) canvasElement.fire("keydown", { key: "ArrowRight", shiftKey: true });
assert.equal(titleOf().y, keyTitle.y, "sideways keys: the height is untouched");
assert.ok(Math.abs(titleOf().x - keyTitle.x) < 1e-9, "and it is back where it was");
// Out and back in several strokes, or with a hand's jitter, leaves a title held at the bottom where it was.
const strokes = (layout, moves) => {
  fresh();
  view.begin(null);
  templateSelect.value = layout;
  templateSelect.fire("change");
  const first = titleOf();
  const firstInk = inks();
  for (const [dx, dy] of moves) {
    const ink = inks().find((line) => first.text.includes(line.line));
    canvasElement.fire("pointerdown", at(ink.x, ink.y));
    for (let step = 1; step <= 10; step += 1) canvasElement.fire("pointermove", at(ink.x + dx * step / 10, ink.y + (step % 2 ? dy : 0)));
    canvasElement.fire("pointerup", at(ink.x + dx, ink.y));
    fresh();
    view.begin(view.document());
  }
  return { first, last: titleOf(), firstInk: firstInk.map((ink) => [ink.line, ink.y]), lastInk: inks().map((ink) => [ink.line, ink.y]) };
};
for (const layout of ["big-bottom", "band", "two-lines"]) {
  const wobble = DRAG_START_PX - 1; // screen px; the fake stage is drawn at 1:1
  for (const moves of [[[100, 0], [-100, 0]], [[100, 0], [100, 0], [-100, 0], [-100, 0]], [[100, -wobble], [-100, wobble]]]) {
    const { first, last, firstInk, lastInk } = strokes(layout, moves);
    assert.equal(last.y, first.y, `${layout} ${JSON.stringify(moves)}: the title is still held at its height`);
    assert.ok(Math.abs(last.x - first.x) < 1e-9, `${layout} ${JSON.stringify(moves)}: and back across`);
    assert.deepEqual(lastInk, firstInk, `${layout} ${JSON.stringify(moves)}: drawn as before`);
  }
}
// Going up after a sideways stretch that re-wrapped and lifted the title: it moves up from where it is drawn, no jump.
{
  fresh();
  view.begin(null);
  templateSelect.value = "big-bottom";
  templateSelect.fire("change");
  const title = titleOf();
  const grip = inks().find((ink) => title.text.includes(ink.line));
  canvasElement.fire("pointerdown", at(grip.x, grip.y));
  for (let step = 1; step <= 20; step += 1) {
    fresh();
    canvasElement.fire("pointermove", at(grip.x + step * 10, grip.y));
  }
  const sideways = inks();
  assert.ok(sideways.length > 3 && titleOf().y === title.y, "re-wrapped and lifted, its saved height untouched");
  fresh();
  canvasElement.fire("pointermove", at(grip.x + 200, grip.y - 20));
  const up = inks();
  assert.ok(up.every((ink, index) => Math.abs(ink.y - (sideways[index].y - 20)) < 1e-6), `20 px up: every line 20 px up ${JSON.stringify([sideways.map((ink) => ink.y), up.map((ink) => ink.y)])}`);
  canvasElement.fire("pointerup", at(grip.x + 200, grip.y - 20));
}
// Opening a thumbnail only draws it: its saved place is not changed by the drawing.
const kept = word({ x: 0, y: 1 });
view.begin({ ...emptyThumb(), texts: [kept] });
const keptDoc = view.document();
assert.equal(keptDoc.texts[0].x, 0);
assert.equal(keptDoc.texts[0].y, 1);
// Saving: the image is the window's composition, without the selection frame.
view.begin(null);
templateSelect.value = "left-column";
templateSelect.fire("change");
element("#thumb-format").value = "png";
element("#thumb-format").fire("change");
const section = element("#thumb-section");
section.open = true;
fresh();
section.fire("toggle");
const windowDraw = canvasContext.ops;
assert.ok(windowDraw.some((op) => op.op === "frame"), "the window shows the selection frame");
assert.ok(allInside(inks()), "left-column, as shown: inside");
const shown = windowDraw.filter((op) => op.op !== "frame");
fresh();
element("#thumb-save").fire("click");
await new Promise((resolve) => setTimeout(resolve, 20));
assert.equal(blobOps.type, "image/png");
assert.ok(!blobOps.ops.some((op) => op.op === "frame"), "no selection frame in the saved image");
assert.deepEqual(blobOps.ops, shown, "the saved image draws exactly what the window shows");
assert.deepEqual(savedFiles.at(-1), { bytes: 8, folder: "D:\\out", name: `${longTitle} TRUNG QUỐC - thumbnail`, format: "png" });
assert.ok(changes > 0);

console.log("edit thumbnail tests passed");
