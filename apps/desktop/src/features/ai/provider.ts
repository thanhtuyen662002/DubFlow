import Anthropic from "@anthropic-ai/sdk";

/** Which service writes for the user, with the user's own key (ADR-0042). */
export type Provider = "claude" | "gemini";

export const PROVIDERS: Record<Provider, { name: string; label: string; company: string; console: string; placeholder: string }> = {
  claude: { name: "Claude", label: "Claude (Anthropic)", company: "Anthropic", console: "console.anthropic.com", placeholder: "sk-ant-…" },
  gemini: { name: "Gemini", label: "Gemini (Google)", company: "Google", console: "aistudio.google.com/apikey", placeholder: "AIza…" },
};

export const CLAUDE_MODEL = "claude-opus-5-5";
export const GEMINI_MODEL = "gemini-3.8-flash";
const GEMINI_ENDPOINT = `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent`;

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

async function askGemini(key: string, request: JsonRequest, send: typeof fetch): Promise<unknown> {
  const response = await send(GEMINI_ENDPOINT, {
    method: "POST",
    // The key travels in a header, never in the URL.
    headers: { "content-type": "application/json", "x-goog-api-key": key },
    body: JSON.stringify({
      systemInstruction: { parts: [{ text: request.system }] },
      contents: [{
        role: "user",
        parts: request.parts.map((part) => ("text" in part ? { text: part.text } : { inlineData: { mimeType: part.image.mediaType, data: part.image.data } })),
      }],
      generationConfig: { responseMimeType: "application/json", responseSchema: toGeminiSchema(request.schema), maxOutputTokens: 16000 },
    }),
  });
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

/** One structured answer from the chosen provider. Everything in `request.parts` is sent to that provider. */
export function askJson(provider: Provider, key: string, request: JsonRequest, send: typeof fetch = (...args) => fetch(...args)): Promise<unknown> {
  return provider === "gemini" ? askGemini(key, request, send) : askClaude(key, request);
}
