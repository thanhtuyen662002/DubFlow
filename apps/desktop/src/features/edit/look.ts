/**
 * How one segment looks and plays beyond its part, speed and volume (Issue
 * #518): crop, rotation, flips, a slow zoom (Ken Burns), colour, playing
 * backwards, a held frame and sound fades. The host renders the same values
 * (src-tauri/src/edit.rs, `segment_look_filters`); the preview approximates
 * them with CSS on the player.
 */

export type Rotation = 0 | 90 | 180 | 270;
/** Fractions of the source picture: the part kept, then fitted to the frame. */
export type Crop = { readonly x: number; readonly y: number; readonly w: number; readonly h: number };
/** Scale from `from` to `to` over the segment (1 = no zoom), centred on x, y (0–1 of the picture). */
export type Zoom = { readonly from: number; readonly to: number; readonly x: number; readonly y: number };
/** brightness −0.5…0.5, contrast 0.5…1.5, saturation 0…2, temperature −1 (cool)…1 (warm); 0, 1, 1, 0 change nothing. */
export type ColorLook = { readonly brightness: number; readonly contrast: number; readonly saturation: number; readonly temperature: number };
export type SegmentLook = {
  readonly crop: Crop | null;
  readonly rotate: Rotation;
  readonly flipH: boolean;
  readonly flipV: boolean;
  readonly zoom: Zoom | null;
  readonly color: ColorLook | null;
  /** Plays backwards (sound too). */
  readonly reverse: boolean;
  /** Holds the frame at the segment's start for this many seconds (no sound); null plays the part. */
  readonly freeze: number | null;
  /** Sound fades of the segment, in output seconds. */
  readonly fadeIn: number;
  readonly fadeOut: number;
};

export const DEFAULT_LOOK: SegmentLook = {
  crop: null, rotate: 0, flipH: false, flipV: false, zoom: null, color: null, reverse: false, freeze: null, fadeIn: 0, fadeOut: 0,
};
export const NEUTRAL_COLOR: ColorLook = { brightness: 0, contrast: 1, saturation: 1, temperature: 0 };
export const MIN_CROP = 0.1;
export const MAX_ZOOM_LOOK = 3;
export const MIN_FREEZE = 0.2;
export const MAX_FREEZE = 10;
export const DEFAULT_FREEZE = 2;
/** Playing backwards buffers the whole part, so it is limited to two minutes of media. */
export const MAX_REVERSE = 120;
export const MAX_SEGMENT_FADE = 3;

export const COLOR_PRESETS: ReadonlyArray<{ id: string; name: string; color: ColorLook }> = [
  { id: "none", name: "Gốc", color: NEUTRAL_COLOR },
  { id: "vivid", name: "Rực rỡ", color: { brightness: 0.02, contrast: 1.12, saturation: 1.45, temperature: 0 } },
  { id: "warm", name: "Ấm", color: { brightness: 0.02, contrast: 1.05, saturation: 1.1, temperature: 0.55 } },
  { id: "cool", name: "Lạnh", color: { brightness: 0, contrast: 1.05, saturation: 0.95, temperature: -0.55 } },
  { id: "film", name: "Phim cũ", color: { brightness: 0.04, contrast: 0.88, saturation: 0.7, temperature: 0.4 } },
  { id: "soft", name: "Dịu", color: { brightness: 0.05, contrast: 0.85, saturation: 0.85, temperature: 0.1 } },
  { id: "drama", name: "Kịch tính", color: { brightness: -0.04, contrast: 1.3, saturation: 0.8, temperature: -0.15 } },
  { id: "bw", name: "Đen trắng", color: { brightness: 0, contrast: 1.1, saturation: 0, temperature: 0 } },
];

export const CROP_RATIOS: ReadonlyArray<{ id: string; name: string; ratio: number | null }> = [
  { id: "free", name: "Tự do", ratio: null },
  { id: "16:9", name: "16:9", ratio: 16 / 9 },
  { id: "9:16", name: "9:16", ratio: 9 / 16 },
  { id: "1:1", name: "1:1", ratio: 1 },
  { id: "4:5", name: "4:5", ratio: 4 / 5 },
];

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));
const round = (value: number, step = 1000): number => Math.round(value * step) / step;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

