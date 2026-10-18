import { invoke } from "@tauri-apps/api/core";
import { connectDesktopShell } from "./shell/desktop_shell.ts";
import { QueueController, type QueueJob, type SnapshotStorage } from "./features/queue/model.ts";
import { dubbingOptions, filterVoices, parseVoiceCatalog, voiceLabel, type VoiceCatalog } from "./features/voices/model.ts";
import type { JobStatus } from "./features/job_status/status.ts";
import { mountDesktopReview } from "./features/review/host.ts";
import { nativeReviewTransport } from "./features/review/transport.ts";
import { importPackMessage, parseImportPackStatus, type ImportPackStatus } from "./features/import_pack/model.ts";
import { createSourceServiceController, type SourcePhase, type SourceServiceState } from "./features/source_intake/service_controller.ts";
import { cancellationMessage, inputProblemMessage } from "./features/source_intake/ui_messages.ts";

type ReleaseInfo = { version: string; channel: string; backend: string };

const STORAGE_KEY = "dubflow.queue.v2";

function showQueueStorageWarning(): void {
  const notice = document.querySelector<HTMLElement>("#queue-storage-status");
  if (notice) {
    notice.hidden = false;
    notice.textContent = "Chưa lưu được hàng đợi để mở lại. Bạn vẫn có thể xử lý trong phiên này; hãy giữ ứng dụng mở và lưu kết quả đã xuất.";
  }
}

function browserStorage(): SnapshotStorage {
  return {
    read: () => {
      try {
        return window.localStorage.getItem(STORAGE_KEY);
      } catch {
        showQueueStorageWarning();
        return null;
      }
    },
    write: (value) => {
      try {
        window.localStorage.setItem(STORAGE_KEY, value);
      } catch {
        // Preserve current-session local use while making reopen limits visible.
        showQueueStorageWarning();
      }
    },
  };
}

type StatusResponse = { status: JobStatus; output_path: string | null };

const queue = new QueueController(browserStorage());
const pollers = new Map<string, number>();
// Verification is session-local: persisted paths never imply valid file hashes.
const importPacks = new Map<string, ImportPackStatus>();
const packChecks = new Set<string>();

