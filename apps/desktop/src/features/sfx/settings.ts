/** Cài đặt rows for Âm thanh (Issue #420): the sound-effect pack and the user's sound folder. */
import { packSummary, type SfxLibrary } from "./library.ts";

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing settings element: ${selector}`);
  return node;
};

export function mountSfxSettings(library: SfxLibrary, showError: (text: string) => void): void {
  const state = $("#sfx-pack-state");
  const get = $<HTMLButtonElement>("#sfx-pack-get");
  const folderLabel = $("#audio-folder-label");
  const reset = $<HTMLButtonElement>("#reset-audio-folder");

  function render(): void {
    const status = library.status();
    const busy = status?.state === "downloading" || status?.state === "installing";
    state.textContent = packSummary(status);
    get.hidden = !!status?.installed;
    get.disabled = busy || !status;
    get.textContent = status?.state === "failed" ? "Thử lại" : busy ? "Đang tải…" : "Tải về";
    const folder = library.folder();
    folderLabel.textContent = folder ?? "Chưa chọn";
    folderLabel.title = folder ?? "";
    reset.hidden = !folder;
  }

  get.addEventListener("click", () => void library.install());
  $("#choose-audio-folder").addEventListener("click", async () => {
    try {
      await library.pickFolder();
    } catch (error) {
      showError(`Không mở được hộp thoại chọn thư mục: ${String(error).slice(0, 160)}`);
    }
  });
  reset.addEventListener("click", () => library.setFolder(null));
  library.subscribe(render);
  render();
}