export function sameColor(a: ColorLook | null, b: ColorLook | null): boolean {
  const x = a ?? NEUTRAL_COLOR, y = b ?? NEUTRAL_COLOR;
  return x.brightness === y.brightness && x.contrast === y.contrast && x.saturation === y.saturation && x.temperature === y.temperature;
}

export function colorPresetOf(color: ColorLook | null): string | null {
  return COLOR_PRESETS.find((preset) => sameColor(preset.color, color))?.id ?? null;
}

function cleanCrop(raw: unknown): Crop | null {
  if (!raw || typeof raw !== "object") return null;
  const { x, y, w, h } = raw as Record<string, unknown>;
  if (![x, y, w, h].every(finite)) return null;
  const width = round(clamp(w as number, MIN_CROP, 1), 10000), height = round(clamp(h as number, MIN_CROP, 1), 10000);
  const crop = { x: round(clamp(x as number, 0, 1 - width), 10000), y: round(clamp(y as number, 0, 1 - height), 10000), w: width, h: height };
  return crop.x === 0 && crop.y === 0 && crop.w === 1 && crop.h === 1 ? null : crop;
}

function cleanZoom(raw: unknown): Zoom | null {
  if (!raw || typeof raw !== "object") return null;
  const { from, to, x, y } = raw as Record<string, unknown>;
  if (![from, to, x, y].every(finite)) return null;
  const zoom = { from: round(clamp(from as number, 1, MAX_ZOOM_LOOK), 100), to: round(clamp(to as number, 1, MAX_ZOOM_LOOK), 100),
    x: round(clamp(x as number, 0, 1), 1000), y: round(clamp(y as number, 0, 1), 1000) };
  return zoom.from === 1 && zoom.to === 1 ? null : zoom;
}

function cleanColor(raw: unknown): ColorLook | null {
  if (!raw || typeof raw !== "object") return null;
  const { brightness, contrast, saturation, temperature } = raw as Record<string, unknown>;
  if (![brightness, contrast, saturation, temperature].every(finite)) return null;
  const color = {
    brightness: round(clamp(brightness as number, -0.5, 0.5), 100), contrast: round(clamp(contrast as number, 0.5, 1.5), 100),
    saturation: round(clamp(saturation as number, 0, 2), 100), temperature: round(clamp(temperature as number, -1, 1), 100),
  };
  return sameColor(color, NEUTRAL_COLOR) ? null : color;
}

/** A look from a draft or a change, every value clamped; null when it changes nothing. */
export function normalizeLook(raw: unknown): SegmentLook | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Record<string, unknown>;
  const rotate = [0, 90, 180, 270].includes(value.rotate as number) ? value.rotate as Rotation : 0;
  const freeze = finite(value.freeze) ? round(clamp(value.freeze, MIN_FREEZE, MAX_FREEZE)) : null;
  const fade = (key: string): number => finite(value[key]) ? round(clamp(value[key] as number, 0, MAX_SEGMENT_FADE), 100) : 0;
  const look: SegmentLook = {
    crop: cleanCrop(value.crop), rotate, flipH: value.flipH === true, flipV: value.flipV === true, zoom: cleanZoom(value.zoom),
    color: cleanColor(value.color), reverse: freeze === null && value.reverse === true, freeze,
    fadeIn: freeze === null ? fade("fadeIn") : 0, fadeOut: freeze === null ? fade("fadeOut") : 0,
  };
  return isDefaultLook(look) ? null : look;
}

export function isDefaultLook(look: SegmentLook | null | undefined): boolean {
  return !look || (!look.crop && look.rotate === 0 && !look.flipH && !look.flipV && !look.zoom && !look.color && !look.reverse
    && look.freeze === null && look.fadeIn === 0 && look.fadeOut === 0);
}

