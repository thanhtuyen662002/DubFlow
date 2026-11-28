/** Discovery data as returned by the host (`discover`, `collect_discovery`). */
export type Platform = "all" | "bilibili" | "youtube" | "skypixel" | "douyin" | "kuaishou";
/** A real source; "all" searches several of them at once. */
export type Source = Exclude<Platform, "all">;

/**
 * The tabs of Khám phá. `trends`: the source has hot keywords; `page`: it can be browsed in the app's
 * browser (keyword and channel pages); `apiSearch`: its keyword search runs without the page.
 */
export const PLATFORMS: ReadonlyArray<{ id: Platform; name: string; suggest: boolean; apiSearch: boolean; trends: boolean; page: boolean }> = [
  { id: "all", name: "Tất cả nguồn", suggest: false, apiSearch: true, trends: false, page: false },
  { id: "bilibili", name: "Bilibili", suggest: true, apiSearch: true, trends: true, page: true },
  { id: "youtube", name: "YouTube", suggest: false, apiSearch: true, trends: false, page: false },
  { id: "skypixel", name: "SkyPixel", suggest: false, apiSearch: true, trends: false, page: false },
  { id: "douyin", name: "Douyin", suggest: true, apiSearch: false, trends: true, page: true },
  { id: "kuaishou", name: "Kuaishou", suggest: false, apiSearch: false, trends: true, page: true },
];

/** What "Tất cả nguồn" searches at once, in the order the grid mixes them (#978). */
export const ALL_SOURCES: readonly Source[] = ["bilibili", "youtube", "skypixel"];
/** Sources only their own page can search; "Tất cả nguồn" offers them as "Tìm thêm trên trang". */
export const PAGE_ONLY_SOURCES: readonly Source[] = ["douyin", "kuaishou"];

export const SOURCE_NAMES: Record<Source, string> = {
  bilibili: "Bilibili", youtube: "YouTube", skypixel: "SkyPixel", douyin: "Douyin", kuaishou: "Kuaishou",
};

/** YouTube's own search filters; "cc" is Creative Commons, footage its owner allows to be reused. */
export const YOUTUBE_FILTERS: ReadonlyArray<{ id: "any" | "hd" | "4k" | "cc"; name: string }> = [
  { id: "4k", name: "4K" }, { id: "hd", name: "HD" }, { id: "any", name: "Mọi chất lượng" }, { id: "cc", name: "CC (được dùng lại)" },
];

/**
 * Words that find clean footage (B-roll without people talking, captions or logos), appended to a search
 * with one click. `zh` goes to the Chinese sources, `en` to YouTube.
 */
export type Suffix = { zh: string; en: string; vi: string };
export const SUFFIXES: readonly Suffix[] = [
  { zh: "空镜", en: "B-roll", vi: "Cảnh trống: chỉ cảnh vật, máy móc, không người nói, không chữ" },
  { zh: "航拍 4K", en: "4K drone", vi: "Flycam 4K" },
  { zh: "无字幕", en: "no text", vi: "Không phụ đề" },
  { zh: "素材", en: "stock footage", vi: "Tư liệu để dựng phim" },
  { zh: "4K 超清", en: "4K", vi: "Siêu nét 4K" },
  { zh: "延时摄影", en: "timelapse", vi: "Quay tua nhanh (timelapse)" },
  { zh: "纪录片", en: "documentary", vi: "Phim tài liệu" },
  { zh: "企业宣传片", en: "corporate film", vi: "Phim giới thiệu doanh nghiệp, nhà máy" },
  { zh: "工业", en: "industrial", vi: "Công nghiệp" },
  { zh: "工厂", en: "factory", vi: "Nhà máy" },
];

/** Every form of every suffix, longest first, so "航拍 4K" wins over "4K". */
const SUFFIX_FORMS: ReadonlyArray<{ form: string; suffix: Suffix }> = SUFFIXES
  .flatMap((suffix) => [{ form: suffix.zh, suffix }, { form: suffix.en, suffix }])
  .sort((a, b) => b.form.length - a.form.length);

