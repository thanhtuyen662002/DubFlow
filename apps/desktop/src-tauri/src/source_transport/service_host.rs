//! Current f68 source-serve Tauri surface. Config is supplied once by the trusted
//! entrypoint; webview requests cannot select roots, manifest pins or budgets.
use crate::source_transport::service::{
    AdmissionWindow, Client, LaunchPin, NativeRequest, Observation, Snapshot, Submission,
};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};

pub struct SourceServiceHost {
    pin: LaunchPin,
    admission: AdmissionWindow,
    client: Mutex<Option<Client>>,
}
impl SourceServiceHost {
    pub fn new(pin: LaunchPin, admission: AdmissionWindow) -> Self {
        Self {
            pin,
            admission,
            client: Mutex::new(None),
        }
    }
    fn client(&self) -> Result<Client, String> {
        self.client
            .lock()
            .map_err(|_| "SOURCE_HOST_LOCK".to_owned())?
            .clone()
            .ok_or_else(|| "SOURCE_NOT_STARTED".into())
    }
}
struct CancelOnDrop(Arc<AtomicBool>);
impl Drop for CancelOnDrop {
    fn drop(&mut self) {
        self.0.store(true, Ordering::Release);
    }
}
fn error(value: crate::source_transport::service::Failure) -> String {
    format!("SOURCE_SERVICE_{value:?}")
}

#[tauri::command]
pub fn source_service_start_v1(
    state: tauri::State<'_, SourceServiceHost>,
) -> Result<Snapshot, String> {
    let mut client = state
        .client
        .lock()
        .map_err(|_| "SOURCE_HOST_LOCK".to_owned())?;
    if client.is_none() {
        *client = Some(Client::launch(state.pin.clone(), state.admission).map_err(error)?);
    }
    Ok(client.as_ref().unwrap().snapshot()) // admitting, never a fake native ready
}
#[tauri::command]
pub fn source_service_state_v1(
    state: tauri::State<'_, SourceServiceHost>,
) -> Result<Snapshot, String> {
    Ok(state.client()?.snapshot())
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
    let client = state.client()?;
    client.stop();
    Ok(client.snapshot())
}
