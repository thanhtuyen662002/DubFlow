import { normalizeSuggestion, writeSuggestion, type PostSuggestion } from "./ai.ts";
import { createAiSession, isKeyError, isProvider, keyHint, PROVIDERS, type AiSession } from "../ai/provider.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

/** Where rc7 kept the key; only ever removed now, never read. */
const LEGACY_KEY_STORAGE = "dubflow.anthropicApiKey.v1";
const CJK = /[㐀-鿿]/;

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing ${selector}`);
  return node;
};

export const KEY_HINT = keyHint("claude");
export const KEY_CLEAR_LABEL = "Xóa khóa đã lưu";
export const KEY_CLEARED = "Đã xóa khóa API khỏi máy này.";

/** The AI provider choice and keys (saved through the host when it gives a store). */
export type SessionKey = AiSession;

export function createSessionKey(): SessionKey {
  return createAiSession();
}

/** Drop a key an earlier version left in persistent WebView storage. */
function purgeLegacyKey(): void {
  try { window.localStorage.removeItem(LEGACY_KEY_STORAGE); } catch { /* nothing stored */ }
}

/** Offline suggestions from `post_suggestions`, or null when the engine failed. */
export function parseOffline(value: unknown): PostSuggestion | null {
  const object = (value ?? {}) as Record<string, unknown>;
  if (object.ok !== true) return null;
  try {
    return normalizeSuggestion(object);
  } catch {
    return null;
  }
}

/** The posting-suggestions dialog for one finished video. */
export function mountPostSuggestions(invoke: Invoke, apiKey: SessionKey = createSessionKey()): { open(outputPath: string, displayName: string): void } {
  const dialog = $("#post-dialog");
  const body = $("#post-body");
  const state = $("#post-state");
  const aiButton = $<HTMLButtonElement>("#post-ai");
  const keyRow = $("#post-key-row");
  const keyInput = $<HTMLInputElement>("#post-key");
  const keyClear = $<HTMLButtonElement>("#post-key-clear");
  const providerSelect = $<HTMLSelectElement>("#post-provider");
  purgeLegacyKey();
  function showProvider(): void {
    providerSelect.value = apiKey.provider();
    keyRow.querySelector(".hint")!.textContent = keyHint(apiKey.provider());
    keyInput.placeholder = PROVIDERS[apiKey.provider()].placeholder;
    keyInput.setAttribute("aria-label", `Khóa API ${PROVIDERS[apiKey.provider()].name}`);
  }
  showProvider();
  providerSelect.addEventListener("change", () => {
    if (isProvider(providerSelect.value)) apiKey.setProvider(providerSelect.value);
    showProvider();
    keyRow.hidden = Boolean(apiKey.read()) || keyRow.hidden;
  });
  keyClear.textContent = KEY_CLEAR_LABEL;
  let outputPath = "";
  let sourceTitle = "";
  let request = 0;

  function copyButton(text: () => string): HTMLButtonElement {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "link copy";
    button.textContent = "Sao chép";
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(text());
        button.textContent = "Đã chép";
        window.setTimeout(() => { button.textContent = "Sao chép"; }, 1500);
      } catch {
        button.textContent = "Không chép được";
      }
    });
    return button;
  }

  function section(title: string, copy: () => string): HTMLElement {
    const block = document.createElement("section");
    block.className = "post-section";
    const head = document.createElement("div");
    head.className = "post-head";
    const label = document.createElement("h3");
    label.textContent = title;
    head.append(label, copyButton(copy));
    block.append(head);
    return block;
  }

  function render(suggestion: PostSuggestion, source: string): void {
    const titles = section("Tiêu đề", () => suggestion.titles.join("\n"));
    const list = document.createElement("ol");
    list.className = "post-titles";
    for (const title of suggestion.titles) {
      const item = document.createElement("li");
      const text = document.createElement("span");
      text.textContent = title;
      item.append(text, copyButton(() => title));
      list.append(item);
    }
    titles.append(list);
    const description = section("Mô tả", () => suggestion.description);
    const paragraph = document.createElement("p");
    paragraph.textContent = suggestion.description;
    description.append(paragraph);
    const hashtags = section("Hashtag", () => suggestion.hashtags.join(" "));
    const tags = document.createElement("p");
    tags.className = "post-tags";
    tags.textContent = suggestion.hashtags.join(" ");
    hashtags.append(tags);
    const origin = document.createElement("p");
    origin.className = "hint";
    origin.textContent = source;
    body.replaceChildren(origin, titles, description, hashtags);
  }

  async function loadOffline(): Promise<void> {
    const current = ++request;
    state.textContent = "Đang tạo gợi ý…";
    body.replaceChildren();
    try {
      const value = await invoke<unknown>("post_suggestions", { outputPath, sourceTitle: CJK.test(sourceTitle) ? "" : sourceTitle });
      if (current !== request) return;
      const suggestion = parseOffline(value);
      if (!suggestion) {
        state.textContent = "Chưa tạo được gợi ý cho video này.";
        return;
      }
      render(suggestion, "Gợi ý tạo offline từ phụ đề tiếng Việt. Hãy chỉnh lại cho hợp kênh của bạn.");
      state.textContent = "";
    } catch (error) {
      if (current === request) state.textContent = String(error).slice(0, 200);
    }
  }

  async function runAi(): Promise<void> {
    const key = apiKey.read();
    if (!key) {
      keyRow.hidden = false;
      keyInput.focus();
      return;
    }
    const current = ++request;
    aiButton.disabled = true;
    const provider = apiKey.provider();
    const { name, company } = PROVIDERS[provider];
    state.textContent = `${name} đang viết gợi ý…`;
    try {
      const cues = await invoke<Array<{ text: string }>>("review_cues", { outputPath });
      const subtitles = cues.map((cue) => cue.text).filter((text) => typeof text === "string" && text.trim());
      const suggestion = await writeSuggestion(provider, key, subtitles, sourceTitle);
      if (current !== request) return;
      render(suggestion, `Gợi ý do ${name} viết từ phụ đề tiếng Việt (đã gửi phụ đề tới ${company}).`);
      state.textContent = "";
    } catch (error) {
      if (current !== request) return;
      const message = String(error);
      state.textContent = isKeyError(message)
        ? `Khóa API không hợp lệ. Nhập lại khóa ${name}.`
        : `Không viết được bằng AI: ${message.slice(0, 160)}`;
      if (isKeyError(message)) keyRow.hidden = false;
    } finally {
      aiButton.disabled = false;
    }
  }

  aiButton.addEventListener("click", () => void runAi());
  $("#post-key-save").addEventListener("click", async () => {
    try {
      await apiKey.set(keyInput.value);
    } catch (error) {
      state.textContent = String(error).slice(0, 200);
      return;
    }
    keyInput.value = "";
    if (!apiKey.read()) return;
    keyRow.hidden = true;
    void runAi();
  });
  keyClear.addEventListener("click", async () => {
    try {
      await apiKey.clear();
      keyInput.value = "";
      state.textContent = KEY_CLEARED;
    } catch (error) {
      state.textContent = String(error).slice(0, 200);
    }
  });
  $("#post-close").addEventListener("click", () => { dialog.hidden = true; request += 1; });
  dialog.addEventListener("keydown", (event) => { if (event.key === "Escape") { dialog.hidden = true; request += 1; } });

  return {
    open(path: string, displayName: string) {
      outputPath = path;
      sourceTitle = displayName.replace(/\.[A-Za-z0-9]{2,4}$/, "");
      keyRow.hidden = true;
      showProvider();
      dialog.hidden = false;
      void loadOffline();
    },
  };
}
