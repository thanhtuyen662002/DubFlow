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

/** A two-pole band-pass at `centre` Hz, `width` Hz wide: [gain, a1, a2] (beep_sounds._resonator). */
function resonator(centre: number, width: number, rate: number): [number, number, number] {
  const radius = Math.exp(-Math.PI * width / rate);
  return [1 - radius, 2 * radius * Math.cos(2 * Math.PI * centre / rate), -radius * radius];
}

/** A value moving in straight lines through [time, value] points (beep_sounds._glide). */
function glide(t: number, points: ReadonlyArray<readonly [number, number]>): number {
  if (t <= points[0][0]) return points[0][1];
  for (let index = 1; index < points.length; index++) {
    const [t0, v0] = points[index - 1], [t1, v1] = points[index];
    if (t <= t1) return v0 + (v1 - v0) * (t - t0) / (t1 - t0);
  }
  return points[points.length - 1][1];
}

/** One "gâu" (beep_sounds._bark): a rough glottal buzz through a mouth opening from "w" to "a". */
function bark(rate: number, seconds: number, peakHz: number, seed: number): number[] {
  const random = lcg(seed);
  const count = Math.trunc(seconds * rate);
  const pitch: Array<[number, number]> = [[0, 0.5], [Math.min(0.06, seconds * 0.4), 1], [Math.max(seconds * 0.6, 0.07), 0.67], [seconds, 0.79]];
  const f1: Array<[number, number]> = [[0, 326], [0.022, 1157], [seconds, 1100]];
  const f2: Array<[number, number]> = [[0, 944], [0.022, 1625], [seconds, 2156]];
  const fixed: Array<[number, number, number]> = [[3055, 320, 0.53], [5500, 500, 0.17]];
  const states = [[0, 0], [0, 0], [0, 0], [0, 0]];
  const out: number[] = [];
  let cycle = 0, periods = 0, wobble = 0, previous = 0;
  const hold = seconds * 0.34;
  for (let frame = 0; frame < count; frame++) {
    const t = frame / rate;
    wobble += (0.26 * (random() - 0.5) - wobble) * (25 / rate);
    cycle += peakHz * glide(t, pitch) * (1 + 7 * wobble) / rate;
    if (cycle >= 1) {
      cycle -= 1;
      periods += 1;
    }
    const pulse = cycle < 0.4 ? 0.5 * (1 - Math.cos(Math.PI * cycle / 0.4)) : cycle < 0.56 ? Math.cos(Math.PI * (cycle - 0.4) / 0.32) : 0;
    const slope = (pulse - previous) * rate / (peakHz * 8);
    previous = pulse;
    const strength = periods % 2 === 0 ? 1 : 0.78;
    const noise = (random() * 2 - 1) * (0.36 + 0.64 * pulse);
    const source = strength * slope + 1.28 * noise;
    let sample = 0;
    const bands: Array<[number, number, number]> = [[glide(t, f1), 233, 1], [glide(t, f2), 192, 1.17], ...fixed];
    bands.forEach(([centre, width, gain], index) => {
      const [g, a1, a2] = resonator(centre, width, rate);
      const state = states[index];
      const value = g * source + a1 * state[0] + a2 * state[1];
      state[1] = state[0];
      state[0] = value;
      sample += gain * value;
    });
    const attack = Math.min(1, t / 0.015);
    const tail = t < hold ? 1 : Math.exp(-(t - hold) / (seconds * 0.08));
    out.push(attack * tail * sample);
  }
  const peak = out.reduce((most, value) => Math.max(most, Math.abs(value)), 0) || 1;
  return out.map((value) => 0.55 * value / peak);
}

export function dogClip(rate: number): Float32Array {
  const pause = (seconds: number): number[] => new Array<number>(Math.trunc(seconds * rate)).fill(0);
  return Float32Array.from([...bark(rate, 0.3, 780, 1), ...pause(0.2), ...bark(rate, 0.15, 830, 2), ...pause(0.16)]);
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
