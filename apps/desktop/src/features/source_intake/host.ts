import { submitSources, type SourceReply } from "./controller.ts";
type Invoke = (command: string, args: { items: Array<Record<string, string>> }) => Promise<SourceReply>;

/** Scoped source UI: never disables the local picker or processing controls. */
export function connectSourceIntake(root: ParentNode, addPaths: (paths: string[]) => unknown, invoke: Invoke): void {
  const button = root.querySelector<HTMLButtonElement>("#add-sources");
  const input = root.querySelector<HTMLTextAreaElement>("#source-input");
  const state = root.querySelector<HTMLElement>("#source-intake-state");
  if (!button || !input || !state) throw new Error("SOURCE_INTAKE_ELEMENTS_MISSING");
  const entries = root.querySelector<HTMLElement>("#source-intake-errors");
  button.addEventListener("click", async () => {
    const submitted = input.value;
    input.value = "";
    state.textContent = "Đang tiếp nhận nguồn; vẫn có thể thêm và xử lý tệp cục bộ.";
    try {
      const result = await submitSources(submitted, { addLocal: addPaths, enqueue: (items) => invoke("enqueue_sources", { items }) });
      state.textContent = `Đã thêm ${result.local} tệp cục bộ; đã tiếp nhận ${result.online} URL; ${result.rejected.length} mục cần kiểm tra.`;
      if (entries) entries.replaceChildren(...result.rejected.map((failure) => {
        const item = document.createElement("li");
        item.textContent = `${failure.input}: ${failure.message} (${failure.code})`;
        return item;
      }));
    } catch {
      state.textContent = "Không tiếp nhận được danh sách. Tệp cục bộ đã có vẫn dùng được.";
    }
  });
}
