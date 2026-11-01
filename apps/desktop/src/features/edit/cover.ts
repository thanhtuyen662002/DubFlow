/**
 * Che (Issue #418): how the burned-in source captions are hidden and where.
 * The engine draws the same styles (engine/dubflow/media/adapter.py,
 * `cover_filtergraph`) and validates cover.json again; sizes in px are on
 * the 1080-line scale, like the subtitles.
 */
import { clampRegion, type BlurRegion } from "../blur/region.ts";

export type CoverMode = "stretch" | "blur" | "color" | "mosaic";
export type CoverDirection = "top" | "bottom" | "both";
export type CoverStyle = {
  mode: CoverMode;
  /** Stretch: the strip above, below, or above for the upper half and below for the lower one. */
  direction: CoverDirection;
  /** Stretch: rows in the strip, 2–12. */
  strip_px: number;
  /** Color: #RRGGBB. */
  color: string;
  /** Mosaic: block size, 8–64. */
  block: number;
  /** Stretch and mosaic: soft edges, 0–8. */
  feather: number;
};
export type Rect = BlurRegion;
/** A rectangle covered while it is on (both times, in source ms) or for the whole video. */
export type Region = Rect & { start_ms?: number; end_ms?: number };
/** A detected caption rectangle and its index in caption_scan.json. */
export type AutoRegion = Region & { index: number };
/** A rectangle drawn in Che. */
export type ManualRegion = Region & { id: string };
/** What the editor holds for the cover of one version. */
export type CoverState = { style: CoverStyle; manual: ManualRegion[]; fixed: Rect | null; disabled: ReadonlySet<number> };
/** cover.json as the host stores it (without schema_version). */
export type CoverDocument = { style: CoverStyle; regions: Region[]; disabled_auto: number[]; fixed: Rect | null };

export const COVER_STORAGE_KEY = "dubflow.coverStyle.v1";
export const MAX_MANUAL = 64;
export const MIN_STRIP = 2;
export const MAX_STRIP = 12;
export const MIN_BLOCK = 8;
export const MAX_BLOCK = 64;
export const MAX_FEATHER = 8;
/** New videos: stretch the edge above the caption. */
export const DEFAULT_COVER_STYLE: CoverStyle = { mode: "stretch", direction: "top", strip_px: 4, color: "#000000", block: 24, feather: 0 };
/** A version without cover.json was rendered with today's Gaussian blur. */
export const LEGACY_COVER_STYLE: CoverStyle = { ...DEFAULT_COVER_STYLE, mode: "blur" };

export const COVER_MODES: ReadonlyArray<{ id: CoverMode; label: string; short: string; hint: string }> = [
  { id: "stretch", label: "Kéo dãn mép (mặc định)", short: "Kéo mép", hint: "Kéo dãn dải ảnh sát mép vùng che, liền cảnh" },
  { id: "blur", label: "Làm mờ", short: "Làm mờ", hint: "Làm mờ chữ tại chỗ" },
  { id: "color", label: "Màu đặc", short: "Màu", hint: "Khối màu đặc, che chắc" },
  { id: "mosaic", label: "Khảm", short: "Khảm", hint: "Vỡ thành ô vuông" },
];
export const DIRECTIONS: ReadonlyArray<{ id: CoverDirection; label: string }> = [
  { id: "top", label: "Phía trên" }, { id: "bottom", label: "Phía dưới" }, { id: "both", label: "Hai phía" },
];
export const COVER_SWATCHES = ["#000000", "#1F2937", "#6B7280", "#FFFFFF"] as const;

const HEX = /^#[0-9A-Fa-f]{6}$/;
const MODES = new Set<string>(COVER_MODES.map((mode) => mode.id));
const SIDES = new Set<string>(DIRECTIONS.map((direction) => direction.id));
const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));
const integer = (value: unknown, low: number, high: number): value is number => Number.isInteger(value) && (value as number) >= low && (value as number) <= high;
const round4 = (value: number): number => Math.round(value * 10_000) / 10_000;

