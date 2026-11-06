import { PROVIDERS, type AiSession } from "../ai/provider.ts";
import { confirmDialog } from "../../shell/confirm.ts";
import {
  AMBIENT_SFX, CHAPTER_NAMES, CHAPTER_ORDER, DEFAULT_MOODS, GRAPHIC_NAMES, GRAPHIC_TYPES, MAX_NARRATION, MOODS, MOOD_NAMES, SFX_NAMES, readable, sceneName, sourceName, sourceNames,
  NARRATION_SPEEDS, acknowledge, applyFlags, buildRequest, chapterTargetSeconds, completedDocuJobs, estimatedSeconds, flattenScenes,
  hasNarration, moveItem, narrationStale, parseCapcut, parseMcp, parseNarration, parseOpen, parseMcpActivity, parseRender, parseRenderOptions, parseRuns, parseVisual, parseVoices, planStale,
  calloutTexts, midrollHint, renumberScenes, sourceTitle, wordCount, type CapcutExport, type DocuLabel, type NarrationInfo, type NarrationVoice, type RenderInfo, type VisualInfo,
  type DocuJob, type DocuRequest, type DocuRun, type DocuScene, type DocuScript, type FramingPreset, type McpActivity, type GraphicType, type Mood, type OpenDocuRun, type SourceForm,
} from "./model.ts";
import { writeScript } from "./ai.ts";
import { closeDrawerOutside } from "../../shell/drawer.ts";
import { addText, type Edit } from "../edit/model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Settings = {
  jobs(): readonly DocuJob[]; outputRoot(): string | null; roots?(): string[]; mediaUrl?(path: string): Promise<string | null>;
  /** Open the rough cut in the editor; `seed` fills its first draft. */
  edit?(video: { outputPath: string; displayName: string }, seed: (edit: Edit) => Edit): void;
  /** What Claude is doing over MCP now (null when idle), for the rail. */
  mcpBusy?(label: string | null): void;
};
const escapeHtml = (value: unknown): string => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]!));
const videoName = (path: string): string => path.split(/[\\/]/).at(-1) ?? path;
const clock = (seconds: number): string => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
const emptyScript = (title = ""): DocuScript => ({ project_title: title, target_Coordinates_3D: null,
  chapters: CHAPTER_ORDER.map(chapter_type => ({ chapter_type, recommended_bgm_mood: DEFAULT_MOODS[chapter_type], scenes: [] })), review_notes: [] });
const emptyScene = (): DocuScene => ({ scene_id: "", narration_text: "", matched_source_refs: [], visual_search_tags: [],
  on_screen_graphic: null, ambient_sfx_tag: null, needs_review: false,
  review_notes: [], review_numbers: [], acknowledged_numbers: [] });

/**
 * Loads media into the one player. URL lookups can resolve out of order, so only the newest load,
 * still on the project it started on, may apply its result; `cancel` voids any load in flight.
 * `apply` gets a `current()` check for work it defers (a seek waiting for metadata).
 */
export function playerLoader(mediaUrl: (path: string) => Promise<string | null>, project: () => string | undefined) {
  let latest = 0;
  return {
    async load(path: string, apply: (url: string | null, current: () => boolean) => void): Promise<boolean> {
      const request = ++latest, dir = project();
      const current = () => request === latest && project() === dir;
      const url = await mediaUrl(path).catch(() => null);
      if (!current()) return false;
      apply(url, current);
      return true;
    },
    cancel(): void { latest++; },
  };
}

