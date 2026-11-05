/**
 * Louder than 100 % in the preview: an <audio> element's `volume` stops at 1,
 * so sounds the editor may turn up (Âm thanh clips, Lớp âm thanh) play
 * through a Web Audio gain instead. The element must load with CORS
 * (`crossOrigin`, which DubFlow's media URLs allow) or the gain would only
 * hear silence; when such a load fails, `boosted` hands back a plain element
 * that plays up to 100 %.
 */

type Routed = { gain: GainNode };

let shared: AudioContext | null = null;
const routes = new WeakMap<HTMLMediaElement, Routed>();
const plain = new WeakSet<HTMLMediaElement>();

function context(): AudioContext | null {
  if (shared) return shared;
  try {
    shared = new AudioContext();
  } catch {
    shared = null;
  }
  return shared;
}

/** A new <audio> that can go past 100 % (and below 0, inverted, for Lớp âm thanh). */
export function boostedAudio(): HTMLAudioElement {
  const element = new Audio();
  element.preload = "auto";
  element.crossOrigin = "anonymous";
  const audio = context();
  if (!audio) {
    plain.add(element);
    element.removeAttribute("crossorigin");
    return element;
  }
  try {
    const gain = audio.createGain();
    audio.createMediaElementSource(element).connect(gain).connect(audio.destination);
    routes.set(element, { gain });
  } catch {
    plain.add(element);
    element.removeAttribute("crossorigin");
  }
  return element;
}

/** A plain element in place of a routed one whose CORS load failed. */
export function fallbackAudio(): HTMLAudioElement {
  const element = new Audio();
  element.preload = "auto";
  plain.add(element);
  return element;
}

/** Whether `element` plays through a gain (levels past 100 % and negative ones are heard). */
export function isBoosted(element: HTMLMediaElement): boolean {
  return routes.has(element);
}

/** Play `element` at `level` (1 = 100 %); a plain element is held to 0–100 %. */
export function setLevel(element: HTMLMediaElement, level: number): void {
  const route = routes.get(element);
  const value = Number.isFinite(level) ? level : 0;
  if (route) {
    element.volume = 1;
    element.muted = value === 0;
    if (Math.abs(route.gain.gain.value - value) > 1e-4) route.gain.gain.value = value;
    return;
  }
  element.volume = Math.min(1, Math.max(0, value));
  element.muted = value <= 0;
}

/** Web Audio starts suspended until the page has been used; playing resumes it. */
export function wake(): void {
  if (shared && shared.state === "suspended") void shared.resume().catch(() => undefined);
}
