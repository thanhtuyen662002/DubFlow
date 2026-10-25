/** Discovery data as returned by the host (`discover`, `collect_discovery`). */
export type Platform = "douyin" | "bilibili" | "kuaishou";

export const PLATFORMS: ReadonlyArray<{ id: Platform; name: string; suggest: boolean; apiSearch: boolean }> = [
  { id: "douyin", name: "Douyin", suggest: true, apiSearch: false },
  { id: "bilibili", name: "Bilibili", suggest: true, apiSearch: true },
  { id: "kuaishou", name: "Kuaishou", suggest: false, apiSearch: false },
];

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
};

export type DiscoverResult<T> = { ok: true; items: T[]; nextPage: number | null } | { ok: false; code: string; message: string };

const text = (value: unknown, limit = 200): string => (typeof value === "string" ? value.replace(/\s+/g, " ").trim().slice(0, limit) : "");
const count = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.floor(value) : null);
const https = (value: unknown): string | null => (typeof value === "string" && /^https:\/\/[^\s]+$/.test(value) && value.length <= 2048 ? value : null);

function envelope(value: unknown): { ok: true; items: unknown[]; nextPage: number | null } | { ok: false; code: string; message: string } {
  const object = (value ?? {}) as Record<string, unknown>;
  if (object.ok !== true) {
    return { ok: false, code: text(object.code, 40) || "BAD_RESPONSE", message: text(object.message, 300) || "Không lấy được dữ liệu" };
  }
  return { ok: true, items: Array.isArray(object.items) ? object.items : [], nextPage: count(object.next_page) };
}

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
    return [{ title: text(item.title) || "(không có tiêu đề)", author: text(item.author, 80), cover: https(item.cover_url),
      durationS: count(item.duration_s), plays: count(item.plays), pageUrl, fetchUrl: pageUrl }];
  });
  return { ok: true, items, nextPage: result.nextPage };
}

/** Items read from the discovery browser page by the host. */
export function parseCollected(value: unknown): DiscoveredVideo[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((raw) => {
    const item = (raw ?? {}) as Record<string, unknown>;
    const pageUrl = https(item.page_url);
    const fetchUrl = https(item.fetch_url);
    if (!pageUrl || !fetchUrl) return [];
    return [{ title: text(item.title) || "(không có tiêu đề)", author: text(item.author, 80), cover: https(item.cover_url),
      durationS: null, plays: null, pageUrl, fetchUrl }];
  });
}

/** Merge new results into a list without duplicating the same page. */
export function mergeVideos(current: readonly DiscoveredVideo[], incoming: readonly DiscoveredVideo[]): DiscoveredVideo[] {
  const seen = new Set(current.map((video) => video.pageUrl));
  return [...current, ...incoming.filter((video) => !seen.has(video.pageUrl) && seen.add(video.pageUrl))];
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

/** Vietnamese explanation for an adapter failure code. */
export function discoveryError(code: string, platform: Platform): string {
  switch (code) {
    case "BLOCKED": return "Trang tạm chặn truy cập tự động. Hãy dùng “Mở trang” để xem và lấy video trực tiếp.";
    case "LOGIN_REQUIRED": return "Trang yêu cầu đăng nhập cho tính năng này. Hãy dùng “Mở trang”.";
    case "UNSUPPORTED": return platform === "kuaishou" ? "Kuaishou chưa hỗ trợ tính năng này qua API." : "Tính năng này chưa hỗ trợ qua API.";
    case "NETWORK": return "Không kết nối được. Kiểm tra mạng rồi thử lại.";
    default: return "Không lấy được dữ liệu. Thử lại sau.";
  }
}
