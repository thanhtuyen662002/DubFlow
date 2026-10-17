export type ReviewOperation = "initialize" | "apply" | "undo" | "standard_export" | "editable_export";
export type ReviewActivity = {
  schema_version: 1; kind: "review_desktop_request_activity"; request_id: string; job_id: string;
  operation: ReviewOperation; state: "running" | "cancelling" | "ended";
  phase: "checking" | "processing" | "verifying" | "ended";
  elapsed_ms: number; phase_elapsed_ms: number;
  process_state: "idle" | "running" | "exited" | "unobserved";
  process_observation_age_ms: number | null;
};
const NATIVE_STAGES = ["admitting_current", "admitting_processing", "verifying_runtime_files", "preparing_cue", "synthesizing_cue", "validating_cue",
  "mixing_audio", "validating_mix", "deriving_timeline", "rendering_video", "validating_render", "retaining_publication", "committing_revision",
  "reopening_current", "publishing_standard", "publishing_editable"] as const;
export type NativeStage = { schema_version: 1; kind: "review_native_stage_observation"; sequence: number; stage_epoch: number;
  stage: typeof NATIVE_STAGES[number]; elapsed_ms: number; units_completed: number; bytes_verified: number };
export type NativeObservation = { event: NativeStage; received_age_ms: number; advance_age_ms: number };
export type ReviewActivityUpdate = { kind: "observed"; activity: ReviewActivity; native?: NativeObservation } | { kind: "unavailable" };
export type ReviewActivityObserver = (update: ReviewActivityUpdate) => void;

export function readReviewActivity(raw: unknown, id: string, job: string, operation: ReviewOperation): ReviewActivity {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("invalid review activity");
  const v = raw as Record<string, unknown>;
  if (Object.keys(v).sort().join(",") !== "elapsed_ms,job_id,kind,operation,phase,phase_elapsed_ms,process_observation_age_ms,process_state,request_id,schema_version,state" ||
    v.schema_version !== 1 || v.kind !== "review_desktop_request_activity" || v.request_id !== id || v.job_id !== job || v.operation !== operation ||
    !["running", "cancelling", "ended"].includes(v.state as string) || !["checking", "processing", "verifying", "ended"].includes(v.phase as string) ||
    !["idle", "running", "exited", "unobserved"].includes(v.process_state as string)) throw new Error("unbound review activity");
  const ms = (n: unknown): n is number => Number.isSafeInteger(n) && (n as number) >= 0 && (n as number) <= 86_400_000;
  if (!ms(v.elapsed_ms) || !ms(v.phase_elapsed_ms) || v.phase_elapsed_ms > v.elapsed_ms ||
    (v.process_observation_age_ms !== null && !ms(v.process_observation_age_ms)) ||
    ((v.process_state === "idle") !== (v.process_observation_age_ms === null)) ||
    ((v.state === "ended") !== (v.phase === "ended"))) throw new Error("invalid review activity timing");
  return structuredClone(v) as ReviewActivity;
}

/** New envelope leaves the original closed host-only activity v1 unchanged. */
export function readReviewStage(raw: unknown, id: string, job: string, operation: ReviewOperation): Extract<ReviewActivityUpdate, { kind: "observed" }> {
  const object = (value: unknown, fields: string) => {
    if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).sort().join(",") !== fields) throw new Error("invalid native stage observation");
    return value as Record<string, unknown>;
  };
  const v = object(raw, "activity,kind,native_observation,native_stage,schema_version");
  if (v.schema_version !== 1 || v.kind !== "review_desktop_request_stage") throw new Error("unsupported native stage observation");
  const activity = readReviewActivity(v.activity, id, job, operation);
  if (v.native_observation === "unavailable" && v.native_stage === null) return { kind: "observed", activity };
  if (v.native_observation !== "observed") throw new Error("invalid native observation availability");
  const n = object(v.native_stage, "advance_age_ms,event,received_age_ms");
  const e = object(n.event, "bytes_verified,elapsed_ms,kind,schema_version,sequence,stage,stage_epoch,units_completed");
  const count = (v: unknown, max: number): v is number => Number.isSafeInteger(v) && (v as number) >= 0 && (v as number) <= max;
  if (e.schema_version !== 1 || e.kind !== "review_native_stage_observation" || !NATIVE_STAGES.includes(e.stage as NativeStage["stage"]) ||
    !count(e.sequence, 32768) || e.sequence === 0 || !count(e.stage_epoch, e.sequence) || e.stage_epoch === 0 ||
    !count(e.elapsed_ms, activity.elapsed_ms) || !count(e.units_completed, Number.MAX_SAFE_INTEGER) || !count(e.bytes_verified, Number.MAX_SAFE_INTEGER) ||
    (e.stage !== "verifying_runtime_files" && (e.units_completed !== 0 || e.bytes_verified !== 0)) ||
    !count(n.received_age_ms, 86_400_000) || !count(n.advance_age_ms, 86_400_000) || n.advance_age_ms < n.received_age_ms) throw new Error("invalid native observation counters");
  return { kind: "observed", activity, native: structuredClone(n) as NativeObservation };
}

