/**
 * Presentation adapter for contracts/job_status/schema-v1.json.
 * The desktop never infers liveness from a percentage or a wall clock; it
 * displays the supervisor's explicit state and actionable message.
 */
export type JobStatusState =
  | "QUEUED"
  | "RUNNING"
  | "WAITING_RESOURCE"
  | "WAITING_EXTERNAL"
  | "RETRYING"
  | "WORKER_LOST"
  | "RECOVERED"
  | "BLOCKED_NEEDS_ACTION"
  | "COMPLETED"
  | "FAILED";

export type JobStatus = {
  schema_version: 1;
  state: JobStatusState;
  reason: string;
  message: string;
  checkpoint_id: string | null;
  progress: {
    completed_units: string;
    total_units: string | null;
    heartbeat_sequence: string;
  };
  retry: {
    attempt: string;
    max_attempts: string;
    condition_fingerprint: string | null;
  };
  resource: {
    kind: "GPU" | "CPU" | "DISK" | "NETWORK" | "WORKER_SLOT" | null;
    held: boolean;
    release_requested: boolean;
  };
};

const STATE_LABELS: Record<JobStatusState, string> = {
  QUEUED: "Chờ xử lý",
  RUNNING: "Đang xử lý",
  WAITING_RESOURCE: "Đang chờ tài nguyên",
  WAITING_EXTERNAL: "Cần thao tác",
  RETRYING: "Đang thử lại",
  WORKER_LOST: "Mất kết nối",
  RECOVERED: "Đã khôi phục",
  BLOCKED_NEEDS_ACTION: "Cần thao tác",
  COMPLETED: "Hoàn tất",
  FAILED: "Thất bại",
};

export function stateLabel(state: JobStatusState): string {
  return STATE_LABELS[state];
}

/** Pipeline stages in the order the production worker reports them. */
export const PIPELINE_STAGES = [
  { id: "probe", label: "Kiểm tra media", from: 0 },
  { id: "audio", label: "Tách audio", from: 0.08 },
  { id: "asr", label: "Nhận dạng lời thoại", from: 0.18 },
  { id: "translate", label: "Dịch sang tiếng Việt", from: 0.44 },
  { id: "subtitles", label: "Tạo phụ đề", from: 0.66 },
  { id: "dubbing", label: "Lồng tiếng", from: 0.7 },
  { id: "render", label: "Render video", from: 0.74 },
  { id: "qc", label: "Kiểm tra chất lượng", from: 0.88 },
] as const;

/** Whole-pipeline fraction, or null when the supervisor reports no total. */
export function progressFraction(status: JobStatus): number | null {
  if (status.state === "COMPLETED") return 1;
  if (status.progress.total_units === null) return null;
  const total = BigInt(status.progress.total_units);
  if (total === 0n) return 0;
  const completed = BigInt(status.progress.completed_units);
  const bounded = completed > total ? total : completed;
  return Number((bounded * 10000n) / total) / 10000;
}

/** Index of the active stage; -1 before work starts, length when complete. */
export function activeStageIndex(status: JobStatus): number {
  if (status.state === "COMPLETED") return PIPELINE_STAGES.length;
  const fraction = progressFraction(status);
  if (status.state === "QUEUED" || fraction === null) return status.state === "QUEUED" ? -1 : 0;
  let index = 0;
  PIPELINE_STAGES.forEach((stage, position) => {
    if (fraction >= stage.from) index = position;
  });
  return index;
}

/** Plain-language explanations for failures users can act on. */
const FAILURE_HINTS: Array<[RegExp, string]> = [
  [/^ASR_EMPTY(?![A-Z_])/, "Không tìm thấy lời thoại trong video (có thể chỉ có nhạc nền hoặc tiếng quá nhỏ)."],
  [/^AUDIO_STREAM_MISSING(?![A-Z_])/, "Video không có âm thanh nên không thể nhận dạng lời thoại."],
  [/^SOURCE_LANGUAGE_(UNRESOLVED|MISMATCH)(?![A-Z_])/, "Không xác định được ngôn ngữ gốc. Hãy chọn “Ngôn ngữ gốc” rồi xử lý lại."],
  [/^LANGUAGE_UNSUPPORTED(?![A-Z_])/, "Ngôn ngữ của video chưa được hỗ trợ (hiện hỗ trợ tiếng Trung và tiếng Anh)."],
  [/^EDITABLE_STORAGE_INSUFFICIENT(?![A-Z_])/, "Ổ đĩa không đủ dung lượng trống. Hãy giải phóng dung lượng rồi xử lý lại."],
  [/^QC_DURATION_SHORT(?![A-Z_])/, "Video xuất ra ngắn hơn bản gốc; có thể file gốc bị lỗi. Hãy thử xử lý lại."],
  [/^TRANSLATION_OVERRIDES_INVALID(?![A-Z_])/, "Bản sửa dịch không hợp lệ. Hãy mở “Xem & sửa bản dịch” và lưu lại."],
];

export function failureHint(message: string): string | null {
  for (const [pattern, hint] of FAILURE_HINTS) if (pattern.test(message)) return hint;
  return null;
}

export function displayJobStatus(status: JobStatus): string {
  if (status.state === "FAILED") {
    const hint = failureHint(status.message);
    if (hint) return hint;
  }
  if (status.state === "WAITING_RESOURCE" && status.resource.kind === "GPU") {
    return "Đang chờ GPU";
  }
  if (status.state === "WAITING_EXTERNAL") {
    return status.message || "Cần thao tác";
  }
  if (status.state === "RETRYING") {
    return "Đang thử lại với đoạn nhỏ hơn";
  }
  if (status.state === "RECOVERED") {
    return "Đã khôi phục từ checkpoint";
  }
  if (status.state === "BLOCKED_NEEDS_ACTION") {
    return "Đã dừng; cần thao tác";
  }
  if (status.state === "QUEUED") return "Chờ xử lý";
  // Checkpoint ids are internal; show the stage instead.
  if (status.reason === "checkpoint" || status.message.startsWith("Đã lưu checkpoint")) {
    const index = activeStageIndex(status);
    return PIPELINE_STAGES[Math.min(Math.max(index, 0), PIPELINE_STAGES.length - 1)].label;
  }
  return status.message;
}
