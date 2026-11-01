/**
 * Phụ đề (Issue #414): the look of the burned Vietnamese subtitles, the
 * lines with their edited text and re-voice marks, and what "Áp dụng" sends
 * to the host. The engine validates the same style again
 * (engine/dubflow/subtitle/style.py); its ASS canvas is 1920×1080 with a
 * 48 px base font and 40/48 px margins, which the preview reproduces.
 */
import { MAIN, outputStarts, type Edit } from "./model.ts";

/** Stock Windows families covering every Vietnamese letter (Georgia, Trebuchet MS, Arial Black, Impact and Bahnschrift do not). */
export const SUBTITLE_FONTS = [
  "Arial", "Segoe UI", "Tahoma", "Verdana", "Times New Roman", "Calibri", "Cambria",
  "Palatino Linotype", "Constantia", "Corbel", "Candara", "Segoe UI Black",
] as const;
export type SubtitleFont = typeof SUBTITLE_FONTS[number];
export type SubtitleAlign = "bottom" | "middle" | "top";
export type SubtitleStyle = {
  font: SubtitleFont;
  /** Relative to today's size, 0.6–2. */
  size: number;
  primary: string;
  outline: string;
  /** Box colour, or null for no box. */
  box: string | null;
  box_opacity: number;
  /** ASS outline width on the 1080-line canvas, 0–8. */
  outline_width: number;
  bold: boolean;
  align: SubtitleAlign;
  /** Normalized centre of a dragged block; both or neither. */
  x?: number;
  y?: number;
};

export const STYLE_STORAGE_KEY = "dubflow.subtitleStyle.v1";
export const MIN_SIZE = 0.6;
export const MAX_SIZE = 2;
export const MAX_OUTLINE = 8;
export const CANVAS_WIDTH = 1920;
export const CANVAS_HEIGHT = 1080;
export const BASE_FONT = 48;
export const MARGIN_X = 40;
export const MARGIN_V = 48;
/** Snap targets for a dragged block: the centre line and the title-safe margins. */
export const SAFE_MARGIN = 0.1;
const MAX_TEXT = 2000;

/** Today's look: the style line production_job.py writes when no style is given. */
export const DEFAULT_STYLE: SubtitleStyle = {
  font: "Arial", size: 1, primary: "#FFFFFF", outline: "#000000", box: null, box_opacity: 0.6, outline_width: 3, bold: false, align: "bottom",
};

export type PresetId = "default" | "dark-box" | "yellow" | "thick" | "minimal";
type Look = Omit<SubtitleStyle, "align" | "x" | "y">;
/** A preset changes the look and keeps the position. */
export const PRESETS: ReadonlyArray<{ id: PresetId; label: string; look: Look }> = [
  { id: "default", label: "Mặc định", look: { font: "Arial", size: 1, primary: "#FFFFFF", outline: "#000000", box: null, box_opacity: 0.6, outline_width: 3, bold: false } },
  { id: "dark-box", label: "Hộp tối", look: { font: "Segoe UI", size: 1, primary: "#FFFFFF", outline: "#000000", box: "#000000", box_opacity: 0.65, outline_width: 0, bold: false } },
  { id: "yellow", label: "Vàng nổi", look: { font: "Arial", size: 1.1, primary: "#FDE047", outline: "#000000", box: null, box_opacity: 0.6, outline_width: 3.5, bold: true } },
  { id: "thick", label: "Viền dày", look: { font: "Segoe UI Black", size: 1.05, primary: "#FFFFFF", outline: "#000000", box: null, box_opacity: 0.6, outline_width: 6, bold: false } },
  { id: "minimal", label: "Tối giản", look: { font: "Segoe UI", size: 0.9, primary: "#FFFFFF", outline: "#000000", box: null, box_opacity: 0.6, outline_width: 1.5, bold: false } },
];

export const TEXT_SWATCHES = ["#FFFFFF", "#FDE047", "#7DD3FC", "#86EFAC", "#FCA5A5"] as const;
export const OUTLINE_SWATCHES = ["#000000", "#FFFFFF", "#1E3A8A", "#7F1D1D"] as const;
export const BOX_SWATCHES = ["#000000", "#1F2937", "#FFFFFF", "#FDE047"] as const;

