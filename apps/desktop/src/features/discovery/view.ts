import {
  PLATFORMS, TOPICS, bilibiliMid, parseGlosses, discoveryError, formatCount, formatDuration, mergeVideos, parseCollected, parseTrends, parseVideos,
  type DiscoveredVideo, type Platform, type Trend,
} from "./model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Mode = { kind: "api"; action: "search" | "popular" | "creator"; query: string; mid?: string; page: number } | { kind: "page" } | null;

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing ${selector}`);
  return node;
};

/**
 * Wire the "Khám phá" view. `enqueue` downloads one link into the queue;
 * `glossEngine` says which translator may gloss keywords ("hachimi" only once
 * the user accepted its licence warning).
 */
export function mountDiscovery(invoke: Invoke, enqueue: (url: string) => Promise<void>, glossEngine: () => "argos" | "hachimi"): { show(): void } {
  const tabs = $("#platform-tabs");
  const trendList = $("#trend-list");
  const trendState = $("#trend-state");
  const form = $<HTMLFormElement>("#discover-form");
  const kind = $<HTMLSelectElement>("#discover-kind");
  const query = $<HTMLInputElement>("#discover-query");
  const suggestList = $("#suggest-list");
  const popular = $<HTMLButtonElement>("#discover-popular");
  const browser = $("#discover-browser");
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
  let suggestRequest = 0;
  let trendRequest = 0;
  const trendCache = new Map<Platform, { at: number; items: Trend[] }>();
  const glossToggle = $<HTMLInputElement>("#trend-gloss");
  const glosses = new Map<string, string>();
  let shownTrends: Trend[] = [];
  let glossRequest = 0;

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
      : "Từ khóa tiếng Trung, ví dụ 仙侠 hoặc 美食";
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

  function renderResults(): void {
    results.replaceChildren(...videos.map((video) => {
      const card = document.createElement("li");
      card.className = selected.has(video.pageUrl) ? "video-card selected" : "video-card";
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
      check.setAttribute("aria-label", `Chọn ${video.title}`);
      const meta = document.createElement("div");
      meta.className = "meta";
      const title = document.createElement("span");
      title.className = "title";
      title.textContent = video.title;
      title.title = video.title;
      const sub = document.createElement("span");
      sub.className = "sub";
      sub.textContent = [video.author, video.plays !== null ? `${formatCount(video.plays)} lượt xem` : ""].filter(Boolean).join(" · ");
      meta.append(title, sub);
      card.append(thumb, check, meta);
      const toggle = () => {
        if (selected.has(video.pageUrl)) selected.delete(video.pageUrl);
        else selected.add(video.pageUrl);
        renderSelection();
        card.classList.toggle("selected", selected.has(video.pageUrl));
        check.checked = selected.has(video.pageUrl);
      };
      card.addEventListener("click", (event) => {
        if (event.target !== check) toggle();
      });
      check.addEventListener("change", toggle);
      return card;
    }));
    resultBar.hidden = videos.length === 0;
    more.hidden = !(mode?.kind === "api" && nextPage !== null);
    renderSelection();
  }

  function renderSelection(): void {
    const count = videos.filter((video) => selected.has(video.pageUrl)).length;
    selectedCount.textContent = videos.length ? `Đã chọn ${count}/${videos.length}` : "";
    selectAll.checked = videos.length > 0 && count === videos.length;
    addButton.disabled = count === 0;
    addButton.textContent = count ? `Thêm ${count} video vào hàng đợi` : "Thêm vào hàng đợi";
  }

  function resetResults(): void {
    videos = [];
    selected.clear();
    nextPage = null;
    renderResults();
  }

  async function runApi(next: Exclude<Mode, { kind: "page" } | null>, append: boolean): Promise<void> {
    mode = next;
    browser.hidden = true;
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
    } catch (error) {
      state.textContent = `Không tải được: ${String(error).slice(0, 160)}`;
    } finally {
      more.disabled = false;
    }
  }

  async function openPage(): Promise<void> {
    mode = { kind: "page" };
    try {
      await invoke("open_discovery", { platform, kind: kind.value, query: query.value });
      browser.hidden = false;
      more.hidden = true;
      state.textContent = "";
    } catch (error) {
      state.textContent = String(error).slice(0, 200);
    }
  }

  async function search(): Promise<void> {
    hideSuggestions();
    const text = query.value.trim();
    if (!text) {
      state.textContent = kind.value === "creator" ? "Hãy dán link trang kênh." : "Hãy nhập từ khóa.";
      return;
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
    if (!text || kind.value !== "search" || !info().suggest) return hideSuggestions();
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
    platform = next;
    mode = null;
    browser.hidden = true;
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
  $("#discover-scroll").addEventListener("click", () => {
    void invoke("scroll_discovery").catch((error) => { state.textContent = String(error).slice(0, 200); });
  });
  $("#discover-collect").addEventListener("click", async () => {
    state.textContent = "Đang đọc trang…";
    try {
      const found = parseCollected(await invoke<unknown>("collect_discovery", { platform }));
      const before = videos.length;
      videos = mergeVideos(videos, found);
      state.textContent = found.length
        ? `Đã lấy ${videos.length - before} video mới (tổng ${videos.length}). Cuộn thêm rồi lấy tiếp nếu cần.`
        : "Chưa thấy video trên trang. Đợi trang tải xong hoặc cuộn xuống rồi thử lại.";
      renderResults();
    } catch (error) {
      state.textContent = String(error).slice(0, 200);
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
        await enqueue(video.fetchUrl);
        added += 1;
        selected.delete(video.pageUrl);
      } catch (error) {
        failures.push(String(error).slice(0, 120));
      }
    }
    state.textContent = `Đã đưa ${added} video vào hàng đợi tải.` + (failures.length ? ` ${failures.length} video lỗi: ${failures[0]}` : " Xem tiến độ ở Hàng đợi video.");
    renderResults();
  });

  glossToggle.addEventListener("change", () => {
    renderTrends(shownTrends);
    void loadGlosses(shownTrends);
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
      query.focus();
    },
  };
}
