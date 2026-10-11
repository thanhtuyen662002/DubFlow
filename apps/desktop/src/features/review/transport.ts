import type { ReviewTransport } from "./connection.ts";
import type { ReviewCueRequest } from "./session.ts";

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
  let current: { jobId: string; id: string; signal: AbortSignal } | null = null;
  const cancel = (id: string) => { void invoke("review_cancel_v1", { requestId: id }).catch(() => {}); };
  return {
    snapshot: async (jobId, signal) => {
      if (signal.aborted) throw aborted();
      const binding = { jobId, id: nextId(), signal };
      const release = () => { cancel(binding.id); if (current === binding) current = null; };
      signal.addEventListener("abort", release, { once: true });
      try {
        const response = await invoke<unknown>("review_snapshot_v1", { jobId, requestId: binding.id });
        if (signal.aborted) throw aborted();
        if (!response || typeof response !== "object" || Array.isArray(response)) throw new Error("invalid review session");
        const session = response as Record<string, unknown>;
        if (Object.keys(session).sort().join(",") !== "kind,schema_version,session_id,snapshot" ||
          session.schema_version !== 1 || session.kind !== "review_desktop_session" || session.session_id !== binding.id) throw new Error("invalid review session");
        current = binding;
        return session.snapshot;
      } catch (error) {
        release(); signal.removeEventListener("abort", release); throw error;
      }
      // Keep the listener while this job is selected, including after loading.
    },
    preview: async (request: ReviewCueRequest, signal) => {
      const binding = current;
      if (!binding || binding.jobId !== request.job_id || binding.signal.aborted || signal.aborted) throw aborted();
      const child = new AbortController();
      const id = nextId(), cancelRead = () => { child.abort(); cancel(id); };
      signal.addEventListener("abort", cancelRead, { once: true });
      binding.signal.addEventListener("abort", cancelRead, { once: true });
      try {
        if (signal.aborted || binding.signal.aborted) { cancelRead(); throw aborted(); }
        const bytes = await invoke<ArrayBuffer>("review_audio_v1", { sessionId: binding.id, requestId: id, request: { ...request } });
        if (signal.aborted || binding.signal.aborted || current !== binding) throw aborted();
        if (!(bytes instanceof ArrayBuffer) || bytes.byteLength < 44 || bytes.byteLength > 64 * 1024 * 1024) throw new Error("invalid review audio response");
        await play(bytes, child.signal);
        if (signal.aborted || binding.signal.aborted || current !== binding) throw aborted();
      } finally { signal.removeEventListener("abort", cancelRead); binding.signal.removeEventListener("abort", cancelRead); }
    },
  };
}
