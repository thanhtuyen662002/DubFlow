import assert from "node:assert/strict";
import {
  addAudio, createEdit, exportPlan, isUnchanged, matchesExport, parseProject, serializeProject, setAudioClean, setMainClean,
} from "../src/features/edit/model.ts";

const info = { path: "D:\\a.mp4", width: 1920, height: 1080 };
const base = createEdit(100, info);
const roundTrip = (edit) => parseProject(JSON.parse(JSON.stringify(serializeProject(edit, 1, null))), 100, info).edit;
const exported = (edit) => { const plan = exportPlan(edit); return { ...plan, main_volume: plan.mainVolume, main_denoise: plan.mainDenoise, main_eq: plan.mainEq }; };

// ---------- Lọc ồn / chỉnh âm of an added sound ----------
const voice = addAudio(base, { source: { kind: "file", path: "D:\\thu-am\\giong-1.m4a" }, title: "giong-1", duration: 12 }, 5);
const id = voice.audio[0].id;
const cleaned = setAudioClean(voice, id, { denoise: true, eq: "voice" });
assert.deepEqual([cleaned.audio[0].denoise, cleaned.audio[0].eq], [true, "voice"]);
assert.equal(setAudioClean(cleaned, id, { denoise: true }), cleaned, "nothing changed");
assert.equal(setAudioClean(cleaned, id, { eq: "loud" }), cleaned, "unknown presets are ignored");
const plain = setAudioClean(cleaned, id, { denoise: false, eq: null });
assert.equal("denoise" in plain.audio[0] || "eq" in plain.audio[0], false, "off leaves the keys out");
assert.deepEqual([exportPlan(cleaned).audio[0].denoise, exportPlan(cleaned).audio[0].eq], [true, "voice"]);
assert.equal("denoise" in exportPlan(voice).audio[0], false);
assert.equal(matchesExport(cleaned, exported(voice)), false);
assert.equal(matchesExport(cleaned, exported(cleaned)), true);
assert.deepEqual([roundTrip(cleaned).audio[0].denoise, roundTrip(cleaned).audio[0].eq], [true, "voice"]);

// ---------- The output's own sound ----------
const main = setMainClean(base, { denoise: true, eq: "warm" });
assert.deepEqual([main.mainDenoise, main.mainEq], [true, "warm"]);
assert.equal(isUnchanged(base, 100), true);
assert.equal(isUnchanged(main, 100), false, "a cleaned sound is something to export");
assert.deepEqual([exportPlan(main).mainDenoise, exportPlan(main).mainEq], [true, "warm"]);
assert.equal("mainDenoise" in exportPlan(base), false);
assert.equal(matchesExport(main, exported(base)), false);
assert.equal(matchesExport(main, exported(main)), true);
assert.deepEqual([roundTrip(main).mainDenoise, roundTrip(main).mainEq], [true, "warm"]);
const off = setMainClean(main, { denoise: false, eq: null });
assert.equal("mainDenoise" in off || "mainEq" in off, false);
assert.equal(setMainClean(base, { eq: "loud" }), base);

console.log("edit_voice: ok");
