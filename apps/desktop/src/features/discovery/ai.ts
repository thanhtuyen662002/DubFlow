import { askJson, type Provider } from "../ai/provider.ts";

const SCHEMA = {
  type: "object",
  properties: { zh: { type: "string" } },
  required: ["zh"],
  additionalProperties: false,
};

const SYSTEM = `Bạn chuyển cụm từ tìm kiếm tiếng Việt thành từ khóa tìm kiếm tiếng Trung giản thể mà người dùng Douyin, Bilibili, Kuaishou hay gõ.
Trả về zh: một từ khóa ngắn (tối đa 20 chữ Hán), giữ nguyên tên riêng và chữ Latin như tên thương hiệu. Không giải thích.`;

/** A Chinese search keyword for a Vietnamese phrase, from the chosen provider with the user's own key. Only the phrase is sent. */
export async function chineseKeyword(provider: Provider, apiKey: string, text: string): Promise<string> {
  const answer = (await askJson(provider, apiKey, { system: SYSTEM, schema: SCHEMA, effort: "low", parts: [{ text }] })) as { zh?: unknown } | null;
  return typeof answer?.zh === "string" ? answer.zh.trim().slice(0, 100) : "";
}
