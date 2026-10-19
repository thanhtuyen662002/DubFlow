import type { ReviewOperation } from "./request-activity.ts";

export type ReviewRequestFailure = {
  schema_version: 1; kind: "review_desktop_request_failure"; request_id: string; job_id: string;
  operation: ReviewOperation; outcome: "unconfirmed"; phase: "checking" | "processing" | "verifying";
  native_stage: string | null; message: string; diagnostic: string | null;
};
const stages = ["admitting_current", "admitting_processing", "verifying_runtime_files", "preparing_cue", "synthesizing_cue", "validating_cue",
  "mixing_audio", "validating_mix", "deriving_timeline", "rendering_video", "validating_render", "retaining_publication", "committing_revision",
  "reopening_current", "publishing_standard", "publishing_editable"];
const text = (v: unknown, limit: number): v is string => typeof v === "string" && v.length > 0 &&
  new TextEncoder().encode(v).byteLength <= limit && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/.test(v) &&
  ![...v].some(c => { const n = c.codePointAt(0)!; return n >= 0xd800 && n <= 0xdfff; });

export function readReviewFailure(raw: unknown, id: string, job: string, operation: ReviewOperation): ReviewRequestFailure {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("invalid review failure");
  const v = raw as Record<string, unknown>;
  if (Object.keys(v).sort().join(",") !== "diagnostic,job_id,kind,message,native_stage,operation,outcome,phase,request_id,schema_version" ||
    v.schema_version !== 1 || v.kind !== "review_desktop_request_failure" || v.request_id !== id || v.job_id !== job || v.operation !== operation ||
    !/^[A-Za-z0-9-]{16,64}$/.test(id) || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(job) ||
    v.outcome !== "unconfirmed" || !["checking", "processing", "verifying"].includes(v.phase as string) ||
    (v.native_stage !== null && !stages.includes(v.native_stage as string)) || !text(v.message, 1024) ||
    (v.diagnostic !== null && !text(v.diagnostic, 4096))) throw new Error("unbound review failure");
  return structuredClone(v) as ReviewRequestFailure;
}

export class ReviewFailureError extends Error {
  readonly failure: ReviewRequestFailure;
  constructor(failure: ReviewRequestFailure) { super(failure.message); this.name = "ReviewFailureError"; this.failure = structuredClone(failure); }
}

/** Invalid or legacy errors retain uncertainty and cannot become typed details. */
export function boundReviewFailure(raw: unknown, id: string, job: string, operation: ReviewOperation): unknown {
  try { return new ReviewFailureError(readReviewFailure(raw, id, job, operation)); } catch { return raw; }
}

/** Returned text must use textContent, never HTML, links, commands or playback. */
export function reviewFailureDetails(error: unknown, job: string, operation: ReviewOperation): string | null {
  if (!(error instanceof ReviewFailureError)) return null;
  let f: ReviewRequestFailure;
  try { f = readReviewFailure(error.failure, error.failure.request_id, job, operation); } catch { return null; }
  const phase = { checking: "Kiểm tra dữ liệu trước khi chạy", processing: "Xử lý yêu cầu", verifying: "Kiểm tra kết quả vừa tạo" }[f.phase];
  return [`Bước xử lý: ${phase}`, f.message, f.diagnostic].filter(value => value !== null).join("\n\n");
}

/** Presentation only: a diagnostic never proves mutation state or grants retry. */
export function reviewFailureHint(error: unknown, job: string, operation: ReviewOperation): string | null {
  if (operation !== "apply" || !(error instanceof ReviewFailureError)) return null;
  let f: ReviewRequestFailure;
  try { f = readReviewFailure(error.failure, error.failure.request_id, job, operation); } catch { return null; }
  const refusal = "Review temporary path is too long for Windows process startup; create a new processing version in a shorter output folder; prior export remains unchanged";
  if (!f.diagnostic?.includes(refusal)) return null;
  return "Đường dẫn thư mục tạm vượt giới hạn Windows. Chưa xác nhận được kết quả sửa. Bấm “Tải lại lời thoại” để kiểm tra phiên hiện hành; không tự gửi lại yêu cầu. Cần khắc phục vị trí lưu tạm trước khi sửa tiếp.";
}