/** The search phrase and the suffixes at its end (in the order they appear). */
export function splitSuffixes(text: string): { base: string; suffixes: Suffix[] } {
  let rest = text.replace(/\s+/g, " ").trim();
  const suffixes: Suffix[] = [];
  for (let guard = 0; guard < 20 && rest; guard++) {
    const lower = rest.toLowerCase();
    const hit = SUFFIX_FORMS.find(({ form }) => {
      const key = form.toLowerCase();
      if (!lower.endsWith(key)) return false;
      const before = lower.length - key.length;
      return before === 0 || lower[before - 1] === " ";
    });
    if (!hit) break;
    rest = rest.slice(0, rest.length - hit.form.length).trim();
    if (!suffixes.includes(hit.suffix)) suffixes.unshift(hit.suffix);
  }
  return { base: rest, suffixes };
}

/** A phrase with its suffixes in one language. */
export function joinQuery(base: string, suffixes: readonly Suffix[], language: "zh" | "en"): string {
  return [base, ...suffixes.map((suffix) => suffix[language])].filter(Boolean).join(" ").slice(0, 100);
}

/** Put a suffix at the end of the search, or take it away when it is there. */
export function toggleSuffix(text: string, suffix: Suffix, language: "zh" | "en"): string {
  const { base, suffixes } = splitSuffixes(text);
  const next = suffixes.includes(suffix) ? suffixes.filter((item) => item !== suffix) : [...suffixes, suffix];
  return joinQuery(base, next, language);
}

/** Whether a phrase should go through the English translator for YouTube: it has letters that are not plain English. */
export function needsEnglish(text: string): boolean {
  return /[^\x00-\x7f]/.test(text) && /\p{L}/u.test(text);
}

/** Mix result lists one by one (Bilibili, YouTube, SkyPixel, Bilibili, ...), without repeating a page. */
export function interleave(lists: ReadonlyArray<readonly DiscoveredVideo[]>): DiscoveredVideo[] {
  const out: DiscoveredVideo[] = [];
  const seen = new Set<string>();
  const longest = Math.max(0, ...lists.map((list) => list.length));
  for (let index = 0; index < longest; index++) {
    for (const list of lists) {
      const video = list[index];
      if (video && !seen.has(video.pageUrl)) {
        seen.add(video.pageUrl);
        out.push(video);
      }
    }
  }
  return out;
}

/**
 * The picture quality a card can show: from the size the source reports (the short side, so a vertical
 * 2160x3840 is 4K too), else the source's own label, else a 4K/超清 in the title (`sure` false).
 */
export function qualityLabel(video: Pick<DiscoveredVideo, "title"> & Partial<Pick<DiscoveredVideo, "width" | "height" | "badges">>): { label: string; sure: boolean } | null {
  const sides = [video.width, video.height].filter((side): side is number => typeof side === "number" && side > 0);
  if (sides.length) {
    const short = Math.min(...sides);
    if (short >= 2000) return { label: "4K", sure: true };
    if (short >= 1400) return { label: "2K", sure: true };
    if (short >= 1000) return { label: "1080p", sure: true };
    if (short >= 700) return { label: "720p", sure: true };
    return { label: "SD", sure: true };
  }
  const badges = video.badges ?? [];
  for (const label of ["8K", "4K", "HD"]) if (badges.includes(label)) return { label: label === "HD" ? "HD" : label, sure: true };
  if (/(^|[^0-9a-z])(8k|4k|2160p)([^0-9a-z]|$)|超清|超高清/i.test(video.title)) return { label: "4K", sure: false };
  return null;
}

export type Trend = { keyword: string; rank: number; heat: number | null };

/**
 * Vietnamese topics a reup channel usually looks for, with the Chinese search
 * keyword that finds them on Douyin/Bilibili/Kuaishou.
 */
export const TOPICS: ReadonlyArray<{ vi: string; zh: string }> = [
  { vi: "Review phim", zh: "电影解说" }, { vi: "Phim ngắn", zh: "短剧" }, { vi: "Tiên hiệp", zh: "仙侠剧" },
  { vi: "Cổ trang", zh: "古装剧" }, { vi: "Hoạt hình TQ", zh: "国漫" }, { vi: "Anime", zh: "动漫" },
  { vi: "Tổng tài", zh: "霸道总裁" }, { vi: "Xuyên không", zh: "穿越剧" }, { vi: "Võ thuật", zh: "功夫" },
  { vi: "Kinh dị", zh: "恐怖片解说" }, { vi: "Ẩm thực", zh: "美食" }, { vi: "Nấu ăn", zh: "做饭教程" },
  { vi: "Du lịch", zh: "旅行vlog" }, { vi: "Hài hước", zh: "搞笑视频" }, { vi: "Thú cưng", zh: "萌宠" },
  { vi: "Nông thôn", zh: "农村生活" }, { vi: "Thủ công", zh: "手工制作" }, { vi: "Mẹo vặt", zh: "生活小妙招" },
  { vi: "Làm đẹp", zh: "美妆教程" }, { vi: "Thời trang", zh: "穿搭" }, { vi: "Công nghệ", zh: "数码测评" },
  { vi: "Ô tô", zh: "汽车评测" }, { vi: "Game", zh: "游戏解说" }, { vi: "Lịch sử", zh: "历史故事" },
  { vi: "Khoa học", zh: "科普" }, { vi: "Thể hình", zh: "健身" }, { vi: "Cảm động", zh: "感人视频" },
];

