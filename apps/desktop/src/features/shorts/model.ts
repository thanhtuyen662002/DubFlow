export type Cue = { start_ms: number; end_ms: number; text: string };
export type Segment = { start_ms: number; end_ms: number; title: string };
export type Row = Segment & { selected: boolean; preview: string };
export type Layout = "auto" | "ban-tin" | "mo-nen";
export type Output = { path: string; text_path: string; title: string; text: string };
export type ShortsStatus = { state: "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED"; done: number; total: number; message: string; outputs: Output[] };

const CONTROL = /[\u0000-\u001f\u007f-\u009f]/u;
export const validTitle = (value: string): boolean => !!value.trim() && [...value].length <= 80 && !CONTROL.test(value);

/** mm:ss, with optional millisecond precision to retain exact cue boundaries. */
export function parseTime(value: string): number | null {
  const match = /^(\d+):([0-5]\d)(?:\.(\d{1,3}))?$/.exec(value.trim());
  if (!match) return null;
  const ms = Number(match[1]) * 60000 + Number(match[2]) * 1000 + Number((match[3] ?? "").padEnd(3, "0"));
  return Number.isSafeInteger(ms) ? ms : null;
}

export function formatTime(ms: number): string {
  return `${String(Math.floor(ms / 60000)).padStart(2, "0")}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`
    + (ms % 1000 ? `.${String(ms % 1000).padStart(3, "0")}` : "");
}

export function parseCues(value: unknown): Cue[] {
  if (!Array.isArray(value)) return [];
  return value.filter((cue): cue is Cue => !!cue && Number.isSafeInteger(cue.start_ms) && Number.isSafeInteger(cue.end_ms)
    && cue.start_ms >= 0 && cue.end_ms > cue.start_ms && typeof cue.text === "string").map(({ start_ms, end_ms, text }) => ({ start_ms, end_ms, text }))
    .sort((a, b) => a.start_ms - b.start_ms);
}

export function firstLine(cues: readonly Cue[], start: number, end: number): string {
  return cues.find((cue) => cue.end_ms > start && cue.start_ms < end)?.text ?? "Chưa có phụ đề ở đoạn này";
}

/** Snap each AI time to its nearest cue boundary; reject invalid/overlapping excerpts. */
export function normalizeSegments(value: unknown, cues: readonly Cue[]): Row[] {
  const ordered = parseCues(cues);
  const items = (value as { items?: unknown })?.items;
  if (!Array.isArray(items) || !ordered.length) return [];
  const nearest = (time: number, edge: "start_ms" | "end_ms"): number => ordered.reduce((best, cue) =>
    Math.abs(cue[edge] - time) < Math.abs(best - time) ? cue[edge] : best, ordered[0][edge]);
  const rows: Row[] = [];
  for (const item of items) {
    if (!item || !Number.isSafeInteger(item.start_ms) || !Number.isSafeInteger(item.end_ms)
      || item.start_ms < 0 || item.end_ms <= item.start_ms || typeof item.title !== "string") continue;
    const title = item.title.replace(/\s+/g, " ").trim();
    if (!validTitle(item.title) || !validTitle(title)) continue;
    const start_ms = nearest(item.start_ms, "start_ms"), end_ms = nearest(item.end_ms, "end_ms");
    if (end_ms - start_ms < 5000 || end_ms - start_ms > 60000) continue;
    if (rows.some((row) => start_ms < row.end_ms && end_ms > row.start_ms)) continue;
    rows.push({ start_ms, end_ms, title, selected: true, preview: firstLine(ordered, start_ms, end_ms) });
    if (rows.length === 3) break;
  }
  return rows.sort((a, b) => a.start_ms - b.start_ms);
}

/** Manual edits are checked without snapping, so the user's exact selection survives. */
export function buildRequest(outputPath: string, rows: readonly Row[], layout: string): { outputPath: string; items: Segment[]; layout: Layout } | string {
  const selected = rows.filter((row) => row.selected);
  if (!outputPath || selected.length < 1 || selected.length > 3) return "Chọn từ 1 đến 3 đoạn Shorts.";
  if (!["auto", "ban-tin", "mo-nen"].includes(layout)) return "Bố cục không hợp lệ.";
  for (const row of selected) {
    if (!validTitle(row.title)) return "Tiêu đề cần 1–80 ký tự và không chứa ký tự điều khiển.";
    if (!Number.isSafeInteger(row.start_ms) || !Number.isSafeInteger(row.end_ms) || row.start_ms < 0
      || row.end_ms - row.start_ms < 5000 || row.end_ms - row.start_ms > 60000) return "Nhập thời gian mm:ss; mỗi đoạn dài 5–60 giây.";
  }
  if (selected.some((a, i) => selected.slice(i + 1).some((b) => a.start_ms < b.end_ms && a.end_ms > b.start_ms))) return "Các đoạn đã chọn không được chồng nhau.";
  return { outputPath, layout: layout as Layout, items: selected.map(({ start_ms, end_ms, title }) => ({ start_ms, end_ms, title: title.trim() })) };
}

export function parseStatus(value: unknown): ShortsStatus | null {
  if (!value || typeof value !== "object") return null;
  const status = value as ShortsStatus;
  if (!["RUNNING", "COMPLETED", "FAILED", "CANCELLED"].includes(status.state) || typeof status.message !== "string"
    || !Number.isInteger(status.done) || !Number.isInteger(status.total) || status.done < 0 || status.total < status.done
    || !Array.isArray(status.outputs) || !status.outputs.every((o) => o && [o.path, o.text_path, o.title, o.text].every((s) => typeof s === "string"))) return null;
  return status;
}
