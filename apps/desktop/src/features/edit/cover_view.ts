/**
 * The Che mode of the editor (Issue #418): the cover style, the detected
 * caption regions grouped per line (each can be switched off), regions drawn
 * on the preview for one stretch of time or the whole video (moved and
 * resized with handles), "Còn sót chữ?", and the live preview of the style,
 * drawn on a canvas from the source picture the way the engine renders it.
 * "Áp dụng" is shared with Phụ đề (subtitles_view.ts).
 */
import type { QueueJob } from "../queue/model.ts";
import { dragRegion, type DragHandle } from "../blur/region.ts";
import { formatClock, MAIN, outputToSource, parseClock, type Edit } from "./model.ts";
import { cueOutputTime } from "./subtitles.ts";
import type { CoverHooks } from "./subtitles_view.ts";
import {
  activeRegions, COVER_MODES, COVER_SWATCHES, coverDocument, coverSummary, even, groupAuto, LEGACY_COVER_STYLE, loadCover, MAX_MANUAL,
  mosaicCells, nextMoment, normalizeCoverStyle, rectBetween, regionOn, sameCover, stretchDraws, uncoveredMoments,
  type AutoGroup, type AutoRegion, type CoverDirection, type CoverMode as Mode, type CoverState, type CoverStyle,
  type ManualRegion, type Rect, type Region,
} from "./cover.ts";

type Box = { left: number; top: number; width: number; height: number };
type Selection = { kind: "auto"; key: string } | { kind: "manual"; id: string } | { kind: "fixed" } | null;

export type CoverContext = {
  video: HTMLVideoElement;
  /** The picture inside the player frame. */
  picture: () => Box;
  edit: () => Edit;
  outputTime: () => number;
  seek: (time: number) => void;
  job: () => QueueJob | null;
  /** The cover changed: redraw the footer and the preview. */
  changed: () => void;
};

export type CoverEditor = CoverHooks & {
  /** A new editing session. */
  begin(): void;
  end(): void;
  enter(): void;
  leave(): void;
  readonly active: boolean;
  /** A key while Che is open; true when it was used. */
  keydown(event: KeyboardEvent): boolean;
};