/** One status IPC at a time. Losing status never retries/cancels the mutation. */
export function observeReviewRequest(invoke: (command: string, args: Record<string, unknown>) => Promise<unknown>,
  id: string, job: string, operation: ReviewOperation, signal: AbortSignal, notify?: ReviewActivityObserver): () => void {
  if (!notify || signal.aborted) return () => {};
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined, watchdog: ReturnType<typeof setTimeout> | undefined;
  let previousElapsed = 0;
  let previousNative: NativeStage | undefined;
  const report = (value: ReviewActivityUpdate) => { if (!stopped && !signal.aborted) { try { notify(value); } catch { /* Observation cannot change a mutation. */ } } };
  const stop = () => { stopped = true; clearTimeout(timer); clearTimeout(watchdog); signal.removeEventListener("abort", stop); };
  const poll = async () => {
    if (stopped || signal.aborted) return;
    watchdog = setTimeout(() => report({ kind: "unavailable" }), 5000);
    try {
      const raw = await invoke("review_request_stage_v1", { requestId: id });
      if (stopped || signal.aborted) return;
      const update = readReviewStage(raw, id, job, operation), { activity } = update;
      if (activity.elapsed_ms < previousElapsed) throw new Error("review activity clock went backwards");
      previousElapsed = activity.elapsed_ms;
      const event = update.native?.event;
      if (event && previousNative && (event.sequence < previousNative.sequence || event.elapsed_ms < previousNative.elapsed_ms || event.stage_epoch < previousNative.stage_epoch ||
        (event.sequence === previousNative.sequence && (["stage_epoch", "stage", "elapsed_ms", "units_completed", "bytes_verified"] as const).some(key => event[key] !== previousNative![key])) ||
        (event.stage_epoch === previousNative.stage_epoch && (event.stage !== previousNative.stage || event.units_completed < previousNative.units_completed || event.bytes_verified < previousNative.bytes_verified)))) throw new Error("native stage observation went backwards");
      if (event) previousNative = event;
      report(update);
      // Ended does not mean successful, committed, QC passed or retryable.
      if (activity.state === "ended") { stop(); return; }
    } catch { report({ kind: "unavailable" }); }
    finally { clearTimeout(watchdog); }
    if (!stopped && !signal.aborted) timer = setTimeout(() => { void poll(); }, 1000);
  };
  signal.addEventListener("abort", stop, { once: true });
  timer = setTimeout(() => { void poll(); }, 200);
  return stop;
}

export function reviewActivityText(update: ReviewActivityUpdate): string {
  if (update.kind === "unavailable") return "Chưa đọc được trạng thái tác vụ. Kết quả chưa được xác nhận; bạn vẫn có thể dừng và tải lại.";
  const a = update.activity;
  const seconds = Math.floor(a.elapsed_ms / 1000), elapsed = `${Math.floor(seconds / 60)} phút ${seconds % 60} giây`;
  if (a.state === "ended") return `Yêu cầu đã kết thúc sau ${elapsed}. Đang chờ xác thực kết quả; chưa thể kết luận thành công.`;
  if (a.state === "cancelling") return `Đã yêu cầu dừng · ${elapsed}. Hãy tải lại để kiểm tra bản hiện hành sau khi tác vụ dừng.`;
  const phase = { checking: "Đang kiểm tra dữ liệu", processing: "Đang xử lý yêu cầu", verifying: "Đang kiểm tra kết quả", ended: "Yêu cầu đã kết thúc" }[a.phase];
  const observed = a.process_state === "running" && a.process_observation_age_ms !== null && a.process_observation_age_ms <= 5000 ?
    "Tiến trình xử lý đang chạy; chưa có kết quả được xác nhận." :
    a.process_state === "exited" ? "Tiến trình đã trả về; kết quả còn phải được kiểm tra." :
    a.process_state === "unobserved" || a.process_state === "running" ? "Chưa cập nhật được trạng thái tiến trình." : "Chưa có kết quả được xác nhận.";
  const stage = update.native;
  if (!stage || a.phase !== "processing") return `${phase} · ${elapsed}. ${observed}`;
  const labels: Record<NativeStage["stage"], string> = {
    admitting_current: "Kiểm tra bản đang dùng", admitting_processing: "Kiểm tra bộ xử lý", verifying_runtime_files: "Kiểm tra các tệp xử lý",
    preparing_cue: "Chuẩn bị câu thoại", synthesizing_cue: "Tạo giọng đọc cho câu", validating_cue: "Kiểm tra giọng đọc vừa tạo",
    mixing_audio: "Trộn âm thanh", validating_mix: "Kiểm tra bản trộn", deriving_timeline: "Tạo timeline đã sửa", rendering_video: "Dựng video",
    validating_render: "Kiểm tra video vừa dựng", retaining_publication: "Kiểm tra và giữ bản thay thế", committing_revision: "Lưu bản sửa",
    reopening_current: "Đọc lại bản đang dùng", publishing_standard: "Tạo kết quả chuẩn", publishing_editable: "Tạo gói chỉnh sửa",
  };
  const files = stage.event.stage === "verifying_runtime_files" ? ` Đã kiểm tra đầy đủ ${stage.event.units_completed} tệp trong lượt này (${stage.event.bytes_verified} byte).` : "";
  return `${labels[stage.event.stage]} · ${elapsed}.${files} ${Math.floor(stage.advance_age_ms / 1000)} giây từ mốc xử lý mới nhất. ${observed}`;
}
