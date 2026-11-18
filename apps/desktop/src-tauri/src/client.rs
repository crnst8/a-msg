//! Authenticated service access: requests, the event stream, the outbox and file transfer.
//! Tokens stay in this process. The renderer receives records and signals.

use crate::routes;
use crate::secrets;
use crate::store::{OutboxItem, SavedServer, Store};
use futures_util::StreamExt;
use rand::Rng;
use reqwest::{Client, Method, StatusCode, multipart};
use serde::Serialize;
use serde_json::{Value, json};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Emitter};
use tokio::io::AsyncWriteExt;
use tokio::sync::Notify;
use tokio_util::io::ReaderStream;
use tokio_util::sync::CancellationToken;
use url::Url;

const BACKOFF_MIN: u64 = 1000;
const BACKOFF_MAX: u64 = 30000;
const SERVICE_API: &str = "1";

#[derive(Debug, Serialize, Clone)]
pub struct Failure {
    pub status: u16,
    pub code: String,
    pub message: String,
    pub retryable: bool,
    pub details: Value,
}

impl Failure {
    pub fn new(status: u16, code: &str, message: &str, retryable: bool) -> Self {
        Self {
            status,
            code: code.into(),
            message: message.into(),
            retryable,
            details: json!({}),
        }
    }
    pub fn offline() -> Self {
        Self::new(0, "offline", "The service is not reachable.", true)
    }
    pub fn local(message: impl Into<String>) -> Self {
        Self::new(0, "local", &message.into(), false)
    }
}

fn describe(error: reqwest::Error) -> Failure {
    let text = format!("{error:?}").to_lowercase();
    if text.contains("certificate") || text.contains("tls") {
        return Failure::new(
            0,
            "tls",
            "The service certificate could not be verified.",
            false,
        );
    }
    Failure::offline()
}

#[derive(Clone)]
struct Session {
    endpoint: String,
    service_id: String,
    token: String,
}

pub struct Core {
    app: AppHandle,
    data_dir: PathBuf,
    store: Mutex<Store>,
    http: Client,
    streaming: Client,
    session: Mutex<Option<Session>>,
    stream: Mutex<Option<CancellationToken>>,
    uploads: Mutex<HashMap<String, CancellationToken>>,
    wake: Notify,
}

async fn failure_from(response: reqwest::Response) -> Failure {
    let status = response.status().as_u16();
    match response.json::<Value>().await {
        Ok(body) if body.get("error").is_some() => {
            let error = &body["error"];
            Failure {
                status,
                code: error["code"].as_str().unwrap_or("error").into(),
                message: error["message"]
                    .as_str()
                    .unwrap_or("The request failed.")
                    .into(),
                retryable: error["retryable"].as_bool().unwrap_or(false),
                details: error["details"].clone(),
            }
        }
        _ => Failure::offline(),
    }
}

fn not_paired() -> Failure {
    Failure::new(401, "unauthenticated", "This device is not paired.", false)
}

/// Plain HTTP is accepted for loopback only, such as an SSH tunnel. TLS validation is never disabled.
pub fn normalize_endpoint(raw: &str) -> Result<String, Failure> {
    let invalid = || {
        Failure::new(
            0,
            "endpoint",
            "Enter a full address starting with https://.",
            false,
        )
    };
    let url = Url::parse(raw.trim()).map_err(|_| invalid())?;
    let host = url.host_str().ok_or_else(invalid)?;
    let loopback = matches!(host, "localhost" | "127.0.0.1" | "[::1]" | "::1");
    if url.scheme() != "https" && !(url.scheme() == "http" && loopback) {
        return Err(Failure::new(
            0,
            "endpoint",
            "Remote servers must use https://.",
            false,
        ));
    }
    Ok(url.as_str().trim_end_matches('/').to_string())
}

