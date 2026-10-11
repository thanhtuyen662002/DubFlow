import { previewProductionCorrection, readReviewPayload, type ProductionCorrection, type ProductionPlan,
  type ProductionSnapshot, type ReviewPayload } from "./wire.ts";

export type ReviewCueRequest = { job_id: string; revision: number; finding_id: string; cue_id: string };
export type ReviewCorrectionRequest = ReviewCueRequest & { correction: ProductionCorrection };

/** Backend mutations still require their own durable CAS and artifact validation. */
export function correctionRequest(payload: ReviewPayload, correction: ProductionCorrection): ReviewCorrectionRequest {
  previewProductionCorrection(payload, correction);
  if (payload.schema_version !== 2) throw new Error("legacy review is display-only");
  const item = payload.findings.find(finding => finding.finding_id === correction.finding_id)!;
  return { job_id: payload.job_id, revision: payload.revision, finding_id: item.finding_id, cue_id: item.cue_id!,
    correction: { finding_id: item.finding_id,
      ...(correction.translation === undefined ? {} : { translation: correction.translation.trim() }),
      ...(correction.voice === undefined ? {} : { voice: correction.voice.trim() }) } };
}

/** A response may only acknowledge this edit; it cannot switch jobs or rewrite other cues. */
export function acceptCorrectionResponse(previous: ProductionSnapshot, request: ReviewCorrectionRequest,
  raw: unknown, expectedPlan: ProductionPlan): ProductionSnapshot {
  const expectedRequest = correctionRequest(previous, request.correction);
  if (JSON.stringify(expectedRequest) !== JSON.stringify(request)) throw new Error("correction request changed");
  const expected = structuredClone(previous);
  expected.revision++;
  expected.last_plan = previewProductionCorrection(previous, request.correction);
  if (JSON.stringify(expected.last_plan) !== JSON.stringify(expectedPlan)) throw new Error("correction plan changed");
  for (const item of expected.findings) {
    if (item.cue_id !== request.cue_id) continue;
    if (request.correction.translation !== undefined) item.translated_text = request.correction.translation;
    if (request.correction.voice !== undefined) item.voice = request.correction.voice;
  }
  const next = readReviewPayload(raw);
  if (next.schema_version !== 2 || JSON.stringify(next) !== JSON.stringify(expected)) {
    throw new Error("correction response is stale or does not match the requested cue");
  }
  return next;
}
