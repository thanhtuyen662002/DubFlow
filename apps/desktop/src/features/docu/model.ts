import type { QueueJob } from "../queue/model.ts";
import type { TextLook } from "../edit/model.ts";

export const CHAPTER_ORDER = ["HOOK", "PROBLEM", "ENGINEERING_SOLUTION", "CRITICAL_EVALUATION"] as const;
export type ChapterType = typeof CHAPTER_ORDER[number];
export const CHAPTER_NAMES: Record<ChapterType, string> = {
  HOOK: "Mở đầu gây tò mò", PROBLEM: "Vấn đề", ENGINEERING_SOLUTION: "Giải pháp kỹ thuật", CRITICAL_EVALUATION: "Đánh giá",
};
export const CHAPTER_SHARES: Record<ChapterType, number> = {
  HOOK: 0.07, PROBLEM: 0.20, ENGINEERING_SOLUTION: 0.50, CRITICAL_EVALUATION: 0.23,
};
export const FRAMING_PRESETS = ["none", "letterbox_235", "overscan_108"] as const;
export type FramingPreset = typeof FRAMING_PRESETS[number];
export const MOODS = ["cinematic_tension_drone", "epic_build", "curious_pulse", "reflective_ambient", "hopeful_rise", "dark_suspense"] as const;
export const AMBIENT_SFX = ["wind_high_altitude", "heavy_machinery_hum", "water_turbine_roar", "data_beeps", "construction_site", "city_traffic", "flowing_water", "rain", "crowd_murmur", "industrial_metal"] as const;
export type Mood = typeof MOODS[number];
export type AmbientSfx = typeof AMBIENT_SFX[number];

export type DocuSource =
  | { kind: "job"; outputPath: string; title: string }
  | { kind: "file"; video: string; subtitles: string; title: string };