export function sameLook(a: SegmentLook | null | undefined, b: SegmentLook | null | undefined): boolean {
  return JSON.stringify(normalizeLook(a ?? null)) === JSON.stringify(normalizeLook(b ?? null));
}

/** The look as the host reads it (snake case, only what changes something). */
export function planLook(look: SegmentLook | null | undefined): Record<string, unknown> {
  const clean = normalizeLook(look ?? null);
  if (!clean) return {};
  const plan: Record<string, unknown> = {};
  if (clean.crop) plan.crop = { ...clean.crop };
  if (clean.rotate) plan.rotate = clean.rotate;
  if (clean.flipH) plan.flip_h = true;
  if (clean.flipV) plan.flip_v = true;
  if (clean.zoom) plan.zoom = { ...clean.zoom };
  if (clean.color) plan.color = { ...clean.color };
  if (clean.reverse) plan.reverse = true;
  if (clean.freeze !== null) plan.freeze = clean.freeze;
  if (clean.fadeIn) plan.fade_in = clean.fadeIn;
  if (clean.fadeOut) plan.fade_out = clean.fadeOut;
  return plan;
}

export const LOOK_PLAN_KEYS = ["crop", "rotate", "flip_h", "flip_v", "zoom", "color", "reverse", "freeze", "fade_in", "fade_out"] as const;

/** A centred crop of `ratio` (width / height) on a `width` × `height` picture, as large as fits. */
export function cropForRatio(width: number, height: number, ratio: number): Crop | null {
  if (!(width > 0 && height > 0 && ratio > 0)) return null;
  const picture = width / height;
  const w = picture > ratio ? ratio / picture : 1, h = picture > ratio ? 1 : picture / ratio;
  return cleanCrop({ x: (1 - w) / 2, y: (1 - h) / 2, w, h });
}

/** The crop ratio preset a crop matches on a picture, or "free". */
export function cropRatioOf(crop: Crop | null, width: number, height: number): string {
  if (!crop || !(width > 0 && height > 0)) return "free";
  const ratio = (crop.w * width) / (crop.h * height);
  return CROP_RATIOS.find((item) => item.ratio && Math.abs(item.ratio - ratio) < 0.01)?.id ?? "free";
}

/** CSS filter that previews a colour (temperature is drawn by a tint layer, see `temperatureTint`). */
export function cssFilter(color: ColorLook | null): string {
  if (!color) return "";
  const parts: string[] = [];
  if (color.brightness) parts.push(`brightness(${round(1 + color.brightness * 0.9, 1000)})`);
  if (color.contrast !== 1) parts.push(`contrast(${color.contrast})`);
  if (color.saturation !== 1) parts.push(`saturate(${color.saturation})`);
  return parts.join(" ");
}

/** A soft-light layer over the picture that previews the temperature. */
export function temperatureTint(color: ColorLook | null): { color: string; opacity: number } {
  const value = color?.temperature ?? 0;
  return { color: value >= 0 ? "#ff8a2a" : "#2a7bff", opacity: round(Math.abs(value) * 0.45, 1000) };
}

/** Picture scale at `progress` (0–1) through the segment. */
export function zoomAt(zoom: Zoom | null, progress: number): number {
  if (!zoom) return 1;
  return zoom.from + (zoom.to - zoom.from) * clamp(progress, 0, 1);
}

/** A short description for the timeline badge and the panel title. */
export function lookBadges(look: SegmentLook | null | undefined): string[] {
  if (!look) return [];
  const badges: string[] = [];
  if (look.freeze !== null) badges.push("Dừng hình");
  if (look.reverse) badges.push("Tua ngược");
  if (look.color) badges.push(COLOR_PRESETS.find((preset) => sameColor(preset.color, look.color))?.name ?? "Màu");
  if (look.crop || look.rotate || look.flipH || look.flipV) badges.push("Khung");
  if (look.zoom) badges.push("Zoom");
  return badges;
}
