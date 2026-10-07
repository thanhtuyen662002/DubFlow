import { invoke } from "@tauri-apps/api/core";
import { connectDesktopShell } from "./shell/desktop_shell.ts";
import { QueueController, type QueueJob, type SnapshotStorage } from "./features/queue/model.ts";
import { dubbingOptions, filterVoices, parseVoiceCatalog, voiceLabel, type VoiceCatalog } from "./features/voices/model.ts";
import type { JobStatus } from "./features/job_status/status.ts";

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
const voiceControls = $<HTMLFieldSetElement>("#voice-controls");
const dubbingToggle = $<HTMLInputElement>("#enable-dubbing");
const voiceSelect = $<HTMLSelectElement>("#tts-voice");
const genderFilter = $<HTMLSelectElement>("#voice-gender");
const accentFilter = $<HTMLSelectElement>("#voice-accent");
const styleFilter = $<HTMLSelectElement>("#voice-style");
let voiceCatalog: VoiceCatalog | null = null;
let voiceCatalogError = "";
let voiceJobId: string | null = null;

function selectedJob(): QueueJob | undefined {
  const snapshot = queue.getSnapshot();
  return snapshot.jobs.find((job) => job.id === snapshot.selected_job_id);
}

function canStart(job: QueueJob | undefined): boolean {
  return !!job && ["QUEUED", "RECOVERED", "FAILED"].includes(job.status.state) &&
    (!job.dubbing.enabled || !!voiceCatalog?.voices.some((voice) => voice.voice_id === job.dubbing.voiceId));
}

function renderVoiceControls(job: QueueJob | undefined): void {
  if (voiceJobId !== (job?.id ?? null)) {
    genderFilter.value = accentFilter.value = styleFilter.value = "";
    voiceJobId = job?.id ?? null;
  }
  voiceControls.disabled = !job || job.status.state !== "QUEUED" || !voiceCatalog;
  dubbingToggle.checked = job?.dubbing.enabled ?? false;
  $("#voice-picker").hidden = !dubbingToggle.checked;
  voiceSelect.replaceChildren();
  if (!voiceCatalog) {
    $("#voice-catalog-status").textContent = voiceCatalogError || "Đang đọc danh mục giọng…";
    return;
  }
  const selected = job?.dubbing.voiceId ?? voiceCatalog.default_voice_id;
  const choices = filterVoices(voiceCatalog, { gender: genderFilter.value, accent: accentFilter.value, style: styleFilter.value });
  const pinned = voiceCatalog.voices.find((voice) => voice.voice_id === selected);
  // Filtering narrows browsing; it never silently changes a saved voice.
  const rows = pinned && !choices.includes(pinned) ? [pinned, ...choices] : choices;
  for (const voice of rows) {
    const option = document.createElement("option");
    option.value = voice.voice_id;
    option.textContent = voiceLabel(voice) + (voice === pinned && !choices.includes(voice) ? " (đang chọn)" : "");
    voiceSelect.append(option);
  }
  if (!pinned && job?.dubbing.voiceId) {
    const unavailable = document.createElement("option");
    unavailable.value = job.dubbing.voiceId;
    unavailable.textContent = "Giọng đã lưu không có trong phiên bản này";
    voiceSelect.append(unavailable);
  }
  voiceSelect.value = selected;
  $("#voice-description").textContent = pinned?.description ?? "Cần chọn một giọng có sẵn trước khi lồng tiếng.";
  $("#voice-catalog-status").textContent = job && job.status.state !== "QUEUED"
    ? "Giọng của video này đã được lưu khi bắt đầu xử lý."
    : `${voiceCatalog.voices.length} giọng có sẵn · ${choices.length} giọng khớp bộ lọc`;
}

function render(model: Parameters<Parameters<typeof connectDesktopShell>[1]>[0]): void {
  const { view } = model;
  renderVoiceControls(view.selected ?? undefined);
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

$("#start-processing").addEventListener("click", async () => {
  const selected = queue.getSnapshot().jobs.find((job) => job.id === queue.getSnapshot().selected_job_id);
  if (!selected) return;
  const button = $("#start-processing") as HTMLButtonElement;
  if (!canStart(selected)) return;
  button.disabled = true;
  queue.updateStatus(selected.id, { ...selected.status, state: "RUNNING", reason: "starting", message: "Đang khởi động xử lý" });
  try {
    await invoke("start_job", {
      jobId: selected.id, sourcePath: selected.sourcePath, outputDir: null,
      enableDubbing: selected.dubbing.enabled, ttsVoiceId: selected.dubbing.voiceId,
    });
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
    button.disabled = !canStart(current);
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
  startButton.disabled = !canStart(selected);
  cancelButton.disabled = !selected || !["RUNNING", "RETRYING", "RECOVERED", "WORKER_LOST"].includes(selected.status.state);
  if (selected && ["RUNNING", "RECOVERED", "WORKER_LOST"].includes(selected.status.state)) beginPolling(selected.id);
});
for (const job of queue.getSnapshot().jobs) beginPolling(job.id);

dubbingToggle.addEventListener("change", () => {
  const job = selectedJob();
  if (job && voiceCatalog) queue.setDubbing(job.id, dubbingOptions(voiceCatalog, dubbingToggle.checked, job.dubbing.voiceId));
});
voiceSelect.addEventListener("change", () => {
  const job = selectedJob();
  if (job && voiceCatalog) queue.setDubbing(job.id, dubbingOptions(voiceCatalog, true, voiceSelect.value));
});
for (const filter of [genderFilter, accentFilter, styleFilter]) {
  filter.addEventListener("change", () => renderVoiceControls(selectedJob()));
}
void invoke<unknown>("voice_catalog").then((data) => {
  voiceCatalog = parseVoiceCatalog(data);
  for (const [select, key] of [[genderFilter, "gender"], [accentFilter, "accent"], [styleFilter, "style"]] as const) {
    for (const value of new Set(voiceCatalog.voices.map((voice) => voice[key]))) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = key === "accent" ? `Miền ${value}` : value;
      select.append(option);
    }
  }
  renderVoiceControls(selectedJob());
  startButton.disabled = !canStart(selectedJob());
}).catch((error) => {
  voiceCatalogError = `Không đọc được danh mục giọng: ${String(error).slice(0, 160)}`;
  renderVoiceControls(selectedJob());
});

void invoke<ReleaseInfo>("release_info")
  .then((info) => {
    $("#release-version").textContent = `v${info.version}`;
    $("#release-channel").textContent = info.channel;
  })
  .catch(() => {
    $("#release-version").textContent = "development";
  });

window.addEventListener("beforeunload", () => shell.dispose());
