//! Device tokens live in the OS credential store and are read only here.
//! Linux development falls back to a 0600 file when no Secret Service is running.

use std::path::Path;

const SERVICE: &str = "a-msg";

pub fn store(data_dir: &Path, service_id: &str, token: &str) -> Result<(), String> {
    match keyring::Entry::new(SERVICE, service_id).and_then(|entry| entry.set_password(token)) {
        Ok(()) => Ok(()),
        Err(error) => fallback::store(data_dir, service_id, token, &error.to_string()),
    }
}

pub fn load(data_dir: &Path, service_id: &str) -> Option<String> {
    keyring::Entry::new(SERVICE, service_id)
        .and_then(|entry| entry.get_password())
        .ok()
        .or_else(|| fallback::load(data_dir, service_id))
}

pub fn delete(data_dir: &Path, service_id: &str) {
    if let Ok(entry) = keyring::Entry::new(SERVICE, service_id) {
        let _ = entry.delete_credential();
    }
    fallback::delete(data_dir, service_id);
}

#[cfg(target_os = "macos")]
mod fallback {
    use std::path::Path;

    pub fn store(_: &Path, _: &str, _: &str, error: &str) -> Result<(), String> {
        Err(format!("Keychain refused the credential: {error}"))
    }
    pub fn load(_: &Path, _: &str) -> Option<String> {
        None
    }
    pub fn delete(_: &Path, _: &str) {}
}

#[cfg(not(target_os = "macos"))]
mod fallback {
    use std::fs;
    use std::os::unix::fs::OpenOptionsExt;
    use std::path::{Path, PathBuf};

    fn path(dir: &Path, service_id: &str) -> PathBuf {
        dir.join(format!("credential-{service_id}"))
    }

    pub fn store(dir: &Path, service_id: &str, token: &str, _: &str) -> Result<(), String> {
        use std::io::Write;
        let mut file = fs::OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .mode(0o600)
            .open(path(dir, service_id))
            .map_err(|error| error.to_string())?;
        file.write_all(token.as_bytes())
            .map_err(|error| error.to_string())
    }
    pub fn load(dir: &Path, service_id: &str) -> Option<String> {
        fs::read_to_string(path(dir, service_id)).ok()
    }
    pub fn delete(dir: &Path, service_id: &str) {
        let _ = fs::remove_file(path(dir, service_id));
    }
}
