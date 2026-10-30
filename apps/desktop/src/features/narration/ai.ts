import { askJson, type Provider } from "../ai/provider.ts";
import { NATURAL_SYLLABLES_PER_SECOND } from "../translation_review/flow.ts";

/**
 * "Thuyết minh / Bình luận": instead of a line-by-line translation, the AI
 * retells each cue in the creator's own narrating voice, sized to fit the same
 * time so the existing dubbing and review path re-renders it.
 */
export type NarrationStyle = "ke-chuyen" | "binh-luan" | "hai-huoc";

export const NARRATION_STYLES: Record<NarrationStyle, { label: string; brief: string }> = {
  "ke-chuyen": { label: "Kể chuyện", brief: "Người kể chuyện: kể lại diễn biến ở ngôi thứ ba, giọng cuốn hút, giữ mạch truyện." },
  "binh-luan": { label: "Bình luận, giải thích", brief: "Người bình luận: vừa kể vừa giải thích vì sao, nêu nhận xét ngắn và góc nhìn riêng như người review." },
  "hai-huoc": { label: "Hài hước", brief: "Người kể dí dỏm: kể lại nhịp nhanh, chơi chữ nhẹ nhàng, không xúc phạm ai." },
};

export type NarrationCue = { cue_id: string; start_ms: number; end_ms: number; source_text: string; text: string; speaker?: string | null };

/** Cues per request: long videos are rewritten in parts. */
export const BATCH_CUES = 80;

const SCHEMA = {
  type: "object",
  properties: {
    lines: {
      type: "array",
      items: { type: "object", properties: { id: { type: "string" }, text: { type: "string" } }, required: ["id", "text"], additionalProperties: false },
    },
  },
  required: ["lines"],
  additionalProperties: false,
};

const SYSTEM = `Bạn là biên tập viên lời thuyết minh tiếng Việt cho kênh video (YouTube, TikTok).
Bạn nhận danh sách câu của một video: lời gốc, bản dịch máy, thời lượng và số tiếng tối đa.
Hãy viết lại MỖI câu thành lời thuyết minh của người dẫn chuyện (không còn là hội thoại nhân vật), theo phong cách được yêu cầu.
Quy tắc:
- Giữ đúng id; trả đủ mọi câu theo thứ tự.
- Mỗi câu KHÔNG vượt quá số tiếng tối đa đã cho (một tiếng = một âm tiết tiếng Việt), vì giọng đọc phải khớp thời lượng.
- Các câu nối nhau thành một mạch kể liền lạc; câu đầu tiên của video là câu mở đầu gây tò mò.
- Chỉ dựa vào nội dung video; không bịa sự kiện, số liệu, tên riêng.
- Không emoji, hashtag, ký hiệu, chú thích dựng cảnh; chỉ lời đọc.`;

/** At most this many Vietnamese syllables (space-separated words) fit the cue at natural pace. */
export function syllableBudget(cue: Pick<NarrationCue, "start_ms" | "end_ms">): number {
  return Math.max(2, Math.floor(((cue.end_ms - cue.start_ms) / 1000) * NATURAL_SYLLABLES_PER_SECOND));
}

function words(text: string): string[] {
  return text.split(/\s+/).filter(Boolean);
}

/** The model's lines for known cues: trimmed, no emoji or hashtags, cut to the cue's budget. */
export function normalizeLines(value: unknown, cues: readonly NarrationCue[]): Map<string, string> {
  const budget = new Map(cues.map((cue) => [cue.cue_id, syllableBudget(cue)]));
  const lines = new Map<string, string>();
  const items = Array.isArray((value as { lines?: unknown })?.lines) ? (value as { lines: unknown[] }).lines : [];
  for (const item of items) {
    const { id, text } = (item ?? {}) as { id?: unknown; text?: unknown };
    if (typeof id !== "string" || typeof text !== "string" || !budget.has(id) || lines.has(id)) continue;
    const clean = text.replace(/[\p{Extended_Pictographic}\u{FE0F}\u{200D}]/gu, "").replace(/#\S+/g, "").replace(/[*_`>]/g, "").replace(/\s+/g, " ").trim();
    if (!/[\p{L}\p{N}]/u.test(clean)) continue;
    const kept = words(clean).slice(0, budget.get(id)!).join(" ");
    lines.set(id, kept);
  }
  return lines;
}

function prompt(cues: readonly NarrationCue[], style: NarrationStyle, part: number, parts: number, title: string): string {
  const rows = cues.map((cue) => JSON.stringify({
    id: cue.cue_id, giay: Number(((cue.end_ms - cue.start_ms) / 1000).toFixed(1)), toi_da_tieng: syllableBudget(cue),
    goc: cue.source_text.slice(0, 300), dich: cue.text.slice(0, 300), ...(cue.speaker ? { nguoi_noi: cue.speaker } : {}),
  }));
  return [
    `Phong cách: ${NARRATION_STYLES[style].brief}`,
    title ? `Tên video: ${title}` : "",
    parts > 1 ? `Đây là phần ${part}/${parts} của video${part > 1 ? "; tiếp nối mạch kể của phần trước, không mở đầu lại" : ""}.` : "",
    "Các câu (mỗi dòng một JSON):",
    ...rows,
  ].filter(Boolean).join("\n");
}

/**
 * Narration lines for every cue, by cue id. The cues' source and translated
 * text are sent to the provider. Cues the model skipped are left out, so the
 * caller keeps their current text.
 */
export async function rewriteAsNarration(provider: Provider, apiKey: string, cues: readonly NarrationCue[], style: NarrationStyle,
                                         options: { title?: string; onProgress?: (done: number, total: number) => void; ask?: typeof askJson } = {}): Promise<Map<string, string>> {
  const ask = options.ask ?? askJson;
  const ordered = [...cues].sort((a, b) => a.start_ms - b.start_ms);
  const parts = Math.max(1, Math.ceil(ordered.length / BATCH_CUES));
  const result = new Map<string, string>();
  for (let part = 0; part < parts; part += 1) {
    const batch = ordered.slice(part * BATCH_CUES, (part + 1) * BATCH_CUES);
    options.onProgress?.(part, parts);
    const value = await ask(provider, apiKey, { system: SYSTEM, schema: SCHEMA, effort: "medium",
      parts: [{ text: prompt(batch, style, part + 1, parts, options.title ?? "") }] });
    for (const [id, text] of normalizeLines(value, batch)) result.set(id, text);
  }
  options.onProgress?.(parts, parts);
  if (!result.size) throw new Error("AI không trả về lời thuyết minh nào; hãy thử lại.");
  return result;
}