const HAN = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/u;

/** Lower case, no Vietnamese tone marks, single spaces: "Ẩm  thực" and "am thuc" match. */
function fold(text: string): string {
  return text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D").toLowerCase().replace(/\s+/g, " ").trim();
}

/** Whether a search phrase should go through Vietnamese → Chinese first: it has letters, no Chinese and is not a link. */
export function needsChinese(text: string): boolean {
  const value = text.trim();
  return /\p{L}/u.test(value) && !HAN.test(value) && !/^(https?:\/\/|www\.)/i.test(value);
}

/** The Chinese keyword of a built-in topic typed in Vietnamese (with or without tone marks), or null. */
export function topicChinese(text: string): string | null {
  const key = fold(text);
  return TOPICS.find((topic) => fold(topic.vi) === key)?.zh ?? null;
}

/** The Chinese phrase from `translate_query`, or null unless it holds Chinese. */
export function parseChinese(value: unknown): string | null {
  const object = (value ?? {}) as Record<string, unknown>;
  if (object.ok !== true || typeof object.text !== "string") return null;
  const phrase = object.text.replace(/\s+/g, " ").trim().slice(0, 100);
  return HAN.test(phrase) ? phrase : null;
}

/** Glosses keyed by keyword; an empty gloss means the term could not be translated. */
export function parseGlosses(value: unknown, terms: readonly string[]): Map<string, string> | null {
  const object = (value ?? {}) as Record<string, unknown>;
  const items = object.items;
  if (object.ok !== true || !Array.isArray(items) || items.length !== terms.length) return null;
  return new Map(terms.map((term, index) => [term, text(items[index], 120)]));
}

/** One video the user can pick, from the API or from the discovery page. */
export type DiscoveredVideo = {
  title: string;
  author: string;
  cover: string | null;
  durationS: number | null;
  plays: number | null;
  pageUrl: string;
  /** What to download; differs from pageUrl when a page is not itself fetchable. */
  fetchUrl: string;
  /** Where it was found, when a list mixes sources. */
  source?: Source;
  /** The upload's picture size and the source's own labels ("4K", "CC"), when the source says. */
  width?: number | null;
  height?: number | null;
  badges?: string[];
  /** The source's licence for this video (SkyPixel), once known. */
  license?: string;
};

export type DiscoverResult<T> = { ok: true; items: T[]; nextPage: number | null; nextCursor?: string | null } | { ok: false; code: string; message: string };

const text = (value: unknown, limit = 200): string => (typeof value === "string" ? value.replace(/\s+/g, " ").trim().slice(0, limit) : "");
const count = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.floor(value) : null);
const https = (value: unknown): string | null => (typeof value === "string" && /^https:\/\/[^\s]+$/.test(value) && value.length <= 2048 ? value : null);

function envelope(value: unknown): { ok: true; items: unknown[]; nextPage: number | null; nextCursor: string | null } | { ok: false; code: string; message: string } {
  const object = (value ?? {}) as Record<string, unknown>;
  if (object.ok !== true) {
    return { ok: false, code: text(object.code, 40) || "BAD_RESPONSE", message: text(object.message, 300) || "Không lấy được dữ liệu" };
  }
  const cursor = typeof object.next_cursor === "string" && /^[0-9A-Za-z_=%-]{8,4096}$/.test(object.next_cursor) ? object.next_cursor : null;
  return { ok: true, items: Array.isArray(object.items) ? object.items : [], nextPage: count(object.next_page), nextCursor: cursor };
}

