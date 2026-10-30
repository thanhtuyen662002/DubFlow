import { readProduct, writeScripts, type Photo } from "./ai.ts";
import {
  addPaths, buildRequest, fileName, formatFeatures, isImage, MAX_PHOTOS_TO_READ, MAX_VARIANTS, MAX_VISUALS,
  parseStatus, readForm, toBase64, type ProductStatus,
} from "./model.ts";
import { KEY_CLEARED, type SessionKey } from "../seo/view.ts";
import { isKeyError, isProvider, keyHint, PROVIDERS } from "../ai/provider.ts";
import { parseVoiceCatalog } from "../voices/model.ts";

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

const SENT = "ảnh và thông tin sản phẩm bạn chọn";

/** The "Video bán hàng" page: photos → product facts → scripts → videos (ADR-0041). */
export function mountProductStudio(invoke: Invoke, apiKey: SessionKey, settings: { outputRoot(): string | null; cpuOnly(): boolean }): { show(): void } {
  const photos: string[] = [];
  let media: string[] = [];
  let scripts: Script[] = [];
  let music: string | null = null;
  let background: string | null = null;
  let polling = 0;
  let running = "";
  let pendingKeyAction: (() => void) | null = null;
  const thumbs = new Map<string, string>();
  const keyRow = $("#product-key-row");
  const keyInput = $<HTMLInputElement>("#product-key");
  const providerSelect = $<HTMLSelectElement>("#product-provider");
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
  const field = (id: string) => $<HTMLInputElement | HTMLTextAreaElement>(id);
  const voiceSelect = $<HTMLSelectElement>("#product-voice");

  async function thumbnail(path: string, image: HTMLImageElement): Promise<void> {
    const known = thumbs.get(path);
    if (known) {
      image.src = known;
      return;
    }
    try {
      const bytes = await invoke<ArrayBuffer>("product_image", { path, maxSide: 240 });
      const url = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
      thumbs.set(path, url);
      image.src = url;
    } catch {
      image.alt = fileName(path);
    }
  }

  function renderThumbs(list: HTMLElement, paths: readonly string[], remove: (path: string) => void): void {
    list.replaceChildren(...paths.map((path) => {
      const item = document.createElement("li");
      item.className = "product-thumb";
      item.title = path;
      if (isImage(path)) {
        const image = document.createElement("img");
        image.alt = "";
        void thumbnail(path, image);
        item.append(image);
      } else {
        const clip = document.createElement("span");
        clip.className = "product-clip";
        clip.textContent = `🎬 ${fileName(path)}`;
        item.append(clip);
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "product-remove";
      button.setAttribute("aria-label", `Bỏ ${fileName(path)}`);
      button.textContent = "×";
      button.addEventListener("click", () => remove(path));
      item.append(button);
      return item;
    }));
  }

  function renderPhotos(): void {
    renderThumbs($("#product-photos"), photos, (path) => {
      photos.splice(photos.indexOf(path), 1);
      renderPhotos();
    });
    $<HTMLButtonElement>("#product-read").disabled = !photos.length;
  }

  function renderMedia(): void {
    renderThumbs($("#product-media"), media, (path) => {
      media = media.filter((item) => item !== path);
      renderMedia();
    });
  }

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
      box.addEventListener("change", () => { script.use = box.checked; });
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
      text.addEventListener("input", () => { script.text = text.value; words(); });
      item.append(head, text);
      return item;
    }));
    $("#product-scripts-empty").hidden = scripts.length > 0;
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

  async function readPhotos(key: string): Promise<void> {
    const state = $("#product-read-state");
    const button = $<HTMLButtonElement>("#product-read");
    button.disabled = true;
    const provider = apiKey.provider();
    const { name, company } = PROVIDERS[provider];
    state.textContent = `${name} đang đọc ảnh…`;
    try {
      const sent: Photo[] = [];
      for (const path of photos.slice(0, MAX_PHOTOS_TO_READ)) {
        const bytes = await invoke<ArrayBuffer>("product_image", { path });
        sent.push({ mediaType: "image/jpeg", data: toBase64(new Uint8Array(bytes)) });
      }
      const product = await readProduct(provider, key, sent);
      field("#product-name").value = product.name;
      field("#product-price").value = product.price;
      field("#product-features").value = formatFeatures(product.features);
      field("#product-audience").value = product.audience;
      field("#product-notes").value = product.notes;
      state.textContent = `Đã điền thông tin từ ảnh (ảnh đã gửi tới ${company}). Hãy kiểm tra và sửa lại cho đúng.`;
    } catch (error) {
      state.textContent = apiError(error, name);
      if (isKeyError(error)) keyRow.hidden = false;
    } finally {
      button.disabled = !photos.length;
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
    const visuals = addPaths($<HTMLInputElement>("#product-use-photos").checked ? photos : [], media, MAX_VISUALS + 1);
    const request = buildRequest({
      info: info(), voiceId: voiceSelect.value || null, scripts, visuals, music, background,
      template: $<HTMLSelectElement>("#product-template").value, speed: Number($<HTMLSelectElement>("#product-speed").value),
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

  $("#product-pick").addEventListener("click", async () => {
    const picked = await invoke<string[]>("pick_product_files", { kind: "images" });
    photos.splice(0, photos.length, ...addPaths(photos, picked, MAX_VISUALS));
    renderPhotos();
  });
  $("#product-add-media").addEventListener("click", async () => {
    media = addPaths(media, await invoke<string[]>("pick_product_files", { kind: "media" }), MAX_VISUALS);
    renderMedia();
  });
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
  // Full-screen media covers the whole frame, so a background would never show.
  $("#product-template").addEventListener("change", () => {
    const full = $<HTMLSelectElement>("#product-template").value === "toan-man-hinh";
    $("#product-background-row").hidden = full;
    $("#product-background-hint").hidden = full;
  });
  $("#product-background").addEventListener("click", async () => {
    const [picked] = await invoke<string[]>("pick_product_files", { kind: "background" });
    if (!picked) return;
    background = picked;
    $("#product-background-name").textContent = fileName(picked);
    $("#product-background-clear").hidden = false;
  });
  $("#product-background-clear").addEventListener("click", () => {
    background = null;
    $("#product-background-name").textContent = "Mặc định (xanh lá)";
    $("#product-background-clear").hidden = true;
  });
  $("#product-read").addEventListener("click", () => withKey(readPhotos));
  $("#product-write").addEventListener("click", () => withKey(write));
  $("#product-add-script").addEventListener("click", () => {
    scripts.push({ text: "", use: true });
    renderScripts();
    ($("#product-scripts").lastElementChild?.querySelector("textarea") as HTMLTextAreaElement | null)?.focus();
  });
  $("#product-render").addEventListener("click", () => void render());
  $("#product-cancel").addEventListener("click", () => void cancel());
  $("#product-key-save").addEventListener("click", () => {
    apiKey.set(keyInput.value);
    keyInput.value = "";
    if (!apiKey.read()) return;
    keyRow.hidden = true;
    const action = pendingKeyAction;
    pendingKeyAction = null;
    action?.();
  });
  $("#product-key-clear").addEventListener("click", () => {
    apiKey.clear();
    keyInput.value = "";
    $("#product-read-state").textContent = KEY_CLEARED;
  });
  renderPhotos();
  renderScripts();

  return {
    show: () => {
      showProvider();
      void loadVoices();
    },
  };
}
