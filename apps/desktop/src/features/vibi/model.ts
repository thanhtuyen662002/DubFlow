/**
 * Vibi.pro voices (ElevenLabs, MiniMax) with the user's own key (#632): the
 * chosen voice and every parameter, kept in WebView storage (not a secret; the
 * key lives in Windows Credential Manager on the host).
 */

export type VibiProvider = "elevenlabs" | "minimax";
export type VibiSource = "shared" | "default" | "system" | "cloned";

/** One voice and all of its parameters, as the engine's VoiceSettings reads them. */
export type VibiSettings = {
  provider: VibiProvider;
  voice_id: string;
  voice_name: string;
  model_id: string;
  language_code?: string;
  stability: number;
  similarity_boost: number;
  style: number;
  use_speaker_boost: boolean;
  speed: number;
  pitch: number;
  vol: number;
  preview_url?: string;
};

export type VibiVoice = {
  provider: VibiProvider;
  voice_id: string;
  name: string;
  gender: string;
  age: string;
  accent: string;
  language: string;
  languages: string[];
  description: string;
  preview_url: string;
  image_url: string;
  tags: string[];
  popularity: number | null;
};

export type VibiModel = { model_id: string; name: string; description: string; can_use_style?: boolean; can_use_speaker_boost?: boolean };

/** Where DubFlow uses the chosen voice. */
export type VibiUse = { dubbing: boolean; documentary: boolean };
export type VibiChoice = { settings: VibiSettings | null; use: VibiUse };

export const SOURCES: Record<VibiSource, { provider: VibiProvider; label: string; hint: string }> = {
  shared: { provider: "elevenlabs", label: "ElevenLabs · Thư viện", hint: "Hàng nghìn giọng cộng đồng, lọc theo giới tính, tuổi, giọng vùng, mục đích" },
  default: { provider: "elevenlabs", label: "ElevenLabs · Mặc định", hint: "Các giọng gốc của ElevenLabs" },
  system: { provider: "minimax", label: "MiniMax · Hệ thống", hint: "Giọng MiniMax theo ngôn ngữ, giới tính, mục đích" },
  cloned: { provider: "minimax", label: "MiniMax · Nhân bản", hint: "Giọng bạn đã nhân bản trên Vibi.pro" },
};

/** Shown before the key lists the account's own models (and if listing fails). */
export const FALLBACK_MODELS: Record<VibiProvider, VibiModel[]> = {
  elevenlabs: [
    { model_id: "eleven_multilingual_v2", name: "Eleven Multilingual v2", description: "Giọng tự nhiên nhất, nhiều ngôn ngữ", can_use_style: true, can_use_speaker_boost: true },
    { model_id: "eleven_turbo_v2_5", name: "Eleven Turbo v2.5", description: "Nhanh, rẻ hơn khoảng 50 % credit", can_use_style: true, can_use_speaker_boost: true },
    { model_id: "eleven_flash_v2_5", name: "Eleven Flash v2.5", description: "Nhanh nhất, rẻ nhất", can_use_style: false, can_use_speaker_boost: true },
    { model_id: "eleven_v3", name: "Eleven v3", description: "Biểu cảm mạnh nhất", can_use_style: false, can_use_speaker_boost: false },
  ],
  minimax: [
    { model_id: "speech-2.8-hd", name: "Speech 2.8 HD", description: "Chất lượng cao nhất" },
    { model_id: "speech-2.8-turbo", name: "Speech 2.8 Turbo", description: "Nhanh, tiết kiệm" },
  ],
};

/** Oscar's settings for English Mega Projects documentaries. */
export const MEGA_PROJECTS = { model_id: "eleven_turbo_v2_5", stability: 0.45, similarity_boost: 0.78, style: 0.15, use_speaker_boost: true, speed: 1 } as const;

/** The Mega Projects ranges, shown beside each slider. */
export const MEGA_RANGES = { stability: [0.4, 0.5], similarity_boost: [0.75, 0.8], style: [0.1, 0.2] } as const;

export const LIMITS = {
  elevenlabs: { speed: [0.7, 1.2] },
  minimax: { speed: [0.5, 2], pitch: [-12, 12], vol: [0.01, 10] },
} as const;

/** The line read by "Nghe thử với cài đặt này": punctuation that ElevenLabs pauses on before a big number. */
export const SAMPLE_LINE = "This bridge didn't just cost millions... it swallowed a staggering — twenty billion dollars.";
export const SAMPLE_LINE_VI = "Cây cầu này không chỉ tốn hàng triệu đô... nó nuốt trọn một con số khủng khiếp — hai mươi tỷ đô la.";

