import { PROVIDERS, type AiSession } from "../ai/provider.ts";
import { confirmDialog } from "../../shell/confirm.ts";
import {
  AMBIENT_SFX, CHAPTER_NAMES, CHAPTER_ORDER, DEFAULT_MOODS, GRAPHIC_NAMES, GRAPHIC_TYPES, MAX_NARRATION, MOODS, MOOD_NAMES,
  acknowledge, applyFlags, buildRequest, chapterTargetSeconds, completedDocuJobs, estimatedSeconds, flattenScenes, hasNarration,
  moveItem, parseOpen, parseRuns, renumberScenes, sourceTitle, wordCount,
  type DocuJob, type DocuRequest, type DocuRun, type DocuScene, type DocuScript, type GraphicType, type Mood, type OpenDocuRun, type SourceForm,
} from "./model.ts";
import { writeScript } from "./ai.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Settings = { jobs(): readonly DocuJob[]; outputRoot(): string | null; roots?(): string[]; mediaUrl?(path: string): Promise<string | null> };
const escapeHtml = (value: unknown): string => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]!));
const videoName = (path: string): string => path.split(/[\\/]/).at(-1) ?? path;
const clock = (seconds: number): string => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
const emptyScript = (title = ""): DocuScript => ({ project_title: title, target_Coordinates_3D: null,
  chapters: CHAPTER_ORDER.map(chapter_type => ({ chapter_type, recommended_bgm_mood: DEFAULT_MOODS[chapter_type], scenes: [] })), review_notes: [] });
const emptyScene = (): DocuScene => ({ scene_id: "", narration_text: "", matched_source_refs: [], visual_search_tags: [],
  on_screen_graphic: null, ambient_sfx_tag: null, needs_review: false,
  review_notes: [], review_numbers: [], acknowledged_numbers: [] });

