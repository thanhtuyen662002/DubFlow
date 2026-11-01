/** Douyin inside DubFlow: the "Đăng nhập Douyin" dialog that shows douyin.com
 * over its box, the automatic session for Douyin links, and the account row
 * in Settings. */
type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing ${selector}`);
  return node;
};

export type DouyinLogin = {
  /** Show the dialog; resolves when it closes, true when a session was saved. */
  open(): Promise<boolean>;
  /** Make sure Douyin links have a session; `force` refreshes it now. */
  ensureSession(force: boolean): Promise<void>;
  refreshAccount(): Promise<void>;
};

/** `onWindowMoved` reports moves of the app window (the page box moves with it). */
export function mountDouyinLogin(invoke: Invoke, onWindowMoved: (listener: () => void) => void): DouyinLogin {
  const dialog = $("#douyin-dialog");
  const box = $("#douyin-view");
  const state = $("#douyin-state");
  const done = $<HTMLButtonElement>("#douyin-done");
  const close = $<HTMLButtonElement>("#douyin-close");
  const accountState = $("#douyin-account-state");
  const login = $<HTMLButtonElement>("#douyin-login");
  const logout = $<HTMLButtonElement>("#douyin-logout");
  let resolveOpen: ((saved: boolean) => void) | null = null;
  let opened: Promise<boolean> | null = null;
  let returnFocus: HTMLElement | null = null;
  let ensuring: Promise<void> | null = null;
  let ensuringForced = false;

  function bounds(): Record<string, number> {
    const rect = box.getBoundingClientRect();
    return { x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) };
  }
  function syncBounds(): void {
    if (dialog.hidden) return;
    invoke("douyin_view_bounds", bounds()).catch(() => undefined);
  }
  const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(syncBounds);
  window.addEventListener("resize", syncBounds);
  onWindowMoved(syncBounds);

  function finish(saved: boolean): void {
    if (dialog.hidden) return;
    dialog.hidden = true;
    observer?.disconnect();
    invoke("douyin_view_close").catch(() => undefined);
    if (returnFocus?.isConnected) returnFocus.focus();
    resolveOpen?.(saved);
    resolveOpen = null;
    opened = null;
    void refreshAccount();
  }

  async function open(): Promise<boolean> {
    if (opened) return opened;
    returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.hidden = false;
    state.textContent = "";
    done.disabled = false;
    done.textContent = "Xong";
    done.focus();
    const result = new Promise<boolean>((resolve) => { resolveOpen = resolve; });
    opened = result;
    // Wait for layout so the box has its final size.
    await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
    observer?.observe(box);
    try {
      await invoke("douyin_view_open", bounds());
    } catch (error) {
      state.textContent = `Không mở được trang Douyin: ${String(error).slice(0, 160)}`;
    }
    return result;
  }

  done.addEventListener("click", async () => {
    done.disabled = true;
    done.textContent = "Đang lưu…";
    state.textContent = "";
    try {
      await invoke<number>("save_douyin_session");
      finish(true);
    } catch (error) {
      state.textContent = String(error).slice(0, 200);
      done.disabled = false;
      done.textContent = "Xong";
    }
  });
  close.addEventListener("click", () => finish(false));
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      finish(false);
    }
  });

  function ensureSession(force: boolean): Promise<void> {
    // One refresh at a time; a forced one never reuses a plain check.
    if (ensuring && (ensuringForced || !force)) return ensuring;
    const run = (ensuring ?? Promise.resolve()).catch(() => undefined)
      .then(() => invoke<string>("ensure_douyin_session", { force }))
      .then(() => undefined);
    ensuring = run;
    ensuringForced = force;
    void run.finally(() => {
      if (ensuring === run) ensuring = null;
    }).catch(() => undefined);
    return run;
  }

  async function refreshAccount(): Promise<void> {
    try {
      const account = await invoke<{ logged_in?: boolean } | null>("douyin_account");
      const loggedIn = account?.logged_in === true;
      accountState.textContent = loggedIn ? "Đã đăng nhập" : "Chưa đăng nhập";
      login.hidden = loggedIn;
      logout.hidden = !loggedIn;
    } catch {
      accountState.textContent = "Chưa kiểm tra được";
      login.hidden = false;
      logout.hidden = true;
    }
  }

  login.addEventListener("click", () => void open());
  logout.addEventListener("click", async () => {
    logout.disabled = true;
    try {
      await invoke("douyin_logout");
      // Douyin links keep working with a fresh visitor session.
      void ensureSession(true).catch(() => undefined);
    } catch (error) {
      accountState.textContent = `Không đăng xuất được: ${String(error).slice(0, 120)}`;
    } finally {
      logout.disabled = false;
      await refreshAccount();
    }
  });

  return { open, ensureSession, refreshAccount };
}
