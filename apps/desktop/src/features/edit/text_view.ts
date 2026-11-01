/**
 * The Chữ mode of the editor (Issue #422): adding texts (plain or from a
 * preset), the selected text's controls, the list, the text row of the
 * timeline (move, change length) and the texts on the preview, drawn in
 * HTML/CSS at the export's scale and animated from the playhead; in this
 * mode the selected text is dragged (snapping to guides), widened from its
 * sides and edited in place with a double click.
 */
import { SUBTITLE_FONTS } from "./fonts.ts";
import {
  addText, animSpan, audioLanes, duplicateText, formatClock, MAX_TEXT_CHARS, MAX_TEXTS, MIN_TEXT, moveText, outputDuration, parseClock,
  removeText, resizeText, setTextProps, setTextTime, textLength, TEXT_ANIMATIONS, type Edit, type TextClip, type TextLook,
} from "./model.ts";
import {
  ANIMATION_NAMES, BOX_COLORS, OUTLINE_COLORS, previewLine, snapText, TEXT_COLORS, TEXT_PRESETS, textFrame, textMetrics, textUnits,
  type Guide, type Picture,
} from "./text.ts";
import { rgba } from "./subtitles.ts";

type Rect = { left: number; top: number; width: number; height: number };

export type TextContext = {
  /** The edit on screen (a drag's draft included). */
  edit: () => Edit;
  /** The committed edit operations start from. */
  base: () => Edit;
  /** Another change is being dragged. */
  busy: () => boolean;
  commit: (next: Edit) => void;
  /** Show an uncommitted change (null drops it). */
  preview: (next: Edit | null) => void;
  outputTime: () => number;
  seek: (time: number) => void;
  pause: () => void;
  /** Pixels per second and the output time under a client x. */
  pps: () => number;
  timeAt: (clientX: number) => number;
  /** The video picture inside the player frame. */
  picture: () => Rect;
  /** Open the Chữ panel. */
  enterMode: () => void;
  note: (text: string) => void;
  /** Draw everything again (the selection changed). */
  redraw: () => void;
};

export type TextMode = {
  begin(): void;
  end(): void;
  /** Draw the panel (when shown); the texts on the preview take the mouse only then. */
  render(shown: boolean): void;
  /** Draw the text row; its height in pixels (0 when hidden). */
  renderTrack(row: HTMLElement, shown: boolean, pad: number): number;
  /** Draw the texts at output time `time`. */
  frame(time: number): void;
  readonly selected: string | null;
  select(id: string | null): void;
  /** Delete the selected text; false when none is selected. */
  deleteSelected(): boolean;
};