const STORAGE = "dubflow.vibiVoice.v1";

const clamp = (value: unknown, low: number, high: number, fallback: number): number => {
  const number = typeof value === "number" && Number.isFinite(value) ? value : fallback;
  return Math.round(Math.min(high, Math.max(low, number)) * 1000) / 1000;
};

export function defaultSettings(provider: VibiProvider, voice?: Pick<VibiVoice, "voice_id" | "name" | "preview_url">): VibiSettings {
  return {
    provider, voice_id: voice?.voice_id ?? "", voice_name: voice?.name ?? "", preview_url: voice?.preview_url || undefined,
    model_id: provider === "elevenlabs" ? MEGA_PROJECTS.model_id : FALLBACK_MODELS.minimax[0].model_id,
    ...(provider === "elevenlabs" ? { stability: MEGA_PROJECTS.stability, similarity_boost: MEGA_PROJECTS.similarity_boost, style: MEGA_PROJECTS.style }
      : { stability: 0.5, similarity_boost: 0.75, style: 0 }),
    use_speaker_boost: true, speed: 1, pitch: 0, vol: 1,
  };
}

/** Any stored or typed value as valid settings, every number inside its provider's range; null when unusable. */
export function normalizeSettings(value: unknown): VibiSettings | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const provider = item.provider === "minimax" ? "minimax" : item.provider === "elevenlabs" ? "elevenlabs" : null;
  if (!provider || typeof item.voice_id !== "string" || !/^[A-Za-z0-9_.:()\- ]{1,128}$/.test(item.voice_id.trim())) return null;
  const model = typeof item.model_id === "string" && /^[A-Za-z0-9_.:()\- ]{1,80}$/.test(item.model_id) ? item.model_id : defaultSettings(provider).model_id;
  const speed = provider === "elevenlabs" ? LIMITS.elevenlabs.speed : LIMITS.minimax.speed;
  return {
    provider, voice_id: item.voice_id.trim(), model_id: model,
    voice_name: typeof item.voice_name === "string" ? item.voice_name.replace(/\s+/g, " ").trim().slice(0, 120) : "",
    ...(typeof item.language_code === "string" && /^[A-Za-z ()-]{2,40}$/.test(item.language_code) ? { language_code: item.language_code } : {}),
    stability: clamp(item.stability, 0, 1, 0.5),
    similarity_boost: clamp(item.similarity_boost, 0, 1, 0.75),
    style: clamp(item.style, 0, 1, 0),
    use_speaker_boost: item.use_speaker_boost !== false,
    speed: clamp(item.speed, speed[0], speed[1], 1),
    pitch: Math.round(clamp(item.pitch, -12, 12, 0)),
    vol: clamp(item.vol, 0.01, 10, 1),
    ...(typeof item.preview_url === "string" && item.preview_url.startsWith("https://") ? { preview_url: item.preview_url.slice(0, 2000) } : {}),
  };
}

/** The Mega Projects preset on top of the current voice (ElevenLabs only). */
export function withMegaProjects(settings: VibiSettings): VibiSettings {
  return settings.provider === "elevenlabs" ? { ...settings, ...MEGA_PROJECTS } : settings;
}

/** What the engine receives for one use: the language set by where the voice speaks. */
export function settingsFor(settings: VibiSettings, use: "dubbing" | "documentary"): Record<string, unknown> {
  const language = use === "dubbing" ? (settings.provider === "elevenlabs" ? "vi" : "Vietnamese") : (settings.provider === "elevenlabs" ? "en" : "English");
  const { preview_url: _preview, ...rest } = settings;
  return { ...rest, language_code: language };
}

const percent = (value: number) => `${Math.round(value * 100)} %`;

export function modelName(provider: VibiProvider, modelId: string): string {
  return FALLBACK_MODELS[provider].find((model) => model.model_id === modelId)?.name.replace(/^Eleven /, "") ?? modelId;
}

/** One line describing the choice: "Arnold · ElevenLabs Turbo v2.5 · ổn định 45 % · rõ 78 % · kịch tính 15 %". */
export function settingsSummary(settings: VibiSettings): string {
  const name = settings.voice_name || settings.voice_id;
  if (settings.provider === "minimax") {
    return `${name} · MiniMax ${modelName("minimax", settings.model_id)} · tốc độ ${settings.speed}× · cao độ ${settings.pitch > 0 ? "+" : ""}${settings.pitch} · âm lượng ${settings.vol}`;
  }
  return `${name} · ElevenLabs ${modelName("elevenlabs", settings.model_id)} · ổn định ${percent(settings.stability)} · rõ ${percent(settings.similarity_boost)} · kịch tính ${percent(settings.style)}` +
    (settings.speed !== 1 ? ` · tốc độ ${settings.speed}×` : "");
}

