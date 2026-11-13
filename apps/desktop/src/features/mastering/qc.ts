/**
 * The "Hậu kỳ" card of a finished Lồng tiếng video: what Tối ưu hậu kỳ tự động did, read from the export's own QC
 * (`dubbing_qc`), nothing recomputed. A climax suggestion is only kept or dropped here, never put into the video.
 */
import { LEVEL_NAMES, isLevel } from "./model.ts";

export const TRUE_PEAK_CEILING = -1.5;

type Loud = { integrated: number; true_peak: number; lra: number };

const CONFORMANCE_STATUSES = ["passed", "corrected", "failed", "unmeasured", "silent", "not_applicable"] as const;
const CONFORMANCE_PATHS = ["B2", "B1", "none"] as const;
/** Issue #821: the export's true peak judged by the engine against the ceiling after AAC, and the trim it took. */
export type Conformance = {
  status: (typeof CONFORMANCE_STATUSES)[number];
  path: (typeof CONFORMANCE_PATHS)[number];
  truePeak: number | null;
  trimDb: number;
  tries: number;
  ceilingMet: boolean | null;
  /** B1 / Vietsub only: the export could not be measured (B2 says so with status "unmeasured"). */
  unmeasured: boolean;
};

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

/** The QC's conformance record, checked strictly: anything unknown or inconsistent is null (nothing is shown). */
function conformance(value: unknown): Conformance | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  const status = CONFORMANCE_STATUSES.find((known) => known === item.status);
  const path = CONFORMANCE_PATHS.find((known) => known === item.path);
  if (!status || !path || item.ceiling_dbtp !== TRUE_PEAK_CEILING) return null;
  if (item.true_peak_dbtp !== null && !finite(item.true_peak_dbtp)) return null;
  if (!finite(item.trim_db) || item.trim_db < 0 || !Number.isInteger(item.tries) || (item.tries as number) < 0) return null;
  const ceilingMet = item.ceiling_met === undefined || item.ceiling_met === null ? null : item.ceiling_met;
  if (ceilingMet !== null && typeof ceilingMet !== "boolean") return null;
  const truePeak = item.true_peak_dbtp as number | null;
  const judged = status === "passed" || status === "corrected" || status === "failed";
  if ((status === "not_applicable") === (path === "B2")) return null;
  if (judged && (truePeak === null || (truePeak > TRUE_PEAK_CEILING) !== (status === "failed"))) return null;
  if (status === "corrected" && item.trim_db === 0) return null;
  if (item.unmeasured !== undefined && typeof item.unmeasured !== "boolean") return null;
  const unmeasured = item.unmeasured === true;
  if (unmeasured && (path === "B2" || truePeak !== null || ceilingMet !== null)) return null;
  return { status, path, truePeak, trimDb: item.trim_db, tries: item.tries as number, ceilingMet, unmeasured };
}

export type DubbingQc = {
  level: keyof typeof LEVEL_NAMES;
  notes: string[];
  loudness: boolean;
  filters: number;
  before: Loud | null;
  after: Loud | null;
  conformance: Conformance | null;
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
    conformance: conformance(item.conformance),
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
  const ceiling = `${number(TRUE_PEAK_CEILING)} dBTP`;
  const check = qc.conformance;
  // issue #821: what the engine did and found after AAC, on top of the measurement itself
  const failed = check?.status === "failed";
  const afterAac = check?.status === "corrected"
    ? `. Đã giảm thêm ${number(check.trimDb)} dB sau AAC để đỉnh ≤ ${ceiling}`
    : failed ? `. Không đạt trần ${ceiling} sau AAC` + (check.trimDb > 0 ? ` dù đã giảm thêm ${number(check.trimDb)} dB qua ${check.tries} lần thử mã hóa`
      // every trim was tried and none measured lower: the untrimmed mix was kept
      : check.tries > 1 ? ` dù đã thử giảm ${check.tries - 1} lần (không lần nào hạ được đỉnh nên giữ bản chưa giảm)` : "")
    : "";
  const unmeasured = check?.status === "unmeasured" || check?.unmeasured === true;
  // B1 is the source sound, which mastering never touches: its measurement is said to be under or over the ceiling,
  // never "đạt" (the record says not_applicable: no mastering result to pass)
  const b1 = check?.path === "B1";
  const against = (over: boolean) => over ? ` (vượt trần ${ceiling})` : b1 ? ` (dưới trần ${ceiling})` : ` (trần ${ceiling}: đạt)`;
  const b1Said = b1 ? ". Đây là âm thanh gốc (B1), không được chỉnh" : "";
  if (check?.path === "none" && qc.after) {
    // Vietsub only: the sound is the source's, never mastered, so the ceiling does not apply (the engine judges
    // nothing and warns nothing): a measurement only, neither "vượt trần" nor "đạt"
    rows.push({
      label: "Âm lượng",
      text: `Vietsub: âm thanh gốc, không áp trần ${ceiling}. Đo bản xuất: ${number(qc.after.integrated)} LUFS, đỉnh ${number(qc.after.true_peak)} dBTP sau AAC`,
      tone: "info",
    });
  } else if (qc.loudness && qc.after) {
    const over = qc.after.true_peak > TRUE_PEAK_CEILING || failed;
    rows.push({
      label: "Âm lượng",
      text: `Trước ${before} → sau ${number(qc.after.integrated)} LUFS, đỉnh ${number(qc.after.true_peak)} dBTP sau AAC`
        + against(over) + afterAac + b1Said
        // rc2 Windows check (D5): why the result is quieter than the target, from the planner's own note
        + (note("Âm lượng:")?.includes("thấp hơn mục tiêu") ? ". Thấp hơn mục tiêu vì đỉnh của bản trộn đã cao: chỉ tăng/giảm tuyến tính, không nén." : ""),
      tone: over ? "warn" : b1 ? "info" : "ok",
    });
  } else if (qc.loudness) {
    rows.push({
      label: "Âm lượng",
      text: unmeasured ? `Đã chuẩn hóa từ ${before}; không đo được sau AAC nên chưa xác nhận trần ${ceiling}.`
        : `Đã chuẩn hóa từ ${before}; không đo lại được sau khi xuất.`,
      tone: "warn",
    });
  } else if (qc.after) {
    // issue #715: no normalisation was applied (none needed, or the B1 fallback), but the final video was measured
    const over = qc.after.true_peak > TRUE_PEAK_CEILING || failed;
    rows.push({
      label: "Âm lượng",
      text: `${(note("Âm lượng:") ?? "Không chuẩn hóa").replace(/\.$/, "")}. Đo bản xuất: ${number(qc.after.integrated)} LUFS, đỉnh ${number(qc.after.true_peak)} dBTP sau AAC`
        // issue #821: the B1 fallback is the source sound, which mastering never trims
        + against(over) + afterAac + b1Said,
      tone: over ? "warn" : "info", // nothing was normalised: a measurement, not a result of mastering
    });
  } else if (unmeasured) {
    const kept = check?.path === "B1" ? "Âm thanh gốc (B1), không được chỉnh" : (note("Âm lượng:") ?? "Giữ nguyên").replace(/\.$/, "");
    // Vietsub only: mastering does not touch the sound, so an unmeasured export is said, not flagged
    rows.push({ label: "Âm lượng", text: `${kept}; không đo được sau AAC nên chưa xác nhận trần ${ceiling}.`, tone: check?.path === "none" ? "info" : "warn" });
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
