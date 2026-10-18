//! Current f68 source-serve Tauri surface. Config is supplied once by the trusted
//! entrypoint; webview requests cannot select roots, manifest pins or budgets.
#[path = "host_state.rs"]
mod host_state;
use crate::source_transport::service::{NativeRequest, Observation, Snapshot, Submission};
pub use host_state::{SourceServiceHost, UnavailableReason};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
struct CancelOnDrop(Arc<AtomicBool>);
impl Drop for CancelOnDrop {
    fn drop(&mut self) {
        self.0.store(true, Ordering::Release);
    }
}
fn error(value: crate::source_transport::service::Failure) -> String {
    host_state::service_error(value)
}

#[tauri::command]
pub fn source_service_start_v1(
    state: tauri::State<'_, SourceServiceHost>,
) -> Result<Snapshot, String> {
    state.start() // unavailable is an IPC error; local-file host may stay managed
}
#[tauri::command]
pub fn source_service_state_v1(
    state: tauri::State<'_, SourceServiceHost>,
) -> Result<Snapshot, String> {
    state.state()
}
#[tauri::command]
pub async fn source_service_events_v1(
    state: tauri::State<'_, SourceServiceHost>,
) -> Result<Vec<Observation>, String> {
    let client = state.client()?;
    tauri::async_runtime::spawn_blocking(move || client.drain())
        .await
        .map_err(|_| "SOURCE_HOST_TASK".into())
}
#[tauri::command]
pub async fn source_service_send_v1(
    state: tauri::State<'_, SourceServiceHost>,
    request: NativeRequest,
) -> Result<Submission, String> {
    let client = state.client()?;
    let cancelled = Arc::new(AtomicBool::new(false));
    let guard = CancelOnDrop(cancelled.clone());
    let result = tauri::async_runtime::spawn_blocking(move || client.submit(request, cancelled))
        .await
        .map_err(|_| "SOURCE_HOST_TASK".to_owned())?
        .map_err(error);
    drop(guard);
    result // submitted only: consume native events separately
}
#[tauri::command]
pub fn source_service_stop_v1(
    state: tauri::State<'_, SourceServiceHost>,
) -> Result<Snapshot, String> {
    state.stop()
}
