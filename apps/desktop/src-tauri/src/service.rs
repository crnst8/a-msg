//! The bundled local service: a loopback child process owned by this app. Quitting stops it.
//! It starts only when the person chooses local mode; nothing registers a login item.

use crate::client::{Core, Failure};
use serde_json::{Value, json};
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Manager};
use tokio::process::{Child, Command};

const RESTART_LIMIT: u32 = 3;

#[derive(Default)]
pub struct LocalService {
    child: Mutex<Option<Child>>,
    stopping: Mutex<bool>,
}

fn binary(app: &AppHandle) -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let beside = exe.parent()?.join("amsg-service/amsg");
    if beside.is_file() {
        return Some(beside);
    }
    let resource = app.path().resource_dir().ok()?.join("amsg-service/amsg");
    resource.is_file().then_some(resource)
}

fn service_dir(app: &AppHandle) -> Result<PathBuf, Failure> {
    app.path()
        .app_data_dir()
        .map(|dir| dir.join("service"))
        .map_err(|error| Failure::local(error.to_string()))
}

fn free_port() -> Result<u16, Failure> {
    let listener =
        TcpListener::bind(("127.0.0.1", 0)).map_err(|error| Failure::local(error.to_string()))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| Failure::local(error.to_string()))
}

impl LocalService {
    pub fn available(&self, app: &AppHandle) -> bool {
        binary(app).is_some()
    }

    fn spawn(&self, app: &AppHandle, port: u16) -> Result<(), Failure> {
        let program =
            binary(app).ok_or_else(|| Failure::local("This build has no local service."))?;
        let data = service_dir(app)?;
        let child = Command::new(program)
            .args([
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                &port.to_string(),
                "--data-dir",
            ])
            .arg(&data)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .map_err(|error| {
                Failure::local(format!("The local service could not start: {error}"))
            })?;
        *self.child.lock().unwrap() = Some(child);
        Ok(())
    }

    async fn run_cli(app: &AppHandle, arguments: &[&str]) -> Result<String, Failure> {
        let program =
            binary(app).ok_or_else(|| Failure::local("This build has no local service."))?;
        let data = service_dir(app)?;
        let output = Command::new(program)
            .args(arguments)
            .arg("--data-dir")
            .arg(&data)
            .output()
            .await
            .map_err(|error| Failure::local(error.to_string()))?;
        if !output.status.success() {
            return Err(Failure::local(
                String::from_utf8_lossy(&output.stderr).trim().to_string(),
            ));
        }
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
    }

    pub async fn start(
        self: &Arc<Self>,
        app: AppHandle,
        core: Arc<Core>,
    ) -> Result<Value, Failure> {
        let port = free_port()?;
        let token = Self::run_cli(&app, &["device-token", "--name", "this-mac"]).await?;
        self.spawn(&app, port)?;
        let endpoint = format!("http://127.0.0.1:{port}");
        let client = reqwest::Client::new();
        let mut live = false;
        for _ in 0..100 {
            if client
                .get(format!("{endpoint}/health/live"))
                .send()
                .await
                .is_ok()
            {
                live = true;
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        if !live {
            return Err(Failure::local("The local service did not start."));
        }
        let capabilities: Value = client
            .get(format!("{endpoint}/v1/capabilities"))
            .bearer_auth(&token)
            .send()
            .await
            .map_err(|_| Failure::offline())?
            .json()
            .await
            .map_err(|_| Failure::offline())?;
        let service_id = capabilities["service_id"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        let paired = core.adopt(endpoint, service_id, token).await?;
        let service = self.clone();
        tauri::async_runtime::spawn(async move { service.watch(app, port).await });
        Ok(paired)
    }

    /// Restarts a crashed child on the same port, a bounded number of times.
    async fn watch(self: Arc<Self>, app: AppHandle, port: u16) {
        let mut restarts = 0;
        loop {
            tokio::time::sleep(Duration::from_secs(2)).await;
            if *self.stopping.lock().unwrap() {
                return;
            }
            let exited = {
                let mut guard = self.child.lock().unwrap();
                match guard.as_mut() {
                    Some(child) => matches!(child.try_wait(), Ok(Some(_))),
                    None => return,
                }
            };
            if exited {
                restarts += 1;
                if restarts > RESTART_LIMIT || self.spawn(&app, port).is_err() {
                    return;
                }
            }
        }
    }

    pub fn stop(&self) {
        *self.stopping.lock().unwrap() = true;
        if let Some(mut child) = self.child.lock().unwrap().take() {
            let _ = child.start_kill();
        }
    }

    pub fn describe(&self, app: &AppHandle) -> Value {
        json!({"available": self.available(app), "running": self.child.lock().unwrap().is_some()})
    }
}
