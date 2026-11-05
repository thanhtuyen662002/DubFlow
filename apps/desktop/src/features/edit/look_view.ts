/**
 * The segment panel's Khung hình and Màu tabs and the look parts of Cơ bản
 * (Issue #518): rotation, flips, crop, slow zoom, colour, reverse, held frame
 * and sound fades of the selected segment, and the CSS that previews a look
 * on the player.
 */
import {
  COLOR_PRESETS, colorPresetOf, lutName, CROP_RATIOS, cropForRatio, cropRatioOf, cssFilter, DEFAULT_FREEZE, MAX_REVERSE, NEUTRAL_COLOR, temperatureTint, zoomAt,
  type ColorLook, type Crop, type SegmentLook,
} from "./look.ts";
import { freezeFrame, lookOf, mediaOf, segmentLength, setLook, type Edit, type Segment } from "./model.ts";

export type LookContext = {
  edit: () => Edit;
  base: () => Edit;
  busy: () => boolean;
  commit: (next: Edit, selected?: string) => void;
  preview: (next: Edit) => void;
  /** The segment the panel shows. */
  selected: () => Segment | undefined;
  outputTime: () => number;
  note: (text: string) => void;
  /** LUT màu: the user's .cube file, or null when they cancel. */
  pickLut: () => Promise<string | null>;
  /** The selected segment's frame at the playhead through `lut`, as an image URL. */
  lutFrame: (segment: Segment, lut: string) => Promise<string>;
};

export type LookPanel = {
  render(segment: Segment | undefined): void;
  /** Show a look on a player: the picture rect is the output frame inside the player box. */
  apply(player: HTMLVideoElement, segment: Segment | null, progress: number, picture: Rect, source: { width: number; height: number }): void;
  clear(player: HTMLVideoElement): void;
};

