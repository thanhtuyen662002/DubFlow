import type { ReviewTransport } from "./connection.ts";
import { sameCurrentToken, samePreviewRequest, type NativeCurrentToken, type ReviewPreviewRequest } from "./session.ts";
import { readReviewPayload } from "./wire.ts";
import type { DisplaySnapshot } from "./wire.ts";
import { sameCurrentPlanRequest, type CurrentPlanRequest } from "./current-plan.ts";
import { readCurrentConfirmation, readAppliedCurrent, type CurrentConfirmation } from "./current-apply.ts";
import { readCurrentUndoRequest, readUndoneCurrent, type CurrentUndoRequest } from "./current-undo.ts";
import { readCurrentExportRequest, readExportedCurrent, type CurrentExportRequest, type CurrentExportReceipt } from "./current-export.ts";

export type ReviewInvoke = <T>(command: string, args: Record<string, unknown>) => Promise<T>;
export type ReviewPlay = (bytes: ArrayBuffer, signal: AbortSignal) => Promise<void>;
const aborted = () => new DOMException("Review selection changed", "AbortError");

/** Playback owns only an admitted memory buffer, never an artifact path URL. */
export async function playReviewAudio(bytes: ArrayBuffer, signal: AbortSignal): Promise<void> {
  if (signal.aborted) throw aborted();
  if (!(bytes instanceof ArrayBuffer) || bytes.byteLength < 44 || bytes.byteLength > 64 * 1024 * 1024) throw new Error("invalid review audio response");
  const audio = new Audio();
  const url = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
  try {
    await new Promise<void>((resolve, reject) => {
      let finished = false;
      const finish = (error?: Error) => {
        if (finished) return; finished = true;
        signal.removeEventListener("abort", cancel);
        audio.removeEventListener("ended", ended); audio.removeEventListener("error", failed);
        audio.pause(); audio.removeAttribute("src"); audio.load();
        error ? reject(error) : resolve();
      };
      const cancel = () => finish(aborted()), ended = () => finish();
      const failed = () => finish(new Error("Không phát được âm thanh của câu này."));
      signal.addEventListener("abort", cancel, { once: true });
      audio.addEventListener("ended", ended, { once: true }); audio.addEventListener("error", failed, { once: true });
      if (signal.aborted) { cancel(); return; }
      audio.src = url;
      void audio.play().catch(() => failed());
    });
  } finally { URL.revokeObjectURL(url); }
}

