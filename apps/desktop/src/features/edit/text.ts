/**
 * Chữ (Issue #422): presets, colours, how a text is drawn on the preview so
 * it matches the export (libass, see edit.rs `texts_ass`), its animation at a
 * moment, and snapping a dragged text to the guides.
 */
import { EM_SCALE } from "./fonts.ts";
import { animSpan, textLength, type TextClip, type TextLook } from "./model.ts";

export type TextPresetId = "title" | "callout" | "channel" | "emphasis" | "label";
export type TextPreset = {
  id: TextPresetId; label: string; text: string; look: Partial<TextLook>;
  /** Seconds it lasts; "end" runs to the end of the video. */
  seconds: number | "end";
};

export const TEXT_PRESETS: readonly TextPreset[] = [
  { id: "title", label: "Tiêu đề lớn", text: "Tiêu đề", seconds: 3, look: {
    font: "Segoe UI Black", size: 120, color: "#FFFFFF", outline: "#000000", outline_width: 6, box: null, bold: false, italic: false,
    align: "center", x: 0.5, y: 0.22, w: 0.9, animation: "pop", anim_duration: 0.5 } },
  { id: "callout", label: "Chú thích", text: "Chú thích ở đây", seconds: 3, look: {
    font: "Segoe UI", size: 52, color: "#FFFFFF", outline: "#000000", outline_width: 0, box: "#000000", box_opacity: 0.6, bold: false, italic: false,
    align: "center", x: 0.5, y: 0.8, w: 0.7, animation: "fade", anim_duration: 0.4 } },
  { id: "channel", label: "Tên kênh góc", text: "@TenKenh", seconds: "end", look: {
    font: "Arial", size: 44, color: "#FFFFFF", outline: "#000000", outline_width: 3, box: null, bold: true, italic: false,
    align: "left", x: 0.22, y: 0.08, w: 0.38, animation: "slide-up", anim_duration: 0.5 } },
  { id: "emphasis", label: "Phụ đề nhấn", text: "Quá đỉnh!", seconds: 2, look: {
    font: "Arial", size: 84, color: "#FDE047", outline: "#000000", outline_width: 5, box: null, bold: true, italic: false,
    align: "center", x: 0.5, y: 0.62, w: 0.85, animation: "pop", anim_duration: 0.4 } },
  { id: "label", label: "Nhãn hộp", text: "Phần 2", seconds: 3, look: {
    font: "Segoe UI", size: 64, color: "#111111", outline: "#000000", outline_width: 0, box: "#FDE047", box_opacity: 1, bold: true, italic: false,
    align: "center", x: 0.5, y: 0.5, w: 0.5, animation: "typewriter", anim_duration: 0.8 } },
];

export const TEXT_COLORS = ["#FFFFFF", "#111111", "#FDE047", "#7DD3FC", "#86EFAC", "#FCA5A5"] as const;
export const OUTLINE_COLORS = ["#000000", "#FFFFFF", "#1E3A8A", "#7F1D1D"] as const;
export const BOX_COLORS = ["#000000", "#FFFFFF", "#FDE047", "#DC2626"] as const;

export const ANIMATION_NAMES: Readonly<Record<TextClip["animation"], string>> = {
  none: "Không", fade: "Hiện và mờ dần", pop: "Bật ra", "slide-up": "Trượt lên", typewriter: "Gõ chữ",
};
export const ALIGN_NAMES: Readonly<Record<TextClip["align"], string>> = { left: "Trái", center: "Giữa", right: "Phải" };

/** Slide up starts this far below (a fraction of the frame height). */
export const SLIDE = 0.05;
/** Pop: grows from 40 % to 112 % over 60 % of its span, settles at 100 %, fading in over the first 40 %. */
export const POP_FROM = 0.4;
export const POP_OVER = 1.12;
/** Snap targets: the safe margin a box edge keeps from the frame edge. */
export const TEXT_SAFE = 0.05;

// ---------- Drawing ----------

export type Picture = { width: number; height: number };

/**
 * A text as libass draws it on a picture of this size: sizes follow the
 * short side (1080 = the given pixels), the line height is the font's ASS
 * size, a box pads the block by 20 % of the size (at least 2 px).
 */
export function textMetrics(text: Pick<TextClip, "font" | "size" | "outline_width" | "box" | "w">, picture: Picture): {
  fontPx: number; lineHeight: number; outlinePx: number; paddingPx: number; widthPx: number;
} {
  const scale = Math.min(picture.width, picture.height) / 1080;
  return {
    fontPx: text.size * scale * EM_SCALE[text.font],
    lineHeight: 1 / EM_SCALE[text.font],
    outlinePx: text.outline_width * scale,
    paddingPx: text.box ? Math.max(2, Math.round(text.size * 0.2)) * scale : 0,
    widthPx: text.w * picture.width,
  };
}

