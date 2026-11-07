import Anthropic from "@anthropic-ai/sdk";

/**
 * Which service writes for the user, with the user's own key (ADR-0042). Claude and
 * Gemini are called from the webview; the others speak the OpenAI Chat Completions
 * shape and are called from the host (`ai_ask` → engine/dubflow/ai), so the webview's
 * network policy stays closed. Ids match engine/dubflow/ai/client.py PRESETS.
 */
export const PROVIDER_IDS = ["claude", "gemini", "openai", "deepseek", "qwen", "kimi", "glm", "grok", "openrouter", "groq", "mistral", "custom"] as const;
export type Provider = typeof PROVIDER_IDS[number];

type ProviderInfo = {
  name: string; label: string; company: string; console: string; placeholder: string;
  /** Called through the host (OpenAI-compatible), not from the webview. */
  host: boolean;
  /** Reads images (Bán hàng's product photos). */
  vision: boolean;
  /** The model used when the user has not picked one; "" for custom servers. */
  model: string;
  /** Whether the user can change the server address (their own server, or Qwen's region). */
  baseUrl?: string;
  /** One line under the card: what this provider is good at. */
  note?: string;
};

/**
 * The provider's current chat models (checked 2026-11), shown before a key is saved or when its list
 * cannot be loaded. With a working key the list comes from the provider itself and replaces this one.
 */
export const MODEL_CATALOG: Record<Provider, readonly string[]> = {
  claude: ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1", "claude-opus-5"],
  gemini: ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3-flash-preview", "gemini-2.5-pro", "gemini-2.5-flash"],
  openai: ["gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-astra", "gpt-6-luna", "gpt-5.5", "gpt-5.4-mini", "gpt-5-mini"],
  deepseek: ["deepseek-v4-pro", "deepseek-flash"],
  qwen: ["qwen3.8-max", "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus", "qwen3.8-flash", "qwen3.7-flash", "qwen-max", "qwen-plus", "qwen-flash"],
  kimi: ["kimi-k3.1-pro", "kimi-k3.1-pro-highspeed", "kimi-k3", "kimi-k2.6"],
  glm: ["glm-5.3", "glm-5.3-flash", "glm-5.3-flashx", "glm-5.2", "glm-5.1", "glm-5", "glm-4.7", "glm-4.7-flash", "glm-4.5-air", "glm-4.5-flash"],
  grok: ["grok-4.7", "grok-4.6", "grok-4.5", "grok-4.3", "grok-4.20", "grok-4.1-fast"],
  // Hundreds of models from many companies: the list loads once the key is saved.
  openrouter: [],
  groq: ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "qwen/qwen3.8-27b"],
  mistral: ["mistral-large-latest", "mistral-large-4", "mistral-medium-latest", "mistral-small-latest", "ministral-14b-2512", "ministral-8b-latest"],
  custom: [],
};

