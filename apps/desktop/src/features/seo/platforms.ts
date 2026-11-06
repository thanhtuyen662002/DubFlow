import { askJson, type Provider } from "../ai/provider.ts";
import type { PostSuggestion } from "./ai.ts";

/** Where a finished video gets posted. */
export type Platform = "youtube" | "facebook" | "tiktok";
export const PLATFORM_ORDER: readonly Platform[] = ["youtube", "facebook", "tiktok"];

/** Ready-to-paste content for one platform. `tags` is YouTube's keyword box. */
export type PlatformPost = { titles: string[]; description: string; hashtags: string[]; tags: string[] };
export type PlatformPosts = Record<Platform, PlatformPost>;

/** Each platform's limits and how its post is laid out. */
export const PLATFORM_RULES: Record<Platform, {
  name: string; titleLabel: string; titleMax: number; descriptionMax: number; hashtagMin: number; hashtagMax: number; tagsMax: number; note: string;
}> = {
  youtube: {
    name: "YouTube", titleLabel: "Tiêu đề", titleMax: 100, descriptionMax: 5000, hashtagMin: 3, hashtagMax: 5, tagsMax: 500,
    note: "Tiêu đề tối đa 100 ký tự (nên dưới 70). Hai dòng đầu mô tả hiện ngay dưới video nên chứa từ khóa. 3 hashtag đầu hiện trên tiêu đề.",
  },
  facebook: {
    name: "Facebook", titleLabel: "Câu mở đầu", titleMax: 120, descriptionMax: 2000, hashtagMin: 3, hashtagMax: 5, tagsMax: 0,
    note: "Câu đầu tiên là thứ người xem thấy trước khi bấm “Xem thêm”. Kết bằng một câu hỏi để kéo bình luận.",
  },
  tiktok: {
    name: "TikTok", titleLabel: "Câu mở đầu", titleMax: 100, descriptionMax: 2200, hashtagMin: 4, hashtagMax: 8, tagsMax: 0,
    note: "Chú thích ngắn, câu đầu thật cuốn. Tiêu đề và hashtag cộng lại nên dưới 150 ký tự để không bị cắt.",
  },
};

const MAX_TITLES = 5;
const HASHTAG = /^#[a-z0-9_]{2,40}$/;

function clip(text: string, limit: number): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if ([...clean].length <= limit) return clean;
  const cut = [...clean].slice(0, limit - 1).join("");
  const space = cut.lastIndexOf(" ");
  return (space > limit * 0.6 ? cut.slice(0, space) : cut).replace(/[\s,;:|.-]+$/, "") + "…";
}