export type DocuRequest = {
  title: string; topic: string; targetMinutes: number; cinematicFramingPreset: FramingPreset;
  burnEndCredits: boolean; sources: DocuSource[]; outputRoot: string | null;
};
export type SourceForm = { kind: "job" | "file"; outputPath?: string; video?: string; subtitles?: string; title: string };
export type DocuJob = Pick<QueueJob, "id" | "displayName" | "outputPath" | "status">;
export type DocuSpecSource = { id: string; title: string; video: string; transcript: string; origin_url: string | null; credit: unknown | null };
export type DocuSpec = {
  schema_version: 1; title: string; topic: string; target_minutes: number; cinematic_framing_preset: FramingPreset;
  burn_end_credits: boolean; sources: DocuSpecSource[];
};
export type QuantityFact = { text: string; dimension: string; si: number; imperial: string | null; comparisons: string[] };
export type ContextSegment = {
  seg_id: string; start: number; end: number; original_text: string; extracted_keywords: string[]; quantities: QuantityFact[];
};
export type SourceContext = { schema_version?: number; source_id: string; title: string; video_path: string; duration: number; segments: ContextSegment[] };
export type SourceRef = { source_id: string; start: number; end: number };
export const GRAPHIC_TYPES = ["METRIC_CALLOUT", "LOWER_THIRD", "LABEL"] as const;
export type GraphicType = typeof GRAPHIC_TYPES[number];
export const GRAPHIC_NAMES: Record<GraphicType, string> = { METRIC_CALLOUT: "Thẻ số liệu", LOWER_THIRD: "Lower third", LABEL: "Nhãn" };
export type OnScreenGraphic = { type: GraphicType; title: string; value: string; subtext: string } | null;
export type DocuScene = {
  scene_id: string; narration_text: string; matched_source_refs: SourceRef[]; visual_search_tags: string[];
  on_screen_graphic: OnScreenGraphic; ambient_sfx_tag: AmbientSfx | null;
  needs_review: boolean; review_notes: string[]; review_numbers: string[]; acknowledged_numbers: string[];
  [key: string]: unknown;
};
export type DocuChapter = { chapter_type: ChapterType; recommended_bgm_mood?: Mood; scenes: DocuScene[]; [key: string]: unknown };
export const DEFAULT_MOODS: Record<ChapterType, Mood> = {
  HOOK: "cinematic_tension_drone", PROBLEM: "dark_suspense", ENGINEERING_SOLUTION: "curious_pulse", CRITICAL_EVALUATION: "reflective_ambient",
};
export const MOOD_NAMES: Record<Mood, string> = {
  cinematic_tension_drone: "Căng thẳng điện ảnh", epic_build: "Hùng tráng dần", curious_pulse: "Tò mò, nhịp đều",
  reflective_ambient: "Trầm lắng", hopeful_rise: "Lạc quan", dark_suspense: "Hồi hộp, u tối",
};
/** Engine limits (engine/dubflow/docu/script.py). */
export const MAX_NARRATION = 600;
export const MAX_REFS = 6;
export type DocuScript = {
  project_title: string; target_Coordinates_3D: { lat: number; lng: number; location_name: string } | null;
  chapters: DocuChapter[]; review_notes?: string[]; [key: string]: unknown;
};
export type DocuRun = { dir: string; created: number; spec: DocuSpec; script: boolean };
export type NarrationVoice = { voiceId: string; name: string; gender: string; accent: string; description: string };
export type TimelineScene = { scene_id: string; chapter_type: string; start: number; end: number; slot_end: number; exact_duration_ms: number };
export type NarrationTimeline = { script_sha256: string; voice_id: string; speed: number; duration: number; scenes: TimelineScene[] };
export type NarrationStatus = { state: "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED"; done: number; total: number; message: string };
export type NarrationInfo = { audio: string | null; timeline: NarrationTimeline | null; status: NarrationStatus | null };
export const NARRATION_SPEEDS = [0.9, 0.95, 1, 1.05, 1.1] as const;
export type PlanSlice = { source_id: string; start: number; end: number; at: number; duration: number; tier: number };
export type PlanScene = { scene_id: string; start: number; end: number; warnings: string[]; slices: PlanSlice[] };
export type VisualPlan = { script_sha256: string; duration: number; scenes: PlanScene[]; warnings: string[] };
export type VisualInfo = { plan: VisualPlan | null; status: NarrationStatus | null };
export type RenderOptions = { graphics: boolean; captions: boolean; music: "generated" | "file" | "none"; music_path: string | null };
export type RenderStatus = NarrationStatus & { overlays?: { graphics: boolean; captions: boolean; music: string } };
export type RenderInfo = { video: string | null; status: RenderStatus | null; options: RenderOptions };
/** The last CapCut export (engine/dubflow/docu/capcut_draft.py). */
export type CapcutExport = { folder: string; name: string; clips: number; duration: number; captions: string };
/** An English label box set over MCP (apply_smart_label_overlay): scene, words, normalized centre and width. */
export type DocuLabel = { id: string; scene_id: string; text: string; x: number; y: number; w: number };
export type OpenDocuRun = { dir: string; spec: DocuSpec; contexts: SourceContext[]; script: DocuScript | null; narration: NarrationInfo; visual: VisualInfo; render: RenderInfo; capcut: CapcutExport | null; labels: DocuLabel[] };
export type McpConnection = { python: string; app: string; claudeCode: string; claudeDesktop: string };
export type CreateDocuResult = { dir: string; sources: Array<{ id: string; duration: number; segments: number }> };

const CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u;
const VIDEO_EXT = /\.(mp4|mov|mkv|webm|avi|m4v|flv)$/i;
const absolute = (value: string): boolean => /^(?:[a-z]:[\\/]|\\\\|\/)/i.test(value);
const validText = (value: string, limit: number, required = false): boolean =>
  [...value].length <= limit && !CONTROL.test(value) && (!required || Boolean(value.trim()));
const obj = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const string = (value: unknown): string => typeof value === "string" ? value : "";

export function completedDocuJobs(jobs: readonly DocuJob[]): DocuJob[] {
  return jobs.filter(job => job.status.state === "COMPLETED" && Boolean(job.outputPath));
}

