import {
  checkProvider, isKeyError, isProvider, KEY_STORAGE_NOTE, PROVIDER_IDS, PROVIDERS, setKeyFor,
  type AiSession, type KeyStore, type Provider, type ProviderOptions,
} from "./provider.ts";
import {
  DEFAULT_SENSITIVE_WORDS, formatWordList, loadPrefs, parseWordList, savePrefs, type SensitiveMode, type TranslationPrefs,
} from "./translation_prefs.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

/** The choice of provider and each provider's model are not secrets; only they live in WebView storage. */
const PROVIDER_STORAGE = "dubflow.aiProvider.v1";
const OPTIONS_STORAGE = "dubflow.aiOptions.v1";

/** Keys in the host's Windows Credential Manager (`ai_key_*`), the provider choice and models in local storage. */
export function hostKeyStore(invoke: Invoke): KeyStore {
  return {
    load: (provider) => invoke<string | null>("ai_key_get", { provider }),
    save: (provider, key) => invoke<string>("ai_key_set", { provider, key }),
    remove: (provider) => invoke<void>("ai_key_delete", { provider }),
    loadProvider: () => {
      try {
        const value = window.localStorage.getItem(PROVIDER_STORAGE);
        return isProvider(value) ? value : null;
      } catch {
        return null;
      }
    },
    saveProvider: (provider) => {
      try { window.localStorage.setItem(PROVIDER_STORAGE, provider); } catch { /* the choice just resets next start */ }
    },
    loadOptions: () => {
      try {
        const value = JSON.parse(window.localStorage.getItem(OPTIONS_STORAGE) ?? "{}") as Record<string, ProviderOptions>;
        return Object.fromEntries(Object.entries(value).filter(([id, item]) => isProvider(id) && item && typeof item === "object")
          .map(([id, item]) => [id, { ...(typeof item.model === "string" ? { model: item.model } : {}),
            ...(typeof item.baseUrl === "string" ? { baseUrl: item.baseUrl } : {}) }]));
      } catch {
        return {};
      }
    },
    saveOptions: (options) => {
      try { window.localStorage.setItem(OPTIONS_STORAGE, JSON.stringify(options)); } catch { /* defaults next start */ }
    },
  };
}

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing ${selector}`);
  return node;
};

function element<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> = {}, ...children: (Node | string)[]): HTMLElementTagNameMap[K] {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

/** The user's message for a failed "Kiểm tra khóa". */
export function checkMessage(provider: Provider, error: unknown): string {
  const name = PROVIDERS[provider].name;
  const text = String((error as Error)?.message ?? error).replace(/^Error: /, "");
  if (isKeyError(error)) return `Khóa ${name} không dùng được: ${text.replace(/^KEY: /, "")}`;
  if (/^BALANCE:/.test(text)) return `Khóa đúng nhưng tài khoản ${name} hết số dư hoặc hạn mức. Hãy nạp thêm rồi thử lại.`;
  if (/^NETWORK:/.test(text)) return `Không kết nối được ${name}: hãy kiểm tra mạng hoặc địa chỉ máy chủ.`;
  return `Chưa kiểm tra được ${name}: ${text.replace(/^[A-Z]+: /, "")}`.slice(0, 300);
}

/** "Cài đặt AI": the default provider, its key, model and server, subtitle translation and sensitive words. */
export function mountAiSettings(session: AiSession): { open(): void; prefs(): TranslationPrefs } {
  const dialog = $("#ai-settings-dialog");
  const state = $("#ai-settings-state");
  const providerSelect = $<HTMLSelectElement>("#ai-settings-provider");
  providerSelect.replaceChildren(...PROVIDER_IDS.map((id) => element("option", { value: id, textContent: PROVIDERS[id].label })));
  $("#ai-settings-note").textContent = `${KEY_STORAGE_NOTE.replace("; đổi hoặc xóa trong “Cài đặt AI”", "")} Khóa chỉ được dùng để gọi đúng nhà cung cấp đó khi bạn dùng tính năng AI hoặc dịch phụ đề.`;
  let prefs = loadPrefs();

  function card(provider: Provider): HTMLElement {
    const info = PROVIDERS[provider];
    const saved = session.saved(provider);
    const options = session.options(provider);
    const status = element("span", { className: saved ? "ai-key-status saved" : "ai-key-status", textContent: saved ? `Đã lưu (${saved})` : "Chưa có khóa" });
    const head = element("div", { className: "ai-key-head" }, element("span", { textContent: info.label }), status);
    const where = element("p", { className: "hint", textContent: `Lấy khóa tại ${info.console}.${info.note ? ` ${info.note}` : ""}` });
    const input = element("input", { type: "password", autocomplete: "off", placeholder: saved ? "Dán khóa mới để thay" : info.placeholder });
    input.setAttribute("aria-label", `Khóa API ${info.name}`);
    const save = element("button", { type: "button", className: "btn primary", textContent: "Lưu" });
    const remove = element("button", { type: "button", className: "link", textContent: "Xóa khóa", hidden: !saved });
    const keyRow = element("div", { className: "post-key-actions" }, input, save, remove);

    const listId = `ai-models-${provider}`;
    const models = element("datalist", { id: listId });
    const model = element("input", { type: "text", autocomplete: "off", value: options.model ?? "", placeholder: info.model || "tên model, ví dụ qwen2.5:14b" });
    model.setAttribute("list", listId);
    model.setAttribute("aria-label", `Model ${info.name}`);
    const modelLabel = element("label", { className: "ai-field" }, `Model${info.model ? ` (mặc định ${info.model})` : ""}`, model, models);
    const fields = element("div", { className: "ai-fields" }, modelLabel);
    let base: HTMLInputElement | null = null;
    if (info.baseUrl !== undefined) {
      base = element("input", { type: "url", autocomplete: "off", value: options.baseUrl ?? "", placeholder: info.baseUrl || "https://…/v1" });
      base.setAttribute("aria-label", `Địa chỉ máy chủ ${info.name}`);
      fields.append(element("label", { className: "ai-field" }, "Địa chỉ máy chủ (Base URL)", base));
    }
    const remember = () => session.setOptions(provider, { model: model.value, baseUrl: base?.value });
    model.addEventListener("change", remember);
    base?.addEventListener("change", remember);

    const check = element("button", { type: "button", className: "btn", textContent: "Kiểm tra khóa" });
    const checkState = element("p", { className: "hint ai-check-state", textContent: "" });
    check.addEventListener("click", async () => {
      remember();
      const key = input.value.trim() || session.saved(provider) !== null ? (input.value.trim() || keyOf(provider)) : "";
      check.disabled = true;
      checkState.textContent = `Đang hỏi ${info.name}…`;
      try {
        const result = await checkProvider(provider, key, session.options(provider));
        models.replaceChildren(...result.models.slice(0, 400).map((name) => element("option", { value: name })));
        const chosen = model.value.trim() || info.model;
        if (chosen && !result.models.includes(chosen) && result.suggested) {
          model.value = result.suggested;
          remember();
          checkState.textContent = `Khóa dùng được. Model “${chosen}” không có trong tài khoản này nên đã đổi sang “${result.suggested}”.`;
        } else {
          checkState.textContent = `Khóa dùng được (${result.models.length} model). Bấm vào ô Model để chọn model khác.`;
        }
      } catch (error) {
        checkState.textContent = checkMessage(provider, error);
      } finally {
        check.disabled = false;
      }
    });

    save.addEventListener("click", async () => {
      if (!input.value.trim()) return;
      save.disabled = true;
      try {
        await setKeyFor(session, provider, input.value);
        state.textContent = `Đã lưu khóa ${info.name}. Bấm “Kiểm tra khóa” để chắc chắn khóa dùng được.`;
        render();
      } catch (error) {
        state.textContent = String(error).slice(0, 200);
        save.disabled = false;
      }
    });
    remove.addEventListener("click", async () => {
      try {
        await setKeyFor(session, provider, "");
        state.textContent = `Đã xóa khóa ${info.name} khỏi máy này.`;
        render();
      } catch (error) {
        state.textContent = String(error).slice(0, 200);
      }
    });
    return element("section", { className: "ai-key-card" }, head, where, keyRow, fields, element("div", { className: "ai-check" }, check, checkState));
  }

  /** The saved key of a provider (for "Kiểm tra khóa" without retyping it). */
  function keyOf(provider: Provider): string {
    const current = session.provider();
    session.setProvider(provider);
    const key = session.read();
    session.setProvider(current);
    return key;
  }

  function savedList(): HTMLElement | string {
    const others = PROVIDER_IDS.filter((id) => id !== session.provider() && session.saved(id) !== null);
    if (!others.length) return "";
    return element("p", { className: "hint", textContent: `Khóa khác đã lưu: ${others.map((id) => PROVIDERS[id].name).join(", ")}. Chọn ở “AI mặc định” để dùng hoặc đổi.` });
  }

  const aiToggle = $<HTMLInputElement>("#ai-translate-subs");
  const sensitiveMode = $<HTMLSelectElement>("#sensitive-mode");
  const sensitiveWords = $<HTMLTextAreaElement>("#sensitive-words");
  function renderPrefs(): void {
    aiToggle.checked = prefs.aiTranslation;
    sensitiveMode.value = prefs.sensitiveMode;
    sensitiveWords.value = formatWordList(prefs.words);
    $("#sensitive-words-box").hidden = prefs.sensitiveMode === "off";
    const provider = session.provider();
    const ready = provider === "custom" ? Boolean(session.options(provider).baseUrl) : session.saved(provider) !== null;
    $("#ai-translate-hint").textContent = ready
      ? `Video mới: ${PROVIDERS[provider].name} dịch cả đoạn hội thoại theo ngữ cảnh (xưng hô, thành ngữ, văn nói). Nếu AI lỗi, phụ đề dùng bản dịch offline như cũ. Lời thoại được gửi tới ${PROVIDERS[provider].company}.`
      : `Chưa có khóa ${PROVIDERS[provider].name}: phụ đề đang dùng bộ dịch offline (dịch từng câu). Lưu khóa ở trên để dịch theo ngữ cảnh.`;
  }
  aiToggle.addEventListener("change", () => { prefs = { ...prefs, aiTranslation: aiToggle.checked }; savePrefs(prefs); });
  sensitiveMode.addEventListener("change", () => {
    prefs = { ...prefs, sensitiveMode: sensitiveMode.value as SensitiveMode };
    savePrefs(prefs);
    renderPrefs();
  });
  sensitiveWords.addEventListener("change", () => {
    prefs = { ...prefs, words: parseWordList(sensitiveWords.value) };
    savePrefs(prefs);
    sensitiveWords.value = formatWordList(prefs.words);
  });
  $("#sensitive-reset").addEventListener("click", () => {
    prefs = { ...prefs, words: [...DEFAULT_SENSITIVE_WORDS] };
    savePrefs(prefs);
    renderPrefs();
  });

  function render(): void {
    providerSelect.value = session.provider();
    $("#ai-settings-keys").replaceChildren(card(session.provider()), savedList());
    renderPrefs();
  }

  providerSelect.addEventListener("change", () => {
    if (isProvider(providerSelect.value)) session.setProvider(providerSelect.value);
    state.textContent = "";
    render();
  });
  const close = () => { dialog.hidden = true; };
  $("#ai-settings-close").addEventListener("click", close);
  dialog.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });

  return {
    open() {
      state.textContent = "";
      render();
      dialog.hidden = false;
    },
    prefs: () => prefs,
  };
}