/** Pieces a typewriter reveals one by one: characters with their combining marks; a line break is not one. */
export function textUnits(text: string): Array<{ text: string; index: number | null }> {
  const units: Array<{ text: string; index: number | null }> = [];
  let index = 0;
  for (const char of text) {
    if (char === "\n") units.push({ text: char, index: null });
    else if (/^[\u0300-\u036f]$/.test(char) && units.length && units[units.length - 1].index !== null) units[units.length - 1].text += char;
    else units.push({ text: char, index: index++ });
  }
  return units;
}

/** Centiseconds after the start at which typewriter unit `index` of `count` appears, as the export's \ko tags place it. */
export function revealAt(index: number, count: number, span: number): number {
  return count > 0 ? Math.round(index * Math.round(span * 100) / count) / 100 : 0;
}

export type TextFrame = {
  opacity: number; scale: number;
  /** Shift down, a fraction of the frame height. */
  dy: number;
  /** Typewriter units shown (all of them otherwise). */
  shown: number;
};

/** How a text looks at output time `time`, or null when it is not on screen. */
export function textFrame(text: TextClip, time: number, total: number): TextFrame | null {
  const length = textLength(text, total);
  const t = time - text.at;
  if (t < 0 || t >= length) return null;
  const span = animSpan(text, length);
  const frame: TextFrame = { opacity: 1, scale: 1, dy: 0, shown: Number.POSITIVE_INFINITY };
  if (span <= 0) return frame;
  const part = Math.min(1, t / span);
  if (text.animation === "fade") {
    frame.opacity = Math.min(1, t / span, (length - t) / span);
  } else if (text.animation === "pop") {
    frame.opacity = Math.min(1, t / (span * 0.4));
    frame.scale = part < 0.6 ? POP_FROM + (POP_OVER - POP_FROM) * part / 0.6 : part < 1 ? POP_OVER + (1 - POP_OVER) * (part - 0.6) / 0.4 : 1;
  } else if (text.animation === "slide-up") {
    frame.opacity = part;
    frame.dy = SLIDE * (1 - part);
  } else if (text.animation === "typewriter") {
    const count = textUnits(text.text).filter((unit) => unit.index !== null).length;
    let shown = 0;
    while (shown < count && revealAt(shown, count, span) <= t + 1e-9) shown += 1;
    frame.shown = shown;
  }
  return frame;
}

// ---------- Dragging ----------

export type Guide = { axis: "x" | "y"; at: number };

/**
 * A dragged box's centre snapped to the guides: its centre to the middle and
 * the thirds, its edges to the safe margins, each within `snap` (fractions
 * of the frame); the box stays inside the frame sideways.
 */
export function snapText(center: { x: number; y: number }, size: { w: number; h: number }, snap: { x: number; y: number }, free = false): {
  x: number; y: number; guides: Guide[];
} {
  const halfW = size.w / 2, halfH = size.h / 2;
  let x = Math.min(Math.max(center.x, halfW), Math.max(halfW, 1 - halfW));
  let y = Math.min(Math.max(center.y, 0), 1);
  const guides: Guide[] = [];
  if (!free) {
    const pick = (value: number, targets: Array<{ offset: number; at: number }>, within: number): { value: number; at: number } | null => {
      let best: { value: number; at: number; distance: number } | null = null;
      for (const { offset, at } of targets) {
        const distance = Math.abs(value + offset - at);
        if (distance <= within && (!best || distance < best.distance)) best = { value: at - offset, at, distance };
      }
      return best;
    };
    const lines = [0.5, 1 / 3, 2 / 3];
    const sx = pick(x, [...lines.map((at) => ({ offset: 0, at })), { offset: -halfW, at: TEXT_SAFE }, { offset: halfW, at: 1 - TEXT_SAFE }], snap.x);
    if (sx) {
      x = sx.value;
      guides.push({ axis: "x", at: sx.at });
    }
    const sy = pick(y, [...lines.map((at) => ({ offset: 0, at })), { offset: -halfH, at: TEXT_SAFE }, { offset: halfH, at: 1 - TEXT_SAFE }], snap.y);
    if (sy) {
      y = sy.value;
      guides.push({ axis: "y", at: sy.at });
    }
  }
  return { x: Number(x.toFixed(4)), y: Number(y.toFixed(4)), guides };
}

/** The words on one line, shortened for a list. */
export function previewLine(text: string, limit = 60): string {
  const line = text.replace(/\s+/g, " ").trim();
  return line.length > limit ? `${line.slice(0, limit - 1)}…` : line;
}
