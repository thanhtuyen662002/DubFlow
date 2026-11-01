/**
 * The parts of a product video's look shared by the Bán hàng page and the
 * editor's Mẫu mode (Issue #426): photo/background thumbnails, the template
 * buttons, the background library list and the live 9:16 preview card.
 */
import { captionChunks, LAYOUT } from "./layout.ts";
import {
  backgroundBox, coverOverflow, dragFocus, isImage, TEMPLATES, type Focus, type LibraryBackground, type Template,
} from "./model.ts";
import { previewLayers, type PreviewLayers, type PreviewState } from "./preview.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export const THUMB_SIDE = 240;
export const PREVIEW_SIDE = 720;
export const CAPTION_PLACEHOLDER = "Phụ đề lời đọc hiện ở đây";

export type Images = {
  /** A JPEG of a photo (or a clip's frame) as an object URL, at most `side` px; cached. */
  url(path: string, side: number): Promise<string | null>;
  /** The image now if it is loaded, else null and `ready()` once it is. */
  now(path: string, side: number, ready: () => void): string | null;
};

export function createImages(invoke: Invoke): Images {
  const pending = new Map<string, Promise<string | null>>();
  const loaded = new Map<string, string | null>();
  function url(path: string, side: number): Promise<string | null> {
    const key = `${side}|${path}`;
    let request = pending.get(key);
    if (!request) {
      const bytes = isImage(path)
        ? invoke<ArrayBuffer>("product_image", { path, maxSide: side })
        : invoke<ArrayBuffer>("video_frame", { sourcePath: path, position: 0.1, maxWidth: Math.min(960, side) });
      request = bytes
        .then((data) => URL.createObjectURL(new Blob([data], { type: "image/jpeg" })))
        .catch(() => null)
        .then((value) => {
          loaded.set(key, value);
          return value;
        });
      pending.set(key, request);
    }
    return request;
  }
  return {
    url,
    now(path, side, ready) {
      const key = `${side}|${path}`;
      if (loaded.has(key)) return loaded.get(key) ?? null;
      void url(path, side).then(ready);
      return null;
    },
  };
}

/** The three template buttons, as the Bán hàng page shows them. */
export function templateButtonsHtml(): string {
  const hints: Record<Template, string> = { "ban-tin": "Ảnh trên, nền dưới", "toan-man-hinh": "Ảnh phủ cả khung", "chi-chu": "Không cần ảnh" };
  return TEMPLATES.map((item) => `<button type="button" role="radio" aria-checked="${item.id === "ban-tin"}" data-template="${item.id}">`
    + `<span class="product-mini" data-mini="${item.id}"><span></span><span></span></span><b>${item.label}</b><span class="hint">${hints[item.id]}</span></button>`).join("");
}

/** The `[data-template]` buttons in `group` as a radio group: click or arrow keys pick one. */
export function wireTemplates(group: HTMLElement, pick: (template: Template) => void): { render(current: Template): void } {
  const buttons = [...group.querySelectorAll<HTMLButtonElement>("[data-template]")];
  const isTemplate = (value: unknown): value is Template => TEMPLATES.some((item) => item.id === value);
  for (const button of buttons) {
    button.addEventListener("click", () => {
      if (isTemplate(button.dataset.template)) pick(button.dataset.template);
    });
    button.addEventListener("keydown", (event) => {
      const step = ({ ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 } as Record<string, number>)[event.key];
      if (!step) return;
      event.preventDefault();
      const next = buttons[(buttons.indexOf(button) + step + buttons.length) % buttons.length];
      if (isTemplate(next.dataset.template)) pick(next.dataset.template);
      next.focus();
    });
  }
  return {
    render(current) {
      for (const button of buttons) {
        button.setAttribute("aria-checked", String(button.dataset.template === current));
        button.tabIndex = button.dataset.template === current ? 0 : -1;
      }
    },
  };
}

