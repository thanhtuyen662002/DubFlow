/**
 * Closing with an editing draft that could not be saved (Issue #929).
 *
 * On the PC a failed draft save was never tried again, and closing right after (the × button, or leaving the editor)
 * dropped the change without a word. Every close now waits for the draft to reach the disk first. When it cannot
 * (a write failed, or is still running after `CLOSE_WAIT_MS`), the user is asked: "Ở lại" keeps the window or the
 * editor open while the save is tried again; only "Vẫn đóng" / "Vẫn thoát" lets the change go.
 */
import type { ConfirmOptions } from "../../shell/confirm.ts";
import type { Timers } from "./autosave.ts";

/** How long a close waits for the draft to reach the disk before asking. */
export const CLOSE_WAIT_MS = 10_000;

/** `flush`, where a write that fails, throws or is still running after `ms` counts as not saved. */
export function flushWithin(flush: () => Promise<boolean>, ms: number, timers: Timers): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false;
    const end = (saved: boolean): void => {
      if (settled) return;
      settled = true;
      timers.clear(handle);
      resolve(saved);
    };
    const handle = timers.set(() => end(false), ms);
    let running: Promise<boolean>;
    try {
      running = flush();
    } catch {
      running = Promise.resolve(false);
    }
    running.then((saved) => end(saved === true), () => end(false));
  });
}

/** The question when the draft could not be saved: closing the window, or leaving the editor. */
export function unsavedPrompt(action: "close" | "exit", unapplied = false): ConfirmOptions {
  const leaving = action === "close" ? "Đóng DubFlow" : "Thoát chỉnh sửa";
  return {
    title: "Chưa lưu được bản nháp",
    message: `Thay đổi mới nhất trong Chỉnh sửa chưa lưu được vào ổ đĩa (tệp nháp có thể đang bị chương trình khác giữ, `
      + `hoặc ổ đĩa lỗi). ${leaving} bây giờ sẽ mất thay đổi đó${unapplied ? " và các thay đổi chưa bấm Áp dụng" : ""}. `
      + "Chọn Ở lại để DubFlow tự thử lưu lại.",
    confirmLabel: action === "close" ? "Vẫn đóng" : "Vẫn thoát",
    cancelLabel: "Ở lại",
    danger: true,
  };
}

export type CloseGuard = {
  /** Something to wait for first: a close of the editor already asking (one question at a time). */
  before?: () => Promise<unknown> | null;
  /** Write the pending draft; true when the disk has every change. */
  flush(): Promise<boolean>;
  /** Ask whether to close anyway; true only when the user chose to lose the change. */
  ask(): Promise<boolean>;
  timers: Timers;
  /** How long to wait for the draft before asking (CLOSE_WAIT_MS). */
  waitMs?: number;
};

/**
 * Whether the window may close now: the draft is on disk, or the user chose to lose what is not. One answer at a
 * time: a second close while the first one waits or asks gets the same answer (no second question).
 */
export function closeGuard(guard: CloseGuard): () => Promise<boolean> {
  let running: Promise<boolean> | null = null;
  return () => {
    if (running) return running;
    const current = (async () => {
      const first = guard.before?.();
      if (first) await first.catch(() => undefined);  // whatever it answered, the draft is checked below
      if (await flushWithin(guard.flush, guard.waitMs ?? CLOSE_WAIT_MS, guard.timers)) return true;
      try {
        return (await guard.ask()) === true;
      } catch {
        return false;
      }
    })();
    running = current;
    void current.finally(() => { if (running === current) running = null; });
    return current;
  };
}

/** The bits of a Tauri window the close wiring uses. */
export type ClosableWindow = {
  /** Asks to close. While a close request listener is in place the system holds the close and the listener runs. */
  close(): Promise<void>;
  /** Listens to every close request (Alt+F4, the taskbar, `close`); resolves to the function that stops listening. */
  onCloseRequested(handler: (event: { preventDefault(): void }) => Promise<void>): Promise<() => unknown>;
};

/** The system's close request, as Tauri names it. */
export const CLOSE_EVENT = "tauri://close-requested";
/** Where the page keeps the ids of its close request listeners (sessionStorage: it outlives a reload, not the app). */
export const CLOSE_LISTENERS_KEY = "dubflow.closeListeners.v1";

/** Tauri's event bridge, by listener id (its own `listen` hides the id). */
export type EventBridge = {
  /** Listens to `event` on the window `label`; resolves to the listener's id. */
  listen(event: string, label: string, handler: () => void): Promise<number>;
  unlisten(event: string, id: number): Promise<void>;
};

/** A small store that outlives a reload of the page; it may refuse (then nothing is remembered). */
export type IdStore = { get(): string | null; set(value: string | null): void };