/** The documentary source-and-script workspace (ADR-0049, phase 1). */
export function mountDocu(invoke: Invoke, session: AiSession, settings: Settings): { show(): void } {
  const root = document.querySelector<HTMLElement>("#docu-view")!;
  root.innerHTML = `
    <div id="docu-screen" class="queue-screen docu-screen">
      <section class="queue-col docu-col" aria-labelledby="docu-list-title">
        <div class="queue-col-head"><h2 id="docu-list-title">Dự án</h2><button id="docu-refresh" class="quiet-btn" type="button">Làm mới</button></div>
        <button id="docu-mcp-chip" class="docu-mcp-chip" type="button" hidden><span id="docu-mcp-chip-text"></span></button>
        <div class="queue-scroll"><ul id="docu-runs" class="queue-list" aria-label="Dự án phim tài liệu"></ul><p id="docu-runs-empty" class="queue-empty">Chưa có dự án phim tài liệu nào.</p></div>
        <div class="queue-col-foot"><button id="docu-new" class="btn block" type="button">Tạo dự án mới</button></div>
      </section>
      <section class="player-col docu-player-col" aria-label="Xem trước và kịch bản">
        <div class="player-head"><button id="docu-list-toggle" class="btn ghost list-toggle" type="button" aria-controls="docu-screen" aria-expanded="false">Dự án</button><h2 id="docu-project-name" class="player-title">Tạo dự án mới</h2></div>
        <div class="docu-workspace">
          <section id="docu-player" class="docu-player" aria-label="Xem trước">
            <div class="docu-player-head">
              <div class="docu-seg" role="group" aria-label="Xem video nào">
                <button id="docu-show-source" type="button" aria-pressed="true">Video nguồn</button>
                <button id="docu-show-draft" type="button" aria-pressed="false" disabled title="Chưa có bản nháp: dựng ở thẻ Dựng phim">Bản nháp</button>
              </div>
              <select id="docu-player-source" aria-label="Chọn video nguồn"></select>
              <span id="docu-player-badge" class="docu-player-badge"></span>
            </div>
            <video id="docu-video" controls playsinline preload="metadata" aria-label="Video xem trước"></video>
            <p id="docu-player-empty" class="player-placeholder">Chọn dự án có nguồn video để xem trước.</p>
          </section>
          <div class="docu-editor-head"><h3>Kịch bản theo cảnh</h3><span>Bấm mốc nguồn trong cảnh để xem đoạn đó ở khung trên.</span></div>
          <div id="docu-editor" class="docu-editor"><p class="queue-empty">Chọn dự án hoặc tạo dự án mới để bắt đầu.</p></div>
        </div>
      </section>
      <aside class="inspector docu-inspector" aria-label="Thiết lập và các bước làm phim">
        <div class="tabs" role="tablist" aria-label="Phim tài liệu">
          <button id="docu-tab-setup-btn" type="button" role="tab" data-tab="setup" aria-controls="docu-tab-setup" aria-selected="true">Dự án</button>
          <button id="docu-tab-script-btn" type="button" role="tab" data-tab="script" aria-controls="docu-tab-script" aria-selected="false">Kịch bản</button>
          <button id="docu-tab-produce-btn" type="button" role="tab" data-tab="produce" aria-controls="docu-tab-produce" aria-selected="false">Dựng phim</button>
          <button id="docu-tab-claude-btn" type="button" role="tab" data-tab="claude" aria-controls="docu-tab-claude" aria-selected="false">Claude</button>
        </div>
        <div id="docu-tab-setup" class="tab-panel" role="tabpanel" aria-labelledby="docu-tab-setup-btn">
          <div class="tab-scroll">
            <section id="docu-project-view" class="docu-card" aria-labelledby="docu-project-view-title" hidden>
              <h3 id="docu-project-view-title">Thông tin dự án</h3>
              <dl id="docu-project-info" class="docu-summary"></dl>
            </section>
            <fieldset id="docu-project-fields" class="docu-fields">
              <section class="docu-card" aria-labelledby="docu-new-title">
                <h3 id="docu-new-title">Dự án mới</h3>
                <label>Tiêu đề dự án<input id="docu-title" maxlength="120" /></label>
                <label>Chủ đề hoặc ghi chú<textarea id="docu-topic" rows="3" maxlength="600"></textarea></label>
                <label>Thời lượng mục tiêu<select id="docu-minutes">${Array.from({ length: 18 }, (_, i) => `<option value="${i + 3}" ${i === 8 ? "selected" : ""}>${i + 3} phút</option>`).join("")}</select></label>
                <p id="docu-minutes-hint" class="hint" role="status" hidden></p>
              </section>
              <section class="docu-card docu-source-picker" aria-labelledby="docu-source-title">
                <h3 id="docu-source-title">Nguồn (2–5)</h3>
                <label>Video đã hoàn tất<select id="docu-job-select"></select></label>
                <button id="docu-add-job" class="btn" type="button">Thêm video đã xong</button>
                <button id="docu-add-file" class="btn" type="button">Thêm video (kèm phụ đề nếu có)</button>
                <p class="hint">Video không có phụ đề .srt: DubFlow tự nhận dạng lời khi tạo dự án (vài phút mỗi video).</p>
                <ol id="docu-source-order" class="docu-source-order"></ol>
              </section>
              <section class="docu-card" aria-labelledby="docu-look-title">
                <h3 id="docu-look-title">Hình thức</h3>
                <label>Khung hình<select id="docu-framing"><option value="none">Không</option><option value="letterbox_235">Viền đen điện ảnh 2,35:1</option><option value="overscan_108">Phóng nhẹ 108% (cắt mép)</option></select></label>
                <label class="docu-check"><input id="docu-credits" type="checkbox" /> Chèn màn hình credit cuối video</label>
                <label>Thư mục lưu<input id="docu-output-root" readonly /></label>
              </section>
              <p id="docu-setup-state" class="hint" role="status"></p>
              <button id="docu-create" class="btn primary block" type="button">Tạo dự án và phân tích nguồn</button>
            </fieldset>
          </div>
        </div>
        <div id="docu-tab-script" class="tab-panel" role="tabpanel" aria-labelledby="docu-tab-script-btn" hidden>
          <div class="tab-scroll">
            <section class="docu-card" aria-labelledby="docu-write-title">
              <h3 id="docu-write-title">Viết kịch bản</h3>
              <button id="docu-write" class="btn primary block" type="button">Viết kịch bản bằng AI</button>
              <button id="docu-check" class="btn block" type="button">Kiểm tra lại</button>
              <p id="docu-ai-state" class="discover-state" role="status"></p>
              <p id="docu-provider" class="hint"></p>
            </section>
            <section class="docu-card" aria-labelledby="docu-overview-title">
              <h3 id="docu-overview-title">Tổng quan kịch bản</h3>
              <dl id="docu-summary" class="docu-summary"></dl>
              <ul id="docu-script-notes" class="docu-script-notes"></ul>
            </section>
          </div>
        </div>
        <div id="docu-tab-produce" class="tab-panel" role="tabpanel" aria-labelledby="docu-tab-produce-btn" hidden>
          <div class="tab-scroll">
            <p class="hint">Làm lần lượt từ trên xuống. Sửa kịch bản thì các bước sau cần làm lại để khớp.</p>
            <section class="docu-card docu-narration" aria-labelledby="docu-narration-title">
              <header class="docu-step-head"><span class="docu-step-num">1</span><h3 id="docu-narration-title">Lời đọc tiếng Anh</h3><span id="docu-narration-badge" class="docu-badge"></span></header>
              <label>Giọng<select id="docu-voice"></select></label>
              <label>Tốc độ<select id="docu-speed">${NARRATION_SPEEDS.map(speed => `<option value="${speed}" ${speed === 1 ? "selected" : ""}>${speed === 1 ? "Bình thường" : `${speed}×`}</option>`).join("")}</select></label>
              <button id="docu-narrate" class="btn primary block" type="button">Tạo lời đọc</button>
              <div id="docu-narration-progress" class="docu-narration-progress" hidden>
                <div class="bar busy-bar"><span id="docu-narration-bar"></span></div>
                <button id="docu-narration-cancel" class="btn" type="button">Hủy</button>
              </div>
              <p id="docu-narration-state" class="hint" role="status"></p>
              <p id="docu-narration-stale" class="docu-stale" hidden>Kịch bản đã sửa sau lần tạo lời đọc này; hãy tạo lại để khớp.</p>
              <audio id="docu-narration-audio" controls preload="metadata" hidden></audio>
              <p class="hint" id="docu-narration-note">Lần đầu DubFlow tải giọng đọc tiếng Anh (khoảng 370 MB). Lời đọc được chuẩn hóa ở -16 LUFS; câu nào không đổi sẽ không phải đọc lại.</p>
            </section>
            <section class="docu-card docu-narration docu-visual" aria-labelledby="docu-visual-title">
              <header class="docu-step-head"><span class="docu-step-num">2</span><h3 id="docu-visual-title">Kế hoạch hình ảnh</h3><span id="docu-plan-badge" class="docu-badge"></span></header>
              <p class="hint">Tách cảnh các video nguồn, tránh vùng phụ đề gốc rồi chia mỗi cảnh lời đọc thành các lát 3–4,5 giây, xen kẽ góc máy giữa các nguồn.</p>
              <button id="docu-plan" class="btn primary block" type="button">Lập kế hoạch hình ảnh</button>
              <div id="docu-plan-progress" class="docu-narration-progress" hidden>
                <div class="bar busy-bar"><span id="docu-plan-bar"></span></div>
                <button id="docu-plan-cancel" class="btn" type="button">Hủy</button>
              </div>
              <p id="docu-plan-state" class="hint" role="status"></p>
              <p id="docu-plan-stale" class="docu-stale" hidden>Kịch bản hoặc lời đọc đã đổi sau lần lập kế hoạch này; hãy lập lại.</p>
              <ul id="docu-plan-warnings" class="docu-script-notes"></ul>
            </section>
            <section class="docu-card docu-narration docu-render" aria-labelledby="docu-render-title">
              <header class="docu-step-head"><span class="docu-step-num">3</span><h3 id="docu-render-title">Bản nháp</h3><span id="docu-render-badge" class="docu-badge"></span></header>
              <p class="hint">Dựng hình theo kế hoạch với lời đọc thành <code>documentary.mp4</code> (1080p, 30 fps, bỏ tiếng gốc), kèm <code>description.txt</code> có chương và nguồn.</p>
              <div class="docu-render-options" role="group" aria-label="Trong bản nháp">
                <label><input id="docu-opt-graphics" type="checkbox" checked /> Thẻ số liệu và tên địa điểm</label>
                <label><input id="docu-opt-captions" type="checkbox" /> In phụ đề tiếng Anh lên hình</label>
                <label>Nhạc nền<select id="docu-opt-music"><option value="generated">Tự tạo theo không khí từng chương</option><option value="none">Không có</option><option value="file" hidden>File nhạc đã chọn</option></select></label>
                <button id="docu-opt-music-file" class="btn" type="button">Chọn file nhạc…</button>
                <label title="Tiếng gió, máy móc, nước… theo thẻ âm thanh của từng cảnh, và tiếng nhấn khi sang hồi mới"><input id="docu-opt-sfx" type="checkbox" checked /> Âm thanh môi trường theo cảnh</label>
              </div>
              <button id="docu-render" class="btn primary block" type="button">Dựng bản nháp</button>
              <div id="docu-render-progress" class="docu-narration-progress" hidden>
                <div class="bar busy-bar"><span id="docu-render-bar"></span></div>
                <button id="docu-render-cancel" class="btn" type="button">Hủy</button>
              </div>
              <p id="docu-render-state" class="hint" role="status"></p>
              <div class="docu-render-actions" id="docu-render-actions" hidden>
                <button id="docu-render-watch" class="btn" type="button">Xem bản nháp</button>
                <button id="docu-render-edit" class="btn primary" type="button" title="Cắt ghép, chèn, chữ, âm thanh và phụ đề tiếng Anh">Chỉnh sửa</button>
                <button id="docu-render-reveal" class="btn" type="button">Mở thư mục</button>
              </div>
            </section>
            <section class="docu-card docu-narration docu-capcut" id="docu-capcut" aria-labelledby="docu-capcut-title" hidden>
              <header class="docu-step-head"><span class="docu-step-num">4</span><h3 id="docu-capcut-title">Xuất sang CapCut</h3><span id="docu-capcut-badge" class="docu-badge"></span></header>
              <button id="docu-capcut-export" class="btn block" type="button" title="Mỗi lát cắt là một clip riêng; lời đọc ở rãnh âm thanh">Xuất sang CapCut</button>
              <p id="docu-capcut-state" class="hint" role="status"></p>
              <button id="docu-capcut-open" class="btn block" type="button" hidden>Mở CapCut</button>
            </section>
          </div>
        </div>
        <div id="docu-tab-claude" class="tab-panel" role="tabpanel" aria-labelledby="docu-tab-claude-btn" hidden>
          <div class="tab-scroll">
            <section id="docu-mcp-activity" class="docu-card docu-mcp-activity" aria-label="Claude đang làm qua MCP">
              <div class="docu-mcp-head"><h3>Claude đang làm gì</h3><label class="docu-mcp-follow"><input id="docu-mcp-follow" type="checkbox" checked /> Theo dõi</label></div>
              <p id="docu-mcp-current" class="docu-mcp-current" role="status" aria-live="polite">Chưa có hoạt động nào từ Claude.</p>
              <button id="docu-mcp-open" class="btn" type="button" hidden>Mở dự án đang làm</button>
              <ol id="docu-mcp-recent" class="docu-mcp-recent"></ol>
            </section>
            <section class="docu-card docu-narration docu-mcp" aria-labelledby="docu-mcp-title">
              <h3 id="docu-mcp-title">Kết nối Claude (MCP)</h3>
              <p class="hint">Cho Claude xem và chỉnh dự án: đọc dòng thời gian, đổi lát cắt, đặt nhãn tiếng Anh, chỉnh nhịp, xem khung hình, dựng lại và xuất CapCut. Sau mỗi lần cập nhật DubFlow, hãy thêm lại lệnh này.</p>
              <label>Claude Code<textarea id="docu-mcp-code" rows="3" readonly></textarea></label>
              <button id="docu-mcp-copy-code" class="btn" type="button">Sao chép lệnh</button>
              <label>Claude Desktop (claude_desktop_config.json)<textarea id="docu-mcp-desktop" rows="6" readonly></textarea></label>
              <button id="docu-mcp-copy-desktop" class="btn" type="button">Sao chép cấu hình</button>
            </section>
          </div>
        </div>
      </aside>
    </div>`;
  const $ = <T extends HTMLElement = HTMLInputElement>(selector: string): T => root.querySelector<T>(selector)!;
  const screen = $("#docu-screen");
  const video = $("#docu-video") as HTMLVideoElement;
  const editor = $("#docu-editor");
  let runs: DocuRun[] = [];
  let selected: OpenDocuRun | null = null;
  let currentScript: DocuScript | null = null;
  let sourceForms: SourceForm[] = [];
  let shown = false;
  let voices: NarrationVoice[] = [];
  let narration: NarrationInfo = { audio: null, timeline: null, status: null };
  let narrationTimer: ReturnType<typeof setInterval> | undefined;
  let narrationAudio: string | null = null;
  let visual: VisualInfo = { plan: null, status: null };
  let visualTimer: ReturnType<typeof setInterval> | undefined;
  let render: RenderInfo = { video: null, status: null, options: parseRenderOptions(null) };
  let capcut: CapcutExport | null = null;
  let labels: DocuLabel[] = [];
  let renderTimer: ReturnType<typeof setInterval> | undefined;
  const ROUGH_CUT = "__rough_cut";
  let checkTimer: ReturnType<typeof setTimeout> | undefined;
  let sourceVideoUrl: string | null = null;
  let stoppingAt: number | null = null;
  /** The player shows either a source video or the rendered rough cut, never ambiguously. */
  let playerMode: "source" | "draft" = "source";
  const player = playerLoader(path => settings.mediaUrl?.(path) ?? Promise.resolve(null), () => selected?.dir);

  type Tab = "setup" | "script" | "produce" | "claude";
  function setTab(tab: Tab): void {
    for (const name of ["setup", "script", "produce", "claude"] as const) {
      $<HTMLButtonElement>(`#docu-tab-${name}-btn`).setAttribute("aria-selected", String(name === tab));
      $<HTMLElement>(`#docu-tab-${name}`).hidden = name !== tab;
    }
  }

  function renderRuns(): void {
    const list = $("#docu-runs");
    list.innerHTML = runs.map(run => `<li><button type="button" class="job ${selected?.dir === run.dir ? "selected" : ""}" data-run="${escapeHtml(run.dir)}" aria-current="${selected?.dir === run.dir ? "true" : "false"}">
      <span class="dot ${run.script ? "ok" : ""}"></span><span class="job-main"><strong class="job-title">${escapeHtml(run.spec.title)}</strong><span class="job-meta"><span class="msg">${run.spec.sources.length} nguồn · ${run.spec.target_minutes} phút</span></span></span></button></li>`).join("");
    $("#docu-runs-empty").hidden = runs.length > 0;
  }

  function renderSourcePicker(): void {
    const jobs = completedDocuJobs(settings.jobs());
    const select = $("#docu-job-select") as HTMLSelectElement;
    const previous = select.value;
    select.innerHTML = `<option value="">Chọn video đã hoàn tất…</option>${jobs.map(job => `<option value="${escapeHtml(job.id)}">${escapeHtml(job.displayName)}</option>`).join("")}`;
    if (jobs.some(job => job.id === previous)) select.value = previous;
    $("#docu-source-order").innerHTML = sourceForms.map((source, index) => `<li><span><strong>${index + 1}. ${escapeHtml(source.title)}</strong><small>${escapeHtml(source.kind === "job" ? videoName(source.outputPath ?? "") : videoName(source.video ?? ""))}</small></span>
      <button class="icon-btn" type="button" data-source-move="-1" data-source-index="${index}" aria-label="Di chuyển nguồn ${index + 1} lên">↑</button>
      <button class="icon-btn" type="button" data-source-move="1" data-source-index="${index}" aria-label="Di chuyển nguồn ${index + 1} xuống">↓</button>
      <button class="icon-btn" type="button" data-source-remove="${index}" aria-label="Bỏ nguồn ${index + 1}">×</button></li>`).join("");
  }

  function renderContexts(): void {
    const contexts = selected?.contexts ?? [];
    const select = $("#docu-player-source") as HTMLSelectElement;
    select.innerHTML = contexts.map(context => `<option value="${escapeHtml(context.source_id)}">${escapeHtml(sourceName(context.source_id))} · ${escapeHtml(context.title)}</option>`).join("");
    if (playerMode === "draft" && !render.video) playerMode = "source";
    if (playerMode === "draft") { void setPlayerSource(ROUGH_CUT); return; }
    if (!contexts.length) {
      player.cancel();
      video.removeAttribute("src"); video.load(); sourceVideoUrl = null;
      $("#docu-player-empty").textContent = "Chọn dự án có nguồn video để xem trước.";
      $("#docu-player-empty").hidden = false;
      renderPlayerMode();
      return;
    }
    $("#docu-player-empty").hidden = true;
    void setPlayerSource(contexts[0].source_id);
  }

  /** Which video the player holds, said on the toggle, the badge and the frame colour. */
  function renderPlayerMode(): void {
    const draft = playerMode === "draft";
    $("#docu-show-source").setAttribute("aria-pressed", String(!draft));
    $("#docu-show-draft").setAttribute("aria-pressed", String(draft));
    const drafted = Boolean(render.video) && render.status?.state !== "RUNNING";
    $<HTMLButtonElement>("#docu-show-draft").disabled = !drafted;
    $("#docu-show-draft").title = drafted ? "Xem documentary.mp4 đã dựng" : "Chưa có bản nháp: dựng ở thẻ Dựng phim";
    $<HTMLButtonElement>("#docu-show-source").disabled = !selected?.contexts.length;
    $("#docu-player-source").hidden = draft || !selected?.contexts.length;
    $("#docu-player").classList.toggle("draft", draft);
    $("#docu-player-badge").textContent = draft ? `documentary.mp4 · bản đã dựng${renderStale() ? " (cũ, cần dựng lại)" : ""}` : selected?.contexts.length ? "Video gốc, chưa dựng" : "";
  }

  async function setPlayerSource(sourceId: string, start?: number, end?: number): Promise<void> {
    const draft = sourceId === ROUGH_CUT;
    const path = draft ? render.video : selected?.contexts.find(source => source.source_id === sourceId)?.video_path;
    if (!path) return;
    playerMode = draft ? "draft" : "source";
    if (!draft) ($("#docu-player-source") as HTMLSelectElement).value = sourceId;
    renderPlayerMode();
    await player.load(path, (url, current) => {
      if (!url) {
        $("#docu-player-empty").hidden = false;
        $("#docu-player-empty").textContent = draft ? "Không mở được bản nháp để xem." : "Không mở được video nguồn để xem trước.";
        return;
      }
      $("#docu-player-empty").hidden = true;
      if (sourceVideoUrl !== url) { sourceVideoUrl = url; video.src = url; video.load(); }
      if (start !== undefined) {
        stoppingAt = end ?? null;
        const seek = () => { if (!current()) return; video.currentTime = Math.max(0, start); void video.play().catch(() => undefined); };
        if (video.readyState >= 1) seek(); else video.addEventListener("loadedmetadata", seek, { once: true });
      } else stoppingAt = null;
    });
  }

  function updateSummary(): void {
    const script = currentScript;
    if (!script || !selected) { $("#docu-summary").innerHTML = ""; $("#docu-script-notes").innerHTML = ""; return; }
    const scenes = flattenScenes(script.chapters).map(item => item.scene);
    const words = scenes.reduce((sum, scene) => sum + wordCount(scene.narration_text), 0);
    const seconds = scenes.reduce((sum, scene) => sum + estimatedSeconds(scene.narration_text), 0);
    const target = selected.spec.target_minutes * 60;
    const reviewCount = scenes.filter(scene => scene.needs_review).length;
    const spoken = narration.timeline && !narrationStale(script, narration.timeline)
      ? `<dt>Lời đọc thật</dt><dd>${clock(narration.timeline.duration)} / ${clock(target)}</dd>` : "";
    $("#docu-summary").innerHTML = `<dt>Cảnh</dt><dd>${scenes.length}</dd><dt>Từ</dt><dd>${words}</dd><dt>Thời lượng ước tính</dt><dd>${clock(seconds)} / ${clock(target)}</dd>${spoken}<dt>Cần kiểm tra</dt><dd>${reviewCount}</dd>`;
    $("#docu-script-notes").innerHTML = (script.review_notes ?? []).map(note => `<li>${escapeHtml(note)}</li>`).join("");
  }

  function renderProjectInfo(): void {
    $("#docu-ai-state").textContent = "";
    const spec = selected?.spec;
    $("#docu-project-view").hidden = !spec;
    $("#docu-project-fields").hidden = Boolean(spec);
    const framing: Record<string, string> = { none: "Không", letterbox_235: "Viền đen điện ảnh 2,35:1", overscan_108: "Phóng nhẹ 108% (cắt mép)" };
    $("#docu-project-info").innerHTML = spec ? `<dt>Nguồn</dt><dd>${spec.sources.map(source => `${escapeHtml(sourceName(source.id))} · ${escapeHtml(source.title)}`).join("<br>")}</dd>
      <dt>Mục tiêu</dt><dd>${spec.target_minutes} phút</dd><dt>Khung hình</dt><dd><select id="docu-framing-change" aria-label="Đổi khung hình">${Object.entries(framing).map(([value, name]) => `<option value="${value}" ${spec.cinematic_framing_preset === value ? "selected" : ""}>${escapeHtml(name)}</option>`).join("")}</select><small id="docu-framing-state" class="hint" role="status"></small></dd>
      <dt>Ghi nguồn cuối video</dt><dd>${spec.burn_end_credits ? "Có" : "Không (chỉ ghi trong mô tả)"}</dd>` : "";
  }

  /** Change the framing look in place: slices, hand edits and caption scans stay; re-render to see it. */
  async function changeFraming(preset: string): Promise<void> {
    if (!selected) return;
    const dir = selected.dir;
    const state = $("#docu-framing-state");
    state.textContent = "Đang đổi khung hình…";
    try {
      const result = await invoke("docu_reframe", { dir, preset }) as { plan?: boolean; sources?: { source_id: string; clear: boolean }[] };
      if (selected?.dir !== dir) return;
      selected.spec.cinematic_framing_preset = preset as FramingPreset;
      visual = parseVisual(await invoke("docu_plan_status", { dir }));
      renderVisual();
      const covered = (result.sources ?? []).filter(source => !source.clear).map(source => source.source_id);
      state.textContent = !result.plan ? "Đã đổi khung hình." : covered.length
        ? `Đã đổi. ${covered.join(", ")} vẫn còn một phần phụ đề gốc, được làm mờ và phủ dải tối. Dựng lại bản nháp để thấy khung mới.`
        : "Đã đổi; mọi nguồn che được phụ đề gốc. Dựng lại bản nháp để thấy khung mới.";
    } catch (error) {
      state.textContent = error instanceof Error ? error.message : String(error);
      renderProjectInfo();
    }
  }

  function renderProvider(): void {
    const provider = PROVIDERS[session.provider()];
    $("#docu-provider").textContent = `Khi viết, tiêu đề, ghi chú chủ đề, lời gốc tiếng Trung kèm mốc thời gian, các số liệu đã quy đổi và những phần đã viết được gửi tới ${provider.company} (${provider.name}) bằng khóa trong Cài đặt AI.`;
  }

  function reviewHtml(scene: DocuScene, chapterIndex: number, sceneIndex: number): string {
    if (!scene.needs_review) return "";
    const numbers = scene.review_numbers.length
      ? `<button type="button" class="btn" data-acknowledge="${chapterIndex}:${sceneIndex}">Đã kiểm tra số liệu</button>` : "";
    return `<div class="docu-review" role="status"><strong>Cần kiểm tra</strong><ul>${scene.review_notes.map(note => `<li>${escapeHtml(note)}</li>`).join("")}</ul>${numbers}</div>`;
  }

  function refsHtml(scene: DocuScene, chapterIndex: number, sceneIndex: number): string {
    return scene.matched_source_refs.map((ref, index) => `<span class="docu-ref-wrap"><button type="button" class="docu-ref" data-seek-source="${escapeHtml(ref.source_id)}" data-seek-start="${ref.start}" data-seek-end="${ref.end}" aria-label="Xem ${escapeHtml(sourceName(ref.source_id))} từ ${ref.start.toFixed(1)} đến ${ref.end.toFixed(1)} giây">${escapeHtml(sourceName(ref.source_id))} · ${ref.start.toFixed(1).replace(".", ",")}–${ref.end.toFixed(1).replace(".", ",")} s</button><button type="button" class="docu-ref-remove" data-remove-ref="${index}" data-chapter="${chapterIndex}" data-scene="${sceneIndex}" aria-label="Bỏ nguồn dẫn ${index + 1}">×</button></span>`).join("") || `<span class="hint">Chưa có nguồn dẫn</span>`;
  }

  function renderEditor(): void {
    if (!selected) { editor.innerHTML = `<p class="queue-empty">Chọn dự án hoặc tạo dự án mới để bắt đầu.</p>`; return; }
    if (!currentScript) currentScript = emptyScript(selected.spec.title);
    editor.innerHTML = currentScript.chapters.map((chapter, chapterIndex) => `<section class="docu-chapter" data-chapter="${chapterIndex}">
      <header><div><h3>${escapeHtml(CHAPTER_NAMES[chapter.chapter_type])}</h3><small>${chapterTargetSeconds(selected!.spec.target_minutes, chapter.chapter_type)} giây mục tiêu</small></div>
        <label class="docu-mood">Nhạc nền<select data-mood="${chapterIndex}">${MOODS.map(mood => `<option value="${mood}" ${(chapter.recommended_bgm_mood ?? DEFAULT_MOODS[chapter.chapter_type]) === mood ? "selected" : ""}>${MOOD_NAMES[mood]}</option>`).join("")}</select></label>
        <button type="button" class="quiet-btn" data-add-scene="${chapterIndex}">Thêm cảnh</button></header>
      ${chapter.scenes.map((scene, sceneIndex) => sceneCard(scene, chapterIndex, sceneIndex)).join("") || `<p class="docu-chapter-empty">Chưa có cảnh trong phần này.</p>`}
    </section>`).join("");
    updateSummary();
  }

  function sceneCard(scene: DocuScene, chapterIndex: number, sceneIndex: number): string {
    const graphic = scene.on_screen_graphic;
    return `<article class="docu-scene" data-scene-card="${escapeHtml(scene.scene_id)}" data-chapter="${chapterIndex}" data-scene="${sceneIndex}">
      <header><strong>${escapeHtml(sceneName(scene.scene_id))}</strong><span class="docu-scene-time">${sceneTime(scene)}</span><button type="button" class="quiet-btn" data-add-scene="${chapterIndex}" data-after="${sceneIndex}">Thêm sau</button><button type="button" class="quiet-btn" data-delete-scene="${chapterIndex}" data-scene="${sceneIndex}">Xóa cảnh</button></header>
      <label>Lời dẫn<textarea data-field="narration_text" data-chapter="${chapterIndex}" data-scene="${sceneIndex}" maxlength="${MAX_NARRATION}" rows="4">${escapeHtml(scene.narration_text)}</textarea></label>
      <p class="docu-word-count"><span>${wordCount(scene.narration_text)} từ</span><span>Ước tính ${estimatedSeconds(scene.narration_text)} giây</span></p>
      <div class="docu-refs" aria-label="Nguồn dẫn">${refsHtml(scene, chapterIndex, sceneIndex)}</div>
      <div class="docu-slices" data-slices="${escapeHtml(scene.scene_id)}">${slicesHtml(scene)}</div>
      <label>Đồ họa trên màn hình<select data-field="graphic_type" data-chapter="${chapterIndex}" data-scene="${sceneIndex}"><option value="">Không có</option>${GRAPHIC_TYPES.map(type => `<option value="${type}" ${graphic?.type === type ? "selected" : ""}>${GRAPHIC_NAMES[type]}</option>`).join("")}</select></label>
      <div class="docu-graphic-fields" ${graphic ? "" : "hidden"}><label>Tiêu đề<input data-field="graphic_title" data-chapter="${chapterIndex}" data-scene="${sceneIndex}" value="${escapeHtml(graphic?.title ?? "")}" maxlength="60" /></label><label>Giá trị<input data-field="graphic_value" data-chapter="${chapterIndex}" data-scene="${sceneIndex}" value="${escapeHtml(graphic?.value ?? "")}" maxlength="60" /></label><label>Chú thích<input data-field="graphic_subtext" data-chapter="${chapterIndex}" data-scene="${sceneIndex}" value="${escapeHtml(graphic?.subtext ?? "")}" maxlength="120" /></label></div>
      <label>Âm thanh nền<select data-field="ambient_sfx_tag" data-chapter="${chapterIndex}" data-scene="${sceneIndex}"><option value="">Không có</option>${AMBIENT_SFX.map(tag => `<option value="${tag}" ${scene.ambient_sfx_tag === tag ? "selected" : ""}>${SFX_NAMES[tag]}</option>`).join("")}</select></label>
      ${reviewHtml(scene, chapterIndex, sceneIndex)}
    </article>`;
  }

  /** The scene's B-roll slices from the visual plan, each a preview button. */
  function slicesHtml(scene: DocuScene): string {
    const planned = visual.plan?.scenes.find(item => item.scene_id === scene.scene_id);
    if (!planned) return "";
    const seconds = (value: number) => value.toFixed(1).replace(".", ",");
    return `<span class="docu-slices-label">Hình:</span>${planned.slices.map(piece => `<button type="button" class="docu-ref docu-slice ${piece.tier ? "far" : ""}" data-seek-source="${escapeHtml(piece.source_id)}" data-seek-start="${piece.start}" data-seek-end="${piece.end}" title="${piece.tier ? "Ngoài đoạn nguồn gợi ý" : "Trong đoạn nguồn gợi ý"}">${escapeHtml(sourceName(piece.source_id))} · ${seconds(piece.start)}–${seconds(piece.end)}</button>`).join("")}${planned.warnings.map(note => `<small class="docu-slice-note">${escapeHtml(sourceNames(note))}</small>`).join("")}`;
  }

  function renderVisual(): void {
    const running = visual.status?.state === "RUNNING";
    const timelineFresh = Boolean(narration.timeline) && !narrationStale(currentScript, narration.timeline);
    $<HTMLButtonElement>("#docu-plan").disabled = running || !selected || !timelineFresh;
    $<HTMLButtonElement>("#docu-plan").textContent = visual.plan ? "Lập lại kế hoạch hình ảnh" : "Lập kế hoạch hình ảnh";
    $("#docu-plan-progress").hidden = !running;
    const total = visual.status?.total ?? 0;
    $("#docu-plan-bar").style.width = `${running && total ? Math.round(100 * (visual.status!.done / total)) : 0}%`;
    const failed = visual.status && ["FAILED", "CANCELLED"].includes(visual.status.state);
    const count = visual.plan?.scenes.reduce((sum, scene) => sum + scene.slices.length, 0) ?? 0;
    $("#docu-plan-state").textContent = running || failed ? sourceNames(visual.status!.message)
      : !timelineFresh ? "Cần lời đọc khớp với kịch bản trước." : visual.plan ? `${count} lát cắt cho ${visual.plan.scenes.length} cảnh` : "";
    $("#docu-plan-stale").hidden = !planStale(currentScript, narration.timeline, visual.plan);
    badge("#docu-plan-badge", running ? "busy" : !visual.plan ? "todo" : planStale(currentScript, narration.timeline, visual.plan) ? "stale" : "done");
    $("#docu-plan-warnings").innerHTML = (visual.plan?.warnings ?? []).map(note => `<li>${escapeHtml(sourceNames(note))}</li>`).join("");
    renderRough();
    for (const holder of editor.querySelectorAll<HTMLElement>("[data-slices]")) {
      const card = holder.closest<HTMLElement>("[data-scene-card]");
      const scene = card ? sceneAt(Number(card.dataset.chapter), Number(card.dataset.scene)) : null;
      holder.innerHTML = scene ? slicesHtml(scene) : "";
    }
  }

  const BADGES = { todo: "Chưa làm", optional: "Chưa làm · tùy chọn", busy: "Đang chạy", stale: "Cần làm lại", done: "Xong" } as const;
  /** A rough cut on disk whose script, narration or plan has changed since (no matching plan to have rendered from). */
  function renderStale(): boolean {
    return Boolean(render.video) && (!visual.plan || planStale(currentScript, narration.timeline, visual.plan) || optionsChanged());
  }
  /** The draft was made with other cards, captions, music or ambience than the ones now chosen. */
  function optionsChanged(): boolean {
    const made = render.status?.state === "COMPLETED" ? render.status.overlays : undefined;
    const chosen = render.options;
    return Boolean(made) && (made!.graphics !== chosen.graphics || made!.captions !== chosen.captions || made!.sfx !== chosen.sfx
      || (made!.music !== "" && made!.music !== chosen.music));
  }
  function badge(selector: string, state: keyof typeof BADGES): void {
    const element = $(selector);
    element.textContent = BADGES[state];
    element.dataset.state = state;
  }

  function renderRough(): void {
    const running = render.status?.state === "RUNNING";
    const planFresh = Boolean(visual.plan) && !planStale(currentScript, narration.timeline, visual.plan);
    $<HTMLButtonElement>("#docu-render").disabled = running || !selected || !planFresh;
    $<HTMLButtonElement>("#docu-render").textContent = render.video ? "Dựng lại bản nháp" : "Dựng bản nháp";
    $("#docu-render-progress").hidden = !running;
    const total = render.status?.total ?? 0;
    $("#docu-render-bar").style.width = `${running && total ? Math.round(100 * (render.status!.done / total)) : 0}%`;
    const failed = render.status && ["FAILED", "CANCELLED"].includes(render.status.state);
    $("#docu-render-state").textContent = running || failed ? sourceNames(render.status!.message)
      : !planFresh ? (render.video ? "Bản nháp hiện có dựng từ kịch bản, lời đọc hoặc kế hoạch cũ. Lập lại kế hoạch hình ảnh rồi dựng lại." : "Cần kế hoạch hình ảnh khớp với lời đọc trước.")
      : render.video ? "Bản nháp đã sẵn sàng." : "";
    $("#docu-render-actions").hidden = !render.video || running;
    badge("#docu-render-badge", running ? "busy" : !render.video ? "todo" : renderStale() ? "stale" : "done");
    renderPlayerMode();
    $("#docu-capcut").hidden = !render.video || running;
    renderCapcut();
    $("#docu-render-edit").hidden = !settings.edit;
    const options = render.options;
    $<HTMLInputElement>("#docu-opt-graphics").checked = options.graphics;
    $<HTMLInputElement>("#docu-opt-captions").checked = options.captions;
    $<HTMLInputElement>("#docu-opt-sfx").checked = options.sfx;
    const music = $<HTMLSelectElement>("#docu-opt-music");
    const fileOption = music.querySelector("option[value=file]") as HTMLOptionElement;
    fileOption.hidden = !options.music_path;
    fileOption.textContent = options.music_path ? `File: ${options.music_path.split(/[\\/]/).pop()}` : "File nhạc đã chọn";
    music.value = options.music;
    for (const id of ["#docu-opt-graphics", "#docu-opt-captions", "#docu-opt-music", "#docu-opt-music-file", "#docu-opt-sfx"]) $<HTMLInputElement>(id).disabled = running || !selected;
  }

  /** What the next render draws and plays; saved in the project, applied on "Dựng lại bản nháp". */
  async function saveRenderOptions(musicPath?: string): Promise<void> {
    if (!selected) return;
    const dir = selected.dir;
    try {
      const saved = await invoke("docu_render_options", { dir, graphics: $<HTMLInputElement>("#docu-opt-graphics").checked,
        captions: $<HTMLInputElement>("#docu-opt-captions").checked, music: musicPath ? "file" : $<HTMLSelectElement>("#docu-opt-music").value,
        musicPath: musicPath ?? null, sfx: $<HTMLInputElement>("#docu-opt-sfx").checked });
      if (selected?.dir !== dir) return;
      render = { ...render, options: parseRenderOptions(saved) };
      renderRough();
      if (render.video) $("#docu-render-state").textContent = "Đã lưu; dựng lại bản nháp để áp dụng.";
    } catch (error) { $("#docu-render-state").textContent = error instanceof Error ? error.message : String(error); renderRough(); }
  }

  function renderCapcut(message?: string): void {
    badge("#docu-capcut-badge", capcut ? "done" : "optional");
    $("#docu-capcut-open").hidden = !capcut;
    $("#docu-capcut-state").textContent = message ?? (capcut
      ? `Đã có dự án “${capcut.name}” trong CapCut (${capcut.clips} clip). Phụ đề tiếng Anh: nhập captions_en.srt trong CapCut.`
      : "Đóng CapCut trước khi xuất; dự án mới hiện ở đầu danh sách dự án (Projects) của CapCut.");
  }

  async function exportCapcut(): Promise<void> {
    if (!selected) return;
    const button = $<HTMLButtonElement>("#docu-capcut-export");
    button.disabled = true;
    renderCapcut("Đang tạo dự án CapCut…");
    try {
      capcut = parseCapcut(await invoke("docu_capcut", { dir: selected.dir }));
      renderCapcut();
    } catch (error) { renderCapcut(error instanceof Error ? error.message : String(error)); }
    finally { button.disabled = false; }
  }

  function followRender(): void {
    clearInterval(renderTimer);
    if (render.status?.state !== "RUNNING" || !selected) return;
    const dir = selected.dir;
    renderTimer = setInterval(async () => {
      if (selected?.dir !== dir) { clearInterval(renderTimer); return; }
      try {
        render = parseRender(await invoke("docu_render_status", { dir }));
        if (render.status?.state !== "RUNNING") { clearInterval(renderTimer); if (render.video) renderContexts(); }
        renderRough();
      } catch { clearInterval(renderTimer); }
    }, 1000);
  }

  async function makeRoughCut(): Promise<void> {
    if (!selected) return;
    try {
      await invoke("docu_render", { dir: selected.dir });
      render = parseRender(await invoke("docu_render_status", { dir: selected.dir }));
      renderRough(); followRender();
    } catch (error) { $("#docu-render-state").textContent = error instanceof Error ? error.message : String(error); }
  }

  function followVisual(): void {
    clearInterval(visualTimer);
    if (visual.status?.state !== "RUNNING" || !selected) return;
    const dir = selected.dir;
    visualTimer = setInterval(async () => {
      if (selected?.dir !== dir) { clearInterval(visualTimer); return; }
      try {
        visual = parseVisual(await invoke("docu_plan_status", { dir }));
        if (visual.status?.state !== "RUNNING") clearInterval(visualTimer);
        renderVisual();
      } catch { clearInterval(visualTimer); }
    }, 1000);
  }

  async function makePlan(): Promise<void> {
    if (!selected) return;
    try {
      await invoke("docu_plan", { dir: selected.dir });
      visual = parseVisual(await invoke("docu_plan_status", { dir: selected.dir }));
      renderVisual(); followVisual();
    } catch (error) { $("#docu-plan-state").textContent = error instanceof Error ? error.message : String(error); }
  }

  /** The narrated length when the narration matches the script, else the estimate. */
  function sceneTime(scene: DocuScene): string {
    const timed = !narrationStale(currentScript, narration.timeline) ? narration.timeline?.scenes.find(item => item.scene_id === scene.scene_id) : undefined;
    return timed ? `${(timed.exact_duration_ms / 1000).toFixed(1).replace(".", ",")} giây đã đọc` : `${estimatedSeconds(scene.narration_text)} giây (ước tính)`;
  }

  function renderNarration(): void {
    const info = narration, running = info.status?.state === "RUNNING";
    $<HTMLButtonElement>("#docu-narrate").disabled = running || !selected || !voices.length;
    $<HTMLButtonElement>("#docu-narrate").textContent = info.timeline ? "Tạo lại lời đọc" : "Tạo lời đọc";
    $("#docu-narration-progress").hidden = !running;
    const total = info.status?.total ?? 0;
    $("#docu-narration-bar").style.width = `${running && total ? Math.round(100 * (info.status!.done / total)) : 0}%`;
    const failed = info.status && ["FAILED", "CANCELLED"].includes(info.status.state);
    $("#docu-narration-state").textContent = running || failed ? info.status!.message
      : info.timeline ? `Lời đọc ${clock(info.timeline.duration)} · giọng ${voices.find(voice => voice.voiceId === info.timeline!.voice_id)?.name ?? info.timeline.voice_id}` : "";
    $("#docu-narration-stale").hidden = !narrationStale(currentScript, info.timeline);
    badge("#docu-narration-badge", running ? "busy" : !info.timeline ? "todo" : narrationStale(currentScript, info.timeline) ? "stale" : "done");
    const audio = $<HTMLAudioElement>("#docu-narration-audio");
    audio.hidden = !info.audio;
    if (info.audio && info.audio !== narrationAudio) {
      narrationAudio = info.audio;
      // A new take replaces the file in place: a fresh URL makes the player reload it.
      void settings.mediaUrl?.(info.audio).then(url => { if (url) audio.src = `${url}${url.includes("?") ? "&" : "?"}t=${Date.now()}`; });
    } else if (!info.audio) { narrationAudio = null; audio.removeAttribute("src"); }
    for (const card of editor.querySelectorAll<HTMLElement>("[data-scene-card]")) {
      const scene = sceneAt(Number(card.dataset.chapter), Number(card.dataset.scene));
      const time = card.querySelector(".docu-scene-time");
      if (scene && time) time.textContent = sceneTime(scene);
    }
    updateSummary();
    renderVisual();
  }

  async function loadVoices(): Promise<void> {
    if (voices.length) return;
    try {
      const parsed = parseVoices(await invoke("docu_voices"));
      voices = parsed.voices;
      $("#docu-voice").innerHTML = voices.map(voice => `<option value="${escapeHtml(voice.voiceId)}" title="${escapeHtml(voice.description)}" ${voice.voiceId === parsed.defaultVoiceId ? "selected" : ""}>${escapeHtml(voice.name)} · ${escapeHtml(voice.gender)}, giọng ${escapeHtml(voice.accent)}</option>`).join("");
    } catch (error) { $("#docu-narration-state").textContent = error instanceof Error ? error.message : String(error); }
    renderNarration();
  }

  function followNarration(): void {
    clearInterval(narrationTimer);
    if (narration.status?.state !== "RUNNING" || !selected) return;
    const dir = selected.dir;
    narrationTimer = setInterval(async () => {
      if (selected?.dir !== dir) { clearInterval(narrationTimer); return; }
      try {
        narration = parseNarration(await invoke("docu_narration_status", { dir }));
        if (narration.status?.state !== "RUNNING") clearInterval(narrationTimer);
        renderNarration();
      } catch { clearInterval(narrationTimer); }
    }, 1000);
  }

  async function narrate(): Promise<void> {
    if (!selected || !currentScript) return;
    if (!hasNarration(currentScript.chapters)) { $("#docu-narration-state").textContent = "Kịch bản chưa có lời đọc."; return; }
    // The engine reads the saved script, so save the edits first.
    await checkScript();
    if (!currentScript.script_sha256) return;
    try {
      await invoke("docu_narrate", { dir: selected.dir, voice: $<HTMLSelectElement>("#docu-voice").value, speed: Number($<HTMLSelectElement>("#docu-speed").value) });
      narration = parseNarration(await invoke("docu_narration_status", { dir: selected.dir }));
      renderNarration(); followNarration();
    } catch (error) { $("#docu-narration-state").textContent = error instanceof Error ? error.message : String(error); }
  }

  function updateSceneMetrics(text: HTMLTextAreaElement): void {
    const card = text.closest<HTMLElement>("[data-scene-card]");
    if (!card) return;
    const words = wordCount(text.value), seconds = estimatedSeconds(text.value);
    card.querySelector(".docu-word-count")!.innerHTML = `<span>${words} từ</span><span>Ước tính ${seconds} giây</span>`;
    const scene = sceneAt(Number(card.dataset.chapter), Number(card.dataset.scene));
    if (scene) card.querySelector(".docu-scene-time")!.textContent = sceneTime(scene);
  }

  function sceneAt(chapterIndex: number, sceneIndex: number): DocuScene | null {
    return currentScript?.chapters[chapterIndex]?.scenes[sceneIndex] ?? null;
  }

  function requestCheck(delay = 1500): void {
    if (!selected || !currentScript) return;
    clearTimeout(checkTimer);
    checkTimer = setTimeout(() => void checkScript(), delay);
  }

  async function checkScript(): Promise<void> {
    if (!selected || !currentScript) return;
    clearTimeout(checkTimer);
    if (!hasNarration(currentScript.chapters)) { $("#docu-ai-state").textContent = "Chưa có cảnh nào có lời đọc để kiểm tra."; return; }
    const script = currentScript;
    $("#docu-ai-state").textContent = "Đang kiểm tra và lưu kịch bản…";
    try {
      const checked = await invoke<Partial<DocuScript>>("docu_check_script", { dir: selected.dir, script });
      if (!Array.isArray(checked?.chapters)) throw new Error("Bộ kiểm tra không trả về kịch bản hợp lệ.");
      if (script !== currentScript) return;
      applyFlags(script.chapters, checked.chapters);
      script.script_sha256 = typeof checked.script_sha256 === "string" ? checked.script_sha256 : undefined;
      script.review_notes = Array.isArray(checked.review_notes) ? checked.review_notes : [];
      // Badges only: the text being typed in keeps its caret.
      for (const card of editor.querySelectorAll<HTMLElement>("[data-scene-card]")) {
        const chapterIndex = Number(card.dataset.chapter), sceneIndex = Number(card.dataset.scene);
        const scene = sceneAt(chapterIndex, sceneIndex);
        card.querySelector(".docu-review")?.remove();
        if (scene) card.insertAdjacentHTML("beforeend", reviewHtml(scene, chapterIndex, sceneIndex));
      }
      renderNarration();
      $("#docu-ai-state").textContent = "Đã kiểm tra và lưu kịch bản.";
    } catch (error) {
      $("#docu-ai-state").textContent = error instanceof Error ? error.message : String(error);
    }
  }

  function readForm(): DocuRequest | string {
    return buildRequest({ title: $("#docu-title").value, topic: $("#docu-topic").value,
      targetMinutes: $("#docu-minutes").value, cinematicFramingPreset: $("#docu-framing").value,
      burnEndCredits: $("#docu-credits").checked, sources: sourceForms, outputRoot: settings.outputRoot() });
  }

  async function createProject(): Promise<void> {
    const request = readForm();
    if (typeof request === "string") { $("#docu-setup-state").textContent = request; return; }
    const button = $("#docu-create") as HTMLButtonElement;
    button.disabled = true; $("#docu-setup-state").textContent = "Đang phân tích nguồn…";
    try {
      const created = await invoke<{ dir: string }>("docu_create", { request });
      await refreshRuns();
      const result = parseOpen(await invoke("docu_open", { dir: created.dir }));
      if (!result) throw new Error("Dự án mới chưa mở được.");
      selected = result; playerMode = "source"; currentScript = result.script ?? emptyScript(result.spec.title); narration = result.narration; visual = result.visual; render = result.render; capcut = result.capcut; labels = result.labels;
      $("#docu-project-name").textContent = result.spec.title;
      setTab("script"); renderRuns(); renderContexts(); renderEditor(); renderProjectInfo(); renderNarration(); followNarration();
      $("#docu-setup-state").textContent = "Đã tạo dự án và phân tích nguồn.";
    } catch (error) {
      $("#docu-setup-state").textContent = error instanceof Error ? error.message : String(error);
    } finally { button.disabled = false; }
  }

  async function writeAllChapters(): Promise<void> {
    if (!selected) { $("#docu-ai-state").textContent = "Trước hết hãy tạo dự án."; return; }
    renderProvider();
    if (!session.read()) { $("#docu-ai-state").textContent = `Hãy thêm khóa ${PROVIDERS[session.provider()].name} trong Cài đặt AI.`; return; }
    if (currentScript && hasNarration(currentScript.chapters) && !await confirmDialog({ title: "Viết lại toàn bộ kịch bản?",
      message: "AI sẽ viết lại cả 4 phần và thay kịch bản hiện tại, kể cả những chỗ bạn đã sửa.", confirmLabel: "Viết lại", cancelLabel: "Giữ kịch bản", danger: true })) return;
    const button = $("#docu-write") as HTMLButtonElement;
    button.disabled = true;
    try {
      currentScript = await writeScript(session.provider(), session.read(), { title: selected.spec.title, topic: selected.spec.topic,
        targetMinutes: selected.spec.target_minutes }, selected.contexts, undefined, (chapter, index, total) => {
        $("#docu-ai-state").textContent = `Đang viết phần ${index}/${total}: ${CHAPTER_NAMES[chapter]}…`;
      });
      renderEditor(); renderNarration();
      await checkScript();
    } catch (error) {
      $("#docu-ai-state").textContent = error instanceof Error ? error.message : String(error);
    } finally { button.disabled = false; }
  }

  async function refreshRuns(): Promise<void> {
    try { runs = parseRuns(await invoke("docu_runs", { roots: settings.roots?.() ?? [] })); renderRuns(); }
    catch { $("#docu-runs-empty").textContent = "Không tải được danh sách dự án."; }
  }

  async function openRun(dir: string, keepTab = false): Promise<void> {
    try {
      const opened = parseOpen(await invoke("docu_open", { dir }));
      if (!opened) throw new Error("Dữ liệu dự án không hợp lệ.");
      if (!sameDir(selected?.dir, opened.dir)) playerMode = "source";
      selected = opened; currentScript = opened.script ?? emptyScript(opened.spec.title); narration = opened.narration; visual = opened.visual; render = opened.render; capcut = opened.capcut; labels = opened.labels;
      $("#docu-project-name").textContent = opened.spec.title;
      renderRuns(); renderContexts(); renderEditor(); renderProjectInfo(); renderNarration(); followNarration(); followVisual(); followRender();
      if (!keepTab) setTab("script");
    } catch (error) { $("#docu-setup-state").textContent = error instanceof Error ? error.message : String(error); }
  }

  // ---- What Claude does over MCP, live (the MCP server writes control/mcp-activity.json) ----
  let mcpSeen = "";
  const MCP_PROGRESS: Record<string, string> = { narrate: "docu_narration_status", plan_visuals: "docu_plan_status", render_rough_cut: "docu_render_status",
    adjust_pacing_and_transitions: "docu_plan_status" };
  const sameDir = (a?: string | null, b?: string | null) => Boolean(a && b) && a!.replace(/\\/g, "/").toLowerCase() === b!.replace(/\\/g, "/").toLowerCase();
  // Today's calls show their time; older ones their date too, so the list reads in order.
  const timeOf = (value: unknown) => {
    if (typeof value !== "string" || Number.isNaN(Date.parse(value))) return "";
    const when = new Date(value), time = when.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
    return when.toDateString() === new Date().toDateString() ? time : `${when.getDate()}/${when.getMonth() + 1} ${time}`;
  };
  const voiceName = (voiceId: string) => voices.find(voice => voice.voiceId === voiceId)?.name;
  const said = (text: string) => readable(text, voiceName);

  async function pollMcp(): Promise<void> {
    let activity: McpActivity | null = null;
    try { activity = parseMcpActivity(await invoke("docu_mcp_activity")); } catch { return; }
    const current = activity?.current ?? null;
    settings.mcpBusy?.(current ? current.label : null);
    $("#docu-mcp-chip").hidden = !current;
    $("#docu-mcp-chip-text").textContent = current ? `Claude đang làm: ${said(current.label)}` : "";
    if (!activity) return;
    let progress = "";
    const command = current?.project ? MCP_PROGRESS[current.tool] : undefined;
    if (current?.project && command) {
      try {
        const info = await invoke(command, { dir: current.project }) as { status?: { done?: number; total?: number; message?: string } | null };
        const status = info?.status;
        if (status?.total) progress = ` · ${status.message ?? ""} (${status.done ?? 0}/${status.total})`;
      } catch { /* the project may not be readable yet */ }
    }
    $("#docu-mcp-current").textContent = current ? `Đang làm: ${said(current.label)}${progress}` : "Không làm gì lúc này.";
    $("#docu-mcp-current").classList.toggle("busy", Boolean(current));
    const target = current?.project ?? activity.recent[0]?.project ?? null;
    $("#docu-mcp-open").hidden = !target || sameDir(target, selected?.dir);
    $("#docu-mcp-open").dataset.dir = target ?? "";
    $("#docu-mcp-recent").innerHTML = [...activity.recent].sort((a, b) => (Date.parse(String(b.finished)) || 0) - (Date.parse(String(a.finished)) || 0)).slice(0, 6).map(item => `<li class="${item.state === "failed" ? "failed" : ""}"><time>${escapeHtml(timeOf(item.finished))}</time> ${escapeHtml(said(item.label))}<small>${escapeHtml(said(item.state === "failed" ? `Lỗi: ${item.message}` : item.message))}</small></li>`).join("");
    // Follow: open the project Claude works on, and reload it when a call on it finishes.
    const follow = $<HTMLInputElement>("#docu-mcp-follow").checked && !root.hidden;
    const newest = activity.recent[0];
    const stamp = `${current?.started ?? ""}|${newest?.finished ?? ""}`;
    if (stamp === mcpSeen) return;
    const first = mcpSeen === "";
    mcpSeen = stamp;
    if (first || !follow) return;
    if (current?.project && !sameDir(current.project, selected?.dir)) { await openRun(current.project, true); return; }
    if (newest?.project && newest.state === "done") {
      if (sameDir(newest.project, selected?.dir) || !selected) await openRun(newest.project, true);
      if (newest.tool === "create_project" || newest.tool === "list_projects") void refreshRuns();
    }
  }

  setInterval(() => void pollMcp(), 2000);
  void pollMcp();

  /** Under 8 minutes: a quiet tip about YouTube mid-roll ads; it never blocks creating the project. */
  function showMidrollHint(): void {
    const hint = $("#docu-minutes-hint");
    hint.textContent = midrollHint($("#docu-minutes").value);
    hint.hidden = !hint.textContent;
  }

  function newProject(): void {
    selected = null; playerMode = "source"; currentScript = null; sourceForms = []; narration = { audio: null, timeline: null, status: null }; clearInterval(narrationTimer);
    visual = { plan: null, status: null }; clearInterval(visualTimer);
    render = { video: null, status: null, options: parseRenderOptions(null) }; clearInterval(renderTimer); capcut = null; labels = [];
    $("#docu-project-name").textContent = "Tạo dự án mới";
    $("#docu-title").value = ""; $("#docu-topic").value = ""; $("#docu-minutes").value = "11"; showMidrollHint();
    $("#docu-framing").value = "none"; $("#docu-credits").checked = false; $("#docu-setup-state").textContent = "";
    $("#docu-output-root").value = settings.outputRoot() ?? "%USERPROFILE%\\Videos";
    renderSourcePicker(); renderRuns(); renderContexts(); renderEditor(); renderProjectInfo(); renderNarration(); setTab("setup");
  }

  // One "Thêm video" at a time: a second click while the file or subtitle question is open does nothing.
  let addingFile = false;
  async function addFileSource(): Promise<void> {
    if (addingFile) return;
    addingFile = true;
    $<HTMLButtonElement>("#docu-add-file").disabled = true;
    try {
      const videoPath = await invoke<string | null>("docu_pick_file", { kind: "video" });
      if (!videoPath) return;
      const withSubtitles = await confirmDialog({ title: "Phụ đề của video", message: "Video này có file phụ đề .srt không? Nếu không, DubFlow tự nhận dạng lời khi tạo dự án.",
        confirmLabel: "Chọn file .srt", cancelLabel: "Không có, tự nhận dạng" });
      const subtitlePath = withSubtitles ? await invoke("docu_pick_file", { kind: "subtitles" }) as string | null : null;
      if (withSubtitles && !subtitlePath) return;
      if (sourceForms.some(source => source.kind === "file" && source.video === videoPath)) { $("#docu-setup-state").textContent = "Video này đã có trong danh sách nguồn."; return; }
      sourceForms.push({ kind: "file", video: videoPath, subtitles: subtitlePath ?? undefined, title: videoName(videoPath).replace(/\.[^.]+$/, "") });
      renderSourcePicker();
    } catch (error) { $("#docu-setup-state").textContent = error instanceof Error ? error.message : String(error); }
    finally { addingFile = false; $<HTMLButtonElement>("#docu-add-file").disabled = false; }
  }

  $("#docu-refresh").addEventListener("click", () => void refreshRuns());
  $("#docu-minutes").addEventListener("change", showMidrollHint);
  $("#docu-mcp-open").addEventListener("click", () => { const dir = $("#docu-mcp-open").dataset.dir; if (dir) void openRun(dir); });
  $("#docu-new").addEventListener("click", () => { setListOpen(false); newProject(); });
  const setListOpen = (open: boolean) => {
    screen.classList.toggle("list-open", open); $("#docu-list-toggle").setAttribute("aria-expanded", String(open));
  };
  $("#docu-list-toggle").addEventListener("click", () => setListOpen(!screen.classList.contains("list-open")));
  closeDrawerOutside(screen, $(".docu-col"), $("#docu-list-toggle"), () => setListOpen(false));
  $("#docu-runs").addEventListener("click", event => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-run]");
    if (button) { setListOpen(false); void openRun(button.dataset.run!); }
  });
  for (const button of root.querySelectorAll<HTMLButtonElement>(".docu-inspector [data-tab]")) {
    button.addEventListener("click", () => setTab(button.dataset.tab as Tab));
  }
  $("#docu-add-job").addEventListener("click", () => {
    const id = ($("#docu-job-select") as HTMLSelectElement).value;
    const job = completedDocuJobs(settings.jobs()).find(item => item.id === id);
    if (!job?.outputPath) return;
    if (sourceForms.some(source => source.kind === "job" && source.outputPath === job.outputPath)) return;
    sourceForms.push({ kind: "job", outputPath: job.outputPath, title: sourceTitle(job) }); renderSourcePicker();
  });
  $("#docu-add-file").addEventListener("click", () => void addFileSource());
  $("#docu-source-order").addEventListener("click", event => {
    const target = (event.target as HTMLElement).closest<HTMLButtonElement>("button");
    if (!target) return;
    if (target.dataset.sourceRemove !== undefined) sourceForms.splice(Number(target.dataset.sourceRemove), 1);
    else if (target.dataset.sourceMove !== undefined) sourceForms = moveItem(sourceForms, Number(target.dataset.sourceIndex), Number(target.dataset.sourceMove));
    renderSourcePicker();
  });
  $("#docu-create").addEventListener("click", () => void createProject());
  $("#docu-write").addEventListener("click", () => void writeAllChapters());
  $("#docu-check").addEventListener("click", () => void checkScript());
  $("#docu-narrate").addEventListener("click", () => void narrate());
  $("#docu-plan").addEventListener("click", () => void makePlan());
  $("#docu-render").addEventListener("click", () => void makeRoughCut());
  for (const id of ["#docu-opt-graphics", "#docu-opt-captions", "#docu-opt-music", "#docu-opt-sfx"]) $(id).addEventListener("change", () => void saveRenderOptions());
  $("#docu-opt-music-file").addEventListener("click", async () => {
    const path = await invoke("docu_pick_file", { kind: "music" }).catch(() => null) as string | null;
    if (path) void saveRenderOptions(path);
  });
  $("#docu-render-cancel").addEventListener("click", () => { if (selected) void invoke("docu_render_cancel", { dir: selected.dir }).catch(() => undefined); });
  $("#docu-render-watch").addEventListener("click", () => void setPlayerSource(ROUGH_CUT, 0));
  $("#docu-show-draft").addEventListener("click", () => void setPlayerSource(ROUGH_CUT));
  $("#docu-show-source").addEventListener("click", () => {
    const source = $<HTMLSelectElement>("#docu-player-source").value || selected?.contexts[0]?.source_id;
    if (source) void setPlayerSource(source);
  });
  $("#docu-mcp-chip").addEventListener("click", () => setTab("claude"));
  $("#docu-render-edit").addEventListener("click", () => {
    if (!render.video || !settings.edit) return;
    // The first draft gets the script's on-screen graphics; a saved draft is kept as it is.
    // A rough cut that already burns the number cards gets none again in the editor's first draft.
    const callouts = calloutTexts(currentScript, narration.timeline, labels, { graphics: !render.status?.overlays?.graphics });
    settings.edit({ outputPath: render.video, displayName: currentScript?.project_title || selected?.spec.title || "documentary.mp4" },
      edit => callouts.reduce((draft, callout) => addText(draft, callout.at, callout.text, callout.look, callout.seconds), edit));
  });
  $("#docu-capcut-export").addEventListener("click", () => void exportCapcut());
  for (const [button, field] of [["#docu-mcp-copy-code", "#docu-mcp-code"], ["#docu-mcp-copy-desktop", "#docu-mcp-desktop"]] as const) {
    $(button).addEventListener("click", () => {
      const text = $<HTMLTextAreaElement>(field).value;
      void navigator.clipboard?.writeText(text).then(() => { $(button).textContent = "Đã sao chép"; setTimeout(() => { $(button).textContent = button.endsWith("code") ? "Sao chép lệnh" : "Sao chép cấu hình"; }, 1500); });
    });
  }
  void invoke("docu_mcp_connection").then(value => {
    const connection = parseMcp(value);
    if (!connection) return;
    $<HTMLTextAreaElement>("#docu-mcp-code").value = connection.claudeCode;
    $<HTMLTextAreaElement>("#docu-mcp-desktop").value = connection.claudeDesktop;
  }).catch(() => { $("#docu-mcp-title").closest("section")!.hidden = true; });
  $("#docu-capcut-open").addEventListener("click", () => void invoke("docu_launch_capcut").catch(error => renderCapcut(error instanceof Error ? error.message : String(error))));
  $("#docu-render-reveal").addEventListener("click", () => { if (render.video) void invoke("reveal_path", { path: render.video }).catch(() => undefined); });
  $("#docu-plan-cancel").addEventListener("click", () => { if (selected) void invoke("docu_plan_cancel", { dir: selected.dir }).catch(() => undefined); });
  $("#docu-narration-cancel").addEventListener("click", () => { if (selected) void invoke("docu_narration_cancel", { dir: selected.dir }).catch(() => undefined); });
  $("#docu-project-info").addEventListener("change", event => {
    const target = event.target as HTMLElement;
    if (target.id === "docu-framing-change") void changeFraming((target as HTMLSelectElement).value);
  });
  $("#docu-player-source").addEventListener("change", () => void setPlayerSource(($("#docu-player-source") as HTMLSelectElement).value));
  video.addEventListener("timeupdate", () => {
    if (stoppingAt !== null && video.currentTime >= stoppingAt) { video.pause(); stoppingAt = null; }
  });
  editor.addEventListener("click", event => {
    const target = (event.target as HTMLElement).closest<HTMLButtonElement>("button");
    if (!target || !currentScript) return;
    if (target.dataset.seekSource) { void setPlayerSource(target.dataset.seekSource, Number(target.dataset.seekStart), Number(target.dataset.seekEnd)); return; }
    if (target.dataset.addScene !== undefined) {
      const chapter = currentScript.chapters[Number(target.dataset.addScene)];
      const index = target.dataset.after === undefined ? chapter.scenes.length : Number(target.dataset.after) + 1;
      chapter.scenes.splice(index, 0, emptyScene()); currentScript.chapters = renumberScenes(currentScript.chapters); currentScript.script_sha256 = undefined; renderEditor(); renderNarration(); requestCheck(); return;
    }
    if (target.dataset.deleteScene !== undefined) {
      const chapter = currentScript.chapters[Number(target.dataset.deleteScene)];
      chapter.scenes.splice(Number(target.dataset.scene), 1); currentScript.chapters = renumberScenes(currentScript.chapters); currentScript.script_sha256 = undefined; renderEditor(); renderNarration(); requestCheck(); return;
    }
    if (target.dataset.removeRef !== undefined) {
      sceneAt(Number(target.dataset.chapter), Number(target.dataset.scene))?.matched_source_refs.splice(Number(target.dataset.removeRef), 1);
      currentScript.chapters = renumberScenes(currentScript.chapters); renderEditor(); requestCheck(); return;
    }
    if (target.dataset.acknowledge) {
      const [chapterIndex, sceneIndex] = target.dataset.acknowledge.split(":").map(Number);
      const scene = sceneAt(chapterIndex, sceneIndex);
      if (scene) { currentScript.chapters[chapterIndex].scenes[sceneIndex] = acknowledge(scene); requestCheck(0); }
    }
  });
  editor.addEventListener("input", event => {
    const field = (event.target as HTMLElement).closest<HTMLInputElement | HTMLTextAreaElement>("[data-field]");
    if (!field || !currentScript) return;
    const scene = sceneAt(Number(field.dataset.chapter), Number(field.dataset.scene));
    if (!scene) return;
    const name = field.dataset.field;
    if (name === "narration_text") { scene.narration_text = field.value; currentScript.script_sha256 = undefined; updateSceneMetrics(field as HTMLTextAreaElement); renderNarration(); }
    else if (name === "graphic_title" || name === "graphic_value" || name === "graphic_subtext") {
      const current = scene.on_screen_graphic;
      if (current) current[name === "graphic_title" ? "title" : name === "graphic_value" ? "value" : "subtext"] = field.value;
    }
    requestCheck();
  });
  editor.addEventListener("change", event => {
    const mood = (event.target as HTMLElement).closest<HTMLSelectElement>("[data-mood]");
    if (mood && currentScript) { currentScript.chapters[Number(mood.dataset.mood)].recommended_bgm_mood = mood.value as Mood; requestCheck(); return; }
    const field = (event.target as HTMLElement).closest<HTMLSelectElement>("[data-field]");
    if (!field || !currentScript) return;
    const scene = sceneAt(Number(field.dataset.chapter), Number(field.dataset.scene));
    if (!scene) return;
    if (field.dataset.field === "graphic_type") {
      const kept = scene.on_screen_graphic;
      scene.on_screen_graphic = field.value ? { type: field.value as GraphicType, title: kept?.title ?? "", value: kept?.value ?? "", subtext: kept?.subtext ?? "" } : null;
      renderEditor();
    } else if (field.dataset.field === "ambient_sfx_tag") scene.ambient_sfx_tag = field.value as DocuScene["ambient_sfx_tag"];
    requestCheck();
  });

  $("#docu-output-root").value = settings.outputRoot() ?? "%USERPROFILE%\\Videos";
  renderProvider();
  renderSourcePicker();
  renderRuns();
  return {
    show() {
      if (!shown) { shown = true; newProject(); void refreshRuns(); void loadVoices(); }
      renderSourcePicker(); renderProvider();
    },
  };
}
