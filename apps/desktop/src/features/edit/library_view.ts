import {
  FILTERS, KIND_LABELS, distinctTitles, ageGroup, countByKind, filterEntries, folderOf, forgetFile, isVideoPath, mergeEntries, parseLibrary,
  parseRecent, rememberFile, sizeLabel, fileTitle, type LibraryEntry, type LibraryFilter,
} from "./library.ts";
import { shortDate } from "../product/model.ts";
import { confirmDeleteProject, runFolder } from "../../shell/project_delete.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export type EditLibraryHost = {
  /** Finished queue videos. */
  jobs: () => LibraryEntry[];
  /** Folders whose "DubFlow Output" holds the other pages' videos. */
  roots: () => string[];
  /** Open a video in the editor; false when it did not open. */
  open: (entry: LibraryEntry) => Promise<boolean>;
  showError: (text: string) => void;
};

export type EditLibrary = {
  show(): void;
  /** Videos dropped on the window while the screen is shown: the first opens. */
  drop(paths: string[]): void;
  /** Remember a video opened from the computer. */
  remember(path: string, title?: string): void;
  /** The other pages' finished videos last found (Chèn › Từ DubFlow offers them too). */
  found(): readonly LibraryEntry[];
};

const RECENT_KEY = "dubflow.edit.recentFiles";
const FILTER_KEY = "dubflow.edit.libraryFilter";
const THUMB_PX = 384;
const THUMB_WORKERS = 3;
const GROUP_FROM = 12;
const ICON_PLUS = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
const ICON_SEARCH = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M11 4.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM16 16l4 4" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" fill="none"/></svg>';
const ICON_EDIT = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M4 20h4L18.5 9.5a2.1 2.1 0 0 0-3-3L5 17v3M13.5 6.5l3 3" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>';
const ICON_TRASH = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>';
const ICON_FILM = '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><rect x="3.5" y="5" width="17" height="14" rx="2.5" stroke="currentColor" stroke-width="1.5" fill="none"/><path d="M10 9.5v5l4.5-2.5z" fill="currentColor"/></svg>';

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // A full or blocked storage only forgets the list.
  }
}

/**
 * The Chỉnh sửa page (Issue #497): pick a finished video or one on the
 * computer, then edit it. Filters by where the video came from, searches
 * titles and paths, and shows a frame of each video.
 */