export const PROVIDERS: Record<Provider, ProviderInfo> = {
  claude: { name: "Claude", label: "Claude (Anthropic)", company: "Anthropic", console: "console.anthropic.com", placeholder: "sk-ant-…", host: false, vision: true, model: "claude-opus-5-5" },
  gemini: { name: "Gemini", label: "Gemini (Google)", company: "Google", console: "aistudio.google.com/apikey", placeholder: "AIza…", host: false, vision: true, model: "gemini-3.5-flash-lite" },
  openai: { name: "ChatGPT", label: "ChatGPT (OpenAI)", company: "OpenAI", console: "platform.openai.com/api-keys", placeholder: "sk-…", host: true, vision: true, model: "gpt-5-mini" },
  deepseek: { name: "DeepSeek", label: "DeepSeek", company: "DeepSeek", console: "platform.deepseek.com/api_keys", placeholder: "sk-…", host: true, vision: true, model: "deepseek-flash",
    note: "Rẻ, dịch tiếng Trung rất tốt." },
  qwen: { name: "Qwen", label: "Qwen (Alibaba Cloud)", company: "Alibaba Cloud", console: "modelstudio.console.alibabacloud.com", placeholder: "sk-…", host: true, vision: false, model: "qwen-plus",
    baseUrl: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", note: "Mạnh về tiếng Trung. Khóa vùng Bắc Kinh: đổi địa chỉ thành https://dashscope.aliyuncs.com/compatible-mode/v1." },
  kimi: { name: "Kimi", label: "Kimi (Moonshot AI)", company: "Moonshot AI", console: "platform.kimi.ai", placeholder: "sk-…", host: true, vision: true, model: "kimi-k3" },
  glm: { name: "GLM", label: "GLM (Z.ai / Zhipu)", company: "Z.ai", console: "z.ai/manage-apikey/apikey-list", placeholder: "…", host: true, vision: false, model: "glm-5.3" },
  grok: { name: "Grok", label: "Grok (xAI)", company: "xAI", console: "console.x.ai", placeholder: "xai-…", host: true, vision: true, model: "grok-4.7" },
  openrouter: { name: "OpenRouter", label: "OpenRouter (nhiều model)", company: "OpenRouter", console: "openrouter.ai/keys", placeholder: "sk-or-…", host: true, vision: true, model: "openrouter/auto",
    note: "Một khóa dùng được hàng trăm model (GPT, Claude, Gemini, DeepSeek, Qwen…): nhập tên model, ví dụ deepseek/deepseek-chat." },
  groq: { name: "Groq", label: "Groq", company: "Groq", console: "console.groq.com/keys", placeholder: "gsk_…", host: true, vision: false, model: "openai/gpt-oss-120b", note: "Rất nhanh, có gói miễn phí." },
  mistral: { name: "Mistral", label: "Mistral AI", company: "Mistral AI", console: "console.mistral.ai/api-keys", placeholder: "…", host: true, vision: true, model: "mistral-medium-latest" },
  custom: { name: "AI tùy chỉnh", label: "Tùy chỉnh (tương thích OpenAI)", company: "máy chủ bạn nhập", console: "máy chủ của bạn", placeholder: "Khóa (để trống nếu máy chủ trên máy này không cần)", host: true, vision: true, model: "",
    baseUrl: "", note: "Mọi máy chủ nói chuẩn OpenAI /chat/completions: LM Studio, Ollama (http://localhost:11434/v1), vLLM, one-api, Azure… Nhập địa chỉ và tên model." },
};

/** Per-provider choices that are not secret: the model and (custom, Qwen) the server address. */
export type ProviderOptions = { model?: string; baseUrl?: string };

export const CLAUDE_MODEL = "claude-opus-5-5";
/**
 * Fast and widely available first; the larger Flash model only when the first is busy.
 * In live use, gemini-3.8-flash often answered 503 or took minutes, while
 * gemini-3.5-flash-lite answered in about a second.
 */
export const GEMINI_MODEL = "gemini-3.5-flash-lite";
export const GEMINI_FALLBACK_MODEL = "gemini-3.8-flash";
const geminiEndpoint = (model: string) => `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`;
const KEEP_DIACRITICS = "Luôn viết tiếng Việt có dấu đầy đủ trong mọi trường văn bản.";
/** One attempt never keeps the user waiting longer than this. */
export const GEMINI_TIMEOUT_MS = 90_000;
/**
 * The longest pause DubFlow takes when Google asks it to wait (Retry-After or retryDelay).
 * A longer request stops the attempts with a "try again later" message instead of retrying early.
 */
export const GEMINI_MAX_RETRY_WAIT_MS = 30_000;

export function isProvider(value: unknown): value is Provider {
  return typeof value === "string" && (PROVIDER_IDS as readonly string[]).includes(value);
}

export const KEY_STORAGE_NOTE = "Khóa được lưu an toàn trên máy này (Windows Credential Manager) nên lần sau không phải nhập lại; đổi hoặc xóa trong “Cài đặt › Tài khoản”.";

/** What the key prompt says: where to get a key, where it is kept, what is sent and who bills. */
export function keyHint(provider: Provider, sent = "phụ đề tiếng Việt của video"): string {
  const { name, console, company } = PROVIDERS[provider];
  return `Nhập khóa API ${name} của bạn (${console}). ${KEY_STORAGE_NOTE} Khi dùng, ${sent} được gửi tới ${company} và tính phí theo tài khoản của bạn.`;
}

/** Where keys are kept between app runs: the host's Credential Manager (ADR-0043). */
export interface KeyStore {
  load(provider: Provider): Promise<string | null>;
  /** Saves the key and returns its masked form ("…abcd"). */
  save(provider: Provider, key: string): Promise<string>;
  remove(provider: Provider): Promise<void>;
  loadProvider(): Provider | null;
  saveProvider(provider: Provider): void;
  loadOptions?(): Partial<Record<Provider, ProviderOptions>>;
  saveOptions?(options: Partial<Record<Provider, ProviderOptions>>): void;
}

/** The chosen provider and one key per provider: saved through `store`, cached in memory while the app runs. */
export interface AiSession {
  provider(): Provider;
  setProvider(provider: Provider): void;
  /** The current provider's key, or "". */
  read(): string;
  /** Sets (or, when blank, removes) the current provider's key. */
  set(value: string): Promise<void>;
  clear(): Promise<void>;
  /** Loads the saved provider choice and keys. */
  restore(): Promise<void>;
  /** A provider's saved key, masked, or null. */
  saved(provider: Provider): string | null;
  /** The provider's model and server address choices. */
  options(provider: Provider): ProviderOptions;
  setOptions(provider: Provider, options: ProviderOptions): void;
}

function mask(key: string): string | null {
  return key ? `…${key.slice(-4)}` : null;
}

export function createAiSession(store?: KeyStore): AiSession {
  let current: Provider = "claude";
  const keys = Object.fromEntries(PROVIDER_IDS.map((id) => [id, ""])) as Record<Provider, string>;
  let options: Partial<Record<Provider, ProviderOptions>> = {};
  async function put(provider: Provider, value: string): Promise<void> {
    const key = value.trim();
    const previous = keys[provider];
    keys[provider] = key;
    try {
      if (store) await (key ? store.save(provider, key) : store.remove(provider));
    } catch (error) {
      keys[provider] = previous;
      throw error;
    }
  }
  return {
    provider: () => current,
    setProvider: (provider) => {
      current = provider;
      store?.saveProvider(provider);
    },
    read: () => keys[current],
    set: (value) => put(current, value),
    clear: () => put(current, ""),
    restore: async () => {
      if (!store) return;
      current = store.loadProvider() ?? current;
      try {
        options = store.loadOptions?.() ?? {};
      } catch {
        options = {};
      }
      for (const provider of Object.keys(keys) as Provider[]) {
        try {
          keys[provider] = (await store.load(provider)) ?? "";
        } catch {
          keys[provider] = "";
        }
      }
    },
    saved: (provider) => mask(keys[provider]),
    options: (provider) => ({ ...(options[provider] ?? {}) }),
    setOptions: (provider, value) => {
      const clean: ProviderOptions = {};
      if (value.model?.trim()) clean.model = value.model.trim().slice(0, 200);
      if (value.baseUrl?.trim()) clean.baseUrl = value.baseUrl.trim().replace(/\/+$/, "").slice(0, 300);
      options = { ...options, [provider]: clean };
      store?.saveOptions?.(options);
    },
  };
}

/** Set a provider's key without changing the current choice. */
export async function setKeyFor(session: AiSession, provider: Provider, value: string): Promise<void> {
  const current = session.provider();
  session.setProvider(provider);
  try {
    await session.set(value);
  } finally {
    session.setProvider(current);
  }
}

/** A rejected or missing key, from either service's error text. */
export function isKeyError(error: unknown): boolean {
  return /^(Error: )?KEY:|\b(401|403)\b|authentication|api[_ -]?key|PERMISSION_DENIED|UNAUTHENTICATED/i.test(String(error));
}

export type Part = { text: string } | { image: { mediaType: "image/jpeg"; data: string } };
/** `language` is the answer's language: Vietnamese (the default) or English, which skips the Vietnamese-diacritics guard. */
export type JsonRequest = { system: string; schema: Record<string, unknown>; parts: Part[]; effort: "low" | "medium"; language?: "vi" | "en" };

/**
 * fetch that refuses every redirect: on a cross-origin redirect Fetch strips only
 * Authorization, so x-api-key would follow it. The Claude SDK uses this one.
 */
export function noRedirectFetch(send: typeof fetch = (...args) => fetch(...args)): typeof fetch {
  return ((input: RequestInfo | URL, init?: RequestInit) => send(input, { ...init, redirect: "error" })) as typeof fetch;
}

async function askClaude(key: string, request: JsonRequest): Promise<unknown> {
  // The key belongs to the user of this desktop app and stays on this machine.
  const client = new Anthropic({ apiKey: key, dangerouslyAllowBrowser: true, fetch: noRedirectFetch() });
  const response = await client.beta.messages.create({
    model: CLAUDE_MODEL,
    max_tokens: 16000,
    betas: ["server-side-fallback-2026-07-01"],
    fallbacks: "default",
    output_config: { effort: request.effort, format: { type: "json_schema", schema: request.schema } },
    system: request.system,
    messages: [{
      role: "user",
      content: request.parts.map((part): Anthropic.Beta.BetaContentBlockParam => ("text" in part
        ? { type: "text", text: part.text }
        : { type: "image", source: { type: "base64", media_type: part.image.mediaType, data: part.image.data } })),
    }],
  });
  if (response.stop_reason === "refusal") throw new Error("Claude từ chối yêu cầu này.");
  if (response.stop_reason === "max_tokens") throw new Error("Câu trả lời bị cắt ngắn; hãy thử lại.");
  return JSON.parse(response.content.map((block) => (block.type === "text" ? block.text : "")).join(""));
}

/** JSON Schema as Gemini's schema subset: upper-case types and no additionalProperties. */
export function toGeminiSchema(schema: unknown): unknown {
  if (Array.isArray(schema)) return schema.map(toGeminiSchema);
  if (!schema || typeof schema !== "object") return schema;
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(schema)) {
    if (key === "additionalProperties") continue;
    if (key === "type" && typeof value === "string") result.type = value.toUpperCase();
    else if (key === "properties" && value && typeof value === "object") {
      result.properties = Object.fromEntries(Object.entries(value).map(([name, field]) => [name, toGeminiSchema(field)]));
    } else result[key] = toGeminiSchema(value);
  }
  return result;
}