/** The documentary source-and-script workspace (ADR-0049, phase 1). */
export function mountDocu(invoke: Invoke, session: AiSession, settings: Settings): { show(): void } {
  const root = document.querySelector<HTMLElement>("#docu-view")!;
  root.innerHTML = `
    <div id="docu-screen" class="queue-screen docu-screen">
      <section class="queue-col docu-col" aria-labelledby="docu-list-title">
        <div class="queue-col-head"><h2 id="docu-list-title">Phim tài liệu</h2><button id="docu-refresh" class="quiet-btn" type="button">Làm mới</button></div>
        <div class="queue-scroll"><ul id="docu-runs" class="queue-list" aria-label="Dự án phim tài liệu"></ul><p id="docu-runs-empty" class="queue-empty">Chưa có dự án phim tài liệu nào.</p></div>
        <div class="queue-col-foot"><button id="docu-new" class="btn block" type="button">Tạo dự án mới</button></div>
      </section>
      <section class="player-col docu-player-col" aria-label="Kịch bản phim tài liệu">
        <div class="player-head"><button id="docu-list-toggle" class="btn ghost list-toggle" type="button" aria-controls="docu-screen" aria-expanded="false">Dự án</button><h2 id="docu-project-name" class="player-title">Tạo dự án mới</h2></div>
        <div class="docu-workspace">
          <div class="docu-player">
            <div class="docu-player-head"><label for="docu-player-source">Nguồn xem trước</label><select id="docu-player-source" aria-label="Chọn video nguồn"></select></div>
            <video id="docu-video" controls playsinline preload="metadata" aria-label="Video nguồn"></video>
            <p id="docu-player-empty" class="player-placeholder">Chọn dự án có nguồn video để xem trước.</p>
          </div>
          <div id="docu-editor" class="docu-editor"><p class="queue-empty">Chọn dự án hoặc tạo dự án mới để bắt đầu.</p></div>
        </div>
      </section>
      <aside class="inspector docu-inspector" aria-label="Thiết lập và kịch bản">
        <div class="tabs" role="tablist" aria-label="Phim tài liệu">
          <button id="docu-tab-setup-btn" type="button" role="tab" data-tab="setup" aria-controls="docu-tab-setup" aria-selected="true">Thiết lập</button>
          <button id="docu-tab-script-btn" type="button" role="tab" data-tab="script" aria-controls="docu-tab-script" aria-selected="false">Kịch bản</button>
        </div>
        <div id="docu-tab-setup" class="tab-panel" role="tabpanel" aria-labelledby="docu-tab-setup-btn">
          <div class="tab-scroll">
            <fieldset id="docu-project-fields" class="docu-fields">
              <label>Tiêu đề dự án<input id="docu-title" maxlength="120" /></label>
              <label>Chủ đề hoặc ghi chú<textarea id="docu-topic" rows="3" maxlength="600"></textarea></label>
              <label>Thời lượng mục tiêu<select id="docu-minutes">${Array.from({ length: 18 }, (_, i) => `<option value="${i + 3}" ${i === 8 ? "selected" : ""}>${i + 3} phút</option>`).join("")}</select></label>
              <label>Khung hình<select id="docu-framing"><option value="none">Không</option><option value="letterbox_235">Letterbox 2.35:1</option><option value="overscan_108">Tight overscan 108%</option></select></label>
              <label class="docu-check"><input id="docu-credits" type="checkbox" /> Chèn màn hình credit cuối video</label>
              <section class="docu-source-picker" aria-labelledby="docu-source-title">
                <h3 id="docu-source-title">Nguồn (2–5)</h3>
                <label>Video đã hoàn tất<select id="docu-job-select"></select></label>
                <button id="docu-add-job" class="btn" type="button">Thêm video đã xong</button>
                <button id="docu-add-file" class="btn" type="button">Thêm video và phụ đề</button>
                <ol id="docu-source-order" class="docu-source-order"></ol>
              </section>
              <label>Thư mục lưu<input id="docu-output-root" readonly /></label>
              <p id="docu-setup-state" class="hint" role="status"></p>
              <button id="docu-create" class="btn primary block" type="button">Tạo dự án và phân tích nguồn</button>
            </fieldset>
          </div>
        </div>
        <div id="docu-tab-script" class="tab-panel" role="tabpanel" aria-labelledby="docu-tab-script-btn" hidden>
          <div class="tab-scroll">
            <dl id="docu-project-info" class="docu-summary"></dl>
            <p id="docu-provider" class="tab-note boxed"></p>
            <button id="docu-write" class="btn primary block" type="button">Viết kịch bản bằng AI</button>
            <p id="docu-ai-state" class="discover-state" role="status"></p>
            <button id="docu-check" class="btn block" type="button">Kiểm tra lại</button>
            <dl id="docu-summary" class="docu-summary"></dl>
            <ul id="docu-script-notes" class="docu-script-notes"></ul>
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
  let checkTimer: ReturnType<typeof setTimeout> | undefined;
  let sourceVideoUrl: string | null = null;
  let stoppingAt: number | null = null;

  function setTab(tab: "setup" | "script"): void {
    for (const name of ["setup", "script"] as const) {
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
    select.innerHTML = contexts.map(context => `<option value="${escapeHtml(context.source_id)}">${escapeHtml(context.source_id)} · ${escapeHtml(context.title)}</option>`).join("");
    if (!contexts.length) {
      video.removeAttribute("src"); video.load();
      $("#docu-player-empty").hidden = false;
      return;
    }
    $("#docu-player-empty").hidden = true;
    void setPlayerSource(contexts[0].source_id);
  }

  async function setPlayerSource(sourceId: string, start?: number, end?: number): Promise<void> {
    const context = selected?.contexts.find(source => source.source_id === sourceId);
    if (!context) return;
    const url = await settings.mediaUrl?.(context.video_path) ?? null;
    if (!url) {
      $("#docu-player-empty").hidden = false;
      $("#docu-player-empty").textContent = "Không mở được video nguồn để xem trước.";
      return;
    }
    if (sourceVideoUrl !== url) { sourceVideoUrl = url; video.src = url; video.load(); }
    ($( "#docu-player-source") as HTMLSelectElement).value = sourceId;
    if (start !== undefined) {
      stoppingAt = end ?? null;
      const seek = () => { video.currentTime = Math.max(0, start); void video.play().catch(() => undefined); };
      if (video.readyState >= 1) seek(); else video.addEventListener("loadedmetadata", seek, { once: true });
    } else stoppingAt = null;
  }

  function updateSummary(): void {
    const script = currentScript;
    if (!script || !selected) { $("#docu-summary").innerHTML = ""; $("#docu-script-notes").innerHTML = ""; return; }
    const scenes = flattenScenes(script.chapters).map(item => item.scene);
    const words = scenes.reduce((sum, scene) => sum + wordCount(scene.narration_text), 0);
    const seconds = scenes.reduce((sum, scene) => sum + estimatedSeconds(scene.narration_text), 0);
    const target = selected.spec.target_minutes * 60;
    const reviewCount = scenes.filter(scene => scene.needs_review).length;
    $("#docu-summary").innerHTML = `<dt>Cảnh</dt><dd>${scenes.length}</dd><dt>Từ</dt><dd>${words}</dd><dt>Thời lượng ước tính</dt><dd>${clock(seconds)} / ${clock(target)}</dd><dt>Cần kiểm tra</dt><dd>${reviewCount}</dd>`;
    $("#docu-script-notes").innerHTML = (script.review_notes ?? []).map(note => `<li>${escapeHtml(note)}</li>`).join("");
  }

  function renderProjectInfo(): void {
    $("#docu-ai-state").textContent = "";
    const spec = selected?.spec;
    const framing: Record<string, string> = { none: "Không", letterbox_235: "Letterbox 2.35:1", overscan_108: "Tight overscan 108%" };
    $("#docu-project-info").innerHTML = spec ? `<dt>Nguồn</dt><dd>${spec.sources.map(source => `${escapeHtml(source.id)} · ${escapeHtml(source.title)}`).join("<br>")}</dd>
      <dt>Mục tiêu</dt><dd>${spec.target_minutes} phút</dd><dt>Khung hình</dt><dd>${escapeHtml(framing[spec.cinematic_framing_preset] ?? spec.cinematic_framing_preset)}</dd>
      <dt>Credit cuối video</dt><dd>${spec.burn_end_credits ? "Có" : "Không (chỉ ghi trong mô tả)"}</dd>` : "";
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
    return scene.matched_source_refs.map((ref, index) => `<span class="docu-ref-wrap"><button type="button" class="docu-ref" data-seek-source="${escapeHtml(ref.source_id)}" data-seek-start="${ref.start}" data-seek-end="${ref.end}" aria-label="Xem ${escapeHtml(ref.source_id)} từ ${ref.start.toFixed(1)} đến ${ref.end.toFixed(1)} giây">${escapeHtml(ref.source_id)} ${ref.start.toFixed(1)}–${ref.end.toFixed(1)}</button><button type="button" class="docu-ref-remove" data-remove-ref="${index}" data-chapter="${chapterIndex}" data-scene="${sceneIndex}" aria-label="Bỏ nguồn dẫn ${index + 1}">×</button></span>`).join("") || `<span class="hint">Chưa có nguồn dẫn</span>`;
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
      <header><strong>${escapeHtml(scene.scene_id)}</strong><span class="docu-scene-time">${estimatedSeconds(scene.narration_text)} giây</span><button type="button" class="quiet-btn" data-add-scene="${chapterIndex}" data-after="${sceneIndex}">Thêm sau</button><button type="button" class="quiet-btn" data-delete-scene="${chapterIndex}" data-scene="${sceneIndex}">Xóa cảnh</button></header>
      <label>Lời dẫn<textarea data-field="narration_text" data-chapter="${chapterIndex}" data-scene="${sceneIndex}" maxlength="${MAX_NARRATION}" rows="4">${escapeHtml(scene.narration_text)}</textarea></label>
      <p class="docu-word-count"><span>${wordCount(scene.narration_text)} từ</span><span>Ước tính ${estimatedSeconds(scene.narration_text)} giây</span></p>
      <div class="docu-refs" aria-label="Nguồn dẫn">${refsHtml(scene, chapterIndex, sceneIndex)}</div>
      <label>Đồ họa trên màn hình<select data-field="graphic_type" data-chapter="${chapterIndex}" data-scene="${sceneIndex}"><option value="">Không có</option>${GRAPHIC_TYPES.map(type => `<option value="${type}" ${graphic?.type === type ? "selected" : ""}>${GRAPHIC_NAMES[type]}</option>`).join("")}</select></label>
      <div class="docu-graphic-fields" ${graphic ? "" : "hidden"}><label>Tiêu đề<input data-field="graphic_title" data-chapter="${chapterIndex}" data-scene="${sceneIndex}" value="${escapeHtml(graphic?.title ?? "")}" maxlength="60" /></label><label>Giá trị<input data-field="graphic_value" data-chapter="${chapterIndex}" data-scene="${sceneIndex}" value="${escapeHtml(graphic?.value ?? "")}" maxlength="60" /></label><label>Chú thích<input data-field="graphic_subtext" data-chapter="${chapterIndex}" data-scene="${sceneIndex}" value="${escapeHtml(graphic?.subtext ?? "")}" maxlength="120" /></label></div>
      <label>Âm thanh nền<select data-field="ambient_sfx_tag" data-chapter="${chapterIndex}" data-scene="${sceneIndex}"><option value="">Không có</option>${AMBIENT_SFX.map(tag => `<option value="${tag}" ${scene.ambient_sfx_tag === tag ? "selected" : ""}>${tag}</option>`).join("")}</select></label>
      ${reviewHtml(scene, chapterIndex, sceneIndex)}
    </article>`;
  }

  function updateSceneMetrics(text: HTMLTextAreaElement): void {
    const card = text.closest<HTMLElement>("[data-scene-card]");
    if (!card) return;
    const words = wordCount(text.value), seconds = estimatedSeconds(text.value);
    card.querySelector(".docu-word-count")!.innerHTML = `<span>${words} từ</span><span>Ước tính ${seconds} giây</span>`;
    card.querySelector(".docu-scene-time")!.textContent = `${seconds} giây`;
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
      script.review_notes = Array.isArray(checked.review_notes) ? checked.review_notes : [];
      // Badges only: the text being typed in keeps its caret.
      for (const card of editor.querySelectorAll<HTMLElement>("[data-scene-card]")) {
        const chapterIndex = Number(card.dataset.chapter), sceneIndex = Number(card.dataset.scene);
        const scene = sceneAt(chapterIndex, sceneIndex);
        card.querySelector(".docu-review")?.remove();
        if (scene) card.insertAdjacentHTML("beforeend", reviewHtml(scene, chapterIndex, sceneIndex));
      }
      updateSummary();
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
      selected = result; currentScript = result.script ?? emptyScript(result.spec.title);
      $("#docu-project-name").textContent = result.spec.title;
      setTab("script"); renderRuns(); renderContexts(); renderEditor(); renderProjectInfo();
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
      renderEditor();
      await checkScript();
    } catch (error) {
      $("#docu-ai-state").textContent = error instanceof Error ? error.message : String(error);
    } finally { button.disabled = false; }
  }

  async function refreshRuns(): Promise<void> {
    try { runs = parseRuns(await invoke("docu_runs", { roots: settings.roots?.() ?? [] })); renderRuns(); }
    catch { $("#docu-runs-empty").textContent = "Không tải được danh sách dự án."; }
  }

  async function openRun(dir: string): Promise<void> {
    try {
      const opened = parseOpen(await invoke("docu_open", { dir }));
      if (!opened) throw new Error("Dữ liệu dự án không hợp lệ.");
      selected = opened; currentScript = opened.script ?? emptyScript(opened.spec.title);
      $("#docu-project-name").textContent = opened.spec.title;
      renderRuns(); renderContexts(); renderEditor(); renderProjectInfo(); setTab("script");
    } catch (error) { $("#docu-setup-state").textContent = error instanceof Error ? error.message : String(error); }
  }

  function newProject(): void {
    selected = null; currentScript = null; sourceForms = [];
    $("#docu-project-name").textContent = "Tạo dự án mới";
    $("#docu-title").value = ""; $("#docu-topic").value = ""; $("#docu-minutes").value = "11";
    $("#docu-framing").value = "none"; $("#docu-credits").checked = false; $("#docu-setup-state").textContent = "";
    $("#docu-output-root").value = settings.outputRoot() ?? "%USERPROFILE%\\Videos";
    renderSourcePicker(); renderRuns(); renderContexts(); renderEditor(); renderProjectInfo(); setTab("setup");
  }

  async function addFileSource(): Promise<void> {
    try {
      const videoPath = await invoke<string | null>("docu_pick_file", { kind: "video" });
      if (!videoPath) return;
      const subtitlePath = await invoke<string | null>("docu_pick_file", { kind: "subtitles" });
      if (!subtitlePath) return;
      if (sourceForms.some(source => source.kind === "file" && source.video === videoPath)) { $("#docu-setup-state").textContent = "Video này đã có trong danh sách nguồn."; return; }
      sourceForms.push({ kind: "file", video: videoPath, subtitles: subtitlePath, title: videoName(videoPath).replace(/\.[^.]+$/, "") });
      renderSourcePicker();
    } catch (error) { $("#docu-setup-state").textContent = error instanceof Error ? error.message : String(error); }
  }

  $("#docu-refresh").addEventListener("click", () => void refreshRuns());
  $("#docu-new").addEventListener("click", newProject);
  $("#docu-list-toggle").addEventListener("click", () => {
    const open = screen.classList.toggle("list-open"); $("#docu-list-toggle").setAttribute("aria-expanded", String(open));
  });
  $("#docu-runs").addEventListener("click", event => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-run]");
    if (button) void openRun(button.dataset.run!);
  });
  for (const button of root.querySelectorAll<HTMLButtonElement>(".docu-inspector [data-tab]")) {
    button.addEventListener("click", () => setTab(button.dataset.tab as "setup" | "script"));
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
      chapter.scenes.splice(index, 0, emptyScene()); currentScript.chapters = renumberScenes(currentScript.chapters); renderEditor(); requestCheck(); return;
    }
    if (target.dataset.deleteScene !== undefined) {
      const chapter = currentScript.chapters[Number(target.dataset.deleteScene)];
      chapter.scenes.splice(Number(target.dataset.scene), 1); currentScript.chapters = renumberScenes(currentScript.chapters); renderEditor(); requestCheck(); return;
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
    if (name === "narration_text") { scene.narration_text = field.value; updateSceneMetrics(field as HTMLTextAreaElement); updateSummary(); }
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
      if (!shown) { shown = true; newProject(); void refreshRuns(); }
      renderSourcePicker(); renderProvider();
    },
  };
}
