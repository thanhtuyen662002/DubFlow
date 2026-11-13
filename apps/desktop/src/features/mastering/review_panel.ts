/**
 * "Duyệt cảnh" (Phase 5 MVP): one mastered video's sentences, the footage the
 * AI chose for each, why, and the next best shots. Every change is one host
 * call that saves it in the run (`mreview-<n>.json`); "Render lại" makes the
 * video again with the pins and exclusions, keeping its narration.
 */
import { confirmDialog } from "../../shell/confirm.ts";
import {
  coverageText, evidenceHelp, exclusionText, familyLines, nextInQueue, parseReview, PRIORITY_LABELS, provenanceText, queueSummary, seconds, startUnit, STATE_LABELS,
  unitBadge, unitNumber, wholeShotOf, type ReviewUnit, type ReviewView, type Shot,
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
  const exclusionsHead = el("p", "mreview-label", "Cảnh đã loại");
  exclusionsHead.hidden = true;
  const exclusions = el("ul", "mreview-warnings mreview-exclusions");
  // the review queue: what is left by group, and the way to the next sentence without hunting for it
  const queueBar = el("div", "mreview-queue");
  const queueText = el("p", "hint", "");
  const nextButton = button("Câu tiếp theo ›", "btn primary", "Đi tới câu kế tiếp trong hàng đợi duyệt (câu nghiêm trọng trước)");
  queueBar.append(queueText, nextButton);
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
  side.append(player, playerNote, warnings, exclusionsHead, exclusions);
  const body = el("div", "mreview-body");
  const main = el("div", "mreview-main");
  main.append(queueBar, list);
  body.append(side, main);
  dialog.append(head, context, body, foot);
  backdrop.append(dialog);
  document.body.append(backdrop);

  let view: ReviewView | null = null;
  let current = "";            // the sentence the user is on
  let walked: string[] = [];   // the queue as the user was walking it
  const picking = new Set<string>();  // sentences whose full shot list is open
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
    if (shot.excluded) {
      // left out (by this cut or its whole shot): no pin or use until the user lets it back in (#805)
      card.classList.add("excluded");
      caption.append(el("span", "mreview-bad", `Đã loại${shot.excluded_by ? ` ở câu ${unitNumber(shot.excluded_by)}` : ""}`
        + (shot.parent !== shot.id ? ` (cả cảnh gốc ${shot.parent})` : "")));
      row.append(includeButton(shot));
    } else {
      row.append(...actions);
    }
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

  const SCOPE = target.part ? "ở mọi câu của phần này" : "ở mọi câu của video này";

  /** "Bỏ loại": every exclusion that leaves this shot out goes, as one change (undo brings it back). */
  function includeButton(shot: Shot): HTMLButtonElement {
    // the sentence that made the exclusion; any sentence will do (the engine lets the footage back in for all of them)
    const unitId = shot.excluded_by ?? view?.exclusions.find((item) => item.covers.includes(shot.id))?.by ?? (current || view?.units[0]?.unit_id || "");
    const back = button("Bỏ loại", "btn", `Dùng lại cảnh này ${SCOPE}`);
    back.addEventListener("click", () => void act({ action: "decide", unit: unitId, what: "include", ids: [shot.id] },
      "Đã bỏ loại. Render lại để dùng lại cảnh này."));
    return back;
  }

  function excludeButton(unit: ReviewUnit, shot: Shot, label = "Bỏ cảnh này"): HTMLButtonElement {
    const drop = button(label, "btn danger", `Không dùng cảnh này ${SCOPE}; render lại sẽ chọn cảnh khác`);
    drop.addEventListener("click", () => void act({ action: "decide", unit: unit.unit_id, what: "exclude", ids: [shot.id] },
      "Đã loại cảnh. Bấm render lại để dựng lại."));
    return drop;
  }

  /** "Bỏ cả cảnh gốc": the whole shot a piece was cut from, after the user has seen every piece it leaves out. */
  function wholeButton(unit: ReviewUnit, shot: Shot): HTMLButtonElement | null {
    const whole = view ? wholeShotOf(view, shot) : null;
    if (!whole) return null;
    const drop = button("Bỏ cả cảnh gốc", "btn danger", `Không dùng mọi đoạn cắt từ cảnh gốc ${whole.id} ${SCOPE}`);
    drop.addEventListener("click", () => {
      void confirmDialog({
        title: `Bỏ cả cảnh gốc ${whole.id}?`,
        message: `${seconds(whole.start_ms)}–${seconds(whole.end_ms)} của nguồn, ${whole.pieces.length} đoạn sẽ không được dùng ${SCOPE}:\n`
          + familyLines(whole).join("\n") + "\nCó thể hoàn tác hoặc bấm “Bỏ loại” sau.",
        confirmLabel: "Bỏ cả cảnh gốc", danger: true,
      }).then((yes) => {
        if (yes) void act({ action: "decide", unit: unit.unit_id, what: "exclude", ids: [whole.id] },
          `Đã loại cả cảnh gốc (${whole.pieces.length} đoạn). Bấm render lại để dựng lại.`);
      });
    });
    return drop;
  }

  /** A piece of a split shot, used or not, also offers "Bỏ cả cảnh gốc" (#805 B1: the unused f1 of rc15 W2). */
  function withWhole(actions: HTMLElement[], unit: ReviewUnit, shot: Shot): HTMLElement[] {
    const whole = wholeButton(unit, shot);
    return whole ? [...actions, whole] : actions;
  }

  function unitItem(unit: ReviewUnit): HTMLElement {
    const item = el("li", `mreview-unit${unit.unreviewed ? " open" : ""}`);
    item.id = `mreview-${unit.unit_id}`;
    if (unit.unit_id === current) item.classList.add("current");
    const top = el("div", "mreview-unit-head");
    const badge = unitBadge(unit);
    top.append(el("span", `mreview-badge ${badge.tone}`, badge.text), el("b", "", `Câu ${unitNumber(unit.unit_id)}`),
      el("span", "hint", ` ${seconds(unit.start_ms)}–${seconds(unit.end_ms)} · ${seconds(unit.end_ms - unit.start_ms)} · ${coverageText(unit)}`));
    // the state badge already says "Cần xem lại": the queue chip is added when it says more (serious, or put off)
    if (unit.priority === "critical" || unit.later) {
      const chip = PRIORITY_LABELS[unit.priority ?? "review"];
      top.prepend(el("span", `mreview-badge ${chip.tone} mreview-priority`, unit.later ? `${chip.text} · để sau` : chip.text));
    }
    const text = el("p", "mreview-text", unit.text);
    const help = evidenceHelp(unit);
    const facts = el("p", "hint", [help, ...unit.notes, unit.decision?.invalidated ?? ""].filter(Boolean).join(" "));
    // why a sentence is serious (for "Cần xem" the notes below already say it)
    const why = el("p", "mreview-reasons", unit.priority === "critical" ? `Vì sao nghiêm trọng: ${unit.queue_reasons.join("; ")}` : "");
    why.hidden = unit.priority !== "critical" || !unit.queue_reasons.length;
    if (unit.user_state === "USER_APPROVED" && unit.state !== "READY") {
      facts.textContent += ` Đánh giá của AI vẫn là “${STATE_LABELS[unit.state]?.text ?? unit.state}”.`;
    }
    const play = button("▶ Xem câu này", "btn", "Phát video ở câu này, có cả lời đọc");
    play.addEventListener("click", () => void playAt(unit));
    const approve = button(unit.user_state === "USER_APPROVED" ? "Bỏ duyệt" : "Duyệt cảnh này", "btn",
      "Bạn đã xem và chấp nhận cảnh của câu này (không làm tăng điểm của AI)");
    approve.addEventListener("click", () => void act({ action: "decide", unit: unit.unit_id,
      what: unit.user_state === "USER_APPROVED" ? "unapprove" : "approve" }, "Đã lưu."));
    const later = button(unit.later ? "Bỏ để sau" : "Để sau", "btn", "Xem câu này sau; nó vẫn nằm trong hàng đợi, ở cuối nhóm của nó");
    later.addEventListener("click", () => void act({ action: "decide", unit: unit.unit_id, what: "later" },
      unit.later ? "Đã đưa câu về hàng đợi." : "Đã để sau."));
    const missing = button(unit.decision?.source_missing ? "Bỏ ghi “không có cảnh”" : "Không có cảnh phù hợp", "btn",
      "Ghi nhận rằng footage không có cảnh cho câu này: câu được xuất không có cảnh minh họa riêng (đánh giá của AI không đổi)");
    missing.addEventListener("click", () => void act({ action: "decide", unit: unit.unit_id, what: "missing" },
      unit.decision?.source_missing ? "Đã bỏ ghi nhận." : "Đã ghi: câu này không có cảnh phù hợp."));
    const pick = button(picking.has(unit.unit_id) ? "Ẩn danh sách cảnh" : "Chọn cảnh khác…", "btn",
      "Xem mọi cảnh đã nhập và ghim một cảnh cho câu này");
    pick.addEventListener("click", () => {
      if (picking.has(unit.unit_id)) picking.delete(unit.unit_id); else picking.add(unit.unit_id);
      draw();
    });
    const reset = button("Đặt lại", "link", "Bỏ mọi lựa chọn của bạn ở câu này");
    reset.hidden = !unit.decision;
    reset.addEventListener("click", () => void act({ action: "decide", unit: unit.unit_id, what: "reset" }, "Đã đặt lại câu này."));
    const actions = el("div", "mreview-unit-actions");
    actions.append(play, approve, pick, later, missing, reset);
    const chosen = el("div", "mreview-shots");
    for (const shot of unit.chosen) {
      const pinned = unit.decision?.pin.includes(shot.id);
      const drop = excludeButton(unit, shot, shot.parent !== shot.id ? "Bỏ đoạn này" : "Bỏ cảnh này");
      const whole = wholeButton(unit, shot);
      const keep = button(pinned ? "Đã ghim" : "Ghim", "btn", "Giữ cảnh này cho câu này ở mọi lần dựng sau");
      keep.disabled = Boolean(pinned);
      keep.addEventListener("click", () => void act({ action: "decide", unit: unit.unit_id, what: "pin", ids: [shot.id] }, "Đã ghim."));
      chosen.append(shotCard(shot, pinned ? "Đang dùng (đã ghim)" : "Đang dùng", whole ? [keep, drop, whole] : [keep, drop]));
    }
    if (!unit.chosen.length) chosen.append(el("p", "hint", "Câu này chưa có cảnh."));
    const others = el("div", "mreview-shots alt");
    for (const shot of unit.alternatives) {
      const use = button("Dùng cảnh này", "btn primary", "Ghim cảnh này cho câu; render lại để thấy kết quả");
      use.addEventListener("click", () => void act({ action: "decide", unit: unit.unit_id, what: "pin", ids: [shot.id] },
        "Đã chọn cảnh. Bấm “Render lại video này” để dựng lại."));
      others.append(shotCard(shot, typeof shot.score === "number" ? `Phương án · điểm ${shot.score}` : "Phương án",
        withWhole([use, excludeButton(unit, shot, shot.parent !== shot.id ? "Bỏ đoạn này" : "Bỏ cảnh này")], unit, shot)));
    }
    const altHead = el("p", "mreview-label", unit.alternatives.length ? "Cảnh thay thế" : "Không còn cảnh thay thế");
    item.append(top, text, why, facts, actions, chosen, altHead, others);
    if (picking.has(unit.unit_id) && view) {
      const shown = new Set([...unit.chosen, ...unit.alternatives].map((shot) => shot.id));
      const every = el("div", "mreview-shots alt");
      for (const shot of view.shots.filter((item) => !shown.has(item.id))) {
        const use = button("Dùng cảnh này", "btn primary", "Ghim cảnh này cho câu; render lại để thấy kết quả");
        use.addEventListener("click", () => void act({ action: "decide", unit: unit.unit_id, what: "pin", ids: [shot.id] },
          "Đã chọn cảnh. Bấm render lại để dựng lại."));
        every.append(shotCard(shot, "Cảnh đã nhập",
          withWhole([use, excludeButton(unit, shot, shot.parent !== shot.id ? "Bỏ đoạn này" : "Bỏ cảnh này")], unit, shot)));
      }
      item.append(el("p", "mreview-label", every.childElementCount ? "Mọi cảnh đã nhập" : "Không còn cảnh nào khác trong footage đã nhập"),
        el("p", "hint", "Cần cảnh mới: thêm clip ở trang tạo video rồi tạo lại; hoặc bấm “Không có cảnh phù hợp”."), every);
    }
    return item;
  }

  /** Go to a sentence: show it, mark it, and remember the place (no revision, no undo step). */
  function goTo(unitId: string | null, save = true): void {
    if (!unitId) return;
    current = unitId;
    for (const node of list.querySelectorAll(".mreview-unit.current")) node.classList.remove("current");
    const node = document.getElementById(`mreview-${unitId}`);
    node?.classList.add("current");
    node?.scrollIntoView({ block: "start" });
    if (!save) return;
    const args = { action: "decide", unit: unitId, what: "focus" };
    const saving = target.part ? target.part.review(args)
      : invoke("mastering_review", { outputDir: target.outputDir, video: target.video, ...args });
    void saving.catch(() => undefined);
  }

  function draw(): void {
    if (!view) return;
    const open = view.unreviewed.length;
    queueText.textContent = queueSummary(view);
    nextButton.disabled = !view.queue.length;
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
    // a decision a new plan voided is a warning too (rc7 P3-N4: "Không có cảnh báo." while one was voided)
    const voided = view.units.filter((unit) => unit.user_state === "INVALIDATED").length;
    if (voided) warnings.prepend(el("li", "", `${voided} câu cần chọn lại vì lựa chọn trước không còn hợp lệ.`));
    if (!view.warnings.length && !voided) warnings.append(el("li", "read", "Không có cảnh báo."));
    // every exclusion, what it leaves out now and a way back: no exclusion is wider than it shows (#805)
    exclusions.replaceChildren(...view.exclusions.map((item) => {
      const row = el("li", item.reused.length ? "" : "read");
      row.append(el("span", "", exclusionText(item)));
      const back = button("Bỏ loại", "link", `Dùng lại ${item.whole ? "cả cảnh gốc" : "cảnh"} ${item.id} ${SCOPE}`);
      back.addEventListener("click", () => void act({ action: "decide", unit: item.by, what: "include", ids: [item.id] }, "Đã bỏ loại."));
      row.append(back);
      return row;
    }));
    exclusionsHead.hidden = !view.exclusions.length;
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
    nextButton.addEventListener("click", () => {
      if (!view) return;
      const next = nextInQueue(view, current, walked.length ? walked : view.queue);
      walked = view.queue.slice();
      goTo(next);
    });
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
      if (!view) return;
      walked = view.queue.slice();
      goTo(startUnit(view) ?? view.unreviewed[0] ?? null, false);  // where the user left off, kept across reopen
    });
    void changed;
    close.focus();
  });
}
