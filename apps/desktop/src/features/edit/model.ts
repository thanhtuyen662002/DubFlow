/**
 * Cắt ghép and Chèn: an edit is an ordered list of segments, each playing
 * [start, end) of one media file (the finished final_vi.mp4 is media "main",
 * inserted clips are other media), in that media's seconds, at its own speed
 * and volume; the edited output is the segments back to back. A joint may
 * carry a transition, and overlays play other media over the output. Âm
 * thanh adds sound clips (sound effects or the user's audio files) mixed over
 * the output's own sound, which has one volume for the whole video. Chữ adds
 * texts drawn over the picture at output times, burned in by libass.
 * Every operation is pure and returns a new edit, or the same object when
 * nothing changes, so the history can skip no-op steps.
 */
import { isFont, type SubtitleFont } from "./fonts.ts";

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
export const PROJECT_SCHEMA = 4;
/** Added sound clips in one edit. */
export const MAX_AUDIO = 100;
/** The shortest sound clip (some clicks last 30 ms). */
export const MIN_AUDIO = 0.02;
export const MAX_FADE = 3;
/** "Giảm âm gốc khi phát": how far the original sound dips under a clip. */
export const MAX_DUCK_DB = 24;
export const DEFAULT_DUCK_DB = 10;
export const MAIN = "main";
/** Chữ: texts in one edit, characters in one text, and their limits. */
export const MAX_TEXTS = 50;
export const MAX_TEXT_CHARS = 300;
export const MIN_TEXT = 0.2;
export const DEFAULT_TEXT_SECONDS = 3;
/** Font size in pixels on a frame whose short side is 1080. */
export const MIN_TEXT_SIZE = 16;
export const MAX_TEXT_SIZE = 240;
export const MAX_TEXT_OUTLINE = 12;
/** Wrapping width as a fraction of the frame width. */
export const MIN_TEXT_WIDTH = 0.1;
export const MIN_ANIM = 0.1;
export const MAX_ANIM = 2;
export const TEXT_ANIMATIONS = ["none", "fade", "pop", "slide-up", "typewriter"] as const;
export const TEXT_ALIGNS = ["left", "center", "right"] as const;

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
export type AudioSource = { readonly kind: "sfx"; readonly id: string } | { readonly kind: "file"; readonly path: string };
/**
 * A sound over the output: `at` is output time, `start`/`end` its own seconds
 * (`duration` is the whole sound), `volume` 0–2, fades in seconds, and
 * `duckDb` > 0 lowers the original sound by that much while it plays.
 * Clips are anchored to output time: they stay put when segments are
 * inserted, cut or moved, unlike overlays, which ripple with inserts.
 */
export type AudioClip = {
  readonly id: string; readonly source: AudioSource; readonly title: string; readonly duration: number;
  readonly at: number; readonly start: number; readonly end: number;
  readonly volume: number; readonly fadeIn: number; readonly fadeOut: number; readonly duckDb: number;
};
/** A sound before it joins an edit. */
export type AudioInfo = { source: AudioSource; title: string; duration: number };
export type Edit = {
  /** Seconds of the main video. */
  readonly duration: number;
  /** "main" first, then the other media the segments and overlays use. */
  readonly media: readonly Media[];
  readonly segments: readonly Segment[];
  /** Keyed by the id of the segment before the joint; absent = a cut. */
  readonly transitions: Readonly<Record<string, Transition>>;
  readonly overlays: readonly Overlay[];
  /** Added sounds, in the order they were added. */
  readonly audio: readonly AudioClip[];
  /** "Âm thanh gốc": the volume of the output's own sound (segments and overlays), 0–2. */
  readonly mainVolume: number;
  /** Chữ: texts over the picture, later ones drawn on top. */
  readonly texts: readonly TextClip[];
  readonly nextId: number;
};
export type TextAnimation = typeof TEXT_ANIMATIONS[number];
export type TextAlign = typeof TEXT_ALIGNS[number];
/**
 * A text over the picture (Chữ): shown from output time `at` for `duration`
 * seconds, anchored to output time like sound clips. `x`, `y` are the
 * normalized centre of its box, `w` the box width (lines wrap inside it),
 * `size` and `outline_width` pixels on a frame whose short side is 1080,
 * `box` a colour behind the text or null.
 */
