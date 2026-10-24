import { displayJobStatus, progressFraction, stateLabel, type JobStatus } from "../job_status/status.ts";
import { summarizeQueue, type QueueJob, type QueueSnapshot, type QueueSummary } from "./model.ts";

export type QueueRow = {
  id: string;
  title: string;
  sourcePath: string;
  status: string;
  stateLabel: string;
  state: JobStatus["state"];
  progress: string;
  fraction: number | null;
  dubbing: boolean;
  selected: boolean;
};

export type QueueViewModel = {
  summary: QueueSummary;
  rows: QueueRow[];
  selected: QueueJob | null;
};

function progressLabel(status: JobStatus): string {
  const fraction = progressFraction(status);
  if (fraction === null) return `${BigInt(status.progress.completed_units).toString()} units`;
  return `${Math.floor(fraction * 100)}%`;
}

export function toQueueViewModel(snapshot: QueueSnapshot): QueueViewModel {
  const selected = snapshot.jobs.find((job) => job.id === snapshot.selected_job_id) ?? null;
  return {
    summary: summarizeQueue(snapshot),
    rows: snapshot.jobs.map((job) => ({
      id: job.id,
      title: job.displayName,
      sourcePath: job.sourcePath,
      status: displayJobStatus(job.status),
      stateLabel: stateLabel(job.status.state),
      state: job.status.state,
      progress: progressLabel(job.status),
      fraction: progressFraction(job.status),
      dubbing: job.dubbing.enabled,
      selected: job.id === snapshot.selected_job_id,
    })),
    selected,
  };
}
