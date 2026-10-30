import { MAX_SCRIPT_CHARS, type ProductInfo } from "./ai.ts";

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
  speed: number;
  cpuOnly: boolean;
  outputRoot: string | null;
};

/** `.dubflow-work/status.json`, written by `engine.dubflow.product`. */
export type ProductStatus = {
  state: "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
  done: number;
  total: number;
  message: string;
  videos: Array<{ index: number; ok: boolean; path?: string; message?: string; duration_ms?: number }>;
};

export type Template = "ban-tin" | "toan-man-hinh";
export const SPEEDS = [1, 1.1, 1.2] as const;

export const MAX_PHOTOS_TO_READ = 8;
export const MAX_VISUALS = 30;
export const MAX_VARIANTS = 5;
const IMAGE = /\.(jpe?g|png|webp|bmp)$/i;

export function isImage(path: string): boolean {
  return IMAGE.test(path);
}

export function fileName(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

/** The form's feature box: one feature per line, bullets removed. */
export function parseFeatures(text: string): string[] {
  return [...new Set(text.split("\n").map((line) => line.replace(/^\s*[-•*+\d.)]+\s*/, "").trim()).filter(Boolean))].slice(0, 8);
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

/** The render request, or the reason it cannot start yet. */
export function buildRequest(input: {
  info: ProductInfo; voiceId: string | null; scripts: readonly { text: string; use: boolean }[];
  visuals: readonly string[]; music: string | null; volumePercent: number; cpuOnly: boolean; outputRoot: string | null;
  background?: string | null; template?: string; speed?: number;
}): ProductRequest | string {
  const variants = input.scripts.filter((item) => item.use).map((item) => item.text.trim()).filter((text) => /[\p{L}\p{N}]/u.test(text));
  if (!variants.length) return "Hãy chọn ít nhất một kịch bản.";
  if (variants.length > MAX_VARIANTS) return `Mỗi lần tạo tối đa ${MAX_VARIANTS} video.`;
  if (variants.some((text) => text.length > MAX_SCRIPT_CHARS)) return `Mỗi kịch bản tối đa ${MAX_SCRIPT_CHARS} ký tự.`;
  if (!input.visuals.length) return "Hãy chọn ảnh hoặc clip cho video.";
  if (input.visuals.length > MAX_VISUALS) return `Tối đa ${MAX_VISUALS} ảnh hoặc clip.`;
  if (!input.voiceId) return "Hãy chọn giọng đọc.";
  return {
    name: input.info.name, price: input.info.price, voiceId: input.voiceId, variants, visuals: [...input.visuals],
    music: input.music, musicVolume: Math.min(1, Math.max(0, Math.round(input.volumePercent) / 100)), background: input.background ?? null,
    template: input.template === "toan-man-hinh" ? "toan-man-hinh" : "ban-tin",
    speed: SPEEDS.find((speed) => speed === input.speed) ?? 1,
    cpuOnly: input.cpuOnly, outputRoot: input.outputRoot,
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
