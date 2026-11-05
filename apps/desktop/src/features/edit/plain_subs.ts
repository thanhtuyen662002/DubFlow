/**
 * Phụ đề for a video without a re-runnable dubbing job (Issue #523): the lines
 * live in the edit draft (`project.subtitles`), in the main video's own time
 * so they follow cuts and speed, seeded from the video's captions. The export
 * burns them in when "In phụ đề vào video khi xuất" is on.
 */
import { MAIN, outputToSource, type Cue, type Edit } from "./model.ts";
import { cleanText, DEFAULT_STYLE, normalizeStyle, parseStyle, type SubtitleCue, type SubtitleStyle } from "./subtitles.ts";

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
