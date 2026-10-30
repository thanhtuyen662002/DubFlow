import Anthropic from "@anthropic-ai/sdk";

/** What Claude read from the product photos; every field stays editable. */
export type ProductInfo = { name: string; price: string; features: string[]; audience: string; notes: string };
export type ScriptRequest = { count: number; seconds: number; tone: string };
export type Photo = { mediaType: "image/jpeg"; data: string };

/** Vietnamese narration runs at about 3.2 syllables (words) per second. */
export const WORDS_PER_SECOND = 3.2;
export const MAX_SCRIPT_CHARS = 3000;

const INFO_SCHEMA = {
  type: "object",
  properties: {
    name: { type: "string" },
    price: { type: "string" },
    features: { type: "array", items: { type: "string" } },
    audience: { type: "string" },
    notes: { type: "string" },
  },
  required: ["name", "price", "features", "audience", "notes"],
  additionalProperties: false,
};

const SCRIPTS_SCHEMA = {
  type: "object",
  properties: { scripts: { type: "array", items: { type: "string" } } },
  required: ["scripts"],
  additionalProperties: false,
};

const READ_SYSTEM = `Bạn đọc ảnh sản phẩm (bao bì, nhãn, tờ thông số, ảnh chụp trang sàn thương mại điện tử) cho người bán hàng Việt Nam.
Trả về bằng tiếng Việt (dịch nếu ảnh là tiếng Trung hoặc tiếng Anh):
- name: tên sản phẩm ngắn gọn, có thương hiệu và dung tích/kích thước nếu thấy.
- price: giá đúng như in trên ảnh (ví dụ 299.000đ); không thấy thì để rỗng.
- features: tối đa 8 điểm nổi bật ngắn, mỗi điểm một câu.
- audience: sản phẩm dành cho ai, nếu ảnh cho biết; không thì để rỗng.
- notes: thông tin khác có ích khi bán (bảo hành, xuất xứ, quà tặng…) nếu thấy.
Chỉ ghi điều nhìn thấy trong ảnh. Không đoán giá, công dụng hay chứng nhận.`;

const WRITE_SYSTEM = `Bạn viết lời đọc cho video bán hàng ngắn dạng dọc (TikTok, Facebook Reels, YouTube Shorts) bằng tiếng Việt.
Mỗi kịch bản:
- mở đầu bằng một câu thu hút trong 3 giây đầu;
- nêu 2-4 lợi ích lấy từ thông tin sản phẩm được cung cấp;
- kết thúc bằng lời mời mua hàng ngắn gọn.
Quy tắc:
- Chỉ dùng thông tin được cung cấp. Không bịa giá, khuyến mãi, quà tặng, chứng nhận, số liệu hay công dụng chữa bệnh.
- Viết số, đơn vị và giá bằng chữ để giọng đọc tự nhiên (ví dụ "hai trăm chín mươi chín nghìn đồng", "năm phẩy hai lít").
- Chỉ viết lời đọc: không emoji, hashtag, ký hiệu, tiêu đề, ghi chú dựng cảnh hay tên người nói.
- Câu ngắn, dễ nghe, mỗi câu kết thúc bằng dấu chấm, chấm than hoặc chấm hỏi.
- Các kịch bản phải khác nhau rõ về câu mở đầu và góc tiếp cận.`;

const PICTOGRAPHS = /[\p{Extended_Pictographic}\u{FE0F}\u{200D}]/gu;

function clean(value: unknown, limit: number): string {
  return typeof value === "string" ? value.replace(/\s+/g, " ").trim().slice(0, limit) : "";
}

