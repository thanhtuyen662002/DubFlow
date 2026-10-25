import { invoke as tauriInvoke } from "@tauri-apps/api/core";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { connectDesktopShell, type DesktopShellModel } from "./shell/desktop_shell.ts";
import { previewInvoke } from "./shell/preview_invoke.ts";
import { QueueController, type QueueJob, type SnapshotStorage } from "./features/queue/model.ts";
import { mountDiscovery } from "./features/discovery/view.ts";
import { extractLinks } from "./features/links/extract.ts";
import { DEFAULT_REGION, describeRegions, dragRegion, isBlurRegionList, type BlurRegion, type DragHandle } from "./features/blur/region.ts";
import { mountPostSuggestions } from "./features/seo/view.ts";
import { ReviewDraftHistory } from "./features/review/history.ts";
import { dubbingOptions, filterVoices, parseVoiceCatalog, type VoiceCatalog, type VoiceChoice } from "./features/voices/model.ts";
import {
  activeStageIndex, displayJobStatus, isCancelled, PIPELINE_STAGES, progressFraction, stateLabel,
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
type FetchStatus = {
  scan_id: string; url: string; provider: string; state: "preparing" | "downloading" | "done" | "failed";
  downloaded_bytes: number; total_bytes: number | null; media_path: string | null; error: string | null;
};
type FetchEntry = { status: FetchStatus; outputDir: string; timer: number };
const fetches = new Map<string, FetchEntry>();

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
const burnInToggle = $<HTMLInputElement>("#burn-in");
const sourceLanguageSelect = $<HTMLSelectElement>("#source-language");
const translationEngineSelect = $<HTMLSelectElement>("#translation-engine");
const blurModeSelect = $<HTMLSelectElement>("#blur-mode");
const runAllButton = $<HTMLButtonElement>("#run-all");
let batchRunning = false;
let blurJobId: string | null = null;
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

function tone(state: JobStatusState, cancelled = false): string {
  if (cancelled) return "";
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
    if (job && voiceCatalog) {
      queue.setDubbing(job.id, dubbingOptions(voiceCatalog, true, voice.voice_id));
      rememberDefaults(selectedJob() ?? job);
    }
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
  burnInToggle.checked = job?.burnIn ?? true;
  const blurMode = job?.blurAuto ? "auto" : job?.blurRegions?.length ? "manual" : "off";
  // "Thủ công" stays selected while the user has not drawn a region yet.
  if (blurModeSelect.value !== "manual" || blurMode !== "off" || blurJobId === null) blurModeSelect.value = blurMode;
  $("#blur-summary").textContent = blurMode === "auto" ? "Tự dò phụ đề và làm mờ khi nó xuất hiện" : describeRegions(job?.blurRegions);
  $("#blur-manual-row").hidden = blurModeSelect.value !== "manual";
  sourceLanguageSelect.value = job?.sourceLanguage ?? "";
  translationEngineSelect.value = job?.translationEngine ?? "argos";
  // Hachimi only translates Chinese, so the choice is moot for English sources.
  $("#translator-row").hidden = job?.sourceLanguage === "en";
  $("#hachimi-warning").hidden = job?.translationEngine !== "hachimi" || job?.sourceLanguage === "en";
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
  dropZone.hidden = hasJobs || fetches.size > 0;
  queueList.hidden = !hasJobs;
  $("#clear-queue").hidden = !hasJobs;
  renderRunAll();
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
    if (row.revision) meta.append(element("span", "mini-tag muted", "Bản sửa"));
    if (row.fromLink) meta.append(element("span", "mini-tag muted", "Từ link"));
    meta.append(element("span", "msg", row.state === "QUEUED" ? row.origin : row.status));
    main.append(element("span", "job-title", row.title), meta);
    if (row.state !== "QUEUED") main.append(progressBar(row.fraction, row.state));
    const side = element("div", "job-side");
    side.append(element("span", `badge ${tone(row.state, row.cancelled)}`, row.stateLabel));
    if (row.state !== "QUEUED") side.append(element("span", "percent", row.progress));
    item.append(thumb, main, side);
    item.title = row.origin;
    const select = () => queue.selectJob(row.id);
    item.addEventListener("click", select);
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") select();
    });
    queueList.append(item);
  }
}

