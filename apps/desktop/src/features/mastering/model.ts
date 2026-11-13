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
  // Tổng hợp từ video gốc (spec v2): only what engine/dubflow/compilation/raw.py and its planner do at each level.
  compilation: {
    off: "Video gốc luôn cần chọn cảnh theo lời bình, nên Tắt được dựng như Nhẹ.",
    light: "Chọn cảnh theo từng câu lời bình, cắt trong ranh giới cảnh, giữ thứ tự trong mỗi nguồn và thứ hạng của bạn; chuẩn hóa âm lượng. Nhịp cắt chậm, không mờ dần giữa các phần.",
    balanced: "Như Nhẹ, nhịp cắt vừa và mờ dần ở đầu và cuối mỗi phần; câu mở màn (nếu bạn viết) có thể dùng một cảnh nổi bật tới 6 giây lấy từ phần sau, luôn cần bạn duyệt.",
    dynamic: "Như Cân bằng, nhịp cắt nhanh hơn; cảnh mở màn được dài tới 8 giây. Không đổi thứ hạng.",
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
  USER_SOURCE_MISSING: { text: "Bạn ghi: không có cảnh phù hợp", tone: "info" },
  INVALIDATED: { text: "Cần chọn lại", tone: "bad" },
};

export const EVIDENCE_HELP: Record<string, string> = {
  MATCH_AVAILABLE: "Có cảnh khớp lời câu này.",
  MATCH_NOT_FOUND: "Chưa tìm thấy cảnh khớp. Footage có thể vẫn có: hãy xem các cảnh khác hoặc chọn tay.",
  SOURCE_INSUFFICIENT: "Mọi cảnh dùng được đều đã được xem và không cảnh nào khớp: nguồn có vẻ không quay nội dung này.",
  NOT_NEEDED: "Câu mở đầu/kêu gọi: không cần cảnh riêng.",
};

/** The evidence line of a sentence; a match the planner could not place is said so, never "has a match… 0 %". */
export function evidenceHelp(unit: Pick<ReviewUnit, "evidence" | "coverage" | "decision"> & { state?: string }): string {
  // an opening that needs no shot of its own but whose shot needs a look (P1-N1): never "không cần cảnh riêng" alone
  if (unit.evidence === "NOT_NEEDED" && unit.state && unit.state !== "READY") {
    return "Câu mở đầu/kêu gọi: không cần cảnh riêng, nhưng cảnh đang dùng cần kiểm tra (xem lý do bên dưới).";
  }
  if (unit.decision?.pin.length) {
    return "Cảnh do bạn chọn. AI không xác nhận cảnh này khớp lời câu: hãy tự xem lại.";
  }
  if (unit.evidence === "MATCH_AVAILABLE" && unit.coverage.illustrated_ms === 0) {
    return "Có cảnh khớp lời câu này nhưng chưa đặt được vào câu (cảnh quá ngắn hoặc cần cho câu sau), nên câu đang dùng cảnh chung: hãy xem các cảnh khác hoặc chọn tay.";
  }
  return EVIDENCE_HELP[unit.evidence] ?? "";
}

export type Shot = {
  id: string; source_id: string; start_ms: number; end_ms: number; keyframe: string | null; summary: string; usable: boolean;
  problems: string[]; score?: number; reasons?: string[];
  /** The whole shot this one is a piece of (its own id when it is not a piece), and whether an exclusion leaves it
   * out now, made in which sentence (#805). */
  parent: string; excluded: boolean; excluded_by: string | null;
};
/** A shot split into pieces, each with the sentences that use it (#805). */
export type Family = {
  id: string; source_id: string; start_ms: number; end_ms: number;
  pieces: Array<{ id: string; start_ms: number; end_ms: number; used_by: string[] }>;
};
/** One exclusion: the sentence that made it, whether it names a whole shot, what it leaves out now, and footage used
 * anyway because nothing else was left. */
