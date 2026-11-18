//! Local state owned by the core. Every row carries the service identity, so pairing to a different
//! server can neither mix histories nor resend another server's outbox.

use rusqlite::{Connection, OptionalExtension, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::Path;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct OutboxItem {
    pub client_message_id: String,
    pub space_id: String,
    pub markdown: String,
    pub artifacts: Value,
    pub created_at: String,
    pub state: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// A server this device holds a credential for. The credential itself stays in the Keychain.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SavedServer {
    pub service_id: String,
    pub endpoint: String,
}

pub struct Store {
    db: Connection,
}

impl Store {
    pub fn open(path: &Path) -> rusqlite::Result<Self> {
        let db = Connection::open(path)?;
        Self::init(db)
    }

    #[cfg(test)]
    pub fn memory() -> rusqlite::Result<Self> {
        Self::init(Connection::open_in_memory()?)
    }

    fn init(db: Connection) -> rusqlite::Result<Self> {
        db.execute_batch(
            "PRAGMA journal_mode=WAL;
             CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
             CREATE TABLE IF NOT EXISTS cache (
               service_id TEXT NOT NULL, key TEXT NOT NULL, body TEXT NOT NULL,
               PRIMARY KEY (service_id, key));
             CREATE TABLE IF NOT EXISTS outbox (
               service_id TEXT NOT NULL, client_message_id TEXT NOT NULL, space_id TEXT NOT NULL,
               markdown TEXT NOT NULL, artifacts TEXT NOT NULL, created_at TEXT NOT NULL,
               state TEXT NOT NULL, error TEXT,
               PRIMARY KEY (service_id, client_message_id));",
        )?;
        Ok(Self { db })
    }

    pub fn get(&self, key: &str) -> Option<String> {
        self.db
            .query_row("SELECT value FROM kv WHERE key = ?1", [key], |row| {
                row.get(0)
            })
            .optional()
            .ok()
            .flatten()
    }

    pub fn set(&self, key: &str, value: &str) {
        let _ = self.db.execute(
            "INSERT INTO kv(key, value) VALUES (?1, ?2) ON CONFLICT(key) DO UPDATE SET value = ?2",
            params![key, value],
        );
    }

    pub fn remove(&self, key: &str) {
        let _ = self.db.execute("DELETE FROM kv WHERE key = ?1", [key]);
    }

    pub fn servers(&self) -> Vec<SavedServer> {
        self.get("servers")
            .and_then(|list| serde_json::from_str(&list).ok())
            .unwrap_or_default()
    }

    /// Keeps one entry per server, with the address it was last reached at.
    pub fn remember_server(&self, service_id: &str, endpoint: &str) {
        let mut servers = self.servers();
        servers.retain(|server| server.service_id != service_id);
        servers.push(SavedServer {
            service_id: service_id.to_string(),
            endpoint: endpoint.to_string(),
        });
        self.save_servers(&servers);
    }

    pub fn forget_server(&self, service_id: &str) {
        let mut servers = self.servers();
        servers.retain(|server| server.service_id != service_id);
        self.save_servers(&servers);
    }

    fn save_servers(&self, servers: &[SavedServer]) {
        if let Ok(list) = serde_json::to_string(servers) {
            self.set("servers", &list);
        }
    }

    pub fn cache_put(&self, service_id: &str, key: &str, body: &str) {
        let _ = self.db.execute(
            "INSERT INTO cache(service_id, key, body) VALUES (?1, ?2, ?3)
             ON CONFLICT(service_id, key) DO UPDATE SET body = ?3",
            params![service_id, key, body],
        );
    }

    pub fn cache_get(&self, service_id: &str, key: &str) -> Option<String> {
        self.db
            .query_row(
                "SELECT body FROM cache WHERE service_id = ?1 AND key = ?2",
                params![service_id, key],
                |row| row.get(0),
            )
            .optional()
            .ok()
            .flatten()
    }

    pub fn outbox_add(&self, service_id: &str, item: &OutboxItem) -> rusqlite::Result<()> {
        self.db.execute(
            "INSERT OR IGNORE INTO outbox
             (service_id, client_message_id, space_id, markdown, artifacts, created_at, state, error)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                service_id,
                item.client_message_id,
                item.space_id,
                item.markdown,
                item.artifacts.to_string(),
                item.created_at,
                item.state,
                item.error
            ],
        )?;
        Ok(())
    }

    fn row(row: &rusqlite::Row<'_>) -> rusqlite::Result<OutboxItem> {
        let artifacts: String = row.get(3)?;
        Ok(OutboxItem {
            client_message_id: row.get(0)?,
            space_id: row.get(1)?,
            markdown: row.get(2)?,
            artifacts: serde_json::from_str(&artifacts).unwrap_or(Value::Array(vec![])),
            created_at: row.get(4)?,
            state: row.get(5)?,
            error: row.get(6)?,
        })
    }

    pub fn outbox_list(&self, service_id: &str) -> Vec<OutboxItem> {
        let Ok(mut query) = self.db.prepare(
            "SELECT client_message_id, space_id, markdown, artifacts, created_at, state, error
             FROM outbox WHERE service_id = ?1 ORDER BY created_at, client_message_id",
        ) else {
            return vec![];
        };
        query
            .query_map([service_id], Self::row)
            .map(|rows| rows.flatten().collect())
            .unwrap_or_default()
    }

    pub fn outbox_next(&self, service_id: &str) -> Option<OutboxItem> {
        self.outbox_list(service_id)
            .into_iter()
            .find(|item| item.state == "queued")
    }

    pub fn outbox_mark(&self, service_id: &str, id: &str, state: &str, error: Option<&str>) {
        let _ = self.db.execute(
            "UPDATE outbox SET state = ?3, error = ?4 WHERE service_id = ?1 AND client_message_id = ?2",
            params![service_id, id, state, error],
        );
    }

    pub fn outbox_remove(&self, service_id: &str, id: &str) {
        let _ = self.db.execute(
            "DELETE FROM outbox WHERE service_id = ?1 AND client_message_id = ?2",
            params![service_id, id],
        );
    }

    /// A send interrupted by a crash goes back in the queue. The original client ID makes it safe.
    pub fn outbox_recover(&self) {
        let _ = self.db.execute(
            "UPDATE outbox SET state = 'queued' WHERE state = 'sending'",
            [],
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn saved_servers_keep_one_entry_each_and_can_be_forgotten() {
        let store = Store::memory().unwrap();
        store.remember_server("a", "https://one.example");
        store.remember_server("b", "https://two.example");
        store.remember_server("a", "https://moved.example");
        let saved = store.servers();
        assert_eq!(saved.len(), 2);
        assert_eq!(saved[1].service_id, "a");
        assert_eq!(saved[1].endpoint, "https://moved.example");
        store.forget_server("a");
        assert_eq!(store.servers().len(), 1);
        assert_eq!(store.servers()[0].service_id, "b");
    }

    fn item(id: &str) -> OutboxItem {
        OutboxItem {
            client_message_id: id.into(),
            space_id: "space".into(),
            markdown: "hello".into(),
            artifacts: json!([]),
            created_at: "2026-11-06T00:00:00Z".into(),
            state: "queued".into(),
            error: None,
        }
    }

    #[test]
    fn outbox_is_keyed_by_server_identity() {
        let store = Store::memory().unwrap();
        store.outbox_add("server-a", &item("one")).unwrap();
        assert_eq!(store.outbox_list("server-a").len(), 1);
        assert!(store.outbox_list("server-b").is_empty());
        assert!(store.outbox_next("server-b").is_none());
    }

    #[test]
    fn repeating_an_enqueue_keeps_one_row() {
        let store = Store::memory().unwrap();
        store.outbox_add("a", &item("one")).unwrap();
        store.outbox_add("a", &item("one")).unwrap();
        assert_eq!(store.outbox_list("a").len(), 1);
    }

    #[test]
    fn interrupted_sends_return_to_the_queue() {
        let store = Store::memory().unwrap();
        store.outbox_add("a", &item("one")).unwrap();
        store.outbox_mark("a", "one", "sending", None);
        assert!(store.outbox_next("a").is_none());
        store.outbox_recover();
        assert!(store.outbox_next("a").is_some());
    }

    #[test]
    fn cache_does_not_cross_servers() {
        let store = Store::memory().unwrap();
        store.cache_put("a", "snapshot", "{}");
        assert_eq!(store.cache_get("a", "snapshot").as_deref(), Some("{}"));
        assert!(store.cache_get("b", "snapshot").is_none());
    }
}
