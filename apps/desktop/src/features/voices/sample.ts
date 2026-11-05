/**
 * Nghe thử giọng: a short line read by a Vietnamese voice before choosing it.
 * The host makes each voice's sample once (`voice_sample`, the first one may
 * download the voice model) and one sample plays at a time.
 */
type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export type VoiceSampler = {
  /** A round play button for a voice in a list (outside the voice's own button). */
  button(voiceId: string, name: string): HTMLButtonElement;
  /** A "Nghe thử" button that plays whichever voice `select` has chosen. */
  forSelect(select: HTMLSelectElement): HTMLButtonElement;
  stop(): void;
};

const PLAY = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M7 4.5v15l12.5-7.5z" fill="currentColor"/></svg>';
const STOP = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor"/></svg>';
const WAIT = '<span class="voice-sample-spin" aria-hidden="true"></span>';

export function createVoiceSampler(invoke: Invoke, mediaUrl: (path: string) => Promise<string | null>, showError: (text: string) => void): VoiceSampler {
  const audio = new Audio();
  audio.preload = "auto";
  /** The voice playing or being made, and whether it is still being made. */
  let current: string | null = null;
  let waiting = false;
  let ticket = 0;
  const made = new Map<string, string>();
  const buttons = new Set<{ element: HTMLButtonElement; voice: () => string; label: boolean; shown: boolean }>();

  function paint(): void {
    for (const entry of [...buttons]) {
      if (!entry.element.isConnected) {
        // Gone from the page after it was shown (a list drawn again): forget it.
        if (entry.shown) buttons.delete(entry);
        continue;
      }
      entry.shown = true;
      const mine = current !== null && entry.voice() === current;
      const state = mine ? (waiting ? "wait" : "play") : "idle";
      if (entry.element.dataset.state === state) continue;
      entry.element.dataset.state = state;
      entry.element.classList.toggle("on", mine);
      const icon = state === "wait" ? WAIT : state === "play" ? STOP : PLAY;
      entry.element.innerHTML = entry.label ? `${icon}<span>${state === "wait" ? "Đang tạo…" : state === "play" ? "Dừng" : "Nghe thử"}</span>` : icon;
      entry.element.title = state === "wait" ? "Đang tạo giọng mẫu (lần đầu có thể tải giọng đọc, mất một lúc)" : state === "play" ? "Dừng nghe thử" : "Nghe thử giọng này";
    }
  }

  function stop(): void {
    ticket += 1;
    current = null;
    waiting = false;
    audio.pause();
    paint();
  }
  audio.addEventListener("ended", stop);

  async function toggle(voiceId: string): Promise<void> {
    if (!voiceId) return;
    if (current === voiceId) {
      stop();
      return;
    }
    const mine = ++ticket;
    current = voiceId;
    waiting = true;
    audio.pause();
    paint();
    try {
      let url = made.get(voiceId);
      if (!url) {
        const path = await invoke<string>("voice_sample", { voiceId });
        url = (await mediaUrl(path)) ?? undefined;
        if (!url) throw new Error("Không phát được giọng mẫu");
        made.set(voiceId, url);
      }
      if (mine !== ticket) return;
      waiting = false;
      audio.src = url;
      audio.currentTime = 0;
      paint();
      await audio.play();
    } catch (error) {
      if (mine !== ticket) return;
      stop();
      showError(String(error).replace(/^Error:\s*/, "").slice(0, 240));
    }
  }

  function register(element: HTMLButtonElement, voice: () => string, label: boolean): HTMLButtonElement {
    element.type = "button";
    const entry = { element, voice, label, shown: false };
    buttons.add(entry);
    element.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      void toggle(voice());
    });
    element.innerHTML = label ? `${PLAY}<span>Nghe thử</span>` : PLAY;
    element.title = "Nghe thử giọng này";
    return element;
  }

  return {
    button(voiceId, name) {
      const element = document.createElement("button");
      element.className = "icon-btn voice-sample";
      element.setAttribute("aria-label", `Nghe thử giọng ${name}`);
      return register(element, () => voiceId, false);
    },
    forSelect(select) {
      const element = document.createElement("button");
      element.className = "btn small voice-sample-btn";
      element.setAttribute("aria-label", "Nghe thử giọng đã chọn");
      // Another voice chosen while one plays: the button shows Nghe thử for the new one.
      select.addEventListener("change", () => paint());
      return register(element, () => select.value, true);
    },
    stop,
  };
}
