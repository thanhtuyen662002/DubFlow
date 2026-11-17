/**
 * Thumbnail (Issue #901): the picture a video is posted with, drawn in the
 * window from a frame of the video or a picture of the user's, with text on
 * it. Kept with the edit's draft (`project.thumbnail`); making or changing it
 * never renders the video. This file is the pure model; thumbnail_view.ts
 * draws it.
 */

import { SUBTITLE_FONTS, isFont, type SubtitleFont } from "./fonts.ts";

export const THUMB_WIDTH = 1280;
export const THUMB_HEIGHT = 720;
export const MAX_THUMB_ZOOM = 3;
export const MAX_THUMB_TEXTS = 6;
export const MAX_THUMB_TEXT = 120;
export const MIN_TEXT_SIZE = 24;
export const MAX_TEXT_SIZE = 220;

/** The background: a moment of a video (seconds into that file), or a picture. */
export type ThumbBackground = { kind: "frame"; path: string; time: number } | { kind: "image"; path: string };

export type ThumbText = {
  id: string;
  text: string;
  /** The text's centre, 0–1 across and down the thumbnail. */
  x: number;
  y: number;
  /** Pixels on the 1280×720 thumbnail. */
  size: number;
  font: SubtitleFont;
  bold: boolean;
  color: string;
  /** Outline colour and width (0 = none), in thumbnail pixels. */
  stroke: string;
  strokeWidth: number;
  shadow: boolean;
  /** A filled band behind the text, or none. */
  band: string | null;
  /** The place a layout made this text for; a later layout puts it in its own place again. Texts the user adds have none. */
  role?: ThumbRole;
};

export type ThumbRole = "title" | "label";

export type ThumbDoc = {
  version: 1;
  background: ThumbBackground | null;
  /** 1 = the picture just covers the thumbnail; more zooms in. */
  zoom: number;
  /** -1…1: which part of a picture wider or taller than the thumbnail is kept (0 = centre). */
  panX: number;
  panY: number;
  /** 0–0.7: a dark layer over the picture so text reads. */
  darken: number;
  texts: ThumbText[];
  format: "png" | "jpeg";
};

const HEX = /^#[0-9a-f]{6}$/i;

export function emptyThumb(): ThumbDoc {
  return { version: 1, background: null, zoom: 1, panX: 0, panY: 0, darken: 0, texts: [], format: "jpeg" };
}

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));
const finite = (value: unknown, fallback: number): number => typeof value === "number" && Number.isFinite(value) ? value : fallback;
const colour = (value: unknown, fallback: string): string => typeof value === "string" && HEX.test(value) ? value.toLowerCase() : fallback;

let made = 0;
export function newTextId(): string {
  made += 1;
  return `t${Date.now().toString(36)}${made.toString(36)}`;
}

export function makeText(text: string, over: Partial<ThumbText> = {}): ThumbText {
  return normalizeText({ id: newTextId(), text, x: 0.5, y: 0.8, size: 96, font: "Segoe UI Black", bold: true, color: "#ffffff",
    stroke: "#000000", strokeWidth: 8, shadow: true, band: null, ...over }) as ThumbText;
}

function normalizeText(value: unknown): ThumbText | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  if (typeof row.text !== "string") return null;
  const text = row.text.replace(/\r\n?/g, "\n").slice(0, MAX_THUMB_TEXT);
  return {
    id: typeof row.id === "string" && row.id ? row.id.slice(0, 40) : newTextId(),
    text,
    x: clamp(finite(row.x, 0.5), 0, 1),
    y: clamp(finite(row.y, 0.5), 0, 1),
    size: Math.round(clamp(finite(row.size, 96), MIN_TEXT_SIZE, MAX_TEXT_SIZE)),
    font: isFont(row.font) ? row.font : "Segoe UI Black",
    bold: row.bold !== false,
    color: colour(row.color, "#ffffff"),
    stroke: colour(row.stroke, "#000000"),
    strokeWidth: Math.round(clamp(finite(row.strokeWidth, 0), 0, 24)),
    shadow: row.shadow === true,
    band: row.band === null || row.band === undefined ? null : colour(row.band, "#e11d48"),
    ...(row.role === "title" || row.role === "label" ? { role: row.role } : {}),
  };
}

