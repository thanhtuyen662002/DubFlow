import { invoke as tauriInvoke } from "@tauri-apps/api/core";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { connectDesktopShell, type DesktopShellModel } from "./shell/desktop_shell.ts";
import { previewInvoke } from "./shell/preview_invoke.ts";
import { QueueController, type QueueJob, type SnapshotStorage } from "./features/queue/model.ts";
import { dubbingOptions, filterVoices, parseVoiceCatalog, type VoiceCatalog, type VoiceChoice } from "./features/voices/model.ts";
import {
  activeStageIndex, displayJobStatus, PIPELINE_STAGES, progressFraction, stateLabel,
  type JobStatus, type JobStatusState,
} from "./features/job_status/status.ts";

type ReleaseInfo = { version: string; channel: string; backend: string };
type StatusResponse = { status: JobStatus; output_path: string | null };

const STORAGE_KEY = "dubflow.queue.v2";
const VIDEO_EXTENSIONS = /\.(mp4|mkv|mov|avi|webm|m4v)$/i;
const ACTIVE: JobStatusState[] = ["RUNNING", "RETRYING", "RECOVERED", "WORKER_LOST"];
const isTauri = "__TAURI_INTERNALS__" in window;
const invoke: <T>(command: string, args?: Record<string, unknown>) => Promise<T> = isTauri ? tauriInvoke : previewInvoke;

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

const queue = new QueueController(browserStorage());
const pollers = new Map<string, number>();

