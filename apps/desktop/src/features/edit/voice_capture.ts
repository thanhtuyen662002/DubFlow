/**
 * Thu giọng (Issue #533): starting the microphone without races. The
 * permission prompt can stay open for a while; a second click must not ask
 * again, and closing the editor meanwhile must leave no microphone open and
 * no recording started.
 */

/** What a capture needs of a recorder (MediaRecorder in the app). */
export type CaptureRecorder = { start(timeslice?: number): void };

export type CaptureDeps<R extends CaptureRecorder> = {
  getUserMedia: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
  /** A recorder for `stream`, with its listeners attached; may throw. */
  createRecorder: (stream: MediaStream) => R;
};

export type CaptureResult<R> =
  | { kind: "started"; recorder: R; stream: MediaStream }
  /** Another start is waiting for the microphone. */
  | { kind: "busy" }
  /** Cancelled (the editor closed) while the microphone was being opened. */
  | { kind: "cancelled" }
  /** The microphone could not be opened (no device, permission refused). */
  | { kind: "denied"; error: unknown }
  /** The recorder could not be made or started. */
  | { kind: "failed"; error: unknown };

export const CAPTURE_CONSTRAINTS: MediaStreamConstraints = {
  audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
};

export function stopTracks(stream: MediaStream | null | undefined): void {
  for (const track of stream?.getTracks() ?? []) {
    try {
      track.stop();
    } catch {
      // A track that cannot stop is already gone.
    }
  }
}

export class VoiceCapture<R extends CaptureRecorder> {
  #ticket = 0;
  #starting = false;

  readonly deps: CaptureDeps<R>;

  constructor(deps: CaptureDeps<R>) {
    this.deps = deps;
  }

  /** Whether a start is waiting for the microphone. */
  get starting(): boolean {
    return this.#starting;
  }

  /** Open the microphone and start a recorder on it; every path but "started" leaves no track open. */
  async start(timeslice = 1000, make: (stream: MediaStream) => R = this.deps.createRecorder): Promise<CaptureResult<R>> {
    if (this.#starting) return { kind: "busy" };
    const ticket = ++this.#ticket;
    this.#starting = true;
    const done = (): void => {
      if (ticket === this.#ticket) this.#starting = false;
    };
    let stream: MediaStream;
    try {
      stream = await this.deps.getUserMedia(CAPTURE_CONSTRAINTS);
    } catch (error) {
      done();
      return ticket === this.#ticket ? { kind: "denied", error } : { kind: "cancelled" };
    }
    if (ticket !== this.#ticket) {
      stopTracks(stream);
      return { kind: "cancelled" };
    }
    try {
      const recorder = make(stream);
      recorder.start(timeslice);
      done();
      return { kind: "started", recorder, stream };
    } catch (error) {
      stopTracks(stream);
      done();
      return { kind: "failed", error };
    }
  }

  /** Drop a start that is still waiting; its microphone is closed as soon as it opens. */
  cancel(): void {
    this.#ticket += 1;
    this.#starting = false;
  }
}

/** What a take needs of a recorder (MediaRecorder in the app). */
export type TakeRecorder = CaptureRecorder & {
  stop(): void;
  readonly state: string;
  readonly mimeType: string;
  addEventListener(type: "dataavailable" | "stop", listener: (event: { data?: Blob }) => void): void;
};

/** One recording: its own bytes and where on the output it began. */
export type Take = { readonly at: number; readonly chunks: Blob[]; mimeType: string };

export type TakeState = "idle" | "starting" | "recording" | "finishing";

export type TakesOptions<R extends TakeRecorder> = {
  getUserMedia: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
  /** A recorder for `stream` (no listeners needed); may throw. */
  makeRecorder: (stream: MediaStream) => R;
  /** A take ended: save it. No new take starts until this settles. */
  finished: (take: Take) => Promise<void>;
  /** The state changed (to redraw the record button). */
  changed?: () => void;
};

/**
 * Takes one after another: each owns its chunks and start time, and a new
 * take cannot start from the moment a stop is asked for until the previous
 * take's stop event has come and its save has settled.
 */
export class VoiceTakes<R extends TakeRecorder> {
  readonly options: TakesOptions<R>;
  readonly #capture: VoiceCapture<R>;
  #recorder: R | null = null;
  #stream: MediaStream | null = null;
  #finishing = 0;

  constructor(options: TakesOptions<R>) {
    this.options = options;
    this.#capture = new VoiceCapture<R>({ getUserMedia: options.getUserMedia, createRecorder: options.makeRecorder });
  }

  get state(): TakeState {
    if (this.#recorder) return "recording";
    if (this.#capture.starting) return "starting";
    return this.#finishing > 0 ? "finishing" : "idle";
  }

  /** Start a take at output time `at`; "busy" unless idle. */
  async start(at: number, timeslice = 1000): Promise<CaptureResult<R>> {
    if (this.state !== "idle") return { kind: "busy" };
    const pending = this.#capture.start(timeslice, (stream) => {
      const recorder = this.options.makeRecorder(stream);
      const own: Take = { at, chunks: [], mimeType: "" };
      recorder.addEventListener("dataavailable", (event) => { if (event.data && event.data.size) own.chunks.push(event.data); });
      recorder.addEventListener("stop", () => this.#ended(recorder, own));
      return recorder;
    });
    this.options.changed?.();
    const result = await pending;
    if (result.kind === "started") {
      this.#recorder = result.recorder;
      this.#stream = result.stream;
    }
    this.options.changed?.();
    return result;
  }

  /** Stop the take (or drop a start still waiting for the microphone). */
  stop(): void {
    if (this.#capture.starting) this.#capture.cancel();
    const recorder = this.#recorder;
    if (recorder) {
      this.#recorder = null;
      stopTracks(this.#stream);
      this.#stream = null;
      if (recorder.state !== "inactive") {
        // Blocks the next take until this one's stop event and save are done.
        this.#finishing += 1;
        recorder.stop();
      }
    }
    this.options.changed?.();
  }

  #ended(recorder: R, take: Take): void {
    const asked = this.#recorder !== recorder;
    if (!asked) {
      // The recorder stopped on its own (device lost): end the take here.
      this.#recorder = null;
      stopTracks(this.#stream);
      this.#stream = null;
      this.#finishing += 1;
    }
    take.mimeType = recorder.mimeType;
    void this.options.finished(take).catch(() => undefined).finally(() => {
      this.#finishing = Math.max(0, this.#finishing - 1);
      this.options.changed?.();
    });
  }
}