/** A stored or loaded style (missing options take their defaults), or null when anything is off. */
export function parseCoverStyle(value: unknown): CoverStyle | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  if (Object.keys(raw).some((key) => !(key in DEFAULT_COVER_STYLE)) || !MODES.has(String(raw.mode))) return null;
  const style = { ...DEFAULT_COVER_STYLE, ...raw } as Record<string, unknown>;
  if (!SIDES.has(String(style.direction)) || !integer(style.strip_px, MIN_STRIP, MAX_STRIP) || !integer(style.block, MIN_BLOCK, MAX_BLOCK)
    || !integer(style.feather, 0, MAX_FEATHER) || typeof style.color !== "string" || !HEX.test(style.color)) return null;
  return normalizeCoverStyle(style as CoverStyle);
}

/** The last applied style (`dubflow.coverStyle.v1`), or edge stretch for new videos. */
export function storedCoverStyle(raw: string | null): CoverStyle {
  if (!raw) return DEFAULT_COVER_STYLE;
  try {
    return parseCoverStyle(JSON.parse(raw)) ?? DEFAULT_COVER_STYLE;
  } catch {
    return DEFAULT_COVER_STYLE;
  }
}

export function normalizeCoverStyle(style: CoverStyle): CoverStyle {
  return {
    mode: style.mode, direction: style.direction, strip_px: Math.round(clamp(style.strip_px, MIN_STRIP, MAX_STRIP)),
    color: style.color.toUpperCase(), block: Math.round(clamp(style.block, MIN_BLOCK, MAX_BLOCK)),
    feather: Math.round(clamp(style.feather, 0, MAX_FEATHER)),
  };
}

export function sameCoverStyle(a: CoverStyle, b: CoverStyle): boolean {
  return JSON.stringify(normalizeCoverStyle(a)) === JSON.stringify(normalizeCoverStyle(b));
}

/** "Tự dò chữ gốc và che bằng kéo dãn mép" for the setup panel. */
export function describeCoverStyle(style: CoverStyle): string {
  return { stretch: "kéo dãn mép", blur: "làm mờ", color: "khối màu", mosaic: "khảm ô vuông" }[style.mode];
}

// ---------- Regions ----------

export function isRect(value: unknown): value is Rect {
  if (!value || typeof value !== "object") return false;
  const { x, y, w, h } = value as Record<string, unknown>;
  return [x, y, w, h].every((part) => typeof part === "number" && Number.isFinite(part))
    && (x as number) >= 0 && (y as number) >= 0 && (w as number) >= 0.01 && (h as number) >= 0.01
    && (x as number) + (w as number) <= 1.000001 && (y as number) + (h as number) <= 1.000001;
}

function timed(value: Record<string, unknown>): boolean {
  const { start_ms: start, end_ms: end } = value;
  if (start === undefined && end === undefined) return true;
  return Number.isInteger(start) && Number.isInteger(end) && (start as number) >= 0 && (start as number) < (end as number) && (end as number) <= 86_400_000;
}

/** A region on at a time of the main video (ms), as the render enables it. */
export function regionOn(region: { start_ms?: number; end_ms?: number }, ms: number): boolean {
  return region.start_ms === undefined || region.end_ms === undefined || (ms >= region.start_ms && ms < region.end_ms);
}

/** The cover a loaded version was rendered with: its cover.json, else blur with nothing changed. */
export function loadCover(value: unknown, makeId: () => string): CoverState {
  const raw = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
  const style = parseCoverStyle(raw.style) ?? LEGACY_COVER_STYLE;
  const manual = (Array.isArray(raw.regions) ? raw.regions : []).filter((region): region is Region => isRect(region) && timed(region as Record<string, unknown>))
    .slice(0, MAX_MANUAL).map((region) => ({ ...pick(region), id: makeId() }));
  const fixed = isRect(raw.fixed) ? rectOf(raw.fixed) : null;
  const disabled = new Set((Array.isArray(raw.disabled_auto) ? raw.disabled_auto : []).filter((index): index is number => integer(index, 0, 999_999)));
  return { style, manual, fixed, disabled };
}

function rectOf(rect: Rect): Rect {
  return { x: round4(rect.x), y: round4(rect.y), w: round4(rect.w), h: round4(rect.h) };
}

