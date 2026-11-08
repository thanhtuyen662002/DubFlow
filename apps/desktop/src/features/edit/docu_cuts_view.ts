/**
 * The documentary's cuts in Chỉnh sửa (Issue #630): a "Lát cắt" lane under
 * Video with one block per planned slice, and a panel for the selected cut
 * (its footage, its place, its own Che). The film is rendered again with the
 * edits; the narration timing stays, so the draft still fits.
 */
import { dragRegion, type DragHandle } from "../blur/region.ts";
import { sourceName } from "../docu/model.ts";
import { formatClock } from "./model.ts";
import { clampBand, docuDir, sourceCovers, type Logo, type SourceCover } from "./docu_cover.ts";
import {
  changedCuts, changedFlags, changedScenes, cutEnd, cutKindName, cutLength, cutsFrom, edgeRange, firstOfScene, removeCut, replaceFootage,
  setCutCover, slicesRequest, swapCuts, trimEdge, type CutCover, type CutPlan,
} from "./docu_cuts.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export type DocuCutsContext = {
  note: (text: string) => void;
  confirm: (options: { title: string; message: string; confirmLabel: string; cancelLabel: string }) => Promise<boolean>;
  /** Open the rendered film again in the editor, keeping the draft. */
  reload: () => Promise<void>;
  /** Pixels per second of the timeline. */
  pps: () => number;
  /** Seconds into the film → seconds on the timeline (null: cut out of the draft). */
  toTimeline: (filmTime: number) => number | null;
  /** Move the playhead (timeline seconds). */
  seek: (time: number) => void;
  /** Redraw the panel and the timeline. */
  changed: () => void;
  /** Another rebuild of the film is running (the per-source Che). */
  otherBusy: () => boolean;
};

export type DocuCuts = {
  begin(outputPath: string, kind: string): void;
  end(): void;
  /** The lane under Video; returns its height. */
  renderTrack(row: HTMLElement, pad: number): number;
  /** The panel for the selected cut, under Cắt ghép or Che chữ (null: hidden). */
  renderPanel(where: "cut" | "cover" | null): void;
  /** Select the cut on screen at this timeline time (null: none). */
  select(index: number | null): void;
  /** Delete the selected cut (its neighbour takes its time). */
  removeSelected(): void;
  readonly selected: number | null;
  readonly busy: boolean;
  readonly dirty: boolean;
};

const HANDLES: DragHandle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
const FRAME_PX = 480;
const LANE_PX = 26;
const UNDO_LIMIT = 100;

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function button(text: string, className: string, title = ""): HTMLButtonElement {
  const node = element("button", className, text);
  node.type = "button";
  if (title) node.title = title;
  return node;
}

const seconds = (value: number): string => `${value.toFixed(1).replace(".", ",")} giây`;
const percent = (value: number): string => `${Math.round(value * 100)}%`;

