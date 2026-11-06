import { convertFileSrc, invoke as tauriInvoke } from "@tauri-apps/api/core";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { connectDesktopShell, type DesktopShellModel } from "./shell/desktop_shell.ts";
import { confirmDialog } from "./shell/confirm.ts";
import { mountLayoutPanels, refitPanels } from "./shell/layout_panels.ts";
import { PREVIEW_EDIT_LIBRARY, previewInvoke } from "./shell/preview_invoke.ts";
import { createVoiceSampler } from "./features/voices/sample.ts";
import { QueueController, type QueueJob, type SnapshotStorage } from "./features/queue/model.ts";
import {
  DEFAULTS_KEY, FIRST_RUN_DEFAULTS, LEGACY_DEFAULTS_KEY, readJobDefaults, serializeJobDefaults, type JobDefaults,
} from "./features/queue/defaults.ts";
import { matchesQueueFilter, type QueueFilter } from "./features/queue/view_model.ts";
import { mountDiscovery } from "./features/discovery/view.ts";
import { chineseKeyword } from "./features/discovery/ai.ts";
import { extractLinks } from "./features/links/extract.ts";
import { fetchBadge, fetchMessage, isDouyinLink, needsDouyinLogin, shortPath, type FetchStatus } from "./features/links/status.ts";
import { addSample, fractionDone, progressLine, type Sample } from "./features/fetch/progress.ts";
import { mountDouyinLogin } from "./features/douyin/view.ts";
import { mountUpdates } from "./features/update/view.ts";
import { mountStorage } from "./features/storage/view.ts";
import { queueOutputs } from "./features/storage/model.ts";
import { DEFAULT_REGION, describeRegions, dragRegion, type BlurRegion, type DragHandle } from "./features/blur/region.ts";
import { mountPostContent } from "./features/seo/posts.ts";
import { mountSourceCredit } from "./features/credit/view.ts";
import { mountPackage } from "./features/package/view.ts";
import { mountShorts } from "./features/shorts/view.ts";
import { mountReframe } from "./features/reframe/view.ts";
import { mountCompilation } from "./features/compilation/view.ts";
import { mountDocu } from "./features/docu/view.ts";
import { mountProductStudio } from "./features/product/view.ts";
import { shortDate } from "./features/product/model.ts";
import { jobTarget, mountEditor, type EditTarget, type ProxyState } from "./features/edit/view.ts";
import { mountEditLibrary } from "./features/edit/library_view.ts";
import { KIND_LABELS, distinctTitles, type LibraryEntry } from "./features/edit/library.ts";
import { createSfxLibrary } from "./features/sfx/library.ts";
import { mountSfxSettings } from "./features/sfx/settings.ts";
import { mountPerformanceSettings } from "./features/performance/settings.ts";
import { STYLE_STORAGE_KEY, storedStyle, type SubtitleStyle } from "./features/edit/subtitles.ts";
import { COVER_STORAGE_KEY, describeCoverStyle, normalizeCoverStyle, storedCoverStyle, type CoverMode, type CoverStyle } from "./features/edit/cover.ts";
import { configureHostAi, createAiSession, isKeyError, PROVIDERS } from "./features/ai/provider.ts";
import { loadPrefs, translationJobArgs } from "./features/ai/translation_prefs.ts";
import { rewriteAsNarration, type NarrationStyle } from "./features/narration/ai.ts";
import { hostKeyStore, mountAiSettings } from "./features/ai/settings.ts";
import { ReviewDraftHistory } from "./features/review/history.ts";
import {
  applySourceLanguage, chooseTranslator, effectiveTranslator, FAST_PHRASE_SYLLABLES_PER_SECOND, hachimiAllowed,
  phrasePaces, phraseSizes, reviewOverrides, saveReviewRevision, startJobRequest,
} from "./features/translation_review/flow.ts";
import { dubbingOptions, filterVoices, parseVoiceCatalog, type VoiceCatalog, type VoiceChoice } from "./features/voices/model.ts";
import {
  acceptsPolledStatus, activeStageIndex, displayJobStatus, isCancelled, PIPELINE_STAGES, progressFraction, stateLabel,
  type JobStatus, type JobStatusState,
} from "./features/job_status/status.ts";

type ReleaseInfo = { version: string; channel: string; backend: string };
type StatusResponse = { status: JobStatus; output_path: string | null };

const STORAGE_KEY = "dubflow.queue.v2";
const VIDEO_EXTENSIONS = /\.(mp4|mkv|mov|avi|webm|m4v|flv|ts|m2ts|mts|3gp|wmv|mpg|mpeg)$/i;
const FORMAT_LIST = "MP4, MKV, MOV, AVI, WEBM, M4V, FLV, TS, 3GP, WMV, MPG";
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
type FetchEntry = {
  status: FetchStatus; outputDir: string; timer: number;
  /** Bytes seen at each poll, for speed and time left. */
  samples: Sample[];
  /** Already retried once with a fresh Douyin session. */
  authRetried: boolean;
  /** Refreshing the Douyin session before that retry. */
  renewing: boolean;
  /** Finished; being copied to the download folder. */
  saving: boolean;
};
const fetches = new Map<string, FetchEntry>();

const $ = <T extends HTMLElement>(selector: string): T => {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Missing desktop element: ${selector}`);
  return element;
};

const queueList = $("#queue-list");
const fetchList = $("#fetch-list");
const dropZone = $("#drop-zone");
const queueState = $("#queue-state");
const inspector = $("#inspector");
const queueScreen = $("#queue-content");
const listToggle = $<HTMLButtonElement>("#list-toggle");
const playerVideo = $<HTMLVideoElement>("#player-video");
const playButton = $<HTMLButtonElement>("#player-play");
const sourceCredit = mountSourceCredit(invoke);
const packageVideo = mountPackage(invoke, { cpuOnly });
// One AI choice and saved keys, shared by every AI feature. The inspector
// sections below are used by the first render, so they are mounted up here.
const claudeKey = createAiSession(hostKeyStore(invoke));
// ChatGPT, DeepSeek, Qwen… are called through the host with the model chosen in Cài đặt AI.
configureHostAi(invoke, (provider) => claudeKey.options(provider));
/** Saved keys and model choices; a job started before they load waits for them. */
const aiRestored = claudeKey.restore().catch(() => undefined);
// The sound-effect pack and the user's sound folder (editor Âm thanh, Cài đặt).
const sfxLibrary = createSfxLibrary(invoke);
const shorts = mountShorts(invoke, claudeKey);
const postContent = mountPostContent(invoke, claudeKey);
const reframe = mountReframe(invoke, claudeKey);
const voiceControls = $<HTMLFieldSetElement>("#voice-controls");
const dubbingToggle = $<HTMLInputElement>("#enable-dubbing");
const multiVoiceToggle = $<HTMLInputElement>("#multi-voice");
const separateToggle = $<HTMLInputElement>("#separate-background");
const burnInToggle = $<HTMLInputElement>("#burn-in");
const sourceLanguageSelect = $<HTMLSelectElement>("#source-language");
const hachimiOption = $<HTMLOptionElement>('#translation-engine option[value="hachimi"]');
const hachimiNeedsChinese = document.createElement("small");
hachimiNeedsChinese.className = "hint";
hachimiNeedsChinese.textContent = "Muốn dùng HachimiMT-60, hãy chọn ngôn ngữ nguồn là Tiếng Trung (không dùng được với Tự động).";
hachimiNeedsChinese.hidden = true;
const translationEngineSelect = $<HTMLSelectElement>("#translation-engine");
translationEngineSelect.after(hachimiNeedsChinese);
const blurModeSelect = $<HTMLSelectElement>("#blur-mode");
const coverStyleSelect = $<HTMLSelectElement>("#cover-style");
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
let queueFilter: QueueFilter = "all";
/** A link still downloading (or failed) shown in the player instead of a job. */
let selectedFetchId: string | null = null;
type DetailTab = "result" | "setup";
let detailTab: DetailTab = "result";
/** The job and phase the current tab was chosen for; a new one picks the default tab again. */
let detailTabKey = "";
/** Durations the player has read, shown on the list thumbnails. */
const knownDurations = new Map<string, number>();

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

/** A job whose worker is gone holds nothing, so it can leave the list like a finished one. */
function isRemovable(job: QueueJob | undefined): boolean {
  return !!job && (!isActive(job) || job.status.state === "WORKER_LOST");
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

function hashOf(id: string): number {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash;
}

const AVATAR_COLORS = ["#6d66f5", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#14b8a6"];
function avatarColor(id: string): string {
  return AVATAR_COLORS[hashOf(id) % AVATAR_COLORS.length];
}

// Muted placeholder colours for list thumbnails until real frames exist.
const THUMB_TONES = ["oklch(0.30 0.025 60)", "oklch(0.29 0.025 230)", "oklch(0.28 0.03 320)", "oklch(0.30 0.025 160)"];
const VIDEO_ICON = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="m10 9.5 4.5 2.5-4.5 2.5z" fill="currentColor"/></svg>`;
const LINK_ICON = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round"/></svg>`;

/** One small frame per source video for the queue list; null when it cannot be read. */
const queueFrames = new Map<string, string | null>();
const queueFrameWaits = new Set<string>();

function jobThumb(id: string, icon: string, seconds?: number, sourcePath?: string): HTMLElement {
  const thumb = element("span", "job-thumb");
  thumb.style.background = THUMB_TONES[hashOf(id) % THUMB_TONES.length];
  const frame = sourcePath ? queueFrames.get(sourcePath) : null;
  if (frame) {
    thumb.classList.add("framed");
    thumb.style.setProperty("--thumb", `url("${frame}")`);
  } else {
    thumb.innerHTML = icon;
  }
  if (sourcePath && !queueFrames.has(sourcePath) && !queueFrameWaits.has(sourcePath)) {
    queueFrameWaits.add(sourcePath);
    void invoke<ArrayBuffer>("video_frame", { sourcePath, position: 0.15, maxWidth: 160 })
      .then((bytes) => queueFrames.set(sourcePath, URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }))))
      .catch(() => queueFrames.set(sourcePath, null))
      .finally(() => {
        queueFrameWaits.delete(sourcePath);
        if (queueFrames.get(sourcePath) && lastModel) renderQueue(lastModel.view);
      });
  }
  if (seconds !== undefined) thumb.append(element("span", "job-dur", duration(seconds * 1000)));
  return thumb;
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(-2).map((part) => part[0]).join("").toUpperCase();
}

/** Nghe thử giọng: one sample at a time, made once per voice. */
const voiceSampler = createVoiceSampler(invoke, (path) => playableUrl(path), (message) => showError(message));

/** A voice to choose, with its Nghe thử button beside it. */
function voiceButton(voice: VoiceChoice, selected: boolean, note = ""): HTMLElement {
  const row = element("div", "voice-row");
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
  row.append(button, voiceSampler.button(voice.voice_id, voice.name));
  return row;
}