impl Core {
    pub fn new(app: AppHandle, data_dir: PathBuf) -> Result<Arc<Self>, String> {
        std::fs::create_dir_all(&data_dir).map_err(|error| error.to_string())?;
        let store = Store::open(&data_dir.join("local.db")).map_err(|error| error.to_string())?;
        store.outbox_recover();
        let session = match (store.get("service_id"), store.get("endpoint")) {
            (Some(service_id), Some(endpoint)) => {
                secrets::load(&data_dir, &service_id).map(|token| Session {
                    endpoint,
                    service_id,
                    token,
                })
            }
            _ => None,
        };
        let core = Arc::new(Self {
            app,
            data_dir,
            store: Mutex::new(store),
            http: Client::builder()
                .connect_timeout(Duration::from_secs(10))
                .timeout(Duration::from_secs(60))
                .build()
                .map_err(|error| error.to_string())?,
            streaming: Client::builder()
                .connect_timeout(Duration::from_secs(10))
                .read_timeout(Duration::from_secs(45))
                .build()
                .map_err(|error| error.to_string())?,
            session: Mutex::new(session),
            stream: Mutex::new(None),
            uploads: Mutex::new(HashMap::new()),
            wake: Notify::new(),
        });
        let worker = core.clone();
        tauri::async_runtime::spawn(async move { worker.outbox_loop().await });
        Ok(core)
    }

    fn current(&self) -> Option<Session> {
        self.session.lock().unwrap().clone()
    }

    fn require(&self) -> Result<Session, Failure> {
        self.current().ok_or_else(not_paired)
    }

    pub fn pairing(&self) -> Value {
        match self.current() {
            Some(session) => {
                json!({"paired": true, "endpoint": session.endpoint, "service_id": session.service_id})
            }
            None => json!({"paired": false}),
        }
    }

