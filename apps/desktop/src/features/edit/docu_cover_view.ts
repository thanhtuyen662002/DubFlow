/**
 * Che › "Che có sẵn trong phim" for a documentary (Issue #520): each source's
 * burned-in caption band and logo boxes on a frame of that source, resized by
 * dragging (or removed), then the film is rendered again with them.
 */
import { dragRegion, type DragHandle } from "../blur/region.ts";
import { sourceName } from "../docu/model.ts";
import { applyButton, clampBand, coverRequest, docuDir, sameCover, sourceCovers, type Logo, type SourceCover } from "./docu_cover.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export type DocuCoverContext = {
  /** Show a short line under the player. */
  note: (text: string) => void;
  /** Open the rendered film again in the editor, keeping the draft. */
  reload: () => Promise<void>;
  confirm: (options: { title: string; message: string; confirmLabel: string; cancelLabel: string }) => Promise<boolean>;
};

export type DocuCover = {
  begin(outputPath: string, kind: string): void;
  end(): void;
  /** A render is running or a change is not rendered yet. */
  readonly busy: boolean;
  readonly dirty: boolean;
};

const HANDLES: DragHandle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
const FRAME_PX = 480;

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

const percent = (value: number): string => `${Math.round(value * 100)}%`;

export function mountDocuCover(invoke: Invoke, context: DocuCoverContext): DocuCover {
  const section = document.querySelector<HTMLElement>("#cover-docu")!;
  const list = document.querySelector<HTMLElement>("#cover-docu-list")!;
  const state = document.querySelector<HTMLElement>("#cover-docu-state")!;
  const apply = document.querySelector<HTMLButtonElement>("#cover-docu-apply")!;
  let dir = "";
  let ticket = 0;
  let base: SourceCover[] = [];
  let covers: SourceCover[] = [];
  let frames = new Map<string, string>();
  let rendering = false;
  /** Cover edits saved for a render that has not completed (it failed or was cancelled). */
  let unrendered = false;
  let timer = 0;

  const changed = (): SourceCover[] => covers.filter((cover, index) => !sameCover(cover, base[index]));

  function renderFoot(): void {
    const button = applyButton(changed().length, unrendered, rendering);
    apply.disabled = !button.enabled;
    apply.textContent = button.label;
    if (!rendering && button.state) state.textContent = button.state;
  }

  function card(cover: SourceCover, index: number): HTMLElement {
    const item = element("article", "cover-docu-card");
    const head = element("div", "cover-docu-head");
    head.append(element("b", "", sourceName(cover.id)), element("span", "cover-docu-title", cover.title));
    const stage = element("div", "cover-docu-stage");
    const image = element("img", "cover-docu-frame");
    image.alt = `Khung hình của ${sourceName(cover.id)}`;
    image.draggable = false;
    const url = frames.get(cover.id);
    // The boxes are fractions of the source frame: the stage takes its shape.
    image.addEventListener("load", () => { if (image.naturalWidth && image.naturalHeight) stage.style.aspectRatio = `${image.naturalWidth} / ${image.naturalHeight}`; });
    if (url) image.src = url;
    stage.append(image);
    if (cover.band) stage.append(box(index, "band", { x: 0, y: cover.band.top, w: 1, h: cover.band.bottom - cover.band.top }, "Dải che phụ đề"));
    cover.logos.forEach((logo, at) => stage.append(box(index, at, { x: logo[0], y: logo[1], w: logo[2], h: logo[3] }, `Logo ${at + 1}`)));
    const rows = element("div", "cover-docu-rows");
    const bandRow = element("label", "cover-docu-row");
    const toggle = element("input", "switch");
    toggle.type = "checkbox";
    toggle.setAttribute("role", "switch");
    toggle.checked = !!cover.band;
    toggle.addEventListener("change", () => {
      cover.band = toggle.checked ? (base[index].band ?? { top: 0.82, bottom: 0.95 }) : null;
      render();
    });
    bandRow.append(toggle, element("span", "", cover.band
      ? `Dải che phụ đề: ${percent(cover.band.top)} – ${percent(cover.band.bottom)} chiều cao` : "Không che dải phụ đề"));
    rows.append(bandRow);
    cover.logos.forEach((logo, at) => {
      const row = element("div", "cover-docu-row");
      row.append(element("span", "", `Logo ${at + 1} được làm mờ · rộng ${percent(logo[2])} × cao ${percent(logo[3])}`));
      const remove = element("button", "btn ghost small", "Bỏ");
      remove.type = "button";
      remove.title = "Không làm mờ vùng này nữa";
      remove.addEventListener("click", () => { cover.logos.splice(at, 1); render(); });
      row.append(remove);
      rows.append(row);
    });
    if (!sameCover(cover, base[index])) {
      const reset = element("button", "btn ghost small cover-docu-reset", "Hoàn tác nguồn này");
      reset.type = "button";
      reset.addEventListener("click", () => { covers[index] = structuredClone(base[index]); render(); });
      rows.append(reset);
    }
    item.append(head, stage, rows);
    return item;
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
    node.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || rendering) return;
      event.preventDefault();
      try { node.setPointerCapture(event.pointerId); } catch { /* A pointer that already ended. */ }
      drag = { pointer: event.pointerId, x: event.clientX, y: event.clientY, handle: ((event.target as HTMLElement).dataset.handle ?? "move") as DragHandle, start: rect };
    });
    node.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.pointer) return;
      const stage = node.parentElement!.getBoundingClientRect();
      const next = dragRegion(drag.start, drag.handle, (event.clientX - drag.x) / Math.max(1, stage.width), (event.clientY - drag.y) / Math.max(1, stage.height));
      const cover = covers[index];
      if (which === "band") {
        cover.band = clampBand(next.y, next.y + next.h);
        Object.assign(node.style, { top: `${cover.band.top * 100}%`, height: `${(cover.band.bottom - cover.band.top) * 100}%` });
      } else {
        cover.logos[which] = [next.x, next.y, next.w, next.h] as Logo;
        Object.assign(node.style, { left: `${next.x * 100}%`, top: `${next.y * 100}%`, width: `${next.w * 100}%`, height: `${next.h * 100}%` });
      }
    });
    const end = (event: PointerEvent): void => {
      if (!drag || event.pointerId !== drag.pointer) return;
      drag = null;
      render();
    };
    node.addEventListener("pointerup", end);
    node.addEventListener("pointercancel", end);
    return node;
  }

  function render(): void {
    list.replaceChildren(...covers.map(card));
    renderFoot();
  }

  async function load(path: string): Promise<void> {
    const mine = ++ticket;
    dir = docuDir(path);
    section.hidden = true;
    try {
      const [open, plan] = await Promise.all([invoke<unknown>("docu_open", { dir }), invoke<unknown>("docu_plan_status", { dir })]);
      if (mine !== ticket) return;
      base = sourceCovers(open, plan);
      covers = structuredClone(base);
    } catch {
      // A rough cut moved away from its project: nothing burned in can be changed here.
      base = [];
      covers = [];
    }
    section.hidden = !covers.length;
    render();
    for (const cover of covers) {
      if (frames.has(cover.id)) continue;
      void invoke<ArrayBuffer>("video_frame", { sourcePath: cover.video, position: 0.4, maxWidth: FRAME_PX }).then((bytes) => {
        if (mine !== ticket) return;
        frames.set(cover.id, URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" })));
        render();
      }).catch(() => undefined);
    }
  }

  async function follow(): Promise<void> {
    window.clearTimeout(timer);
    const mine = ticket;
    try {
      const info = await invoke<{ video: string | null; status: { state?: string; message?: string; done?: number; total?: number } | null }>("docu_render_status", { dir });
      if (mine !== ticket) return;
      const status = info.status;
      if (status?.state === "RUNNING") {
        const share = status.total ? ` ${Math.floor(((status.done ?? 0) / status.total) * 100)}%` : "";
        state.textContent = `${status.message || "Đang dựng lại phim…"}${share}`;
        timer = window.setTimeout(() => void follow(), 1500);
        return;
      }
      rendering = false;
      if (status?.state === "COMPLETED") {
        unrendered = false;
        state.textContent = "Đã dựng lại phim với vùng che mới.";
        context.note("Đã dựng lại phim với vùng che mới");
        await context.reload();
      } else {
        renderFoot();
        state.textContent = `${status?.message || "Không dựng lại được phim."} Bấm Dựng lại phim để thử lại.`;
      }
    } catch (error) {
      rendering = false;
      renderFoot();
      state.textContent = String(error).slice(0, 200);
    }
  }

  apply.addEventListener("click", async () => {
    const edits = changed();
    if ((!edits.length && !unrendered) || rendering) return;
    const ok = await context.confirm({
      title: "Dựng lại phim với vùng che mới?",
      message: "Phim tài liệu sẽ được dựng lại từ các nguồn với dải che và vùng logo mới, thường mất vài phút. Bản nháp chỉnh sửa (cắt ghép, chữ, âm thanh) vẫn được giữ.",
      confirmLabel: "Dựng lại", cancelLabel: "Xem lại",
    });
    if (!ok) return;
    rendering = true;
    renderFoot();
    state.textContent = "Đang lưu vùng che…";
    try {
      for (const cover of edits) await invoke("docu_cover", coverRequest(dir, cover));
      base = structuredClone(covers);
      // Saved: until the render completes, the button offers to render them again.
      unrendered = true;
      await invoke("docu_render", { dir });
      void follow();
    } catch (error) {
      rendering = false;
      renderFoot();
      state.textContent = String(error).slice(0, 200);
    }
  });

  return {
    get busy() { return rendering; },
    get dirty() { return changed().length > 0; },
    begin(outputPath, kind) {
      window.clearTimeout(timer);
      rendering = false;
      unrendered = false;
      state.textContent = "";
      if (kind !== "docu") {
        ticket += 1;
        section.hidden = true;
        base = [];
        covers = [];
        list.replaceChildren();
        return;
      }
      void load(outputPath);
    },
    end() {
      ticket += 1;
      window.clearTimeout(timer);
      section.hidden = true;
      list.replaceChildren();
      base = [];
      covers = [];
      for (const url of frames.values()) URL.revokeObjectURL(url);
      frames = new Map();
    },
  };
}
