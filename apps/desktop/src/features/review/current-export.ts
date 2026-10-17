import { sameCurrentToken, type NativeCurrentToken } from "./session.ts";
import { readReviewPayload, type CurrentReviewSnapshot } from "./wire.ts";

export type CurrentExportFormat = "standard" | "editable";
export type CurrentExportRequest = { schema_version: 1; kind: "review_current_export_confirmation"; confirm: true; format: CurrentExportFormat; current_token: NativeCurrentToken };
export type CurrentExportReceipt = { schema_version: 1; kind: "review_desktop_export_receipt"; format: CurrentExportFormat; generation: string; path: string;
  manifest_sha256: string; standard_manifest_sha256: string; files_verified: number; standard_bundle_published: true; production_editable_pack_published: boolean; vendor_execution: false };
const fields = (raw: unknown, keys: readonly string[]): raw is Record<string, unknown> => !!raw && typeof raw === "object" && !Array.isArray(raw) &&
  Object.keys(raw).length === keys.length && keys.every(k => Object.hasOwn(raw, k));
const hash = (v: unknown): v is string => typeof v === "string" && /^sha256:[0-9a-f]{64}$/.test(v);

export function currentExportRequest(snapshot: CurrentReviewSnapshot, format: CurrentExportFormat): CurrentExportRequest {
  const checked = readReviewPayload(snapshot);
  if (checked.schema_version !== 3 || !["standard", "editable"].includes(format)) throw new Error("current export requires its actual snapshot and format");
  return { schema_version: 1, kind: "review_current_export_confirmation", confirm: true, format, current_token: { ...checked.current_token } };
}

export function readCurrentExportRequest(snapshot: CurrentReviewSnapshot, raw: unknown): CurrentExportRequest {
  if (!fields(raw, ["schema_version", "kind", "confirm", "format", "current_token"]) || raw.schema_version !== 1 || raw.kind !== "review_current_export_confirmation" ||
    raw.confirm !== true || raw.format !== "standard" && raw.format !== "editable" || !sameCurrentToken(snapshot.current_token, raw.current_token)) {
    throw new Error("explicit export requires the complete actual current token and closed format");
  }
  return currentExportRequest(snapshot, raw.format);
}

/** This is a display receipt from the native publisher. Its path is never an
 * IO capability, media URL, processing root or automatic vendor launch target. */
export function readExportedCurrent(previous: CurrentReviewSnapshot, request: CurrentExportRequest, raw: unknown,
  standard?: CurrentExportReceipt): { snapshot: CurrentReviewSnapshot; receipt: CurrentExportReceipt } {
  const accepted = readCurrentExportRequest(previous, request);
  if (!fields(raw, ["schema_version", "kind", "format", "current_token", "session_id", "snapshot", "receipt"]) || raw.schema_version !== 1 || raw.kind !== "review_desktop_exported_current" ||
    raw.format !== accepted.format || !sameCurrentToken(previous.current_token, raw.current_token) || typeof raw.session_id !== "string" || !/^[A-Za-z0-9-]{16,64}$/.test(raw.session_id)) {
    throw new Error("export result is not bound to this native session");
  }
  const snapshot = readReviewPayload(raw.snapshot), r = raw.receipt;
  if (snapshot.schema_version !== 3 || JSON.stringify(snapshot) !== JSON.stringify(readReviewPayload(previous))) throw new Error("export changed the current graph or display evidence");
  const expectedCount = previous.mode === "b1" ? accepted.format === "standard" ? 7 : 10 : accepted.format === "standard" ? 10 : 13;
  if (!fields(r, ["schema_version", "kind", "format", "generation", "path", "manifest_sha256", "standard_manifest_sha256", "files_verified", "standard_bundle_published", "production_editable_pack_published", "vendor_execution"]) ||
    r.schema_version !== 1 || r.kind !== "review_desktop_export_receipt" || r.format !== accepted.format ||
    typeof r.generation !== "string" || !/^[0-9a-f]{64}$/.test(r.generation) || !hash(r.manifest_sha256) || !hash(r.standard_manifest_sha256) ||
    typeof r.path !== "string" || !r.path || r.path.length > 32767 || /[\u0000-\u001f]/.test(r.path) || !/^(?:[A-Za-z]:[\\/]|[\\/]{2}|\/)/.test(r.path) || r.path.split(/[\\/]/).includes("..") ||
    r.files_verified !== expectedCount || r.standard_bundle_published !== true || r.production_editable_pack_published !== (accepted.format === "editable") || r.vendor_execution !== false ||
    accepted.format === "standard" && r.standard_manifest_sha256 !== r.manifest_sha256 ||
    accepted.format === "editable" && (!standard || standard.format !== "standard" || r.generation !== standard.generation || r.standard_manifest_sha256 !== standard.manifest_sha256)) {
    throw new Error("export publication receipt changed identity, format or standard bundle");
  }
  return { snapshot, receipt: structuredClone(r) as CurrentExportReceipt };
}
