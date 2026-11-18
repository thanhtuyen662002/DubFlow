import { PROVIDERS, type AiSession, type Provider } from "./provider.ts";
import { BEEP_SOUNDS, type BeepSound } from "./beep_sound.ts";
import { providerReady, type AiContext } from "./ai_context.ts";

/**
 * How a new video's subtitles are cleaned (Cài đặt › Dịch phụ đề): the words that platforms'
 * community rules often flag. Not secret: kept in local storage. Whether the default provider's
 * saved key translates in context is asked and stored on its own (ai_context.ts), so a save of
 * these settings never changes it.
 */
export type SensitiveMode = "off" | "variant" | "beep";
export type SensitiveWord = { from: string; to: string };
/**
 * `beepSound`: what covers a flagged word in the voice when the mode is beep
 * (the tone, a bird, a dog, or `beepFile`, a sound the user picked).
 */
export type TranslationPrefs = {
  sensitiveMode: SensitiveMode; words: SensitiveWord[]; beepSound: BeepSound; beepFile: string | null;
};

const STORAGE = "dubflow.translationPrefs.v1";

/** A starting list (same as engine/dubflow/translation/sensitive.py); the user edits it. */
export const DEFAULT_SENSITIVE_WORDS: readonly SensitiveWord[] = [
  ["chết", "chớt"], ["giết", "giớt"], ["dao", "dzao"], ["súng", "sún"], ["máu", "máo"], ["tự tử", "tự tữ"], ["tự sát", "tự xát"],
  ["đâm", "đăm"], ["bắn", "bắng"], ["ma túy", "ma thúy"], ["ma tuý", "ma thuý"], ["đánh bom", "đánh bôm"], ["khủng bố", "khủng bớ"],
  ["thi thể", "thi thễ"], ["xác chết", "xác chớt"], ["cờ bạc", "cờ bặc"],
].map(([from, to]) => ({ from, to }));

export const DEFAULT_PREFS: TranslationPrefs = {
  sensitiveMode: "off", words: [...DEFAULT_SENSITIVE_WORDS], beepSound: "beep", beepFile: null,
};
const MAX_PATH = 1024;

export const MAX_WORDS = 300;
export const MAX_WORD_CHARS = 40;

/** "chết = chớt" per line (or "chết" alone, masked/beeped as is); blank and duplicate lines are dropped. */
export function parseWordList(text: string): SensitiveWord[] {
  const seen = new Set<string>();
  const words: SensitiveWord[] = [];
  for (const line of text.split(/\r?\n/)) {
    const [left, ...rest] = line.split(/=|→|->/);
    const from = (left ?? "").normalize("NFC").replace(/\s+/g, " ").trim().toLowerCase();
    const to = rest.join("=").normalize("NFC").replace(/\s+/g, " ").trim() || from;
    if (!from || from.length > MAX_WORD_CHARS || to.length > MAX_WORD_CHARS || seen.has(from)) continue;
    seen.add(from);
    words.push({ from, to });
    if (words.length >= MAX_WORDS) break;
  }
  return words;
}

export function formatWordList(words: readonly SensitiveWord[]): string {
  return words.map(({ from, to }) => (to && to !== from ? `${from} = ${to}` : from)).join("\n");
}

export function normalizePrefs(value: unknown): TranslationPrefs {
  const raw = (value && typeof value === "object" ? value : {}) as Partial<TranslationPrefs>;
  const mode: SensitiveMode = raw.sensitiveMode === "variant" || raw.sensitiveMode === "beep" ? raw.sensitiveMode : "off";
  const words = Array.isArray(raw.words) ? parseWordList(formatWordList(raw.words.filter((word): word is SensitiveWord =>
    Boolean(word) && typeof word.from === "string" && typeof word.to === "string"))) : [...DEFAULT_SENSITIVE_WORDS];
  const beepFile = typeof raw.beepFile === "string" && raw.beepFile.trim() && raw.beepFile.length <= MAX_PATH && !/[\x00-\x1f]/.test(raw.beepFile)
    ? raw.beepFile : null;
  const picked = BEEP_SOUNDS.includes(raw.beepSound as BeepSound) ? raw.beepSound as BeepSound : "beep";
  // A file choice without its file plays the plain beep.
  const beepSound = picked === "custom" && !beepFile ? "beep" : picked;
  return { sensitiveMode: mode, words, beepSound, beepFile };
}

/** `sensitiveWords` for the host: the words, and in beep mode the covering sound. */
export function sensitiveArgs(prefs: TranslationPrefs): Record<string, unknown> | null {
  if (prefs.sensitiveMode === "off" || !prefs.words.length) return null;
  if (prefs.sensitiveMode !== "beep") return { mode: prefs.sensitiveMode, words: prefs.words };
  const custom = prefs.beepSound === "custom" && prefs.beepFile;
  const sound = custom ? "custom" : BEEP_SOUNDS.includes(prefs.beepSound) && prefs.beepSound !== "custom" ? prefs.beepSound : "beep";
  return { mode: "beep", words: prefs.words, sound, ...(custom ? { sound_file: prefs.beepFile } : {}) };
}

export function loadPrefs(): TranslationPrefs {
  try {
    const text = window.localStorage.getItem(STORAGE);
    return text ? normalizePrefs(JSON.parse(text)) : { ...DEFAULT_PREFS, words: [...DEFAULT_SENSITIVE_WORDS] };
  } catch {
    return { ...DEFAULT_PREFS, words: [...DEFAULT_SENSITIVE_WORDS] };
  }
}

export function savePrefs(prefs: TranslationPrefs): void {
  try { window.localStorage.setItem(STORAGE, JSON.stringify(prefs)); } catch { /* the defaults apply next start */ }
}

/**
 * The `start_job` arguments for a new video: the provider that translates in context
 * (only when the user said yes to it and its key is saved; the host reads the key itself)
 * and the sensitive words. `aiContext` is required so no caller can forget the user's answer.
 */
export function translationJobArgs(prefs: TranslationPrefs, session: Pick<AiSession, "provider" | "saved" | "options">, title: string,
  aiContext: AiContext): Record<string, unknown> {
  const provider: Provider = session.provider();
  const options = session.options(provider);
  const info = PROVIDERS[provider];
  return {
    aiTranslation: aiContext === "on" && providerReady(session, provider) ? {
      provider,
      model: options.model?.trim() || info.model || null,
      baseUrl: info.baseUrl !== undefined ? (options.baseUrl?.trim() || info.baseUrl || null) : null,
    } : null,
    sensitiveWords: sensitiveArgs(prefs),
    title: title.slice(0, 200),
  };
}
