import { currentPlanRequest, readCurrentPlan, type CurrentCorrectionPlan } from "./current-plan.ts";
import { sameCurrentToken } from "./session.ts";
import { readReviewPayload, type CurrentReviewSnapshot } from "./wire.ts";

export type CurrentConfirmation = { schema_version: 1; kind: "review_current_confirmation"; confirm: true; plan: CurrentCorrectionPlan };
const fields = (raw: unknown, keys: readonly string[]): raw is Record<string, unknown> => !!raw && typeof raw === "object" && !Array.isArray(raw) &&
  Object.keys(raw).length === keys.length && keys.every(k => Object.hasOwn(raw, k));

export function currentConfirmation(snapshot: CurrentReviewSnapshot, plan: CurrentCorrectionPlan): CurrentConfirmation {
  const request = currentPlanRequest(snapshot, { finding_id: plan.finding_id, ...plan.changes });
  return { schema_version: 1, kind: "review_current_confirmation", confirm: true, plan: readCurrentPlan(snapshot, request, plan) };
}

export function readCurrentConfirmation(snapshot: CurrentReviewSnapshot, raw: unknown): CurrentConfirmation {
  if (!fields(raw, ["schema_version", "kind", "confirm", "plan"]) || raw.schema_version !== 1 || raw.kind !== "review_current_confirmation" || raw.confirm !== true ||
    !raw.plan || typeof raw.plan !== "object" || Array.isArray(raw.plan)) throw new Error("explicit current plan confirmation required");
  return currentConfirmation(snapshot, raw.plan as CurrentCorrectionPlan);
}

/** Logical proposal metadata never substitutes for the physical committed
 * current snapshot. The native host reopens it before returning this result. */
export function readAppliedCurrent(previous: CurrentReviewSnapshot, confirmation: CurrentConfirmation, raw: unknown): CurrentReviewSnapshot {
  const accepted = readCurrentConfirmation(previous, confirmation), plan = accepted.plan;
  if (!fields(raw, ["schema_version", "kind", "before_token", "session_id", "snapshot"]) || raw.schema_version !== 1 || raw.kind !== "review_desktop_applied_current" ||
    !sameCurrentToken(previous.current_token, raw.before_token) || typeof raw.session_id !== "string" || !/^[A-Za-z0-9-]{16,64}$/.test(raw.session_id)) throw new Error("applied current result is not bound to this confirmation");
  const next = readReviewPayload(raw.snapshot);
  if (next.schema_version !== 3 || next.job_id !== previous.job_id || next.mode !== previous.mode ||
    next.current_token.revision !== plan.candidate_revision || next.current_token.original_manifest_sha256 !== previous.current_token.original_manifest_sha256 ||
    next.current_token.snapshot_sha256 === previous.current_token.snapshot_sha256 || next.current_token.graph_sha256 === previous.current_token.graph_sha256 ||
    next.findings.length !== previous.findings.length) throw new Error("applied current identity changed unexpectedly");
  const previews = new Set(next.findings.map(f => f.output_preview));
  if (previews.size !== 1 || previews.has(null)) throw new Error("applied current snapshot lacks its native retained preview");
  const expected = structuredClone(previous);
  expected.current_token = { ...next.current_token }; expected.last_plan = structuredClone(plan.plan);
  for (let i = 0; i < expected.findings.length; i++) {
    const item = expected.findings[i]; item.output_preview = next.findings[i].output_preview;
    if (item.cue_id !== plan.cue_id) continue;
    if (plan.changes.translation !== undefined) item.translated_text = plan.changes.translation;
    if (plan.changes.voice !== undefined) item.voice = plan.changes.voice;
  }
  // The same strict display parser gives both objects the same field shape.
  // Arrays and exact text/confidence/ticks/token strings are never sorted or
  // rounded, and this display comparison never rewrites durable snapshot bytes.
  if (JSON.stringify(readReviewPayload(expected)) !== JSON.stringify(next)) throw new Error("applied current snapshot lost source evidence, prior cues or the confirmed change");
  return next;
}
