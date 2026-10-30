import { askJson, type Provider } from "../ai/provider.ts";

/** Titles, description and hashtags for re-posting a localized video. */
export type PostSuggestion = { titles: string[]; description: string; hashtags: string[] };

const SCHEMA = {
  type: "object",
  properties: {
    titles: { type: "array", items: { type: "string" } },
    description: { type: "string" },
    hashtags: { type: "array", items: { type: "string" } },
  },
  required: ["titles", "description", "hashtags"],
  additionalProperties: false,
};

const SYSTEM = `Bạn là biên tập viên mạng xã hội cho kênh Việt hóa video ngắn Trung Quốc (TikTok, YouTube Shorts, Facebook Reels).
Từ phụ đề tiếng Việt (dịch máy, có thể chưa mượt) và tiêu đề gốc, hãy viết:
- titles: 5 tiêu đề tiếng Việt tự nhiên, gây tò mò nhưng đúng nội dung, tối đa 90 ký tự mỗi tiêu đề, không giật tít sai sự thật.
- description: mô tả 2-3 câu tiếng Việt, tối đa 300 ký tự, nêu nội dung chính và ghi rõ đây là video Vietsub từ nguồn Trung Quốc.
- hashtags: 10-15 hashtag phổ biến ở Việt Nam, viết liền không dấu chữ thường (ví dụ #phimtienhiep), có #vietsub.
Chỉ dùng thông tin có trong phụ đề; không bịa tên diễn viên hay tên phim nếu phụ đề không nhắc tới.`;

/** Clean what the model returned: trimmed, bounded, de-duplicated, hashtags normalised. */
export function normalizeSuggestion(value: unknown): PostSuggestion {
  const object = (value ?? {}) as Record<string, unknown>;
  const strings = (items: unknown, limit: number, length: number): string[] =>
    Array.isArray(items) ? [...new Set(items.filter((item): item is string => typeof item === "string").map((item) => item.trim()).filter(Boolean))].slice(0, limit).map((item) => item.slice(0, length)) : [];
  const hashtags = strings(object.hashtags, 20, 40)
    .map((tag) => "#" + tag.replace(/^#+/, "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D").replace(/[^A-Za-z0-9_]/g, "").toLowerCase())
    .filter((tag) => tag.length > 1);
  const result = { titles: strings(object.titles, 8, 120), description: typeof object.description === "string" ? object.description.trim().slice(0, 600) : "", hashtags: [...new Set(hashtags)] };
  if (!result.titles.length || !result.description) throw new Error("AI không trả về đủ tiêu đề và mô tả");
  return result;
}

/**
 * Ask the chosen provider (with the user's own API key, from this desktop app)
 * for posting suggestions. The subtitles are sent to that provider; the caller
 * makes that clear.
 */
export async function writeSuggestion(provider: Provider, apiKey: string, subtitles: readonly string[], sourceTitle: string): Promise<PostSuggestion> {
  return normalizeSuggestion(await askJson(provider, apiKey, {
    system: SYSTEM,
    schema: SCHEMA,
    // A short writing task: low effort keeps it quick.
    effort: "low",
    parts: [{ text: `Tiêu đề gốc: ${sourceTitle || "(không có)"}\n\nPhụ đề tiếng Việt:\n${subtitles.join("\n")}` }],
  }));
}