type Rect = { left: number; top: number; width: number; height: number };
type Tab = "basic" | "frame" | "color";

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing editor element: ${selector}`);
  return node;
};

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));
const percent = (value: number): string => `${Math.round(value * 100)}%`;
const seconds = (value: number): string => `${String(Math.round(value * 10) / 10).replace(".", ",")} giây`;
const signed = (value: number): string => (value > 0 ? `+${value}` : String(value));

const FOCUS: ReadonlyArray<[number, number, string]> = [
  [0, 0, "Trên trái"], [0.5, 0, "Trên giữa"], [1, 0, "Trên phải"],
  [0, 0.5, "Giữa trái"], [0.5, 0.5, "Giữa"], [1, 0.5, "Giữa phải"],
  [0, 1, "Dưới trái"], [0.5, 1, "Dưới giữa"], [1, 1, "Dưới phải"],
];

export function mountLook(context: LookContext): LookPanel {
  const tabs = [...document.querySelectorAll<HTMLButtonElement>("#seg-tabs [data-tab]")];
  const panes: Record<Tab, HTMLElement> = { basic: $("#seg-basic"), frame: $("#seg-frame"), color: $("#seg-color") };
  const freeze = $<HTMLInputElement>("#look-freeze");
  const fadeIn = $<HTMLInputElement>("#look-fadein");
  const fadeOut = $<HTMLInputElement>("#look-fadeout");
  const reverse = $<HTMLButtonElement>("#look-reverse");
  const flipH = $<HTMLButtonElement>("#look-flip-h");
  const flipV = $<HTMLButtonElement>("#look-flip-v");
  const ratios = $("#look-ratios");
  const cropInputs = {
    left: $<HTMLInputElement>("#look-crop-left"), right: $<HTMLInputElement>("#look-crop-right"),
    top: $<HTMLInputElement>("#look-crop-top"), bottom: $<HTMLInputElement>("#look-crop-bottom"),
  };
  const zoomFrom = $<HTMLInputElement>("#look-zoom-from");
  const zoomTo = $<HTMLInputElement>("#look-zoom-to");
  const zoomPresets = [...document.querySelectorAll<HTMLButtonElement>("#look-zoom-presets [data-zoom]")];
  const focus = $("#look-focus");
  const presets = $("#look-presets");
  const colorInputs = {
    brightness: $<HTMLInputElement>("#look-brightness"), contrast: $<HTMLInputElement>("#look-contrast"),
    saturation: $<HTMLInputElement>("#look-saturation"), temperature: $<HTMLInputElement>("#look-temperature"),
  };
  let tab: Tab = "basic";
  // Xem thử LUT: the last frame asked for, so renders do not ask again.
  let lutShown = "";
  let lutUrl = "";

  for (const ratio of CROP_RATIOS) {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "radio");
    button.dataset.ratio = ratio.id;
    button.textContent = ratio.name;
    ratios.append(button);
  }
  for (const [x, y, name] of FOCUS) {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "radio");
    button.dataset.x = String(x);
    button.dataset.y = String(y);
    button.title = name;
    button.setAttribute("aria-label", name);
    focus.append(button);
  }
  for (const preset of COLOR_PRESETS) {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "radio");
    button.dataset.preset = preset.id;
    const swatch = document.createElement("span");
    swatch.className = "look-swatch";
    swatch.style.filter = cssFilter(preset.color) || "none";
    const tint = temperatureTint(preset.color);
    swatch.style.setProperty("--tint", tint.color);
    swatch.style.setProperty("--tint-opacity", String(tint.opacity));
    const label = document.createElement("span");
    label.textContent = preset.name;
    button.append(swatch, label);
    presets.append(button);
  }

  const segmentNow = (edit: Edit): Segment | undefined => {
    const selected = context.selected();
    return selected && edit.segments.find((segment) => segment.id === selected.id);
  };

  /** Change the selected segment's look: `draft` previews (a slider moving), otherwise it is committed. */
  function change(partial: Partial<SegmentLook>, draft = false): boolean {
    if (context.busy() && !draft) return false;
    const segment = segmentNow(context.base());
    if (!segment) return false;
    const next = setLook(context.base(), segment.id, partial);
    if (next === context.base()) {
      if (draft) context.preview(next);
      return false;
    }
    if (draft) context.preview(next);
    else context.commit(next, segment.id);
    return true;
  }

  function pictureSize(edit: Edit, segment: Segment): { width: number; height: number } {
    const media = mediaOf(edit, segment.mediaId);
    return { width: media?.width || 16, height: media?.height || 9 };
  }

  // ---------- Tabs ----------
  for (const button of tabs) {
    button.addEventListener("click", () => {
      tab = button.dataset.tab as Tab;
      render(context.selected());
    });
  }

  // ---------- Cơ bản ----------
  freeze.addEventListener("input", () => { change({ freeze: Number(freeze.value) }, true); });
  freeze.addEventListener("change", () => { change({ freeze: Number(freeze.value) }); });
  for (const [input, key] of [[fadeIn, "fadeIn"], [fadeOut, "fadeOut"]] as const) {
    input.addEventListener("input", () => { change({ [key]: Number(input.value) }, true); });
    input.addEventListener("change", () => { change({ [key]: Number(input.value) }); });
  }
  reverse.addEventListener("click", () => {
    const segment = segmentNow(context.base());
    if (!segment) return;
    const on = !lookOf(segment).reverse;
    if (!change({ reverse: on }) && on) context.note(`Tua ngược chỉ dùng cho đoạn dài tối đa ${MAX_REVERSE / 60} phút; hãy tách đoạn ngắn hơn.`);
  });
  $("#look-freeze-add").addEventListener("click", () => {
    if (context.busy()) return;
    const base = context.base();
    const next = freezeFrame(base, context.outputTime(), DEFAULT_FREEZE);
    if (next === base) {
      context.note("Không thêm được dừng hình ở đây (đã đạt tối đa 500 đoạn).");
      return;
    }
    const added = next.segments.find((segment) => !base.segments.some((other) => other.id === segment.id));
    context.commit(next, added?.id);
    context.note("Đã thêm 2 giây dừng hình; chỉnh độ dài ở mục Giữ hình.");
  });

  // ---------- Khung hình ----------
  $("#look-rotate-left").addEventListener("click", () => {
    const segment = segmentNow(context.base());
    if (segment) change({ rotate: ((lookOf(segment).rotate + 270) % 360) as SegmentLook["rotate"] });
  });
  $("#look-rotate-right").addEventListener("click", () => {
    const segment = segmentNow(context.base());
    if (segment) change({ rotate: ((lookOf(segment).rotate + 90) % 360) as SegmentLook["rotate"] });
  });
  flipH.addEventListener("click", () => {
    const segment = segmentNow(context.base());
    if (segment) change({ flipH: !lookOf(segment).flipH });
  });
  flipV.addEventListener("click", () => {
    const segment = segmentNow(context.base());
    if (segment) change({ flipV: !lookOf(segment).flipV });
  });
  ratios.addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-ratio]");
    const segment = segmentNow(context.base());
    if (!button || !segment) return;
    const ratio = CROP_RATIOS.find((item) => item.id === button.dataset.ratio);
    if (!ratio?.ratio) {
      change({ crop: null });
      return;
    }
    const size = pictureSize(context.base(), segment);
    // A sideways rotation turns the picture first: crop for the shape it ends up as.
    const sideways = lookOf(segment).rotate % 180 !== 0;
    change({ crop: cropForRatio(size.width, size.height, sideways ? 1 / ratio.ratio : ratio.ratio) });
  });
  const cropFromInputs = (): Crop => {
    const left = Number(cropInputs.left.value) / 100, right = Number(cropInputs.right.value) / 100;
    const top = Number(cropInputs.top.value) / 100, bottom = Number(cropInputs.bottom.value) / 100;
    const w = clamp(1 - left - right, 0.1, 1), h = clamp(1 - top - bottom, 0.1, 1);
    return { x: clamp(left, 0, 1 - w), y: clamp(top, 0, 1 - h), w, h };
  };
  for (const input of Object.values(cropInputs)) {
    input.addEventListener("input", () => { change({ crop: cropFromInputs() }, true); });
    input.addEventListener("change", () => { change({ crop: cropFromInputs() }); });
  }
  const zoomValue = (): SegmentLook["zoom"] => {
    const segment = segmentNow(context.base());
    const current = segment ? lookOf(segment).zoom : null;
    return { from: Number(zoomFrom.value) / 100, to: Number(zoomTo.value) / 100, x: current?.x ?? 0.5, y: current?.y ?? 0.5 };
  };
  for (const input of [zoomFrom, zoomTo]) {
    input.addEventListener("input", () => { change({ zoom: zoomValue() }, true); });
    input.addEventListener("change", () => { change({ zoom: zoomValue() }); });
  }
  for (const button of zoomPresets) {
    button.addEventListener("click", () => {
      const segment = segmentNow(context.base());
      if (!segment) return;
      const at = lookOf(segment).zoom;
      const kind = button.dataset.zoom;
      change({ zoom: kind === "none" ? null : { from: kind === "in" ? 1 : 1.3, to: kind === "in" ? 1.3 : 1, x: at?.x ?? 0.5, y: at?.y ?? 0.5 } });
    });
  }
  focus.addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-x]");
    const segment = segmentNow(context.base());
    if (!button || !segment) return;
    const zoom = lookOf(segment).zoom ?? { from: 1, to: 1.3, x: 0.5, y: 0.5 };
    change({ zoom: { ...zoom, x: Number(button.dataset.x), y: Number(button.dataset.y) } });
  });
  $("#look-frame-reset").addEventListener("click", () => { change({ crop: null, rotate: 0, flipH: false, flipV: false, zoom: null }); });

  // ---------- Màu ----------
  presets.addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-preset]");
    const preset = COLOR_PRESETS.find((item) => item.id === button?.dataset.preset);
    if (preset) change({ color: preset.color });
  });
  const colorFromInputs = (): ColorLook => ({
    brightness: Number(colorInputs.brightness.value) / 100, contrast: Number(colorInputs.contrast.value) / 100,
    saturation: Number(colorInputs.saturation.value) / 100, temperature: Number(colorInputs.temperature.value) / 100,
  });
  for (const input of Object.values(colorInputs)) {
    input.addEventListener("input", () => { change({ color: colorFromInputs() }, true); });
    input.addEventListener("change", () => { change({ color: colorFromInputs() }); });
  }
  $("#look-color-reset").addEventListener("click", () => { change({ color: null }); });
  $("#look-lut-pick").addEventListener("click", () => {
    if (context.busy()) return;
    void context.pickLut().then((path) => {
      if (!path) return;
      if (!/\.cube$/i.test(path)) {
        context.note("LUT phải là file .cube.");
        return;
      }
      change({ lut: path });
    }).catch((error: unknown) => context.note(error instanceof Error ? error.message : "Không mở được hộp chọn file."));
  });
  $("#look-lut-clear").addEventListener("click", () => { change({ lut: null }); });
  $("#look-color-all").addEventListener("click", () => {
    if (context.busy()) return;
    const segment = segmentNow(context.base());
    if (!segment) return;
    const { color, lut } = lookOf(segment);
    let next = context.base();
    for (const other of next.segments) next = setLook(next, other.id, { color, lut: lut ?? null });
    if (next === context.base()) {
      context.note("Mọi đoạn đã dùng màu này.");
      return;
    }
    context.commit(next, segment.id);
    context.note(`Đã dùng màu này cho cả ${next.segments.length} đoạn.`);
  });

  // ---------- Rendering ----------
  function setValue(input: HTMLInputElement, value: number): void {
    if (document.activeElement !== input) input.value = String(value);
  }

  function render(segment: Segment | undefined): void {
    for (const button of tabs) button.setAttribute("aria-selected", String(button.dataset.tab === tab));
    for (const [key, pane] of Object.entries(panes)) pane.hidden = key !== tab;
    if (!segment) return;
    const look = lookOf(segment);
    const held = look.freeze !== null;
    // Cơ bản.
    $("#seg-times").hidden = held;
    $("#seg-speed-field").hidden = held;
    $("#seg-volume-field").hidden = held;
    $("#seg-fades").hidden = held;
    $("#seg-freeze-field").hidden = !held;
    reverse.hidden = held;
    setValue(freeze, look.freeze ?? DEFAULT_FREEZE);
    $("#look-freeze-value").textContent = seconds(look.freeze ?? DEFAULT_FREEZE);
    const room = Math.min(3, segmentLength(segment));
    fadeIn.max = fadeOut.max = String(Math.round(room * 10) / 10);
    setValue(fadeIn, look.fadeIn);
    setValue(fadeOut, look.fadeOut);
    $("#look-fadein-value").textContent = look.fadeIn ? seconds(look.fadeIn) : "Tắt";
    $("#look-fadeout-value").textContent = look.fadeOut ? seconds(look.fadeOut) : "Tắt";
    reverse.setAttribute("aria-pressed", String(look.reverse));
    $("#look-reverse-note").hidden = !look.reverse;
    // Khung hình.
    flipH.setAttribute("aria-pressed", String(look.flipH));
    flipV.setAttribute("aria-pressed", String(look.flipV));
    const size = pictureSize(context.edit(), segment);
    const sideways = look.rotate % 180 !== 0;
    // After a sideways turn the output's width is the crop's height.
    const ratio = sideways && look.crop
      ? cropRatioOf({ ...look.crop, w: look.crop.h, h: look.crop.w }, size.height, size.width)
      : cropRatioOf(look.crop, size.width, size.height);
    for (const button of ratios.querySelectorAll<HTMLButtonElement>("[data-ratio]")) {
      button.setAttribute("aria-checked", String(look.crop ? button.dataset.ratio === ratio && ratio !== "free" : button.dataset.ratio === "free"));
    }
    const crop = look.crop ?? { x: 0, y: 0, w: 1, h: 1 };
    setValue(cropInputs.left, Math.round(crop.x * 100));
    setValue(cropInputs.right, Math.round((1 - crop.x - crop.w) * 100));
    setValue(cropInputs.top, Math.round(crop.y * 100));
    setValue(cropInputs.bottom, Math.round((1 - crop.y - crop.h) * 100));
    const parts = [look.rotate ? `xoay ${look.rotate}°` : "", look.flipH ? "lật ngang" : "", look.flipV ? "lật dọc" : ""].filter(Boolean);
    $("#look-crop-value").textContent = look.crop ? `Giữ ${percent(crop.w)} × ${percent(crop.h)}` : "Không cắt";
    $("#seg-tabs [data-tab='frame']").classList.toggle("dot", !!(look.crop || look.zoom || parts.length));
    const zoom = look.zoom;
    setValue(zoomFrom, Math.round((zoom?.from ?? 1) * 100));
    setValue(zoomTo, Math.round((zoom?.to ?? 1) * 100));
    const zoomKind = !zoom ? "none" : zoom.to > zoom.from ? "in" : zoom.to < zoom.from ? "out" : "";
    for (const button of zoomPresets) button.setAttribute("aria-checked", String(button.dataset.zoom === zoomKind));
    $("#look-zoom-value").textContent = zoom ? `${percent(zoom.from)} → ${percent(zoom.to)}` : "Không";
    for (const button of focus.querySelectorAll<HTMLButtonElement>("[data-x]")) {
      button.setAttribute("aria-checked", String(Number(button.dataset.x) === (zoom?.x ?? 0.5) && Number(button.dataset.y) === (zoom?.y ?? 0.5)));
      button.disabled = !zoom;
    }
    // Màu.
    const color = look.color ?? NEUTRAL_COLOR;
    const preset = colorPresetOf(look.color);
    for (const button of presets.querySelectorAll<HTMLButtonElement>("[data-preset]")) button.setAttribute("aria-checked", String(button.dataset.preset === preset));
    setValue(colorInputs.brightness, Math.round(color.brightness * 100));
    setValue(colorInputs.contrast, Math.round(color.contrast * 100));
    setValue(colorInputs.saturation, Math.round(color.saturation * 100));
    setValue(colorInputs.temperature, Math.round(color.temperature * 100));
    $("#look-brightness-value").textContent = signed(Math.round(color.brightness * 100));
    $("#look-contrast-value").textContent = signed(Math.round((color.contrast - 1) * 100));
    $("#look-saturation-value").textContent = signed(Math.round((color.saturation - 1) * 100));
    $("#look-temperature-value").textContent = color.temperature > 0 ? `Ấm ${Math.round(color.temperature * 100)}` : color.temperature < 0 ? `Lạnh ${Math.round(-color.temperature * 100)}` : "0";
    $("#seg-tabs [data-tab='color']").classList.toggle("dot", !!look.color || !!look.lut);
    const lut = look.lut ?? null;
    $("#look-lut-value").textContent = lut ? lutName(lut) : "Không";
    $("#look-lut-value").title = lut ?? "";
    $("#look-lut-clear").hidden = !lut;
    $("#look-lut-preview").hidden = !lut;
    if (lut && tab === "color") showLut(segment, lut);
  }

  /** The frame at the playhead through the LUT, asked for again when the segment, file or time changes. */
  function showLut(segment: Segment, lut: string, again = false): void {
    const key = `${segment.id}|${segment.start}|${lut}|${Math.round(context.outputTime() * 2)}`;
    if (key === lutShown && !again) return;
    lutShown = key;
    const caption = $("#look-lut-caption");
    caption.textContent = "Đang áp LUT lên khung tại đầu phát…";
    void context.lutFrame(segment, lut).then((url) => {
      if (lutShown !== key) return;
      if (lutUrl) URL.revokeObjectURL(lutUrl);
      lutUrl = url;
      $<HTMLImageElement>("#look-lut-img").src = url;
      caption.textContent = "Khung tại đầu phát khi áp LUT · khung xem trước lớn không hiện LUT, video xuất ra có LUT.";
    }).catch((error: unknown) => {
      if (lutShown !== key) return;
      caption.textContent = error instanceof Error ? error.message : String(error || "Không xem thử được LUT này.");
    });
  }

  function clear(player: HTMLVideoElement): void {
    player.style.transform = "";
    player.style.transformOrigin = "";
    player.style.clipPath = "";
    player.style.filter = "";
  }

  function apply(player: HTMLVideoElement, segment: Segment | null, progress: number, picture: Rect, source: { width: number; height: number }): void {
    const tint = document.querySelector<HTMLElement>("#edit-tint");
    const look = segment ? lookOf(segment) : null;
    if (!look || (!look.crop && !look.rotate && !look.flipH && !look.flipV && !look.zoom && !look.color)) {
      clear(player);
      if (tint) tint.style.opacity = "0";
      return;
    }
    // The player shows its own picture letterboxed (object-fit: contain) in the same box as the output frame.
    const box = { width: player.clientWidth || picture.width, height: player.clientHeight || picture.height };
    const scale = Math.min(box.width / source.width, box.height / source.height);
    const shown = { left: (box.width - source.width * scale) / 2, top: (box.height - source.height * scale) / 2, width: source.width * scale, height: source.height * scale };
    const crop = look.crop ?? { x: 0, y: 0, w: 1, h: 1 };
    const cut = { left: shown.left + crop.x * shown.width, top: shown.top + crop.y * shown.height, width: crop.w * shown.width, height: crop.h * shown.height };
    const sideways = look.rotate % 180 !== 0;
    const turned = { width: sideways ? cut.height : cut.width, height: sideways ? cut.width : cut.height };
    const fit = Math.min(picture.width / turned.width, picture.height / turned.height);
    const origin = { x: cut.left + cut.width / 2, y: cut.top + cut.height / 2 };
    const centre = { x: picture.left + picture.width / 2, y: picture.top + picture.height / 2 };
    const zoom = zoomAt(look.zoom, progress);
    const focal = { x: picture.left + (look.zoom?.x ?? 0.5) * picture.width, y: picture.top + (look.zoom?.y ?? 0.5) * picture.height };
    const parts: string[] = [];
    if (zoom !== 1) parts.push(`translate(${focal.x - origin.x}px, ${focal.y - origin.y}px) scale(${zoom}) translate(${origin.x - focal.x}px, ${origin.y - focal.y}px)`);
    parts.push(`translate(${centre.x - origin.x}px, ${centre.y - origin.y}px)`);
    if (look.rotate) parts.push(`rotate(${look.rotate}deg)`);
    parts.push(`scale(${fit * (look.flipH ? -1 : 1)}, ${fit * (look.flipV ? -1 : 1)})`);
    player.style.transformOrigin = `${origin.x}px ${origin.y}px`;
    player.style.transform = parts.join(" ");
    player.style.clipPath = look.crop
      ? `inset(${cut.top}px ${box.width - cut.left - cut.width}px ${box.height - cut.top - cut.height}px ${cut.left}px)` : "";
    player.style.filter = cssFilter(look.color);
    if (tint) {
      const { color, opacity } = temperatureTint(look.color);
      tint.style.background = color;
      tint.style.opacity = String(opacity);
    }
  }

  return { render, apply, clear };
}