export type Exclusion = { id: string; by: string; whole: boolean; covers: string[]; reused: string[] };
export type Piece = {
  segment_id: string; source_id: string; role: string; source_start: number; source_end: number;
  timeline_start: number; timeline_end: number; confidence: number | null; needs_review: boolean; reasons: string[];
};
export type Decision = { pin: string[]; exclude: string[]; approved?: string[]; invalidated?: string; later?: boolean; source_missing?: boolean };
/** The review queue's groups; they say what was flagged, not a measured risk (uncalibrated). */
export type Priority = "critical" | "review" | "optional";
export const PRIORITY_LABELS: Record<Priority, { text: string; tone: "bad" | "warn" | "info" }> = {
  critical: { text: "Nghiêm trọng", tone: "bad" }, review: { text: "Cần xem", tone: "warn" }, optional: { text: "Tuỳ chọn", tone: "info" },
};
const isPriority = (value: unknown): value is Priority => value === "critical" || value === "review" || value === "optional";
export type ReviewUnit = {
  unit_id: string; text: string; function: string; start_ms: number; end_ms: number;
  coverage: { milli?: number; status?: string; need_ms?: number; illustrated_ms?: number; user_ms?: number };
  notes: string[]; evidence: string; state: string; user_state: string | null; decision: Decision | null;
  chosen: Shot[]; pieces: Piece[]; alternatives: Shot[]; unreviewed: boolean;
  /** Its place in the review queue (null: settled by the user) and why. */
  priority: Priority | null; queue_reasons: string[]; later: boolean;
};
export type ReviewWarning = { id: string; text: string; acknowledged: boolean };
export type ReviewView = {
  plan_id: string; mode: string; revision: number; can_undo: boolean; units: ReviewUnit[]; warnings: ReviewWarning[];
  unreviewed: string[]; accept_unreviewed: boolean; summary: Record<string, number>;
  /** Provenance → shots with evidence of it (vision, transcript, user_note, ocr, filename_metadata, signal). */
  provenance: Record<string, number>;
  /** The sentences still to look at, most serious first; where the user was; every usable shot to choose from. */
  queue: string[]; queue_calibrated: boolean; position: string; shots: Shot[];
  families: Record<string, Family>; exclusions: Exclusion[];
};

const PROVENANCE_NAMES: Array<[string, string]> = [
  ["vision", "AI nhìn hình"], ["transcript", "phụ đề nguồn"], ["user_note", "ghi chú"], ["ocr", "chữ trên hình"],
  ["filename_metadata", "tên file"],
];

/** Where the evidence came from, said plainly; a file name is named as not being content (P2-14). */
export function provenanceText(view: Pick<ReviewView, "mode" | "provenance">): string {
  const parts = PROVENANCE_NAMES.filter(([key]) => (view.provenance[key] ?? 0) > 0)
    .map(([key, name]) => `${name} ${view.provenance[key]} cảnh`);
  if (!parts.length) return view.mode === "signals_only" ? "Chưa có bằng chứng nội dung: cảnh chọn theo điểm cắt và độ nét." : "";
  const named = (view.provenance.filename_metadata ?? 0) > 0;
  return `Bằng chứng: ${parts.join(" · ")}` + (named ? " (tên file chỉ để xếp hạng, không tính là đã thấy nội dung)" : "")
    + (view.mode === "signals_only" ? ". Chưa có bằng chứng nội dung: không câu nào được coi là sẵn sàng." : ".");
}

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
    parent: isString(item.parent) ? item.parent : item.id, excluded: item.excluded === true,
    excluded_by: isString(item.excluded_by) ? item.excluded_by : null,
  };
}

function family(value: unknown): Family | null {
  const item = (value ?? {}) as Record<string, unknown>;
  if (!isString(item.id) || !isString(item.source_id) || !isNumber(item.start_ms) || !isNumber(item.end_ms) || !Array.isArray(item.pieces)) return null;
  const pieces = item.pieces.flatMap((raw) => {
    const piece = (raw ?? {}) as Record<string, unknown>;
    return isString(piece.id) && isNumber(piece.start_ms) && isNumber(piece.end_ms)
      ? [{ id: piece.id, start_ms: piece.start_ms, end_ms: piece.end_ms, used_by: strings(piece.used_by, 50) }] : [];
  }).slice(0, 50);
  return { id: item.id, source_id: item.source_id, start_ms: item.start_ms, end_ms: item.end_ms, pieces };
}

function exclusion(value: unknown): Exclusion | null {
  const item = (value ?? {}) as Record<string, unknown>;
  if (!isString(item.id) || !isString(item.by)) return null;
  return { id: item.id, by: item.by, whole: item.whole === true, covers: strings(item.covers, 300), reused: strings(item.reused, 300) };
}

/** The whole shot a piece was cut from, when "Bỏ cả cảnh gốc" applies to it: any piece of a split shot, in use, an
 * alternative or in the list of every shot (#805: rc15 W2's unused f1 only showed as an alternative). */
export function wholeShotOf(view: Pick<ReviewView, "families">, shot: Pick<Shot, "id" | "parent">): Family | null {
  if (shot.parent === shot.id) return null;
  const whole = view.families[shot.parent];
  return whole && whole.pieces.some((piece) => piece.id === shot.id) ? whole : null;
}