export function moveItem<T>(items: readonly T[], index: number, delta: number): T[] {
  const result = [...items], target = index + delta;
  if (index >= 0 && index < result.length && target >= 0 && target < result.length) [result[index], result[target]] = [result[target], result[index]];
  return result;
}

export function sourceTitle(job: DocuJob): string {
  return [...job.displayName.replace(/\.[^.]+$/, "")].slice(0, 120).join("") || "Video";
}

export function buildRequest(input: {
  title: string; topic: string; targetMinutes: number | string; cinematicFramingPreset: string;
  burnEndCredits: boolean; sources: readonly SourceForm[]; outputRoot: string | null;
}): DocuRequest | string {
  const minutes = typeof input.targetMinutes === "number" ? input.targetMinutes : Number(input.targetMinutes);
  if (!validText(input.title, 120, true)) return "Nhập tiêu đề dự án, tối đa 120 ký tự.";
  if (!validText(input.topic, 600)) return "Ghi chú chủ đề tối đa 600 ký tự.";
  if (!Number.isInteger(minutes) || minutes < 3 || minutes > 20) return "Thời lượng mục tiêu từ 3 đến 20 phút.";
  if (!(FRAMING_PRESETS as readonly string[]).includes(input.cinematicFramingPreset)) return "Kiểu khung hình không hợp lệ.";
  if (typeof input.burnEndCredits !== "boolean") return "Thiết lập credit cuối video không hợp lệ.";
  if (input.outputRoot !== null && (!absolute(input.outputRoot) || !input.outputRoot.trim())) return "Thư mục lưu phải là đường dẫn tuyệt đối.";
  if (input.sources.length < 2 || input.sources.length > 5) return "Hãy chọn từ 2 đến 5 nguồn.";
  const sources: DocuSource[] = [];
  for (const [index, source] of input.sources.entries()) {
    if (!validText(source.title, 120, true)) return `Nguồn ${index + 1}: tiêu đề từ 1 đến 120 ký tự.`;
    if (source.kind === "job") {
      if (!source.outputPath || !absolute(source.outputPath) || !/[/\\]final_vi\.mp4$/i.test(source.outputPath)) {
        return `Nguồn ${index + 1}: chọn video DubFlow đã hoàn tất.`;
      }
      sources.push({ kind: "job", outputPath: source.outputPath, title: source.title.trim() });
    } else if (source.kind === "file") {
      if (!source.video || !absolute(source.video) || !VIDEO_EXT.test(source.video)
          || !source.subtitles || !absolute(source.subtitles) || !/\.srt$/i.test(source.subtitles)) {
        return `Nguồn ${index + 1}: chọn video và phụ đề SRT hợp lệ.`;
      }
      sources.push({ kind: "file", video: source.video, subtitles: source.subtitles, title: source.title.trim() });
    } else return `Nguồn ${index + 1}: loại nguồn không hợp lệ.`;
  }
  return { title: input.title.trim(), topic: input.topic.trim(), targetMinutes: minutes,
    cinematicFramingPreset: input.cinematicFramingPreset as FramingPreset, burnEndCredits: input.burnEndCredits,
    sources, outputRoot: input.outputRoot?.trim() || null };
}

export function chapterTargetSeconds(targetMinutes: number, chapter: ChapterType): number {
  return Math.round(targetMinutes * 60 * CHAPTER_SHARES[chapter]);
}

export function chapterWordBudget(targetMinutes: number, chapter: ChapterType): number {
  return Math.round(chapterTargetSeconds(targetMinutes, chapter) * 150 / 60);
}

export function wordCount(text: string): number {
  return text.trim() ? text.trim().split(/\s+/u).length : 0;
}

export function estimatedSeconds(text: string): number {
  return Math.round(wordCount(text) * 60 / 150);
}