/** Clean what the model read: trimmed, bounded and de-duplicated. */
export function normalizeInfo(value: unknown): ProductInfo {
  const object = (value ?? {}) as Record<string, unknown>;
  const features = Array.isArray(object.features)
    ? [...new Set(object.features.map((item) => clean(item, 200)).filter(Boolean))].slice(0, 8)
    : [];
  const info = { name: clean(object.name, 120), price: clean(object.price, 60), features, audience: clean(object.audience, 200), notes: clean(object.notes, 500) };
  if (!info.name && !features.length) throw new Error("Claude không đọc được thông tin sản phẩm từ ảnh; hãy chọn ảnh rõ hơn hoặc tự nhập.");
  return info;
}

/** Narration only: no emoji, hashtags or markup, bounded, at most `count`. */
export function normalizeScripts(value: unknown, count: number): string[] {
  const object = (value ?? {}) as Record<string, unknown>;
  const scripts = (Array.isArray(object.scripts) ? object.scripts : [])
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.replace(PICTOGRAPHS, "").replace(/#\S+/g, "").replace(/[*_`>]/g, "").replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim().slice(0, MAX_SCRIPT_CHARS))
    .filter((item) => /[\p{L}\p{N}]/u.test(item));
  if (!scripts.length) throw new Error("Claude không trả về kịch bản nào; hãy thử lại.");
  return scripts.slice(0, count);
}

/** The product form as the prompt's facts. */
export function describeProduct(info: ProductInfo): string {
  return [
    `Tên sản phẩm: ${info.name || "(chưa có)"}`,
    info.price && `Giá: ${info.price}`,
    info.features.length && `Điểm nổi bật:\n${info.features.map((item) => `- ${item}`).join("\n")}`,
    info.audience && `Dành cho: ${info.audience}`,
    info.notes && `Thông tin thêm: ${info.notes}`,
  ].filter(Boolean).join("\n");
}

function client(apiKey: string): Anthropic {
  // The key belongs to the user of this desktop app and stays on this machine.
  return new Anthropic({ apiKey, dangerouslyAllowBrowser: true });
}

async function structured(apiKey: string, system: string, schema: Record<string, unknown>, content: Anthropic.Beta.BetaContentBlockParam[]): Promise<unknown> {
  const response = await client(apiKey).beta.messages.create({
    model: "claude-opus-5-5",
    max_tokens: 16000,
    betas: ["server-side-fallback-2026-07-01"],
    fallbacks: "default",
    output_config: { effort: "medium", format: { type: "json_schema", schema } },
    system,
    messages: [{ role: "user", content }],
  });
  if (response.stop_reason === "refusal") throw new Error("Claude từ chối yêu cầu này.");
  if (response.stop_reason === "max_tokens") throw new Error("Câu trả lời bị cắt ngắn; hãy thử lại.");
  return JSON.parse(response.content.map((block) => (block.type === "text" ? block.text : "")).join(""));
}

/** Read the product facts printed on the photos. The photos are sent to Anthropic. */
export async function readProduct(apiKey: string, photos: readonly Photo[]): Promise<ProductInfo> {
  const content: Anthropic.Beta.BetaContentBlockParam[] = photos.map((photo) => ({
    type: "image", source: { type: "base64", media_type: photo.mediaType, data: photo.data },
  }));
  content.push({ type: "text", text: "Đọc thông tin sản phẩm trong các ảnh trên." });
  return normalizeInfo(await structured(apiKey, READ_SYSTEM, INFO_SCHEMA, content));
}

/** Write `count` different narration scripts from the (user-checked) product form. */
export async function writeScripts(apiKey: string, info: ProductInfo, request: ScriptRequest): Promise<string[]> {
  const words = Math.round(request.seconds * WORDS_PER_SECOND);
  const text = `${describeProduct(info)}\n\nHãy viết ${request.count} kịch bản khác nhau, mỗi kịch bản khoảng ${words} chữ (đọc trong khoảng ${request.seconds} giây). Giọng điệu: ${request.tone}.`;
  return normalizeScripts(await structured(apiKey, WRITE_SYSTEM, SCRIPTS_SCHEMA, [{ type: "text", text }]), request.count);
}
