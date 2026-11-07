/**
 * Hàng đợi: two columns, the list of every downloaded or added video on the
 * left and a player for the chosen one on the right. From here a video goes
 * on to Lồng tiếng, Bán hàng or Ghép video.
 */
import {
  addMedia, countMedia, MAX_MEDIA, filterMedia, folderOf, MEDIA_FILTERS, mediaUse, originLabel, removeMedia, serializeMedia, USE_LABELS,
  type DubJobLike, type MediaEntry, type MediaFilter, type NewMedia,
} from "./model.ts";

/** A link still downloading (or failed), shown at the top of the list. */
export type DownloadRow = {
  id: string;
  title: string;
  /** "Douyin", "Bilibili"… */
  provider: string;
  badge: string;
  message: string;
  /** Long text for the player panel (progress, folders). */
  detail: string;
  fraction: number | null;
  bar: boolean;
  failed: boolean;
  /** The row's × (absent while the file is being saved). */
  cancel?: () => void;
  /** Buttons for a failed download (Thử lại, Đăng nhập Douyin…). */
  actions(): HTMLElement[];
};

/** Saves the list; false when it could not be saved (storage off or full). */
export type MediaStore = { write(value: string): boolean };

export type MediaScreen = {
  show(): void;
  /** New videos at the top of the list; the first one is shown. */
  add(items: readonly NewMedia[], reveal?: boolean): string[];
  entries(): readonly MediaEntry[];
  /** Downloads changed (progress, finished, cancelled). */
  renderDownloads(): void;
  /** Dub jobs changed: the rows' "Đã lồng tiếng" marks follow. */
  refresh(): void;
  /** Show a download in the player panel (a link just pasted). */
  selectDownload(id: string): void;
};