export function cleanString(value: unknown, limit: number): string {
  if (typeof value !== "string") return "";
  return [...value.replace(/[\u0000-\u001f\u007f-\u009f]/gu, " ").replace(/\s+/gu, " ").trim()].slice(0, limit).join("");
}

export function normalizeScene(value: unknown, knownSources: ReadonlySet<string>, index = 0): DocuScene | null {
  const scene = obj(value);
  if (!scene) return null;
  const narration = cleanString(scene.narration_text, MAX_NARRATION);
  if (!narration) return null;
  const refs = (Array.isArray(scene.matched_source_refs) ? scene.matched_source_refs : []).flatMap((raw) => {
    const ref = obj(raw);
    if (!ref) return [];
    const source_id = cleanString(ref.source_id, 20);
    const start = Number(ref.start), end = Number(ref.end);
    if (!knownSources.has(source_id) || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) return [];
    return [{ source_id, start: Math.round(start * 1000) / 1000, end: Math.round(end * 1000) / 1000 }];
  }).slice(0, MAX_REFS);
  const graphicValue = obj(scene.on_screen_graphic);
  const graphic: OnScreenGraphic = graphicValue && (GRAPHIC_TYPES as readonly string[]).includes(string(graphicValue.type))
    ? { type: graphicValue.type as GraphicType, title: cleanString(graphicValue.title, 60),
      value: cleanString(graphicValue.value, 60), subtext: cleanString(graphicValue.subtext, 120) } : null;
  const sfx = AMBIENT_SFX.includes(scene.ambient_sfx_tag as AmbientSfx) ? scene.ambient_sfx_tag as AmbientSfx : null;
  const extra = Array.isArray(scene.visual_search_tags) ? scene.visual_search_tags : [];
  const tags = extra.map(tag => cleanString(tag, 60)).filter(Boolean).slice(0, 6);
  const { recommended_bgm_mood: _mood, ...rest } = scene;
  return {
    ...rest, scene_id: `sc_${String(index + 1).padStart(3, "0")}`, narration_text: narration,
    matched_source_refs: refs, visual_search_tags: tags, on_screen_graphic: graphic,
    ambient_sfx_tag: sfx, needs_review: Boolean(scene.needs_review),
    review_notes: (Array.isArray(scene.review_notes) ? scene.review_notes : []).map(note => cleanString(note, 240)).filter(Boolean).slice(0, 8),
    review_numbers: (Array.isArray(scene.review_numbers) ? scene.review_numbers : []).map(number => cleanString(number, 40)).filter(Boolean).slice(0, 24),
    acknowledged_numbers: (Array.isArray(scene.acknowledged_numbers) ? scene.acknowledged_numbers : []).map(number => cleanString(number, 40)).filter(Boolean).slice(0, 24),
  };
}

export function normalizeScript(value: unknown, knownSources: ReadonlySet<string>): DocuScript | null {
  const script = obj(value);
  if (!script || !Array.isArray(script.chapters)) return null;
  const inputChapters = new Map<string, Record<string, unknown>>();
  for (const raw of script.chapters) {
    const chapter = obj(raw);
    const type = string(chapter?.chapter_type ?? chapter?.type);
    if (chapter && CHAPTER_ORDER.includes(type as ChapterType) && !inputChapters.has(type)) inputChapters.set(type, chapter);
  }
  const chapters = CHAPTER_ORDER.map((chapterType) => {
    const raw = inputChapters.get(chapterType);
    const scenes = (Array.isArray(raw?.scenes) ? raw.scenes : []).map(scene => normalizeScene(scene, knownSources)).filter((scene): scene is DocuScene => Boolean(scene));
    const mood = MOODS.includes(raw?.recommended_bgm_mood as Mood) ? raw!.recommended_bgm_mood as Mood : DEFAULT_MOODS[chapterType];
    return { ...(raw ?? {}), chapter_type: chapterType, recommended_bgm_mood: mood, scenes };
  });
  const flattened = renumberScenes(chapters);
  const coords = obj(script.target_Coordinates_3D);
  const lat = Number(coords?.lat), lng = Number(coords?.lng);
  const target_Coordinates_3D = coords && Number.isFinite(lat) && Math.abs(lat) <= 90 && Number.isFinite(lng) && Math.abs(lng) <= 180
    ? { lat, lng, location_name: cleanString(coords.location_name, 100) } : null;
  return { ...script, project_title: cleanString(script.project_title, 100), target_Coordinates_3D,
    chapters: flattened, review_notes: (Array.isArray(script.review_notes) ? script.review_notes : []).map(note => cleanString(note, 240)).filter(Boolean).slice(0, 24) };
}

