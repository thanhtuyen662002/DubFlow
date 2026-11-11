import { readProduct, WORDS_PER_SECOND, writeScripts, type Photo } from "./ai.ts";
import { LAYOUT } from "./layout.ts";
import {
  addMedia, buildRequest, CENTER, fileName, focusKey, formatFeatures, isImage, MAX_VARIANTS, MAX_VISUALS, moveItem,
  parseBackgroundMemory, parseBackgrounds, parseRuns, parseStatus, photosToRead, readForm, runRow, TEMPLATES, toBase64,
  videoMedia, type BackgroundMemory, type Focus, type LibraryBackground, type MediaItem, type ProductStatus, type Template,
} from "./model.ts";
import type { PreviewState } from "./preview.ts";
import {
  backgroundFit, backgroundHint, backgroundItems, captionAt, createImages, mountPreviewCard, PREVIEW_SIDE, templateNote, THUMB_SIDE, wireTemplates,
} from "./look.ts";
import { KEY_CLEARED, type SessionKey } from "../seo/view.ts";
import { isKeyError, isProvider, keyHint, PROVIDER_IDS, PROVIDERS } from "../ai/provider.ts";
import { parseVoiceCatalog, voiceOptionGroups } from "../voices/model.ts";
import { confirmDialog } from "../../shell/confirm.ts";
import { confirmDeleteProject, deleteProjectButton } from "../../shell/project_delete.ts";
import { closeDrawerOutside } from "../../shell/drawer.ts";
import { mountPlayerBar } from "../media/player_bar.ts";
import { isLevel, levelText, masteringOf, needsExportConfirm, parseReview } from "../mastering/model.ts";
import { openReviewPanel } from "../mastering/review_panel.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Script = { text: string; use: boolean };
/** A finished product video the editor can open. */
export type ProductVideo = { outputPath: string; displayName: string; review?: { outputDir: string; video: number } };

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing ${selector}`);
  return node;
};

function apiError(error: unknown, name: string): string {
  const message = String(error);
  return isKeyError(message) ? `Khóa API không hợp lệ. Nhập lại khóa ${name}.` : `${name} chưa làm được: ${message.slice(0, 200)}`;
}

const SENT = "văn bản và ảnh sản phẩm bạn đưa vào";
export const MEMORY_KEY = "dubflow.product.backgrounds.v1";
const TICK_MS = 200;

export function readMemory(): BackgroundMemory {
  try {
    return parseBackgroundMemory(window.localStorage.getItem(MEMORY_KEY));
  } catch {
    return parseBackgroundMemory(null);
  }
}

export function saveMemory(memory: BackgroundMemory): void {
  try {
    window.localStorage.setItem(MEMORY_KEY, JSON.stringify(memory));
  } catch {
    // Remembering the choice is a convenience only.
  }
}

/**
 * The "Video bán hàng" page: pasted text and/or photos → product facts →
 * scripts → videos (ADR-0041). Finished videos play in the page and open in
 * the editor (Issue #426), including those of earlier runs.
 */
export function mountProductStudio(invoke: Invoke, apiKey: SessionKey, settings: {
  outputRoot(): string | null; cpuOnly(): boolean;
  /** Folders whose "DubFlow Output" may hold earlier runs. */
  roots(): string[];
  /** A URL the in-page player can stream a local video from, or null. */
  mediaUrl(path: string): Promise<string | null>;
  /** Open a finished product video in the editor. */
  edit(video: ProductVideo): void;
  /** "Chọn từ Hàng đợi": the page asks the app to open the picker. */
  fromQueue?(): void;
}): { show(): void; addClips(paths: readonly string[]): number } {
  let media: MediaItem[] = [];
  let scripts: Script[] = [];
  let music: string | null = null;
  let template: Template = "ban-tin";
  let library: LibraryBackground[] = [];
  let libraryLoaded = false;
  const memory = readMemory();
  let polling = 0;
  let running = "";
  let pendingKeyAction: (() => void) | null = null;
  const images = createImages(invoke);
  let runs: ReturnType<typeof parseRuns> = [];
  let runsToken = 0;
  /** The list's open row: "" for "Tạo video mới", else a run's folder; `playing` is the video shown. */
  let current = "";
  let playing = "";
  const posters = new Map<string, string | null>();
  const keyRow = $("#product-key-row");
  const keyInput = $<HTMLInputElement>("#product-key");
  const providerSelect = $<HTMLSelectElement>("#product-provider");
  const source = $<HTMLTextAreaElement>("#product-source");
  const field = (id: string) => $<HTMLInputElement | HTMLTextAreaElement>(id);
  const voiceSelect = $<HTMLSelectElement>("#product-voice");
  const masteringSelect = $<HTMLSelectElement>("#product-mastering");
  const showMastering = () => {
    const level = isLevel(masteringSelect.value) ? masteringSelect.value : "off";
    $("#product-mastering-hint").textContent = levelText("product", level)
      + (level !== "off" ? " Sau khi tạo, bấm “Duyệt cảnh” để xem và sửa cảnh từng câu." : "");
  };
  masteringSelect.addEventListener("change", showMastering);
  showMastering();
  const player = $<HTMLVideoElement>("#product-player");
  mountPlayerBar(player, player.parentElement as HTMLElement);
  let playStarted = performance.now();

  // Product photos need a provider that reads images.
  providerSelect.replaceChildren(...PROVIDER_IDS.filter((id) => PROVIDERS[id].vision).map((id) => {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = PROVIDERS[id].label;
    return option;
  }));
  function showProvider(): void {
    const { name, placeholder } = PROVIDERS[apiKey.provider()];
    providerSelect.value = apiKey.provider();
    keyRow.querySelector(".hint")!.textContent = keyHint(apiKey.provider(), SENT);
    keyInput.placeholder = placeholder;
    keyInput.setAttribute("aria-label", `Khóa API ${name}`);
  }
  showProvider();
  providerSelect.addEventListener("change", () => {
    if (isProvider(providerSelect.value)) apiKey.setProvider(providerSelect.value);
    showProvider();
    if (apiKey.read()) keyRow.hidden = true;
  });

  /** The image now if it is loaded, else null and a preview refresh once it is. */
  function previewImage(path: string): string | null {
    return images.now(path, PREVIEW_SIDE, () => updatePreview());
  }

  // ---- Photos and clips --------------------------------------------------

  let dragged = -1;

  function renderPhotos(): void {
    const list = $("#product-photos");
    list.replaceChildren(...media.map((item, index) => {
      const row = document.createElement("li");
      row.className = "product-thumb";
      row.classList.toggle("off", !item.video);
      row.title = item.path;
      row.draggable = true;
      row.addEventListener("dragstart", (event) => {
        dragged = index;
        event.dataTransfer?.setData("text/plain", String(index));
        row.classList.add("dragging");
      });
      row.addEventListener("dragend", () => row.classList.remove("dragging"));
      row.addEventListener("dragover", (event) => event.preventDefault());
      row.addEventListener("drop", (event) => {
        event.preventDefault();
        if (dragged >= 0 && dragged !== index) move(dragged, index);
        dragged = -1;
      });
      const image = document.createElement("img");
      image.alt = "";
      image.draggable = false;
      void images.url(item.path, THUMB_SIDE).then((url) => {
        if (url) image.src = url;
      });
      row.append(image);
      if (!isImage(item.path)) {
        const clip = document.createElement("span");
        clip.className = "product-clip";
        clip.textContent = "Clip";
        row.append(clip);
      }
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "product-remove";
      remove.setAttribute("aria-label", `Bỏ ${fileName(item.path)}`);
      remove.textContent = "×";
      remove.addEventListener("click", () => {
        media = media.filter((other) => other !== item);
        renderPhotos();
      });
      const use = document.createElement("label");
      use.className = "product-use";
      use.title = "Bỏ chọn để ảnh chỉ dùng cho AI đọc, không đưa vào video";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = item.video;
      box.addEventListener("change", () => {
        item.video = box.checked;
        renderPhotos();
      });
      use.append(box, "Video");
      const order = document.createElement("span");
      order.className = "product-order";
      for (const [label, step, text] of [["Lên trước", -1, "‹"], ["Ra sau", 1, "›"]] as const) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = text;
        button.setAttribute("aria-label", `${label}: ${fileName(item.path)}`);
        button.disabled = index + step < 0 || index + step >= media.length;
        button.addEventListener("click", () => move(index, index + step));
        order.append(button);
      }
      row.append(remove, use, order);
      return row;
    }));
    updateReadButton();
    updatePreview();
  }

  function move(from: number, to: number): void {
    media = moveItem(media, from, to);
    renderPhotos();
  }

  function updateReadButton(): void {
    const text = source.value.trim();
    const photos = photosToRead(media).length;
    $<HTMLButtonElement>("#product-read").disabled = !text && !photos;
    const words = text ? text.split(/\s+/).length : 0;
    $("#product-source-count").textContent = [
      words ? `${words} chữ` : "Chưa có văn bản",
      photos ? `${photos} ảnh cho AI đọc` : "",
    ].filter(Boolean).join(" · ");
  }

  // ---- Scripts ----------------------------------------------------------

  function renderScripts(): void {
    const list = $("#product-scripts");
    list.replaceChildren(...scripts.map((script, index) => {
      const item = document.createElement("li");
      item.className = "product-script";
      const head = document.createElement("div");
      head.className = "product-script-head";
      const use = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = script.use;
      box.addEventListener("change", () => {
        script.use = box.checked;
        updateRenderButton();
        updatePreview();
      });
      use.append(box, ` Kịch bản ${index + 1}`);
      const count = document.createElement("span");
      count.className = "hint";
      const words = () => { count.textContent = `${script.text.split(/\s+/).filter(Boolean).length} chữ`; };
      words();
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "link";
      remove.textContent = "Xóa";
      remove.addEventListener("click", () => {
        scripts.splice(index, 1);
        renderScripts();
      });
      head.append(use, count, remove);
      const text = document.createElement("textarea");
      text.rows = 5;
      text.value = script.text;
      text.setAttribute("aria-label", `Kịch bản ${index + 1}`);
      text.addEventListener("input", () => {
        script.text = text.value;
        words();
        updatePreview();
      });
      item.append(head, text);
      return item;
    }));
    $("#product-scripts-empty").hidden = scripts.length > 0;
    updateRenderButton();
    updatePreview();
  }

  function updateRenderButton(): void {
    const chosen = scripts.filter((script) => script.use && script.text.trim()).length;
    $("#product-render").textContent = chosen > 1 ? `Tạo ${chosen} video` : "Tạo video";
  }

  function info() {
    return readForm({
      name: field("#product-name").value, price: field("#product-price").value, features: field("#product-features").value,
      audience: field("#product-audience").value, notes: field("#product-notes").value,
    });
  }

  /** Run `action` now if the session has a key, else after the user enters one. */
  function withKey(action: (key: string) => Promise<void>): void {
    const key = apiKey.read();
    if (key) {
      void action(key);
      return;
    }
    pendingKeyAction = () => void action(apiKey.read());
    keyRow.hidden = false;
    keyInput.focus();
  }

  async function readSource(key: string): Promise<void> {
    const state = $("#product-read-state");
    const button = $<HTMLButtonElement>("#product-read");
    button.disabled = true;
    const provider = apiKey.provider();
    const { name, company } = PROVIDERS[provider];
    const text = source.value;
    const paths = photosToRead(media);
    const what = text.trim() && paths.length ? "văn bản và ảnh" : text.trim() ? "văn bản" : "ảnh";
    state.textContent = `${name} đang đọc ${what}…`;
    try {
      const photos: Photo[] = [];
      for (const path of paths) {
        const bytes = await invoke<ArrayBuffer>("product_image", { path });
        photos.push({ mediaType: "image/jpeg", data: toBase64(new Uint8Array(bytes)) });
      }
      const product = await readProduct(provider, key, { text, photos });
      field("#product-name").value = product.name;
      field("#product-price").value = product.price;
      field("#product-features").value = formatFeatures(product.features);
      field("#product-audience").value = product.audience;
      field("#product-notes").value = product.notes;
      state.textContent = `Đã điền thông tin (${what} đã gửi tới ${company}). Hãy kiểm tra và sửa lại cho đúng.`;
      updatePreview();
    } catch (error) {
      state.textContent = apiError(error, name);
      if (isKeyError(error)) keyRow.hidden = false;
    } finally {
      updateReadButton();
    }
  }

  async function write(key: string): Promise<void> {
    const state = $("#product-write-state");
    const product = info();
    if (!product.name && !product.features.length) {
      state.textContent = "Hãy nhập tên sản phẩm hoặc vài điểm nổi bật trước.";
      return;
    }
    const button = $<HTMLButtonElement>("#product-write");
    button.disabled = true;
    const provider = apiKey.provider();
    const { name } = PROVIDERS[provider];
    state.textContent = `${name} đang viết kịch bản…`;
    try {
      const written = await writeScripts(provider, key, product, {
        count: Number($<HTMLSelectElement>("#product-count").value),
        seconds: Number($<HTMLSelectElement>("#product-seconds").value),
        speed: Number($<HTMLSelectElement>("#product-speed").value),
        tone: $<HTMLSelectElement>("#product-tone").value,
      });
      scripts = [...scripts.filter((script) => script.text.trim()), ...written.map((text) => ({ text, use: true }))].slice(-MAX_VARIANTS * 2);
      renderScripts();
      state.textContent = `Đã viết ${written.length} kịch bản. Hãy đọc lại và sửa trước khi tạo video.`;
    } catch (error) {
      state.textContent = apiError(error, name);
      if (isKeyError(error)) keyRow.hidden = false;
    } finally {
      button.disabled = false;
    }
  }

  // ---- Template and background library ----------------------------------

  function selectedBackground(): LibraryBackground | null {
    if (template === "toan-man-hinh") return null;
    const id = memory.choice[template];
    return library.find((entry) => entry.id === id) ?? null;
  }

  function focusOf(entry: LibraryBackground): Focus {
    return memory.focus[focusKey(template, entry.id)] ?? CENTER;
  }

  const templatePicker = wireTemplates($("#product-templates"), (next) => setTemplate(next));

  function renderTemplates(): void {
    templatePicker.render(template);
    const meta = TEMPLATES.find((item) => item.id === template)!;
    $("#product-background-block").hidden = !meta.size;
    $("#product-background-size").textContent = meta.size ? `Cỡ đẹp nhất ${meta.size}` : "";
    $("#product-template-note").textContent = templateNote(template);
    $("#product-photos-hint").textContent = template === "chi-chu"
      ? "Mẫu “Chỉ chữ” không đưa ảnh hay clip vào video; ảnh vẫn giúp AI đọc thông tin."
      : "Lấy clip từ nút “Chọn từ Hàng đợi” dưới danh sách bên trái, hoặc thêm ảnh/clip từ máy. Không bắt buộc. AI đọc tối đa 8 ảnh đầu. Kéo (hoặc bấm ‹ ›) để đổi thứ tự trong video; bỏ chọn “Video” để ảnh chỉ dùng cho AI đọc.";
    renderBackgrounds();
  }

  function setTemplate(next: Template): void {
    template = next;
    playStarted = performance.now();
    renderTemplates();
  }

  function renderBackgrounds(): void {
    const chosen = selectedBackground();
    $("#product-backgrounds").replaceChildren(...backgroundItems({
      template, library, chosen, images, addId: "product-background",
      choose, remove: (entry) => void removeBackground(entry), add: () => void addBackground(),
    }));
    $("#product-background-hint").textContent = backgroundHint(template, chosen);
    updatePreview();
  }

  function choose(id: string | null): void {
    if (template === "toan-man-hinh") return;
    if (id) memory.choice[template] = id;
    else delete memory.choice[template];
    saveMemory(memory);
    renderBackgrounds();
  }

  async function loadLibrary(): Promise<void> {
    try {
      library = parseBackgrounds(await invoke<unknown>("product_backgrounds"));
      libraryLoaded = true;
    } catch (error) {
      $("#product-background-hint").textContent = `Không mở được kho ảnh nền: ${String(error).slice(0, 160)}`;
    }
    renderBackgrounds();
  }

  async function addBackground(): Promise<void> {
    const [picked] = await invoke<string[]>("pick_product_files", { kind: "background" });
    if (!picked) return;
    $("#product-background-hint").textContent = "Đang lưu ảnh nền vào kho…";
    try {
      const added = parseBackgrounds([await invoke<unknown>("product_background_add", { path: picked })])[0];
      await loadLibrary();
      if (added) choose(added.id);
    } catch (error) {
      $("#product-background-hint").textContent = String(error).slice(0, 200);
    }
  }

  async function removeBackground(entry: LibraryBackground): Promise<void> {
    const confirmed = await confirmDialog({
      title: "Xóa ảnh nền?",
      message: `“${entry.name || "Ảnh nền"}” sẽ bị xóa khỏi kho ảnh nền của DubFlow. Ảnh gốc trên máy bạn vẫn còn.`,
      confirmLabel: "Xóa ảnh nền", danger: true,
    });
    if (!confirmed) return;
    try {
      await invoke("product_background_delete", { id: entry.id });
      for (const item of TEMPLATES) {
        if (memory.choice[item.id] === entry.id) delete memory.choice[item.id];
        delete memory.focus[focusKey(item.id, entry.id)];
      }
      saveMemory(memory);
      await loadLibrary();
    } catch (error) {
      $("#product-background-hint").textContent = String(error).slice(0, 200);
    }
  }

  // ---- Live preview -----------------------------------------------------

  function selectedScript(): string {
    return scripts.find((script) => script.use && script.text.trim())?.text ?? "";
  }

  /** About how long the first chosen script reads, else the chosen length. */
  function totalMs(): number {
    const words = selectedScript().split(/\s+/).filter(Boolean).length;
    const speed = Number($<HTMLSelectElement>("#product-speed").value) || 1;
    return words ? Math.round((words / (WORDS_PER_SECOND * speed)) * 1000) + 1000 : Number($<HTMLSelectElement>("#product-seconds").value) * 1000;
  }

  function playTime(total: number): number {
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    return still ? 0 : (performance.now() - playStarted) % total;
  }

  function previewState(): PreviewState {
    const product = info();
    const total = totalMs();
    const time = playTime(total);
    const first = template === "chi-chu" ? undefined : videoMedia(media)[0];
    const chosen = selectedBackground();
    const fit = backgroundFit(template, chosen);
    return {
      template, name: product.name, price: product.price, features: product.features, caption: captionAt(selectedScript(), time, total),
      media: first ? { url: previewImage(first), label: fileName(first) } : null,
      background: chosen && fit ? { url: previewImage(chosen.path), width: chosen.width, height: chosen.height, focus: focusOf(chosen), box: fit.box } : null,
      timeMs: time, totalMs: total,
    };
  }

  const card = mountPreviewCard({
    preview: $("#product-preview"), stage: $("#product-stage"), base: $("#product-stage-base"), card: $("#product-stage-card"),
    caption: $("#product-stage-caption"), fill: $("#product-progress-fill"), time: $("#product-progress-time"),
  }, {
    visible: () => !$("#product-view").hidden && $("#product-player-wrap").hidden,
    state: previewState,
    background: () => {
      const chosen = selectedBackground();
      const fit = backgroundFit(template, chosen);
      return chosen && fit ? { entry: chosen, box: fit.box, focus: focusOf(chosen) } : null;
    },
    focus: (focus, save) => {
      const chosen = selectedBackground();
      if (!chosen) return;
      memory.focus[focusKey(template, chosen.id)] = focus;
      if (save) saveMemory(memory);
    },
  });

  function updatePreview(): void {
    card.update();
  }

  // ---- Finished videos: the in-page player and the editor ---------------

  /** Play a finished video where the preview is; "Xem trước mẫu" goes back. */
  async function play(video: ProductVideo): Promise<void> {
    playing = video.outputPath;
    $("#product-player-wrap").hidden = false;
    $("#product-preview-wrap").hidden = true;
    $("#product-player-title").textContent = video.displayName;
    $("#product-player-title").title = video.outputPath;
    $("#product-player-edit").dataset.path = video.outputPath;
    $("#product-player-edit").dataset.name = video.displayName;
    $("#product-player-note").hidden = true;
    player.removeAttribute("src");
    try {
      const url = await settings.mediaUrl(video.outputPath);
      if (url) {
        player.src = url;
        void player.play().catch(() => undefined);
      } else {
        $("#product-player-note").hidden = false;
      }
    } catch {
      $("#product-player-note").hidden = false;
    }
  }

  function closePlayer(): void {
    playing = "";
    player.pause();
    player.removeAttribute("src");
    player.load();
    $("#product-player-wrap").hidden = true;
    $("#product-preview-wrap").hidden = false;
    card.reset();
    card.scale();
    updatePreview();
  }

  function videoActions(video: ProductVideo): HTMLElement[] {
    const watch = document.createElement("button");
    watch.type = "button";
    watch.className = "btn";
    watch.textContent = "Xem";
    watch.addEventListener("click", () => void play(video));
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "btn";
    edit.textContent = "Chỉnh sửa";
    edit.title = "Mở trong trình chỉnh sửa: cắt ghép, chèn, âm thanh, chữ và đổi mẫu";
    edit.addEventListener("click", () => void openEditor(video));
    return [watch, edit];
  }

  /** The editor is where a video is exported for real: a mastered video with unreviewed sentences asks first. */
  async function openEditor(video: ProductVideo): Promise<void> {
    const found = video.review;
    if (found) {
      try {
        const view = parseReview(await invoke<unknown>("mastering_review", { outputDir: found.outputDir, video: found.video, action: "view" }));
        if (needsExportConfirm(view)) {
          const go = await confirmDialog({
            title: "Còn câu chưa duyệt cảnh",
            message: `${view!.unreviewed.length} câu có cảnh AI chưa chắc chắn và bạn chưa duyệt. Bạn có thể bấm “Duyệt cảnh” để xem trước. `
              + "Vẫn mở để chỉnh sửa và xuất video với các điểm chưa kiểm chứng?",
            confirmLabel: "Vẫn mở", cancelLabel: "Để tôi duyệt trước",
          });
          if (!go) return;
          await invoke<unknown>("mastering_review", { outputDir: found.outputDir, video: found.video, action: "accept", value: true });
        }
      } catch {
        // no review to read (an older run): the editor opens as before
      }
    }
    settings.edit(video);
  }

  /** Earlier runs found below the output folders, newest first. */
  async function loadRuns(): Promise<void> {
    const token = ++runsToken;
    try {
      const found = parseRuns(await invoke<unknown>("product_runs", { roots: settings.roots() }));
      if (token !== runsToken) return;
      runs = found;
    } catch {
      // The list is a convenience; the page works without it.
    }
    if (current && !runs.some((run) => run.dir === current) && current !== running) current = "";
    renderRuns();
  }

  function requestPoster(path: string): void {
    if (posters.has(path)) return;
    posters.set(path, null);
    void invoke<ArrayBuffer>("video_frame", { sourcePath: path, position: 0.15, maxWidth: 320 })
      .then((bytes) => {
        posters.set(path, URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" })));
        renderRuns();
      })
      .catch(() => undefined);
  }

  /** The list on the left, laid out like the queue: one row per run, its videos under the open one. */
  function renderRuns(): void {
    $("#product-runs-empty").hidden = runs.length > 0;
    const fresh = $("#product-new");
    fresh.classList.toggle("selected", !current);
    fresh.setAttribute("aria-pressed", String(!current));
    $("#product-runs").replaceChildren(...runs.map((run) => {
      const row = runRow(run);
      const open = row.dir === current;
      const item = document.createElement("li");
      item.className = `job project-row product-run${open ? " selected" : ""}`;
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      item.setAttribute("aria-current", String(open));
      item.title = row.dir;
      const thumb = document.createElement("span");
      thumb.className = "job-thumb product-run-thumb";
      thumb.style.background = "oklch(0.30 0.03 250)";
      const first = row.videos[0];
      if (first) {
        const poster = posters.get(first.outputPath);
        if (poster) thumb.style.background = `center / cover no-repeat url("${poster}")`;
        else if (poster === undefined) requestPoster(first.outputPath);
        if (first.seconds) {
          const duration = document.createElement("span");
          duration.className = "job-dur";
          duration.textContent = `0:${String(first.seconds).padStart(2, "0")}`;
          if (first.seconds >= 60) duration.textContent = `${Math.floor(first.seconds / 60)}:${String(first.seconds % 60).padStart(2, "0")}`;
          thumb.append(duration);
        }
      }
      const main = document.createElement("span");
      main.className = "job-main";
      const title = document.createElement("span");
      title.className = "job-title";
      title.textContent = row.title;
      const meta = document.createElement("span");
      meta.className = "job-meta";
      const dot = document.createElement("span");
      dot.className = `dot ${row.state === "COMPLETED" ? "ok" : row.state === "RUNNING" ? "run" : row.state === "FAILED" ? "err" : "warn"}`;
      const text = document.createElement("span");
      text.className = `msg${row.state === "FAILED" ? " err" : ""}`;
      text.textContent = row.text;
      meta.append(dot, text);
      if (row.fraction !== null) {
        const pct = document.createElement("span");
        pct.className = "pct";
        pct.textContent = `${Math.floor(row.fraction * 100)}%`;
        meta.append(pct);
      }
      main.append(title, meta);
      if (row.state === "RUNNING") {
        const bar = document.createElement("div");
        bar.className = `bar${row.fraction === null ? " indeterminate" : ""}`;
        const fill = document.createElement("span");
        fill.style.width = row.fraction === null ? "" : `${Math.max(4, Math.round(row.fraction * 100))}%`;
        bar.append(fill);
        main.append(bar);
      }
      // The open run lists its videos (one per script) so each can be watched.
      if (open && row.videos.length > 1) {
        const chips = document.createElement("span");
        chips.className = "product-run-videos";
        for (const video of row.videos) {
          const chip = document.createElement("button");
          chip.type = "button";
          chip.className = "btn chip";
          chip.textContent = `Video ${video.index}`;
          chip.setAttribute("aria-pressed", String(video.outputPath === playing));
          chip.addEventListener("click", (event) => { event.stopPropagation(); void play(video); renderRuns(); });
          chip.addEventListener("keydown", (event) => event.stopPropagation());
          chips.append(chip);
        }
        main.append(chips);
      }
      item.append(thumb, main);
      // A running run is cancelled first, so it has no delete button.
      if (row.state !== "RUNNING") item.append(deleteProjectButton(row.title, () => void removeRun(row.dir, row.title)));
      item.addEventListener("click", () => select(row.dir));
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(row.dir); }
      });
      return item;
    }));
  }

  /** Open a run: its first finished video plays where the preview is. "" goes back to a new video. */
  function select(dir: string): void {
    current = dir;
    setListOpen(false);
    const run = runs.find((item) => item.dir === dir);
    const video = run ? runRow(run).videos[0] : undefined;
    if (video) void play(video);
    else if (!$("#product-player-wrap").hidden) closePlayer();
    // An earlier run lists its videos with their review ("Duyệt cảnh") as a new one does.
    if (run?.status && dir !== running) renderResults(run.status, dir);
    else if (!dir && !running) $("#product-results").replaceChildren();
    renderRuns();
  }

  function setListOpen(open: boolean): void {
    $("#product-screen").classList.toggle("list-open", open);
    $("#product-list-toggle").setAttribute("aria-expanded", String(open));
  }

  /** Delete a whole run (every video of it). */
  async function removeRun(dir: string, name: string): Promise<void> {
    if (!await confirmDeleteProject(invoke, "product", dir, name)) return;
    runs = runs.filter((run) => run.dir !== dir);
    if (current === dir) {
      current = "";
      if (!$("#product-player-wrap").hidden) closePlayer();
    }
    renderRuns();
    void loadRuns();
  }

  // ---- Render -------------------------------------------------------------

  function renderResults(status: ProductStatus, outputDir: string): void {
    const list = $("#product-results");
    list.replaceChildren(...status.videos.map((video) => {
      const item = document.createElement("li");
      item.className = video.ok ? "ok" : "failed";
      const label = document.createElement("span");
      label.textContent = video.ok
        ? `Video ${video.index}: xong${video.duration_ms ? ` (${Math.round(video.duration_ms / 1000)} giây)` : ""}`
        : `Video ${video.index}: ${video.message ?? "lỗi"}`;
      item.append(label);
      if (video.ok && video.path) {
        const path = video.path;
        const name = `${runs.find((item) => item.dir === outputDir)?.name || info().name || "Video bán hàng"} · video ${video.index}`;
        const open = document.createElement("button");
        open.type = "button";
        open.className = "btn";
        open.textContent = "Mở";
        open.title = "Mở bằng trình phát của máy";
        open.addEventListener("click", () => void invoke("open_path", { path }).catch(() => undefined));
        const report = masteringOf(video);
        const review = report && !report.fallback ? { outputDir, video: video.index } : undefined;
        item.append(...videoActions({ outputPath: path, displayName: name, review }), open);
        if (report) {
          const note = document.createElement("span");
          note.className = report.fallback ? "product-mastering-note bad" : report.unreviewed ? "product-mastering-note warn" : "product-mastering-note";
          note.textContent = report.fallback
            ? report.fallback
            : report.unreviewed ? `${report.unreviewed} câu cần xem lại cảnh` : "Cảnh đã đạt kiểm tra tự động";
          if (review && status.state !== "RUNNING") {
            const check = document.createElement("button");
            check.type = "button";
            check.className = report.unreviewed ? "btn primary" : "btn";
            check.textContent = "Duyệt cảnh";
            check.title = "Xem cảnh AI chọn cho từng câu, lý do, cảnh thay thế; ghim, loại và render lại";
            check.addEventListener("click", () => void openReviewPanel(invoke, {
              outputDir, video: video.index, videoPath: path, title: name, mediaUrl: settings.mediaUrl, cpuOnly: settings.cpuOnly,
              rerendered: () => follow(outputDir),
            }));
            item.append(check);
          }
          item.append(note);
        }
      }
      return item;
    }));
    if (status.state !== "RUNNING") {
      const folder = document.createElement("li");
      const open = document.createElement("button");
      open.type = "button";
      open.className = "link";
      open.textContent = "Mở thư mục kết quả";
      open.addEventListener("click", () => void invoke("open_folder", { path: outputDir }).catch(() => undefined));
      folder.append(open);
      list.append(folder);
    }
  }

  async function poll(outputDir: string): Promise<void> {
    try {
      const status = parseStatus(await invoke<unknown>("product_status", { outputDir }));
      if (!status) return;
      $("#product-render-state").textContent = status.message;
      renderResults(status, outputDir);
      // The running row in the list follows the same status.
      const run = runs.find((item) => item.dir === outputDir);
      if (run) { run.status = status; renderRuns(); } else void loadRuns();
      if (status.state !== "RUNNING") finish();
    } catch {
      // The status file is being replaced; the next poll reads it.
    }
  }

  function finish(): void {
    window.clearInterval(polling);
    polling = 0;
    running = "";
    void loadRuns();
    $<HTMLButtonElement>("#product-render").disabled = false;
    const cancel = $<HTMLButtonElement>("#product-cancel");
    cancel.hidden = true;
    cancel.disabled = false;
    cancel.textContent = "Hủy";
  }

  async function cancel(): Promise<void> {
    if (!running) return;
    const button = $<HTMLButtonElement>("#product-cancel");
    button.disabled = true;
    button.textContent = "Đang hủy…";
    try {
      await invoke("product_cancel", { outputDir: running });
      $("#product-render-state").textContent = "Đang dừng; các video đã xong được giữ lại…";
    } catch (error) {
      button.disabled = false;
      button.textContent = "Hủy";
      $("#product-render-state").textContent = String(error).slice(0, 300);
    }
  }

  /** Follow a run that has just started (a new render or a re-render of one video). */
  function follow(outputDir: string): void {
    running = outputDir;
    $<HTMLButtonElement>("#product-render").disabled = true;
    $("#product-cancel").hidden = false;
    $("#product-render-state").textContent = "Đang khởi động…";
    void loadRuns();
    window.clearInterval(polling);
    polling = window.setInterval(() => void poll(outputDir), 1500);
  }

  async function render(): Promise<void> {
    const state = $("#product-render-state");
    const chosen = selectedBackground();
    const request = buildRequest({
      info: info(), voiceId: voiceSelect.value || null, scripts, visuals: videoMedia(media), music,
      background: chosen?.path ?? null, focus: chosen ? focusOf(chosen) : CENTER,
      template, speed: Number($<HTMLSelectElement>("#product-speed").value),
      volumePercent: Number($<HTMLInputElement>("#product-volume").value), cpuOnly: settings.cpuOnly(), outputRoot: settings.outputRoot(),
      mastering: masteringSelect.value, visionProvider: apiKey.provider(),
    });
    if (typeof request === "string") {
      state.textContent = request;
      return;
    }
    const button = $<HTMLButtonElement>("#product-render");
    button.disabled = true;
    $("#product-results").replaceChildren();
    state.textContent = "Đang khởi động…";
    try {
      follow(await invoke<string>("product_render", { request }));
    } catch (error) {
      state.textContent = String(error).slice(0, 300);
      button.disabled = false;
    }
  }

  async function loadVoices(): Promise<void> {
    if (voiceSelect.options.length) return;
    try {
      const catalog = parseVoiceCatalog(await invoke<unknown>("voice_catalog"));
      voiceSelect.replaceChildren(...voiceOptionGroups(catalog, catalog.default_voice_id));
    } catch (error) {
      $("#product-render-state").textContent = `Không tải được danh sách giọng: ${String(error).slice(0, 160)}`;
    }
  }

  // ---- Wiring -------------------------------------------------------------

  $("#product-pick").addEventListener("click", async () => {
    media = addMedia(media, await invoke<string[]>("pick_product_files", { kind: "media" }), MAX_VISUALS);
    renderPhotos();
  });
  source.addEventListener("input", updateReadButton);
  for (const id of ["#product-name", "#product-price", "#product-features"]) field(id).addEventListener("input", updatePreview);
  for (const id of ["#product-seconds", "#product-speed"]) $(id).addEventListener("change", updatePreview);
  $("#product-music").addEventListener("click", async () => {
    const [picked] = await invoke<string[]>("pick_product_files", { kind: "music" });
    if (!picked) return;
    music = picked;
    $("#product-music-name").textContent = fileName(picked);
    $("#product-music-clear").hidden = false;
    $("#product-volume-row").hidden = false;
  });
  $("#product-music-clear").addEventListener("click", () => {
    music = null;
    $("#product-music-name").textContent = "Không có";
    $("#product-music-clear").hidden = true;
    $("#product-volume-row").hidden = true;
  });
  $("#product-read").addEventListener("click", () => withKey(readSource));
  $("#product-write").addEventListener("click", () => withKey(write));
  $("#product-add-script").addEventListener("click", () => {
    scripts.push({ text: "", use: true });
    renderScripts();
    ($("#product-scripts").lastElementChild?.querySelector("textarea") as HTMLTextAreaElement | null)?.focus();
  });
  $("#product-render").addEventListener("click", () => void render());
  const DRAFT_FIELDS = ["#product-name", "#product-price", "#product-features", "#product-audience", "#product-notes"];
  /** Something typed or added for the next video (what "Tạo video mới" would clear). */
  function hasDraft(): boolean {
    return media.length > 0 || Boolean(source.value.trim()) || scripts.some((script) => script.text.trim())
      || DRAFT_FIELDS.some((id) => field(id).value.trim());
  }
  /** "Tạo video mới": a blank form for the next product; voice, template and music stay as they are. */
  async function startNew(): Promise<void> {
    if (current) {
      // From an earlier video: first back to the form being typed.
      select("");
      if (hasDraft()) $("#product-render-state").textContent = "Đây là nội dung đang nhập. Bấm “Thêm mới” lần nữa nếu muốn làm trống để làm sản phẩm khác.";
    } else if (hasDraft()) {
      const clear = await confirmDialog({
        title: "Thêm video bán hàng mới",
        message: "Làm trống ảnh/clip, mô tả, thông tin sản phẩm và kịch bản đang nhập để bắt đầu sản phẩm khác? Giọng đọc, mẫu video và nhạc nền giữ nguyên. Các video đã tạo không bị ảnh hưởng.",
        confirmLabel: "Làm trống, bắt đầu mới",
        cancelLabel: "Giữ nội dung đang nhập",
      });
      if (clear) {
        media = [];
        scripts = [];
        source.value = "";
        for (const id of DRAFT_FIELDS) field(id).value = "";
        $("#product-results").replaceChildren();
        $("#product-render-state").textContent = "";
        renderScripts();
        renderPhotos();
      }
    }
    select("");
    $(".product-body").scrollTop = 0;
    const first = $("#product-media-title").closest<HTMLElement>(".product-card");
    first?.classList.remove("flash");
    void first?.offsetWidth;
    first?.classList.add("flash");
  }
  $("#product-new").addEventListener("click", () => void startNew());
  $("#product-refresh").addEventListener("click", () => void loadRuns());
  $("#product-list-toggle").addEventListener("click", () => setListOpen(!$("#product-screen").classList.contains("list-open")));
  closeDrawerOutside($("#product-screen"), $("#product-list"), $("#product-list-toggle"), () => setListOpen(false));
  $("#product-player-close").addEventListener("click", () => {
    closePlayer();
    current = "";
    renderRuns();
  });
  $("#product-player-edit").addEventListener("click", () => {
    const button = $("#product-player-edit");
    if (button.dataset.path) settings.edit({ outputPath: button.dataset.path, displayName: button.dataset.name ?? fileName(button.dataset.path) });
  });
  $("#product-cancel").addEventListener("click", () => void cancel());
  $("#product-key-save").addEventListener("click", async () => {
    try {
      await apiKey.set(keyInput.value);
    } catch (error) {
      $("#product-read-state").textContent = String(error).slice(0, 200);
      return;
    }
    keyInput.value = "";
    if (!apiKey.read()) return;
    keyRow.hidden = true;
    const action = pendingKeyAction;
    pendingKeyAction = null;
    action?.();
  });
  $("#product-key-clear").addEventListener("click", async () => {
    try {
      await apiKey.clear();
      keyInput.value = "";
      $("#product-read-state").textContent = KEY_CLEARED;
    } catch (error) {
      $("#product-read-state").textContent = String(error).slice(0, 200);
    }
  });
  // The mock playback moves the cards, caption and progress bar while the page is open.
  window.setInterval(() => {
    if (!document.hidden) updatePreview();
  }, TICK_MS);
  renderPhotos();
  renderScripts();
  renderTemplates();

  $("#product-from-queue").addEventListener("click", () => settings.fromQueue?.());
  return {
    addClips: (paths) => {
      // From Hàng đợi: clips go into a new video, so the form (not an earlier run) is shown.
      const before = media.length;
      media = addMedia(media, [...paths], MAX_VISUALS);
      renderPhotos();
      if (current) select("");
      return media.length - before;
    },
    show: () => {
      showProvider();
      void loadVoices();
      if (!libraryLoaded) void loadLibrary();
      void loadRuns();
      card.scale();
      updatePreview();
    },
  };
}
