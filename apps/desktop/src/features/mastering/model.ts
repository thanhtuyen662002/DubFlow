/**
 * Tối ưu hậu kỳ tự động (Phase 5 MVP): the level picker's choices and texts per
 * workflow, and the review panel's data as the engine prints it
 * (`python -m engine.dubflow.mastering.review`). The planner's verdict and the
 * user's decision are kept apart: an approval never reads as "AI got it right".
 */

export type Level = "off" | "light" | "balanced" | "dynamic";
export type Workflow = "dubbing" | "product" | "compilation";
export const LEVELS: readonly Level[] = ["off", "light", "balanced", "dynamic"];
export const LEVEL_NAMES: Record<Level, string> = { off: "Tắt", light: "Nhẹ", balanced: "Cân bằng", dynamic: "Mạnh" };

/** What each level does, in each workflow: only what the engine really does there. */
const LEVEL_TEXT: Record<Workflow, Record<Level, string>> = {
  dubbing: {
    off: "Xuất như cũ, không phân tích thêm.",
    light: "Chuẩn hóa âm lượng bản lồng tiếng (tuyến tính, không nén) và chỉnh màu an toàn khi hình bị xỉn.",
    balanced: "Như Nhẹ, thêm bỏ viền đen bốn phía khi có, và phụ đề canh theo lúc giọng Việt được đọc.",
    dynamic: "Như Cân bằng, giới hạn màu rộng hơn; có thể gợi ý đoạn cao trào (chỉ gợi ý, không chèn vào video).",
  },
  product: {
    off: "Ghép ảnh và clip như cũ.",
    light: "Chọn cảnh theo nội dung từng câu, giữ thứ tự clip; mỗi cảnh nằm trọn trong một shot.",
    balanced: "Như Nhẹ, được đổi thứ tự cảnh để câu nào cũng có hình hợp, cảnh mở đầu nổi bật.",
    dynamic: "Như Cân bằng, nhịp cắt nhanh hơn trong giới hạn an toàn.",
  },
  compilation: {
    off: "Ghép như cũ.",
    light: "Chọn đoạn theo lời dẫn, giữ thứ tự và thứ hạng chương trình.",
    balanced: "Như Nhẹ, thêm đoạn mở đầu gợi tò mò lấy từ chính các nguồn.",
    dynamic: "Như Cân bằng, nhịp cắt nhanh hơn trong giới hạn an toàn; không đổi thứ hạng.",
  },
};

export function isLevel(value: unknown): value is Level {
  return typeof value === "string" && (LEVELS as readonly string[]).includes(value);
}

export function levelText(workflow: Workflow, level: Level): string {
  return LEVEL_TEXT[workflow][level];
}

/** The planner's verdict on a sentence, and the user's word, as the panel says them. */
export const STATE_LABELS: Record<string, { text: string; tone: "ok" | "warn" | "bad" | "info" }> = {
  READY: { text: "Đạt", tone: "ok" },
  NEEDS_REVIEW: { text: "Cần xem lại", tone: "warn" },
  MATCH_NOT_FOUND: { text: "Chưa tìm thấy cảnh khớp", tone: "bad" },
  SOURCE_INSUFFICIENT: { text: "Nguồn không có cảnh này", tone: "bad" },
  USER_APPROVED: { text: "Bạn đã duyệt", tone: "info" },
  INVALIDATED: { text: "Cần chọn lại", tone: "bad" },
};

export const EVIDENCE_HELP: Record<string, string> = {
  MATCH_AVAILABLE: "Có cảnh khớp lời câu này.",
  MATCH_NOT_FOUND: "Chưa tìm thấy cảnh khớp. Footage có thể vẫn có: hãy xem các cảnh khác hoặc chọn tay.",
  SOURCE_INSUFFICIENT: "Mọi cảnh dùng được đều đã được xem và không cảnh nào khớp: nguồn có vẻ không quay nội dung này.",
  NOT_NEEDED: "Câu mở đầu/kêu gọi: không cần cảnh riêng.",
};