/** What "Bỏ cả cảnh gốc" will leave out, said before it is done: each piece, its time in the source, and the
 * sentences using it now (#805). */
export function familyLines(whole: Family): string[] {
  return whole.pieces.map((piece, index) => `Đoạn ${index + 1} · ${seconds(piece.start_ms)}–${seconds(piece.end_ms)}`
    + (piece.used_by.length ? ` · ${piece.used_by.map((id) => `Câu ${unitNumber(id)}`).join(", ")} đang dùng` : " · chưa câu nào dùng"));
}

/** One exclusion in a line: what it names and how much footage it leaves out now. */
export function exclusionText(item: Exclusion): string {
  const what = item.whole ? `cả cảnh gốc ${item.id} (${item.covers.length} đoạn)` : `cảnh ${item.id}`;
  const reused = item.reused.length ? " · vẫn phải dùng vì không còn cảnh nào khác" : "";
  return `Câu ${unitNumber(item.by)} đã loại ${what}${reused}`;
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
        ...(decision.later === true ? { later: true } : {}), ...(decision.source_missing === true ? { source_missing: true } : {}),
      } : null,
      chosen: (Array.isArray(unit.chosen) ? unit.chosen : []).map(shot).filter((item): item is Shot => item !== null),
      pieces: (Array.isArray(unit.pieces) ? unit.pieces : []).map(piece).filter((item): item is Piece => item !== null),
      alternatives: (Array.isArray(unit.alternatives) ? unit.alternatives : []).map(shot).filter((item): item is Shot => item !== null).slice(0, 3),
      unreviewed: unit.unreviewed === true,
      priority: isPriority(unit.priority) ? unit.priority : null, queue_reasons: strings(unit.queue_reasons, 8), later: unit.later === true,
    });
  }
  const warnings = (Array.isArray(object.warnings) ? object.warnings : []).flatMap((raw) => {
    const item = (raw ?? {}) as Record<string, unknown>;
    return isString(item.id) && isString(item.text) ? [{ id: item.id, text: item.text, acknowledged: item.acknowledged === true }] : [];
  });
  const summary: Record<string, number> = {};
  for (const [key, count] of Object.entries((object.summary ?? {}) as Record<string, unknown>)) if (isNumber(count)) summary[key] = count;
  const provenance: Record<string, number> = {};
  for (const [key, count] of Object.entries((object.provenance ?? {}) as Record<string, unknown>)) if (isNumber(count)) provenance[key] = count;
  return {
    plan_id: isString(object.plan_id) ? object.plan_id : "", mode: isString(object.mode) ? object.mode : "",
    revision: isNumber(object.revision) ? object.revision : 0, can_undo: object.can_undo === true, units, warnings,
    unreviewed: strings(object.unreviewed, 500), accept_unreviewed: object.accept_unreviewed === true, summary, provenance,
    queue: strings(object.queue, 500).filter((id) => units.some((unit) => unit.unit_id === id)),
    queue_calibrated: object.queue_calibrated === true, position: isString(object.position) ? object.position : "",
    shots: (Array.isArray(object.shots) ? object.shots : []).map(shot).filter((item): item is Shot => item !== null).slice(0, 300),
    families: Object.fromEntries(Object.entries((object.families ?? {}) as Record<string, unknown>).slice(0, 500)
      .map(([key, raw]) => [key, family(raw)] as const).filter((entry): entry is readonly [string, Family] => entry[1] !== null && entry[1].id === entry[0])),
    exclusions: (Array.isArray(object.exclusions) ? object.exclusions : []).map(exclusion).filter((item): item is Exclusion => item !== null).slice(0, 300),
  };
}

/** The badge of a sentence: the user's word when there is one, else the planner's verdict. */
export function unitBadge(unit: ReviewUnit): { text: string; tone: string } {
  if (unit.user_state === "INVALIDATED") return STATE_LABELS.INVALIDATED;
  if (unit.user_state === "USER_APPROVED") return STATE_LABELS.USER_APPROVED;
  if (unit.user_state === "USER_SOURCE_MISSING") return STATE_LABELS.USER_SOURCE_MISSING;
  if (unit.decision?.pin.length) return { text: "Bạn đã chọn cảnh", tone: "info" };
  return STATE_LABELS[unit.state] ?? { text: unit.state, tone: "warn" };
}

