# Desktop shell

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

Run from this directory after TypeScript is available:

```text
npm run build
npm test
```