export type TextClip = {
  readonly id: string; readonly text: string; readonly at: number; readonly duration: number;
  readonly x: number; readonly y: number; readonly w: number;
  readonly font: SubtitleFont; readonly size: number; readonly color: string; readonly outline: string; readonly outline_width: number;
  readonly box: string | null; readonly box_opacity: number; readonly bold: boolean; readonly italic: boolean; readonly align: TextAlign;
  readonly animation: TextAnimation; readonly anim_duration: number;
};
/** Everything about a text but its words and times. */
export type TextLook = Omit<TextClip, "id" | "text" | "at" | "duration">;
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
    transitions: {}, overlays: [], audio: [], mainVolume: 1, texts: [], nextId: 2,
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
/**
 * Where an edit opens: a queue video from its player's time (the player
 * showed that video), any other video at its beginning (Issue #497).
 */
/**
 * What the editor opens a video as: the host's answer wins. A page's kind
 * the host could not prove (a run DubFlow kept no record of) opens as a plain
 * file, without the job, template or documentary behaviour (Issue #497).
 */
export function resolveTarget<T extends { kind: string; job?: unknown; seed?: unknown }>(target: T, hostKind: unknown): T {
  if (hostKind !== "file" || target.kind === "file") return target;
  const { job: _job, seed: _seed, ...rest } = target;
  return { ...rest, kind: "file" } as unknown as T;
}

/**
 * The editor modes a video has: Mẫu for Bán hàng videos, Phụ đề for dubbing
 * jobs that can be re-run, Che for every video (Issue #510: a job re-runs
 * with it, any other video gets it at export).
 */
export function modeOffered(mode: string, kind: string, reviewable: boolean): boolean {
  if (mode === "template") return kind === "product";
  if (mode === "subs") return kind === "job" && reviewable;
  // Tạo thêm: đóng gói, Shorts and đổi khung work from a dubbing job's outputs.
  if (mode === "more") return kind === "job";
  return true;
}

export function openingTime(kind: string, edit: Edit, playerTime: number | null): number {
  return kind === "job" && playerTime !== null ? sourceToOutput(edit, playerTime) ?? 0 : 0;
}

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

/** The new segment, overlay, sound or text an operation added, if any. */
export function addedId(before: Edit, after: Edit): string | null {
  const ids = new Set([...before.segments.map((segment) => segment.id), ...before.overlays.map((overlay) => overlay.id),
    ...before.audio.map((clip) => clip.id), ...before.texts.map((text) => text.id)]);
  return after.segments.find((segment) => !ids.has(segment.id))?.id ?? after.overlays.find((overlay) => !ids.has(overlay.id))?.id
    ?? after.audio.find((clip) => !ids.has(clip.id))?.id ?? after.texts.find((text) => !ids.has(text.id))?.id ?? null;
}

/**
 * Insert `range` of a media (the whole file by default) at output time `t`:
 * a segment under `t` is split there and the clip goes after its left part
 * (or before/after it within 0.1 s of its edges); everything after ripples,
 * overlays included. Sound clips stay at their output time (see AudioClip).
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

// ---------- Âm thanh: sound clips over the output ----------

const fade = (value: number): number => Math.round(clamp(value, 0, MAX_FADE) * 10) / 10;

/** Seconds a sound plays in the output (cut at the end of the output). */
export function audioLength(clip: Pick<AudioClip, "at" | "start" | "end">, total: number): number {
  return Math.max(0, Math.min(clip.end - clip.start, total - clip.at));
}

/** Fades that fit the clip: each within 0–3 s, together no longer than it plays. */
function fitFades(clip: AudioClip, total: number): AudioClip {
  const length = audioLength(clip, total);
  const fadeIn = Math.min(fade(clip.fadeIn), floorMs(length));
  const fadeOut = Math.min(fade(clip.fadeOut), floorMs(Math.max(0, length - fadeIn)));
  return fadeIn === clip.fadeIn && fadeOut === clip.fadeOut ? clip : { ...clip, fadeIn, fadeOut };
}

function changeAudio(edit: Edit, id: string, change: (clip: AudioClip) => AudioClip): Edit {
  const index = edit.audio.findIndex((clip) => clip.id === id);
  if (index < 0) return edit;
  const before = edit.audio[index];
  const after = fitFades(change(before), outputDuration(edit));
  if ((Object.keys(after) as Array<keyof AudioClip>).every((key) => after[key] === before[key])) return edit;
  return { ...edit, audio: [...edit.audio.slice(0, index), after, ...edit.audio.slice(index + 1)] };
}

