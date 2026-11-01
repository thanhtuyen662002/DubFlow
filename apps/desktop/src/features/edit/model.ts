/**
 * Cắt ghép and Chèn: an edit is an ordered list of segments, each playing
 * [start, end) of one media file (the finished final_vi.mp4 is media "main",
 * inserted clips are other media), in that media's seconds, at its own speed
 * and volume; the edited output is the segments back to back. A joint may
 * carry a transition, and overlays play other media over the output.
 * Every operation is pure and returns a new edit, or the same object when
 * nothing changes, so the history can skip no-op steps.
 */

export const MIN_SEGMENT = 0.1;
export const MIN_SPEED = 0.5;
export const MAX_SPEED = 2;
export const MAX_VOLUME = 2;
export const MAX_SEGMENTS = 500;
export const MAX_MEDIA = 20;
export const MAX_OVERLAYS = 20;
export const MIN_TRANSITION = 0.2;
export const MAX_TRANSITION = 2;
export const DEFAULT_TRANSITION = 0.5;
/** Overlay width as a fraction of the frame width. */
export const MIN_OVERLAY_WIDTH = 0.2;
export const DEFAULT_OVERLAY_WIDTH = 0.35;
/** Gap between an overlay placed by a preset and the frame edge. */
export const OVERLAY_MARGIN = 0.03;
/** Recently picked files kept with the draft. */
export const RECENT_LIMIT = 12;
export const SPEED_PRESETS: readonly number[] = [0.5, 0.75, 1, 1.25, 1.5, 2];
export const HISTORY_LIMIT = 200;
/** A remapped cue piece shorter than this is dropped, unless it is the whole cue. */
export const MIN_CUE_PIECE = 0.1;
export const PROJECT_SCHEMA = 2;
export const MAIN = "main";

export type MediaKind = "main" | "job" | "file";
export type Media = {
  readonly id: string; readonly path: string; readonly title: string; readonly duration: number;
  readonly width: number; readonly height: number; readonly fps: number; readonly hasAudio: boolean; readonly kind: MediaKind;
};
/** A media file before it joins an edit. */
export type MediaInfo = Omit<Media, "id">;
export type Segment = {
  readonly id: string; readonly mediaId: string; readonly start: number; readonly end: number; readonly speed: number; readonly volume: number;
};
export type TransitionType = "fade" | "dissolve";
export type Transition = { readonly type: TransitionType; readonly duration: number };
/** `at` is output time; `start`/`end` are the overlay media's seconds; x, y, w are fractions of the frame (the height follows the media's aspect). */
export type Overlay = {
  readonly id: string; readonly mediaId: string; readonly at: number; readonly start: number; readonly end: number;
  readonly x: number; readonly y: number; readonly w: number; readonly volume: number;
};
export type Edit = {
  /** Seconds of the main video. */
  readonly duration: number;
  /** "main" first, then the other media the segments and overlays use. */
  readonly media: readonly Media[];
  readonly segments: readonly Segment[];
  /** Keyed by the id of the segment before the joint; absent = a cut. */
  readonly transitions: Readonly<Record<string, Transition>>;
  readonly overlays: readonly Overlay[];
  readonly nextId: number;
};
export type Cue = { start_ms: number; end_ms: number; text: string };
export type SourcePoint = { segment: Segment; index: number; sourceTime: number; outputStart: number };
export type OverlayPreset = "tl" | "tr" | "bl" | "br" | "center";

const ms = (value: number): number => Math.round(value * 1000) / 1000;
/** Down to the millisecond, so a clamped length never rounds past its limit. */
const floorMs = (value: number): number => Math.floor(value * 1000 + 1e-6) / 1000;
const unit = (value: number): number => Math.round(value * 10000) / 10000;
const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));

export function mainMedia(duration: number, info: Partial<Omit<MediaInfo, "duration" | "kind">> = {}): Media {
  return { path: "", title: "", width: 0, height: 0, fps: 30, hasAudio: true, ...info, id: MAIN, duration, kind: "main" };
}

export function createEdit(duration: number, info: Partial<Omit<MediaInfo, "duration" | "kind">> = {}): Edit {
  return {
    duration, media: [mainMedia(duration, info)],
    segments: [{ id: "s1", mediaId: MAIN, start: 0, end: ms(duration), speed: 1, volume: 1 }],
    transitions: {}, overlays: [], nextId: 2,
  };
}