type GeminiReply = {
  error?: { status?: string; message?: string; details?: Array<{ "@type"?: string; retryDelay?: unknown }> };
  promptFeedback?: { blockReason?: string };
  candidates?: Array<{ finishReason?: string; content?: { parts?: Array<{ text?: string; thought?: boolean }> } }>;
};

/** One failed Gemini attempt: whether another try may help, and how long Google asked DubFlow to wait first. */
class GeminiAttemptError extends Error {
  retryable: boolean;
  retryAfterMs: number | null;
  constructor(message: string, retryable: boolean, retryAfterMs: number | null = null) {
    super(message);
    this.retryable = retryable;
    this.retryAfterMs = retryAfterMs;
  }
}

/** Only rate limits (429) and server errors (500–599) are worth another try. */
const retryableStatus = (status: number): boolean => status === 429 || (status >= 500 && status <= 599);
const isTimeout = (error: unknown): boolean => ["TimeoutError", "AbortError"].includes((error as Error)?.name);

/**
 * An HTTP Retry-After value in milliseconds: whole seconds, or an HTTP date (a past date is 0).
 * Anything else is not a valid hint and gives null.
 */
export function parseRetryAfter(value: string | null | undefined, now = Date.now()): number | null {
  const text = (value ?? "").trim();
  if (/^\d{1,9}$/.test(text)) return Number(text) * 1000;
  // IMF-fixdate, RFC 850 and asctime all start with a day name and carry hh:mm:ss.
  if (!/^[A-Za-z]{3}/.test(text) || !/\d{2}:\d{2}:\d{2}/.test(text)) return null;
  const date = Date.parse(text);
  return Number.isFinite(date) ? Math.max(0, date - now) : null;
}

