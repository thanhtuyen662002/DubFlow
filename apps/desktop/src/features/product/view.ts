import { readProduct, WORDS_PER_SECOND, writeScripts, type Photo } from "./ai.ts";
import { captionChunks, LAYOUT } from "./layout.ts";
import {
  addMedia, backgroundBox, buildRequest, CENTER, coverOverflow, dragFocus, fileName, focusKey, formatFeatures, isImage, isTemplate,
  MAX_VARIANTS, MAX_VISUALS, moveItem, parseBackgroundMemory, parseBackgrounds, parseStatus, photosToRead, readForm, TEMPLATES, toBase64,
  videoMedia, type BackgroundMemory, type Focus, type LibraryBackground, type MediaItem, type ProductStatus, type Template,
} from "./model.ts";
import { previewLayers, type PreviewLayers, type PreviewState } from "./preview.ts";
import { KEY_CLEARED, type SessionKey } from "../seo/view.ts";
import { isKeyError, isProvider, keyHint, PROVIDERS } from "../ai/provider.ts";
import { parseVoiceCatalog } from "../voices/model.ts";
import { confirmDialog } from "../../shell/confirm.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Script = { text: string; use: boolean };

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
const MEMORY_KEY = "dubflow.product.backgrounds.v1";
const THUMB_SIDE = 240;
const PREVIEW_SIDE = 720;
const TICK_MS = 200;
const CAPTION_PLACEHOLDER = "Phụ đề lời đọc hiện ở đây";

function readMemory(): BackgroundMemory {
  try {
    return parseBackgroundMemory(window.localStorage.getItem(MEMORY_KEY));
  } catch {
    return parseBackgroundMemory(null);
  }
}

function saveMemory(memory: BackgroundMemory): void {
  try {
    window.localStorage.setItem(MEMORY_KEY, JSON.stringify(memory));
  } catch {
    // Remembering the choice is a convenience only.
  }
}

