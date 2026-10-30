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
/** Busy (503), rate-limited (429), server errors and time-outs are worth another try. */
const RETRYABLE = /^(429|500|502|503|504|timeout)\b/;

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
export type JsonRequest = { system: string; schema: Record<string, unknown>; parts: Part[]; effort: "low" | "medium" };

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
  error?: { status?: string; message?: string };
  promptFeedback?: { blockReason?: string };
  candidates?: Array<{ finishReason?: string; content?: { parts?: Array<{ text?: string; thought?: boolean }> } }>;
};

async function askGeminiOnce(key: string, request: JsonRequest, send: typeof fetch, model: string): Promise<unknown> {
  let response: Response;
  try {
    response = await send(geminiEndpoint(model), {
      signal: AbortSignal.timeout(GEMINI_TIMEOUT_MS),
      method: "POST",
      // The key travels in a header, never in the URL.
      headers: { "content-type": "application/json", "x-goog-api-key": key },
      body: JSON.stringify({
        // The fast model sometimes drops Vietnamese diacritics under a schema; ask for them.
        systemInstruction: { parts: [{ text: `${request.system}\n${KEEP_DIACRITICS}` }] },
        contents: [{
          role: "user",
          parts: request.parts.map((part) => ("text" in part ? { text: part.text } : { inlineData: { mimeType: part.image.mediaType, data: part.image.data } })),
        }],
        generationConfig: { responseMimeType: "application/json", responseSchema: toGeminiSchema(request.schema), maxOutputTokens: 16000 },
      }),
    });
  } catch (error) {
    if ((error as Error)?.name === "TimeoutError" || (error as Error)?.name === "AbortError") throw new Error("timeout: Gemini trả lời quá lâu.");
    throw error;
  }
  const reply = await response.json().catch(() => ({})) as GeminiReply;
  if (!response.ok) throw new Error(`${response.status} ${reply.error?.status ?? ""}: ${reply.error?.message ?? "Gemini lỗi"}`.slice(0, 300));
  if (reply.promptFeedback?.blockReason) throw new Error("Gemini từ chối yêu cầu này.");
  const candidate = reply.candidates?.[0];
  if (candidate?.finishReason === "MAX_TOKENS") throw new Error("Câu trả lời bị cắt ngắn; hãy thử lại.");
  if (["SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION", "SPII"].includes(candidate?.finishReason ?? "")) {
    throw new Error("Gemini từ chối yêu cầu này.");
  }
  const text = (candidate?.content?.parts ?? []).filter((part) => !part.thought).map((part) => part.text ?? "").join("");
  if (!text.trim()) throw new Error("Gemini không trả về nội dung; hãy thử lại.");
  return JSON.parse(text);
}

/** The user's message for a Gemini failure that retrying did not fix. */
export function geminiFailure(error: unknown): Error {
  const message = String((error as Error)?.message ?? error);
  if (/^429\b/.test(message)) return new Error("429: Đã vượt giới hạn số lần gọi Gemini mỗi phút; hãy chờ một phút rồi thử lại.");
  if (/^(500|502|503|504)\b/.test(message)) return new Error(`${message.slice(0, 3)}: Gemini đang quá tải; hãy thử lại sau ít phút.`);
  if (/^timeout\b/.test(message)) return new Error("Gemini trả lời quá lâu; hãy thử lại sau ít phút.");
  return error instanceof Error ? error : new Error(message);
}

/**
 * Bounded retry that changes a condition: the fast model, the same model once more
 * after a pause, then the larger model. Key, refusal and format errors are not retried.
 */
async function askGemini(key: string, request: JsonRequest, send: typeof fetch, wait: (ms: number) => Promise<void>): Promise<unknown> {
  const attempts: Array<[string, number]> = [[GEMINI_MODEL, 0], [GEMINI_MODEL, 4000], [GEMINI_FALLBACK_MODEL, 2000]];
  let last: unknown = null;
  for (const [model, pause] of attempts) {
    if (last !== null) await wait(pause);
    try {
      return await askGeminiOnce(key, request, send, model);
    } catch (error) {
      if (!RETRYABLE.test(String((error as Error)?.message ?? error))) throw error;
      last = error;
    }
  }
  throw geminiFailure(last);
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** One structured answer from the chosen provider. Everything in `request.parts` is sent to that provider. */
export function askJson(provider: Provider, key: string, request: JsonRequest, send: typeof fetch = (...args) => fetch(...args),
                        wait: (ms: number) => Promise<void> = sleep): Promise<unknown> {
  return provider === "gemini" ? askGemini(key, request, send, wait) : askClaude(key, request);
}
