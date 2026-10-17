import { sameCurrentToken, type NativeCurrentToken } from "./session.ts";
import { readReviewPayload, type CurrentReviewSnapshot } from "./wire.ts";

export type CurrentUndoRequest = { schema_version: 1; kind: "review_current_undo"; confirm: true; current_token: NativeCurrentToken };
const fields = (raw: unknown, keys: readonly string[]): raw is Record<string, unknown> => !!raw && typeof raw === "object" && !Array.isArray(raw) &&
  Object.keys(raw).length === keys.length && keys.every(k => Object.hasOwn(raw, k));

export function currentUndoRequest(snapshot: CurrentReviewSnapshot): CurrentUndoRequest {
  const checked = readReviewPayload(snapshot);
  if (checked.schema_version !== 3 || BigInt(checked.current_token.revision) === 0n || BigInt(checked.current_token.revision) >= 9223372036854775807n) {
    throw new Error("current undo requires an existing revision with room for the next monotonic token");
  }
  return { schema_version: 1, kind: "review_current_undo", confirm: true, current_token: { ...checked.current_token } };
}

export function readCurrentUndoRequest(snapshot: CurrentReviewSnapshot, raw: unknown): CurrentUndoRequest {
  const expected = currentUndoRequest(snapshot);
  if (!fields(raw, ["schema_version", "kind", "confirm", "current_token"]) || raw.schema_version !== 1 || raw.kind !== expected.kind || raw.confirm !== true ||
    !sameCurrentToken(expected.current_token, raw.current_token)) throw new Error("explicit undo requires the actual complete current token");
  return expected;
}

/** The native prior-graph reader owns target selection and physical admission.
 * This client check protects immutable display evidence; it does not grant a
 * browser-selected snapshot or graph authority to become the undo target. */
export function readUndoneCurrent(previous: CurrentReviewSnapshot, request: CurrentUndoRequest, raw: unknown): CurrentReviewSnapshot {
  readCurrentUndoRequest(previous, request);
  if (!fields(raw, ["schema_version", "kind", "before_token", "session_id", "snapshot"]) || raw.schema_version !== 1 || raw.kind !== "review_desktop_undone_current" ||
    !sameCurrentToken(previous.current_token, raw.before_token) || typeof raw.session_id !== "string" || !/^[A-Za-z0-9-]{16,64}$/.test(raw.session_id)) {
    throw new Error("undo result is not bound to this current session");
  }
  const next = readReviewPayload(raw.snapshot);
  if (next.schema_version !== 3 || next.job_id !== previous.job_id || next.mode !== previous.mode ||
    next.current_token.revision !== String(BigInt(previous.current_token.revision) + 1n) ||
    next.current_token.original_manifest_sha256 !== previous.current_token.original_manifest_sha256 ||
    next.current_token.snapshot_sha256 === previous.current_token.snapshot_sha256 || next.current_token.graph_sha256 === previous.current_token.graph_sha256 ||
    next.findings.length !== previous.findings.length) throw new Error("undo did not return the next admitted current identity");
  const expected = structuredClone(previous);
  expected.current_token = { ...next.current_token }; expected.last_plan = structuredClone(next.last_plan);
  for (let i = 0; i < expected.findings.length; i++) {
    // Undo may restore several prior cue edits together. Source, identity,
    // exact ticks/confidence/warnings and unrelated evidence stay unchanged.
    expected.findings[i].translated_text = next.findings[i].translated_text;
    expected.findings[i].voice = next.findings[i].voice;
    expected.findings[i].output_preview = next.findings[i].output_preview;
  }
  if (JSON.stringify(readReviewPayload(expected)) !== JSON.stringify(next)) throw new Error("undo changed immutable source evidence or cue identity");
  return next;
}
