import type { ReviewReason } from "./model.ts";
import { previewProductionCorrection, readReviewPayload, reviewRangeLabel,
  type ProductionCorrection, type ProductionFinding, type ReviewPayload } from "./wire.ts";
import { acceptCorrectionResponse, correctionRequest, type ReviewCorrectionRequest, type ReviewCueRequest } from "./session.ts";

export type ReviewVoice = { voice_id: string; label: string };
export type ReviewActions = {
  /** Supply voices compatible with the pinned producer/model. */
  voices?: readonly ReviewVoice[];
  preview?: (request: ReviewCueRequest, signal: AbortSignal) => Promise<void>;
  correct?: (request: ReviewCorrectionRequest) => Promise<unknown>;
};
const LABELS: Record<ReviewReason, string> = {
  ASR_OCR_CONFLICT: "Lời nói và chữ trên hình", SPEAKER_UNCERTAINTY: "Người nói",
  TRANSLATION_ANOMALY: "Bản dịch", TTS_FAILURE: "Giọng đọc", INPAINT_RISK: "Xóa chữ", SYNC_QC_WARNING: "Đồng bộ và chất lượng",
};
const STAGES: Record<string, string> = { translation: "Bản dịch", subtitles: "Phụ đề", tts: "Giọng đọc",
  mix: "Trộn âm thanh", render: "Dựng video", qc: "Kiểm tra chất lượng", export: "Xuất kết quả" };