/** A whole sound from output time `at` (kept inside the output); no-op at 100 sounds or for one too short. */
export function addAudio(edit: Edit, info: AudioInfo, at: number): Edit {
  const total = outputDuration(edit);
  if (edit.audio.length >= MAX_AUDIO || !Number.isFinite(at) || !(info.duration >= MIN_AUDIO) || total < MIN_AUDIO) return edit;
  if (info.source.kind === "sfx" ? !info.source.id : !info.source.path) return edit;
  const clip: AudioClip = {
    id: `a${edit.nextId}`, source: info.source, title: info.title.slice(0, 300), duration: info.duration,
    at: ms(clamp(at, 0, total - MIN_AUDIO)), start: 0, end: ms(info.duration), volume: 1, fadeIn: 0, fadeOut: 0, duckDb: 0,
  };
  return { ...edit, audio: [...edit.audio, clip], nextId: edit.nextId + 1 };
}

/** Output start, and the part of its sound a clip plays (at least 20 ms). */
export function setAudioTime(edit: Edit, id: string, times: { at?: number; start?: number; end?: number }): Edit {
  if (![times.at, times.start, times.end].every((value) => value === undefined || Number.isFinite(value))) return edit;
  const total = outputDuration(edit);
  return changeAudio(edit, id, (clip) => {
    const start = ms(clamp(times.start ?? clip.start, 0, clip.duration - MIN_AUDIO));
    const end = ms(clamp(times.end ?? clip.end, start + MIN_AUDIO, clip.duration));
    const at = ms(clamp(times.at ?? clip.at, 0, Math.max(0, total - MIN_AUDIO)));
    return { ...clip, at, start, end };
  });
}

/** Volume 0–200 %, fades 0–3 s (0.1 s steps) and ducking 0 (off) or 1–24 dB. */
export function setAudioProps(edit: Edit, id: string, props: { volume?: number; fadeIn?: number; fadeOut?: number; duckDb?: number }): Edit {
  if (![props.volume, props.fadeIn, props.fadeOut, props.duckDb].every((value) => value === undefined || Number.isFinite(value))) return edit;
  return changeAudio(edit, id, (clip) => ({
    ...clip,
    volume: props.volume === undefined ? clip.volume : Math.round(clamp(props.volume, 0, MAX_VOLUME) * 100) / 100,
    fadeIn: props.fadeIn === undefined ? clip.fadeIn : fade(props.fadeIn),
    fadeOut: props.fadeOut === undefined ? clip.fadeOut : fade(props.fadeOut),
    duckDb: props.duckDb === undefined ? clip.duckDb : Math.round(clamp(props.duckDb, 0, MAX_DUCK_DB)),
  }));
}

export function removeAudio(edit: Edit, id: string): Edit {
  if (!edit.audio.some((clip) => clip.id === id)) return edit;
  return { ...edit, audio: edit.audio.filter((clip) => clip.id !== id) };
}

/** "Âm thanh gốc", 0–200 %. */
export function setMainVolume(edit: Edit, volume: number): Edit {
  if (!Number.isFinite(volume)) return edit;
  const value = Math.round(clamp(volume, 0, MAX_VOLUME) * 100) / 100;
  return value === edit.mainVolume ? edit : { ...edit, mainVolume: value };
}

/** How loud a clip is at output time `time` (0 outside it), its fades included. */
export function audioGain(clip: AudioClip, time: number, total: number): number {
  const length = audioLength(clip, total);
  const into = time - clip.at;
  if (into < 0 || into >= length) return 0;
  let gain = clip.volume;
  if (clip.fadeIn > 0 && into < clip.fadeIn) gain *= into / clip.fadeIn;
  if (clip.fadeOut > 0 && length - into < clip.fadeOut) gain *= (length - into) / clip.fadeOut;
  return gain;
}

/** The original sound's level at output time `time`: its volume, lowered under ducking clips (the deepest one wins). */
export function mainLevel(edit: Edit, time: number, total = outputDuration(edit)): number {
  let duck = 0;
  for (const clip of edit.audio) {
    if (clip.duckDb > 0 && time >= clip.at && time < clip.at + audioLength(clip, total)) duck = Math.max(duck, clip.duckDb);
  }
  return edit.mainVolume * Math.pow(10, -duck / 20);
}

/** Rows for the timeline: each clip in the first row where it overlaps nothing. */
export function audioLanes(clips: readonly Pick<AudioClip, "id" | "at" | "start" | "end">[], total: number): Map<string, number> {
  const ends: number[] = [];
  const lanes = new Map<string, number>();
  for (const clip of [...clips].sort((a, b) => a.at - b.at)) {
    const end = clip.at + Math.max(audioLength(clip, total), 0.001);
    let lane = ends.findIndex((last) => last <= clip.at + 1e-6);
    if (lane < 0) lane = ends.push(end) - 1;
    else ends[lane] = end;
    lanes.set(clip.id, lane);
  }
  return lanes;
}

