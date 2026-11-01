/**
 * The live 9:16 preview: the chosen template drawn in HTML at the engine's
 * 1080×1920 coordinates (the page scales it down), with the first photo or
 * clip, the background, the name, price, highlight cards and a caption.
 */
import { anchorStyle, cardTexts, cardTimes, inkStyle, LAYOUT, type TextPlace } from "./layout.ts";
import type { Focus, Template } from "./model.ts";

export type PreviewState = {
  template: Template;
  name: string;
  price: string;
  features: readonly string[];
  /** The caption shown now (a placeholder when there is no script yet). */
  caption: string;
  /** The first photo or clip in the video; `url` is null while it loads or when it cannot be shown. */
  media: { url: string | null; label: string } | null;
  /** The chosen background; `box` says whether it fills the panel or the whole frame. */
  background: { url: string | null; width: number; height: number; focus: Focus; box: "panel" | "frame" } | null;
  /** Where the mock playback is, for the card on screen. */
  timeMs: number;
  totalMs: number;
};

export type PreviewLayers = { base: string; card: string; caption: string };

const ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ESCAPES[character]);
}

/** Text the way the engine's ASS keeps it: one line of words, no override braces. */
function plain(value: string): string {
  return value.replace(/\\/g, "/").replace(/\{/g, "(").replace(/\}/g, ")").replace(/\s+/g, " ").trim();
}

function text(place: TextPlace, value: string, className = ""): string {
  if (!plain(value)) return "";
  return `<div class="pv-text ${className}" style="${anchorStyle(place)}"><span style="${inkStyle(place)}">${escapeHtml(plain(value))}</span></div>`;
}

function picture(url: string | null, focus: Focus, label: string): string {
  return url
    ? `<img src="${escapeHtml(url)}" alt="" draggable="false" style="object-position: ${(focus.x * 100).toFixed(2)}% ${(focus.y * 100).toFixed(2)}%">`
    : `<span class="pv-missing">${escapeHtml(label)}</span>`;
}

function backgroundLayer(state: PreviewState): string {
  const background = state.background;
  if (!background) return "";
  const top = background.box === "panel" ? LAYOUT.templates["ban-tin"].media : 0;
  return `<div class="pv-bg" data-box="${background.box}" style="top: ${top}px; height: ${LAYOUT.height - top}px" title="Kéo để chọn phần ảnh giữ lại">`
    + `${picture(background.url, background.focus, "Đang tải ảnh nền…")}</div>`;
}

function mediaLayer(state: PreviewState, height: number): string {
  const media = state.media;
  const inner = media ? picture(media.url, { x: 0.5, y: 0.5 }, media.label) : `<span class="pv-missing">Chưa có ảnh hoặc clip</span>`;
  return `<div class="pv-media" style="height: ${height}px">${inner}</div>`;
}

/** The current highlight card for Chỉ chữ, or "" for the other templates and when there is nothing to show. */
function cardLayer(state: PreviewState): string {
  if (state.template !== "chi-chu") return "";
  const layout = LAYOUT.templates["chi-chu"];
  const { cards } = cardTexts(state.name, state.features);
  const slots = cardTimes(cards.length, state.totalMs);
  if (!slots.length) return "";
  const time = Math.min(Math.max(0, state.timeMs), state.totalMs - 1);
  const index = Math.max(0, slots.findIndex(([start, end]) => time >= start && time < end));
  const { cardBox: box } = layout;
  const counter = slots.length > 1 ? text(layout.counter, `${index + 1}/${slots.length}`) : "";
  return `<div class="pv-card" data-card="${index}" style="--slide: ${layout.slide.px}px; --slide-ms: ${layout.slide.ms}ms">`
    + `<div class="pv-card-box" style="left: ${box.x}px; top: ${box.y}px; width: ${box.width}px; height: ${box.height}px; border-radius: ${box.radius}px; background: rgba(0, 0, 0, ${box.opacity})"></div>`
    + `<div class="pv-card-box" style="left: ${box.x + box.bar.x}px; top: ${box.y + box.bar.y}px; width: ${box.bar.width}px; height: ${box.bar.height}px; border-radius: ${box.bar.width / 2}px; background: ${box.bar.colour}"></div>`
    + `${counter}${text(layout.card, cards[index])}</div>`;
}

/** The preview as three layers, so a new card or caption does not redraw (and re-animate) the rest. */
export function previewLayers(state: PreviewState): PreviewLayers {
  const parts: string[] = [];
  let caption: TextPlace;
  if (state.template === "ban-tin") {
    const layout = LAYOUT.templates["ban-tin"];
    const outlined = state.background ? layout.outlined : layout;
    parts.push(`<div class="pv-fill" style="background: ${layout.panel}"></div>`, backgroundLayer(state), mediaLayer(state, layout.media),
      `<div class="pv-fill" style="top: ${layout.strip.y}px; height: ${layout.strip.height}px; background: ${layout.strip.colour}"></div>`,
      text(outlined.title, state.name), text(layout.price, state.price));
    caption = outlined.caption;
  } else if (state.template === "toan-man-hinh") {
    const layout = LAYOUT.templates["toan-man-hinh"];
    parts.push(mediaLayer(state, layout.media), text(layout.title, state.name), text(layout.price, state.price));
    caption = layout.caption;
  } else {
    const layout = LAYOUT.templates["chi-chu"];
    parts.push(`<div class="pv-fill" style="background: linear-gradient(180deg, ${layout.gradient[0]}, ${layout.gradient[1]})"></div>`);
    if (state.background) parts.push(backgroundLayer(state), `<div class="pv-fill pv-scrim" style="background: rgba(0, 0, 0, ${layout.scrim})"></div>`);
    parts.push(text(layout.title, cardTexts(state.name, state.features).title), text(layout.price, state.price));
    caption = layout.caption;
  }
  return { base: parts.join(""), card: cardLayer(state), caption: text(caption, state.caption, "pv-caption") };
}