const PAGE_SIZE = 30;
let nextInstance = 0;

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
  let busy = false, stale = false, dirty = false, destroyed = false, message = "";
  let previewAbort: AbortController | null = null;
  const invalidate = () => { epoch++; previewAbort?.abort(); previewAbort = null; return epoch; };
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
  function render() {
    dirty = false;
    const root = el("section", undefined, "review-center");
    root.setAttribute("aria-label", "Review Center"); root.setAttribute("aria-busy", String(busy));
    root.append(el("h2", "Kiểm tra và sửa lời thoại"));
    root.append(el("p", payload.schema_version === 2 ? `${payload.findings.length} cảnh báo · Phiên bản ${payload.revision}` :
      "Bản review cũ: chỉ xem nội dung, chưa có mốc thời gian xác thực."));
    const status = el("p", message, "review-status"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite"); root.append(status);
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
      const entry = el("li"), choose = button(`${reviewRangeLabel(payload, item)} · ${item.translated_text.slice(0, 100)}`, () => {
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
      detail.append(el("h3", reviewRangeLabel(payload, item)));
      const metadata = el("dl");
      for (const [label, value] of [["Lời gốc", item.source_text], ["Người nói", item.speaker ?? "Chưa xác định"],
        ["Giọng hiện tại", voices.find(voice => voice.voice_id === item.voice)?.label ?? item.voice ?? "Chưa có giọng"],
        ["Độ tin cậy", item.confidence === null ? "Chưa có phép đo" : item.confidence]]) {
        metadata.append(el("dt", label), el("dd", value));
      }
      detail.append(metadata);
      const bound = payload.schema_version === 2 ? item as ProductionFinding : null;
      if (bound?.warning) detail.append(el("p", bound.warning, "review-warning"));
      const canEdit = !!bound?.cue_id && !!bound.range && !!actions.correct;
      const canPreview = !!bound?.cue_id && !!bound.output_preview && !!actions.preview;
      const listen = button("Nghe câu này", () => {
        if (payload.schema_version !== 2 || !bound?.cue_id || !actions.preview) return;
        const at = epoch; const request = { job_id: payload.job_id, revision: payload.revision,
          finding_id: bound.finding_id, cue_id: bound.cue_id };
        previewAbort?.abort(); const playback = new AbortController(); previewAbort = playback;
        listen.disabled = true; status.textContent = "Đang kiểm tra và phát câu thoại…";
        void Promise.resolve().then(() => { if (playback.signal.aborted) throw new Error("preview cancelled");
          return actions.preview!(request, playback.signal); }).then(() => {
          if (at === epoch && !destroyed) status.textContent = "Đã phát câu thoại.";
        }, () => { if (at === epoch && !destroyed) status.textContent = "Không thể phát câu thoại. Hãy kiểm tra lại kết quả.";
        }).finally(() => { if (previewAbort === playback) previewAbort = null;
          if (at === epoch && !destroyed) listen.disabled = busy || stale; });
      }, !canPreview, false); detail.append(listen);
      const form = el("form"), translation = el("textarea"), voice = el("select");
      translation.id = prefix + "-translation"; translation.value = item.translated_text; translation.maxLength = 8192;
      translation.rows = 4; translation.disabled = !canEdit || busy || stale;
      const translationLabel = el("label", "Bản dịch"); translationLabel.htmlFor = translation.id;
      voice.id = prefix + "-voice"; voice.disabled = !canEdit || busy || stale || !voices.length;
      if (!voices.some(candidate => candidate.voice_id === item.voice)) {
        const retained = el("option", item.voice ?? "Giữ nguyên giọng hiện tại"); retained.value = item.voice ?? ""; voice.append(retained);
      }
      for (const candidate of voices) { const option = el("option", candidate.label); option.value = candidate.voice_id; voice.append(option); }
      voice.value = item.voice ?? "";
      const voiceLabel = el("label", "Giọng cho câu này"); voiceLabel.htmlFor = voice.id;
      const planText = el("p", "Sửa bản dịch hoặc chọn giọng để xem các bước cần chạy lại.", "review-plan");
      planText.setAttribute("role", "status");
      const submit = el("button", "Xác nhận sửa câu này"); submit.type = "submit"; submit.disabled = true;
      const discard = el("button", "Bỏ thay đổi chưa lưu"); discard.type = "button"; discard.disabled = true;
      function candidate() {
        const correction: ProductionCorrection = { finding_id: item!.finding_id };
        if (translation.value.trim() !== item!.translated_text) correction.translation = translation.value;
        if (voice.value !== (item!.voice ?? "")) {
          if (!voices.some(value => value.voice_id === voice.value)) throw new Error("invalid voice");
          correction.voice = voice.value;
        }
        return { correction, plan: previewProductionCorrection(payload, correction) };
      }
      function refreshPlan() {
        submit.disabled = true;
        if (!canEdit || busy || stale) return;
        dirty = translation.value.trim() !== item!.translated_text || voice.value !== (item!.voice ?? "");
        discard.disabled = !dirty;
        for (const control of root.querySelectorAll<HTMLButtonElement>("button[data-review-navigation]")) {
          control.disabled = control.dataset.reviewBlocked === "true" || dirty;
        }
        try { const { plan } = candidate(); planText.textContent = "Chạy lại: " + plan.stages_to_rerun.map(stage => STAGES[stage] ?? stage).join(" → ") +
          ". Giữ kết quả nhận dạng lời nói và chữ trên hình. Giữ bản xuất hiện tại đến khi bản thay thế được kiểm tra."; submit.disabled = false;
        } catch { planText.textContent = "Nhập bản dịch hợp lệ hoặc chọn một giọng khác để tiếp tục."; }
      }
      discard.addEventListener("click", () => { translation.value = item.translated_text; voice.value = item.voice ?? "";
        refreshPlan(); translation.focus(); });
      translation.addEventListener("input", refreshPlan); voice.addEventListener("change", refreshPlan);
      form.addEventListener("submit", event => {
        event.preventDefault(); if (!canEdit || busy || stale || destroyed || payload.schema_version !== 2 || !actions.correct) return;
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
      form.append(translationLabel, translation, voiceLabel, voice, planText, submit, discard); detail.append(form);
      if (!canEdit) detail.append(el("p", bound?.cue_id ? "Chức năng sửa câu chưa sẵn sàng cho kết quả này." :
        "Cảnh báo này chưa gắn với một câu thoại xác thực; chỉ có thể xem."));
    }
    layout.append(detail); root.append(layout); container.replaceChildren(root);
  }
  render();
  return {
    replace(value: unknown) { const next = readReviewPayload(value); if (destroyed) throw new Error("review view was destroyed");
      invalidate(); payload = next; busy = stale = false; message = ""; page = 0;
      group = payload.findings[0]?.reason ?? "TRANSLATION_ANOMALY"; selected = payload.findings[0]?.finding_id ?? null; render(); },
    destroy() { invalidate(); destroyed = true; container.replaceChildren(); },
  };
}