/** The engine's Gaussian blur, in frame pixels. */
const BLUR_SIGMA = 40;
const HANDLES: DragHandle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing cover element: ${selector}`);
  return node;
};

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

const percent = (value: number): string => `${Math.round(value * 100)}%`;
const span = (start: number, end: number): string => `${formatClock(start / 1000)} – ${formatClock(end / 1000)}`;

function cloneState(state: CoverState): CoverState {
  return { style: { ...state.style }, manual: state.manual.map((region) => ({ ...region })), fixed: state.fixed ? { ...state.fixed } : null,
    disabled: new Set(state.disabled) };
}

/** Where each caption line sits, for the list: "dòng dưới", or "dòng trên" and "dòng dưới" when the lines share an area. */
function lineNames(groups: readonly AutoGroup[]): Map<string, string> {
  const zone = (rect: Rect): string => {
    const middle = rect.y + rect.h / 2;
    return middle > 0.66 ? "dưới" : middle < 0.33 ? "trên" : "giữa";
  };
  const names = new Map<string, string>();
  const byZone = new Map<string, AutoGroup[]>();
  for (const group of groups) byZone.set(zone(group.rect), [...(byZone.get(zone(group.rect)) ?? []), group]);
  for (const [area, members] of byZone) {
    const ordered = [...members].sort((a, b) => a.rect.y + a.rect.h / 2 - (b.rect.y + b.rect.h / 2));
    ordered.forEach((group, index) => {
      const suffix = ordered.length === 1 ? "" : index === 0 ? ", dòng trên" : index === ordered.length - 1 ? ", dòng dưới" : `, dòng ${index + 1}`;
      names.set(group.key, `phần ${area}${suffix}`);
    });
  }
  return names;
}

export function mountCover(context: CoverContext): CoverEditor {
  const { video } = context;
  const canvas = $<HTMLCanvasElement>("#cover-canvas");
  const boxes = $("#cover-boxes");
  const drawSurface = $("#cover-draw");
  const hint = $("#cover-hint");
  const section = $("#edit-cover");
  const modeButtons = [...document.querySelectorAll<HTMLButtonElement>("#cover-modes [data-cover-mode]")];
  const directionButtons = [...document.querySelectorAll<HTMLButtonElement>("#cover-direction [data-direction]")];
  const strip = $<HTMLInputElement>("#cover-strip");
  const block = $<HTMLInputElement>("#cover-block");
  const feather = $<HTMLInputElement>("#cover-feather");
  const list = $<HTMLUListElement>("#cover-list");
  const selectedPanel = $("#cover-selected");
  const spanButtons = [...document.querySelectorAll<HTMLButtonElement>("#cover-span [data-span]")];
  const startInput = $<HTMLInputElement>("#cover-start");
  const endInput = $<HTMLInputElement>("#cover-end");
  const addButton = $<HTMLButtonElement>("#cover-add");
  const missedButton = $<HTMLButtonElement>("#cover-missed");
  const fixButton = $<HTMLButtonElement>("#cover-fix");
  const deleteButton = $<HTMLButtonElement>("#cover-delete");
  const scratch = document.createElement("canvas");
  const patch = document.createElement("canvas");

  let active = false;
  let base: CoverState | null = null;
  let state: CoverState | null = null;
  let detected: AutoRegion[] = [];
  let groups: AutoGroup[] = [];
  let names = new Map<string, string>();
  let selection: Selection = null;
  let nextId = 1;
  let drawing = false;
  /** The cover is on screen (a main-video moment with the preview layer shown). */
  let shown = false;
  type Drag = { pointer: number; x: number; y: number; handle: DragHandle; start: Rect; target: "manual" | "fixed"; id?: string; moved: boolean };
  let drag: Drag | null = null;
  let sketch: { pointer: number; from: { x: number; y: number }; rect: Rect | null } | null = null;

  const jobRects = (): Rect[] => context.job()?.blurRegions ?? [];
  const makeId = (): string => `m${nextId++}`;
  const style = (): CoverStyle => state?.style ?? LEGACY_COVER_STYLE;
  const manualOf = (id: string): ManualRegion | undefined => state?.manual.find((region) => region.id === id);
  let byIndex = new Map<number, AutoRegion>();
  /** What the region outlines last showed, so playback does not rebuild them every frame. */
  let boxesKey = "";

  // ---------- Preview drawing ----------

  function mainSeconds(): number | null {
    const point = outputToSource(context.edit(), context.outputTime());
    return point && point.segment.mediaId === MAIN ? point.sourceTime : null;
  }

  /** Draw the chosen style over every region on at `seconds` of the main video. */
  function draw(seconds: number | null): void {
    shown = seconds !== null && !!state;
    const rect = context.picture();
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(rect.width * ratio)), height = Math.max(1, Math.round(rect.height * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, width, height);
    if (active) renderBoxes(seconds);
    if (seconds === null || !state || video.readyState < 2 || !video.videoWidth || !video.videoHeight) return;
    const ms = seconds * 1000;
    const vw = video.videoWidth, vh = video.videoHeight;
    const kx = width / vw, ky = height / vh;
    const look = state.style;
    const regions = activeRegions(state, detected, jobRects()).filter((region) => regionOn(region, ms));
    // The engine covers top to bottom (bottom to top for strips from below).
    regions.sort((a, b) => look.mode === "stretch" && look.direction === "bottom" ? b.y + b.h - (a.y + a.h) : a.y - b.y);
    for (const region of regions) paintRegion(ctx, region, look, vw, vh, kx, ky);
  }

  function paintRegion(ctx: CanvasRenderingContext2D, region: Rect, look: CoverStyle, vw: number, vh: number, kx: number, ky: number): void {
    const x = even(vw, region.x), y = even(vh, region.y), w = Math.max(2, even(vw, region.w)), h = Math.max(2, even(vh, region.h));
    const dx = x * kx, dy = y * ky, dw = w * kx, dh = h * ky;
    if (look.mode === "color") {
      ctx.fillStyle = look.color;
      ctx.fillRect(dx, dy, dw, dh);
      return;
    }
    if (look.mode === "blur") {
      const pad = Math.min(BLUR_SIGMA * 2, vw, vh);
      const sx = Math.max(0, x - pad), sy = Math.max(0, y - pad);
      const sw = Math.min(vw, x + w + pad) - sx, sh = Math.min(vh, y + h + pad) - sy;
      ctx.save();
      ctx.beginPath();
      ctx.rect(dx, dy, dw, dh);
      ctx.clip();
      ctx.filter = `blur(${Math.max(1, BLUR_SIGMA * kx).toFixed(1)}px)`;
      ctx.drawImage(video, sx, sy, sw, sh, sx * kx, sy * ky, sw * kx, sh * ky);
      ctx.restore();
      return;
    }
    // Stretch and mosaic are drawn on a patch first, so feathering can fade its edges.
    patch.width = Math.max(1, Math.round(dw));
    patch.height = Math.max(1, Math.round(dh));
    const pctx = patch.getContext("2d");
    if (!pctx) return;
    pctx.clearRect(0, 0, patch.width, patch.height);
    if (look.mode === "stretch") {
      pctx.imageSmoothingEnabled = true;
      for (const part of stretchDraws(region, look, vw, vh)) {
        pctx.drawImage(video, part.sx, part.sy, part.sw, part.sh, (part.dx - x) * kx, (part.dy - y) * ky, part.dw * kx, part.dh * ky);
      }
    } else {
      const cells = mosaicCells(region, look.block, vw, vh);
      scratch.width = cells.columns;
      scratch.height = cells.rows;
      const sctx = scratch.getContext("2d");
      if (!sctx) return;
      sctx.imageSmoothingEnabled = true;
      sctx.imageSmoothingQuality = "high";
      sctx.drawImage(video, x, y, w, h, 0, 0, cells.columns, cells.rows);
      pctx.imageSmoothingEnabled = false;
      pctx.drawImage(scratch, 0, 0, cells.columns, cells.rows, 0, 0, patch.width, patch.height);
    }
    if (look.feather > 0) {
      const inset = look.feather * vh / 1080 * ky;
      pctx.save();
      pctx.globalCompositeOperation = "destination-in";
      pctx.filter = `blur(${Math.max(0.5, look.feather / 2 * ky).toFixed(1)}px)`;
      pctx.fillStyle = "#fff";
      pctx.fillRect(inset, inset, Math.max(1, patch.width - inset * 2), Math.max(1, patch.height - inset * 2));
      pctx.restore();
    }
    ctx.drawImage(patch, dx, dy);
  }

  for (const name of ["seeked", "loadeddata"]) video.addEventListener(name, () => { if (shown) context.changed(); });

  // ---------- Regions on the preview ----------

  function boxNode(rect: Rect, className: string, label: string): HTMLDivElement {
    const node = element("div", `cover-box ${className}`);
    Object.assign(node.style, { left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%` });
    // Near the top edge the label goes under the box, where the picture still shows it.
    if (label) node.append(element("span", `cover-box-label${rect.y < 0.08 ? " below" : ""}`, label));
    return node;
  }

  function editable(node: HTMLDivElement, target: "manual" | "fixed", id?: string): void {
    node.classList.add("editable");
    node.dataset.handle = "move";
    node.title = "Kéo để di chuyển, kéo các góc để đổi cỡ";
    for (const handle of HANDLES) {
      const knob = element("span", `cover-handle ${handle}`);
      knob.dataset.handle = handle;
      node.append(knob);
    }
    node.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !state) return;
      event.preventDefault();
      event.stopPropagation();
      const rect = target === "fixed" ? state.fixed : manualOf(id ?? "");
      if (!rect) return;
      try { node.setPointerCapture(event.pointerId); } catch { /* A pointer that already ended. */ }
      const handle = ((event.target as HTMLElement).dataset.handle ?? "move") as DragHandle;
      drag = { pointer: event.pointerId, x: event.clientX, y: event.clientY, handle, start: { x: rect.x, y: rect.y, w: rect.w, h: rect.h }, target, id, moved: false };
    });
    node.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.pointer || !state) return;
      const layer = boxes.getBoundingClientRect();
      const next = dragRegion(drag.start, drag.handle, (event.clientX - drag.x) / Math.max(1, layer.width), (event.clientY - drag.y) / Math.max(1, layer.height));
      drag.moved = true;
      if (drag.target === "fixed") state.fixed = next;
      else {
        const region = manualOf(drag.id ?? "");
        if (region) Object.assign(region, next);
      }
      Object.assign(node.style, { left: `${next.x * 100}%`, top: `${next.y * 100}%`, width: `${next.w * 100}%`, height: `${next.h * 100}%` });
      context.changed();
    });
    const end = (event: PointerEvent): void => {
      if (!drag || event.pointerId !== drag.pointer) return;
      const moved = drag.moved;
      drag = null;
      if (moved) changed();
    };
    node.addEventListener("pointerup", end);
    node.addEventListener("pointercancel", end);
    node.addEventListener("click", (event) => event.stopPropagation());
  }

  /** Outlines of the regions on now, the selected one with its handles. */
  function renderBoxes(seconds: number | null): void {
    boxes.hidden = !active || !state;
    if (!active || !state || drag) {
      if (!drag) {
        boxes.replaceChildren();
        boxesKey = "";
      }
      return;
    }
    const ms = seconds === null ? null : seconds * 1000;
    const on = (region: Region): boolean => ms !== null && regionOn(region, ms);
    const visible = groups.filter((group) => (selection?.kind === "auto" && selection.key === group.key)
      || (ms !== null && group.indices.some((index) => { const region = byIndex.get(index); return !!region && on(region); })));
    const key = JSON.stringify([selection, visible.map((group) => group.key), [...state.disabled].length, state.fixed,
      state.manual.map((region) => [region, on(region)]), sketch?.rect ?? null]);
    if (key === boxesKey) return;
    boxesKey = key;
    const nodes: HTMLElement[] = [];
    for (const group of visible) {
      const off = state.disabled.has(group.indices[0]);
      const chosen = selection?.kind === "auto" && selection.key === group.key;
      const node = boxNode(group.rect, `auto${chosen ? " selected" : ""}${off ? " off" : ""}`, chosen ? (off ? "Tự phát hiện · đang tắt" : "Tự phát hiện") : "");
      node.addEventListener("pointerdown", (event) => { event.stopPropagation(); select({ kind: "auto", key: group.key }, false); });
      node.addEventListener("click", (event) => event.stopPropagation());
      nodes.push(node);
    }
    if (state.fixed) {
      const chosen = selection?.kind === "fixed";
      const node = boxNode(state.fixed, `fixed${chosen ? " selected" : ""}`, chosen ? "Che cố định cả video" : "");
      if (chosen) editable(node, "fixed");
      else node.addEventListener("pointerdown", (event) => { event.stopPropagation(); select({ kind: "fixed" }, false); });
      nodes.push(node);
    }
    state.manual.forEach((region, index) => {
      const chosen = selection?.kind === "manual" && selection.id === region.id;
      if (!chosen && !on(region)) return;
      const node = boxNode(region, `manual${chosen ? " selected" : ""}${on(region) ? "" : " idle"}`, chosen ? `Vùng che ${index + 1}` : "");
      if (chosen) editable(node, "manual", region.id);
      else node.addEventListener("pointerdown", (event) => { event.stopPropagation(); select({ kind: "manual", id: region.id }, false); });
      nodes.push(node);
    });
    if (sketch?.rect) nodes.push(boxNode(sketch.rect, "manual selected sketch", ""));
    boxes.replaceChildren(...nodes);
  }

  /** Redraw the outlines on the next frame even when nothing they show changed. */
  function dirtyBoxes(): void {
    boxesKey = "";
  }

  // Drawing a new region.
  function setDrawing(on: boolean): void {
    drawing = on && active;
    drawSurface.hidden = !drawing;
    sketch = null;
    addButton.setAttribute("aria-pressed", String(drawing));
    addButton.textContent = drawing ? "Hủy vẽ vùng" : "Thêm vùng che";
    showHint(drawing ? "Kéo trên video để vẽ vùng che · Esc để hủy" : null);
    context.changed();
  }

  function showHint(text: string | null): void {
    hint.hidden = !text;
    hint.textContent = text ?? "";
  }

  const pointAt = (event: PointerEvent): { x: number; y: number } => {
    const layer = drawSurface.getBoundingClientRect();
    return { x: (event.clientX - layer.left) / Math.max(1, layer.width), y: (event.clientY - layer.top) / Math.max(1, layer.height) };
  };
  drawSurface.addEventListener("pointerdown", (event) => {
    if (!drawing || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    try { drawSurface.setPointerCapture(event.pointerId); } catch { /* A pointer that already ended. */ }
    sketch = { pointer: event.pointerId, from: pointAt(event), rect: null };
  });
  drawSurface.addEventListener("pointermove", (event) => {
    if (!sketch || event.pointerId !== sketch.pointer) return;
    sketch.rect = rectBetween(sketch.from, pointAt(event));
    context.changed();
  });
  drawSurface.addEventListener("pointerup", (event) => {
    if (!sketch || event.pointerId !== sketch.pointer || !state) return;
    const to = pointAt(event);
    const moved = Math.abs(to.x - sketch.from.x) >= 0.01 && Math.abs(to.y - sketch.from.y) >= 0.01;
    const rect = moved ? rectBetween(sketch.from, to) : null;
    sketch = null;
    if (!rect) return context.changed();
    const times = segmentTimes();
    const region: ManualRegion = { ...rect, ...(times ?? {}), id: makeId() };
    state.manual.push(region);
    setDrawing(false);
    select({ kind: "manual", id: region.id }, false);
    changed();
  });
  drawSurface.addEventListener("pointercancel", () => { sketch = null; context.changed(); });
  drawSurface.addEventListener("click", (event) => event.stopPropagation());

  /** "Chỉ đoạn này": the cut segment under the playhead, in source ms, when it is the main video's. */
  function segmentTimes(): { start_ms: number; end_ms: number } | null {
    const point = outputToSource(context.edit(), context.outputTime());
    if (!point || point.segment.mediaId !== MAIN) return null;
    const start = Math.round(point.segment.start * 1000), end = Math.round(point.segment.end * 1000);
    return end > start ? { start_ms: start, end_ms: end } : null;
  }

  // ---------- Panel ----------

  function changed(): void {
    dirtyBoxes();
    renderStyle();
    renderList();
    renderSelected();
    context.changed();
  }

  function setStyle(change: Partial<CoverStyle>): void {
    if (!state) return;
    state.style = normalizeCoverStyle({ ...state.style, ...change });
    changed();
  }

  const swatches = $("#cover-colors");
  const swatchButtons = COVER_SWATCHES.map((color) => {
    const button = element("button", "subs-swatch");
    button.type = "button";
    button.setAttribute("role", "radio");
    button.style.background = color;
    button.dataset.color = color;
    button.title = color;
    button.setAttribute("aria-label", `Màu che ${color}`);
    button.addEventListener("click", () => setStyle({ color }));
    return button;
  });
  const custom = element("label", "subs-swatch custom");
  custom.title = "Màu che tùy chọn";
  const picker = element("input");
  picker.type = "color";
  picker.setAttribute("aria-label", "Màu che tùy chọn");
  picker.addEventListener("input", () => setStyle({ color: picker.value.toUpperCase() }));
  custom.append(picker);
  swatches.replaceChildren(...swatchButtons, custom);

  function renderStyle(): void {
    const look = style();
    for (const button of modeButtons) button.setAttribute("aria-checked", String(button.dataset.coverMode === look.mode));
    $("#cover-mode-hint").textContent = COVER_MODES.find((mode) => mode.id === look.mode)?.hint ?? "";
    $("#cover-stretch-options").hidden = look.mode !== "stretch";
    $("#cover-color-options").hidden = look.mode !== "color";
    $("#cover-mosaic-options").hidden = look.mode !== "mosaic";
    $("#cover-feather-row").hidden = look.mode !== "stretch" && look.mode !== "mosaic";
    $("#cover-blur-note").hidden = look.mode !== "blur";
    for (const button of directionButtons) button.setAttribute("aria-checked", String(button.dataset.direction === look.direction));
    if (document.activeElement !== strip) strip.value = String(look.strip_px);
    $("#cover-strip-value").textContent = `${look.strip_px} px`;
    if (document.activeElement !== block) block.value = String(look.block);
    $("#cover-block-value").textContent = `${look.block} px`;
    if (document.activeElement !== feather) feather.value = String(look.feather);
    $("#cover-feather-value").textContent = look.feather ? `${look.feather} px` : "Tắt";
    let matched = false;
    for (const button of swatchButtons) {
      const on = button.dataset.color === look.color;
      matched ||= on;
      button.setAttribute("aria-checked", String(on));
    }
    custom.classList.toggle("on", !matched);
    picker.value = look.color.toLowerCase();
    custom.style.setProperty("--custom", look.color);
  }

  for (const button of modeButtons) button.addEventListener("click", () => setStyle({ mode: button.dataset.coverMode as Mode }));
  for (const button of directionButtons) button.addEventListener("click", () => setStyle({ direction: button.dataset.direction as CoverDirection }));
  strip.addEventListener("input", () => setStyle({ strip_px: Number(strip.value) }));
  block.addEventListener("input", () => setStyle({ block: Number(block.value) }));
  feather.addEventListener("input", () => setStyle({ feather: Number(feather.value) }));

  function row(className: string, title: string, meta: string, chosen: boolean, onSelect: () => void): HTMLLIElement {
    const item = element("li", `cover-row ${className}${chosen ? " selected" : ""}`);
    const main = element("button", "cover-row-main");
    main.type = "button";
    main.setAttribute("aria-pressed", String(chosen));
    main.append(element("span", "cover-row-title", title), element("span", "cover-row-meta", meta));
    main.addEventListener("click", onSelect);
    item.append(main);
    return item;
  }

  function removeButton(label: string, onRemove: () => void): HTMLButtonElement {
    const button = element("button", "icon-btn cover-row-remove");
    button.type = "button";
    button.setAttribute("aria-label", label);
    button.title = label;
    button.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M5 7h14M10 11v6M14 11v6M7 7l1 13h8l1-13M9 7V4h6v3" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>';
    button.addEventListener("click", onRemove);
    return button;
  }

  function renderList(): void {
    if (!state) {
      list.replaceChildren();
      return;
    }
    const current = state;
    const rows: HTMLLIElement[] = [];
    for (const group of groups) {
      const off = current.disabled.has(group.indices[0]);
      const last = group.spans[group.spans.length - 1];
      const chosen = selection?.kind === "auto" && selection.key === group.key;
      const item = row(`auto${off ? " off" : ""}`, `Tự phát hiện · ${group.indices.length} lần xuất hiện`,
        `${span(group.first, last[1])} · ${(names.get(group.key) ?? "")}${off ? " · đang tắt" : ""}`, chosen, () => select({ kind: "auto", key: group.key }, true));
      const toggle = element("input", "switch cover-row-switch");
      toggle.type = "checkbox";
      toggle.setAttribute("role", "switch");
      toggle.checked = !off;
      toggle.setAttribute("aria-label", `Che nhóm tự phát hiện ${(names.get(group.key) ?? "")}`);
      toggle.title = off ? "Đang tắt: chữ gốc ở đây sẽ không được che" : "Đang che; tắt nếu vùng này che nhầm";
      toggle.addEventListener("change", () => {
        for (const index of group.indices) {
          if (toggle.checked) (current.disabled as Set<number>).delete(index);
          else (current.disabled as Set<number>).add(index);
        }
        changed();
      });
      item.append(toggle);
      rows.push(item);
    }
    jobRects().forEach((rect) => {
      rows.push(row("job", "Vùng chọn khi thêm video", `Cả video · rộng ${percent(rect.w)} × cao ${percent(rect.h)}`, false, () => undefined));
    });
    if (current.fixed) {
      const item = row("fixed", "Che cố định cả video", `Rộng ${percent(current.fixed.w)} × cao ${percent(current.fixed.h)}`,
        selection?.kind === "fixed", () => select({ kind: "fixed" }, true));
      item.append(removeButton("Bỏ che cố định", () => { current.fixed = null; if (selection?.kind === "fixed") selection = null; changed(); }));
      rows.push(item);
    }
    current.manual.forEach((region, index) => {
      const item = row("manual", `Vùng che ${index + 1}`, region.start_ms !== undefined && region.end_ms !== undefined ? span(region.start_ms, region.end_ms) : "Cả video",
        selection?.kind === "manual" && selection.id === region.id, () => select({ kind: "manual", id: region.id }, true));
      item.append(removeButton(`Xóa vùng che ${index + 1}`, () => removeManual(region.id)));
      rows.push(item);
    });
    list.replaceChildren(...rows);
    const auto = groups.reduce((total, group) => total + group.indices.length, 0);
    const drawn = current.manual.length + (current.fixed ? 1 : 0) + jobRects().length;
    $("#cover-count").textContent = [auto ? `${auto} tự dò` : "", drawn ? `${drawn} thêm tay` : ""].filter(Boolean).join(" · ") || "Chưa có";
    $("#cover-empty").hidden = rows.length > 0;
    addButton.disabled = current.manual.length >= MAX_MANUAL;
    renderMissed();
  }

  function renderMissed(): void {
    // The scan's appearances are the only record of where captions were; without one, the helper has nothing to go on.
    missedButton.hidden = !detected.length;
    if (!state || !detected.length) return;
    const moments = uncoveredMoments(state, detected, jobRects());
    missedButton.disabled = !moments.length;
    missedButton.textContent = moments.length ? `Còn sót chữ? Đến chỗ chưa che (${moments.length})` : "Không còn chữ gốc nào bị bỏ sót";
    missedButton.title = moments.length ? "Đến lúc có chữ gốc được dò thấy mà không vùng nào che hết" : "Mọi chữ gốc dò được đều đang được che";
  }

  missedButton.addEventListener("click", () => {
    if (!state) return;
    const moments = uncoveredMoments(state, detected, jobRects());
    const now = (mainSeconds() ?? 0) * 1000;
    // Moments inside cut parts have no output time; try the next ones.
    let moment = nextMoment(moments, now);
    for (let tries = 0; moment !== null && tries < moments.length; tries++) {
      const time = cueOutputTime(context.edit(), { start_ms: moment, end_ms: moment + 1 });
      if (time !== null) {
        context.seek(time + 0.05);
        const caption = detected.find((region) => region.start_ms === moment);
        const group = caption ? groups.find((candidate) => candidate.indices.includes(caption.index)) : undefined;
        if (group) select({ kind: "auto", key: group.key }, false);
        return;
      }
      moment = nextMoment(moments, moment);
    }
  });

  function removeManual(id: string): void {
    if (!state) return;
    state.manual = state.manual.filter((region) => region.id !== id);
    if (selection?.kind === "manual" && selection.id === id) selection = null;
    changed();
  }

  function select(next: Selection, seek: boolean): void {
    selection = next;
    if (seek && next?.kind === "auto") {
      const group = groups.find((candidate) => candidate.key === next.key);
      const time = group ? firstOutputTime(group.spans) : null;
      if (time !== null) context.seek(time + 0.05);
    } else if (seek && next?.kind === "manual") {
      const region = manualOf(next.id);
      const time = region?.start_ms !== undefined && region.end_ms !== undefined
        ? cueOutputTime(context.edit(), { start_ms: region.start_ms, end_ms: region.end_ms }) : null;
      if (time !== null) context.seek(time + 0.05);
    }
    changed();
  }

  /** The first appearance still in the edited video. */
  function firstOutputTime(spans: Array<[number, number]>): number | null {
    for (const [start, end] of spans) {
      const time = cueOutputTime(context.edit(), { start_ms: start, end_ms: end });
      if (time !== null) return time;
    }
    return null;
  }

  function renderSelected(): void {
    const chosen = selection;
    const group = chosen?.kind === "auto" ? groups.find((candidate) => candidate.key === chosen.key) : undefined;
    const region = chosen?.kind === "manual" ? manualOf(chosen.id) : undefined;
    const fixed = chosen?.kind === "fixed" ? state?.fixed ?? null : null;
    selectedPanel.hidden = !group && !region && !fixed;
    if (selectedPanel.hidden || !state) return;
    const title = $("#cover-selected-title");
    const meta = $("#cover-selected-meta");
    $("#cover-span-field").hidden = !region;
    fixButton.hidden = !group;
    deleteButton.hidden = !region && !fixed;
    if (group) {
      const off = state.disabled.has(group.indices[0]);
      title.textContent = `Tự phát hiện · ${(names.get(group.key) ?? "")}`;
      meta.textContent = `${group.indices.length} lần xuất hiện, từ ${formatClock(group.first / 1000)}. ${off
        ? "Đang tắt: chữ gốc ở dải này sẽ không được che." : "Bật/tắt bằng công tắc trong danh sách."}`;
      fixButton.disabled = !!state.fixed && sameRect(state.fixed, group.rect);
    } else if (region) {
      const index = state.manual.indexOf(region);
      title.textContent = `Vùng che ${index + 1}`;
      meta.textContent = `Rộng ${percent(region.w)} × cao ${percent(region.h)}. Kéo trên video để di chuyển, kéo góc để đổi cỡ.`;
      const timed = region.start_ms !== undefined && region.end_ms !== undefined;
      for (const button of spanButtons) button.setAttribute("aria-checked", String(button.dataset.span === (timed ? "segment" : "all")));
      $("#cover-times").hidden = !timed;
      if (timed) {
        if (document.activeElement !== startInput) startInput.value = formatClock(region.start_ms! / 1000, true);
        if (document.activeElement !== endInput) endInput.value = formatClock(region.end_ms! / 1000, true);
      }
      deleteButton.textContent = "Xóa vùng che";
    } else if (fixed) {
      title.textContent = "Che cố định cả video";
      meta.textContent = `Rộng ${percent(fixed.w)} × cao ${percent(fixed.h)}, suốt video. Kéo trên video để di chuyển, kéo góc để đổi cỡ.`;
      deleteButton.textContent = "Bỏ che cố định";
    }
  }

  const sameRect = (a: Rect, b: Rect): boolean => Math.abs(a.x - b.x) + Math.abs(a.y - b.y) + Math.abs(a.w - b.w) + Math.abs(a.h - b.h) < 1e-4;

  for (const button of spanButtons) {
    button.addEventListener("click", () => {
      if (selection?.kind !== "manual") return;
      const region = manualOf(selection.id);
      if (!region) return;
      if (button.dataset.span === "all") {
        delete region.start_ms;
        delete region.end_ms;
      } else {
        const times = segmentTimes();
        if (!times) {
          showHint("Đưa đầu phát vào một đoạn của video chính để lấy thời gian của đoạn đó");
          window.setTimeout(() => showHint(drawing ? "Kéo trên video để vẽ vùng che · Esc để hủy" : null), 2500);
          return;
        }
        Object.assign(region, times);
      }
      changed();
    });
  }

  function commitTimes(): void {
    if (selection?.kind !== "manual") return;
    const region = manualOf(selection.id);
    const start = parseClock(startInput.value), end = parseClock(endInput.value);
    if (!region || start === null || end === null || end - start < 0.1) {
      renderSelected();
      return;
    }
    region.start_ms = Math.round(start * 1000);
    region.end_ms = Math.min(86_400_000, Math.round(end * 1000));
    changed();
  }
  for (const input of [startInput, endInput]) {
    input.addEventListener("change", commitTimes);
    input.addEventListener("keydown", (event) => { if (event.key === "Enter") input.blur(); });
  }

  fixButton.addEventListener("click", () => {
    if (!state || selection?.kind !== "auto") return;
    const group = groups.find((candidate) => candidate.key === (selection as { key: string }).key);
    if (!group) return;
    state.fixed = { ...group.rect };
    selection = { kind: "fixed" };
    changed();
  });
  deleteButton.addEventListener("click", () => {
    if (!state) return;
    if (selection?.kind === "manual") removeManual(selection.id);
    else if (selection?.kind === "fixed") {
      state.fixed = null;
      selection = null;
      changed();
    }
  });
  addButton.addEventListener("click", () => setDrawing(!drawing));

  return {
    get active() { return active; },
    begin() {
      base = null;
      state = null;
      detected = [];
      groups = [];
      selection = null;
      setDrawing(false);
      renderStyle();
      renderList();
      renderSelected();
    },
    end() {
      this.leave();
      base = null;
      state = null;
      detected = [];
      groups = [];
    },
    enter() {
      active = true;
      section.hidden = false;
      dirtyBoxes();
      renderStyle();
      renderList();
      renderSelected();
    },
    leave() {
      active = false;
      section.hidden = true;
      setDrawing(false);
      drag = null;
      dirtyBoxes();
      boxes.replaceChildren();
      boxes.hidden = true;
      showHint(null);
    },
    keydown(event) {
      if (!active) return false;
      const key = event.key.toLowerCase();
      if (key === "escape" && (drawing || selection)) {
        if (drawing) setDrawing(false);
        else select(null, false);
        return true;
      }
      if ((key === "delete" || key === "backspace") && (selection?.kind === "manual" || selection?.kind === "fixed")) {
        deleteButton.click();
        return true;
      }
      return false;
    },
    // ---------- Hooks for the shared preview and "Áp dụng" ----------
    loaded(cover, regions) {
      state = loadCover(cover, makeId);
      base = cloneState(state);
      detected = regions.filter((region) => Number.isInteger(region.index));
      byIndex = new Map(detected.map((region) => [region.index, region]));
      groups = groupAuto(detected);
      names = lineNames(groups);
      selection = null;
      renderStyle();
      renderList();
      renderSelected();
    },
    draw(seconds) {
      if (seconds === null) {
        shown = false;
        const ctx = canvas.getContext("2d");
        ctx?.clearRect(0, 0, canvas.width, canvas.height);
        if (active) renderBoxes(null);
        return;
      }
      draw(seconds);
    },
    pending() {
      if (!state || !base || sameCover(state, base)) return null;
      return { document: coverDocument(state), summary: coverSummary(base, state) };
    },
    applied() {
      if (state) base = cloneState(state);
    },
  };
}
