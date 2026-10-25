/** A source-caption rectangle to blur, as fractions (0–1) of the video frame. */
export type BlurRegion = { x: number; y: number; w: number; h: number };

/** Which part of the rectangle is being dragged: its body or an edge/corner. */
export type DragHandle = "move" | "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

export const MIN_SIZE = 0.02;
export const MAX_REGIONS = 16;
/** Where burned-in captions usually sit on Douyin/Bilibili videos. */
export const DEFAULT_REGION: BlurRegion = { x: 0.1, y: 0.8, w: 0.8, h: 0.1 };

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));
const round = (value: number): number => Math.round(value * 10_000) / 10_000;

export function isBlurRegion(value: unknown): value is BlurRegion {
  if (!value || typeof value !== "object") return false;
  const { x, y, w, h } = value as Record<string, unknown>;
  return [x, y, w, h].every((part) => typeof part === "number" && Number.isFinite(part))
    && (x as number) >= 0 && (y as number) >= 0 && (w as number) >= 0.01 && (h as number) >= 0.01
    && (x as number) + (w as number) <= 1.000001 && (y as number) + (h as number) <= 1.000001;
}

export function isBlurRegionList(value: unknown): value is BlurRegion[] {
  return Array.isArray(value) && value.length > 0 && value.length <= MAX_REGIONS && value.every(isBlurRegion);
}

/** Keep a region inside the frame and at least MIN_SIZE in each direction. */
export function clampRegion(region: BlurRegion): BlurRegion {
  const w = clamp(region.w, MIN_SIZE, 1);
  const h = clamp(region.h, MIN_SIZE, 1);
  return { x: round(clamp(region.x, 0, 1 - w)), y: round(clamp(region.y, 0, 1 - h)), w: round(w), h: round(h) };
}

/**
 * The region after dragging `handle` by (dx, dy), both fractions of the frame.
 * Moving keeps the size; an edge moves only that side and stops before the
 * opposite one, so the rectangle never flips or leaves the frame.
 */
export function dragRegion(start: BlurRegion, handle: DragHandle, dx: number, dy: number): BlurRegion {
  if (handle === "move") {
    return clampRegion({ ...start, x: clamp(start.x + dx, 0, 1 - start.w), y: clamp(start.y + dy, 0, 1 - start.h) });
  }
  let left = start.x;
  let top = start.y;
  let right = start.x + start.w;
  let bottom = start.y + start.h;
  if (handle.includes("w")) left = clamp(left + dx, 0, right - MIN_SIZE);
  if (handle.includes("e")) right = clamp(right + dx, left + MIN_SIZE, 1);
  if (handle.includes("n")) top = clamp(top + dy, 0, bottom - MIN_SIZE);
  if (handle.includes("s")) bottom = clamp(bottom + dy, top + MIN_SIZE, 1);
  return clampRegion({ x: left, y: top, w: right - left, h: bottom - top });
}

/** Short Vietnamese description for the options panel. */
export function describeRegions(regions: readonly BlurRegion[] | undefined): string {
  if (!regions?.length) return "Tắt";
  if (regions.length > 1) return `${regions.length} vùng`;
  const [region] = regions;
  const place = region.y + region.h / 2 > 0.66 ? "dưới" : region.y + region.h / 2 < 0.33 ? "trên" : "giữa";
  return `Vùng ${place}, rộng ${Math.round(region.w * 100)}% × cao ${Math.round(region.h * 100)}%`;
}
