import { type ReviewActivityObserver } from "./request-activity.ts";
import { acceptCorrectionResponse, correctionRequest, previewRequest, samePreviewRequest, type ReviewCorrectionRequest, type ReviewPreviewRequest } from "./session.ts";
import { previewProductionCorrection, readReviewPayload, type DisplaySnapshot } from "./wire.ts";
import type { ReviewActions, ReviewVoice } from "./view.ts";
import { currentPlanRequest, sameCurrentPlanRequest, readCurrentPlan, type CurrentPlanRequest } from "./current-plan.ts";
import { readCurrentConfirmation, readAppliedCurrent, type CurrentConfirmation } from "./current-apply.ts";
import { readCurrentUndoRequest, readUndoneCurrent, type CurrentUndoRequest } from "./current-undo.ts";
import { readCurrentExportRequest, readExportedCurrent, type CurrentExportRequest, type CurrentExportReceipt } from "./current-export.ts";
import { readCurrentInitializeRequest, readInitializedCurrent, type CurrentInitializeRequest } from "./current-initialize.ts";
import { readResultVideoRequest, type ResultVideoRequest, type ResultVideoLaunch } from "./result-video.ts";

/** The native host resolves committed pins and owns playback/durable writes. */
export type ReviewTransport = {
  snapshot(jobId: string, signal: AbortSignal): Promise<unknown>;
  preview(request: ReviewPreviewRequest, signal: AbortSignal): Promise<void>;
  openResult?: (request: ResultVideoRequest, signal: AbortSignal) => Promise<ResultVideoLaunch>;
  correct?: (request: ReviewCorrectionRequest, signal: AbortSignal) => Promise<unknown>;
  planCurrent?: (request: CurrentPlanRequest, signal: AbortSignal) => Promise<unknown>;
  applyCurrent?: (confirmation: CurrentConfirmation, signal: AbortSignal, observe?: ReviewActivityObserver) => Promise<unknown>;
  undoCurrent?: (request: CurrentUndoRequest, signal: AbortSignal, observe?: ReviewActivityObserver) => Promise<unknown>;
  exportCurrent?: (request: CurrentExportRequest, signal: AbortSignal, observe?: ReviewActivityObserver) => Promise<unknown>;
  initializeCurrent?: (request: CurrentInitializeRequest, signal: AbortSignal, observe?: ReviewActivityObserver) => Promise<unknown>;
};

function cancelled(): Error { return new DOMException("Review selection changed", "AbortError"); }

/** Selection lifecycle shared by the real desktop host and its component harness. */
export class ReviewConnection {
  private current: DisplaySnapshot | null = null;
  private generation = 0;
  private abort = new AbortController();
  private disposed = false;
  private correcting: number | null = null;
  private standardExport: CurrentExportReceipt | undefined;
  private readonly transport: ReviewTransport;

  constructor(transport: ReviewTransport) { this.transport = transport; }