export function mediaOf(edit: Edit, id: string): Media | undefined {
  return edit.media.find((media) => media.id === id);
}

function mediaDuration(edit: Edit, id: string): number {
  return id === MAIN ? edit.duration : mediaOf(edit, id)?.duration ?? 0;
}

/** Seconds a segment lasts in the edited output. */
export function segmentLength(segment: Segment): number {
  return (segment.end - segment.start) / segment.speed;
}

/**
 * The transition at the joint after segment `index` as it renders: its
 * duration kept within 0.2–2 s and half of each neighbour; null for a cut or
 * when the neighbours are too short for one.
 */
export function transitionAt(edit: Edit, index: number): Transition | null {
  const segment = edit.segments[index], next = edit.segments[index + 1];
  const stored = segment && next ? edit.transitions[segment.id] : undefined;
  if (!stored) return null;
  const room = Math.min(MAX_TRANSITION, segmentLength(segment) / 2, segmentLength(next) / 2);
  if (room < MIN_TRANSITION - 1e-9) return null;
  return { type: stored.type, duration: floorMs(clamp(stored.duration, MIN_TRANSITION, room)) };
}

/** A dissolve overlaps its neighbours; fade through black keeps the length. */
function overlapAfter(edit: Edit, index: number): number {
  const transition = transitionAt(edit, index);
  return transition?.type === "dissolve" ? transition.duration : 0;
}

/** Output time at which each segment starts. */
export function outputStarts(edit: Edit): number[] {
  const starts: number[] = [];
  let time = 0;
  edit.segments.forEach((segment, index) => {
    starts.push(time);
    time += segmentLength(segment) - overlapAfter(edit, index);
  });
  return starts;
}

export function outputDuration(edit: Edit): number {
  const count = edit.segments.length;
  if (!count) return 0;
  return outputStarts(edit)[count - 1] + segmentLength(edit.segments[count - 1]);
}

/** The segment playing at output time `t` (a boundary, or a dissolve, belongs to the next segment). */
export function outputToSource(edit: Edit, t: number): SourcePoint | null {
  const { segments } = edit;
  if (!segments.length) return null;
  const starts = outputStarts(edit);
  const total = starts[segments.length - 1] + segmentLength(segments[segments.length - 1]);
  const time = clamp(t, 0, total);
  if (time >= total) {
    const index = segments.length - 1;
    return { segment: segments[index], index, sourceTime: segments[index].end, outputStart: starts[index] };
  }
  let index = 0;
  while (index + 1 < segments.length && starts[index + 1] <= time) index += 1;
  const segment = segments[index];
  const sourceTime = clamp(segment.start + (time - starts[index]) * segment.speed, segment.start, segment.end);
  return { segment, index, sourceTime, outputStart: starts[index] };
}

/**
 * Output time of a time in one media: inside the preferred segment when it
 * holds that time (segments may share ranges), else the first that does.
 */
export function sourceToOutput(edit: Edit, sourceTime: number, preferredIndex = -1, mediaId = MAIN): number | null {
  const starts = outputStarts(edit);
  const at = (index: number): number => starts[index] + (sourceTime - edit.segments[index].start) / edit.segments[index].speed;
  const preferred = edit.segments[preferredIndex];
  if (preferred && preferred.mediaId === mediaId && sourceTime >= preferred.start && sourceTime <= preferred.end) return at(preferredIndex);
  const index = edit.segments.findIndex((segment) => segment.mediaId === mediaId && sourceTime >= segment.start && sourceTime < segment.end);
  return index < 0 ? null : at(index);
}

function replace(edit: Edit, index: number, segments: Segment[], nextId = edit.nextId): Edit {
  return { ...edit, segments: [...edit.segments.slice(0, index), ...segments, ...edit.segments.slice(index + 1)], nextId };
}

function withoutKey<T>(record: Readonly<Record<string, T>>, key: string): Record<string, T> {
  const { [key]: _, ...rest } = record;
  return rest;
}

/** Media no segment or overlay uses any more leave the edit ("main" stays). */
function pruneMedia(edit: Edit): Edit {
  const used = new Set<string>([MAIN, ...edit.segments.map((segment) => segment.mediaId), ...edit.overlays.map((overlay) => overlay.mediaId)]);
  const media = edit.media.filter((item) => used.has(item.id));
  return media.length === edit.media.length ? edit : { ...edit, media };
}

