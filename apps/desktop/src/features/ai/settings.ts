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
const MODELS_STORAGE = "dubflow.aiModels.v1";

/** The model names a provider listed for the saved key at the last check (not secret). */
export function modelsOf(provider: Provider): string[] {
  try {
    const value = JSON.parse(window.localStorage.getItem(MODELS_STORAGE) ?? "{}") as Record<string, unknown>;
    const list = value[provider];
    return Array.isArray(list) ? list.filter((name): name is string => typeof name === "string" && name.length <= 200).slice(0, 1000) : [];
  } catch {
    return [];
  }
}

function rememberModels(provider: Provider, names: readonly string[]): void {
  try {
    const value = JSON.parse(window.localStorage.getItem(MODELS_STORAGE) ?? "{}") as Record<string, unknown>;
    window.localStorage.setItem(MODELS_STORAGE, JSON.stringify({ ...value, [provider]: names.slice(0, 1000) }));
  } catch { /* the list is fetched again next time */ }
}

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

/**
 * The AI part of Cài đặt: the default provider, its key, model and server (tab Tài khoản),
 * subtitle translation and sensitive words (tab Dịch phụ đề). `reveal` shows Cài đặt at Tài khoản.
 */
export function mountAiSettings(session: AiSession, reveal: () => void): { open(): void; refresh(): void; prefs(): TranslationPrefs } {
  const state = $("#ai-settings-state");
  const providerSelect = $<HTMLSelectElement>("#ai-settings-provider");
  providerSelect.replaceChildren(...PROVIDER_IDS.map((id) => element("option", { value: id, textContent: PROVIDERS[id].label })));
  $("#ai-settings-note").textContent = `${KEY_STORAGE_NOTE.replace("; đổi hoặc xóa trong “Cài đặt › Tài khoản”", "")} Khóa chỉ được dùng để gọi đúng nhà cung cấp đó khi bạn dùng tính năng AI hoặc dịch phụ đề.`;
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

    // The model is picked from the provider's own list (typing a name by hand caused wrong names such as
    // "Deepseek V4 Pro"). Only a custom server that lists nothing falls back to typing.
    const model = element("select", { className: "ai-model-select" });
    model.setAttribute("aria-label", `Model ${info.name}`);
    const manual = element("input", { type: "text", autocomplete: "off", value: options.model ?? "", placeholder: "tên model, ví dụ qwen2.5:14b", hidden: true });
    manual.setAttribute("aria-label", `Tên model ${info.name}`);
    const modelHint = element("span", { className: "hint", textContent: "" });
    const modelLabel = element("label", { className: "ai-field" }, "Model", model, manual, modelHint);
    const fields = element("div", { className: "ai-fields" }, modelLabel);
    let base: HTMLInputElement | null = null;
    if (info.baseUrl !== undefined) {
      base = element("input", { type: "url", autocomplete: "off", value: options.baseUrl ?? "", placeholder: info.baseUrl || "https://…/v1" });
      base.setAttribute("aria-label", `Địa chỉ máy chủ ${info.name}`);
      fields.append(element("label", { className: "ai-field" }, "Địa chỉ máy chủ (Base URL)", base));
    }
    const chosenModel = () => (manual.hidden ? model.value : manual.value).trim();
    const fillModels = (names: readonly string[], chosen = chosenModel() || options.model?.trim() || "") => {
      const list = [...names];
      if (chosen && !list.includes(chosen)) list.unshift(chosen);
      model.replaceChildren(
        element("option", { value: "", textContent: info.model ? `Mặc định (${info.model})` : "Chọn model" }),
        ...list.map((name) => element("option", { value: name, textContent: names.includes(name) ? name : `${name} (không có trong danh sách)` })),
      );
      model.value = chosen;
      const typing = provider === "custom" && !names.length;
      manual.hidden = !typing;
      model.hidden = typing;
      if (typing) manual.value = chosen;
      modelHint.textContent = names.length ? `${names.length} model của tài khoản này.`
        : typing ? "Máy chủ chưa trả danh sách model: nhập tên model." : "Bấm “Kiểm tra khóa” để tải danh sách model.";
    };
    fillModels(modelsOf(provider));
    const remember = () => {
      session.setOptions(provider, { model: chosenModel(), baseUrl: base?.value });
      renderPrefs();
    };
    model.addEventListener("change", remember);
    manual.addEventListener("change", remember);
    base?.addEventListener("change", remember);

    const check = element("button", { type: "button", className: "btn", textContent: "Kiểm tra khóa" });
    const checkState = element("p", { className: "hint ai-check-state", textContent: "" });
    const keyFor = () => input.value.trim() || (session.saved(provider) !== null ? keyOf(provider) : "");
    /** Loads the key's model list; ``quiet`` (on opening) leaves the message line alone. */
    const loadModels = async (quiet: boolean) => {
      const result = await checkProvider(provider, keyFor(), session.options(provider));
      rememberModels(provider, result.models);
      const chosen = chosenModel() || info.model;
      fillModels(result.models);
      if (chosen && result.models.length && !result.models.includes(chosen) && result.suggested) {
        fillModels(result.models, result.suggested);
        remember();
        checkState.textContent = `Model “${chosen}” không có trong tài khoản này nên đã đổi sang “${result.suggested}”.`;
      } else if (!quiet) {
        checkState.textContent = `Khóa dùng được (${result.models.length} model). Chọn model trong danh sách.`;
      }
    };
    check.addEventListener("click", async () => {
      remember();
      check.disabled = true;
      checkState.textContent = `Đang hỏi ${info.name}…`;
      try {
        await loadModels(false);
      } catch (error) {
        checkState.textContent = checkMessage(provider, error);
      } finally {
        check.disabled = false;
      }
    });
    if (saved !== null || (provider === "custom" && options.baseUrl)) {
      // Fresh list on opening, so a new model the provider added shows up without a click.
      loadModels(true).catch(() => { /* the cached list stays; "Kiểm tra khóa" shows the reason */ });
    }

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
    const ready = provider === "custom" ? Boolean(session.options(provider).baseUrl && session.options(provider).model) : session.saved(provider) !== null;
    $("#ai-translate-hint").textContent = ready
      ? `Video mới: ${PROVIDERS[provider].name} dịch cả đoạn hội thoại theo ngữ cảnh (xưng hô, thành ngữ, văn nói). Nếu AI lỗi, phụ đề dùng bản dịch offline như cũ. Lời thoại được gửi tới ${PROVIDERS[provider].company}.`
      : provider === "custom"
        ? "Chưa nhập địa chỉ máy chủ và model: phụ đề đang dùng bộ dịch offline (dịch từng câu)."
        : `Chưa có khóa ${PROVIDERS[provider].name}: phụ đề đang dùng bộ dịch offline (dịch từng câu). Lưu khóa ở mục Tài khoản để dịch theo ngữ cảnh.`;
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
  render();

  return {
    open() {
      state.textContent = "";
      render();
      reveal();
    },
    refresh: render,
    prefs: () => prefs,
  };
}
