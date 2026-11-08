// Vibi.pro voices (#632): the chosen voice, its parameters and where it is used.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  MEGA_PROJECTS, MEGA_RANGES, defaultSettings, dubbingVoice, loadChoice, normalizeSettings, parseVoicePage, saveChoice, settingsAdvice,
  settingsFor, settingsSummary, voiceTraits, withMegaProjects,
} from "../src/features/vibi/model.ts";

// Oscar's Mega Projects recipe is the ElevenLabs default and sits inside its own ranges.
const fresh = defaultSettings("elevenlabs", { voice_id: "VR6AewLTigWG4xSOukaG", name: "Arnold", preview_url: "https://x/a.mp3" });
assert.equal(fresh.model_id, "eleven_turbo_v2_5");
for (const key of ["stability", "similarity_boost", "style"]) {
  assert.ok(MEGA_RANGES[key][0] <= MEGA_PROJECTS[key] && MEGA_PROJECTS[key] <= MEGA_RANGES[key][1], key);
  assert.equal(fresh[key], MEGA_PROJECTS[key]);
}
assert.equal(defaultSettings("minimax").model_id, "speech-2.8-hd");

// Every value is clamped into its provider's range; a bad voice id is refused.
const wild = normalizeSettings({ provider: "elevenlabs", voice_id: "abc", stability: 4, similarity_boost: -1, style: 0.155555, speed: 3, use_speaker_boost: false });
assert.deepEqual([wild.stability, wild.similarity_boost, wild.style, wild.speed, wild.use_speaker_boost], [1, 0, 0.156, 1.2, false]);
const minimax = normalizeSettings({ provider: "minimax", voice_id: "English_ManWithDeepVoice", speed: 3, pitch: -20.4, vol: 50 });
assert.deepEqual([minimax.speed, minimax.pitch, minimax.vol], [2, -12, 10]);
for (const bad of [null, {}, { provider: "openai", voice_id: "a" }, { provider: "elevenlabs", voice_id: "../x" }, { provider: "elevenlabs", voice_id: "" }]) {
  assert.equal(normalizeSettings(bad), null);
}

// The preset only touches ElevenLabs settings; the language follows where the voice speaks.
const tuned = withMegaProjects({ ...fresh, stability: 0.9, model_id: "eleven_multilingual_v2" });
assert.equal(tuned.stability, 0.45);
assert.equal(tuned.model_id, "eleven_turbo_v2_5");
assert.deepEqual(withMegaProjects(minimax), minimax);
assert.equal(settingsFor(fresh, "documentary").language_code, "en");
assert.equal(settingsFor(fresh, "dubbing").language_code, "vi");
assert.equal(settingsFor(minimax, "dubbing").language_code, "Vietnamese");
assert.equal("preview_url" in settingsFor(fresh, "dubbing"), false);

assert.equal(settingsSummary(fresh), "Arnold · ElevenLabs Turbo v2.5 · ổn định 45 % · rõ 78 % · kịch tính 15 %");
assert.ok(settingsSummary(minimax).includes("MiniMax Speech 2.8 HD"));
assert.equal(settingsAdvice(fresh).length, 0);
assert.equal(settingsAdvice({ ...fresh, stability: 0.8, similarity_boost: 0.5, style: 0.5 }).length, 3);

// Stored choice: the switches never stay on without a voice; the dubbing gets the voice only when both are on.
const store = new Map();
const storage = { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) };
assert.deepEqual(loadChoice(storage), { settings: null, use: { dubbing: false, documentary: false } });
saveChoice({ settings: null, use: { dubbing: true, documentary: true } }, storage);
assert.deepEqual(loadChoice(storage).use, { dubbing: false, documentary: false });
saveChoice({ settings: fresh, use: { dubbing: true, documentary: false } }, storage);
const choice = loadChoice(storage);
assert.equal(choice.settings.voice_name, "Arnold");
assert.equal(dubbingVoice(choice, true).language_code, "vi");
assert.equal(dubbingVoice(choice, false), null);
assert.equal(dubbingVoice({ ...choice, use: { dubbing: false, documentary: true } }, true), null);

// Voice pages from the host: only HTTPS previews, bounded fields.
const page = parseVoicePage({ voices: [
  { provider: "elevenlabs", voice_id: "a", name: "Arnold", gender: "male", age: "middle aged", accent: "american", languages: ["en", "vi"], preview_url: "https://x/a.mp3" },
  { voice_id: "b", name: "B", preview_url: "http://x/b.mp3" }, { name: "no id" }], has_more: true, total: 40 });
assert.equal(page.voices.length, 2);
assert.equal(page.voices[1].preview_url, "");
assert.deepEqual([page.hasMore, page.total], [true, 40]);
assert.equal(voiceTraits(page.voices[0]), "Nam · trung niên · american · tiếng en, vi");

// The host and engine agree on the preset (engine/dubflow/tts/vibi.py).
const engine = readFileSync(new URL("../../../engine/dubflow/tts/vibi.py", import.meta.url), "utf8");
assert.ok(engine.includes(`"model_id": "${MEGA_PROJECTS.model_id}", "stability": ${MEGA_PROJECTS.stability}, "similarity_boost": ${MEGA_PROJECTS.similarity_boost}`));
console.log("vibi tests passed");
