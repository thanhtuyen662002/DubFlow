/**
 * "Dịch phụ đề bằng AI theo ngữ cảnh": asked once before a saved key is used for it (Part L item 1).
 *
 * Before, AI translation was on unless the user had turned it off, so saving a key (or starting a fresh profile on a
 * machine with a key already saved) made every new video's subtitles a run of paid AI calls without a word. Now the
 * choice is "ask" until the user answers: no AI translation meanwhile. The question comes once, when a key is saved in
 * Cài đặt › Tài khoản, or at the first dubbing job or Tách phụ đề while a provider is ready, whichever comes first. It
 * is stored on its own, so a save of the other translation settings can never change it.
 */
import { confirmDialog, type ConfirmOptions } from "../../shell/confirm.ts";
import { PROVIDER_IDS, PROVIDERS, type AiSession, type Provider } from "./provider.ts";

/** Not chosen yet (no AI; ask before the first use), on, or off. */
export type AiContext = "ask" | "on" | "off";

/** "on" | "off" | "ask"; absent: read once from the older translation settings. */
export const AI_CONTEXT_KEY = "dubflow.aiContext.v1";
/** The older settings (translation_prefs.ts), where only an explicit `aiTranslation: false` was the user's choice. */
const LEGACY_KEY = "dubflow.translationPrefs.v1";

export type ContextStore = { getItem(key: string): string | null; setItem(key: string, value: string): void };

/** The choice from the stored value and, when none is stored, the older settings: a stored default is not consent. */
export function readAiContext(stored: string | null, legacyPrefs: string | null): AiContext {
  if (stored === "on" || stored === "off" || stored === "ask") return stored;
  try {
    const legacy: unknown = legacyPrefs ? JSON.parse(legacyPrefs) : null;
    if (legacy && typeof legacy === "object" && (legacy as { aiTranslation?: unknown }).aiTranslation === false) return "off";
  } catch {
    // unreadable: not a choice
  }
  return "ask";
}

function browserStore(): ContextStore | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** Answers that could not be stored last for this run (per store). */
const unsaved = new WeakMap<object, AiContext>();
const NO_STORE = {};

/** The current choice; an older explicit "off" is written to the new key the first time. */
export function loadAiContext(store: ContextStore | null = browserStore()): AiContext {
  // An answer that could not be written is newer than whatever the store still holds.
  const remembered = unsaved.get(store ?? NO_STORE);
  if (remembered) return remembered;
  if (!store) return "ask";
  let stored: string | null;
  let legacy: string | null;
  try {
    stored = store.getItem(AI_CONTEXT_KEY);
    legacy = stored === null ? store.getItem(LEGACY_KEY) : null;
  } catch {
    return "ask";
  }
  const value = readAiContext(stored, legacy);
  if (stored === null && value === "off") saveAiContext(value, store);
  return value;
}

export function saveAiContext(value: AiContext, store: ContextStore | null = browserStore()): void {
  unsaved.delete(store ?? NO_STORE);
  try {
    if (!store) throw new Error("no storage");
    store.setItem(AI_CONTEXT_KEY, value);
  } catch {
    unsaved.set(store ?? NO_STORE, value);
  }
}

/** Whether `provider` can translate now: a saved key, or for a custom server its address and model. */
export function providerReady(session: Pick<AiSession, "saved" | "options">, provider: Provider): boolean {
  return provider === "custom"
    ? Boolean(session.options(provider).baseUrl && session.options(provider).model)
    : session.saved(provider) !== null;
}

/**
 * After a key is deleted: with no provider ready any more, an "on" goes back to "ask", so the next first key asks
 * again instead of being used at once. "off" stays: the user said no.
 */
export function resetAiContextIfNoKey(session: Pick<AiSession, "saved" | "options">, store: ContextStore | null = browserStore()): void {
  if (PROVIDER_IDS.some((id) => providerReady(session, id))) return;
  if (loadAiContext(store) === "on") saveAiContext("ask", store);
}

