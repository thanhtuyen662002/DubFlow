/**
 * Cắt ghép: an edit is an ordered list of segments over the finished video
 * (final_vi.mp4). Each segment plays [start, end) of the source, in seconds,
 * at its own speed and volume; the edited output is the segments back to back.
 * Every operation is pure and returns a new edit, or the same object when
 * nothing changes, so the history can skip no-op steps.
 */

export const MIN_SEGMENT = 0.1;
export const MIN_SPEED = 0.5;
export const MAX_SPEED = 2;
export const MAX_VOLUME = 2;
export const MAX_SEGMENTS = 500;
export const SPEED_PRESETS: readonly number[] = [0.5, 0.75, 1, 1.25, 1.5, 2];
export const HISTORY_LIMIT = 200;
/** A remapped cue piece shorter than this is dropped, unless it is the whole cue. */
export const MIN_CUE_PIECE = 0.1;
export const PROJECT_SCHEMA = 1;

export type Segment = { readonly id: string; readonly start: number; readonly end: number; readonly speed: number; readonly volume: number };
export type Edit = { readonly duration: number; readonly segments: readonly Segment[]; readonly nextId: number };
export type Cue = { start_ms: number; end_ms: number; text: string };
export type SourcePoint = { segment: Segment; index: number; sourceTime: number; outputStart: number };

const ms = (value: number): number => Math.round(value * 1000) / 1000;
const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));

export function createEdit(duration: number): Edit {
  return { duration, segments: [{ id: "s1", start: 0, end: ms(duration), speed: 1, volume: 1 }], nextId: 2 };
}

/** Seconds a segment lasts in the edited output. */
export function segmentLength(segment: Segment): number {
  return (segment.end - segment.start) / segment.speed;
}

export function outputDuration(edit: Edit): number {
  return edit.segments.reduce((total, segment) => total + segmentLength(segment), 0);
}

/** Output time at which each segment starts. */
export function outputStarts(edit: Edit): number[] {
  const starts: number[] = [];
  let time = 0;
  for (const segment of edit.segments) {
    starts.push(time);
    time += segmentLength(segment);
  }
  return starts;
}

/** The segment playing at output time `t` (a boundary belongs to the next segment). */
export function outputToSource(edit: Edit, t: number): SourcePoint | null {
  const { segments } = edit;
  if (!segments.length) return null;
  const time = clamp(t, 0, outputDuration(edit));
  let start = 0;
  for (let index = 0; index < segments.length; index++) {
    const segment = segments[index];
    const length = segmentLength(segment);
    if (time < start + length) {
      return { segment, index, sourceTime: Math.min(segment.end, segment.start + (time - start) * segment.speed), outputStart: start };
    }
    start += length;
  }
  const index = segments.length - 1;
  const segment = segments[index];
  return { segment, index, sourceTime: segment.end, outputStart: start - segmentLength(segment) };
}

/**
 * Output time of a source time: inside the preferred segment when it holds
 * that time (segments may share source ranges), else the first that does.
 */
export function sourceToOutput(edit: Edit, sourceTime: number, preferredIndex = -1): number | null {
  const starts = outputStarts(edit);
  const at = (index: number): number => starts[index] + (sourceTime - edit.segments[index].start) / edit.segments[index].speed;
  const preferred = edit.segments[preferredIndex];
  if (preferred && sourceTime >= preferred.start && sourceTime <= preferred.end) return at(preferredIndex);
  const index = edit.segments.findIndex((segment) => sourceTime >= segment.start && sourceTime < segment.end);
  return index < 0 ? null : at(index);
}

function replace(edit: Edit, index: number, segments: Segment[], nextId = edit.nextId): Edit {
  return { ...edit, segments: [...edit.segments.slice(0, index), ...segments, ...edit.segments.slice(index + 1)], nextId };
}

/** Split the segment under output time `t` in two; no-op within 0.1 s of its edges. */
export function split(edit: Edit, t: number): Edit {
  const point = outputToSource(edit, t);
  if (!point || edit.segments.length >= MAX_SEGMENTS) return edit;
  const { segment, index } = point;
  const at = ms(point.sourceTime);
  if (at - segment.start < MIN_SEGMENT || segment.end - at < MIN_SEGMENT) return edit;
  return replace(edit, index, [{ ...segment, end: at }, { ...segment, id: `s${edit.nextId}`, start: at }], edit.nextId + 1);
}