/** The "Video bán hàng" page: pasted text and/or photos → product facts → scripts → videos (ADR-0041). */
export function mountProductStudio(invoke: Invoke, apiKey: SessionKey, settings: { outputRoot(): string | null; cpuOnly(): boolean }): { show(): void } {
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
  const images = new Map<string, Promise<string | null>>();
  const loaded = new Map<string, string | null>();
  const keyRow = $("#product-key-row");
  const keyInput = $<HTMLInputElement>("#product-key");
  const providerSelect = $<HTMLSelectElement>("#product-provider");
  const source = $<HTMLTextAreaElement>("#product-source");
  const field = (id: string) => $<HTMLInputElement | HTMLTextAreaElement>(id);
  const voiceSelect = $<HTMLSelectElement>("#product-voice");
  const stage = $("#product-stage");
  const layers = { base: $("#product-stage-base"), card: $("#product-stage-card"), caption: $("#product-stage-caption") };
  const shown: PreviewLayers = { base: "", card: "", caption: "" };
  let playStarted = performance.now();

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

  /** A JPEG of a photo (or a clip's frame) as an object URL, at most `side` px; cached. */
  function imageUrl(path: string, side: number): Promise<string | null> {
    const key = `${side}|${path}`;
    let pending = images.get(key);
    if (!pending) {
      const request = isImage(path)
        ? invoke<ArrayBuffer>("product_image", { path, maxSide: side })
        : invoke<ArrayBuffer>("video_frame", { sourcePath: path, position: 0.1, maxWidth: Math.min(960, side) });
      pending = request
        .then((bytes) => URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" })))
        .catch(() => null)
        .then((url) => {
          loaded.set(key, url);
          return url;
        });
      images.set(key, pending);
    }
    return pending;
  }

  /** The image now if it is loaded, else null and a preview refresh once it is. */
  function previewImage(path: string): string | null {
    const key = `${PREVIEW_SIDE}|${path}`;
    if (loaded.has(key)) return loaded.get(key) ?? null;
    void imageUrl(path, PREVIEW_SIDE).then(() => updatePreview());
    return null;
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
      void imageUrl(item.path, THUMB_SIDE).then((url) => {
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

  function renderTemplates(): void {
    for (const button of document.querySelectorAll<HTMLButtonElement>("#product-templates [data-template]")) {
      button.setAttribute("aria-checked", String(button.dataset.template === template));
      button.tabIndex = button.dataset.template === template ? 0 : -1;
    }
    const meta = TEMPLATES.find((item) => item.id === template)!;
    $("#product-background-block").hidden = !meta.size;
    $("#product-background-size").textContent = meta.size ? `Cỡ đẹp nhất ${meta.size}` : "";
    $("#product-template-note").textContent = template === "toan-man-hinh"
      ? "Ảnh hoặc clip phủ kín khung nên mẫu này không dùng ảnh nền."
      : template === "chi-chu"
        ? "Không dùng ảnh hay clip: tên, giá và từng điểm nổi bật hiện thành thẻ chữ động, kèm giọng đọc và phụ đề."
        : "Ảnh hoặc clip ở trên, nền xanh (hoặc ảnh nền của bạn) bên dưới che chữ gốc của clip.";
    $("#product-photos-hint").textContent = template === "chi-chu"
      ? "Mẫu “Chỉ chữ” không đưa ảnh hay clip vào video; ảnh vẫn giúp AI đọc thông tin."
      : "Ảnh và clip không bắt buộc. AI đọc tối đa 8 ảnh đầu. Kéo (hoặc bấm ‹ ›) để đổi thứ tự trong video; bỏ chọn “Video” để ảnh chỉ dùng cho AI đọc.";
    renderBackgrounds();
  }

  function setTemplate(next: Template): void {
    template = next;
    playStarted = performance.now();
    renderTemplates();
  }

  function backgroundItem(label: string, detail: string, checked: boolean, choose: () => void, swatch: HTMLElement): HTMLLIElement {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "product-bg";
    button.setAttribute("role", "radio");
    button.setAttribute("aria-checked", String(checked));
    button.title = detail ? `${label} · ${detail}` : label;
    button.addEventListener("click", choose);
    const name = document.createElement("span");
    name.className = "product-bg-name";
    name.textContent = label;
    const size = document.createElement("span");
    size.className = "product-bg-size";
    size.textContent = detail;
    button.append(swatch, name, size);
    item.append(button);
    return item;
  }

  function renderBackgrounds(): void {
    const list = $("#product-backgrounds");
    const chosen = selectedBackground();
    const plainSwatch = document.createElement("span");
    plainSwatch.className = "product-bg-swatch";
    plainSwatch.style.background = template === "chi-chu"
      ? `linear-gradient(180deg, ${LAYOUT.templates["chi-chu"].gradient.join(", ")})`
      : LAYOUT.templates["ban-tin"].panel;
    const items = [backgroundItem(template === "chi-chu" ? "Chuyển màu" : "Xanh lá", "Mặc định", !chosen, () => choose(null), plainSwatch)];
    for (const entry of library) {
      const swatch = document.createElement("span");
      swatch.className = "product-bg-swatch";
      const image = document.createElement("img");
      image.alt = "";
      void imageUrl(entry.path, THUMB_SIDE).then((url) => {
        if (url) image.src = url;
      });
      swatch.append(image);
      const item = backgroundItem(entry.name || "Ảnh nền", `${entry.width}×${entry.height}`, chosen?.id === entry.id, () => choose(entry.id), swatch);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "product-remove";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Xóa ảnh nền ${entry.name}`);
      remove.addEventListener("click", () => void removeBackground(entry));
      item.append(remove);
      items.push(item);
    }
    const add = document.createElement("li");
    const upload = document.createElement("button");
    upload.id = "product-background";
    upload.type = "button";
    upload.className = "product-bg product-bg-add";
    upload.textContent = "+ Tải ảnh nền…";
    upload.addEventListener("click", () => void addBackground());
    add.append(upload);
    items.push(add);
    list.replaceChildren(...items);
    const box = chosen ? backgroundBox(template, chosen, LAYOUT.fullFrameRatio) : null;
    const ideal = box === "panel" ? LAYOUT.templates["ban-tin"].background.panel : LAYOUT.templates["ban-tin"].background.frame;
    $("#product-background-hint").textContent = !chosen
      ? "Ảnh tải lên được lưu trong kho của DubFlow để dùng lại lần sau."
      : chosen.width === ideal[0] && chosen.height === ideal[1]
        ? `Ảnh vừa khít ${box === "panel" ? "nền dưới" : "cả khung"} ${ideal[0]}×${ideal[1]}.`
        : `Ảnh ${chosen.width}×${chosen.height} được cắt cho vừa ${box === "panel" ? "nền dưới" : "cả khung"} ${ideal[0]}×${ideal[1]}; kéo ảnh trong khung xem trước để chọn phần giữ lại.`;
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

  function captionAt(time: number, total: number): string {
    const chunks = captionChunks(selectedScript());
    if (!chunks.length) return CAPTION_PLACEHOLDER;
    const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    let at = (time / total) * length;
    for (const chunk of chunks) {
      at -= chunk.length;
      if (at < 0) return chunk;
    }
    return chunks[chunks.length - 1];
  }

  function previewState(): PreviewState {
    const product = info();
    const total = totalMs();
    const time = playTime(total);
    const first = template === "chi-chu" ? undefined : videoMedia(media)[0];
    const chosen = selectedBackground();
    const box = chosen ? backgroundBox(template, chosen, LAYOUT.fullFrameRatio) : null;
    return {
      template, name: product.name, price: product.price, features: product.features, caption: captionAt(time, total),
      media: first ? { url: previewImage(first), label: fileName(first) } : null,
      background: chosen && box ? { url: previewImage(chosen.path), width: chosen.width, height: chosen.height, focus: focusOf(chosen), box } : null,
      timeMs: time, totalMs: total,
    };
  }

  function updatePreview(): void {
    if ($("#product-view").hidden) return;
    const state = previewState();
    const next = previewLayers(state);
    for (const key of ["base", "card", "caption"] as const) {
      if (shown[key] !== next[key]) {
        layers[key].innerHTML = next[key];
        shown[key] = next[key];
      }
    }
    $("#product-progress-fill").style.width = `${((state.timeMs / state.totalMs) * 100).toFixed(1)}%`;
    const seconds = (value: number) => `${Math.floor(value / 60000)}:${String(Math.floor(value / 1000) % 60).padStart(2, "0")}`;
    $("#product-progress-time").textContent = `${seconds(state.timeMs)} / ${seconds(state.totalMs)}`;
    $("#product-preview").tabIndex = state.background ? 0 : -1;
    $("#product-preview").classList.toggle("draggable", !!state.background);
  }

  function scaleStage(): void {
    const width = $("#product-preview").clientWidth;
    if (width) stage.style.transform = `scale(${width / LAYOUT.width})`;
  }
  new ResizeObserver(scaleStage).observe($("#product-preview"));

  /** Move the chosen background's kept part by (dx, dy) frame pixels; `save` remembers it. */
  function nudge(start: Focus, dx: number, dy: number, save: boolean): void {
    const chosen = selectedBackground();
    const box = chosen ? backgroundBox(template, chosen, LAYOUT.fullFrameRatio) : null;
    if (!chosen || !box) return;
    const [width, height] = box === "panel" ? LAYOUT.templates["ban-tin"].background.panel : LAYOUT.templates["ban-tin"].background.frame;
    const focus = dragFocus(start, coverOverflow(chosen, { width, height }), dx, dy);
    memory.focus[focusKey(template, chosen.id)] = focus;
    const image = layers.base.querySelector<HTMLImageElement>(".pv-bg img");
    if (image) image.style.objectPosition = `${(focus.x * 100).toFixed(2)}% ${(focus.y * 100).toFixed(2)}%`;
    if (save) {
      saveMemory(memory);
      shown.base = "";
      updatePreview();
    }
  }

  const preview = $("#product-preview");
  preview.addEventListener("pointerdown", (event) => {
    const chosen = selectedBackground();
    if (!chosen || event.button !== 0) return;
    const start = focusOf(chosen);
    const scale = preview.clientWidth / LAYOUT.width;
    const [x, y] = [event.clientX, event.clientY];
    try {
      preview.setPointerCapture(event.pointerId);
    } catch {
      // Without capture the drag still follows the pointer inside the preview.
    }
    preview.classList.add("dragging");
    const moved = (next: PointerEvent) => nudge(start, (next.clientX - x) / scale, (next.clientY - y) / scale, false);
    const done = (next: PointerEvent) => {
      preview.removeEventListener("pointermove", moved);
      preview.removeEventListener("pointerup", done);
      preview.removeEventListener("pointercancel", done);
      preview.classList.remove("dragging");
      nudge(start, (next.clientX - x) / scale, (next.clientY - y) / scale, true);
    };
    preview.addEventListener("pointermove", moved);
    preview.addEventListener("pointerup", done);
    preview.addEventListener("pointercancel", done);
  });
  preview.addEventListener("keydown", (event) => {
    const chosen = selectedBackground();
    const step = ({ ArrowLeft: [-40, 0], ArrowRight: [40, 0], ArrowUp: [0, -40], ArrowDown: [0, 40] } as Record<string, [number, number]>)[event.key];
    if (!chosen || !step) return;
    event.preventDefault();
    // The keys move the picture, like dragging it.
    nudge(focusOf(chosen), step[0], step[1], true);
  });

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
        const open = document.createElement("button");
        open.type = "button";
        open.className = "link";
        open.textContent = "Mở";
        open.addEventListener("click", () => void invoke("open_path", { path: video.path }).catch(() => undefined));
        item.append(open);
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
      if (status.state !== "RUNNING") finish();
    } catch {
      // The status file is being replaced; the next poll reads it.
    }
  }

  function finish(): void {
    window.clearInterval(polling);
    polling = 0;
    running = "";
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

  async function render(): Promise<void> {
    const state = $("#product-render-state");
    const chosen = selectedBackground();
    const request = buildRequest({
      info: info(), voiceId: voiceSelect.value || null, scripts, visuals: videoMedia(media), music,
      background: chosen?.path ?? null, focus: chosen ? focusOf(chosen) : CENTER,
      template, speed: Number($<HTMLSelectElement>("#product-speed").value),
      volumePercent: Number($<HTMLInputElement>("#product-volume").value), cpuOnly: settings.cpuOnly(), outputRoot: settings.outputRoot(),
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
      const outputDir = await invoke<string>("product_render", { request });
      running = outputDir;
      $("#product-cancel").hidden = false;
      window.clearInterval(polling);
      polling = window.setInterval(() => void poll(outputDir), 1500);
    } catch (error) {
      state.textContent = String(error).slice(0, 300);
      button.disabled = false;
    }
  }

  async function loadVoices(): Promise<void> {
    if (voiceSelect.options.length) return;
    try {
      const catalog = parseVoiceCatalog(await invoke<unknown>("voice_catalog"));
      voiceSelect.replaceChildren(...catalog.voices.map((voice) => {
        const option = document.createElement("option");
        option.value = voice.voice_id;
        option.textContent = `${voice.name} · ${voice.gender} · ${voice.accent} · ${voice.style}`;
        option.selected = voice.voice_id === catalog.default_voice_id;
        return option;
      }));
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
  const templateButtons = [...document.querySelectorAll<HTMLButtonElement>("#product-templates [data-template]")];
  for (const button of templateButtons) {
    button.addEventListener("click", () => {
      if (isTemplate(button.dataset.template)) setTemplate(button.dataset.template);
    });
    button.addEventListener("keydown", (event) => {
      const step = ({ ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 } as Record<string, number>)[event.key];
      if (!step) return;
      event.preventDefault();
      const next = templateButtons[(templateButtons.indexOf(button) + step + templateButtons.length) % templateButtons.length];
      if (isTemplate(next.dataset.template)) setTemplate(next.dataset.template);
      next.focus();
    });
  }
  $("#product-music").addEventListener("click", async () => {
    const [picked] = await invoke<string[]>("pick_product_files", { kind: "music" });
    if (!picked) return;
    music = picked;
    $("#product-music-name").textContent = fileName(picked);
    $("#product-music-clear").hidden = false;
  });
  $("#product-music-clear").addEventListener("click", () => {
    music = null;
    $("#product-music-name").textContent = "Không có";
    $("#product-music-clear").hidden = true;
  });
  $("#product-read").addEventListener("click", () => withKey(readSource));
  $("#product-write").addEventListener("click", () => withKey(write));
  $("#product-add-script").addEventListener("click", () => {
    scripts.push({ text: "", use: true });
    renderScripts();
    ($("#product-scripts").lastElementChild?.querySelector("textarea") as HTMLTextAreaElement | null)?.focus();
  });
  $("#product-render").addEventListener("click", () => void render());
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

  return {
    show: () => {
      showProvider();
      void loadVoices();
      if (!libraryLoaded) void loadLibrary();
      scaleStage();
      updatePreview();
    },
  };
}