export function flattenScenes(chapters: readonly DocuChapter[]): Array<{ chapter: ChapterType; scene: DocuScene }> {
  return chapters.flatMap(chapter => chapter.scenes.map(scene => ({ chapter: chapter.chapter_type, scene })));
}

export function renumberScenes(chapters: readonly DocuChapter[]): DocuChapter[] {
  let index = 0;
  return chapters.map(chapter => ({ ...chapter, scenes: chapter.scenes.map(scene => ({ ...scene, scene_id: `sc_${String(++index).padStart(3, "0")}` })) }));
}

/**
 * Copy the checker's flags onto the scenes being edited. The checker drops
 * scenes without narration and renumbers the rest, so scenes pair up in order,
 * skipping empty ones; the user's text and ids stay as they are.
 */
export function applyFlags(chapters: readonly DocuChapter[], checked: readonly DocuChapter[]): void {
  const flagged = flattenScenes(checked).map(item => item.scene);
  let next = 0;
  for (const { scene } of flattenScenes(chapters)) {
    if (!scene.narration_text.trim()) { Object.assign(scene, { needs_review: false, review_notes: [], review_numbers: [] }); continue; }
    const flags = flagged[next++];
    if (!flags) break;
    scene.needs_review = Boolean(flags.needs_review);
    scene.review_notes = Array.isArray(flags.review_notes) ? flags.review_notes.map(note => cleanString(note, 240)) : [];
    scene.review_numbers = Array.isArray(flags.review_numbers) ? flags.review_numbers.map(number => cleanString(number, 60)) : [];
    scene.acknowledged_numbers = Array.isArray(flags.acknowledged_numbers) ? flags.acknowledged_numbers.map(number => cleanString(number, 60)) : [];
  }
}

/** Mostly plain Latin letters: English, not Vietnamese or Chinese (engine/dubflow/docu/script.py has the same rule). */
export function isEnglish(text: string): boolean {
  const letters = text.match(/\p{L}/gu)?.length ?? 0;
  const plain = text.match(/[A-Za-z]/g)?.length ?? 0;
  return letters === 0 || plain / letters >= 0.95;
}

export function hasNarration(chapters: readonly DocuChapter[]): boolean {
  return flattenScenes(chapters).some(({ scene }) => Boolean(scene.narration_text.trim()));
}

export function acknowledge(scene: DocuScene): DocuScene {
  const confirmed = new Set(scene.acknowledged_numbers);
  scene.review_numbers.forEach(number => confirmed.add(number));
  return { ...scene, acknowledged_numbers: [...confirmed] };
}

export function parseRuns(value: unknown): DocuRun[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap(raw => {
    const run = obj(raw), spec = obj(run?.spec);
    if (!run || !spec || typeof run.dir !== "string" || spec.schema_version !== 1 || !Array.isArray(spec.sources)) return [];
    return [{ dir: run.dir, created: Number(run.created) || 0, spec: spec as unknown as DocuSpec, script: Boolean(run.script) }];
  }).sort((a, b) => b.created - a.created);
}

export function parseVoices(value: unknown): { voices: NarrationVoice[]; defaultVoiceId: string } {
  const result = obj(value), voices = (Array.isArray(result?.voices) ? result.voices : []).flatMap(raw => {
    const voice = obj(raw);
    return voice && typeof voice.voiceId === "string" && voice.voiceId ? [{ voiceId: voice.voiceId, name: string(voice.name) || voice.voiceId,
      gender: string(voice.gender), accent: string(voice.accent), description: string(voice.description) }] : [];
  });
  const fallback = voices[0]?.voiceId ?? "";
  const preferred = string(result?.defaultVoiceId);
  return { voices, defaultVoiceId: voices.some(voice => voice.voiceId === preferred) ? preferred : fallback };
}