/** Split the segment under output time `t` in two; no-op within 0.1 s of its edges. */
export function split(edit: Edit, t: number): Edit {
  const point = outputToSource(edit, t);
  if (!point || edit.segments.length >= MAX_SEGMENTS) return edit;
  const { segment, index } = point;
  const at = ms(point.sourceTime);
  if (at - segment.start < MIN_SEGMENT || segment.end - at < MIN_SEGMENT) return edit;
  const id = `s${edit.nextId}`;
  const next = replace(edit, index, [{ ...segment, end: at }, { ...segment, id, start: at }], edit.nextId + 1);
  // The joint after the original segment now follows its right part.
  const moved = edit.transitions[segment.id];
  return moved ? { ...next, transitions: { ...withoutKey(edit.transitions, segment.id), [id]: moved } } : next;
}

/** Remove a segment; the last one left stays. */
export function remove(edit: Edit, id: string): Edit {
  const index = edit.segments.findIndex((segment) => segment.id === id);
  if (index < 0 || edit.segments.length <= 1) return edit;
  return pruneMedia({ ...edit, segments: edit.segments.filter((segment) => segment.id !== id), transitions: withoutKey(edit.transitions, id) });
}

/**
 * Move one edge of a segment to `sourceTime`. Segments are independent ranges
 * of their media, so only the media bounds and the 0.1 s minimum length apply.
 */