/** Advice beside the sliders, from Oscar's Mega Projects notes. */
export function settingsAdvice(settings: VibiSettings): string[] {
  if (settings.provider !== "elevenlabs") return [];
  const advice: string[] = [];
  if (settings.stability > 0.7) advice.push("Ổn định trên 70 % làm giọng đều đều như đọc báo, dễ gây buồn ngủ; 40–50 % có nhấn nhá tự nhiên hơn.");
  if (settings.similarity_boost < 0.6) advice.push("Rõ nét dưới 60 % dễ bị rè khi đè lên nhạc nền Cinematic; nên để 75–80 %.");
  if (settings.style > 0.35) advice.push("Kịch tính cao dễ làm giọng bị “kịch”, mất tự nhiên; 10–20 % là đủ.");
  return advice;
}

export function loadChoice(storage: Pick<Storage, "getItem"> | null = safeStorage()): VibiChoice {
  try {
    const value = JSON.parse(storage?.getItem(STORAGE) ?? "{}") as Record<string, unknown>;
    const use = (value.use ?? {}) as Record<string, unknown>;
    const settings = normalizeSettings(value.settings);
    return { settings, use: { dubbing: Boolean(settings) && use.dubbing === true, documentary: Boolean(settings) && use.documentary === true } };
  } catch {
    return { settings: null, use: { dubbing: false, documentary: false } };
  }
}

export function saveChoice(choice: VibiChoice, storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
  try { storage?.setItem(STORAGE, JSON.stringify(choice)); } catch { /* the choice resets next start */ }
}

function safeStorage(): Storage | null {
  try { return typeof window === "undefined" ? null : window.localStorage; } catch { return null; }
}

/** The `cloudVoice` argument of `start_job`: the voice when "Lồng tiếng" should use it, else null. */
export function dubbingVoice(choice: VibiChoice, dubbing: boolean): Record<string, unknown> | null {
  return dubbing && choice.use.dubbing && choice.settings ? settingsFor(choice.settings, "dubbing") : null;
}

/** Parse one page of voices from the host. */
export function parseVoicePage(value: unknown): { voices: VibiVoice[]; hasMore: boolean; total: number | null } {
  const page = (value ?? {}) as Record<string, unknown>;
  const text = (item: unknown, limit = 200) => (typeof item === "string" ? item.slice(0, limit) : "");
  const voices = (Array.isArray(page.voices) ? page.voices : []).filter((item): item is Record<string, unknown> =>
    Boolean(item) && typeof item === "object" && typeof (item as Record<string, unknown>).voice_id === "string" && Boolean((item as Record<string, unknown>).voice_id))
    .map((item) => ({
      provider: item.provider === "minimax" ? "minimax" as const : "elevenlabs" as const,
      voice_id: text(item.voice_id, 128), name: text(item.name, 120) || text(item.voice_id, 128), gender: text(item.gender, 20),
      age: text(item.age, 20), accent: text(item.accent, 40), language: text(item.language, 20),
      languages: Array.isArray(item.languages) ? item.languages.filter((lang): lang is string => typeof lang === "string").slice(0, 12) : [],
      description: text(item.description, 300), preview_url: text(item.preview_url, 2000).startsWith("https://") ? text(item.preview_url, 2000) : "",
      image_url: text(item.image_url, 2000).startsWith("https://") ? text(item.image_url, 2000) : "",
      tags: Array.isArray(item.tags) ? item.tags.filter((tag): tag is string => typeof tag === "string").slice(0, 8) : [],
      popularity: typeof item.popularity === "number" ? item.popularity : null,
    }));
  return { voices, hasMore: page.has_more === true, total: typeof page.total === "number" ? page.total : null };
}

const GENDERS: Record<string, string> = { male: "Nam", female: "Nữ", neutral: "Trung tính" };

/** "Nam · trung niên · american · tiếng en, vi" */
export function voiceTraits(voice: VibiVoice): string {
  const parts = [GENDERS[voice.gender.toLowerCase()] ?? voice.gender, voice.age.replace("middle aged", "trung niên").replace("young", "trẻ").replace("old", "lớn tuổi"),
    voice.accent, voice.languages.length ? `tiếng ${voice.languages.join(", ")}` : voice.language ? `tiếng ${voice.language}` : ""];
  return parts.filter(Boolean).join(" · ");
}