export function parseNarration(value: unknown): NarrationInfo {
  const info = obj(value), timeline = obj(info?.timeline), status = obj(info?.status);
  const scenes = (Array.isArray(timeline?.scenes) ? timeline.scenes : []).flatMap(raw => {
    const scene = obj(raw);
    return scene && typeof scene.scene_id === "string" && Number.isFinite(Number(scene.start)) && Number.isFinite(Number(scene.end))
      ? [{ scene_id: scene.scene_id, chapter_type: string(scene.chapter_type), start: Number(scene.start), end: Number(scene.end),
        slot_end: Number(scene.slot_end) || Number(scene.end), exact_duration_ms: Number(scene.exact_duration_ms) || 0 }] : [];
  });
  const states = ["RUNNING", "COMPLETED", "FAILED", "CANCELLED"];
  return {
    audio: typeof info?.audio === "string" ? info.audio : null,
    timeline: timeline && typeof timeline.script_sha256 === "string" ? { script_sha256: timeline.script_sha256, voice_id: string(timeline.voice_id),
      speed: Number(timeline.speed) || 1, duration: Number(timeline.duration) || 0, scenes } : null,
    status: status && states.includes(string(status.state)) ? { state: status.state as NarrationStatus["state"], done: Number(status.done) || 0,
      total: Number(status.total) || 0, message: string(status.message) } : null,
  };
}

function parseStatus(value: unknown): NarrationStatus | null {
  const status = obj(value);
  return status && ["RUNNING", "COMPLETED", "FAILED", "CANCELLED"].includes(string(status.state)) ? { state: status.state as NarrationStatus["state"],
    done: Number(status.done) || 0, total: Number(status.total) || 0, message: string(status.message) } : null;
}

export function parseVisual(value: unknown): VisualInfo {
  const info = obj(value), plan = obj(info?.plan);
  const strings = (items: unknown) => (Array.isArray(items) ? items : []).filter((item): item is string => typeof item === "string");
  const scenes = (Array.isArray(plan?.scenes) ? plan.scenes : []).flatMap(raw => {
    const scene = obj(raw);
    if (!scene || typeof scene.scene_id !== "string") return [];
    const slices = (Array.isArray(scene.slices) ? scene.slices : []).flatMap(item => {
      const piece = obj(item);
      return piece && typeof piece.source_id === "string" && [piece.start, piece.end, piece.at].every(v => Number.isFinite(Number(v)))
        ? [{ source_id: piece.source_id, start: Number(piece.start), end: Number(piece.end), at: Number(piece.at),
          duration: Number(piece.duration) || Number(piece.end) - Number(piece.start), tier: Number(piece.tier) || 0 }] : [];
    });
    return [{ scene_id: scene.scene_id, start: Number(scene.start) || 0, end: Number(scene.end) || 0, warnings: strings(scene.warnings), slices }];
  });
  return {
    plan: plan && typeof plan.script_sha256 === "string" ? { script_sha256: plan.script_sha256, duration: Number(plan.duration) || 0, scenes, warnings: strings(plan.warnings) } : null,
    status: parseStatus(info?.status),
  };
}

export function parseLabels(value: unknown): DocuLabel[] {
  const items = obj(value)?.labels;
  return (Array.isArray(items) ? items : []).flatMap(raw => {
    const label = obj(raw);
    const [x, y, w] = [Number(label?.x), Number(label?.y), Number(label?.w)];
    return label && typeof label.text === "string" && typeof label.scene_id === "string" && [x, y, w].every(Number.isFinite)
      ? [{ id: string(label.id), scene_id: label.scene_id, text: label.text, x, y, w }] : [];
  });
}

