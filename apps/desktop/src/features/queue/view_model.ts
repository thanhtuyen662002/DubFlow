import { displayJobStatus, type JobStatus } from "../job_status/status.ts";
import { summarizeQueue, type QueueJob, type QueueSnapshot, type QueueSummary } from "./model.ts";

export type QueueRow = {
  id: string;
  title: string;
  sourcePath: string;
  status: string;
  state: JobStatus["state"];
  progress: string;
  selected: boolean;
};

export type QueueViewModel = {
  summary: QueueSummary;
  rows: QueueRow[];
  selected: QueueJob | null;
};

function progressLabel(status: JobStatus): string {
  const completed = BigInt(status.progress.completed_units);
  if (status.progress.total_units === null) return `${completed.toString()} units`;
  const total = BigInt(status.progress.total_units);
  if (total === 0n) return "0%";
  const bounded = completed > total ? total : completed;
  return `${((bounded * 100n) / total).toString()}%`;
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
      state: job.status.state,
      progress: progressLabel(job.status),
      selected: job.id === snapshot.selected_job_id,
    })),
    selected,
  };
}
