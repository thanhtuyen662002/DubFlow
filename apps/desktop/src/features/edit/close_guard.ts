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
  /** Asks to close: the window's close request handlers run first. */
  close(): Promise<void>;
  /** Listens to every close request (the × button through `close`, Alt+F4, the taskbar). */
  onCloseRequested(handler: (event: { preventDefault(): void }) => Promise<void>): Promise<unknown>;
};

/**
 * The × button and the system's close (Alt+F4, the taskbar) both go through `mayClose`; the window stays open when it
 * answers false. Should the close request listener not start, the × button still asks first.
 */
export function wireWindowClose(window: ClosableWindow, button: { addEventListener(type: "click", listener: () => void): void },
  mayClose: () => Promise<boolean>): Promise<void> {
  let listening = false;
  button.addEventListener("click", () => {
    if (listening) void window.close().catch(() => undefined);
    else void mayClose().then((ok) => (ok ? window.close() : undefined)).catch(() => undefined);
  });
  return window.onCloseRequested(async (event) => {
    if (!(await mayClose())) event.preventDefault();
  }).then(() => { listening = true; }, () => undefined);
}
