//! Privileged core. The renderer calls typed commands below; it never sees a token or a raw URL.

mod client;
mod routes;
mod secrets;
mod service;
mod store;

use client::{Core, Failure};
use serde::Serialize;
use serde_json::Value;
use service::LocalService;
use std::path::PathBuf;
use std::sync::Arc;
use store::{OutboxItem, SavedServer};
use tauri::ipc::{InvokeBody, Request, Response};
use tauri::{AppHandle, Manager, RunEvent, State};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;

type CoreState<'a> = State<'a, Arc<Core>>;

#[tauri::command]
fn connection_status(core: CoreState<'_>) -> Value {
    core.pairing()
}

#[tauri::command]
async fn connection_pair(
    core: CoreState<'_>,
    endpoint: String,
    code: String,
    device_name: String,
) -> Result<Value, Failure> {
    core.pair(&endpoint, &code, &device_name).await
}

#[tauri::command]
fn connection_servers(core: CoreState<'_>) -> Vec<SavedServer> {
    core.servers()
}

#[tauri::command]
fn connection_leave(core: CoreState<'_>) {
    core.leave();
}

#[tauri::command]
async fn connection_switch(core: CoreState<'_>, service_id: String) -> Result<Value, Failure> {
    core.switch(&service_id).await
}

#[tauri::command]
async fn connection_sign_out(
    core: CoreState<'_>,
    service_id: Option<String>,
) -> Result<bool, Failure> {
    Ok(core.sign_out(service_id).await)
}

#[tauri::command]
async fn api_request(
    core: CoreState<'_>,
    route: String,
    id: Option<String>,
    query: Option<Value>,
    body: Option<Value>,
) -> Result<Value, Failure> {
    core.request(&route, id, query, body).await
}

#[tauri::command]
fn stream_start(core: CoreState<'_>, after: i64) {
    core.start_stream(after);
}

#[tauri::command]
fn stream_stop(core: CoreState<'_>) {
    core.stop_stream();
}

#[tauri::command]
fn outbox_list(core: CoreState<'_>) -> Vec<OutboxItem> {
    core.outbox_list()
}

#[tauri::command]
fn outbox_enqueue(core: CoreState<'_>, item: OutboxItem) -> Result<(), Failure> {
    core.outbox_add(item)
}

#[tauri::command]
fn outbox_discard(core: CoreState<'_>, id: String) {
    core.outbox_discard(&id);
}

#[tauri::command]
fn outbox_resend(core: CoreState<'_>, id: String) {
    core.outbox_resend(&id);
}

/// Bytes from a paste or an in-page file. They become a managed local copy, then upload like any file.
#[tauri::command]
fn upload_stage(core: CoreState<'_>, request: Request<'_>) -> Result<String, Failure> {
    let InvokeBody::Raw(bytes) = request.body() else {
        return Err(Failure::local("Expected file bytes."));
    };
    let directory = core.staging_dir();
    std::fs::create_dir_all(&directory).map_err(|error| Failure::local(error.to_string()))?;
    let path = directory.join(uuid::Uuid::new_v4().to_string());
    std::fs::write(&path, bytes).map_err(|error| Failure::local(error.to_string()))?;
    Ok(path.to_string_lossy().into_owned())
}

#[tauri::command]
async fn upload_file(
    core: CoreState<'_>,
    id: String,
    path: String,
    name: String,
) -> Result<Value, Failure> {
    let path = PathBuf::from(path);
    let staged = path.starts_with(core.staging_dir());
    let result = core.upload(id, path.clone(), name).await;
    if staged {
        let _ = std::fs::remove_file(path);
    }
    result
}

#[tauri::command]
fn upload_cancel(core: CoreState<'_>, id: String) {
    core.cancel_upload(&id);
}

#[tauri::command]
async fn artifact_bytes(
    core: CoreState<'_>,
    id: String,
    version: Option<u32>,
) -> Result<Response, Failure> {
    Ok(Response::new(core.artifact_bytes(&id, version).await?))
}

#[tauri::command]
async fn artifact_save(
    app: AppHandle,
    core: CoreState<'_>,
    id: String,
    version: Option<u32>,
    name: String,
) -> Result<Option<String>, Failure> {
    let dialog = app.dialog().file().set_file_name(&name);
    let picked = tauri::async_runtime::spawn_blocking(move || dialog.blocking_save_file())
        .await
        .map_err(|error| Failure::local(error.to_string()))?;
    let Some(path) = picked.and_then(|file| file.into_path().ok()) else {
        return Ok(None);
    };
    core.artifact_save(&id, version, path.clone()).await?;
    Ok(Some(path.to_string_lossy().into_owned()))
}