const SOURCES = new Set<string>(["bilibili", "youtube", "skypixel", "douyin", "kuaishou"]);

export function parseTrends(value: unknown): DiscoverResult<Trend> {
  const result = envelope(value);
  if (!result.ok) return result;
  const items = result.items.flatMap((raw, index) => {
    const item = (raw ?? {}) as Record<string, unknown>;
    const keyword = text(item.keyword, 80);
    return keyword ? [{ keyword, rank: count(item.rank) ?? index + 1, heat: count(item.heat) }] : [];
  });
  return { ok: true, items, nextPage: result.nextPage };
}

export function parseVideos(value: unknown): DiscoverResult<DiscoveredVideo> {
  const result = envelope(value);
  if (!result.ok) return result;
  const items = result.items.flatMap((raw) => {
    const item = (raw ?? {}) as Record<string, unknown>;
    const pageUrl = https(item.page_url);
    if (!pageUrl) return [];
    const video: DiscoveredVideo = { title: text(item.title) || "(không có tiêu đề)", author: text(item.author, 80), cover: https(item.cover_url),
      durationS: count(item.duration_s), plays: count(item.plays), pageUrl, fetchUrl: pageUrl };
    if (typeof item.platform === "string" && SOURCES.has(item.platform)) video.source = item.platform as Source;
    const width = count(item.width);
    const height = count(item.height);
    if (width) video.width = width;
    if (height) video.height = height;
    if (Array.isArray(item.badges)) {
      const badges = item.badges.filter((badge): badge is string => typeof badge === "string" && /^[0-9A-Za-z°]{1,8}$/.test(badge)).slice(0, 6);
      if (badges.length) video.badges = badges;
    }
    return [video];
  });
  return { ok: true, items, nextPage: result.nextPage, nextCursor: result.nextCursor };
}

/** The MP4 rendition `discover` "media" found for a SkyPixel video, or null. */
export function parseMedia(value: unknown): { url: string; quality: string; license: string } | null {
  const object = (value ?? {}) as Record<string, unknown>;
  const url = https(object.url);
  if (object.ok !== true || !url) return null;
  try {
    const host = new URL(url).hostname;
    if (!["dji.net", "djicdn.com", "djivideos.com"].some((domain) => host.endsWith(`.${domain}`))) return null;
  } catch {
    return null;
  }
  return { url, quality: text(object.quality, 12), license: text(object.license, 80) };
}

/** A video read from the discovery page; `current` marks the one a video page is playing. */
export type CollectedVideo = DiscoveredVideo & { current: boolean };

/** Items read from the discovery browser page by the host. */
export function parseCollected(value: unknown): CollectedVideo[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((raw) => {
    const item = (raw ?? {}) as Record<string, unknown>;
    const pageUrl = https(item.page_url);
    const fetchUrl = https(item.fetch_url);
    if (!pageUrl || !fetchUrl) return [];
    return [{ title: text(item.title) || "(không có tiêu đề)", author: text(item.author, 80), cover: https(item.cover_url),
      durationS: null, plays: null, pageUrl, fetchUrl, current: item.current === true }];
  });
}

/**
 * What the page shows the user, for the list kept while they browse: on a
 * video page only the video they are watching (its suggestions are not
 * something they looked at), on a list page every card it shows.
 */
export function seenOnPage(items: readonly CollectedVideo[]): DiscoveredVideo[] {
  const playing = items.filter((item) => item.current);
  return (playing.length ? playing : items).map(({ current: _current, ...video }) => video);
}

/** Update the titles and covers of listed videos the page now describes better. */
export function refreshVideos(current: readonly DiscoveredVideo[], incoming: readonly DiscoveredVideo[]): DiscoveredVideo[] {
  const fresh = new Map(incoming.map((video) => [video.pageUrl, video]));
  return current.map((video) => {
    const update = fresh.get(video.pageUrl);
    if (!update) return video;
    const title = video.title === "(không có tiêu đề)" ? update.title : video.title;
    const cover = video.cover ?? update.cover;
    const author = video.author || update.author;
    return title === video.title && cover === video.cover && author === video.author ? video : { ...video, title, cover, author };
  });
}

/** Merge new results into a list without duplicating the same page. */
export function mergeVideos(current: readonly DiscoveredVideo[], incoming: readonly DiscoveredVideo[]): DiscoveredVideo[] {
  const seen = new Set(current.map((video) => video.pageUrl));
  return [...current, ...incoming.filter((video) => !seen.has(video.pageUrl) && seen.add(video.pageUrl))];
}