/** Google's RetryInfo detail ("retryDelay": "37s" or "1.5s") in milliseconds, or null. */
export function parseRetryDelay(details: unknown): number | null {
  if (!Array.isArray(details)) return null;
  for (const detail of details) {
    if (!detail || typeof detail !== "object" || !String(detail["@type"] ?? "").endsWith("google.rpc.RetryInfo")) continue;
    const match = typeof detail.retryDelay === "string" ? /^(\d{1,9}(?:\.\d{1,9})?)s$/.exec(detail.retryDelay.trim()) : null;
    if (match) return Math.ceil(Number(match[1]) * 1000);
  }
  return null;
}

/** The longest valid wait the failed response asked for (header or Google error details), or null. */
function retryHint(response: Response, reply: GeminiReply): number | null {
  const hints = [parseRetryAfter(response.headers?.get?.("retry-after")), parseRetryDelay(reply.error?.details)]
    .filter((hint): hint is number => hint !== null);
  return hints.length ? Math.max(...hints) : null;
}

async function askGeminiOnce(key: string, request: JsonRequest, send: typeof fetch, model: string): Promise<unknown> {
  let response: Response;
  let reply: GeminiReply;
  try {
    response = await send(geminiEndpoint(model), {
      // Covers the answer's body too: a stalled download times out like a stalled request.
      signal: AbortSignal.timeout(GEMINI_TIMEOUT_MS),
      // A redirect would carry x-goog-api-key to its target (Fetch strips only Authorization): fail instead.
      redirect: "error",
      method: "POST",
      // The key travels in a header, never in the URL.
      headers: { "content-type": "application/json", "x-goog-api-key": key },
      body: JSON.stringify({
        // The fast model sometimes drops Vietnamese diacritics under a schema; ask for them.
        systemInstruction: { parts: [{ text: request.language === "en" ? request.system : `${request.system}\n${KEEP_DIACRITICS}` }] },
        contents: [{
          role: "user",
          parts: request.parts.map((part) => ("text" in part ? { text: part.text } : { inlineData: { mimeType: part.image.mediaType, data: part.image.data } })),
        }],
        generationConfig: { responseMimeType: "application/json", responseSchema: toGeminiSchema(request.schema), maxOutputTokens: 16000 },
      }),
    });
    reply = await response.json().catch((error: unknown) => {
      if (isTimeout(error)) throw error;
      return {};
    }) as GeminiReply;
  } catch (error) {
    if (isTimeout(error)) throw new GeminiAttemptError("timeout: Gemini trả lời quá lâu.", true);
    throw error;
  }
  if (!response.ok) {
    const message = `${response.status} ${reply.error?.status ?? ""}: ${reply.error?.message ?? "Gemini lỗi"}`.slice(0, 300);
    const retryable = retryableStatus(response.status);
    throw new GeminiAttemptError(message, retryable, retryable ? retryHint(response, reply) : null);
  }
  if (reply.promptFeedback?.blockReason) throw new Error("Gemini từ chối yêu cầu này.");
  const candidate = reply.candidates?.[0];
  if (candidate?.finishReason === "MAX_TOKENS") throw new Error("Câu trả lời bị cắt ngắn; hãy thử lại.");
  if (["SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION", "SPII"].includes(candidate?.finishReason ?? "")) {
    throw new Error("Gemini từ chối yêu cầu này.");
  }
  const text = (candidate?.content?.parts ?? []).filter((part) => !part.thought).map((part) => part.text ?? "").join("");
  if (!text.trim()) throw new Error("Gemini không trả về nội dung; hãy thử lại.");
  try {
    return JSON.parse(text);
  } catch {
    throw new Error("Gemini trả về dữ liệu sai định dạng JSON; hãy thử lại.");
  }
}

