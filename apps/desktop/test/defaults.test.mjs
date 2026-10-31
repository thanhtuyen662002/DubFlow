import assert from "node:assert/strict";
import { FIRST_RUN_DEFAULTS, readJobDefaults, serializeJobDefaults } from "../src/features/queue/defaults.ts";

const VOICE = "vi-truc-ly-vieneu3-v1";
const REGION = { x: 0.1, y: 0.8, w: 0.8, h: 0.1 };

// First run: dubbed, subtitled, background kept and source captions covered.
assert.deepEqual(FIRST_RUN_DEFAULTS, {
  dubbing: true, voiceId: null, burnIn: true, blurAuto: true, separateBackground: true, multiVoice: false,
});
assert.equal(readJobDefaults(null, null), null);
assert.equal(readJobDefaults("not json", "{"), null);
assert.equal(readJobDefaults(JSON.stringify({ voiceId: VOICE }), null), null);

// v1 omitted false flags: missing blurAuto / separateBackground now mean "on".
assert.deepEqual(readJobDefaults(null, JSON.stringify({ dubbing: true, voiceId: VOICE, burnIn: true })), {
  dubbing: true, voiceId: VOICE, burnIn: true, blurAuto: true, separateBackground: true, multiVoice: false,
});
// ...but saved manual regions keep the manual choice instead of automatic blur.
assert.deepEqual(readJobDefaults(null, JSON.stringify({ dubbing: false, voiceId: null, burnIn: false, blurRegions: [REGION], multiVoice: true })), {
  dubbing: false, voiceId: null, burnIn: false, blurAuto: false, separateBackground: true, multiVoice: true, blurRegions: [REGION],
});
// v1 keeps its explicit language and Hachimi only with a Chinese source.
assert.deepEqual(readJobDefaults(null, JSON.stringify({ dubbing: true, voiceId: null, burnIn: true, sourceLanguage: "zh", translationEngine: "hachimi", blurAuto: true })), {
  dubbing: true, voiceId: null, burnIn: true, blurAuto: true, separateBackground: true, multiVoice: false, sourceLanguage: "zh", translationEngine: "hachimi",
});
assert.equal(readJobDefaults(null, JSON.stringify({ dubbing: true, voiceId: null, burnIn: true, translationEngine: "hachimi" })).translationEngine, undefined);

// v2 is explicit: a stored false stays false, and v2 wins over v1.
const v2 = serializeJobDefaults({ dubbing: { enabled: true, voiceId: VOICE }, burnIn: true });
assert.deepEqual(JSON.parse(v2), { dubbing: true, voiceId: VOICE, burnIn: true, blurAuto: false, separateBackground: false, multiVoice: false });
assert.deepEqual(readJobDefaults(v2, JSON.stringify({ dubbing: false, voiceId: null, burnIn: false })), JSON.parse(v2));
// A v2 record with missing flags (hand-edited or older build) does not turn them on.
assert.equal(readJobDefaults(JSON.stringify({ dubbing: true, voiceId: null, burnIn: true }), null).separateBackground, false);
// An unreadable v2 falls back to the legacy record.
assert.equal(readJobDefaults("{", JSON.stringify({ dubbing: true, voiceId: null, burnIn: true })).blurAuto, true);

// Round trip of a fully configured job.
const full = { dubbing: { enabled: true, voiceId: VOICE }, burnIn: false, blurAuto: true, separateBackground: true, multiVoice: true,
  sourceLanguage: "zh", translationEngine: "hachimi" };
assert.deepEqual(readJobDefaults(serializeJobDefaults(full), null), {
  dubbing: true, voiceId: VOICE, burnIn: false, blurAuto: true, separateBackground: true, multiVoice: true, sourceLanguage: "zh", translationEngine: "hachimi",
});
const manual = readJobDefaults(serializeJobDefaults({ dubbing: { enabled: false, voiceId: null }, blurRegions: [REGION] }), null);
assert.deepEqual(manual.blurRegions, [REGION]);
assert.equal(manual.blurAuto, false);
// Hachimi without a Chinese source is never stored.
assert.equal(JSON.parse(serializeJobDefaults({ dubbing: { enabled: true, voiceId: null }, translationEngine: "hachimi" })).translationEngine, undefined);

console.log("desktop defaults tests passed");
