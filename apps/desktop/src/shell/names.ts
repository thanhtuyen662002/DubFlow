/**
 * Đổi tên: the user's own short names for videos and projects, shown instead of the
 * long original name (often a Chinese title or a file name). Display only: files on
 * disk keep their names. Remembered on this computer, keyed by the original name.
 */
const STORAGE_KEY = "dubflow.names.v1";
const PENCIL = `<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>`;

let names: Record<string, string> | null = null;
const watchers: Array<() => void> = [];

function load(): Record<string, string> {
  if (names) return names;
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "{}") as unknown;
    names = parsed && typeof parsed === "object" ? (parsed as Record<string, string>) : {};
  } catch {
    names = {};
  }
  return names;
}

/** The user's name for `original`, or `original` itself. */
export function shownName(original: string): string {
  return load()[original.trim()] || original;
}

export function setName(original: string, name: string): void {
  const map = load();
  const key = original.trim();
  const value = name.trim();
  if (!value || value === key) delete map[key];
  else map[key] = value.slice(0, 200);
  try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(map)); } catch { /* this session only */ }
  for (const watch of watchers) watch();
}

/** Show the user's name on `el` (keeps the original in a tooltip). Safe to call again. */
function present(el: HTMLElement): void {
  if (el.querySelector("input")) return;
  const text = (el.textContent ?? "").trim();
  // Text we did not write means the page put a (new) original name there.
  if (el.dataset.origName === undefined || text !== el.dataset.shown) el.dataset.origName = text;
  const original = el.dataset.origName;
  if (!original) return;
  const shown = shownName(original);
  if (el.textContent !== shown) el.textContent = shown;
  el.dataset.shown = shown;
  if (shown !== original) el.title = `Tên gốc: ${original}`;
  else el.removeAttribute("title");
}

/** Swap the title for a text box; Enter saves, Esc cancels, empty goes back to the original. */
export function startRename(el: HTMLElement): void {
  present(el);
  const original = el.dataset.origName;
  if (!original || el.querySelector("input")) return;
  const input = document.createElement("input");
  input.className = "rename-input";
  input.value = shownName(original);
  input.setAttribute("aria-label", "Tên mới");
  input.placeholder = original;
  let done = false;
  const finish = (save: boolean) => {
    if (done) return;
    done = true;
    input.remove();
    el.textContent = el.dataset.shown ?? original;
    if (save) setName(original, input.value);
    present(el);
  };
  for (const type of ["click", "mousedown", "dblclick", "keyup"]) input.addEventListener(type, (event) => event.stopPropagation());
  input.addEventListener("keydown", (event) => {
    event.stopPropagation();
    if (event.key === "Enter") finish(true);
    if (event.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
  el.textContent = "";
  el.append(input);
  input.focus();
  input.select();
}

/**
 * Every row of `list` gets its title shown with the user's name, a pencil button
 * and double-click to rename. Rows re-rendered by the page are picked up again.
 */
export function mountRenames(list: HTMLElement | null, titleSelector = ".job-title"): void {
  if (!list) return;
  const apply = () => {
    for (const title of list.querySelectorAll<HTMLElement>(titleSelector)) {
      present(title);
      const row = title.closest<HTMLElement>("li, .el-card") ?? title.parentElement!;
      if (row.querySelector(":scope > .rename-btn")) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "icon-btn rename-btn";
      button.title = "Đổi tên";
      button.setAttribute("aria-label", "Đổi tên");
      button.innerHTML = PENCIL;
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        startRename(title);
      });
      title.addEventListener("dblclick", (event) => {
        event.stopPropagation();
        startRename(title);
      });
      row.append(button);
    }
  };
  new MutationObserver(apply).observe(list, { childList: true, subtree: true });
  watchers.push(apply);
  apply();
}

/** A heading that names the open video: shows the user's name and follows renames. */
export function mountNamedHeading(el: HTMLElement | null): void {
  if (!el) return;
  const apply = () => present(el);
  new MutationObserver(apply).observe(el, { childList: true, characterData: true, subtree: true });
  watchers.push(apply);
  apply();
}