function normalizeBackground(value: unknown): ThumbBackground | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  if (typeof row.path !== "string" || !row.path.trim()) return null;
  if (row.kind === "image") return { kind: "image", path: row.path };
  if (row.kind === "frame") return { kind: "frame", path: row.path, time: Math.max(0, finite(row.time, 0)) };
  return null;
}

/** A saved thumbnail, checked and clamped; null when there is none (or it is not one). */
export function normalizeThumb(value: unknown): ThumbDoc | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  if (row.version !== 1) return null;
  const texts = Array.isArray(row.texts) ? row.texts.map(normalizeText).filter((text): text is ThumbText => !!text).slice(0, MAX_THUMB_TEXTS) : [];
  return {
    version: 1,
    background: normalizeBackground(row.background),
    zoom: clamp(finite(row.zoom, 1), 1, MAX_THUMB_ZOOM),
    panX: clamp(finite(row.panX, 0), -1, 1),
    panY: clamp(finite(row.panY, 0), -1, 1),
    darken: clamp(finite(row.darken, 0), 0, 0.7),
    texts,
    format: row.format === "png" ? "png" : "jpeg",
  };
}

/** Whether there is anything to keep (a blank thumbnail is not saved with the draft). */
export function hasThumb(doc: ThumbDoc | null): doc is ThumbDoc {
  return !!doc && (!!doc.background || doc.texts.some((text) => text.text.trim() !== ""));
}

export type Rect = { x: number; y: number; width: number; height: number };

/**
 * Where a picture of `width`×`height` is drawn so that it covers the whole
 * thumbnail (no blank edge at any zoom or pan), zoomed and panned.
 */
export function coverRect(width: number, height: number, zoom: number, panX: number, panY: number): Rect {
  if (!(width > 0) || !(height > 0)) return { x: 0, y: 0, width: THUMB_WIDTH, height: THUMB_HEIGHT };
  const scale = Math.max(THUMB_WIDTH / width, THUMB_HEIGHT / height) * clamp(zoom, 1, MAX_THUMB_ZOOM);
  const drawnWidth = width * scale, drawnHeight = height * scale;
  const spareX = drawnWidth - THUMB_WIDTH, spareY = drawnHeight - THUMB_HEIGHT;
  return {
    x: (-spareX / 2 - clamp(panX, -1, 1) * spareX / 2) || 0,
    y: (-spareY / 2 - clamp(panY, -1, 1) * spareY / 2) || 0,
    width: drawnWidth,
    height: drawnHeight,
  };
}

/** The pan after dragging the picture by (dx, dy) thumbnail pixels: the picture follows the pointer, never past its edge. */
export function dragPan(doc: ThumbDoc, width: number, height: number, dx: number, dy: number): { panX: number; panY: number } {
  const rect = coverRect(width, height, doc.zoom, 0, 0);
  const spareX = rect.width - THUMB_WIDTH, spareY = rect.height - THUMB_HEIGHT;
  return {
    panX: spareX > 0.5 ? clamp(doc.panX - 2 * dx / spareX, -1, 1) : 0,
    panY: spareY > 0.5 ? clamp(doc.panY - 2 * dy / spareY, -1, 1) : 0,
  };
}

