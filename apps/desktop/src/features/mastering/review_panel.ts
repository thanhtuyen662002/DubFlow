/**
 * "Duyệt cảnh" (Phase 5 MVP): one mastered video's sentences, the footage the
 * AI chose for each, why, and the next best shots. Every change is one host
 * call that saves it in the run (`mreview-<n>.json`); "Render lại" makes the
 * video again with the pins and exclusions, keeping its narration.
 */
import {
  coverageText, evidenceHelp, parseReview, provenanceText, seconds, STATE_LABELS, unitBadge, unitNumber, type ReviewUnit, type ReviewView, type Shot,
} from "./model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export type ReviewTarget = {
  outputDir: string; video: number; videoPath: string; title: string;
  mediaUrl(path: string): Promise<string | null>; cpuOnly(): boolean;
  /** Called after a re-render starts, so the page can follow its progress. */
  rerendered(): void;
  /** Tổng hợp: one part of a programme, with its own commands and place in the video (absent: a Bán hàng video). */
  part?: PartTarget;
};

/** A part (câu mở màn, mở đầu, a ranked item, kết thúc) of a Tổng hợp programme in the panel. */
export type PartTarget = {
  /** What the part is: "Mục #3 · Tiêu đề" and its narration, shown above the sentences. */
  heading: string; lines: string[];
  /** Where the part starts in the programme video, so ▶ plays the right moment. */
  offsetMs: number;
  review(args: Record<string, unknown>): Promise<unknown>;
  keyframe(name: string): Promise<ArrayBuffer>;
  rerender(): Promise<void>;
  rerenderLabel: string;
  /** The source a shot comes from, as the page names it ("Nguồn 2 của mục 1"). */
  sourceLabel(sourceId: string): string;
};

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function button(text: string, className = "btn", title = ""): HTMLButtonElement {
  const node = el("button", className, text);
  node.type = "button";
  if (title) node.title = title;
  return node;
}

