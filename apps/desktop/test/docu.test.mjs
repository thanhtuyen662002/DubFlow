import assert from "node:assert/strict";
import {
  CHAPTER_ORDER, acknowledge, applyFlags, buildRequest, chapterTargetSeconds, chapterWordBudget, flattenScenes, hasNarration,
  isEnglish, narrationStale, normalizeScript, parseCapcut, parseLabels, parseMcp, parseRender, parseVisual, planStale, parseNarration, parseOpen, parseRuns, parseVoices, quantityLines, renumberScenes, sourceLines,
} from "../src/features/docu/model.ts";
import { writeChapter, writeScript } from "../src/features/docu/ai.ts";
import { addText, createEdit } from "../src/features/edit/model.ts";
import { calloutTexts, parseMcpActivity, parseRenderOptions } from "../src/features/docu/model.ts";

const path = name => `D:\\Documentary\\${name}`;
const form = { title: "Three Gorges Dam", topic: "Engineering and community impact", targetMinutes: 11,
  cinematicFramingPreset: "letterbox_235", burnEndCredits: false, outputRoot: null,
  sources: [{ kind: "job", outputPath: path("job\\final_vi.mp4"), title: "Build notes" },
    { kind: "file", video: path("tour.mp4"), subtitles: path("tour.srt"), title: "Dam tour" }] };
assert.deepEqual(buildRequest(form), { ...form, title: form.title, targetMinutes: 11 });
for (const change of [{ title: "" }, { title: "x".repeat(121) }, { topic: "x".repeat(601) }, { topic: "x\u0001" },
  { targetMinutes: 2 }, { targetMinutes: 21 }, { targetMinutes: 3.5 }, { cinematicFramingPreset: "bad" },
  { sources: [] }, { sources: [form.sources[0]] }, { sources: [...form.sources, ...form.sources, ...form.sources, ...form.sources] },
  { sources: [{ ...form.sources[1], subtitles: path("captions.ass") }, form.sources[0]] },
  { sources: [{ ...form.sources[0], outputPath: "relative/final_vi.mp4" }, form.sources[1]] },
  { sources: [{ ...form.sources[0], title: "" }, form.sources[1]] }]) {
  assert.equal(typeof buildRequest({ ...form, ...change }), "string");
}
assert.equal(typeof buildRequest({ ...form, sources: [form.sources[0], form.sources[1]] }), "object");
// A plain video without subtitles is a source too: its speech is recognised when the project is created.
assert.deepEqual(buildRequest({ ...form, sources: [form.sources[0], { ...form.sources[1], subtitles: undefined }] }).sources[1],
  { kind: "file", video: path("tour.mp4"), subtitles: null, title: "Dam tour" });

assert.deepEqual(CHAPTER_ORDER, ["HOOK", "PROBLEM", "ENGINEERING_SOLUTION", "CRITICAL_EVALUATION"]);
assert.deepEqual(CHAPTER_ORDER.map(chapter => chapterTargetSeconds(10, chapter)), [42, 120, 300, 138]);
assert.deepEqual(CHAPTER_ORDER.map(chapter => chapterWordBudget(10, chapter)), [105, 300, 750, 345]);

