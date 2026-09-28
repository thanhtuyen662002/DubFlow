/**
 * Presentation adapter for contracts/job_status/schema-v1.json.
 * The desktop never infers liveness from a percentage or a wall clock; it
 * displays the supervisor's explicit state and actionable message.
 */
export type JobStatusState =
  | "QUEUED"
  | "RUNNING"
  | "WAITING_RESOURCE"
  | "WAITING_EXTERNAL"
  | "RETRYING"
  | "WORKER_LOST"
  | "RECOVERED"
  | "BLOCKED_NEEDS_ACTION"
  | "COMPLETED"
  | "FAILED";

export type JobStatus = {
  schema_version: 1;
  state: JobStatusState;
  reason: string;
  message: string;
  checkpoint_id: string | null;
  progress: {
    completed_units: string;
    total_units: string | null;
    heartbeat_sequence: string;
  };
  retry: {
    attempt: string;
    max_attempts: string;
    condition_fingerprint: string | null;
  };
  resource: {
    kind: "GPU" | "CPU" | "DISK" | "NETWORK" | "WORKER_SLOT" | null;
    held: boolean;
    release_requested: boolean;
  };
};

export function displayJobStatus(status: JobStatus): string {
  if (status.state === "WAITING_RESOURCE" && status.resource.kind === "GPU") {
    return "Waiting for GPU";
  }
  if (status.state === "WAITING_EXTERNAL") {
    return status.message || "Action required";
  }
  if (status.state === "RETRYING") {
    return "Retrying smaller chunk";
  }
  if (status.state === "RECOVERED") {
    return "Stalled/recovered";
  }
  if (status.state === "BLOCKED_NEEDS_ACTION") {
    return "Stalled; action required";
  }
  return status.message;
}
