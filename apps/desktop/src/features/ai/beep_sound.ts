/**
 * "Nghe thử" for the sound laid over a flagged word (Cài đặt › Dịch phụ đề):
 * the same recipes as engine/dubflow/worker/beep_sounds.py, so what plays
 * here is what the video gets. Pure functions return samples; `playSamples`
 * plays them with Web Audio.
 */
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

function bark(rate: number, seconds: number, startHz: number, seed: number): number[] {
  const random = lcg(seed);
  const count = Math.trunc(seconds * rate);
  const formants: Array<[number, number, number]> = [[650, 220, 1], [1450, 260, 0.55], [2700, 400, 0.3]];
  const out: number[] = [];
  let phase = 0, noise = 0;
  for (let frame = 0; frame < count; frame++) {
    const t = frame / rate;
    const f0 = startHz * (0.72 + 0.28 * Math.exp(-t / 0.05)) * (1 - 0.18 * t / seconds);
    phase += 2 * Math.PI * f0 / rate;
    let voice = 0;
    for (let harmonic = 1; harmonic < 14; harmonic++) {
      const freq = harmonic * f0;
      if (freq >= rate / 2) break;
      const weight = formants.reduce((sum, [centre, width, gain]) => sum + gain * Math.exp(-(((freq - centre) / width) ** 2)), 0) + 0.04;
      voice += weight / harmonic ** 0.5 * Math.sin(harmonic * phase);
    }
    noise = 0.6 * noise + 0.4 * (random() * 2 - 1);
    const attack = Math.min(1, t / 0.006);
    const envelope = attack * Math.exp(-t / (seconds * 0.38));
    out.push(envelope * (0.32 * voice + 0.35 * Math.exp(-t / 0.03) * noise));
  }
  const peak = out.reduce((most, value) => Math.max(most, Math.abs(value)), 0) || 1;
  return out.map((value) => 0.5 * value / peak);
}

export function dogClip(rate: number): Float32Array {
  const pause = (seconds: number): number[] => new Array<number>(Math.trunc(seconds * rate)).fill(0);
  return Float32Array.from([...bark(rate, 0.2, 560, 1), ...pause(0.09), ...bark(rate, 0.22, 500, 2), ...pause(0.14)]);
}

/** The built-in sound's samples, or null for a file (played from its own URL). */
export function soundClip(sound: BeepSound, rate: number): Float32Array | null {
  if (sound === "bird") return birdClip(rate);
  if (sound === "dog") return dogClip(rate);
  if (sound === "beep") return toneClip(rate);
  return null;
}

let context: AudioContext | null = null;

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
