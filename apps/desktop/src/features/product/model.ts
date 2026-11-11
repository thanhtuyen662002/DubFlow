import { MAX_SCRIPT_CHARS, type ProductInfo } from "./ai.ts";
import { isLevel } from "../mastering/model.ts";

/** What `product_render` takes; the host and the engine check it again. */
export type ProductRequest = {
  name: string;
  price: string;
  voiceId: string;
  variants: string[];
  visuals: string[];
  music: string | null;
  musicVolume: number;
  background: string | null;
  template: Template;
  /** The highlights "Chỉ chữ" shows as cards; empty for the other templates. */
  features: string[];
  /** The part of the background kept when it is cropped (0–1 across and down). */
  focus: Focus;
  speed: number;
  cpuOnly: boolean;
  outputRoot: string | null;
  /** Tối ưu hậu kỳ tự động: absent is off. */
  mastering?: "light" | "balanced" | "dynamic";
  /** The AI that may look at the footage when mastering is on (its saved key, never sent from here). */
  visionAi?: { provider: string };
};

/** `.dubflow-work/status.json`, written by `engine.dubflow.product`. */
export type ProductStatus = {
  state: "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
  done: number;
  total: number;
  message: string;
  videos: Array<{ index: number; ok: boolean; path?: string; message?: string; duration_ms?: number; mastering?: unknown }>;
};

export type Template = "ban-tin" | "toan-man-hinh" | "chi-chu";
export type Focus = { x: number; y: number };
export const CENTER: Focus = { x: 0.5, y: 0.5 };

/** The three templates, in the order the page offers them; Bản tin is the default. */
export const TEMPLATES: ReadonlyArray<{ id: Template; label: string; hint: string; size: string | null; media: boolean }> = [
  { id: "ban-tin", label: "Bản tin", hint: "Ảnh ở trên, nền dưới che chữ gốc", size: "1080×720 (nền dưới) hoặc 1080×1920 (cả khung)", media: true },
  { id: "toan-man-hinh", label: "Toàn màn hình", hint: "Ảnh phủ cả khung, chữ trên ô mờ", size: null, media: true },
  { id: "chi-chu", label: "Chỉ chữ", hint: "Không cần ảnh: thẻ chữ động trên nền", size: "1080×1920", media: false },
];

export function isTemplate(value: unknown): value is Template {
  return TEMPLATES.some((template) => template.id === value);
}

export const SPEEDS = [1, 1.1, 1.2] as const;

export const MAX_PHOTOS_TO_READ = 8;
export const MAX_VISUALS = 30;
export const MAX_VARIANTS = 5;
export const MAX_FEATURE_CHARS = 200;
const IMAGE = /\.(jpe?g|png|webp|bmp)$/i;

export function isImage(path: string): boolean {
  return IMAGE.test(path);
}

export function fileName(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

/** The form's feature box: one feature per line, bullets removed. */
export function parseFeatures(text: string): string[] {
  return [...new Set(text.split("\n").map((line) => line.replace(/^\s*[-•*+\d.)]+\s*/, "").trim().slice(0, MAX_FEATURE_CHARS)).filter(Boolean))].slice(0, 8);
}

export function formatFeatures(features: readonly string[]): string {
  return features.join("\n");
}

export function readForm(fields: { name: string; price: string; features: string; audience: string; notes: string }): ProductInfo {
  return {
    name: fields.name.trim(), price: fields.price.trim(), features: parseFeatures(fields.features),
    audience: fields.audience.trim(), notes: fields.notes.trim(),
  };
}

/** Paths in order without repeats, at most `limit`. */
export function addPaths(current: readonly string[], added: readonly string[], limit: number): string[] {
  return [...new Set([...current, ...added])].slice(0, limit);
}

/** A photo or clip on the page: the AI reads the photos; `video` ones go into the video, in order. */
export type MediaItem = { path: string; video: boolean };

export function addMedia(current: readonly MediaItem[], added: readonly string[], limit: number): MediaItem[] {
  const known = new Set(current.map((item) => item.path));
  const fresh = [...new Set(added)].filter((path) => !known.has(path)).map((path) => ({ path, video: true }));
  return [...current, ...fresh].slice(0, limit);
}

