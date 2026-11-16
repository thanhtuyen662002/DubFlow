/**
 * Hàng đợi: every source video the user brought into DubFlow, downloaded from
 * a link or added from the computer. Nothing is processed here; Lồng tiếng,
 * Bán hàng and Ghép video each pick their videos from this list.
 */
import { fileTitle, fold, folderOf, pathKey } from "../edit/library.ts";

export const MEDIA_STORAGE_KEY = "dubflow.media.v1";
export const MAX_MEDIA = 5_000;

export type MediaEntry = {
  id: string;
  path: string;
  title: string;
  /** ISO time it joined the list. */
  addedAt: string;
  /** The link it was downloaded from; absent for a file from the computer. */
  originUrl?: string;
  /** Where a dub of this link saves its results (chosen when the link was added). */
  outputDir?: string;
};

export type MediaFilter = "all" | "link" | "file";
export const MEDIA_FILTERS: ReadonlyArray<{ id: MediaFilter; label: string }> = [
  { id: "all", label: "Tất cả" },
  { id: "link", label: "Tải về" },
  { id: "file", label: "Từ máy" },
];

/** What a Hàng đợi video has been used for, read from the dub jobs. */
export type MediaUse = "none" | "dubbing" | "dubbed" | "failed";

export type DubJobLike = {
  id: string; sourcePath: string; displayName: string; originUrl?: string; outputDir?: string; createdAt: string;
  revisionOf?: string; outputPath: string | null; status: { state: string };
};

const LINK = /^https?:\/\//i;