function renderVoiceControls(job: QueueJob | undefined): void {
  if (voiceJobId !== (job?.id ?? null)) {
    genderFilter.value = accentFilter.value = styleFilter.value = "";
    voiceJobId = job?.id ?? null;
  }
  const editable = !!job && job.status.state === "QUEUED" && !!voiceCatalog;
  voiceControls.disabled = !editable;
  dubbingToggle.checked = job?.dubbing.enabled ?? false;
  multiVoiceToggle.checked = job?.multiVoice ?? false;
  $("#multi-voice-row").hidden = !dubbingToggle.checked;
  separateToggle.checked = job?.separateBackground ?? false;
  $("#separate-row").hidden = !dubbingToggle.checked;
  burnInToggle.checked = job?.burnIn ?? true;
  const blurMode = job?.blurAuto ? "auto" : job?.blurRegions?.length ? "manual" : "off";
  // "Thủ công" stays selected while the user has not drawn a region yet.
  if (blurModeSelect.value !== "manual" || blurMode !== "off" || blurJobId === null) blurModeSelect.value = blurMode;
  const coverStyle = rememberedCoverStyle();
  $("#blur-summary").textContent = blurMode === "auto" ? `Tự dò chữ gốc, che bằng ${describeCoverStyle(coverStyle)}`
    : blurModeSelect.value === "off" ? "Giữ nguyên chữ có sẵn trong video"
      : job?.blurRegions?.length ? describeRegions(job.blurRegions) : "Chưa chọn vùng — bấm Chọn vùng…";
  $("#blur-manual-row").hidden = blurModeSelect.value !== "manual";
  $("#cover-style-row").hidden = blurModeSelect.value === "off";
  coverStyleSelect.value = coverStyle.mode;
  sourceLanguageSelect.value = job?.sourceLanguage ?? "";
  // Hachimi only translates Chinese: English hides the choice, Auto shows Argos and asks for Chinese.
  const engine = job ? effectiveTranslator(job) : "argos";
  const chinese = hachimiAllowed(job?.sourceLanguage);
  translationEngineSelect.value = engine;
  hachimiOption.disabled = !chinese;
  $("#translator-row").hidden = job?.sourceLanguage === "en";
  hachimiNeedsChinese.hidden = chinese || job?.sourceLanguage === "en" || !editable;
  $("#hachimi-warning").hidden = engine !== "hachimi";
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
  // Listening stays possible on a locked job; choosing does not.
  if (!editable) voiceList.querySelectorAll<HTMLButtonElement>("button.voice").forEach((button) => { button.disabled = true; });
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
  $("#clear-queue").hidden = !hasJobs;
  renderRunAll();
  const { summary } = view;
  const navCount = $("#nav-count");
  navCount.textContent = String(summary.total);
  navCount.hidden = summary.total === 0;
  $("#stat-running").textContent = String(summary.active);
  $("#stat-done").textContent = String(summary.completed);
  $("#stat-failed").textContent = String(summary.failed);
  const queueSummary = $("#queue-summary");
  queueSummary.textContent = hasJobs
    ? [`${summary.total} video`, summary.active ? `${summary.active} đang chạy` : "", summary.queued ? `${summary.queued} chờ` : "",
      summary.completed ? `${summary.completed} xong` : "", summary.failed ? `${summary.failed} lỗi` : ""].filter(Boolean).join(" · ")
    : "Chưa có video";
  queueSummary.title = queueSummary.textContent;

  queueList.replaceChildren();
  const rows = view.rows.filter((row) => matchesQueueFilter(row.state, queueFilter));
  queueList.hidden = rows.length === 0;
  // Videos with the same name (several links of one channel) read apart by when they were added.
  const named = new Map<string, number>();
  for (const row of view.rows) named.set(row.title, (named.get(row.title) ?? 0) + 1);
  for (const row of rows) {
    const selected = row.selected && selectedFetchId === null;
    const added = (named.get(row.title) ?? 0) > 1 ? shortDate(Math.floor(Date.parse(row.created) / 1000)) : "";
    const title = added ? `${row.title} · ${added}` : row.title;
    const item = element("li", `job${selected ? " selected" : ""}`);
    item.tabIndex = 0;
    if (selected) item.setAttribute("aria-current", "true");
    const working = !["QUEUED", "COMPLETED", "FAILED"].includes(row.state);
    const main = element("span", "job-main");
    const meta = element("span", "job-meta");
    meta.append(element("span", `dot ${tone(row.state, row.cancelled)}`), element("span", "msg", working ? row.status || row.stateLabel : row.stateLabel));
    if (working && row.fraction !== null) meta.append(element("span", "pct", row.progress));
    if (row.revision) meta.append(element("span", "mini-tag muted", "Bản sửa"));
    main.append(element("span", "job-title", title), meta);
    if (working) main.append(progressBar(row.fraction, row.state));
    item.append(jobThumb(row.id, VIDEO_ICON, knownDurations.get(row.id), row.sourcePath), main);
    item.title = `${title}\n${row.origin}`;
    const select = () => {
      selectedFetchId = null;
      setListOpen(false);
      queue.selectJob(row.id);
    };
    item.addEventListener("click", select);
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        select();
      }
    });
    queueList.append(item);
  }
  renderQueueEmpty();
}

