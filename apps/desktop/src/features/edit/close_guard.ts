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
export type IdStore = { get(): string | null; set(value: string | null): void };

/** The bits of Web Storage the store uses. */
export type KeyStorage = { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void };

/** An IdStore in Web Storage under `key` (sessionStorage: it outlives a reload, not the app); read only when used. */
export function storageIds(storage: () => KeyStorage, key = CLOSE_LISTENERS_KEY): IdStore {
  return {
    get: () => storage().getItem(key),
    set: (value) => {
      if (value === null) storage().removeItem(key);
      else storage().setItem(key, value);
    },
  };
}

/**
 * What the store keeps: the ids of the close listeners still to remove, and, while a listen is under way, the lowest
 * id it can get (`from`): a page that reloads before the id comes back leaves a listener nothing names.
 */
type Listeners = { ids: number[]; from?: number };

const isId = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;

/** What the store holds, or null when it is not what closeRequests writes. */
function parseListeners(raw: string): Listeners | null {
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const { ids, from } = value as Record<string, unknown>;
    if (!Array.isArray(ids) || !ids.every(isId) || (from !== undefined && !isId(from))) return null;
    return from === undefined ? { ids } : { ids, from };
  } catch {
    return null;
  }
}

/**
 * The store as closeRequests uses it. `failed`: a read or a write threw, so a reload may leave a listener nothing
 * names; `unknown`: it held something else, so the page before may have left one.
 */
function trackedStore(store: IdStore) {
  const state = { failed: false, unknown: false };
  const read = (): Listeners => {
    let raw: string | null;
    try {
      raw = store.get();
    } catch {
      state.failed = true;
      return { ids: [] };
    }
    if (raw === null) return { ids: [] };
    const listeners = parseListeners(raw);
    if (!listeners) state.unknown = true;
    return listeners ?? { ids: [] };
  };
  const write = (listeners: Listeners): void => {
    try {
      store.set(listeners.ids.length || listeners.from !== undefined ? JSON.stringify(listeners) : null);
    } catch {
      state.failed = true;
    }
  };
  return {
    state,
    read,
    write,
    forget: (id: number) => {
      const listeners = read();
      write({ ...listeners, ids: listeners.ids.filter((kept) => kept !== id) });
    },
    remember: (id: number) => {
      const listeners = read();
      write({ ...listeners, ids: [...listeners.ids.filter((kept) => kept !== id), id] });
    },
  };
}

/** At most this many ids are tried when one is not known (Tauri counts every listener, any event). */
export const SWEEP_LIMIT = 4096;

/**
 * A close request listener is in place, but one left behind (by a page before a reload, or one this page could not
 * remove) is, or may be, still there: it would hold every close of the window for a handler that no longer runs, so
 * the guard is not ready (the editor stays closed). `stop` removes the ones known first, then this page's own; it
 * throws while one remains.
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
 * nothing runs it any more: the window could no longer be closed. So the ids are kept where a reload finds them, and
 * every listener left behind (a page before a reload, or one this page could not remove) is removed before a new one
 * starts. Where the ids are not known, every close listener in that range is removed (Tauri numbers listeners in
 * order, removes by event and id, and nothing else listens to close requests): from where a listen whose id never
 * came back started (a reload mid-listen), or from the first id when the store could not be read or held something
 * else, at most `sweepLimit` ids below this page's own (the ones further down stay marked, swept again after a reload).
 *
 * Ready only when no listener can be left: none failed to go, every id below this page's own was accounted for, and
 * the store took this page's id (a store that refuses would leave it to the page after a reload). Otherwise the new
 * listener still hears every close, but the answer is `StaleCloseListeners`: the editor stays closed, and its `stop`
 * (and the next listener) tries the removal again.
 */