/** The user's message for a Gemini failure that retrying did not fix: 429, every 500–599 and time-outs. */
export function geminiFailure(error: unknown): Error {
  const message = String((error as Error)?.message ?? error);
  if (/^429\b/.test(message)) return new Error("429: Đã vượt giới hạn số lần gọi Gemini; hãy chờ một phút rồi thử lại.");
  if (/^5\d\d\b/.test(message)) return new Error(`${message.slice(0, 3)}: Gemini đang quá tải hoặc gặp lỗi máy chủ; hãy thử lại sau ít phút.`);
  if (/^timeout\b/.test(message)) return new Error("Gemini trả lời quá lâu; hãy thử lại sau ít phút.");
  return error instanceof Error ? error : new Error(message);
}

/** Google asked for a longer wait than DubFlow takes: stop and say when to try again. */
export function geminiRetryLater(error: unknown, waitMs: number): Error {
  const status = /^(429|5\d\d)\b/.exec(String((error as Error)?.message ?? error))?.[1] ?? "429";
  const minutes = Math.max(1, Math.ceil(waitMs / 60_000));
  const when = minutes > 60 ? "hơn một giờ" : `khoảng ${minutes} phút`;
  return new Error(`${status}: Gemini yêu cầu chờ ${when} trước khi gọi lại; hãy thử lại sau.`);
}

