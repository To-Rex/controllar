//! Autostart registration via the per-user `Run` key.
//!
//! `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` is the non-elevated,
//! per-user autostart list Windows already understands; no service or task
//! scheduler entry is involved. The stored command line is the quoted
//! executable path plus `--autostart`, so the app knows it was started with
//! the session and can go straight to the tray.

use std::path::Path;

use anyhow::{Context, Result};
use tracing::{debug, info};
use winreg::enums::HKEY_CURRENT_USER;
use winreg::RegKey;

/// Registry key holding the per-user autostart entries.
const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";

/// Value name of our entry.
const VALUE_NAME: &str = "FlowLink";

/// Command line stored in the registry for `exe_path`.
pub fn command_line(exe_path: &Path) -> String {
    // Quoted so paths with spaces survive; `--autostart` makes the shell start
    // hidden in the tray.
    format!("\"{}\" --autostart", exe_path.display())
}

/// True when a `FlowLink` autostart entry exists.
pub fn is_enabled() -> bool {
    match RegKey::predef(HKEY_CURRENT_USER).open_subkey(RUN_KEY) {
        Ok(key) => match key.get_value::<String, _>(VALUE_NAME) {
            Ok(value) => {
                debug!(value, "autostart entry present");
                true
            }
            Err(_) => false,
        },
        Err(err) => {
            debug!(%err, "could not open the autostart key");
            false
        }
    }
}

/// Create/overwrite the autostart entry.
pub fn enable(exe_path: &Path) -> Result<()> {
    let key = RegKey::predef(HKEY_CURRENT_USER)
        .create_subkey(RUN_KEY)
        .context("opening the per-user Run key for writing")?
        .0;
    key.set_value(VALUE_NAME, &command_line(exe_path))
        .with_context(|| format!("writing the {VALUE_NAME} autostart entry"))?;
    info!(path = %exe_path.display(), "autostart enabled");
    Ok(())
}

/// Remove the autostart entry (a missing entry is not an error).
pub fn disable() -> Result<()> {
    let key = match RegKey::predef(HKEY_CURRENT_USER).open_subkey(RUN_KEY) {
        Ok(key) => key,
        // No Run key at all → nothing to remove.
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(err) => return Err(err).context("opening the per-user Run key"),
    };
    match key.delete_value(VALUE_NAME) {
        Ok(()) => {
            info!("autostart disabled");
            Ok(())
        }
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(err) => Err(err).context("removing the autostart entry"),
    }
}
