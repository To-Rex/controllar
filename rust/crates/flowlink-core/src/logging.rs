//! Logging: rotating file logs + stderr, configured from `config.json`.

use std::path::Path;

use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;
use tracing_subscriber::EnvFilter;

pub use crate::config::logs_dir;

/// Initialize logging. Returns the file appender guard — keep it alive for
/// the lifetime of the process (dropping it stops the writer).
///
/// Safe to call once; subsequent calls are no-ops.
pub fn init(config_dir: &Path, level: &str) -> Option<tracing_appender::non_blocking::WorkerGuard> {
    let logs = logs_dir(config_dir);
    // Same restrictive 0o700 permissions as the rest of the config dir.
    crate::config::ensure_dir(&logs).ok();

    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new(format!("flowlink={level},flowlink_core={level},info")));

    let file_appender = tracing_appender::rolling::daily(&logs, "flowlink.log");
    let (file_writer, guard) = tracing_appender::non_blocking(file_appender);
    let file_layer = tracing_subscriber::fmt::layer()
        .with_writer(file_writer)
        .with_ansi(false)
        .with_target(true);

    let stderr_layer = tracing_subscriber::fmt::layer()
        .with_writer(std::io::stderr)
        .with_target(false);

    // try_init: ignores double initialization (e.g. in tests).
    let _ = tracing_subscriber::registry()
        .with(filter)
        .with(file_layer)
        .with(stderr_layer)
        .try_init();

    Some(guard)
}

/// Read the last `max_lines` lines of the newest log file (for the in-app
/// log viewer). Bounded read: at most 4 MiB from the end.
pub fn tail_lines(config_dir: &Path, max_lines: usize) -> Vec<String> {
    let dir = logs_dir(config_dir);
    let latest = std::fs::read_dir(&dir)
        .ok()
        .and_then(|entries| {
            entries
                .filter_map(|e| e.ok())
                .filter(|e| e.file_name().to_string_lossy().starts_with("flowlink.log"))
                .max_by_key(|e| e.metadata().and_then(|m| m.modified()).ok())
        })
        .map(|e| e.path());
    let Some(path) = latest else {
        return Vec::new();
    };

    let Ok(mut file) = std::fs::File::open(&path) else {
        return Vec::new();
    };
    use std::io::{Read, Seek, SeekFrom};
    let len = file.metadata().map(|m| m.len()).unwrap_or(0);
    let start = len.saturating_sub(4 * 1024 * 1024);
    if file.seek(SeekFrom::Start(start)).is_err() {
        return Vec::new();
    }
    let mut buf = String::new();
    if file.read_to_string(&mut buf).is_err() {
        return Vec::new();
    }
    let mut lines: Vec<String> = buf.lines().map(|l| l.to_string()).collect();
    if start > 0 && !lines.is_empty() {
        lines.remove(0); // first line may be cut in half
    }
    let skip = lines.len().saturating_sub(max_lines);
    lines.split_off(skip)
}