const $ = <T extends HTMLElement>(selector: string): T => {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Missing desktop element: ${selector}`);
  return element;
};

const queueList = $("#queue-list");
const dropZone = $("#drop-zone");
const queueState = $("#queue-state");
const inspector = $("#inspector");
const voiceControls = $<HTMLFieldSetElement>("#voice-controls");
const dubbingToggle = $<HTMLInputElement>("#enable-dubbing");
const voiceList = $("#voice-list");
const genderFilter = $<HTMLSelectElement>("#voice-gender");
const accentFilter = $<HTMLSelectElement>("#voice-accent");
const styleFilter = $<HTMLSelectElement>("#voice-style");
const startButton = $<HTMLButtonElement>("#start-processing");
const cancelButton = $<HTMLButtonElement>("#cancel-processing");
const newVersionButton = $<HTMLButtonElement>("#new-processing-version");
let voiceCatalog: VoiceCatalog | null = null;
let voiceCatalogError = "";
let voiceJobId: string | null = null;

function selectedJob(): QueueJob | undefined {
  const snapshot = queue.getSnapshot();
  return snapshot.jobs.find((job) => job.id === snapshot.selected_job_id);
}

function canStart(job: QueueJob | undefined): boolean {
  return !!job && ["QUEUED", "RECOVERED"].includes(job.status.state) &&
    (!job.dubbing.enabled || !!voiceCatalog?.voices.some((voice) => voice.voice_id === job.dubbing.voiceId));
}

function canCreateNewVersion(job: QueueJob | undefined): boolean {
  return !!job && ["COMPLETED", "FAILED", "BLOCKED_NEEDS_ACTION"].includes(job.status.state);
}

function isActive(job: QueueJob | undefined): boolean {
  return !!job && ACTIVE.includes(job.status.state);
}

function tone(state: JobStatusState): string {
  if (state === "COMPLETED") return "ok";
  if (state === "FAILED") return "err";
  if (["RUNNING", "RETRYING", "RECOVERED"].includes(state)) return "run";
  if (state === "QUEUED") return "";
  return "warn";
}

function showError(message: string): void {
  queueState.textContent = message;
}

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function progressBar(fraction: number | null, state: JobStatusState): HTMLElement {
  const bar = element("div", `bar ${state === "COMPLETED" ? "ok" : state === "FAILED" ? "err" : ""}`);
  const fill = element("span");
  fill.style.width = `${Math.round((fraction ?? 0) * 100)}%`;
  bar.append(fill);
  return bar;
}

const AVATAR_COLORS = ["#6d66f5", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#14b8a6"];
function avatarColor(id: string): string {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(-2).map((part) => part[0]).join("").toUpperCase();
}

function voiceButton(voice: VoiceChoice, selected: boolean, note = ""): HTMLButtonElement {
  const button = element("button", "voice");
  button.type = "button";
  button.setAttribute("role", "radio");
  button.setAttribute("aria-checked", String(selected));
  button.title = voice.description;
  const avatar = element("span", "avatar", initials(voice.name));
  avatar.style.background = avatarColor(voice.voice_id);
  const text = element("span");
  text.append(
    element("span", "voice-name", voice.name + note),
    element("span", "voice-meta", `${voice.gender} · miền ${voice.accent} · ${voice.style}`),
  );
  button.append(avatar, text);
  button.addEventListener("click", () => {
    const job = selectedJob();
    if (job && voiceCatalog) queue.setDubbing(job.id, dubbingOptions(voiceCatalog, true, voice.voice_id));
  });
  return button;
}

function renderVoiceControls(job: QueueJob | undefined): void {
  if (voiceJobId !== (job?.id ?? null)) {
    genderFilter.value = accentFilter.value = styleFilter.value = "";
    voiceJobId = job?.id ?? null;
  }
  const editable = !!job && job.status.state === "QUEUED" && !!voiceCatalog;
  voiceControls.disabled = !editable;
  dubbingToggle.checked = job?.dubbing.enabled ?? false;
  $("#voice-picker").hidden = !dubbingToggle.checked || (!!job && job.status.state !== "QUEUED");
  voiceList.replaceChildren();
  const status = $("#voice-catalog-status");
  if (!voiceCatalog) {
    status.textContent = voiceCatalogError || "Đang đọc danh mục giọng…";
    return;
  }
  const selected = job?.dubbing.voiceId ?? voiceCatalog.default_voice_id;
  const choices = filterVoices(voiceCatalog, { gender: genderFilter.value, accent: accentFilter.value, style: styleFilter.value });
  const pinned = voiceCatalog.voices.find((voice) => voice.voice_id === selected);
  // Filtering narrows browsing; it never silently changes a saved voice.
  if (pinned && !choices.includes(pinned)) voiceList.append(voiceButton(pinned, true, " (đang chọn)"));
  for (const voice of choices) voiceList.append(voiceButton(voice, voice.voice_id === selected));
  if (!editable) voiceList.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  if (!pinned && job?.dubbing.voiceId) {
    status.textContent = "Giọng đã lưu không có trong phiên bản này — hãy chọn giọng khác.";
  } else if (job && job.status.state !== "QUEUED") {
    status.textContent = job.dubbing.enabled && pinned
      ? `Giọng: ${pinned.name} (${pinned.gender} · miền ${pinned.accent}) · đã khóa khi bắt đầu.`
      : "Chỉ tạo phụ đề · đã khóa khi bắt đầu.";
  } else if (dubbingToggle.checked) {
    status.textContent = `${choices.length}/${voiceCatalog.voices.length} giọng · dữ liệu giọng tải ở lần lồng tiếng đầu, sau đó chạy ngoại tuyến.`;
  } else {
    status.textContent = "";
  }
}

function renderQueue(view: DesktopShellModel["view"]): void {
  const hasJobs = view.rows.length > 0;
  dropZone.hidden = hasJobs;
  queueList.hidden = !hasJobs;
  $("#clear-queue").hidden = !hasJobs;
  const { summary } = view;
  $("#nav-count").textContent = String(summary.total);
  $("#stat-running").textContent = String(summary.active);
  $("#stat-done").textContent = String(summary.completed);
  $("#stat-failed").textContent = String(summary.failed);
  $("#queue-summary").textContent = hasJobs
    ? [`${summary.total} video`, summary.active ? `${summary.active} đang chạy` : "", summary.queued ? `${summary.queued} chờ xử lý` : "",
      summary.completed ? `${summary.completed} hoàn tất` : ""].filter(Boolean).join(" · ")
    : "Chưa có video";

  queueList.replaceChildren();
  for (const row of view.rows) {
    const item = element("li", `job${row.selected ? " selected" : ""}`);
    item.tabIndex = 0;
    const thumb = element("span", "job-thumb");
    thumb.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="m10 9.5 4.5 2.5-4.5 2.5z" fill="currentColor"/></svg>`;
    const main = element("div", "job-main");
    const meta = element("div", "job-meta");
    meta.append(element("span", `mini-tag${row.dubbing ? "" : " muted"}`, row.dubbing ? "Lồng tiếng" : "Vietsub"));
    meta.append(element("span", "msg", row.state === "QUEUED" ? row.sourcePath : row.status));
    main.append(element("span", "job-title", row.title), meta);
    if (row.state !== "QUEUED") main.append(progressBar(row.fraction, row.state));
    const side = element("div", "job-side");
    side.append(element("span", `badge ${tone(row.state)}`, row.stateLabel));
    if (row.state !== "QUEUED") side.append(element("span", "percent", row.progress));
    item.append(thumb, main, side);
    item.title = row.sourcePath;
    const select = () => queue.selectJob(row.id);
    item.addEventListener("click", select);
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") select();
    });
    queueList.append(item);
  }
}