/** A sentence's number as the panel says it: the trailing number of its id (`s3` → 3, `i2.u4` → 4). */
export function unitNumber(unitId: string): string {
  return /(\d+)$/.exec(unitId)?.[1] ?? unitId;
}

export function coverageText(unit: ReviewUnit): string {
  if (unit.coverage.status === "generic") return "Không cần cảnh riêng";
  const milli = unit.coverage.milli;
  const need = unit.coverage.need_ms ?? 0;
  const user = unit.coverage.user_ms ?? 0;
  // the user's pinned shot is theirs, never "khớp" (rc9 P2-N14)
  if (user > 0 && need > 0) {
    const mine = Math.min(100, Math.round(user * 100 / need));
    return `${typeof milli === "number" && milli > 0 ? `AI thấy khớp ${Math.round(milli / 10)} % · ` : ""}cảnh bạn chọn ${mine} % thời lượng câu`;
  }
  return typeof milli === "number" ? `Hình khớp ${Math.round(milli / 10)} % thời lượng câu` : "";
}

export function seconds(ms: number): string {
  const total = Math.max(0, Math.round(ms / 100)) / 10;
  const minutes = Math.floor(total / 60);
  const rest = (total - minutes * 60).toFixed(1).replace(".", ",");
  return minutes ? `${minutes}:${rest.padStart(4, "0")}` : `${rest} s`;
}

/** The queue in one line: how many sentences of each group are left, and that the groups are not calibrated. */
export function queueSummary(view: Pick<ReviewView, "units" | "queue" | "queue_calibrated">): string {
  if (!view.queue.length) return "Hàng đợi duyệt trống: mọi câu đã được bạn xử lý.";
  const count = (priority: Priority) => view.units.filter((unit) => unit.priority === priority).length;
  const parts = (["critical", "review", "optional"] as const).map((key) => [PRIORITY_LABELS[key].text.toLowerCase(), count(key)] as const)
    .filter(([, n]) => n > 0).map(([label, n]) => `${n} ${label}`);
  return `Hàng đợi duyệt: ${parts.join(" · ")}` + (view.queue_calibrated ? "" : " (nhóm theo cờ của AI, chưa hiệu chỉnh)");
}

/** The sentence to go to after `current`: the next one of the queue as it was (`before`, the queue the user was
 * walking) that is still waiting, else the first one waiting; null when nothing waits. */
export function nextInQueue(view: Pick<ReviewView, "queue">, current: string, before: readonly string[] = view.queue): string | null {
  if (!view.queue.length) return null;
  const at = before.indexOf(current);
  const after = at >= 0 ? before.slice(at + 1).find((id) => view.queue.includes(id) && id !== current) : undefined;
  return after ?? view.queue.find((id) => id !== current) ?? view.queue[0];
}