/** Remove a segment; the last one left stays. */
export function remove(edit: Edit, id: string): Edit {
  const index = edit.segments.findIndex((segment) => segment.id === id);
  if (index < 0 || edit.segments.length <= 1) return edit;
  return { ...edit, segments: edit.segments.filter((segment) => segment.id !== id) };
}

/**
 * Move one edge of a segment to `sourceTime`. Segments are independent source
 * ranges, so only the source bounds and the 0.1 s minimum length apply.
 */
export function trim(edit: Edit, id: string, edge: "start" | "end", sourceTime: number): Edit {
  const index = edit.segments.findIndex((segment) => segment.id === id);
  if (index < 0 || !Number.isFinite(sourceTime)) return edit;
  const segment = edit.segments[index];
  const value = edge === "start"
    ? ms(clamp(sourceTime, 0, segment.end - MIN_SEGMENT))
    : ms(clamp(sourceTime, segment.start + MIN_SEGMENT, edit.duration));
  if (value === segment[edge]) return edit;
  return replace(edit, index, [{ ...segment, [edge]: value }]);
}

export function setSpeed(edit: Edit, id: string, speed: number): Edit {
  const index = edit.segments.findIndex((segment) => segment.id === id);
  if (index < 0 || !Number.isFinite(speed)) return edit;
  const value = Math.round(clamp(speed, MIN_SPEED, MAX_SPEED) * 100) / 100;
  if (value === edit.segments[index].speed) return edit;
  return replace(edit, index, [{ ...edit.segments[index], speed: value }]);
}

export function setVolume(edit: Edit, id: string, volume: number): Edit {
  const index = edit.segments.findIndex((segment) => segment.id === id);
  if (index < 0 || !Number.isFinite(volume)) return edit;
  const value = Math.round(clamp(volume, 0, MAX_VOLUME) * 100) / 100;
  if (value === edit.segments[index].volume) return edit;
  return replace(edit, index, [{ ...edit.segments[index], volume: value }]);
}

/** The whole video once, at normal speed and volume: nothing to export. */
export function isUnchanged(edit: Edit, duration: number): boolean {
  const [only] = edit.segments;
  return edit.segments.length === 1 && only.start <= 0.001 && only.end >= duration - 0.001 && only.speed === 1 && only.volume === 1;
}

export function sameSegments(a: readonly Segment[], b: readonly Pick<Segment, "start" | "end" | "speed" | "volume">[]): boolean {
  return a.length === b.length && a.every((segment, index) => {
    const other = b[index];
    return Math.abs(segment.start - other.start) < 0.0005 && Math.abs(segment.end - other.end) < 0.0005
      && segment.speed === other.speed && segment.volume === other.volume;
  });
}

/**
 * Subtitles on the edited timeline: cues fully inside deleted parts are
 * dropped, partial ones clipped, the rest shifted and scaled by each segment's
 * speed, in output order. A cue split only by a cut that keeps both sides
 * adjacent comes back as one cue.
 */
export function remapCues(cues: readonly Cue[], edit: Edit): Cue[] {
  const result: Array<Cue & { source: number }> = [];
  const starts = outputStarts(edit);
  const ordered = cues.map((cue, source) => ({ cue, source })).sort((a, b) => a.cue.start_ms - b.cue.start_ms || a.source - b.source);
  edit.segments.forEach((segment, index) => {
    for (const { cue, source } of ordered) {
      const cueStart = cue.start_ms / 1000, cueEnd = cue.end_ms / 1000;
      const from = Math.max(cueStart, segment.start), to = Math.min(cueEnd, segment.end);
      const kept = to - from;
      if (kept <= 0 || (kept < MIN_CUE_PIECE && kept < cueEnd - cueStart - 1e-6)) continue;
      const start_ms = Math.round((starts[index] + (from - segment.start) / segment.speed) * 1000);
      const end_ms = Math.round((starts[index] + (to - segment.start) / segment.speed) * 1000);
      const previous = result.at(-1);
      if (previous && previous.source === source && Math.abs(previous.end_ms - start_ms) <= 1) {
        previous.end_ms = end_ms;
        continue;
      }
      result.push({ start_ms, end_ms, text: cue.text, source });
    }
  });
  return result.map(({ start_ms, end_ms, text }) => ({ start_ms, end_ms, text }));
}

/**
 * Undo/redo over whole edits (at most 200 steps back), counting the steps
 * between the current edit and the last exported one.
 */