export function mountDocuCuts(invoke: Invoke, context: DocuCutsContext): DocuCuts {
  const section = document.querySelector<HTMLElement>("#docu-cut")!;
  let dir = "";
  let ticket = 0;
  let base: CutPlan | null = null;
  let plan: CutPlan | null = null;
  let undo: CutPlan[] = [];
  let sources: SourceCover[] = [];
  let selected: number | null = null;
  let rendering = false;
  let unrendered = false;
  let stateText = "";
  let timer = 0;
  const frames = new Map<string, string | null>();
  let dragging: { kind: "edge" | "move"; index: number; pointer: number; x: number; moved: boolean; preview: number;
    left: number; width: number; before: number } | null = null;

  const dirty = (): boolean => !!plan && !!base && changedScenes(base, plan).length > 0;

  function commit(next: CutPlan | null, failure = ""): void {
    if (!plan) return;
    if (!next) {
      if (failure) context.note(failure);
      return;
    }
    if (next === plan) return;
    undo.push(plan);
    if (undo.length > UNDO_LIMIT) undo.shift();
    plan = next;
    if (selected !== null) selected = Math.min(selected, plan.cuts.length - 1);
    context.changed();
  }

  function sourceCover(source: string): CutCover {
    const found = sources.find((item) => item.id === source);
    return { band: found?.band ? { ...found.band } : null, logos: found ? found.logos.map((logo) => [...logo] as Logo) : [] };
  }

  function sourceVideo(source: string): string {
    return sources.find((item) => item.id === source)?.video ?? "";
  }

  function frameUrl(source: string, time: number): string | null | undefined {
    const duration = plan?.durations[source] ?? 0;
    const video = sourceVideo(source);
    if (!duration || !video) return null;
    const at = Math.round(Math.min(1, Math.max(0, time / duration)) * 1000) / 1000;
    const key = `${source}|${at}`;
    if (frames.has(key)) return frames.get(key);
    // Asked once: null while it comes (or when the frame cannot be read).
    frames.set(key, null);
    const mine = ticket;
    void invoke<ArrayBuffer>("video_frame", { sourcePath: video, position: at, maxWidth: FRAME_PX }).then((bytes) => {
      if (mine !== ticket) return;
      frames.set(key, URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" })));
      context.changed();
    }).catch(() => undefined);
    return undefined;
  }

  // ---------- The lane ----------

  function renderTrack(row: HTMLElement, pad: number): number {
    row.hidden = !plan;
    if (!plan) {
      row.replaceChildren();
      return 0;
    }
    const pps = context.pps();
    const blocks: HTMLElement[] = [];
    const flags = base ? changedFlags(base, plan) : [];
    plan.cuts.forEach((cut, index) => {
      const from = context.toTimeline(cut.at), to = context.toTimeline(cutEnd(plan!, index) - 1e-3);
      if (from === null || to === null || to <= from) return;
      const block = element("div", `tl-cut${index === selected ? " selected" : ""}${cut.kind ? " generated" : ""}${cut.cover ? " covered" : ""}${flags[index] ? " changed" : ""}${firstOfScene(plan!, index) ? " scene-start" : ""}`);
      block.dataset.index = String(index);
      const width = (to - from) * pps;
      Object.assign(block.style, { left: `${pad + from * pps}px`, width: `${Math.max(3, width - 1)}px` });
      const name = cut.kind ? cutKindName(cut.kind) : sourceName(cut.source);
      block.title = `Lát cắt ${index + 1}/${plan!.cuts.length} · ${name}${cut.kind ? "" : ` từ ${formatClock(cut.start, true)}`} · ${seconds(cutLength(plan!, index))}`
        + `${cut.cover ? " · che riêng" : ""}\nBấm để chọn, kéo sang lát khác để đổi chỗ${firstOfScene(plan!, index) ? "" : ", kéo mép trái để cắt"}`;
      if (width >= 22) block.append(element("span", "tl-cut-name", width >= 60 ? `${index + 1} · ${name}` : String(index + 1)));
      if (!firstOfScene(plan!, index) && width >= 10) {
        const edge = element("span", "tl-handle start");
        edge.dataset.edge = "start";
        block.append(edge);
      }
      blocks.push(block);
    });
    row.replaceChildren(...blocks);
    row.style.height = `${LANE_PX}px`;
    return LANE_PX;
  }

  function lane(): HTMLElement | null {
    return document.querySelector<HTMLElement>("#tl-cuts");
  }

  function indexAtX(clientX: number): number | null {
    const row = lane();
    if (!row || !plan) return null;
    for (const block of row.querySelectorAll<HTMLElement>(".tl-cut")) {
      const box = block.getBoundingClientRect();
      if (clientX >= box.left && clientX <= box.right) return Number(block.dataset.index);
    }
    return null;
  }

  function bindLane(): void {
    const row = lane();
    if (!row || row.dataset.bound) return;
    row.dataset.bound = "1";
    row.addEventListener("pointerdown", (event) => {
      const block = (event.target as HTMLElement).closest<HTMLElement>(".tl-cut");
      if (!block || !plan || event.button !== 0 || rendering) return;
      event.preventDefault();
      event.stopPropagation();
      const index = Number(block.dataset.index);
      const edge = (event.target as HTMLElement).dataset.edge === "start";
      try { row.setPointerCapture(event.pointerId); } catch { /* A pointer that already ended. */ }
      const before = row.querySelector<HTMLElement>(`.tl-cut[data-index="${index - 1}"]`);
      dragging = { kind: edge ? "edge" : "move", index, pointer: event.pointerId, x: event.clientX, moved: false, preview: plan.cuts[index].at,
        left: parseFloat(block.style.left), width: parseFloat(block.style.width), before: before ? parseFloat(before.style.width) : 0 };
    });
    row.addEventListener("pointermove", (event) => {
      if (!dragging || event.pointerId !== dragging.pointer || !plan) return;
      const dx = event.clientX - dragging.x;
      if (!dragging.moved && Math.abs(dx) < 4) return;
      dragging.moved = true;
      if (dragging.kind === "edge") {
        const range = edgeRange(plan, dragging.index);
        if (!range) return;
        const at = Math.min(range.max, Math.max(range.min, plan.cuts[dragging.index].at + dx / Math.max(1e-6, context.pps())));
        dragging.preview = at;
        const block = row.querySelector<HTMLElement>(`.tl-cut[data-index="${dragging.index}"]`);
        const before = row.querySelector<HTMLElement>(`.tl-cut[data-index="${dragging.index - 1}"]`);
        const shift = (at - plan.cuts[dragging.index].at) * context.pps();
        if (block) Object.assign(block.style, { left: `${dragging.left + shift}px`, width: `${Math.max(3, dragging.width - shift)}px` });
        if (before) before.style.width = `${Math.max(3, dragging.before + shift)}px`;
        block?.classList.add("dragging");
      } else {
        const target = indexAtX(event.clientX);
        for (const block of row.querySelectorAll<HTMLElement>(".tl-cut.drop")) block.classList.remove("drop");
        row.querySelector<HTMLElement>(`.tl-cut[data-index="${dragging.index}"]`)?.classList.add("dragging");
        if (target !== null && target !== dragging.index) row.querySelector<HTMLElement>(`.tl-cut[data-index="${target}"]`)?.classList.add("drop");
      }
    });
    const finish = (event: PointerEvent, cancelled: boolean): void => {
      if (!dragging || event.pointerId !== dragging.pointer || !plan) return;
      const drag = dragging;
      dragging = null;
      if (cancelled) {
        context.changed();
        return;
      }
      if (!drag.moved) {
        select(drag.index);
        const time = context.toTimeline(plan.cuts[drag.index].at + 0.02);
        if (time !== null) context.seek(time);
        return;
      }
      if (drag.kind === "edge") {
        selected = drag.index;
        commit(trimEdge(plan, drag.index, drag.preview));
        context.changed();
        return;
      }
      const target = indexAtX(event.clientX);
      if (target === null || target === drag.index) {
        context.changed();
        return;
      }
      const next = swapCuts(plan, drag.index, target);
      if (next) selected = target;
      commit(next, "Video nguồn ngắn hơn chỗ của lát cắt kia nên không đổi chỗ được");
      context.changed();
    };
    row.addEventListener("pointerup", (event) => finish(event, false));
    row.addEventListener("pointercancel", (event) => finish(event, true));
  }

  // ---------- The panel ----------

  function renderPanel(where: "cut" | "cover" | null): void {
    const visible = where !== null && !!plan && (selected !== null || dirty() || rendering || unrendered);
    section.hidden = !visible;
    if (!visible || !plan) return;
    // Under Che chữ the panel scrolls inside its pane: the cut goes on top of it, above the per-source cover.
    const home = where === "cover" ? document.querySelector<HTMLElement>("#edit-cover .cover-pane") : document.querySelector<HTMLElement>("#edit-panel-body");
    if (home && section.parentElement !== home) {
      if (where === "cover") home.prepend(section);
      else document.querySelector("#edit-layers")?.after(section);
    }
    const parts: HTMLElement[] = [];
    const head = element("div", "docu-cut-head");
    head.append(element("h3", "edit-label", "Lát cắt phim tài liệu"));
    parts.push(head);
    if (selected !== null && plan.cuts[selected]) parts.push(...cutPanel(selected));
    else parts.push(element("p", "edit-note", "Bấm một lát cắt trên dòng Lát cắt để chỉnh nó."));
    parts.push(...footer());
    section.replaceChildren(...parts);
  }

  function cutPanel(index: number): HTMLElement[] {
    const current = plan!;
    const cut = current.cuts[index];
    const length = cutLength(current, index);
    const parts: HTMLElement[] = [];
    const title = element("div", "docu-cut-title");
    title.append(element("b", "", `Lát cắt ${index + 1}/${current.cuts.length}`),
      element("span", "", `${formatClock(cut.at, true)} – ${formatClock(cut.at + length, true)} · ${seconds(length)}`));
    const close = button("×", "btn ghost small docu-cut-close", "Bỏ chọn lát cắt");
    close.setAttribute("aria-label", "Bỏ chọn lát cắt");
    close.addEventListener("click", () => select(null));
    title.append(close);
    parts.push(title);

    // Footage.
    const stage = element("div", "cover-docu-stage docu-cut-stage");
    const image = element("img", "cover-docu-frame");
    image.alt = `Khung hình lát cắt ${index + 1}`;
    image.draggable = false;
    image.addEventListener("load", () => { if (image.naturalWidth && image.naturalHeight) stage.style.aspectRatio = `${image.naturalWidth} / ${image.naturalHeight}`; });
    const url = cut.kind ? null : frameUrl(cut.source, cut.start + length / 2);
    if (url) image.src = url;
    stage.append(image);
    if (cut.kind) stage.append(element("span", "docu-cut-generated", `${cutKindName(cut.kind)} (vẽ lúc dựng phim)`));
    const cover = cut.cover;
    if (cover && !cut.kind) {
      if (cover.band) stage.append(box(index, "band", { x: 0, y: cover.band.top, w: 1, h: cover.band.bottom - cover.band.top }, "Dải che phụ đề"));
      cover.logos.forEach((logo, at) => stage.append(box(index, at, { x: logo[0], y: logo[1], w: logo[2], h: logo[3] }, `Vùng mờ ${at + 1}`)));
    } else if (!cut.kind) {
      const own = sourceCover(cut.source);
      if (own.band) stage.append(ghost({ x: 0, y: own.band.top, w: 1, h: own.band.bottom - own.band.top }, "Che theo nguồn"));
      own.logos.forEach((logo) => stage.append(ghost({ x: logo[0], y: logo[1], w: logo[2], h: logo[3] }, own.band ? "" : "Che theo nguồn")));
    }
    parts.push(stage);

    const footage = element("div", "docu-cut-fields");
    const pick = element("label", "edit-field");
    pick.append(element("span", "edit-label", "Hình của lát cắt"));
    const select_ = element("select", "docu-cut-source");
    if (cut.kind) {
      const option = element("option", "", cutKindName(cut.kind));
      option.value = "";
      option.selected = true;
      select_.append(option);
    }
    for (const source of Object.keys(current.durations)) {
      const option = element("option", "", `${sourceName(source)} · ${sources.find((item) => item.id === source)?.title ?? ""}`.replace(/ · $/, ""));
      option.value = source;
      option.selected = !cut.kind && source === cut.source;
      option.disabled = current.durations[source] < length - 1e-3;
      select_.append(option);
    }
    select_.addEventListener("change", () => {
      if (!select_.value) return;
      commit(replaceFootage(plan!, index, select_.value, select_.value === cut.source ? cut.start : 0), "Video nguồn này ngắn hơn lát cắt");
    });
    pick.append(select_);
    footage.append(pick);
    if (!cut.kind) {
      const room = Math.max(0, (current.durations[cut.source] ?? 0) - length);
      const field = element("label", "edit-field");
      const value = element("output", "", formatClock(cut.start, true));
      const label = element("span", "edit-label", "Bắt đầu trong video nguồn ");
      label.append(value);
      const slider = element("input", "docu-cut-start");
      Object.assign(slider, { type: "range", min: "0", max: String(room), step: "0.1", value: String(cut.start) });
      slider.setAttribute("aria-label", "Bắt đầu trong video nguồn");
      slider.addEventListener("input", () => { value.textContent = formatClock(Number(slider.value), true); });
      slider.addEventListener("change", () => commit(replaceFootage(plan!, index, cut.source, Number(slider.value))));
      field.append(label, slider);
      footage.append(field);
    }
    parts.push(footage);

    // Place in the film.
    const actions = element("div", "docu-cut-actions");
    const earlier = button("◀ Đổi chỗ với lát trước", "btn small", "Đổi hình của lát này với lát ngay trước nó");
    earlier.disabled = index === 0 || rendering;
    earlier.addEventListener("click", () => {
      const next = swapCuts(plan!, index, index - 1);
      if (next) selected = index - 1;
      commit(next, "Video nguồn ngắn hơn chỗ của lát kia nên không đổi chỗ được");
    });
    const later = button("Đổi chỗ với lát sau ▶", "btn small", "Đổi hình của lát này với lát ngay sau nó");
    later.disabled = index >= current.cuts.length - 1 || rendering;
    later.addEventListener("click", () => {
      const next = swapCuts(plan!, index, index + 1);
      if (next) selected = index + 1;
      commit(next, "Video nguồn ngắn hơn chỗ của lát kia nên không đổi chỗ được");
    });
    const remove = button("Xóa lát cắt", "btn ghost small danger", "Bỏ lát này; lát bên cạnh trong cùng cảnh lấp chỗ của nó");
    remove.disabled = rendering;
    remove.addEventListener("click", () => commit(removeCut(plan!, index), "Cảnh này chỉ còn một lát cắt: hãy đổi hình của nó thay vì xóa"));
    actions.append(earlier, later, remove);
    parts.push(actions);
    const range = edgeRange(current, index);
    parts.push(element("p", "edit-note", range
      ? "Kéo mép trái của lát trên dòng Lát cắt để cắt bớt hoặc kéo dài (lát trước bù lại). Kéo cả lát sang lát khác để đổi chỗ."
      : "Lát này mở đầu một câu đọc nên mép trái giữ nguyên theo lời đọc. Kéo cả lát sang lát khác để đổi chỗ."));

    // Its own Che.
    if (!cut.kind) {
      const coverRow = element("label", "cover-docu-row docu-cut-own");
      const toggle = element("input", "switch");
      toggle.type = "checkbox";
      toggle.setAttribute("role", "switch");
      toggle.checked = !!cover;
      toggle.disabled = rendering;
      toggle.addEventListener("change", () => commit(setCutCover(plan!, index, toggle.checked ? sourceCover(cut.source) : null)));
      coverRow.append(toggle, element("span", "", cover ? "Che riêng cho lát này" : `Che theo ${sourceName(cut.source)} (bật để chỉnh riêng lát này)`));
      parts.push(coverRow);
      if (cover) {
        const rows = element("div", "cover-docu-rows");
        const bandRow = element("label", "cover-docu-row");
        const band = element("input", "switch");
        band.type = "checkbox";
        band.setAttribute("role", "switch");
        band.checked = !!cover.band;
        band.addEventListener("change", () => commit(setCutCover(plan!, index, { ...cover, band: band.checked ? (sourceCover(cut.source).band ?? { top: 0.82, bottom: 0.95 }) : null })));
        bandRow.append(band, element("span", "", cover.band ? `Dải che phụ đề: ${percent(cover.band.top)} – ${percent(cover.band.bottom)} chiều cao` : "Không che dải phụ đề"));
        rows.append(bandRow);
        cover.logos.forEach((logo, at) => {
          const row = element("div", "cover-docu-row");
          row.append(element("span", "", `Vùng mờ ${at + 1} · rộng ${percent(logo[2])} × cao ${percent(logo[3])}`));
          const drop = button("Bỏ", "btn ghost small", "Không làm mờ vùng này ở lát cắt này");
          drop.addEventListener("click", () => commit(setCutCover(plan!, index, { ...cover, logos: cover.logos.filter((_, other) => other !== at) })));
          row.append(drop);
          rows.append(row);
        });
        const add = button("+ Thêm vùng làm mờ", "btn ghost small", "Thêm một ô làm mờ (logo, chữ) cho riêng lát này");
        add.disabled = cover.logos.length >= 16;
        add.addEventListener("click", () => commit(setCutCover(plan!, index, { ...cover, logos: [...cover.logos, [0.7, 0.05, 0.25, 0.12]] })));
        rows.append(add);
        parts.push(rows);
      }
    }
    return parts;
  }

  function ghost(rect: { x: number; y: number; w: number; h: number }, label: string): HTMLElement {
    const node = element("div", "cover-box ghost");
    Object.assign(node.style, { left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%` });
    if (label) node.append(element("span", `cover-box-label${rect.y < 0.08 ? " below" : ""}`, label));
    return node;
  }

  function box(index: number, which: "band" | number, rect: { x: number; y: number; w: number; h: number }, label: string): HTMLElement {
    const node = element("div", `cover-box editable ${which === "band" ? "band" : "logo"}`);
    Object.assign(node.style, { left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%` });
    node.append(element("span", `cover-box-label${rect.y < 0.08 ? " below" : ""}`, label));
    node.dataset.handle = "move";
    node.title = which === "band" ? "Kéo mép trên, mép dưới để đổi cỡ dải che" : "Kéo để di chuyển, kéo các góc để đổi cỡ";
    for (const handle of which === "band" ? (["n", "s"] as DragHandle[]) : HANDLES) {
      const knob = element("span", `cover-handle ${handle}`);
      knob.dataset.handle = handle;
      node.append(knob);
    }
    let drag: { pointer: number; x: number; y: number; handle: DragHandle; start: typeof rect } | null = null;
    let draft: CutCover | null = null;
    node.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || rendering || !plan) return;
      event.preventDefault();
      try { node.setPointerCapture(event.pointerId); } catch { /* A pointer that already ended. */ }
      drag = { pointer: event.pointerId, x: event.clientX, y: event.clientY, handle: ((event.target as HTMLElement).dataset.handle ?? "move") as DragHandle, start: rect };
      draft = structuredClone(plan.cuts[index].cover);
    });
    node.addEventListener("pointermove", (event) => {
      if (!drag || !draft || event.pointerId !== drag.pointer) return;
      const stage = node.parentElement!.getBoundingClientRect();
      const next = dragRegion(drag.start, drag.handle, (event.clientX - drag.x) / Math.max(1, stage.width), (event.clientY - drag.y) / Math.max(1, stage.height));
      if (which === "band") {
        draft.band = clampBand(next.y, next.y + next.h);
        Object.assign(node.style, { top: `${draft.band.top * 100}%`, height: `${(draft.band.bottom - draft.band.top) * 100}%` });
      } else {
        draft.logos[which] = [next.x, next.y, next.w, next.h] as Logo;
        Object.assign(node.style, { left: `${next.x * 100}%`, top: `${next.y * 100}%`, width: `${next.w * 100}%`, height: `${next.h * 100}%` });
      }
    });
    const end = (event: PointerEvent): void => {
      if (!drag || event.pointerId !== drag.pointer) return;
      drag = null;
      if (draft && plan) commit(setCutCover(plan, index, draft));
      draft = null;
    };
    node.addEventListener("pointerup", end);
    node.addEventListener("pointercancel", end);
    return node;
  }

  function footer(): HTMLElement[] {
    const parts: HTMLElement[] = [];
    const count = plan && base ? changedCuts(base, plan) : 0;
    const state = element("p", "edit-note docu-cut-state", stateText || (count ? `${count} lát cắt đã sửa, chưa dựng lại phim.` : unrendered ? "Lát cắt đã lưu nhưng phim chưa dựng lại xong." : ""));
    state.setAttribute("role", "status");
    if (state.textContent) parts.push(state);
    const row = element("div", "docu-cut-foot");
    const back = button("Hoàn tác", "btn ghost small", "Hoàn tác thay đổi lát cắt gần nhất");
    back.disabled = !undo.length || rendering;
    back.addEventListener("click", () => {
      const previous = undo.pop();
      if (!previous) return;
      plan = previous;
      if (selected !== null) selected = Math.min(selected, plan.cuts.length - 1);
      context.changed();
    });
    const reset = button("Bỏ mọi sửa", "btn ghost small", "Trở về lát cắt như lúc mở phim");
    reset.disabled = !dirty() || rendering;
    reset.addEventListener("click", () => {
      if (!base || !plan) return;
      undo.push(plan);
      plan = structuredClone(base);
      context.changed();
    });
    row.append(back, reset);
    parts.push(row);
    const apply = button(rendering ? "Đang dựng lại phim…" : count ? `Dựng lại phim (${count} lát cắt đã sửa)` : "Dựng lại phim", "btn primary block");
    apply.disabled = rendering || (!count && !unrendered) || context.otherBusy();
    apply.addEventListener("click", () => void rebuild());
    parts.push(apply);
    if (rendering) {
      const cancel = button("Hủy dựng lại", "btn ghost block");
      cancel.addEventListener("click", () => {
        cancel.disabled = true;
        stateText = "Đang hủy…";
        void invoke("docu_render_cancel", { dir }).catch((error) => { stateText = String(error).slice(0, 200); context.changed(); });
      });
      parts.push(cancel);
    }
    return parts;
  }

  async function rebuild(): Promise<void> {
    if (!plan || !base || rendering) return;
    const scenes = changedScenes(base, plan);
    if (!scenes.length && !unrendered) return;
    const ok = await context.confirm({
      title: "Dựng lại phim với lát cắt mới?",
      message: "Phim tài liệu sẽ được dựng lại với các lát cắt đã sửa; lát không đổi dùng lại bản đã dựng nên nhanh hơn. Lời đọc giữ nguyên, bản nháp chỉnh sửa (chữ, âm thanh, phụ đề) vẫn được giữ.",
      confirmLabel: "Dựng lại", cancelLabel: "Xem lại",
    });
    if (!ok || !plan || !base) return;
    rendering = true;
    stateText = "Đang lưu lát cắt…";
    context.changed();
    try {
      if (scenes.length) await invoke("docu_slices", { dir, scenes: slicesRequest(plan, scenes) });
      base = structuredClone(plan);
      undo = [];
      unrendered = true;
      await invoke("docu_render", { dir });
      void follow();
    } catch (error) {
      rendering = false;
      stateText = String(error).slice(0, 200);
      context.changed();
    }
  }

  async function follow(): Promise<void> {
    window.clearTimeout(timer);
    const mine = ticket;
    try {
      const info = await invoke<{ status: { state?: string; message?: string; done?: number; total?: number } | null }>("docu_render_status", { dir });
      if (mine !== ticket) return;
      const status = info.status;
      if (status?.state === "RUNNING") {
        const share = status.total ? ` ${Math.floor(((status.done ?? 0) / status.total) * 100)}%` : "";
        stateText = `${status.message || "Đang dựng lại phim…"}${share}`;
        context.changed();
        timer = window.setTimeout(() => void follow(), 1500);
        return;
      }
      rendering = false;
      if (status?.state === "COMPLETED") {
        unrendered = false;
        stateText = "";
        context.note("Đã dựng lại phim với lát cắt mới");
        await context.reload();
      } else {
        stateText = `${status?.message || "Không dựng lại được phim."} Bấm Dựng lại phim để thử lại.`;
        context.changed();
      }
    } catch (error) {
      rendering = false;
      stateText = String(error).slice(0, 200);
      context.changed();
    }
  }

  async function load(path: string): Promise<void> {
    const mine = ++ticket;
    dir = docuDir(path);
    try {
      const [open, status] = await Promise.all([invoke<unknown>("docu_open", { dir }), invoke<unknown>("docu_plan_status", { dir })]);
      if (mine !== ticket) return;
      base = cutsFrom(status);
      plan = base ? structuredClone(base) : null;
      sources = sourceCovers(open, status);
      // A render started here and still running (the editor was reopened): follow it.
      const render = await invoke<{ status: { state?: string } | null }>("docu_render_status", { dir }).catch(() => null);
      if (mine !== ticket) return;
      if (render?.status?.state === "RUNNING" && plan) {
        rendering = true;
        void follow();
      }
    } catch {
      base = null;
      plan = null;
      sources = [];
    }
    bindLane();
    context.changed();
  }

  function select(index: number | null): void {
    selected = plan && index !== null && plan.cuts[index] ? index : null;
    context.changed();
  }

  return {
    get selected() { return selected; },
    get busy() { return rendering; },
    get dirty() { return dirty(); },
    begin(outputPath, kind) {
      window.clearTimeout(timer);
      rendering = false;
      unrendered = false;
      stateText = "";
      selected = null;
      undo = [];
      base = null;
      plan = null;
      if (kind !== "docu") {
        ticket += 1;
        section.hidden = true;
        return;
      }
      void load(outputPath);
    },
    end() {
      ticket += 1;
      window.clearTimeout(timer);
      section.hidden = true;
      section.replaceChildren();
      base = null;
      plan = null;
      selected = null;
      undo = [];
      for (const url of frames.values()) if (url) URL.revokeObjectURL(url);
      frames.clear();
    },
    renderTrack,
    renderPanel,
    select,
    removeSelected() {
      if (!plan || selected === null || rendering) return;
      commit(removeCut(plan, selected), "Cảnh này chỉ còn một lát cắt: hãy đổi hình của nó thay vì xóa");
    },
  };

}