// ---------- Chữ: texts over the picture ----------

const HEX_COLOR = /^#[0-9A-Fa-f]{6}$/;
const isHex = (value: unknown): value is string => typeof value === "string" && HEX_COLOR.test(value);

/** The look "Thêm chữ" starts from. */
export const DEFAULT_TEXT_LOOK: TextLook = {
  x: 0.5, y: 0.5, w: 0.8, font: "Arial", size: 72, color: "#FFFFFF", outline: "#000000", outline_width: 4,
  box: null, box_opacity: 0.6, bold: true, italic: false, align: "center", animation: "none", anim_duration: 0.5,
};

/**
 * Words as they render: composed (NFC) so libass and the browser shape
 * Vietnamese marks alike, CR/LF as one line break, no other control
 * characters, at most 300 characters.
 */
export function cleanTextValue(value: string): string {
  const text = value.normalize("NFC").replace(/\r\n?/g, "\n").replace(/[\u0000-\u0009\u000B-\u001F\u007F-\u009F\u2028\u2029]/g, "");
  return [...text].slice(0, MAX_TEXT_CHARS).join("");
}

/** Seconds a text shows in the output (cut at the end of the output). */
export function textLength(text: Pick<TextClip, "at" | "duration">, total: number): number {
  return Math.max(0, Math.min(text.duration, total - text.at));
}

/** How long the animation runs as it renders: a fade in and out shares the text's length, the others take at most all of it. */
export function animSpan(text: Pick<TextClip, "animation" | "anim_duration">, length: number): number {
  if (text.animation === "none") return 0;
  return floorMs(Math.max(0, Math.min(text.anim_duration, text.animation === "fade" ? length / 2 : length)));
}

/** Every value within its limits and steps; the box keeps inside the frame sideways. */
function fitText(text: TextClip): TextClip {
  const w = unit(clamp(text.w, MIN_TEXT_WIDTH, 1));
  return {
    ...text, w, x: unit(clamp(text.x, w / 2, 1 - w / 2)), y: unit(clamp(text.y, 0, 1)),
    size: Math.round(clamp(text.size, MIN_TEXT_SIZE, MAX_TEXT_SIZE)),
    outline_width: Math.round(clamp(text.outline_width, 0, MAX_TEXT_OUTLINE) * 2) / 2,
    box_opacity: Math.round(clamp(text.box_opacity, 0, 1) * 20) / 20,
    anim_duration: Math.round(clamp(text.anim_duration, MIN_ANIM, MAX_ANIM) * 10) / 10,
  };
}

function changeText(edit: Edit, id: string, change: (text: TextClip) => TextClip): Edit {
  const index = edit.texts.findIndex((text) => text.id === id);
  if (index < 0) return edit;
  const before = edit.texts[index];
  const after = fitText(change(before));
  if ((Object.keys(after) as Array<keyof TextClip>).every((key) => after[key] === before[key])) return edit;
  return { ...edit, texts: [...edit.texts.slice(0, index), after, ...edit.texts.slice(index + 1)] };
}

/** Output start and length that fit the output: at least 0.2 s, ending by the end of the video. */
function placeTime(at: number, duration: number, total: number): { at: number; duration: number } {
  const start = ms(clamp(at, 0, Math.max(0, total - MIN_TEXT)));
  return { at: start, duration: ms(clamp(duration, MIN_TEXT, Math.max(MIN_TEXT, total - start))) };
}

/** The valid parts of a look; anything else is left out. */
function validLook(look: Partial<TextLook>): Partial<TextLook> {
  const value: Record<string, unknown> = {};
  for (const key of ["x", "y", "w", "size", "outline_width", "box_opacity", "anim_duration"] as const) {
    if (finite(look[key])) value[key] = look[key];
  }
  if (isFont(look.font)) value.font = look.font;
  if (isHex(look.color)) value.color = look.color.toUpperCase();
  if (isHex(look.outline)) value.outline = look.outline.toUpperCase();
  if (look.box === null) value.box = null;
  else if (isHex(look.box)) value.box = look.box.toUpperCase();
  if (typeof look.bold === "boolean") value.bold = look.bold;
  if (typeof look.italic === "boolean") value.italic = look.italic;
  if ((TEXT_ALIGNS as readonly unknown[]).includes(look.align)) value.align = look.align;
  if ((TEXT_ANIMATIONS as readonly unknown[]).includes(look.animation)) value.animation = look.animation;
  return value as Partial<TextLook>;
}

