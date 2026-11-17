// Thumbnail (Issue #901): the pure model of the editor's thumbnail maker.
import assert from "node:assert/strict";
import {
  THUMB_HEIGHT, THUMB_WIDTH, THUMB_TEMPLATES, applyTemplate, coverRect, dragPan, emptyThumb, hasThumb, makeText,
  moveText, normalizeThumb, textAt, textBox, thumbName, wrapLines,
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
// A thumbnail saved before layouts marked their texts: its largest text is the title, an untouched MỚI goes.
const strip = (thumb) => ({ ...thumb, texts: thumb.texts.map(({ role, ...text }) => text) });
const old = strip(typed(applyTemplate(doc, "two-lines", "x"), [undefined, "Tiêu đề cũ"]));
assert.deepEqual(words(applyTemplate(old, "two-lines", "x")), ["MỚI", "Tiêu đề cũ"]);
assert.deepEqual(words(applyTemplate(old, "band", "x")), ["Tiêu đề cũ"]);
assert.deepEqual(words(applyTemplate(strip({ ...doc, texts: [twice.texts[0]] }), "band", "Tên video")), ["Tên video"], "an untouched MỚI is never an old title");
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

console.log("edit thumbnail tests passed");
