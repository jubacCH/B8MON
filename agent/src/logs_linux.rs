use crate::client::LogEntry;
use tokio::process::Command;
use tracing::{debug, warn};
use std::sync::Mutex;

/// Cursor for incremental journalctl collection.
static JOURNAL_CURSOR: Mutex<Option<String>> = Mutex::new(None);

/// Collect logs from systemd journal (priority 0-4 = emerg..warning).
pub async fn collect_journal_logs() -> Vec<LogEntry> {
    let mut args = vec![
        "--output=json".to_string(),
        "-p".to_string(),
        "0..4".to_string(),
        "--no-pager".to_string(),
        "-n".to_string(),
        "200".to_string(),
    ];

    // Use cursor for incremental collection
    let cursor = JOURNAL_CURSOR.lock().unwrap().clone();
    if let Some(ref c) = cursor {
        args.push("--after-cursor".to_string());
        args.push(c.clone());
    } else {
        // First run: only recent entries
        args.push("--since".to_string());
        args.push("5 min ago".to_string());
    }

    let output = match Command::new("journalctl").args(&args).output().await {
        Ok(o) => o,
        Err(e) => {
            // journalctl not available, try /var/log/syslog fallback
            debug!("journalctl failed ({e}), trying syslog file fallback");
            return collect_syslog_file().await;
        }
    };

    if !output.status.success() {
        warn!("journalctl exited with {}", output.status);
        return Vec::new();
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut entries = Vec::new();
    let mut last_cursor = cursor;

    for line in stdout.lines() {
        let Ok(obj) = serde_json::from_str::<serde_json::Value>(line) else {
            continue;
        };

        // Update cursor
        if let Some(c) = obj.get("__CURSOR").and_then(|v| v.as_str()) {
            last_cursor = Some(c.to_string());
        }

        let priority: u8 = obj
            .get("PRIORITY")
            .and_then(|v| v.as_str().or_else(|| v.as_u64().map(|_| "")))
            .and_then(|s| if s.is_empty() {
                obj.get("PRIORITY").and_then(|v| v.as_u64()).map(|n| n as u8)
            } else {
                s.parse().ok()
            })
            .unwrap_or(6);

        // Map journald priority to syslog severity
        let severity = priority; // Same scale: 0=emerg, 4=warning

        let timestamp = obj
            .get("__REALTIME_TIMESTAMP")
            .and_then(|v| v.as_str())
            .and_then(|s| s.parse::<u64>().ok())
            .map(|us| {
                chrono::DateTime::from_timestamp((us / 1_000_000) as i64, ((us % 1_000_000) * 1000) as u32)
                    .unwrap_or_default()
                    .to_rfc3339()
            })
            .unwrap_or_default();

        let app_name = obj
            .get("SYSLOG_IDENTIFIER")
            .or_else(|| obj.get("_COMM"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let message = obj
            .get("MESSAGE")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        if message.is_empty() {
            continue;
        }

        entries.push(LogEntry {
            timestamp,
            severity,
            app_name,
            message,
            facility: obj
                .get("SYSLOG_FACILITY")
                .and_then(|v| v.as_str())
                .and_then(|s| s.parse().ok()),
        });
    }

    // Save cursor for next iteration
    if let Some(c) = last_cursor {
        *JOURNAL_CURSOR.lock().unwrap() = Some(c);
    }

    debug!("Collected {} journal entries", entries.len());
    entries
}

/// Fallback: parse /var/log/syslog or /var/log/messages.
async fn collect_syslog_file() -> Vec<LogEntry> {
    let paths = ["/var/log/syslog", "/var/log/messages"];
    let path = paths.iter().find(|p| std::path::Path::new(p).exists());
    let Some(path) = path else {
        return Vec::new();
    };

    let output = Command::new("tail")
        .args(["-n", "50", path])
        .output()
        .await;

    let Ok(output) = output else {
        return Vec::new();
    };

    let stdout = String::from_utf8_lossy(&output.stdout);
    stdout
        .lines()
        .filter_map(|line| {
            // Basic syslog line: "Mar 16 12:34:56 host app[pid]: message"
            let parts: Vec<&str> = line.splitn(5, ' ').collect();
            if parts.len() < 5 {
                return None;
            }
            let app_msg: Vec<&str> = parts[4].splitn(2, ": ").collect();
            let app = app_msg.first().unwrap_or(&"").trim_end_matches(|c: char| c == ']' || c.is_ascii_digit() || c == '[');
            let message = app_msg.get(1).unwrap_or(&"").to_string();

            if message.is_empty() {
                return None;
            }

            Some(LogEntry {
                timestamp: chrono::Utc::now().to_rfc3339(),
                severity: 4, // Default to warning
                app_name: app.to_string(),
                message,
                facility: None,
            })
        })
        .collect()
}
