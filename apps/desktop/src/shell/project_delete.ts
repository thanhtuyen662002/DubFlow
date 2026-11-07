import { confirmDialog } from "./confirm.ts";
import { showToast } from "./toast.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

/** The project pages whose run folders `project_delete` removes. */
export type ProjectKind = "docu" | "compilation" | "product";

const TRASH_ICON = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>';

/** What the confirmation says is removed, by page. */
const CONTENTS: Record<ProjectKind, string> = {
  docu: "kịch bản, lời đọc, kế hoạch hình ảnh và bản dựng",
  compilation: "video tổng hợp, mô tả và các tệp làm việc",
  product: "mọi video của lần tạo này và các tệp làm việc",
};

export function deleteMessage(kind: ProjectKind): string {
  return `Cả thư mục dự án (${CONTENTS[kind]}) sẽ được chuyển vào Thùng rác của Windows. Video và ảnh gốc bạn đã chọn không bị xóa.`;
}

/** The run folder a project video sits in (its parent folder). */
export function runFolder(path: string): string {
  return path.replace(/[\\/][^\\/]*$/, "");
}

/** A small trash button for a project row; it never opens the row it sits on. */
export function deleteProjectButton(title: string, onClick: () => void): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon-btn project-delete";
  button.title = "Xóa dự án";
  button.setAttribute("aria-label", `Xóa dự án “${title}”`);
  button.innerHTML = TRASH_ICON;
  button.addEventListener("click", event => { event.stopPropagation(); onClick(); });
  button.addEventListener("keydown", event => event.stopPropagation());
  return button;
}

/** Ask, then delete; the outcome shows as a toast. True when the project was deleted. */
export async function confirmDeleteProject(invoke: Invoke, kind: ProjectKind, dir: string, title: string): Promise<boolean> {
  const confirmed = await confirmDialog({ title: `Xóa dự án “${title}”?`, message: deleteMessage(kind),
    confirmLabel: "Xóa dự án", cancelLabel: "Giữ lại", danger: true });
  if (!confirmed) return false;
  try {
    await invoke("project_delete", { kind, dir });
    showToast(`Đã chuyển “${title}” vào Thùng rác.`);
    return true;
  } catch (error) {
    showToast(error instanceof Error ? error.message : String(error || "Không xóa được dự án"), "err");
    return false;
  }
}
