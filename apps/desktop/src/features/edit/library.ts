/**
 * The Chỉnh sửa screen's list (Issue #497): every finished video the editor
 * can open (queue videos, Bán hàng videos, compilations, documentary rough
 * cuts) and the videos the user opened from the computer, newest first.
 */
export type LibraryKind = "job" | "product" | "compilation" | "docu" | "file";
export type LibraryEntry = {
  path: string;
  title: string;
  kind: LibraryKind;
  /** Seconds since 1970 (finished, or last opened for a file); 0 when unknown. */
  modified: number;
  /** The queue job (kind "job"). */
  jobId?: string;
  sizeBytes?: number;
};
export type LibraryFilter = "all" | LibraryKind;

export const FILTERS: ReadonlyArray<{ id: LibraryFilter; label: string }> = [
  { id: "all", label: "Tất cả" },
  { id: "job", label: "Lồng tiếng" },
  { id: "product", label: "Bán hàng" },
  { id: "compilation", label: "Tổng hợp" },
  { id: "docu", label: "Phim tài liệu" },
  { id: "file", label: "Trên máy" },
];
export const KIND_LABELS: Record<LibraryKind, string> = {
  job: "Lồng tiếng", product: "Bán hàng", compilation: "Tổng hợp", docu: "Phim tài liệu", file: "Trên máy",
};
/** Files remembered under "Trên máy". */
export const MAX_RECENT = 40;
const KINDS = new Set<string>(["product", "compilation", "docu"]);
const VIDEO = /\.(mp4|mkv|mov|avi|webm|m4v|ts|flv)$/i;

export function isVideoPath(path: string): boolean {
  return VIDEO.test(path.trim());
}

export function fileTitle(path: string): string {
  const name = path.split(/[\\/]/).pop() || path;
  return name.replace(/\.[^.]+$/, "") || name;
}

export function folderOf(path: string): string {
  const index = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  return index > 0 ? path.slice(0, index) : "";
}

/** Windows paths ignore case and either slash. */
export function pathKey(path: string): string {
  return path.trim().replace(/\//g, "\\").toLowerCase();
}

/** `edit_library`'s answer, keeping only well-formed rows. */
export function parseLibrary(value: unknown): LibraryEntry[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((row): LibraryEntry[] => {
    if (!row || typeof row !== "object") return [];
    const { path, title, kind, modified, size_bytes: size } = row as Record<string, unknown>;
    if (typeof path !== "string" || !path || typeof kind !== "string" || !KINDS.has(kind)) return [];
    return [{
      path, kind: kind as LibraryKind, title: typeof title === "string" && title.trim() ? title.trim() : fileTitle(path),
      modified: typeof modified === "number" && Number.isFinite(modified) ? modified : 0,
      ...(typeof size === "number" && Number.isFinite(size) ? { sizeBytes: size } : {}),
    }];
  });
}

/** The remembered files, from storage; bad rows are dropped. */
export function parseRecent(text: string | null): LibraryEntry[] {
  let value: unknown;
  try {
    value = JSON.parse(text ?? "[]");
  } catch {
    return [];
  }
  if (!Array.isArray(value)) return [];
  return value.flatMap((row): LibraryEntry[] => {
    if (!row || typeof row !== "object") return [];
    const { path, title, modified } = row as Record<string, unknown>;
    if (typeof path !== "string" || !isVideoPath(path)) return [];
    return [{ path, kind: "file", title: typeof title === "string" && title.trim() ? title : fileTitle(path),
      modified: typeof modified === "number" && Number.isFinite(modified) ? modified : 0 }];
  }).slice(0, MAX_RECENT);
}

/** `path` first among the remembered files, opened `now`. */
export function rememberFile(recent: readonly LibraryEntry[], path: string, now: number, title = fileTitle(path)): LibraryEntry[] {
  const key = pathKey(path);
  return [{ path, kind: "file" as const, title, modified: now }, ...recent.filter((entry) => pathKey(entry.path) !== key)].slice(0, MAX_RECENT);
}

export function forgetFile(recent: readonly LibraryEntry[], path: string): LibraryEntry[] {
  const key = pathKey(path);
  return recent.filter((entry) => pathKey(entry.path) !== key);
}

/** Everything, newest first; a file that is also a DubFlow video is listed once, as that video. */
export function mergeEntries(...lists: ReadonlyArray<readonly LibraryEntry[]>): LibraryEntry[] {
  const seen = new Map<string, LibraryEntry>();
  for (const entry of lists.flat()) {
    const key = pathKey(entry.path);
    const known = seen.get(key);
    if (!known) {
      seen.set(key, entry);
      continue;
    }
    // Opened lately as a file: listed as the DubFlow video, at the later time.
    const kept = known.kind === "file" && entry.kind !== "file" ? entry : known;
    seen.set(key, { ...kept, modified: Math.max(entry.modified, known.modified) });
  }
  return [...seen.values()].sort((a, b) => b.modified - a.modified || a.title.localeCompare(b.title, "vi"));
}

/** Videos with the same title get their date ("· 02/11 00:14") so they can be told apart. */
export function distinctTitles<T extends { title: string; modified: number }>(entries: readonly T[]): T[] {
  const counts = new Map<string, number>();
  for (const entry of entries) counts.set(entry.title, (counts.get(entry.title) ?? 0) + 1);
  return entries.map((entry) => (counts.get(entry.title) ?? 0) > 1 && entry.modified
    ? { ...entry, title: `${entry.title} · ${stamp(entry.modified)}` } : entry);
}

function stamp(seconds: number): string {
  const when = new Date(seconds * 1000);
  const two = (value: number): string => String(value).padStart(2, "0");
  return `${two(when.getDate())}/${two(when.getMonth() + 1)} ${two(when.getHours())}:${two(when.getMinutes())}`;
}

/** Search ignores case and Vietnamese marks ("bàn" finds "ban"). */
export function fold(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D").toLowerCase();
}

export function filterEntries(entries: readonly LibraryEntry[], filter: LibraryFilter, query: string): LibraryEntry[] {
  const words = fold(query).split(/\s+/).filter(Boolean);
  return entries.filter((entry) => (filter === "all" || entry.kind === filter)
    && words.every((word) => fold(`${entry.title} ${entry.path}`).includes(word)));
}

export function countByKind(entries: readonly LibraryEntry[]): Record<LibraryFilter, number> {
  const counts: Record<LibraryFilter, number> = { all: entries.length, job: 0, product: 0, compilation: 0, docu: 0, file: 0 };
  for (const entry of entries) counts[entry.kind] += 1;
  return counts;
}

/** "Hôm nay", "Hôm qua", "Tuần này" or "Trước đó", for the list's headings. */
export function ageGroup(seconds: number, now: Date): string {
  if (!seconds) return "Trước đó";
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  if (seconds >= day) return "Hôm nay";
  if (seconds >= day - 86_400) return "Hôm qua";
  if (seconds >= day - 6 * 86_400) return "7 ngày qua";
  return "Trước đó";
}

export function sizeLabel(bytes: number | undefined): string {
  if (!bytes || !Number.isFinite(bytes)) return "";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1).replace(".", ",")} GB`;
  return `${Math.max(1, Math.round(bytes / 1024 ** 2))} MB`;
}
