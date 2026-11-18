/**
 * Closing with an editing draft that could not be saved (Issue #929).
 *
 * On the PC a failed draft save was never tried again, and closing right after (the × button, or leaving the editor)
 * dropped the change without a word. Every close now waits for the draft to reach the disk first. When it cannot
 * (a write failed, or is still running after `CLOSE_WAIT_MS`), the user is asked: "Ở lại" keeps the window or the
 * editor open while the save is tried again; only "Vẫn đóng" / "Vẫn thoát" lets the change go.
 */
import type { ConfirmOptions } from "../../shell/confirm.ts";
import { retryDelay, type Timers } from "./autosave.ts";

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

/** At most this many video names in the question; the rest are counted. */
const NAMED_VIDEOS = 5;

/**
 * The question when a draft could not be saved: closing the window (every video whose draft change is not on disk,
 * `videos`, would lose it), or leaving the editor (only its own video's).
 */
export function unsavedPrompt(action: "close" | "exit", unapplied = false, videos: readonly string[] = []): ConfirmOptions {
  const leaving = action === "close" ? "Đóng DubFlow" : "Thoát chỉnh sửa";
  const names = videos.slice(0, NAMED_VIDEOS).map((name) => `“${name.length > 60 ? `${name.slice(0, 59)}…` : name}”`);
  if (videos.length > NAMED_VIDEOS) names.push(`${videos.length - NAMED_VIDEOS} video khác`);
  const what = videos.length > 1
    ? `Thay đổi trong Chỉnh sửa của ${videos.length} video chưa lưu được vào ổ đĩa (${names.join(", ")})`
    : videos.length === 1 ? `Thay đổi trong Chỉnh sửa của video ${names[0]} chưa lưu được vào ổ đĩa`
    : "Thay đổi mới nhất trong Chỉnh sửa chưa lưu được vào ổ đĩa";
  return {
    title: "Chưa lưu được bản nháp",
    message: `${what} (tệp nháp có thể đang bị chương trình khác giữ, hoặc ổ đĩa lỗi). ${leaving} bây giờ sẽ mất `
      + `${videos.length > 1 ? "tất cả các thay đổi đó" : "thay đổi đó"}${unapplied ? " và các thay đổi chưa bấm Áp dụng" : ""}. `
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
export type IdStore = { get(): string | null; set(value: string): void };

/** The bits of Web Storage the store uses. */
export type KeyStorage = { getItem(key: string): string | null; setItem(key: string, value: string): void };

/** An IdStore in Web Storage under `key` (sessionStorage: it outlives a reload, not the app); read only when used. */
export function storageIds(storage: () => KeyStorage, key = CLOSE_LISTENERS_KEY): IdStore {
  return {
    get: () => storage().getItem(key),
    set: (value) => storage().setItem(key, value),
  };
}

/**
 * What the store keeps: the ids of close listeners still to remove, and `from`: no close listener below that id is
 * left but those in `ids`. A page moves `from` past its own listener only once it has swept every id below it; a page
 * that reloads mid-listen or mid-sweep, or whose writes the store did not take, leaves it lower, never higher.
 */
type Listeners = { ids: number[]; from: number };

const isId = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;

/** What the store holds, or null when it is not what closeRequests writes. */
function parseListeners(raw: string): Listeners | null {
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const { ids, from } = value as Record<string, unknown>;
    if (!Array.isArray(ids) || !ids.every(isId) || !isId(from)) return null;
    return { ids, from };
  } catch {
    return null;
  }
}

/**
 * The store as closeRequests uses it: what it holds, else nothing known (every id from 0 swept). `failed`: a read or
 * a write threw.
 */
function trackedStore(store: IdStore) {
  const state = { failed: false };
  return {
    state,
    read: (): Listeners => {
      try {
        const raw = store.get();
        return (raw === null ? null : parseListeners(raw)) ?? { ids: [], from: 0 };
      } catch {
        state.failed = true;
        return { ids: [], from: 0 };
      }
    },
    write: (listeners: Listeners): void => {
      try {
        store.set(JSON.stringify(listeners));
      } catch {
        state.failed = true;
      }
    },
  };
}

/** At most this many ids are tried before the listener is ready to answer; the rest are tried by the close (`stop`). */
export const SWEEP_LIMIT = 4096;

/**
 * A close request listener is in place, but one left behind (by a page before a reload, or one this page could not
 * remove) is, or may be, still there: it would hold every close of the window for a handler that no longer runs, so
 * the guard is not ready (the editor stays closed). `stop` removes those first, then this page's own; it throws while
 * one remains.
 */
export class StaleCloseListeners extends Error {
  readonly stop: () => Promise<void>;

  constructor(stop: () => Promise<void>) {
    super("a close request listener left behind could not be removed");
    this.stop = stop;
  }
}

/**
 * `ClosableWindow.onCloseRequested` that a reload cannot break (Issue #929). Tauri keeps a page's listeners after
 * the page reloads (F5, Ctrl+R, Tải lại), and while one is there it holds every close of the window for it, though
 * nothing runs it any more: the window could no longer be closed. So every listener left behind is removed before
 * this page's own can let a close through: the ids the store names, and every close listener from the store's `from`
 * up to this page's own (Tauri numbers listeners in order, removes by event and id, and nothing else listens to close
 * requests). A store that cannot be read, holds something else or never took a write gives `from` 0: the sweep does
 * not depend on the page before having written anything. At most `sweepLimit` ids are swept before the answer; the
 * ids below those are swept by `stop`, before this page's own goes.
 *
 * Ready only when no listener can be left: none failed to go, every id below this page's own was swept, and the store
 * took every write (fail closed: a store that refuses is not trusted). Otherwise the new listener still hears every
 * close, but the answer is `StaleCloseListeners`: the editor stays closed, and its `stop` (and the next listener)
 * tries the removal again.
 */
export function closeRequests(label: string, bridge: EventBridge, store: IdStore, sweepLimit = SWEEP_LIMIT): ClosableWindow["onCloseRequested"] {
  return async (handler) => {
    const ids = trackedStore(store);
    const before = ids.read();
    /** Left behind and still in place: removed before this page's own goes. */
    const left = new Set<number>();
    for (const id of before.ids) {
      try {
        await bridge.unlisten(CLOSE_EVENT, id);
      } catch {
        left.add(id);  // kept: tried again by stop and before the next listener
      }
    }
    const from = before.from;
    // a reload before the id comes back sweeps from the same place
    ids.write({ ids: [...left], from });
    const id = await bridge.listen(CLOSE_EVENT, label, () => {
      void handler({ preventDefault: () => undefined }).catch(() => undefined);
    });
    let own = true;
    /** Where the ids not swept yet start (null once every id below this page's own was tried). */
    let unswept: number | null = from;
    const record = (): void => ids.write({ ids: [...left, ...(own ? [id] : [])], from: unswept ?? id + 1 });
    record();
    const sweep = async (start: number, end: number): Promise<void> => {
      for (let lost = start; lost < end; lost += 1) {
        try {
          await bridge.unlisten(CLOSE_EVENT, lost);
        } catch {
          left.add(lost);
        }
      }
    };
    const swept = Math.max(from, id - sweepLimit);
    await sweep(swept, id);
    if (swept === from) unswept = null;
    record();
    const stop = async (): Promise<void> => {
      // every one left behind first: while one is there it holds the close; one that still fails keeps this page's
      // own in place too, so the close stays guarded
      if (unswept !== null) {
        await sweep(unswept, swept);
        unswept = null;
        record();
      }
      for (const stale of [...left]) {
        await bridge.unlisten(CLOSE_EVENT, stale);
        left.delete(stale);
        record();
      }
      await bridge.unlisten(CLOSE_EVENT, id);
      own = false;
      record();
    };
    if (left.size || unswept !== null || ids.state.failed) throw new StaleCloseListeners(stop);
    return stop;
  };
}

/** The window close guard as wired. */
export type CloseWiring = {
  /** True while the guard is in place and no leftover listener can hold a close (waits for one being put in place). */
  ready(): Promise<boolean>;
  /** The same, now, without waiting (false while the listener is being put in place). */
  now(): boolean;
};

export type WireOptions = {
  /**
   * Told when a close that failed could not put the guard back: no listener is in place, so a system close (Alt+F4,
   * the taskbar) is not held any more, and nothing in the page can hold it.
   */
  unguarded?: () => void;
  /** With timers, a listener that could not be put in place is tried again: 1 s later, then twice as long, up to 30 s. */
  timers?: Timers;
};

/**
 * The × button and the system's close (Alt+F4, the taskbar) both go through `mayClose`; the window stays open when it
 * answers false. A close request is always held: when `mayClose` lets it through, the listener is removed first and
 * the window closed again, so the system closes it normally (Tauri's own wrapper would destroy the window instead,
 * which needs a permission the app does not have). Two closes at once (× and Alt+F4) share one answer and one
 * release: the listener is removed once and the window closed once. A close asked for while the listener is still
 * starting waits for it. `ready()` and `now()` say whether the guard is in place now, without a leftover listener:
 * false while it could not start (also after a close that failed and could not put it back), or a listener left
 * behind could not be removed; the editor opens only when it is ready (a system close the guard cannot see, or one a
 * leftover listener holds, would lose a change without asking or keep the window open for good).
 */
export function wireWindowClose(window: ClosableWindow, button: { addEventListener(type: "click", listener: () => void): void },
  mayClose: () => Promise<boolean>, options: WireOptions = {}): CloseWiring {
  /** Removes the listener (and any left behind, first); null while none is in place. */
  let stop: (() => unknown) | null = null;
  /** The listener being put in place: a close waits for it. */
  let arming: Promise<boolean> | null = null;
  /** The close under way: another close shares it. */
  let releasing: Promise<void> | null = null;
  /** The guard is in place with no leftover listener (not while a close removes it). */
  let armed = false;
  /** The next try to put the listener in place, and how many failed in a row. */
  let retry = 0;
  let failures = 0;
  const release = (): Promise<void> => {
    if (releasing) return releasing;
    const current = (async () => {
      if (arming) await arming;
      if (retry) options.timers?.clear(retry);
      retry = 0;
      const unlisten = stop;
      const wasArmed = armed;
      stop = null;
      armed = false;
      let removed = false;
      try {
        if (unlisten) await unlisten();
        removed = true;
        await window.close();
      } catch {
        // still open, and guarded again: by the listener still in place (a leftover one could not go: tried again
        // on the next close), or by a new one; when none can be put in place, editing must not go on
        if (!removed && unlisten) {
          stop = unlisten;
          armed = wasArmed;
        } else {
          void arm().then((ready) => { if (!ready && !stop) options.unguarded?.(); }).catch(() => undefined);
        }
      }
    })();
    releasing = current;
    void current.finally(() => { if (releasing === current) releasing = null; });
    return current;
  };
  const request = async (): Promise<void> => {
    if (await mayClose()) await release();
  };
  const arm = (): Promise<boolean> => {
    const current = window.onCloseRequested(async (event) => {
      event.preventDefault();
      await request();
    }).then((unlisten) => {
      stop = unlisten;
      return true;
    }, (error: unknown) => {
      // in place, but a leftover listener holds (or may hold) every close: not ready; the next close removes it first
      if (error instanceof StaleCloseListeners) stop = error.stop;
      return false;
    }).then((ready) => {
      armed = ready;
      failures = ready ? 0 : failures + 1;
      const timers = options.timers;
      // none in place: tried again (not while a close is under way: it puts one back itself if the window stays open)
      if (!ready && !stop && timers) {
        retry = timers.set(() => {
          retry = 0;
          if (!stop && !arming && !releasing) void arm();
        }, retryDelay(failures));
      }
      return ready;
    });
    arming = current;
    void current.finally(() => { if (arming === current) arming = null; });
    return current;
  };
  button.addEventListener("click", () => { void request().catch(() => undefined); });
  void arm();
  return { ready: () => arming ?? Promise.resolve(armed), now: () => armed && !arming };
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