/** Open the panel over the page; resolves when it closes. */
export function openReviewPanel(invoke: Invoke, target: ReviewTarget): Promise<void> {
  const backdrop = el("div", "mreview-backdrop");
  const dialog = el("section", "mreview-dialog");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-label", `Duyệt cảnh: ${target.title}`);
  const head = el("header", "mreview-head");
  const title = el("h2", "", `Duyệt cảnh · ${target.title}`);
  const summary = el("p", "hint mreview-summary");
  const close = button("Đóng", "btn");
  head.append(title, summary, close);
  const context = el("div", "mreview-context");
  if (target.part) {
    context.append(el("b", "", target.part.heading), ...target.part.lines.filter(Boolean).map((line) => el("p", "hint", line)));
  } else {
    context.hidden = true;
  }
  const player = el("video", "mreview-player");
  player.controls = true;
  player.preload = "metadata";
  const playerNote = el("p", "hint", "");
  const warnings = el("ul", "mreview-warnings");
  const list = el("ol", "mreview-units");
  const foot = el("footer", "mreview-foot");
  const status = el("p", "discover-state mreview-state");
  status.setAttribute("role", "status");
  const undo = button("Hoàn tác", "btn", "Bỏ thay đổi gần nhất");
  const accept = el("label", "mreview-accept");
  const acceptBox = el("input");
  acceptBox.type = "checkbox";
  accept.append(acceptBox, document.createTextNode(" Tôi chấp nhận xuất video với các câu chưa duyệt"));
  const rerender = target.part
    ? button(target.part.rerenderLabel, "btn primary", "Dựng lại các phần có thay đổi với các cảnh bạn đã chọn hoặc loại; phần không đổi được dùng lại")
    : button("Render lại video này", "btn primary", "Dựng lại video với các cảnh bạn đã chọn hoặc loại; giữ giọng đọc");
  foot.append(status, accept, undo, rerender);
  const side = el("div", "mreview-side");
  side.append(player, playerNote, warnings);
  const body = el("div", "mreview-body");
  body.append(side, list);
  dialog.append(head, context, body, foot);
  backdrop.append(dialog);
  document.body.append(backdrop);

  let view: ReviewView | null = null;
  let busy = false;
  let changed = false;
  const urls: string[] = [];
  const thumbs = new Map<string, Promise<string | null>>();

  function thumb(name: string | null): Promise<string | null> {
    if (!name) return Promise.resolve(null);
    if (!thumbs.has(name)) {
      const read = target.part ? target.part.keyframe(name) : invoke<ArrayBuffer>("mastering_keyframe", { outputDir: target.outputDir, name });
      thumbs.set(name, read
        .then((bytes) => {
          const url = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
          urls.push(url);
          return url;
        }).catch(() => null));
    }
    return thumbs.get(name)!;
  }

  function shotCard(shot: Shot, label: string, actions: HTMLElement[]): HTMLElement {
    const card = el("figure", `mreview-shot${shot.usable ? "" : " unusable"}`);
    const image = el("img");
    image.alt = shot.summary || `Cảnh ${shot.id}`;
    image.loading = "lazy";
    void thumb(shot.keyframe).then((url) => { if (url) image.src = url; else image.replaceWith(el("span", "mreview-noimage", "Không có ảnh")); });
    const caption = el("figcaption");
    const source = target.part ? target.part.sourceLabel(shot.source_id) : `Clip ${shot.source_id.slice(1)}`;
    caption.append(el("b", "", label), el("span", "hint", ` ${source} · ${seconds(shot.start_ms)}–${seconds(shot.end_ms)}`));
    if (shot.summary) caption.append(el("span", "mreview-summary-text", shot.summary));
    if (shot.problems.length) caption.append(el("span", "mreview-bad", `Ảnh kém: ${shot.problems.join(", ")}`));
    if (shot.reasons?.length) caption.append(el("span", "hint", shot.reasons.slice(0, 2).join("; ")));
    const row = el("div", "mreview-shot-actions");
    row.append(...actions);
    card.append(image, caption, row);
    return card;
  }

  async function act(args: Record<string, unknown>, done = ""): Promise<void> {
    if (busy) return;
    busy = true;
    status.textContent = "Đang lưu…";
    try {
      const raw = target.part ? await target.part.review(args)
        : await invoke<unknown>("mastering_review", { outputDir: target.outputDir, video: target.video, ...args });
      const next = parseReview(raw);
      if (!next) throw new Error("Dữ liệu duyệt không hợp lệ");
      if (args.action !== "view") changed = true;
      view = next;
      draw();
      status.textContent = done;
    } catch (error) {
      status.textContent = String(error).slice(0, 300);
    } finally {
      busy = false;
    }
  }

  async function playAt(unit: ReviewUnit): Promise<void> {
    if (!player.src) {
      const url = await target.mediaUrl(target.videoPath).catch(() => null);
      if (!url) { playerNote.textContent = "Không phát được video này trong ứng dụng."; return; }
      player.src = url;
    }
    const start = ((target.part?.offsetMs ?? 0) + (unit.pieces[0]?.timeline_start ?? unit.start_ms)) / 1000;
    const seek = () => { player.currentTime = start; void player.play().catch(() => undefined); };
    if (player.readyState >= 1) seek(); else player.addEventListener("loadedmetadata", seek, { once: true });
    playerNote.textContent = `Câu ${unitNumber(unit.unit_id)}: ${unit.text}`;
  }

  function unitItem(unit: ReviewUnit): HTMLElement {
    const item = el("li", `mreview-unit${unit.unreviewed ? " open" : ""}`);
    item.id = `mreview-${unit.unit_id}`;
    const top = el("div", "mreview-unit-head");
    const badge = unitBadge(unit);
    top.append(el("span", `mreview-badge ${badge.tone}`, badge.text), el("b", "", `Câu ${unitNumber(unit.unit_id)}`),
      el("span", "hint", ` ${seconds(unit.start_ms)}–${seconds(unit.end_ms)} · ${coverageText(unit)}`));
    const text = el("p", "mreview-text", unit.text);
    const help = evidenceHelp(unit);
    const facts = el("p", "hint", [help, ...unit.notes, unit.decision?.invalidated ?? ""].filter(Boolean).join(" "));
    if (unit.user_state === "USER_APPROVED" && unit.state !== "READY") {
      facts.textContent += ` Đánh giá của AI vẫn là “${STATE_LABELS[unit.state]?.text ?? unit.state}”.`;
    }
    const play = button("▶ Xem câu này", "btn", "Phát video ở câu này, có cả lời đọc");
    play.addEventListener("click", () => void playAt(unit));
    const approve = button(unit.user_state === "USER_APPROVED" ? "Bỏ duyệt" : "Duyệt cảnh này", "btn",
      "Bạn đã xem và chấp nhận cảnh của câu này (không làm tăng điểm của AI)");
    approve.addEventListener("click", () => void act({ action: "decide", unit: unit.unit_id,
      what: unit.user_state === "USER_APPROVED" ? "unapprove" : "approve" }, "Đã lưu."));
    const reset = button("Đặt lại", "link", "Bỏ mọi lựa chọn của bạn ở câu này");
    reset.hidden = !unit.decision;
    reset.addEventListener("click", () => void act({ action: "decide", unit: unit.unit_id, what: "reset" }, "Đã đặt lại câu này."));
    const actions = el("div", "mreview-unit-actions");
    actions.append(play, approve, reset);
    const chosen = el("div", "mreview-shots");
    for (const shot of unit.chosen) {
      const pinned = unit.decision?.pin.includes(shot.id);
      const drop = button("Bỏ cảnh này", "btn danger", "Không dùng cảnh này; render lại sẽ chọn cảnh khác");
      drop.addEventListener("click", () => void act({ action: "decide", unit: unit.unit_id, what: "exclude", ids: [shot.id] },
        "Đã loại cảnh. Bấm “Render lại video này” để dựng lại."));
      const keep = button(pinned ? "Đã ghim" : "Ghim", "btn", "Giữ cảnh này cho câu này ở mọi lần dựng sau");
      keep.disabled = Boolean(pinned);
      keep.addEventListener("click", () => void act({ action: "decide", unit: unit.unit_id, what: "pin", ids: [shot.id] }, "Đã ghim."));
      chosen.append(shotCard(shot, pinned ? "Đang dùng (đã ghim)" : "Đang dùng", [keep, drop]));
    }
    if (!unit.chosen.length) chosen.append(el("p", "hint", "Câu này chưa có cảnh."));
    const others = el("div", "mreview-shots alt");
    for (const shot of unit.alternatives) {
      const use = button("Dùng cảnh này", "btn primary", "Ghim cảnh này cho câu; render lại để thấy kết quả");
      use.addEventListener("click", () => void act({ action: "decide", unit: unit.unit_id, what: "pin", ids: [shot.id] },
        "Đã chọn cảnh. Bấm “Render lại video này” để dựng lại."));
      others.append(shotCard(shot, typeof shot.score === "number" ? `Phương án · điểm ${shot.score}` : "Phương án", [use]));
    }
    const altHead = el("p", "mreview-label", unit.alternatives.length ? "Cảnh thay thế" : "Không còn cảnh thay thế");
    item.append(top, text, facts, actions, chosen, altHead, others);
    return item;
  }

  function draw(): void {
    if (!view) return;
    const open = view.unreviewed.length;
    summary.textContent = `${view.units.length} câu · ${open ? `${open} câu cần xem lại` : "không còn câu cần xem lại"}`
      + (provenanceText(view) ? ` · ${provenanceText(view)}` : "");
    warnings.replaceChildren(...view.warnings.map((warning) => {
      const item = el("li", warning.acknowledged ? "read" : "");
      item.append(el("span", "", warning.text));
      if (!warning.acknowledged) {
        const read = button("Đã xem", "link");
        read.addEventListener("click", () => void act({ action: "ack", warning: warning.id }));
        item.append(read);
      }
      return item;
    }));
    if (!view.warnings.length) warnings.append(el("li", "read", "Không có cảnh báo."));
    const scroll = list.scrollTop;
    list.replaceChildren(...view.units.map(unitItem));
    list.scrollTop = scroll;  // a change keeps the sentence the user is on in view
    undo.disabled = !view.can_undo;
    acceptBox.checked = view.accept_unreviewed;
    accept.hidden = !open;
  }

  return new Promise<void>((resolve) => {
    function finish(): void {
      player.pause();
      player.removeAttribute("src");
      for (const url of urls) URL.revokeObjectURL(url);
      backdrop.remove();
      document.removeEventListener("keydown", onKey);
      resolve();
    }
    function onKey(event: KeyboardEvent): void {
      if (event.key === "Escape" && !document.querySelector(".confirm-backdrop")) finish();
    }
    document.addEventListener("keydown", onKey);
    close.addEventListener("click", finish);
    undo.addEventListener("click", () => void act({ action: "undo" }, "Đã hoàn tác."));
    acceptBox.addEventListener("change", () => void act({ action: "accept", value: acceptBox.checked }));
    rerender.addEventListener("click", () => {
      if (busy) return;
      busy = true;
      status.textContent = "Đang bắt đầu render lại…";
      (target.part ? target.part.rerender()
        : invoke("product_rerender", { outputDir: target.outputDir, video: target.video, cpuOnly: target.cpuOnly() }))
        .then(() => { target.rerendered(); finish(); })
        .catch((error) => { status.textContent = String(error).slice(0, 300); busy = false; });
    });
    void act({ action: "view" }).then(() => {
      changed = false;
      const first = view?.unreviewed[0];
      if (first) document.getElementById(`mreview-${first}`)?.scrollIntoView({ block: "start" });
    });
    void changed;
    close.focus();
  });
}