  async load(jobId: string): Promise<DisplaySnapshot | null> {
    if (this.disposed) throw new Error("review connection was disposed");
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(jobId)) throw new Error("review job ID is invalid");
    this.clear();
    const generation = this.generation, signal = this.abort.signal;
    try {
      const raw = await this.transport.snapshot(jobId, signal);
      if (generation !== this.generation || signal.aborted || this.disposed) return null;
      const snapshot = readReviewPayload(raw);
      if (snapshot.schema_version === 1 || snapshot.job_id !== jobId) throw new Error("review snapshot does not belong to selected job");
      this.current = snapshot;
      return structuredClone(snapshot);
    } catch (error) {
      if (generation !== this.generation || signal.aborted || this.disposed) return null;
      this.current = null;
      throw error;
    }
  }

  snapshot(): DisplaySnapshot | null { return structuredClone(this.current); }

  actions(voices: readonly ReviewVoice[] = []): ReviewActions {
    if (!this.current || this.disposed) throw new Error("review has not loaded");
    const currentB1 = this.current.schema_version === 3 && this.current.mode !== "b2";
    const generation = this.generation, jobSignal = this.abort.signal;
    const bound = (request: ReviewPreviewRequest) => {
      if (generation !== this.generation || jobSignal.aborted || !this.current || this.disposed) throw cancelled();
      const snapshot = this.current;
      const finding = snapshot.findings.find(item => item.finding_id === request.finding_id);
      if (!finding || !samePreviewRequest(previewRequest(snapshot, request.finding_id), request)) {
        throw new Error("review request does not match current job/revision/cue");
      }
      return { snapshot, finding };
    };
    const observeBound = (notify?: ReviewActivityObserver): ReviewActivityObserver | undefined => notify ? update => {
      if (generation === this.generation && !jobSignal.aborted && this.current && !this.disposed && this.correcting === generation) notify(update);
    } : undefined;
    const result: ReviewActions = { voices: voices.map(voice => ({ ...voice })), refreshed: () => this.actions(voices) };
    if (this.transport.openResult) result.openResult = async (request, viewSignal) => {
      const assertBound = () => {
        if (generation !== this.generation || jobSignal.aborted || viewSignal.aborted || !this.current || this.disposed || this.correcting === generation) throw cancelled();
        return readResultVideoRequest(this.current, request);
      };
      const accepted = assertBound(), child = new AbortController(), cancel = () => child.abort();
      jobSignal.addEventListener("abort", cancel, { once: true }); viewSignal.addEventListener("abort", cancel, { once: true });
      try {
        if (jobSignal.aborted || viewSignal.aborted) throw cancelled();
        const launched = await this.transport.openResult!(accepted, child.signal);
        if (child.signal.aborted) throw cancelled();
        assertBound(); return launched;
      } finally { jobSignal.removeEventListener("abort", cancel); viewSignal.removeEventListener("abort", cancel); }
    };
    if (this.transport.initializeCurrent && this.current.schema_version === 2 && this.current.revision === 0) result.initializeCurrent = async (request, viewSignal, observe) => {
      if (generation !== this.generation || jobSignal.aborted || !this.current || this.disposed || this.current.schema_version !== 2) throw cancelled();
      if (this.correcting === generation) throw new Error("review mutation is already in progress");
      const previous = structuredClone(this.current), accepted = readCurrentInitializeRequest(previous, request);
      const child = new AbortController(), cancel = () => child.abort();
      jobSignal.addEventListener("abort", cancel, { once: true }); viewSignal.addEventListener("abort", cancel, { once: true });
      this.correcting = generation;
      try {
        if (jobSignal.aborted || viewSignal.aborted) throw cancelled();
        const response = await this.transport.initializeCurrent!(accepted, child.signal, observeBound(observe));
        if (generation !== this.generation || jobSignal.aborted || child.signal.aborted || !this.current || this.disposed) throw cancelled();
        const next = readInitializedCurrent(previous, accepted, response);
        this.current = next; this.standardExport = undefined; return structuredClone(next);
      } catch (error) {
        if (generation === this.generation) this.clear();
        throw error;
      } finally {
        jobSignal.removeEventListener("abort", cancel); viewSignal.removeEventListener("abort", cancel);
        if (this.correcting === generation) this.correcting = null;
      }
    };
    if (this.transport.planCurrent && this.current.schema_version === 3) result.planCurrent = async (request, viewSignal) => {
      const assertBound = () => {
        if (generation !== this.generation || jobSignal.aborted || !this.current || this.disposed || this.current.schema_version !== 3) throw cancelled();
        const expected = currentPlanRequest(this.current, { finding_id: request.finding_id, ...request.changes });
        if (!sameCurrentPlanRequest(expected, request)) throw new Error("current plan request does not match actual token/cue/changes");
        return this.current;
      };
      assertBound();
      const child = new AbortController(), cancel = () => child.abort();
      jobSignal.addEventListener("abort", cancel, { once: true }); viewSignal.addEventListener("abort", cancel, { once: true });
      try {
        if (jobSignal.aborted || viewSignal.aborted) throw cancelled();
        const response = await this.transport.planCurrent!(structuredClone(request), child.signal);
        if (child.signal.aborted) throw cancelled();
        return readCurrentPlan(assertBound(), request, response);
      } finally { jobSignal.removeEventListener("abort", cancel); viewSignal.removeEventListener("abort", cancel); }
    };
    result.preview = async (request, viewSignal) => {
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
      };
    if (currentB1) delete result.preview;
    if (this.transport.applyCurrent && this.current.schema_version === 3) result.applyCurrent = async (confirmation, viewSignal, observe) => {
      if (generation !== this.generation || jobSignal.aborted || !this.current || this.disposed || this.current.schema_version !== 3) throw cancelled();
      if (this.correcting === generation) throw new Error("review correction is already in progress");
      const previous = structuredClone(this.current), accepted = readCurrentConfirmation(previous, confirmation);
      const child = new AbortController(), cancel = () => child.abort();
      jobSignal.addEventListener("abort", cancel, { once: true }); viewSignal.addEventListener("abort", cancel, { once: true });
      this.correcting = generation;
      try {
        if (jobSignal.aborted || viewSignal.aborted) throw cancelled();
        const response = await this.transport.applyCurrent!(accepted, child.signal, observeBound(observe));
        if (generation !== this.generation || jobSignal.aborted || child.signal.aborted || !this.current || this.disposed) throw cancelled();
        const next = readAppliedCurrent(previous, accepted, response);
        this.current = next; this.standardExport = undefined; return structuredClone(next);
      } catch (error) {
        // A failed/cancelled transport may already have committed. Require fresh
        // native discovery and never automatically resubmit an uncertain edit.
        if (generation === this.generation) this.clear();
        throw error;
      } finally {
        jobSignal.removeEventListener("abort", cancel); viewSignal.removeEventListener("abort", cancel);
        if (this.correcting === generation) this.correcting = null;
      }
    };
    if (this.transport.undoCurrent && this.current.schema_version === 3) result.undoCurrent = async (request, viewSignal, observe) => {
      if (generation !== this.generation || jobSignal.aborted || !this.current || this.disposed || this.current.schema_version !== 3) throw cancelled();
      if (this.correcting === generation) throw new Error("review mutation is already in progress");
      const previous = structuredClone(this.current), accepted = readCurrentUndoRequest(previous, request);
      const child = new AbortController(), cancel = () => child.abort();
      jobSignal.addEventListener("abort", cancel, { once: true }); viewSignal.addEventListener("abort", cancel, { once: true });
      this.correcting = generation;
      try {
        if (jobSignal.aborted || viewSignal.aborted) throw cancelled();
        const response = await this.transport.undoCurrent!(accepted, child.signal, observeBound(observe));
        if (generation !== this.generation || jobSignal.aborted || child.signal.aborted || !this.current || this.disposed) throw cancelled();
        const next = readUndoneCurrent(previous, accepted, response);
        this.current = next; this.standardExport = undefined; return structuredClone(next);
      } catch (error) {
        if (generation === this.generation) this.clear();
        throw error;
      } finally {
        jobSignal.removeEventListener("abort", cancel); viewSignal.removeEventListener("abort", cancel);
        if (this.correcting === generation) this.correcting = null;
      }
    };
    if (this.transport.exportCurrent && this.current.schema_version === 3) result.exportCurrent = async (request, viewSignal, observe) => {
      if (generation !== this.generation || jobSignal.aborted || !this.current || this.disposed || this.current.schema_version !== 3) throw cancelled();
      if (this.correcting === generation) throw new Error("review mutation is already in progress");
      const previous = structuredClone(this.current), accepted = readCurrentExportRequest(previous, request);
      if (accepted.format === "editable" && !this.standardExport) throw new Error("publish the current standard bundle before editable export");
      const child = new AbortController(), cancel = () => child.abort();
      jobSignal.addEventListener("abort", cancel, { once: true }); viewSignal.addEventListener("abort", cancel, { once: true });
      this.correcting = generation;
      try {
        if (jobSignal.aborted || viewSignal.aborted) throw cancelled();
        const response = await this.transport.exportCurrent!(accepted, child.signal, observeBound(observe));
        if (generation !== this.generation || jobSignal.aborted || child.signal.aborted || !this.current || this.disposed) throw cancelled();
        const checked = readExportedCurrent(previous, accepted, response, this.standardExport);
        this.current = checked.snapshot;
        if (accepted.format === "standard") this.standardExport = structuredClone(checked.receipt);
        return checked.receipt;
      } catch (error) {
        if (generation === this.generation) this.clear();
        throw error;
      } finally {
        jobSignal.removeEventListener("abort", cancel); viewSignal.removeEventListener("abort", cancel);
        if (this.correcting === generation) this.correcting = null;
      }
    };
    if (this.transport.correct && this.current.schema_version === 2) result.correct = async request => {
      const cue = { job_id: request.job_id, revision: request.revision, finding_id: request.finding_id, cue_id: request.cue_id };
      const { snapshot } = bound(cue);
      if (snapshot.schema_version !== 2) throw new Error("current correction requires its full-token route");
      if (this.correcting === generation) throw new Error("review correction is already in progress");
      const expected = correctionRequest(snapshot, request.correction);
      if (JSON.stringify(expected) !== JSON.stringify(request)) throw new Error("review correction identity changed");
      const plan = previewProductionCorrection(snapshot, request.correction);
      this.correcting = generation;
      try {
        const response = await this.transport.correct!(structuredClone(request), jobSignal);
        bound(cue);
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

  clear(): void { this.generation++; this.abort.abort(); this.abort = new AbortController(); this.current = null; this.correcting = null; this.standardExport = undefined; }
  dispose(): void { this.clear(); this.disposed = true; }
}