/** A hashtag the platforms accept: no accents, no spaces, lowercase. */
export function toHashtag(text: string): string {
  const body = text.replace(/^#+/, "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D").replace(/[^A-Za-z0-9_]/g, "").toLowerCase();
  const tag = "#" + body;
  return HASHTAG.test(tag) ? tag : "";
}

/** The hashtags have their own box and are added on copy: drop hashtag-only lines from a description. */
export function withoutHashtagLines(text: string): string {
  return text.replace(/\r\n?/g, "\n").split("\n").filter((line) => !/^\s*(#\S+\s*)+$/.test(line)).join("\n")
    .replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

function unique<T>(items: Iterable<T>): T[] {
  return [...new Set(items)];
}

function strings(items: unknown): string[] {
  return Array.isArray(items) ? items.filter((item): item is string => typeof item === "string").map((item) => item.replace(/\s+/g, " ").trim()).filter(Boolean) : [];
}

/** YouTube's tag box takes at most 500 characters, commas included. */
export function fitTags(tags: readonly string[], limit = 500): string[] {
  const result: string[] = [];
  let used = 0;
  for (const tag of unique(tags.map((item) => item.replace(/[#,<>]/g, " ").replace(/\s+/g, " ").trim().toLocaleLowerCase("vi")).filter((item) => item.length >= 2))) {
    const cost = tag.length + (result.length ? 1 : 0);
    if (used + cost > limit) continue;
    result.push(tag); used += cost;
  }
  return result;
}

function hashtags(items: readonly string[], min: number, max: number, extras: readonly string[]): string[] {
  const tags = unique(items.map(toHashtag).filter(Boolean));
  for (const extra of extras) if (tags.length < max && !tags.includes(extra)) tags.push(extra);
  return tags.slice(0, Math.max(min, max));
}

/** Clean one platform's AI output to the platform's limits. Throws when titles or description are missing. */
export function normalizePlatformPost(platform: Platform, value: unknown): PlatformPost {
  const rules = PLATFORM_RULES[platform];
  const object = (value ?? {}) as Record<string, unknown>;
  const titles = unique(strings(object.titles).map((title) => clip(title.replace(/#[^\s#]+/g, " "), rules.titleMax)).filter(Boolean)).slice(0, MAX_TITLES);
  const description = typeof object.description === "string" ? withoutHashtagLines(object.description).slice(0, rules.descriptionMax) : "";
  if (!titles.length || !description) throw new Error(`AI không trả về đủ tiêu đề và mô tả cho ${rules.name}`);
  return {
    titles,
    description,
    hashtags: hashtags(strings(object.hashtags), 0, rules.hashtagMax, []),
    tags: rules.tagsMax ? fitTags(strings(object.tags), rules.tagsMax) : [],
  };
}

export function normalizePlatformPosts(value: unknown): PlatformPosts {
  const object = (value ?? {}) as Record<string, unknown>;
  return {
    youtube: normalizePlatformPost("youtube", object.youtube),
    facebook: normalizePlatformPost("facebook", object.facebook),
    tiktok: normalizePlatformPost("tiktok", object.tiktok),
  };
}

function firstSentence(text: string): string {
  const flat = text.replace(/#\S+/g, " ").replace(/\s+/g, " ").trim();
  const match = flat.match(/^.+?[.!?…](?=\s|$)/);
  return (match ? match[0] : flat).trim();
}

/** Offline: shape the subtitle-based suggestion for each platform. */
export function platformPostsFromBase(base: PostSuggestion, keywords: readonly string[] = []): PlatformPosts {
  const body = withoutHashtagLines(base.description).replace(/(^|\s)#\S+/g, " ").replace(/[ \t]+/g, " ").replace(/ *\n */g, "\n").trim();
  const lead = firstSentence(body) || base.titles[0];
  const youtubeTags = hashtags(base.hashtags, 3, 5, ["#vietsub", "#shorts"]);
  const youtube: PlatformPost = {
    titles: unique(base.titles.map((title) => clip(title, 70))).slice(0, MAX_TITLES),
    description: [
      body,
      "Video đã được Việt hóa (lồng tiếng và phụ đề tiếng Việt). Nhớ bấm Đăng ký và bật chuông để không bỏ lỡ video mới!",
    ].filter(Boolean).join("\n\n"),
    hashtags: youtubeTags,
    tags: fitTags([...keywords, ...base.titles.slice(0, 2), ...base.hashtags.map((tag) => tag.replace(/^#/, "")), "vietsub", "lồng tiếng việt"]),
  };
  const facebookTags = hashtags(base.hashtags, 3, 5, ["#vietsub", "#reels"]);
  const facebook: PlatformPost = {
    titles: unique(base.titles.map((title) => clip(title, 120))).slice(0, MAX_TITLES),
    description: [body, "Bạn nghĩ sao về đoạn này? Bình luận cho mình biết nhé 👇"].filter(Boolean).join("\n\n"),
    hashtags: facebookTags,
    tags: [],
  };
  const tiktokTags = hashtags(base.hashtags, 4, 6, ["#vietsub", "#xuhuong", "#fyp"]);
  const tiktok: PlatformPost = {
    titles: unique(base.titles.map((title) => clip(title, 80))).slice(0, MAX_TITLES),
    description: clip(lead, 100),
    hashtags: tiktokTags,
    tags: [],
  };
  return { youtube, facebook, tiktok };
}

/** What "Sao chép bài đăng" puts on the clipboard for one platform: title, description, hashtags. */
export function postText(platform: Platform, post: PlatformPost, title: string): string {
  const tags = post.hashtags.join(" ");
  if (platform === "tiktok") return [[title, post.description.trim()].filter(Boolean).join("\n"), tags].filter(Boolean).join("\n\n");
  return [title, post.description.trim(), tags].filter(Boolean).join("\n\n");
}

const POST_SCHEMA = {
  type: "object",
  properties: {
    titles: { type: "array", items: { type: "string" } },
    description: { type: "string" },
    hashtags: { type: "array", items: { type: "string" } },
    tags: { type: "array", items: { type: "string" } },
  },
  required: ["titles", "description", "hashtags", "tags"],
  additionalProperties: false,
};

const SCHEMA = {
  type: "object",
  properties: { youtube: POST_SCHEMA, facebook: POST_SCHEMA, tiktok: POST_SCHEMA },
  required: ["youtube", "facebook", "tiktok"],
  additionalProperties: false,
};

const SYSTEM = `Bạn là chuyên gia nội dung và SEO mạng xã hội cho kênh Việt hóa video Trung Quốc ở Việt Nam.
Từ phụ đề tiếng Việt (dịch máy, có thể chưa mượt) và tiêu đề gốc, viết nội dung đăng sẵn cho từng nền tảng, tiếng Việt có dấu, tự nhiên.

youtube:
- titles: 5 tiêu đề hot, gây tò mò, có từ khóa chính ở đầu, 50-70 ký tự (tối đa 100).
- description: chuẩn SEO, 3 đoạn, không chèn hashtag (hashtag để riêng ở mục hashtags). Hai câu đầu chứa từ khóa chính và tóm nội dung hấp dẫn. Đoạn giữa kể chi tiết hơn và lặp lại tự nhiên 2-3 từ khóa phụ. Đoạn cuối kêu gọi đăng ký, bật chuông, bình luận. Ghi rõ là video Việt hóa từ nguồn Trung Quốc. Tối đa 1200 ký tự.
- hashtags: 3-5 hashtag liên quan nhất (3 cái đầu hiện trên tiêu đề), có #vietsub; thêm #shorts nếu là video ngắn.
- tags: 10-15 từ khóa tìm kiếm tiếng Việt có dấu (cụm 1-4 từ người Việt hay gõ trên YouTube), không có dấu #.

facebook:
- titles: 5 câu mở đầu cuốn hút (câu đầu bài viết, hiện trước "Xem thêm"), có thể có 1 emoji, tối đa 120 ký tự.
- description: 2-4 câu kể ngắn nội dung, giọng gần gũi, kết bằng một câu hỏi kéo bình luận. Tối đa 500 ký tự.
- hashtags: 3-5 hashtag, có #vietsub.
- tags: để trống [].

tiktok:
- titles: 5 câu mở đầu (hook) cực ngắn, gây tò mò, tối đa 80 ký tự.
- description: một câu chú thích ngắn bổ sung cho hook, tối đa 100 ký tự.
- hashtags: 4-8 hashtag đang phổ biến ở TikTok Việt Nam và đúng chủ đề (ví dụ #xuhuong #fyp #vietsub cùng hashtag chủ đề).
- tags: để trống [].

Quy tắc chung: hashtag viết liền, không dấu, chữ thường. Không đặt hashtag trong titles. Không giật tít sai sự thật; chỉ dùng thông tin có trong phụ đề, không bịa tên diễn viên, tên phim hay con số.`;

/** Ask the chosen provider (with the user's own key) for all three platforms in one go. */
export async function writePlatformPosts(provider: Provider, apiKey: string, subtitles: readonly string[], sourceTitle: string, send?: typeof fetch): Promise<PlatformPosts> {
  // Long videos: the opening and a spread of lines are enough for a summary.
  const lines = subtitles.length > 400 ? subtitles.filter((_, index) => index < 200 || index % Math.ceil(subtitles.length / 200) === 0) : subtitles;
  return normalizePlatformPosts(await askJson(provider, apiKey, {
    system: SYSTEM,
    schema: SCHEMA,
    effort: "low",
    language: "vi",
    parts: [{ text: `Tiêu đề gốc: ${sourceTitle || "(không có)"}\n\nPhụ đề tiếng Việt:\n${lines.join("\n")}` }],
  }, send));
}
