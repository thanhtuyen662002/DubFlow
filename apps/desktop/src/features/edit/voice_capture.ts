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
  async start(timeslice = 1000): Promise<CaptureResult<R>> {
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
      const recorder = this.deps.createRecorder(stream);
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
