/**
 * Lưu nháp tự động (Phase 9A): when the editor's draft goes to disk.
 *
 * Before, a save ran 500 ms after the *last* change only, so a burst of quick edits (typing, nudging a slider, a
 * string of cuts) was never saved while it went on, and a crash or a power cut during it lost all of it (measured on
 * the PC: a hard kill 151 ms or 345 ms after an edit lost the edit). Now a save runs `QUIET_MS` after the last change
 * but never later than `MAX_WAIT_MS` after the first change not yet on disk, and saves never overlap: a change made
 * while one is being written is saved by the next one. `flush` writes what is pending now (leaving the editor,
 * closing the window, the window being hidden).
 */
export const QUIET_MS = 300;
export const MAX_WAIT_MS = 1000;

export type Timers = {
  now(): number;
  set(callback: () => void, ms: number): number;
  clear(handle: number): void;
};

/** How long to wait before saving, from now, when the first unsaved change was at `since`. */
export function saveDelay(now: number, since: number): number {
  return Math.max(0, Math.min(QUIET_MS, since + MAX_WAIT_MS - now));
}

export class Autosaver {
  private timer = 0;
  private since: number | null = null;
  private writing: Promise<void> = Promise.resolve();
  private readonly write: () => Promise<void>;
  private readonly timers: Timers;

  constructor(write: () => Promise<void>, timers: Timers) {
    this.write = write;
    this.timers = timers;
  }

  /** A change to save. */
  changed(): void {
    const now = this.timers.now();
    if (this.since === null) this.since = now;
    if (this.timer) this.timers.clear(this.timer);
    this.timer = this.timers.set(() => {
      this.timer = 0;
      this.start();
    }, saveDelay(now, this.since));
  }

  /** Whether a change is waiting to be written (or being written). */
  get pending(): boolean {
    return this.since !== null;
  }

  /** Write what is pending now and wait for every write to end. */
  async flush(): Promise<void> {
    if (this.timer) {
      this.timers.clear(this.timer);
      this.timer = 0;
      this.start();
    }
    await this.writing;
  }

  /** Forget a pending change (the session ended without it mattering). */
  drop(): void {
    if (this.timer) this.timers.clear(this.timer);
    this.timer = 0;
    this.since = null;
  }

  private start(): void {
    const since = this.since;
    // one write after another: the next one reads the draft as it is when it starts
    this.writing = this.writing.then(() => this.write()).catch(() => undefined).finally(() => {
      if (this.since === since && !this.timer) this.since = null;
    });
  }
}

export const windowTimers: Timers = {
  now: () => Date.now(),
  set: (callback, ms) => window.setTimeout(callback, ms),
  clear: (handle) => window.clearTimeout(handle),
};
