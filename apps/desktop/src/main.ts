import { invoke } from "@tauri-apps/api/core";
import { connectDesktopShell } from "./shell/desktop_shell.ts";
import { QueueController, type SnapshotStorage } from "./features/queue/model.ts";
import type { JobStatus } from "./features/job_status/status.ts";
import { parseSourceInput, supervisorSourceItems } from "./features/source_intake/model.ts";

type ReleaseInfo = { version: string; channel: string; backend: string };

const STORAGE_KEY = "dubflow.queue.v2";

function browserStorage(): SnapshotStorage {
  return {
    read: () => {
      try {
        return window.localStorage.getItem(STORAGE_KEY);
      } catch {
        return null;
      }
    },
    write: (value) => {
      try {
        window.localStorage.setItem(STORAGE_KEY, value);
      } catch {
        // The queue remains usable when a browser profile disallows storage.
      }
    },
  };
}

type StatusResponse = { status: JobStatus; output_path: string | null };

const queue = new QueueController(browserStorage());
const pollers = new Map<string, number>();

const $ = <T extends HTMLElement>(selector: string): T => {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Missing desktop element: ${selector}`);
  return element;
};

const summary = $("#queue-summary");
const queueState = $("#queue-state");
const queueList = $("#queue-list");
const detail = $("#job-detail");

function render(model: Parameters<Parameters<typeof connectDesktopShell>[1]>[0]): void {
  const { view } = model;
  const total = view.summary.total;
  summary.textContent = `${total} video${total === 1 ? "" : "s"}`;
  queueState.textContent = total === 0 ? "Sẵn sàng" : `${view.summary.queued} đang chờ`;
  queueList.replaceChildren();
  if (view.rows.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "Chưa có video. Bấm “Thêm video” để bắt đầu.";
    queueList.append(empty);
  } else {
    for (const row of view.rows) {
      const item = document.createElement("li");
      item.className = `job${row.selected ? " selected" : ""}`;
      item.tabIndex = 0;
      item.innerHTML = `<span class="job-title"></span><span class="job-progress"></span><span class="job-status"></span>`;
      (item.querySelector(".job-title") as HTMLElement).textContent = row.title;
      (item.querySelector(".job-progress") as HTMLElement).textContent = row.progress;
      (item.querySelector(".job-status") as HTMLElement).textContent = row.status;
      const select = () => queue.selectJob(row.id);
      item.addEventListener("click", select);
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") select();
      });
      queueList.append(item);
    }
  }

  detail.replaceChildren();
  if (!view.selected) {
    const empty = document.createElement("div");
    empty.className = "detail-empty";
    empty.textContent = "Chọn một video trong hàng đợi.";
    detail.append(empty);
    return;
  }
  const card = document.createElement("dl");
  card.className = "detail-card";
  for (const [label, value] of [
    ["Tên", view.selected.displayName],
    ["Đường dẫn", view.selected.sourcePath],
    ["Trạng thái", view.selected.status.message],
  ]) {
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = value;
    card.append(term, description);
  }
  detail.append(card);
}

const shell = connectDesktopShell(queue, render);

async function refreshJob(jobId: string): Promise<void> {
  try {
    const response = await invoke<StatusResponse | null>("job_status", { jobId });
    if (!response) return;
    const job = queue.getSnapshot().jobs.find((candidate) => candidate.id === jobId);
    if (!job) return;
    queue.updateStatus(jobId, response.status, response.output_path);
    if (response.status.state === "COMPLETED" || response.status.state === "FAILED") {
      const timer = pollers.get(jobId);
      if (timer !== undefined) window.clearInterval(timer);
      pollers.delete(jobId);
    }
  } catch (error) {
    const job = queue.getSnapshot().jobs.find((candidate) => candidate.id === jobId);
    if (job && job.status.state === "RUNNING") {
      queue.updateStatus(jobId, {
        ...job.status,
        state: "WORKER_LOST",
        reason: "status_unavailable",
        message: `Không đọc được trạng thái supervisor: ${String(error).slice(0, 200)}`,
        resource: { ...job.status.resource, held: false },
      });
    }
  }
}

function beginPolling(jobId: string): void {
  if (pollers.has(jobId)) return;
  void refreshJob(jobId);
  const timer = window.setInterval(() => void refreshJob(jobId), 1000);
  pollers.set(jobId, timer);
}

$("#add-files").addEventListener("click", async () => {
  try {
    const paths = await invoke<string[]>("pick_files");
    queue.addPaths(paths);
  } catch (error) {
    $("#queue-state").textContent = `Không mở được hộp thoại file: ${String(error).slice(0, 160)}`;
  }
});

$("#add-sources").addEventListener("click", async () => {
  const input = $("#source-input") as HTMLTextAreaElement;
  const state = $("#source-intake-state");
  const batch = parseSourceInput(input.value);
  const localPaths = batch.items.filter((item) => item.kind === "local").map((item) => item.local_path as string);
  if (localPaths.length > 0) queue.addPaths(localPaths);
  const networkItems = batch.items.filter((item) => item.kind === "url");
  try {
    if (networkItems.length > 0) {
      const response = await invoke<{ event: string; scan?: unknown }>("enqueue_sources", { items: supervisorSourceItems({ ...batch, items: networkItems }) });
      const scan = response.scan as { scan?: { scan_id?: string; discovered_count?: number } } | undefined;
      state.textContent = `Đã đưa ${networkItems.length} URL vào hàng đợi nguồn (${scan?.scan?.scan_id ?? "đã ghi nhận"}).`;
    } else {
      state.textContent = localPaths.length > 0 ? `Đã thêm ${localPaths.length} đường dẫn cục bộ.` : "Chưa có nguồn hợp lệ.";
    }
  } catch (error) {
    state.textContent = `Không thể ghi hàng đợi nguồn: ${String(error).slice(0, 240)}`;
  }
  if (batch.rejected.length > 0) {
    state.textContent += ` Bỏ qua ${batch.rejected.length} mục không hợp lệ/trùng.`;
  }
  input.value = "";
});

$("#start-processing").addEventListener("click", async () => {
  const selected = queue.getSnapshot().jobs.find((job) => job.id === queue.getSnapshot().selected_job_id);
  if (!selected) return;
  const button = $("#start-processing") as HTMLButtonElement;
  button.disabled = true;
  try {
    await invoke("start_job", { jobId: selected.id, sourcePath: selected.sourcePath, outputDir: null });
    beginPolling(selected.id);
    await refreshJob(selected.id);
  } catch (error) {
    queue.updateStatus(selected.id, {
      ...selected.status,
      state: "FAILED",
      reason: "supervisor_start_failed",
      message: String(error).slice(0, 400),
      resource: { ...selected.status.resource, held: false },
    });
  } finally {
    const current = queue.getSnapshot().jobs.find((job) => job.id === selected.id);
    button.disabled = !current || !["QUEUED", "RECOVERED", "FAILED"].includes(current.status.state);
  }
});

const cancelButton = $("#cancel-processing") as HTMLButtonElement;
cancelButton.addEventListener("click", async () => {
  const selected = queue.getSnapshot().jobs.find((job) => job.id === queue.getSnapshot().selected_job_id);
  if (!selected || !["RUNNING", "RETRYING", "RECOVERED", "WORKER_LOST"].includes(selected.status.state)) return;
  cancelButton.disabled = true;
  try {
    await invoke("cancel_job", { jobId: selected.id });
    beginPolling(selected.id);
  } catch (error) {
    queue.updateStatus(selected.id, {
      ...selected.status,
      reason: "cancel_failed",
      message: "Không thể hủy job: " + String(error).slice(0, 300),
    });
  }
});

$("#clear-queue").addEventListener("click", () => {
  for (const job of queue.getSnapshot().jobs) {
    const timer = pollers.get(job.id);
    if (timer !== undefined) window.clearInterval(timer);
    pollers.delete(job.id);
    queue.removeJob(job.id);
  }
});

const startButton = $("#start-processing") as HTMLButtonElement;
queue.subscribe((snapshot) => {
  const selected = snapshot.jobs.find((job) => job.id === snapshot.selected_job_id);
  startButton.disabled = !selected || !["QUEUED", "RECOVERED", "FAILED"].includes(selected.status.state);
  cancelButton.disabled = !selected || !["RUNNING", "RETRYING", "RECOVERED", "WORKER_LOST"].includes(selected.status.state);
  if (selected && ["RUNNING", "RECOVERED", "WORKER_LOST"].includes(selected.status.state)) beginPolling(selected.id);
});
for (const job of queue.getSnapshot().jobs) beginPolling(job.id);

void invoke<ReleaseInfo>("release_info")
  .then((info) => {
    $("#release-version").textContent = `v${info.version}`;
    $("#release-channel").textContent = info.channel;
  })
  .catch(() => {
    $("#release-version").textContent = "development";
  });

window.addEventListener("beforeunload", () => shell.dispose());
