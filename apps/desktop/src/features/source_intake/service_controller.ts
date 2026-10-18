/** Current native source-serve connector. No legacy enqueue_sources RPC.
 * Raw URL references live only until first dispatch in this controller's memory.
 * Mount ONE controller/event pump; poll explicitly from the host's observer.
 * Enumeration is not media completion or proof that its worker has exited.
 */
import { parseSourceInput, utf8Bytes, type SourceRejection } from "./model.ts";
import { createSourceEventPump, type MaterializedQueue, type NativeObservation } from "./materialized.ts";

export type ServiceCommand = "source_service_start_v1" | "source_service_state_v1"
  | "source_service_events_v1" | "source_service_send_v1" | "source_service_stop_v1";
export type ServiceInvoke = (command: ServiceCommand, args?: { request: SourceRequest }) => Promise<unknown>;
export type SourceRequest =
  | { command: "start_video"; scan_id: string; provider_id: string; source_ref: string }
  | { command: "items"; scan_id: string; offset: number; limit: number }
  | { command: "download"; scan_id: string; identity_key: string; resume: false }
  | { command: "status"; scan_id: string }
  | { command: "cancel"; scan_id: string };
export type SourcePhase = "queued" | "sending" | "submitted" | "preparing" | "enumerating" | "enumerated"
  | "download_sending" | "download_submitted" | "download_preparing" | "downloading" | "downloaded" | "queue_error"
  | "cancelling" | "cancelled" | "paused" | "failed" | "interrupted";
