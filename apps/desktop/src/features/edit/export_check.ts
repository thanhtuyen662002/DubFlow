/**
 * Kiểm tra kỹ thuật (Issue #935): the advisory check the host runs on each
 * editor export once it is saved (apps/desktop/src-tauri/src/edit_check.rs).
 * It is shown next to the export and never gates anything: it has no passed
 * or READY state, and its words never say "đạt", "Sẵn sàng" or "Kiểm tra
 * cuối", so it cannot be mistaken for Final QC.
 */
import type { QcCheck } from "../mastering/model.ts";

export type CheckState = "RUNNING" | "DONE" | "STOPPED" | "INTERRUPTED" | "UNAVAILABLE";
/** `waiting`: RUNNING but queued behind another check (the host checks one video at a time). */
export interface ExportCheck { state: CheckState; percent: number; checks: QcCheck[]; error: string; waiting: boolean }
export type CheckTone = "ok" | "warn" | "info";

const STATES: readonly CheckState[] = ["RUNNING", "DONE", "STOPPED", "INTERRUPTED", "UNAVAILABLE"];
const MAX_CHECKS = 40;
const MAX_DETAIL = 400;

/** Footer and tooltip of every check. */
export const CHECK_NOTE = "Chỉ để tham khảo: kiểm tra này không chặn, không xóa và không thay đổi video.";

/** The host's check, or null when it is not one (then nothing is shown). */
export function parseExportCheck(raw: unknown): ExportCheck | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Record<string, unknown>;
  if (value.kind !== "export_check" || value.advisory !== true || !STATES.includes(value.state as CheckState)) return null;
  if (!Array.isArray(value.checks)) return null;
  const checks: QcCheck[] = [];
  for (const item of (value.checks as unknown[]).slice(0, MAX_CHECKS)) {
    const check = (item ?? {}) as Record<string, unknown>;
    // a check that is neither fine, a warning nor "not measured" makes the whole answer unreadable
    if (typeof check.id !== "string" || !(check.ok === true || check.ok === false || check.ok === null)) return null;
    checks.push({ id: check.id, label: typeof check.label === "string" ? check.label : check.id, ok: check.ok as boolean | null,
                  detail: typeof check.detail === "string" ? check.detail.slice(0, MAX_DETAIL) : "" });
  }
  const percent = typeof value.percent === "number" && Number.isFinite(value.percent) ? Math.min(100, Math.max(0, value.percent)) : 0;
  const error = typeof value.error === "string" ? value.error.slice(0, MAX_DETAIL) : "";
  return { state: value.state as CheckState, percent, checks, error, waiting: value.state === "RUNNING" && value.waiting === true };
}

/** One line for the check, and its tone. */
export function checkSummary(check: ExportCheck): { text: string; tone: CheckTone } {
  switch (check.state) {
    case "RUNNING":
      if (check.waiting) return { text: "Đang chờ kiểm tra kỹ thuật (mỗi lần kiểm tra một video) — video đã lưu và dùng được ngay.", tone: "info" };
      // While it runs FFmpeg reads the file, and Windows then refuses to rename, move or delete it (#935, recheck 11).
      return { text: `Đang kiểm tra kỹ thuật… ${Math.floor(check.percent)}% — video đã lưu và xem được ngay; muốn đổi tên, di chuyển hay xóa tệp thì bấm Dừng kiểm tra trước.`, tone: "info" };
    case "STOPPED":
      return { text: "Đã dừng kiểm tra kỹ thuật. Video vẫn được giữ nguyên.", tone: "info" };
    case "INTERRUPTED":
      return { text: "Kiểm tra kỹ thuật chưa xong vì DubFlow đã đóng. Video vẫn được giữ nguyên.", tone: "info" };
    case "UNAVAILABLE":
      return { text: `Không chạy được kiểm tra kỹ thuật (${check.error || "không rõ lỗi"}). Video vẫn được giữ nguyên.`, tone: "info" };
    case "DONE": {
      const warnings = checkWarnings(check);
      // not measured is named, never counted as fine, and never hidden behind a warning (#935, Lead review)
      const unmeasured = check.checks.filter((item) => item.ok === null).map((item) => item.label);
      const notMeasured = unmeasured.length ? ` Chưa đo: ${unmeasured.join(", ")}.` : "";
      if (warnings) return { text: `Kiểm tra kỹ thuật: ${warnings} điểm cần xem.${notMeasured}`, tone: "warn" };
      return { text: `Kiểm tra kỹ thuật: không thấy lỗi.${notMeasured}`, tone: "ok" };
    }
  }
}

/** How many points of a finished check are worth a look (not measured is not counted here). */
export function checkWarnings(check: ExportCheck): number {
  return check.state === "DONE" ? check.checks.filter((item) => item.ok === false).length : 0;
}

/** "{nhãn}: {chi tiết}" for each point to look at, then "Chưa đo — {nhãn}: {lý do}" for each check that was not measured. */
export function checkLines(check: ExportCheck): string[] {
  if (check.state !== "DONE") return [];
  const warnings = check.checks.filter((item) => item.ok === false).map((item) => `${item.label}: ${item.detail}`);
  const unmeasured = check.checks.filter((item) => item.ok === null)
    .map((item) => `Chưa đo — ${item.label}${item.detail ? `: ${item.detail}` : ""}`);
  return [...warnings, ...unmeasured];
}

/** Whether some export's check still runs, so its result is worth polling for. */
export function checkRunning(exports: ReadonlyArray<{ check?: unknown }>): boolean {
  return exports.some((entry) => parseExportCheck(entry.check)?.state === "RUNNING");
}

/** The check line under an export in a list: its text, classes and tooltip; null when there is no readable check. */
export function checkBadge(raw: unknown): { text: string; className: string; title: string } | null {
  const check = parseExportCheck(raw);
  if (!check) return null;
  const { text, tone } = checkSummary(check);
  const className = `edit-export-check${tone === "warn" ? " mqc-warn" : tone === "ok" ? " mqc-ok" : ""}`;
  return { text, className, title: [text, ...checkLines(check), CHECK_NOTE].join("\n") };
}
