import type { JobStatus } from "../features/job_status/status.ts";
import { QueueController, type SnapshotStorage } from "../features/queue/model.ts";

export type MockTransport = {
  queue: QueueController;
  start(jobId: string): void;
  recover(jobId: string): void;
  complete(jobId: string, outputPath: string): void;
};

const status = (state: JobStatus["state"], message: string): JobStatus => ({
  schema_version: 1,
  state,
  reason: state.toLowerCase(),
  message,
  checkpoint_id: state === "QUEUED" ? null : `checkpoint-${state.toLowerCase()}`,
  progress: {
    completed_units: state === "COMPLETED" ? "1" : "0",
    total_units: "1",
    heartbeat_sequence: state === "RUNNING" ? "1" : "0",
  },
  retry: {
    attempt: "0",
    max_attempts: "3",
    condition_fingerprint: null,
  },
  resource: {
    kind: state === "RUNNING" ? "WORKER_SLOT" : null,
    held: state === "RUNNING",
    release_requested: false,
  },
});

export function createMockTransport(
  storage: SnapshotStorage,
  clock = () => new Date().toISOString(),
): MockTransport {
  const queue = new QueueController(storage, clock);
  return {
    queue,
    start(jobId) {
      queue.updateStatus(jobId, status("RUNNING", "Processing"));
    },
    recover(jobId) {
      queue.updateStatus(jobId, status("RECOVERED", "Recovered from the last checkpoint"));
    },
    complete(jobId, outputPath) {
      queue.updateStatus(jobId, status("COMPLETED", "Validated output ready"), outputPath);
    },
  };
}