/** Every string in a JSON answer, joined. */
function strings(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(strings).join(" ");
  if (value && typeof value === "object") return Object.values(value).map(strings).join(" ");
  return "";
}

/**
 * Vietnamese written without its diacritics ("Ban co bao gio…"). Real Vietnamese
 * marks a large share of its letters; an answer of 40+ letters with almost none
 * marked was written unaccented. The fast model occasionally does this.
 */
export function lacksDiacritics(value: unknown): boolean {
  const text = strings(value);
  const letters = text.match(/\p{L}/gu)?.length ?? 0;
  const marked = text.match(/[À-ÃÈ-ÊÌÍÒ-ÕÙÚÝà-ãè-êìíò-õùúýĂăĐđĨĩŨũƠơƯưẠ-ỹ]/gu)?.length ?? 0;
  return letters >= 40 && marked / letters < 0.03;
}

/**
 * Bounded retry that changes a condition: the fast model, the same model once more
 * after a pause, then the larger model. Only 429, 500–599 and time-outs are retried;
 * key, other 4xx, refusal and format errors are not. A wait Google asks for
 * (Retry-After or retryDelay) is honoured up to GEMINI_MAX_RETRY_WAIT_MS; a longer
 * one stops the attempts with a "try again later" message.
 * An unaccented answer is asked again of the fast model once (it usually answers with
 * diacritics the second time, in about two seconds) and kept if that fails or repeats.
 */