/** A text from output time `at` (3 s, or to the end of the video); no-op at 50 texts, for empty words or a video too short. */
export function addText(edit: Edit, at: number, text = "Chữ của bạn", look: Partial<TextLook> = {}, seconds = DEFAULT_TEXT_SECONDS): Edit {
  const total = outputDuration(edit);
  if (edit.texts.length >= MAX_TEXTS || !Number.isFinite(at) || !Number.isFinite(seconds) || total < MIN_TEXT) return edit;
  const words = cleanTextValue(text);
  if (!words.trim()) return edit;
  const clip = fitText({ ...DEFAULT_TEXT_LOOK, ...validLook(look), id: `t${edit.nextId}`, text: words, ...placeTime(at, seconds, total) });
  return { ...edit, texts: [...edit.texts, clip], nextId: edit.nextId + 1 };
}

/** New words or look; invalid values are ignored, and empty words keep the old ones. */
export function setTextProps(edit: Edit, id: string, props: Partial<TextLook> & { text?: string }): Edit {
  return changeText(edit, id, (text) => {
    const words = props.text === undefined ? text.text : cleanTextValue(props.text);
    return { ...text, ...validLook(props), text: words.trim() ? words : text.text };
  });
}

/** Output start and length (at least 0.2 s, kept inside the output). */
export function setTextTime(edit: Edit, id: string, times: { at?: number; duration?: number }): Edit {
  if (![times.at, times.duration].every((value) => value === undefined || Number.isFinite(value))) return edit;
  const total = outputDuration(edit);
  return changeText(edit, id, (text) => ({ ...text, ...placeTime(times.at ?? text.at, times.duration ?? text.duration, total) }));
}

/** New centre; the box stays inside the frame sideways. */
export function moveText(edit: Edit, id: string, x: number, y: number): Edit {
  if (!Number.isFinite(x) || !Number.isFinite(y)) return edit;
  return changeText(edit, id, (text) => ({ ...text, x, y }));
}

/** New box width (10–100 % of the frame), centred at `x` when given. */
export function resizeText(edit: Edit, id: string, w: number, x?: number): Edit {
  if (!Number.isFinite(w) || (x !== undefined && !Number.isFinite(x))) return edit;
  return changeText(edit, id, (text) => ({ ...text, w, x: x ?? text.x }));
}

export function removeText(edit: Edit, id: string): Edit {
  if (!edit.texts.some((text) => text.id === id)) return edit;
  return { ...edit, texts: edit.texts.filter((text) => text.id !== id) };
}

/** A copy right after the text when it fits there, else at the same time a little lower. */
export function duplicateText(edit: Edit, id: string): Edit {
  const source = edit.texts.find((text) => text.id === id);
  const total = outputDuration(edit);
  if (!source || edit.texts.length >= MAX_TEXTS) return edit;
  const after = source.at + textLength(source, total);
  const placed = total - after >= MIN_TEXT ? placeTime(after, source.duration, total) : { y: source.y + 0.08 };
  const copy = fitText({ ...source, id: `t${edit.nextId}`, ...placed });
  return { ...edit, texts: [...edit.texts, copy], nextId: edit.nextId + 1 };
}

/** The texts on screen at output time `time`, bottom first. */
export function textsAt(edit: Edit, time: number, total = outputDuration(edit)): TextClip[] {
  return edit.texts.filter((text) => time >= text.at && time < text.at + textLength(text, total));
}

// ---------- A new render of the main video ----------

/**
 * The edit carried to a new render of the main video lasting `duration`
 * (Mẫu, Issue #426). Parts of the main video keep their times: those that
 * reached the old end reach the new end, the rest are cut to the new length
 * and parts past it go. Inserted videos stay; overlays, sounds and texts that
 * no longer start inside the output go. `trimmed` counts what was cut or
 * removed, for a warning.
 */