function storedIds(store: IdStore): number[] {
  try {
    const value: unknown = JSON.parse(store.get() ?? "[]");
    return Array.isArray(value) ? value.filter((id): id is number => Number.isSafeInteger(id) && id >= 0) : [];
  } catch {
    return [];
  }
}

function storeIds(store: IdStore, ids: number[]): void {
  try {
    store.set(ids.length ? JSON.stringify(ids) : null);
  } catch {
    // a store that refuses: a reload then leaves the listener behind, as it did before
  }
}

/**
 * `ClosableWindow.onCloseRequested` that a reload cannot break (Issue #929). Tauri keeps a page's listeners after
 * the page reloads (F5, Ctrl+R, Tải lại), and while one is there it holds every close of the window for it, though
 * nothing runs it any more: the window could no longer be closed. So the ids are kept where a reload finds them, and
 * every listener left behind (a page before a reload, or one this page could not remove) is removed before a new one
 * starts. A removal that fails is tried again the next time.
 */
export function closeRequests(label: string, bridge: EventBridge, store: IdStore): ClosableWindow["onCloseRequested"] {
  return async (handler) => {
    for (const id of storedIds(store)) {
      try {
        await bridge.unlisten(CLOSE_EVENT, id);
        storeIds(store, storedIds(store).filter((kept) => kept !== id));
      } catch {
        // kept: tried again before the next listener
      }
    }
    const id = await bridge.listen(CLOSE_EVENT, label, () => {
      void handler({ preventDefault: () => undefined }).catch(() => undefined);
    });
    storeIds(store, [...storedIds(store).filter((kept) => kept !== id), id]);
    return async () => {
      await bridge.unlisten(CLOSE_EVENT, id);
      storeIds(store, storedIds(store).filter((kept) => kept !== id));
    };
  };
}

/**
 * The × button and the system's close (Alt+F4, the taskbar) both go through `mayClose`; the window stays open when it
 * answers false. A close request is always held: when `mayClose` lets it through, the listener is removed first and
 * the window closed again, so the system closes it normally (Tauri's own wrapper would destroy the window instead,
 * which needs a permission the app does not have). A close asked for while the listener is still starting waits for
 * it. Resolves true once the guard is in place, false when it could not start: the editor opens only once it is in
 * place (a system close the guard cannot see would lose a change without asking).
 */
export function wireWindowClose(window: ClosableWindow, button: { addEventListener(type: "click", listener: () => void): void },
  mayClose: () => Promise<boolean>): Promise<boolean> {
  let stop: (() => unknown) | null = null;
  let arming = false;
  let wanted = false;
  const release = async (): Promise<void> => {
    if (arming) {
      wanted = true;
      return;
    }
    const unlisten = stop;
    stop = null;
    try {
      if (unlisten) await unlisten();
      await window.close();
    } catch {
      // still open: guarded again
      void arm();
    }
  };
  const request = async (): Promise<void> => {
    if (await mayClose()) await release();
  };
  const arm = (): Promise<boolean> => {
    arming = true;
    return window.onCloseRequested(async (event) => {
      event.preventDefault();
      await request();
    }).then((unlisten) => {
      stop = unlisten;
      return true;
    }, () => false).then((armed) => {
      arming = false;
      if (wanted) {
        wanted = false;
        void release();
      }
      return armed;
    });
  };
  button.addEventListener("click", () => { void request().catch(() => undefined); });
  return arm();
}

/** Where a question shows: whether one is open now, and the "stay" button of the newest one. */
export type QuestionPlace = { open(): boolean; focusStay(): void };

/** confirmDialog's panels in the page (shell/confirm.ts puts the cancel button first). */
export const pageQuestions: QuestionPlace = {
  open: () => typeof document !== "undefined" && document.querySelector(".confirm-backdrop") !== null,
  focusStay: () => {
    const panels = typeof document !== "undefined" ? document.querySelectorAll<HTMLElement>(".confirm-panel") : [];
    panels[panels.length - 1]?.querySelector<HTMLButtonElement>(".confirm-actions button")?.focus();
  },
};

/**
 * The unsaved-draft question, only when no other question is open: two at once would both take the keys, so Enter
 * on one could answer the other. With another open the answer is "stay" (false), the safe one; the user closes again
 * after answering it. The question starts on "Ở lại", so Enter right after Alt+F4 keeps the change.
 */
export function askAlone(ask: () => Promise<boolean>, place: QuestionPlace = pageQuestions): Promise<boolean> {
  if (place.open()) return Promise.resolve(false);
  const answer = ask();
  place.focusStay();
  return answer;
}