function renderInspector(job: QueueJob | null): void {
  inspector.hidden = !job;
  if (!job) return;
  const { status } = job;
  $("#detail-name").textContent = job.displayName;
  $("#detail-path").textContent = job.sourcePath;
  const badge = $("#detail-state");
  badge.className = `badge ${tone(status.state)}`;
  badge.textContent = stateLabel(status.state);
  const fraction = progressFraction(status);
  $("#detail-percent").textContent = status.state === "QUEUED" || fraction === null ? "" : `${Math.floor(fraction * 100)}%`;
  const bar = $("#detail-bar");
  bar.style.width = `${Math.round((fraction ?? 0) * 100)}%`;
  bar.parentElement!.className = `bar ${status.state === "COMPLETED" ? "ok" : status.state === "FAILED" ? "err" : ""}`;
  bar.parentElement!.hidden = status.state === "QUEUED";
  $("#detail-message").textContent = status.state === "QUEUED"
    ? "Chọn tùy chọn bên dưới rồi bấm “Bắt đầu xử lý”."
    : displayJobStatus(status);

  const stages = $("#stage-list");
  stages.replaceChildren();
  const active = activeStageIndex(status);
  PIPELINE_STAGES.forEach((stage, index) => {
    const item = element("li", "", stage.label);
    if (stage.id === "dubbing" && !job.dubbing.enabled) item.classList.add("skipped");
    else if (index < active) item.classList.add("done");
    else if (index === active) item.classList.add(status.state === "FAILED" ? "failed" : "active");
    stages.append(item);
  });
  stages.hidden = status.state === "QUEUED";

  const output = status.state === "COMPLETED" ? job.outputPath : null;
  $("#result-card").hidden = !output;
  $("#result-path").textContent = output ?? "";

  renderVoiceControls(job);
  startButton.disabled = !canStart(job);
  startButton.hidden = !["QUEUED", "RECOVERED"].includes(status.state);
  startButton.textContent = status.state === "RECOVERED" ? "Tiếp tục xử lý" : "Bắt đầu xử lý";
  cancelButton.hidden = !isActive(job);
  cancelButton.disabled = !isActive(job);
  newVersionButton.hidden = !canCreateNewVersion(job);
  newVersionButton.disabled = !canCreateNewVersion(job);
  $<HTMLButtonElement>("#remove-job").disabled = isActive(job);
  $("#action-hint").textContent = canCreateNewVersion(job)
    ? "Tạo bản mới để đổi giọng hoặc tùy chọn. Kết quả cũ vẫn được giữ lại."
    : isActive(job) ? "Có thể đóng ứng dụng — tiến trình được lưu checkpoint." : "";
}

