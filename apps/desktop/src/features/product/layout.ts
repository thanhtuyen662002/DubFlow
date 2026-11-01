/**
 * The engine's template layout for the live preview. `layout.json` is printed
 * by `python -m engine.dubflow.product.layout`; tests/product checks it still
 * matches the engine, so the preview and the render share every position.
 */
import data from "./layout.json" with { type: "json" };
import type { Template } from "./model.ts";

export type TextPlace = {
  x: number; y: number; size: number; colour: string; bold: boolean; align: number; wrap: number;
  box: { colour: string; opacity: number; pad: number } | null;
  outline: number;
  shadow: { offset: number; opacity: number } | null;
};
type Box = { x: number; y: number; width: number; height: number };

export type Layout = {
  width: number; height: number; font: string; fullFrameRatio: number;
  templates: {
    "ban-tin": {
      media: number; panel: string; strip: { y: number; height: number; colour: string };
      background: { panel: [number, number]; frame: [number, number] };
      title: TextPlace; caption: TextPlace; price: TextPlace; outlined: { title: TextPlace; caption: TextPlace };
    };
    "toan-man-hinh": { media: number; title: TextPlace; caption: TextPlace; price: TextPlace };
    "chi-chu": {
      media: number; gradient: [string, string]; scrim: number; background: { frame: [number, number] };
      cardBox: Box & { radius: number; opacity: number; bar: Box & { colour: string } };
      minCardMs: number; slide: { px: number; ms: number };
      title: TextPlace; card: TextPlace; counter: TextPlace; price: TextPlace; caption: TextPlace;
    };
  };
};

export const LAYOUT = data as unknown as Layout;

/**
 * libass sizes Arial so its ascent plus descent (2288 of 2048 font units)
 * equals the ASS font size; CSS sizes the em square. A line is `size` tall.
 */
export const ASS_EM = 2048 / 2288;

/** Inline CSS for the block holding one text item, anchored like ASS `\an` at (x, y). */
export function anchorStyle(place: TextPlace): string {
  const row = Math.floor((place.align - 1) / 3); // 0 bottom, 1 middle, 2 top
  const column = (place.align - 1) % 3; // 0 left, 1 centre, 2 right
  const shift = row === 2 ? "0" : row === 1 ? "-50%" : "-100%";
  const left = column === 1 ? place.x - place.wrap / 2 : column === 0 ? place.x : place.x - place.wrap;
  const width = column === 1 ? `width: ${place.wrap}px; ` : "";
  return `left: ${left}px; top: ${place.y}px; ${width}transform: translateY(${shift}); text-align: ${["left", "center", "right"][column]}; `
    + `font: ${place.bold ? 700 : 400} ${(place.size * ASS_EM).toFixed(2)}px/${place.size}px ${LAYOUT.font}, sans-serif; color: ${place.colour}`;
}

function rgba(hex: string, opacity: number): string {
  const value = Number.parseInt(hex.slice(1), 16);
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${opacity})`;
}

/** Inline CSS for the text itself: the opaque box, the outline and the drop shadow libass draws. */
export function inkStyle(place: TextPlace): string {
  const shadows: string[] = [];
  if (place.outline) {
    for (let angle = 0; angle < 360; angle += 45) {
      const radians = (angle * Math.PI) / 180;
      shadows.push(`${(Math.cos(radians) * place.outline).toFixed(1)}px ${(Math.sin(radians) * place.outline).toFixed(1)}px 0 #000`);
    }
  }
  if (place.shadow) shadows.push(`${place.shadow.offset}px ${place.shadow.offset}px 0 ${rgba("#000000", place.shadow.opacity)}`);
  const box = place.box
    ? `background: ${rgba(place.box.colour, place.box.opacity)}; padding: ${place.box.pad}px; box-decoration-break: clone; -webkit-box-decoration-break: clone; `
    : "";
  return `${box}text-shadow: ${shadows.join(", ") || "none"}`;
}

/** Equal slots for the highlight cards, each at least `minCardMs`; like `layout.card_times`. */
export function cardTimes(count: number, durationMs: number, minCardMs = LAYOUT.templates["chi-chu"].minCardMs): Array<[number, number]> {
  const shown = count && durationMs > 0 ? Math.min(count, Math.max(1, Math.floor(durationMs / minCardMs))) : 0;
  return Array.from({ length: shown }, (_, index) => [Math.floor((durationMs * index) / shown), Math.floor((durationMs * (index + 1)) / shown)]);
}

/** What the template shows as cards (Chỉ chữ): the highlights, or the name when there are none. */
export function cardTexts(name: string, features: readonly string[]): { title: string; cards: string[] } {
  const clean = (value: string) => value.replace(/\s+/g, " ").trim();
  const cards = features.map(clean).filter(Boolean);
  const title = clean(name);
  return !cards.length && title ? { title: "", cards: [title] } : { title, cards };
}

/** One caption: at most two lines in the template, like the engine's 48-character captions. */
export const MAX_CAPTION_CHARS = 48;

/** A script as the captions the engine would show, split at sentences and clauses. */
export function captionChunks(script: string): string[] {
  const chunks: string[] = [];
  for (const sentence of script.match(/[^.!?…\n]+[.!?…]*/g) ?? []) {
    const text = sentence.replace(/\s+/g, " ").trim();
    if (!/[\p{L}\p{N}]/u.test(text)) continue;
    let current = "";
    for (const word of text.split(" ")) {
      const candidate = current ? `${current} ${word}` : word;
      if (candidate.length <= MAX_CAPTION_CHARS || !current) current = candidate;
      else {
        chunks.push(current);
        current = word;
      }
    }
    if (current) chunks.push(current);
  }
  return chunks;
}

/** Where the preview's media fills, from the top: 1200 rows, the whole frame, or nothing. */
export function mediaHeight(template: Template): number {
  return LAYOUT.templates[template].media;
}