    pub async fn pair(
        &self,
        endpoint: &str,
        code: &str,
        device_name: &str,
    ) -> Result<Value, Failure> {
        let endpoint = normalize_endpoint(endpoint)?;
        let live = self
            .http
            .get(format!("{endpoint}/health/live"))
            .send()
            .await
            .map_err(describe)?;
        if !live.status().is_success() {
            return Err(Failure::offline());
        }
        let response = self
            .http
            .post(format!("{endpoint}/v1/pair"))
            .json(&json!({"code": code, "device_name": device_name}))
            .send()
            .await
            .map_err(describe)?;
        if !response.status().is_success() {
            return Err(failure_from(response).await);
        }
        let paired: Value = response.json().await.map_err(describe)?;
        let token = paired["token"].as_str().unwrap_or_default().to_string();
        let service_id = paired["service_id"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        self.adopt(endpoint, service_id, token).await
    }

    /// Verifies compatibility, then stores the credential. Used by pairing and the bundled service.
    pub async fn adopt(
        &self,
        endpoint: String,
        service_id: String,
        token: String,
    ) -> Result<Value, Failure> {
        let capabilities = self
            .http
            .get(format!("{endpoint}/v1/capabilities"))
            .bearer_auth(&token)
            .send()
            .await
            .map_err(describe)?;
        if !capabilities.status().is_success() {
            return Err(failure_from(capabilities).await);
        }
        let body: Value = capabilities.json().await.map_err(describe)?;
        if body["api_version"].as_str() != Some(SERVICE_API) {
            return Err(Failure::new(
                0,
                "incompatible",
                "This server needs a different version of a-msg.",
                false,
            ));
        }
        secrets::store(&self.data_dir, &service_id, &token).map_err(Failure::local)?;
        {
            let store = self.store.lock().unwrap();
            store.set("service_id", &service_id);
            store.set("endpoint", &endpoint);
            store.remember_server(&service_id, &endpoint);
        }
        *self.session.lock().unwrap() = Some(Session {
            endpoint,
            service_id,
            token,
        });
        self.wake.notify_waiters();
        Ok(self.pairing())
    }

    pub fn servers(&self) -> Vec<SavedServer> {
        self.store.lock().unwrap().servers()
    }

    fn clear_session(&self) -> Option<Session> {
        self.stop_stream();
        let session = self.session.lock().unwrap().take();
        let store = self.store.lock().unwrap();
        store.remove("service_id");
        store.remove("endpoint");
        session
    }

    /// Leaves the current server and keeps its credential, so the device can return without a code.
    pub fn leave(&self) {
        if let Some(session) = self.clear_session() {
            let store = self.store.lock().unwrap();
            store.remember_server(&session.service_id, &session.endpoint);
        }
    }

    /// Returns to a saved server. A credential the server refuses is removed.
    pub async fn switch(&self, service_id: &str) -> Result<Value, Failure> {
        let saved = self
            .servers()
            .into_iter()
            .find(|server| server.service_id == service_id);
        let token = secrets::load(&self.data_dir, service_id);
        let (Some(saved), Some(token)) = (saved, token) else {
            self.forget(service_id);
            return Err(not_paired());
        };
        self.stop_stream();
        let result = self.adopt(saved.endpoint, saved.service_id, token).await;
        if matches!(&result, Err(failure) if failure.status == 401) {
            self.forget(service_id);
        }
        result
    }

    fn forget(&self, service_id: &str) {
        secrets::delete(&self.data_dir, service_id);
        self.store.lock().unwrap().forget_server(service_id);
    }

    /// Revokes the device on the server when it can be reached, then removes the credential here.
    /// Returns whether the server confirmed. Local removal happens either way.
    pub async fn sign_out(&self, service_id: Option<String>) -> bool {
        let session = self.current();
        let target = service_id.or_else(|| session.as_ref().map(|s| s.service_id.clone()));
        let Some(target) = target else {
            return false;
        };
        let current = session.as_ref().is_some_and(|s| s.service_id == target);
        let credential = match session.filter(|_| current) {
            Some(session) => Some((session.endpoint, session.token)),
            None => self
                .servers()
                .into_iter()
                .find(|server| server.service_id == target)
                .zip(secrets::load(&self.data_dir, &target))
                .map(|(server, token)| (server.endpoint, token)),
        };
        let revoked = match credential {
            Some((endpoint, token)) => self.revoke_remote(&endpoint, &token).await,
            None => false,
        };
        if current {
            self.clear_session();
        }
        self.forget(&target);
        revoked
    }

    async fn revoke_remote(&self, endpoint: &str, token: &str) -> bool {
        const PATIENCE: Duration = Duration::from_secs(5);
        let Ok(listed) = self
            .http
            .get(format!("{endpoint}/v1/devices"))
            .bearer_auth(token)
            .timeout(PATIENCE)
            .send()
            .await
        else {
            return false;
        };
        // The server already refuses this credential, which is the outcome being asked for.
        if listed.status().as_u16() == 401 {
            return true;
        }
        let Ok(devices) = listed.json::<Vec<Value>>().await else {
            return false;
        };
        let Some(id) = devices
            .iter()
            .find(|device| device["current"] == true)
            .and_then(|device| device["id"].as_str())
        else {
            return false;
        };
        self.http
            .delete(format!("{endpoint}/v1/devices/{id}"))
            .bearer_auth(token)
            .timeout(PATIENCE)
            .send()
            .await
            .is_ok_and(|response| response.status().is_success())
    }

    pub async fn request(
        &self,
        name: &str,
        id: Option<String>,
        query: Option<Value>,
        body: Option<Value>,
    ) -> Result<Value, Failure> {
        let (method, template) = routes::resolve(name).ok_or_else(|| {
            Failure::new(
                403,
                "route_not_allowed",
                "That request is not allowed.",
                false,
            )
        })?;
        let session = self.require()?;
        let mut path = template.to_string();
        if let Some(id) = &id {
            if !routes::valid_id(id) {
                return Err(Failure::new(
                    422,
                    "invalid_id",
                    "That identifier is not valid.",
                    false,
                ));
            }
            path = path.replace("{id}", id);
        }
        if path.contains('{') {
            return Err(Failure::new(
                422,
                "invalid_id",
                "This request needs an identifier.",
                false,
            ));
        }
        let mut url = Url::parse(&format!("{}{path}", session.endpoint))
            .map_err(|_| Failure::local("The service address is not valid."))?;
        if let Some(Value::Object(params)) = &query {
            let mut pairs = url.query_pairs_mut();
            for (key, value) in params {
                match value {
                    Value::Null => {}
                    Value::String(text) => {
                        pairs.append_pair(key, text);
                    }
                    other => {
                        pairs.append_pair(key, &other.to_string());
                    }
                }
            }
        }
        let cache_key = (method == "GET"
            && (name == "snapshot" || name == "listMessages")
            && url.query().is_none_or(|q| !q.contains("before=")))
        .then(|| format!("{name}:{}", id.as_deref().unwrap_or("")));
        let method = Method::from_bytes(method.as_bytes()).expect("route method");
        let mut request = self.http.request(method, url).bearer_auth(&session.token);
        if let Some(body) = &body {
            request = request.json(body);
        }
        let response = match request.send().await {
            Ok(response) => response,
            Err(error) => {
                if let Some(key) = &cache_key {
                    let cached = self
                        .store
                        .lock()
                        .unwrap()
                        .cache_get(&session.service_id, key);
                    if let Some(text) = cached {
                        return serde_json::from_str(&text).map_err(|_| Failure::offline());
                    }
                }
                return Err(describe(error));
            }
        };
        if response.status() == StatusCode::NO_CONTENT {
            return Ok(Value::Null);
        }
        if !response.status().is_success() {
            return Err(failure_from(response).await);
        }
        let text = response.text().await.map_err(describe)?;
        if let Some(key) = &cache_key {
            self.store
                .lock()
                .unwrap()
                .cache_put(&session.service_id, key, &text);
        }
        serde_json::from_str(&text).map_err(|_| Failure::offline())
    }

    // Event stream --------------------------------------------------------------------------------
    pub fn start_stream(self: &Arc<Self>, after: i64) {
        self.stop_stream();
        let token = CancellationToken::new();
        *self.stream.lock().unwrap() = Some(token.clone());
        let core = self.clone();
        tauri::async_runtime::spawn(async move { core.stream_loop(after, token).await });
    }

    pub fn stop_stream(&self) {
        if let Some(token) = self.stream.lock().unwrap().take() {
            token.cancel();
        }
    }

    fn signal(&self, value: Value) {
        let _ = self.app.emit("amsg://stream", value);
    }

    fn link(&self, state: &str) {
        self.signal(json!({"kind": "link", "state": state}));
    }

    async fn stream_loop(self: Arc<Self>, after: i64, token: CancellationToken) {
        let mut cursor = after;
        let mut delay = BACKOFF_MIN;
        while !token.is_cancelled() {
            self.link("connecting");
            let Ok(session) = self.require() else { return };
            let request = self
                .streaming
                .get(format!("{}/v1/events?after={cursor}", session.endpoint))
                .bearer_auth(&session.token)
                .send();
            let response = tokio::select! {
                _ = token.cancelled() => return,
                response = request => response,
            };
            match response {
                Ok(response) if response.status() == StatusCode::GONE => {
                    self.signal(json!({"kind": "resync"}));
                    return;
                }
                Ok(response) if response.status() == StatusCode::UNAUTHORIZED => {
                    self.signal(json!({"kind": "unauthorized"}));
                    return;
                }
                Ok(response) if response.status().is_success() => {
                    delay = BACKOFF_MIN;
                    let mut bytes = response.bytes_stream();
                    let mut buffer: Vec<u8> = Vec::new();
                    loop {
                        let chunk = tokio::select! {
                            _ = token.cancelled() => return,
                            chunk = bytes.next() => chunk,
                        };
                        let Some(Ok(chunk)) = chunk else { break };
                        buffer.extend_from_slice(&chunk);
                        while let Some(end) = buffer.windows(2).position(|pair| pair == b"\n\n") {
                            let frame: Vec<u8> = buffer.drain(..end + 2).collect();
                            let text = String::from_utf8_lossy(&frame);
                            if text.starts_with("event: ready") {
                                self.link("online");
                                self.wake.notify_waiters();
                                continue;
                            }
                            let data: String = text
                                .lines()
                                .filter_map(|line| line.strip_prefix("data: "))
                                .collect();
                            if data.is_empty() {
                                continue;
                            }
                            if let Ok(event) = serde_json::from_str::<Value>(&data) {
                                cursor = event["seq"].as_i64().unwrap_or(cursor);
                                self.signal(json!({"kind": "event", "event": event}));
                            }
                        }
                    }
                }
                _ => {}
            }
            if token.is_cancelled() {
                return;
            }
            self.link("offline");
            let jitter = rand::rng().random_range(0.7..1.3);
            let wait = Duration::from_millis((delay as f64 * jitter) as u64);
            delay = (delay * 2).min(BACKOFF_MAX);
            tokio::select! {
                _ = token.cancelled() => return,
                _ = tokio::time::sleep(wait) => {}
            }
        }
    }

    // Outbox --------------------------------------------------------------------------------------
    pub fn outbox_list(&self) -> Vec<OutboxItem> {
        match self.current() {
            Some(session) => self.store.lock().unwrap().outbox_list(&session.service_id),
            None => vec![],
        }
    }

    pub fn outbox_add(&self, item: OutboxItem) -> Result<(), Failure> {
        let session = self.require()?;
        self.store
            .lock()
            .unwrap()
            .outbox_add(&session.service_id, &item)
            .map_err(|error| Failure::local(error.to_string()))?;
        let _ = self.app.emit("amsg://outbox", json!({"item": item}));
        self.wake.notify_waiters();
        Ok(())
    }

    pub fn outbox_discard(&self, id: &str) {
        if let Some(session) = self.current() {
            self.store
                .lock()
                .unwrap()
                .outbox_remove(&session.service_id, id);
        }
    }

    pub fn outbox_resend(&self, id: &str) {
        if let Some(session) = self.current() {
            self.store
                .lock()
                .unwrap()
                .outbox_mark(&session.service_id, id, "queued", None);
            self.wake.notify_waiters();
        }
    }

    async fn outbox_loop(self: Arc<Self>) {
        loop {
            let mut retry = false;
            if let Some(session) = self.current() {
                let next = self.store.lock().unwrap().outbox_next(&session.service_id);
                if let Some(mut item) = next {
                    retry = !self.send_one(&session, &mut item).await;
                    if !retry {
                        continue;
                    }
                }
            }
            let wait = if retry {
                Duration::from_secs(4)
            } else {
                Duration::from_secs(30)
            };
            tokio::select! {
                _ = self.wake.notified() => {}
                _ = tokio::time::sleep(wait) => {}
            }
        }
    }

    /// Returns false when the send should be tried again later with the same client message ID.
    async fn send_one(&self, session: &Session, item: &mut OutboxItem) -> bool {
        let service = session.service_id.clone();
        self.store
            .lock()
            .unwrap()
            .outbox_mark(&service, &item.client_message_id, "sending", None);
        item.state = "sending".into();
        let _ = self.app.emit("amsg://outbox", json!({"item": item}));
        let artifact_ids: Vec<Value> = item
            .artifacts
            .as_array()
            .map(|list| list.iter().map(|artifact| artifact["id"].clone()).collect())
            .unwrap_or_default();
        let response = self
            .http
            .post(format!(
                "{}/v1/spaces/{}/messages",
                session.endpoint, item.space_id
            ))
            .bearer_auth(&session.token)
            .json(&json!({
                "client_message_id": item.client_message_id,
                "markdown": item.markdown,
                "artifact_ids": artifact_ids,
            }))
            .send()
            .await;
        let store = |state: &str, error: Option<&str>| {
            self.store
                .lock()
                .unwrap()
                .outbox_mark(&service, &item.client_message_id, state, error);
        };
        match response {
            Ok(response) if response.status().is_success() => {
                let receipt: Value = response.json().await.unwrap_or(Value::Null);
                self.store
                    .lock()
                    .unwrap()
                    .outbox_remove(&service, &item.client_message_id);
                self.link("online");
                let _ = self
                    .app
                    .emit("amsg://outbox", json!({"item": item, "receipt": receipt}));
                true
            }
            Ok(response) => {
                let failure = failure_from(response).await;
                if failure.status == 0 || failure.retryable {
                    store("queued", None);
                    item.state = "queued".into();
                    let _ = self.app.emit("amsg://outbox", json!({"item": item}));
                    return false;
                }
                store("failed", Some(&failure.message));
                item.state = "failed".into();
                item.error = Some(failure.message);
                let _ = self.app.emit("amsg://outbox", json!({"item": item}));
                true
            }
            Err(_) => {
                self.link("offline");
                store("queued", None);
                item.state = "queued".into();
                let _ = self.app.emit("amsg://outbox", json!({"item": item}));
                false
            }
        }
    }

    // Files ---------------------------------------------------------------------------------------
    pub fn staging_dir(&self) -> PathBuf {
        self.data_dir.join("staging")
    }

    pub fn opened_dir(&self) -> PathBuf {
        self.data_dir.join("opened")
    }

    pub async fn upload(
        &self,
        upload_id: String,
        path: PathBuf,
        name: String,
    ) -> Result<Value, Failure> {
        let session = self.require()?;
        let token = CancellationToken::new();
        self.uploads
            .lock()
            .unwrap()
            .insert(upload_id.clone(), token.clone());
        let result = self
            .upload_inner(&session, &upload_id, &path, name, &token)
            .await;
        self.uploads.lock().unwrap().remove(&upload_id);
        result
    }

    async fn upload_inner(
        &self,
        session: &Session,
        upload_id: &str,
        path: &Path,
        name: String,
        token: &CancellationToken,
    ) -> Result<Value, Failure> {
        let file = tokio::fs::File::open(path)
            .await
            .map_err(|error| Failure::local(error.to_string()))?;
        let size = file
            .metadata()
            .await
            .map_err(|error| Failure::local(error.to_string()))?
            .len();
        let sent = Arc::new(AtomicU64::new(0));
        let app = self.app.clone();
        let id = upload_id.to_string();
        let progress = sent.clone();
        let stream = ReaderStream::with_capacity(file, 256 * 1024).map(move |chunk| {
            if let Ok(bytes) = &chunk {
                let total =
                    progress.fetch_add(bytes.len() as u64, Ordering::Relaxed) + bytes.len() as u64;
                let _ = app.emit("amsg://upload", json!({"id": id, "sent": total}));
            }
            chunk
        });
        let part = multipart::Part::stream_with_length(reqwest::Body::wrap_stream(stream), size)
            .file_name(name);
        let form = multipart::Form::new().part("file", part);
        let request = self
            .streaming
            .post(format!("{}/v1/uploads", session.endpoint))
            .bearer_auth(&session.token)
            .multipart(form)
            .send();
        let response = tokio::select! {
            _ = token.cancelled() => return Err(Failure::new(0, "aborted", "Upload cancelled.", false)),
            response = request => response.map_err(describe)?,
        };
        if !response.status().is_success() {
            return Err(failure_from(response).await);
        }
        response.json().await.map_err(describe)
    }

    pub fn cancel_upload(&self, id: &str) {
        if let Some(token) = self.uploads.lock().unwrap().get(id) {
            token.cancel();
        }
    }

    async fn artifact_response(
        &self,
        id: &str,
        version: Option<u32>,
    ) -> Result<reqwest::Response, Failure> {
        if !routes::valid_id(id) {
            return Err(Failure::new(
                422,
                "invalid_id",
                "That identifier is not valid.",
                false,
            ));
        }
        let session = self.require()?;
        let mut url = format!("{}/v1/artifacts/{id}/content", session.endpoint);
        if let Some(version) = version {
            url.push_str(&format!("?version={version}"));
        }
        let response = self
            .streaming
            .get(url)
            .bearer_auth(&session.token)
            .send()
            .await
            .map_err(describe)?;
        if !response.status().is_success() {
            return Err(failure_from(response).await);
        }
        Ok(response)
    }

    pub async fn artifact_bytes(&self, id: &str, version: Option<u32>) -> Result<Vec<u8>, Failure> {
        let response = self.artifact_response(id, version).await?;
        Ok(response.bytes().await.map_err(describe)?.to_vec())
    }

    pub async fn artifact_save(
        &self,
        id: &str,
        version: Option<u32>,
        target: PathBuf,
    ) -> Result<(), Failure> {
        let response = self.artifact_response(id, version).await?;
        let partial = target.with_extension(format!("part-{}", uuid::Uuid::new_v4().simple()));
        let local = |error: std::io::Error| Failure::local(error.to_string());
        let mut file = tokio::fs::File::create(&partial).await.map_err(local)?;
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = match chunk {
                Ok(chunk) => chunk,
                Err(error) => {
                    let _ = tokio::fs::remove_file(&partial).await;
                    return Err(describe(error));
                }
            };
            file.write_all(&chunk).await.map_err(local)?;
        }
        file.flush().await.map_err(local)?;
        tokio::fs::rename(&partial, &target).await.map_err(local)
    }
}

#[cfg(test)]
mod tests {
    use super::normalize_endpoint;

    #[test]
    fn remote_services_must_use_tls() {
        assert!(normalize_endpoint("http://agent.example.net").is_err());
        assert!(normalize_endpoint("agent.example.net").is_err());
        assert_eq!(
            normalize_endpoint("https://agent.example.net/").unwrap(),
            "https://agent.example.net"
        );
    }

    #[test]
    fn loopback_may_use_http_for_tunnels() {
        assert!(normalize_endpoint("http://127.0.0.1:8710").is_ok());
        assert!(normalize_endpoint("http://localhost:8710").is_ok());
    }
}
