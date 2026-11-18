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
 * again `RETRY_FIRST_MS` later, then after twice as long each time up to `RETRY_MAX_MS`. Nothing brings a try
 * forward of that backoff, neither an edit nor a timer set before the write failed; only `flush` (a close, the window
 * hidden) tries a pending change at once, and it says whether every change reached the disk, so a close can ask
 * instead of dropping one.
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
  /** When a failed write is due to be tried again: no timer fires before it. */
  private retryAt = 0;
  /** The ticket of the write waiting for the one in progress (it reads the draft as it is when it starts); 0: none. */
  private waiting = 0;
  /** The waiting write was asked for by `flush`: it does not wait out a backoff. */
  private forced = false;
  private tickets = 0;
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
    // after a failed write: not before it is due again (an edit never shortens the backoff)
    if (this.failures > 0) delay = Math.max(delay, this.retryAt - now);
    // and never later than a retry would be, whatever the clock did
    this.schedule(Math.min(Math.max(delay, 0), RETRY_MAX_MS));
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
      this.start(true);
      await this.writing;
      if (this.failures > 0) return false;
    }
    await this.writing;
    return this.since === null && this.failures === 0;
  }

  /** Forget a pending change: the session ended without it mattering, or the user chose to lose it. */
  drop(): void {
    this.cancel();
    // a write still waiting belongs to what was dropped: it ends without writing, and a new change gets its own
    this.waiting = 0;
    this.forced = false;
    this.since = null;
    this.failures = 0;
    this.epoch += 1;
    // nor does the next session wait for a write of the dropped change, even one that never ends (a close gave up on
    // it after CLOSE_WAIT_MS); the host still runs draft saves one after another
    this.writing = Promise.resolve();
  }

  private schedule(delay: number): void {
    this.cancel();
    // whole milliseconds, rounded up: a timer is never set to fire before a retry is due
    this.timer = this.timers.set(() => {
      this.timer = 0;
      this.start();
    }, Math.ceil(delay));
  }

  private cancel(): void {
    if (this.timer) this.timers.clear(this.timer);
    this.timer = 0;
  }

  private start(force = false): void {
    // one write after another, and at most one waiting: it saves every change made until it starts
    if (force) this.forced = true;
    if (this.waiting) return;
    const ticket = ++this.tickets;
    this.waiting = ticket;
    const epoch = this.epoch;
    this.writing = this.writing.then(async () => {
      const mine = this.waiting === ticket;
      const forced = mine && this.forced;
      if (mine) {
        this.waiting = 0;
        this.forced = false;
      }
      if (epoch !== this.epoch || this.since === null) return;
      // a timer's write that waited behind one that failed waits out the backoff too: the retry timer writes it. A
      // timer can also fire a little early (setTimeout drops the fraction of a millisecond): then it is set again,
      // never left without one
      if (!forced && this.failures > 0 && this.timers.now() < this.retryAt) {
        if (!this.timer) this.schedule(this.retryAt - this.timers.now());
        return;
      }
      const version = this.version;
      try {
        await this.write();
      } catch {
        if (epoch !== this.epoch) return;
        // still pending: tried again after the backoff, which also replaces a timer an edit set during this write
        this.failures += 1;
        const wait = retryDelay(this.failures);
        this.retryAt = this.timers.now() + wait;
        this.schedule(wait);
        return;
      }
      if (epoch !== this.epoch) return;
      this.failures = 0;
      // a change made during the write is still pending: its own timer (or a flush) writes it
      if (this.version === version) this.since = null;
    });
  }
}

/**
 * The editor's drafts, one saver per video (Issue #929). The open editor's draft and a change a reload carried that no
 * editor took up yet (a copy) are written by the saver of their video, so a held file is retried on its own backoff:
 * it never slows another video's saves, and letting one video's change go leaves every other one as it is. A video's
 * writes stay in order, with one backoff, whoever makes the change: the copy's and then its reopened editor's.
 */
export class DraftSavers<T extends { outputPath: string }> {
  private readonly savers = new Map<string, Autosaver>();
  private readonly copies = new Map<string, T>();
  private readonly write: (outputPath: string, copy: T | undefined) => Promise<void>;
  private readonly timers: Timers;

  /** `write` writes the draft of the video at `outputPath`: its open editor's, else `copy`, else nothing. */
  constructor(write: (outputPath: string, copy: T | undefined) => Promise<void>, timers: Timers) {
    this.write = write;
    this.timers = timers;
  }

  /** A change of the draft of `outputPath`, to write. */
  changed(outputPath: string): void {
    let saver = this.savers.get(outputPath);
    if (!saver) {
      saver = new Autosaver(() => this.writeDraft(outputPath), this.timers);
      this.savers.set(outputPath, saver);
    }
    saver.changed();
  }

  /** Write the draft of `outputPath` now; true when the disk has every change of it. */
  flush(outputPath: string): Promise<boolean> {
    return this.savers.get(outputPath)?.flush() ?? Promise.resolve(true);
  }

  /**
   * Write every video's draft now, each on its own: one whose write fails or hangs holds up no other. True when every
   * one reached the disk, a change made meanwhile (while another video's write ran) included, a few rounds at most.
   */
  async flushAll(): Promise<boolean> {
    for (let round = 0; round < 3; round += 1) {
      const saved = await Promise.all([...this.savers.values()].map((saver) => saver.flush()));
      if (!saved.every(Boolean)) return false;
      if (!this.unsaved().length) return true;
    }
    return false;
  }

  /**
   * The videos with a change not on disk yet (waiting, being written, or tried again after a failed write); a copy is
   * one of them until its video's saver wrote it.
   */
  unsaved(): string[] {
    return [...this.savers].filter(([, saver]) => saver.pending).map(([outputPath]) => outputPath);
  }

  /** The copy pending for the video at `outputPath`. */
  get(outputPath: string): T | undefined {
    return this.copies.get(outputPath);
  }

  /**
   * Keep `copy`, the change its video's editor could not write, for the video's reopened editor; written from it
   * until one takes it up. One per video: another video's copy is never replaced. The video's saver goes on: its
   * backoff, and a write under way.
   */
  carry(copy: T): void {
    this.copies.set(copy.outputPath, copy);
    this.changed(copy.outputPath);
  }

  /** An editor of its video opened `copy` and goes on with it (through the same saver); false when it is not pending. */
  take(copy: T): boolean {
    if (this.copies.get(copy.outputPath) !== copy) return false;
    this.copies.delete(copy.outputPath);
    return true;
  }

  /** The draft of `outputPath` on disk has every change: a copy of it is older, never written over it. */
  forget(outputPath: string): void {
    this.copies.delete(outputPath);
  }

  /**
   * The user chose to lose the change of `outputPath` ("Vẫn thoát"): its copy and its saver go, and a write of it
   * under way holds up no later one. Every other video's goes on as it was: its backoff, and a write under way.
   */
  drop(outputPath: string): void {
    this.copies.delete(outputPath);
    this.savers.get(outputPath)?.drop();
    this.savers.delete(outputPath);
  }

  private async writeDraft(outputPath: string): Promise<void> {
    const copy = this.copies.get(outputPath);
    await this.write(outputPath, copy);
    // on disk: the copy goes, unless a newer one replaced it meanwhile (its own write follows)
    if (copy && this.copies.get(outputPath) === copy) this.copies.delete(outputPath);
  }
}

export const windowTimers: Timers = {
  // a steady clock: a system time change cannot push a save or a retry away
  now: () => performance.now(),
  set: (callback, ms) => window.setTimeout(callback, ms),
  clear: (handle) => window.clearTimeout(handle),
};
