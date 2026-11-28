/** Bilibili inside DubFlow (#976): the "Đăng nhập Bilibili" dialog that shows
 * passport.bilibili.com's QR login over its box, the session Bilibili links
 * download with (1080p logged in, 4K for 大会员 instead of 480p), and the
 * account row in Settings. */
type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing ${selector}`);
  return node;
};

export type BilibiliLogin = {
  /** Show the dialog; resolves when it closes, true when a session was saved. */
  open(): Promise<boolean>;
  /** Before a Bilibili link starts: save the browser's login for it (or drop one that ended). */
  ensureSession(): Promise<"logged_in" | "anonymous">;
  refreshAccount(): Promise<void>;
};

export const BILIBILI_LOGGED_IN = "Đã đăng nhập: link Bilibili tải 1080p (tài khoản 大会员: 4K và 1080P+).";
export const BILIBILI_ANONYMOUS = "Chưa đăng nhập: Bilibili chỉ cho tải tối đa 480p.";

/** `onWindowMoved` reports moves of the app window (the page box moves with it). */
export function mountBilibiliLogin(invoke: Invoke, onWindowMoved: (listener: () => void) => void): BilibiliLogin {
  const dialog = $("#bilibili-dialog");
  const box = $("#bilibili-view");
  const state = $("#bilibili-state");
  const done = $<HTMLButtonElement>("#bilibili-done");
  const close = $<HTMLButtonElement>("#bilibili-close");
  const accountState = $("#bilibili-account-state");
  const login = $<HTMLButtonElement>("#bilibili-login");
  const logout = $<HTMLButtonElement>("#bilibili-logout");
  let resolveOpen: ((saved: boolean) => void) | null = null;
  let opened: Promise<boolean> | null = null;
  let returnFocus: HTMLElement | null = null;
  let ensuring: Promise<"logged_in" | "anonymous"> | null = null;

  function bounds(): Record<string, number> {
    const rect = box.getBoundingClientRect();
    return { x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) };
  }
  function syncBounds(): void {
    if (dialog.hidden) return;
    invoke("bilibili_view_bounds", bounds()).catch(() => undefined);
  }
  const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(syncBounds);
  window.addEventListener("resize", syncBounds);
  onWindowMoved(syncBounds);

  function finish(saved: boolean): void {
    if (dialog.hidden) return;
    dialog.hidden = true;
    observer?.disconnect();
    const closing = invoke("bilibili_view_close").catch(() => undefined);
    if (returnFocus?.isConnected) returnFocus.focus();
    resolveOpen?.(saved);
    resolveOpen = null;
    opened = null;
    void closing.then(() => refreshAccount());
  }

  async function open(): Promise<boolean> {
    if (opened) return opened;
    returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.hidden = false;
    state.textContent = "Mở app Bilibili trên điện thoại, quét mã QR trong khung rồi bấm Xong.";
    done.disabled = false;
    done.textContent = "Xong";
    done.focus();
    const result = new Promise<boolean>((resolve) => { resolveOpen = resolve; });
    opened = result;
    // Wait for layout so the box has its final size.
    await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
    observer?.observe(box);
    try {
      await invoke("bilibili_view_open", bounds());
    } catch (error) {
      state.textContent = `Không mở được trang Bilibili: ${String(error).slice(0, 160)}`;
    }
    return result;
  }

  done.addEventListener("click", async () => {
    done.disabled = true;
    done.textContent = "Đang lưu…";
    state.textContent = "";
    // The first save after starting DubFlow waits for the link service to check the install.
    const slow = setTimeout(() => {
      state.textContent = "Lần lưu đầu tiên sau khi mở DubFlow có thể mất khoảng 1–2 phút vì dịch vụ tải đang kiểm tra bản cài đặt…";
    }, 8000);
    try {
      await invoke<number>("save_bilibili_session");
      finish(true);
    } catch (error) {
      state.textContent = String(error).slice(0, 200);
      done.disabled = false;
      done.textContent = "Xong";
    } finally {
      clearTimeout(slow);
    }
  });
  close.addEventListener("click", () => finish(false));
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      finish(false);
    }
  });

  function ensureSession(): Promise<"logged_in" | "anonymous"> {
    // One check at a time; links started meanwhile wait for the same one.
    ensuring ??= invoke<string>("ensure_bilibili_session")
      .then((value) => (value === "logged_in" ? "logged_in" : "anonymous") as "logged_in" | "anonymous")
      .finally(() => { ensuring = null; });
    return ensuring;
  }

  let refreshing: Promise<void> | null = null;
  function refreshAccount(): Promise<void> {
    refreshing ??= checkAccount().finally(() => { refreshing = null; });
    return refreshing;
  }
  async function checkAccount(): Promise<void> {
    let failure = "";
    // Reading cookies can fail for a moment while the login page closes.
    for (const wait of [0, 700, 1500]) {
      if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
      try {
        const account = await invoke<{ logged_in?: boolean } | null>("bilibili_account");
        const loggedIn = account?.logged_in === true;
        accountState.textContent = loggedIn ? BILIBILI_LOGGED_IN : BILIBILI_ANONYMOUS;
        accountState.title = "";
        login.hidden = loggedIn;
        logout.hidden = !loggedIn;
        return;
      } catch (error) {
        failure = String(error).slice(0, 200);
      }
    }
    accountState.textContent = "Chưa kiểm tra được";
    accountState.title = failure;
    login.hidden = false;
    logout.hidden = true;
  }

  login.addEventListener("click", () => void open());
  logout.addEventListener("click", async () => {
    logout.disabled = true;
    try {
      await invoke("bilibili_logout");
    } catch (error) {
      accountState.textContent = `Không đăng xuất được: ${String(error).slice(0, 120)}`;
      logout.disabled = false;
      return;
    }
    logout.disabled = false;
    await refreshAccount();
  });

  return { open, ensureSession, refreshAccount };
}
