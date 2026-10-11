import { mountReviewCenter } from "../../apps/desktop/src/features/review/view.ts";
import { previewProductionCorrection, type ProductionSnapshot } from "../../apps/desktop/src/features/review/wire.ts";
import type { ReviewCorrectionRequest } from "../../apps/desktop/src/features/review/session.ts";
import { ReviewConnection } from "../../apps/desktop/src/features/review/connection.ts";

const container = document.querySelector<HTMLElement>("#review")!;
const log = document.querySelector<HTMLElement>("#qa-log")!;
const snapshot: ProductionSnapshot = { schema_version: 2, job_id: "qa-review-job", revision: 0,
  time_base: { numerator: "1", denominator: "48000" }, last_plan: null,
  findings: Array.from({ length: 65 }, (_, i) => ({ finding_id: "finding-" + i, cue_id: "cue-" + i,
    reason: "TTS_FAILURE", range: { start_ticks: String(i * 48000), end_ticks: String((i + 1) * 48000) },
    source_text: i === 0 ? '<img src=x onerror="alert(1)">Stay here.' : "Line " + (i + 1),
    translated_text: i === 0 ? "Ngồi yên." : "Câu " + (i + 1), voice: "vi-truc-ly-vieneu3-v1", speaker: "speaker-1",
    confidence: null, confidence_source: null, warning: "Chưa có điểm đo tự động cho câu này.",
    output_preview: "file:///C:/untrusted-preview.wav", affected_stages: ["tts", "mix", "render"] })) };
snapshot.findings.push({ ...snapshot.findings[0], finding_id: "translation-0", reason: "TRANSLATION_ANOMALY" });
snapshot.findings.push({ ...snapshot.findings[0], finding_id: "global-warning", cue_id: null, range: null,
  reason: "SYNC_QC_WARNING", output_preview: null, warning: "Cảnh báo cho toàn video, chưa gắn với câu thoại." });
let current = structuredClone(snapshot), delayed = false, wrongJob = false;
let release: (() => void) | null = null;
const note = (kind: string, request: unknown) => { log.textContent = JSON.stringify({ kind, request }, null, 2); };
function response(request: ReviewCorrectionRequest) {
  const next = structuredClone(current); next.revision++; next.last_plan = previewProductionCorrection(current, request.correction);
  for (const item of next.findings) if (item.cue_id === request.cue_id) {
    if (request.correction.translation !== undefined) item.translated_text = request.correction.translation;
    if (request.correction.voice !== undefined) item.voice = request.correction.voice;
  }
  if (wrongJob) next.job_id = "wrong-job";
  return next;
}
const choices = [{ voice_id: "vi-truc-ly-vieneu3-v1", label: "Trúc Ly" }, { voice_id: "vi-hai-dang-vieneu3-v1", label: "Hải Đăng" }];
const connection = new ReviewConnection({
  async snapshot() { return structuredClone(current); },
  async preview(request, signal) {
    note("preview-semantic-only", request);
    if (delayed) await new Promise<void>((resolve, reject) => {
      const cancel = () => { note("preview-cancelled", request); reject(new DOMException("Cancelled", "AbortError")); };
      signal.addEventListener("abort", cancel, { once: true });
      release = () => { signal.removeEventListener("abort", cancel); resolve(); };
    });
  },
  async correct(request) {
    note("correction-demo-only", request); const next = response(request);
    if (delayed) await new Promise<void>(resolve => { release = resolve; });
    current = next; return next;
  },
});
let view = mountReviewCenter(container, (await connection.load(snapshot.job_id))!, connection.actions(choices));
document.querySelector("#reset")!.addEventListener("click", async () => {
  current = structuredClone(snapshot); view.destroy(); log.textContent = ""; wrongJob = false;
  const fresh = await connection.load(snapshot.job_id);
  if (fresh) view = mountReviewCenter(container, fresh, connection.actions(choices));
});
document.querySelector("#legacy")!.addEventListener("click", () => { const { cue_id, warning, confidence_source, ...first } = snapshot.findings[0];
  connection.clear(); view.destroy();
  view = mountReviewCenter(container, { schema_version: 1, findings: [{ ...first, confidence: "0.4" }] }, { voices: choices }); });
document.querySelector("#delay")!.addEventListener("click", () => { delayed = !delayed; log.textContent = "Trì hoãn: " + delayed; });
document.querySelector("#release")!.addEventListener("click", () => { release?.(); release = null; });
document.querySelector("#reject")!.addEventListener("click", () => { wrongJob = true; log.textContent = "Lượt sửa tiếp theo sẽ trả sai job."; });
