/**
 * "Chọn từ Hàng đợi": Lồng tiếng, Bán hàng and Ghép video take their videos
 * from the Hàng đợi list through this dialog.
 */
import { filterMedia, originLabel, type MediaEntry } from "./model.ts";

export type PickOptions = {
  title: string;
  hint: string;
  confirmLabel: string;
  entries: readonly MediaEntry[];
  /** Why a video cannot be chosen here (e.g. not dubbed yet), or "" when it can. */
  blocked?(entry: MediaEntry): string;
  /** A small mark after the name ("Đã lồng tiếng"). */
  note?(entry: MediaEntry): string;
  /** Already in use on that page: shown ticked and locked. */
  taken?(entry: MediaEntry): boolean;
  thumb?(entry: MediaEntry): HTMLElement;
  max?: number;
  /** Shown when the list is empty. */
  empty: string;
  /** "Mở Hàng đợi" in the empty state. */
  openQueue?(): void;
};

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

export function pickFromMedia(options: PickOptions): Promise<MediaEntry[]> {
  const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const backdrop = element("div", "confirm-backdrop");
  const panel = element("div", "confirm-panel media-picker");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  panel.setAttribute("aria-label", options.title);
  const head = element("div", "media-picker-head");
  head.append(element("h2", "", options.title), element("p", "hint", options.hint));
  const search = element("input", "media-picker-search");
  search.type = "search";
  search.placeholder = "Tìm video";
  search.setAttribute("aria-label", "Tìm video");
  const list = element("ul", "media-picker-list");
  const empty = element("p", "queue-empty");
  const actions = element("div", "confirm-actions");
  const count = element("span", "hint media-picker-count");
  const cancel = element("button", "btn", "Hủy");
  cancel.type = "button";
  const confirm = element("button", "btn primary", options.confirmLabel);
  confirm.type = "button";
  actions.append(count, cancel, confirm);
  panel.append(head, search, list, empty, actions);
  backdrop.append(panel);

  const chosen = new Set<string>();
  const max = options.max ?? Infinity;

  function render(): void {
    const shown = filterMedia(options.entries, "all", search.value);
    list.replaceChildren(...shown.map((entry) => {
      const blocked = options.blocked?.(entry) ?? "";
      const taken = options.taken?.(entry) ?? false;
      const item = element("li");
      const label = element("label", `media-pick${blocked ? " blocked" : ""}`);
      const check = element("input");
      check.type = "checkbox";
      check.checked = taken || chosen.has(entry.id);
      check.disabled = Boolean(blocked) || taken || (!chosen.has(entry.id) && chosen.size >= max);
      check.addEventListener("change", () => {
        if (check.checked) chosen.add(entry.id); else chosen.delete(entry.id);
        render();
      });
      const text = element("span", "media-pick-text");
      text.append(element("span", "media-pick-name", entry.title));
      const meta = element("span", "media-pick-meta");
      meta.append(element("span", "mini-tag muted", originLabel(entry)));
      const note = taken ? "Đã có ở đây" : options.note?.(entry) ?? "";
      if (note) meta.append(element("span", "mini-tag", note));
      if (blocked) meta.append(element("span", "hint", blocked));
      text.append(meta);
      label.title = entry.path;
      label.append(check);
      if (options.thumb) label.append(options.thumb(entry));
      label.append(text);
      item.append(label);
      return item;
    }));
    empty.hidden = shown.length > 0;
    empty.replaceChildren(options.entries.length ? "Không có video khớp." : options.empty);
    if (!options.entries.length && options.openQueue) {
      const open = element("button", "link", "Mở Hàng đợi");
      open.type = "button";
      open.addEventListener("click", () => { finish([]); options.openQueue?.(); });
      empty.append(" ", open);
    }
    count.textContent = chosen.size ? `Đã chọn ${chosen.size}` : "";
    confirm.disabled = chosen.size === 0;
  }

  let resolve: (value: MediaEntry[]) => void = () => undefined;
  const onKey = (event: KeyboardEvent): void => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); finish([]); }
  };
  function finish(value: MediaEntry[]): void {
    document.removeEventListener("keydown", onKey, true);
    backdrop.remove();
    previous?.focus();
    resolve(value);
  }
  cancel.addEventListener("click", () => finish([]));
  confirm.addEventListener("click", () => finish(options.entries.filter((entry) => chosen.has(entry.id))));
  backdrop.addEventListener("click", (event) => { if (event.target === backdrop) finish([]); });
  search.addEventListener("input", render);
  document.addEventListener("keydown", onKey, true);
  render();
  document.body.append(backdrop);
  search.focus();
  return new Promise((done) => { resolve = done; });
}
