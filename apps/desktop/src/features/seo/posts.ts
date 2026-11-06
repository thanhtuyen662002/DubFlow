import { isKeyError, PROVIDERS, type AiSession } from "../ai/provider.ts";
import { appendCredit, sourceCreditForJob, sourceRightsHint, type SourceCredit } from "../credit/model.ts";
import { parseOffline } from "./view.ts";
import { fitTags, PLATFORM_ORDER, PLATFORM_RULES, platformPostsFromBase, postText, toHashtag, writePlatformPosts, type Platform, type PlatformPost, type PlatformPosts } from "./platforms.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Job = { id: string; outputPath: string | null; displayName: string; originUrl?: string; status: { state: string } };

const CJK = /[㐀-鿿]/;
const count = (text: string) => [...text].length;

/** Offline engine keywords (YouTube tags), when it sent any. */
export function offlineKeywords(value: unknown): string[] {
  const keywords = (value as { keywords?: unknown } | null)?.keywords;
  return Array.isArray(keywords) ? keywords.filter((item): item is string => typeof item === "string") : [];
}

/** Tạo thêm → Nội dung đăng bài: titles, SEO description and hashtags per platform. */
export function mountPostContent(invoke: Invoke, session: AiSession): { select(job: Job | null): void } {
  const $ = <T extends HTMLElement>(id: string): T => {
    const node = document.querySelector<T>(`#posts-${id}`);
    if (!node) throw new Error(`Missing posts-${id}`);
    return node;
  };
  const section = $<HTMLDetailsElement>("section"), ai = $<HTMLButtonElement>("ai"), offline = $<HTMLButtonElement>("offline");
  const state = $("state"), creditHint = $("credit"), content = $("content"), provider = $("provider");
  const tabs = [...document.querySelectorAll<HTMLButtonElement>("#posts-tabs [data-platform]")];
  let selection = "", path = "", title = "", originUrl = "", generation = 0, busy = false, loaded = false;
  let platform: Platform = "youtube";
  let posts: PlatformPosts | null = null;
  const chosen: Record<Platform, number> = { youtube: 0, facebook: 0, tiktok: 0 };
  let credit: Promise<SourceCredit> | null = null;

  function disclosure(): void {
    const { label } = PROVIDERS[session.provider()];
    provider.textContent = `“Viết bằng AI” gửi phụ đề tiếng Việt tới ${label} bằng khóa của bạn (Cài đặt AI). “Tạo nhanh” làm ngay trên máy, không gửi gì đi.`;
  }

  function controls(): void {
    ai.disabled = busy || !path;
    offline.disabled = busy || !path;
  }

  function copyButton(text: () => string, label = "Sao chép", className = "link copy"): HTMLButtonElement {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = label;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(text());
        button.textContent = "Đã chép";
      } catch {
        button.textContent = "Không chép được";
      }
      window.setTimeout(() => { button.textContent = label; }, 1500);
    });
    return button;
  }

  function block(label: string, copy: () => string, counter?: () => string): { root: HTMLElement; meter: HTMLElement } {
    const root = document.createElement("section");
    root.className = "posts-block";
    const head = document.createElement("div");
    head.className = "posts-head";
    const name = document.createElement("h3");
    name.textContent = label;
    const meter = document.createElement("small");
    meter.className = "posts-count";
    if (counter) meter.textContent = counter();
    head.append(name, meter, copyButton(copy));
    root.append(head);
    return { root, meter };
  }

  function field(multiline: boolean, value: string, rows: number, aria: string, onInput: (text: string) => void): HTMLTextAreaElement | HTMLInputElement {
    const input = multiline ? document.createElement("textarea") : document.createElement("input");
    if (input instanceof HTMLTextAreaElement) input.rows = rows;
    input.value = value;
    input.spellcheck = false;
    input.setAttribute("aria-label", aria);
    input.addEventListener("input", () => onInput(input.value));
    return input;
  }

  function showTabs(): void {
    for (const tab of tabs) tab.setAttribute("aria-selected", String(tab.dataset.platform === platform));
  }

  function render(): void {
    showTabs();
    if (!posts) { content.replaceChildren(); return; }
    const rules = PLATFORM_RULES[platform], post: PlatformPost = posts[platform], current = platform;
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = rules.note;

    const titles = block(rules.titleLabel, () => post.titles[chosen[current]] ?? "");
    const list = document.createElement("div");
    list.className = "posts-titles";
    list.setAttribute("role", "radiogroup");
    list.setAttribute("aria-label", `Chọn ${rules.titleLabel.toLocaleLowerCase("vi")}`);
    post.titles.forEach((text, index) => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "posts-title";
      option.setAttribute("role", "radio");
      option.setAttribute("aria-checked", String(index === chosen[current]));
      const words = document.createElement("span");
      words.textContent = text;
      const size = document.createElement("small");
      size.textContent = `${count(text)}/${rules.titleMax}`;
      option.append(words, size);
      option.addEventListener("click", () => { chosen[current] = index; render(); });
      list.append(option);
    });
    titles.root.append(list);

    const descriptionCount = () => `${count(post.description)}/${rules.descriptionMax}`;
    const description = block(platform === "tiktok" ? "Chú thích" : "Mô tả", () => post.description, descriptionCount);
    description.root.append(field(true, post.description, platform === "tiktok" ? 2 : 8, "Mô tả", (text) => {
      post.description = text.slice(0, rules.descriptionMax);
      description.meter.textContent = descriptionCount();
    }));

    const tagsCount = () => `${post.hashtags.length} hashtag`;
    const hashtags = block("Hashtag", () => post.hashtags.join(" "), tagsCount);
    hashtags.root.append(field(false, post.hashtags.join(" "), 1, "Hashtag", (text) => {
      post.hashtags = [...new Set(text.split(/[\s,]+/).map(toHashtag).filter(Boolean))];
      hashtags.meter.textContent = tagsCount();
    }));

    const parts: HTMLElement[] = [note, titles.root, description.root, hashtags.root];
    if (rules.tagsMax) {
      const keywordCount = () => `${post.tags.join(",").length}/${rules.tagsMax}`;
      const keywords = block("Thẻ (tags) YouTube", () => post.tags.join(", "), keywordCount);
      keywords.root.append(field(true, post.tags.join(", "), 3, "Thẻ YouTube", (text) => {
        post.tags = fitTags(text.split(","), rules.tagsMax);
        keywords.meter.textContent = keywordCount();
      }));
      parts.push(keywords.root);
    }
    const all = copyButton(() => postText(current, post, post.titles[chosen[current]] ?? ""), `Sao chép bài đăng ${rules.name}`, "btn block posts-copy-all");
    parts.push(all);
    content.replaceChildren(...parts);
  }

  async function withCredit(next: PlatformPosts): Promise<PlatformPosts> {
    const source = await (credit ?? Promise.resolve(null));
    if (!source) return next;
    for (const name of PLATFORM_ORDER) next[name].description = appendCredit(next[name].description, source);
    return next;
  }

  function show(next: PlatformPosts, message: string): void {
    posts = next;
    for (const name of PLATFORM_ORDER) chosen[name] = 0;
    state.textContent = message;
    render();
  }

  async function runOffline(): Promise<void> {
    if (busy || !path) return;
    const token = generation, outputPath = path;
    busy = true; controls();
    state.textContent = "Đang tạo nội dung từ phụ đề…";
    try {
      const value = await invoke<unknown>("post_suggestions", { outputPath, sourceTitle: CJK.test(title) ? "" : title });
      if (token !== generation) return;
      const base = parseOffline(value);
      if (!base) { state.textContent = "Chưa tạo được nội dung cho video này. Thử “Viết bằng AI”."; return; }
      const next = await withCredit(platformPostsFromBase(base, offlineKeywords(value)));
      if (token !== generation) return;
      show(next, "Bản nhanh từ phụ đề. Bấm “Viết bằng AI” để có tiêu đề hot và mô tả SEO hay hơn.");
    } catch (error) {
      if (token === generation) state.textContent = String(error).slice(0, 200);
    } finally {
      if (token === generation) { busy = false; controls(); }
    }
  }

  async function runAi(): Promise<void> {
    if (busy || !path) return;
    disclosure();
    const name = session.provider(), key = session.read(), { name: label, company } = PROVIDERS[name];
    if (!key) { state.textContent = `Hãy lưu khóa ${label} trong “Cài đặt AI” rồi bấm lại.`; return; }
    const token = generation, outputPath = path, source = title;
    busy = true; controls();
    state.textContent = `${label} đang viết tiêu đề, mô tả và hashtag cho 3 nền tảng…`;
    try {
      const cues = await invoke<Array<{ text: string }>>("review_cues", { outputPath });
      if (token !== generation) return;
      const subtitles = cues.map((cue) => cue.text).filter((text) => typeof text === "string" && text.trim());
      if (!subtitles.length) { state.textContent = "Video này chưa có phụ đề tiếng Việt để viết nội dung."; return; }
      const next = await withCredit(await writePlatformPosts(name, key, subtitles, CJK.test(source) ? "" : source));
      if (token !== generation) return;
      show(next, `Do ${label} viết từ phụ đề (đã gửi phụ đề tới ${company}). Chọn tiêu đề, sửa nếu cần rồi sao chép.`);
    } catch (error) {
      if (token !== generation) return;
      state.textContent = isKeyError(error) ? `Khóa ${label} không hợp lệ; hãy lưu lại trong “Cài đặt AI”.` : `Không viết được bằng AI: ${String(error).slice(0, 160)}`;
    } finally {
      if (token === generation) { busy = false; controls(); }
    }
  }

  for (const tab of tabs) {
    tab.addEventListener("click", () => { platform = tab.dataset.platform as Platform; render(); });
    tab.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      event.preventDefault();
      const next = tabs[(tabs.indexOf(tab) + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      platform = next.dataset.platform as Platform;
      render();
      next.focus();
    });
  }
  ai.addEventListener("click", () => void runAi());
  offline.addEventListener("click", () => void runOffline());
  section.addEventListener("toggle", () => {
    disclosure();
    // First open: a quick offline draft so there is something to copy right away.
    if (section.open && !loaded && path) { loaded = true; void runOffline(); }
  });

  return { select(job) {
    disclosure();
    const outputPath = job?.status.state === "COMPLETED" ? job.outputPath : null;
    section.hidden = !outputPath;
    const next = outputPath ? `${job!.id}\n${outputPath}` : "";
    if (selection === next) return;
    selection = next;
    generation += 1;
    busy = false; loaded = false; posts = null;
    path = outputPath ?? "";
    title = (job?.displayName ?? "").replace(/\.[A-Za-z0-9]{2,4}$/, "");
    originUrl = job?.originUrl ?? "";
    state.textContent = ""; creditHint.textContent = ""; creditHint.hidden = true;
    controls(); render();
    if (!outputPath) { credit = null; return; }
    const token = generation;
    credit = invoke<unknown>("source_credit_get", { outputPath }).then((value) => {
      const source = sourceCreditForJob(value, originUrl);
      if (token === generation) { creditHint.textContent = sourceRightsHint(source); creditHint.hidden = !creditHint.textContent; }
      return source;
    }).catch(() => sourceCreditForJob(null, originUrl));
    if (section.open) { loaded = true; void runOffline(); }
  } };
}
