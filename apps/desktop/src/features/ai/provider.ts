import Anthropic from "@anthropic-ai/sdk";

/** Which service writes for the user, with the user's own key (ADR-0042). */
export type Provider = "claude" | "gemini";

export const PROVIDERS: Record<Provider, { name: string; label: string; company: string; console: string; placeholder: string }> = {
  claude: { name: "Claude", label: "Claude (Anthropic)", company: "Anthropic", console: "console.anthropic.com", placeholder: "sk-ant-…" },
  gemini: { name: "Gemini", label: "Gemini (Google)", company: "Google", console: "aistudio.google.com/apikey", placeholder: "AIza…" },
};

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
  return value === "claude" || value === "gemini";
}

export const KEY_STORAGE_NOTE = "Khóa được lưu an toàn trên máy này (Windows Credential Manager) nên lần sau không phải nhập lại; đổi hoặc xóa trong “Cài đặt AI”.";

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
}

function mask(key: string): string | null {
  return key ? `…${key.slice(-4)}` : null;
}

export function createAiSession(store?: KeyStore): AiSession {
  let current: Provider = "claude";
  const keys: Record<Provider, string> = { claude: "", gemini: "" };
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
      for (const provider of Object.keys(keys) as Provider[]) {
        try {
          keys[provider] = (await store.load(provider)) ?? "";
        } catch {
          keys[provider] = "";
        }
      }
    },
    saved: (provider) => mask(keys[provider]),
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
  return /\b(401|403)\b|authentication|api[_ -]?key|PERMISSION_DENIED|UNAUTHENTICATED/i.test(String(error));
}

export type Part = { text: string } | { image: { mediaType: "image/jpeg"; data: string } };
/** `language` is the answer's language: Vietnamese (the default) or English, which skips the Vietnamese-diacritics guard. */
export type JsonRequest = { system: string; schema: Record<string, unknown>; parts: Part[]; effort: "low" | "medium"; language?: "vi" | "en" };

async function askClaude(key: string, request: JsonRequest): Promise<unknown> {
  // The key belongs to the user of this desktop app and stays on this machine.
  const client = new Anthropic({ apiKey: key, dangerouslyAllowBrowser: true });
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
  if (!key.trim()) throw new Error("Chưa có khóa API Gemini (API key); hãy nhập khóa trong “Cài đặt AI”.");
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
export function askJson(provider: Provider, key: string, request: JsonRequest, send: typeof fetch = (...args) => fetch(...args),
                        wait: (ms: number) => Promise<void> = sleep): Promise<unknown> {
  return provider === "gemini" ? askGemini(key, request, send, wait) : askClaude(key, request);
}