export function parseMcp(value: unknown): McpConnection | null {
  const info = obj(value);
  return info && typeof info.claudeCode === "string" && typeof info.claudeDesktop === "string"
    ? { python: string(info.python), app: string(info.app), claudeCode: info.claudeCode, claudeDesktop: info.claudeDesktop } : null;
}

export function parseCapcut(value: unknown): CapcutExport | null {
  const info = obj(value);
  return info && typeof info.folder === "string" && typeof info.name === "string"
    ? { folder: info.folder, name: info.name, clips: Number(info.clips) || 0, duration: Number(info.duration) || 0, captions: string(info.captions) } : null;
}

export function parseRenderOptions(value: unknown): RenderOptions {
  const info = obj(value);
  const musicPath = typeof info?.music_path === "string" && info.music_path ? info.music_path : null;
  const music = info?.music === "none" ? "none" : info?.music === "file" && musicPath ? "file" : "generated";
  return { graphics: info?.graphics !== false, captions: info?.captions === true, music, music_path: musicPath };
}

export function parseRender(value: unknown): RenderInfo {
  const info = obj(value);
  const status = parseStatus(info?.status) as RenderStatus | null;
  const overlays = obj(obj(info?.status)?.overlays);
  if (status && overlays) status.overlays = { graphics: overlays.graphics === true, captions: overlays.captions === true, music: String(overlays.music ?? "") };
  return { video: typeof info?.video === "string" ? info.video : null, status, options: parseRenderOptions(info?.options) };
}

/** A plan made before the script or its narration changed. */
export function planStale(script: DocuScript | null, timeline: NarrationTimeline | null, plan: VisualPlan | null): boolean {
  return Boolean(plan && (!script || script.script_sha256 !== plan.script_sha256 || !timeline || Math.abs(timeline.duration - plan.duration) > 0.01));
}

/** A narration made from a different script (the checker stamps each saved script with the hash of its spoken words). */
export function narrationStale(script: DocuScript | null, timeline: NarrationTimeline | null): boolean {
  return Boolean(timeline && (!script || typeof script.script_sha256 !== "string" || script.script_sha256 !== timeline.script_sha256));
}

export function parseOpen(value: unknown): OpenDocuRun | null {
  const result = obj(value), spec = obj(result?.spec);
  if (!result || typeof result.dir !== "string" || !spec || spec.schema_version !== 1 || !Array.isArray(result.contexts)) return null;
  const contexts = result.contexts.flatMap(raw => {
    const context = obj(raw);
    return context && typeof context.source_id === "string" && Array.isArray(context.segments)
      ? [context as unknown as SourceContext] : [];
  });
  const sourceIds = new Set(contexts.map(context => context.source_id));
  return { dir: result.dir, spec: spec as unknown as DocuSpec, contexts,
    script: result.script === null || result.script === undefined ? null : normalizeScript(result.script, sourceIds),
    narration: parseNarration(result.narration), visual: parseVisual(result.visual), render: parseRender(result.render),
    capcut: parseCapcut(result.capcut), labels: parseLabels(result.labels) };
}

export function sourceLines(contexts: readonly SourceContext[], charLimit = 120_000): string[] {
  type Line = { source_id: string; start: number; end: number; text: string };
  const original: Line[] = contexts.flatMap(context => context.segments.map(segment => ({ source_id: context.source_id,
    start: segment.start, end: segment.end, text: segment.original_text.trim() })));
  const format = (lines: readonly Line[]) => lines.map(line => `[${line.source_id}:${line.start.toFixed(1)}-${line.end.toFixed(1)}] ${line.text}`).join("\n");
  let packed = [...original];
  for (const minWindow of [8, 15]) {
    if (format(packed).length <= charLimit) break;
    const merged: Line[] = [];
    for (const line of packed) {
      const prior = merged.at(-1);
      if (prior && prior.source_id === line.source_id && line.start - prior.start >= 0 && line.end - prior.start <= minWindow) {
        prior.end = Math.max(prior.end, line.end);
        prior.text = `${prior.text} ${line.text}`.trim();
      } else merged.push({ ...line });
    }
    packed = merged;
  }
  const result = format(packed);
  if (result.length <= charLimit) return [result];
  const truncated = [...result].slice(0, charLimit).join("");
  return [truncated];
}

