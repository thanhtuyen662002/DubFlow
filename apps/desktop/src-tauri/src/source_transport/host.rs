//! Legacy candidate184-only glue. Never mount on f68/current source-serve.
//! The current-service host is service_host.rs and keeps one native owner alive.
use serde_json::{json, Value};
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

async fn call(request: Value) -> Result<Value, String> {
    let flag = Arc::new(AtomicBool::new(false));
    let cancel_on_drop = CancelOnDrop(flag.clone());
    let result = tauri::async_runtime::spawn_blocking(move || {
        let root = crate::version_root()?;
        let control = crate::control_root()?;
        let binary = crate::supervisor_binary(&root)?;
        let mut command = crate::command_with_no_window(&binary);
        command.args([
            "server",
            "--root",
            root.to_string_lossy().as_ref(),
            "--data-root",
            control.to_string_lossy().as_ref(),
            "--model-root",
            control.join("models").to_string_lossy().as_ref(),
        ]);
        crate::source_transport::json_request(&mut command, &request, &flag)
    })
    .await
    .map_err(|_| "SOURCE_SERVICE_TASK_FAILED".to_owned())?;
    drop(cancel_on_drop);
    result
}

#[tauri::command]
pub async fn enqueue_sources(items: Value) -> Result<Value, String> {
    if !items.is_array() {
        return Err("SOURCE_ITEMS_INVALID".into());
    }
    call(json!({"command":"enqueue_sources", "items":items})).await
}
#[tauri::command]
pub async fn source_scan_status(scan_id: String) -> Result<Value, String> {
    crate::validate_id(&scan_id)?;
    call(json!({"command":"source_status", "scan_id":scan_id})).await
}
#[tauri::command]
pub async fn pause_source_scan(scan_id: String) -> Result<Value, String> {
    crate::validate_id(&scan_id)?;
    call(json!({"command":"pause_source_scan", "scan_id":scan_id})).await
}
#[tauri::command]
pub async fn resume_source_scan(scan_id: String) -> Result<Value, String> {
    crate::validate_id(&scan_id)?;
    call(json!({"command":"resume_source_scan", "scan_id":scan_id})).await
}
#[tauri::command]
pub async fn cancel_source_scan(scan_id: String) -> Result<Value, String> {
    crate::validate_id(&scan_id)?;
    call(json!({"command":"cancel_source_scan", "scan_id":scan_id})).await
}
