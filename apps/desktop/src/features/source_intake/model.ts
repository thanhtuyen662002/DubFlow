/**
 * Validated desktop source-intake boundary.
 *
 * URL credentials and signed query values are never retained in the desktop
 * queue snapshot.  The supervisor receives a short-lived transport value via
 * an explicit command and owns any OS-protected session handoff.  Local paths
 * remain fully usable when the network/provider path is unavailable.
 */

export const SOURCE_INTAKE_SCHEMA_VERSION = 1 as const;
export const SOURCE_INTAKE_LIMIT = 10_000;

export type SourceKind = "local" | "url";
export type SourceProvider = "generic" | "youtube" | "bilibili" | "douyin" | "unknown";

export type SourceItem = {
  kind: SourceKind;
  provider: SourceProvider;
  identity_key: string;
  display_ref: string;
  local_path: string | null;
  url: string | null;
  source_id: string;
  /** In-memory only; never place this field in localStorage or durable JSON. */
  transport_url?: string;
};

export type SourceIntakeBatch = {
  schema_version: typeof SOURCE_INTAKE_SCHEMA_VERSION;
  items: SourceItem[];
  rejected: Array<{ input: string; code: string; message: string }>;
};

const CONTROL = /[\u0000-\u001f\u007f]/;
const URL_SCHEME = /^https?:\/\//i;
const WINDOWS_PATH = /^[A-Za-z]:[\\/]/;
const UNC_PATH = /^\\\\[^\\]+\\[^\\]+/;

function fnv1a(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

function normalizedLocalPath(value: string): string {
  // Preserve the user's path for the supervisor, but make duplicate checks
  // case-insensitive and separator-stable on Windows.
  return value.trim().replaceAll("/", "\\").replace(/[\\]+$/, "").toLocaleLowerCase();
}

function providerFor(hostname: string): SourceProvider {
  const host = hostname.toLocaleLowerCase();
  if (host === "youtube.com" || host.endsWith(".youtube.com") || host === "youtu.be") return "youtube";
  if (host === "bilibili.com" || host.endsWith(".bilibili.com")) return "bilibili";
  if (host === "douyin.com" || host.endsWith(".douyin.com") || host === "iesdouyin.com") return "douyin";
  return "generic";
}

function redactUrl(parsed: URL): string {
  const safe = new URL(parsed.toString());
  safe.username = "";
  safe.password = "";
  // Keep public identity parameters such as YouTube's `v` and `list`, while
  // replacing credential/signature values before the URL crosses into the
  // durable supervisor queue.
  for (const key of [...safe.searchParams.keys()]) {
    if (/token|auth|secret|password|cookie|signature|^sig$|access[_-]?key|credential/i.test(key)) {
      safe.searchParams.set(key, "[redacted]");
    }
  }
  safe.hash = "";
  return safe.toString();
}

function canonicalUrl(parsed: URL): string {
  const host = parsed.hostname.toLocaleLowerCase();
  const pathname = parsed.pathname.replace(/\/{2,}/g, "/").replace(/\/$/, "") || "/";
  const safeQuery = [...parsed.searchParams.entries()]
    .filter(([key]) => !/token|auth|secret|password|cookie|signature|^sig$|access[_-]?key|credential/i.test(key))
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join("&");
  return `${parsed.protocol.toLocaleLowerCase()}//${host}${parsed.port ? `:${parsed.port}` : ""}${pathname}${safeQuery ? `?${safeQuery}` : ""}`;
}

function parseOne(input: string): SourceItem {
  const value = input.trim();
  if (!value) throw new Error("EMPTY_INPUT");
  if (value.length > 32_768 || CONTROL.test(value)) throw new Error("INPUT_INVALID");
  if (!URL_SCHEME.test(value)) {
    if (value.startsWith("\\\\") && !UNC_PATH.test(value)) throw new Error("LOCAL_PATH_INVALID");
    if (!WINDOWS_PATH.test(value) && !UNC_PATH.test(value) && !value.startsWith("/") && !value.startsWith(".")) {
      throw new Error("LOCAL_PATH_INVALID");
    }
    const normalized = normalizedLocalPath(value);
    if (!normalized) throw new Error("LOCAL_PATH_INVALID");
    const identity = `local:${normalized}`;
    return { kind: "local", provider: "unknown", identity_key: identity, display_ref: value, local_path: value, url: null, source_id: `local-${fnv1a(identity)}` };
  }
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("URL_INVALID");
  }
  if (!/^https?:$/.test(parsed.protocol) || !parsed.hostname || parsed.username || parsed.password) throw new Error("URL_INVALID");
  const canonical = canonicalUrl(parsed);
  const identity = `url:${canonical}`;
  return { kind: "url", provider: providerFor(parsed.hostname), identity_key: identity, display_ref: redactUrl(parsed), local_path: null, url: redactUrl(parsed), source_id: `source-${fnv1a(identity)}`, transport_url: value };
}

/** Parse newline/comma separated local paths and provider URLs. */
export function parseSourceInput(input: string, limit = SOURCE_INTAKE_LIMIT): SourceIntakeBatch {
  if (typeof input !== "string") throw new Error("INPUT_INVALID");
  if (!Number.isInteger(limit) || limit < 1 || limit > SOURCE_INTAKE_LIMIT) throw new Error("LIMIT_INVALID");
  const values = input.split(/[\r\n,]+/u).map((value) => value.trim()).filter(Boolean);
  const items: SourceItem[] = [];
  const rejected: SourceIntakeBatch["rejected"] = [];
  const seen = new Set<string>();
  for (const value of values) {
    if (items.length >= limit) {
      rejected.push({ input: value, code: "QUEUE_LIMIT", message: `Tối đa ${limit} nguồn trong một lần thêm` });
      continue;
    }
    try {
      const item = parseOne(value);
      if (seen.has(item.identity_key)) {
        rejected.push({ input: value, code: "DUPLICATE_SOURCE", message: "Nguồn trùng đã được bỏ qua" });
        continue;
      }
      seen.add(item.identity_key);
      items.push(item);
    } catch (error) {
      const code = error instanceof Error ? error.message : "INPUT_INVALID";
      rejected.push({ input: value, code, message: "Nguồn không hợp lệ hoặc không được hỗ trợ" });
    }
  }
  return { schema_version: SOURCE_INTAKE_SCHEMA_VERSION, items, rejected };
}

export function supervisorSourceItems(batch: SourceIntakeBatch): Array<Record<string, string>> {
  // Only redacted values are safe to persist.  A native provider adapter may
  // replace `url` with an ephemeral OS-protected session value immediately
  // before enqueueing; this function never reads browser cookies or tokens.
  return batch.items.map((item) => ({
    identity_key: item.identity_key,
    source_id: item.source_id,
    provider_id: item.provider,
    source_url: item.url ?? item.display_ref,
    source_ref: item.display_ref,
  }));
}

export function sourceIntakeLabel(item: SourceItem): string {
  if (item.kind === "local") return "Tệp cục bộ";
  if (item.provider === "unknown") return "Nguồn web";
  return item.provider[0].toUpperCase() + item.provider.slice(1);
}

export { redactUrl as redactSourceUrl };
