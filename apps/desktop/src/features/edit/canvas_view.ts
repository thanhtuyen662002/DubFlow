/**
 * Tỉ lệ khung hình (Issue #626): the output frame's shape (16:9, 9:16, 1:1…
 * or a custom W:H) and how the picture fills it, chosen under the preview.
 * The player frame takes that shape, so everything drawn on the picture
 * (texts, Phụ đề, Che, overlays) stays where the export puts it.
 */
import {
  ASPECT_PRESETS, aspectRatio, canvasSize, parseAspect, setCanvas,
  type Canvas, type CanvasFill, type Edit,
} from "./model.ts";

export type CanvasContext = {
  /** The edit, or null when the editor is closed. */
  edit: () => Edit | null;
  commit: (next: Edit) => void;
  /** The main video's frame size. */
  source: () => { width: number; height: number };
  /** The player showing the picture now. */
  active: () => HTMLVideoElement;
};

export type CanvasView = {
  /** Shape the player and the controls after the edit changed. */
  render(): void;
  /** Draw the blurred backdrop for the current frame. */
  frame(): void;
  /** Back to the plain player (the editor closed). */
  end(): void;
  /** The popover, under `anchor` (the Tỉ lệ button by default). */
  open(anchor?: HTMLElement): void;
  close(): void;
  /** How the picture fills the frame now: "fill" crops it (the picture is larger than the frame). */
  fill(): CanvasFill | null;
};

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing editor element: ${selector}`);
  return node;
};

const FILL_HINTS: Readonly<Record<CanvasFill, string>> = {
  blur: "Giữ trọn hình; phần thừa là bản mờ của chính video.",
  black: "Giữ trọn hình; phần thừa là viền đen.",
  fill: "Phóng kín khung; phần tràn ra ngoài bị cắt, kể cả chữ nằm ở đó.",
};

/** A small rectangle of `aspect` ("" = the source's own) for buttons. */
function shapeStyle(element: HTMLElement, ratio: number, size: number): void {
  const width = ratio >= 1 ? size : Math.max(4, Math.round(size * ratio));
  const height = ratio >= 1 ? Math.max(4, Math.round(size / ratio)) : size;
  element.style.width = `${width}px`;
  element.style.height = `${height}px`;
}

export function mountCanvas(context: CanvasContext): CanvasView {
  const frame = $("#player-frame");
  const stage = frame.parentElement as HTMLElement;
  const backdrop = $<HTMLCanvasElement>("#edit-backdrop");
  const button = $<HTMLButtonElement>("#edit-ratio");
  const pop = $("#ratio-pop");
  const presets = $("#ratio-presets");
  const fillGroup = $("#ratio-fill");
  const widthInput = $<HTMLInputElement>("#ratio-w");
  const heightInput = $<HTMLInputElement>("#ratio-h");
  const error = $("#ratio-error");
  let anchor: HTMLElement = button;
  /** The fill a new shape starts with: the last one chosen. */
  let lastFill: CanvasFill = "blur";
  let drawn = { video: null as HTMLVideoElement | null, time: -1, width: 0, height: 0, aspect: "" };

  const tiles = [{ aspect: "", hint: "Giữ khung của video gốc" }, ...ASPECT_PRESETS].map(({ aspect, hint }) => {
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "ratio-tile";
    tile.setAttribute("role", "radio");
    tile.dataset.aspect = aspect;
    tile.title = hint;
    const shape = document.createElement("span");
    shape.className = "ratio-shape";
    shape.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.textContent = aspect || "Gốc";
    tile.append(shape, label);
    presets.append(tile);
    return { tile, shape, aspect };
  });

  function canvas(): Canvas | null {
    return context.edit()?.canvas ?? null;
  }

  function sourceRatio(): number {
    const { width, height } = context.source();
    return width > 0 && height > 0 ? width / height : 16 / 9;
  }

  function choose(next: Canvas | null): void {
    const edit = context.edit();
    if (!edit) return;
    if (next) lastFill = next.fill;
    error.hidden = true;
    context.commit(setCanvas(edit, next));
  }

  function layout(): void {
    const shape = context.edit() ? canvas() : null;
    frame.classList.toggle("canvas-on", !!shape);
    for (const fill of ["blur", "black", "fill"] as const) frame.classList.toggle(`canvas-${fill}`, shape?.fill === fill);
    if (!shape) {
      frame.style.flex = frame.style.width = frame.style.height = frame.style.alignSelf = "";
      return;
    }
    const style = getComputedStyle(stage);
    const room = {
      width: stage.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
      height: stage.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom),
    };
    if (room.width <= 0 || room.height <= 0) return;
    const ratio = aspectRatio(shape.aspect);
    let width = room.width, height = width / ratio;
    if (height > room.height) {
      height = room.height;
      width = height * ratio;
    }
    frame.style.flex = "none";
    frame.style.alignSelf = "center";
    frame.style.width = `${Math.floor(width)}px`;
    frame.style.height = `${Math.floor(height)}px`;
  }

  function renderControls(): void {
    const shape = canvas();
    const ratio = shape ? aspectRatio(shape.aspect) : sourceRatio();
    $("#edit-ratio-label").textContent = shape ? shape.aspect : "Gốc";
    shapeStyle($("#edit-ratio-shape"), ratio, 14);
    button.classList.toggle("on", !!shape);
    if (pop.hidden) return;
    const custom = !!shape && !ASPECT_PRESETS.some((preset) => preset.aspect === shape.aspect);
    for (const { tile, shape: icon, aspect } of tiles) {
      tile.setAttribute("aria-checked", String((shape?.aspect ?? "") === aspect));
      shapeStyle(icon, aspect ? aspectRatio(aspect) : sourceRatio(), 22);
    }
    if (custom && document.activeElement !== widthInput && document.activeElement !== heightInput) {
      [widthInput.value, heightInput.value] = shape!.aspect.split(":");
    }
    pop.classList.toggle("custom-on", custom);
    const fill = shape?.fill ?? lastFill;
    for (const option of fillGroup.querySelectorAll<HTMLButtonElement>("[data-fill]")) {
      option.setAttribute("aria-checked", String(option.dataset.fill === fill));
      option.disabled = !shape;
    }
    $("#ratio-fill-hint").textContent = shape ? FILL_HINTS[shape.fill] : "Chọn một tỉ lệ khác gốc để đặt phần thừa.";
    const { width, height } = context.source();
    const size = canvasSize(width, height, shape);
    $("#ratio-pop-size").textContent = width && height ? `Xuất ${size.width}×${size.height}` : "";
  }

  function render(): void {
    layout();
    renderControls();
    frameBackdrop(true);
  }

  /** The blurred copy behind the picture: a small cover-fitted frame, blurred and darkened by CSS. */
  function frameBackdrop(force = false): void {
    const shape = canvas();
    if (!shape || shape.fill !== "blur" || !context.edit()) return;
    const video = context.active();
    if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) return;
    const ratio = aspectRatio(shape.aspect);
    const width = ratio >= 1 ? 96 : Math.max(8, Math.round(96 * ratio));
    const height = ratio >= 1 ? Math.max(8, Math.round(96 / ratio)) : 96;
    if (!force && drawn.video === video && drawn.time === video.currentTime && drawn.width === width && drawn.height === height && drawn.aspect === shape.aspect) return;
    drawn = { video, time: video.currentTime, width, height, aspect: shape.aspect };
    if (backdrop.width !== width || backdrop.height !== height) {
      backdrop.width = width;
      backdrop.height = height;
    }
    const ctx = backdrop.getContext("2d");
    if (!ctx) return;
    const vw = video.videoWidth, vh = video.videoHeight;
    const scale = Math.max(width / vw, height / vh);
    const sw = width / scale, sh = height / scale;
    try {
      ctx.drawImage(video, (vw - sw) / 2, (vh - sh) / 2, sw, sh, 0, 0, width, height);
    } catch { /* a frame that cannot be read yet: the black frame shows */ }
  }

  function place(): void {
    const rect = anchor.getBoundingClientRect();
    const width = pop.offsetWidth, height = pop.offsetHeight;
    const left = Math.min(window.innerWidth - width - 8, Math.max(8, rect.right - width));
    const above = rect.top - height - 8;
    const top = above >= 8 ? above : Math.min(window.innerHeight - height - 8, rect.bottom + 8);
    pop.style.left = `${Math.round(left)}px`;
    pop.style.top = `${Math.round(Math.max(8, top))}px`;
  }

  function open(at: HTMLElement = button): void {
    if (!context.edit()) return;
    anchor = at;
    const shape = canvas();
    if (!shape || ASPECT_PRESETS.some((preset) => preset.aspect === shape.aspect)) widthInput.value = heightInput.value = "";
    error.hidden = true;
    pop.hidden = false;
    button.setAttribute("aria-expanded", "true");
    renderControls();
    place();
    (pop.querySelector<HTMLElement>('[aria-checked="true"]') ?? tiles[0].tile).focus();
  }

  function close(): void {
    if (pop.hidden) return;
    pop.hidden = true;
    button.setAttribute("aria-expanded", "false");
  }

  function applyCustom(): void {
    const aspect = parseAspect(`${widthInput.value.trim()}:${heightInput.value.trim()}`);
    if (!aspect) {
      error.textContent = "Nhập hai số nguyên từ 1 đến 100, tỉ lệ trong khoảng 1:4 đến 4:1 (ví dụ 5 : 4).";
      error.hidden = false;
      return;
    }
    choose({ aspect, fill: canvas()?.fill ?? lastFill });
  }

  button.addEventListener("click", () => (pop.hidden ? open() : close()));
  presets.addEventListener("click", (event) => {
    const tile = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-aspect]");
    if (!tile) return;
    const aspect = tile.dataset.aspect ?? "";
    choose(aspect ? { aspect, fill: canvas()?.fill ?? lastFill } : null);
  });
  fillGroup.addEventListener("click", (event) => {
    const option = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-fill]");
    const shape = canvas();
    if (!option || option.disabled || !shape) return;
    choose({ aspect: shape.aspect, fill: option.dataset.fill as CanvasFill });
  });
  $("#ratio-apply").addEventListener("click", applyCustom);
  for (const input of [widthInput, heightInput]) {
    input.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Enter") {
        event.preventDefault();
        applyCustom();
      } else if (event.key === "Escape") {
        close();
        button.focus();
      }
    });
    input.addEventListener("input", () => { input.value = input.value.replace(/\D/g, "").slice(0, 3); });
  }
  pop.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    close();
    button.focus();
  });
  document.addEventListener("pointerdown", (event) => {
    const target = event.target as Node;
    if (!pop.hidden && !pop.contains(target) && !button.contains(target) && !anchor.contains(target)) close();
  }, true);
  window.addEventListener("resize", () => { if (!pop.hidden) place(); });
  new ResizeObserver(() => { if (context.edit()) layout(); }).observe(stage);

  return {
    render,
    frame: () => frameBackdrop(),
    end(): void {
      close();
      frame.classList.remove("canvas-on", "canvas-blur", "canvas-black", "canvas-fill");
      frame.style.flex = frame.style.width = frame.style.height = frame.style.alignSelf = "";
      drawn = { video: null, time: -1, width: 0, height: 0, aspect: "" };
    },
    open,
    close,
    fill: () => (context.edit() ? canvas()?.fill ?? null : null),
  };
}
