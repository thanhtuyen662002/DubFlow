import assert from "node:assert/strict";
import {
  createEdit, defaultStems, exportPlan, isUnchanged, matchesExport, parseProject, serializeProject, setStems, stemLevel, stemWeights,
} from "../src/features/edit/model.ts";

// ---------- Lớp âm thanh: levels ----------
const edit = createEdit(30, { path: "C:/out/final_vi.mp4" });
assert.equal(setStems(edit, null), edit, "off stays off");
const on = setStems(edit, defaultStems(true));
assert.deepEqual(on.mainStems, { voice: 1, original: 0, background: 1 });
assert.deepEqual(defaultStems(false), { voice: 1, original: 0.35, background: 1 }, "an unseparated original sound starts low");
assert.equal(isUnchanged(edit, 30), true);
assert.equal(isUnchanged(on, 30), false, "parts on is an edit to export");
const loud = setStems(on, { voice: 2.7, original: -1, background: 0.333 });
assert.deepEqual(loud.mainStems, { voice: 2, original: 0, background: 0.33 }, "0–200 %, hundredths");
assert.equal(setStems(loud, { ...loud.mainStems }), loud, "same levels, same edit");
assert.equal(setStems(loud, { voice: Number.NaN, original: 0, background: 1 }), loud, "not a number: unchanged");
assert.equal(setStems(loud, null).mainStems, undefined);

// The weights cancel the source's accompaniment: only the original voice keeps `original`.
assert.deepEqual(stemWeights({ voice: 1.5, original: 0.2, background: 1 }, true), { voice: 1.5, source: 0.2, spoken: 0, background: 0.8, music: 0, sfx: 0 });
assert.deepEqual(stemWeights({ voice: 0, original: 1, background: 0 }, true), { voice: 0, source: 1, spoken: 0, background: -1, music: 0, sfx: 0 });
assert.deepEqual(stemWeights({ voice: 1, original: 0.35, background: 1 }, false), { voice: 1, source: 0.35, spoken: 0, background: 0, music: 0, sfx: 0 });
// Split into music and SFX: each cancels its share of the source; SFX follows the music until set.
assert.deepEqual(stemWeights({ voice: 0, original: 0, background: 1, sfx: 0.5 }, true, true), { voice: 0, source: 0, spoken: 0, background: 0, music: 1, sfx: 0.5 });
assert.deepEqual(stemWeights({ voice: 0, original: 1, background: 0 }, true, true), { voice: 0, source: 1, spoken: 0, background: 0, music: -1, sfx: -1 });
// With the original voice on its own (voice.wav), each part plays at its own level: nothing cancels, so the
// preview's separate players cannot leave the music in "Bỏ nhạc" or double it in "Bỏ lời".
assert.deepEqual(stemWeights({ voice: 0, original: 1, background: 0, sfx: 1 }, true, true, true), { voice: 0, source: 0, spoken: 1, background: 0, music: 0, sfx: 1 }, "Bỏ nhạc");
assert.deepEqual(stemWeights({ voice: 0, original: 0, background: 1, sfx: 1 }, true, true, true), { voice: 0, source: 0, spoken: 0, background: 0, music: 1, sfx: 1 }, "Bỏ lời");
assert.deepEqual(stemWeights({ voice: 1, original: 0.2, background: 0.6 }, true, false, true), { voice: 1, source: 0, spoken: 0.2, background: 0.6, music: 0, sfx: 0 });
for (const levels of [{ voice: 0, original: 1, background: 0, sfx: 1 }, { voice: 0.5, original: 0.3, background: 1.4, sfx: 0.2 }]) {
  // Same sound either way, as voice = source − music − sfx.
  const direct = stemWeights(levels, true, true, true), cancel = stemWeights(levels, true, true);
  assert.ok(Math.abs((direct.music - direct.spoken) - cancel.music) < 1e-9 && Math.abs((direct.sfx - direct.spoken) - cancel.sfx) < 1e-9 && direct.spoken === cancel.source);
}
assert.equal(stemLevel({ voice: 0, original: 1, background: 0.4 }, "sfx"), 0.4);
assert.equal(stemLevel({ voice: 0, original: 1, background: 0.4, sfx: 0 }, "sfx"), 0);

