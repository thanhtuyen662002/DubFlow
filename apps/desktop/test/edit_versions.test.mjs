import assert from "node:assert/strict";
import {
  addAudio, addOverlay, addText, applyLayers, createEdit, layersSummary, serializeLayers, setAudioClean, setMainClean, setMainVolume,
  setOverlayStyle,
} from "../src/features/edit/model.ts";

const info = (path) => ({ path, width: 1920, height: 1080 });
const logo = { path: "D:\\logo.png", title: "logo", duration: 3600, width: 512, height: 512, fps: 30, hasAudio: false, kind: "file" };

// ---------- A template of the layers of one video ----------
let source = createEdit(60, info("D:\\a.mp4"));
source = addText(source, 2, "Đăng ký kênh");
source = addText(source, 50, "Hẹn gặp lại");
source = addOverlay(source, logo, 0);
source = setOverlayStyle(source, source.overlays[0].id, { shape: "ellipse", border: 4 });
source = addAudio(source, { source: { kind: "sfx", id: "whoosh-01" }, title: "Vút", duration: 0.6 }, 1);
source = addAudio(source, { source: { kind: "file", path: "D:\\nhac.mp3" }, title: "nhạc", duration: 30 }, 45);
source = setAudioClean(source, source.audio[1].id, { eq: "warm" });
source = setMainClean(setMainVolume(source, 0.8), { denoise: true });
const layers = JSON.parse(JSON.stringify(serializeLayers(source)));
assert.equal(layers.schema, 1);
assert.equal(layers.overlays[0].media.path, "D:\\logo.png");
assert.equal(layersSummary(layers), "2 chữ · 1 lớp chồng · 2 âm thanh");
assert.equal(layersSummary({ schema: 1 }), "Chỉ cài đặt âm thanh");

// ---------- Laid over a shorter video ----------
let target = createEdit(30, info("D:\\b.mp4"));
target = addText(target, 0, "Có sẵn");
const { edit, added, skipped } = applyLayers(target, layers);
assert.equal(added, 3, "the text at 50 s and the music at 45 s start after the end");
assert.equal(skipped, 2);
assert.deepEqual(edit.texts.map((text) => text.text), ["Có sẵn", "Đăng ký kênh"]);
assert.equal(new Set([...edit.texts, ...edit.overlays, ...edit.audio].map((item) => item.id)).size, 4, "new ids (with the text it had)");
assert.equal(edit.overlays[0].shape, "ellipse");
assert.equal(edit.media.find((media) => media.id === edit.overlays[0].mediaId)?.path, "D:\\logo.png", "the logo's media is attached");
assert.equal(edit.audio[0].source.id, "whoosh-01");
assert.deepEqual([edit.mainVolume, edit.mainDenoise], [0.8, true]);
assert.ok(edit.nextId > target.nextId);

// Nothing to lay: unchanged.
assert.equal(applyLayers(target, { schema: 2 }).edit, target);
assert.equal(applyLayers(target, null).edit, target);
const longer = applyLayers(createEdit(120, info("D:\\c.mp4")), layers);
assert.equal(longer.added, 5);
assert.equal(longer.edit.audio[1].eq, "warm");

console.log("edit_versions: ok");
