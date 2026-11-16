/**
 * Nhịp dựng (Phase 9C): cut a part into short pieces and give pieces a slight, seeded zoom and tilt.
 *
 * Every value is drawn once from the seed and written into each piece's look, so the preview, the export and a retry
 * of the export all read the same numbers; nothing is drawn again at render time. The same seed, the same pieces
 * and the same limits give the same values. A piece's timing never changes: its part and speed stay, so the sound,
 * the subtitles, voices and sound effects (all placed on the output's clock) stay where they were. A turn is always
 * enlarged enough that no corner of the frame is empty (`tiltCover`). This is for the rhythm of the cut only; it is
 * not a way around a platform's copy checks.
 */
import { MAX_TILT, MAX_ZOOM_LOOK } from "./look.ts";
import { lookOf, MIN_SEGMENT, outputStarts, segmentLength, setLook, split, type Edit } from "./model.ts";

export type MotionLimits = {
  /** Largest zoom reached, e.g. 1.04 (4 % closer); 1 = no zoom. */
  readonly maxZoom: number;
  /** Largest tilt either way, in degrees; 0 = no tilt. */
  readonly maxTilt: number;
};

export const MOTION_PRESETS: ReadonlyArray<{ id: string; name: string; limits: MotionLimits }> = [
  { id: "light", name: "Rất nhẹ", limits: { maxZoom: 1.02, maxTilt: 0.5 } },
  { id: "normal", name: "Nhẹ", limits: { maxZoom: 1.04, maxTilt: 0.8 } },
  { id: "strong", name: "Rõ hơn", limits: { maxZoom: 1.08, maxTilt: 1.5 } },
];
export const DEFAULT_MOTION = MOTION_PRESETS[1].limits;
/** Kept low on purpose: a larger random move reads as shaking. */
export const MAX_MOTION_ZOOM = 1.15;
export const MIN_SPLIT_SECONDS = 0.5;

/** Mulberry32: a small, fast, seeded generator; the same seed gives the same numbers on every machine. */
export function generator(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A new seed for "Ngẫu nhiên lại". */
export function freshSeed(): number {
  return Math.floor(Math.random() * 0xffffffff) >>> 0;
}

function clean(limits: MotionLimits): MotionLimits {
  const zoom = Number.isFinite(limits.maxZoom) ? Math.min(Math.max(limits.maxZoom, 1), Math.min(MAX_MOTION_ZOOM, MAX_ZOOM_LOOK)) : 1;
  const tilt = Number.isFinite(limits.maxTilt) ? Math.min(Math.max(limits.maxTilt, 0), MAX_TILT) : 0;
  return { maxZoom: zoom, maxTilt: tilt };
}

const round = (value: number, step: number): number => Math.round(value * step) / step;

/**
 * The look values for the `index`-th piece drawn from `seed`: a slow zoom in or out between 1 and a share of the
 * limit, centred near the middle, and a tilt of 30–100 % of the limit, alternating sides from piece to piece (a
 * gentle rock, never two pieces leaning the same way in a row, which reads as a crooked camera).
 */
export function motionFor(seed: number, index: number, limits: MotionLimits): { zoom: { from: number; to: number; x: number; y: number } | null; tilt: number } {
  const { maxZoom, maxTilt } = clean(limits);
  const next = generator((seed ^ Math.imul(index + 1, 0x9e3779b1)) >>> 0);
  const reach = 1 + (maxZoom - 1) * (0.5 + next() * 0.5);
  const inwards = next() < 0.5;
  const x = round(0.5 + (next() - 0.5) * 0.1, 1000), y = round(0.5 + (next() - 0.5) * 0.1, 1000);
  const zoom = maxZoom > 1 ? { from: round(inwards ? 1 : reach, 100), to: round(inwards ? reach : 1, 100), x, y } : null;
  const first = generator(seed)() < 0.5 ? 0 : 1;  // which way the first piece leans; then they alternate
  const side = (index + first) % 2 === 0 ? 1 : -1;
  const tilt = maxTilt > 0 ? round(side * maxTilt * (0.3 + next() * 0.7), 100) : 0;
  return { zoom: zoom && zoom.from !== zoom.to ? zoom : null, tilt };
}

/** Apply seeded motion to the pieces `ids` (in timeline order). */
export function applyMotion(edit: Edit, ids: readonly string[], seed: number, limits: MotionLimits = DEFAULT_MOTION): Edit {
  const wanted = new Set(ids);
  let next = edit;
  let index = 0;
  for (const segment of edit.segments) {
    if (!wanted.has(segment.id)) continue;
    if (lookOf(segment).freeze !== null) {
      index += 1;
      continue;  // a held frame keeps its own look
    }
    const { zoom, tilt } = motionFor(seed, index, limits);
    next = setLook(next, segment.id, { zoom, tilt });
    index += 1;
  }
  return next;
}

/** Remove the zoom and tilt of the pieces `ids` (their other looks stay). */
export function clearMotion(edit: Edit, ids: readonly string[]): Edit {
  let next = edit;
  for (const id of ids) next = setLook(next, id, { zoom: null, tilt: 0 });
  return next;
}

/**
 * Cut the piece `id` every `seconds` of output time. The last piece takes what is left when that is at least half a
 * step (else it joins the one before). Returns the edit and the ids of the pieces, first to last.
 */
export function splitEvery(edit: Edit, id: string, seconds: number): { edit: Edit; ids: string[] } {
  const index = edit.segments.findIndex((segment) => segment.id === id);
  if (index < 0 || !(seconds >= MIN_SPLIT_SECONDS)) return { edit, ids: index < 0 ? [] : [id] };
  const segment = edit.segments[index];
  const start = outputStarts(edit)[index];
  const length = segmentLength(segment);
  const cuts: number[] = [];
  for (let at = seconds; length - at >= Math.max(seconds / 2, MIN_SEGMENT); at += seconds) cuts.push(start + at);
  let next = edit;
  for (const at of cuts) next = split(next, at);
  const first = next.segments.findIndex((piece) => piece.id === id);
  const ids = next.segments.slice(first, first + cuts.length + 1).map((piece) => piece.id);
  return { edit: next, ids };
}