const ICON_LINK = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" fill="none"/></svg>`;
const ICON_CLOSE = `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`;
const ICON_PLUS = `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function when(iso: string): string {
  const time = Date.parse(iso);
  if (!Number.isFinite(time)) return "";
  const date = new Date(time), now = new Date();
  const hm = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  if (date.toDateString() === now.toDateString()) return `Hôm nay ${hm}`;
  return `${date.getDate()}/${date.getMonth() + 1} ${hm}`;
}

export function mountMediaScreen(deps: {
  store: MediaStore;
  /** The list as stored (or seeded from Lồng tiếng on first run). */
  initial: readonly MediaEntry[];
  now(): string;
  jobs(): readonly DubJobLike[];
  downloads(): readonly DownloadRow[];
  /** A URL the player can stream the file from, or null. */
  mediaUrl(path: string): Promise<string | null>;
  thumb(entry: MediaEntry): HTMLElement;
  addFiles(): void;
  dub(entries: readonly MediaEntry[]): void;
  product(entries: readonly MediaEntry[]): void;
  compile(entries: readonly MediaEntry[]): void;
  edit(entry: MediaEntry): void;
  reveal(entry: MediaEntry): void;
  confirm(title: string, message: string, confirmLabel: string): Promise<boolean>;
}): MediaScreen {
  const root = document.querySelector<HTMLElement>("#media-view")!;
  root.innerHTML = `
    <div id="media-screen" class="queue-screen media-screen">
      <section id="media-col" class="queue-col media-col" aria-labelledby="media-col-title">
        <div class="queue-col-head"><h2 id="media-col-title">Video</h2><span id="media-count" class="hint"></span></div>
        <div id="media-filter" class="seg queue-filter" role="tablist" aria-label="Lọc danh sách"></div>
        <label class="media-search"><span class="visually-hidden">Tìm video</span><input id="media-search" type="search" placeholder="Tìm theo tên, link hoặc thư mục" autocomplete="off" /></label>
        <div class="media-bulk" id="media-bulk" hidden>
          <label class="media-all"><input id="media-all" type="checkbox" /> <span id="media-picked">Đã chọn 0</span></label>
          <button id="media-bulk-clear" class="link" type="button">Bỏ chọn</button>
        </div>
        <div class="queue-scroll">
          <ul id="media-downloads" class="queue-list fetch-list" aria-label="Đang tải về" hidden></ul>
          <ul id="media-list" class="queue-list" aria-label="Video trong hàng đợi"></ul>
          <p id="media-empty" class="queue-empty"></p>
        </div>
        <div class="queue-col-foot media-foot">
          <div id="media-bulk-actions" class="media-bulk-actions" hidden>
            <button id="media-bulk-dub" class="btn primary" type="button">Lồng tiếng</button>
            <button id="media-bulk-product" class="btn" type="button">Bán hàng</button>
            <button id="media-bulk-compile" class="btn" type="button">Ghép video</button>
            <button id="media-bulk-remove" class="btn ghost" type="button" title="Bỏ khỏi Hàng đợi (không xóa file)">Bỏ khỏi danh sách</button>
          </div>
          <button id="media-add" class="btn block" type="button">${ICON_PLUS}Thêm video từ máy</button>
        </div>
      </section>
      <section class="player-col media-player-col" aria-label="Xem video">
        <div class="player-head">
          <button id="media-list-toggle" class="btn ghost list-toggle" type="button" aria-controls="media-col" aria-expanded="false">Danh sách</button>
          <h2 id="media-name" class="player-title">Hàng đợi</h2>
        </div>
        <p id="media-state" class="queue-state" role="status"></p>
        <div class="player-stage">
          <div class="player-frame media-frame">
            <video id="media-video" controls playsinline preload="metadata" hidden></video>
            <div id="media-placeholder" class="player-placeholder">
              <svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="m10 9.5 4.5 2.5-4.5 2.5z" fill="currentColor"/></svg>
              <span id="media-placeholder-text"></span>
            </div>
            <div id="media-busy" class="player-overlay" role="status" hidden>
              <strong id="media-busy-title"></strong>
              <div class="bar busy-bar"><span id="media-busy-bar"></span></div>
              <span id="media-busy-detail" class="overlay-detail"></span>
              <div class="overlay-actions"><button id="media-busy-cancel" class="btn" type="button">Hủy tải</button></div>
            </div>
            <div id="media-failed" class="player-overlay failed" role="alert" hidden>
              <svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.75"/><path d="M12 7.5v5.5M12 16.5v.01" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/></svg>
              <strong>Không tải được video này</strong>
              <span id="media-failed-cause" class="overlay-detail"></span>
              <div id="media-failed-actions" class="overlay-actions"></div>
            </div>
          </div>
        </div>
        <div id="media-info" class="media-info" hidden>
          <div class="media-facts">
            <span id="media-origin" class="mini-tag muted"></span>
            <span id="media-use" class="mini-tag" hidden></span>
            <span id="media-meta" class="media-meta"></span>
          </div>
          <div class="media-actions">
            <span class="media-actions-label">Dùng video này cho</span>
            <button id="media-dub" class="btn primary" type="button" title="Dịch và lồng tiếng Việt">Lồng tiếng</button>
            <button id="media-product" class="btn" type="button" title="Thêm làm clip cho video bán hàng">Bán hàng</button>
            <button id="media-compile" class="btn" type="button" title="Ghép với các video đã lồng tiếng khác">Ghép video</button>
            <button id="media-edit" class="btn" type="button" title="Mở trong trình chỉnh sửa">Chỉnh sửa</button>
            <span class="spacer"></span>
            <button id="media-reveal" class="btn ghost" type="button">Mở thư mục</button>
            <button id="media-remove" class="btn ghost" type="button" title="Bỏ khỏi Hàng đợi (không xóa file)">Bỏ khỏi danh sách</button>
          </div>
        </div>
      </section>
    </div>`;

  const $ = <T extends HTMLElement>(id: string): T => root.querySelector<T>(`#media-${id}`)!;
  const screen = $("screen") as HTMLElement;
  const video = $<HTMLVideoElement>("video");

  let entries: MediaEntry[] = [...deps.initial];
  let filter: MediaFilter = "all";
  let query = "";
  /** The row in the player: a video id, or "dl:<scan id>" for a download. */
  let current = entries[0]?.id ?? "";
  const picked = new Set<string>();
  let playing = "";
  let request = 0;

  /** Saved, or the user is told the list lives only until DubFlow closes. */
  let unsaved = false;
  const save = (): boolean => {
    const ok = deps.store.write(serializeMedia(entries));
    if (!ok) warn("Không lưu được danh sách Hàng đợi (bộ nhớ của ứng dụng bị tắt hoặc đã đầy). Danh sách vẫn dùng được đến khi đóng DubFlow; file video trên máy không bị ảnh hưởng.");
    else if (unsaved) warn("");
    unsaved = !ok;
    return ok;
  };
  function warn(text: string): void {
    $("state").textContent = text;
  }
  const byId = (id: string) => entries.find((entry) => entry.id === id);
  const pickedEntries = () => entries.filter((entry) => picked.has(entry.id));

  function setListOpen(open: boolean): void {
    screen.classList.toggle("list-open", open);
    $("list-toggle").setAttribute("aria-expanded", String(open));
  }
  $("list-toggle").addEventListener("click", () => setListOpen(!screen.classList.contains("list-open")));
  screen.addEventListener("click", (event) => {
    if (screen.classList.contains("list-open") && !(event.target as HTMLElement).closest("#media-col, #media-list-toggle")) setListOpen(false);
  });

  function renderFilters(): void {
    const counts = countMedia(entries);
    $("filter").replaceChildren(...MEDIA_FILTERS.map(({ id, label }) => {
      const button = element("button", "", counts[id] && id !== "all" ? `${label} ${counts[id]}` : label);
      button.type = "button";
      button.setAttribute("role", "tab");
      button.setAttribute("aria-selected", String(filter === id));
      button.addEventListener("click", () => { filter = id; renderList(); });
      return button;
    }));
  }

  function select(id: string): void {
    current = id;
    setListOpen(false);
    renderList();
    renderDownloads();
    renderPlayer();
  }

  function row(entry: MediaEntry, jobs: readonly DubJobLike[]): HTMLElement {
    const item = element("li", `job media-row${entry.id === current ? " selected" : ""}${picked.has(entry.id) ? " picked" : ""}`);
    item.tabIndex = 0;
    item.title = `${entry.title}\n${entry.path}${entry.originUrl ? `\n${entry.originUrl}` : ""}`;
    if (entry.id === current) item.setAttribute("aria-current", "true");
    const check = element("input", "media-check");
    check.type = "checkbox";
    check.checked = picked.has(entry.id);
    check.setAttribute("aria-label", `Chọn ${entry.title}`);
    check.addEventListener("click", (event) => event.stopPropagation());
    check.addEventListener("change", () => {
      if (check.checked) picked.add(entry.id); else picked.delete(entry.id);
      renderList();
    });
    const main = element("span", "job-main");
    main.append(element("span", "job-title", entry.title));
    const meta = element("span", "job-meta");
    meta.append(element("span", "mini-tag muted", originLabel(entry)));
    const use = mediaUse(entry, jobs);
    if (use !== "none") meta.append(element("span", `mini-tag${use === "failed" ? " err" : ""}`, USE_LABELS[use]));
    meta.append(element("span", "msg", when(entry.addedAt)));
    main.append(meta);
    item.append(check, deps.thumb(entry), main);
    item.addEventListener("click", () => select(entry.id));
    item.addEventListener("keydown", (event) => {
      if (event.target !== item) return;
      if (event.key === "Enter") { event.preventDefault(); select(entry.id); }
      if (event.key === " ") { event.preventDefault(); check.click(); }
    });
    return item;
  }

  function renderList(): void {
    for (const id of [...picked]) if (!byId(id)) picked.delete(id);
    renderFilters();
    const jobs = deps.jobs();
    const shown = filterMedia(entries, filter, query);
    $("list").replaceChildren(...shown.map((entry) => row(entry, jobs)));
    $("count").textContent = entries.length ? `${entries.length} video` : "";
    const downloading = deps.downloads().length;
    const rail = document.getElementById("nav-media-count");
    if (rail) {
      rail.textContent = String(entries.length + downloading);
      rail.hidden = entries.length + downloading === 0;
      rail.title = downloading ? `${downloading} video đang tải về` : "";
    }
    $("empty").hidden = shown.length > 0 || (downloading > 0 && !entries.length);
    $("empty").textContent = entries.length
      ? "Không có video khớp. Thử từ khóa khác hoặc xem “Tất cả”."
      : "Chưa có video. Dán link ở thanh trên cùng, chọn video ở Khám phá, hoặc thêm video từ máy.";
    // Several chosen: the actions work on all of them at once.
    const count = picked.size;
    $("bulk").hidden = count === 0;
    $("bulk-actions").hidden = count === 0;
    $("picked").textContent = `Đã chọn ${count} video`;
    const all = $<HTMLInputElement>("all");
    all.checked = count > 0 && shown.every((entry) => picked.has(entry.id));
    all.indeterminate = count > 0 && !all.checked;
    $<HTMLButtonElement>("bulk-compile").disabled = count < 2;
    $("bulk-compile").title = count < 2 ? "Chọn ít nhất 2 video để ghép" : "Ghép các video đã chọn (cần lồng tiếng xong)";
  }

  function renderDownloads(): void {
    const rows = deps.downloads();
    const list = $("downloads");
    list.hidden = rows.length === 0 || filter === "file";
    list.replaceChildren(...rows.map((download) => {
      const id = `dl:${download.id}`;
      const item = element("li", `job${download.failed ? " failed" : ""}${current === id ? " selected" : ""}`);
      item.tabIndex = 0;
      const thumb = element("span", "job-thumb media-dl-thumb");
      thumb.innerHTML = ICON_LINK;
      const main = element("span", "job-main");
      const meta = element("span", "job-meta");
      meta.append(element("span", `dot ${download.failed ? "err" : "run"}`), element("span", `msg${download.failed ? " err" : ""}`, download.failed ? "Tải không được" : download.message));
      main.append(element("span", "job-title", download.title), meta);
      if (download.bar) {
        const bar = element("div", `bar${download.fraction === null ? " indeterminate" : ""}`);
        const fill = element("span");
        if (download.fraction !== null) fill.style.width = `${Math.round(download.fraction * 100)}%`;
        bar.append(fill);
        main.append(bar);
      }
      item.append(thumb, main);
      if (download.cancel) {
        const remove = element("button", "icon-btn job-dismiss");
        remove.type = "button";
        remove.title = download.failed ? "Bỏ" : "Hủy tải";
        remove.setAttribute("aria-label", remove.title);
        remove.innerHTML = ICON_CLOSE;
        remove.addEventListener("click", (event) => { event.stopPropagation(); download.cancel?.(); });
        item.append(remove);
      }
      item.addEventListener("click", () => select(id));
      item.addEventListener("keydown", (event) => {
        if (event.target === item && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); select(id); }
      });
      return item;
    }));
    if (current.startsWith("dl:")) renderPlayer();
  }

  let failedKey = "";
  function renderPlayer(): void {
    const download = current.startsWith("dl:") ? deps.downloads().find((row) => `dl:${row.id}` === current) : undefined;
    if (current.startsWith("dl:") && !download) current = entries[0]?.id ?? "";
    const entry = download ? undefined : byId(current);
    if (!download && !entry && entries.length) current = entries[0].id;
    const shown = download ? undefined : byId(current);
    $("busy").hidden = !download || download.failed;
    $("failed").hidden = !download?.failed;
    $("info").hidden = !shown;
    if (download) {
      $("name").textContent = download.title;
      stop();
      placeholder("Video hiện ở đây khi tải xong");
      if (download.failed) {
        $("failed-cause").textContent = download.message;
        const key = `${download.id}:${download.message}`;
        if (key !== failedKey) { failedKey = key; $("failed-actions").replaceChildren(...download.actions()); }
      } else {
        failedKey = "";
        $("busy-title").textContent = `${download.badge} từ ${download.provider}`;
        const bar = $("busy-bar");
        bar.parentElement!.hidden = !download.bar;
        bar.parentElement!.classList.toggle("indeterminate", download.fraction === null);
        bar.style.width = download.fraction === null ? "" : `${Math.round(download.fraction * 100)}%`;
        $("busy-detail").textContent = download.detail;
        $("busy-cancel").hidden = !download.cancel;
      }
      return;
    }
    if (!shown) {
      $("name").textContent = "Hàng đợi";
      stop();
      placeholder(deps.downloads().length ? "Chọn một video trong danh sách để xem" : "Video bạn tải về hoặc thêm từ máy hiện ở đây");
      return;
    }
    $("name").textContent = shown.title;
    $("name").title = shown.path;
    $("origin").textContent = originLabel(shown);
    $("origin").title = shown.originUrl ?? shown.path;
    const use = mediaUse(shown, deps.jobs());
    $("use").hidden = use === "none";
    $("use").textContent = USE_LABELS[use];
    $("meta").textContent = [when(shown.addedAt) ? `Thêm ${when(shown.addedAt).toLowerCase()}` : "", folderOf(shown.path)].filter(Boolean).join(" · ");
    $("meta").title = shown.path;
    play(shown.path);
  }

  function placeholder(text: string): void {
    $("placeholder").hidden = false;
    $("placeholder-text").textContent = text;
  }

  function stop(): void {
    playing = "";
    request += 1;
    video.pause();
    video.removeAttribute("src");
    video.hidden = true;
    video.load();
  }

  function play(path: string): void {
    if (playing === path) return;
    stop();
    playing = path;
    const mine = request;
    placeholder("Đang mở video…");
    deps.mediaUrl(path).then((url) => {
      if (mine !== request) return;
      if (!url) { placeholder("Không xem được video này trong ứng dụng; bấm “Mở thư mục” để xem bằng trình phát của máy."); return; }
      video.src = url;
      video.hidden = false;
      $("placeholder").hidden = true;
    }).catch(() => {
      if (mine === request) placeholder("Không mở được video để xem.");
    });
  }
  video.addEventListener("error", () => {
    if (!video.hidden && playing) { video.hidden = true; placeholder("Không phát được video này trong ứng dụng; bấm “Mở thư mục” để xem bằng trình phát của máy."); }
  });

  async function remove(ids: string[]): Promise<void> {
    if (!ids.length) return;
    const many = ids.length > 1;
    if (!(await deps.confirm(many ? `Bỏ ${ids.length} video khỏi Hàng đợi?` : "Bỏ video này khỏi Hàng đợi?",
      "File video trên máy vẫn được giữ nguyên. Video đang lồng tiếng hoặc đã làm xong ở các màn khác không bị ảnh hưởng.", "Bỏ khỏi danh sách"))) return;
    entries = removeMedia(entries, ids);
    for (const id of ids) picked.delete(id);
    save();
    if (ids.includes(current)) current = entries[0]?.id ?? "";
    renderList();
    renderPlayer();
  }

  const one = (action: (entries: readonly MediaEntry[]) => void) => () => {
    const entry = byId(current);
    if (entry) action([entry]);
  };
  $("dub").addEventListener("click", one(deps.dub));
  $("product").addEventListener("click", one(deps.product));
  $("compile").addEventListener("click", one(deps.compile));
  $("edit").addEventListener("click", () => { const entry = byId(current); if (entry) { stop(); deps.edit(entry); } });
  $("reveal").addEventListener("click", () => { const entry = byId(current); if (entry) deps.reveal(entry); });
  $("remove").addEventListener("click", () => void remove(current && byId(current) ? [current] : []));
  $("bulk-dub").addEventListener("click", () => deps.dub(pickedEntries()));
  $("bulk-product").addEventListener("click", () => deps.product(pickedEntries()));
  $("bulk-compile").addEventListener("click", () => deps.compile(pickedEntries()));
  $("bulk-remove").addEventListener("click", () => void remove([...picked]));
  $("bulk-clear").addEventListener("click", () => { picked.clear(); renderList(); });
  $<HTMLInputElement>("all").addEventListener("change", (event) => {
    const shown = filterMedia(entries, filter, query);
    if ((event.target as HTMLInputElement).checked) for (const entry of shown) picked.add(entry.id);
    else picked.clear();
    renderList();
  });
  $("add").addEventListener("click", () => deps.addFiles());
  $("busy-cancel").addEventListener("click", () => deps.downloads().find((row) => `dl:${row.id}` === current)?.cancel?.());
  $<HTMLInputElement>("search").addEventListener("input", (event) => { query = (event.target as HTMLInputElement).value; renderList(); });

  renderList();
  renderDownloads();

  return {
    show() {
      renderList();
      renderDownloads();
      renderPlayer();
    },
    add(items, reveal = true) {
      const result = addMedia(entries, items, deps.now());
      entries = result.entries;
      if (result.ids.length) save();
      if (result.refused) warn(`Hàng đợi đã đủ ${MAX_MEDIA} video nên ${result.refused} video chưa được thêm. Hãy bỏ bớt video cũ khỏi danh sách rồi thêm lại.`);
      if (reveal && result.ids.length) { current = result.ids[0]; filter = "all"; }
      renderList();
      renderDownloads();
      if (!root.hidden) renderPlayer();
      return result.ids;
    },
    entries: () => entries,
    renderDownloads() {
      renderList();
      renderDownloads();
      if (!root.hidden) renderPlayer();
    },
    refresh() {
      renderList();
      if (!root.hidden && !current.startsWith("dl:")) {
        const entry = byId(current);
        if (entry) { const use = mediaUse(entry, deps.jobs()); $("use").hidden = use === "none"; $("use").textContent = USE_LABELS[use]; }
      }
    },
    selectDownload(id) {
      current = `dl:${id}`;
      renderList();
      renderDownloads();
      if (!root.hidden) renderPlayer();
    },
  };
}

/** Pause the Hàng đợi player when another page opens. */
export function pauseMedia(): void {
  document.querySelector<HTMLVideoElement>("#media-video")?.pause();
}
