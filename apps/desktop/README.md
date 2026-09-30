# Desktop shell and host

The desktop feature layer is deliberately transport-agnostic. Tauri file
pickers and the Rust supervisor are adapters around the queue model; they do
not become a second source of durable job state.

The current shell provides:

- local file/folder path admission with duplicate suppression;
- durable versioned queue snapshots and restart recovery;
- explicit queued, running, waiting, recovered, failed and completed states;
- queue summaries and job detail view models that keep large integer progress
  values as decimal strings until display time;
- a deterministic mock transport for UI development without OCR, TTS or a
  live source provider;
- bounded queue admission (10,000 jobs by default) so a malformed snapshot or
  a large batch cannot make the UI allocate without limit.

The repository now also contains a small Tauri host. It opens a native window
and renders the queue shell, but the supervisor/worker backend is intentionally
reported as unavailable until its versioned transport is wired. The preview
queue uses browser-profile storage only; it is not a replacement for durable
supervisor state and does not claim to process media.

Run from this directory after Node.js is available:

```text
npm ci
npm run build
npm test
```

The Windows release workflow builds the native host with the app-owned build
inputs and copies only the resulting executable into the release payload. An
installed machine never needs Node.js or Python to open the host.