/** The session ID refers to a native pin held in memory, not a browser hash. */
export function nativeReviewTransport(invoke: ReviewInvoke, play: ReviewPlay = playReviewAudio,
  nextId: () => string = () => crypto.randomUUID()): ReviewTransport {
  let current: { jobId: string; id: string; signal: AbortSignal; nativeCurrent: boolean; token: NativeCurrentToken | null; view: DisplaySnapshot | null; applying: boolean; standard?: CurrentExportReceipt } | null = null;
  const cancel = (id: string) => { void invoke("review_cancel_v1", { requestId: id }).catch(() => {}); };
  return {
    snapshot: async (jobId, signal) => {
      if (signal.aborted) throw aborted();
      const binding = { jobId, id: nextId(), signal, nativeCurrent: false, token: null as NativeCurrentToken | null, view: null as DisplaySnapshot | null, applying: false };
      const release = () => { cancel(binding.id); if (current === binding) current = null; };
      signal.addEventListener("abort", release, { once: true });
      try {
        const response = await invoke<unknown>("review_snapshot_v2", { jobId, requestId: binding.id });
        if (signal.aborted) throw aborted();
        if (!response || typeof response !== "object" || Array.isArray(response)) throw new Error("invalid review session");
        const session = response as Record<string, unknown>;
        if (Object.keys(session).sort().join(",") !== "kind,schema_version,session_id,snapshot" ||
          session.schema_version !== 2 || session.kind !== "review_desktop_current_session" || session.session_id !== binding.id) throw new Error("invalid review session");
        if (!session.snapshot || typeof session.snapshot !== "object" || Array.isArray(session.snapshot)) throw new Error("invalid review snapshot");
        const snapshot = readReviewPayload(session.snapshot);
        if (snapshot.schema_version === 1 || snapshot.job_id !== jobId) throw new Error("unsupported native review snapshot");
        binding.nativeCurrent = snapshot.schema_version === 3;
        binding.token = snapshot.schema_version === 3 ? { ...snapshot.current_token } : null;
        binding.view = structuredClone(snapshot);
        current = binding;
        return snapshot;
      } catch (error) {
        release(); signal.removeEventListener("abort", release); throw error;
      }
      // Keep the listener while this job is selected, including after loading.
    },
    preview: async (request: ReviewPreviewRequest, signal) => {
      const binding = current;
      if (!binding || binding.applying || binding.jobId !== request.job_id || binding.signal.aborted || signal.aborted) throw aborted();
      if (binding.nativeCurrent ? !("current_token" in request) || !binding.token || !sameCurrentToken(binding.token, request.current_token) : "current_token" in request) {
        throw new Error("native audio requires the session's complete token binding");
      }
      const expected = binding.nativeCurrent ?
        { job_id: binding.jobId, current_token: binding.token!, finding_id: request.finding_id, cue_id: request.cue_id } :
        { job_id: binding.jobId, revision: (request as { revision: number }).revision, finding_id: request.finding_id, cue_id: request.cue_id };
      if (!samePreviewRequest(expected, request)) throw new Error("native audio request has unsupported fields");
      const child = new AbortController();
      const id = nextId(), cancelRead = () => { child.abort(); cancel(id); };
      signal.addEventListener("abort", cancelRead, { once: true });
      binding.signal.addEventListener("abort", cancelRead, { once: true });
      try {
        if (signal.aborted || binding.signal.aborted) { cancelRead(); throw aborted(); }
        const bytes = await invoke<ArrayBuffer>(binding.nativeCurrent ? "review_audio_v2" : "review_audio_v1", { sessionId: binding.id, requestId: id, request: structuredClone(request) });
        if (signal.aborted || binding.signal.aborted || current !== binding) throw aborted();
        if (!(bytes instanceof ArrayBuffer) || bytes.byteLength < 44 || bytes.byteLength > 64 * 1024 * 1024) throw new Error("invalid review audio response");
        await play(bytes, child.signal);
        if (signal.aborted || binding.signal.aborted || current !== binding) throw aborted();
      } finally { signal.removeEventListener("abort", cancelRead); binding.signal.removeEventListener("abort", cancelRead); }
    },
    planCurrent: async (request: CurrentPlanRequest, signal) => {
      const binding = current;
      if (!binding || binding.applying || !binding.nativeCurrent || !binding.token || binding.jobId !== request.job_id || binding.signal.aborted || signal.aborted) throw aborted();
      const expected = { job_id: binding.jobId, current_token: binding.token, finding_id: request.finding_id, cue_id: request.cue_id, changes: request.changes };
      if (!sameCurrentPlanRequest(expected, request)) throw new Error("native plan requires the complete session token and closed changes");
      const id = nextId(), cancelRead = () => cancel(id);
      signal.addEventListener("abort", cancelRead, { once: true }); binding.signal.addEventListener("abort", cancelRead, { once: true });
      try {
        if (signal.aborted || binding.signal.aborted) { cancelRead(); throw aborted(); }
        const response = await invoke<unknown>("review_plan_v1", { sessionId: binding.id, requestId: id, request: structuredClone(request) });
        if (signal.aborted || binding.signal.aborted || current !== binding) throw aborted();
        return response;
      } finally { signal.removeEventListener("abort", cancelRead); binding.signal.removeEventListener("abort", cancelRead); }
    },
    exportCurrent: async (request: CurrentExportRequest, signal) => {
      const binding = current;
      if (!binding || binding.applying || !binding.nativeCurrent || !binding.token || binding.view?.schema_version !== 3 || binding.signal.aborted || signal.aborted) throw aborted();
      const previous = structuredClone(binding.view), accepted = readCurrentExportRequest(previous, request);
      if (accepted.format === "editable" && !binding.standard) throw new Error("publish current standard output first");
      const id = nextId(), cancelRead = () => cancel(id);
      binding.applying = true;
      signal.addEventListener("abort", cancelRead, { once: true }); binding.signal.addEventListener("abort", cancelRead, { once: true });
      try {
        if (signal.aborted || binding.signal.aborted) { cancelRead(); throw aborted(); }
        const response = await invoke<unknown>("review_export_v1", { sessionId: binding.id, requestId: id, request: accepted });
        if (signal.aborted || binding.signal.aborted || current !== binding) throw aborted();
        const checked = readExportedCurrent(previous, accepted, response, binding.standard);
        if ((response as { session_id: string }).session_id !== id) throw new Error("export native session identity changed");
        binding.id = id; binding.token = { ...checked.snapshot.current_token }; binding.view = structuredClone(checked.snapshot);
        if (accepted.format === "standard") binding.standard = structuredClone(checked.receipt);
        return response;
      } catch (error) {
        cancel(id); if (current === binding) current = null;
        throw error;
      } finally {
        binding.applying = false;
        signal.removeEventListener("abort", cancelRead); binding.signal.removeEventListener("abort", cancelRead);
      }
    },
    undoCurrent: async (request: CurrentUndoRequest, signal) => {
      const binding = current;
      if (!binding || binding.applying || !binding.nativeCurrent || !binding.token || binding.view?.schema_version !== 3 || binding.signal.aborted || signal.aborted) throw aborted();
      const previous = structuredClone(binding.view), accepted = readCurrentUndoRequest(previous, request);
      const id = nextId(), cancelRead = () => cancel(id);
      binding.applying = true;
      signal.addEventListener("abort", cancelRead, { once: true }); binding.signal.addEventListener("abort", cancelRead, { once: true });
      try {
        if (signal.aborted || binding.signal.aborted) { cancelRead(); throw aborted(); }
        const response = await invoke<unknown>("review_undo_v1", { sessionId: binding.id, requestId: id, request: accepted });
        if (signal.aborted || binding.signal.aborted || current !== binding) throw aborted();
        const next = readUndoneCurrent(previous, accepted, response);
        if ((response as { session_id: string }).session_id !== id) throw new Error("undo native session identity changed");
        binding.id = id; binding.token = { ...next.current_token }; binding.view = structuredClone(next); binding.standard = undefined;
        return response;
      } catch (error) {
        cancel(id); if (current === binding) current = null;
        throw error;
      } finally {
        binding.applying = false;
        signal.removeEventListener("abort", cancelRead); binding.signal.removeEventListener("abort", cancelRead);
      }
    },
    applyCurrent: async (confirmation: CurrentConfirmation, signal) => {
      const binding = current;
      if (!binding || binding.applying || !binding.nativeCurrent || !binding.token || binding.view?.schema_version !== 3 || binding.signal.aborted || signal.aborted) throw aborted();
      const previous = structuredClone(binding.view), accepted = readCurrentConfirmation(previous, confirmation);
      const id = nextId(), cancelRead = () => cancel(id);
      binding.applying = true;
      signal.addEventListener("abort", cancelRead, { once: true }); binding.signal.addEventListener("abort", cancelRead, { once: true });
      try {
        if (signal.aborted || binding.signal.aborted) { cancelRead(); throw aborted(); }
        const response = await invoke<unknown>("review_apply_v1", { sessionId: binding.id, requestId: id, confirmation: accepted });
        if (signal.aborted || binding.signal.aborted || current !== binding) throw aborted();
        const next = readAppliedCurrent(previous, accepted, response);
        if ((response as { session_id: string }).session_id !== id) throw new Error("applied native session identity changed");
        binding.id = id; binding.token = { ...next.current_token }; binding.view = structuredClone(next); binding.standard = undefined;
        return response;
      } catch (error) {
        cancel(id); if (current === binding) current = null;
        throw error;
      } finally {
        binding.applying = false;
        signal.removeEventListener("abort", cancelRead); binding.signal.removeEventListener("abort", cancelRead);
      }
    },
  };
}
