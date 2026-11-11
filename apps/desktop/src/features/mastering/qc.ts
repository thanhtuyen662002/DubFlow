/**
 * The "Hậu kỳ" card of a finished Lồng tiếng video: what Tối ưu hậu kỳ tự động did, read from the export's own QC
 * (`dubbing_qc`), nothing recomputed. A climax suggestion is only kept or dropped here, never put into the video.
 */
import { LEVEL_NAMES, isLevel } from "./model.ts";

export const TRUE_PEAK_CEILING = -1.5;

type Loud = { integrated: number; true_peak: number; lra: number };
export type DubbingQc = {
  level: keyof typeof LEVEL_NAMES;
  notes: string[];
  loudness: boolean;
  filters: number;
  before: Loud | null;
  after: Loud | null;
  climax: { start_ms: number; end_ms: number } | null;
  climaxDecision: "keep" | "drop" | null;
  fallback: boolean;
};

function loud(value: unknown): Loud | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const fields = ["integrated", "true_peak", "lra"] as const;
  if (!fields.every((key) => typeof item[key] === "number" && Number.isFinite(item[key]))) return null;
  return { integrated: item.integrated as number, true_peak: item.true_peak as number, lra: item.lra as number };
}

/** The command's answer, checked; null when the video was made with mastering off. */
export function parseDubbingQc(value: unknown): DubbingQc | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  if (!isLevel(item.level)) return null;
  const notes = Array.isArray(item.notes) ? item.notes.filter((note): note is string => typeof note === "string").slice(0, 40) : [];
  const measured = item.measured && typeof item.measured === "object" ? item.measured as Record<string, unknown> : {};
  const climax = item.climax && typeof item.climax === "object" ? item.climax as Record<string, unknown> : null;
  const span = climax && typeof climax.start_ms === "number" && typeof climax.end_ms === "number" && climax.end_ms > climax.start_ms
    ? { start_ms: climax.start_ms, end_ms: climax.end_ms } : null;
  return {
    level: item.level,
    notes,
    loudness: item.loudness === true,
    filters: Array.isArray(item.video_filters) ? item.video_filters.length : 0,
    before: loud(measured.mix),
    after: loud(item.output_loudness),
    climax: span,
    climaxDecision: item.climax_decision === "keep" || item.climax_decision === "drop" ? item.climax_decision : null,
    fallback: item.fallback === true,
  };
}

const number = (value: number): string => value.toFixed(1).replace(".", ",").replace("-", "−");

/** One row per thing mastering looked at: label, what happened, and whether it needs a look. */
export function qcRows(qc: DubbingQc): { label: string; text: string; tone: "ok" | "warn" | "info" }[] {
  const note = (prefix: string): string | undefined => {
    const text = qc.notes.find((item) => item.startsWith(prefix))?.slice(prefix.length).trim();
    return text ? text[0].toUpperCase() + text.slice(1) : undefined;
  };
  const rows: { label: string; text: string; tone: "ok" | "warn" | "info" }[] = [];
  rows.push({ label: "Mức", text: LEVEL_NAMES[qc.level], tone: "info" });
  if (qc.fallback) {
    rows.push({ label: "Quay về", text: "Bản lồng tiếng không qua kiểm tra nên video dùng âm thanh gốc (B1); phụ đề theo thời gian nguồn.", tone: "warn" });
  }
  const before = qc.before ? `${number(qc.before.integrated)} LUFS, đỉnh ${number(qc.before.true_peak)} dBTP` : "chưa đo";
  if (qc.loudness && qc.after) {
    const over = qc.after.true_peak > TRUE_PEAK_CEILING;
    rows.push({
      label: "Âm lượng",
      text: `Trước ${before} → sau ${number(qc.after.integrated)} LUFS, đỉnh ${number(qc.after.true_peak)} dBTP sau AAC`
        + (over ? ` (vượt trần ${number(TRUE_PEAK_CEILING)} dBTP)` : ` (trần ${number(TRUE_PEAK_CEILING)} dBTP: đạt)`)
        // rc2 Windows check (D5): why the result is quieter than the target, from the planner's own note
        + (note("Âm lượng:")?.includes("thấp hơn mục tiêu") ? ". Thấp hơn mục tiêu vì đỉnh của bản trộn đã cao: chỉ tăng/giảm tuyến tính, không nén." : ""),
      tone: over ? "warn" : "ok",
    });
  } else if (qc.loudness) {
    rows.push({ label: "Âm lượng", text: `Đã chuẩn hóa từ ${before}; không đo lại được sau khi xuất.`, tone: "warn" });
  } else {
    rows.push({ label: "Âm lượng", text: note("Âm lượng:") ?? "Giữ nguyên.", tone: "info" });
  }
  for (const [prefix, label] of [["Màu:", "Màu"], ["Khung hình:", "Khung hình"], ["Phụ đề:", "Phụ đề"]] as const) {
    const text = note(prefix);
    if (text) rows.push({ label, text, tone: "info" });
  }
  return rows;
}

export function climaxText(qc: DubbingQc): string {
  if (!qc.climax) return "";
  const at = (ms: number): string => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;
  const state = qc.climaxDecision === "keep" ? "Bạn đã giữ gợi ý (chưa chèn vào video)."
    : qc.climaxDecision === "drop" ? "Bạn đã bỏ gợi ý." : "Chỉ là gợi ý, không chèn vào video.";
  return `Gợi ý đoạn cao trào ${at(qc.climax.start_ms)}–${at(qc.climax.end_ms)} làm teaser. ${state}`;
}
