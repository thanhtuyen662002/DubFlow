import { writeEditorial, MAX_CUES } from "./ai.ts";
import {
  buildRequest, completedVideos, moveItem, newItem, parseRuns, parseStatus, runFraction, specToForm,
  type CompilationRun, type CompletedVideo, type ItemForm,
} from "./model.ts";
import { PROVIDERS, type AiSession } from "../ai/provider.ts";
import { parseVoiceCatalog } from "../voices/model.ts";
import { closeDrawerOutside } from "../../shell/drawer.ts";
import { confirmDeleteProject, deleteProjectButton } from "../../shell/project_delete.ts";
import { fileName, shortDate } from "../product/model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type ExportEntry = { path: string; created: number; duration: number };
/** A finished compilation the editor can open. */
export type CompilationVideo = { outputPath: string; displayName: string };

const NEW = "new";
const POLL_MS = 1500;
const LAYOUT_NAMES: Record<string, string> = { "bang-thong-tin": "bảng thông tin bên trái", "toan-man-hinh": "toàn màn hình" };
const ICON_PLAY = '<svg class="icon-play" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M7 4.5v15l12.5-7.5z" fill="currentColor"/></svg>'
  + '<svg class="icon-pause" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" fill="currentColor"/></svg>';
const ICON_EDIT = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M4 20h4L18.5 9.5a2.1 2.1 0 0 0-3-3L5 17v3M13.5 6.5l3 3" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>';

