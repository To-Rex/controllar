//! Persistent configuration (`config.json`) and filesystem layout helpers.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::types::{DeviceId, Edge, Hotkey, LastServer, Mode, PairedDevice};

pub const CONFIG_FILE: &str = "config.json";
pub const IDENTITY_DIR: &str = "identity";
pub const LOGS_DIR: &str = "logs";
/// Default TCP port (same as Barrier's, for familiarity).
pub const DEFAULT_PORT: u16 = 24800;

/// Everything the engine persists between runs.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub version: u32,
    pub device_id: DeviceId,
    pub device_name: String,
    pub mode: Mode,
    pub listen_port: u16,
    /// Local screen edge the cursor exits through (server role).
    pub edge: Edge,
    pub hotkey: Hotkey,
    pub clipboard_enabled: bool,
    pub clipboard_max_bytes: usize,
    pub log_level: String,
    /// Where the client should look for its server on next launch.
    pub last_server: Option<LastServer>,
    /// Devices that completed pairing (device memory).
    pub paired_devices: Vec<PairedDevice>,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            version: 1,
            device_id: Uuid::new_v4().to_string(),
            device_name: crate::util::hostname(),
            mode: Mode::Server,
            listen_port: DEFAULT_PORT,
            edge: Edge::Right,
            hotkey: Hotkey::default(),
            clipboard_enabled: true,
            clipboard_max_bytes: 1 << 20, // 1 MiB of text
            log_level: "info".to_string(),
            last_server: None,
            paired_devices: Vec::new(),
        }
    }
}

impl Config {
    /// Load from `<dir>/config.json`, creating a default file on first run.
    ///
    /// A corrupt file is moved aside (`.corrupt-<ts>`) so the app always
    /// starts; the user is informed through a notice.
    pub fn load(dir: &Path) -> Result<(Self, Option<String>)> {
        let path = dir.join(CONFIG_FILE);
        if !path.exists() {
            let config = Config::default();
            config.save(dir)?;
            return Ok((config, None));
        }
        let raw = std::fs::read_to_string(&path)
            .with_context(|| format!("reading {}", path.display()))?;
        match serde_json::from_str::<Config>(&raw) {
            Ok(mut config) => {
                if config.version == 0 {
                    config.version = 1;
                }
                Ok((config, None))
            }
            Err(err) => {
                let backup = dir.join(format!("config.corrupt-{}.json", crate::util::now_ms()));
                std::fs::rename(&path, &backup).ok();
                let config = Config::default();
                config.save(dir)?;
                Ok((
                    config,
                    Some(format!(
                        "config.json was invalid ({err}); moved to {} and recreated",
                        backup.display()
                    )),
                ))
            }
        }
    }

    /// Persist atomically (temp file + rename), with restrictive permissions.
    pub fn save(&self, dir: &Path) -> Result<()> {
        ensure_dir(dir)?;
        let path = dir.join(CONFIG_FILE);
        let tmp = dir.join(format!(".config.json.tmp-{}", std::process::id()));
        let json = serde_json::to_string_pretty(self)?;
        std::fs::write(&tmp, json).with_context(|| format!("writing {}", tmp.display()))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600)).ok();
        }
        std::fs::rename(&tmp, &path).with_context(|| format!("replacing {}", path.display()))?;
        Ok(())
    }

    pub fn find_paired(&self, device_id: &str) -> Option<&PairedDevice> {
        self.paired_devices.iter().find(|d| d.id == device_id)
    }

    pub fn upsert_paired(&mut self, device: PairedDevice) {
        if let Some(existing) = self.paired_devices.iter_mut().find(|d| d.id == device.id) {
            *existing = device;
        } else {
            self.paired_devices.push(device);
        }
    }

    pub fn forget_paired(&mut self, device_id: &str) -> bool {
        let before = self.paired_devices.len();
        self.paired_devices.retain(|d| d.id != device_id);
        self.paired_devices.len() != before
    }
}

/// Create `dir` (and parents) with 0700 permissions on Unix.
pub fn ensure_dir(dir: &Path) -> Result<()> {
    std::fs::create_dir_all(dir).with_context(|| format!("creating {}", dir.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700)).ok();
    }
    Ok(())
}

/// Platform default: `~/Library/Application Support/FlowLink` on macOS,
/// `%APPDATA%\FlowLink` on Windows.
pub fn default_config_dir() -> PathBuf {
    dirs::config_dir()
        .map(|d| d.join("FlowLink"))
        .unwrap_or_else(|| PathBuf::from(".flowlink"))
}

/// `<config_dir>/identity`
pub fn identity_dir(dir: &Path) -> PathBuf {
    dir.join(IDENTITY_DIR)
}

/// `<config_dir>/logs`
pub fn logs_dir(dir: &Path) -> PathBuf {
    dir.join(LOGS_DIR)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn default_config_is_persisted_and_reloaded() {
        let dir = TempDir::new().unwrap();
        let (first, notice) = Config::load(dir.path()).unwrap();
        assert!(notice.is_none());
        assert!(dir.path().join(CONFIG_FILE).exists());

        let (second, _) = Config::load(dir.path()).unwrap();
        assert_eq!(first.device_id, second.device_id);
        assert_eq!(first.device_name, second.device_name);
    }

    #[test]
    fn corrupt_config_is_backed_up() {
        let dir = TempDir::new().unwrap();
        std::fs::write(dir.path().join(CONFIG_FILE), "{ not json").unwrap();
        let (config, notice) = Config::load(dir.path()).unwrap();
        assert!(notice.is_some());
        assert_eq!(config.version, 1);
        // A backup file now exists.
        let backups: Vec<_> = std::fs::read_dir(dir.path())
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| {
                e.file_name()
                    .to_string_lossy()
                    .starts_with("config.corrupt-")
            })
            .collect();
        assert_eq!(backups.len(), 1);
    }

    #[test]
    fn paired_device_memory() {
        let mut config = Config::default();
        config.upsert_paired(PairedDevice {
            id: "dev-1".into(),
            name: "PC".into(),
            token: "aa".into(),
            fingerprint: "ff".into(),
            last_addr: None,
            last_seen_ms: 0,
        });
        assert!(config.find_paired("dev-1").is_some());
        config.upsert_paired(PairedDevice {
            id: "dev-1".into(),
            name: "PC 2".into(),
            token: "bb".into(),
            fingerprint: "ff".into(),
            last_addr: None,
            last_seen_ms: 1,
        });
        assert_eq!(config.paired_devices.len(), 1);
        assert_eq!(config.find_paired("dev-1").unwrap().name, "PC 2");
        assert!(config.forget_paired("dev-1"));
        assert!(config.find_paired("dev-1").is_none());
    }
}