/**
 * Font size → CSS em: libass sizes a font so that its Windows ascent plus
 * descent equals the ASS size, while CSS sizes the em square.
 */
export const EM_SCALE: Readonly<Record<SubtitleFont, number>> = {
  "Arial": 0.895, "Segoe UI": 0.752, "Tahoma": 0.828, "Verdana": 0.823, "Times New Roman": 0.903, "Calibri": 0.819, "Cambria": 0.853,
  "Palatino Linotype": 0.741, "Constantia": 0.819, "Corbel": 0.819, "Candara": 0.819, "Segoe UI Black": 0.752,
};

const HEX = /^#[0-9A-Fa-f]{6}$/;
const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const round = (value: number, step: number): number => Math.round(value / step) * step;

export function isColor(value: unknown): value is string {
  return typeof value === "string" && HEX.test(value);
}

/** A stored or loaded style, or null when anything is off. */
export function parseStyle(value: unknown): SubtitleStyle | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const known = new Set(["font", "size", "primary", "outline", "box", "box_opacity", "outline_width", "bold", "align", "x", "y"]);
  if (Object.keys(raw).some((key) => !known.has(key))) return null;
  const { font, size, primary, outline, box, box_opacity, outline_width, bold, align, x, y } = raw;
  if (!SUBTITLE_FONTS.includes(font as SubtitleFont) || !finite(size) || size < MIN_SIZE || size > MAX_SIZE) return null;
  if (!isColor(primary) || !isColor(outline) || (box !== null && !isColor(box))) return null;
  if (!finite(box_opacity) || box_opacity < 0 || box_opacity > 1 || !finite(outline_width) || outline_width < 0 || outline_width > MAX_OUTLINE) return null;
  if (typeof bold !== "boolean" || !["bottom", "middle", "top"].includes(String(align))) return null;
  const placed = x !== undefined && x !== null, placedY = y !== undefined && y !== null;
  if (placed !== placedY || (placed && (!finite(x) || !finite(y) || x < 0 || x > 1 || y < 0 || y > 1))) return null;
  const style: SubtitleStyle = {
    font: font as SubtitleFont, size, primary: primary.toUpperCase(), outline: outline.toUpperCase(), box: box === null ? null : (box as string).toUpperCase(),
    box_opacity, outline_width, bold, align: align as SubtitleAlign,
  };
  return placed ? { ...style, x: x as number, y: y as number } : style;
}