export type Shot = {
  id: string; source_id: string; start_ms: number; end_ms: number; keyframe: string | null; summary: string; usable: boolean;
  problems: string[]; score?: number; reasons?: string[];
};
export type Piece = {
  segment_id: string; source_id: string; role: string; source_start: number; source_end: number;
  timeline_start: number; timeline_end: number; confidence: number | null; needs_review: boolean; reasons: string[];
};
export type Decision = { pin: string[]; exclude: string[]; approved?: string[]; invalidated?: string };
export type ReviewUnit = {
  unit_id: string; text: string; function: string; start_ms: number; end_ms: number;
  coverage: { milli?: number; status?: string; need_ms?: number; illustrated_ms?: number };
  notes: string[]; evidence: string; state: string; user_state: string | null; decision: Decision | null;
  chosen: Shot[]; pieces: Piece[]; alternatives: Shot[]; unreviewed: boolean;
};
export type ReviewWarning = { id: string; text: string; acknowledged: boolean };
export type ReviewView = {
  plan_id: string; mode: string; revision: number; can_undo: boolean; units: ReviewUnit[]; warnings: ReviewWarning[];
  unreviewed: string[]; accept_unreviewed: boolean; summary: Record<string, number>;
};

const isString = (value: unknown): value is string => typeof value === "string";
const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const strings = (value: unknown, limit = 50): string[] => Array.isArray(value) ? value.filter(isString).slice(0, limit) : [];

function shot(value: unknown): Shot | null {
  const item = (value ?? {}) as Record<string, unknown>;
  if (!isString(item.id) || !isString(item.source_id) || !isNumber(item.start_ms) || !isNumber(item.end_ms)) return null;
  return {
    id: item.id, source_id: item.source_id, start_ms: item.start_ms, end_ms: item.end_ms,
    keyframe: isString(item.keyframe) ? item.keyframe : null, summary: isString(item.summary) ? item.summary : "",
    usable: item.usable !== false, problems: strings(item.problems),
    ...(isNumber(item.score) ? { score: item.score } : {}), ...(Array.isArray(item.reasons) ? { reasons: strings(item.reasons, 8) } : {}),
  };
}

function piece(value: unknown): Piece | null {
  const item = (value ?? {}) as Record<string, unknown>;
  const numbers = ["source_start", "source_end", "timeline_start", "timeline_end"] as const;
  if (!isString(item.source_id) || numbers.some((key) => !isNumber(item[key]))) return null;
  return {
    segment_id: isString(item.segment_id) ? item.segment_id : "", source_id: item.source_id, role: isString(item.role) ? item.role : "",
    source_start: item.source_start as number, source_end: item.source_end as number,
    timeline_start: item.timeline_start as number, timeline_end: item.timeline_end as number,
    confidence: isNumber(item.confidence) ? item.confidence : null, needs_review: item.needs_review === true, reasons: strings(item.reasons, 16),
  };
}