const LANE_PX = 20;
const MAX_LANES = 3;
const SNAP_PX = 6;

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing text element: ${selector}`);
  return node;
};

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));
const secondsText = (value: number): string => `${String(Math.round(value * 10) / 10).replace(".", ",")} giây`;

/** One text on the preview: its box (positioned) and the painted words inside. */
type Shown = { box: HTMLDivElement; body: HTMLSpanElement; painted: string; units: HTMLSpanElement[]; shown: number };

export function mountText(context: TextContext): TextMode {
  const section = $("#edit-text");
  const layer = $("#edit-texts");
  const selectedBox = $("#tx-selected");
  const textInput = $<HTMLTextAreaElement>("#tx-text");
  const atInput = $<HTMLInputElement>("#tx-at");
  const durationInput = $<HTMLInputElement>("#tx-duration");
  const fontSelect = $<HTMLSelectElement>("#tx-font");
  const sizeInput = $<HTMLInputElement>("#tx-size");
  const outlineInput = $<HTMLInputElement>("#tx-outline-width");
  const boxToggle = $<HTMLInputElement>("#tx-box");
  const boxOpacity = $<HTMLInputElement>("#tx-box-opacity");
  const animationSelect = $<HTMLSelectElement>("#tx-animation");
  const animInput = $<HTMLInputElement>("#tx-anim");
  const boldButton = $<HTMLButtonElement>("#tx-bold");
  const italicButton = $<HTMLButtonElement>("#tx-italic");
  const alignButtons = [...document.querySelectorAll<HTMLButtonElement>("#tx-align [data-align]")];
  const list = $("#tx-list");
  const addButton = $<HTMLButtonElement>("#tx-add");

  let active = false;
  let shown = false;
  let selected: string | null = null;
  /** The text being edited on the preview, if any. */
  let editing: string | null = null;
  const views = new Map<string, Shown>();
  const guides: HTMLSpanElement[] = [];

  const selectedText = (): TextClip | undefined => selected ? context.edit().texts.find((text) => text.id === selected) : undefined;
  const selectedId = (): string | null => selectedText() ? selected : null;

  // ---------- Adding ----------

  /** Add as one step, select it and show it whole. */
  function add(next: Edit, focus: boolean): void {
    const before = context.base();
    if (next === before) {
      context.note(before.texts.length >= MAX_TEXTS ? `Đã đạt tối đa ${MAX_TEXTS} chữ.` : "Video quá ngắn để thêm chữ.");
      return;
    }
    const clip = next.texts[next.texts.length - 1];
    selected = clip.id;
    context.pause();
    context.commit(next);
    context.seek(showTime(clip, outputDuration(next)));
    if (focus) {
      textInput.focus();
      textInput.select();
    }
  }

  /** A moment the text shows whole: right after its entrance. */
  function showTime(text: TextClip, total: number): number {
    const length = textLength(text, total);
    return text.at + Math.min(animSpan(text, length), Math.max(0, length - 0.05));
  }

  addButton.addEventListener("click", () => {
    if (!active || context.busy()) return;
    add(addText(context.base(), context.outputTime()), true);
  });
  const presetButtons = TEXT_PRESETS.map((preset) => {
    const button = element("button", "tx-preset");
    button.type = "button";
    button.title = `${preset.label}: ${ANIMATION_NAMES[preset.look.animation ?? "none"].toLowerCase()}`;
    const sample = element("span", "tx-preset-sample", preset.label);
    const look = preset.look;
    Object.assign(sample.style, {
      fontFamily: `"${look.font}", sans-serif`, fontWeight: look.bold ? "700" : "400", color: look.color ?? "#FFFFFF",
      webkitTextStroke: look.outline_width ? `${Math.min(3, look.outline_width / 2)}px ${look.outline}` : "0",
      background: look.box ? rgba(look.box, look.box_opacity ?? 0.6) : "transparent", padding: look.box ? "1px 6px" : "0",
    });
    button.append(sample);
    button.addEventListener("click", () => {
      if (!active || context.busy()) return;
      const base = context.base();
      const total = outputDuration(base);
      const at = context.outputTime();
      add(addText(base, at, preset.text, preset.look, preset.seconds === "end" ? Math.max(MIN_TEXT, total - at) : preset.seconds), true);
    });
    return button;
  });
  $("#tx-presets").replaceChildren(...presetButtons);

  // ---------- Panel ----------

  for (const font of SUBTITLE_FONTS) {
    const option = element("option", "", font);
    option.value = font;
    option.style.fontFamily = `"${font}", sans-serif`;
    fontSelect.append(option);
  }
  for (const animation of TEXT_ANIMATIONS) {
    const option = element("option", "", ANIMATION_NAMES[animation]);
    option.value = animation;
    animationSelect.append(option);
  }

  const swatchState: Array<() => void> = [];
  function swatchRow(container: HTMLElement, colors: readonly string[], name: string, get: () => string | null, set: (value: string, live: boolean) => void): void {
    const buttons = colors.map((color) => {
      const button = element("button", "subs-swatch");
      button.type = "button";
      button.setAttribute("role", "radio");
      button.setAttribute("aria-label", `${name} ${color}`);
      button.title = color;
      button.style.background = color;
      button.addEventListener("click", () => set(color, false));
      return button;
    });
    const custom = element("label", "subs-swatch custom");
    custom.title = `${name} tùy chọn`;
    const picker = element("input");
    picker.type = "color";
    picker.setAttribute("aria-label", `${name} tùy chọn`);
    picker.addEventListener("input", () => set(picker.value.toUpperCase(), true));
    picker.addEventListener("change", () => set(picker.value.toUpperCase(), false));
    custom.append(picker);
    container.replaceChildren(...buttons, custom);
    swatchState.push(() => {
      const value = get();
      for (const button of buttons) button.setAttribute("aria-checked", String(button.title === value));
      const isCustom = !!value && !colors.includes(value);
      custom.classList.toggle("on", isCustom);
      custom.style.setProperty("--custom", value ?? "transparent");
      if (value && document.activeElement !== picker) picker.value = value.toLowerCase();
    });
  }

  /** A change to the selected text: live ones are previewed until the control lets go. */
  function change(props: Partial<TextLook> & { text?: string }, live = false): void {
    const id = selectedId();
    if (!id || !active) return;
    if (live) {
      context.preview(setTextProps(context.base(), id, props));
      return;
    }
    context.preview(null);
    context.commit(setTextProps(context.base(), id, props));
  }

  /** The live preview of a control becomes a step. */
  function settle(): void {
    const next = context.busy() ? context.edit() : null;
    context.preview(null);
    if (next && selectedId()) context.commit(next);
  }

  swatchRow($("#tx-color"), TEXT_COLORS, "Màu chữ", () => selectedText()?.color ?? null, (color, live) => change({ color }, live));
  swatchRow($("#tx-outline"), OUTLINE_COLORS, "Màu viền", () => selectedText()?.outline ?? null, (color, live) => change({ outline: color }, live));
  swatchRow($("#tx-box-color"), BOX_COLORS, "Màu hộp", () => selectedText()?.box ?? null, (color, live) => change({ box: color }, live));

  textInput.addEventListener("input", () => {
    $("#tx-chars").textContent = `${[...textInput.value].length}/${MAX_TEXT_CHARS} ký tự`;
    if (textInput.value.trim()) change({ text: textInput.value }, true);
  });
  textInput.addEventListener("change", () => {
    if (!textInput.value.trim()) {
      context.preview(null);
      context.note("Chữ không được để trống.");
      context.redraw();
      return;
    }
    change({ text: textInput.value });
  });
  textInput.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      context.preview(null);
      textInput.blur();
      context.redraw();
    } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      textInput.blur();
    }
  });
  fontSelect.addEventListener("change", () => change({ font: fontSelect.value as TextLook["font"] }));
  sizeInput.addEventListener("input", () => change({ size: Number(sizeInput.value) }, true));
  outlineInput.addEventListener("input", () => change({ outline_width: Number(outlineInput.value) }, true));
  boxOpacity.addEventListener("input", () => change({ box_opacity: Number(boxOpacity.value) / 100 }, true));
  animInput.addEventListener("input", () => change({ anim_duration: Number(animInput.value) }, true));
  for (const input of [sizeInput, outlineInput, boxOpacity, animInput]) input.addEventListener("change", settle);
  boxToggle.addEventListener("change", () => change({ box: boxToggle.checked ? selectedText()?.box ?? "#000000" : null }));
  boldButton.addEventListener("click", () => change({ bold: !selectedText()?.bold }));
  italicButton.addEventListener("click", () => change({ italic: !selectedText()?.italic }));
  for (const button of alignButtons) button.addEventListener("click", () => change({ align: button.dataset.align as TextLook["align"] }));
  animationSelect.addEventListener("change", () => change({ animation: animationSelect.value as TextLook["animation"] }));
  for (const [input, field] of [[atInput, "at"], [durationInput, "duration"]] as const) {
    input.addEventListener("change", () => {
      const id = selectedId();
      const value = parseClock(input.value);
      if (id && value !== null) context.commit(setTextTime(context.base(), id, { [field]: value }));
      else context.redraw();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") input.blur();
      if (event.key === "Escape") {
        input.blur();
        context.redraw();
      }
    });
  }
  $("#tx-duplicate").addEventListener("click", () => {
    const id = selectedId();
    if (!id || context.busy()) return;
    const next = duplicateText(context.base(), id);
    if (next === context.base()) context.note(`Đã đạt tối đa ${MAX_TEXTS} chữ.`);
    else add(next, false);
  });
  $("#tx-delete").addEventListener("click", () => { deleteSelected(); });

  function renderSelected(edit: Edit): void {
    const text = selectedText();
    selectedBox.hidden = !text;
    if (!text) return;
    const total = outputDuration(edit);
    const length = textLength(text, total);
    $("#tx-title").textContent = `Chữ · ${formatClock(text.at, true)} – ${formatClock(text.at + length, true)}`;
    if (document.activeElement !== textInput && textInput.value !== text.text) textInput.value = text.text;
    $("#tx-chars").textContent = `${[...(document.activeElement === textInput ? textInput.value : text.text)].length}/${MAX_TEXT_CHARS} ký tự`;
    if (document.activeElement !== atInput) atInput.value = formatClock(text.at, true);
    if (document.activeElement !== durationInput) durationInput.value = formatClock(text.duration, true);
    $("#tx-length").textContent = length < text.duration - 0.01 ? `Hiện ${formatClock(length, true)} · bị cắt ở cuối video` : `Hiện ${secondsText(length)}`;
    fontSelect.value = text.font;
    fontSelect.style.fontFamily = `"${text.font}", sans-serif`;
    if (document.activeElement !== sizeInput) sizeInput.value = String(text.size);
    $("#tx-size-value").textContent = String(text.size);
    if (document.activeElement !== outlineInput) outlineInput.value = String(text.outline_width);
    $("#tx-outline-value").textContent = text.outline_width ? String(text.outline_width).replace(".", ",") : "Không";
    boldButton.setAttribute("aria-pressed", String(text.bold));
    italicButton.setAttribute("aria-pressed", String(text.italic));
    for (const button of alignButtons) button.setAttribute("aria-checked", String(button.dataset.align === text.align));
    boxToggle.checked = text.box !== null;
    $("#tx-box-options").hidden = text.box === null;
    if (document.activeElement !== boxOpacity) boxOpacity.value = String(Math.round(text.box_opacity * 100));
    $("#tx-box-opacity-value").textContent = `${Math.round(text.box_opacity * 100)}%`;
    animationSelect.value = text.animation;
    $("#tx-anim-row").hidden = text.animation === "none";
    if (document.activeElement !== animInput) animInput.value = String(text.anim_duration);
    const span = animSpan(text, length);
    $("#tx-anim-value").textContent = span < text.anim_duration - 0.01 ? `${secondsText(span)} (chữ ngắn)` : secondsText(text.anim_duration);
    for (const update of swatchState) update();
  }

  function renderList(edit: Edit): void {
    const total = outputDuration(edit);
    const time = context.outputTime();
    list.replaceChildren(...[...edit.texts].sort((a, b) => a.at - b.at).map((text) => {
      const row = element("li", `tx-item${text.id === selected ? " selected" : ""}`);
      row.dataset.id = text.id;
      const length = textLength(text, total);
      if (time >= text.at && time < text.at + length) row.classList.add("on");
      const when = element("span", "tx-item-time", formatClock(text.at));
      const words = element("span", "tx-item-text", previewLine(text.text));
      words.style.fontFamily = `"${text.font}", sans-serif`;
      const remove = element("button", "icon-btn tx-item-delete");
      remove.type = "button";
      remove.title = "Xóa chữ này";
      remove.setAttribute("aria-label", `Xóa chữ “${previewLine(text.text, 30)}”`);
      remove.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        if (context.busy()) return;
        if (selected === text.id) selected = null;
        context.commit(removeText(context.base(), text.id));
      });
      row.append(when, words, remove);
      row.title = `${ANIMATION_NAMES[text.animation]} · ${formatClock(text.at, true)} – ${formatClock(text.at + length, true)}`;
      row.addEventListener("click", () => {
        selected = text.id;
        context.seek(showTime(text, outputDuration(context.edit())));
        context.redraw();
      });
      return row;
    }));
    $("#tx-empty").hidden = edit.texts.length > 0;
    $("#tx-count-all").textContent = edit.texts.length ? `${edit.texts.length}/${MAX_TEXTS} chữ` : "";
    addButton.disabled = edit.texts.length >= MAX_TEXTS;
    for (const button of presetButtons) button.disabled = edit.texts.length >= MAX_TEXTS;
  }

  function render(show: boolean): void {
    shown = show;
    section.hidden = !show;
    layer.classList.toggle("interactive", show && active);
    if (!show || !active) return;
    const edit = context.edit();
    renderSelected(edit);
    renderList(edit);
  }

  // ---------- Timeline row ----------

  function renderTrack(row: HTMLElement, show: boolean, pad: number): number {
    const edit = context.edit();
    const total = outputDuration(edit);
    const visible = active && (show || edit.texts.length > 0);
    row.hidden = !visible;
    if (!visible) {
      row.replaceChildren();
      return 0;
    }
    const pps = context.pps();
    const lanes = audioLanes(edit.texts.map(({ id, at, duration }) => ({ id, at, start: 0, end: duration })), total);
    const count = Math.min(MAX_LANES, Math.max(1, ...[...lanes.values()].map((lane) => lane + 1)));
    const blocks: HTMLElement[] = edit.texts.map((text) => {
      const length = textLength(text, total);
      const block = element("div", `tl-tx${text.id === selected ? " selected" : ""}`);
      block.dataset.id = text.id;
      Object.assign(block.style, {
        left: `${pad + text.at * pps}px`, width: `${Math.max(6, length * pps)}px`, top: `${Math.min(count - 1, lanes.get(text.id) ?? 0) * LANE_PX}px`,
      });
      block.title = `${previewLine(text.text)} · ${formatClock(text.at, true)} – ${formatClock(text.at + length, true)} · kéo để dời, kéo mép để đổi độ dài`;
      block.append(element("span", "tl-tx-name", previewLine(text.text, 80)));
      if (length * pps >= 24) {
        const start = element("span", "tl-handle start");
        start.dataset.edge = "start";
        const end = element("span", "tl-handle end");
        end.dataset.edge = "end";
        block.append(start, end);
      }
      return block;
    });
    if (!edit.texts.length) {
      const hint = element("span", "tl-au-hint", "Bấm Thêm chữ hoặc chọn một mẫu trong bảng Chữ");
      hint.style.left = `${pad}px`;
      blocks.push(hint);
    }
    row.replaceChildren(...blocks);
    row.style.height = `${count * LANE_PX}px`;
    return count * LANE_PX;
  }

  type RowDrag = { kind: "move" | "start" | "end"; id: string; x: number; base: Edit; moved: boolean; pointer: number };
  let rowDrag: RowDrag | null = null;
  const row = $("#tl-text");

  row.addEventListener("pointerdown", (event) => {
    if (!active || event.button !== 0) return;
    const target = event.target as HTMLElement;
    const block = target.closest<HTMLElement>(".tl-tx");
    if (!block || context.busy()) return;
    event.stopPropagation();
    event.preventDefault();
    row.setPointerCapture(event.pointerId);
    const handle = target.closest<HTMLElement>(".tl-handle");
    selected = block.dataset.id ?? null;
    rowDrag = { kind: handle ? (handle.dataset.edge === "start" ? "start" : "end") : "move", id: selected!, x: event.clientX, base: context.base(),
      moved: false, pointer: event.pointerId };
    context.pause();
    context.enterMode();
  });
  row.addEventListener("pointermove", (event) => {
    if (!rowDrag || event.pointerId !== rowDrag.pointer) return;
    const dragging = rowDrag;
    if (!dragging.moved && Math.abs(event.clientX - dragging.x) <= 4) return;
    dragging.moved = true;
    const text = dragging.base.texts.find((candidate) => candidate.id === dragging.id);
    if (!text) return;
    const total = outputDuration(dragging.base);
    const shift = (event.clientX - dragging.x) / context.pps();
    let next: Edit;
    if (dragging.kind === "move") {
      next = setTextTime(dragging.base, dragging.id, { at: clamp(text.at + shift, 0, Math.max(0, total - text.duration)) });
    } else if (dragging.kind === "end") {
      next = setTextTime(dragging.base, dragging.id, { duration: text.duration + shift });
    } else {
      const end = text.at + text.duration;
      const at = clamp(text.at + shift, 0, end - MIN_TEXT);
      next = setTextTime(dragging.base, dragging.id, { at, duration: end - at });
    }
    context.preview(next);
    const moved = next.texts.find((candidate) => candidate.id === dragging.id)!;
    context.seek(dragging.kind === "end" ? moved.at + textLength(moved, outputDuration(next)) - 0.02 : moved.at);
  });
  const endRowDrag = (event: PointerEvent): void => {
    if (!rowDrag || event.pointerId !== rowDrag.pointer) return;
    const ended = rowDrag;
    rowDrag = null;
    const next = context.busy() ? context.edit() : null;
    context.preview(null);
    if (ended.moved && next) {
      context.commit(next);
      return;
    }
    const text = context.base().texts.find((candidate) => candidate.id === ended.id);
    if (text && !ended.moved) {
      const length = textLength(text, outputDuration(context.base()));
      context.seek(clamp(context.timeAt(ended.x), text.at, text.at + Math.max(0, length - 0.02)));
    }
    context.redraw();
  };
  row.addEventListener("pointerup", endRowDrag);
  row.addEventListener("pointercancel", endRowDrag);

  // ---------- The texts on the preview ----------

  function viewFor(text: TextClip): Shown {
    let view = views.get(text.id);
    if (!view) {
      const box = element("div", "tx-box");
      box.dataset.id = text.id;
      const body = element("span", "tx-body");
      const west = element("span", "tx-handle w");
      west.dataset.side = "w";
      west.title = "Kéo để đổi độ rộng";
      const east = element("span", "tx-handle e");
      east.dataset.side = "e";
      east.title = "Kéo để đổi độ rộng";
      box.append(body, west, east);
      layer.append(box);
      view = { box, body, painted: "", units: [], shown: -1 };
      views.set(text.id, view);
    }
    return view;
  }

  /** Words, look and place; redone only when one of them changed. */
  function paint(view: Shown, text: TextClip, picture: Picture): void {
    const key = JSON.stringify([text.text, text.font, text.size, text.color, text.outline, text.outline_width, text.box, text.box_opacity, text.bold,
      text.italic, text.align, text.animation, text.x, text.y, text.w, picture.width, picture.height]);
    if (key === view.painted) return;
    view.painted = key;
    const metrics = textMetrics(text, picture);
    const width = metrics.widthPx + metrics.paddingPx * 2;
    Object.assign(view.box.style, {
      left: `${text.x * picture.width - width / 2}px`, top: `${text.y * picture.height}px`, width: `${width}px`,
      justifyContent: text.align === "left" ? "flex-start" : text.align === "right" ? "flex-end" : "center",
      transformOrigin: text.align === "left" ? "0% 50%" : text.align === "right" ? "100% 50%" : "50% 50%",
    });
    Object.assign(view.body.style, {
      fontFamily: `"${text.font}", sans-serif`, fontSize: `${metrics.fontPx}px`, lineHeight: String(metrics.lineHeight),
      fontWeight: text.bold ? "700" : "400", fontStyle: text.italic ? "italic" : "normal", color: text.color,
      webkitTextStroke: metrics.outlinePx > 0 ? `${metrics.outlinePx * 2}px ${text.outline}` : "0",
      background: text.box ? rgba(text.box, text.box_opacity) : "transparent", padding: `${metrics.paddingPx}px`, width: "", textAlign: text.align,
    });
    if (editing !== text.id) {
      view.units = [];
      view.shown = -1;
      if (text.animation === "typewriter") {
        view.body.replaceChildren(...textUnits(text.text).map((unit) => {
          if (unit.index === null) return document.createTextNode(unit.text);
          const span = element("span", "", unit.text);
          view.units.push(span);
          return span;
        }));
      } else {
        view.body.textContent = text.text;
      }
    }
    fitBox(view, metrics.paddingPx);
  }

  /** A wrapped inline block is as wide as its container; libass's box hugs the widest line. */
  function fitBox(view: Shown, padding: number): void {
    view.body.style.width = "";
    if (padding <= 0 || !view.body.textContent || view.box.classList.contains("editing")) return;
    const range = document.createRange();
    range.selectNodeContents(view.body);
    const widest = Math.max(0, ...[...range.getClientRects()].map((line) => line.width));
    const box = view.box.getBoundingClientRect();
    const scale = box.width > 0 && view.box.offsetWidth > 0 ? box.width / view.box.offsetWidth : 1;
    if (widest > 0) view.body.style.width = `${Math.ceil(widest / scale + padding * 2 + 1)}px`;
  }

  function frame(time: number): void {
    if (!active) return;
    const rect = context.picture();
    Object.assign(layer.style, { left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px` });
    const edit = context.edit();
    const total = outputDuration(edit);
    const seen = new Set<string>();
    for (const text of edit.texts) {
      seen.add(text.id);
      const state = textFrame(text, time, total);
      const chosen = shown && text.id === selected;
      const existing = views.get(text.id);
      if (!state && !chosen && editing !== text.id) {
        if (existing) existing.box.hidden = true;
        continue;
      }
      const view = viewFor(text);
      view.box.hidden = false;
      view.box.classList.toggle("selected", chosen);
      view.box.classList.toggle("idle", !state && editing !== text.id);
      view.box.classList.toggle("editing", editing === text.id);
      paint(view, text, rect);
      const look = (editing === text.id ? null : state) ?? { opacity: 1, scale: 1, dy: 0, shown: Number.POSITIVE_INFINITY };
      // A selected text off its time shows faintly, to be placed.
      view.body.style.opacity = !state && editing !== text.id ? "0.35" : look.opacity.toFixed(3);
      view.box.style.transform = `translateY(calc(-50% + ${(look.dy * rect.height).toFixed(2)}px)) scale(${look.scale.toFixed(4)})`;
      const reveal = Math.min(view.units.length, look.shown);
      if (view.units.length && reveal !== view.shown) {
        view.shown = reveal;
        view.units.forEach((unit, index) => unit.classList.toggle("tx-hide", index >= reveal));
      }
    }
    // Later texts draw on top, as in the export.
    const order = edit.texts.map((text) => views.get(text.id)?.box).filter((box): box is HTMLDivElement => !!box);
    if (order.some((box, index) => layer.children[index] !== box)) layer.prepend(...order);
    for (const [id, view] of views) {
      if (seen.has(id)) continue;
      view.box.remove();
      views.delete(id);
    }
  }

  // ---------- Dragging and editing on the preview ----------

  type BoxDrag = { id: string; side: "move" | "w" | "e"; x: number; y: number; base: Edit; moved: boolean; pointer: number; height: number };
  let boxDrag: BoxDrag | null = null;

  function showGuides(lines: Guide[]): void {
    while (guides.length < lines.length) {
      const guide = element("span", "tx-guide");
      guides.push(guide);
    }
    guides.forEach((guide, index) => {
      const line = lines[index];
      if (!line) {
        guide.remove();
        return;
      }
      guide.className = `tx-guide ${line.axis}`;
      guide.style.left = line.axis === "x" ? `${line.at * 100}%` : "";
      guide.style.top = line.axis === "y" ? `${line.at * 100}%` : "";
      if (guide.parentElement !== layer) layer.append(guide);
    });
  }

  layer.addEventListener("pointerdown", (event) => {
    if (!active || !shown || event.button !== 0 || editing) return;
    const target = event.target as HTMLElement;
    const box = target.closest<HTMLElement>(".tx-box");
    if (!box || context.busy()) return;
    event.preventDefault();
    event.stopPropagation();
    box.setPointerCapture(event.pointerId);
    const handle = target.closest<HTMLElement>(".tx-handle");
    selected = box.dataset.id!;
    context.pause();
    boxDrag = { id: selected, side: handle?.dataset.side === "w" ? "w" : handle ? "e" : "move", x: event.clientX, y: event.clientY,
      base: context.base(), moved: false, pointer: event.pointerId, height: views.get(selected)?.body.getBoundingClientRect().height ?? 0 };
    context.redraw();
  });
  layer.addEventListener("pointermove", (event) => {
    if (!boxDrag || event.pointerId !== boxDrag.pointer) return;
    const dragging = boxDrag;
    if (!dragging.moved && Math.abs(event.clientX - dragging.x) + Math.abs(event.clientY - dragging.y) < 3) return;
    dragging.moved = true;
    const text = dragging.base.texts.find((candidate) => candidate.id === dragging.id);
    const rect = context.picture();
    if (!text || rect.width <= 0 || rect.height <= 0) return;
    const dx = (event.clientX - dragging.x) / rect.width, dy = (event.clientY - dragging.y) / rect.height;
    if (dragging.side === "move") {
      const pad = textMetrics(text, rect).paddingPx;
      const snapped = snapText({ x: text.x + dx, y: text.y + dy }, { w: text.w + pad * 2 / rect.width, h: dragging.height / rect.height },
        { x: SNAP_PX / rect.width, y: SNAP_PX / rect.height }, event.altKey);
      showGuides(snapped.guides);
      context.preview(moveText(dragging.base, dragging.id, snapped.x, snapped.y));
    } else {
      const left = text.x - text.w / 2, right = text.x + text.w / 2;
      const w = dragging.side === "e" ? clamp(text.w + dx, 0.1, 1 - left) : clamp(text.w - dx, 0.1, right);
      context.preview(resizeText(dragging.base, dragging.id, w, dragging.side === "e" ? left + w / 2 : right - w / 2));
    }
  });
  const endBoxDrag = (event: PointerEvent): void => {
    if (!boxDrag || event.pointerId !== boxDrag.pointer) return;
    const ended = boxDrag;
    boxDrag = null;
    showGuides([]);
    const next = context.busy() ? context.edit() : null;
    context.preview(null);
    if (ended.moved && next) context.commit(next);
    else context.redraw();
  };
  layer.addEventListener("pointerup", endBoxDrag);
  layer.addEventListener("pointercancel", endBoxDrag);
  // A click on a text does not play or pause the video.
  layer.addEventListener("click", (event) => { if ((event.target as HTMLElement).closest(".tx-box")) event.stopPropagation(); });

  layer.addEventListener("dblclick", (event) => {
    const box = (event.target as HTMLElement).closest<HTMLElement>(".tx-box");
    if (!active || !shown || !box || context.busy()) return;
    event.preventDefault();
    event.stopPropagation();
    startEditing(box.dataset.id!);
  });

  function startEditing(id: string): void {
    const view = views.get(id);
    const text = context.base().texts.find((candidate) => candidate.id === id);
    if (!view || !text) return;
    selected = id;
    editing = id;
    context.pause();
    view.units = [];
    view.body.textContent = text.text;
    view.body.contentEditable = "plaintext-only";
    view.body.spellcheck = false;
    view.box.classList.add("editing");
    context.redraw();
    view.body.focus();
    const range = document.createRange();
    range.selectNodeContents(view.body);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }

  function stopEditing(keep: boolean): void {
    const id = editing;
    const view = id ? views.get(id) : undefined;
    editing = null;
    if (!id || !view) return;
    const words = view.body.innerText.replace(/\n$/, "");
    view.body.contentEditable = "false";
    view.body.removeAttribute("contenteditable");
    view.painted = "";
    view.box.classList.remove("editing");
    if (keep && words.trim()) context.commit(setTextProps(context.base(), id, { text: words }));
    else {
      if (keep) context.note("Chữ không được để trống.");
      context.redraw();
    }
  }

  layer.addEventListener("keydown", (event) => {
    if (!editing) return;
    event.stopPropagation();
    if (event.key === "Escape") {
      event.preventDefault();
      stopEditing(false);
    } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      stopEditing(true);
    } else if ([...(event.target as HTMLElement).innerText ?? ""].length >= MAX_TEXT_CHARS && event.key.length === 1 && !event.ctrlKey) {
      event.preventDefault();
    }
  });
  layer.addEventListener("focusout", (event) => {
    if (editing && (event.target as HTMLElement).classList.contains("tx-body")) stopEditing(true);
  });

  function deleteSelected(): boolean {
    const id = selectedId();
    if (!id || context.busy() || editing) return false;
    selected = null;
    context.commit(removeText(context.base(), id));
    return true;
  }

  return {
    begin: () => {
      active = true;
      selected = null;
      editing = null;
    },
    end: () => {
      active = false;
      editing = null;
      boxDrag = null;
      rowDrag = null;
      selected = null;
      for (const view of views.values()) view.box.remove();
      views.clear();
      showGuides([]);
      layer.classList.remove("interactive");
    },
    render,
    renderTrack,
    frame,
    get selected() { return selectedId(); },
    select: (id) => {
      if (editing && id !== editing) stopEditing(true);
      selected = id;
    },
    deleteSelected,
  };
}

