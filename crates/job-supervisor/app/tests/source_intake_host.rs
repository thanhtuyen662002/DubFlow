//! Exercise the same managed state used by all five Tauri source commands.
//! The Tauri wrapper itself is separately compiled by desktop integration.
#[path = "../../../../apps/desktop/src-tauri/src/source_transport/host_state.rs"]
mod host_state;
#[path = "../../../../apps/desktop/src-tauri/src/source_transport/mod.rs"]
mod source_transport;
