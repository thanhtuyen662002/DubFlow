import { acceptCorrectionResponse, correctionRequest, type ReviewCorrectionRequest, type ReviewCueRequest } from "./session.ts";
import { previewProductionCorrection, readReviewPayload, type ProductionSnapshot } from "./wire.ts";
import type { ReviewActions, ReviewVoice } from "./view.ts";

/** The native host resolves committed pins and owns playback/durable writes. */
export type ReviewTransport = {
  snapshot(jobId: string, signal: AbortSignal): Promise<unknown>;
  preview(request: ReviewCueRequest, signal: AbortSignal): Promise<void>;
  correct?: (request: ReviewCorrectionRequest, signal: AbortSignal) => Promise<unknown>;
};

function cancelled(): Error { return new DOMException("Review selection changed", "AbortError"); }

/** Selection lifecycle shared by the real desktop host and its component harness. */
export class ReviewConnection {
  private current: ProductionSnapshot | null = null;
  private generation = 0;
  private abort = new AbortController();
  private disposed = false;
  private correcting: number | null = null;
  private readonly transport: ReviewTransport;

  constructor(transport: ReviewTransport) { this.transport = transport; }

  async load(jobId: string): Promise<ProductionSnapshot | null> {
    if (this.disposed) throw new Error("review connection was disposed");
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(jobId)) throw new Error("review job ID is invalid");
    this.clear();
    const generation = this.generation, signal = this.abort.signal;
    try {
      const raw = await this.transport.snapshot(jobId, signal);
      if (generation !== this.generation || signal.aborted || this.disposed) return null;
      const snapshot = readReviewPayload(raw);
      if (snapshot.schema_version !== 2 || snapshot.job_id !== jobId) throw new Error("review snapshot does not belong to selected job");
      this.current = snapshot;
      return structuredClone(snapshot);
    } catch (error) {
      if (generation !== this.generation || signal.aborted || this.disposed) return null;
      this.current = null;
      throw error;
    }
  }

  snapshot(): ProductionSnapshot | null { return structuredClone(this.current); }

  actions(voices: readonly ReviewVoice[] = []): ReviewActions {
    if (!this.current || this.disposed) throw new Error("review has not loaded");
    const generation = this.generation, jobSignal = this.abort.signal;
    const bound = (request: ReviewCueRequest) => {
      if (generation !== this.generation || jobSignal.aborted || !this.current || this.disposed) throw cancelled();
      const snapshot = this.current;
      const finding = snapshot.findings.find(item => item.finding_id === request.finding_id);
      if (request.job_id !== snapshot.job_id || request.revision !== snapshot.revision ||
        !finding || finding.cue_id === null || finding.range === null || finding.cue_id !== request.cue_id) {
        throw new Error("review request does not match current job/revision/cue");
      }
      return { snapshot, finding };
    };
    const result: ReviewActions = {
      voices: voices.map(voice => ({ ...voice })),
      preview: async (request, viewSignal) => {
        const { finding } = bound(request);
        if (finding.output_preview === null) throw new Error("cue has no validated preview descriptor");
        const child = new AbortController();
        const cancel = () => child.abort();
        jobSignal.addEventListener("abort", cancel, { once: true });
        viewSignal.addEventListener("abort", cancel, { once: true });
        try {
          if (jobSignal.aborted || viewSignal.aborted) throw cancelled();
          await this.transport.preview({ ...request }, child.signal);
          if (child.signal.aborted) throw cancelled();
          bound(request);
        } finally {
          jobSignal.removeEventListener("abort", cancel);
          viewSignal.removeEventListener("abort", cancel);
        }
      },
    };
    if (this.transport.correct) result.correct = async request => {
      const { snapshot } = bound(request);
      if (this.correcting === generation) throw new Error("review correction is already in progress");
      const expected = correctionRequest(snapshot, request.correction);
      if (JSON.stringify(expected) !== JSON.stringify(request)) throw new Error("review correction identity changed");
      const plan = previewProductionCorrection(snapshot, request.correction);
      this.correcting = generation;
      try {
        const response = await this.transport.correct!(structuredClone(request), jobSignal);
        bound(request);
        const next = acceptCorrectionResponse(snapshot, request, response, plan);
        this.current = next;
        return structuredClone(next);
      } catch (error) {
        // An uncertain write cannot reuse a binding, even outside the DOM view.
        if (generation === this.generation) this.clear();
        throw error;
      } finally {
        if (this.correcting === generation) this.correcting = null;
      }
    };
    return result;
  }

  clear(): void { this.generation++; this.abort.abort(); this.abort = new AbortController(); this.current = null; this.correcting = null; }
  dispose(): void { this.clear(); this.disposed = true; }
}
