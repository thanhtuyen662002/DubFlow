import {
  GLOSS_BATCH, PLATFORMS, TITLE_GLOSS_KEY, TOPICS, bilibiliMid, cleanTitle, parseGlosses, discoveryError, formatCount, formatDuration, loadGlossCache,
  mergeVideos, needsChinese, needsTitleGloss, pageAddress, parseChinese, topicChinese, parseCollected, parseTrends, parseVideos, previewSource, refreshVideos, rememberGloss, saveGlossCache, seenOnPage,
  type DiscoveredVideo, type Platform, type PreviewSource, type Trend,
} from "./model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Mode = { kind: "api"; action: "search" | "popular" | "creator"; query: string; mid?: string; page: number } | { kind: "page" } | null;

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing ${selector}`);
  return node;
};

const NO_TITLE = "(không có tiêu đề)";
const HOVER_KEY = "dubflow.discoverHover.v1";
const AUTO_ZH_KEY = "dubflow.discoverAutoZh.v1";
const HOVER_DELAY_MS = 450;
const ADDED_NOTE = " Xem tiến độ tải ở Hàng đợi.";

const readStorage = (key: string): string | null => {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
};
const writeStorage = (key: string, value: string): void => {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage is optional: the setting or cache then lasts this session only.
  }
};

/** One rendered result card. */
type Card = { video: DiscoveredVideo; card: HTMLLIElement; thumb: HTMLElement; check: HTMLInputElement; paintTitle(): void };

/**
 * The element that plays a source. Embeds are sandboxed without top
 * navigation or popups, so a click inside a player never leaves the app.
 */
function playerElement(source: PreviewSource, muted: boolean): HTMLIFrameElement | HTMLVideoElement {
  if (source.kind === "iframe") {
    const frame = document.createElement("iframe");
    frame.allow = "autoplay; fullscreen; encrypted-media; picture-in-picture";
    frame.allowFullscreen = true;
    frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-presentation");
    frame.title = muted ? "Xem trước video" : "Trình phát video";
    frame.src = source.src;
    return frame;
  }
  const video = document.createElement("video");
  video.muted = muted;
  video.defaultMuted = muted;
  video.autoplay = true;
  video.playsInline = true;
  video.preload = "auto";
  video.loop = muted;
  video.controls = !muted;
  video.src = source.src;
  void video.play().catch(() => undefined);
  return video;
}

/** Stop a player's sound and network, then take it off the page. */
function dropPlayer(node: Element | null | undefined): void {
  if (!node) return;
  for (const video of node instanceof HTMLVideoElement ? [node] : node.querySelectorAll("video")) {
    video.pause();
    video.removeAttribute("src");
    video.load();
  }
  node.remove();
}

/**
 * Wire the "Khám phá" view. `enqueue` downloads one link (with its title) into the queue;
 * `glossEngine` says which translator may gloss keywords ("hachimi" only once
 * the user accepted its licence warning); `onWindowMoved` reports moves of the
 * app window (the discovery page moves with it). `aiChinese`, when given, turns a
 * Vietnamese phrase into a Chinese keyword with the user's saved AI key (null
 * without a key); it is the fallback when the web translators fail.
 */
export function mountDiscovery(invoke: Invoke, enqueue: (url: string, title?: string) => Promise<void>, glossEngine: () => "argos" | "hachimi",
  onWindowMoved: (listener: () => void) => void, aiChinese?: (text: string) => Promise<string | null>): { show(): void; hide(): void } {
  const tabs = $("#platform-tabs");
  const trendList = $("#trend-list");
  const trendState = $("#trend-state");
  const form = $<HTMLFormElement>("#discover-form");
  const kind = $<HTMLSelectElement>("#discover-kind");
  const query = $<HTMLInputElement>("#discover-query");
  const suggestList = $("#suggest-list");
  const autoChinese = $<HTMLInputElement>("#discover-auto-zh");
  const translatedNote = $("#discover-translated");
  const popular = $<HTMLButtonElement>("#discover-popular");
  const main = $("#discover-view .discover-main");
  const panel = $("#discover-panel");
  const pageBox = $("#discover-page");
  const siteLabel = $("#discover-site");
  const urlLabel = $("#discover-url");
  const showResults = $<HTMLButtonElement>("#discover-show-results");
  const reopenBar = $("#discover-reopen-bar");
  const state = $("#discover-state");
  const resultBar = $("#result-bar");
  const selectAll = $<HTMLInputElement>("#select-all");
  const selectedCount = $("#selected-count");
  const addButton = $<HTMLButtonElement>("#discover-add");
  const results = $("#discover-results");
  const more = $<HTMLButtonElement>("#discover-more");

  let platform: Platform = "douyin";
  let videos: DiscoveredVideo[] = [];
  const selected = new Set<string>();
  let mode: Mode = null;
  let nextPage: number | null = null;
  let suggestTimer = 0;
  let searchRequest = 0;
  // Vietnamese phrase (lower case) -> Chinese keyword, for this app run.
  const chineseCache = new Map<string, string>();
  let suggestRequest = 0;
  let trendRequest = 0;
  const trendCache = new Map<Platform, { at: number; items: Trend[] }>();
  const glossToggle = $<HTMLInputElement>("#trend-gloss");
  const glosses = new Map<string, string>();
  let shownTrends: Trend[] = [];
  let glossRequest = 0;

  const resultTools = $("#result-tools");
  const hoverToggle = $<HTMLInputElement>("#discover-hover");
  const scroller = results.closest<HTMLElement>(".discover-main") ?? results;
  const player = $("#discover-player");
  const stage = $("#pop-stage");
  const playerTitle = $("#pop-title");
  const playerOriginal = $("#pop-original");
  const playerSub = $("#pop-sub");
  const playerToggle = $<HTMLButtonElement>("#pop-toggle");
  const playerDownload = $<HTMLButtonElement>("#pop-download");
  const playerState = $("#pop-state");

  const cards = new Map<string, Card>();
  // Vietnamese titles by original title, most recently used last.
  const titleGloss = loadGlossCache(readStorage(TITLE_GLOSS_KEY));
  const titlePending = new Set<string>();
  const titleFailed = new Set<string>();
  let titleBusy = false;
  let titleAgain = false;

  // Hover preview: at most one, inside the card under the pointer.
  let hoverCard: Card | null = null;
  let hoverTimer = 0;
  let hoverArmed = false;
  let preview: { entry: Card; layer: HTMLElement } | null = null;
  const reducedMotion = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  let clickMemory: { url: string; was: boolean } | null = null;
  let playing: { video: DiscoveredVideo } | null = null;

  const info = () => PLATFORMS.find((entry) => entry.id === platform)!;

  function renderTabs(): void {
    tabs.replaceChildren(...PLATFORMS.map((entry) => {
      const button = document.createElement("button");
      button.type = "button";
      button.role = "tab";
      button.textContent = entry.name;
      button.setAttribute("aria-selected", String(entry.id === platform));
      button.addEventListener("click", () => switchPlatform(entry.id));
      return button;
    }));
    popular.hidden = platform !== "bilibili";
    query.placeholder = kind.value === "creator"
      ? `Dán link trang kênh ${info().name}`
      : autoChinese.checked ? "Gõ tiếng Việt hoặc tiếng Trung, ví dụ nuôi lợn, 美食" : "Từ khóa tiếng Trung, ví dụ 仙侠 hoặc 美食";
  }

  async function loadGlosses(items: Trend[]): Promise<void> {
    const missing = items.map((trend) => trend.keyword).filter((keyword) => !glosses.has(keyword)).slice(0, 60);
    if (!glossToggle.checked || missing.length === 0) return;
    const request = ++glossRequest;
    trendState.textContent = "Đang dịch nghĩa…";
    try {
      const result = parseGlosses(await invoke<unknown>("translate_terms", { terms: missing, engine: glossEngine() }), missing);
      if (request !== glossRequest) return;
      if (!result) {
        trendState.textContent = "Chưa dịch được (cần xử lý một video tiếng Trung trước để tải model dịch).";
        return;
      }
      for (const [keyword, gloss] of result) glosses.set(keyword, gloss);
      trendState.textContent = "";
      renderTrends(shownTrends);
    } catch (error) {
      if (request === glossRequest) trendState.textContent = `Không dịch được: ${String(error).slice(0, 120)}`;
    }
  }

  function renderTrends(items: Trend[]): void {
    shownTrends = items;
    trendList.replaceChildren(...items.slice(0, 50).map((trend, index) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.title = `Tìm “${trend.keyword}”`;
      const rank = document.createElement("span");
      rank.className = index < 3 ? "rank top" : "rank";
      rank.textContent = String(trend.rank);
      const word = document.createElement("span");
      word.className = "word";
      word.textContent = trend.keyword;
      const gloss = glossToggle.checked ? glosses.get(trend.keyword) : undefined;
      if (gloss) {
        const small = document.createElement("small");
        small.textContent = gloss;
        word.append(small);
      }
      const heat = document.createElement("span");
      heat.className = "heat";
      heat.textContent = formatCount(trend.heat);
      button.append(rank, word, heat);
      button.addEventListener("click", () => {
        kind.value = "search";
        query.value = trend.keyword;
        void search();
      });
      item.append(button);
      return item;
    }));
  }

  async function loadTrends(force = false): Promise<void> {
    const cached = trendCache.get(platform);
    if (!force && cached && Date.now() - cached.at < 5 * 60_000) {
      renderTrends(cached.items);
      trendState.textContent = "";
      void loadGlosses(cached.items);
      return;
    }
    const request = ++trendRequest;
    const forPlatform = platform;
    trendList.replaceChildren();
    trendState.textContent = "Đang tải xu hướng…";
    try {
      const result = parseTrends(await invoke<unknown>("discover", { platform: forPlatform, action: "trends" }));
      if (request !== trendRequest) return;
      if (!result.ok) {
        trendState.textContent = discoveryError(result.code, forPlatform);
        return;
      }
      trendCache.set(forPlatform, { at: Date.now(), items: result.items });
      renderTrends(result.items);
      trendState.textContent = result.items.length ? "" : "Chưa có xu hướng.";
      void loadGlosses(result.items);
    } catch (error) {
      if (request === trendRequest) trendState.textContent = `Không tải được xu hướng: ${String(error).slice(0, 120)}`;
    }
  }

  const viTitle = (video: DiscoveredVideo): string => (glossToggle.checked ? titleGloss.get(video.title) ?? "" : "");
  /** The title a download is named after: the Vietnamese one when the card shows it. */
  const downloadTitle = (video: DiscoveredVideo): string | undefined => viTitle(video) || (video.title === NO_TITLE ? undefined : video.title);

  function renderResults(): void {
    stopPreview();
    hoverCard = null;
    cards.clear();
    results.replaceChildren(...videos.map((video) => {
      const card = document.createElement("li");
      card.className = selected.has(video.pageUrl) ? "video-card selected" : "video-card";
      card.tabIndex = 0;
      const thumb = document.createElement("div");
      thumb.className = "thumb";
      if (video.cover) {
        const image = document.createElement("img");
        image.src = video.cover;
        image.alt = "";
        image.loading = "lazy";
        image.referrerPolicy = "no-referrer";
        thumb.append(image);
      }
      if (video.durationS !== null) {
        const duration = document.createElement("span");
        duration.className = "dur";
        duration.textContent = formatDuration(video.durationS);
        thumb.append(duration);
      }
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = selected.has(video.pageUrl);
      const meta = document.createElement("div");
      meta.className = "meta";
      const title = document.createElement("span");
      title.className = "title";
      const original = document.createElement("span");
      original.className = "orig";
      const sub = document.createElement("span");
      sub.className = "sub";
      sub.textContent = [video.author, video.plays !== null ? `${formatCount(video.plays)} lượt xem` : ""].filter(Boolean).join(" · ");
      meta.append(title, original, sub);
      card.append(thumb, check, meta);
      const entry: Card = {
        video, card, thumb, check,
        paintTitle() {
          const vi = viTitle(video);
          const pending = !vi && glossToggle.checked && titlePending.has(video.title);
          title.textContent = vi || video.title;
          title.title = vi ? `${vi}\n${video.title}` : video.title;
          original.textContent = vi ? video.title : pending ? "Đang dịch…" : "";
          original.title = vi ? video.title : "";
          original.classList.toggle("pending", pending);
          original.hidden = !vi && !pending;
          check.setAttribute("aria-label", `Chọn ${vi || video.title}`);
        },
      };
      entry.paintTitle();
      cards.set(video.pageUrl, entry);
      wireCard(entry);
      return card;
    }));
    resultBar.hidden = videos.length === 0;
    resultTools.hidden = videos.length === 0;
    more.hidden = !(mode?.kind === "api" && nextPage !== null);
    showResults.textContent = videos.length ? `Xem kết quả (${videos.length})` : "Xem kết quả";
    renderSelection();
  }

  function setSelected(video: DiscoveredVideo, on: boolean): void {
    if (on) selected.add(video.pageUrl);
    else selected.delete(video.pageUrl);
    const entry = cards.get(video.pageUrl);
    if (entry) {
      entry.card.classList.toggle("selected", on);
      entry.check.checked = on;
    }
    renderSelection();
    if (playing?.video.pageUrl === video.pageUrl) paintPlayer();
  }

  function wireCard(entry: Card): void {
    const { video, card, check } = entry;
    const url = video.pageUrl;
    card.addEventListener("click", (event) => {
      // The second click of a double-click belongs to the player, not the selection.
      if (event.target === check || event.detail > 1) return;
      clickMemory = { url, was: selected.has(url) };
      setSelected(video, !selected.has(url));
    });
    card.addEventListener("dblclick", (event) => {
      if (event.target === check) return;
      // The first click of the pair toggled the card: put it back as it was.
      if (clickMemory?.url === url) setSelected(video, clickMemory.was);
      clickMemory = null;
      openPlayer(video);
    });
    card.addEventListener("keydown", (event) => {
      if (event.target !== card) return;
      if (event.key === "Enter") {
        event.preventDefault();
        openPlayer(video);
      } else if (event.key === " ") {
        event.preventDefault();
        setSelected(video, !selected.has(url));
      }
    });
    check.addEventListener("change", () => setSelected(video, check.checked));
    // Pointer only: focusing a card with the keyboard never plays anything.
    card.addEventListener("pointerenter", (event) => {
      if (event.pointerType === "touch") return;
      hoverCard = entry;
      armHover(entry);
    });
    card.addEventListener("pointermove", (event) => {
      if (event.pointerType === "touch") return;
      hoverCard = entry;
      if (!hoverArmed && preview?.entry !== entry) armHover(entry);
    });
    card.addEventListener("pointerleave", () => {
      // A pending preview for this card no longer fires once another card is hovered.
      if (hoverCard === entry) hoverCard = null;
      if (preview?.entry === entry) dropPreview();
    });
  }

  const hoverAllowed = (): boolean => hoverToggle.checked && !reducedMotion?.matches && player.hidden && !document.hidden;

  function armHover(entry: Card): void {
    window.clearTimeout(hoverTimer);
    hoverArmed = false;
    if (!hoverAllowed() || preview?.entry === entry || !previewSource(entry.video, { muted: true })) return;
    hoverArmed = true;
    hoverTimer = window.setTimeout(() => {
      hoverArmed = false;
      if (hoverCard === entry && entry.card.isConnected && hoverAllowed()) startPreview(entry);
    }, HOVER_DELAY_MS);
  }

  function startPreview(entry: Card): void {
    const source = previewSource(entry.video, { muted: true });
    if (!source) return;
    stopPreview();
    const layer = document.createElement("div");
    layer.className = "card-preview loading";
    const media = playerElement(source, true);
    const loaded = () => layer.classList.remove("loading");
    media.addEventListener(media instanceof HTMLVideoElement ? "playing" : "load", loaded, { once: true });
    // Clicks and double-clicks land on this shield, never inside the player.
    const shield = document.createElement("div");
    shield.className = "card-preview-shield";
    const chip = document.createElement("span");
    chip.className = "card-preview-chip";
    chip.textContent = "Xem trước";
    const shimmer = document.createElement("span");
    shimmer.className = "card-preview-shimmer";
    if (media instanceof HTMLVideoElement) {
      media.addEventListener("error", () => {
        layer.classList.replace("loading", "failed");
        chip.textContent = "Không phát được";
      }, { once: true });
    }
    layer.append(media, shield, chip, shimmer);
    entry.thumb.append(layer);
    preview = { entry, layer };
  }

  /** Stop the preview and any pending one. */
  function stopPreview(): void {
    window.clearTimeout(hoverTimer);
    hoverArmed = false;
    dropPreview();
  }

  function dropPreview(): void {
    if (!preview) return;
    dropPlayer(preview.layer);
    preview = null;
  }

  function paintTitles(): void {
    for (const entry of cards.values()) entry.paintTitle();
    if (playing) paintPlayer();
  }

  function saveTitles(): void {
    writeStorage(TITLE_GLOSS_KEY, saveGlossCache(titleGloss));
  }

  /**
   * Translate the listed titles that are not cached yet, at most 60 per call.
   * A failure keeps the original titles without a message.
   */
  async function translateTitles(): Promise<void> {
    if (!glossToggle.checked) return;
    if (titleBusy) {
      titleAgain = true;
      return;
    }
    titleBusy = true;
    try {
      do {
        titleAgain = false;
        let touched = false;
        // Cleaned text to translate, with the titles it stands for.
        const wanted = new Map<string, string[]>();
        for (const video of videos) {
          const original = video.title;
          if (original === NO_TITLE || titlePending.has(original) || titleFailed.has(original)) continue;
          const known = titleGloss.get(original);
          if (known !== undefined) {
            rememberGloss(titleGloss, original, known);
            touched = true;
            continue;
          }
          const cleaned = cleanTitle(original);
          if (!needsTitleGloss(cleaned)) continue;
          const titles = wanted.get(cleaned) ?? [];
          if (!titles.includes(original)) titles.push(original);
          wanted.set(cleaned, titles);
        }
        if (touched) saveTitles();
        const terms = [...wanted.keys()];
        for (let start = 0; start < terms.length; start += GLOSS_BATCH) {
          if (!glossToggle.checked) return;
          const batch = terms.slice(start, start + GLOSS_BATCH);
          const originals = batch.flatMap((term) => wanted.get(term) ?? []);
          for (const original of originals) titlePending.add(original);
          paintTitles();
          let glossed: Map<string, string> | null = null;
          try {
            glossed = parseGlosses(await invoke<unknown>("translate_terms", { terms: batch, engine: glossEngine() }), batch);
          } catch {
            glossed = null;
          }
          for (const original of originals) titlePending.delete(original);
          if (!glossed) {
            // No translator, or it failed: the rest keep their originals this session.
            for (const term of terms.slice(start)) for (const original of wanted.get(term) ?? []) titleFailed.add(original);
            paintTitles();
            break;
          }
          for (const term of batch) {
            const gloss = glossed.get(term) ?? "";
            for (const original of wanted.get(term) ?? []) {
              if (gloss && gloss !== term) rememberGloss(titleGloss, original, gloss);
              else titleFailed.add(original);
            }
          }
          saveTitles();
          paintTitles();
        }
      } while (titleAgain && glossToggle.checked);
    } finally {
      titleBusy = false;
    }
  }

  function openPlayer(video: DiscoveredVideo): void {
    stopPreview();
    hoverCard = null;
    playing = { video };
    playerState.textContent = "";
    playerDownload.disabled = false;
    dropPlayer(stage.firstElementChild);
    stage.replaceChildren();
    const source = previewSource(video, { muted: false });
    stage.className = source?.vertical ? "pop-stage vertical" : "pop-stage";
    if (source) {
      const frame = document.createElement("div");
      frame.className = "pop-frame";
      const media = playerElement(source, false);
      if (media instanceof HTMLVideoElement) {
        media.addEventListener("loadedmetadata", () => {
          stage.classList.toggle("vertical", media.videoHeight > media.videoWidth);
        }, { once: true });
      }
      frame.append(media);
      stage.append(frame);
    } else {
      stage.classList.add("empty");
      if (video.cover) {
        const image = document.createElement("img");
        image.src = video.cover;
        image.alt = "";
        image.referrerPolicy = "no-referrer";
        stage.append(image);
      }
      const note = document.createElement("p");
      note.className = "pop-note";
      note.textContent = "Không xem trước được video này trong ứng dụng.";
      stage.append(note);
    }
    paintPlayer();
    player.hidden = false;
    playerToggle.focus();
  }

  function paintPlayer(): void {
    if (!playing) return;
    const { video } = playing;
    const vi = viTitle(video);
    playerTitle.textContent = vi || video.title;
    playerOriginal.textContent = vi ? video.title : "";
    playerOriginal.hidden = !vi;
    playerSub.textContent = [video.author, formatDuration(video.durationS), video.plays !== null ? `${formatCount(video.plays)} lượt xem` : ""]
      .filter(Boolean).join(" · ");
    const picked = selected.has(video.pageUrl);
    playerToggle.textContent = picked ? "Bỏ chọn" : "Chọn video này";
    playerToggle.className = picked ? "btn" : "btn primary";
  }

  function closePlayer(restoreFocus = true): void {
    if (!playing) return;
    const url = playing.video.pageUrl;
    playing = null;
    dropPlayer(stage.firstElementChild);
    stage.replaceChildren();
    player.hidden = true;
    const card = cards.get(url)?.card;
    if (restoreFocus && card?.isConnected) card.focus();
  }

  function renderSelection(): void {
    const count = videos.filter((video) => selected.has(video.pageUrl)).length;
    selectedCount.textContent = videos.length ? `Đã chọn ${count}/${videos.length}` : "";
    selectAll.checked = videos.length > 0 && count === videos.length;
    addButton.disabled = count === 0;
    addButton.textContent = count ? `Thêm ${count} video vào Hàng đợi` : "Thêm vào Hàng đợi";
  }

  function resetResults(): void {
    closePlayer(false);
    videos = [];
    selected.clear();
    nextPage = null;
    renderResults();
  }

  // The platform page, shown by the host over `pageBox` while browsing. At most
  // one exists; it stays loaded while hidden (results shown, another screen,
  // or something drawn over the panel that the native page would cover).
  let page: { platform: Platform } | null = null;
  let browsing = false;
  let screenShown = false;
  let opening = 0;
  let pageShown = false;
  let lastBounds = "";
  let urlTimer = 0;
  // Host calls about the page run in order, so a quick hide/show never lands reversed.
  let hostChain: Promise<unknown> = Promise.resolve();
  function host<T>(command: string, args?: Record<string, unknown>): Promise<T> {
    const run = hostChain.then(() => invoke<T>(command, args));
    hostChain = run.catch(() => undefined);
    return run;
  }

  function pageBounds(): Record<string, number> {
    const rect = pageBox.getBoundingClientRect();
    return { x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) };
  }

  /** Something the native page would hide is open: a dialog, the player or the keyword suggestions. */
  function covered(): boolean {
    if (!suggestList.hidden || !player.hidden) return true;
    return [...document.querySelectorAll('[aria-modal="true"]')].some((node) => !node.closest("[hidden]"));
  }

  function syncPage(): void {
    if (opening > 0) return;
    const want = page !== null && browsing && screenShown && !covered() && pageBox.getClientRects().length > 0;
    if (want === pageShown) {
      if (want) syncBounds();
      return;
    }
    pageShown = want;
    window.clearInterval(urlTimer);
    if (!want) {
      void host("discovery_view_hide").catch(() => undefined);
      return;
    }
    const bounds = pageBounds();
    lastBounds = JSON.stringify(bounds);
    host("discovery_view_show", bounds).catch((error) => {
      // The page is gone (its fallback window was closed): back to the results.
      dropPage();
      state.textContent = String(error).slice(0, 200);
    });
    watchAddress();
  }

  /** Keep the native page over the box; `force` re-places it when the window itself moved. */
  function syncBounds(force = false): void {
    if (!pageShown) return;
    const bounds = pageBounds();
    const key = JSON.stringify(bounds);
    if (!force && key === lastBounds) return;
    lastBounds = key;
    void host("discovery_view_bounds", bounds).catch(() => undefined);
  }

  async function readAddress(): Promise<void> {
    try {
      const url = await host<string | null>("discovery_view_url");
      if (!page) return;
      if (url === null) {
        dropPage();
        return;
      }
      urlLabel.textContent = pageAddress(url);
      urlLabel.title = url;
      void keepSeen();
    } catch {
      // The address is informational.
    }
  }

  /**
   * Keep what the user looks at in the page on the result list as they
   * browse: the cards of each list page, and each video they open. Collecting
   * only when asked read whatever page was open then (often a video page,
   * whose suggestions the user never saw), so the list did not match the
   * pages they had looked at.
   */
  let seenBusy = false;
  let seenAt = 0;
  async function keepSeen(): Promise<void> {
    const forPlatform = page?.platform;
    if (!forPlatform || !pageShown || seenBusy || Date.now() - seenAt < 2500) return;
    seenBusy = true;
    try {
      const found = seenOnPage(parseCollected(await host<unknown>("collect_discovery", { platform: forPlatform })));
      if (forPlatform !== platform || page?.platform !== forPlatform || !found.length) return;
      const refreshed = refreshVideos(videos, found);
      const merged = mergeVideos(refreshed, found);
      if (merged.length === videos.length && refreshed.every((video, index) => video === videos[index])) return;
      videos = merged;
      renderResults();
      void translateTitles();
    } catch {
      // The page may be loading; the next look catches up.
    } finally {
      seenAt = Date.now();
      seenBusy = false;
    }
  }

  function watchAddress(): void {
    window.clearInterval(urlTimer);
    void readAddress();
    urlTimer = window.setInterval(() => void readAddress(), 1500);
  }

  // Redraws, dialogs and layout changes anywhere may cover or move the panel; bursts are checked once.
  let checkTimer = 0;
  const pageObserver = typeof MutationObserver === "undefined" ? null : new MutationObserver(() => {
    if (checkTimer) return;
    checkTimer = window.setTimeout(() => {
      checkTimer = 0;
      syncPage();
    }, 30);
  });
  const boxObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => syncBounds());
  function observePage(on: boolean): void {
    pageObserver?.disconnect();
    boxObserver?.disconnect();
    if (!on) return;
    pageObserver?.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden"] });
    boxObserver?.observe(pageBox);
  }
  window.addEventListener("resize", () => syncBounds());
  onWindowMoved(() => syncBounds(true));

  function renderPage(): void {
    const panelOpen = page !== null && browsing;
    main.classList.toggle("browsing", panelOpen);
    panel.hidden = !panelOpen;
    reopenBar.hidden = !(page !== null && !browsing);
    const name = PLATFORMS.find((entry) => entry.id === page?.platform)?.name ?? info().name;
    for (const label of [siteLabel, $("#discover-page-site"), $("#discover-reopen-site")]) label.textContent = name;
    observePage(page !== null && screenShown);
    syncPage();
  }

  /** Forget the page on this side (the host has closed it or will). */
  function dropPage(): void {
    page = null;
    browsing = false;
    pageShown = false;
    window.clearInterval(urlTimer);
    urlLabel.textContent = "";
    urlLabel.title = "";
    renderPage();
  }

  function closePage(): void {
    if (!page) return;
    dropPage();
    void host("discovery_view_close").catch(() => undefined);
  }

  async function runApi(next: Exclude<Mode, { kind: "page" } | null>, append: boolean): Promise<void> {
    mode = next;
    closePage();
    state.textContent = "Đang tải…";
    more.disabled = true;
    try {
      const value = await invoke<unknown>("discover", { platform, action: next.action, query: next.query || null, page: next.page, mid: next.mid ?? null });
      const result = parseVideos(value);
      if (!result.ok) {
        state.textContent = discoveryError(result.code, platform);
        // Search pages work in the real site even when the API is blocked.
        if (next.action !== "popular" && (result.code === "BLOCKED" || result.code === "LOGIN_REQUIRED")) await openPage();
        return;
      }
      videos = append ? mergeVideos(videos, result.items) : result.items;
      nextPage = result.nextPage;
      state.textContent = videos.length ? "" : "Không tìm thấy video.";
      renderResults();
      void translateTitles();
    } catch (error) {
      state.textContent = `Không tải được: ${String(error).slice(0, 160)}`;
    } finally {
      more.disabled = false;
    }
  }

  /** Open the search or creator page in the panel (navigating the page already there). */
  async function openPage(): Promise<void> {
    mode = { kind: "page" };
    more.hidden = true;
    const had = page !== null;
    page = { platform };
    browsing = true;
    opening += 1;
    urlLabel.textContent = "";
    state.textContent = "Đang mở trang…";
    renderPage();
    // Wait for layout so the box has its final size (frames pause in a hidden window).
    await new Promise((resolve) => {
      requestAnimationFrame(resolve);
      window.setTimeout(resolve, 100);
    });
    try {
      const url = await host<string>("open_discovery", { platform, kind: kind.value, query: query.value, ...pageBounds() });
      if (!page) return;
      pageShown = true;
      lastBounds = "";
      urlLabel.textContent = pageAddress(url);
      urlLabel.title = url;
      state.textContent = "Video bạn lướt qua và mở xem trên trang tự vào danh sách kết quả. “Thu thập video trên trang” lấy thêm cả video gợi ý của trang đang mở. Trang đòi đăng nhập thì quét mã QR ngay trong trang.";
      watchAddress();
    } catch (error) {
      if (!had) {
        page = null;
        browsing = false;
      }
      state.textContent = String(error).slice(0, 200);
    } finally {
      opening -= 1;
      // Left the screen or opened a dialog meanwhile: hide it again.
      renderPage();
    }
  }

  function showTranslated(original: string, chinese: string | null): void {
    translatedNote.classList.toggle("warn", chinese === null);
    if (chinese === null) {
      translatedNote.textContent = `Chưa dịch được “${original}” sang tiếng Trung (kiểm tra mạng), đang tìm bằng chữ đã gõ. Bạn có thể gõ thẳng tiếng Trung.`;
      return;
    }
    const lead = document.createElement("b");
    lead.textContent = chinese;
    translatedNote.replaceChildren(`Đã dịch “${original}” → `, lead);
  }

  /** The Chinese keyword for a Vietnamese phrase: built-in topic, cache, web translator, then the user's AI key. */
  async function toChinese(text: string): Promise<string | null> {
    const key = text.toLowerCase();
    const known = topicChinese(text) ?? chineseCache.get(key);
    if (known) return known;
    let chinese: string | null = null;
    try {
      chinese = parseChinese(await invoke<unknown>("translate_query", { text }));
    } catch {
      chinese = null;
    }
    if (!chinese && aiChinese) {
      try {
        chinese = parseChinese({ ok: true, text: await aiChinese(text) });
      } catch {
        chinese = null;
      }
    }
    if (chinese) chineseCache.set(key, chinese);
    return chinese;
  }

  async function search(): Promise<void> {
    hideSuggestions();
    let text = query.value.trim();
    if (!text) {
      state.textContent = kind.value === "creator" ? "Hãy dán link trang kênh." : "Hãy nhập từ khóa.";
      return;
    }
    const request = ++searchRequest;
    translatedNote.textContent = "";
    if (kind.value === "search" && autoChinese.checked && needsChinese(text)) {
      state.textContent = `Đang dịch “${text}” sang tiếng Trung…`;
      const chinese = await toChinese(text);
      // Typed again or switched platform meanwhile: that search wins.
      if (request !== searchRequest) return;
      state.textContent = "";
      showTranslated(text, chinese);
      if (chinese) {
        query.value = chinese;
        text = chinese;
      }
    }
    resetResults();
    if (platform === "bilibili" && kind.value === "search") return runApi({ kind: "api", action: "search", query: text, page: 1 }, false);
    const mid = platform === "bilibili" && kind.value === "creator" ? bilibiliMid(text) : null;
    if (mid) return runApi({ kind: "api", action: "creator", query: "", mid, page: 1 }, false);
    return openPage();
  }

  function hideSuggestions(): void {
    suggestRequest += 1;
    window.clearTimeout(suggestTimer);
    suggestList.hidden = true;
    suggestList.replaceChildren();
  }

  async function suggest(): Promise<void> {
    const text = query.value.trim();
    // Platform suggestions only understand Chinese; Vietnamese is translated on "Tìm".
    if (!text || kind.value !== "search" || !info().suggest || needsChinese(text)) return hideSuggestions();
    const request = ++suggestRequest;
    try {
      const result = parseTrends(await invoke<unknown>("discover", { platform, action: "suggest", query: text }));
      if (request !== suggestRequest || !result.ok || !result.items.length) return;
      suggestList.replaceChildren(...result.items.slice(0, 10).map((item) => {
        const option = document.createElement("li");
        option.role = "option";
        option.textContent = item.keyword;
        option.addEventListener("mousedown", (event) => {
          event.preventDefault();
          query.value = item.keyword;
          void search();
        });
        return option;
      }));
      suggestList.hidden = false;
    } catch {
      // Suggestions are optional.
    }
  }

  function switchPlatform(next: Platform): void {
    if (next === platform) return;
    stopPreview();
    platform = next;
    mode = null;
    searchRequest += 1;
    // The page belongs to the previous platform.
    closePage();
    state.textContent = "";
    hideSuggestions();
    resetResults();
    renderTabs();
    void loadTrends();
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void search();
  });
  kind.addEventListener("change", () => {
    hideSuggestions();
    renderTabs();
  });
  query.addEventListener("input", () => {
    window.clearTimeout(suggestTimer);
    suggestTimer = window.setTimeout(() => void suggest(), 400);
  });
  query.addEventListener("blur", () => window.setTimeout(hideSuggestions, 150));
  query.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideSuggestions();
  });
  popular.addEventListener("click", () => {
    resetResults();
    void runApi({ kind: "api", action: "popular", query: "", page: 1 }, false);
  });
  more.addEventListener("click", () => {
    if (mode?.kind === "api" && nextPage !== null) void runApi({ ...mode, page: nextPage }, true);
  });
  $("#trends-refresh").addEventListener("click", () => void loadTrends(true));
  const pageAction = (command: string) => () => {
    void host(command).catch((error) => { state.textContent = String(error).slice(0, 200); });
    if (command !== "scroll_discovery") window.setTimeout(() => void readAddress(), 600);
  };
  $("#discover-scroll").addEventListener("click", pageAction("scroll_discovery"));
  $("#discover-back").addEventListener("click", pageAction("discovery_view_back"));
  $("#discover-reload").addEventListener("click", pageAction("discovery_view_reload"));
  $("#discover-exit").addEventListener("click", () => {
    closePage();
    state.textContent = "";
  });
  showResults.addEventListener("click", () => {
    browsing = false;
    renderPage();
  });
  $("#discover-reopen").addEventListener("click", () => {
    if (!page) return;
    closePlayer(false);
    browsing = true;
    renderPage();
  });
  const collect = $<HTMLButtonElement>("#discover-collect");
  collect.addEventListener("click", async () => {
    const forPlatform = page?.platform ?? platform;
    collect.disabled = true;
    state.textContent = "Đang đọc trang…";
    try {
      const found = parseCollected(await host<unknown>("collect_discovery", { platform: forPlatform }))
        .map(({ current: _current, ...video }) => video);
      if (forPlatform !== platform) return;
      const before = videos.length;
      videos = mergeVideos(refreshVideos(videos, found), found);
      state.textContent = found.length
        ? `Đã lấy ${videos.length - before} video mới (tổng ${videos.length}). Cuộn thêm rồi thu thập tiếp, hoặc bấm “Xem kết quả”.`
        : "Chưa thấy video trên trang. Đợi trang tải xong hoặc cuộn xuống rồi thử lại.";
      renderResults();
      void translateTitles();
    } catch (error) {
      state.textContent = String(error).slice(0, 200);
    } finally {
      collect.disabled = false;
    }
  });
  selectAll.addEventListener("change", () => {
    for (const video of videos) {
      if (selectAll.checked) selected.add(video.pageUrl);
      else selected.delete(video.pageUrl);
    }
    renderResults();
  });
  $("#discover-clear").addEventListener("click", () => {
    resetResults();
    state.textContent = "";
  });
  addButton.addEventListener("click", async () => {
    const picks = videos.filter((video) => selected.has(video.pageUrl));
    addButton.disabled = true;
    let added = 0;
    const failures: string[] = [];
    for (const video of picks) {
      try {
        // The title as the card shows it names the download and its job.
        await enqueue(video.fetchUrl, downloadTitle(video));
        added += 1;
        selected.delete(video.pageUrl);
      } catch (error) {
        failures.push(String(error).slice(0, 120));
      }
    }
    state.textContent = `Đã đưa ${added} video về Hàng đợi.` + (failures.length ? ` ${failures.length} video lỗi: ${failures[0]}` : ADDED_NOTE);
    renderResults();
  });

  glossToggle.addEventListener("change", () => {
    renderTrends(shownTrends);
    void loadGlosses(shownTrends);
    if (glossToggle.checked) titleFailed.clear();
    paintTitles();
    void translateTitles();
  });

  autoChinese.checked = readStorage(AUTO_ZH_KEY) !== "0";
  autoChinese.addEventListener("change", () => {
    writeStorage(AUTO_ZH_KEY, autoChinese.checked ? "1" : "0");
    translatedNote.textContent = "";
    renderTabs();
  });
  hoverToggle.checked = readStorage(HOVER_KEY) !== "0";
  hoverToggle.addEventListener("change", () => {
    writeStorage(HOVER_KEY, hoverToggle.checked ? "1" : "0");
    if (!hoverToggle.checked) stopPreview();
  });
  scroller.addEventListener("scroll", stopPreview, { passive: true });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopPreview();
  });
  reducedMotion?.addEventListener("change", () => {
    if (reducedMotion.matches) stopPreview();
  });

  playerToggle.addEventListener("click", () => {
    if (playing) setSelected(playing.video, !selected.has(playing.video.pageUrl));
  });
  playerDownload.addEventListener("click", async () => {
    if (!playing) return;
    const { video } = playing;
    playerDownload.disabled = true;
    playerState.textContent = "Đang thêm vào Hàng đợi…";
    try {
      await enqueue(video.fetchUrl, downloadTitle(video));
    } catch (error) {
      if (playing?.video === video) {
        playerState.textContent = `Không thêm được: ${String(error).slice(0, 160)}`;
        playerDownload.disabled = false;
      }
      return;
    }
    setSelected(video, false);
    if (playing?.video === video) closePlayer();
    state.textContent = `Đã đưa 1 video về Hàng đợi.${ADDED_NOTE}`;
  });
  $("#pop-close").addEventListener("click", () => closePlayer());
  player.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      closePlayer();
    }
  });
  player.addEventListener("mousedown", (event) => {
    if (event.target === player) closePlayer();
  });
  $("#topic-chips").replaceChildren(...TOPICS.map((topic) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.textContent = topic.vi;
    chip.title = `Tìm “${topic.zh}”`;
    chip.addEventListener("click", () => {
      kind.value = "search";
      query.value = topic.zh;
      renderTabs();
      void search();
    });
    return chip;
  }));

  renderTabs();
  let loaded = false;
  return {
    show() {
      if (!loaded) {
        loaded = true;
        void loadTrends();
      }
      screenShown = true;
      renderPage();
      query.focus();
    },
    /** The view is leaving the screen: stop anything playing and hide the page (it stays loaded). */
    hide() {
      stopPreview();
      closePlayer(false);
      screenShown = false;
      renderPage();
    },
  };
}