export function retargetEdit(edit: Edit, duration: number, info: Partial<Omit<MediaInfo, "duration" | "kind">> = {}): { edit: Edit; trimmed: number } {
  const old = mediaOf(edit, MAIN);
  const main = mainMedia(duration, { path: old?.path, title: old?.title, width: old?.width, height: old?.height, fps: old?.fps, hasAudio: old?.hasAudio, ...info });
  const end = ms(duration);
  let trimmed = 0;
  let nextId = edit.nextId;
  const segments: Segment[] = [];
  for (const segment of edit.segments) {
    if (segment.mediaId !== MAIN) {
      segments.push(segment);
      continue;
    }
    const reachedEnd = segment.end >= edit.duration - 0.05;
    if (segment.start > end - MIN_SEGMENT) {
      trimmed += 1;
      continue;
    }
    const cut = reachedEnd ? end : Math.min(segment.end, end);
    if (cut < segment.end - 0.0005 && !reachedEnd) trimmed += 1;
    segments.push({ ...segment, end: cut });
  }
  if (!segments.length) segments.push({ id: `s${nextId++}`, mediaId: MAIN, start: 0, end, speed: 1, volume: 1 });
  const kept = new Set(segments.slice(0, -1).map((segment) => segment.id));
  const transitions = Object.fromEntries(Object.entries(edit.transitions).filter(([id]) => kept.has(id)));
  const next = pruneMedia({ ...edit, duration, media: [main, ...edit.media.filter((media) => media.id !== MAIN)], segments, transitions, nextId });
  const total = outputDuration(next);
  const overlays = next.overlays.filter((overlay) => overlay.at < total - MIN_SEGMENT);
  const audio = next.audio.filter((clip) => clip.at < total - MIN_AUDIO);
  const texts = next.texts.filter((text) => text.at < total - MIN_TEXT);
  trimmed += next.overlays.length - overlays.length + next.audio.length - audio.length + next.texts.length - texts.length;
  return { edit: pruneMedia({ ...next, overlays, audio, texts }), trimmed };
}

// ---------- Comparing, exporting ----------

/** The whole main video once, at normal speed and volume, nothing added: nothing to export. */
export function isUnchanged(edit: Edit, duration: number): boolean {
  const [only] = edit.segments;
  return edit.segments.length === 1 && only.mediaId === MAIN && only.start <= 0.001 && only.end >= duration - 0.001
    && only.speed === 1 && only.volume === 1 && !edit.overlays.length && !edit.audio.length && edit.mainVolume === 1 && !edit.texts.length;
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
export type PlanAudio = {
  source: { kind: "sfx"; id: string } | { kind: "file"; path: string };
  at: number; start: number; end: number; volume: number; fade_in: number; fade_out: number; duck_db: number;
};
/** A text as the host draws it: its shown length and animation span already fitted to the output. */
export type PlanText = Omit<TextClip, "id">;
/** What the host renders: media 0 is the main video, `media` lists the others by path. */
export type ExportPlan = {
  media: string[]; segments: PlanSegment[]; transitions: PlanTransition[]; overlays: PlanOverlay[]; audio: PlanAudio[]; mainVolume: number;
  texts: PlanText[];
};

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
  const audio = edit.audio.filter((clip) => audioLength(clip, total) >= MIN_AUDIO).map(planAudio);
  const texts = edit.texts.filter((text) => text.text.trim() && textLength(text, total) >= 0.05).map((text) => planText(text, total));
  return { media, segments, transitions, overlays, audio, mainVolume: edit.mainVolume, texts };
}

function planText({ id: _, ...text }: TextClip, total: number): PlanText {
  const duration = floorMs(textLength(text, total));
  return { ...text, duration, anim_duration: text.animation === "none" ? text.anim_duration : animSpan(text, duration) };
}

function planAudio(clip: AudioClip): PlanAudio {
  const source = clip.source.kind === "sfx" ? { kind: "sfx" as const, id: clip.source.id } : { kind: "file" as const, path: clip.source.path };
  return { source, at: clip.at, start: clip.start, end: clip.end, volume: clip.volume, fade_in: clip.fadeIn, fade_out: clip.fadeOut, duck_db: clip.duckDb };
}

const near = (a: unknown, b: unknown, tolerance = 0.0005): boolean => typeof a === "number" && typeof b === "number" && Math.abs(a - b) < tolerance;