export function trim(edit: Edit, id: string, edge: "start" | "end", sourceTime: number): Edit {
  const index = edit.segments.findIndex((segment) => segment.id === id);
  if (index < 0 || !Number.isFinite(sourceTime)) return edit;
  const segment = edit.segments[index];
  const value = edge === "start"
    ? ms(clamp(sourceTime, 0, segment.end - MIN_SEGMENT))
    : ms(clamp(sourceTime, segment.start + MIN_SEGMENT, mediaDuration(edit, segment.mediaId)));
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

// ---------- Chèn: other media ----------

/** Windows paths compare without case and slash direction. */
export function samePath(a: string, b: string): boolean {
  return a.replace(/\//g, "\\").toLocaleLowerCase("en") === b.replace(/\//g, "\\").toLocaleLowerCase("en");
}

/** The media id for `info` in the edit, adding it when new; null at the 20-media limit or for unusable media. */
function attachMedia(edit: Edit, info: MediaInfo): { edit: Edit; id: string } | null {
  if (!(info.duration >= MIN_SEGMENT) || !info.path) return null;
  const existing = edit.media.find((media) => media.path && samePath(media.path, info.path));
  if (existing) return { edit, id: existing.id };
  if (edit.media.length >= MAX_MEDIA) return null;
  const id = `m${edit.nextId}`;
  const media: Media = { ...info, id, kind: info.kind === "main" ? "file" : info.kind };
  return { edit: { ...edit, media: [...edit.media, media], nextId: edit.nextId + 1 }, id };
}

/** The new segment or overlay an operation added, if any. */
export function addedId(before: Edit, after: Edit): string | null {
  const ids = new Set([...before.segments.map((segment) => segment.id), ...before.overlays.map((overlay) => overlay.id)]);
  return after.segments.find((segment) => !ids.has(segment.id))?.id ?? after.overlays.find((overlay) => !ids.has(overlay.id))?.id ?? null;
}

/**
 * Insert `range` of a media (the whole file by default) at output time `t`:
 * a segment under `t` is split there and the clip goes after its left part
 * (or before/after it within 0.1 s of its edges); everything after ripples,
 * overlays included.
 */
export function insertAt(edit: Edit, t: number, info: MediaInfo, range?: { start?: number; end?: number }): Edit {
  if (edit.segments.length >= MAX_SEGMENTS || !Number.isFinite(t)) return edit;
  const attached = attachMedia(edit, info);
  if (!attached) return edit;
  const length = mediaDuration(attached.edit, attached.id);
  const start = ms(clamp(range?.start ?? 0, 0, length - MIN_SEGMENT));
  const end = ms(clamp(range?.end ?? length, start + MIN_SEGMENT, length));
  let next = attached.edit;
  const total = outputDuration(next);
  const time = clamp(t, 0, total);
  let index = next.segments.length;
  const point = time < total - 1e-6 ? outputToSource(next, time) : null;
  if (point) {
    if (point.sourceTime - point.segment.start < MIN_SEGMENT) index = point.index;
    else if (point.segment.end - point.sourceTime < MIN_SEGMENT) index = point.index + 1;
    else {
      if (next.segments.length + 2 > MAX_SEGMENTS) return edit;
      next = split(next, time);
      index = point.index + 1;
    }
  }
  const joint = index < next.segments.length ? outputStarts(next)[index] : total;
  const segment: Segment = { id: `s${next.nextId}`, mediaId: attached.id, start, end, speed: 1, volume: 1 };
  next = { ...next, segments: [...next.segments.slice(0, index), segment, ...next.segments.slice(index)], nextId: next.nextId + 1 };
  const shift = outputDuration(next) - total;
  const overlays = next.overlays.map((overlay) => overlay.at >= joint - 1e-6 ? { ...overlay, at: ms(overlay.at + shift) } : overlay);
  return { ...next, overlays };
}

/** Add a media (or part of it) after the last segment. */
export function append(edit: Edit, info: MediaInfo, range?: { start?: number; end?: number }): Edit {
  return insertAt(edit, outputDuration(edit), info, range);
}

/** Overlay height as a fraction of the frame height. */
export function overlayHeight(edit: Edit, overlay: Pick<Overlay, "mediaId" | "w">): number {
  const frame = mediaOf(edit, MAIN);
  const media = mediaOf(edit, overlay.mediaId);
  if (!frame?.width || !frame.height || !media?.width || !media.height) return overlay.w;
  return overlay.w * (media.height / media.width) * (frame.width / frame.height);
}

/** Size within 20–100 % (less only for media too tall to fit otherwise), position inside the frame. */
function placeOverlay(edit: Edit, overlay: Overlay, x: number, y: number, w: number): Overlay {
  const ratio = overlayHeight(edit, { mediaId: overlay.mediaId, w: 1 });
  const widest = ratio > 1 ? 1 / ratio : 1;
  const width = unit(clamp(w, Math.min(MIN_OVERLAY_WIDTH, widest), widest));
  const height = width * ratio;
  return { ...overlay, w: width, x: unit(clamp(x, 0, Math.max(0, 1 - width))), y: unit(clamp(y, 0, Math.max(0, 1 - height))) };
}

function changeOverlay(edit: Edit, id: string, change: (overlay: Overlay) => Overlay): Edit {
  const index = edit.overlays.findIndex((overlay) => overlay.id === id);
  if (index < 0) return edit;
  const before = edit.overlays[index];
  const after = change(before);
  if ((Object.keys(after) as Array<keyof Overlay>).every((key) => after[key] === before[key])) return edit;
  return { ...edit, overlays: [...edit.overlays.slice(0, index), after, ...edit.overlays.slice(index + 1)] };
}

/** Show `range` of a media over the output from output time `at`, in the top-right corner. */
export function addOverlay(edit: Edit, info: MediaInfo, at: number, range?: { start?: number; end?: number }): Edit {
  if (edit.overlays.length >= MAX_OVERLAYS || !Number.isFinite(at)) return edit;
  const total = outputDuration(edit);
  const attached = attachMedia(edit, info);
  if (!attached || total < MIN_SEGMENT) return edit;
  const length = mediaDuration(attached.edit, attached.id);
  const start = ms(clamp(range?.start ?? 0, 0, length - MIN_SEGMENT));
  const span = Math.min(clamp(range?.end ?? length, start + MIN_SEGMENT, length) - start, total);
  let time = clamp(at, 0, total - MIN_SEGMENT);
  if (time + span > total) time = Math.max(0, total - span);
  const id = `o${attached.edit.nextId}`;
  const base: Overlay = { id, mediaId: attached.id, at: ms(time), start, end: ms(start + span), x: 0, y: 0, w: DEFAULT_OVERLAY_WIDTH, volume: 1 };
  const next = { ...attached.edit, nextId: attached.edit.nextId + 1 };
  const placed = placeOverlay(next, base, 1, OVERLAY_MARGIN, DEFAULT_OVERLAY_WIDTH);
  return { ...next, overlays: [...next.overlays, placeOverlay(next, placed, 1 - placed.w - OVERLAY_MARGIN, OVERLAY_MARGIN, placed.w)] };
}

export function moveOverlay(edit: Edit, id: string, x: number, y: number): Edit {
  if (!Number.isFinite(x) || !Number.isFinite(y)) return edit;
  return changeOverlay(edit, id, (overlay) => placeOverlay(edit, overlay, x, y, overlay.w));
}

/** New width, keeping the top-left corner where it fits. */
export function resizeOverlay(edit: Edit, id: string, w: number): Edit {
  if (!Number.isFinite(w)) return edit;
  return changeOverlay(edit, id, (overlay) => placeOverlay(edit, overlay, overlay.x, overlay.y, w));
}

export function overlayPreset(edit: Edit, id: string, preset: OverlayPreset): Edit {
  const overlay = edit.overlays.find((candidate) => candidate.id === id);
  if (!overlay) return edit;
  const height = overlayHeight(edit, overlay);
  const right = 1 - overlay.w - OVERLAY_MARGIN, bottom = 1 - height - OVERLAY_MARGIN;
  const [x, y] = preset === "tl" ? [OVERLAY_MARGIN, OVERLAY_MARGIN] : preset === "tr" ? [right, OVERLAY_MARGIN]
    : preset === "bl" ? [OVERLAY_MARGIN, bottom] : preset === "br" ? [right, bottom] : [(1 - overlay.w) / 2, (1 - height) / 2];
  return moveOverlay(edit, id, x, y);
}

/** Which preset the overlay sits at, if any. */
export function overlayPresetOf(edit: Edit, overlay: Overlay): OverlayPreset | null {
  for (const preset of ["tl", "tr", "bl", "br", "center"] as const) {
    const placed = overlayPreset(edit, overlay.id, preset).overlays.find((candidate) => candidate.id === overlay.id)!;
    if (Math.abs(placed.x - overlay.x) < 0.002 && Math.abs(placed.y - overlay.y) < 0.002) return preset;
  }
  return null;
}

/** Output start, and the part of its media an overlay shows (at least 0.1 s). */
export function setOverlayTime(edit: Edit, id: string, times: { at?: number; start?: number; end?: number }): Edit {
  if (![times.at, times.start, times.end].every((value) => value === undefined || Number.isFinite(value))) return edit;
  const total = outputDuration(edit);
  return changeOverlay(edit, id, (overlay) => {
    const length = mediaDuration(edit, overlay.mediaId);
    const start = ms(clamp(times.start ?? overlay.start, 0, length - MIN_SEGMENT));
    const end = ms(clamp(times.end ?? overlay.end, start + MIN_SEGMENT, length));
    const at = ms(clamp(times.at ?? overlay.at, 0, Math.max(0, total - MIN_SEGMENT)));
    return { ...overlay, at, start, end };
  });
}

export function setOverlayVolume(edit: Edit, id: string, volume: number): Edit {
  if (!Number.isFinite(volume)) return edit;
  return changeOverlay(edit, id, (overlay) => ({ ...overlay, volume: Math.round(clamp(volume, 0, MAX_VOLUME) * 100) / 100 }));
}

export function removeOverlay(edit: Edit, id: string): Edit {
  if (!edit.overlays.some((overlay) => overlay.id === id)) return edit;
  return pruneMedia({ ...edit, overlays: edit.overlays.filter((overlay) => overlay.id !== id) });
}

/** Seconds an overlay shows in the output (cut at the end of the output). */
export function overlayLength(edit: Edit, overlay: Overlay, total = outputDuration(edit)): number {
  return Math.max(0, Math.min(overlay.end - overlay.start, total - overlay.at));
}

/**
 * The transition at the joint after a segment; null makes it a cut. The
 * duration is kept within 0.2–2 s and half of each neighbour; no-op where
 * the neighbours are too short.
 */
export function setTransition(edit: Edit, afterId: string, type: TransitionType | null, duration = DEFAULT_TRANSITION): Edit {
  const index = edit.segments.findIndex((segment) => segment.id === afterId);
  if (index < 0 || index >= edit.segments.length - 1) return edit;
  if (type === null) return afterId in edit.transitions ? { ...edit, transitions: withoutKey(edit.transitions, afterId) } : edit;
  if (!Number.isFinite(duration)) return edit;
  const room = transitionRoom(edit, index);
  if (room < MIN_TRANSITION - 1e-9) return edit;
  const value = floorMs(clamp(duration, MIN_TRANSITION, room));
  const stored = edit.transitions[afterId];
  if (stored && stored.type === type && stored.duration === value) return edit;
  return { ...edit, transitions: { ...edit.transitions, [afterId]: { type, duration: value } } };
}

/** The longest transition the joint after segment `index` allows (below 0.2 s: none fits). */
export function transitionRoom(edit: Edit, index: number): number {
  const segment = edit.segments[index], next = edit.segments[index + 1];
  if (!segment || !next) return 0;
  return Math.min(MAX_TRANSITION, segmentLength(segment) / 2, segmentLength(next) / 2);
}

// ---------- Comparing, exporting ----------

/** The whole main video once, at normal speed and volume, nothing added: nothing to export. */
export function isUnchanged(edit: Edit, duration: number): boolean {
  const [only] = edit.segments;
  return edit.segments.length === 1 && only.mediaId === MAIN && only.start <= 0.001 && only.end >= duration - 0.001
    && only.speed === 1 && only.volume === 1 && !edit.overlays.length;
}

export function sameSegments(a: readonly Segment[], b: readonly Pick<Segment, "start" | "end" | "speed" | "volume">[]): boolean {
  return a.length === b.length && a.every((segment, index) => {
    const other = b[index];
    return Math.abs(segment.start - other.start) < 0.0005 && Math.abs(segment.end - other.end) < 0.0005
      && segment.speed === other.speed && segment.volume === other.volume;
  });
}

export type PlanSegment = { media: number; start: number; end: number; speed: number; volume: number };
export type PlanTransition = { after: number; type: TransitionType; duration: number };
export type PlanOverlay = { media: number; at: number; start: number; end: number; x: number; y: number; w: number; volume: number };
/** What the host renders: media 0 is the main video, `media` lists the others by path. */
export type ExportPlan = { media: string[]; segments: PlanSegment[]; transitions: PlanTransition[]; overlays: PlanOverlay[] };

export function exportPlan(edit: Edit): ExportPlan {
  const media: string[] = [];
  const index = (id: string): number => {
    if (id === MAIN) return 0;
    const path = mediaOf(edit, id)?.path ?? "";
    let at = media.indexOf(path);
    if (at < 0) at = media.push(path) - 1;
    return at + 1;
  };
  const segments = edit.segments.map(({ mediaId, start, end, speed, volume }) => ({ media: index(mediaId), start, end, speed, volume }));
  const transitions: PlanTransition[] = [];
  edit.segments.forEach((_, at) => {
    const transition = transitionAt(edit, at);
    if (transition) transitions.push({ after: at, type: transition.type, duration: transition.duration });
  });
  const total = outputDuration(edit);
  const overlays = edit.overlays.filter((overlay) => overlayLength(edit, overlay, total) >= 0.05)
    .map(({ mediaId, at, start, end, x, y, w, volume }) => ({ media: index(mediaId), at, start, end, x, y, w, volume }));
  return { media, segments, transitions, overlays };
}

const near = (a: unknown, b: unknown, tolerance = 0.0005): boolean => typeof a === "number" && typeof b === "number" && Math.abs(a - b) < tolerance;

/** An edit renders the same as an exported one (4a exports list only main segments). */
export function matchesExport(edit: Edit, exported: { media?: unknown; segments?: unknown; transitions?: unknown; overlays?: unknown }): boolean {
  const plan = exportPlan(edit);
  const list = (value: unknown): Array<Record<string, unknown>> => Array.isArray(value) ? value.filter((item) => item && typeof item === "object") : [];
  const media = Array.isArray(exported.media) ? exported.media : [];
  const segments = list(exported.segments), transitions = list(exported.transitions), overlays = list(exported.overlays);
  return media.length === plan.media.length && media.every((path, at) => typeof path === "string" && samePath(path, plan.media[at]))
    && segments.length === plan.segments.length && plan.segments.every((segment, at) => {
      const other = segments[at];
      return (other.media ?? 0) === segment.media && near(other.start, segment.start) && near(other.end, segment.end)
        && other.speed === segment.speed && other.volume === segment.volume;
    })
    && transitions.length === plan.transitions.length && plan.transitions.every((transition, at) => {
      const other = transitions[at];
      return other.after === transition.after && other.type === transition.type && near(other.duration, transition.duration);
    })
    && overlays.length === plan.overlays.length && plan.overlays.every((overlay, at) => {
      const other = overlays[at];
      return other.media === overlay.media && near(other.at, overlay.at) && near(other.start, overlay.start) && near(other.end, overlay.end)
        && near(other.x, overlay.x, 0.0002) && near(other.y, overlay.y, 0.0002) && near(other.w, overlay.w, 0.0002) && other.volume === overlay.volume;
    });
}

export function sameEdit(a: Edit, b: Edit): boolean {
  return a === b || matchesExport(a, exportPlan(b));
}

/**
 * Subtitles on the edited timeline: cues fully inside deleted parts are
 * dropped, partial ones clipped, the rest shifted and scaled by each segment's
 * speed, in output order. Only main-video segments carry cues. A cue split
 * only by a cut that keeps both sides adjacent comes back as one cue.
 */
export function remapCues(cues: readonly Cue[], edit: Edit): Cue[] {
  const result: Array<Cue & { source: number }> = [];
  const starts = outputStarts(edit);
  const ordered = cues.map((cue, source) => ({ cue, source })).sort((a, b) => a.cue.start_ms - b.cue.start_ms || a.source - b.source);
  edit.segments.forEach((segment, index) => {
    if (segment.mediaId !== MAIN) return;
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
      if (sameEdit(this.#states[candidate], edit)) index = candidate;
    }
    if (index >= 0) {
      this.#exported = index;
    } else {
      this.#exported = -1;
      this.#base = 1 - this.#index;
    }
  }
}

// ---------- Draft file ----------

export type Project = { edit: Edit; changes: number; selected: string | null; recent: Media[] };

function serializeMedia({ id, path, title, duration, width, height, fps, hasAudio, kind }: Media): Record<string, unknown> {
  return { id, path, title, duration, width, height, fps, hasAudio, kind };
}

/** The draft: schema 2, with the recently picked files kept in `media` (ids "r…" when no part of the edit uses them). */
export function serializeProject(edit: Edit, changes: number, selected: string | null, recent: readonly Media[] = []): Record<string, unknown> {
  const extra = recent.filter((media) => !edit.media.some((used) => samePath(used.path, media.path)))
    .slice(0, RECENT_LIMIT).map((media, index) => ({ ...media, id: `r${index + 1}` }));
  return {
    schema_version: PROJECT_SCHEMA, duration: edit.duration, next_id: edit.nextId, changes, selected,
    media: [...edit.media, ...extra].map(serializeMedia),
    segments: edit.segments.map(({ id, mediaId, start, end, speed, volume }) => ({ id, mediaId, start, end, speed, volume })),
    transitions: Object.fromEntries(Object.entries(edit.transitions).map(([id, { type, duration }]) => [id, { type, duration }])),
    overlays: edit.overlays.map(({ id, mediaId, at, start, end, x, y, w, volume }) => ({ id, mediaId, at, start, end, x, y, w, volume })),
  };
}

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

function parseMedia(raw: unknown): Media | null {
  if (!raw || typeof raw !== "object") return null;
  const { id, path, title, duration, width, height, fps, hasAudio, kind } = raw as Record<string, unknown>;
  if (typeof id !== "string" || !/^(main|[mr]\d{1,9})$/.test(id) || typeof path !== "string" || path.length > 2048) return null;
  if (!finite(duration) || duration <= 0 || ![width, height, fps].every((number) => finite(number) && number >= 0)) return null;
  if (typeof hasAudio !== "boolean" || !["main", "job", "file"].includes(String(kind)) || (id === MAIN) !== (kind === "main")) return null;
  if (id !== MAIN && !path) return null;
  return { id, path, title: typeof title === "string" ? title.slice(0, 300) : "", duration, width: width as number, height: height as number,
    fps: fps as number, hasAudio, kind: kind as MediaKind };
}

/**
 * A saved draft for this video, or null when it is missing, invalid or for
 * another render. Schema-1 drafts (4a) become all-main edits.
 */
export function parseProject(value: unknown, duration: number, info: Partial<Omit<MediaInfo, "duration" | "kind">> = {}): Project | null {
  if (!value || typeof value !== "object") return null;
  const data = value as Record<string, unknown>;
  const schema = data.schema_version;
  if ((schema !== 1 && schema !== 2) || typeof data.duration !== "number" || Math.abs(data.duration - duration) > 0.5) return null;
  if (!Array.isArray(data.segments) || !data.segments.length || data.segments.length > MAX_SEGMENTS) return null;
  const main = mainMedia(duration, info);
  const known = new Map<string, Media>([[MAIN, main]]);
  const recent: Media[] = [];
  if (schema === 2) {
    if (!Array.isArray(data.media) || data.media.length > MAX_MEDIA + RECENT_LIMIT) return null;
    for (const raw of data.media as unknown[]) {
      const media = parseMedia(raw);
      if (!media || (media.id !== MAIN && known.has(media.id))) return null;
      if (media.id === MAIN) continue;
      if (media.id.startsWith("m")) known.set(media.id, media);
      if (media.kind === "file" && !recent.some((item) => samePath(item.path, media.path)) && recent.length < RECENT_LIMIT) recent.push(media);
    }
  }
  const ids = new Set<string>();
  const segments: Segment[] = [];
  let highest = 0;
  const count = (id: string): void => { highest = Math.max(highest, Number(id.slice(1)) || 0); };
  for (const media of known.values()) if (media.id !== MAIN) count(media.id);
  for (const raw of data.segments as unknown[]) {
    if (!raw || typeof raw !== "object") return null;
    const { id, start, end, speed, volume } = raw as Record<string, unknown>;
    const mediaId = schema === 1 ? MAIN : (raw as Record<string, unknown>).mediaId ?? MAIN;
    if (typeof id !== "string" || !/^s\d{1,9}$/.test(id) || ids.has(id)) return null;
    if (typeof mediaId !== "string" || !known.has(mediaId)) return null;
    if (![start, end, speed, volume].every(finite)) return null;
    const length = mediaId === MAIN ? ms(duration) : known.get(mediaId)!.duration;
    const segment = { id, mediaId, start: start as number, end: Math.min(end as number, length), speed: speed as number, volume: volume as number };
    if (segment.start < 0 || segment.end - segment.start < MIN_SEGMENT - 1e-6 || segment.speed < MIN_SPEED || segment.speed > MAX_SPEED
      || segment.volume < 0 || segment.volume > MAX_VOLUME) return null;
    ids.add(id);
    count(id);
    segments.push(segment);
  }
  const transitions: Record<string, Transition> = {};
  if (schema === 2 && data.transitions && typeof data.transitions === "object") {
    for (const [id, raw] of Object.entries(data.transitions as Record<string, unknown>)) {
      const { type, duration: length } = (raw ?? {}) as Record<string, unknown>;
      const index = segments.findIndex((segment) => segment.id === id);
      if (index < 0 || index >= segments.length - 1 || (type !== "fade" && type !== "dissolve") || !finite(length)) continue;
      transitions[id] = { type, duration: clamp(length, MIN_TRANSITION, MAX_TRANSITION) };
    }
  }
  const overlays: Overlay[] = [];
  if (schema === 2 && Array.isArray(data.overlays)) {
    for (const raw of (data.overlays as unknown[]).slice(0, MAX_OVERLAYS)) {
      if (!raw || typeof raw !== "object") continue;
      const { id, mediaId, at, start, end, x, y, w, volume } = raw as Record<string, unknown>;
      if (typeof id !== "string" || !/^o\d{1,9}$/.test(id) || ids.has(id) || typeof mediaId !== "string" || !known.has(mediaId)) continue;
      if (![at, start, end, x, y, w, volume].every(finite)) continue;
      const media = known.get(mediaId)!;
      const overlay = { id, mediaId, at: at as number, start: start as number, end: Math.min(end as number, media.duration),
        x: x as number, y: y as number, w: w as number, volume: volume as number };
      if (overlay.at < 0 || overlay.start < 0 || overlay.end - overlay.start < MIN_SEGMENT - 1e-6 || overlay.volume < 0 || overlay.volume > MAX_VOLUME
        || overlay.w <= 0 || overlay.w > 1 || overlay.x < 0 || overlay.x > 1 || overlay.y < 0 || overlay.y > 1) continue;
      ids.add(id);
      count(id);
      overlays.push(overlay);
    }
  }
  const nextId = typeof data.next_id === "number" && Number.isInteger(data.next_id) && data.next_id > highest ? data.next_id : highest + 1;
  const changes = typeof data.changes === "number" && Number.isInteger(data.changes) && data.changes >= 0 ? data.changes : 0;
  const selected = typeof data.selected === "string" && segments.some((segment) => segment.id === data.selected) ? data.selected : null;
  const edit = pruneMedia({ duration, media: [main, ...[...known.values()].filter((media) => media.id !== MAIN)], segments, transitions, overlays, nextId });
  return { edit, changes, selected, recent };
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