/** Where the panel opens: the saved place when it is still in the queue, else the first sentence of the queue. */
export function startUnit(view: Pick<ReviewView, "queue" | "position">): string | null {
  return view.queue.includes(view.position) ? view.position : view.queue[0] ?? null;
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

/** One Final QC check: `ok` null means it was not measured (said so, never counted as passed). */
export interface QcCheck { id: string; label: string; ok: boolean | null; detail: string }
export interface FinalQc { state: string; passed: boolean; checks: QcCheck[]; unmeasured: string[] }

export const QC_STATE_LABELS: Record<string, string> = {
  RENDERING: "Đang dựng", RENDER_PENDING: "Còn lựa chọn chưa render", REVIEW_PENDING: "Chưa duyệt xong",
  FINAL_QC_FAILED: "Kiểm tra cuối chưa đạt", READY_TO_FINALIZE: "Sẵn sàng xuất bản cuối",
};

/** The engine's Final QC answer, or null when it is not one (the caller then fails closed). */
export function parseFinalQc(raw: unknown): FinalQc | null {
  const value = (raw ?? {}) as Record<string, unknown>;
  if (value.kind !== "final_qc" || !isString(value.state) || !Array.isArray(value.checks)) return null;
  const checks = (value.checks as unknown[]).slice(0, 40).flatMap((item) => {
    const check = (item ?? {}) as Record<string, unknown>;
    if (!isString(check.id) || !(check.ok === true || check.ok === false || check.ok === null)) return [];
    return [{ id: check.id, label: isString(check.label) ? check.label : check.id, ok: check.ok as boolean | null,
              detail: isString(check.detail) ? check.detail.slice(0, 400) : "" }];
  });
  // passed only when the engine says so AND no check failed: a successful render is never a passed QC
  const passed = value.passed === true && value.state === "READY_TO_FINALIZE" && checks.every((check) => check.ok !== false);
  return { state: value.state, passed, checks, unmeasured: strings(value.unmeasured, 40) };
}

/** What blocks the export, one line per failed check, plus what was not measured. */
export function qcMessage(qc: FinalQc): string {
  const failed = qc.checks.filter((check) => check.ok === false).map((check) => `${check.label}: ${check.detail}`);
  const lines = [`${QC_STATE_LABELS[qc.state] ?? qc.state}.`, ...failed];
  if (qc.state === "RENDER_PENDING") lines.push("Hãy render lại để áp dụng các lựa chọn mới trước khi xuất.");
  const unmeasured = qc.checks.filter((check) => check.ok === null).map((check) => check.label);
  if (unmeasured.length) lines.push(`Chưa đo: ${unmeasured.join(", ")}.`);
  return lines.join("\n");
}

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

/**
 * Whether a mastered video may open in the editor (where it is exported). Fails closed: the review must be read and
 * understood, and when sentences are unreviewed the user's acceptance must be saved; otherwise the editor stays shut
 * and `error` says why. Videos without mastering never come here.
 */
export async function exportGate(invoke: Invoke, review: { outputDir: string; video: number },
  ask: (unreviewed: number) => Promise<boolean>): Promise<{ open: boolean; error?: string }> {
  let raw: unknown;
  try {
    raw = await invoke<unknown>("mastering_review", { outputDir: review.outputDir, video: review.video, action: "view" });
  } catch (error) {
    return { open: false, error: `Không đọc được bản duyệt cảnh của video này: ${String(error).slice(0, 160)}` };
  }
  const view = parseReview(raw);
  if (!view) return { open: false, error: "Bản duyệt cảnh của video này không đọc được. Hãy bấm “Render lại video này” hoặc tạo lại video." };
  if (!needsExportConfirm(view)) return finalQcGate(invoke, review);
  if (!await ask(view.unreviewed.length)) return { open: false };
  try {
    await invoke<unknown>("mastering_review", { outputDir: review.outputDir, video: review.video, action: "accept", value: true });
  } catch (error) {
    return { open: false, error: `Không lưu được xác nhận xuất các câu chưa duyệt: ${String(error).slice(0, 160)}` };
  }
  return finalQcGate(invoke, review);
}

/** The Final QC after the review gate: the editor (where the video is exported) opens only when it passes. */
async function finalQcGate(invoke: Invoke, review: { outputDir: string; video: number }): Promise<{ open: boolean; error?: string }> {
  let qc: FinalQc | null;
  try {
    qc = parseFinalQc(await invoke<unknown>("mastering_final_qc", { outputDir: review.outputDir, video: review.video }));
  } catch (error) {
    return { open: false, error: `Không chạy được kiểm tra cuối của video này: ${String(error).slice(0, 160)}` };
  }
  if (!qc) return { open: false, error: "Kiểm tra cuối của video này không đọc được." };
  return qc.passed ? { open: true } : { open: false, error: qcMessage(qc) };
}

/** The report the product job writes per video: whether it has a review to open, and its counts. */
export function masteringOf(video: unknown): {
  unreviewed: number; userSettled: number | null; warnings: string[]; fallback: string | null; level: string;
} | null {
  const report = ((video ?? {}) as Record<string, unknown>).mastering as Record<string, unknown> | undefined;
  if (!report || typeof report !== "object") return null;
  return {
    unreviewed: isNumber(report.unreviewed) ? report.unreviewed : 0,
    // null: a run from before the count existed, so who settled the sentences is not known
    userSettled: isNumber(report.user_settled) ? report.user_settled : null, warnings: strings(report.warnings, 30),
    fallback: isString(report.fallback) ? report.fallback : null, level: isString(report.level) ? report.level : "",
  };
}

/** A video's review line. What the user settled is said as the user's decision, never as an automatic pass (P1-N3). */
export function masteringNote(report: { unreviewed: number; userSettled: number | null; fallback: string | null }): string {
  if (report.fallback) return report.fallback;
  if (report.unreviewed) return `${report.unreviewed} câu cần xem lại cảnh`;
  if (report.userSettled === null) return "Không còn câu chờ duyệt";
  if (report.userSettled > 0) return `Bạn đã duyệt hoặc chọn cảnh cho ${report.userSettled} câu AI chưa chắc; không còn câu chờ duyệt`;
  return "Cảnh đã đạt kiểm tra tự động (chưa hiệu chỉnh, nên vẫn nên xem lại)";
}