export function quantityLines(contexts: readonly SourceContext[]): string[] {
  return contexts.flatMap(context => context.segments.flatMap(segment => (segment.quantities ?? []).map(quantity =>
    `[${context.source_id}:${segment.start.toFixed(1)}-${segment.end.toFixed(1)}] ${quantity.text}${quantity.imperial ? ` = ${quantity.imperial}` : ""}${quantity.comparisons?.length ? `; ${quantity.comparisons.join("; ")}` : ""}`)));
}

/** A text for the editor's Chữ track: output time, length, words and look. */
export type Callout = { at: number; seconds: number; text: string; look: Partial<TextLook> };

/** Dark slate card (rgba(15, 23, 42, 0.9): the editor steps opacity by 0.05), neon accent; white words. */
const CARD = { font: "Segoe UI", color: "#FFFFFF", outline: "#38BDF8", outline_width: 2, box: "#0F172A", box_opacity: 0.9,
  bold: true, italic: false, animation: "pop", anim_duration: 0.4 } as const satisfies Partial<TextLook>;
const CALLOUT_LOOKS: Record<GraphicType, Partial<TextLook>> = {
  METRIC_CALLOUT: { ...CARD, size: 54, align: "center", x: 0.76, y: 0.2, w: 0.36 },
  LOWER_THIRD: { ...CARD, size: 46, align: "left", x: 0.3, y: 0.82, w: 0.5, animation: "slide-up" },
  LABEL: { ...CARD, size: 44, align: "center", x: 0.5, y: 0.5, w: 0.4, animation: "fade" },
};
export const CALLOUT_SECONDS = 5;

/**
 * The script's on-screen graphics as Chữ texts at their scene's narrated time
 * (a little after the scene starts, at most 5 s and never past the scene).
 */
export function calloutTexts(script: DocuScript | null, timeline: NarrationTimeline | null, labels: readonly DocuLabel[] = [],
  { graphics = true }: { graphics?: boolean } = {}): Callout[] {
  if (!script || !timeline) return [];
  const slots = new Map(timeline.scenes.map(scene => [scene.scene_id, scene]));
  // Labels are burned into the rough cut already; in the editor they are Chữ texts to move or restyle.
  const boxes = labels.flatMap(label => {
    const scene = slots.get(label.scene_id);
    return scene ? [{ at: Math.round(scene.start * 1000) / 1000, seconds: Math.max(1, scene.slot_end - scene.start), text: label.text,
      look: { ...CALLOUT_LOOKS.LABEL, x: label.x, y: label.y, w: label.w, animation: "none" } as Partial<TextLook> }] : [];
  });
  return [...(graphics ? calloutsOf(script, timeline) : []), ...boxes];
}

function calloutsOf(script: DocuScript, timeline: NarrationTimeline): Callout[] {
  const times = new Map(timeline.scenes.map(scene => [scene.scene_id, scene]));
  return flattenScenes(script.chapters).flatMap(({ scene }) => {
    const graphic = scene.on_screen_graphic, timed = times.get(scene.scene_id);
    if (!graphic || !timed) return [];
    const lines = graphic.type === "METRIC_CALLOUT" ? [graphic.value, graphic.title, graphic.subtext]
      : [graphic.title, graphic.value, graphic.subtext];
    const text = lines.map(line => line.trim()).filter(Boolean).join("\n");
    const at = Math.round((timed.start + 0.3) * 1000) / 1000, seconds = Math.min(CALLOUT_SECONDS, Math.max(1, timed.slot_end - at));
    return text ? [{ at, seconds, text, look: CALLOUT_LOOKS[graphic.type] }] : [];
  });
}