let lastModel: DesktopShellModel | null = null;
function render(model: DesktopShellModel): void {
  lastModel = model;
  renderQueue(model.view);
  renderInspector(model.view.selected);
}

const shell = connectDesktopShell(queue, render);

function stopPolling(jobId: string): void {
  const timer = pollers.get(jobId);
  if (timer !== undefined) window.clearInterval(timer);
  pollers.delete(jobId);
}

async function refreshJob(jobId: string): Promise<void> {
  try {
    const response = await invoke<StatusResponse | null>("job_status", { jobId });
    if (!response) return;
    if (!queue.getSnapshot().jobs.some((candidate) => candidate.id === jobId)) return;
    queue.updateStatus(jobId, response.status, response.output_path);
    if (response.status.state === "COMPLETED" || response.status.state === "FAILED") stopPolling(jobId);
  } catch (error) {
    const job = queue.getSnapshot().jobs.find((candidate) => candidate.id === jobId);
    if (job && job.status.state === "RUNNING") {
      queue.updateStatus(jobId, {
        ...job.status,
        state: "WORKER_LOST",
        reason: "status_unavailable",
        message: `Không đọc được trạng thái xử lý: ${String(error).slice(0, 200)}`,
        resource: { ...job.status.resource, held: false },
      });
    }
  }
}

function beginPolling(jobId: string): void {
  if (pollers.has(jobId)) return;
  void refreshJob(jobId);
  pollers.set(jobId, window.setInterval(() => void refreshJob(jobId), 1000));
}

function addPaths(paths: string[]): void {
  const videos = paths.filter((path) => VIDEO_EXTENSIONS.test(path));
  if (paths.length && !videos.length) {
    showError("Chỉ hỗ trợ file video MP4, MKV, MOV, AVI, WEBM, M4V.");
    return;
  }
  showError("");
  const added = queue.addPaths(videos);
  if (added.length && !selectedJob()) queue.selectJob(added[0]);
  else if (added.length) queue.selectJob(added[added.length - 1]);
}

async function pickFiles(): Promise<void> {
  try {
    addPaths(await invoke<string[]>("pick_files"));
  } catch (error) {
    showError(`Không mở được hộp thoại chọn file: ${String(error).slice(0, 160)}`);
  }
}

$("#add-files").addEventListener("click", () => void pickFiles());
$("#add-files-inline").addEventListener("click", () => void pickFiles());

startButton.addEventListener("click", async () => {
  const selected = selectedJob();
  if (!selected || !canStart(selected)) return;
  startButton.disabled = true;
  queue.updateStatus(selected.id, { ...selected.status, state: "RUNNING", reason: "starting", message: "Đang khởi động xử lý" });
  try {
    await invoke("start_job", {
      jobId: selected.id, sourcePath: selected.sourcePath, outputDir: null,
      enableDubbing: selected.dubbing.enabled, ttsVoiceId: selected.dubbing.voiceId,
    });
    beginPolling(selected.id);
  } catch (error) {
    queue.updateStatus(selected.id, {
      ...selected.status,
      state: "FAILED",
      reason: "supervisor_start_failed",
      message: String(error).slice(0, 400),
      resource: { ...selected.status.resource, held: false },
    });
  }
});

cancelButton.addEventListener("click", async () => {
  const selected = selectedJob();
  if (!selected || !isActive(selected)) return;
  cancelButton.disabled = true;
  try {
    await invoke("cancel_job", { jobId: selected.id });
    beginPolling(selected.id);
  } catch (error) {
    queue.updateStatus(selected.id, {
      ...selected.status,
      reason: "cancel_failed",
      message: "Không thể hủy: " + String(error).slice(0, 300),
    });
  }
});