function pick(region: Region): Region {
  return region.start_ms !== undefined && region.end_ms !== undefined
    ? { ...rectOf(region), start_ms: Math.round(region.start_ms), end_ms: Math.round(region.end_ms) } : rectOf(region);
}

/** What "Áp dụng" stores as cover.json. */
export function coverDocument(state: CoverState): CoverDocument {
  return {
    style: normalizeCoverStyle(state.style),
    regions: state.manual.map(pick),
    disabled_auto: [...state.disabled].sort((a, b) => a - b),
    fixed: state.fixed ? rectOf(state.fixed) : null,
  };
}

export function sameCover(a: CoverState, b: CoverState): boolean {
  return JSON.stringify(coverDocument(a)) === JSON.stringify(coverDocument(b));
}

/** Short footer words for what changed: "kiểu che mới · 2 vùng che mới". */
export function coverSummary(base: CoverState, current: CoverState): string[] {
  const parts: string[] = [];
  if (!sameCoverStyle(base.style, current.style)) parts.push("kiểu che mới");
  const before = JSON.stringify({ manual: coverDocument(base).regions, fixed: coverDocument(base).fixed, disabled: coverDocument(base).disabled_auto });
  const after = JSON.stringify({ manual: coverDocument(current).regions, fixed: coverDocument(current).fixed, disabled: coverDocument(current).disabled_auto });
  if (before !== after) parts.push("vùng che mới");
  return parts;
}

/** Every rectangle the render covers, in the engine's order: the job's, drawn ones, the fixed one, then detected ones left on. */
export function activeRegions(state: CoverState, auto: readonly AutoRegion[], job: readonly Rect[] = []): Region[] {
  return [...job, ...state.manual, ...(state.fixed ? [state.fixed] : []), ...auto.filter((region) => !state.disabled.has(region.index))].slice(0, 256);
}

/**
 * Detected captions grouped per caption line: rectangles whose heights
 * overlap by half share a group, shown as the union of its rectangles with
 * each appearance's time span, in order of first appearance.
 */
export type AutoGroup = { key: string; rect: Rect; indices: number[]; spans: Array<[number, number]>; first: number };
export function groupAuto(regions: readonly AutoRegion[]): AutoGroup[] {
  type Row = { x0: number; y0: number; x1: number; y1: number; members: AutoRegion[] };
  const rows: Row[] = [];
  for (const region of [...regions].sort((a, b) => a.y + a.h / 2 - (b.y + b.h / 2) || a.x - b.x)) {
    const y0 = region.y, y1 = region.y + region.h;
    const row = rows.find((candidate) => Math.min(candidate.y1, y1) - Math.max(candidate.y0, y0) >= 0.5 * Math.min(region.h, candidate.y1 - candidate.y0));
    if (row) {
      row.x0 = Math.min(row.x0, region.x);
      row.y0 = Math.min(row.y0, y0);
      row.x1 = Math.max(row.x1, region.x + region.w);
      row.y1 = Math.max(row.y1, y1);
      row.members.push(region);
    } else {
      rows.push({ x0: region.x, y0, x1: region.x + region.w, y1, members: [region] });
    }
  }
  return rows.map((row) => {
    const members = [...row.members].sort((a, b) => (a.start_ms ?? 0) - (b.start_ms ?? 0) || a.index - b.index);
    const indices = members.map((region) => region.index);
    return {
      key: `auto-${indices[0]}`,
      rect: rectOf({ x: row.x0, y: row.y0, w: row.x1 - row.x0, h: row.y1 - row.y0 }),
      indices,
      spans: members.map((region) => [region.start_ms ?? 0, region.end_ms ?? 0] as [number, number]),
      first: members[0].start_ms ?? 0,
    };
  }).sort((a, b) => a.first - b.first);
}

function contains(outer: Rect, inner: Rect, slack = 0.002): boolean {
  return outer.x <= inner.x + slack && outer.y <= inner.y + slack && outer.x + outer.w >= inner.x + inner.w - slack
    && outer.y + outer.h >= inner.y + inner.h - slack;
}

/**
 * "Còn sót chữ?": detected captions that nothing covers fully while they are
 * on screen (their group switched off and no drawn or fixed region over
 * them), as source ms of their first moment, in time order.
 */