/** Readable form of a page address for the toolbar: decoded, no scheme or trailing slash, at most 300 characters. */
export function pageAddress(url: string): string {
  let text = url;
  try {
    text = decodeURI(url);
  } catch {
    // Malformed escapes: show the address as it is.
  }
  return text.replace(/^https?:\/\//, "").replace(/\/$/, "").replace(/\s+/g, " ").slice(0, 300);
}

/** Bilibili creator id from a space link, so the API can list it directly. */
export function bilibiliMid(link: string): string | null {
  const match = /^https:\/\/space\.bilibili\.com\/(\d{1,20})(?:[/?#]|$)/.exec(link.trim());
  return match ? match[1] : null;
}

/** "12,3 vạn" style counts, as Chinese platforms show them. */
export function formatCount(value: number | null): string {
  if (value === null) return "";
  const short = (scaled: number) => scaled.toFixed(1).replace(/\.0$/, "").replace(".", ",");
  if (value >= 100_000_000) return `${short(value / 100_000_000)} tỷ`;
  if (value >= 10_000) return `${short(value / 10_000)} vạn`;
  return String(value);
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "";
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** How a result can be watched inside the app: an official embed player or the media file itself. */
export type PreviewSource = { kind: "iframe"; src: string; vertical: boolean } | { kind: "video"; src: string; vertical: boolean };

const BILIBILI_HOSTS = new Set(["www.bilibili.com", "bilibili.com", "m.bilibili.com"]);
const YOUTUBE_HOSTS = new Set(["www.youtube.com", "youtube.com", "m.youtube.com"]);
/** SkyPixel renditions on DJI's video CDN, allowed by the CSP `media-src`. */
const DJI_MEDIA = ["dji.net"];
const DOUYIN_HOSTS = new Set(["www.douyin.com", "douyin.com"]);
/** Media hosts the CSP `media-src` allows (subdomains only). */
const KUAISHOU_MEDIA = ["kwaicdn.com", "yximgs.com", "kwimgs.com", "kuaishou.com"];

const parseHttps = (value: string): URL | null => {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.port ? url : null;
  } catch {
    return null;
  }
};

/**
 * Where to play a result. Bilibili and Douyin go through their official embed
 * players; Kuaishou plays the direct media URL collected from its page. Douyin's
 * player has no documented mute flag, so it is never used for a muted preview.
 */
export function previewSource(video: Pick<DiscoveredVideo, "pageUrl" | "fetchUrl">, options: { muted: boolean }): PreviewSource | null {
  const page = parseHttps(video.pageUrl);
  if (page && BILIBILI_HOSTS.has(page.hostname)) {
    const match = /^\/video\/(BV[0-9A-Za-z]{10})\/?$/.exec(page.pathname);
    if (!match) return null;
    const muted = options.muted ? 1 : 0;
    return { kind: "iframe", vertical: false,
      src: `https://player.bilibili.com/player.html?bvid=${match[1]}&autoplay=1&muted=${muted}&danmaku=0&high_quality=0&as_wide=1&page=1` };
  }
  if (page && YOUTUBE_HOSTS.has(page.hostname)) {
    const id = page.pathname === "/watch" ? page.searchParams.get("v") ?? "" : "";
    if (!/^[0-9A-Za-z_-]{11}$/.test(id)) return null;
    const muted = options.muted ? 1 : 0;
    return { kind: "iframe", vertical: false, src: `https://www.youtube-nocookie.com/embed/${id}?autoplay=1&mute=${muted}&playsinline=1&rel=0` };
  }
  if (page && DOUYIN_HOSTS.has(page.hostname)) {
    const match = /^\/video\/(\d{8,24})\/?$/.exec(page.pathname);
    if (!match || options.muted) return null;
    return { kind: "iframe", vertical: true, src: `https://open.douyin.com/player/video?vid=${match[1]}&autoplay=1` };
  }
  if (video.fetchUrl !== video.pageUrl && video.fetchUrl.length <= 2048) {
    const media = parseHttps(video.fetchUrl);
    if (media && KUAISHOU_MEDIA.some((domain) => media.hostname.endsWith(`.${domain}`))) {
      return { kind: "video", vertical: true, src: media.href };
    }
    if (media && DJI_MEDIA.some((domain) => media.hostname.endsWith(`.${domain}`))) {
      return { kind: "video", vertical: false, src: media.href };
    }
  }
  return null;
}

/** Longest text the term translator accepts per item. */
export const GLOSS_TERM_LIMIT = 80;
/** Titles per `translate_terms` call. */
export const GLOSS_BATCH = 60;
export const TITLE_GLOSS_KEY = "dubflow.titleGloss.v1";
export const TITLE_GLOSS_MAX = 800;

/**
 * A title reduced to what is worth translating: no hashtags, mentions or
 * emoji, single spaces, at most 80 characters cut at a word boundary when
 * there is one.
 */
export function cleanTitle(title: string): string {
  const cleaned = title
    .replace(/[#＃][^\s#＃]+/gu, " ")
    .replace(/[@＠][^\s@＠]+/gu, " ")
    .replace(/[\p{Extended_Pictographic}\p{Regional_Indicator}\u{1F3FB}-\u{1F3FF}‍︎️⃣]/gu, "")
    .replace(/[\p{Cc}\p{Cf}]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
  const chars = Array.from(cleaned);
  if (chars.length <= GLOSS_TERM_LIMIT) return cleaned;
  const cut = chars.slice(0, GLOSS_TERM_LIMIT).join("");
  const space = cut.lastIndexOf(" ");
  return (space >= GLOSS_TERM_LIMIT / 2 ? cut.slice(0, space) : cut).trim();
}

/** Whether a cleaned title still needs translating: it has letters and most are not Latin (Vietnamese included). */
export function needsTitleGloss(cleaned: string): boolean {
  const letters = cleaned.match(/\p{L}/gu)?.length ?? 0;
  const latin = cleaned.match(/\p{Script=Latin}/gu)?.length ?? 0;
  return letters > 0 && latin / letters < 0.5;
}

/** Record a gloss as most recently used, dropping the oldest entries past `max`. */
export function rememberGloss(cache: Map<string, string>, original: string, gloss: string, max = TITLE_GLOSS_MAX): void {
  cache.delete(original);
  cache.set(original, gloss);
  for (const key of cache.keys()) {
    if (cache.size <= max) break;
    cache.delete(key);
  }
}

/** Read a stored cache (a JSON list of [original, gloss] pairs, oldest first); anything malformed is dropped. */
export function loadGlossCache(raw: string | null, max = TITLE_GLOSS_MAX): Map<string, string> {
  const cache = new Map<string, string>();
  let value: unknown = null;
  try {
    value = raw ? JSON.parse(raw) : null;
  } catch {
    return cache;
  }
  if (!Array.isArray(value)) return cache;
  for (const entry of value) {
    if (Array.isArray(entry) && entry.length === 2 && typeof entry[0] === "string" && typeof entry[1] === "string"
      && entry[0] && entry[1] && entry[0].length <= 400 && entry[1].length <= 200) {
      rememberGloss(cache, entry[0], entry[1], max);
    }
  }
  return cache;
}

/** Serialise the non-empty glosses, newest last, at most `max`. */
export function saveGlossCache(cache: ReadonlyMap<string, string>, max = TITLE_GLOSS_MAX): string {
  return JSON.stringify([...cache].filter(([, gloss]) => gloss).slice(-max));
}

/** Vietnamese explanation for an adapter failure code. */
export function discoveryError(code: string, platform: Platform): string {
  const browsable = PLATFORMS.find((entry) => entry.id === platform)?.page ?? false;
  switch (code) {
    case "BLOCKED": return browsable ? "Trang tạm chặn truy cập tự động. Hãy dùng “Mở trang” để xem và lấy video trực tiếp." : "Trang tạm chặn truy cập tự động. Thử lại sau ít phút.";
    case "LOGIN_REQUIRED": return browsable ? "Trang yêu cầu đăng nhập cho tính năng này. Hãy dùng “Mở trang”." : "Trang yêu cầu đăng nhập cho tính năng này.";
    case "UNSUPPORTED": return platform === "kuaishou" ? "Kuaishou chưa hỗ trợ tính năng này qua API." : "Tính năng này chưa hỗ trợ qua API.";
    case "NETWORK": return "Không kết nối được. Kiểm tra mạng rồi thử lại.";
    default: return "Không lấy được dữ liệu. Thử lại sau.";
  }
}