function renderQueueEmpty(): void {
  const empty = $("#queue-empty");
  empty.hidden = !queueList.hidden || !fetchList.hidden;
  empty.textContent = queue.getSnapshot().jobs.length || fetches.size
    ? "Không có video nào ở mục này."
    : "Chưa có video. Thêm từ máy hoặc dán link ở thanh trên cùng.";
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

/** Short stage names for the slim strip under the player; the full label is the tooltip. */
const STAGE_SHORT: Record<string, string> = {
  probe: "Kiểm tra", audio: "Tách", asr: "Nhận dạng", translate: "Dịch", subtitles: "Phụ đề",
  dubbing: "Lồng tiếng", render: "Xuất", qc: "Kiểm tra lại",
};

function selectTab(tab: DetailTab): void {
  detailTab = tab;
  for (const button of document.querySelectorAll<HTMLButtonElement>("#detail-tabs [data-tab]")) {
    const active = button.dataset.tab === tab;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  }
  $("#tab-result").hidden = tab !== "result";
  $("#tab-setup").hidden = tab !== "setup";
}

/** How a kept download reads: its file name, unless that is a hashed store name. */
function downloadedName(url: string, path: string): string {
  const file = path.split(/[\\/]/).filter(Boolean).pop() ?? "";
  if (!/^[0-9a-f]{24,}\.[a-z0-9]+$/i.test(file)) return file;
  let host = "";
  try { host = new URL(url).hostname; } catch { /* keep the generic name */ }
  const site = /bilibili|b23\.tv/.test(host) ? "Bilibili" : /douyin/.test(host) ? "Douyin" : /kuaishou/.test(host) ? "Kuaishou" : "";
  return site ? `Video gốc tải từ ${site}` : "Video gốc đã tải";
}

function finishedLabel(job: QueueJob): string {
  if (!job.startedAt || !job.finishedAt) return "Đã xong";
  return `Đã xong sau ${duration(Date.parse(job.finishedAt) - Date.parse(job.startedAt))}`;
}

function renderInspector(job: QueueJob | null): void {
  // A selected download has no options yet; the job's panels keep their state.
  if (selectedFetchId !== null) {
    inspector.hidden = true;
    return;
  }
  inspector.hidden = !job;
  sourceCredit.select(job);
  packageVideo.select(job);
  shorts.select(job);
  postContent.select(job);
  reframe.select(job);
  if (!job) return;
  const { status } = job;
  // Queued videos open on their options, everything else on its results.
  const tabKey = `${job.id}:${status.state === "QUEUED"}`;
  if (tabKey !== detailTabKey) {
    detailTabKey = tabKey;
    detailTab = status.state === "QUEUED" ? "setup" : "result";
  }
  selectTab(detailTab);
  // A link job shows its link under Nguồn & quyền and its file in Tải từ link: no third copy.
  $("#detail-path").textContent = job.originUrl ? "" : job.sourcePath;
  $("#detail-path-label").hidden = !!job.originUrl;
  $("#detail-path").hidden = !!job.originUrl;
  $("#create-report-done").hidden = status.state !== "COMPLETED";

  const output = status.state === "COMPLETED" ? job.outputPath : null;
  const pending = $("#result-pending");
  pending.hidden = !!output;
  pending.textContent = status.state === "QUEUED"
    ? "Video lồng tiếng, Shorts và bản đóng gói sẽ hiện ở đây khi xử lý xong. Bạn vẫn đổi được thiết lập ở thẻ bên cạnh."
    : ["FAILED", "BLOCKED_NEEDS_ACTION"].includes(status.state)
      ? "Lần xử lý này chưa tạo ra video. Nguyên nhân và cách thử lại hiện trên khung video."
      : "Video lồng tiếng, Shorts và bản đóng gói sẽ hiện ở đây khi xử lý xong.";
  $("#open-post").hidden = !output;
  $("#result-title").textContent = finishedLabel(job);
  $("#result-card").hidden = !output;
  // Partial dubbing keeps the original voice for some lines; say so and point
  // to the review dialog, whose filter starts on those lines.
  const warning = $("#result-warning");
  warning.hidden = !(output && status.reason === "completed_partial_dubbing");
  warning.textContent = warning.hidden ? "" : "Một số câu chưa lồng tiếng được (vẫn là tiếng gốc), thường vì câu dịch quá dài. Bấm “Sửa phụ đề & giọng đọc” để rút gọn rồi áp dụng lại.";
  $("#open-review").classList.toggle("primary", !warning.hidden);
  const resultPath = $("#result-path");
  resultPath.textContent = output ? shortPath(output) : "";
  resultPath.title = output ?? "";
  // A link job: where its downloaded video and its results live.
  $("#link-card").hidden = !job.originUrl;
  const linkMedia = $("#link-media");
  // The kept download can sit in a hashed store folder: its file name is what reads.
  linkMedia.textContent = job.originUrl ? downloadedName(job.originUrl, job.sourcePath) : "";
  linkMedia.title = job.originUrl ? job.sourcePath : "";
  const linkOutput = $("#link-output");
  linkOutput.textContent = job.originUrl ? shortPath(job.outputDir ?? "") : "";
  linkOutput.title = job.originUrl ? job.outputDir ?? "" : "";

  renderVoiceControls(job);
  startButton.disabled = !canStart(job);
  startButton.hidden = !["QUEUED", "RECOVERED"].includes(status.state);
  startButton.textContent = status.state === "RECOVERED" ? "Tiếp tục xử lý" : "Bắt đầu xử lý";
  cancelButton.hidden = !isActive(job);
  cancelButton.disabled = !isActive(job);
  newVersionButton.hidden = !canCreateNewVersion(job);
  newVersionButton.disabled = !canCreateNewVersion(job);
  $("#action-hint").textContent = canCreateNewVersion(job)
    ? "Tạo bản mới để đổi giọng hoặc tùy chọn. Kết quả cũ vẫn được giữ lại."
    : isActive(job) ? "Có thể đóng ứng dụng — tiến trình được lưu checkpoint." : "";
  const locked = $("#setup-locked");
  locked.hidden = status.state === "QUEUED";
  locked.textContent = canCreateNewVersion(job)
    ? "Thiết lập của lần xử lý này đã khóa. Bấm “Xử lý lại với tùy chọn khác” để đổi."
    : "Thiết lập đã khóa khi bắt đầu xử lý. Muốn đổi, hãy hủy rồi chạy lại.";
}

// In-app player: the finished video ("Bản Việt"), the source ("Bản gốc") or
// an edited export picked in Kết quả ("Bản sửa").
let playerMode: "output" | "source" | "edited" = "output";
let playerJobId: string | null = null;
/** The edited export shown as "Bản sửa" for the player's job. */
let playerEditedPath: string | null = null;
let playerPath: string | null = null;
let playerRequest = 0;
let failedActionsKey = "";
const PLAYER_NOTE = "Xem video ngay trong ứng dụng DubFlow";

function showPlaceholder(text: string): void {
  $("#player-placeholder-text").textContent = text;
  $("#player-placeholder").hidden = false;
}

function renderPlayButton(): void {
  // While editing, other players may be playing the output (inserted clips).
  const playing = editor.isPlaying() ?? (!playerVideo.paused && !playerVideo.ended);
  const covered = !$("#player-busy").hidden || !$("#player-failed").hidden;
  playButton.disabled = covered || !playerVideo.getAttribute("src") || playerVideo.readyState < 1;
  playButton.classList.toggle("playing", playing);
  playButton.setAttribute("aria-label", playing ? "Tạm dừng" : "Phát");
  const length = Number.isFinite(playerVideo.duration) ? playerVideo.duration : 0;
  // While editing, the time is on the edited output.
  $("#player-time").textContent = editor.timeLabel() ?? `${duration(playerVideo.currentTime * 1000)} / ${duration(length * 1000)}`;
}

// Edit mode (Cắt ghép, Chèn): the player previews the edit; the panel and timeline replace the list and details.
const editor = mountEditor(invoke, {
  video: playerVideo,
  confirm: confirmDialog,
  refresh: () => {
    // Every edit happens on the Chỉnh sửa screen (Issue #497).
    if (editor.target && currentView === "queue") showView("queue", "edit");
    if (lastModel) render(lastModel);
    else if (editor.target && editor.target.kind !== "job") renderEditedStage(editor.target);
  },
  renderPlayer: renderPlayButton,
  showError,
  playExport: (jobId, path) => {
    if (selectedJob()?.id !== jobId) return;
    playerMode = "edited";
    playerEditedPath = path;
    playWhenLoaded = true;
    if (lastModel) renderStage(lastModel.view.selected);
  },
  // A heavy video's light copy (Bản xem nhẹ) when it is ready, else the file itself.
  mediaUrl: async (path) => {
    const allowed = await lightPath(path) ?? await invoke<string | null>("allow_media", { path });
    return allowed ? versioned(path, convertFileSrc(allowed)) : null;
  },
  mediaChanged: (path) => {
    mediaVersions.set(path.toLowerCase(), Date.now());
    if (playerPath === path) {
      // Let go of the old file now; the editor opens it again.
      playerVideo.pause();
      playerVideo.removeAttribute("src");
      playerVideo.load();
      playerPath = null;
    }
  },
  proxy: (path) => invoke<ProxyState>("edit_proxy", { path, start: true }),
  reloadPlayer: () => {
    const path = playerPath;
    if (!path || playerLight) return;
    const time = playerVideo.currentTime;
    playerPath = null;
    setPlayerSource(path);
    resumeAt = Number.isFinite(time) ? time : null;
  },
  library: () => [
    ...queue.getSnapshot().jobs.filter((job) => job.status.state === "COMPLETED" && job.outputPath)
      .map((job) => ({ id: job.id, title: job.displayName, outputPath: job.outputPath!, kind: "job" as const,
        // Same-named queue videos read apart by when they finished.
        meta: ["Lồng tiếng", shortDate(Math.floor(Date.parse(job.finishedAt ?? job.createdAt) / 1000))].filter(Boolean).join(" · ") })),
    // The other pages' finished videos (Bán hàng, Tổng hợp, Phim tài liệu), as on the Chỉnh sửa screen.
    ...distinctTitles(editLibrary.found()).map((entry) => ({ id: entry.path, title: entry.title, outputPath: entry.path, kind: entry.kind === "job" ? "file" as const : entry.kind,
      meta: [KIND_LABELS[entry.kind], entry.title.endsWith(shortDate(entry.modified)) ? "" : shortDate(entry.modified)].filter(Boolean).join(" · ") })),
    // The browser preview has no finished jobs of its own to insert.
    ...(isTauri ? [] : PREVIEW_EDIT_LIBRARY.map((item) => ({ ...item, kind: "job" as const }))),
  ],
  revise: async (job, outputDir) => {
    const revisionId = queue.reviseJob(job.id, outputDir);
    const revision = queue.getSnapshot().jobs.find((candidate) => candidate.id === revisionId);
    if (revision) await startJob(revision);
    return revisionId;
  },
  sfx: sfxLibrary,
  ai: claudeKey,
  openAiSettings: () => aiSettings.open(),
  // The recording goes as the raw body (a JSON array of bytes would be several times larger).
  saveRecording: (bytes, outputPath, kind) => isTauri
    ? tauriInvoke<string>("edit_save_recording", new Uint8Array(bytes),
      { headers: { "x-output-path": encodeURIComponent(outputPath), "x-kind": encodeURIComponent(kind) } })
    : previewInvoke<string>("edit_save_recording", { bytes: bytes.byteLength, outputPath, kind }),
  openReview: () => void openReview(),
  cpuOnly: () => cpuOnly(),
  // Back to the page the video was opened from: the Chỉnh sửa list, the queue or another page.
  left: () => {
    const origin = editOrigin ?? "queue";
    editOrigin = null;
    showView(origin);
  },
});
/** The page the open video was opened from (Issue #426); closing the edit goes back there. */
let editOrigin: View | null = null;

/** Open a video in the editor, on the Chỉnh sửa screen (Issue #497); false when it did not open. */
async function editVideo(target: EditTarget, origin: View): Promise<boolean> {
  const previous = editOrigin;
  editOrigin = origin;
  if (target.kind === "job" && target.job) {
    // The editor follows the queue's selection for a queue video.
    selectedFetchId = null;
    if (selectedJob()?.id !== target.job.id) queue.selectJob(target.job.id);
  }
  showView("queue", "edit");
  if (await editor.openTarget(target)) return true;
  // Not opened: the edit that stayed open, else the page it came from.
  editOrigin = previous;
  if (editor.target) showView("queue", "edit");
  else showView(origin);
  return false;
}

/** The middle column while another page's video is being edited. */
function renderEditedStage(target: EditTarget): void {
  dropZone.hidden = true;
  $("#player-frame").hidden = false;
  $("#player-controls").hidden = false;
  $("#stages-strip").hidden = true;
  $("#remove-job").hidden = true;
  $("#player-busy").hidden = true;
  $("#player-failed").hidden = true;
  $("#player-mode").hidden = true;
  $("#detail-name").textContent = target.displayName;
  $("#detail-name").title = target.outputPath;
  playerJobId = null;
  setPlayerSource(target.outputPath);
  $("#detail-timing").textContent = "";
  renderPlayButton();
}
/** "Xem" on an edited export starts it as soon as it has loaded. */
let playWhenLoaded = false;

/** Where the player goes back to after it reloads the same video (a light copy became ready). */
let resumeAt: number | null = null;
/** Whether the player shows the open video's light copy (Bản xem nhẹ). */
let playerLight = false;

/** The open edit's video (the player may be set before the editor is mounted). */
function editedPath(): string | null {
  try {
    return editor.target?.outputPath ?? null;
  } catch {
    return null;
  }
}

/** Videos written again in place (a documentary rendered anew): their URLs carry a version so the player does not replay the old file. */
const mediaVersions = new Map<string, number>();
function versioned(path: string, url: string): string {
  const version = mediaVersions.get(path.toLowerCase());
  return version ? `${url}${url.includes("?") ? "&" : "?"}v=${version}` : url;
}

/** The light copy of a heavy video, when it is ready (Bản xem nhẹ). */
async function lightPath(path: string): Promise<string | null> {
  if (!isTauri) return null;
  const state = await invoke<ProxyState>("edit_proxy", { path, start: false }).catch(() => null);
  return state?.state === "ready" && state.path ? state.path : null;
}

/** Load a local video into the player through the asset protocol. */
function setPlayerSource(path: string | null, note = PLAYER_NOTE): void {
  // Out of the editor the player goes back from the light copy to the video itself.
  if (path === playerPath && !(playerLight && editedPath() !== path)) {
    if (!path) showPlaceholder(note);
    return;
  }
  playerPath = path;
  playerLight = false;
  resumeAt = null;
  const request = ++playerRequest;
  playerVideo.pause();
  playerVideo.removeAttribute("src");
  playerVideo.load();
  showPlaceholder(note);
  renderPlayButton();
  if (!path) return;
  // The edited video previews from its light copy when one is ready (Bản xem nhẹ).
  const editing = editedPath() === path;
  (editing ? lightPath(path) : Promise.resolve(null))
    .then((light) => {
      if (light && request === playerRequest) playerLight = true;
      return light ?? invoke<string | null>("allow_media", { path });
    }).then((allowed) => {
    if (request !== playerRequest) return;
    if (!allowed) {
      showPlaceholder(isTauri ? "Không xem được video này trong ứng dụng." : "Bản xem thử trên trình duyệt không phát video; hãy mở ứng dụng DubFlow để xem.");
      return;
    }
    playerVideo.src = versioned(path, convertFileSrc(allowed));
  }).catch((error) => {
    if (request === playerRequest) showPlaceholder(`Không mở được video để xem: ${String(error).slice(0, 160)}`);
  });
}

playerVideo.addEventListener("loadedmetadata", () => {
  if (resumeAt === null) return;
  playerVideo.currentTime = Math.min(resumeAt, Number.isFinite(playerVideo.duration) ? playerVideo.duration : resumeAt);
  resumeAt = null;
});
playerVideo.addEventListener("loadeddata", () => {
  $("#player-placeholder").hidden = true;
  if (playWhenLoaded) void playerVideo.play().catch(() => undefined);
  playWhenLoaded = false;
  renderPlayButton();
});
playerVideo.addEventListener("loadedmetadata", () => {
  if (playerJobId && Number.isFinite(playerVideo.duration) && !knownDurations.has(playerJobId)) {
    knownDurations.set(playerJobId, playerVideo.duration);
    if (lastModel) renderQueue(lastModel.view);
  }
  renderPlayButton();
});
playerVideo.addEventListener("error", () => {
  if (playerVideo.getAttribute("src")) showPlaceholder("Trình xem trong ứng dụng chưa đọc được định dạng này. Hãy mở bằng trình phát khác.");
  renderPlayButton();
});
for (const name of ["play", "pause", "ended", "timeupdate", "durationchange"]) playerVideo.addEventListener(name, renderPlayButton);
const togglePlayback = (): void => {
  if (playButton.disabled) return;
  if (editor.target !== null) return editor.togglePlay();
  if (playerVideo.paused || playerVideo.ended) void playerVideo.play().catch(() => undefined);
  else playerVideo.pause();
};
playButton.addEventListener("click", togglePlayback);
// In Chỉnh sửa a click on the picture first drops what is selected on it.
playerVideo.addEventListener("click", () => {
  if (editor.target !== null && !playButton.disabled) editor.clickPicture();
  else togglePlayback();
});
for (const button of document.querySelectorAll<HTMLButtonElement>("#player-mode [data-mode]")) {
  button.addEventListener("click", () => {
    playerMode = button.dataset.mode === "source" ? "source" : button.dataset.mode === "edited" && playerEditedPath ? "edited" : "output";
    if (lastModel) renderStage(lastModel.view.selected);
  });
}

function renderStages(job: QueueJob): void {
  const { status } = job;
  const badge = $("#detail-state");
  badge.className = `badge ${tone(status.state, isCancelled(status))}`;
  badge.textContent = stateLabel(status.state, status.reason);
  const fraction = progressFraction(status);
  $("#detail-percent").textContent = status.state === "QUEUED" || fraction === null ? "" : `${Math.floor(fraction * 100)}%`;
  const stages = $("#stage-list");
  stages.replaceChildren();
  const active = activeStageIndex(status);
  PIPELINE_STAGES.forEach((stage, index) => {
    const item = element("li");
    item.title = stage.label;
    item.append(element("span", "stage-dot"), element("span", "stage-label", STAGE_SHORT[stage.id] ?? stage.label));
    if (stage.id === "dubbing" && !job.dubbing.enabled) item.classList.add("skipped");
    else if (index < active) item.classList.add("done");
    else if (index === active) item.classList.add(["FAILED", "BLOCKED_NEEDS_ACTION"].includes(status.state) ? "failed" : "active");
    stages.append(item);
  });
}

function actionButton(label: string, className: string, onClick: () => void): HTMLButtonElement {
  const button = element("button", className, label);
  button.type = "button";
  button.addEventListener("click", onClick);
  return button;
}

/** The middle column: drop zone, the selected job's video, or a download in progress. */
function renderStage(job: QueueJob | null): void {
  const edited = editor.target;
  if (edited && edited.kind !== "job") {
    renderEditedStage(edited);
    return;
  }
  const fetch = selectedFetchId === null ? undefined : fetches.get(selectedFetchId);
  if (selectedFetchId !== null && !fetch) selectedFetchId = null;
  const subject = !!job || !!fetch;
  dropZone.hidden = subject;
  $("#player-frame").hidden = !subject;
  $("#player-controls").hidden = !subject;
  $("#stages-strip").hidden = !job || !!fetch;
  $("#remove-job").hidden = !job || !!fetch;
  const busy = $("#player-busy");
  const failed = $("#player-failed");
  if (fetch) {
    renderFetchStage(fetch);
    renderPlayButton();
    return;
  }
  if (!job) {
    $("#detail-name").textContent = queue.getSnapshot().jobs.length ? "Chọn một video trong danh sách" : "Thêm video để bắt đầu";
    $("#player-mode").hidden = true;
    busy.hidden = failed.hidden = true;
    playerJobId = null;
    setPlayerSource(null);
    renderPlayButton();
    return;
  }
  const { status } = job;
  if (playerJobId !== job.id) {
    playerMode = "output";
    playerEditedPath = null;
  }
  playerJobId = job.id;
  $("#detail-name").textContent = job.displayName;
  $("#detail-name").title = job.displayName;
  $("#remove-job").title = isRemovable(job) ? "Xóa video khỏi danh sách" : "Hủy xử lý rồi xóa video khỏi danh sách";
  const done = status.state === "COMPLETED" && !!job.outputPath;
  $("#player-mode").hidden = !done;
  $("#player-mode-edited").hidden = !playerEditedPath;
  for (const button of document.querySelectorAll<HTMLButtonElement>("#player-mode [data-mode]")) {
    button.setAttribute("aria-selected", String(button.dataset.mode === playerMode));
  }
  // Edit mode always previews the finished video.
  const editing = editor.jobId === job.id;
  setPlayerSource(done && (editing || playerMode === "output") ? job.outputPath
    : done && playerMode === "edited" && playerEditedPath ? playerEditedPath : job.sourcePath);

  const fraction = progressFraction(status);
  const stopped = ["FAILED", "BLOCKED_NEEDS_ACTION"].includes(status.state);
  busy.hidden = stopped || ["QUEUED", "COMPLETED"].includes(status.state);
  failed.hidden = !stopped;
  if (!busy.hidden) {
    const stage = PIPELINE_STAGES[Math.min(Math.max(activeStageIndex(status), 0), PIPELINE_STAGES.length - 1)];
    $("#busy-title").textContent = isActive(job) && fraction !== null && status.state !== "RECOVERED"
      ? `${stage.label} · ${Math.floor(fraction * 100)}%`
      : stateLabel(status.state, status.reason);
    $("#detail-bar").style.width = `${Math.round((fraction ?? 0) * 100)}%`;
    $("#detail-bar").parentElement!.hidden = fraction === null;
    $("#detail-bar").parentElement!.classList.remove("indeterminate");
    $("#detail-message").textContent = displayJobStatus(status);
    const resume = $<HTMLButtonElement>("#busy-resume");
    resume.hidden = status.state !== "RECOVERED";
    resume.disabled = !canStart(job);
    const cancel = $<HTMLButtonElement>("#busy-cancel");
    cancel.textContent = "Hủy xử lý";
    cancel.hidden = !isActive(job);
    cancel.disabled = cancelButton.disabled;
    $("#busy-folder").hidden = true;
  }
  if (stopped) {
    const cancelled = isCancelled(status);
    $("#failed-title").textContent = cancelled ? "Đã hủy xử lý video này"
      : status.state === "BLOCKED_NEEDS_ACTION" ? "Video này đang chờ bạn thao tác" : "Không xử lý được video này";
    $("#failed-cause").textContent = cancelled ? "Bấm Thử lại để xử lý lại từ đầu với cùng thiết lập." : displayJobStatus(status) || "Không rõ nguyên nhân.";
    $("#failed-more").hidden = false;
    const key = `job:${job.id}:${status.state}`;
    if (failedActionsKey !== key) {
      failedActionsKey = key;
      $("#failed-actions").replaceChildren(actionButton("Thử lại", "btn primary", () => void retrySelected()));
    }
  } else {
    failedActionsKey = "";
  }
  $("#detail-timing").textContent = timingLabel(job, fraction) || (status.state === "QUEUED" ? "Chưa bắt đầu" : stateLabel(status.state, status.reason));
  renderStages(job);
  renderPlayButton();
}

function renderFetchStage(entry: FetchEntry): void {
  const { status } = entry;
  playerJobId = null;
  $("#detail-name").textContent = fetchName(status);
  $("#detail-name").title = `${fetchName(status)}\n${status.url}`;
  $("#player-mode").hidden = true;
  setPlayerSource(null, "Video hiện ở đây khi tải xong");
  const view = fetchView(entry, Date.now());
  $("#player-busy").hidden = view.failed;
  $("#player-failed").hidden = !view.failed;
  const provider = PROVIDER_NAMES[status.provider] ?? status.provider;
  if (!view.failed) {
    $("#busy-title").textContent = entry.saving || entry.renewing ? view.badge : `${view.badge} từ ${provider}`;
    const bar = $("#detail-bar");
    const track = bar.parentElement!;
    track.hidden = !view.bar;
    track.classList.toggle("indeterminate", view.fraction === null);
    bar.style.width = view.fraction === null ? "" : `${Math.round(view.fraction * 100)}%`;
    $("#detail-message").textContent = `${view.message}\nVideo tải về: ${shortPath(downloadDir() ?? defaultDownloadDir)}\nKết quả: ${shortPath(entry.outputDir)}`;
    $("#busy-resume").hidden = true;
    const cancel = $<HTMLButtonElement>("#busy-cancel");
    cancel.hidden = entry.saving || entry.renewing;
    cancel.disabled = false;
    cancel.textContent = "Hủy tải";
    $("#busy-folder").hidden = false;
    failedActionsKey = "";
  } else {
    $("#failed-title").textContent = "Không tải được video này";
    $("#failed-cause").textContent = view.message;
    $("#failed-more").hidden = true;
    const key = `fetch:${status.scan_id}:${status.state}:${needsDouyinLogin(status)}`;
    if (failedActionsKey !== key) {
      failedActionsKey = key;
      $("#failed-actions").replaceChildren(...failedFetchActions(entry));
    }
  }
  $("#detail-timing").textContent = view.badge;
}

let lastModel: DesktopShellModel | null = null;
function render(model: DesktopShellModel): void {
  lastModel = model;
  if (selectedFetchId !== null && !fetches.has(selectedFetchId)) selectedFetchId = null;
  editor.select(selectedFetchId === null ? model.view.selected : null);
  renderQueue(model.view);
  renderInspector(model.view.selected);
  renderStage(model.view.selected);
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
    const current = queue.getSnapshot().jobs.find((candidate) => candidate.id === jobId);
    if (!current || !acceptsPolledStatus(current.status, response.status)) return;
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
// "Ghi nhớ thiết lập lần trước" off: new videos start from the first-run defaults.
const REMEMBER_KEY = "dubflow.rememberDefaults.v1";
function rememberEnabled(): boolean {
  try {
    return window.localStorage.getItem(REMEMBER_KEY) !== "0";
  } catch {
    return true;
  }
}

function currentDefaults(): JobDefaults {
  if (!rememberEnabled()) return FIRST_RUN_DEFAULTS;
  try {
    return readJobDefaults(window.localStorage.getItem(DEFAULTS_KEY), window.localStorage.getItem(LEGACY_DEFAULTS_KEY)) ?? FIRST_RUN_DEFAULTS;
  } catch {
    return FIRST_RUN_DEFAULTS;
  }
}

function rememberDefaults(job: QueueJob): void {
  try {
    window.localStorage.setItem(DEFAULTS_KEY, serializeJobDefaults(job));
  } catch {
    // Defaults are a convenience; the queue works without storage.
  }
}

/** Videos added before the voice catalog loaded; they get their dubbing default once it arrives. */
const awaitingCatalog = new Set<string>();

function applyDubbingDefault(id: string, voiceId: string | null): void {
  if (!voiceCatalog) {
    awaitingCatalog.add(id);
    return;
  }
  const known = voiceCatalog.voices.some((voice) => voice.voice_id === voiceId);
  queue.setDubbing(id, dubbingOptions(voiceCatalog, true, known ? voiceId : null));
}

function applyDefaults(jobIds: readonly string[]): void {
  const defaults = currentDefaults();
  for (const id of jobIds) {
    try {
      if (!defaults.burnIn) queue.setBurnIn(id, false);
      if (defaults.sourceLanguage) queue.setSourceLanguage(id, defaults.sourceLanguage);
      if (defaults.blurRegions) queue.setBlurRegions(id, defaults.blurRegions);
      else if (defaults.blurAuto) queue.setBlurAuto(id, true);
      if (defaults.multiVoice) queue.setMultiVoice(id, true);
      if (defaults.separateBackground) queue.setSeparateBackground(id, true);
      if (defaults.translationEngine) queue.setTranslationEngine(id, defaults.translationEngine);
      if (defaults.dubbing) applyDubbingDefault(id, defaults.voiceId);
    } catch {
      // A job that already started keeps its own options.
    }
  }
}

function addPaths(paths: string[]): void {
  const videos = paths.filter((path) => VIDEO_EXTENSIONS.test(path));
  if (paths.length && !videos.length) {
    showError(`Chỉ hỗ trợ file video ${FORMAT_LIST}.`);
    return;
  }
  showError("");
  const added = queue.addPaths(videos);
  applyDefaults(added);
  if (added.length) {
    selectedFetchId = null;
    showView("queue");
  }
  if (added.length && !selectedJob()) queue.selectJob(added[0]);
  else if (added.length) queue.selectJob(added[added.length - 1]);
}

/** Dropped files and folders: the host expands folders to the videos inside. */
async function addDropped(paths: string[]): Promise<void> {
  if (!paths.length) return;
  try {
    const videos = await invoke<string[]>("expand_video_paths", { paths });
    if (!videos.length) {
      showError(`Không tìm thấy video ${FORMAT_LIST} trong mục đã thả.`);
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
  runAllButton.hidden = waiting < 1 && !batchRunning;
  $("#queue-col-foot").hidden = runAllButton.hidden;
  runAllButton.textContent = batchRunning ? `Dừng hàng đợi (${waiting} chờ)` : `Bắt đầu ${waiting} video đang chờ`;
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
  label.textContent = root ?? "Cạnh video gốc (thư mục “DubFlow Output”)";
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

// Processing device: an NVIDIA GPU is used automatically when present, unless
// the user keeps every model on the CPU.
const CPU_ONLY_KEY = "dubflow.cpuOnly.v1";
const cpuOnlyToggle = $<HTMLInputElement>("#cpu-only");
function cpuOnly(): boolean {
  try {
    return window.localStorage.getItem(CPU_ONLY_KEY) === "1";
  } catch {
    return false;
  }
}
cpuOnlyToggle.checked = cpuOnly();
// Thread budget and CPU-only reach every FFmpeg/model process the host starts.
const applyPerformance = mountPerformanceSettings(invoke, cpuOnly, showError);
cpuOnlyToggle.addEventListener("change", () => {
  try {
    if (cpuOnlyToggle.checked) window.localStorage.setItem(CPU_ONLY_KEY, "1");
    else window.localStorage.removeItem(CPU_ONLY_KEY);
  } catch {
    cpuOnlyToggle.checked = !cpuOnlyToggle.checked;
    showError("Không lưu được lựa chọn thiết bị xử lý trên máy này.");
  }
  applyPerformance();
});

// One click: a small zip of this job's reports (no media) on the Desktop, shown in Explorer.
async function createReport(button: HTMLButtonElement): Promise<void> {
  const job = selectedJob();
  if (!job) return;
  button.disabled = true;
  button.textContent = "Đang tạo báo cáo…";
  try {
    const path = await invoke<string>("create_report", { jobId: job.id, outputDir: job.outputDir ?? null });
    button.textContent = "Đã tạo báo cáo trên Desktop";
    button.title = path;
  } catch (error) {
    button.textContent = "Gửi báo cáo lỗi";
    showError(String(error).slice(0, 400));
  } finally {
    button.disabled = false;
    window.setTimeout(() => { button.textContent = "Gửi báo cáo lỗi"; }, 6000);
  }
}
for (const id of ["#create-report", "#create-report-done"]) {
  const button = $<HTMLButtonElement>(id);
  button.addEventListener("click", () => void createReport(button));
}

/** The look last applied in Phụ đề, the default for new videos (none: today's look). */
function rememberedSubtitleStyle(): SubtitleStyle | null {
  try {
    return storedStyle(window.localStorage.getItem(STYLE_STORAGE_KEY));
  } catch {
    return null;
  }
}

/** The cover last applied in Che or chosen in Thiết lập, the default for new videos (none: edge stretch). */
function rememberedCoverStyle(): CoverStyle {
  try {
    return storedCoverStyle(window.localStorage.getItem(COVER_STORAGE_KEY));
  } catch {
    return storedCoverStyle(null);
  }
}

async function startJob(job: QueueJob): Promise<void> {
  if (!canStart(job)) return;
  startButton.disabled = true;
  queue.updateStatus(job.id, { ...job.status, state: "RUNNING", reason: "starting", message: "Đang khởi động xử lý" });
  try {
    await aiRestored;
    const started = await invoke<{ output_dir: string } | null>("start_job", {
      ...startJobRequest(job, outputRoot(), cpuOnly(), rememberedSubtitleStyle(), rememberedCoverStyle()),
      // "Dịch phụ đề bằng AI theo ngữ cảnh" and "Từ nhạy cảm" (Cài đặt › Tài khoản); the host adds the saved key.
      ...translationJobArgs(loadPrefs(), claudeKey, job.displayName),
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
$("#busy-resume").addEventListener("click", () => startButton.click());

/** Copy a stopped job's options to its new version, so "Thử lại" reruns the same choices. */
function copyOptions(from: QueueJob, id: string): void {
  if (from.burnIn === false) queue.setBurnIn(id, false);
  if (from.sourceLanguage) queue.setSourceLanguage(id, from.sourceLanguage);
  if (from.translationEngine) queue.setTranslationEngine(id, from.translationEngine);
  if (from.blurRegions?.length) queue.setBlurRegions(id, from.blurRegions);
  else if (from.blurAuto) queue.setBlurAuto(id, true);
  if (from.multiVoice) queue.setMultiVoice(id, true);
  if (from.speakerVoices) queue.setSpeakerVoices(id, from.speakerVoices);
  if (from.separateBackground) queue.setSeparateBackground(id, true);
}

/** "Thử lại" on a stopped video: a new version with the same options, started at once. */
async function retrySelected(): Promise<void> {
  const selected = selectedJob();
  if (!selected) return;
  if (canStart(selected)) return startJob(selected);
  if (!canCreateNewVersion(selected)) return;
  try {
    const id = queue.retryJob(selected.id);
    copyOptions(selected, id);
    const retry = queue.getSnapshot().jobs.find((job) => job.id === id);
    if (retry && canStart(retry)) await startJob(retry);
  } catch (error) {
    showError(`Không thử lại được: ${String(error).slice(0, 180)}`);
  }
}

async function cancelSelected(): Promise<void> {
  const selected = selectedJob();
  if (!selected || !isActive(selected)) return;
  cancelButton.disabled = true;
  $<HTMLButtonElement>("#busy-cancel").disabled = true;
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
}
cancelButton.addEventListener("click", () => void cancelSelected());
$("#busy-cancel").addEventListener("click", () => {
  const entry = selectedFetchId === null ? undefined : fetches.get(selectedFetchId);
  if (entry) void cancelFetch(entry.status.scan_id);
  else void cancelSelected();
});
$("#busy-folder").addEventListener("click", () => {
  const entry = selectedFetchId === null ? undefined : fetches.get(selectedFetchId);
  if (entry) openFolder(entry.outputDir);
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

$("#remove-job").addEventListener("click", async () => {
  const selected = selectedJob();
  if (!selected) return;
  if (!isRemovable(selected)) {
    // A running video is cancelled first, so its worker does not keep writing.
    const confirmed = await confirmDialog({
      title: "Hủy và xóa video này?",
      message: "Video đang được xử lý. DubFlow sẽ dừng xử lý rồi xóa video khỏi danh sách; file đã tạo trên ổ đĩa không bị xóa.",
      confirmLabel: "Hủy và xóa",
      cancelLabel: "Giữ lại",
      danger: true,
    });
    if (!confirmed) return;
    try {
      await invoke("cancel_job", { jobId: selected.id });
    } catch (error) {
      showError(`Không dừng được xử lý: ${String(error).slice(0, 180)}`);
      return;
    }
  }
  if (!queue.getSnapshot().jobs.some((job) => job.id === selected.id)) return;
  stopPolling(selected.id);
  queue.removeJob(selected.id);
});
$("#failed-remove").addEventListener("click", () => $("#remove-job").click());

$("#clear-queue").addEventListener("click", async () => {
  const jobs = queue.getSnapshot().jobs;
  const active = jobs.filter((job) => !isRemovable(job)).length;
  const removable = jobs.length - active;
  if (!removable) {
    showError("Video đang chạy không xóa được khỏi danh sách; hãy hủy xử lý trước.");
    return;
  }
  const confirmed = await confirmDialog({
    title: `Xóa ${removable} video khỏi danh sách?`,
    message: active
      ? `${active} video đang chạy vẫn được giữ lại. File kết quả trên ổ đĩa không bị xóa.`
      : "File kết quả trên ổ đĩa không bị xóa.",
    confirmLabel: `Xóa ${removable} video`,
    cancelLabel: "Giữ lại",
    danger: true,
  });
  if (!confirmed) return;
  // Re-read: a video may have started while the dialog was open.
  for (const job of queue.getSnapshot().jobs) {
    if (!isRemovable(job)) continue;
    stopPolling(job.id);
    queue.removeJob(job.id);
  }
});

$("#open-output").addEventListener("click", () => {
  const path = selectedJob()?.outputPath;
  if (path) invoke("open_path", { path }).catch((error) => showError(`Không mở được video: ${String(error).slice(0, 160)}`));
});
$("#reveal-download").addEventListener("click", () => {
  const path = selectedJob()?.sourcePath;
  if (path) invoke("reveal_download", { path }).catch((error) => showError(`Không mở được video gốc: ${String(error).slice(0, 160)}`));
});
$("#open-link-output").addEventListener("click", () => {
  const dir = selectedJob()?.outputDir;
  if (dir) openFolder(dir);
});
$("#reveal-output").addEventListener("click", () => {
  const path = selectedJob()?.outputPath;
  if (path) invoke("reveal_path", { path }).catch((error) => showError(`Không mở được thư mục: ${String(error).slice(0, 160)}`));
});

// Links: the host downloads through the supervisor's source service; the
// finished file is copied to the download folder and joins the queue as a
// local job with its own output folder.
const PROVIDER_NAMES: Record<string, string> = { bilibili: "Bilibili", douyin: "Douyin", generic: "Link video" };
const CLOSE_ICON = `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`;

function linkName(url: string): string {
  try {
    const parsed = new URL(url);
    const tail = decodeURIComponent(parsed.pathname.split("/").filter(Boolean).at(-1) ?? "");
    return tail || parsed.hostname;
  } catch {
    return url;
  }
}

/** The video's title when known (Khám phá), else the link's last part. */
function fetchName(status: FetchStatus): string {
  return status.title?.trim() || linkName(status.url);
}

function openFolder(path: string): void {
  invoke("open_folder", { path }).catch((error) => showError(`Không mở được thư mục: ${String(error).slice(0, 160)}`));
}

/** What a download row and the player overlay show right now. */
function fetchView(entry: FetchEntry, now: number, compact = false): { failed: boolean; badge: string; message: string; fraction: number | null; bar: boolean } {
  const { status } = entry;
  if (entry.saving) return { failed: false, badge: "Đang lưu video", message: "Đang chép video vào thư mục tải về…", fraction: null, bar: true };
  if (entry.renewing) return { failed: false, badge: "Đang thử lại", message: "Đang làm mới phiên Douyin rồi tải lại…", fraction: null, bar: true };
  if (status.state === "downloading") {
    const line = progressLine(status.downloaded_bytes, status.total_bytes, entry.samples, compact);
    return { failed: false, badge: fetchBadge(status.state), message: status.detail ? `${status.detail} · ${line}` : line,
      fraction: fractionDone(status.downloaded_bytes, status.total_bytes), bar: true };
  }
  return { failed: status.state === "failed", badge: fetchBadge(status.state), message: fetchMessage(status, now), fraction: null, bar: status.state === "preparing" };
}

/** A determinate bar, or a sliding one when the size is not known yet. */
function fetchBar(fraction: number | null): HTMLElement {
  const bar = progressBar(fraction, "RUNNING");
  if (fraction === null) {
    bar.classList.add("indeterminate");
    bar.firstElementChild!.removeAttribute("style");
    bar.style.setProperty("--phase", `-${Date.now() % 1400}ms`);
  }
  return bar;
}

function renderFetches(): void {
  const stale = selectedFetchId !== null && !fetches.has(selectedFetchId);
  if (stale) selectedFetchId = null;
  fetchList.replaceChildren();
  fetchList.hidden = fetches.size === 0 || queueFilter === "done";
  const now = Date.now();
  for (const entry of fetches.values()) {
    const { status, outputDir } = entry;
    const view = fetchView(entry, now, true);
    const selected = selectedFetchId === status.scan_id;
    const item = element("li", `job${view.failed ? " failed" : ""}${selected ? " selected" : ""}`);
    item.tabIndex = 0;
    if (selected) item.setAttribute("aria-current", "true");
    const main = element("span", "job-main");
    const meta = element("span", "job-meta");
    meta.append(element("span", `dot ${view.failed ? "err" : status.state === "queued" ? "" : "run"}`),
      element("span", `msg${view.failed ? " err" : ""}`, view.failed ? "Tải không được" : view.message));
    const title = element("span", "job-title", fetchName(status));
    main.append(title, meta);
    if (view.bar) main.append(fetchBar(view.fraction));
    item.append(jobThumb(status.scan_id, LINK_ICON), main);
    if (!entry.saving) {
      const remove = element("button", "icon-btn job-dismiss");
      remove.type = "button";
      remove.title = "Hủy tải";
      remove.setAttribute("aria-label", "Hủy tải");
      remove.innerHTML = CLOSE_ICON;
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        void cancelFetch(status.scan_id);
      });
      item.append(remove);
    }
    // Where the results of this link will be saved, from the moment it is added.
    item.title = `${fetchName(status)}\n${status.url}\nVideo tải về: ${downloadDir() ?? defaultDownloadDir}\nKết quả: ${outputDir}`;
    const select = () => {
      selectedFetchId = status.scan_id;
      setListOpen(false);
      renderFetches();
    };
    item.addEventListener("click", select);
    item.addEventListener("keydown", (event) => {
      if (event.target === item && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        select();
      }
    });
    fetchList.append(item);
  }
  renderQueueEmpty();
  // The player follows the selected download, and returns to the job once it is gone.
  if ((selectedFetchId !== null || stale) && lastModel) render(lastModel);
}

function scheduleFetchPoll(entry: FetchEntry): void {
  window.clearTimeout(entry.timer);
  const active = entry.status.state === "preparing" || entry.status.state === "downloading";
  entry.timer = window.setTimeout(() => void pollFetch(entry.status.scan_id), active ? 1000 : 1500);
}

/** Take a row off the list (the host has already let go of it). */
function removeFetch(scanId: string): void {
  window.clearTimeout(fetches.get(scanId)?.timer);
  fetches.delete(scanId);
  renderFetches();
}

/**
 * The row's × and the overlay's "Hủy tải": a link being prepared or
 * downloading asks first; a waiting or failed one goes at once.
 */
async function cancelFetch(scanId: string): Promise<void> {
  const entry = fetches.get(scanId);
  if (!entry || entry.saving) return;
  if (["preparing", "downloading"].includes(entry.status.state) && !(await confirmDialog({
    title: "Hủy tải video này?",
    message: "Phần đã tải sẽ bị bỏ.",
    confirmLabel: "Hủy tải",
    cancelLabel: "Tiếp tục tải",
    danger: true,
  }))) return;
  // Re-read: the download may have finished while the dialog was open.
  const current = fetches.get(scanId);
  if (!current || current.saving) return;
  if (!["failed", "cancelled"].includes(current.status.state)) {
    try {
      if (!(await invoke<boolean>("cancel_fetch", { scanId }))) {
        const latest = await invoke<FetchStatus | null>("fetch_status", { scanId }).catch(() => null);
        // Finished just now: it joins the queue as usual.
        if (latest?.state === "done") return;
      }
    } catch (error) {
      showError(`Không hủy được: ${String(error).slice(0, 160)}`);
      return;
    }
  }
  removeFetch(scanId);
}

/** Start a failed link again; the new download takes over its place in the player. */
async function refetch(entry: FetchEntry, authRetried = entry.authRetried): Promise<void> {
  const { status } = entry;
  const scanId = await startFetch(status.url, status.title ?? undefined, authRetried);
  const wasSelected = selectedFetchId === status.scan_id;
  window.clearTimeout(entry.timer);
  fetches.delete(status.scan_id);
  if (wasSelected) selectedFetchId = scanId;
  renderFetches();
}

/** A Douyin link refused for lack of a session: refresh it once and retry by itself. */
async function retryWithFreshSession(entry: FetchEntry): Promise<void> {
  entry.authRetried = true;
  entry.renewing = true;
  renderFetches();
  try {
    await douyin.ensureSession(true);
    if (fetches.get(entry.status.scan_id) !== entry) return;
    await refetch(entry, true);
  } catch {
    // Shown as failed with "Đăng nhập Douyin".
    entry.renewing = false;
    renderFetches();
  }
}

function failedFetchActions(entry: FetchEntry): HTMLButtonElement[] {
  const retry = actionButton("Thử lại", "btn primary", async () => {
    retry.disabled = true;
    try {
      await refetch(entry);
    } catch (error) {
      retry.disabled = false;
      showError(String(error).slice(0, 300));
    }
  });
  const skip = actionButton("Bỏ qua", "btn", () => removeFetch(entry.status.scan_id));
  // The zip carries the source worker's error log; a link job has no results folder yet.
  const report = actionButton("Gửi báo cáo lỗi", "btn", async () => {
    report.disabled = true;
    report.textContent = "Đang tạo báo cáo…";
    try {
      report.title = await invoke<string>("create_report", { jobId: entry.status.scan_id, outputDir: null });
      report.textContent = "Đã tạo báo cáo trên Desktop";
    } catch (error) {
      report.textContent = "Gửi báo cáo lỗi";
      showError(String(error).slice(0, 400));
    } finally {
      report.disabled = false;
    }
  });
  if (!needsDouyinLogin(entry.status)) return [retry, report, skip];
  retry.className = "btn";
  const login = actionButton("Đăng nhập Douyin", "btn primary", async () => {
    login.disabled = true;
    try {
      if (await douyin.open()) await refetch(entry, true);
    } catch (error) {
      showError(String(error).slice(0, 300));
    } finally {
      login.disabled = false;
    }
  });
  return [login, retry, skip];
}

// Download folder: copies of finished link downloads, under their titles.
const DOWNLOAD_DIR_KEY = "dubflow.downloadDir.v1";
let defaultDownloadDir = "";
/** A failed copy is reported once; the job then uses the file in DubFlow's store. */
let keepErrorShown = false;
/** The folder the user chose, or null for the default. */
function downloadDir(): string | null {
  try {
    return window.localStorage.getItem(DOWNLOAD_DIR_KEY) || null;
  } catch {
    return null;
  }
}
function renderDownloadDir(): void {
  const chosen = downloadDir();
  const label = $("#download-dir-label");
  label.textContent = chosen ?? (defaultDownloadDir || "Videos\\DubFlow Downloads");
  label.title = chosen ?? defaultDownloadDir;
  $("#reset-download-dir").hidden = !chosen;
}
$("#choose-download-dir").addEventListener("click", async () => {
  const folder = await invoke<string | null>("pick_folder").catch(() => null);
  if (!folder) return;
  try {
    window.localStorage.setItem(DOWNLOAD_DIR_KEY, folder);
  } catch {
    showError("Không lưu được lựa chọn thư mục trên máy này.");
  }
  keepErrorShown = false;
  renderDownloadDir();
});
$("#reset-download-dir").addEventListener("click", () => {
  try {
    window.localStorage.removeItem(DOWNLOAD_DIR_KEY);
  } catch {
    // Nothing stored.
  }
  keepErrorShown = false;
  renderDownloadDir();
});
void invoke<string>("default_download_dir").then((dir) => {
  defaultDownloadDir = dir;
  renderDownloadDir();
}).catch(() => undefined);
renderDownloadDir();

/** Copy a finished download to the download folder and queue it. */
async function finishFetch(entry: FetchEntry, mediaPath: string): Promise<void> {
  const { status } = entry;
  const title = fetchName(status);
  entry.saving = true;
  renderFetches();
  let path = mediaPath;
  try {
    path = await invoke<string>("keep_download", { mediaPath, title, folder: downloadDir() });
  } catch (error) {
    if (!keepErrorShown) {
      keepErrorShown = true;
      showError(`Không chép được video vào thư mục tải về; dùng bản trong kho của DubFlow. ${String(error).slice(0, 160)}`);
    }
  }
  // A pasted link learns its title while it downloads; its results folder follows.
  let outputDir = entry.outputDir;
  if (status.title?.trim()) {
    outputDir = await invoke<string>("link_titled_output", { outputDir, url: status.url, title: status.title, scanId: status.scan_id }).catch(() => outputDir);
    entry.outputDir = outputDir;
  }
  fetches.delete(status.scan_id);
  try {
    const linkJob = queue.addLinkJob(path, status.url, title, outputDir);
    if (linkJob) applyDefaults([linkJob]);
  } catch (error) {
    showError(`Không thêm được video vào danh sách: ${String(error).slice(0, 160)}`);
  }
  renderFetches();
}

// Rail navigation between the queue and the other screens.
/** Embedded pages follow the app window (their fallback windows are placed in screen pixels). */
const onWindowMoved = (listener: () => void): void => {
  if (isTauri) void getCurrentWindow().onMoved(listener).catch(() => undefined);
};
const douyin = mountDouyinLogin(invoke, onWindowMoved);
const discovery = mountDiscovery(invoke, async (url, title) => { await startFetch(url, title); }, () => {
  try {
    return window.localStorage.getItem("dubflow.hachimiAck.v1") === "1" ? "hachimi" : "argos";
  } catch {
    return "argos";
  }
}, onWindowMoved, async (text) => {
  // Only with a key the user already saved; the phrase alone goes to that provider.
  const key = claudeKey.read();
  return key ? chineseKeyword(claudeKey.provider(), key, text) : null;
});
const aiSettings = mountAiSettings(claudeKey, () => openSettings("account"));
mountSfxSettings(sfxLibrary, showError);
/** Folders whose "DubFlow Output" may hold earlier Bán hàng and Tổng hợp runs: the chosen one and the queue's. */
function outputRoots(): string[] {
  const roots = new Set<string>();
  const chosen = outputRoot();
  if (chosen) roots.add(chosen);
  for (const job of queue.getSnapshot().jobs) {
    const dir = job.outputDir ?? job.outputPath?.replace(/[\\/][^\\/]*$/, "");
    if (dir) roots.add(dir);
  }
  return [...roots].slice(0, 64);
}
async function playableUrl(path: string): Promise<string | null> {
  const allowed = await invoke<string | null>("allow_media", { path });
  return allowed ? versioned(path, convertFileSrc(allowed)) : null;
}
const productStudio = mountProductStudio(invoke, claudeKey, {
  outputRoot, cpuOnly, roots: outputRoots, mediaUrl: playableUrl,
  edit: (video) => void editVideo({ id: video.outputPath, kind: "product", outputPath: video.outputPath, displayName: video.displayName }, "product"),
});
const compilation = mountCompilation(invoke, claudeKey, {
  jobs: () => queue.getSnapshot().jobs, outputRoot, cpuOnly, roots: outputRoots, mediaUrl: playableUrl,
  edit: (video) => void editVideo({ id: video.outputPath, kind: "compilation", outputPath: video.outputPath, displayName: video.displayName }, "compilation"),
});
const docu = mountDocu(invoke, claudeKey, { jobs: () => queue.getSnapshot().jobs, outputRoot, roots: outputRoots, mediaUrl: playableUrl,
  edit: (video, seed) => void editVideo({ id: video.outputPath, kind: "docu", outputPath: video.outputPath, displayName: video.displayName, seed }, "docu"),
  // Claude working over MCP: a pulsing dot on "Phim tài liệu" wherever the user is.
  mcpBusy: label => {
    $("#nav-docu").classList.toggle("mcp-busy", Boolean(label));
    $("#nav-docu").title = label ? `Claude đang làm: ${label}` : "";
  },
});
// Every page's side panels: drag to resize, fold into a strip, sizes remembered.
mountLayoutPanels();
/** The Chỉnh sửa screen's list: finished videos of every page and videos on the computer (Issue #497). */
const editLibrary = mountEditLibrary(invoke, {
  jobs: () => queue.getSnapshot().jobs.filter((job) => job.status.state === "COMPLETED" && job.outputPath)
    .map((job): LibraryEntry => ({ path: job.outputPath!, title: job.displayName, kind: "job", jobId: job.id,
      modified: Math.floor(Date.parse(job.finishedAt ?? job.createdAt) / 1000) || 0 }))
    // The browser preview has no finished jobs of its own.
    .concat(isTauri ? [] : PREVIEW_EDIT_LIBRARY.map((item, index): LibraryEntry => ({ path: item.outputPath, title: item.title, kind: "job",
      modified: Math.floor(Date.now() / 1000) - 1_800 * (index + 1), sizeBytes: 46_000_000 }))),
  roots: outputRoots,
  open: (entry) => {
    const job = entry.jobId ? queue.getSnapshot().jobs.find((candidate) => candidate.id === entry.jobId) : undefined;
    const target: EditTarget = job ? jobTarget(job) : { id: entry.path, kind: entry.kind === "job" ? "file" : entry.kind, outputPath: entry.path, displayName: entry.title };
    return editVideo(target, "edit");
  },
  showError,
});
type View = "queue" | "discover" | "product" | "compilation" | "docu" | "edit";
const VIEW_HEADINGS: Record<View, [string, string]> = {
  queue: ["Hàng đợi", ""],
  discover: ["Khám phá", "Douyin, Bilibili, Kuaishou"],
  product: ["Video bán hàng", "Từ mô tả hoặc ảnh"],
  compilation: ["Tổng hợp", "Ghép các video đã xong"],
  docu: ["Phim tài liệu", "Kịch bản phim tài liệu tiếng Anh"],
  edit: ["Chỉnh sửa", "Chọn một video, hoặc kéo thả video vào cửa sổ"],
};
/** The page shown (the queue screen also hosts the editor). */
let currentView: View = "queue";
/** Show a page; `highlight` is the rail item lit (the page an edited video came from). */
function showView(view: View, highlight: View = view): void {
  currentView = view;
  // Chỉnh sửa has its own "Mở video trên máy"; the queue's link bar and "Thêm từ máy" would only confuse it.
  document.body.dataset.page = highlight;
  $("#edit-view").hidden = view !== "edit";
  $("#compilation-view").hidden = view !== "compilation";
  $("#docu-view").hidden = view !== "docu";
  $("#discover-view").hidden = view !== "discover";
  $("#product-view").hidden = view !== "product";
  $("#queue-content").hidden = view !== "queue";
  const [title, meta] = VIEW_HEADINGS[highlight];
  $("#page-title").textContent = title;
  $("#queue-summary").hidden = highlight !== "queue";
  $("#page-meta").hidden = highlight === "queue";
  // While editing, the header names the video.
  $("#page-meta").textContent = view === "queue" && highlight === "edit" && editor.target ? editor.target.displayName : meta;
  if (view !== "queue") setListOpen(false);
  for (const [id, active] of [["#nav-queue", highlight === "queue"], ["#nav-discover", highlight === "discover"], ["#nav-product", highlight === "product"], ["#nav-compilation", highlight === "compilation"], ["#nav-docu", highlight === "docu"], ["#nav-edit", highlight === "edit"]] as const) {
    $(id).classList.toggle("active", active);
    if (active) $(id).setAttribute("aria-current", "page");
    else $(id).removeAttribute("aria-current");
  }
  if (view === "discover") discovery.show();
  else discovery.hide();
  if (view === "product") productStudio.show();
  if (view === "compilation") compilation.show();
  if (view === "docu") docu.show();
  if (view === "edit") editLibrary.show();
  // A page shown again re-checks its panels against the window it now has.
  refitPanels();
}
/** Go to a page; editing another page's video ends first (asking when something is not saved). */
async function navigate(view: View): Promise<void> {
  if (editor.target) {
    // "Chỉnh sửa" shows the open edit; another page ends it first.
    if (view === "edit") return showView("queue", "edit");
    const origin = editOrigin;
    editOrigin = view;
    if (!(await editor.close(true))) {
      editOrigin = origin;
      return;
    }
    return;
  }
  showView(view);
}
for (const [id, view] of [["#nav-queue", "queue"], ["#nav-discover", "discover"], ["#nav-product", "product"], ["#nav-compilation", "compilation"], ["#nav-docu", "docu"], ["#nav-edit", "edit"]] as const) {
  $(id).addEventListener("click", (event) => {
    event.preventDefault();
    void navigate(view);
  });
}

async function startFetch(url: string, title?: string, authRetried = false): Promise<string> {
  // A Douyin link is opened in DubFlow's browser first to read the video's
  // details (a few seconds). The saved session is refreshed alongside, never
  // in the way: it is only the fallback when the page cannot be read.
  const note = "Đang mở video trên Douyin…";
  if (isDouyinLink(url)) {
    void douyin.ensureSession(false).catch(() => undefined);
    if (!queueState.textContent) showError(note);
  }
  let started: { status: FetchStatus; output_dir: string };
  try {
    started = await invoke<{ status: FetchStatus; output_dir: string }>("fetch_url",
      { url, title: title?.trim() || null, outputRoot: outputRoot(), downloadDir: downloadDir() });
  } finally {
    if (queueState.textContent === note) showError("");
  }
  const entry: FetchEntry = { status: started.status, outputDir: started.output_dir, timer: 0, samples: [], authRetried, renewing: false, saving: false };
  fetches.set(started.status.scan_id, entry);
  scheduleFetchPoll(entry);
  renderFetches();
  return started.status.scan_id;
}

async function pollFetch(scanId: string): Promise<void> {
  const entry = fetches.get(scanId);
  if (!entry) return;
  let status: FetchStatus | null;
  try {
    status = await invoke<FetchStatus | null>("fetch_status", { scanId });
  } catch (error) {
    if (fetches.get(scanId) !== entry) return;
    entry.status = { ...entry.status, state: "failed", error: String(error).slice(0, 200) };
    renderFetches();
    return;
  }
  // Cancelled or retried while the host answered.
  if (fetches.get(scanId) !== entry) return;
  if (!status) {
    scheduleFetchPoll(entry);
    return;
  }
  entry.status = status;
  if (status.state === "downloading") entry.samples = addSample(entry.samples, status.downloaded_bytes, Date.now());
  if (status.state === "done" && status.media_path) {
    await finishFetch(entry, status.media_path);
    return;
  }
  if (status.state === "cancelled") {
    removeFetch(scanId);
    return;
  }
  if (status.state === "failed") {
    if (needsDouyinLogin(status) && !entry.authRetried) void retryWithFreshSession(entry);
    else renderFetches();
    return;
  }
  scheduleFetchPoll(entry);
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
  // The header is on every screen; the downloads show up in the queue.
  showView("queue");
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
  showError(notes.length ? `${links.length - failures.length}/${links.length} link đã được thêm vào hàng tải. ${notes.join(" · ")}`.slice(0, 600) : "");
});
// Pasting several lines into the one-line box keeps the line breaks as spaces.
$<HTMLInputElement>("#link-input").addEventListener("paste", (event) => {
  const text = event.clipboardData?.getData("text") ?? "";
  if (!/[\r\n]/.test(text)) return;
  event.preventDefault();
  const input = event.currentTarget as HTMLInputElement;
  input.value = text.replace(/\s+/g, " ").trim();
});
// Narrow windows get a shorter hint so the box never shows a clipped sentence.
const narrowHeader = window.matchMedia("(max-width: 1199.98px)");
function syncLinkHint(): void {
  $<HTMLInputElement>("#link-input").placeholder = narrowHeader.matches ? "Dán link video" : "Dán link Douyin, Bilibili hoặc Kuaishou";
}
narrowHeader.addEventListener("change", syncLinkHint);
syncLinkHint();

// Review: edit Vietnamese text per cue, then re-run in the same output folder.
type ReviewCue = {
  cue_id: string; start_ms: number; end_ms: number; source_text: string;
  machine_text: string; text: string; edited: boolean; dub_failed: boolean; speaker?: string | null;
};
type ReviewSpeaker = { id: string; gender?: string; voice_id: string; cues?: number };
let reviewSpeakers: ReviewSpeaker[] = [];
/** Voice per speaker as currently chosen in the review dialog. */
const speakerChoice = new Map<string, string>();

function speakersChanged(): boolean {
  return reviewSpeakers.some((speaker) => (speakerChoice.get(speaker.id) ?? speaker.voice_id) !== speaker.voice_id);
}

function renderReviewSpeakers(): void {
  const panel = $("#review-speakers");
  panel.hidden = reviewSpeakers.length < 2 || !voiceCatalog;
  if (panel.hidden || !voiceCatalog) return;
  const catalog = voiceCatalog;
  $("#review-speaker-list").replaceChildren(...reviewSpeakers.map((speaker) => {
    const row = element("label", "review-speaker");
    const gender = speaker.gender === "male" ? "giọng nam" : speaker.gender === "female" ? "giọng nữ" : "";
    row.append(element("span", "speaker-chip", speaker.id), element("span", "", [gender, speaker.cues ? `${speaker.cues} câu` : ""].filter(Boolean).join(" · ")));
    const select = document.createElement("select");
    for (const voice of catalog.voices) {
      const option = document.createElement("option");
      option.value = voice.voice_id;
      option.textContent = `${voice.name} (${voice.gender}, ${voice.accent})`;
      select.append(option);
    }
    select.value = speakerChoice.get(speaker.id) ?? speaker.voice_id;
    select.addEventListener("change", () => {
      speakerChoice.set(speaker.id, select.value);
      updateReviewControls();
    });
    row.append(select);
    return row;
  }));
}
// Estimate only: the built-in voice failed a 3.9 syllable/s cue in real runs
// while ~2.5 syllable/s cues fit, so warn above 3.5.
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

/** Pace of the phrase this cue is dubbed in, for the current drafts. */
function pace(cue: ReviewCue): number {
  return phrasePaces(reviewCues, (cueId) => reviewDrafts.draftOf(cueId)).get(cue.cue_id) ?? 0;
}

function draftOf(cue: ReviewCue): string {
  return reviewDrafts.draftOf(cue.cue_id);
}

function needsAttention(cue: ReviewCue): boolean {
  return cue.dub_failed || pace(cue) > FAST_PHRASE_SYLLABLES_PER_SECOND;
}

function reviewDirty(): boolean {
  return reviewDrafts.isDirty;
}

function updateReviewControls(): void {
  reviewApply.disabled = reviewSaving || !(reviewDrafts.canSave || (speakersChanged() && !reviewDrafts.isDirty));
  reviewUndo.disabled = reviewSaving || !reviewDrafts.canUndo;
}

function renderReviewMeta(cue: ReviewCue, meta: HTMLElement, item: HTMLElement): void {
  const text = draftOf(cue);
  const rate = pace(cue);
  meta.replaceChildren();
  if (cue.dub_failed) meta.append(element("span", "badge err", "Lồng tiếng lỗi"));
  if (text.trim() !== cue.machine_text.trim()) meta.append(element("span", "badge run", "Đã sửa"));
  const fast = rate > FAST_PHRASE_SYLLABLES_PER_SECOND;
  // Close lines of one speaker are dubbed as one phrase and share its pace: say so.
  const joined = (phraseSizes(reviewCues).get(cue.cue_id) ?? 1) > 1;
  const speed = element("span", `pace${fast ? " fast" : ""}`,
    `${joined ? "Cả cụm câu liền nhau: " : ""}${rate.toFixed(1).replace(".", ",")} tiếng/giây${fast ? ` · có thể không kịp lồng tiếng, nên rút gọn${joined ? " cụm này" : ""}` : ""}`);
  if (joined) speed.title = "Các câu sát nhau của cùng một người được lồng tiếng chung một lượt, nên tốc độ tính cho cả cụm.";
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
  const paces = phrasePaces(reviewCues, (cueId) => reviewDrafts.draftOf(cueId));
  const fast = reviewCues.filter((cue) => !cue.dub_failed && (paces.get(cue.cue_id) ?? 0) > FAST_PHRASE_SYLLABLES_PER_SECOND).length;
  $("#review-summary").textContent = [`${reviewCues.length} câu`, failed ? `${failed} câu lồng tiếng lỗi` : "",
    fast ? `${fast} câu có thể không kịp lồng tiếng` : ""].filter(Boolean).join(" · ");
  if (!visible.length) reviewList.append(element("li", "review-empty", "Không có câu nào cần xem."));
  for (const cue of visible) {
    const item = element("li", "review-cue");
    const time = element("div", "review-time");
    time.append(element("b", "", clock(cue.start_ms)), document.createTextNode(`${((cue.end_ms - cue.start_ms) / 1000).toFixed(1)} giây`));
    if (cue.speaker) time.append(element("span", "speaker-chip", cue.speaker));
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
  try {
    reviewSpeakers = await invoke<ReviewSpeaker[]>("review_speakers", { outputPath: job.outputPath });
  } catch {
    reviewSpeakers = [];
  }
  speakerChoice.clear();
  renderReviewSpeakers();
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

let reviewClosing = false;
async function closeReview(): Promise<void> {
  if (reviewClosing || review.hidden) return;
  clipPlayer?.pause();
  if (reviewDirty() || speakersChanged()) {
    reviewClosing = true;
    const discard = await confirmDialog({
      title: "Bỏ các thay đổi chưa lưu?",
      message: "Các câu đã sửa và giọng đã đổi trong lần xem này sẽ mất.",
      confirmLabel: "Bỏ thay đổi",
      cancelLabel: "Sửa tiếp",
      danger: true,
    }).finally(() => { reviewClosing = false; });
    if (!discard) return;
  }
  speakerChoice.clear();
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
  const overrides = reviewOverrides(reviewCues, (cueId) => reviewDrafts.draftOf(cueId));
  reviewSaving = true;
  updateReviewControls();
  try {
    const revisionId = await saveReviewRevision(invoke, queue, job, overrides);
    review.hidden = true;
    reviewJobId = null;
    reviewDrafts.reset(reviewCues);
    if (speakersChanged()) {
      queue.setSpeakerVoices(revisionId, Object.fromEntries(reviewSpeakers.map((speaker) => [speaker.id, speakerChoice.get(speaker.id) ?? speaker.voice_id])));
    }
    speakerChoice.clear();
    const revision = queue.getSnapshot().jobs.find((candidate) => candidate.id === revisionId);
    if (revision) await startJob(revision);
  } catch (error) {
    $("#review-hint").textContent = `Không lưu được: ${String(error).slice(0, 200)}`;
  } finally {
    reviewSaving = false;
    updateReviewControls();
  }
});
/** Kết quả › "Sửa phụ đề & giọng đọc" / "Đóng gói, Shorts, đổi khung": the job's tools live in Chỉnh sửa. */
async function editJobAt(mode: "subs" | "more"): Promise<void> {
  const job = selectedJob();
  if (!job?.outputPath || job.status.state !== "COMPLETED") return;
  if (!(await editVideo(jobTarget(job), "queue"))) return;
  // A job without its translation has no Phụ đề: the full review still opens.
  if (!editor.show(mode) && mode === "subs") void openReview();
}
$("#open-review").addEventListener("click", () => void editJobAt("subs"));
$("#open-more").addEventListener("click", () => void editJobAt("more"));

// "Viết lại kiểu thuyết minh": the AI retells each cue in the narrator's voice,
// sized to the cue; one undo restores the previous drafts.
$("#review-narrate").addEventListener("click", async () => {
  const job = queue.getSnapshot().jobs.find((candidate) => candidate.id === reviewJobId);
  const button = $<HTMLButtonElement>("#review-narrate");
  const hint = $("#review-hint");
  if (!job || reviewSaving || !reviewCues.length) return;
  const provider = claudeKey.provider();
  const { name, company } = PROVIDERS[provider];
  const key = claudeKey.read();
  if (!key) {
    hint.textContent = `Chưa có khóa ${name}. Hãy lưu khóa trong “Cài đặt › Tài khoản” rồi thử lại.`;
    aiSettings.open();
    return;
  }
  button.disabled = true;
  hint.textContent = `${name} đang viết lại lời thuyết minh…`;
  try {
    const lines = await rewriteAsNarration(provider, key, reviewCues.map((cue) => ({ ...cue, text: draftOf(cue) })),
      $<HTMLSelectElement>("#review-narrate-style").value as NarrationStyle, {
        title: job.displayName,
        onProgress: (done, total) => {
          if (total > 1 && done < total) hint.textContent = `${name} đang viết lại lời thuyết minh… (phần ${done + 1}/${total})`;
        },
      });
    reviewDrafts.replaceDrafts(lines);
    reviewOnlyIssues.checked = false;
    renderReview();
    hint.textContent = `Đã viết lại ${lines.size} câu (lời thoại đã gửi tới ${company}). Đọc lại và sửa, rồi bấm “Lưu & xử lý lại”; bấm “Hoàn tác” để quay lại bản cũ.`;
  } catch (error) {
    hint.textContent = isKeyError(error) ? `Khóa ${name} không hợp lệ; hãy lưu lại trong “Cài đặt › Tài khoản”.` : `Không viết lại được: ${String(error).slice(0, 200)}`;
  } finally {
    button.disabled = false;
  }
});
// "Gợi ý đăng bài" opens Chỉnh sửa → Tạo thêm → Nội dung đăng bài (YouTube, Facebook, TikTok).
$("#open-post").addEventListener("click", () => void editJobAt("more").then(() => {
  const posts = $<HTMLDetailsElement>("#posts-section");
  if (posts.hidden) return;
  posts.open = true;
  posts.scrollIntoView({ block: "start" });
}));
$("#review-close").addEventListener("click", () => void closeReview());
$("#review-cancel").addEventListener("click", () => void closeReview());
reviewUndo.addEventListener("click", undoReview);
reviewOnlyIssues.addEventListener("change", renderReview);
review.addEventListener("keydown", (event) => {
  if (event.key === "Escape") void closeReview();
  if (event.ctrlKey && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "z"
    && !(event.target instanceof HTMLTextAreaElement)) {
    event.preventDefault();
    undoReview();
  }
});

// Disk usage in Settings, the "Dọn dẹp" dialog and the automatic clean.
let noticeTimer = 0;
function showNotice(text: string): void {
  const notice = $("#queue-notice");
  notice.textContent = text;
  window.clearTimeout(noticeTimer);
  noticeTimer = window.setTimeout(() => { notice.textContent = ""; }, 10_000);
}
const storage = mountStorage(invoke, { outputs: () => queueOutputs(queue.getSnapshot().jobs), notice: showNotice });

// Tell the user in the taskbar title when a video finishes while they are away.
const lastStates = new Map<string, JobStatusState>();
const baseTitle = document.title;
window.addEventListener("focus", () => { document.title = baseTitle; });

queue.subscribe((snapshot) => {
  for (const job of snapshot.jobs) {
    const previous = lastStates.get(job.id);
    if (previous && ACTIVE.includes(previous) && job.status.state === "COMPLETED") storage.jobFinished();
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
  applySourceLanguage(queue, job.id, sourceLanguageSelect.value);
  rememberDefaults(selectedJob() ?? job);
});
// Ask once per viewer before the first opt-in: the model's licence terms are unverified.
const HACHIMI_ACK_KEY = "dubflow.hachimiAck.v1";
async function hachimiAcknowledged(): Promise<boolean> {
  try {
    if (window.localStorage.getItem(HACHIMI_ACK_KEY) === "1") return true;
  } catch {
    // Without storage, ask every time.
  }
  const accepted = await confirmDialog({
    title: "Dùng bộ dịch HachimiMT-60?",
    message: "HachimiMT-60 là bộ dịch thử nghiệm. Giấy phép chưa được xác minh cho mục đích thương mại "
      + "(model được huấn luyện có dữ liệu chưng cất từ Gemini và một bộ dữ liệu GPL-3.0). Chỉ dùng cá nhân/thử nghiệm và tự kiểm tra điều khoản trước khi dùng thương mại.\n\n"
      + "Lần đầu dùng sẽ tải model khoảng 58 MB.",
    confirmLabel: "Tiếp tục",
    cancelLabel: "Dùng Argos",
  });
  if (accepted) {
    try {
      window.localStorage.setItem(HACHIMI_ACK_KEY, "1");
    } catch {
      // Acknowledgement is a convenience; asking again is safe.
    }
  }
  return accepted;
}
translationEngineSelect.addEventListener("change", async () => {
  const job = selectedJob();
  if (!job) return;
  const requested = translationEngineSelect.value;
  // Only an actual Hachimi opt-in (Chinese source) needs the acknowledgement.
  const accepted = chooseTranslator(requested, job.sourceLanguage, () => false) === null ? await hachimiAcknowledged() : true;
  const current = queue.getSnapshot().jobs.find((candidate) => candidate.id === job.id);
  if (!current || current.status.state !== "QUEUED") return;
  const engine = chooseTranslator(requested, current.sourceLanguage, () => accepted);
  if (engine === null) {
    translationEngineSelect.value = current.translationEngine ?? "argos";
    return;
  }
  queue.setTranslationEngine(current.id, engine);
  rememberDefaults(selectedJob() ?? current);
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
  blurDraft = job.blurRegions?.[0] ?? currentDefaults().blurRegions?.[0] ?? DEFAULT_REGION;
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
coverStyleSelect.addEventListener("change", () => {
  const style = normalizeCoverStyle({ ...rememberedCoverStyle(), mode: coverStyleSelect.value as CoverMode });
  try {
    window.localStorage.setItem(COVER_STORAGE_KEY, JSON.stringify(style));
  } catch {
    // Without storage new videos keep edge stretch.
  }
  renderVoiceControls(selectedJob());
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
separateToggle.addEventListener("change", () => {
  const job = selectedJob();
  if (!job) return;
  queue.setSeparateBackground(job.id, separateToggle.checked);
  rememberDefaults(selectedJob() ?? job);
});
multiVoiceToggle.addEventListener("change", () => {
  const job = selectedJob();
  if (!job) return;
  queue.setMultiVoice(job.id, multiVoiceToggle.checked);
  rememberDefaults(selectedJob() ?? job);
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
  const voiceId = currentDefaults().voiceId;
  for (const id of awaitingCatalog) {
    try {
      applyDubbingDefault(id, voiceId);
    } catch {
      // Removed, started or changed by the user meanwhile.
    }
  }
  awaitingCatalog.clear();
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
  })
  .finally(() => mountUpdates(invoke, (version) => {
    // A found update also shows as a compact button in the header and a dot on Cài đặt.
    updateVersion = version;
    $("#header-update").hidden = version === null;
    $("#header-update-label").textContent = version ? `Cập nhật ${version}` : "Cập nhật";
    $("#nav-settings").classList.toggle("has-update", version !== null);
    $("#settings-tab-update").classList.toggle("has-update", version !== null);
    $("#update-summary").textContent = version
      ? `Đang dùng ${$("#release-version").textContent}, có bản ${version}.`
      : "DubFlow tự kiểm tra bản mới khi mở ứng dụng và mỗi 6 giờ.";
  }));
$("#installed-versions").addEventListener("click", () => $("#release-version").click());

// Settings dialog, opened from the rail and from the header's update button.
// Groups are tabs on the left; the chosen one shows on the right and is remembered.
const settingsDialog = $("#settings-dialog");
let settingsReturnFocus: HTMLElement | null = null;
/** The newer version found, while one is waiting to be installed. */
let updateVersion: string | null = null;
const SETTINGS_TAB_KEY = "dubflow.settingsTab.v1";
const settingsTabs = [...document.querySelectorAll<HTMLButtonElement>("#settings-dialog .settings-tab")];
function settingsTabOf(name: string | null): HTMLButtonElement | undefined {
  return settingsTabs.find((tab) => tab.dataset.tab === name);
}
function showSettingsTab(name: string, focus = false): void {
  const chosen = settingsTabOf(name) ?? settingsTabs[0];
  for (const tab of settingsTabs) {
    const on = tab === chosen;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    const pane = document.getElementById(tab.getAttribute("aria-controls") ?? "");
    if (pane) pane.hidden = !on;
  }
  if (focus) chosen.focus();
  const pane = document.getElementById(chosen.getAttribute("aria-controls") ?? "");
  if (pane) pane.scrollTop = 0;
  try { window.localStorage.setItem(SETTINGS_TAB_KEY, chosen.dataset.tab ?? ""); } catch { /* opens at Tài khoản next time */ }
}
function rememberedSettingsTab(): string {
  try {
    const saved = window.localStorage.getItem(SETTINGS_TAB_KEY);
    if (settingsTabOf(saved)) return saved as string;
  } catch { /* first tab */ }
  return "account";
}
for (const tab of settingsTabs) tab.addEventListener("click", () => showSettingsTab(tab.dataset.tab ?? "account"));
$("#settings-dialog .settings-tabs").addEventListener("keydown", (event) => {
  const index = settingsTabs.findIndex((tab) => tab === document.activeElement);
  if (index < 0) return;
  const next = event.key === "ArrowDown" ? index + 1 : event.key === "ArrowUp" ? index - 1
    : event.key === "Home" ? 0 : event.key === "End" ? settingsTabs.length - 1 : null;
  if (next === null) return;
  event.preventDefault();
  showSettingsTab(settingsTabs[(next + settingsTabs.length) % settingsTabs.length].dataset.tab ?? "account", true);
});
/** Opens Cài đặt at `tab`; without one, at a waiting update or the last tab used. */
function openSettings(tab?: string): void {
  if (settingsDialog.hidden) {
    settingsReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    aiSettings.refresh();
  }
  settingsDialog.hidden = false;
  $("#nav-settings").classList.add("active");
  const target = tab ?? (updateVersion !== null ? "update" : rememberedSettingsTab());
  showSettingsTab(target);
  $<HTMLButtonElement>(`#settings-tab-${settingsTabOf(target) ? target : "account"}`).focus();
  void douyin.refreshAccount();
  storage.refreshSummary();
  // With an update waiting, Settings opens at it.
  if (target === "update" && updateVersion !== null) {
    const install = $<HTMLButtonElement>("#update-install");
    if (!$("#update-card").hidden && !install.disabled) install.focus();
  }
}
function closeSettings(): void {
  if (settingsDialog.hidden) return;
  settingsDialog.hidden = true;
  $("#nav-settings").classList.remove("active");
  if (settingsReturnFocus?.isConnected) settingsReturnFocus.focus();
}
$("#nav-settings").addEventListener("click", () => openSettings());
$("#header-update").addEventListener("click", () => openSettings("update"));
$("#settings-close").addEventListener("click", closeSettings);
// Esc closes the topmost dialog: one opened from Cài đặt (versions, cleanup, Douyin) before Cài đặt itself.
const SETTINGS_CHILDREN = ["versions", "cleanup", "douyin"];
function closeTopSettingsChild(): boolean {
  for (const name of SETTINGS_CHILDREN) {
    if (!$(`#${name}-dialog`).hidden) {
      $<HTMLButtonElement>(`#${name}-close`).click();
      return true;
    }
  }
  return false;
}
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || settingsDialog.hidden || document.querySelector(".confirm-backdrop")) return;
  event.preventDefault();
  event.stopPropagation();
  if (!closeTopSettingsChild()) closeSettings();
}, true);
settingsDialog.addEventListener("mousedown", (event) => {
  if (event.target === settingsDialog) closeSettings();
});
const rememberToggle = $<HTMLInputElement>("#remember-defaults");
rememberToggle.checked = rememberEnabled();
rememberToggle.addEventListener("change", () => {
  try {
    if (rememberToggle.checked) window.localStorage.removeItem(REMEMBER_KEY);
    else window.localStorage.setItem(REMEMBER_KEY, "0");
  } catch {
    rememberToggle.checked = !rememberToggle.checked;
    showError("Không lưu được lựa chọn này trên máy này.");
  }
});

// Right column tabs: Kết quả / Thiết lập / Tạo thêm, with arrow-key movement.
const tabButtons = [...document.querySelectorAll<HTMLButtonElement>("#detail-tabs [data-tab]")];
for (const button of tabButtons) {
  button.addEventListener("click", () => selectTab(button.dataset.tab as DetailTab));
  button.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const next = tabButtons[(tabButtons.indexOf(button) + (event.key === "ArrowRight" ? 1 : tabButtons.length - 1)) % tabButtons.length];
    selectTab(next.dataset.tab as DetailTab);
    next.focus();
  });
}

// List filter: Tất cả / Đang chạy / Xong, applied here in the window.
for (const button of document.querySelectorAll<HTMLButtonElement>("#queue-filter [data-filter]")) {
  button.addEventListener("click", () => {
    queueFilter = button.dataset.filter === "running" ? "running" : button.dataset.filter === "done" ? "done" : "all";
    for (const other of document.querySelectorAll<HTMLButtonElement>("#queue-filter [data-filter]")) {
      other.setAttribute("aria-selected", String(other === button));
    }
    renderFetches();
    if (lastModel) renderQueue(lastModel.view);
  });
}

// Below 1200 px the list becomes a slide-over: "Danh sách" opens it; Esc or a click outside closes it.
function setListOpen(open: boolean): void {
  queueScreen.classList.toggle("list-open", open);
  listToggle.setAttribute("aria-expanded", String(open));
}
listToggle.addEventListener("click", () => setListOpen(!queueScreen.classList.contains("list-open")));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && queueScreen.classList.contains("list-open")) {
    setListOpen(false);
    listToggle.focus();
  }
});
document.addEventListener("mousedown", (event) => {
  if (!queueScreen.classList.contains("list-open")) return;
  const target = event.target as Node;
  if (!$("#queue-col").contains(target) && !listToggle.contains(target)) setListOpen(false);
});
window.addEventListener("resize", () => {
  if (window.innerWidth >= 1200) setListOpen(false);
});

// Custom title bar: drag from empty header space, double-click to maximize.
// In the browser preview there is no window to control, so the buttons do nothing.
const appWindow = isTauri ? getCurrentWindow() : null;
async function syncMaximized(): Promise<void> {
  if (!appWindow) return;
  const maximized = await appWindow.isMaximized().catch(() => false);
  document.documentElement.classList.toggle("window-maximized", maximized);
  const button = $("#window-maximize");
  button.title = maximized ? "Khôi phục" : "Phóng to";
  button.setAttribute("aria-label", button.title);
}
$("#window-minimize").addEventListener("click", () => void appWindow?.minimize());
$("#window-maximize").addEventListener("click", () => void appWindow?.toggleMaximize());
// A pending edit autosave is written first.
$("#window-close").addEventListener("click", () => void editor.flush().finally(() => appWindow?.close()));
$("#app-header").addEventListener("mousedown", (event) => {
  if (!appWindow || event.button !== 0) return;
  if ((event.target as Element).closest("button, a, input, select, textarea, label, form")) return;
  if (event.detail === 2) void appWindow.toggleMaximize();
  else void appWindow.startDragging();
});
if (appWindow) {
  void syncMaximized();
  void appWindow.onResized(() => void syncMaximized()).catch(() => undefined);
}

// Native drag & drop: Tauri intercepts file drops and reports real paths.
if (isTauri) {
  type DropEvent = { payload: { type: "enter" | "over" | "drop" | "leave"; paths?: string[]; position?: { x: number; y: number } } };
  void getCurrentWebview().onDragDropEvent((event: DropEvent) => {
    const overlay = $("#drag-overlay");
    // While editing, dropped videos are inserted into the edit instead of joining the queue;
    // on the Chỉnh sửa list one opens for editing.
    const editing = editor.target !== null;
    const picking = !editing && currentView === "edit";
      if (event.payload.type === "enter" || event.payload.type === "over") {
        overlay.hidden = false;
        $("#drag-overlay-text").textContent = editing ? "Thả để chèn vào video đang sửa" : picking ? "Thả để mở video và chỉnh sửa" : "Thả để thêm video";
        dropZone.classList.add("hover");
      } else {
        overlay.hidden = true;
        dropZone.classList.remove("hover");
        if (event.payload.type !== "drop") return;
        const paths = event.payload.paths ?? [];
        const position = event.payload.position;
        // Tauri reports physical pixels.
        const point = position ? { x: position.x / window.devicePixelRatio, y: position.y / window.devicePixelRatio } : null;
        if (editing) editor.drop(paths, point);
        else if (picking) editLibrary.drop(paths);
        else void addDropped(paths);
      }
  }).catch(() => undefined);
}

window.addEventListener("beforeunload", () => shell.dispose());

// Nghe thử beside the other Vietnamese voice pickers (Bán hàng, Tổng hợp, Chỉnh sửa › Mẫu).
for (const id of ["product-voice", "compilation-voice", "tpl-voice"]) {
  const select = document.getElementById(id);
  if (!(select instanceof HTMLSelectElement) || select.parentElement?.classList.contains("voice-pick")) continue;
  const wrap = document.createElement("span");
  wrap.className = "voice-pick";
  select.replaceWith(wrap);
  wrap.append(select, voiceSampler.forSelect(select));
}
