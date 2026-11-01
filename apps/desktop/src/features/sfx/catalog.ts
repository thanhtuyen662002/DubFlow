/**
 * The DubFlow sound-effect catalog (Issue #420): CC0 sounds the app lists
 * before the pack is downloaded; the AI may only pick from these ids.
 */
import { SFX_ID } from "../edit/model.ts";

export const CATEGORIES = ["Chuyển cảnh", "Bấm/Pop", "Thông báo", "Va chạm", "Hài hước", "Khác"] as const;
export type Category = (typeof CATEGORIES)[number];
export type Sound = {
  id: string; file: string; name_vi: string; tags: string[]; category: Category; duration_ms: number; source: string; license: string;
};
export type Catalog = { pack: string; sounds: Sound[] };

/** The catalog document (schema 1), keeping only well-formed sounds with unique ids. */
export function parseCatalog(value: unknown): Catalog {
  const data = (value ?? {}) as Record<string, unknown>;
  const sounds: Sound[] = [];
  const seen = new Set<string>();
  if (data.schema_version !== 1 || !Array.isArray(data.sounds)) return { pack: "", sounds };
  for (const raw of data.sounds as unknown[]) {
    const { id, file, name_vi, tags, category, duration_ms, source, license } = (raw ?? {}) as Record<string, unknown>;
    if (typeof id !== "string" || !SFX_ID.test(id) || seen.has(id) || file !== `${id}.ogg` || typeof name_vi !== "string" || !name_vi.trim()) continue;
    if (!CATEGORIES.includes(category as Category) || typeof duration_ms !== "number" || !Number.isInteger(duration_ms) || duration_ms <= 0) continue;
    seen.add(id);
    sounds.push({
      id, file, name_vi: name_vi.slice(0, 120), category: category as Category, duration_ms,
      tags: Array.isArray(tags) ? tags.filter((tag): tag is string => typeof tag === "string").slice(0, 12) : [],
      source: typeof source === "string" ? source : "", license: typeof license === "string" ? license : "",
    });
  }
  return { pack: typeof data.pack === "string" ? data.pack : "", sounds };
}

/** Lower case without Vietnamese marks, for search ("vut" finds "Vút"). */
export function fold(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D").toLocaleLowerCase("vi");
}

/** Sounds of a category (null: all) whose name or tags contain every word of the query, marks optional. */
export function searchSounds(sounds: readonly Sound[], query: string, category: Category | null = null): Sound[] {
  const words = fold(query).split(/\s+/).filter(Boolean);
  return sounds.filter((sound) => {
    if (category && sound.category !== category) return false;
    if (!words.length) return true;
    const haystack = fold([sound.name_vi, ...sound.tags, sound.category, sound.id].join(" "));
    return words.every((word) => haystack.includes(word));
  });
}

/** "0,6 giây" */
export function soundLength(ms: number): string {
  const seconds = Math.max(0.1, Math.round(ms / 100) / 10);
  return `${String(seconds).replace(".", ",")} giây`;
}
