import { reviewAudioFailureHint, reviewFailureDetails, reviewFailureHint } from "./request-failure.ts";
import { reviewActivityText, type ReviewActivityObserver, type ReviewActivityUpdate } from "./request-activity.ts";
import type { ReviewReason } from "./model.ts";
import { previewProductionCorrection, readReviewPayload, reviewRangeLabel,
  type ProductionCorrection, type ProductionFinding, type ReviewPayload } from "./wire.ts";
import { acceptCorrectionResponse, correctionRequest, previewRequest, type ReviewCorrectionRequest, type ReviewPreviewRequest } from "./session.ts";
import { currentPlanRequest, type CurrentPlanRequest, type CurrentCorrectionPlan } from "./current-plan.ts";
import { currentConfirmation, type CurrentConfirmation } from "./current-apply.ts";
import { currentUndoRequest, type CurrentUndoRequest } from "./current-undo.ts";
import { currentExportRequest, type CurrentExportRequest, type CurrentExportReceipt, type CurrentExportFormat } from "./current-export.ts";
import { currentInitializeRequest, type CurrentInitializeRequest } from "./current-initialize.ts";
import type { CurrentReviewSnapshot } from "./wire.ts";
import { resultVideoRequest, type ResultVideoRequest, type ResultVideoLaunch } from "./result-video.ts";

export type ReviewVoice = { voice_id: string; label: string };
export type ReviewActions = {
  /** Supply voices compatible with the pinned producer/model. */
  voices?: readonly ReviewVoice[];
  preview?: (request: ReviewPreviewRequest, signal: AbortSignal) => Promise<void>;
  openResult?: (request: ResultVideoRequest, signal: AbortSignal) => Promise<ResultVideoLaunch>;
  correct?: (request: ReviewCorrectionRequest) => Promise<unknown>;
  planCurrent?: (request: CurrentPlanRequest, signal: AbortSignal) => Promise<CurrentCorrectionPlan>;
  applyCurrent?: (confirmation: CurrentConfirmation, signal: AbortSignal, observe?: ReviewActivityObserver) => Promise<CurrentReviewSnapshot>;
  undoCurrent?: (request: CurrentUndoRequest, signal: AbortSignal, observe?: ReviewActivityObserver) => Promise<CurrentReviewSnapshot>;
  exportCurrent?: (request: CurrentExportRequest, signal: AbortSignal, observe?: ReviewActivityObserver) => Promise<CurrentExportReceipt>;
  initializeCurrent?: (request: CurrentInitializeRequest, signal: AbortSignal, observe?: ReviewActivityObserver) => Promise<CurrentReviewSnapshot>;
  /** Rebind optional actions after the native original session becomes current. */
  refreshed?: () => ReviewActions;
};
const LABELS: Record<ReviewReason, string> = {
  ASR_OCR_CONFLICT: "Lời nói và chữ trên hình", SPEAKER_UNCERTAINTY: "Người nói",
  TRANSLATION_ANOMALY: "Bản dịch", TTS_FAILURE: "Giọng đọc", INPAINT_RISK: "Xóa chữ", SYNC_QC_WARNING: "Đồng bộ và chất lượng",
};
const STAGES: Record<string, string> = { translation: "Bản dịch", subtitles: "Phụ đề", tts: "Giọng đọc",
  mix: "Trộn âm thanh", render: "Dựng video", qc: "Kiểm tra chất lượng", export: "Xuất kết quả" };
const PAGE_SIZE = 30;
let nextInstance = 0;

type WarningCopy = { summary: string; action: string };
const MIX_WARNINGS: Record<string, string> = {
  "mix peak was reduced to the configured headroom": "Mức âm thanh đỉnh đã được hạ theo giới hạn cấu hình.",
  "mix RMS was reduced to the configured loudness ceiling": "Âm lượng bản trộn đã được hạ theo mức trần cấu hình.",
  "original source contains clipped samples; it was preserved as supplied": "Báo cáo cho biết âm thanh nguồn có điểm vượt ngưỡng; nguồn được giữ nguyên.",
  "final mix is silent because no usable source or TTS energy was available": "Báo cáo cho biết bản trộn không có âm thanh sử dụng được.",
  "final mix is below the configured target RMS; source and dialogue were preserved": "Báo cáo cho biết âm lượng bản trộn thấp hơn mức mục tiêu.",
  "one or more TTS segments were unavailable; the original source remains in the final mix": "Báo cáo cho biết một số câu thiếu giọng lồng tiếng; âm thanh nguồn vẫn được giữ lại.",
};
/** Presentation only: the raw diagnostic stays in its payload/report. Unknown
 * diagnostics never gain a measured quality claim from their category/code. */
