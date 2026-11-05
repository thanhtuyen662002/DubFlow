/**
 * "Tách lớp để chỉnh" for a documentary in Chỉnh sửa (Issue #525): the film
 * is rendered again with a clean picture, then reopened with its number
 * cards, captions and music bed as layers of the draft.
 */
import { parseOpen, type OpenDocuRun } from "../docu/model.ts";
import { burned, burnedWords, layersFrom, type Layers, type Split } from "./docu_layers.ts";
import { docuDir } from "./docu_cover.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export type DocuLayersContext = {
  note: (text: string) => void;
  confirm: (options: { title: string; message: string; confirmLabel: string; cancelLabel: string }) => Promise<boolean>;
  /** Open the rendered film again and put the layers into its draft. */
  reload: (layers: Layers) => Promise<void>;
};

export type DocuLayers = {
  begin(outputPath: string, kind: string): void;
  end(): void;
  readonly busy: boolean;
};

export function mountDocuLayers(invoke: Invoke, context: DocuLayersContext): DocuLayers {
  const section = document.querySelector<HTMLElement>("#edit-layers")!;
  const text = document.querySelector<HTMLElement>("#edit-layers-text")!;
  const state = document.querySelector<HTMLElement>("#edit-layers-state")!;
  const button = document.querySelector<HTMLButtonElement>("#edit-layers-split")!;
  const cancel = document.querySelector<HTMLButtonElement>("#edit-layers-cancel")!;
  let dir = "";
  let ticket = 0;
  let open: OpenDocuRun | null = null;
  let split: Split | null = null;
  let rendering = false;
  let timer = 0;

  function render(): void {
    const value = burned(open);
    section.hidden = !value && !rendering;
    if (value) text.textContent = `Phim đang in sẵn ${burnedWords(value)} nên chưa sửa riêng được. Tách thành lớp để sửa chữ số liệu, phụ đề và nhạc nền ngay trong Chỉnh sửa.`;
    button.disabled = rendering;
    button.textContent = rendering ? "Đang dựng lại phim…" : "Tách lớp để chỉnh";
    cancel.hidden = !rendering;
    cancel.disabled = false;
  }

  async function load(path: string): Promise<void> {
    const mine = ++ticket;
    dir = docuDir(path);
    open = null;
    render();
    try {
      const value = parseOpen(await invoke<unknown>("docu_open", { dir }));
      if (mine !== ticket) return;
      open = value;
    } catch {
      // A rough cut moved away from its project: it has no layers to split.
      open = null;
    }
    render();
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
        state.textContent = `${status.message || "Đang dựng lại phim…"}${share}`;
        timer = window.setTimeout(() => void follow(), 1500);
        return;
      }
      rendering = false;
      if (status?.state === "COMPLETED" && split) {
        state.textContent = "Đã tách lớp.";
        context.note("Đã tách số liệu, phụ đề và nhạc nền thành lớp riêng");
        await context.reload(layersFrom(open, split));
      } else {
        state.textContent = status?.message || "Không dựng lại được phim.";
        render();
        // The film was not replaced: give it back its cards, captions and music for later renders.
        if (split) {
          const { graphics, captions, music, music_path } = split;
          split = null;
          await invoke("docu_render_options", { dir, graphics, captions, music, musicPath: music_path ?? null }).catch(() => undefined);
        }
      }
    } catch (error) {
      rendering = false;
      state.textContent = String(error).slice(0, 200);
      render();
    }
  }

  button.addEventListener("click", async () => {
    const value = burned(open);
    if (!value || rendering) return;
    const ok = await context.confirm({
      title: "Tách lớp để chỉnh?",
      message: `Phim tài liệu sẽ được dựng lại với hình sạch, rồi ${burnedWords(value)} được đặt lại thành lớp riêng (Chữ, Phụ đề, Âm thanh) trong bản nháp để sửa. Thường mất vài phút; cắt ghép đã làm vẫn được giữ.`,
      confirmLabel: "Tách lớp", cancelLabel: "Để sau",
    });
    if (!ok) return;
    rendering = true;
    state.textContent = "Đang chuẩn bị…";
    render();
    try {
      split = await invoke<Split>("docu_layers", { dir });
      void follow();
    } catch (error) {
      rendering = false;
      state.textContent = String(error).slice(0, 200);
      render();
    }
  });

  cancel.addEventListener("click", () => {
    if (!rendering) return;
    cancel.disabled = true;
    state.textContent = "Đang hủy…";
    void invoke("docu_render_cancel", { dir }).catch((error) => {
      cancel.disabled = false;
      state.textContent = String(error).slice(0, 200);
    });
  });

  return {
    get busy() { return rendering; },
    begin(outputPath, kind) {
      window.clearTimeout(timer);
      rendering = false;
      split = null;
      state.textContent = "";
      if (kind !== "docu") {
        ticket += 1;
        open = null;
        section.hidden = true;
        return;
      }
      void load(outputPath);
    },
    end() {
      ticket += 1;
      window.clearTimeout(timer);
      rendering = false;
      open = null;
      section.hidden = true;
    },
  };
}