function duration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const hours = Math.floor(total / 3600), minutes = Math.floor((total % 3600) / 60), seconds = total % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}` : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/** Elapsed time, and a rough remaining estimate from the pipeline fraction. */
function timingLabel(job: QueueJob, fraction: number | null): string {
  if (!job.startedAt) return "";
  const start = Date.parse(job.startedAt);
  if (job.finishedAt) return `${job.status.state === "COMPLETED" ? "Hoàn tất" : "Dừng"} sau ${duration(Date.parse(job.finishedAt) - start)}`;
  if (!isActive(job)) return "";
  const elapsed = Date.now() - start;
  // Early stages (model download, ASR warm-up) say little about the rest.
  const remaining = fraction !== null && fraction >= 0.15 && fraction < 1 ? elapsed * (1 - fraction) / fraction : null;
  return `Đã chạy ${duration(elapsed)}` + (remaining === null ? "" : ` · còn khoảng ${remaining < 60_000 ? "dưới 1 phút" : `${Math.round(remaining / 60_000)} phút`}`);
}

function renderInspector(job: QueueJob | null): void {
  inspector.hidden = !job;
  if (!job) return;
  const { status } = job;
  $("#detail-name").textContent = job.displayName;
  $("#detail-path").textContent = job.originUrl ?? job.sourcePath;
  const badge = $("#detail-state");
  badge.className = `badge ${tone(status.state, isCancelled(status))}`;
  badge.textContent = stateLabel(status.state, status.reason);
  const fraction = progressFraction(status);
  $("#detail-percent").textContent = status.state === "QUEUED" || fraction === null ? "" : `${Math.floor(fraction * 100)}%`;
  const bar = $("#detail-bar");
  bar.style.width = `${Math.round((fraction ?? 0) * 100)}%`;
  bar.parentElement!.className = `bar ${status.state === "COMPLETED" ? "ok" : status.state === "FAILED" ? "err" : ""}`;
  bar.parentElement!.hidden = status.state === "QUEUED";
  $("#detail-timing").textContent = timingLabel(job, fraction);
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
  // Partial dubbing keeps the original voice for some lines; say so and point
  // to the review dialog, whose filter starts on those lines.
  const warning = $("#result-warning");
  warning.hidden = !(output && status.reason === "completed_partial_dubbing");
  warning.textContent = warning.hidden ? "" : "Một số câu chưa lồng tiếng được (vẫn là tiếng gốc), thường vì câu dịch quá dài. Bấm “Xem & sửa bản dịch” để rút gọn rồi xử lý lại.";
  $("#open-review").classList.toggle("primary", !warning.hidden);
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

// The last options the user chose become the defaults for newly added videos,
// so a batch needs its dubbing/voice/burn-in set once (per-viewer convenience).
const DEFAULTS_KEY = "dubflow.defaults.v1";
type JobDefaults = { dubbing: boolean; voiceId: string | null; burnIn: boolean; sourceLanguage?: "zh" | "en"; translationEngine?: "hachimi"; blurRegions?: BlurRegion[]; blurAuto?: true };

function readDefaults(): JobDefaults | null {
  try {
    const value = JSON.parse(window.localStorage.getItem(DEFAULTS_KEY) ?? "null") as Partial<JobDefaults> | null;
    if (!value || typeof value.dubbing !== "boolean" || typeof value.burnIn !== "boolean") return null;
    return { dubbing: value.dubbing, burnIn: value.burnIn, voiceId: typeof value.voiceId === "string" ? value.voiceId : null,
      sourceLanguage: value.sourceLanguage === "zh" || value.sourceLanguage === "en" ? value.sourceLanguage : undefined,
      translationEngine: value.translationEngine === "hachimi" ? "hachimi" : undefined,
      blurRegions: isBlurRegionList(value.blurRegions) ? value.blurRegions : undefined,
      blurAuto: value.blurAuto === true ? true : undefined };
  } catch {
    return null;
  }
}

function rememberDefaults(job: QueueJob): void {
  try {
    window.localStorage.setItem(DEFAULTS_KEY, JSON.stringify({ dubbing: job.dubbing.enabled, voiceId: job.dubbing.voiceId,
      burnIn: job.burnIn ?? true, sourceLanguage: job.sourceLanguage, translationEngine: job.translationEngine,
      blurRegions: job.blurRegions, blurAuto: job.blurAuto }));
  } catch {
    // Defaults are a convenience; the queue works without storage.
  }
}

function applyDefaults(jobIds: readonly string[]): void {
  const defaults = readDefaults();
  if (!defaults) return;
  for (const id of jobIds) {
    try {
      if (!defaults.burnIn) queue.setBurnIn(id, false);
      if (defaults.sourceLanguage) queue.setSourceLanguage(id, defaults.sourceLanguage);
      if (defaults.blurRegions) queue.setBlurRegions(id, defaults.blurRegions);
      if (defaults.blurAuto) queue.setBlurAuto(id, true);
      if (defaults.translationEngine) queue.setTranslationEngine(id, defaults.translationEngine);
      if (defaults.dubbing && voiceCatalog) {
        const known = voiceCatalog.voices.some((voice) => voice.voice_id === defaults.voiceId);
        queue.setDubbing(id, dubbingOptions(voiceCatalog, true, known ? defaults.voiceId : null));
      }
    } catch {
      // A job that already started keeps its own options.
    }
  }
}

function addPaths(paths: string[]): void {
  const videos = paths.filter((path) => VIDEO_EXTENSIONS.test(path));
  if (paths.length && !videos.length) {
    showError("Chỉ hỗ trợ file video MP4, MKV, MOV, AVI, WEBM, M4V.");
    return;
  }
  showError("");
  const added = queue.addPaths(videos);
  applyDefaults(added);
  if (added.length && !selectedJob()) queue.selectJob(added[0]);
  else if (added.length) queue.selectJob(added[added.length - 1]);
}

/** Dropped files and folders: the host expands folders to the videos inside. */
async function addDropped(paths: string[]): Promise<void> {
  if (!paths.length) return;
  try {
    const videos = await invoke<string[]>("expand_video_paths", { paths });
    if (!videos.length) {
      showError("Không tìm thấy video MP4, MKV, MOV, AVI, WEBM, M4V trong mục đã thả.");
      return;
    }
    addPaths(videos);
  } catch (error) {
    showError(`Không đọc được mục đã thả: ${String(error).slice(0, 160)}`);
  }
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

// Batch: start queued videos one at a time (each job keeps its own options),
// so a long queue never runs several CPU-heavy pipelines at once.
function runnableJobs(): QueueJob[] {
  return queue.getSnapshot().jobs.filter((job) => job.status.state === "QUEUED" && canStart(job));
}

function renderRunAll(): void {
  const waiting = runnableJobs().length;
  runAllButton.hidden = waiting < 2 && !batchRunning;
  runAllButton.textContent = batchRunning ? `Dừng hàng đợi (${waiting} chờ)` : `Xử lý tất cả (${waiting})`;
  runAllButton.title = batchRunning ? "Video đang chạy sẽ chạy xong; các video còn lại không tự bắt đầu" : "Xử lý lần lượt từng video đang chờ";
}

function pumpBatch(): void {
  if (!batchRunning) return;
  if (queue.getSnapshot().jobs.some((job) => isActive(job))) return;
  const next = runnableJobs()[0];
  if (!next) {
    batchRunning = false;
    renderRunAll();
    return;
  }
  void startJob(next);
}

runAllButton.addEventListener("click", () => {
  batchRunning = !batchRunning;
  renderRunAll();
  pumpBatch();
});

// Results folder: next to each source (default) or one chosen folder.
const OUTPUT_ROOT_KEY = "dubflow.outputRoot.v1";
function outputRoot(): string | null {
  try {
    return window.localStorage.getItem(OUTPUT_ROOT_KEY);
  } catch {
    return null;
  }
}

function renderOutputRoot(): void {
  const root = outputRoot();
  const label = $("#output-root-label");
  label.textContent = root ? root.split(/[\\/]/).filter(Boolean).slice(-2).join("\\") : "Cạnh video gốc";
  label.title = root ?? "Mỗi video lưu kết quả trong thư mục “DubFlow Output” cạnh file gốc";
  $("#reset-output-root").hidden = !root;
}

$("#choose-output-root").addEventListener("click", async () => {
  const folder = await invoke<string | null>("pick_folder").catch(() => null);
  if (!folder) return;
  try {
    window.localStorage.setItem(OUTPUT_ROOT_KEY, folder);
  } catch {
    showError("Không lưu được lựa chọn thư mục trên máy này.");
  }
  renderOutputRoot();
});
$("#reset-output-root").addEventListener("click", () => {
  try {
    window.localStorage.removeItem(OUTPUT_ROOT_KEY);
  } catch {
    // Nothing stored.
  }
  renderOutputRoot();
});
renderOutputRoot();

async function startJob(job: QueueJob): Promise<void> {
  if (!canStart(job)) return;
  startButton.disabled = true;
  queue.updateStatus(job.id, { ...job.status, state: "RUNNING", reason: "starting", message: "Đang khởi động xử lý" });
  try {
    const started = await invoke<{ output_dir: string } | null>("start_job", {
      jobId: job.id, sourcePath: job.sourcePath, outputDir: job.outputDir ?? null, outputRoot: job.outputDir ? null : outputRoot(),
      enableDubbing: job.dubbing.enabled, ttsVoiceId: job.dubbing.voiceId, burnIn: job.burnIn ?? true,
      sourceLanguage: job.sourceLanguage ?? "auto", subtitleBox: job.subtitleBox ?? false,
      translationEngine: job.translationEngine ?? "argos", blurRegions: job.blurRegions ?? null,
      blurAuto: job.blurAuto ?? false,
    });
    // Resumes must reuse the folder the first start chose, even if the
    // results-folder setting changes later.
    if (started?.output_dir) queue.pinOutputDir(job.id, started.output_dir);
    beginPolling(job.id);
  } catch (error) {
    queue.updateStatus(job.id, {
      ...job.status,
      state: "FAILED",
      reason: "supervisor_start_failed",
      message: String(error).slice(0, 400),
      resource: { ...job.status.resource, held: false },
    });
  }
}

startButton.addEventListener("click", () => {
  const selected = selectedJob();
  if (selected) void startJob(selected);
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

// Links: the host downloads through the supervisor's source service; the
// finished file joins the queue as a local job with its own output folder.
const fetchList = $("#fetch-list");
const PROVIDER_NAMES: Record<string, string> = { bilibili: "Bilibili", douyin: "Douyin", generic: "Link video" };

function linkName(url: string): string {
  try {
    const parsed = new URL(url);
    const tail = decodeURIComponent(parsed.pathname.split("/").filter(Boolean).at(-1) ?? "");
    return tail || parsed.hostname;
  } catch {
    return url;
  }
}

function megabytes(bytes: number): string {
  return (bytes / 1_048_576).toFixed(bytes < 10_485_760 ? 1 : 0);
}

function renderFetches(): void {
  fetchList.replaceChildren();
  fetchList.hidden = fetches.size === 0;
  dropZone.hidden = fetches.size > 0 || queue.getSnapshot().jobs.length > 0;
  for (const { status } of fetches.values()) {
    const item = element("li", `job${status.state === "failed" ? " failed" : ""}`);
    const thumb = element("span", "job-thumb");
    thumb.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round"/></svg>`;
    const main = element("div", "job-main");
    const meta = element("div", "job-meta");
    meta.append(element("span", "mini-tag muted", PROVIDER_NAMES[status.provider] ?? status.provider));
    const message = status.state === "failed" ? status.error ?? "Tải link thất bại"
      : status.state === "preparing" ? "Đang phân tích link…"
      : status.total_bytes ? `Đang tải ${megabytes(status.downloaded_bytes)}/${megabytes(status.total_bytes)} MB`
      : `Đang tải ${megabytes(status.downloaded_bytes)} MB`;
    meta.append(element("span", `msg${status.state === "failed" ? " err" : ""}`, message));
    main.append(element("span", "job-title", linkName(status.url)), meta);
    if (status.state !== "failed") {
      const fraction = status.state === "downloading" && status.total_bytes ? status.downloaded_bytes / status.total_bytes : null;
      main.append(progressBar(fraction, "RUNNING"));
    }
    if (status.state === "failed" && status.provider === "douyin") main.append(douyinActions(status));
    const side = element("div", "job-side");
    if (status.state === "failed") {
      const dismiss = element("button", "icon-btn");
      dismiss.type = "button";
      dismiss.title = dismiss.ariaLabel = "Bỏ qua";
      dismiss.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`;
      dismiss.addEventListener("click", () => {
        fetches.delete(status.scan_id);
        renderFetches();
      });
      side.append(element("span", "badge err", "Lỗi"), dismiss);
    } else {
      side.append(element("span", "badge run", status.state === "preparing" ? "Đang phân tích" : "Đang tải"));
    }
    item.append(thumb, main, side);
    item.title = status.url;
    fetchList.append(item);
  }
}

function douyinActions(status: FetchStatus): HTMLElement {
  const actions = element("div", "fetch-actions");
  const open = element("button", "btn", "Mở Douyin");
  open.type = "button";
  open.addEventListener("click", () => {
    invoke("open_douyin_login").catch((error) => showError(String(error).slice(0, 200)));
  });
  const save = element("button", "btn primary", "Lưu phiên & thử lại");
  save.type = "button";
  save.addEventListener("click", async () => {
    save.disabled = open.disabled = true;
    save.textContent = "Đang lưu phiên…";
    try {
      await invoke<number>("save_douyin_session");
      fetches.delete(status.scan_id);
      renderFetches();
      await startFetch(status.url);
    } catch (error) {
      save.disabled = open.disabled = false;
      save.textContent = "Lưu phiên & thử lại";
      showError(String(error).slice(0, 300));
    }
  });
  actions.append(open, save);
  return actions;
}

// Sidebar navigation between the queue and the discovery view.
const discovery = mountDiscovery(invoke, startFetch, () => {
  try {
    return window.localStorage.getItem("dubflow.hachimiAck.v1") === "1" ? "hachimi" : "argos";
  } catch {
    return "argos";
  }
});
function showView(view: "queue" | "discover"): void {
  const discover = view === "discover";
  $("#discover-view").hidden = !discover;
  $("#queue-topbar").hidden = discover;
  $("#queue-content").hidden = discover;
  for (const [id, active] of [["#nav-queue", !discover], ["#nav-discover", discover]] as const) {
    $(id).classList.toggle("active", active);
    if (active) $(id).setAttribute("aria-current", "page");
    else $(id).removeAttribute("aria-current");
  }
  if (discover) discovery.show();
}
$("#nav-queue").addEventListener("click", (event) => {
  event.preventDefault();
  showView("queue");
});
$("#nav-discover").addEventListener("click", (event) => {
  event.preventDefault();
  showView("discover");
});

async function startFetch(url: string): Promise<void> {
  const started = await invoke<{ status: FetchStatus; output_dir: string }>("fetch_url", { url });
  const timer = window.setInterval(() => void pollFetch(started.status.scan_id), 1500);
  fetches.set(started.status.scan_id, { status: started.status, outputDir: started.output_dir, timer });
  renderFetches();
}

async function pollFetch(scanId: string): Promise<void> {
  const entry = fetches.get(scanId);
  if (!entry) return;
  try {
    const status = await invoke<FetchStatus | null>("fetch_status", { scanId });
    if (!status) return;
    entry.status = status;
    if (status.state === "done" && status.media_path) {
      window.clearInterval(entry.timer);
      fetches.delete(scanId);
      const linkJob = queue.addLinkJob(status.media_path, status.url, linkName(status.url), entry.outputDir);
      if (linkJob) applyDefaults([linkJob]);
    } else if (status.state === "failed") {
      window.clearInterval(entry.timer);
    }
  } catch (error) {
    window.clearInterval(entry.timer);
    entry.status = { ...entry.status, state: "failed", error: String(error).slice(0, 200) };
  }
  renderFetches();
}

$("#link-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $<HTMLInputElement>("#link-input");
  if (!input.value.trim()) return;
  // Several links (pasted lines or a share message) start one download each;
  // a bad link reports its own error and never blocks the others.
  const { links, tooLong, dropped } = extractLinks(input.value);
  if (links.length === 0) {
    showError(tooLong.length ? "Link quá dài (tối đa 2048 byte)." : "Không thấy link http(s) hợp lệ trong nội dung đã dán.");
    return;
  }
  const submit = $<HTMLButtonElement>("#link-submit");
  submit.disabled = true;
  const failures: string[] = [];
  for (const link of links) {
    try {
      await startFetch(link);
    } catch (error) {
      failures.push(`${link.slice(0, 60)}: ${String(error).slice(0, 120)}`);
    }
  }
  submit.disabled = false;
  input.value = failures.length ? input.value : "";
  const notes = [
    ...failures,
    ...(tooLong.length ? [`${tooLong.length} link quá dài bị bỏ qua.`] : []),
    ...(dropped ? [`Chỉ nhận tối đa 50 link mỗi lần; ${dropped} link chưa được thêm.`] : []),
  ];
  showError(notes.length ? `${links.length - failures.length}/${links.length} link đã bắt đầu tải. ${notes.join(" · ")}`.slice(0, 600) : "");
});
// Pasting several lines into the one-line box keeps the line breaks as spaces.
$<HTMLInputElement>("#link-input").addEventListener("paste", (event) => {
  const text = event.clipboardData?.getData("text") ?? "";
  if (!/[\r\n]/.test(text)) return;
  event.preventDefault();
  const input = event.currentTarget as HTMLInputElement;
  input.value = text.replace(/\s+/g, " ").trim();
});

// Review: edit Vietnamese text per cue, then re-run in the same output folder.
type ReviewCue = {
  cue_id: string; start_ms: number; end_ms: number; source_text: string;
  machine_text: string; text: string; edited: boolean; dub_failed: boolean;
};
// Estimate only: the built-in voice failed a 3.9 syllable/s cue in real runs
// while ~2.5 syllable/s cues fit, so warn above 3.5.
const FAST_SYLLABLES_PER_SECOND = 3.5;
const review = $("#review");
const reviewList = $("#review-list");
const reviewApply = $<HTMLButtonElement>("#review-apply");
const reviewUndo = $<HTMLButtonElement>("#review-undo");
const reviewOnlyIssues = $<HTMLInputElement>("#review-only-issues");
let reviewJobId: string | null = null;
let reviewCues: ReviewCue[] = [];
const reviewDrafts = new ReviewDraftHistory();
let reviewSaving = false;

function clock(ms: number): string {
  const total = Math.floor(ms / 100);
  const minutes = Math.floor(total / 600);
  const seconds = ((total % 600) / 10).toFixed(1).padStart(4, "0");
  return `${String(minutes).padStart(2, "0")}:${seconds}`;
}

function pace(text: string, cue: ReviewCue): number {
  const syllables = text.trim().split(/\s+/).filter(Boolean).length;
  return syllables / Math.max(0.5, (cue.end_ms - cue.start_ms) / 1000);
}

function draftOf(cue: ReviewCue): string {
  return reviewDrafts.draftOf(cue.cue_id);
}

function needsAttention(cue: ReviewCue): boolean {
  return cue.dub_failed || pace(draftOf(cue), cue) > FAST_SYLLABLES_PER_SECOND;
}

function reviewDirty(): boolean {
  return reviewDrafts.isDirty;
}

function updateReviewControls(): void {
  reviewApply.disabled = reviewSaving || !reviewDrafts.canSave;
  reviewUndo.disabled = reviewSaving || !reviewDrafts.canUndo;
}

function renderReviewMeta(cue: ReviewCue, meta: HTMLElement, item: HTMLElement): void {
  const text = draftOf(cue);
  const rate = pace(text, cue);
  meta.replaceChildren();
  if (cue.dub_failed) meta.append(element("span", "badge err", "Lồng tiếng lỗi"));
  if (text.trim() !== cue.machine_text.trim()) meta.append(element("span", "badge run", "Đã sửa"));
  const speed = element("span", `pace${rate > FAST_SYLLABLES_PER_SECOND ? " fast" : ""}`,
    `${rate.toFixed(1)} tiếng/giây${rate > FAST_SYLLABLES_PER_SECOND ? " · có thể quá nhanh, nên rút gọn" : ""}`);
  meta.append(speed);
  if (text.trim() !== cue.machine_text.trim()) {
    const revert = element("button", "revert", "Khôi phục bản máy dịch");
    revert.type = "button";
    revert.addEventListener("click", () => {
      reviewDrafts.replaceDraft(cue.cue_id, cue.machine_text);
      renderReview();
    });
    meta.append(revert);
  }
  item.classList.toggle("changed", text.trim() !== cue.text.trim());
}

function renderReview(): void {
  // Removing a focused editor need not fire blur (for example when filtering).
  reviewDrafts.endEdit();
  reviewList.replaceChildren();
  const visible = reviewCues.filter((cue) => !reviewOnlyIssues.checked || needsAttention(cue));
  const failed = reviewCues.filter((cue) => cue.dub_failed).length;
  const fast = reviewCues.filter((cue) => pace(draftOf(cue), cue) > FAST_SYLLABLES_PER_SECOND).length;
  $("#review-summary").textContent = [`${reviewCues.length} câu`, failed ? `${failed} câu lồng tiếng lỗi` : "",
    fast ? `${fast} câu đọc quá nhanh` : ""].filter(Boolean).join(" · ");
  if (!visible.length) reviewList.append(element("li", "review-empty", "Không có câu nào cần xem."));
  for (const cue of visible) {
    const item = element("li", "review-cue");
    const time = element("div", "review-time");
    time.append(element("b", "", clock(cue.start_ms)), document.createTextNode(`${((cue.end_ms - cue.start_ms) / 1000).toFixed(1)} giây`));
    const body = element("div");
    const editor = document.createElement("textarea");
    editor.value = draftOf(cue);
    editor.maxLength = 2000;
    editor.rows = Math.min(4, Math.max(1, Math.ceil(editor.value.length / 90)));
    editor.setAttribute("aria-label", `Câu lúc ${clock(cue.start_ms)}`);
    const meta = element("div", "review-meta");
    editor.addEventListener("focus", () => reviewDrafts.beginEdit(cue.cue_id));
    editor.addEventListener("blur", () => {
      reviewDrafts.endEdit(cue.cue_id);
      updateReviewControls();
    });
    editor.addEventListener("input", () => {
      reviewDrafts.updateDraft(cue.cue_id, editor.value);
      renderReviewMeta(cue, meta, item);
      updateReviewControls();
    });
    const listen = element("div", "review-listen");
    for (const [label, source] of [["▶ Gốc", true], ["▶ Lồng tiếng", false]] as const) {
      const play = element("button", "btn", label);
      play.type = "button";
      play.addEventListener("click", () => void playClip(cue, source, play));
      listen.append(play);
    }
    body.append(element("p", "review-source", cue.source_text), editor, meta, listen);
    item.append(time, body);
    renderReviewMeta(cue, meta, item);
    reviewList.append(item);
  }
  updateReviewControls();
}

async function openReview(): Promise<void> {
  const job = selectedJob();
  if (!job?.outputPath) return;
  try {
    reviewCues = await invoke<ReviewCue[]>("review_cues", { outputPath: job.outputPath });
  } catch (error) {
    showError(`Không mở được bản dịch: ${String(error).slice(0, 200)}`);
    return;
  }
  reviewJobId = job.id;
  reviewDrafts.reset(reviewCues);
  reviewOnlyIssues.checked = reviewCues.some(needsAttention);
  review.hidden = false;
  renderReview();
  reviewList.querySelector("textarea")?.focus();
}

let clipPlayer: HTMLAudioElement | null = null;

async function playClip(cue: ReviewCue, source: boolean, button: HTMLButtonElement): Promise<void> {
  const job = queue.getSnapshot().jobs.find((candidate) => candidate.id === reviewJobId);
  if (!job?.outputPath) return;
  clipPlayer?.pause();
  button.disabled = true;
  try {
    const bytes = await invoke<ArrayBuffer>("review_clip", {
      outputPath: job.outputPath, startMs: cue.start_ms, endMs: Math.min(cue.end_ms, cue.start_ms + 30_000), source,
    });
    const url = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
    clipPlayer = new Audio(url);
    clipPlayer.addEventListener("ended", () => URL.revokeObjectURL(url), { once: true });
    await clipPlayer.play();
  } catch (error) {
    $("#review-hint").textContent = `Không phát được đoạn này: ${String(error).slice(0, 160)}`;
  } finally {
    button.disabled = false;
  }
}

function closeReview(): void {
  clipPlayer?.pause();
  if (reviewDirty() && !window.confirm("Bỏ các thay đổi chưa lưu?")) return;
  review.hidden = true;
  reviewJobId = null;
  reviewDrafts.reset(reviewCues);
  updateReviewControls();
}

function undoReview(): void {
  if (reviewSaving || reviewDrafts.undo() === null) return;
  renderReview();
}

reviewApply.addEventListener("click", async () => {
  const job = queue.getSnapshot().jobs.find((candidate) => candidate.id === reviewJobId);
  if (!job?.outputPath) return;
  const overrides: Record<string, string> = {};
  for (const cue of reviewCues) {
    const text = draftOf(cue).trim();
    if (text !== cue.machine_text.trim()) overrides[cue.cue_id] = text;
  }
  reviewSaving = true;
  updateReviewControls();
  try {
    const outputDir = await invoke<string>("save_review", { outputPath: job.outputPath, overrides });
    review.hidden = true;
    reviewJobId = null;
    reviewDrafts.reset(reviewCues);
    const revisionId = queue.reviseJob(job.id, outputDir);
    const revision = queue.getSnapshot().jobs.find((candidate) => candidate.id === revisionId);
    if (revision) await startJob(revision);
  } catch (error) {
    $("#review-hint").textContent = `Không lưu được: ${String(error).slice(0, 200)}`;
  } finally {
    reviewSaving = false;
    updateReviewControls();
  }
});
$("#open-review").addEventListener("click", () => void openReview());
const postSuggestions = mountPostSuggestions(invoke);
$("#open-post").addEventListener("click", () => {
  const job = selectedJob();
  if (job?.outputPath) postSuggestions.open(job.outputPath, job.displayName);
});
$("#review-close").addEventListener("click", closeReview);
$("#review-cancel").addEventListener("click", closeReview);
reviewUndo.addEventListener("click", undoReview);
reviewOnlyIssues.addEventListener("change", renderReview);
review.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeReview();
  if (event.ctrlKey && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "z"
    && !(event.target instanceof HTMLTextAreaElement)) {
    event.preventDefault();
    undoReview();
  }
});

// Tell the user in the taskbar title when a video finishes while they are away.
const lastStates = new Map<string, JobStatusState>();
const baseTitle = document.title;
window.addEventListener("focus", () => { document.title = baseTitle; });

queue.subscribe((snapshot) => {
  for (const job of snapshot.jobs) {
    const previous = lastStates.get(job.id);
    if (previous && ACTIVE.includes(previous) && (job.status.state === "COMPLETED" || job.status.state === "FAILED") && !document.hasFocus()) {
      document.title = `${job.status.state === "COMPLETED" ? "✓ Xong" : "✕ Lỗi"}: ${job.displayName} — ${baseTitle}`;
    }
    lastStates.set(job.id, job.status.state);
  }
  if (batchRunning && !snapshot.jobs.some((job) => ACTIVE.includes(job.status.state))) window.setTimeout(pumpBatch, 0);
  const selected = snapshot.jobs.find((job) => job.id === snapshot.selected_job_id);
  if (selected && ["RUNNING", "RECOVERED", "WORKER_LOST"].includes(selected.status.state)) beginPolling(selected.id);
});
for (const job of queue.getSnapshot().jobs) {
  if (!["QUEUED", "COMPLETED", "FAILED"].includes(job.status.state)) beginPolling(job.id);
}

sourceLanguageSelect.addEventListener("change", () => {
  const job = selectedJob();
  if (!job) return;
  const value = sourceLanguageSelect.value;
  queue.setSourceLanguage(job.id, value === "zh" || value === "en" ? value : null);
  rememberDefaults(selectedJob() ?? job);
});
// Ask once per viewer before the first opt-in: the model's licence terms are unverified.
const HACHIMI_ACK_KEY = "dubflow.hachimiAck.v1";
function hachimiAcknowledged(): boolean {
  try {
    if (window.localStorage.getItem(HACHIMI_ACK_KEY) === "1") return true;
  } catch {
    // Without storage, ask every time.
  }
  const accepted = window.confirm("HachimiMT-60 là bộ dịch thử nghiệm. Giấy phép chưa được xác minh cho mục đích thương mại "
    + "(model được huấn luyện có dữ liệu chưng cất từ Gemini và một bộ dữ liệu GPL-3.0). Chỉ dùng cá nhân/thử nghiệm và tự kiểm tra điều khoản trước khi dùng thương mại.\n\n"
    + "Lần đầu dùng sẽ tải model khoảng 58 MB. Tiếp tục?");
  if (accepted) {
    try {
      window.localStorage.setItem(HACHIMI_ACK_KEY, "1");
    } catch {
      // Acknowledgement is a convenience; asking again is safe.
    }
  }
  return accepted;
}
translationEngineSelect.addEventListener("change", () => {
  const job = selectedJob();
  if (!job) return;
  const engine = translationEngineSelect.value === "hachimi" ? "hachimi" : "argos";
  if (engine === "hachimi" && !hachimiAcknowledged()) {
    translationEngineSelect.value = job.translationEngine ?? "argos";
    return;
  }
  queue.setTranslationEngine(job.id, engine);
  rememberDefaults(selectedJob() ?? job);
});
// Blur editor: drag a rectangle over a frame of the video to hide its burned-in captions.
const blurDialog = $("#blur-dialog");
const blurStage = $("#blur-stage");
const blurFrame = $<HTMLImageElement>("#blur-frame");
const blurRect = $("#blur-rect");
const blurTime = $<HTMLInputElement>("#blur-time");
const blurLoading = $("#blur-loading");
let blurDraft: BlurRegion = DEFAULT_REGION;
let blurFrameUrl: string | null = null;
let blurFrameRequest = 0;
let blurDrag: { handle: DragHandle; start: BlurRegion; x: number; y: number } | null = null;

function renderBlurRect(): void {
  blurRect.style.left = `${blurDraft.x * 100}%`;
  blurRect.style.top = `${blurDraft.y * 100}%`;
  blurRect.style.width = `${blurDraft.w * 100}%`;
  blurRect.style.height = `${blurDraft.h * 100}%`;
  blurRect.setAttribute("aria-valuetext", describeRegions([blurDraft]));
}

async function loadBlurFrame(): Promise<void> {
  const job = queue.getSnapshot().jobs.find((candidate) => candidate.id === blurJobId);
  if (!job) return;
  const request = ++blurFrameRequest;
  blurLoading.hidden = false;
  blurLoading.textContent = "Đang lấy khung hình…";
  try {
    const bytes = await invoke<ArrayBuffer>("video_frame", { sourcePath: job.sourcePath, position: Number(blurTime.value) / 100 });
    if (request !== blurFrameRequest) return;
    if (blurFrameUrl) URL.revokeObjectURL(blurFrameUrl);
    blurFrameUrl = URL.createObjectURL(new Blob([bytes]));
    blurFrame.src = blurFrameUrl;
    await blurFrame.decode().catch(() => undefined);
    if (request === blurFrameRequest) blurLoading.hidden = true;
  } catch (error) {
    if (request === blurFrameRequest) blurLoading.textContent = `Không lấy được khung hình: ${String(error).slice(0, 160)}`;
  }
}

function openBlurEditor(): void {
  const job = selectedJob();
  if (!job || job.status.state !== "QUEUED") return;
  blurJobId = job.id;
  blurDraft = job.blurRegions?.[0] ?? readDefaults()?.blurRegions?.[0] ?? DEFAULT_REGION;
  renderBlurRect();
  blurDialog.hidden = false;
  blurRect.focus();
  void loadBlurFrame();
}

function closeBlurEditor(): void {
  blurDialog.hidden = true;
  blurJobId = null;
  // Cancelling a first manual pick returns the mode select to what the job has.
  renderVoiceControls(selectedJob());
  blurFrameRequest += 1;
  blurFrame.removeAttribute("src");
  if (blurFrameUrl) URL.revokeObjectURL(blurFrameUrl);
  blurFrameUrl = null;
}

function applyBlur(regions: BlurRegion[] | null, everyQueued: boolean): void {
  const job = queue.getSnapshot().jobs.find((candidate) => candidate.id === blurJobId);
  if (!job) return closeBlurEditor();
  if (everyQueued) {
    const count = queue.setBlurRegionsForQueued(regions);
    $("#action-hint").textContent = `Đã áp dụng vùng làm mờ cho ${count} video đang chờ.`;
  } else {
    queue.setBlurRegions(job.id, regions);
  }
  rememberDefaults(queue.getSnapshot().jobs.find((candidate) => candidate.id === job.id) ?? job);
  closeBlurEditor();
}

blurRect.addEventListener("pointerdown", (event) => {
  const handle = (event.target as HTMLElement).dataset.handle as DragHandle | undefined;
  if (!handle || event.button !== 0) return;
  event.preventDefault();
  blurRect.setPointerCapture(event.pointerId);
  blurDrag = { handle, start: blurDraft, x: event.clientX, y: event.clientY };
});
blurRect.addEventListener("pointermove", (event) => {
  if (!blurDrag) return;
  const box = blurStage.getBoundingClientRect();
  blurDraft = dragRegion(blurDrag.start, blurDrag.handle, (event.clientX - blurDrag.x) / box.width, (event.clientY - blurDrag.y) / box.height);
  renderBlurRect();
});
const endBlurDrag = (): void => { blurDrag = null; };
blurRect.addEventListener("pointerup", endBlurDrag);
blurRect.addEventListener("pointercancel", endBlurDrag);
blurRect.addEventListener("keydown", (event) => {
  const step = 0.01;
  const moves: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
  const move = moves[event.key];
  if (!move) return;
  event.preventDefault();
  // Shift resizes from the bottom-right corner; plain arrows move the whole box.
  blurDraft = dragRegion(blurDraft, event.shiftKey ? "se" : "move", move[0], move[1]);
  renderBlurRect();
});
blurTime.addEventListener("change", () => void loadBlurFrame());
$("#blur-edit").addEventListener("click", openBlurEditor);
blurModeSelect.addEventListener("change", () => {
  const job = selectedJob();
  if (!job) return;
  if (blurModeSelect.value === "auto") {
    queue.setBlurAuto(job.id, true);
  } else if (blurModeSelect.value === "off") {
    queue.setBlurAuto(job.id, false);
    queue.setBlurRegions(job.id, null);
  } else {
    openBlurEditor();
    queue.setBlurAuto(job.id, false);
  }
  rememberDefaults(selectedJob() ?? job);
});
$("#blur-close").addEventListener("click", closeBlurEditor);
$("#blur-apply").addEventListener("click", () => applyBlur([blurDraft], false));
$("#blur-all").addEventListener("click", () => applyBlur([blurDraft], true));
$("#blur-off").addEventListener("click", () => applyBlur(null, false));
blurDialog.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeBlurEditor();
});
burnInToggle.addEventListener("change", () => {
  const job = selectedJob();
  if (job) {
    queue.setBurnIn(job.id, burnInToggle.checked);
    rememberDefaults(selectedJob() ?? job);
  }
});
dubbingToggle.addEventListener("change", () => {
  const job = selectedJob();
  if (job && voiceCatalog) {
    queue.setDubbing(job.id, dubbingOptions(voiceCatalog, dubbingToggle.checked, job.dubbing.voiceId));
    rememberDefaults(selectedJob() ?? job);
  }
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
        if (event.payload.type === "drop") void addDropped(event.payload.paths ?? []);
      }
  }).catch(() => undefined);
}

window.addEventListener("beforeunload", () => shell.dispose());
