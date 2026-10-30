import type { QueueJob } from "../queue/model.ts";

export type CompletedVideo = Pick<QueueJob, "id" | "displayName" | "outputPath" | "status"> & { durationMs?: number };
export type ItemForm = {
  id: string; video: string; title: string; lead: string; score: string; pros: string; cons: string; start: string; end: string;
};
export type CompilationItem = {
  video: string; title: string; lead: string; score: number | null; pros: string[]; cons: string[]; startMs: number | null; endMs: number | null;
};
export type CompilationRequest = {
  title: string; layout: "bang-thong-tin" | "toan-man-hinh"; intro: string; outro: string; items: CompilationItem[];
  voiceId: string; speed: number; music: string | null; musicVolume: number; outputRoot: string | null; cpuOnly: boolean;
};
export type CompilationStatus = {
  state: "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED"; message: string; done: number; total: number; path: string | null;
};
export const count = (text: string): number => [...text].length;
const CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u;
const validText = (text: string, limit: number, required = false, spoken = false): boolean =>
  count(text) <= limit && !CONTROL.test(text) && (!required || Boolean(text.trim()))
  && (!spoken || !text.trim() || /[\p{L}\p{N}]/u.test(text));

export function completedVideos(jobs: readonly CompletedVideo[]): CompletedVideo[] {
  return jobs.filter(job => job.status.state === "COMPLETED" && Boolean(job.outputPath));
}

export function newItem(job: CompletedVideo): ItemForm {
  return { id: job.id, video: job.outputPath!, title: [...job.displayName.replace(/\.[^.]+$/, "")].slice(0, 80).join(""),
    lead: "", score: "", pros: "", cons: "", start: "", end: "" };
}

export function moveItem<T>(items: readonly T[], index: number, delta: number): T[] {
  const result = [...items], target = index + delta;
  if (index >= 0 && index < result.length && target >= 0 && target < result.length) {
    [result[index], result[target]] = [result[target], result[index]];
  }
  return result;
}

export const lines = (value: string): string[] => value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);

/** Empty means no trim; invalid values stay errors instead of silently changing the cut. */
export function milliseconds(value: string): number | null | "invalid" {
  if (!value.trim()) return null;
  const number = Number(value), ms = Math.round(number * 1000);
  return Number.isFinite(number) && number >= 0 && Number.isSafeInteger(ms) ? ms : "invalid";
}

export function buildRequest(input: Omit<CompilationRequest, "items" | "layout"> & { layout: string; items: readonly ItemForm[] }): CompilationRequest | string {
  if (input.items.length < 2 || input.items.length > 30) return "Hãy chọn từ 2 đến 30 video đã hoàn tất.";
  if (!validText(input.title, 120, true)) return "Nhập tiêu đề, tối đa 120 ký tự.";
  if (!["bang-thong-tin", "toan-man-hinh"].includes(input.layout)) return "Bố cục không hợp lệ.";
  if (!validText(input.intro, 800, false, true) || !validText(input.outro, 800, false, true)) return "Mở đầu và kết thúc tối đa 800 ký tự, có nội dung để đọc.";
  if (!input.voiceId) return "Hãy chọn giọng đọc.";
  if (!Number.isFinite(input.speed) || input.speed < 1 || input.speed > 1.3) return "Tốc độ đọc từ 1,0 đến 1,3.";
  if (!Number.isFinite(input.musicVolume) || input.musicVolume < 0 || input.musicVolume > 1) return "Âm lượng nhạc từ 0 đến 100%.";
  if (input.music && !/\.(mp3|m4a|aac|wav|ogg|flac)$/i.test(input.music)) return "Chọn file nhạc MP3, M4A, AAC, WAV, OGG hoặc FLAC.";
  const items: CompilationItem[] = [];
  for (const [index, item] of input.items.entries()) {
    const pros = lines(item.pros), cons = lines(item.cons), startMs = milliseconds(item.start), endMs = milliseconds(item.end);
    const score = item.score.trim() ? Number(item.score) : null;
    if (!item.video || !validText(item.title, 80, true) || !validText(item.lead, 400, false, true)
        || (score !== null && (!Number.isInteger(score) || score < 0 || score > 10))
        || [pros, cons].some(values => values.length > 3 || values.some(value => !validText(value, 60, true)))
        || startMs === "invalid" || endMs === "invalid" || (endMs !== null && endMs <= (startMs ?? 0))) {
      return `Phần ${index + 1}: kiểm tra tiêu đề (80 ký tự), lời dẫn (400), điểm (0–10), ưu/nhược điểm (3 dòng, mỗi dòng 60 ký tự) và mốc cắt.`;
    }
    items.push({ video: item.video, title: item.title.trim(), lead: item.lead.trim(), score, pros, cons, startMs, endMs });
  }
  return { title: input.title.trim(), layout: input.layout as CompilationRequest["layout"], intro: input.intro.trim(), outro: input.outro.trim(), items,
    voiceId: input.voiceId, speed: input.speed, music: input.music, musicVolume: input.musicVolume, outputRoot: input.outputRoot, cpuOnly: input.cpuOnly };
}

export function parseStatus(value: unknown): CompilationStatus | null {
  const object = (value ?? {}) as Partial<CompilationStatus>;
  if (!["RUNNING", "COMPLETED", "FAILED", "CANCELLED"].includes(object.state as string) || typeof object.message !== "string") return null;
  if (object.state === "COMPLETED" && (typeof object.path !== "string" || !object.path)) return null;
  return { state: object.state as CompilationStatus["state"], message: object.message, path: typeof object.path === "string" ? object.path : null,
    done: Number.isInteger(object.done) && object.done! >= 0 ? object.done! : 0,
    total: Number.isInteger(object.total) && object.total! >= 0 ? object.total! : 0 };
}
