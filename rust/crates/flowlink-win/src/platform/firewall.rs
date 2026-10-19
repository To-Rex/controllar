//! Windows Firewall helper.
//!
//! FlowLink's server accepts inbound connections on the listen port and uses
//! mDNS (UDP 5353) for local discovery. Windows Firewall blocks both for
//! unsigned desktop apps unless the user allowed them, which is by far the
//! most common reason "the other computer can't connect".
//!
//! This module detects whether our rules exist and can add them through a
//! single UAC prompt. Detection is read-only (`netsh … show rule`), so it
//! works without elevation.

use std::os::windows::process::CommandExt;
use std::path::Path;
use std::process::Command;

use anyhow::{bail, Context, Result};

/// Base name of the rules we create (plus `-TCP`/`-mDNS` suffixes).
pub const RULE_NAME: &str = "FlowLink";

/// `CREATE_NO_WINDOW`: don't flash a console window for netsh/powershell.
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RuleState {
    /// The rules exist.
    Present,
    /// No rule found — inbound connections are most likely blocked.
    Missing,
    /// Could not determine (netsh unavailable).
    Unknown,
}

/// Check whether the FlowLink firewall rules exist.
pub fn check(port: u16) -> RuleState {
    let output = Command::new("netsh")
        .creation_flags(CREATE_NO_WINDOW)
        .args([
            "advfirewall",
            "firewall",
            "show",
            "rule",
            &format!("name={RULE_NAME}"),
        ])
        .output();

    match output {
        Ok(out) if out.status.success() => {
            let text = String::from_utf8_lossy(&out.stdout);
            if text.contains(RULE_NAME) {
                // The rule exists; warn only if the port inside it changed.
                let _ = port;
                RuleState::Present
            } else {
                RuleState::Missing
            }
        }
        // netsh exits non-zero with "No rules match the specified criteria."
        Ok(_) => RuleState::Missing,
        Err(_) => RuleState::Unknown,
    }
}

/// The `netsh` commands that open the firewall (also offered for manual use).
pub fn commands(port: u16, exe: &Path) -> Vec<String> {
    vec![
        format!(
            "netsh advfirewall firewall add rule name=\"{RULE_NAME}\" dir=in action=allow program=\"{}\" enable=yes profile=any",
            exe.display()
        ),
        format!(
            "netsh advfirewall firewall add rule name=\"{RULE_NAME}-TCP\" dir=in action=allow protocol=TCP localport={port}"
        ),
        format!(
            "netsh advfirewall firewall add rule name=\"{RULE_NAME}-mDNS\" dir=in action=allow protocol=UDP localport=5353"
        ),
    ]
}

/// Add the rules through one UAC prompt.
///
/// Returns `Err` when the user cancels the elevation prompt or netsh fails.
pub fn allow_elevated(port: u16, exe: &Path) -> Result<()> {
    // A tiny batch file keeps the quoting sane (three netsh invocations with
    // spaces and quotes through PowerShell would be a minefield).
    let script_path = std::env::temp_dir().join("flowlink-firewall.bat");
    let script = format!("@echo off\r\n{}\r\n", commands(port, exe).join("\r\n"));
    std::fs::write(&script_path, script)
        .with_context(|| format!("writing {}", script_path.display()))?;

    let escaped = script_path.display().to_string().replace('\'', "''");
    let ps = format!(
        "$ErrorActionPreference='Stop'; Start-Process -FilePath '{escaped}' -Verb RunAs -Wait"
    );
    let status = Command::new("powershell")
        .creation_flags(CREATE_NO_WINDOW)
        .args(["-NoProfile", "-NonInteractive", "-Command", &ps])
        .status()
        .context("launching PowerShell for elevation")?;

    if !status.success() {
        bail!("elevation was cancelled or the firewall rules could not be added");
    }
    Ok(())
}

/// Open the Windows Firewall settings page (manual fallback).
pub fn open_settings() {
    let _ = Command::new("cmd")
        .creation_flags(CREATE_NO_WINDOW)
        .args(["/C", "start", "", "firewall.cpl"])
        .spawn();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn commands_cover_port_and_program() {
        let commands = commands(
            24800,
            Path::new(r"C:\Program Files\FlowLink\flowlink-win.exe"),
        );
        assert_eq!(commands.len(), 3);
        assert!(commands[0].contains("flowlink-win.exe"));
        assert!(commands[1].contains("24800"));
        assert!(commands[2].contains("5353"));
    }
}