/// Shows a file this app just saved. The path came from the Save dialog, not from page content.
#[tauri::command]
fn artifact_reveal(app: AppHandle, path: String) -> Result<(), Failure> {
    app.opener()
        .reveal_item_in_dir(path)
        .map_err(|error| Failure::local(error.to_string()))
}

/// Saves into a private folder and hands the file to the system's default application.
#[tauri::command]
async fn artifact_open(
    app: AppHandle,
    core: CoreState<'_>,
    id: String,
    version: Option<u32>,
    name: String,
) -> Result<(), Failure> {
    let directory = core.opened_dir().join(uuid::Uuid::new_v4().to_string());
    std::fs::create_dir_all(&directory).map_err(|error| Failure::local(error.to_string()))?;
    let safe = std::path::Path::new(&name)
        .file_name()
        .ok_or_else(|| Failure::local("That file has no usable name."))?;
    let path = directory.join(safe);
    core.artifact_save(&id, version, path.clone()).await?;
    app.opener()
        .open_path(path.to_string_lossy(), None::<&str>)
        .map_err(|error| Failure::local(error.to_string()))
}

#[derive(Serialize)]
struct PickedFile {
    path: String,
    name: String,
    size: u64,
}

#[tauri::command]
async fn files_pick(app: AppHandle) -> Result<Vec<PickedFile>, Failure> {
    let dialog = app.dialog().file();
    let picked = tauri::async_runtime::spawn_blocking(move || dialog.blocking_pick_files())
        .await
        .map_err(|error| Failure::local(error.to_string()))?;
    Ok(picked
        .unwrap_or_default()
        .into_iter()
        .filter_map(|file| file.into_path().ok())
        .filter_map(|path| {
            let size = std::fs::metadata(&path).ok()?.len();
            let name = path.file_name()?.to_string_lossy().into_owned();
            Some(PickedFile {
                path: path.to_string_lossy().into_owned(),
                name,
                size,
            })
        })
        .collect())
}

#[tauri::command]
fn files_describe(paths: Vec<String>) -> Vec<PickedFile> {
    paths
        .into_iter()
        .filter_map(|path| {
            let meta = std::fs::metadata(&path).ok()?;
            if !meta.is_file() {
                return None;
            }
            let name = PathBuf::from(&path)
                .file_name()?
                .to_string_lossy()
                .into_owned();
            Some(PickedFile {
                path,
                name,
                size: meta.len(),
            })
        })
        .collect()
}

#[tauri::command]
fn external_open(app: AppHandle, url: String) -> Result<(), Failure> {
    let lower = url.to_lowercase();
    if !(lower.starts_with("https://")
        || lower.starts_with("http://")
        || lower.starts_with("mailto:"))
    {
        return Err(Failure::local("That link cannot be opened."));
    }
    app.opener()
        .open_url(url, None::<&str>)
        .map_err(|error| Failure::local(error.to_string()))
}

#[tauri::command]
fn notification_send(app: AppHandle, title: String, body: String) -> Result<(), Failure> {
    app.notification()
        .builder()
        .title(title)
        .body(body)
        .show()
        .map_err(|error| Failure::local(error.to_string()))
}

#[tauri::command]
fn local_service_status(app: AppHandle, service: State<'_, Arc<LocalService>>) -> Value {
    service.describe(&app)
}

#[tauri::command]
async fn local_service_start(
    app: AppHandle,
    core: CoreState<'_>,
    service: State<'_, Arc<LocalService>>,
) -> Result<Value, Failure> {
    service
        .inner()
        .clone()
        .start(app, core.inner().clone())
        .await
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let directory = app.path().app_data_dir()?;
            let core = Core::new(app.handle().clone(), directory).map_err(std::io::Error::other)?;
            app.manage(core);
            app.manage(Arc::new(LocalService::default()));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            connection_status,
            connection_pair,
            connection_servers,
            connection_leave,
            connection_switch,
            connection_sign_out,
            api_request,
            stream_start,
            stream_stop,
            outbox_list,
            outbox_enqueue,
            outbox_discard,
            outbox_resend,
            upload_stage,
            upload_file,
            upload_cancel,
            artifact_bytes,
            artifact_save,
            artifact_reveal,
            artifact_open,
            files_pick,
            files_describe,
            external_open,
            notification_send,
            local_service_status,
            local_service_start,
        ])
        .build(tauri::generate_context!())
        .expect("failed to build a-msg");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            handle.state::<Arc<Core>>().stop_stream();
            handle.state::<Arc<LocalService>>().stop();
        }
    });
}
