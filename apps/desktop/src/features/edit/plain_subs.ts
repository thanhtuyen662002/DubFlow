/**
 * Phụ đề for a video without a re-runnable dubbing job (Issue #523): the lines
 * live in the edit draft (`project.subtitles`), in the main video's own time
 * so they follow cuts and speed, seeded from the video's captions. The export
 * burns them in when "In phụ đề vào video khi xuất" is on.
 */
import { MAIN, outputToSource, type Cue, type Edit } from "./model.ts";
import { cleanText, DEFAULT_STYLE, normalizeStyle, parseStyle, withoutPosition, type SubtitleCue, type SubtitleStyle } from "./subtitles.ts";

export type PlainLine = { id: string; start_ms: number; end_ms: number; text: string };
export type PlainSubtitles = { burn: boolean; style: SubtitleStyle; lines: PlainLine[] };

export const MAX_LINES = 20_000;
export const MAX_LINE_TEXT = 2000;
/** A line is on screen at least this long. */
export const MIN_LINE_MS = 200;
/** A new line lasts this long, unless the next line starts sooner. */
export const NEW_LINE_MS = 2000;

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

function obj(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

export function sortLines(lines: PlainLine[]): PlainLine[] {
  return lines.sort((a, b) => a.start_ms - b.start_ms || a.end_ms - b.end_ms);
}

/** The draft's lines, else the video's captions as the first lines (not burned: they are usually in the picture already). */
export function plainSubtitles(project: unknown, cues: readonly Cue[], remembered: SubtitleStyle | null): PlainSubtitles {
  const saved = obj(obj(project)?.subtitles);
  if (saved && Array.isArray(saved.lines)) {
    const seen = new Set<string>();
    const lines = saved.lines.flatMap((raw): PlainLine[] => {
      const line = obj(raw);
      if (!line || typeof line.id !== "string" || !line.id || seen.has(line.id) || typeof line.text !== "string") return [];
      const { start_ms: start, end_ms: end } = line;
      if (!finite(start) || !finite(end) || start < 0 || end <= start) return [];
      seen.add(line.id);
      return [{ id: line.id, start_ms: Math.round(start), end_ms: Math.round(end), text: line.text.slice(0, MAX_LINE_TEXT) }];
    }).slice(0, MAX_LINES);
    const style = parseStyle(saved.style);
    return { burn: saved.burn === true, style: normalizeStyle(style ?? remembered ?? DEFAULT_STYLE), lines: sortLines(lines) };
  }
  const lines = sortLines(cues.slice(0, MAX_LINES).map((cue) => ({ id: "", start_ms: cue.start_ms, end_ms: cue.end_ms, text: cue.text })));
  lines.forEach((line, index) => { line.id = `c${index + 1}`; });
  return { burn: lines.length === 0, style: normalizeStyle(remembered ?? DEFAULT_STYLE), lines };
}

/** The lines as the Phụ đề list shows them. */
export function asCues(lines: readonly PlainLine[]): SubtitleCue[] {
  return lines.map((line) => ({
    cue_id: line.id, start_ms: line.start_ms, end_ms: line.end_ms, source_text: "", machine_text: line.text, text: line.text,
    voice_text: "", edited: false, dub_failed: false,
  }));
}

function freshId(lines: readonly PlainLine[]): string {
  let number = lines.length + 1;
  const taken = new Set(lines.map((line) => line.id));
  while (taken.has(`n${number}`)) number += 1;
  return `n${number}`;
}

/** A new empty line at a time of the main video, up to the next line or the end. */
export function addLine(lines: PlainLine[], atMs: number, endMs: number): PlainLine | null {
  if (lines.length >= MAX_LINES) return null;
  const start = Math.max(0, Math.round(atMs));
  const next = lines.find((line) => line.start_ms > start);
  const end = Math.min(start + NEW_LINE_MS, next?.start_ms ?? Infinity, Math.round(endMs));
  if (end - start < MIN_LINE_MS) return null;
  const line = { id: freshId(lines), start_ms: start, end_ms: end, text: "" };
  lines.push(line);
  sortLines(lines);
  return line;
}

/** A time on the edited timeline as a time of the main video; null on an inserted clip. */
export function mainMs(edit: Edit, outputSeconds: number): number | null {
  const point = outputToSource(edit, outputSeconds);
  return point && point.segment.mediaId === MAIN ? Math.round(point.sourceTime * 1000) : null;
}

/** New start or end for a line, kept at least MIN_LINE_MS long; null when it would not be. */
export function retime(line: PlainLine, change: { start_ms?: number; end_ms?: number }): { start_ms: number; end_ms: number } | null {
  const start = Math.max(0, Math.round(change.start_ms ?? line.start_ms));
  const end = Math.round(change.end_ms ?? line.end_ms);
  return end - start >= MIN_LINE_MS ? { start_ms: start, end_ms: end } : null;
}

/** Lines with words in them, for the export's .srt and burn. */
export function exportLines(subtitles: PlainSubtitles): Cue[] {
  return subtitles.lines.map((line) => ({ start_ms: line.start_ms, end_ms: line.end_ms, text: cleanText(line.text) }))
    .filter((line) => line.text && line.end_ms > line.start_ms);
}

/** What edit_export takes to burn the lines in, or null. */
export function burnRequest(subtitles: PlainSubtitles): { style: SubtitleStyle; lines: Cue[] } | null {
  const lines = exportLines(subtitles);
  return subtitles.burn && lines.length ? { style: normalizeStyle(subtitles.style), lines } : null;
}

/** `project.subtitles` in the draft. */
export function serializePlain(subtitles: PlainSubtitles): unknown {
  return { burn: subtitles.burn, style: normalizeStyle(subtitles.style), lines: subtitles.lines.map((line) => ({ ...line })) };
}

// ---------- Lớp phụ đề: several subtitle layers (Issue: two-layer subtitles) ----------

/**
 * One subtitle layer: its lines and look as before, plus a name, the
 * language its lines are in ("zh", "vi", … or "" when unknown) and whether
 * it is hidden (not drawn on the preview, not burned, not in the .srt).
 */
export type PlainTrack = PlainSubtitles & { id: string; name: string; lang: string; hidden: boolean };
/** Every layer of a plain video, in drawing order (later on top), and the one being edited. */
export type PlainLayers = { tracks: PlainTrack[]; active: string };

export const MAX_TRACKS = 4;
const MAX_NAME = 40;
/** The original-language layer sits above the Vietnamese one, a little smaller. */
export const SOURCE_STYLE: Partial<SubtitleStyle> = { size: 0.8, x: 0.5, y: 0.8 };
export const LANGUAGE_LABELS: Record<string, string> = { zh: "Tiếng Trung", en: "Tiếng Anh", vi: "Tiếng Việt" };

function parseTrack(raw: unknown, remembered: SubtitleStyle | null, taken: Set<string>): PlainTrack | null {
  const value = obj(raw);
  if (!value || typeof value.id !== "string" || !value.id || value.id.length > 40 || taken.has(value.id)) return null;
  const subtitles = plainSubtitles({ subtitles: { burn: value.burn, style: value.style, lines: Array.isArray(value.lines) ? value.lines : [] } }, [], remembered);
  taken.add(value.id);
  return {
    ...subtitles, id: value.id, name: typeof value.name === "string" && value.name.trim() ? value.name.trim().slice(0, MAX_NAME) : "Phụ đề",
    lang: typeof value.lang === "string" && /^[a-z]{0,3}$/.test(value.lang) ? value.lang : "", hidden: value.hidden === true,
  };
}

/** The draft's layers; a draft from before layers (or none) is one layer, "Phụ đề". */
export function plainLayers(project: unknown, cues: readonly Cue[], remembered: SubtitleStyle | null): PlainLayers {
  const saved = obj(obj(project)?.subtitles);
  if (saved && Array.isArray(saved.tracks)) {
    const taken = new Set<string>();
    const tracks = saved.tracks.slice(0, MAX_TRACKS).flatMap((raw) => parseTrack(raw, remembered, taken) ?? []);
    if (tracks.length) {
      const active = typeof saved.active === "string" && tracks.some((track) => track.id === saved.active) ? saved.active : tracks[tracks.length - 1].id;
      return { tracks, active };
    }
  }
  return { tracks: [{ ...plainSubtitles(project, cues, remembered), id: "t1", name: "Phụ đề", lang: "", hidden: false }], active: "t1" };
}

export function activeTrack(layers: PlainLayers): PlainTrack {
  return layers.tracks.find((track) => track.id === layers.active) ?? layers.tracks[0];
}

function freshTrackId(layers: PlainLayers): string {
  let number = layers.tracks.length + 1;
  while (layers.tracks.some((track) => track.id === `t${number}`)) number += 1;
  return `t${number}`;
}

/** A new layer (on top), made the one being edited; null when there are MAX_TRACKS already. */
export function addTrack(layers: PlainLayers, track: { name: string; lang?: string; lines?: Array<Omit<PlainLine, "id">>; style: SubtitleStyle; burn?: boolean }): PlainTrack | null {
  if (layers.tracks.length >= MAX_TRACKS) return null;
  const lines = sortLines((track.lines ?? []).slice(0, MAX_LINES).map((line, index) => ({
    id: `c${index + 1}`, start_ms: Math.max(0, Math.round(line.start_ms)), end_ms: Math.round(line.end_ms), text: line.text.slice(0, MAX_LINE_TEXT),
  })).filter((line) => line.end_ms > line.start_ms));
  const made: PlainTrack = { id: freshTrackId(layers), name: track.name.slice(0, MAX_NAME), lang: track.lang ?? "", hidden: false,
    burn: track.burn ?? true, style: normalizeStyle(track.style), lines };
  layers.tracks.push(made);
  layers.active = made.id;
  return made;
}

/** Remove a layer; the last one left stays (emptied instead). */
export function removeTrack(layers: PlainLayers, id: string): void {
  if (layers.tracks.length <= 1) {
    const only = layers.tracks[0];
    if (only) only.lines = [];
    return;
  }
  const index = layers.tracks.findIndex((track) => track.id === id);
  if (index < 0) return;
  layers.tracks.splice(index, 1);
  if (layers.active === id) layers.active = layers.tracks[Math.min(index, layers.tracks.length - 1)].id;
}

/**
 * The two layers of Tách phụ đề: the original words above, the Vietnamese
 * below. A lone empty layer (nothing written yet) is replaced; others stay.
 */
export function addExtracted(layers: PlainLayers, language: string, source: Array<Omit<PlainLine, "id">>, vi: Array<Omit<PlainLine, "id">>,
  look: SubtitleStyle): { source: PlainTrack | null; vi: PlainTrack | null } {
  if (layers.tracks.length === 1 && !layers.tracks[0].lines.some((line) => cleanText(line.text))) layers.tracks = [];
  const room = MAX_TRACKS - layers.tracks.length;
  const base = withoutPosition(normalizeStyle(look));
  const original = room >= 2 ? addTrack(layers, { name: LANGUAGE_LABELS[language] ?? "Lời gốc", lang: language, lines: source,
    style: normalizeStyle({ ...base, ...SOURCE_STYLE }) }) : null;
  const vietnamese = room >= 1 ? addTrack(layers, { name: "Tiếng Việt", lang: "vi", lines: vi, style: base }) : null;
  if (!layers.tracks.length) layers.tracks.push({ ...plainSubtitles({}, [], look), id: "t1", name: "Phụ đề", lang: "", hidden: false });
  if (!layers.tracks.some((track) => track.id === layers.active)) layers.active = layers.tracks[0].id;
  return { source: original, vi: vietnamese };
}

/** The layers shown and burned: not hidden. */
export function shownTracks(layers: PlainLayers): PlainTrack[] {
  return layers.tracks.filter((track) => !track.hidden);
}

/** The .srt's lines: the Vietnamese layer when shown, else the one being edited (when shown), else the first shown. */
export function srtLines(layers: PlainLayers): Cue[] {
  const shown = shownTracks(layers);
  const track = shown.find((each) => each.lang === "vi") ?? shown.find((each) => each.id === layers.active) ?? shown[0];
  return track ? exportLines(track) : [];
}

/** What edit_export burns: `burn` (the first) and `burnExtra` (drawn over it), from the shown layers that burn. */
export function burnRequests(layers: PlainLayers): { burn?: { style: SubtitleStyle; lines: Cue[] }; burnExtra?: Array<{ style: SubtitleStyle; lines: Cue[] }> } {
  const all = shownTracks(layers).flatMap((track) => burnRequest(track) ?? []);
  if (!all.length) return {};
  return { burn: all[0], ...(all.length > 1 ? { burnExtra: all.slice(1) } : {}) };
}

/** `project.subtitles` with layers; the layer being edited also stays at the top level for older versions of the app. */
export function serializeLayers(layers: PlainLayers): unknown {
  const tracks = layers.tracks.map((track) => ({ ...(serializePlain(track) as object), id: track.id, name: track.name, lang: track.lang, hidden: track.hidden }));
  return { ...(serializePlain(activeTrack(layers)) as object), tracks, active: layers.active };
}
