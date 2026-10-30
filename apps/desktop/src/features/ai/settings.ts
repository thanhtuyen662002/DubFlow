import { isProvider, KEY_STORAGE_NOTE, PROVIDERS, setKeyFor, type AiSession, type KeyStore, type Provider } from "./provider.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

/** The choice of provider is not a secret; only it lives in WebView storage. */
const PROVIDER_STORAGE = "dubflow.aiProvider.v1";

/** Keys in the host's Windows Credential Manager (`ai_key_*`), the provider choice in local storage. */
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
  };
}

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing ${selector}`);
  return node;
};

/** "Cài đặt AI": the default provider and each provider's saved key. */
export function mountAiSettings(session: AiSession): { open(): void } {
  const dialog = $("#ai-settings-dialog");
  const state = $("#ai-settings-state");
  const providerSelect = $<HTMLSelectElement>("#ai-settings-provider");
  $("#ai-settings-note").textContent = `${KEY_STORAGE_NOTE.replace("; đổi hoặc xóa trong “Cài đặt AI”", "")} Khóa chỉ được dùng để gọi đúng nhà cung cấp đó khi bạn bấm nút AI.`;

  function card(provider: Provider): HTMLElement {
    const { label, console, placeholder } = PROVIDERS[provider];
    const box = document.createElement("section");
    box.className = "ai-key-card";
    const head = document.createElement("div");
    head.className = "ai-key-head";
    const title = document.createElement("span");
    title.textContent = label;
    const status = document.createElement("span");
    const saved = session.saved(provider);
    status.className = saved ? "ai-key-status saved" : "ai-key-status";
    status.textContent = saved ? `Đã lưu (${saved})` : "Chưa có khóa";
    head.append(title, status);
    const where = document.createElement("p");
    where.className = "hint";
    where.textContent = `Lấy khóa tại ${console}.`;
    const actions = document.createElement("div");
    actions.className = "post-key-actions";
    const input = document.createElement("input");
    input.type = "password";
    input.autocomplete = "off";
    input.placeholder = saved ? "Dán khóa mới để thay" : placeholder;
    input.setAttribute("aria-label", `Khóa API ${PROVIDERS[provider].name}`);
    const save = document.createElement("button");
    save.type = "button";
    save.className = "btn primary";
    save.textContent = "Lưu";
    save.addEventListener("click", async () => {
      if (!input.value.trim()) return;
      save.disabled = true;
      try {
        await setKeyFor(session, provider, input.value);
        state.textContent = `Đã lưu khóa ${PROVIDERS[provider].name}.`;
        render();
      } catch (error) {
        state.textContent = String(error).slice(0, 200);
        save.disabled = false;
      }
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "link";
    remove.textContent = "Xóa khóa";
    remove.hidden = !saved;
    remove.addEventListener("click", async () => {
      try {
        await setKeyFor(session, provider, "");
        state.textContent = `Đã xóa khóa ${PROVIDERS[provider].name} khỏi máy này.`;
        render();
      } catch (error) {
        state.textContent = String(error).slice(0, 200);
      }
    });
    actions.append(input, save, remove);
    box.append(head, where, actions);
    return box;
  }

  function render(): void {
    providerSelect.value = session.provider();
    $("#ai-settings-keys").replaceChildren(card("claude"), card("gemini"));
  }

  providerSelect.addEventListener("change", () => {
    if (isProvider(providerSelect.value)) session.setProvider(providerSelect.value);
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
  };
}