export function uncoveredMoments(state: CoverState, auto: readonly AutoRegion[], job: readonly Rect[] = []): number[] {
  const active = activeRegions(state, auto, job);
  const moments = auto.filter((caption) => !active.some((cover) => contains(cover, caption) && (
    cover.start_ms === undefined || cover.end_ms === undefined
    || (caption.start_ms !== undefined && caption.end_ms !== undefined && cover.start_ms <= caption.start_ms && cover.end_ms >= caption.end_ms))))
    .map((caption) => caption.start_ms ?? 0);
  return [...new Set(moments)].sort((a, b) => a - b);
}

/** The next moment after `ms` (wrapping to the first), or null when there is none. */
export function nextMoment(moments: readonly number[], ms: number): number | null {
  if (!moments.length) return null;
  return moments.find((moment) => moment > ms + 250) ?? moments[0];
}

/** A rectangle dragged out between two points (fractions), clamped to the frame. */
export function rectBetween(a: { x: number; y: number }, b: { x: number; y: number }): Rect {
  const x0 = clamp(Math.min(a.x, b.x), 0, 1), y0 = clamp(Math.min(a.y, b.y), 0, 1);
  const x1 = clamp(Math.max(a.x, b.x), 0, 1), y1 = clamp(Math.max(a.y, b.y), 0, 1);
  return clampRegion({ x: x0, y: y0, w: x1 - x0, h: y1 - y0 });
}

// ---------- Preview geometry ----------

/** Pixels for the drawing calls, in the picture's own pixels (`scale` = picture px per frame px is applied by the caller). */
export type Draw = { sx: number; sy: number; sw: number; sh: number; dx: number; dy: number; dw: number; dh: number };

/** An even pixel length of a fraction of `size`, rounded as the engine's `trunc(size*f/2)*2` with f to six decimals. */
export function even(size: number, fraction: number): number {
  return Math.floor(size * Number(fraction.toFixed(6)) / 2) * 2;
}

/**
 * The strip copies an edge-stretch cover draws, as the engine crops them:
 * `strip_px` rows (1080-line scale) just outside the region, on the other
 * side when the frame edge leaves no room. Source and destination are in
 * the video's pixels.
 */
export function stretchDraws(rect: Rect, style: Pick<CoverStyle, "direction" | "strip_px">, width: number, height: number): Draw[] {
  const strip = Math.max(2, even(height, style.strip_px / 1080));
  const x = even(width, rect.x), w = Math.max(2, even(width, rect.w));
  const top = even(height, rect.y), h = Math.max(2, even(height, rect.h)), bottom = even(height, rect.y + rect.h);
  const fraction = style.strip_px / 1080;
  const side = (wanted: "top" | "bottom"): "top" | "bottom" => {
    if (wanted === "top" && rect.y < fraction && rect.y + rect.h + fraction <= 1) return "bottom";
    if (wanted === "bottom" && rect.y + rect.h + fraction > 1 && rect.y >= fraction) return "top";
    return wanted;
  };
  const source = (wanted: "top" | "bottom"): number => side(wanted) === "top" ? Math.max(0, top - strip) : Math.min(height - strip, bottom);
  if (style.direction === "both") {
    const upper = Math.max(2, even(height, rect.h / 2));
    return [
      { sx: x, sy: source("top"), sw: w, sh: strip, dx: x, dy: top, dw: w, dh: upper },
      { sx: x, sy: source("bottom"), sw: w, sh: strip, dx: x, dy: top + upper, dw: w, dh: Math.max(2, h - upper) },
    ];
  }
  return [{ sx: x, sy: source(style.direction), sw: w, sh: strip, dx: x, dy: top, dw: w, dh: h }];
}

/** Mosaic: rows of blocks across the region's height (the same count at any resolution), square across its width. */
export function mosaicCells(rect: Rect, block: number, width: number, height: number): { columns: number; rows: number } {
  const rows = Math.max(1, Math.round(rect.h * 1080 / block));
  const pixelsW = Math.max(2, even(width, rect.w)), pixelsH = Math.max(2, even(height, rect.h));
  return { rows, columns: Math.max(1, Math.round(pixelsW * rows / pixelsH)) };
}