function warningCopy(warning: string): WarningCopy {
  const code = warning.split(":", 1)[0].trim();
  if (code === "TTS_INTERCUE_GAP_USED") return {
    summary: "Phần lồng tiếng dùng thêm khoảng trống sau khung thời gian gốc của câu; khoảng trống này chưa được kiểm chứng.",
    action: "Hãy nghe lại câu này và câu kế tiếp trong video đã xuất. Nếu lời đọc quá dài, cân nhắc rút gọn bản dịch hoặc chọn giọng khác rồi kiểm tra lại.",
  };
  if (code === "LOW_CONFIDENCE_CUE") return {
    summary: "Độ tin cậy được báo cáo cho câu này còn thấp.",
    action: "Hãy đối chiếu lời gốc với âm thanh hoặc chữ trên hình và kiểm tra bản dịch trước khi chỉnh sửa.",
  };
  const message = code === "QC_WARNING" ? warning.slice(warning.indexOf(":") + 1).trim() : warning;
  if (Object.hasOwn(MIX_WARNINGS, message)) return {
    summary: MIX_WARNINGS[message],
    action: "Hãy nghe lại video đã xuất ở âm lượng thường dùng và kiểm tra các câu liên quan trước khi sử dụng kết quả.",
  };
  return {
    summary: "Có cảnh báo chưa có lời giải thích chi tiết.",
    action: "Hãy xem và nghe lại đoạn liên quan trong video đã xuất, đối chiếu lời gốc và bản dịch trước khi chỉnh sửa.",
  };
}
function globalWarningCopy(item: ProductionFinding): WarningCopy | null {
  // The producer uses the diagnostic itself for BOTH text placeholders only
  // when there is no mapped dialogue. Code-like real dialogue is never parsed.
  return item.cue_id === null && item.range === null && item.warning !== null &&
    item.source_text === item.warning && item.translated_text === item.warning ? warningCopy(item.warning) : null;
}