async function askGemini(key: string, request: JsonRequest, send: typeof fetch, wait: (ms: number) => Promise<void>): Promise<unknown> {
  if (!key.trim()) throw new Error("Chưa có khóa API Gemini (API key); hãy nhập khóa trong “Cài đặt › Tài khoản”.");
  const attempts: Array<[string, number]> = [[GEMINI_MODEL, 0], [GEMINI_MODEL, 4000], [GEMINI_FALLBACK_MODEL, 2000]];
  let last: GeminiAttemptError | null = null;
  let unaccented: unknown = undefined;
  for (const [index, [model, pause]] of attempts.entries()) {
    if (unaccented !== undefined && model !== GEMINI_MODEL) break;
    if (index > 0) {
      const asked = last?.retryAfterMs ?? 0;
      if (asked > GEMINI_MAX_RETRY_WAIT_MS) throw geminiRetryLater(last, asked);
      await wait(unaccented !== undefined ? 1000 : Math.max(pause, asked));
    }
    try {
      const value = await askGeminiOnce(key, request, send, model);
      // English answers have no diacritics to lose (#444).
      if (request.language === "en" || !lacksDiacritics(value) || unaccented !== undefined) return value;
      unaccented = value;
    } catch (error) {
      if (unaccented !== undefined) return unaccented;
      if (!(error instanceof GeminiAttemptError) || !error.retryable) throw error;
      last = error;
    }
  }
  if (unaccented !== undefined) return unaccented;
  throw geminiFailure(last);
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** One structured answer from the chosen provider. Everything in `request.parts` is sent to that provider. */
type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type HostReply = { ok: boolean; value?: unknown; model?: string; models?: string[]; suggested?: string | null; code?: string; message?: string };
let host: { invoke: Invoke; options: (provider: Provider) => ProviderOptions } | null = null;

/** Wires the host-called providers (main.ts at start-up): the Tauri invoke and the session's model choices. */
export function configureHostAi(invoke: Invoke, options: (provider: Provider) => ProviderOptions): void {
  host = { invoke, options };
}

/** The request the host runs for a provider: its model and, for custom/Qwen, the server address. */
export function hostRequest(provider: Provider, options: ProviderOptions, body: Record<string, unknown>): Record<string, unknown> {
  const info = PROVIDERS[provider];
  return {
    provider,
    ...(options.model?.trim() || info.model ? { model: options.model?.trim() || info.model } : {}),
    ...(info.baseUrl !== undefined && (options.baseUrl?.trim() || info.baseUrl) ? { base_url: options.baseUrl?.trim() || info.baseUrl } : {}),
    ...body,
  };
}

function hostError(reply: HostReply): Error {
  return new Error(`${reply.code ?? "FAILED"}: ${reply.message ?? "AI không trả lời"}`);
}

async function askHost(provider: Provider, key: string, request: JsonRequest): Promise<unknown> {
  if (!host) throw new Error(`${PROVIDERS[provider].name} chỉ dùng được trong ứng dụng DubFlow trên Windows.`);
  if (!key.trim() && provider !== "custom") throw new Error(`KEY: Chưa có khóa API ${PROVIDERS[provider].name}; hãy nhập khóa trong “Cài đặt › Tài khoản”.`);
  if (!PROVIDERS[provider].vision && request.parts.some((part) => "image" in part)) {
    throw new Error(`${PROVIDERS[provider].name} không đọc được ảnh; hãy chọn Claude, Gemini hoặc ChatGPT cho việc này.`);
  }
  const system = request.language === "en" ? request.system : `${request.system}\n${KEEP_DIACRITICS}`;
  const options = host.options(provider);
  const reply = await host.invoke<HostReply>("ai_ask", {
    request: hostRequest(provider, options, { action: "ask", system, schema: request.schema, parts: request.parts, max_tokens: 16000 }),
    key: key.trim() || null,
  });
  if (!reply?.ok) throw hostError(reply ?? { ok: false });
  return reply.value;
}

/** "Kiểm tra khóa": the models this key can use (proves the key and the address), and the one to use. */
export async function checkProvider(provider: Provider, key: string, options: ProviderOptions = {}): Promise<{ models: string[]; suggested: string | null }> {
  if (!host) throw new Error("Chỉ kiểm tra được trong ứng dụng DubFlow trên Windows.");
  const reply = await host.invoke<HostReply>("ai_ask", { request: hostRequest(provider, options, { action: "models" }), key: key.trim() || null });
  if (!reply?.ok) throw hostError(reply ?? { ok: false });
  return { models: reply.models ?? [], suggested: reply.suggested ?? null };
}

export function askJson(provider: Provider, key: string, request: JsonRequest, send: typeof fetch = (...args) => fetch(...args),
                        wait: (ms: number) => Promise<void> = sleep): Promise<unknown> {
  if (PROVIDERS[provider].host) return askHost(provider, key, request);
  return provider === "gemini" ? askGemini(key, request, send, wait) : askClaude(key, request);
}