/** What the template note under the buttons says. */
export function templateNote(template: Template): string {
  return template === "toan-man-hinh"
    ? "Ảnh hoặc clip phủ kín khung nên mẫu này không dùng ảnh nền."
    : template === "chi-chu"
      ? "Không dùng ảnh hay clip: tên, giá và từng điểm nổi bật hiện thành thẻ chữ động, kèm giọng đọc và phụ đề."
      : "Ảnh hoặc clip ở trên, nền xanh (hoặc ảnh nền của bạn) bên dưới che chữ gốc của clip.";
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

/** The background choices for `template`: the plain one, the library images and "+ Thêm nền". */
export function backgroundItems(options: {
  template: Template; library: readonly LibraryBackground[]; chosen: LibraryBackground | null; images: Images;
  choose(id: string | null): void; remove(entry: LibraryBackground): void; add(): void; addId?: string;
}): HTMLLIElement[] {
  const { template, chosen } = options;
  const plain = document.createElement("span");
  plain.className = "product-bg-swatch";
  plain.style.background = template === "chi-chu"
    ? `linear-gradient(180deg, ${LAYOUT.templates["chi-chu"].gradient.join(", ")})`
    : LAYOUT.templates["ban-tin"].panel;
  const items = [backgroundItem(template === "chi-chu" ? "Chuyển màu" : "Xanh lá", "Mặc định", !chosen, () => options.choose(null), plain)];
  for (const entry of options.library) {
    const swatch = document.createElement("span");
    swatch.className = "product-bg-swatch";
    const image = document.createElement("img");
    image.alt = "";
    void options.images.url(entry.path, THUMB_SIDE).then((url) => {
      if (url) image.src = url;
    });
    swatch.append(image);
    const item = backgroundItem(entry.name || "Ảnh nền", `${entry.width}×${entry.height}`, chosen?.id === entry.id, () => options.choose(entry.id), swatch);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "product-remove";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Xóa ảnh nền ${entry.name}`);
    remove.addEventListener("click", () => options.remove(entry));
    item.append(remove);
    items.push(item);
  }
  const add = document.createElement("li");
  const upload = document.createElement("button");
  if (options.addId) upload.id = options.addId;
  upload.type = "button";
  upload.className = "product-bg product-bg-add";
  upload.textContent = "+ Thêm nền";
  upload.title = "Tải ảnh nền từ máy vào kho của DubFlow";
  upload.addEventListener("click", () => options.add());
  add.append(upload);
  items.push(add);
  return items;
}

/** Which part of the frame a background fills for `template`, and the size that fits it exactly. */
export function backgroundFit(template: Template, entry: LibraryBackground | null): { box: "panel" | "frame"; ideal: [number, number] } | null {
  const box = entry ? backgroundBox(template, entry, LAYOUT.fullFrameRatio) : null;
  if (!box) return null;
  return { box, ideal: box === "panel" ? LAYOUT.templates["ban-tin"].background.panel : LAYOUT.templates["ban-tin"].background.frame };
}

/** The line under the background list. */
export function backgroundHint(template: Template, chosen: LibraryBackground | null): string {
  const fit = backgroundFit(template, chosen);
  if (!chosen || !fit) return "Ảnh tải lên được lưu trong kho của DubFlow để dùng lại lần sau.";
  const where = fit.box === "panel" ? "nền dưới" : "cả khung";
  return chosen.width === fit.ideal[0] && chosen.height === fit.ideal[1]
    ? `Ảnh vừa khít ${where} ${fit.ideal[0]}×${fit.ideal[1]}.`
    : `Ảnh ${chosen.width}×${chosen.height} được cắt cho vừa ${where} ${fit.ideal[0]}×${fit.ideal[1]}; kéo ảnh trong khung xem trước để chọn phần giữ lại.`;
}

/** The caption the mock playback shows at `time` of `total` ms for `script`. */
export function captionAt(script: string, time: number, total: number): string {
  const chunks = captionChunks(script);
  if (!chunks.length) return CAPTION_PLACEHOLDER;
  const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  let at = (time / total) * length;
  for (const chunk of chunks) {
    at -= chunk.length;
    if (at < 0) return chunk;
  }
  return chunks[chunks.length - 1];
}

export type PreviewParts = {
  preview: HTMLElement; stage: HTMLElement; base: HTMLElement; card: HTMLElement; caption: HTMLElement;
  fill: HTMLElement; time: HTMLElement;
};

export type PreviewCard = {
  /** Redraw what changed. */
  update(): void;
  /** Fit the 1080×1920 stage to the card's width. */
  scale(): void;
  /** Redraw everything on the next update. */
  reset(): void;
};

/**
 * The live preview: three layers drawn from `state()` (so a new card or
 * caption does not redraw the rest), a progress bar for the mock playback,
 * and dragging (or arrow keys) to move the background's kept part.
 */
export function mountPreviewCard(parts: PreviewParts, hooks: {
  visible(): boolean;
  state(): PreviewState;
  /** The background that can be moved, the part of the frame it fills and its focus now. */
  background(): { entry: LibraryBackground; box: "panel" | "frame"; focus: Focus } | null;
  /** The background's focus moved; `save` once the drag ends. */
  focus(focus: Focus, save: boolean): void;
}): PreviewCard {
  const shown: PreviewLayers = { base: "", card: "", caption: "" };
  const layers = { base: parts.base, card: parts.card, caption: parts.caption };
  const { preview } = parts;

  function update(): void {
    if (!hooks.visible()) return;
    const state = hooks.state();
    const next = previewLayers(state);
    for (const key of ["base", "card", "caption"] as const) {
      if (shown[key] !== next[key]) {
        layers[key].innerHTML = next[key];
        shown[key] = next[key];
      }
    }
    parts.fill.style.width = `${((state.timeMs / state.totalMs) * 100).toFixed(1)}%`;
    const clock = (value: number) => `${Math.floor(value / 60000)}:${String(Math.floor(value / 1000) % 60).padStart(2, "0")}`;
    parts.time.textContent = `${clock(state.timeMs)} / ${clock(state.totalMs)}`;
    preview.tabIndex = state.background ? 0 : -1;
    preview.classList.toggle("draggable", !!state.background);
  }

  function scale(): void {
    const width = preview.clientWidth;
    if (width) parts.stage.style.transform = `scale(${width / LAYOUT.width})`;
  }
  new ResizeObserver(scale).observe(preview);

  /** Move the background's kept part by (dx, dy) frame pixels from `start`. */
  function nudge(start: Focus, dx: number, dy: number, save: boolean): void {
    const chosen = hooks.background();
    if (!chosen) return;
    const [width, height] = chosen.box === "panel" ? LAYOUT.templates["ban-tin"].background.panel : LAYOUT.templates["ban-tin"].background.frame;
    const focus = dragFocus(start, coverOverflow(chosen.entry, { width, height }), dx, dy);
    hooks.focus(focus, save);
    const image = layers.base.querySelector<HTMLImageElement>(".pv-bg img");
    if (image) image.style.objectPosition = `${(focus.x * 100).toFixed(2)}% ${(focus.y * 100).toFixed(2)}%`;
    if (save) {
      shown.base = "";
      update();
    }
  }

  preview.addEventListener("pointerdown", (event) => {
    const chosen = hooks.background();
    if (!chosen || event.button !== 0) return;
    const start = chosen.focus;
    const factor = preview.clientWidth / LAYOUT.width;
    const [x, y] = [event.clientX, event.clientY];
    try {
      preview.setPointerCapture(event.pointerId);
    } catch {
      // Without capture the drag still follows the pointer inside the preview.
    }
    preview.classList.add("dragging");
    const moved = (next: PointerEvent) => nudge(start, (next.clientX - x) / factor, (next.clientY - y) / factor, false);
    const done = (next: PointerEvent) => {
      preview.removeEventListener("pointermove", moved);
      preview.removeEventListener("pointerup", done);
      preview.removeEventListener("pointercancel", done);
      preview.classList.remove("dragging");
      nudge(start, (next.clientX - x) / factor, (next.clientY - y) / factor, true);
    };
    preview.addEventListener("pointermove", moved);
    preview.addEventListener("pointerup", done);
    preview.addEventListener("pointercancel", done);
  });
  preview.addEventListener("keydown", (event) => {
    const chosen = hooks.background();
    const step = ({ ArrowLeft: [-40, 0], ArrowRight: [40, 0], ArrowUp: [0, -40], ArrowDown: [0, 40] } as Record<string, [number, number]>)[event.key];
    if (!chosen || !step) return;
    event.preventDefault();
    // The keys move the picture, like dragging it.
    nudge(chosen.focus, step[0], step[1], true);
  });

  return {
    update,
    scale,
    reset() {
      shown.base = shown.card = shown.caption = "";
    },
  };
}

/** The preview card's markup with ids starting `prefix` (stage, layers, progress). */
export function previewCardHtml(prefix: string, label: string): string {
  return `<div id="${prefix}-preview" class="product-preview" role="img" aria-label="${label}">`
    + `<div id="${prefix}-stage" class="product-stage"><div id="${prefix}-stage-base" class="pv-layer"></div>`
    + `<div id="${prefix}-stage-card" class="pv-layer"></div><div id="${prefix}-stage-caption" class="pv-layer"></div></div></div>`
    + `<div class="product-progress" aria-hidden="true"><span class="product-progress-track"><span id="${prefix}-progress-fill" class="product-progress-fill"></span></span>`
    + `<span id="${prefix}-progress-time">0:00 / 0:30</span></div>`;
}
