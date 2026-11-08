/**
 * "Nghe thử" for the sound laid over a flagged word (Cài đặt › Dịch phụ đề):
 * the same recipes as engine/dubflow/worker/beep_sounds.py, so what plays
 * here is what the video gets. Pure functions return samples; `playSamples`
 * plays them with Web Audio.
 */
import { DOG_BARK_PCM_BASE64, DOG_BARK_RATE } from "./dog_bark_data.ts";

export type BeepSound = "beep" | "bird" | "dog" | "custom";
export const BEEP_SOUNDS: readonly BeepSound[] = ["beep", "bird", "dog", "custom"];
export const BEEP_SOUND_LABELS: Record<BeepSound, string> = {
  beep: "Tiếng bíp", bird: "Tiếng chim hót", dog: "Tiếng chó sủa", custom: "Âm thanh tự chọn",
};

/** The same generator as beep_sounds._Lcg. */
function lcg(seed: number): () => number {
  let state = seed;
  return () => {
    // (state × 1103515245 + 12345) mod 2^31, split so every product stays exact in a double.
    const high = (state * (1103515245 >>> 16)) % 2147483648;
    state = (high * 65536 + state * (1103515245 & 0xffff) + 12345) % 2147483648;
    return state / 2147483648;
  };
}

export function toneClip(rate: number, seconds = 0.6): Float32Array {
  const count = Math.round(seconds * rate);
  const out = new Float32Array(count);
  const fade = Math.max(1, Math.round(0.008 * rate));
  for (let frame = 0; frame < count; frame++) {
    const ramp = Math.min(1, frame / fade, (count - frame) / fade);
    out[frame] = 0.25 * ramp * Math.sin(2 * Math.PI * 1000 * frame / rate);
  }
  return out;
}

export function birdClip(rate: number): Float32Array {
  const random = lcg(7);
  const out: number[] = [];
  for (let index = 0; index < 6; index++) {
    const length = 0.06 + 0.03 * random();
    const high = 5200 + 600 * random(), low = 2900 + 500 * random();
    const count = Math.trunc(length * rate);
    let phase = 0;
    for (let frame = 0; frame < count; frame++) {
      const t = frame / count;
      const freq = low + (high - low) * Math.exp(-4 * t) + 180 * Math.sin(2 * Math.PI * 38 * frame / rate);
      phase += 2 * Math.PI * freq / rate;
      const envelope = Math.sin(Math.PI * t) ** 0.6;
      out.push(0.42 * envelope * (Math.sin(phase) + 0.18 * Math.sin(2 * phase)));
    }
    const gap = index % 3 !== 2 ? 0.035 : 0.11;
    for (let frame = Math.trunc(gap * rate); frame > 0; frame--) out.push(0);
  }
  return Float32Array.from(out);
}

/** The recorded bark ("gâu gâu"), resampled in straight lines (beep_sounds.dog_clip). */
export function dogClip(rate: number): Float32Array {
  const bytes = Uint8Array.from(atob(DOG_BARK_PCM_BASE64), (char) => char.charCodeAt(0));
  const view = new DataView(bytes.buffer);
  const source = new Float64Array(bytes.length >> 1);
  for (let index = 0; index < source.length; index++) source[index] = view.getInt16(index * 2, true) / 32768;
  const count = Math.floor(source.length * rate / DOG_BARK_RATE);
  const out = new Float32Array(count);
  for (let index = 0; index < count; index++) {
    const position = index * DOG_BARK_RATE / rate;
    const left = Math.trunc(position);
    const right = Math.min(left + 1, source.length - 1);
    out[index] = source[left] + (source[right] - source[left]) * (position - left);
  }
  return out;
}

/** The built-in sound's samples, or null for a file (played from its own URL). */
export function soundClip(sound: BeepSound, rate: number): Float32Array | null {
  if (sound === "bird") return birdClip(rate);
  if (sound === "dog") return dogClip(rate);
  if (sound === "beep") return toneClip(rate);
  return null;
}

let context: AudioContext | null = null;

/** Play a chosen file for `ms`, then stop that very player (a later preview keeps its own time). */
export function playFor(player: { play(): Promise<void>; pause(): void }, ms: number,
  later: (run: () => void, ms: number) => unknown = (run, wait) => window.setTimeout(run, wait)): void {
  void player.play().catch(() => undefined);
  later(() => player.pause(), ms);
}

/** Play samples once (a second press restarts nothing: each press plays its own copy). */
export function playSamples(samples: Float32Array, rate: number): void {
  const Context = (window as unknown as { AudioContext?: typeof AudioContext }).AudioContext;
  if (!Context) return;
  context ??= new Context();
  void context.resume();
  const buffer = context.createBuffer(1, samples.length, rate);
  buffer.getChannelData(0).set(samples);
  const source = context.createBufferSource();
  source.buffer = buffer;
  source.connect(context.destination);
  source.start();
}