const contexts = [
  { source_id: "src_01", title: "Build notes", video_path: path("build.mp4"), duration: 100, segments: [
    { seg_id: "a", start: 0, end: 2, original_text: "第一段文字。", extracted_keywords: [], quantities: [] },
    { seg_id: "b", start: 2, end: 4, original_text: "第二段文字。", extracted_keywords: [], quantities: [] },
    { seg_id: "c", start: 4, end: 6, original_text: "第三段文字。", extracted_keywords: [], quantities: [] },
    { seg_id: "d", start: 6, end: 8, original_text: "第四段文字。", extracted_keywords: [], quantities: [] },
  ] },
  { source_id: "src_02", title: "Tour", video_path: path("tour.mp4"), duration: 100, segments: [
    { seg_id: "e", start: 0, end: 4, original_text: "Engineers inspect the turbines.", extracted_keywords: [], quantities: [
      { text: "800万立方米", dimension: "volume", si: 8_000_000, imperial: "282.5 million cubic feet", comparisons: ["about 7.6 times the volume of a large building"] },
    ] },
  ] },
];
const packed = sourceLines(contexts.slice(0, 1), 75).join("\n");
assert.ok(packed.length <= 75);
assert.equal((packed.match(/\[src_01:/g) ?? []).length, 1, "adjacent source lines merge into a time window");
assert.match(packed, /0\.0-8\.0/);

const sourceIds = new Set(["src_01", "src_02"]);
const normalized = normalizeScript({ project_title: " T ", target_Coordinates_3D: { lat: 30, lng: 110, location_name: "Yichang" }, chapters: [
  { chapter_type: "HOOK", scenes: [
    { narration_text: " A supported sentence. ", matched_source_refs: [
      { source_id: "src_01", start: 2, end: 3 }, { source_id: "unknown", start: 0, end: 1 },
      { source_id: "src_02", start: 4, end: 4 }, ...Array.from({ length: 5 }, (_, i) => ({ source_id: "src_02", start: i, end: i + 1 })),
    ], visual_search_tags: Array.from({ length: 10 }, (_, i) => `tag ${i}`), on_screen_graphic: null,
      ambient_sfx_tag: "unknown", recommended_bgm_mood: "unknown", needs_review: true, review_notes: ["Kiểm tra số liệu"], review_numbers: ["700"], acknowledged_numbers: [] },
    { narration_text: "" },
  ] },
]}, sourceIds);
assert.equal(normalized.project_title, "T");
assert.equal(normalized.chapters.length, 4);
assert.equal(normalized.chapters[0].scenes.length, 1);
const normalizedScene = normalized.chapters[0].scenes[0];
assert.equal(normalizedScene.scene_id, "sc_001");
assert.equal(normalizedScene.matched_source_refs.length, 6, "the engine's limit; the AI is asked for 1–4");
assert.equal(normalizedScene.visual_search_tags.length, 6);
assert.equal(normalizedScene.ambient_sfx_tag, null);
assert.equal("recommended_bgm_mood" in normalizedScene, false, "moods belong to chapters");
assert.equal(normalized.chapters[0].recommended_bgm_mood, "cinematic_tension_drone", "an unknown mood falls back to the act's default");
assert.equal(normalizeScript({ chapters: [{ chapter_type: "HOOK", recommended_bgm_mood: "epic_build", scenes: [] }] }, sourceIds).chapters[0].recommended_bgm_mood, "epic_build");
assert.equal(normalizeScript({ chapters: [{ chapter_type: "HOOK", scenes: [{ narration_text: "x", on_screen_graphic: { type: "LABEL", title: "Turbine", value: "", subtext: "" } }] }] }, sourceIds)
  .chapters[0].scenes[0].on_screen_graphic.type, "LABEL");
assert.deepEqual(acknowledge(normalizedScene).acknowledged_numbers, ["700"]);
assert.deepEqual(flattenScenes(renumberScenes(normalized.chapters)).map(({ scene }) => scene.scene_id), ["sc_001"]);
assert.equal(parseRuns([{ dir: "D:/run", created: 1, spec: { schema_version: 1, sources: [] }, script: true }]).length, 1);
assert.equal(parseRuns([{ dir: "D:/run", created: 1, spec: { schema_version: 2, sources: [] } }]).length, 0);
assert.equal(parseOpen({ dir: "D:/run", spec: { schema_version: 1, sources: [] }, contexts, script: null }).script, null);

// Counts and years have no US conversion: no "= null" in the AI's facts.
assert.deepEqual(quantityLines([{ source_id: "src_01", segments: [{ start: 1, end: 2, quantities: [
  { text: "3000名", dimension: "count", si: 3000, imperial: null, comparisons: [] },
  { text: "289米", dimension: "length", si: 289, imperial: "948.2 feet", comparisons: ["about 3.1 times the height of the Statue of Liberty"] }] }] }]),
  ["[src_01:1.0-2.0] 3000名", "[src_01:1.0-2.0] 289米 = 948.2 feet; about 3.1 times the height of the Statue of Liberty"]);

// The checker drops empty scenes and renumbers; flags still land on the right scenes.
const editing = [{ chapter_type: "HOOK", scenes: [
  { scene_id: "sc_001", narration_text: "One.", needs_review: false, review_notes: [], review_numbers: [], acknowledged_numbers: [] },
  { scene_id: "sc_002", narration_text: "  ", needs_review: true, review_notes: ["old"], review_numbers: [], acknowledged_numbers: [] },
  { scene_id: "sc_003", narration_text: "It cost $34B.", needs_review: false, review_notes: [], review_numbers: [], acknowledged_numbers: [] }] }];
applyFlags(editing, [{ chapter_type: "HOOK", scenes: [
  { scene_id: "sc_001", needs_review: false, review_notes: [], review_numbers: [], acknowledged_numbers: [] },
  { scene_id: "sc_002", needs_review: true, review_notes: ["Số liệu “$34B” …"], review_numbers: ["$34B"], acknowledged_numbers: [] }] }]);
assert.deepEqual(editing[0].scenes.map(scene => [scene.scene_id, scene.needs_review, scene.review_numbers]),
  [["sc_001", false, []], ["sc_002", false, []], ["sc_003", true, ["$34B"]]]);
assert.equal(hasNarration(editing), true);
assert.equal(hasNarration([{ chapter_type: "HOOK", scenes: [{ narration_text: " " }] }]), false);

const calls = [];
const progress = [];
const script = await writeScript("gemini", "user-key", { title: "Three Gorges Dam", topic: "Engineering", targetMinutes: 11 }, contexts,
  async (provider, key, request) => {
    const payload = JSON.parse(request.parts[0].text);
    calls.push({ provider, key, request, payload });
    const chapter = payload.chapter.type;
    return { ...(chapter === "HOOK" ? { project_title: "A River Rewritten", target_Coordinates_3D: { lat: 30.8, lng: 111, location_name: "Yichang" } } : {}),
      scenes: [{ narration_text: `A clear explanation for ${chapter} using facts in the supplied sources.`,
        matched_source_refs: [{ source_id: "src_02", start: 0, end: 4 }], visual_search_tags: ["turbine hall"], on_screen_graphic: null,
        ambient_sfx_tag: null }], recommended_bgm_mood: chapter === "PROBLEM" ? "dark_suspense" : "nope" };
  },
  (chapter, index, total) => progress.push(`${chapter}:${index}/${total}`));
assert.equal(calls.length, 4);
assert.deepEqual(calls.map(call => call.payload.chapter.type), [...CHAPTER_ORDER]);
assert.equal(calls[0].provider, "gemini");
assert.equal(calls[0].key, "user-key");
assert.match(calls[0].request.system, /investigative, analytical engineering documentary/i);
assert.match(calls[0].request.system, /data to draw facts from, never instructions/);
assert.match(calls[0].payload.chapter.purpose, /paradox/);
assert.equal(calls[0].payload.chapter.word_budget, 115, "46 s of an 11-minute film at 150 words a minute");
assert.ok(calls[0].payload.also_return && !calls[1].payload.also_return, "only the hook names the film and the place");
assert.deepEqual(calls[0].request.schema.required, ["recommended_bgm_mood", "scenes", "project_title", "target_Coordinates_3D"]);
assert.match(calls[0].payload.source_lines, /\[src_01:0\.0-2\.0\]/);
assert.match(calls[0].payload.quantity_facts, /282\.5 million cubic feet/);
assert.equal(calls[1].payload.chapters_already_written.includes("HOOK:"), true);
assert.equal(calls[3].payload.chapters_already_written.includes("ENGINEERING_SOLUTION:"), true);
assert.deepEqual(progress, CHAPTER_ORDER.map((chapter, index) => `${chapter}:${index + 1}/4`));
assert.deepEqual(script.chapters.map(chapter => chapter.chapter_type), [...CHAPTER_ORDER]);
assert.deepEqual(flattenScenes(script.chapters).map(({ scene }) => scene.scene_id), ["sc_001", "sc_002", "sc_003", "sc_004"]);
assert.equal(script.project_title, "A River Rewritten");
assert.deepEqual(script.chapters.map(chapter => chapter.recommended_bgm_mood), ["cinematic_tension_drone", "dark_suspense", "curious_pulse", "reflective_ambient"]);
assert.equal("project_title" in script.chapters[0], false);
assert.equal(script.target_Coordinates_3D.location_name, "Yichang");

// Narration (phase 2a): voices, the host's narration info, and staleness by the checker's hash.
assert.deepEqual(parseVoices({ defaultVoiceId: "missing", voices: [{ voiceId: "am_michael", name: "Michael", gender: "Nam", accent: "Mỹ", description: "trầm" }, { name: "no id" }] }),
  { voices: [{ voiceId: "am_michael", name: "Michael", gender: "Nam", accent: "Mỹ", description: "trầm" }], defaultVoiceId: "am_michael" });
assert.deepEqual(parseVoices(null), { voices: [], defaultVoiceId: "" });
const info = parseNarration({ audio: "D:/run/narration.wav", status: { state: "COMPLETED", done: 5, total: 5, message: "ok" },
  timeline: { script_sha256: "abc", voice_id: "am_michael", speed: 1, duration: 24.1, scenes: [
    { scene_id: "sc_001", chapter_type: "HOOK", start: 0, end: 11.4, slot_end: 12.2, exact_duration_ms: 11400 }, { scene_id: 5 }] } });
assert.equal(info.timeline.scenes.length, 1);
assert.equal(info.status.state, "COMPLETED");
assert.deepEqual(parseNarration({ status: { state: "WEIRD" } }), { audio: null, timeline: null, status: null });
assert.equal(narrationStale({ chapters: [], script_sha256: "abc" }, info.timeline), false);
assert.equal(narrationStale({ chapters: [], script_sha256: "def" }, info.timeline), true);
assert.equal(narrationStale({ chapters: [] }, info.timeline), true, "an unsaved edit has no hash yet");
assert.equal(narrationStale({ chapters: [] }, null), false, "no narration is not stale");
assert.equal(parseOpen({ dir: "D:/run", spec: { schema_version: 1, sources: [] }, contexts, script: null, narration: { audio: null, timeline: null, status: null } }).narration.audio, null);

// English only (#444): requests say so, and a chapter answered in another language is asked once more, then refused.
assert.equal(isEnglish("Engineers poured 280 million cubic feet of concrete into the Baihetan canyon."), true);
assert.equal(isEnglish("Những nỗ lực tự cứu lấy mình đều thất bại."), false);
assert.equal(isEnglish("坝高289米"), false);
assert.equal(calls.every(call => call.request.language === "en"), true);
const vietnamese = { recommended_bgm_mood: "epic_build", scenes: [{ narration_text: "Những nỗ lực tự cứu lấy mình đều thất bại.",
  matched_source_refs: [], visual_search_tags: [], on_screen_graphic: null, ambient_sfx_tag: null }] };
const englishReply = { recommended_bgm_mood: "epic_build", scenes: [{ narration_text: "Every attempt to fix it failed.",
  matched_source_refs: [], visual_search_tags: [], on_screen_graphic: null, ambient_sfx_tag: null }] };
const retried = [];
const fixed = await writeChapter("gemini", "k", { project: { title: "T", topic: "", targetMinutes: 5 }, chapter: "PROBLEM", contexts, previous: [] },
  async (_provider, _key, request) => { retried.push(request); return retried.length === 1 ? vietnamese : englishReply; });
assert.equal(retried.length, 2);
assert.match(retried[1].parts.at(-1).text, /not in English/);
assert.equal(fixed.scenes[0].narration_text, "Every attempt to fix it failed.");
await assert.rejects(writeChapter("gemini", "k", { project: { title: "T", topic: "", targetMinutes: 5 }, chapter: "PROBLEM", contexts, previous: [] },
  async () => vietnamese), /tiếng Anh/);

// Visual plan (phase 2b-1): parsing and staleness against the script and its narration.
const visualInfo = parseVisual({ status: { state: "COMPLETED", done: 5, total: 5, message: "ok" }, plan: { script_sha256: "abc", duration: 24.1, warnings: ["w", 5],
  scenes: [{ scene_id: "sc_001", start: 0, end: 12.3, warnings: [], slices: [{ source_id: "src_01", start: 10, end: 14.1, at: 0, duration: 4.1, tier: 0 }, { source_id: 3 }] }, { start: 1 }] } });
assert.equal(visualInfo.plan.scenes.length, 1);
assert.equal(visualInfo.plan.scenes[0].slices.length, 1);
assert.deepEqual(visualInfo.plan.warnings, ["w"]);
assert.deepEqual(parseVisual(null), { plan: null, status: null });
const timed = { script_sha256: "abc", voice_id: "am_michael", speed: 1, duration: 24.1, scenes: [] };
assert.equal(planStale({ chapters: [], script_sha256: "abc" }, timed, visualInfo.plan), false);
assert.equal(planStale({ chapters: [], script_sha256: "new" }, timed, visualInfo.plan), true, "the script changed");
assert.equal(planStale({ chapters: [], script_sha256: "abc" }, { ...timed, duration: 30 }, visualInfo.plan), true, "the narration was remade");
assert.equal(planStale({ chapters: [], script_sha256: "abc" }, null, visualInfo.plan), true);
assert.equal(planStale({ chapters: [] }, timed, null), false, "no plan is not stale");

// Rough cut (phase 2b-2).
const defaultOptions = { graphics: true, captions: false, music: "generated", music_path: null };
assert.deepEqual(parseRender({ video: "D:/run/documentary.mp4", status: { state: "COMPLETED", done: 6, total: 6, message: "ok" } }),
  { video: "D:/run/documentary.mp4", status: { state: "COMPLETED", done: 6, total: 6, message: "ok" }, options: defaultOptions });
assert.deepEqual(parseRender({ video: 5, status: { state: "?" } }), { video: null, status: null, options: defaultOptions });
// What the last render burned in, and the options for the next one.
const burned = parseRender({ video: "D:/run/documentary.mp4", status: { state: "COMPLETED", done: 6, total: 6, message: "ok", overlays: { graphics: true, captions: false, music: "generated" } },
  options: { schema_version: 1, graphics: false, captions: true, music: "file", music_path: "D:/music/bed.mp3" } });
assert.deepEqual(burned.status.overlays, { graphics: true, captions: false, music: "generated" });
assert.deepEqual(burned.options, { graphics: false, captions: true, music: "file", music_path: "D:/music/bed.mp3" });
assert.deepEqual(parseRenderOptions({ music: "file" }), defaultOptions, "a file choice without a file falls back to the generated bed");
assert.equal(parseOpen({ dir: "D:/run", spec: { schema_version: 1, sources: [] }, contexts, script: null }).render.video, null);

// Editor (phase 3a): the script's graphics become Chữ texts at their scene's narrated time.
const withGraphics = { project_title: "T", target_Coordinates_3D: null, script_sha256: "abc", chapters: [{ chapter_type: "HOOK", scenes: [
  { scene_id: "sc_001", narration_text: "One.", matched_source_refs: [], visual_search_tags: [], ambient_sfx_tag: null, needs_review: false, review_notes: [], review_numbers: [], acknowledged_numbers: [],
    on_screen_graphic: { type: "METRIC_CALLOUT", title: "TOTAL CONCRETE", value: "280 Million Cubic Feet", subtext: "About 7.6 Empire State Buildings" } },
  { scene_id: "sc_002", narration_text: "Two.", matched_source_refs: [], visual_search_tags: [], ambient_sfx_tag: null, needs_review: false, review_notes: [], review_numbers: [], acknowledged_numbers: [],
    on_screen_graphic: { type: "LOWER_THIRD", title: "Baihetan Dam", value: "", subtext: "Sichuan–Yunnan border" } },
  { scene_id: "sc_003", narration_text: "Three.", matched_source_refs: [], visual_search_tags: [], ambient_sfx_tag: null, needs_review: false, review_notes: [], review_numbers: [], acknowledged_numbers: [], on_screen_graphic: null }] }] };
const timedScenes = { ...timed, duration: 20, scenes: [
  { scene_id: "sc_001", chapter_type: "HOOK", start: 0, end: 7.5, slot_end: 8.3, exact_duration_ms: 7500 },
  { scene_id: "sc_002", chapter_type: "HOOK", start: 8.3, end: 10, slot_end: 10.8, exact_duration_ms: 1700 }] };
const callouts = calloutTexts(withGraphics, timedScenes);
assert.deepEqual(callouts.map(c => [c.at, Math.round(c.seconds * 10) / 10, c.text]), [
  [0.3, 5, "280 Million Cubic Feet\nTOTAL CONCRETE\nAbout 7.6 Empire State Buildings"], [8.6, 2.2, "Baihetan Dam\nSichuan–Yunnan border"]]);
assert.equal(callouts[0].look.box, "#0F172A");
assert.equal(callouts[1].look.animation, "slide-up");
assert.deepEqual(calloutTexts(withGraphics, null), []);
// A rough cut that burned the number cards already: the editor's first draft does not add them again.
assert.deepEqual(calloutTexts(withGraphics, timedScenes, [], { graphics: false }), []);
const draft = callouts.reduce((edit, c) => addText(edit, c.at, c.text, c.look, c.seconds), createEdit(20, { path: "D:/run/documentary.mp4", title: "T", width: 1920, height: 1080, fps: 30, hasAudio: true }));
assert.equal(draft.texts.length, 2);
assert.equal(draft.texts[0].box_opacity, 0.9);
assert.equal(draft.texts[1].at, 8.6);

// CapCut export (phase 4a).
assert.deepEqual(parseCapcut({ folder: "C:/x/DubFlow - T", name: "DubFlow - T", clips: 3, duration: 13.1, captions: "D:/run/captions_en.srt" }),
  { folder: "C:/x/DubFlow - T", name: "DubFlow - T", clips: 3, duration: 13.1, captions: "D:/run/captions_en.srt" });
assert.equal(parseCapcut({ name: "x" }), null);
assert.equal(parseOpen({ dir: "D:/run", spec: { schema_version: 1, sources: [] }, contexts, script: null }).capcut, null);

// MCP (phase 4b): labels join the editor's first draft; the connection commands parse.
const mcpLabels = parseLabels({ labels: [{ id: "l001", scene_id: "sc_002", text: "Turbine hall", x: 0.5, y: 0.4, w: 0.3 }, { text: 5 }] });
assert.equal(mcpLabels.length, 1);
const withLabels = calloutTexts(withGraphics, timedScenes, mcpLabels);
assert.equal(withLabels.length, 3);
assert.deepEqual([withLabels[2].at, withLabels[2].text, withLabels[2].look.x, withLabels[2].look.w], [8.3, "Turbine hall", 0.5, 0.3]);
assert.equal(parseMcp({ claudeCode: "claude mcp add dubflow", claudeDesktop: "{}" }).claudeCode, "claude mcp add dubflow");
assert.equal(parseMcp({}), null);

console.log("docu model and AI passed");

// What Claude does over MCP (control/mcp-activity.json from the MCP server).
const activity = parseMcpActivity({ schema_version: 1, updated: "2026-11-04T07:10:00Z",
  current: { tool: "narrate", label: "Tạo lời đọc (giọng am_michael)", project: "D:/run", started: "2026-11-04T07:09:00Z" },
  recent: [{ tool: "save_script", label: "Lưu kịch bản", project: "D:/run", started: "x", finished: "y", state: "done", message: "26 cảnh" }, { bad: true }] });
assert.equal(activity.current.project, "D:/run");
assert.deepEqual(activity.recent.map(item => [item.tool, item.state]), [["save_script", "done"]]);
assert.equal(parseMcpActivity(null), null);
assert.equal(parseMcpActivity({ updated: "x", current: null, recent: [] }).current, null);
