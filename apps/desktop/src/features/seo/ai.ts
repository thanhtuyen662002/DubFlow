import Anthropic from "@anthropic-ai/sdk";

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
  if (!result.titles.length || !result.description) throw new Error("Claude không trả về đủ tiêu đề và mô tả");
  return result;
}

/**
 * Ask Claude (with the user's own API key, from this desktop app) for posting
 * suggestions. The subtitles are sent to Anthropic; the caller makes that clear.
 */
export async function writeWithClaude(apiKey: string, subtitles: readonly string[], sourceTitle: string): Promise<PostSuggestion> {
  // The key belongs to the user of this desktop app and stays on this machine.
  const client = new Anthropic({ apiKey, dangerouslyAllowBrowser: true });
  const response = await client.beta.messages.create({
    model: "claude-opus-5-5",
    max_tokens: 16000,
    betas: ["server-side-fallback-2026-07-01"],
    fallbacks: "default",
    // A short writing task: low effort keeps it quick.
    output_config: { effort: "low", format: { type: "json_schema", schema: SCHEMA } },
    system: SYSTEM,
    messages: [{
      role: "user",
      content: `Tiêu đề gốc: ${sourceTitle || "(không có)"}\n\nPhụ đề tiếng Việt:\n${subtitles.join("\n")}`,
    }],
  });
  if (response.stop_reason === "refusal") throw new Error("Claude từ chối yêu cầu này; hãy dùng gợi ý offline.");
  if (response.stop_reason === "max_tokens") throw new Error("Câu trả lời bị cắt ngắn; hãy thử lại.");
  const text = response.content.map((block) => (block.type === "text" ? block.text : "")).join("");
  return normalizeSuggestion(JSON.parse(text));
}
