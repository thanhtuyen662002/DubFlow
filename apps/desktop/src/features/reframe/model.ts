import { parseTime } from "../shorts/model.ts";

export type Mode = "bang-thong-tin" | "cat-ngang";
export type Form = { mode: string; title: string; score: string; pros: string; cons: string; summary: string;
  focusStart: number; focusEnd: number; start: string; end: string };
export type Request = { outputPath: string; mode: Mode; title: string; score: number | null; pros: string[]; cons: string[];
  summary: string; focusStart: number; focusEnd: number; startMs: number; endMs: number | null };
export type Info = { width: number; height: number; vertical: boolean };
export type ReframeStatus = { state: "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED"; message: string; path?: string; duration_ms?: number };

export const defaults = (): Form => ({ mode: "bang-thong-tin", title: "", score: "", pros: "", cons: "", summary: "",
  focusStart: 50, focusEnd: 50, start: "", end: "" });
const text = (value: string, limit: number): boolean => [...value].length <= limit && !/[\u0000-\u001f\u007f-\u009f]/u.test(value);

export function buildRequest(outputPath: string, form: Form): Request | string {
  if (!outputPath || !["bang-thong-tin", "cat-ngang"].includes(form.mode)) return "Chọn video dọc đã hoàn tất và chế độ hợp lệ.";
  if (!text(form.title, 80)) return "Tiêu đề tối đa 80 ký tự và không chứa ký tự điều khiển.";
  const panel = form.mode === "bang-thong-tin";
  const points = (value: string): string[] => value.split(/\r?\n/).filter(line => line.trim()).map(line => line.trim());
  const pros = panel ? points(form.pros) : [], cons = panel ? points(form.cons) : [];
  if ([pros, cons].some(lines => lines.length > 3 || lines.some(line => !text(line, 60)))) return "Ưu/nhược điểm tối đa 3 ý mỗi loại, mỗi ý tối đa 60 ký tự.";
  if (panel && !text(form.summary, 120)) return "Tóm tắt tối đa 120 ký tự và không chứa ký tự điều khiển.";
  if (panel && form.score.trim() && !/^(?:[0-9]|10)$/.test(form.score.trim())) return "Điểm phải là số nguyên từ 0 đến 10, hoặc để trống.";
  if ([form.focusStart, form.focusEnd].some(f => !Number.isFinite(f) || f < 0 || f > 100)) return "Vị trí phải từ 0 đến 100.";
  const startMs = form.start.trim() ? parseTime(form.start) : 0, endMs = form.end.trim() ? parseTime(form.end) : null;
  if (startMs === null || (form.end.trim() && endMs === null) || (endMs !== null && endMs <= startMs)) return "Nhập mốc cắt mm:ss; kết thúc phải sau bắt đầu. Để trống để dùng hết video.";
  return { outputPath, mode: form.mode as Mode, title: form.title.trim(), score: panel && form.score.trim() ? Number(form.score) : null,
    pros, cons, summary: panel ? form.summary.trim() : "", focusStart: form.focusStart, focusEnd: form.focusEnd, startMs, endMs };
}

/** Fractions of the displayed frame; the rectangle has the same free travel as FFmpeg. */
export function previewBand(width: number, height: number, focus: number): { top: number; height: number } {
  const band = Math.min(1, width * 9 / 16 / height);
  return { top: (1 - band) * Math.max(0, Math.min(100, focus)) / 100, height: band };
}

export function parseInfo(value: unknown): Info | null {
  const info = value as Info | null;
  return info && Number.isSafeInteger(info.width) && info.width > 0 && Number.isSafeInteger(info.height) && info.height > 0
    && info.vertical === (info.height > info.width) ? info : null;
}

export function parseStatus(value: unknown): ReframeStatus | null {
  const status = value as ReframeStatus | null;
  if (!status || !["RUNNING", "COMPLETED", "FAILED", "CANCELLED"].includes(status.state) || typeof status.message !== "string") return null;
  if (status.state === "COMPLETED" && (typeof status.path !== "string" || !status.path
    || !Number.isSafeInteger(status.duration_ms) || status.duration_ms! <= 0)) return null;
  return status;
}