/** An edit renders the same as an exported one (4a exports list only main segments, 4e adds sounds and the main volume, 4f texts). */
export function matchesExport(edit: Edit, exported: {
  media?: unknown; segments?: unknown; transitions?: unknown; overlays?: unknown; audio?: unknown; main_volume?: unknown; texts?: unknown;
}): boolean {
  const plan = exportPlan(edit);
  const list = (value: unknown): Array<Record<string, unknown>> => Array.isArray(value) ? value.filter((item) => item && typeof item === "object") : [];
  const media = Array.isArray(exported.media) ? exported.media : [];
  const segments = list(exported.segments), transitions = list(exported.transitions), overlays = list(exported.overlays), audio = list(exported.audio);
  const texts = list(exported.texts);
  const sameText = (other: Record<string, unknown>, text: PlanText): boolean => (Object.keys(text) as Array<keyof PlanText>).every((key) => {
    const value = text[key];
    return typeof value === "number" ? near(other[key], value, key === "x" || key === "y" || key === "w" ? 0.0002 : 0.0005) : other[key] === value;
  });
  if (texts.length !== plan.texts.length || !plan.texts.every((text, at) => sameText(texts[at], text))) return false;
  const sameSource = (other: unknown, source: PlanAudio["source"]): boolean => {
    const value = (other ?? {}) as Record<string, unknown>;
    return value.kind === source.kind && (source.kind === "sfx" ? value.id === source.id : typeof value.path === "string" && samePath(value.path, source.path));
  };
  return (exported.main_volume ?? 1) === plan.mainVolume
    && audio.length === plan.audio.length && plan.audio.every((clip, at) => {
      const other = audio[at];
      return sameSource(other.source, clip.source) && near(other.at, clip.at) && near(other.start, clip.start) && near(other.end, clip.end)
        && other.volume === clip.volume && near(other.fade_in, clip.fade_in) && near(other.fade_out, clip.fade_out) && other.duck_db === clip.duck_db;
    })
    && media.length === plan.media.length && media.every((path, at) => typeof path === "string" && samePath(path, plan.media[at]))
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

/**
 * The draft: schema 4, with the recently picked files kept in `media` (ids
 * "r…" when no part of the edit uses them), the sound clips in `audio`, the
 * original sound's volume in `main_volume` and the texts in `texts`.
 */
export function serializeProject(edit: Edit, changes: number, selected: string | null, recent: readonly Media[] = []): Record<string, unknown> {
  const extra = recent.filter((media) => !edit.media.some((used) => samePath(used.path, media.path)))
    .slice(0, RECENT_LIMIT).map((media, index) => ({ ...media, id: `r${index + 1}` }));
  return {
    schema_version: PROJECT_SCHEMA, duration: edit.duration, next_id: edit.nextId, changes, selected,
    media: [...edit.media, ...extra].map(serializeMedia),
    segments: edit.segments.map(({ id, mediaId, start, end, speed, volume }) => ({ id, mediaId, start, end, speed, volume })),
    transitions: Object.fromEntries(Object.entries(edit.transitions).map(([id, { type, duration }]) => [id, { type, duration }])),
    overlays: edit.overlays.map(({ id, mediaId, at, start, end, x, y, w, volume }) => ({ id, mediaId, at, start, end, x, y, w, volume })),
    audio: edit.audio.map(({ id, source, title, duration, at, start, end, volume, fadeIn, fadeOut, duckDb }) => ({
      id, source: source.kind === "sfx" ? { kind: "sfx", id: source.id } : { kind: "file", path: source.path }, title, duration,
      at, start, end, volume, fade_in: fadeIn, fade_out: fadeOut, duck_db: duckDb,
    })),
    main_volume: edit.mainVolume,
    texts: edit.texts.map((text) => ({ ...text })),
  };
}

function parseText(raw: unknown): TextClip | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Record<string, unknown>;
  const { id, text, at, duration, x, y, w, font, size, color, outline, outline_width, box, box_opacity, bold, italic, align, animation, anim_duration } = value;
  if (typeof id !== "string" || !/^t\d{1,9}$/.test(id) || typeof text !== "string" || !isFont(font)) return null;
  if (![at, duration, x, y, w, size, outline_width, box_opacity, anim_duration].every(finite)) return null;
  if (!isHex(color) || !isHex(outline) || (box !== null && !isHex(box)) || typeof bold !== "boolean" || typeof italic !== "boolean") return null;
  if (!(TEXT_ALIGNS as readonly unknown[]).includes(align) || !(TEXT_ANIMATIONS as readonly unknown[]).includes(animation)) return null;
  const words = cleanTextValue(text);
  if (!words.trim() || (at as number) < 0 || (duration as number) < MIN_TEXT - 1e-6) return null;
  return fitText({
    id, text: words, at: at as number, duration: duration as number, x: x as number, y: y as number, w: w as number, font, size: size as number,
    color: color.toUpperCase(), outline: outline.toUpperCase(), outline_width: outline_width as number, box: box === null ? null : (box as string).toUpperCase(),
    box_opacity: box_opacity as number, bold, italic, align: align as TextAlign, animation: animation as TextAnimation, anim_duration: anim_duration as number,
  });
}

/** A sound effect id from the catalog: lower-case letters, digits and dashes. */
export const SFX_ID = /^[a-z0-9][a-z0-9-]{0,39}$/;