/** The question: what is sent where, that it is charged, and that "Không" keeps the offline translation. */
export function aiContextPrompt(provider: Provider, moment: "saved" | "start"): ConfirmOptions {
  const info = PROVIDERS[provider];
  const custom = provider === "custom";
  const name = custom ? "máy chủ AI riêng" : info.name;
  const opening = moment === "saved"
    ? (custom ? "Bạn vừa nhập máy chủ AI riêng." : `Bạn vừa lưu khóa ${name}.`)
    : (custom
      ? "Máy này đã nhập máy chủ AI riêng, nhưng DubFlow chưa hỏi bạn có dùng máy chủ đó để dịch phụ đề hay không."
      : `Máy này đã lưu khóa ${name}, nhưng DubFlow chưa hỏi bạn có dùng khóa này để dịch phụ đề hay không.`);
  const sent = custom
    ? "• Lời thoại của video được gửi tới máy chủ bạn đã nhập; nếu máy chủ đó tính phí thì phí tính vào tài khoản của bạn."
    : `• Lời thoại của video được gửi tới ${info.company}.\n• Mỗi video tốn nhiều lượt gọi AI, tính phí vào tài khoản ${name} của bạn.`;
  const free = provider === "gemini"
    ? "\n• Khóa miễn phí của Gemini không mất tiền nhưng giới hạn lượt gọi, và Google có thể dùng lời thoại để cải thiện sản phẩm."
    : "";
  return {
    title: "Dịch phụ đề bằng AI theo ngữ cảnh?",
    message: `${opening}\n\nNếu bật, ${name} sẽ dịch phụ đề của mỗi video mới theo cả đoạn hội thoại (xưng hô, thành ngữ, văn nói), `
      + `tự nhiên hơn bản dịch offline.\n\n${sent}${free}\n• Nếu không bật, phụ đề dịch bằng bộ dịch offline trên máy, không mất phí.\n\n`
      + "Có thể đổi bất cứ lúc nào trong Cài đặt › Dịch phụ đề.",
    confirmLabel: "Bật dịch AI",
    cancelLabel: "Không, dịch offline",
    // Enter turns it on only from the "Bật dịch AI" button itself, and a click beside the dialog (the second click of
    // a double-click on Bắt đầu) is no answer: only a button or Esc answers.
    initialFocus: "cancel",
    backdropCancels: false,
  };
}

/** The line shown after the answer. */
export function aiContextAnswered(provider: Provider, value: AiContext): string {
  const name = provider === "custom" ? "máy chủ AI riêng" : PROVIDERS[provider].name;
  return value === "on"
    ? `Đã bật dịch phụ đề bằng ${name}. Tắt trong Cài đặt › Dịch phụ đề.`
    : "Phụ đề vẫn dịch offline. Bật dịch AI trong Cài đặt › Dịch phụ đề khi cần.";
}

/**
 * The Cài đặt › Dịch phụ đề hint while `provider` is ready, for each choice. `inUse` is the current text that says
 * what AI translation does and where the dialogue goes.
 */
export function aiContextHint(provider: Provider, value: AiContext, inUse: string): string {
  const custom = provider === "custom";
  const name = custom ? "máy chủ AI riêng" : PROVIDERS[provider].name;
  const charged = custom ? "nếu máy chủ đó tính phí thì phí tính vào tài khoản của bạn" : "tính phí vào tài khoản của bạn";
  if (value === "on") {
    return custom ? `${inUse} Nếu máy chủ đó tính phí thì phí tính vào tài khoản của bạn.`
      : `${inUse} Mỗi video tốn nhiều lượt gọi AI, tính phí vào tài khoản ${name} của bạn.`;
  }
  if (value === "ask") {
    return `Chưa bật. Trước video đầu tiên, DubFlow sẽ hỏi bạn có dùng ${custom ? name : `khóa ${name}`} để dịch theo ngữ cảnh không `
      + `(${charged}). Cho tới lúc đó phụ đề dịch offline.`;
  }
  return `Đang tắt: phụ đề dịch offline (dịch từng câu), không dùng ${custom ? name : `khóa ${name}`}. `
    + `Bật mục này để ${name} dịch theo ngữ cảnh (${charged}).`;
}

let asking: Promise<AiContext> | null = null;
let keysClosed = false;

/**
 * DUBFLOW_NO_SAVED_KEYS (a candidate or test run beside the installed app, from ai_key_status): no question and no AI
 * translation at all for this run, a custom server included (it needs no key, so closing the saved keys alone does
 * not stop it). The host drops it too.
 */
export function setKeysClosed(closed: boolean): void {
  keysClosed = closed;
}

/**
 * The choice to use now. Still "ask" with the default provider ready: the question, once (callers at the same time
 * share it), and the answer is kept; Esc is "Không", a click outside is no answer. Nothing ready: "ask", which sends
 * no AI. Keys closed to this run: "off" for this run, nothing asked or kept.
 */
export function decideAiContext(session: Pick<AiSession, "provider" | "saved" | "options">, moment: "saved" | "start",
  confirm: (options: ConfirmOptions) => Promise<boolean> = confirmDialog, store: ContextStore | null = browserStore()): Promise<AiContext> {
  if (keysClosed) return Promise.resolve("off");
  const current = loadAiContext(store);
  const provider = session.provider();
  if (current !== "ask" || !providerReady(session, provider)) return Promise.resolve(current);
  if (asking) return asking;
  const run = (async (): Promise<AiContext> => {
    let yes: boolean;
    try {
      yes = (await confirm(aiContextPrompt(provider, moment))) === true;
    } catch {
      // no answer: offline this time, asked again next time
      return "off";
    }
    const answer: AiContext = yes ? "on" : "off";
    saveAiContext(answer, store);
    return answer;
  })();
  asking = run;
  void run.finally(() => { if (asking === run) asking = null; });
  return run;
}