newVersionButton.addEventListener("click", () => {
  const selected = selectedJob();
  if (!selected || !canCreateNewVersion(selected)) return;
  try {
    queue.selectJob(queue.retryJob(selected.id));
  } catch (error) {
    showError(`Không tạo được bản xử lý mới: ${String(error).slice(0, 180)}`);
  }
});

$("#remove-job").addEventListener("click", () => {
  const selected = selectedJob();
  if (!selected || isActive(selected)) return;
  stopPolling(selected.id);
  queue.removeJob(selected.id);
});

$("#clear-queue").addEventListener("click", () => {
  const jobs = queue.getSnapshot().jobs;
  const active = jobs.filter((job) => isActive(job)).length;
  if (!window.confirm(active
    ? `Xóa ${jobs.length - active} video khỏi danh sách? ${active} video đang chạy sẽ được giữ lại.`
    : `Xóa toàn bộ ${jobs.length} video khỏi danh sách? File kết quả trên ổ đĩa không bị xóa.`)) return;
  for (const job of jobs) {
    if (isActive(job)) continue;
    stopPolling(job.id);
    queue.removeJob(job.id);
  }
});

$("#open-output").addEventListener("click", () => {
  const path = selectedJob()?.outputPath;
  if (path) invoke("open_path", { path }).catch((error) => showError(`Không mở được video: ${String(error).slice(0, 160)}`));
});
$("#reveal-output").addEventListener("click", () => {
  const path = selectedJob()?.outputPath;
  if (path) invoke("reveal_path", { path }).catch((error) => showError(`Không mở được thư mục: ${String(error).slice(0, 160)}`));
});

queue.subscribe((snapshot) => {
  const selected = snapshot.jobs.find((job) => job.id === snapshot.selected_job_id);
  if (selected && ["RUNNING", "RECOVERED", "WORKER_LOST"].includes(selected.status.state)) beginPolling(selected.id);
});
for (const job of queue.getSnapshot().jobs) {
  if (!["QUEUED", "COMPLETED", "FAILED"].includes(job.status.state)) beginPolling(job.id);
}

dubbingToggle.addEventListener("change", () => {
  const job = selectedJob();
  if (job && voiceCatalog) queue.setDubbing(job.id, dubbingOptions(voiceCatalog, dubbingToggle.checked, job.dubbing.voiceId));
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
      option.textContent = key === "accent" ? `Miền ${value}` : key === "gender" ? `Giọng ${value.toLowerCase()}` : value[0].toUpperCase() + value.slice(1);
      select.append(option);
    }
  }
  if (lastModel) render(lastModel);
}).catch((error) => {
  voiceCatalogError = `Không đọc được danh mục giọng: ${String(error).slice(0, 160)}`;
  renderVoiceControls(selectedJob());
});

void invoke<ReleaseInfo>("release_info")
  .then((info) => {
    $("#release-version").textContent = `v${info.version}`;
    $("#release-channel").textContent = info.channel === "stable" ? "" : info.channel;
  })
  .catch(() => {
    $("#release-version").textContent = "development";
  });

// Native drag & drop: Tauri intercepts file drops and reports real paths.
if (isTauri) {
  type DropEvent = { payload: { type: "enter" | "over" | "drop" | "leave"; paths?: string[] } };
  void getCurrentWebview().onDragDropEvent((event: DropEvent) => {
    const overlay = $("#drag-overlay");
      if (event.payload.type === "enter" || event.payload.type === "over") {
        overlay.hidden = false;
        dropZone.classList.add("hover");
      } else {
        overlay.hidden = true;
        dropZone.classList.remove("hover");
        if (event.payload.type === "drop") addPaths(event.payload.paths ?? []);
      }
  }).catch(() => undefined);
}

window.addEventListener("beforeunload", () => shell.dispose());