/** `list` with the item at `from` moved to `to` (both clamped). */
export function moveItem<T>(list: readonly T[], from: number, to: number): T[] {
  const result = [...list];
  if (from < 0 || from >= result.length) return result;
  const [item] = result.splice(from, 1);
  result.splice(Math.max(0, Math.min(result.length, to)), 0, item);
  return result;
}

/** The photos sent to the AI: the first few images, whether or not they go into the video. */
export function photosToRead(media: readonly MediaItem[]): string[] {
  return media.map((item) => item.path).filter(isImage).slice(0, MAX_PHOTOS_TO_READ);
}

export function videoMedia(media: readonly MediaItem[]): string[] {
  return media.filter((item) => item.video).map((item) => item.path);
}

function clampFocus(focus: Focus | null | undefined): Focus {
  const clamp = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? Math.round(Math.min(1, Math.max(0, value)) * 10_000) / 10_000 : 0.5);
  return { x: clamp(focus?.x), y: clamp(focus?.y) };
}

/** The render request, or the reason it cannot start yet. */
export function buildRequest(input: {
  info: ProductInfo; voiceId: string | null; scripts: readonly { text: string; use: boolean }[];
  visuals: readonly string[]; music: string | null; volumePercent: number; cpuOnly: boolean; outputRoot: string | null;
  background?: string | null; template?: string; speed?: number; focus?: Focus | null;
  /** Tối ưu hậu kỳ tự động; "off" (the default) sends nothing new. */
  mastering?: string; visionProvider?: string | null;
}): ProductRequest | string {
  const template: Template = isTemplate(input.template) ? input.template : "ban-tin";
  const textOnly = template === "chi-chu";
  const variants = input.scripts.filter((item) => item.use).map((item) => item.text.trim()).filter((text) => /[\p{L}\p{N}]/u.test(text));
  if (!variants.length) return "Hãy chọn ít nhất một kịch bản.";
  if (variants.length > MAX_VARIANTS) return `Mỗi lần tạo tối đa ${MAX_VARIANTS} video.`;
  if (variants.some((text) => text.length > MAX_SCRIPT_CHARS)) return `Mỗi kịch bản tối đa ${MAX_SCRIPT_CHARS} ký tự.`;
  if (!textOnly && !input.visuals.length) return "Hãy thêm ảnh hoặc clip cho video, hoặc chọn mẫu “Chỉ chữ”.";
  if (input.visuals.length > MAX_VISUALS) return `Tối đa ${MAX_VISUALS} ảnh hoặc clip.`;
  if (textOnly && !input.info.name && !input.info.features.length) return "Mẫu “Chỉ chữ” cần tên sản phẩm hoặc điểm nổi bật.";
  if (!input.voiceId) return "Hãy chọn giọng đọc.";
  return {
    name: input.info.name, price: input.info.price, voiceId: input.voiceId, variants, visuals: textOnly ? [] : [...input.visuals],
    music: input.music, musicVolume: Math.min(1, Math.max(0, Math.round(input.volumePercent) / 100)),
    // Full-screen media leaves nothing of a background visible.
    background: template === "toan-man-hinh" ? null : input.background ?? null,
    template, features: textOnly ? input.info.features.map((item) => item.slice(0, MAX_FEATURE_CHARS)) : [],
    focus: clampFocus(input.focus),
    speed: SPEEDS.find((speed) => speed === input.speed) ?? 1,
    cpuOnly: input.cpuOnly, outputRoot: input.outputRoot,
    // Mastering picks footage by content, so "Chỉ chữ" (no footage) never asks for it.
    ...(isLevel(input.mastering) && input.mastering !== "off" && !textOnly
      ? { mastering: input.mastering, ...(input.visionProvider ? { visionAi: { provider: input.visionProvider } } : {}) } : {}),
  };
}

export function parseStatus(value: unknown): ProductStatus | null {
  const object = (value ?? {}) as Partial<ProductStatus>;
  if (!["RUNNING", "COMPLETED", "FAILED", "CANCELLED"].includes(object.state as string) || typeof object.message !== "string") return null;
  const videos = Array.isArray(object.videos) ? object.videos.filter((video) => video && typeof video.index === "number" && typeof video.ok === "boolean") : [];
  return {
    state: object.state as ProductStatus["state"], message: object.message,
    done: typeof object.done === "number" ? object.done : 0, total: typeof object.total === "number" ? object.total : 0, videos,
  };
}

