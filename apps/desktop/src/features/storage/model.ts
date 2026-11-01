/** Disk usage and cleanup (Issue #406): the host's report, the texts shown
 * for it, and the automatic clean's rules. */
import { sizeLabel } from "../fetch/progress.ts";

export type StorageCategoryId = "work" | "versions" | "profiles" | "link_cache";
export type StorageCategory = { id: StorageCategoryId; bytes: number; items: number; busy: boolean };
export type StorageReport = {
  categories: StorageCategory[];
  totalUsed: number;
  installFreeBytes: number | null;
  outputFreeBytes: number | null;
};
export type CleanResult = { freedBytes: number; skipped: string[] };
/** The queue's output folders: finished ones to clean, the rest never touched. */
export type QueueOutputs = { finished: string[]; running: string[] };
type JobLike = { status: { state: string }; outputPath: string | null; outputDir?: string };

export const AUTO_CLEAN_KEY = "dubflow.autoClean.v1";
/** First automatic clean after launch. */
export const AUTO_CLEAN_DELAY_MS = 60_000;
/** After a job finishes, once its files are closed. */
export const AFTER_JOB_DELAY_MS = 10_000;
export const AUTO_CLEAN_DAYS = 3;
/** Below this much free space on the output drive, finished work goes at once. */
export const LOW_SPACE_BYTES = 15 * 1_073_741_824;
/** The automatic clean speaks up only when it freed more than this. */
export const NOTICE_MIN_BYTES = 1_073_741_824;
export const AUTO_CATEGORIES: readonly StorageCategoryId[] = ["work", "versions", "profiles"];
export const CATEGORY_ORDER: readonly StorageCategoryId[] = ["work", "versions", "profiles", "link_cache"];

export const CATEGORY_TEXT: Record<StorageCategoryId, { title: string; detail: string }> = {
  work: {
    title: "Tệp tạm của video đã xong",
    detail: "Âm thanh trung gian, bộ đệm giọng đọc và bản dựng tạm khi đóng gói. Video kết quả, phụ đề, dự án chỉnh sửa và bản dịch được giữ; sửa bản dịch, đóng gói, Shorts và đổi khung vẫn dùng được, chỉ chạy lại lâu hơn một chút.",
  },
  versions: { title: "Bản cài cũ", detail: "Giữ bản hiện tại và 1 bản trước để quay lại." },
  profiles: { title: "Dữ liệu trình duyệt của bản cũ", detail: "Thư mục trình duyệt của các bản cài đã gỡ hoặc không giữ lại." },
  link_cache: { title: "Bộ đệm tải link", detail: "Phần tải dở và tệp tạm khi tải video từ link; video đã tải về vẫn giữ." },
};

const number = (value: unknown): number => (typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0);
const optionalNumber = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null);

export function parseReport(value: unknown): StorageReport | null {
  const object = (value ?? {}) as Record<string, unknown>;
  if (!Array.isArray(object.categories)) return null;
  const categories = CATEGORY_ORDER.map((id): StorageCategory => {
    const raw = (object.categories as unknown[]).find((item) => (item as Record<string, unknown> | null)?.id === id) as Record<string, unknown> | undefined;
    return { id, bytes: number(raw?.bytes), items: number(raw?.items), busy: raw?.busy === true };
  });
  return {
    categories, totalUsed: number(object.total_used),
    installFreeBytes: optionalNumber(object.install_free_bytes), outputFreeBytes: optionalNumber(object.output_free_bytes),
  };
}

export function parseClean(value: unknown): CleanResult {
  const object = (value ?? {}) as Record<string, unknown>;
  const skipped = Array.isArray(object.skipped) ? object.skipped.filter((item): item is string => typeof item === "string").slice(0, 50) : [];
  return { freedBytes: number(object.freed_bytes), skipped };
}

/** Bytes the categories that can be cleaned now would free. */
export function cleanableBytes(report: StorageReport, chosen?: ReadonlySet<StorageCategoryId>): number {
  return report.categories.filter((category) => !category.busy && (!chosen || chosen.has(category.id)))
    .reduce((total, category) => total + category.bytes, 0);
}

/** "DubFlow đang dùng 14,2 GB · có thể dọn 9,8 GB" */
export function summaryLine(report: StorageReport): string {
  return `DubFlow đang dùng ${sizeLabel(report.totalUsed)} · có thể dọn ${sizeLabel(cleanableBytes(report))}`;
}

/** What a checkbox row shows on the right. */
export function categorySize(category: StorageCategory): string {
  return category.busy ? "Đang tải link" : sizeLabel(category.bytes);
}

/** Pre-selected: everything that has something to free and is not busy. */
export function defaultSelection(report: StorageReport): Set<StorageCategoryId> {
  return new Set(report.categories.filter((category) => !category.busy && category.bytes > 0).map((category) => category.id));
}

export function cleanButtonLabel(bytes: number): string {
  return `Dọn ${sizeLabel(bytes)}`;
}

export function resultLine(result: CleanResult): string {
  return `Đã giải phóng ${sizeLabel(result.freedBytes)}`;
}

/** The automatic clean's age limit: 3 days, or now when the drive is low. */
export function autoCleanDays(freeBytes: number | null): number {
  return freeBytes !== null && freeBytes < LOW_SPACE_BYTES ? 0 : AUTO_CLEAN_DAYS;
}

/** On by default; only an explicit "0" turns it off. */
export function autoCleanEnabled(stored: string | null): boolean {
  return stored !== "0";
}

/** The queue notice after an automatic clean, or null to stay silent. */
export function autoNotice(freedBytes: number): string | null {
  return freedBytes > NOTICE_MIN_BYTES ? `Đã tự dọn dẹp, giải phóng ${sizeLabel(freedBytes)}` : null;
}

/** The folder holding a file, for Windows or POSIX paths. */
export function parentFolder(path: string): string {
  const index = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  return index > 0 ? path.slice(0, index) : path;
}

/**
 * Finished jobs' folders may be cleaned. Every other job that has a folder
 * (running, queued again, failed and resumable) is passed as running, so its
 * work files are never touched.
 */
export function queueOutputs(jobs: readonly JobLike[]): QueueOutputs {
  const finished = new Set<string>();
  const running = new Set<string>();
  for (const job of jobs) {
    const folder = job.outputDir ?? (job.outputPath ? parentFolder(job.outputPath) : null);
    if (!folder) continue;
    (job.status.state === "COMPLETED" ? finished : running).add(folder);
  }
  for (const folder of running) finished.delete(folder);
  return { finished: [...finished], running: [...running] };
}