/** Lines of `text` no wider than `maxWidth` by `measure` (words wrap; a word too long stays whole). */
export function wrapLines(text: string, maxWidth: number, measure: (line: string) => number): string[] {
  const lines: string[] = [];
  for (const paragraph of text.split("\n")) {
    const words = paragraph.split(/\s+/).filter(Boolean);
    if (!words.length) {
      lines.push("");
      continue;
    }
    let line = words[0];
    for (const word of words.slice(1)) {
      const next = `${line} ${word}`;
      if (measure(next) <= maxWidth) line = next;
      else {
        lines.push(line);
        line = word;
      }
    }
    lines.push(line);
  }
  while (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/** The widest a text's lines are drawn: 90 % of the thumbnail. */
export const MAX_LINE_WIDTH = THUMB_WIDTH * 0.9;

/**
 * The widest a line of `text` may be so that, drawn around its centre, it
 * stays on the thumbnail (rc23: "Chữ bên trái" put a long title centred at
 * 30 % across and cut its start off). Never under two letters' worth, so
 * words still wrap one by one near an edge.
 */
export function textRoom(text: ThumbText): number {
  const extra = text.strokeWidth * 2 + (text.band ? text.size * 0.6 : 0);
  const around = 2 * Math.min(text.x, 1 - text.x) * THUMB_WIDTH - extra;
  return Math.max(text.size * 2, Math.min(MAX_LINE_WIDTH, around));
}

/** Room to keep above and below a text's box: its outline, and accents stacked on capitals (Ể, Ấ) reach past the box. */
export type InkMargin = { top: number; bottom: number };

/** How far a text's ink can reach past its box at the top and bottom (a band already holds part of it). */
export function inkMargin(text: ThumbText): InkMargin {
  const band = text.band ? text.size * 0.15 : 0;
  return { top: Math.max(0, text.size * 0.25 - band) + text.strokeWidth, bottom: Math.max(0, text.size * 0.05 - band) + text.strokeWidth };
}

/**
 * The box moved onto the thumbnail where it fits, only as far as needed, so
 * no text is cut at an edge: with its ink margin where that fits too. One
 * too big stays where it is.
 */
export function fitBox(box: Rect, margin: InkMargin = { top: 0, bottom: 0 }): Rect {
  const x = box.width <= THUMB_WIDTH ? clamp(box.x, 0, THUMB_WIDTH - box.width) : box.x;
  const y = box.height + margin.top + margin.bottom <= THUMB_HEIGHT ? clamp(box.y, margin.top, THUMB_HEIGHT - box.height - margin.bottom)
    : box.height <= THUMB_HEIGHT ? clamp(box.y, 0, THUMB_HEIGHT - box.height) : box.y;
  return { ...box, x, y };
}

/** How a text is drawn: its lines, their widths, and its box on the thumbnail. */
export type TextLayout = { lines: string[]; widths: number[]; box: Rect };

/**
 * A text laid out to stay on the thumbnail. Its lines wrap to the room
 * around its centre (`textRoom`). Where that makes it taller than the
 * thumbnail (with its ink margin), they wrap wider instead, as narrow as
 * still fits top to bottom (at most 90 %), since a box can be moved in
 * sideways but not shrunk. The box is then moved in where it would be cut
 * (`fitBox`).
 */
export function layoutText(text: ThumbText, measure: (line: string) => number): TextLayout {
  const wrap = (room: number): TextLayout => {
    const lines = wrapLines(text.text, room, measure);
    const widths = lines.map(measure);
    return { lines, widths, box: textBox(text, widths) };
  };
  const margin = inkMargin(text);
  const fits = (box: Rect): boolean => box.height + margin.top + margin.bottom <= THUMB_HEIGHT;
  const room = textRoom(text);
  let laid = wrap(room);
  if (!fits(laid.box) && room < MAX_LINE_WIDTH) {
    laid = wrap(MAX_LINE_WIDTH);
    // Fewer lines never come from a narrower wrap, so the narrowest width that fits is found by halving.
    let low = room, high = MAX_LINE_WIDTH;
    while (fits(laid.box) && high - low > 1) {
      const middle = (low + high) / 2;
      const tried = wrap(middle);
      if (fits(tried.box)) {
        high = middle;
        laid = tried;
      } else low = middle;
    }
  }
  return { ...laid, box: fitBox(laid.box, margin) };
}

/** The text with its centre where it is drawn: `box` is where `layoutText` put it. */
export function centreOn(text: ThumbText, box: Rect | undefined): ThumbText {
  if (!box) return text;
  const x = clamp((box.x + box.width / 2) / THUMB_WIDTH, 0, 1);
  const y = clamp((box.y + box.height / 2) / THUMB_HEIGHT, 0, 1);
  return Math.abs(x - text.x) < 1e-6 && Math.abs(y - text.y) < 1e-6 ? text : { ...text, x, y };
}

/**
 * Screen pixels the pointer must go along an axis before a text drag moves
 * the text on that axis. Below it a hand's wobble changes nothing, so a
 * sideways drag never moves a text up or down.
 */
export const DRAG_START_PX = 3;

/**
 * Whether a key move of `move` px along one axis starts from where the text
 * is drawn (`drawn`) rather than its saved place (`saved`), both 0..1 of
 * `span` px. They differ only where the drawing moved the text in, holding
 * it against an edge. Moving away from that edge starts from the drawn
 * place, so the text moves at once; moving toward it keeps the saved
 * place, so the text stays held there when it re-wraps.
 */
export function leavesEdge(saved: number, drawn: number, move: number, span: number): boolean {
  const shift = (drawn - saved) * span;
  return Math.abs(shift) > 1e-6 && move !== 0 && Math.sign(move) === Math.sign(shift);
}

/** The text moved by an arrow key's (dx, dy) px, drawn at `box` (see `leavesEdge`); the other axis is left as it is. */
export function nudgeText(text: ThumbText, box: Rect | undefined, dx: number, dy: number): ThumbText {
  const drawn = centreOn(text, box);
  const from = {
    ...text,
    x: leavesEdge(text.x, drawn.x, dx, THUMB_WIDTH) ? drawn.x : text.x,
    y: leavesEdge(text.y, drawn.y, dy, THUMB_HEIGHT) ? drawn.y : text.y,
  };
  return moveText(from, dx, dy);
}

/** The box a text takes on the thumbnail, from its lines' widths. */
export function textBox(text: ThumbText, widths: number[]): Rect {
  const lineHeight = text.size * 1.15;
  const width = Math.max(0, ...widths) + text.strokeWidth * 2 + (text.band ? text.size * 0.6 : 0);
  const height = Math.max(1, widths.length) * lineHeight + (text.band ? text.size * 0.3 : 0);
  return { x: text.x * THUMB_WIDTH - width / 2, y: text.y * THUMB_HEIGHT - height / 2, width, height };
}

/** The top text under the point (thumbnail pixels), else null. */
export function textAt(boxes: Array<{ id: string; box: Rect }>, x: number, y: number): string | null {
  for (let index = boxes.length - 1; index >= 0; index -= 1) {
    const { id, box } = boxes[index];
    if (x >= box.x - 8 && x <= box.x + box.width + 8 && y >= box.y - 8 && y <= box.y + box.height + 8) return id;
  }
  return null;
}

/** The text moved by (dx, dy) thumbnail pixels, its centre kept on the thumbnail. */
export function moveText(text: ThumbText, dx: number, dy: number): ThumbText {
  return { ...text, x: clamp(text.x + dx / THUMB_WIDTH, 0, 1), y: clamp(text.y + dy / THUMB_HEIGHT, 0, 1) };
}

export type ThumbTemplate = {
  id: string; label: string; darken: number;
  /** Whether the layout has a small label above its title. */
  labelled: boolean;
  texts: (title: string, label: string) => ThumbText[];
};

/** The label a labelled layout starts with. */
export const TEMPLATE_LABEL = "MỚI";
const LABEL_LOOK: Partial<ThumbText> = { x: 0.16, y: 0.16, size: 64, band: "#e11d48", strokeWidth: 0, shadow: false, role: "label" };

/** Ready layouts; each keeps the background and lays the texts out again. */
export const THUMB_TEMPLATES: readonly ThumbTemplate[] = [
  {
    id: "big-bottom", label: "Tiêu đề lớn phía dưới", darken: 0.15, labelled: false,
    texts: (title) => [makeText(title, { y: 0.8, size: 110, role: "title" })],
  },
  {
    id: "two-lines", label: "Hai dòng: nhãn + tiêu đề", darken: 0.25, labelled: true,
    texts: (title, label) => [makeText(label, LABEL_LOOK), makeText(title, { y: 0.74, size: 100, color: "#facc15", role: "title" })],
  },
  {
    id: "left-column", label: "Chữ bên trái", darken: 0.35, labelled: false,
    texts: (title) => [makeText(title, { x: 0.3, y: 0.5, size: 92, role: "title" })],
  },
  {
    id: "band", label: "Dải màu ngang", darken: 0, labelled: false,
    texts: (title) => [makeText(title, { y: 0.84, size: 84, band: "#111827", strokeWidth: 0, shadow: false, role: "title" })],
  },
];

const LOOK_KEYS = ["x", "y", "size", "font", "bold", "color", "stroke", "strokeWidth", "shadow", "band"] as const;
const PLAIN_LABEL = makeText(TEMPLATE_LABEL, LABEL_LOOK);

/** The "MỚI" a layout put there, not retyped, moved or restyled: the one text a layout change may drop. */
export function untouchedLabel(text: ThumbText): boolean {
  return text.text.trim() === TEMPLATE_LABEL && LOOK_KEYS.every((key) => text[key] === PLAIN_LABEL[key]);
}

/**
 * The layout `id` over the thumbnail, or the same thumbnail when the layout's
 * texts do not fit beside the ones already there (at most MAX_THUMB_TEXTS).
 * A change of layout never loses a text the user wrote:
 * - the layout's own title and label (the texts a layout made) go to the new
 *   layout's places; on a layout without a label the label stays where it is;
 * - every other text, such as one the user added, stays exactly as it was;
 * - only the layout's own "MỚI", untouched, can go.
 * A thumbnail saved before layouts marked their texts takes its largest text
 * as the title and its next text as the label, and drops nothing. `name`
 * titles a thumbnail that has no title.
 */
export function applyTemplate(doc: ThumbDoc, id: string, name: string): ThumbDoc {
  const template = THUMB_TEMPLATES.find((item) => item.id === id);
  if (!template) return doc;
  const words = doc.texts.filter((text) => text.text.trim());
  const marked = doc.texts.some((text) => text.role);
  const title = marked
    ? words.find((text) => text.role === "title")
    : words.filter((text) => !untouchedLabel(text)).reduce<ThumbText | undefined>((best, text) => (!best || text.size > best.size ? text : best), undefined);
  // The layout's own "MỚI", still as it made it. On a thumbnail saved before roles nothing is known
  // to be the layout's, so nothing is dropped and its next text is the label.
  const generated = (text: ThumbText): boolean => marked && text.role === "label" && untouchedLabel(text);
  const label = marked
    ? words.find((text) => text !== title && text.role === "label" && !generated(text))
    : words.find((text) => text !== title);
  const moved = template.labelled ? label : undefined;
  const kept = words.filter((text) => text !== title && text !== moved && !generated(text));
  let texts = template.texts(title?.text ?? (name.trim() || "Tiêu đề video"), moved?.text ?? TEMPLATE_LABEL);
  // No room for a fresh "MỚI": the layout goes without one rather than drop a text.
  if (kept.length + texts.length > MAX_THUMB_TEXTS && !moved) texts = texts.filter((text) => text.role !== "label");
  if (kept.length + texts.length > MAX_THUMB_TEXTS) return doc;
  return { ...doc, darken: template.darken, texts: [...kept, ...texts] };
}

/** The suggested file name: the video's name and " - thumbnail". */
export function thumbName(videoName: string): string {
  const stem = videoName.replace(/\.[a-z0-9]{2,4}$/i, "").trim();
  return `${stem || "video"} - thumbnail`;
}

export { SUBTITLE_FONTS };