/** Raw bytes as base64, in chunks small enough for `String.fromCharCode`. */
export function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let start = 0; start < bytes.length; start += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  }
  return btoa(binary);
}

/** One image of the background library (`product_backgrounds`). */
export type LibraryBackground = { id: string; extension: string; name: string; width: number; height: number; path: string };

export function parseBackgrounds(value: unknown): LibraryBackground[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is LibraryBackground => !!item && typeof item.id === "string" && /^[0-9a-f]{64}$/.test(item.id)
    && typeof item.path === "string" && typeof item.name === "string" && Number(item.width) > 0 && Number(item.height) > 0);
}

/** The last background chosen for each template and where it was focused; kept in localStorage. */
export type BackgroundMemory = { choice: Partial<Record<Template, string>>; focus: Record<string, Focus> };

export function parseBackgroundMemory(raw: string | null): BackgroundMemory {
  const memory: BackgroundMemory = { choice: {}, focus: {} };
  try {
    const value = JSON.parse(raw ?? "{}") as Partial<BackgroundMemory>;
    for (const template of TEMPLATES) {
      const id = value.choice?.[template.id];
      if (typeof id === "string" && /^[0-9a-f]{64}$/.test(id)) memory.choice[template.id] = id;
    }
    for (const [key, focus] of Object.entries(value.focus ?? {})) {
      if (/^[a-z-]+:[0-9a-f]{64}$/.test(key) && focus && typeof focus === "object") memory.focus[key] = clampFocus(focus);
    }
  } catch {
    // A damaged value just starts over.
  }
  return memory;
}

export function focusKey(template: Template, id: string): string {
  return `${template}:${id}`;
}

/** Which part of the frame the background fills: the panel or the whole frame, or nothing for Toàn màn hình. */
export function backgroundBox(template: Template, size: { width: number; height: number }, fullFrameRatio: number): "panel" | "frame" | null {
  if (template === "toan-man-hinh") return null;
  if (template === "chi-chu") return "frame";
  return size.height >= size.width * fullFrameRatio ? "frame" : "panel";
}

/** How far an image covering `box` (object-fit: cover) overflows it on each axis, in box pixels. */
export function coverOverflow(image: { width: number; height: number }, box: { width: number; height: number }): { x: number; y: number } {
  const scale = Math.max(box.width / image.width, box.height / image.height);
  return { x: Math.max(0, image.width * scale - box.width), y: Math.max(0, image.height * scale - box.height) };
}

/**
 * Dragging the picture by (dx, dy) box pixels moves the kept part the other
 * way; the engine crops at `(overflow) × focus`, like CSS `object-position`.
 */
export function dragFocus(focus: Focus, overflow: { x: number; y: number }, dx: number, dy: number): Focus {
  return clampFocus({
    x: overflow.x > 0 ? focus.x - dx / overflow.x : focus.x,
    y: overflow.y > 0 ? focus.y - dy / overflow.y : focus.y,
  });
}

/** An earlier run found by `product_runs`. */
export type ProductRun = { dir: string; name: string; template: string; created: number; status: ProductStatus | null };

export function parseRuns(value: unknown): ProductRun[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item) => !!item && typeof item === "object" && typeof item.dir === "string" && item.dir)
    .map((item) => ({
      dir: item.dir as string, name: typeof item.name === "string" ? item.name : "", template: typeof item.template === "string" ? item.template : "ban-tin",
      created: Number.isFinite(item.created) ? Number(item.created) : 0, status: parseStatus(item.status),
    }));
}

/** "dd/mm hh:mm" of Unix seconds, or "" when unknown. */
export function shortDate(seconds: number): string {
  const when = new Date(seconds * 1000);
  if (!seconds || !Number.isFinite(when.getTime())) return "";
  const two = (value: number): string => String(value).padStart(2, "0");
  return `${two(when.getDate())}/${two(when.getMonth() + 1)} ${two(when.getHours())}:${two(when.getMinutes())}`;
}