export function mountEditLibrary(invoke: Invoke, host: EditLibraryHost): EditLibrary {
  const root = document.querySelector<HTMLElement>("#edit-view")!;
  root.innerHTML = `
    <div class="el-toolbar">
      <div class="seg el-filters" role="tablist" aria-label="Nguồn video"></div>
      <div class="el-actions">
      <label class="el-search">
        ${ICON_SEARCH}
        <input id="el-search" type="search" placeholder="Tìm video" autocomplete="off" spellcheck="false" aria-label="Tìm video">
      </label>
      <button id="el-pick" class="btn primary" type="button">${ICON_PLUS}<span>Mở video trên máy</span></button>
      </div>
    </div>
    <div id="el-body" class="el-body" aria-live="polite"></div>
`;
  const filtersBox = root.querySelector<HTMLElement>(".el-filters")!;
  const search = root.querySelector<HTMLInputElement>("#el-search")!;
  const body = root.querySelector<HTMLElement>("#el-body")!;
  const pickButton = root.querySelector<HTMLButtonElement>("#el-pick")!;

  let recent = parseRecent(readStorage(RECENT_KEY));
  let found: LibraryEntry[] = [];
  let loaded = false;
  let loading = false;
  let loadTicket = 0;
  let opening: string | null = null;
  let filter: LibraryFilter = FILTERS.some((item) => item.id === readStorage(FILTER_KEY)) ? readStorage(FILTER_KEY) as LibraryFilter : "all";

  const thumbs = new Map<string, string | null>();
  const waiting: Array<{ path: string; image: HTMLElement }> = [];
  let workers = 0;
  const observer = typeof IntersectionObserver === "function" ? new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      observer?.unobserve(entry.target);
      const image = entry.target as HTMLElement;
      if (image.dataset.path) queueThumb(image.dataset.path, image);
    }
  }, { root: body, rootMargin: "200px" }) : null;

  function paintThumb(image: HTMLElement, url: string | null | undefined): void {
    if (url === undefined) return;
    image.classList.toggle("none", url === null);
    image.classList.remove("loading");
    // A vertical video sits on a blurred copy of itself instead of black bars.
    if (url) image.style.setProperty("--thumb", `url("${url}")`);
  }

  function queueThumb(path: string, image: HTMLElement): void {
    if (thumbs.has(path)) return paintThumb(image, thumbs.get(path));
    waiting.push({ path, image });
    pump();
  }

  function pump(): void {
    while (workers < THUMB_WORKERS && waiting.length) {
      const { path, image } = waiting.shift()!;
      if (thumbs.has(path)) {
        paintThumb(image, thumbs.get(path));
        continue;
      }
      workers += 1;
      void invoke<ArrayBuffer>("video_frame", { sourcePath: path, position: 0.15, maxWidth: THUMB_PX })
        .then((bytes) => thumbs.set(path, URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }))))
        .catch(() => thumbs.set(path, null))
        .finally(() => {
          workers -= 1;
          // The card may have been redrawn meanwhile: paint every card of this video.
          for (const node of body.querySelectorAll<HTMLElement>(".el-thumb")) {
            if (node.dataset.path === path) paintThumb(node, thumbs.get(path));
          }
          pump();
        });
    }
  }

  // Queue videos carry no size of their own; the host reads them once per load.
  const sizes = new Map<string, number>();
  function entries(): LibraryEntry[] {
    return distinctTitles(mergeEntries(host.jobs(), found, recent))
      .map((entry) => entry.sizeBytes || !sizes.get(entry.path) ? entry : { ...entry, sizeBytes: sizes.get(entry.path) });
  }

  async function loadSizes(): Promise<void> {
    const missing = entries().filter((entry) => !entry.sizeBytes && !sizes.has(entry.path)).map((entry) => entry.path).slice(0, 2000);
    if (!missing.length) return;
    try {
      const values = await invoke<number[]>("edit_file_sizes", { paths: missing });
      missing.forEach((path, index) => sizes.set(path, Number(values?.[index]) || 0));
      render();
    } catch {
      // Sizes are a nicety; the cards read fine without them.
    }
  }

  function saveRecent(): void {
    writeStorage(RECENT_KEY, JSON.stringify(recent.map(({ path, title, modified }) => ({ path, title, modified }))));
  }

  function renderFilters(all: readonly LibraryEntry[]): void {
    // The counts follow the search.
    const counts = countByKind(filterEntries(all, "all", search.value));
    filtersBox.replaceChildren(...FILTERS.map((item) => {
      const button = element("button");
      // The label's bold width is reserved so the tabs do not shift when one is selected.
      const label = element("span", "el-tab-label", item.label);
      label.dataset.label = item.label;
      button.append(label);
      button.type = "button";
      button.setAttribute("role", "tab");
      button.setAttribute("aria-selected", String(item.id === filter));
      // Always shown (dimmed at 0) so the tabs keep their width while searching.
      if (counts[item.id] || search.value.trim()) button.append(element("span", `el-count${counts[item.id] ? "" : " zero"}`, String(counts[item.id])));
      button.addEventListener("click", () => {
        filter = item.id;
        writeStorage(FILTER_KEY, filter);
        render();
      });
      return button;
    }));
  }

  function card(entry: LibraryEntry): HTMLElement {
    const node = element("div", `el-card${opening === entry.path ? " opening" : ""}`);
    node.tabIndex = 0;
    node.setAttribute("role", "button");
    node.setAttribute("aria-label", `Chỉnh sửa ${entry.title}`);
    node.title = entry.path;
    const image = element("div", "el-thumb loading");
    image.dataset.path = entry.path;
    image.append(element("span", `el-kind kind-${entry.kind}`, KIND_LABELS[entry.kind]));
    const action = element("span", "el-action");
    action.innerHTML = `${ICON_EDIT}<span>${opening === entry.path ? "Đang mở…" : "Chỉnh sửa"}</span>`;
    image.append(action);
    if (thumbs.has(entry.path)) paintThumb(image, thumbs.get(entry.path));
    else if (observer) observer.observe(image);
    else queueThumb(entry.path, image);
    const info = element("div", "el-info");
    info.append(element("div", "el-title", entry.title));
    // A title that already carries its date (same-titled videos) does not repeat it.
    const dated = entry.modified > 0 && entry.title.endsWith(shortDate(entry.modified));
    const meta = [dated ? "" : shortDate(entry.modified), sizeLabel(entry.sizeBytes)].filter(Boolean).join(" · ");
    const line = element("div", "el-meta", entry.kind === "file" ? (meta ? `Mở lúc ${meta}` : folderOf(entry.path)) : meta);
    info.append(line);
    node.append(image, info);
    if (entry.kind === "file") {
      const remove = element("button", "el-forget", "×");
      remove.type = "button";
      remove.title = "Bỏ khỏi danh sách (không xóa file)";
      remove.setAttribute("aria-label", `Bỏ ${entry.title} khỏi danh sách`);
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        recent = forgetFile(recent, entry.path);
        saveRecent();
        render();
      });
      node.append(remove);
    } else if (entry.kind !== "job") {
      // A Bán hàng, Tổng hợp or Phim tài liệu video: delete the project it belongs to.
      const remove = element("button", "el-forget el-delete");
      remove.type = "button";
      remove.innerHTML = ICON_TRASH;
      remove.title = "Xóa dự án (chuyển vào Thùng rác)";
      remove.setAttribute("aria-label", `Xóa dự án của ${entry.title}`);
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        void deleteProject(entry);
      });
      remove.addEventListener("keydown", (event) => event.stopPropagation());
      node.append(remove);
    }
    const activate = (): void => void openEntry(entry);
    node.addEventListener("click", activate);
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    });
    return node;
  }

  function empty(all: readonly LibraryEntry[]): HTMLElement {
    const box = element("div", "el-empty");
    const icon = element("div", "el-empty-icon");
    icon.innerHTML = ICON_FILM;
    const searching = search.value.trim() !== "";
    const title = searching ? "Không tìm thấy video phù hợp"
      : !all.length ? (loading ? "Đang tìm các video đã làm…" : "Chưa có video nào để chỉnh sửa")
      : filter === "file" ? "Chưa mở video nào từ máy" : `Chưa có video ${KIND_LABELS[filter as Exclude<LibraryFilter, "all">].toLowerCase()} đã xong`;
    const hint = searching ? (filter === "all" ? "Thử từ khóa khác." : "Thử từ khóa khác hoặc xem “Tất cả”.")
      : filter === "file" ? "Bấm “Mở video trên máy” hoặc kéo thả một video vào cửa sổ. Video đã mở được nhớ ở đây."
      : "Video đã xong ở Lồng tiếng, Bán hàng, Tổng hợp và Phim tài liệu sẽ hiện ở đây. Bạn cũng có thể mở bất kỳ video nào trên máy.";
    box.append(icon, element("p", "el-empty-title", title), element("p", "el-empty-hint", hint));
    if (searching) {
      const clear = element("button", "btn", "Xóa tìm kiếm");
      clear.type = "button";
      clear.addEventListener("click", () => {
        search.value = "";
        render();
        search.focus();
      });
      box.append(clear);
    } else {
      const pick = element("button", "btn primary");
      pick.type = "button";
      pick.innerHTML = `${ICON_PLUS}<span>Mở video trên máy</span>`;
      pick.addEventListener("click", () => void pickFile());
      box.append(pick);
    }
    return box;
  }

  function render(): void {
    const all = entries();
    renderFilters(all);
    const shown = filterEntries(all, filter, search.value);
    if (!shown.length) {
      body.replaceChildren(empty(all));
      return;
    }
    // A short list is one grid; a long one is grouped by age.
    if (shown.length <= GROUP_FROM) {
      const grid = element("div", "el-grid el-flat");
      grid.append(...shown.map(card));
      body.replaceChildren(grid);
      return;
    }
    const now = new Date();
    const groups = new Map<string, LibraryEntry[]>();
    for (const entry of shown) {
      const name = ageGroup(entry.modified, now);
      groups.set(name, [...(groups.get(name) ?? []), entry]);
    }
    body.replaceChildren(...[...groups].map(([name, items]) => {
      const section = element("section", "el-group");
      const heading = element("h3", "el-group-title", name);
      heading.append(element("span", "el-group-count", String(items.length)));
      const grid = element("div", "el-grid");
      grid.append(...items.map(card));
      section.append(heading, grid);
      return section;
    }));
  }

  async function load(): Promise<void> {
    const ticket = ++loadTicket;
    loading = true;
    if (!loaded) render();
    try {
      const value = await invoke<unknown>("edit_library", { roots: host.roots() });
      if (ticket !== loadTicket) return;
      found = parseLibrary(value);
      loaded = true;
      void loadSizes();
    } catch (error) {
      if (ticket === loadTicket) host.showError(`Không đọc được danh sách video: ${String(error).slice(0, 160)}`);
    } finally {
      if (ticket === loadTicket) {
        loading = false;
        render();
      }
    }
  }

  async function deleteProject(entry: LibraryEntry): Promise<void> {
    if (entry.kind === "job" || entry.kind === "file") return;
    const dir = runFolder(entry.path);
    // A product video is "<name> · video <n>": the project is the whole run.
    const title = entry.kind === "product" ? entry.title.replace(/ · video \d+$/, "") : entry.title;
    if (!await confirmDeleteProject(invoke, entry.kind, dir, title)) return;
    const prefix = `${dir.replace(/\\/g, "/").toLowerCase()}/`;
    found = found.filter((item) => !item.path.replace(/\\/g, "/").toLowerCase().startsWith(prefix));
    render();
    void load();
  }

  async function openEntry(entry: LibraryEntry): Promise<void> {
    if (opening) return;
    opening = entry.path;
    render();
    try {
      const opened = await host.open(entry);
      if (opened && entry.kind === "file") remember(entry.path, entry.title);
    } finally {
      opening = null;
      render();
    }
  }

  function remember(path: string, title?: string): void {
    recent = rememberFile(recent, path, Math.floor(Date.now() / 1000), title ?? fileTitle(path));
    saveRecent();
  }

  /** Open `paths[0]`; the others are kept under "Trên máy". */
  function openPaths(paths: string[]): void {
    const videos = paths.filter(isVideoPath);
    if (!videos.length) {
      if (paths.length) host.showError("Chỉ mở được file video (MP4, MKV, MOV, WEBM, AVI, M4V, TS, FLV).");
      return;
    }
    for (const path of videos.slice(1).reverse()) remember(path);
    // Picked or dropped: always a video on the computer, its draft kept in DubFlow's own store.
    void openEntry({ path: videos[0], kind: "file", title: fileTitle(videos[0]), modified: Math.floor(Date.now() / 1000) });
  }

  async function pickFile(): Promise<void> {
    pickButton.disabled = true;
    try {
      const paths = await invoke<string[]>("pick_files");
      if (paths?.length) openPaths(paths);
    } catch (error) {
      host.showError(`Không chọn được video: ${String(error).slice(0, 160)}`);
    } finally {
      pickButton.disabled = false;
    }
  }

  pickButton.addEventListener("click", () => void pickFile());
  // Found once at start, so Chèn can offer them before the screen is opened.
  void load();
  search.addEventListener("input", render);
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && search.value) {
      search.value = "";
      render();
    }
  });

  return {
    show: () => {
      render();
      void load();
    },
    drop: (paths) => openPaths(paths),
    remember,
    found: () => found,
  };
}