/** Paginated DOM view. Paths inside artifacts never become browser media URLs. */
export function mountReviewCenter(container: HTMLElement, raw: unknown, actions: ReviewActions = {}) {
  let payload = readReviewPayload(raw);
  const voices = (actions.voices ?? []).map(item => ({ ...item }));
  if (voices.length > 256 || new Set(voices.map(item => item.voice_id)).size !== voices.length ||
    voices.some(item => !item.voice_id.trim() || item.voice_id !== item.voice_id.trim() || item.voice_id.length > 256 ||
      !item.label.trim() || item.label.length > 512 || /[\u0000-\u001f]/.test(item.voice_id + item.label))) {
    throw new Error("review voices are invalid");
  }
  const doc = container.ownerDocument;
  const prefix = "review-" + ++nextInstance;
  let group: ReviewReason = payload.findings[0]?.reason ?? "TRANSLATION_ANOMALY";
  let page = 0, epoch = 0;
  let selected: string | null = payload.findings[0]?.finding_id ?? null;
  let busy = false, stale = false, dirty = false, destroyed = false, message = "", undoArmed = false, initializeArmed = false;
  let exportArmed: CurrentExportFormat | undefined;
  let publications: Partial<Record<CurrentExportFormat, { receipt: CurrentExportReceipt; revision: string }>> = {};
  let activity: ReviewActivityUpdate | null = null;
  let failureDetails: string | null = null;
  let previewAbort: AbortController | null = null;
  let videoAbort: AbortController | null = null;
  let planAbort: AbortController | null = null;
  let applyAbort: AbortController | null = null;
  const invalidate = () => { failureDetails = null; activity = null; epoch++; previewAbort?.abort(); previewAbort = null; videoAbort?.abort(); videoAbort = null; planAbort?.abort(); planAbort = null; applyAbort?.abort(); applyAbort = null; return epoch; };
  const el = <K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, className?: string) => {
    const node = doc.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const button = (text: string, run: () => void, disabled = false, navigation = true) => {
    const node = el("button", text); node.type = "button"; node.disabled = disabled || busy || stale;
    if (navigation) { node.dataset.reviewNavigation = "true"; node.dataset.reviewBlocked = String(disabled); }
    node.addEventListener("click", () => {
      if (busy || stale || destroyed || (navigation && dirty)) return;
      run();
      // Selection replaces the DOM: keep keyboard focus in the same control.
      if (!node.isConnected) {
        const replacement = [...container.querySelectorAll<HTMLButtonElement>("button")]
          .find(candidate => candidate.textContent === text && !candidate.disabled);
        replacement?.focus();
      }
    }); return node;
  };
  const observeAt = (at: number): ReviewActivityObserver => update => {
    if (at !== epoch || destroyed || !busy || applyAbort?.signal.aborted) return;
    activity = update;
    const node = container.querySelector<HTMLElement>("[data-review-activity]");
    if (node) node.textContent = reviewActivityText(update);
  };
  function render() {
    dirty = false;
    const root = el("section", undefined, "review-center");
    root.setAttribute("aria-label", "Review Center"); root.setAttribute("aria-busy", String(busy));
    root.append(el("h2", actions.correct || actions.planCurrent ? "Kiểm tra và sửa lời thoại" : "Kiểm tra lời thoại"));
    root.append(el("p", payload.schema_version === 3 ? `${payload.findings.length} cảnh báo · Phiên bản ${payload.current_token.revision}` : payload.schema_version === 2 ? `${payload.findings.length} cảnh báo · Phiên bản ${payload.revision}` :
      "Bản review cũ: chỉ xem nội dung, chưa có mốc thời gian xác thực."));
    const status = el("p", message, "review-status"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite"); root.append(status);
    if (actions.openResult && (payload.schema_version === 3 || payload.schema_version === 2 && payload.revision === 0)) {
      const open = button("Xem video kết quả", () => {
        const request = resultVideoRequest(payload), at = epoch;
        const revision = request.expected.kind === "current" ? request.expected.current_token.revision : "0";
        videoAbort?.abort(); const controller = new AbortController(); videoAbort = controller;
        open.disabled = true; status.textContent = "Đang kiểm tra video kết quả trước khi mở…";
        void Promise.resolve().then(() => {
          if (controller.signal.aborted) throw new Error("result video cancelled");
          return actions.openResult!(request, controller.signal);
        }).then(() => {
          if (at === epoch && !destroyed && !controller.signal.aborted && videoAbort === controller) {
            status.textContent = `Đã gửi yêu cầu mở video phiên bản ${revision} trong trình phát riêng. Sau khi sửa hoặc hoàn tác, hãy mở lại để xem bản mới.`;
          }
        }, () => {
          if (at === epoch && !destroyed && !controller.signal.aborted && videoAbort === controller) {
            status.textContent = "Chưa xác nhận được yêu cầu mở video. Hãy kiểm tra trình phát và tải lại lời thoại để kiểm tra phiên hiện hành; không tự gửi lại yêu cầu.";
          }
        }).finally(() => { if (videoAbort === controller) videoAbort = null;
          if (at === epoch && !destroyed) open.disabled = busy || stale; });
      }, false, false); root.append(open);
    }
    if (failureDetails) {
      const details = el("details"); details.append(el("summary", "Chi tiết lỗi"));
      const text = el("pre", failureDetails); text.style.whiteSpace = "pre-wrap"; text.style.overflowWrap = "anywhere";
      details.append(text); root.append(details);
    }
    if (busy && applyAbort) {
      const observed = el("p", activity ? reviewActivityText(activity) : "Đang chờ trạng thái xử lý…", "review-activity");
      observed.dataset.reviewActivity = "true"; observed.setAttribute("aria-live", "off"); root.append(observed);
    }
    if (payload.schema_version === 2 && payload.revision === 0 && actions.initializeCurrent) {
      const start = el("section"); start.setAttribute("aria-label", "Bắt đầu chỉnh sửa");
      if (!initializeArmed) start.append(button("Bật chỉnh sửa cho kết quả này", () => { invalidate(); initializeArmed = true; render(); }));
      else {
        start.append(el("p", "Lưu bản gốc vào lịch sử để có thể sửa từng câu và hoàn tác. Bản xuất hiện tại được giữ nguyên."));
        start.append(button("Xác nhận bắt đầu chỉnh sửa", () => {
          if (payload.schema_version !== 2 || !actions.initializeCurrent) return;
          const previous = payload, request = currentInitializeRequest(previous), at = invalidate();
          const controller = new AbortController(); applyAbort = controller;
          initializeArmed = false; busy = true; message = "Đang kiểm tra và lưu bản gốc vào lịch sử chỉnh sửa…"; render();
          void Promise.resolve().then(() => {
            if (controller.signal.aborted) throw new Error("initialization cancelled");
            return actions.initializeCurrent!(request, controller.signal, observeAt(at));
          }).then(next => {
            if (at !== epoch || destroyed) return;
            try {
              if (controller.signal.aborted) throw new Error("initialization cancelled");
              const checked = readReviewPayload(next);
              if (checked.schema_version !== 3 || checked.job_id !== previous.job_id || checked.current_token.revision !== "0") throw new Error("initialized state changed");
              payload = checked; actions = actions.refreshed?.() ?? actions;
              busy = false; message = "Đã lưu bản gốc. Bạn có thể sửa từng câu, kiểm tra phạm vi chạy lại rồi xác nhận.";
            } catch { busy = false; stale = true; message = "Chưa xác nhận được lịch sử vừa tạo. Hãy tải lại lời thoại để kiểm tra."; }
            render();
          }, error => {
            if (at === epoch && !destroyed) { failureDetails = reviewFailureDetails(error, previous.job_id, "initialize"); busy = false; stale = true; message = "Không xác nhận được kết quả khởi tạo. Hãy tải lại lời thoại trước khi tiếp tục."; render(); }
          }).finally(() => { if (applyAbort === controller) applyAbort = null; });
        }));
        start.append(button("Để sau", () => { initializeArmed = false; render(); }));
      }
      root.append(start);
    }
    if (payload.schema_version === 3 && actions.undoCurrent && BigInt(payload.current_token.revision) > 0n && BigInt(payload.current_token.revision) < 9223372036854775807n) {
      const undo = el("section"); undo.setAttribute("aria-label", "Hoàn tác lời thoại");
      if (!undoArmed) undo.append(button("Hoàn tác thay đổi gần nhất", () => { invalidate(); undoArmed = true; render(); }, !!exportArmed));
      else {
        undo.append(el("p", "Khôi phục bản hợp lệ trước đó từ lịch sử đã lưu. Kết quả hiện tại vẫn được giữ lại."));
        undo.append(button("Xác nhận hoàn tác", () => {
          if (payload.schema_version !== 3 || !actions.undoCurrent) return;
          const previous = payload, request = currentUndoRequest(previous), at = invalidate();
          const controller = new AbortController(); applyAbort = controller;
          undoArmed = false; busy = true; message = "Đang kiểm tra và khôi phục bản trước…"; render();
          void Promise.resolve().then(() => {
            if (controller.signal.aborted) throw new Error("undo cancelled");
            return actions.undoCurrent!(request, controller.signal, observeAt(at));
          }).then(next => {
            if (at !== epoch || destroyed) return;
            try {
              if (controller.signal.aborted) throw new Error("undo cancelled");
              const checked = readReviewPayload(next);
              if (checked.schema_version !== 3 || checked.job_id !== previous.job_id ||
                checked.current_token.revision !== String(BigInt(previous.current_token.revision) + 1n)) throw new Error("undo snapshot changed");
              payload = checked; publications = {}; exportArmed = undefined; busy = stale = false; message = "Đã hoàn tác và kiểm tra bản hiện hành.";
            } catch { busy = false; stale = true; message = "Chưa xác nhận được bản sau hoàn tác. Hãy tải lại lời thoại để kiểm tra."; }
            render();
          }, error => {
            if (at === epoch && !destroyed) { failureDetails = reviewFailureDetails(error, previous.job_id, "undo"); busy = false; stale = true; message = "Không xác nhận được kết quả hoàn tác. Hãy tải lại lời thoại trước khi tiếp tục."; render(); }
          }).finally(() => { if (applyAbort === controller) applyAbort = null; });
        }));
        undo.append(button("Giữ bản hiện tại", () => { undoArmed = false; render(); }));
      }
      root.append(undo);
    }
    if (payload.schema_version === 3 && actions.exportCurrent && BigInt(payload.current_token.revision) > 0n) {
      const output = el("section"); output.setAttribute("aria-label", "Xuất bản lời thoại đã sửa");
      if (!exportArmed) {
        output.append(button("Xuất kết quả chuẩn", () => { invalidate(); exportArmed = "standard"; render(); }, undoArmed));
        output.append(button("Tạo gói chỉnh sửa", () => { invalidate(); exportArmed = "editable"; render(); }, undoArmed || !publications.standard));
      } else {
        const format = exportArmed;
        output.append(el("p", format === "standard" ? "Tạo video, phụ đề và tài nguyên chuẩn cho bản đang xem." : "Gói gồm media, phụ đề và hướng dẫn nhập vào trình biên tập."));
        output.append(button(format === "standard" ? "Xác nhận xuất chuẩn" : "Xác nhận tạo gói", () => {
          if (payload.schema_version !== 3 || !actions.exportCurrent) return;
          const previous = payload, request = currentExportRequest(previous, format), at = invalidate();
          const controller = new AbortController(); applyAbort = controller;
          exportArmed = undefined; busy = true; message = "Đang tạo và kiểm tra bản xuất. Các bản đã có được giữ lại…"; render();
          void Promise.resolve().then(() => {
            if (controller.signal.aborted) throw new Error("export cancelled");
            return actions.exportCurrent!(request, controller.signal, observeAt(at));
          }).then(receipt => {
            if (at !== epoch || destroyed) return;
            try {
              if (controller.signal.aborted || receipt.format !== format) throw new Error("export result changed");
              publications[format] = { receipt: structuredClone(receipt), revision: previous.current_token.revision };
              busy = false; message = format === "standard" ? "Đã tạo và kiểm tra kết quả chuẩn." : "Đã tạo và kiểm tra gói chỉnh sửa.";
            } catch { busy = false; stale = true; message = "Chưa xác nhận được bản xuất. Hãy tải lại lời thoại để kiểm tra."; }
            render();
          }, error => {
            if (at === epoch && !destroyed) { failureDetails = reviewFailureDetails(error, previous.job_id, format === "standard" ? "standard_export" : "editable_export"); busy = false; stale = true; message = "Không xác nhận được kết quả xuất. Các bản đã tạo vẫn được giữ lại; hãy tải lại trước khi tiếp tục."; render(); }
          }).finally(() => { if (applyAbort === controller) applyAbort = null; });
        }));
        output.append(button("Để sau", () => { exportArmed = undefined; render(); }));
      }
      root.append(output);
    }
    for (const format of ["standard", "editable"] as const) {
      const published = publications[format]; if (!published) continue;
      const output = el("section");
      output.append(el("h3", `${format === "standard" ? "Kết quả chuẩn" : "Gói chỉnh sửa"} · Phiên bản ${published.revision}`));
      output.append(el("code", published.receipt.path)); root.append(output);
    }
    if (busy && applyAbort) {
      const cancel = el("button", "Dừng tác vụ"); cancel.type = "button";
      cancel.addEventListener("click", () => { cancel.disabled = true; applyAbort?.abort();
        status.textContent = "Đang dừng tác vụ. Hãy tải lại lời thoại sau khi dừng để kiểm tra bản hiện hành."; });
      root.append(cancel);
    }
    const nav = el("nav", undefined, "review-groups"); nav.setAttribute("aria-label", "Nhóm cảnh báo");
    for (const reason of Object.keys(LABELS) as ReviewReason[]) {
      const count = payload.findings.filter(item => item.reason === reason).length;
      const node = button(`${LABELS[reason]} (${count})`, () => { group = reason; page = 0;
        selected = payload.findings.find(item => item.reason === reason)?.finding_id ?? null; message = ""; invalidate(); render(); }, count === 0);
      node.setAttribute("aria-pressed", String(reason === group)); nav.append(node);
    }
    root.append(nav);
    const items = payload.findings.filter(item => item.reason === group);
    page = Math.min(page, Math.max(0, Math.ceil(items.length / PAGE_SIZE) - 1));
    const shown = items.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
    if (!shown.some(item => item.finding_id === selected)) selected = shown[0]?.finding_id ?? null;
    const layout = el("div", undefined, "review-layout"), list = el("ul", undefined, "review-findings");
    list.setAttribute("aria-label", "Cảnh báo trong nhóm");
    for (const item of shown) {
      const globalCopy = payload.schema_version === 1 ? null : globalWarningCopy(item as ProductionFinding);
      const entry = el("li"), choose = button(`${reviewRangeLabel(payload, item)} · ${(globalCopy?.summary ?? item.translated_text).slice(0, 100)}`, () => {
        selected = item.finding_id; message = ""; invalidate(); render(); });
      choose.setAttribute("aria-pressed", String(item.finding_id === selected)); entry.append(choose); list.append(entry);
    }
    const aside = el("div"); aside.append(list);
    const pages = el("nav", undefined, "review-pages"); pages.setAttribute("aria-label", "Trang cảnh báo");
    const move = (delta: number) => { page += delta; selected = null; message = ""; invalidate(); render(); };
    pages.append(button("Trang trước", () => move(-1), page === 0), el("span", `Trang ${page + 1}/${Math.max(1, Math.ceil(items.length / PAGE_SIZE))}`),
      button("Trang sau", () => move(1), (page + 1) * PAGE_SIZE >= items.length)); aside.append(pages); layout.append(aside);
    const detail = el("section", undefined, "review-detail"); detail.setAttribute("aria-label", "Chi tiết câu thoại");
    const item = items.find(item => item.finding_id === selected);
    if (!item) detail.append(el("p", "Không có cảnh báo trong nhóm này."));
    else {
      const bound = payload.schema_version !== 1 ? item as ProductionFinding : null;
      const globalCopy = bound ? globalWarningCopy(bound) : null;
      detail.append(el("h3", reviewRangeLabel(payload, item)));
      const metadata = el("dl");
      for (const [label, value] of [["Lời gốc", globalCopy ? "Cảnh báo toàn video, chưa gắn với lời thoại gốc." : item.source_text], ["Người nói", item.speaker ?? "Chưa xác định"],
        ["Giọng hiện tại", voices.find(voice => voice.voice_id === item.voice)?.label ?? item.voice ?? "Chưa có giọng"],
        ["Độ tin cậy", item.confidence === null ? "Chưa có phép đo" : item.confidence]]) {
        metadata.append(el("dt", label), el("dd", value));
      }
      detail.append(metadata);
      if (bound?.warning) {
        const copy = warningCopy(bound.warning);
        detail.append(el("p", copy.summary + " " + copy.action, "review-warning"));
      }
      const canEdit = payload.schema_version === 2 && !!bound?.cue_id && !!bound.range && !!actions.correct;
      const canPlan = payload.schema_version === 3 && !!bound?.cue_id && !!bound.range && !!actions.planCurrent;
      const canDraft = (canEdit || canPlan) && !undoArmed && !exportArmed && !initializeArmed;
      const canPreview = (payload.schema_version === 2 || payload.schema_version === 3 && payload.mode === "b2") && !!bound?.cue_id && !!bound.output_preview && !!actions.preview;
      const listen = button("Nghe câu này", () => {
        if (payload.schema_version === 1 || !bound?.cue_id || !actions.preview) return;
        const at = epoch; const request = previewRequest(payload, bound.finding_id);
        previewAbort?.abort(); const playback = new AbortController(); previewAbort = playback;
        listen.disabled = true; status.textContent = "Đang kiểm tra và phát câu thoại…";
        void Promise.resolve().then(() => { if (playback.signal.aborted) throw new Error("preview cancelled");
          return actions.preview!(request, playback.signal); }).then(() => {
          if (at === epoch && !destroyed && !playback.signal.aborted && previewAbort === playback) status.textContent = "Đã phát câu thoại.";
        }, error => { if (at === epoch && !destroyed && !playback.signal.aborted && previewAbort === playback) status.textContent = reviewAudioFailureHint(error);
        }).finally(() => { if (previewAbort === playback) previewAbort = null;
          if (at === epoch && !destroyed) listen.disabled = busy || stale; });
      }, !canPreview, false); detail.append(listen);
      const form = el("form"), translation = el("textarea"), voice = el("select");
      translation.id = prefix + "-translation";
      translation.value = globalCopy ? "Cảnh báo toàn video, chưa có bản dịch gắn với câu thoại." : item.translated_text; translation.maxLength = 8192;
      translation.rows = 4; translation.disabled = !canDraft || busy || stale;
      const translationLabel = el("label", "Bản dịch"); translationLabel.htmlFor = translation.id;
      voice.id = prefix + "-voice"; voice.disabled = !canDraft || busy || stale || !voices.length || payload.schema_version === 3 && payload.mode === "b1";
      if (!voices.some(candidate => candidate.voice_id === item.voice)) {
        const retained = el("option", item.voice ?? "Giữ nguyên giọng hiện tại"); retained.value = item.voice ?? ""; voice.append(retained);
      }
      for (const candidate of voices) { const option = el("option", candidate.label); option.value = candidate.voice_id; voice.append(option); }
      voice.value = item.voice ?? "";
      const voiceLabel = el("label", "Giọng cho câu này"); voiceLabel.htmlFor = voice.id;
      const planText = el("p", canDraft ? "Sửa bản dịch hoặc chọn giọng để xem các bước cần chạy lại." :
        "Chỉ xem lời thoại. Chức năng sửa câu chưa sẵn sàng trong phiên bản này.", "review-plan");
      planText.setAttribute("role", "status");
      const submit = el("button", "Xác nhận sửa câu này"); submit.type = "submit"; submit.disabled = true;
      const discard = el("button", "Bỏ thay đổi chưa lưu"); discard.type = "button"; discard.disabled = true;
      const inspect = el("button", "Xem phạm vi chạy lại"); inspect.type = "button"; inspect.disabled = true;
      let verifiedPlan: CurrentCorrectionPlan | null = null;
      if (canPlan) { submit.textContent = actions.applyCurrent ? "Xác nhận và dựng lại câu này" : "Chưa thể áp dụng thay đổi"; }
      function candidateCorrection() {
        const correction: ProductionCorrection = { finding_id: item!.finding_id };
        if (translation.value.trim() !== item!.translated_text) correction.translation = payload.schema_version === 3 ? translation.value.trim() : translation.value;
        if (voice.value !== (item!.voice ?? "")) {
          if (!voices.some(value => value.voice_id === voice.value)) throw new Error("invalid voice");
          correction.voice = voice.value;
        }
        return correction;
      }
      function candidate() {
        const correction = candidateCorrection();
        return { correction, plan: previewProductionCorrection(payload, correction) };
      }
      function refreshPlan() {
        submit.disabled = true;
        inspect.disabled = true;
        verifiedPlan = null;
        planAbort?.abort(); planAbort = null;
        if (!canDraft || busy || stale) return;
        dirty = translation.value.trim() !== item!.translated_text || voice.value !== (item!.voice ?? "");
        discard.disabled = !dirty;
        for (const control of root.querySelectorAll<HTMLButtonElement>("button[data-review-navigation]")) {
          control.disabled = control.dataset.reviewBlocked === "true" || dirty;
        }
        if (canPlan && payload.schema_version === 3) {
          try { currentPlanRequest(payload, candidateCorrection()); inspect.disabled = false;
            planText.textContent = "Kiểm tra phạm vi chạy lại trước khi áp dụng. Bản xuất hiện tại được giữ nguyên.";
          } catch { planText.textContent = "Nhập bản dịch hợp lệ hoặc chọn một giọng khác để tiếp tục."; }
          return;
        }
        try { const { plan } = candidate(); planText.textContent = "Chạy lại: " + plan.stages_to_rerun.map(stage => STAGES[stage] ?? stage).join(" → ") +
          ". Giữ kết quả nhận dạng lời nói và chữ trên hình. Giữ bản xuất hiện tại đến khi bản thay thế được kiểm tra."; submit.disabled = false;
        } catch { planText.textContent = "Nhập bản dịch hợp lệ hoặc chọn một giọng khác để tiếp tục."; }
      }
      discard.addEventListener("click", () => { translation.value = item.translated_text; voice.value = item.voice ?? "";
        refreshPlan(); translation.focus(); });
      translation.addEventListener("input", refreshPlan); voice.addEventListener("change", refreshPlan);
      inspect.addEventListener("click", () => {
        if (!canPlan || busy || stale || destroyed || payload.schema_version !== 3 || !actions.planCurrent) return;
        let request: CurrentPlanRequest;
        try { request = currentPlanRequest(payload, candidateCorrection()); } catch { refreshPlan(); return; }
        planAbort?.abort(); const abort = new AbortController(); planAbort = abort;
        const at = epoch; inspect.disabled = true; planText.textContent = "Đang kiểm tra bản hiện hành và phạm vi chạy lại…";
        void actions.planCurrent(request, abort.signal).then(result => {
          if (at !== epoch || destroyed || abort.signal.aborted) return;
          verifiedPlan = result; submit.disabled = !actions.applyCurrent;
          planText.textContent = "Chạy lại: " + result.plan.stages_to_rerun.map(stage => STAGES[stage] ?? stage).join(" → ") +
            ". Giữ kết quả nhận dạng lời nói và chữ trên hình. Kế hoạch chưa được áp dụng; bản xuất hiện tại được giữ nguyên.";
        }, () => { if (at === epoch && !destroyed && !abort.signal.aborted) planText.textContent = "Chưa xác thực được kế hoạch. Hãy tải lại lời thoại trước khi tiếp tục.";
        }).finally(() => { if (planAbort === abort) { planAbort = null; if (at === epoch && !destroyed) inspect.disabled = false; } });
      });
      form.addEventListener("submit", event => {
        event.preventDefault(); if (busy || stale || destroyed) return;
        if (canPlan && payload.schema_version === 3 && actions.applyCurrent && verifiedPlan) {
          let confirmation: CurrentConfirmation;
          try {
            const selected = currentPlanRequest(payload, candidateCorrection());
            if (JSON.stringify(selected.changes) !== JSON.stringify(verifiedPlan.changes)) throw new Error("draft changed");
            confirmation = currentConfirmation(payload, verifiedPlan);
          } catch { refreshPlan(); return; }
          const before = payload, at = invalidate(), abort = new AbortController(); applyAbort = abort;
          busy = true; message = "Đang dựng và kiểm tra bản thay thế. Bản xuất hiện tại được giữ nguyên…"; render();
          void actions.applyCurrent(confirmation, abort.signal, observeAt(at)).then(next => {
            if (at !== epoch || destroyed || abort.signal.aborted) return;
            try {
              const checked = readReviewPayload(next);
              if (checked.schema_version !== 3 || checked.job_id !== before.job_id || checked.current_token.revision !== confirmation.plan.candidate_revision) throw new Error("applied version changed");
              payload = checked; publications = {}; exportArmed = undefined; message = "Đã kiểm tra và áp dụng bản thay thế. Lời thoại đang hiển thị là bản hiện hành.";
            } catch { stale = true; message = "Kết quả đã đổi phiên bản. Hãy tải lại để kiểm tra trước khi sửa tiếp."; }
          }, error => { if (at === epoch && !destroyed) { failureDetails = reviewFailureDetails(error, before.job_id, "apply"); stale = true;
            message = reviewFailureHint(error, before.job_id, "apply") ?? "Chưa xác định tác vụ đã áp dụng hay chưa. Hãy tải lại lời thoại để kiểm tra; không tự gửi lại yêu cầu.";
          } }).finally(() => { if (applyAbort === abort) applyAbort = null;
            if (at === epoch && !destroyed) { busy = false; render(); } });
          return;
        }
        if (!canEdit || payload.schema_version !== 2 || !actions.correct) return;
        let change: ReturnType<typeof candidate>;
        try { change = candidate(); } catch { refreshPlan(); return; }
        const before = payload, request = correctionRequest(before, change.correction), at = invalidate();
        busy = true; message = "Đang lưu yêu cầu sửa câu thoại…"; render();
        void Promise.resolve().then(() => actions.correct!(request)).then(response => {
          if (at !== epoch || destroyed) return;
          try { payload = acceptCorrectionResponse(before, request, response, change.plan);
            message = "Đã lưu yêu cầu sửa. Bản xuất mới cần được xử lý và kiểm tra.";
          } catch { stale = true; message = "Kết quả phản hồi đã thay đổi. Hãy tải lại review trước khi sửa tiếp."; }
        }, () => { if (at === epoch && !destroyed) { stale = true;
          message = "Chưa xác định yêu cầu đã lưu hay chưa. Hãy tải lại review trước khi sửa tiếp.";
        } }).finally(() => { if (at === epoch && !destroyed) { busy = false; render(); } });
      });
      form.append(translationLabel, translation, voiceLabel, voice, planText); if (canPlan) form.append(inspect);
      form.append(submit, discard); detail.append(form);
      if (!canDraft) detail.append(el("p", bound?.cue_id ? "Chức năng sửa câu chưa sẵn sàng cho kết quả này." :
        "Cảnh báo này chưa gắn với một câu thoại xác thực; chỉ có thể xem."));
    }
    layout.append(detail); root.append(layout); container.replaceChildren(root);
  }
  render();
  return {
    hasUnsavedChanges() { return !destroyed && dirty; },
    replace(value: unknown) { const next = readReviewPayload(value); if (destroyed) throw new Error("review view was destroyed");
      invalidate(); payload = next; publications = {}; exportArmed = undefined; busy = stale = undoArmed = initializeArmed = false; message = ""; page = 0;
      group = payload.findings[0]?.reason ?? "TRANSLATION_ANOMALY"; selected = payload.findings[0]?.finding_id ?? null; render(); },
    destroy() { invalidate(); destroyed = true; container.replaceChildren(); },
  };
}
