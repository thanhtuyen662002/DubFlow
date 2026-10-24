/** One source per line. Original references survive parsing; only display URLs are redacted. */
export const SOURCE_INTAKE_SCHEMA_VERSION = 1 as const;
export const SOURCE_INTAKE_LIMIT = 10_000;
// UTF-8 bytes, matching source_intake.rs and the existing source queue limits.
export const SOURCE_BYTE_LIMITS = { reference: 4096, identity: 1024, sourceId: 512, provider: 64 } as const;
export type SourceKind = "local" | "url";
export type SourceProvider = "generic" | "youtube" | "bilibili" | "douyin" | "unknown";
export type SourceItem = {
  kind: SourceKind; provider: SourceProvider; identity_key: string; display_ref: string;
  local_path: string | null; url: string | null; source_id: string;
  /** Ephemeral only; never persist this field. */
  transport_url?: string;
};
export type SourceRejection = { input: string; code: string; message: string; line: number };
export type SourceIntakeBatch = {
  schema_version: typeof SOURCE_INTAKE_SCHEMA_VERSION; items: SourceItem[]; rejected: SourceRejection[];
};
const CONTROL = /[\p{Cc}]/u;
const SECRET = /token|auth|secret|password|cookie|signature|^sig$|access[_-]?key|credential/i;
const encoder = new TextEncoder();
export function utf8Bytes(value: string): number { return encoder.encode(value).byteLength; }
function bounded(value: string, max: number): void {
  if (utf8Bytes(value) > max) throw new Error("INPUT_TOO_LONG");
  if (!value || CONTROL.test(value)) throw new Error("INPUT_INVALID");
}
function fnv1a(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash = Math.imul(hash ^ value.charCodeAt(index), 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}
function providerFor(host: string): SourceProvider {
  if (host === "youtube.com" || host.endsWith(".youtube.com") || host === "youtu.be") return "youtube";
  if (host === "bilibili.com" || host.endsWith(".bilibili.com")) return "bilibili";
  if (host === "douyin.com" || host.endsWith(".douyin.com") || host === "iesdouyin.com") return "douyin";
  return "generic";
}
export function redactSourceUrl(parsed: URL): string {
  const safe = new URL(parsed.toString());
  safe.username = ""; safe.password = ""; safe.hash = "";
  // Preserve the spelling/order of public query pairs (including commas).
  safe.search = parsed.search.slice(1).split("&").filter(Boolean).map((pair) => {
    const key = pair.split("=", 1)[0];
    const decoded = new URLSearchParams(`${key}=`).keys().next().value ?? key;
    return SECRET.test(decoded) ? `${key}=%5Bredacted%5D` : pair;
  }).join("&");
  return safe.toString();
}
function canonicalUrl(parsed: URL): string {
  const safe = new URL(redactSourceUrl(parsed));
  safe.search = safe.search.slice(1).split("&").filter((pair) => {
    const key = new URLSearchParams(`${pair.split("=", 1)[0]}=`).keys().next().value ?? "";
    return !SECRET.test(key);
  }).join("&");
  // No slash collapsing, final-slash removal or query sorting. Stronger
  // equivalence belongs to a provider adapter with evidence of source identity.
  return safe.toString();
}
function parseOne(value: string): SourceItem {
  bounded(value, SOURCE_BYTE_LIMITS.reference);
  let item: SourceItem;
  if (!/^https?:\/\//i.test(value)) {
    if (value.startsWith("\\\\") && !/^\\\\[^\\]+\\[^\\]+/.test(value)) throw new Error("LOCAL_PATH_INVALID");
    if (!/^[A-Za-z]:[\\/]/.test(value) && !value.startsWith("\\\\") && !value.startsWith("/") && !value.startsWith(".")) {
      throw new Error("LOCAL_PATH_INVALID");
    }
    const identity = `local:${value.replaceAll("/", "\\").replace(/[\\]+$/, "").toLowerCase()}`;
    item = { kind: "local", provider: "unknown", identity_key: identity, display_ref: value,
      local_path: value, url: null, source_id: `local-${fnv1a(identity)}` };
  } else {
    let parsed: URL;
    try { parsed = new URL(value); } catch { throw new Error("URL_INVALID"); }
    if (!parsed.hostname || parsed.username || parsed.password) throw new Error("URL_INVALID");
    const identity = `url:${canonicalUrl(parsed)}`;
    item = { kind: "url", provider: providerFor(parsed.hostname), identity_key: identity,
      display_ref: redactSourceUrl(parsed), local_path: null, url: redactSourceUrl(parsed),
      source_id: `source-${fnv1a(identity)}`, transport_url: value };
  }
  bounded(item.identity_key, SOURCE_BYTE_LIMITS.identity);
  bounded(item.display_ref, SOURCE_BYTE_LIMITS.reference);
  bounded(item.source_id, SOURCE_BYTE_LIMITS.sourceId);
  bounded(item.provider, SOURCE_BYTE_LIMITS.provider);
  return item;
}
/** CRLF/LF/CR delimit sources. Commas and spaces are literal reference bytes. */
export function parseSourceInput(input: string, limit = SOURCE_INTAKE_LIMIT): SourceIntakeBatch {
  if (typeof input !== "string") throw new Error("INPUT_INVALID");
  if (!Number.isInteger(limit) || limit < 1 || limit > SOURCE_INTAKE_LIMIT) throw new Error("LIMIT_INVALID");
  const items: SourceItem[] = [], rejected: SourceRejection[] = [];
  const seen = new Set<string>();
  for (const [index, raw] of input.split(/\r\n|\r|\n/u).entries()) {
    const value = raw.trim();
    if (!value) continue;
    try {
      const item = parseOne(value);
      if (seen.has(item.identity_key)) throw new Error("DUPLICATE_SOURCE");
      if (items.length >= limit) throw new Error("QUEUE_LIMIT");
      seen.add(item.identity_key); items.push(item);
    } catch (error) {
      const code = error instanceof Error ? error.message : "INPUT_INVALID";
      // Raw rejected URLs can contain credentials. Identify the line without
      // copying untrusted secrets into UI error text or durable diagnostics.
      rejected.push({ input: `Dòng ${index + 1}`, line: index + 1, code,
        message: code === "INPUT_TOO_LONG" ? "Nguồn vượt giới hạn byte UTF-8" : "Nguồn không hợp lệ, trùng hoặc vượt số lượng cho phép" });
    }
  }
  return { schema_version: SOURCE_INTAKE_SCHEMA_VERSION, items, rejected };
}
export function supervisorSourceItems(batch: SourceIntakeBatch): Array<Record<string, string>> {
  return batch.items.map((item) => ({ identity_key: item.identity_key, source_id: item.source_id,
    provider_id: item.provider, source_url: item.url ?? item.display_ref, source_ref: item.display_ref }));
}
export function sourceIntakeLabel(item: SourceItem): string {
  return item.kind === "local" ? "Tệp cục bộ" : item.provider[0].toUpperCase() + item.provider.slice(1);
}
