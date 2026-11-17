/**
 * Lưu nháp tự động (Phase 9A): when the editor's draft goes to disk.
 *
 * Before, a save ran 500 ms after the *last* change only, so a burst of quick edits (typing, nudging a slider, a
 * string of cuts) was never saved while it went on, and a crash or a power cut during it lost all of it (measured on
 * the PC: a hard kill 151 ms or 345 ms after an edit lost the edit). Now a save runs `QUIET_MS` after the last change
 * but never later than `MAX_WAIT_MS` after the first change not yet on disk, and saves never overlap: a change made
 * while one is being written is saved by the next one. `flush` writes what is pending now (leaving the editor,
 * closing the window, the window being hidden).
 *
 * A write that fails keeps its change pending (Issue #929: on the PC another program held the draft file, the
 * failed save was never tried again, and closing the app right after lost the change without a word). It is tried
 * again `RETRY_FIRST_MS` later, then after twice as long each time up to `RETRY_MAX_MS`; an edit brings the next
 * try forward to at most `MAX_WAIT_MS` after it (so at most one try a second while the user keeps editing). `flush` tries a pending change at once and says whether every change
 * reached the disk, so a close can ask instead of dropping one.
 */
export const QUIET_MS = 300;
export const MAX_WAIT_MS = 1000;
export const RETRY_FIRST_MS = 1000;
export const RETRY_MAX_MS = 30_000;

export type Timers = {
  now(): number;
  set(callback: () => void, ms: number): number;
  clear(handle: number): void;
};

/** How long to wait before saving, from now, when the first unsaved change was at `since`. */
export function saveDelay(now: number, since: number): number {
  return Math.max(0, Math.min(QUIET_MS, since + MAX_WAIT_MS - now));
}

/** How long to wait before trying again after `failures` failed writes in a row (1, 2, …). */
export function retryDelay(failures: number): number {
  return Math.min(RETRY_MAX_MS, RETRY_FIRST_MS * 2 ** Math.min(Math.max(failures - 1, 0), 16));
}

export class Autosaver {
  private timer = 0;
  /** When the first change not yet on disk was made; null when the disk has every change. */
  private since: number | null = null;
  /** Counts the changes: a write covers the ones made before it started. */
  private version = 0;
  /** Failed writes in a row; back to 0 once one succeeds. */
  private failures = 0;
  /** When a failed write is due to be tried again. */
  private retryAt = 0;
  /** The first edit since the last failed write, if any. */
  private editedSinceFailure: number | null = null;
  /** A write is waiting for the one in progress (it reads the draft as it is when it starts). */
  private queued = false;
  /** Changed by `drop`: a write that ends after it leaves the saver alone. */
  private epoch = 0;
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
    this.version += 1;
    if (this.since === null) this.since = now;
    let delay = saveDelay(now, this.since);
    if (this.failures > 0) {
      // after a failed write: not before it is due again, but no later than MAX_WAIT_MS after the first edit since
      if (this.editedSinceFailure === null) this.editedSinceFailure = now;
      delay = Math.max(delay, Math.min(this.editedSinceFailure + MAX_WAIT_MS, this.retryAt) - now, 0);
    }
    this.schedule(delay);
  }

  /** Whether a change is waiting to be written (or being written, or tried again after a failed write). */
  get pending(): boolean {
    return this.since !== null;
  }

  /** Whether the last write failed: its change is still pending and will be tried again. */
  get failing(): boolean {
    return this.failures > 0;
  }

  /**
   * Write what is pending now (a failed change too) and wait for every write to end. True when the disk has every
   * change; false when a write failed, and then the change stays pending and is tried again later.
   */
  async flush(): Promise<boolean> {
    // a change made while flushing is written too, a few rounds at most
    for (let round = 0; round < 3 && this.since !== null; round += 1) {
      this.cancel();
      this.start();
      await this.writing;
      if (this.failures > 0) return false;
    }
    await this.writing;
    return this.since === null && this.failures === 0;
  }

  /** Forget a pending change: the session ended without it mattering, or the user chose to lose it. */
  drop(): void {
    this.cancel();
    this.since = null;
    this.failures = 0;
    this.editedSinceFailure = null;
    this.epoch += 1;
  }

  private schedule(delay: number): void {
    this.cancel();
    this.timer = this.timers.set(() => {
      this.timer = 0;
      this.start();
    }, delay);
  }

  private cancel(): void {
    if (this.timer) this.timers.clear(this.timer);
    this.timer = 0;
  }

  private start(): void {
    // one write after another, and at most one waiting: it saves every change made until it starts
    if (this.queued) return;
    this.queued = true;
    const epoch = this.epoch;
    this.writing = this.writing.then(async () => {
      this.queued = false;
      if (epoch !== this.epoch || this.since === null) return;
      const version = this.version;
      try {
        await this.write();
      } catch {
        if (epoch !== this.epoch) return;
        // still pending: try again later (a newer change's own timer may come first)
        this.failures += 1;
        this.editedSinceFailure = null;
        const wait = retryDelay(this.failures);
        this.retryAt = this.timers.now() + wait;
        if (!this.timer) this.schedule(wait);
        return;
      }
      if (epoch !== this.epoch) return;
      this.failures = 0;
      this.editedSinceFailure = null;
      // a change made during the write is still pending: its own timer (or a flush) writes it
      if (this.version === version) this.since = null;
    });
  }
}

export const windowTimers: Timers = {
  now: () => Date.now(),
  set: (callback, ms) => window.setTimeout(callback, ms),
  clear: (handle) => window.clearTimeout(handle),
};