const $ = <T extends HTMLElement>(selector: string): T => {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Missing desktop element: ${selector}`);
  return element;
};

const summary = $("#queue-summary");
const queueState = $("#queue-state");
const queueList = $("#queue-list");
const detail = $("#job-detail");
const reviewContainer = document.createElement("section");
reviewContainer.hidden = true;
reviewContainer.className = "desktop-review-panel";
$(".workspace").after(reviewContainer);
const reviewPanel = mountDesktopReview(reviewContainer, nativeReviewTransport(invoke));
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
  return !!job && ["QUEUED", "RECOVERED"].includes(job.status.state) &&
    (!job.dubbing.enabled || !!voiceCatalog?.voices.some((voice) => voice.voice_id === job.dubbing.voiceId));
}

function canCreateNewVersion(job: QueueJob | undefined): boolean {
  return !!job && ["COMPLETED", "FAILED", "BLOCKED_NEEDS_ACTION"].includes(job.status.state);
}

function renderVoiceControls(job: QueueJob | undefined): void {
  if (voiceJobId !== (job?.id ?? null)) {
    genderFilter.value = accentFilter.value = "";
    styleFilter.value = voiceCatalog?.voices.some((voice) => voice.style === "tự nhiên") ? "tự nhiên" : "";
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
  reviewPanel.select(view.selected ? { id: view.selected.id, completed: view.selected.status.state === "COMPLETED" } : null);
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
  if (view.selected.status.state === "COMPLETED") {
    const status = importPacks.get(view.selected.id);
    const term = document.createElement("dt");
    term.textContent = "Gói chỉnh sửa";
    const path = document.createElement("dd");
    path.textContent = importPackMessage(status);
    card.append(term, path);
    if (status?.state === "available" && status.capcutReason === "CAPCUT_VERSION_UNSUPPORTED") {
      const advisory = document.createElement("p");
      advisory.textContent = "Phiên bản CapCut này chưa hỗ trợ mở dự án trực tiếp. Bạn có thể nhập các file trong gói chỉnh sửa.";
      detail.append(advisory);
    }
    const check = document.createElement("button");
    check.type = "button";
    check.textContent = "Kiểm tra lại gói";
    check.disabled = packChecks.has(view.selected.id);
    const jobId = view.selected.id;
    check.addEventListener("click", () => void refreshImportPack(jobId, true));
    detail.append(check);
  }
}

const shell = connectDesktopShell(queue, render);

async function refreshImportPack(jobId: string, force = false): Promise<void> {
  if (packChecks.has(jobId) || (!force && importPacks.has(jobId))) return;
  packChecks.add(jobId);
  importPacks.set(jobId, { state: "checking" });
  const redraw = () => {
    const current = queue.getSnapshot().jobs.find((job) => job.id === jobId);
    if (current) queue.updateStatus(jobId, current.status, current.outputPath);
  };
  redraw();
  try {
    importPacks.set(jobId, parseImportPackStatus(await invoke<unknown>("import_pack_status", { jobId })));
  } catch {
    importPacks.set(jobId, { state: "unavailable", reason: "IMPORT_PACK_VALIDATION_FAILED" });
  } finally {
    packChecks.delete(jobId);
    redraw();
  }
}

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
      if (response.status.state === "COMPLETED") void refreshImportPack(jobId);
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

const newVersionButton = $("#new-processing-version") as HTMLButtonElement;
newVersionButton.addEventListener("click", () => {
  const selected = selectedJob();
  if (!canCreateNewVersion(selected) || !selected) return;
  try {
    queue.retryJob(selected.id);
  } catch (error) {
    $("#queue-state").textContent = `Không tạo được bản xử lý mới: ${String(error).slice(0, 180)}`;
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
  newVersionButton.disabled = !canCreateNewVersion(selected);
  newVersionButton.hidden = !canCreateNewVersion(selected);
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
  reviewPanel.setVoices(voiceCatalog.voices.map(voice => ({ voice_id: voice.voice_id, label: voiceLabel(voice) })));
  for (const [select, key] of [[genderFilter, "gender"], [accentFilter, "accent"], [styleFilter, "style"]] as const) {
    for (const value of new Set(voiceCatalog.voices.map((voice) => voice[key]))) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = key === "accent" ? `Miền ${value}`
        : key === "style" && value === "tự nhiên" ? "Đối thoại phim · tự nhiên" : value;
      select.append(option);
    }
  }
  styleFilter.value = voiceCatalog.voices.some((voice) => voice.style === "tự nhiên") ? "tự nhiên" : "";
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

const sourceController = createSourceServiceController(queue, (command, args) => invoke(command, args));
const sourceInput = $<HTMLTextAreaElement>("#source-input");
const sourceList = $("#source-list");
const sourceFeedback = $("#source-feedback");
const sourceStop = $<HTMLButtonElement>("#source-stop");
const sourcePhases: Record<SourcePhase, string> = {
  queued: "Đang chờ tiếp nhận", sending: "Đang gửi yêu cầu", submitted: "Đã gửi yêu cầu, chờ nguồn xác nhận",
  preparing: "Đang chuẩn bị nguồn", enumerating: "Đang tìm video", enumerated: "Đã kiểm tra danh sách nguồn",
  download_sending: "Đang gửi yêu cầu tải", download_submitted: "Đã gửi yêu cầu tải, chờ xác nhận",
  download_preparing: "Đang chuẩn bị tải", downloading: "Đang tải video", downloaded: "Đã thêm video vào hàng xử lý",
  queue_error: "Video đã nhận nhưng chưa xác nhận thêm vào hàng xử lý", cancelling: "Đã gửi yêu cầu hủy, chờ xác nhận",
  cancelled: "Đã hủy nguồn này", paused: "Nguồn đã tạm dừng", failed: "Không tiếp nhận được nguồn", interrupted: "Nguồn bị gián đoạn",
};
function sourceProblem(code: string): string {
  const inputMessage = inputProblemMessage(code);
  if (inputMessage) return inputMessage;
  const messages: Record<string, string> = {
    SOURCE_PROVIDER_UNSUPPORTED: "Nhà cung cấp này chưa được hỗ trợ. Hãy thêm video đã lưu trên máy.",
    SOURCE_PUBLIC_REFERENCE_REQUIRED: "Cần URL công khai, không có thông tin đăng nhập.",
    SOURCE_RECONCILIATION_REQUIRED: "Nguồn có kết quả cũ cần được kiểm tra trước khi tải tiếp.",
    SOURCE_NO_ITEMS: "Không tìm thấy video trong nguồn này.",
    SOURCE_RECEIPT_UNEXPECTED: "Kết quả nguồn chưa khớp video đã chọn.",
    LOCAL_QUEUE_REJECTED: "Chưa thêm được video vào hàng xử lý. Hãy kiểm tra danh sách và dung lượng trống.",
    SOURCE_MATERIALIZED_INVALID: "Kết quả tải chưa đủ thông tin để xử lý. Các video khác vẫn dùng được.",
    DUPLICATE_SOURCE: "Nguồn này đã có trong danh sách.",
    SOURCE_SETUP_MISSING: "Nguồn trực tuyến chưa sẵn sàng. Hãy kiểm tra bản cài đặt; video trên máy vẫn dùng được.",
    SOURCE_SETUP_INVALID: "Cần kiểm tra lại bản cài đặt để dùng nguồn trực tuyến. Video trên máy vẫn dùng được.",
  };
  return messages[code] ?? "Nguồn chưa hoàn tất. Bạn có thể hủy hoặc dừng nguồn trực tuyến và tiếp tục với video trên máy.";
}
function renderSource(state: SourceServiceState): void {
  const labels = {
    idle: "Chưa mở nguồn trực tuyến.", admitting: "Đang chuẩn bị nguồn trực tuyến. Bạn vẫn có thể thêm video trên máy.",
    ready: "Sẵn sàng tiếp nhận nguồn. Video chỉ vào hàng xử lý sau khi tải xong.",
    stopping: "Đang dừng nguồn trực tuyến, chờ xác nhận.", unavailable: "Nguồn trực tuyến chưa sẵn sàng; video trên máy vẫn dùng được.",
    stopped: state.retired ? "Nguồn trực tuyến đã dừng. Mở lại ứng dụng nếu muốn tiếp tục; video trên máy vẫn dùng được."
      : "Chưa xác nhận nguồn trực tuyến đã dừng. Video trên máy vẫn dùng được.",
  };
  $("#source-service-status").textContent = state.failure ? sourceProblem(state.failure) : labels[state.service];
  sourceStop.disabled = ["idle", "stopped", "unavailable"].includes(state.service) || state.disposed;
  $("#source-rejections").replaceChildren(...state.rejected.map((item) => {
    const line = document.createElement("li");
    line.textContent = `${item.line ? `Dòng ${item.line}: ` : ""}${sourceProblem(item.code)}`;
    return line;
  }));
  sourceList.replaceChildren(...state.rows.map((row) => {
    const item = document.createElement("li");
    const title = document.createElement("p"); title.textContent = row.display_ref;
    const status = document.createElement("p"); status.className = "hint";
    const phase = row.phase === "cancelling" ? cancellationMessage(row.delivery)
      : row.phase === "enumerating" && row.enumeration_complete && !row.enumeration_retired
        ? "Đã tìm xong video, đang chờ nguồn kết thúc." : sourcePhases[row.phase];
    status.textContent = phase + (row.error ? `. ${sourceProblem(row.error)}` : "");
    item.append(title, status);
    if (row.phase === "enumerating" && row.enumeration_complete && !row.enumeration_retired) {
      const refresh = document.createElement("button"); refresh.type = "button"; refresh.textContent = "Kiểm tra trạng thái";
      refresh.disabled = state.service !== "ready" || state.disposed || row.refresh_pending || row.delivery === "pending";
      refresh.addEventListener("click", () => {
        refresh.disabled = true;
        void sourceController.refresh(row.scan_id).then((sent) => {
          if (!sent) sourceFeedback.textContent = "Chưa kiểm tra được trạng thái nguồn. Bạn vẫn có thể thêm video trên máy.";
        });
      });
      item.append(refresh);
    }
    if (row.phase === "enumerated" && !row.selected_identity) {
      for (const media of row.items.filter((value) => value.status === "discovered")) {
        const download = document.createElement("button"); download.type = "button"; download.textContent = "Tải video";
        download.disabled = state.service !== "ready";
        download.addEventListener("click", () => {
          download.disabled = true;
          void sourceController.download(row.scan_id, media.identity_key).then((sent) => {
            if (!sent) sourceFeedback.textContent = "Chưa gửi được yêu cầu tải. Hãy kiểm tra trạng thái nguồn.";
          });
        });
        item.append(download);
      }
    }
    if (!["cancelled", "paused", "failed", "interrupted", "downloaded", "queue_error"].includes(row.phase)) {
      const cancel = document.createElement("button"); cancel.type = "button"; cancel.textContent = "Hủy nguồn này";
      cancel.disabled = row.phase === "cancelling" || state.disposed;
      cancel.addEventListener("click", () => { void sourceController.cancel(row.scan_id); });
      item.append(cancel);
    }
    return item;
  }));
}
sourceController.subscribe(renderSource);
let sourceSubmission = 0;
$("#source-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = sourceInput.value;
  if (!input.trim()) return;
  const request = ++sourceSubmission;
  sourceInput.value = "";
  sourceFeedback.textContent = "Đang tiếp nhận. Bạn có thể thêm video trên máy trong lúc chờ.";
  void sourceController.submit(input).then((result) => {
    if (request !== sourceSubmission) return;
    sourceFeedback.textContent = `Đã thêm ${result.local_added} video trên máy; ${result.local_present} video đã có. ${result.online_staged.length} URL vào danh sách tiếp nhận.`;
  }).catch(() => {
    if (request === sourceSubmission) sourceFeedback.textContent = "Chưa tiếp nhận được nguồn. Bạn vẫn có thể dùng nút Thêm video.";
  });
});
sourceStop.addEventListener("click", () => { void sourceController.stop(); });
const sourcePoll = window.setInterval(() => { void sourceController.poll(); }, 1000);

window.addEventListener("beforeunload", () => {
  window.clearInterval(sourcePoll);
  void sourceController.dispose();
  reviewPanel.destroy(); shell.dispose();
});