// ---------- Export and drafts ----------
const plan = exportPlan(loud);
assert.deepEqual(plan.stems, { voice: 2, original: 0, background: 0.33 });
assert.equal(exportPlan(edit).stems, undefined, "off: nothing sent");
const exported = { segments: plan.segments, main_volume: 1, stems: plan.stems };
assert.equal(matchesExport(loud, exported), true);
assert.equal(matchesExport(edit, exported), false, "an export with parts is not the plain edit");
assert.equal(matchesExport(setStems(loud, { voice: 1, original: 0, background: 0.33 }), exported), false, "other levels");
assert.equal(matchesExport(edit, { segments: plan.segments, main_volume: 1 }), true);

const saved = serializeProject(loud, 3, null);
assert.deepEqual(saved.main_stems, { voice: 2, original: 0, background: 0.33 });
const back = parseProject(JSON.parse(JSON.stringify(saved)), 30, { path: "C:/out/final_vi.mp4" });
assert.deepEqual(back?.edit.mainStems, loud.mainStems);
assert.equal("main_stems" in serializeProject(edit, 0, null), false);
const broken = parseProject({ ...JSON.parse(JSON.stringify(saved)), main_stems: { voice: "x" } }, 30, { path: "C:/out/final_vi.mp4" });
assert.equal(broken?.edit.mainStems, undefined, "a bad entry is dropped");
// SFX is saved and read back only when set on its own.
const withSfx = setStems(loud, { ...loud.mainStems, sfx: 0.5 });
assert.equal(withSfx.mainStems.sfx, 0.5);
const savedSfx = serializeProject(withSfx, 3, null);
assert.deepEqual(parseProject(JSON.parse(JSON.stringify(savedSfx)), 30, { path: "C:/out/final_vi.mp4" })?.edit.mainStems, withSfx.mainStems);
assert.equal(setStems(loud, { ...loud.mainStems, sfx: Number.NaN }), loud);
assert.equal(matchesExport(withSfx, exported), false, "another SFX level is another export");
console.log("edit_stems tests passed");

// ---------- Browser preview: music/SFX paths sit under .dubflow-work\edit\stems ----------
{
  const { previewInvoke } = await import("../src/shell/preview_invoke.ts");
  const outputPath = "D:\\Videos\\DubFlow Output\\run\\final_vi.mp4";
  await previewInvoke("edit_split", { outputPath });
  const now = Date.now;
  Date.now = () => now() + 60_000;
  try {
    const stems = await previewInvoke("edit_stems", { outputPath, kind: "job" });
    assert.equal(stems.music, "D:\\Videos\\DubFlow Output\\run\\.dubflow-work\\edit\\stems\\music.wav");
    assert.equal(stems.sfx, "D:\\Videos\\DubFlow Output\\run\\.dubflow-work\\edit\\stems\\sfx.wav");
  } finally {
    Date.now = now;
  }
}

// ---------- Timeline: a finished separation shows its lanes, with their sound drawn ----------
{
  const { readFileSync } = await import("node:fs");
  const view = readFileSync(new URL("../src/features/edit/view.ts", import.meta.url), "utf8");
  assert.match(view, /if \(lanes !== stemLaneKey\) renderTimeline\(\);/, "new Lời/Nhạc/SFX lanes redraw the timeline");
  const host = readFileSync(new URL("../src-tauri/src/edit.rs", import.meta.url), "utf8");
  assert.match(host, /fn edit_audio_peaks[\s\S]{0,300}sfx::playable_audio/, "a stem lane's .wav gets its waveform");
}
