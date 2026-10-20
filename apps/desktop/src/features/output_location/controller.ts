type Target = { id: string; outputRoot: string | null; outputPath: string | null; status: { state: string } };

export function createOutputLocationController(options: {
  selected(): Target | undefined;
  pick(): Promise<string | null>;
  save(jobId: string, root: string | null): void;
  changed(): void;
  error(message: string): void;
}) {
  let busy = false;
  const editable = (job: Target | undefined): job is Target => !!job && job.status.state === "QUEUED" && job.outputPath === null;
  return {
    get busy() { return busy; },
    async choose(): Promise<void> {
      const before = options.selected();
      if (busy || !editable(before)) return;
      const jobId = before.id, previousRoot = before.outputRoot;
      busy = true;
      options.changed();
      try {
        const root = await options.pick();
        const current = options.selected();
        // A delayed dialog cannot update another row or a job that has started.
        if (root !== null && editable(current) && current.id === jobId && current.outputRoot === previousRoot) {
          options.save(jobId, root);
        }
      } catch (error) {
        options.error(`Không chọn được nơi lưu: ${String(error).slice(0, 240)}`);
      } finally {
        busy = false;
        options.changed();
      }
    },
    reset(): void {
      const job = options.selected();
      if (!busy && editable(job)) options.save(job.id, null);
    },
  };
}

type StartTarget = Target & { sourcePath: string; dubbing: { enabled: boolean; voiceId: string | null } };

export function createProcessingStarter<T extends StartTarget>(options: {
  selected(): T | undefined;
  ready(job: T): boolean;
  admit(job: T): Promise<unknown>;
  execute(job: T): Promise<void>;
  rejected(jobId: string, error: unknown, dispatched: boolean): void;
  changed(): void;
}) {
  let busy = false;
  const identity = (job: T) => JSON.stringify([job.id, job.sourcePath, job.outputRoot, job.outputPath, job.dubbing, job.status]);
  return {
    get busy() { return busy; },
    async start(): Promise<void> {
      const before = options.selected();
      if (busy || !before || !options.ready(before)) return;
      const expected = identity(before), jobId = before.id;
      let dispatched = false;
      busy = true;
      options.changed();
      try {
        await options.admit(before);
        const current = options.selected();
        if (!current || identity(current) !== expected) return;
        dispatched = true;
        await options.execute(current);
      } catch (error) {
        options.rejected(jobId, error, dispatched);
      } finally {
        busy = false;
        options.changed();
      }
    },
  };
}