/** The last applied style (`dubflow.subtitleStyle.v1`), the default for new videos. */
export function storedStyle(raw: string | null): SubtitleStyle | null {
  if (!raw) return null;
  try {
    return parseStyle(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function sameStyle(a: SubtitleStyle, b: SubtitleStyle): boolean {
  return JSON.stringify(normalizeStyle(a)) === JSON.stringify(normalizeStyle(b));
}

/** Keys in a fixed order, values rounded to their control steps. */
export function normalizeStyle(style: SubtitleStyle): SubtitleStyle {
  const value: SubtitleStyle = {
    font: style.font, size: round(clamp(style.size, MIN_SIZE, MAX_SIZE), 0.05), primary: style.primary.toUpperCase(), outline: style.outline.toUpperCase(),
    box: style.box ? style.box.toUpperCase() : null, box_opacity: round(clamp(style.box_opacity, 0, 1), 0.05),
    outline_width: round(clamp(style.outline_width, 0, MAX_OUTLINE), 0.5), bold: style.bold, align: style.align,
  };
  for (const key of ["size", "box_opacity", "outline_width"] as const) value[key] = Number(value[key].toFixed(2));
  if (finite(style.x) && finite(style.y)) {
    value.x = Number(clamp(style.x, 0, 1).toFixed(4));
    value.y = Number(clamp(style.y, 0, 1).toFixed(4));
  }
  return value;
}

export function applyPreset(style: SubtitleStyle, id: PresetId): SubtitleStyle {
  const preset = PRESETS.find((candidate) => candidate.id === id);
  if (!preset) return style;
  const { align, x, y } = style;
  return normalizeStyle({ ...preset.look, align, ...(finite(x) && finite(y) ? { x, y } : {}) });
}

/** The preset whose look this is, or null for a custom look. */
export function presetOf(style: SubtitleStyle): PresetId | null {
  return PRESETS.find((preset) => sameStyle(applyPreset(style, preset.id), style))?.id ?? null;
}

export function withoutPosition(style: SubtitleStyle): SubtitleStyle {
  const { x: _x, y: _y, ...rest } = style;
  return rest;
}

// ---------- Preview geometry ----------

export type Picture = { width: number; height: number };
export type Box = { left: number; top: number; width: number; height: number };

/**
 * How libass draws a style on a picture of this size: everything scales with
 * the picture height (ScaledBorderAndShadow), positions and side margins
 * with its width, as on the 1920×1080 ASS canvas.
 */
export function previewMetrics(style: SubtitleStyle, picture: Picture): {
  fontPx: number; outlinePx: number; paddingPx: number; marginX: number; marginV: number; lineHeight: number;
} {
  const scale = picture.height / CANVAS_HEIGHT;
  const size = Math.max(1, Math.round(BASE_FONT * style.size));
  return {
    fontPx: size * scale * EM_SCALE[style.font],
    outlinePx: style.outline_width * scale,
    // Box padding: libass BorderStyle 4 pads by the Shadow value, 20 % of the size.
    paddingPx: style.box ? Math.max(2, Math.round(size * 0.2)) * scale : 0,
    marginX: MARGIN_X * picture.width / CANVAS_WIDTH,
    marginV: MARGIN_V * scale,
    lineHeight: 1.2,
  };
}

/** CSS colour with opacity for a #RRGGBB value. */
export function rgba(hex: string, alpha: number): string {
  const value = Number.parseInt(hex.slice(1), 16);
  return `rgba(${value >> 16 & 255}, ${value >> 8 & 255}, ${value & 255}, ${Number(clamp(alpha, 0, 1).toFixed(3))})`;
}

/**
 * A dragged block's new centre (fractions of the picture). Without Shift
 * only the height changes; the centre snaps to the middle line and keeps
 * the block's edges on the title-safe margins when close to them.
 */
export function dragPosition(start: { x: number; y: number }, delta: { dx: number; dy: number }, block: { width: number; height: number },
  picture: Picture, free: boolean, snapPx = 8): { x: number; y: number; guides: Array<"center" | "top" | "bottom"> } {
  const halfW = picture.width > 0 ? block.width / 2 / picture.width : 0;
  const halfH = picture.height > 0 ? block.height / 2 / picture.height : 0;
  let x = free ? start.x + delta.dx / Math.max(1, picture.width) : start.x;
  let y = start.y + delta.dy / Math.max(1, picture.height);
  x = clamp(x, Math.min(0.5, halfW), Math.max(0.5, 1 - halfW));
  y = clamp(y, Math.min(0.5, halfH), Math.max(0.5, 1 - halfH));
  const guides: Array<"center" | "top" | "bottom"> = [];
  const snapX = snapPx / Math.max(1, picture.width), snapY = snapPx / Math.max(1, picture.height);
  if (Math.abs(x - 0.5) <= snapX) {
    x = 0.5;
    guides.push("center");
  }
  if (Math.abs(y - halfH - SAFE_MARGIN) <= snapY) {
    y = SAFE_MARGIN + halfH;
    guides.push("top");
  } else if (Math.abs(y + halfH - (1 - SAFE_MARGIN)) <= snapY) {
    y = 1 - SAFE_MARGIN - halfH;
    guides.push("bottom");
  }
  return { x: Number(x.toFixed(4)), y: Number(y.toFixed(4)), guides };
}

// ---------- Lines ----------

/** A line as `edit_subtitles_load` reports it. */
export type SubtitleCue = {
  cue_id: string; start_ms: number; end_ms: number; source_text: string; machine_text: string;
  /** The current version's subtitle text. */
  text: string;
  /** What its voice speaks now. */
  voice_text: string;
  edited: boolean; dub_failed: boolean;
};

export function cleanText(text: string): string {
  return text.trim().replace(/\s+/g, " ");
}

/**
 * Output time where a line starts showing in the edited video (through the
 * cut/insert mapping), or null when every part of it was cut.
 */
export function cueOutputTime(edit: Edit, cue: { start_ms: number; end_ms: number }): number | null {
  const start = cue.start_ms / 1000, end = cue.end_ms / 1000;
  const starts = outputStarts(edit);
  for (let index = 0; index < edit.segments.length; index++) {
    const segment = edit.segments[index];
    if (segment.mediaId !== MAIN || segment.end <= start || segment.start >= end) continue;
    return starts[index] + (Math.max(start, segment.start) - segment.start) / segment.speed;
  }
  return null;
}

/** The line on screen at a time of the main video, if any. */
export function cueAt<T extends { start_ms: number; end_ms: number }>(cues: readonly T[], seconds: number): T | null {
  const ms = seconds * 1000;
  let low = 0, high = cues.length - 1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const cue = cues[middle];
    if (ms < cue.start_ms) high = middle - 1;
    else if (ms >= cue.end_ms) low = middle + 1;
    else return cue;
  }
  return null;
}

export type SubtitleChanges = {
  /** Subtitle text per line that differs from its machine translation (the whole set, as the engine reads it). */
  overrides: Record<string, string>;
  /** What a line's voice speaks where that differs from its subtitle. */
  voice: Record<string, string>;
  revoice: string[];
  /** Lines whose text changed in this session. */
  edited: number;
  empty: number;
  tooLong: number;
};

/**
 * What "Áp dụng" sends. An edited line keeps its earlier speech unless it is
 * marked "Giọng câu này", which voices it again from its edited text.
 */
export function subtitleChanges(cues: readonly SubtitleCue[], draftOf: (cueId: string) => string, marked: ReadonlySet<string>): SubtitleChanges {
  const changes: SubtitleChanges = { overrides: {}, voice: {}, revoice: [], edited: 0, empty: 0, tooLong: 0 };
  for (const cue of cues) {
    const text = cleanText(draftOf(cue.cue_id));
    if (!text) changes.empty += 1;
    if (text.length > MAX_TEXT) changes.tooLong += 1;
    if (text !== cleanText(cue.text)) changes.edited += 1;
    if (text && text !== cleanText(cue.machine_text)) changes.overrides[cue.cue_id] = text;
    const spoken = marked.has(cue.cue_id) ? text : cleanText(cue.voice_text);
    if (spoken && spoken !== text) changes.voice[cue.cue_id] = spoken;
  }
  changes.revoice = cues.filter((cue) => marked.has(cue.cue_id)).map((cue) => cue.cue_id);
  return changes;
}

/** "3 câu đã sửa · 1 câu đọc lại giọng · kiểu chữ mới"; `short` fits the footer: "3 sửa · 1 đọc lại · kiểu mới". */
export function changeSummary(edited: number, revoiced: number, styleChanged: boolean, short = false): string {
  const parts = short
    ? [edited ? `${edited} câu sửa` : "", revoiced ? `${revoiced} đọc lại` : "", styleChanged ? "kiểu mới" : ""].filter(Boolean)
    : [edited ? `${edited} câu đã sửa` : "", revoiced ? `${revoiced} câu đọc lại giọng` : "", styleChanged ? "kiểu chữ mới" : ""].filter(Boolean);
  return parts.length ? parts.join(" · ") : "Chưa có thay đổi";
}

/** A blur rectangle on at a time of the main video (ms), as the render applies it. */
export function regionOn(region: { x: number; start_ms?: number; end_ms?: number }, ms: number): boolean {
  return region.start_ms === undefined || region.end_ms === undefined || (ms >= region.start_ms && ms < region.end_ms);
}