function newId(): string {
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(8));
  return `media-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

function isEntry(value: unknown): value is MediaEntry {
  if (!value || typeof value !== "object") return false;
  const entry = value as Record<string, unknown>;
  return typeof entry.id === "string" && typeof entry.path === "string" && Boolean(entry.path.trim())
    && typeof entry.title === "string" && typeof entry.addedAt === "string"
    && (entry.originUrl === undefined || (typeof entry.originUrl === "string" && LINK.test(entry.originUrl)))
    && (entry.outputDir === undefined || typeof entry.outputDir === "string");
}

/**
 * The stored list, newest first. `missing`: nothing stored yet (first run
 * after the update); `corrupt`: something unreadable is stored. Both are
 * rebuilt from Lồng tiếng's videos, never taken as an empty list.
 */
export type ParsedMedia = { state: "ok"; entries: MediaEntry[]; dropped: number } | { state: "missing" | "corrupt" };

export function readMedia(serialized: string | null): ParsedMedia {
  if (serialized === null) return { state: "missing" };
  let value: unknown;
  try {
    value = JSON.parse(serialized);
  } catch {
    return { state: "corrupt" };
  }
  const list = Array.isArray(value) ? value : (value && typeof value === "object" ? (value as { items?: unknown }).items : null);
  if (!Array.isArray(list)) return { state: "corrupt" };
  const entries = parseMedia(serialized) ?? [];
  return { state: "ok", entries, dropped: list.length - entries.length };
}

/** The stored list's valid rows (see readMedia for what a broken store means). */
export function parseMedia(serialized: string | null): MediaEntry[] | null {
  if (serialized === null) return null;
  try {
    const value = JSON.parse(serialized) as unknown;
    const list = Array.isArray(value) ? value : (value && typeof value === "object" ? (value as { items?: unknown }).items : null);
    if (!Array.isArray(list)) return null;
    const seen = new Set<string>();
    return list.filter(isEntry).filter((entry) => {
      const key = pathKey(entry.path);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, MAX_MEDIA);
  } catch {
    return null;
  }
}

export function serializeMedia(entries: readonly MediaEntry[]): string {
  return JSON.stringify({ schema_version: 1, items: entries });
}

/** Where an unreadable list is kept aside before anything replaces it. */
export const MEDIA_CORRUPT_KEY = `${MEDIA_STORAGE_KEY}.corrupt`;

/** The part of window.localStorage Hàng đợi uses; either call may throw. */
export type MediaStorage = { getItem(key: string): string | null; setItem(key: string, value: string): void };

/**
 * The list Hàng đợi opens with. `sessionOnly`: the stored list could not be
 * read or backed up, so nothing may be written over it this session.
 * `saved`: what the list holds now is in storage. `notice`: what to tell the user.
 */
export type OpenedMedia = {
  state: "ok" | "missing" | "corrupt" | "unavailable";
  entries: MediaEntry[];
  dropped: number;
  saved: boolean;
  sessionOnly: boolean;
  backup: "none" | "saved" | "failed";
  notice: string | null;
};

export const MEDIA_NOTICES = {
  unavailable: "Không đọc được danh sách Hàng đợi đã lưu (bộ nhớ của ứng dụng đang lỗi hoặc bị chặn). DubFlow không ghi đè lên danh sách cũ: danh sách hiện tại được dựng tạm từ các video ở Lồng tiếng và chỉ dùng đến khi đóng DubFlow. Hãy đóng rồi mở lại DubFlow để đọc lại; file video trên máy không bị ảnh hưởng.",
  corrupt: "Danh sách Hàng đợi đã lưu bị hỏng nên được dựng lại từ các video ở Lồng tiếng. Bản cũ được giữ riêng; file video trên máy không bị ảnh hưởng.",
  backupFailed: "Danh sách Hàng đợi đã lưu bị hỏng và DubFlow chưa sao lưu được bản cũ, nên không ghi đè lên nó. Danh sách hiện tại được dựng tạm từ các video ở Lồng tiếng và chỉ dùng đến khi đóng DubFlow. Hãy giải phóng dung lượng ổ đĩa rồi mở lại DubFlow; file video trên máy không bị ảnh hưởng.",
  notSaved: "Chưa lưu được danh sách Hàng đợi (bộ nhớ của ứng dụng bị tắt hoặc đã đầy). Danh sách chỉ nằm trong bộ nhớ đến khi lưu được; file video trên máy không bị ảnh hưởng.",
} as const;

/**
 * Read the stored list once at startup. A read error is never taken for a
 * missing list, and a corrupt list is replaced only after its bytes are
 * confirmed saved under MEDIA_CORRUPT_KEY; otherwise the rebuilt list stays
 * in this session and the stored bytes are left untouched.
 */
export function openMedia(storage: MediaStorage, seed: () => MediaEntry[]): OpenedMedia {
  const write = (key: string, value: string): boolean => {
    try { storage.setItem(key, value); return true; } catch { return false; }
  };
  let raw: string | null;
  try {
    raw = storage.getItem(MEDIA_STORAGE_KEY);
  } catch {
    return { state: "unavailable", entries: seed(), dropped: 0, saved: false, sessionOnly: true, backup: "none", notice: MEDIA_NOTICES.unavailable };
  }
  const parsed = readMedia(raw);
  if (parsed.state === "ok") {
    const notice = parsed.dropped ? `${parsed.dropped} mục hỏng trong danh sách Hàng đợi đã được bỏ qua.` : null;
    return { state: "ok", entries: parsed.entries, dropped: parsed.dropped, saved: true, sessionOnly: false, backup: "none", notice };
  }
  const entries = seed();
  if (parsed.state === "corrupt" && raw !== null) {
    let confirmed = write(MEDIA_CORRUPT_KEY, raw);
    if (confirmed) {
      try { confirmed = storage.getItem(MEDIA_CORRUPT_KEY) === raw; } catch { confirmed = false; }
    }
    if (!confirmed) return { state: "corrupt", entries, dropped: 0, saved: false, sessionOnly: true, backup: "failed", notice: MEDIA_NOTICES.backupFailed };
    const saved = write(MEDIA_STORAGE_KEY, serializeMedia(entries));
    return { state: "corrupt", entries, dropped: 0, saved, sessionOnly: false, backup: "saved",
      notice: saved ? MEDIA_NOTICES.corrupt : `${MEDIA_NOTICES.corrupt} ${MEDIA_NOTICES.notSaved}` };
  }
  const saved = write(MEDIA_STORAGE_KEY, serializeMedia(entries));
  return { state: "missing", entries, dropped: 0, saved, sessionOnly: false, backup: "none", notice: saved ? null : MEDIA_NOTICES.notSaved };
}

/**
 * The first time Hàng đợi opens, the videos already in Lồng tiếng become its
 * list, so nobody loses what they had downloaded. Re-runs (revisions) share
 * their source and are skipped.
 */
export function seedFromJobs(jobs: readonly DubJobLike[]): MediaEntry[] {
  const seen = new Set<string>();
  const entries: MediaEntry[] = [];
  for (const job of [...jobs].reverse()) {
    if (job.revisionOf) continue;
    const key = pathKey(job.sourcePath);
    if (!job.sourcePath.trim() || seen.has(key)) continue;
    seen.add(key);
    entries.push({
      id: `media-${job.id}`, path: job.sourcePath, title: job.displayName.replace(/\.[^.]+$/, "") || fileTitle(job.sourcePath),
      addedAt: job.createdAt,
      ...(job.originUrl && LINK.test(job.originUrl) ? { originUrl: job.originUrl } : {}),
      ...(job.outputDir ? { outputDir: job.outputDir } : {}),
    });
  }
  return entries.slice(0, MAX_MEDIA);
}

export type NewMedia = { path: string; title?: string; originUrl?: string; outputDir?: string };

/**
 * Add videos at the top; a path already listed moves up and keeps its id.
 * A full list (MAX_MEDIA) takes no new video: those are counted in `refused`, never dropped silently.
 */
export function addMedia(entries: readonly MediaEntry[], added: readonly NewMedia[], now: string, makeId: () => string = newId, max = MAX_MEDIA): { entries: MediaEntry[]; ids: string[]; refused: number } {
  let list = [...entries];
  const ids: string[] = [];
  let refused = 0;
  for (const item of added) {
    const path = item.path.trim();
    if (!path) continue;
    const key = pathKey(path);
    const known = list.find((entry) => pathKey(entry.path) === key);
    if (!known && list.length >= max) { refused += 1; continue; }
    const title = item.title?.trim() || known?.title || fileTitle(path);
    const entry: MediaEntry = {
      id: known?.id ?? makeId(), path, title, addedAt: now,
      ...(item.originUrl && LINK.test(item.originUrl) ? { originUrl: item.originUrl } : known?.originUrl ? { originUrl: known.originUrl } : {}),
      ...(item.outputDir ? { outputDir: item.outputDir } : known?.outputDir ? { outputDir: known.outputDir } : {}),
    };
    list = [entry, ...list.filter((other) => other !== known)];
    if (!ids.includes(entry.id)) ids.push(entry.id);
  }
  return { entries: list, ids, refused };
}

export function removeMedia(entries: readonly MediaEntry[], ids: readonly string[]): MediaEntry[] {
  const drop = new Set(ids);
  return entries.filter((entry) => !drop.has(entry.id));
}

/** The newest dub job made from this video (re-runs count as the same video). */
export function dubJobFor<T extends DubJobLike>(entry: MediaEntry, jobs: readonly T[]): T | undefined {
  const key = pathKey(entry.path);
  let found: T | undefined;
  for (const job of jobs) if (pathKey(job.sourcePath) === key && (!found || job.createdAt >= found.createdAt)) found = job;
  return found;
}

/** The finished dub of this video, if any: what Ghép video needs. */
export function finishedDubFor<T extends DubJobLike>(entry: MediaEntry, jobs: readonly T[]): T | undefined {
  const key = pathKey(entry.path);
  let found: T | undefined;
  for (const job of jobs) {
    if (pathKey(job.sourcePath) !== key || job.status.state !== "COMPLETED" || !job.outputPath) continue;
    if (!found || job.createdAt >= found.createdAt) found = job;
  }
  return found;
}

export function mediaUse(entry: MediaEntry, jobs: readonly DubJobLike[]): MediaUse {
  if (finishedDubFor(entry, jobs)) return "dubbed";
  const job = dubJobFor(entry, jobs);
  if (!job) return "none";
  if (["FAILED", "BLOCKED_NEEDS_ACTION"].includes(job.status.state)) return "failed";
  return "dubbing";
}

export const USE_LABELS: Record<MediaUse, string> = {
  none: "", dubbing: "Đang ở Lồng tiếng", dubbed: "Đã lồng tiếng", failed: "Lồng tiếng lỗi",
};

export function filterMedia(entries: readonly MediaEntry[], filter: MediaFilter, query: string): MediaEntry[] {
  const words = fold(query).split(/\s+/).filter(Boolean);
  return entries.filter((entry) => (filter === "all" || (filter === "link") === Boolean(entry.originUrl))
    && words.every((word) => fold(`${entry.title} ${entry.path} ${entry.originUrl ?? ""}`).includes(word)));
}

export function countMedia(entries: readonly MediaEntry[]): Record<MediaFilter, number> {
  const link = entries.filter((entry) => entry.originUrl).length;
  return { all: entries.length, link, file: entries.length - link };
}

/** The site a link came from, for the row's small label. */
export function originLabel(entry: MediaEntry): string {
  if (!entry.originUrl) return "Từ máy";
  try {
    const host = new URL(entry.originUrl).hostname;
    if (/douyin|iesdouyin/.test(host)) return "Douyin";
    if (/bilibili|b23\.tv/.test(host)) return "Bilibili";
    if (/kuaishou|kwai|chenzhongtech/.test(host)) return "Kuaishou";
    return host.replace(/^www\./, "");
  } catch {
    return "Link";
  }
}

export { folderOf };