function clock(seconds: number): string {
  const total = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  const hours = Math.floor(total / 3600), minutes = Math.floor(total / 60) % 60, rest = total % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}` : `${minutes}:${String(rest).padStart(2, "0")}`;
}

/**
 * The Tổng hợp page, laid out like the queue (Issue #426): compilations on
 * the left (earlier runs found in the output folders, "Tạo tổng hợp mới"),
 * the player (or the new compilation's preview) in the middle, and the
 * Thiết lập / Kết quả tabs on the right. The draft (selection, order and
 * editorial content) stays in memory across navigation.
 */
export function mountCompilation(invoke: Invoke, session: AiSession, settings: {
  jobs(): readonly CompletedVideo[]; outputRoot(): string | null; cpuOnly(): boolean;
  /** Folders whose "DubFlow Output" may hold earlier compilations. */
  roots?(): string[];
  /** A URL the page's player can stream a local video from, or null. */
  mediaUrl?(path: string): Promise<string | null>;
  /** Open a finished compilation in the editor. */
  edit?(video: CompilationVideo): void;
}): { show(): void } {
  const root = document.querySelector<HTMLElement>("#compilation-view")!;
  root.innerHTML = `
    <div id="compilation-screen" class="queue-screen compilation-screen">
      <section id="compilation-col" class="queue-col compilation-col" aria-labelledby="compilation-col-title">
        <div class="queue-col-head"><h2 id="compilation-col-title">Video tổng hợp</h2><button id="compilation-refresh" class="quiet-btn" type="button">Làm mới</button></div>
        <div class="queue-scroll">
          <ul id="compilation-runs" class="queue-list" aria-label="Video tổng hợp"></ul>
          <p id="compilation-runs-empty" class="queue-empty">Chưa có video tổng hợp nào. Các lần tạo trước được tìm trong thư mục “DubFlow Output”.</p>
          <p id="compilation-runs-state" class="queue-state" role="status"></p>
        </div>
        <div class="queue-col-foot"><button id="compilation-new" class="btn block" type="button">Tạo tổng hợp mới</button></div>
      </section>
      <section class="player-col compilation-player-col" aria-label="Xem video tổng hợp">
        <div class="player-head">
          <button id="compilation-list-toggle" class="btn ghost list-toggle" type="button" aria-controls="compilation-col" aria-expanded="false">Danh sách</button>
          <h2 id="compilation-name" class="player-title">Tạo tổng hợp mới</h2>
          <button id="compilation-edit" class="btn edit-open" type="button" hidden title="Cắt ghép, chèn video, âm thanh và chữ">${ICON_EDIT}Chỉnh sửa</button>
        </div>
        <div class="player-stage">
          <div id="compilation-frame" class="player-frame">
            <video id="compilation-video" preload="metadata" playsinline></video>
            <div id="compilation-preview" class="compilation-preview" aria-label="Xem trước phần đang soạn"></div>
            <div id="compilation-placeholder" class="player-placeholder" hidden><span id="compilation-placeholder-text"></span></div>
            <div id="compilation-busy" class="player-overlay" role="status" hidden>
              <strong id="compilation-busy-title">Đang tạo video tổng hợp</strong>
              <div class="bar busy-bar"><span id="compilation-busy-bar"></span></div>
              <span id="compilation-busy-detail" class="overlay-detail"></span>
              <div class="overlay-actions"><button id="compilation-busy-cancel" class="btn" type="button">Hủy tổng hợp</button></div>
            </div>
            <div id="compilation-failed" class="player-overlay failed" role="alert" hidden>
              <strong id="compilation-failed-title">Không tạo được video tổng hợp</strong>
              <span id="compilation-failed-detail" class="overlay-detail"></span>
              <div class="overlay-actions"><button id="compilation-failed-reuse" class="btn primary" type="button">Dùng lại thiết lập</button></div>
            </div>
          </div>
        </div>
        <div class="player-controls">
          <button id="compilation-play" class="icon-btn" type="button" aria-label="Phát" disabled>${ICON_PLAY}</button>
          <span id="compilation-time" class="player-time">0:00 / 0:00</span>
          <span class="spacer"></span>
          <span id="compilation-meta" class="status-timing"></span>
        </div>
      </section>
      <aside class="inspector compilation-inspector" aria-label="Chi tiết video tổng hợp">
        <div class="tabs" role="tablist" aria-label="Chi tiết video tổng hợp">
          <button id="compilation-tab-setup-btn" type="button" role="tab" data-tab="setup" aria-controls="compilation-tab-setup" aria-selected="true">Thiết lập</button>
          <button id="compilation-tab-result-btn" type="button" role="tab" data-tab="result" aria-controls="compilation-tab-result" aria-selected="false">Kết quả</button>
        </div>
        <div id="compilation-tab-setup" class="tab-panel" role="tabpanel" aria-labelledby="compilation-tab-setup-btn">
          <div class="tab-scroll">
            <div id="compilation-setup-past" class="compilation-past" hidden>
              <p id="compilation-past-note" class="tab-note boxed"></p>
              <dl id="compilation-past-facts" class="compilation-facts"></dl>
              <ol id="compilation-past-parts" class="compilation-past-parts"></ol>
              <button id="compilation-reuse" class="btn block" type="button">Dùng lại thiết lập này cho bản mới</button>
            </div>
            <fieldset id="compilation-fields" class="compilation-fields">
              <legend class="visually-hidden">Thiết lập video tổng hợp</legend>
              <section class="compilation-group">
                <h3 class="setup-group">Chọn video</h3><p class="hint">Chọn 2–30 video đã hoàn tất. Dùng ↑ ↓ để sắp xếp các phần.</p>
                <p id="compilation-empty" class="hint">Chưa có video đã hoàn tất trong hàng đợi.</p>
                <ul id="compilation-available" class="compilation-list"></ul>
                <ol id="compilation-order" class="compilation-list compilation-order"></ol>
              </section>
              <section class="compilation-group">
                <h3 class="setup-group">Nội dung</h3>
                <div class="product-form">
                  <label class="wide">Chủ đề / tiêu đề<input id="compilation-title" maxlength="120" /></label>
                  <label class="wide">Bố cục<select id="compilation-layout"><option value="bang-thong-tin">Bảng thông tin bên trái (cho video dọc)</option><option value="toan-man-hinh">Toàn màn hình</option></select></label>
                </div>
                <p id="compilation-provider" class="hint"></p>
                <button id="compilation-ai" type="button" class="btn">AI viết mở đầu, chuyển cảnh &amp; nhận xét</button>
                <p id="compilation-ai-state" class="discover-state" role="status"></p>
                <div class="product-form">
                  <label class="wide">Mở đầu (tối đa 800 ký tự)<textarea id="compilation-intro" rows="4" maxlength="800"></textarea></label>
                  <label class="wide">Kết thúc (tối đa 800 ký tự)<textarea id="compilation-outro" rows="4" maxlength="800"></textarea></label>
                </div>
                <ol id="compilation-editors" class="product-scripts"></ol>
              </section>
              <section class="compilation-group">
                <h3 class="setup-group">Giọng đọc và nhạc</h3>
                <div class="product-options">
                  <label>Giọng đọc<select id="compilation-voice"></select></label>
                  <label>Tốc độ đọc<select id="compilation-speed"><option value="1">Bình thường (1,0)</option><option value="1.1">1,1</option><option value="1.2">1,2</option><option value="1.3">Nhanh (1,3)</option></select></label>
                  <span class="product-music">Nhạc: <span id="compilation-music-name">Không có</span><button id="compilation-music" class="link" type="button">Chọn…</button><button id="compilation-music-clear" class="link" type="button" hidden>Bỏ</button></span>
                  <label>Âm lượng nhạc<input id="compilation-volume" type="range" min="0" max="100" value="15" /></label>
                </div>
                <p class="hint">Nhạc chỉ phát dưới lời dẫn, mở đầu và kết thúc. Mỗi clip giữ âm thanh của video đã lồng tiếng.</p>
              </section>
            </fieldset>
          </div>
          <div class="actions tab-foot">
            <button id="compilation-render" type="button" class="btn primary block">Tạo video tổng hợp</button>
            <button id="compilation-cancel" type="button" class="btn danger block" hidden>Hủy</button>
            <p id="compilation-state" class="hint center" role="status"></p>
          </div>
        </div>
        <div id="compilation-tab-result" class="tab-panel" role="tabpanel" aria-labelledby="compilation-tab-result-btn" hidden>
          <div class="tab-scroll">
            <p id="compilation-result-note" class="tab-note"></p>
            <section id="compilation-result-card" class="result-card" hidden>
              <div class="result-head">
                <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>
                <h3>Đã xong</h3>
              </div>
              <p id="compilation-result-path" class="path"></p>
              <div id="compilation-results" class="result-actions" hidden>
                <button id="compilation-open" class="btn" type="button">Mở bằng trình phát</button><button id="compilation-folder" class="btn" type="button">Mở thư mục</button><button id="compilation-copy" class="btn" type="button">Sao chép mô tả</button>
              </div>
              <button id="compilation-result-edit" class="btn block review-btn" type="button">Chỉnh sửa video này</button>
            </section>
            <section id="compilation-exports" class="edit-exports" aria-labelledby="compilation-exports-title" hidden>
              <h3 id="compilation-exports-title">Bản đã sửa</h3>
              <p id="compilation-export-progress" class="hint" hidden></p>
              <ul id="compilation-export-list" class="edit-export-list"></ul>
            </section>
          </div>
        </div>
      </aside>
    </div>`;
  const $ = <T extends HTMLElement>(name: string): T => root.querySelector<T>(`#compilation-${name}`)!;
  const field = (name: string) => $<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(name);
  const video = $<HTMLVideoElement>("video");
  let selected: ItemForm[] = [], music: string | null = null, busy = false, output = "", resultPath = "", timer = 0, polling = false;
  let loadingVoices = false;
  let runs: CompilationRun[] = [];
  /** "new" (the draft) or a run's folder. */
  let current = NEW;
  let tab: "setup" | "result" = "setup";
  let previewIndex = 0;
  let runsToken = 0;
  let exportsToken = 0;
  let playing = "";
  let playingRequest = 0;
  const durations = new Map<string, number>();
  const posters = new Map<string, string | null>();
  const wide = new Set<string>();
  const message = (error: unknown) => String(error).slice(0, 250);
  function showProvider(): void {
    $("provider").textContent = `Khi bấm AI, chủ đề và tối đa 60 dòng phụ đề tiếng Việt mỗi video được gửi tới ${PROVIDERS[session.provider()].company}. Dùng nhà cung cấp và khóa đã lưu trong Cài đặt › Tài khoản.`;
  }
  // Settings may change while this page stays visible.
  document.querySelector("#ai-settings-provider")?.addEventListener("change", showProvider);
  root.addEventListener("focusin", showProvider);
  function lock(value: boolean): void {
    busy = value;
    $<HTMLFieldSetElement>("fields").disabled = value;
    $<HTMLButtonElement>("render").disabled = value || selected.length < 2;
  }

  function button(text: string, action: () => void, label: string): HTMLButtonElement {
    const node = document.createElement("button");
    node.type = "button"; node.className = "btn"; node.textContent = text; node.setAttribute("aria-label", label);
    node.addEventListener("click", action);
    return node;
  }

  const runOf = (dir: string): CompilationRun | undefined => runs.find((run) => run.dir === dir);

  // ---------- Thiết lập: the draft ----------

  function renderSelection(): void {
    const jobs = completedVideos(settings.jobs());
    $("empty").hidden = jobs.length > 0;
    $("available").replaceChildren(...jobs.map(job => {
      const li = document.createElement("li"), label = document.createElement("label"), check = document.createElement("input");
      check.type = "checkbox"; check.checked = selected.some(item => item.id === job.id);
      check.disabled = !check.checked && selected.length >= 30;
      check.addEventListener("change", () => {
        selected = check.checked ? [...selected, newItem(job)] : selected.filter(item => item.id !== job.id);
        renderSelection(); renderEditors(); renderPreview();
      });
      const duration = job.durationMs ?? durations.get(job.id);
      // Same-named videos (several links of one channel) read apart by when they finished and how long they are.
      const name = document.createElement("span"), meta = document.createElement("small");
      name.className = "compilation-pick-name"; name.textContent = job.displayName;
      meta.className = "hint";
      const finished = Date.parse(job.finishedAt ?? job.createdAt ?? "");
      meta.textContent = [Number.isFinite(finished) ? `Xong ${shortDate(Math.floor(finished / 1000))}` : "",
        duration ? `${job.durationMs ? "" : "~"}${Math.round(duration / 1000)} giây` : ""].filter(Boolean).join(" · ");
      const text = document.createElement("span");
      text.className = "compilation-pick-text";
      text.append(name, meta);
      label.className = "compilation-pick";
      label.title = job.outputPath ?? "";
      label.append(check, text);
      li.append(label); return li;
    }));
    const render = $<HTMLButtonElement>("render");
    render.disabled = busy || selected.length < 2;
    render.title = selected.length < 2 ? "Chọn ít nhất 2 video ở mục Chọn video" : "";
    $("order").replaceChildren(...selected.map((item, index) => {
      const li = document.createElement("li"), name = document.createElement("span");
      name.textContent = `Phần ${index + 1}: ${item.title}`;
      const move = (delta: number) => { selected = moveItem(selected, index, delta); renderSelection(); renderEditors(); renderPreview(); };
      const up = button("↑", () => move(-1), `Đưa phần ${index + 1} lên`), down = button("↓", () => move(1), `Đưa phần ${index + 1} xuống`);
      up.disabled = index === 0; down.disabled = index === selected.length - 1;
      li.append(name, up, down); return li;
    }));
  }

  function renderEditors(): void {
    $("editors").replaceChildren(...selected.map((item, index) => {
      const li = document.createElement("li"), heading = document.createElement("h3"), form = document.createElement("div");
      li.className = "compilation-editor"; heading.textContent = `Phần ${index + 1}`; form.className = "product-form";
      li.addEventListener("focusin", () => { if (previewIndex !== index) { previewIndex = index; renderPreview(); } });
      const fields: Array<[keyof Omit<ItemForm, "id" | "video">, string, number, boolean]> = [
        ["title", "Tiêu đề phần", 80, false], ["lead", "Lời dẫn / nhận xét trước clip", 400, true],
        ["score", "Điểm (0–10 hoặc để trống)", 0, false], ["pros", "Ưu điểm (tối đa 3 dòng, 60 ký tự/dòng)", 0, true],
        ["cons", "Nhược điểm (tối đa 3 dòng, 60 ký tự/dòng)", 0, true], ["start", "Bắt đầu (giây, để trống = từ đầu)", 0, false],
        ["end", "Kết thúc (giây, để trống = hết clip)", 0, false],
      ];
      for (const [key, title, limit, multiline] of fields) {
        const label = document.createElement("label"), input = document.createElement(multiline ? "textarea" : "input");
        label.textContent = title;
        label.className = key === "score" || key === "start" || key === "end" ? "" : "wide";
        if (limit) input.maxLength = limit;
        input.value = item[key]; input.setAttribute("aria-label", `Phần ${index + 1}: ${title}`);
        if (input instanceof HTMLInputElement && ["score", "start", "end"].includes(key)) {
          input.type = "number"; input.min = "0"; input.step = key === "score" ? "1" : "0.001";
          if (key === "score") input.max = "10";
        }
        input.addEventListener("input", () => { item[key] = input.value; if (key === "title") renderSelection(); renderPreview(); });
        label.append(input); form.append(label);
      }
      li.append(heading, form); return li;
    }));
  }

  /** The middle column for the draft: the part being written, drawn in its layout. */
  function renderPreview(): void {
    const box = $("preview");
    if (current !== NEW) return;
    const item = selected[Math.min(previewIndex, selected.length - 1)];
    box.replaceChildren();
    box.dataset.layout = field("layout").value;
    $("meta").textContent = selected.length ? `${selected.length} phần · ${LAYOUT_NAMES[field("layout").value] ?? ""}` : "";
    if (!item) {
      const empty = document.createElement("p");
      empty.className = "compilation-preview-empty";
      empty.textContent = "Chọn từ 2 video đã hoàn tất ở thẻ Thiết lập. Phần đang soạn hiện ở đây theo bố cục đã chọn.";
      box.append(empty);
      return;
    }
    const index = selected.indexOf(item);
    // A horizontal clip fills the frame even in Bảng thông tin, with its name on top for the first seconds.
    const fullFrame = field("layout").value === "toan-man-hinh" || wide.has(item.video);
    box.dataset.layout = fullFrame ? "toan-man-hinh" : "bang-thong-tin";
    if (fullFrame && field("layout").value !== "toan-man-hinh") {
      $("meta").textContent = `${selected.length} phần · video ngang hiện toàn khung (bảng thông tin chỉ dùng cho video dọc)`;
    }
    const picture = document.createElement("div");
    picture.className = "compilation-preview-clip";
    const poster = posters.get(item.video);
    if (poster) picture.style.backgroundImage = `url("${poster}")`;
    else if (poster === undefined) requestPoster(item.video);
    const panel = document.createElement("div");
    panel.className = "compilation-preview-panel";
    const label = document.createElement("span");
    label.textContent = `Phần ${index + 1}`;
    const title = document.createElement("strong");
    title.textContent = item.title || "Chưa có tiêu đề";
    panel.append(label, title);
    if (item.score.trim() && field("layout").value === "bang-thong-tin") {
      const score = document.createElement("b");
      score.textContent = `${item.score.trim()}/10`;
      panel.append(score);
    }
    for (const [name, text] of [["Ưu", item.pros], ["Nhược", item.cons]] as const) {
      const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      if (!lines.length) continue;
      const row = document.createElement("small");
      row.textContent = `${name}: ${lines.join(" · ")}`;
      panel.append(row);
    }
    const lead = document.createElement("p");
    lead.className = "compilation-preview-lead";
    lead.textContent = item.lead.trim() || (index === 0 ? field("intro").value.trim() : "");
    box.append(picture, panel, lead);
  }

  function requestPoster(path: string): void {
    if (posters.has(path)) return;
    posters.set(path, null);
    void invoke<ArrayBuffer>("video_frame", { sourcePath: path, position: 0.15, maxWidth: 480 })
      .then((bytes) => {
        const url = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
        posters.set(path, url);
        renderPreview();
        renderRuns();
        // The engine gives only vertical clips the side panel; learn each clip's shape for the preview.
        if (typeof Image === "function") {
          const probe = new Image();
          probe.onload = () => { if (probe.naturalWidth >= probe.naturalHeight) { wide.add(path); renderPreview(); } };
          probe.src = url;
        }
      })
      .catch(() => undefined);
  }

  async function ai(): Promise<void> {
    if (busy) return;
    if (selected.length < 2 || selected.length > 30 || !field("title").value.trim()) {
      $("ai-state").textContent = "Hãy chọn 2–30 video và nhập chủ đề trước."; return;
    }
    const provider = session.provider(), key = session.read();
    showProvider();
    if (!key) { $("ai-state").textContent = "Hãy lưu khóa của nhà cung cấp trong Cài đặt › Tài khoản trước."; return; }
    lock(true); $("ai-state").textContent = `${PROVIDERS[provider].name} đang viết nội dung…`;
    try {
      const items = [];
      for (const item of selected) {
        const cues = await invoke<Array<{ text: string; end_ms: number }>>("review_cues", { outputPath: item.video });
        if (cues.length) durations.set(item.id, cues.reduce((end, cue) => Math.max(end, cue.end_ms), 0));
        items.push({ id: item.id, title: item.title, subtitles: cues.slice(0, MAX_CUES).map(cue => cue.text) });
      }
      const result = await writeEditorial(provider, key, field("title").value, items);
      field("intro").value = result.intro; field("outro").value = result.outro;
      selected = selected.map(item => ({ ...item, ...result.items.get(item.id) }));
      renderSelection(); renderEditors(); renderPreview();
      $("ai-state").textContent = "Đã điền nội dung. Hãy kiểm tra nhận xét và chỉnh sửa trước khi tạo video.";
    } catch (error) { $("ai-state").textContent = message(error); }
    finally { lock(false); }
  }

  /** Fill the draft from an earlier compilation's request. */
  function reuse(run: CompilationRun): void {
    const form = specToForm(run.spec, settings.jobs());
    if (!form) return;
    field("title").value = form.title; field("layout").value = form.layout;
    field("intro").value = form.intro; field("outro").value = form.outro;
    const voice = $<HTMLSelectElement>("voice");
    if ([...voice.options].some((option) => option.value === form.voiceId)) voice.value = form.voiceId;
    field("speed").value = String(form.speed);
    music = form.music;
    $("music-name").textContent = music ? fileName(music) : "Không có";
    $("music-clear").hidden = !music;
    field("volume").value = String(Math.round(form.musicVolume * 100));
    selected = form.items;
    previewIndex = 0;
    renderSelection();
    renderEditors();
    $("state").textContent = "Đã chép thiết lập của bản cũ. Sửa rồi bấm “Tạo video tổng hợp” để tạo bản mới.";
    select(NEW, "setup");
  }

  // ---------- The list of compilations ----------

  async function loadRuns(): Promise<void> {
    const token = ++runsToken;
    try {
      const found = parseRuns(await invoke<unknown>("compilation_runs", { roots: settings.roots?.() ?? [] }));
      if (token !== runsToken) return;
      // A run started here may not be written yet: keep it until it is found.
      const known = new Set(found.map((run) => run.dir.toLocaleLowerCase("en")));
      runs = [...runs.filter((run) => run.dir === output && !known.has(run.dir.toLocaleLowerCase("en"))), ...found];
    } catch {
      // The page still works without the list of earlier runs.
    }
    if (current !== NEW && !runOf(current)) current = NEW;
    renderRuns();
    renderSelected();
    schedule();
  }

  function renderRuns(): void {
    $("runs-empty").hidden = runs.length > 0;
    $("new").classList.toggle("selected", current === NEW);
    $("new").setAttribute("aria-pressed", String(current === NEW));
    $("runs").replaceChildren(...runs.map((run) => {
      const item = document.createElement("li");
      item.className = `job project-row${run.dir === current ? " selected" : ""}`;
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      item.setAttribute("aria-current", String(run.dir === current));
      item.title = run.dir;
      const thumb = document.createElement("span");
      thumb.className = "job-thumb";
      thumb.style.background = "oklch(0.30 0.03 250)";
      if (run.video) {
        const poster = posters.get(run.video);
        if (poster) thumb.style.background = `center / cover no-repeat url("${poster}")`;
        else if (poster === undefined) requestPoster(run.video);
      }
      const main = document.createElement("span");
      main.className = "job-main";
      const title = document.createElement("span");
      title.className = "job-title";
      title.textContent = run.title;
      const meta = document.createElement("span");
      meta.className = "job-meta";
      const state = run.status?.state;
      const dot = document.createElement("span");
      dot.className = `dot ${state === "COMPLETED" ? "ok" : state === "RUNNING" ? "run" : state === "FAILED" ? "err" : "warn"}`;
      const text = document.createElement("span");
      text.className = `msg${state === "FAILED" ? " err" : ""}`;
      const fraction = runFraction(run.status);
      text.textContent = state === "RUNNING" ? (run.status?.message ?? "Đang tạo…")
        : state === "COMPLETED" ? [`${run.parts} phần`, shortDate(run.created)].filter(Boolean).join(" · ")
          : state === "CANCELLED" ? "Đã hủy" : state === "FAILED" ? (run.status?.message ?? "Lỗi") : "Chưa rõ trạng thái";
      meta.append(dot, text);
      if (state === "RUNNING" && fraction !== null) {
        const pct = document.createElement("span");
        pct.className = "pct";
        pct.textContent = `${Math.floor(fraction * 100)}%`;
        meta.append(pct);
      }
      main.append(title, meta);
      if (state === "RUNNING") {
        const bar = document.createElement("div");
        bar.className = `bar${fraction === null ? " indeterminate" : ""}`;
        const fill = document.createElement("span");
        fill.style.width = fraction === null ? "" : `${Math.round(fraction * 100)}%`;
        bar.append(fill);
        main.append(bar);
      }
      item.append(thumb, main);
      // A running compilation is cancelled first, so it has no delete button.
      if (state !== "RUNNING") item.append(deleteProjectButton(run.title, () => void removeRun(run.dir, run.title)));
      item.addEventListener("click", () => select(run.dir));
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(run.dir); }
      });
      return item;
    }));
  }

  async function removeRun(dir: string, title: string): Promise<void> {
    const error = await confirmDeleteProject(invoke, "compilation", dir, title);
    if (error === "") return;
    const note = $("runs-state");
    note.textContent = error ?? `Đã chuyển “${title}” vào Thùng rác.`;
    note.classList.toggle("ok", error === null);
    if (error !== null) return;
    runs = runs.filter((run) => run.dir !== dir);
    if (current === dir) current = NEW;
    renderRuns();
    renderSelected();
    void loadRuns();
  }

  function select(dir: string, next?: "setup" | "result"): void {
    if (current !== dir) {
      current = dir;
      tab = next ?? (dir === NEW ? "setup" : "result");
    } else if (next) {
      tab = next;
    }
    setListOpen(false);
    renderRuns();
    renderSelected();
  }

  // ---------- The selected compilation: player, Thiết lập, Kết quả ----------

  function renderTabs(): void {
    for (const button of root.querySelectorAll<HTMLButtonElement>(".compilation-inspector [data-tab]")) {
      button.setAttribute("aria-selected", String(button.dataset.tab === tab));
    }
    $("tab-setup").hidden = tab !== "setup";
    $("tab-result").hidden = tab !== "result";
  }

  async function play(path: string | null): Promise<void> {
    if (path === playing) return;
    playing = path ?? "";
    const request = ++playingRequest;
    video.pause();
    video.removeAttribute("src");
    video.load();
    renderPlayer();
    if (!path) return;
    try {
      const url = settings.mediaUrl ? await settings.mediaUrl(path) : null;
      if (request !== playingRequest) return;
      if (url) video.src = url;
      else showPlaceholder("Bản xem thử trên trình duyệt không phát video; hãy mở ứng dụng DubFlow để xem.");
    } catch (error) {
      if (request === playingRequest) showPlaceholder(`Không mở được video để xem: ${message(error)}`);
    }
  }

  function showPlaceholder(text: string): void {
    $("placeholder-text").textContent = text;
    $("placeholder").hidden = false;
  }

  function renderPlayer(): void {
    const play = $<HTMLButtonElement>("play");
    play.disabled = !video.getAttribute("src") || video.readyState < 1;
    const on = !video.paused && !video.ended;
    play.classList.toggle("playing", on);
    play.setAttribute("aria-label", on ? "Tạm dừng" : "Phát");
    $("time").textContent = `${clock(video.currentTime)} / ${clock(video.duration)}`;
  }

  function renderSelected(): void {
    renderTabs();
    const run = current === NEW ? null : runOf(current) ?? null;
    const state = run?.status?.state;
    const done = !!run && state === "COMPLETED" && !!run.video;
    $("name").textContent = run ? run.title : "Tạo tổng hợp mới";
    $("name").title = run?.dir ?? "";
    $("edit").hidden = !done || !settings.edit;
    // The middle column: the draft's preview, or the run's video, progress or failure.
    $("preview").hidden = !!run;
    video.hidden = !done;
    $("placeholder").hidden = true;
    $("busy").hidden = state !== "RUNNING";
    $("failed").hidden = !run || !(state === "FAILED" || state === "CANCELLED" || !run.status);
    if (run && state === "RUNNING") {
      const fraction = runFraction(run.status);
      $("busy-title").textContent = fraction === null ? "Đang tạo video tổng hợp" : `Đang tạo video tổng hợp · ${Math.floor(fraction * 100)}%`;
      $("busy-bar").style.width = `${Math.round((fraction ?? 0) * 100)}%`;
      $("busy-detail").textContent = `${run.status!.message}${run.status!.total ? ` (${run.status!.done}/${run.status!.total} đoạn)` : ""}`;
      $<HTMLButtonElement>("busy-cancel").hidden = run.dir !== output;
    }
    if (run && !$("failed").hidden) {
      $("failed-title").textContent = state === "CANCELLED" ? "Đã hủy tổng hợp này" : "Không tạo được video tổng hợp";
      $("failed-detail").textContent = run.status?.message ?? "Không đọc được trạng thái của lần tạo này.";
    }
    void play(done ? run!.video : null);
    $("meta").textContent = run ? [`${run.parts} phần`, LAYOUT_NAMES[run.layout], shortDate(run.created)].filter(Boolean).join(" · ") : "";
    if (!run) renderPreview();
    // Thiết lập: the draft for "Tạo tổng hợp mới", an earlier run's request read-only.
    $("setup-past").hidden = !run;
    $("fields").hidden = !!run;
    $("render").hidden = !!run;
    if (run) renderPast(run);
    renderResult(run);
  }

  function renderPast(run: CompilationRun): void {
    const form = specToForm(run.spec);
    $("past-note").textContent = "Thiết lập của video tổng hợp này. Muốn đổi, hãy dùng lại thiết lập cho một bản mới.";
    const facts: Array<[string, string]> = form ? [
      ["Tiêu đề", form.title], ["Bố cục", LAYOUT_NAMES[form.layout] ?? form.layout], ["Tốc độ đọc", String(form.speed).replace(".", ",")],
      ["Nhạc", form.music ? fileName(form.music) : "Không có"], ["Mở đầu", form.intro || "—"], ["Kết thúc", form.outro || "—"],
    ] : [];
    $("past-facts").replaceChildren(...facts.flatMap(([term, value]) => {
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = term; dd.textContent = value;
      return [dt, dd];
    }));
    $("past-parts").replaceChildren(...(form?.items ?? []).map((item, index) => {
      const li = document.createElement("li");
      li.textContent = `Phần ${index + 1}: ${item.title}${item.score ? ` · ${item.score}/10` : ""}`;
      li.title = item.video;
      return li;
    }));
    $<HTMLButtonElement>("reuse").disabled = !form || busy;
  }

  function renderResult(run: CompilationRun | null): void {
    const done = !!run && run.status?.state === "COMPLETED" && !!run.video;
    $("result-card").hidden = !done;
    $("results").hidden = !done;
    $<HTMLButtonElement>("result-edit").hidden = !settings.edit;
    $("result-note").textContent = !run ? "Kết quả hiện ở đây khi video tổng hợp được tạo xong."
      : done ? "" : run.status?.state === "RUNNING" ? `${run.status.message} Kết quả hiện ở đây khi xong.` : run.status?.message ?? "";
    $("result-note").hidden = done;
    resultPath = done ? run!.video! : "";
    $("result-path").textContent = resultPath;
    void loadExports(done ? run!.video! : null);
  }

  async function loadExports(path: string | null): Promise<void> {
    const ticket = ++exportsToken;
    const section = $("exports");
    if (!path) { section.hidden = true; return; }
    let response: { status: { state: string; percent: number } | null; exports: ExportEntry[] } | null = null;
    try {
      response = await invoke("edit_status", { outputPath: path, kind: "compilation" });
    } catch {
      response = null;
    }
    if (ticket !== exportsToken) return;
    const exports = response?.exports ?? [];
    const running = response?.status?.state === "RUNNING";
    section.hidden = !exports.length && !running;
    $("export-progress").hidden = !running;
    $("export-progress").textContent = running ? `Đang xuất bản sửa… ${Math.floor(response!.status!.percent)}%` : "";
    $("export-list").replaceChildren(...[...exports].reverse().map((entry) => {
      const item = document.createElement("li");
      item.className = "edit-export-item";
      const text = document.createElement("span");
      text.className = "edit-export-text";
      const name = document.createElement("strong"), meta = document.createElement("small");
      name.textContent = fileName(entry.path);
      meta.textContent = [clock(entry.duration), shortDate(entry.created)].filter(Boolean).join(" · ");
      text.append(name, meta);
      text.title = entry.path;
      const actions = document.createElement("span");
      actions.className = "edit-export-actions";
      const view = button("Xem", () => { void play(entry.path); }, `Xem ${fileName(entry.path)}`);
      const reveal = button("Mở thư mục", () => void invoke("reveal_download", { path: entry.path }).catch((error) => { $("state").textContent = message(error); }),
        `Mở thư mục của ${fileName(entry.path)}`);
      actions.append(view, reveal);
      item.append(text, actions);
      return item;
    }));
  }

  // ---------- Making a compilation ----------

  /** Poll every running compilation; the list and the player follow. */
  function schedule(): void {
    const active = runs.some((run) => run.status?.state === "RUNNING");
    if (active && !timer) timer = window.setInterval(() => void poll(), POLL_MS);
    if (!active && timer) { window.clearInterval(timer); timer = 0; }
  }

  async function poll(): Promise<void> {
    if (polling) return;
    polling = true;
    try {
      for (const run of runs.filter((candidate) => candidate.status?.state === "RUNNING")) {
        let status;
        try {
          status = parseStatus(await invoke("compilation_status", { outputDir: run.dir }));
        } catch {
          if (run.dir === output) $("state").textContent = "Chưa đọc được trạng thái; đang thử lại…";
          continue;
        }
        if (!status) continue;
        run.status = status;
        if (status.state === "COMPLETED") run.video = status.path;
        if (run.dir === output) {
          $("state").textContent = `${status.message}${status.total ? ` (${status.done}/${status.total} đoạn)` : ""}`;
          if (status.state !== "RUNNING") {
            lock(false);
            $("cancel").hidden = true; $<HTMLButtonElement>("cancel").disabled = false;
            resultPath = status.path ?? "";
          }
        }
      }
    } finally {
      polling = false;
    }
    renderRuns();
    renderSelected();
    schedule();
  }

  async function render(): Promise<void> {
    if (busy) return;
    const jobs = settings.jobs();
    const ready = new Set(completedVideos(jobs).map(job => job.id));
    const known = new Set(jobs.map(job => job.id));
    // Parts from the queue must still be finished; parts reused from an earlier request are checked by the host.
    if (selected.some(item => known.has(item.id) && !ready.has(item.id))) { $("state").textContent = "Có video không còn hoàn tất trong hàng đợi. Hãy chọn lại."; return; }
    const request = buildRequest({ title: field("title").value, layout: field("layout").value, intro: field("intro").value,
      outro: field("outro").value, items: selected, voiceId: field("voice").value, speed: Number(field("speed").value),
      music, musicVolume: Number(field("volume").value) / 100, outputRoot: settings.outputRoot(), cpuOnly: settings.cpuOnly() });
    if (typeof request === "string") { $("state").textContent = request; return; }
    lock(true); $("results").hidden = true; $("state").textContent = "Đang khởi động tổng hợp…";
    try {
      output = await invoke<string>("compilation_render", { request });
      $("cancel").hidden = false;
      runs = [{ dir: output, title: request.title, layout: request.layout, created: Math.floor(Date.now() / 1000), parts: request.items.length,
        status: { state: "RUNNING", message: "Đang khởi động tổng hợp…", done: 0, total: 0, path: null }, video: null,
        spec: { schema_version: 1, title: request.title, layout: request.layout, voice_id: request.voiceId, speed: request.speed, music: request.music,
          music_volume: request.musicVolume, intro: request.intro, outro: request.outro,
          items: request.items.map((item) => ({ video: item.video, title: item.title, lead: item.lead, score: item.score, pros: item.pros, cons: item.cons,
            start_ms: item.startMs, end_ms: item.endMs })) } }, ...runs.filter((run) => run.dir !== output)];
      current = output;
      tab = "result";
      renderRuns();
      renderSelected();
      timer = timer || window.setInterval(() => void poll(), POLL_MS);
      await poll();
    } catch (error) { lock(false); $("state").textContent = message(error); }
  }

  async function cancel(dir: string): Promise<void> {
    $<HTMLButtonElement>("cancel").disabled = true;
    $<HTMLButtonElement>("busy-cancel").disabled = true;
    try { await invoke("compilation_cancel", { outputDir: dir }); $("state").textContent = "Đang hủy…"; }
    catch (error) { $<HTMLButtonElement>("cancel").disabled = false; $("state").textContent = message(error); }
    finally { $<HTMLButtonElement>("busy-cancel").disabled = false; }
  }

  async function loadVoices(): Promise<void> {
    const select = $<HTMLSelectElement>("voice");
    if (select.options.length || loadingVoices) return;
    loadingVoices = true;
    try {
      const catalog = parseVoiceCatalog(await invoke("voice_catalog"));
      select.replaceChildren(...catalog.voices.map(voice => {
        const option = document.createElement("option"); option.value = voice.voice_id;
        option.textContent = `${voice.name} · ${voice.gender} · miền ${voice.accent} · ${voice.style}`;
        option.selected = voice.voice_id === catalog.default_voice_id; return option;
      }));
    } catch (error) { $("state").textContent = message(error); }
    finally { loadingVoices = false; }
  }

  function setListOpen(open: boolean): void {
    $("screen").classList.toggle("list-open", open);
    $("list-toggle").setAttribute("aria-expanded", String(open));
  }

  function editSelected(): void {
    const run = runOf(current);
    if (run?.video && settings.edit) settings.edit({ outputPath: run.video, displayName: run.title });
  }

  $("ai").addEventListener("click", () => void ai());
  $("render").addEventListener("click", () => void render());
  $("cancel").addEventListener("click", () => void cancel(output));
  $("busy-cancel").addEventListener("click", () => void cancel(current));
  $("new").addEventListener("click", () => select(NEW, "setup"));
  $("refresh").addEventListener("click", () => void loadRuns());
  $("reuse").addEventListener("click", () => { const run = runOf(current); if (run) reuse(run); });
  $("failed-reuse").addEventListener("click", () => { const run = runOf(current); if (run) reuse(run); });
  $("edit").addEventListener("click", editSelected);
  $("result-edit").addEventListener("click", editSelected);
  $("list-toggle").addEventListener("click", () => setListOpen(!$("screen").classList.contains("list-open")));
  closeDrawerOutside($("screen"), $("col"), $("list-toggle"), () => setListOpen(false));
  field("layout").addEventListener("change", renderPreview);
  field("intro").addEventListener("input", renderPreview);
  for (const button of root.querySelectorAll<HTMLButtonElement>(".compilation-inspector [data-tab]")) {
    button.addEventListener("click", () => { tab = button.dataset.tab === "result" ? "result" : "setup"; renderTabs(); });
  }
  const togglePlay = () => {
    if ($<HTMLButtonElement>("play").disabled) return;
    if (video.paused || video.ended) void video.play().catch(() => undefined); else video.pause();
  };
  $("play").addEventListener("click", togglePlay);
  video.addEventListener("click", togglePlay);
  for (const name of ["play", "pause", "ended", "timeupdate", "durationchange", "loadedmetadata"]) video.addEventListener(name, renderPlayer);
  video.addEventListener("loadeddata", () => { $("placeholder").hidden = true; renderPlayer(); });
  video.addEventListener("error", () => { if (video.getAttribute("src")) showPlaceholder("Trình xem trong ứng dụng chưa đọc được video này. Hãy mở bằng trình phát khác."); });
  $("music").addEventListener("click", async () => {
    try {
      const [picked] = await invoke<string[]>("pick_product_files", { kind: "music" });
      if (picked) { music = picked; $("music-name").textContent = fileName(picked); $("music-clear").hidden = false; }
    } catch (error) { $("state").textContent = message(error); }
  });
  $("music-clear").addEventListener("click", () => { music = null; $("music-name").textContent = "Không có"; $("music-clear").hidden = true; });
  $("open").addEventListener("click", () => void invoke("open_path", { path: resultPath }).catch(error => { $("state").textContent = message(error); }));
  $("folder").addEventListener("click", () => void invoke("open_folder", { path: current === NEW ? output : current }).catch(error => { $("state").textContent = message(error); }));
  $("copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(await invoke<string>("compilation_description", { outputDir: current === NEW ? output : current })); $("state").textContent = "Đã sao chép mô tả và nguồn."; }
    catch (error) { $("state").textContent = message(error); }
  });
  return { show: () => {
    showProvider();
    if (!busy) { renderSelection(); renderEditors(); }
    renderSelected();
    void loadVoices();
    void loadRuns();
  } };
}