/** One row of the Bán hàng list: a run with its finished videos and how far it got. */
export type RunRow = {
  dir: string; title: string; state: ProductStatus["state"] | null; text: string; fraction: number | null;
  videos: Array<{ index: number; outputPath: string; displayName: string; seconds: number | null }>;
};

export function runRow(run: ProductRun): RunRow {
  const title = run.name || "Video bán hàng";
  const status = run.status;
  const videos = (status?.videos ?? []).filter((video) => video.ok && video.path).map((video) => ({
    index: video.index, outputPath: video.path!, displayName: `${title} · video ${video.index}`,
    seconds: video.duration_ms ? Math.round(video.duration_ms / 1000) : null,
  }));
  const state = status?.state ?? null;
  const fraction = state === "RUNNING" && status && status.total > 0 ? Math.min(1, status.done / status.total) : null;
  const count = videos.length ? `${videos.length} video` : "";
  const text = state === "RUNNING" ? status!.message || "Đang tạo…"
    : state === "COMPLETED" ? [count, shortDate(run.created)].filter(Boolean).join(" · ")
      : state === "CANCELLED" ? [count ? `Đã hủy, giữ ${count}` : "Đã hủy", shortDate(run.created)].filter(Boolean).join(" · ")
        : state === "FAILED" ? status!.message || "Lỗi" : "Chưa rõ trạng thái";
  return { dir: run.dir, title, state, text, fraction, videos };
}

/** What one product video was made from (`product_spec`), as the editor's Mẫu mode edits it. */
export type ProductSpec = {
  name: string; price: string; voiceId: string; script: string; visuals: string[]; music: string | null; musicVolume: number;
  background: string | null; template: Template; speed: number; features: string[]; focus: Focus;
};
export type SpecInfo = { spec: ProductSpec; variant: number; run: string; durationMs: number | null; narration: boolean };

export function parseProductSpec(value: unknown): SpecInfo | null {
  const object = (value ?? {}) as Record<string, unknown>;
  const raw = (object.spec ?? {}) as Record<string, unknown>;
  const variant = Number(object.variant);
  const variants = Array.isArray(raw.variants) ? raw.variants : [];
  const script = variants[variant - 1];
  if (!Number.isInteger(variant) || variant < 1 || typeof script !== "string" || typeof object.run !== "string" || !object.run) return null;
  const text = (item: unknown, limit: number): string => (typeof item === "string" ? item.slice(0, limit) : "");
  const path = (item: unknown): string | null => (typeof item === "string" && item ? item : null);
  const number = (item: unknown, fallback: number, low: number, high: number): number =>
    typeof item === "number" && Number.isFinite(item) ? Math.min(high, Math.max(low, item)) : fallback;
  const focus = (raw.focus ?? {}) as Partial<Focus>;
  return {
    spec: {
      name: text(raw.name, 120), price: text(raw.price, 60), voiceId: text(raw.voice_id, 96), script,
      visuals: Array.isArray(raw.visuals) ? raw.visuals.filter((item): item is string => typeof item === "string" && !!item).slice(0, MAX_VISUALS) : [],
      music: path(raw.music), musicVolume: number(raw.music_volume, 0.15, 0, 1), background: path(raw.background),
      template: isTemplate(raw.template) ? raw.template : "ban-tin",
      speed: SPEEDS.find((speed) => speed === raw.speed) ?? 1,
      features: Array.isArray(raw.features) ? raw.features.filter((item): item is string => typeof item === "string").slice(0, 8) : [],
      focus: clampFocus({ x: Number(focus.x ?? 0.5), y: Number(focus.y ?? 0.5) }),
    },
    variant, run: object.run,
    durationMs: typeof object.duration_ms === "number" && object.duration_ms > 0 ? object.duration_ms : null,
    narration: object.narration === true,
  };
}

/** Whether a new render can reuse the earlier narration: the same words, voice and speed. */
export function keepsVoice(before: SpecInfo, after: { script: string; voiceId: string; speed: number }): boolean {
  return before.narration && before.spec.script.trim() === after.script.trim() && before.spec.voiceId === after.voiceId && before.spec.speed === after.speed;
}