export function closeRequests(label: string, bridge: EventBridge, store: IdStore, sweepLimit = SWEEP_LIMIT): ClosableWindow["onCloseRequested"] {
  return async (handler) => {
    const ids = trackedStore(store);
    const before = ids.read();
    // the page before left nothing this page can name: every close listener below this page's own is swept
    const blind = ids.state.failed || ids.state.unknown;
    /** Left behind and still in place: removed before this page's own goes. */
    const left = new Set<number>();
    for (const id of before.ids) {
      try {
        await bridge.unlisten(CLOSE_EVENT, id);
        ids.forget(id);
      } catch {
        left.add(id);  // kept: tried again by stop and before the next listener
      }
    }
    // this listen gets an id above every one known; a listen whose id never came back keeps its own start
    const from = before.from ?? before.ids.reduce((top, id) => Math.max(top, id + 1), 0);
    ids.write({ ...ids.read(), from });
    const id = await bridge.listen(CLOSE_EVENT, label, () => {
      void handler({ preventDefault: () => undefined }).catch(() => undefined);
    });
    ids.remember(id);
    const sweepFrom = blind ? 0 : before.from;
    /** Where ids not known to be free of a close listener start (a sweep cut short by `sweepLimit`). */
    let unswept: number | undefined;
    if (sweepFrom !== undefined) {
      const start = Math.max(sweepFrom, id - sweepLimit);
      if (start > sweepFrom) unswept = sweepFrom;
      for (let lost = start; lost < id; lost += 1) {
        try {
          await bridge.unlisten(CLOSE_EVENT, lost);
          ids.forget(lost);
        } catch {
          ids.remember(lost);
          left.add(lost);
        }
      }
    }
    // ids still not accounted for stay marked: the page after a reload sweeps them again
    const { from: _done, ...listeners } = ids.read();
    ids.write(unswept === undefined ? listeners : { ...listeners, from: unswept });
    const stop = async (): Promise<void> => {
      // every one left behind first: while one is there it holds the close; one that still fails keeps this page's
      // own in place too, so the close stays guarded
      for (const stale of [...left]) {
        await bridge.unlisten(CLOSE_EVENT, stale);
        ids.forget(stale);
        left.delete(stale);
      }
      await bridge.unlisten(CLOSE_EVENT, id);
      ids.forget(id);
    };
    if (left.size || unswept !== undefined || ids.state.failed) throw new StaleCloseListeners(stop);
    return stop;
  };
}

/** The window close guard as wired: whether it is ready now. */
export type CloseWiring = {
  /** True while the guard is in place and no leftover listener can hold a close (waits for one being put in place). */
  ready(): Promise<boolean>;
};

/**
 * The × button and the system's close (Alt+F4, the taskbar) both go through `mayClose`; the window stays open when it
 * answers false. A close request is always held: when `mayClose` lets it through, the listener is removed first and
 * the window closed again, so the system closes it normally (Tauri's own wrapper would destroy the window instead,
 * which needs a permission the app does not have). Two closes at once (× and Alt+F4) share one answer and one
 * release: the listener is removed once and the window closed once. A close asked for while the listener is still
 * starting waits for it. `ready()` says whether the guard is in place now, without a leftover listener: false while
 * it could not start (also after a close that failed and could not put it back), or a listener left behind could not
 * be removed; the editor opens only when it is ready (a system close the guard cannot see, or one a leftover listener
 * holds, would lose a change without asking or keep the window open for good). `unguarded` is told when a close that
 * failed could not put the guard back: a system close is not held any more (nothing in the page can hold it without a
 * listener), so editing does not go on.
 */
export function wireWindowClose(window: ClosableWindow, button: { addEventListener(type: "click", listener: () => void): void },
  mayClose: () => Promise<boolean>, unguarded: () => void = () => undefined): CloseWiring {
  /** Removes the listener (and any left behind, first); null while none is in place. */
  let stop: (() => unknown) | null = null;
  /** The listener being put in place: a close waits for it. */
  let arming: Promise<boolean> | null = null;
  /** The close under way: another close shares it. */
  let releasing: Promise<void> | null = null;
  /** The guard is in place with no leftover listener (not while a close removes it). */
  let armed = false;
  const release = (): Promise<void> => {
    if (releasing) return releasing;
    const current = (async () => {
      if (arming) await arming;
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
        // on the next close), or by a new one
        if (!removed && unlisten) {
          stop = unlisten;
          armed = wasArmed;
        } else {
          void arm().then((ready) => { if (!ready) unguarded(); }).catch(() => undefined);
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
      return ready;
    });
    arming = current;
    void current.finally(() => { if (arming === current) arming = null; });
    return current;
  };
  button.addEventListener("click", () => { void request().catch(() => undefined); });
  void arm();
  return { ready: () => arming ?? Promise.resolve(armed) };
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