export type SourceRow = {
  scan_id: string; display_ref: string; provider: string; phase: SourcePhase;
  delivery: "pending" | "submitted" | "unknown" | null;
  enumeration_complete: boolean; enumeration_retired: boolean;
  items: Array<{ identity_key: string; status: string }>;
  selected_identity: string | null; local_queue: "added" | "present" | "error" | null; error: string | null;
};
export type SourceServiceState = {
  service: "idle" | "admitting" | "ready" | "stopping" | "stopped" | "unavailable";
  failure: string | null; retired: boolean; disposed: boolean;
  rows: SourceRow[]; rejected: SourceRejection[];
};
export type SubmitResult = { local_added: number; local_present: number; online_staged: string[]; rejected: SourceRejection[] };
const LIMIT = 10_000;
const terminal = new Set<SourcePhase>(["cancelled", "paused", "failed", "interrupted", "downloaded", "queue_error"]);
function record(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === "object"; }
function id(value: unknown): value is string { return typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/u.test(value); }
function key(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && utf8Bytes(value) <= 1024 && !/\p{Cc}/u.test(value);
}
function code(error: unknown): string {
  // Native/IPC diagnostic strings can contain paths or secrets. Copy only a
  // closed setup category; never persist/render arbitrary exception messages.
  if (typeof error === "string") {
    for (const known of ["SOURCE_SETUP_MISSING", "SOURCE_SETUP_INVALID", "SOURCE_SETUP_POLICY_MISSING", "SOURCE_NOT_STARTED"]) {
      if (error === known || error.startsWith(known + ":")) return known;
    }
  }
  return "SOURCE_IPC_FAILED";
}
function rejection(input: string, reason: string): SourceRejection {
  return { input, line: 0, code: reason, message: reason === "SOURCE_PROVIDER_UNSUPPORTED"
    ? "Nhà cung cấp này chưa được native service hỗ trợ."
    : reason === "LOCAL_QUEUE_REJECTED" ? "Chưa xác nhận lưu vào hàng xử lý; hãy kiểm tra bộ nhớ và danh sách hiện tại."
    : "Nguồn cần được kiểm tra trước khi tiếp tục." };
}
function publicReference(raw: string): boolean {
  // Match f68 public_url before dispatch so one known-invalid reference does
  // not become an uncorrelated SOURCE_REQUEST_REJECTED on the shared stream.
  if (raw.includes("#") || raw.includes("\\") || !/^https?:\/\//u.test(raw)) return false;
  const authority = raw.slice(raw.indexOf("://") + 3).split(/[/?]/u, 1)[0];
  if (!authority || /[@%\s]/u.test(authority)) return false;
  const forbidden = new Set(["token", "access_token", "auth", "authorization", "cookie", "password", "secret",
    "signature", "sig", "key", "api_key", "session", "sessionid", "credential", "credentials",
    "x_amz_credential", "x_amz_security_token", "x_amz_signature", "x_goog_credential",
    "x_goog_signature", "awsaccesskeyid", "client_secret", "oauth_token", "oauth_verifier"]);
  const query = raw.indexOf("?");
  if (query < 0) return true;
  try {
    return raw.slice(query + 1).split("&").every((pair) =>
      !forbidden.has(decodeURIComponent(pair.split("=", 1)[0]).toLowerCase().replaceAll("-", "_")));
  } catch { return false; }
}

export function createSourceServiceController(queue: MaterializedQueue, invoke: ServiceInvoke,
  options: { scanId?: () => string } = {}) {
  const state: SourceServiceState = { service: "idle", failure: null, retired: false, disposed: false, rows: [], rejected: [] };
  const listeners = new Set<(state: SourceServiceState) => void>();
  const references = new Map<string, string>();
  const identities = new Set<string>();
  const itemsRequested = new Set<string>();
  const revisions = new Map<string, number>();
  let active: string | null = null, blocked = false, started = false, stopping = false;
  let starting: Promise<void> | null = null;
  let startPending = false;
  let polling: Promise<SourceServiceState> | null = null;
  let writes: Promise<unknown> = Promise.resolve();
  let lastSequence = 0;
  const snapshot = () => structuredClone(state);
  const rowFor = (scan: unknown) => state.rows.find((row) => row.scan_id === scan);
  function publish() {
    for (const listener of listeners) {
      try { listener(snapshot()); } catch { /* A view cannot stop source/local admission. */ }
    }
  }
  function setFailure(value: string) { state.failure = value; publish(); }
  function readSnapshot(value: unknown) {
    if (!record(value) || !["admitting", "ready", "stopping", "stopped"].includes(String(value.phase))
        || typeof value.retired !== "boolean" || !(value.failure === null || typeof value.failure === "string")) {
      throw new Error("SOURCE_PROTOCOL_INVALID");
    }
    state.service = value.phase as "admitting" | "ready" | "stopping" | "stopped";
    state.retired = value.retired;
    if (value.failure !== null) state.failure = "SOURCE_SERVICE_FAILED";
    if (state.service === "stopped" && active) {
      const row = rowFor(active)!;
      if (!terminal.has(row.phase)) { row.phase = "interrupted"; row.error = "SOURCE_SERVICE_STOPPED"; }
      active = null; blocked = true;
    }
  }
  function send(request: SourceRequest, row: SourceRow): Promise<boolean> {
    const task = writes.then(async () => {
      if (state.disposed || stopping) return false;
      try {
        const reply = await invoke("source_service_send_v1", { request });
        if (state.disposed) return false;
        if (!record(reply) || reply.delivery !== "submitted") throw new Error("SOURCE_PROTOCOL_INVALID");
        row.delivery = "submitted";
        if (request.command === "start_video" && row.phase === "sending") row.phase = "submitted";
        if (request.command === "download" && row.phase === "download_sending") row.phase = "download_submitted";
        publish();
        return true;
      } catch (error) {
        if (!state.disposed) {
          row.delivery = "unknown"; row.error = code(error);
          // A failed/uncertain pipe delivery is not a native rejection. Retain
          // the row and stop online dispatch until explicit diagnosis/control.
          blocked = true; setFailure(row.error);
        }
        return false;
      }
    });
    writes = task;
    return task;
  }
  async function dispatch() {
    if (active || blocked || stopping || state.disposed || state.service !== "ready") return;
    const row = state.rows.find((value) => value.phase === "queued");
    if (!row) return;
    const source = references.get(row.scan_id);
    if (!source) { row.phase = "failed"; row.error = "SOURCE_REFERENCE_UNAVAILABLE"; publish(); return; }
    active = row.scan_id; row.phase = "sending"; row.delivery = "pending";
    references.delete(row.scan_id);
    publish();
    await send({ command: "start_video", scan_id: row.scan_id, provider_id: row.provider, source_ref: source }, row);
  }
  async function ensureStarted() {
    if (starting) return starting;
    if (started || state.disposed || stopping) return;
    // Set before publishing: a subscribed observer may synchronously poll.
    // Deferred native setup has no client/event route until start resolves.
    started = true; startPending = true; state.service = "admitting"; publish();
    starting = (async () => {
      try { const value = await invoke("source_service_start_v1"); if (!state.disposed && !stopping) readSnapshot(value); }
      catch (error) { if (!state.disposed && !stopping) { state.service = "unavailable"; blocked = true; state.failure = code(error); } }
      if (!state.disposed) publish();
    })();
    try { await starting; }
    finally { starting = null; startPending = false; }
  }
  function release(row: SourceRow) { if (active === row.scan_id) active = null; }
  function event(observation: NativeObservation): boolean {
    if (!Number.isSafeInteger(observation.sequence) || observation.sequence <= lastSequence || !record(observation.value)) return false;
    lastSequence = observation.sequence;
    const value = observation.value;
    if (value.event === "source_ready") return false; // snapshot is the host's readiness authority
    if (value.event === "source_shutdown") { state.service = "stopping"; stopping = true; return false; }
    if (value.event === "source_error" && !id(value.scan_id)) {
      // f68 errors have no universal request correlation. Do not assign this
      // error to the most recent command or silently retry that command.
      blocked = true; state.failure = "SOURCE_ERROR_UNCORRELATED"; return false;
    }
    const row = rowFor(value.scan_id);
    if (!row || terminal.has(row.phase)) return false;
    if (value.event === "source_status") {
      if (!Number.isSafeInteger(value.dispatch_revision) || (value.dispatch_revision as number) < 0) {
        state.failure = "SOURCE_EVENT_INVALID"; return false;
      }
      const revision = value.dispatch_revision as number;
      if (revision < (revisions.get(row.scan_id) ?? -1)) return false;
      revisions.set(row.scan_id, revision);
      if (value.status === "cancelled" || value.status === "paused" || value.status === "failed") {
        row.phase = value.status; release(row); return false;
      }
      if (row.phase === "cancelling") return false;
      if (value.status === "completed") {
        row.enumeration_complete = true;
        // Explicit native observation of successful child exit, not a second
        // completed status, items snapshot, pipe write or elapsed-time guess.
        row.enumeration_retired = value.enumeration_retired === true;
        if (!row.selected_identity) row.phase = row.enumeration_retired ? "enumerated" : "enumerating";
      } else if (value.status === "running" && !row.enumeration_complete) row.phase = "enumerating";
    } else if ((value.event === "source_preparation_stopped" || value.event === "source_enumeration_stopped")
        && typeof value.cancelled === "boolean") {
      row.phase = value.cancelled ? "cancelled" : "paused"; release(row);
    } else if (value.event === "source_error" || value.event === "source_download_failed" || value.event === "source_retry_refused") {
      row.phase = "failed"; row.error = "SOURCE_NATIVE_FAILED"; release(row);
    } else if (value.event === "source_download_cancelled" || value.event === "source_download_paused") {
      row.phase = value.event === "source_download_cancelled" ? "cancelled" : "paused"; release(row);
    } else if (value.event === "source_preparing" && row.phase !== "cancelling") row.phase = "preparing";
    else if (value.event === "source_items" && row.enumeration_complete && row.phase !== "cancelling") {
      // start_video admits at most one item. No paging or snapshot-to-receipt
      // promotion: an already-downloaded snapshot still requires reconciliation.
      if (value.offset !== 0 || !Number.isInteger(value.total) || (value.total as number) < 0 || (value.total as number) > 1
          || !Array.isArray(value.items) || value.items.length !== value.total
          || value.items.some((item) => !record(item) || !key(item.identity_key)
            || !["discovered", "downloading", "downloaded", "failed", "skipped", "cancelled"].includes(String(item.status)))) {
        row.error = "SOURCE_ITEMS_INVALID"; return false;
      }
      row.items = value.items.map((item) => ({ identity_key: item.identity_key as string, status: item.status as string }));
      if (value.total === 0) row.error = "SOURCE_NO_ITEMS";
      else if (row.items.some((item) => item.status !== "discovered")) row.error = "SOURCE_RECONCILIATION_REQUIRED";
    } else if (value.event === "source_download_preparing" && row.selected_identity && row.selected_identity === value.identity_key && row.phase !== "cancelling") {
      row.phase = "download_preparing";
    } else if (value.event === "source_download_progress" && row.selected_identity && row.selected_identity === value.identity_key && row.phase !== "cancelling") {
      row.phase = "downloading";
    } else if (value.event === "source_downloaded") {
      // A late receipt after confirmed cancellation, an unknown scan or an
      // identity the user never selected cannot enqueue via the shared pump.
      if (!row.selected_identity || row.selected_identity !== value.identity_key) { row.error = "SOURCE_RECEIPT_UNEXPECTED"; return false; }
      return true;
    }
    return false;
  }
  // Exactly one destructive drain and one existing materialization pump.
  // Process in stream order before allowing only this controller's selected
  // receipts through the pump. Never fabricate replacement native events.
  const pump = createSourceEventPump(queue, async () => {
    const value = await invoke("source_service_events_v1");
    if (state.disposed) return [];
    if (!Array.isArray(value)) throw new Error("SOURCE_PROTOCOL_INVALID");
    const allowed: NativeObservation[] = [];
    for (const observation of value) {
      if (!record(observation)) { state.failure = "SOURCE_EVENT_INVALID"; continue; }
      const acceptedReceipt = event(observation as NativeObservation);
      if (acceptedReceipt) allowed.push(observation as NativeObservation);
    }
    return allowed;
  });
  function poll(): Promise<SourceServiceState> {
    // No event drain while deferred setup has no client, or after observed
    // retirement. Neither boundary is an IPC failure requiring UI diagnosis.
    if (state.disposed || state.retired || startPending || !started || state.service === "unavailable") return Promise.resolve(snapshot());
    if (polling) return polling;
    const task = (async () => {
      try {
        const result = await pump.poll();
        if (state.disposed) return snapshot();
        for (const receipt of [...result.materialized.queued.map((value) => value.source), ...result.materialized.already_present]) {
          const row = rowFor(receipt.scan_id);
          if (row) {
            row.local_queue = result.materialized.queued.some((value) => value.source.receipt_key === receipt.receipt_key) ? "added" : "present";
            if (!terminal.has(row.phase)) { row.phase = "downloaded"; row.error = null; }
            release(row);
          }
        }
        for (const failure of result.materialized.rejected) {
          const receipt = result.events.find((value) => value.sequence === failure.sequence);
          const row = record(receipt?.value) ? rowFor(receipt.value.scan_id) : undefined;
          if (row) {
            row.error = failure.code; row.local_queue = "error";
            if (!terminal.has(row.phase)) row.phase = "queue_error";
            release(row);
          }
          state.failure = failure.code;
        }
        const value = await invoke("source_service_state_v1");
        if (state.disposed) return snapshot();
        readSnapshot(value);
        // Read-only items requests follow observed durable enumeration, once.
        // No automatic materialization or request retry is hidden here.
        for (const row of state.rows) {
          if (row.enumeration_complete && !terminal.has(row.phase) && row.phase !== "cancelling"
              && !row.selected_identity && !itemsRequested.has(row.scan_id) && !blocked && !stopping) {
            itemsRequested.add(row.scan_id);
            await send({ command: "items", scan_id: row.scan_id, offset: 0, limit: 100 }, row);
          }
        }
        await dispatch();
      } catch (error) { if (!state.disposed) setFailure(code(error)); }
      if (!state.disposed) publish();
      return snapshot();
    })();
    polling = task;
    void task.then(() => { if (polling === task) polling = null; }, () => { if (polling === task) polling = null; });
    return task;
  }
  return {
    getState: snapshot,
    subscribe(listener: (state: SourceServiceState) => void): () => void {
      listeners.add(listener); listener(snapshot()); return () => { listeners.delete(listener); };
    },
    async submit(input: string): Promise<SubmitResult> {
      const result: SubmitResult = { local_added: 0, local_present: 0, online_staged: [], rejected: [] };
      if (state.disposed) { result.rejected.push(rejection("", "SOURCE_CONTROLLER_DISPOSED")); return result; }
      const batch = parseSourceInput(input); result.rejected.push(...batch.rejected);
      // Local admission is synchronous and per-item BEFORE any native await.
      for (const item of batch.items.filter((value) => value.kind === "local")) {
        try {
          const added = queue.addPaths([item.local_path!]);
          result.local_added += added.length;
          if (!added.length) result.local_present++;
        } catch { result.rejected.push(rejection(item.display_ref, "LOCAL_QUEUE_REJECTED")); }
      }
      for (const item of batch.items.filter((value) => value.kind === "url")) {
        if (stopping) { result.rejected.push(rejection("URL", "SOURCE_SERVICE_STOPPED")); continue; }
        if (item.display_ref.includes("%5Bredacted%5D") || !publicReference(item.transport_url!)) {
          result.rejected.push(rejection("URL", "SOURCE_PUBLIC_REFERENCE_REQUIRED")); continue;
        }
        if (!["generic", "bilibili", "douyin"].includes(item.provider)) {
          result.rejected.push(rejection(item.display_ref, "SOURCE_PROVIDER_UNSUPPORTED")); continue;
        }
        if (identities.has(item.identity_key)) { result.rejected.push(rejection(item.display_ref, "DUPLICATE_SOURCE")); continue; }
        if (state.rows.length >= LIMIT) { result.rejected.push(rejection(item.display_ref, "QUEUE_LIMIT")); continue; }
        let scan: string;
        try {
          scan = options.scanId?.() ?? ("source-" + crypto.randomUUID());
          if (!id(scan) || rowFor(scan)) throw new Error("SOURCE_ID_INVALID");
        } catch { result.rejected.push(rejection(item.display_ref, "SOURCE_ID_INVALID")); continue; }
        identities.add(item.identity_key); references.set(scan, item.transport_url!);
        state.rows.push({ scan_id: scan, display_ref: item.display_ref, provider: item.provider, phase: "queued",
          delivery: null, enumeration_complete: false, enumeration_retired: false,
          items: [], selected_identity: null, local_queue: null, error: null });
        result.online_staged.push(scan);
      }
      state.rejected = result.rejected.slice(); publish();
      if (result.online_staged.length && !stopping) { await ensureStarted(); await dispatch(); }
      return result;
    },
    poll,
    async refresh(scanId: string): Promise<boolean> {
      const row = rowFor(scanId);
      if (!row || !row.enumeration_complete || terminal.has(row.phase) || row.phase === "cancelling"
          || row.selected_identity || state.service !== "ready" || stopping || state.disposed) return false;
      // Explicit read-only recovery for a lost retirement observation. A
      // submitted query grants nothing; only its observed native status can.
      return send({ command: "status", scan_id: scanId }, row);
    },
    async download(scanId: string, identityKey: string): Promise<boolean> {
      const row = rowFor(scanId);
      if (!row || row.phase !== "enumerated" || !row.enumeration_complete || !row.enumeration_retired || row.selected_identity
          || !row.items.some((item) => item.identity_key === identityKey && item.status === "discovered")
          || active !== scanId || state.service !== "ready" || blocked || stopping || state.disposed) return false;
      row.selected_identity = identityKey; row.phase = "download_sending"; row.delivery = "pending"; publish();
      return send({ command: "download", scan_id: scanId, identity_key: identityKey, resume: false }, row);
    },
    async cancel(scanId: string): Promise<boolean> {
      const row = rowFor(scanId);
      if (!row || terminal.has(row.phase) || row.phase === "cancelling" || state.disposed || stopping) return false;
      if (row.phase === "queued") {
        references.delete(scanId); row.phase = "cancelled"; publish(); return true;
      }
      row.phase = "cancelling"; row.delivery = "pending"; publish();
      // Native cancel also delegates to cancel_download when materializing.
      return send({ command: "cancel", scan_id: scanId }, row);
    },
    async stop(): Promise<SourceServiceState> {
      stopping = true; references.clear();
      for (const row of state.rows) if (row.phase === "queued") { row.phase = "interrupted"; row.error = "SOURCE_SERVICE_STOPPED"; }
      if (started && !state.disposed && state.service !== "unavailable") {
        state.service = "stopping"; publish();
        try { readSnapshot(await invoke("source_service_stop_v1")); }
        catch (error) { state.failure = code(error); }
      }
      publish(); return snapshot(); // request, not an assertion of retirement
    },
    async dispose(): Promise<SourceServiceState> {
      // Suppress every late event before a pending destructive drain resolves.
      const shouldStop = started && !state.disposed && state.service !== "unavailable";
      state.disposed = true; stopping = true; references.clear(); listeners.clear();
      for (const row of state.rows) if (row.phase === "queued") { row.phase = "interrupted"; row.error = "SOURCE_CONTROLLER_DISPOSED"; }
      if (shouldStop) {
        state.service = "stopping";
        try { readSnapshot(await invoke("source_service_stop_v1")); }
        catch (error) { state.failure = code(error); }
      }
      return snapshot();
    },
  };
}