/** The engine's view, checked field by field; anything malformed is dropped, never trusted. */
export function parseReview(value: unknown): ReviewView | null {
  const object = (value ?? {}) as Record<string, unknown>;
  if (object.kind !== "editorial_review_view" || !Array.isArray(object.units)) return null;
  const units: ReviewUnit[] = [];
  for (const raw of object.units) {
    const unit = (raw ?? {}) as Record<string, unknown>;
    if (!isString(unit.unit_id) || !isString(unit.state)) continue;
    const decision = (unit.decision ?? null) as Record<string, unknown> | null;
    units.push({
      unit_id: unit.unit_id, text: isString(unit.text) ? unit.text : "", function: isString(unit.function) ? unit.function : "",
      start_ms: isNumber(unit.start_ms) ? unit.start_ms : 0, end_ms: isNumber(unit.end_ms) ? unit.end_ms : 0,
      coverage: (unit.coverage && typeof unit.coverage === "object" ? unit.coverage : {}) as ReviewUnit["coverage"],
      notes: strings(unit.notes, 8), evidence: isString(unit.evidence) ? unit.evidence : "", state: unit.state,
      user_state: isString(unit.user_state) ? unit.user_state : null,
      decision: decision ? {
        pin: strings(decision.pin), exclude: strings(decision.exclude),
        ...(Array.isArray(decision.approved) ? { approved: strings(decision.approved) } : {}),
        ...(isString(decision.invalidated) ? { invalidated: decision.invalidated } : {}),
      } : null,
      chosen: (Array.isArray(unit.chosen) ? unit.chosen : []).map(shot).filter((item): item is Shot => item !== null),
      pieces: (Array.isArray(unit.pieces) ? unit.pieces : []).map(piece).filter((item): item is Piece => item !== null),
      alternatives: (Array.isArray(unit.alternatives) ? unit.alternatives : []).map(shot).filter((item): item is Shot => item !== null).slice(0, 3),
      unreviewed: unit.unreviewed === true,
    });
  }
  const warnings = (Array.isArray(object.warnings) ? object.warnings : []).flatMap((raw) => {
    const item = (raw ?? {}) as Record<string, unknown>;
    return isString(item.id) && isString(item.text) ? [{ id: item.id, text: item.text, acknowledged: item.acknowledged === true }] : [];
  });
  const summary: Record<string, number> = {};
  for (const [key, count] of Object.entries((object.summary ?? {}) as Record<string, unknown>)) if (isNumber(count)) summary[key] = count;
  return {
    plan_id: isString(object.plan_id) ? object.plan_id : "", mode: isString(object.mode) ? object.mode : "",
    revision: isNumber(object.revision) ? object.revision : 0, can_undo: object.can_undo === true, units, warnings,
    unreviewed: strings(object.unreviewed, 500), accept_unreviewed: object.accept_unreviewed === true, summary,
  };
}

/** The badge of a sentence: the user's word when there is one, else the planner's verdict. */
export function unitBadge(unit: ReviewUnit): { text: string; tone: string } {
  if (unit.user_state === "INVALIDATED") return STATE_LABELS.INVALIDATED;
  if (unit.user_state === "USER_APPROVED") return STATE_LABELS.USER_APPROVED;
  if (unit.decision?.pin.length) return { text: "Bạn đã chọn cảnh", tone: "info" };
  return STATE_LABELS[unit.state] ?? { text: unit.state, tone: "warn" };
}

export function coverageText(unit: ReviewUnit): string {
  if (unit.coverage.status === "generic") return "Không cần cảnh riêng";
  const milli = unit.coverage.milli;
  return typeof milli === "number" ? `Hình khớp ${Math.round(milli / 10)} % thời lượng câu` : "";
}

export function seconds(ms: number): string {
  const total = Math.max(0, Math.round(ms / 100)) / 10;
  const minutes = Math.floor(total / 60);
  const rest = (total - minutes * 60).toFixed(1).replace(".", ",");
  return minutes ? `${minutes}:${rest.padStart(4, "0")}` : `${rest} s`;
}

/** One line for a video's result row: how much of it is unreviewed and how many warnings are unread. */
export function reviewSummary(view: ReviewView): string {
  const open = view.unreviewed.length;
  const unread = view.warnings.filter((warning) => !warning.acknowledged).length;
  const parts = [open ? `${open} câu cần xem lại` : "Mọi câu đã đạt hoặc đã duyệt"];
  if (unread) parts.push(`${unread} cảnh báo chưa xem`);
  return parts.join(" · ");
}

/** Whether exporting this video for real must first ask the user to accept what is unreviewed. */
export function needsExportConfirm(view: ReviewView | null): boolean {
  return Boolean(view && view.unreviewed.length && !view.accept_unreviewed);
}

/** The report the product job writes per video: whether it has a review to open, and its counts. */
export function masteringOf(video: unknown): { unreviewed: number; warnings: string[]; fallback: string | null; level: string } | null {
  const report = ((video ?? {}) as Record<string, unknown>).mastering as Record<string, unknown> | undefined;
  if (!report || typeof report !== "object") return null;
  return {
    unreviewed: isNumber(report.unreviewed) ? report.unreviewed : 0, warnings: strings(report.warnings, 30),
    fallback: isString(report.fallback) ? report.fallback : null, level: isString(report.level) ? report.level : "",
  };
}
