import { climaxText, parseDubbingQc, qcRows } from "./qc.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

let shown: string | null = null;

/** Fills the Hậu kỳ card for a finished video (hidden when mastering was off or the video is not finished). */
export async function renderDubbingQc(card: HTMLElement, invoke: Invoke, outputPath: string | null): Promise<void> {
  if (!outputPath) {
    shown = null;
    card.hidden = true;
    card.replaceChildren();
    return;
  }
  if (shown === outputPath && !card.hidden) return;
  shown = outputPath;
  let value: unknown = null;
  try {
    value = await invoke<unknown>("dubbing_qc", { outputPath });
  } catch {
    value = null;
  }
  if (shown !== outputPath) return; // another video was selected meanwhile
  draw(card, invoke, outputPath, value);
}

function draw(card: HTMLElement, invoke: Invoke, outputPath: string, value: unknown): void {
  const qc = parseDubbingQc(value);
  card.replaceChildren();
  card.hidden = !qc;
  if (!qc) return;
  const title = document.createElement("h3");
  title.textContent = "Hậu kỳ tự động";
  const list = document.createElement("dl");
  list.className = "mqc-rows";
  for (const row of qcRows(qc)) {
    const term = document.createElement("dt");
    term.textContent = row.label;
    const detail = document.createElement("dd");
    detail.textContent = row.text;
    detail.className = `mqc-${row.tone}`;
    list.append(term, detail);
  }
  card.append(title, list);
  if (qc.climax) {
    const box = document.createElement("div");
    box.className = "mqc-climax";
    const text = document.createElement("p");
    text.textContent = climaxText(qc);
    const actions = document.createElement("div");
    actions.className = "mqc-actions";
    for (const [keep, label] of [[true, "Giữ gợi ý"], [false, "Bỏ gợi ý"]] as const) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn";
      button.textContent = label;
      button.disabled = qc.climaxDecision === (keep ? "keep" : "drop");
      button.addEventListener("click", () => {
        button.disabled = true;
        invoke<unknown>("dubbing_qc_climax", { outputPath, keep })
          .then((next) => { if (shown === outputPath) draw(card, invoke, outputPath, next); })
          .catch((error) => { text.textContent = `Không lưu được: ${String(error).slice(0, 160)}`; button.disabled = false; });
      });
      actions.append(button);
    }
    box.append(text, actions);
    card.append(box);
  }
}