export class EditHistory {
  #states: Edit[] = [];
  #index = 0;
  /** Index of the exported edit, or -1 when it is not in the history. */
  #exported = 0;
  /** With no exported index: changes = base + index. */
  #base = 0;

  constructor(initial: Edit, changes = 0) {
    this.reset(initial, changes);
  }

  reset(edit: Edit, changes = 0): void {
    this.#states = [edit];
    this.#index = 0;
    this.#exported = changes > 0 ? -1 : 0;
    this.#base = Math.max(0, Math.round(changes));
  }

  get current(): Edit { return this.#states[this.#index]; }
  get canUndo(): boolean { return this.#index > 0; }
  get canRedo(): boolean { return this.#index < this.#states.length - 1; }
  /** Steps away from the last export; 0 means nothing unexported. */
  get changes(): number {
    return this.#exported >= 0 ? Math.abs(this.#index - this.#exported) : Math.max(1, this.#base + this.#index);
  }

  /** Record a new edit; false when it is the current one (a no-op). */
  push(edit: Edit): boolean {
    if (edit === this.current) return false;
    const before = this.changes;
    this.#states.splice(this.#index + 1);
    if (this.#exported > this.#index) this.#exported = -1;
    this.#states.push(edit);
    this.#index += 1;
    if (this.#exported < 0) this.#base = before + 1 - this.#index;
    if (this.#states.length > HISTORY_LIMIT + 1) {
      const changes = this.changes;
      this.#states.shift();
      this.#index -= 1;
      if (this.#exported >= 0) this.#exported -= 1;
      if (this.#exported < 0) this.#base = changes - this.#index;
    }
    return true;
  }

  undo(): Edit | null {
    if (!this.canUndo) return null;
    this.#index -= 1;
    return this.current;
  }

  redo(): Edit | null {
    if (!this.canRedo) return null;
    this.#index += 1;
    return this.current;
  }

  /** The export of `edit` finished: it becomes the reference for the change count. */
  markExported(edit: Edit): void {
    let index = this.#states.lastIndexOf(edit);
    for (let candidate = this.#states.length - 1; index < 0 && candidate >= 0; candidate--) {
      if (sameSegments(this.#states[candidate].segments, edit.segments)) index = candidate;
    }
    if (index >= 0) {
      this.#exported = index;
    } else {
      this.#exported = -1;
      this.#base = 1 - this.#index;
    }
  }
}

export type Project = { edit: Edit; changes: number; selected: string | null };

export function serializeProject(edit: Edit, changes: number, selected: string | null): Record<string, unknown> {
  return {
    schema_version: PROJECT_SCHEMA, duration: edit.duration, next_id: edit.nextId, changes, selected,
    segments: edit.segments.map(({ id, start, end, speed, volume }) => ({ id, start, end, speed, volume })),
  };
}

/** A saved project for this video, or null when it is missing, invalid or for another render. */
export function parseProject(value: unknown, duration: number): Project | null {
  if (!value || typeof value !== "object") return null;
  const data = value as Record<string, unknown>;
  if (data.schema_version !== PROJECT_SCHEMA || typeof data.duration !== "number" || Math.abs(data.duration - duration) > 0.5) return null;
  if (!Array.isArray(data.segments) || !data.segments.length || data.segments.length > MAX_SEGMENTS) return null;
  const ids = new Set<string>();
  const segments: Segment[] = [];
  let highest = 0;
  for (const raw of data.segments as unknown[]) {
    if (!raw || typeof raw !== "object") return null;
    const { id, start, end, speed, volume } = raw as Record<string, unknown>;
    if (typeof id !== "string" || !/^s\d{1,9}$/.test(id) || ids.has(id)) return null;
    if (![start, end, speed, volume].every((number) => typeof number === "number" && Number.isFinite(number))) return null;
    const segment = { id, start: start as number, end: Math.min(end as number, ms(duration)), speed: speed as number, volume: volume as number };
    if (segment.start < 0 || segment.end - segment.start < MIN_SEGMENT - 1e-6 || segment.speed < MIN_SPEED || segment.speed > MAX_SPEED
      || segment.volume < 0 || segment.volume > MAX_VOLUME) return null;
    ids.add(id);
    highest = Math.max(highest, Number(id.slice(1)));
    segments.push(segment);
  }
  const nextId = typeof data.next_id === "number" && Number.isInteger(data.next_id) && data.next_id > highest ? data.next_id : highest + 1;
  const changes = typeof data.changes === "number" && Number.isInteger(data.changes) && data.changes >= 0 ? data.changes : 0;
  const selected = typeof data.selected === "string" && ids.has(data.selected) ? data.selected : null;
  return { edit: { duration, segments, nextId }, changes, selected };
}

function fileStem(path: string): string {
  return (path.split(/[\\/]/).pop() ?? "").replace(/\.[^.]*$/, "");
}

/** "<title> (sửa N)" with the first N no earlier export of this job uses. */
export function defaultExportName(title: string, exportPaths: readonly string[]): string {
  const base = title.trim().replace(/\.(mp4|mkv|mov|webm|avi)$/i, "").trim() || "video";
  const taken = new Set(exportPaths.map((path) => fileStem(path).toLocaleLowerCase("vi")));
  let number = 1;
  while (taken.has(`${base} (sửa ${number})`.toLocaleLowerCase("vi"))) number += 1;
  return `${base} (sửa ${number})`;
}

export type Quality = "source" | "1080p" | "720p";

/** The frame size an export gets: "1080p" and "720p" set the short side, keeping the aspect and even sizes. */
export function exportSize(width: number, height: number, quality: Quality): { width: number; height: number } {
  if (quality === "source" || width <= 0 || height <= 0) return { width, height };
  const short = quality === "1080p" ? 1080 : 720;
  const even = (value: number): number => Math.max(2, Math.round(value / 2) * 2);
  return width >= height ? { width: even(width * short / height), height: short } : { width: short, height: even(height * short / width) };
}

/** A rough size for the export from the original file's bitrate. */
export function estimateExportBytes(sourceBytes: number, sourceSeconds: number, outputSeconds: number,
  width: number, height: number, quality: Quality): number {
  if (sourceBytes <= 0 || sourceSeconds <= 0 || outputSeconds <= 0) return 0;
  const base = sourceBytes / sourceSeconds * outputSeconds;
  if (quality === "source" || width <= 0 || height <= 0) return base;
  const target = exportSize(width, height, quality);
  return base * Math.pow(target.width * target.height / (width * height), 0.75);
}

/** "m:ss", "h:mm:ss", or with tenths "m:ss.d". */
export function formatClock(seconds: number, tenths = false): string {
  const total = Math.max(0, tenths ? Math.round(seconds * 10) / 10 : Math.round(seconds));
  const whole = Math.floor(total + 1e-9);
  const hours = Math.floor(whole / 3600), minutes = Math.floor((whole % 3600) / 60), secs = whole % 60;
  const tail = tenths ? `.${Math.round((total - whole) * 10) % 10}` : "";
  const ss = String(secs).padStart(2, "0") + tail;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${ss}` : `${minutes}:${ss}`;
}

/** Seconds from "1:23.4", "01:02:03,5" or "83.4"; null when unreadable. */
export function parseClock(text: string): number | null {
  const value = text.trim().replace(",", ".");
  if (!/^(\d{1,3}:){0,2}\d{1,5}(\.\d+)?$/.test(value)) return null;
  const parts = value.split(":").map(Number);
  if (parts.slice(1).some((part) => part >= 60)) return null;
  return parts.reduce((total, part) => total * 60 + part, 0);
}

/** "1,5×" in Vietnamese notation. */
export function formatSpeed(speed: number): string {
  return `${String(Math.round(speed * 100) / 100).replace(".", ",")}×`;
}

/** Seconds between ruler labels so they sit at least `minPx` apart. */
export function tickStep(pxPerSecond: number, minPx = 64): number {
  const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
  return steps.find((step) => step * pxPerSecond >= minPx) ?? steps[steps.length - 1];
}

export const MAX_ZOOM = 20;

/** Pixels per second for zoom `value` (0 = whole video fits, 1 = 20 px/s). */
export function zoomScale(fit: number, value: number): number {
  if (fit >= MAX_ZOOM) return fit;
  return fit * Math.pow(MAX_ZOOM / fit, clamp(value, 0, 1));
}

/** Source times of the thumbnail grid: at most `count` frames across the video. */
export function thumbGrid(duration: number, count = 60): { step: number; times: number[] } {
  if (!(duration > 0)) return { step: 1, times: [] };
  const step = Math.max(duration / count, 1);
  const times: number[] = [];
  for (let time = step / 2; time < duration && times.length < count; time += step) times.push(ms(time));
  return { step, times };
}