function parseAudio(raw: unknown): AudioClip | null {
  if (!raw || typeof raw !== "object") return null;
  const { id, source, title, duration, at, start, end, volume, fade_in, fade_out, duck_db } = raw as Record<string, unknown>;
  if (typeof id !== "string" || !/^a\d{1,9}$/.test(id) || !source || typeof source !== "object") return null;
  const { kind, id: sfx, path } = source as Record<string, unknown>;
  const parsed: AudioSource | null = kind === "sfx" && typeof sfx === "string" && SFX_ID.test(sfx) ? { kind: "sfx", id: sfx }
    : kind === "file" && typeof path === "string" && path && path.length <= 2048 ? { kind: "file", path } : null;
  if (!parsed || ![duration, at, start, end, volume, fade_in, fade_out, duck_db].every(finite)) return null;
  const clip: AudioClip = { id, source: parsed, title: typeof title === "string" ? title.slice(0, 300) : "", duration: duration as number,
    at: at as number, start: start as number, end: Math.min(end as number, duration as number), volume: volume as number,
    fadeIn: fade_in as number, fadeOut: fade_out as number, duckDb: duck_db as number };
  if (clip.duration < MIN_AUDIO || clip.at < 0 || clip.start < 0 || clip.end - clip.start < MIN_AUDIO - 1e-6 || clip.volume < 0 || clip.volume > MAX_VOLUME
    || clip.fadeIn < 0 || clip.fadeIn > MAX_FADE || clip.fadeOut < 0 || clip.fadeOut > MAX_FADE || clip.duckDb < 0 || clip.duckDb > MAX_DUCK_DB) return null;
  return clip;
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
 * another render. Schema-1 drafts (4a) become all-main edits; schema-2
 * drafts (4b–4d) get no sounds and the original sound at 100 %; schema-3
 * drafts (4e) get no texts.
 */
export function parseProject(value: unknown, duration: number, info: Partial<Omit<MediaInfo, "duration" | "kind">> = {}): Project | null {
  if (!value || typeof value !== "object") return null;
  const data = value as Record<string, unknown>;
  const schema = data.schema_version;
  if ((schema !== 1 && schema !== 2 && schema !== 3 && schema !== 4) || typeof data.duration !== "number" || Math.abs(data.duration - duration) > 0.5) return null;
  if (!Array.isArray(data.segments) || !data.segments.length || data.segments.length > MAX_SEGMENTS) return null;
  const main = mainMedia(duration, info);
  const known = new Map<string, Media>([[MAIN, main]]);
  const recent: Media[] = [];
  if (schema !== 1) {
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
  if (schema !== 1 && data.transitions && typeof data.transitions === "object") {
    for (const [id, raw] of Object.entries(data.transitions as Record<string, unknown>)) {
      const { type, duration: length } = (raw ?? {}) as Record<string, unknown>;
      const index = segments.findIndex((segment) => segment.id === id);
      if (index < 0 || index >= segments.length - 1 || (type !== "fade" && type !== "dissolve") || !finite(length)) continue;
      transitions[id] = { type, duration: clamp(length, MIN_TRANSITION, MAX_TRANSITION) };
    }
  }
  const overlays: Overlay[] = [];
  if (schema !== 1 && Array.isArray(data.overlays)) {
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
  const audio: AudioClip[] = [];
  if ((schema === 3 || schema === 4) && Array.isArray(data.audio)) {
    for (const raw of (data.audio as unknown[]).slice(0, MAX_AUDIO)) {
      const clip = parseAudio(raw);
      if (!clip || ids.has(clip.id)) continue;
      ids.add(clip.id);
      count(clip.id);
      audio.push(clip);
    }
  }
  const mainVolume = (schema === 3 || schema === 4) && finite(data.main_volume) ? Math.round(clamp(data.main_volume, 0, MAX_VOLUME) * 100) / 100 : 1;
  const texts: TextClip[] = [];
  if (schema === 4 && Array.isArray(data.texts)) {
    for (const raw of (data.texts as unknown[]).slice(0, MAX_TEXTS)) {
      const text = parseText(raw);
      if (!text || ids.has(text.id)) continue;
      ids.add(text.id);
      count(text.id);
      texts.push(text);
    }
  }
  const nextId = typeof data.next_id === "number" && Number.isInteger(data.next_id) && data.next_id > highest ? data.next_id : highest + 1;
  const changes = typeof data.changes === "number" && Number.isInteger(data.changes) && data.changes >= 0 ? data.changes : 0;
  const selected = typeof data.selected === "string" && segments.some((segment) => segment.id === data.selected) ? data.selected : null;
  const edit = pruneMedia({ duration, media: [main, ...[...known.values()].filter((media) => media.id !== MAIN)], segments, transitions, overlays,
    audio, mainVolume, texts, nextId });
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
