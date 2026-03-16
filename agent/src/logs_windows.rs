use crate::client::LogEntry;
use tokio::process::Command;
use tracing::{debug, warn};
use std::sync::Mutex;

/// Track last-read timestamps per channel.
static LAST_TIMESTAMPS: Mutex<Option<std::collections::HashMap<String, String>>> = Mutex::new(None);

/// Collect Windows Event Log entries via PowerShell.
pub async fn collect_event_logs(channels: &str, levels: &str) -> Vec<LogEntry> {
    let channels: Vec<&str> = if channels.is_empty() {
        vec!["System", "Application"]
    } else {
        channels.split(',').map(str::trim).filter(|s| !s.is_empty()).collect()
    };

    let level_filter: Vec<u8> = if levels.is_empty() {
        vec![1, 2, 3] // Error, Warning, Critical
    } else {
        levels.split(',').filter_map(|s| s.trim().parse().ok()).collect()
    };

    let mut all_entries = Vec::new();
    let mut timestamps = LAST_TIMESTAMPS.lock().unwrap().clone().unwrap_or_default();

    for channel in &channels {
        let last_ts = timestamps.get(*channel).cloned();
        let entries = collect_channel(channel, &level_filter, last_ts.as_deref()).await;

        if let Some(newest) = entries.first() {
            timestamps.insert(channel.to_string(), newest.timestamp.clone());
        }

        all_entries.extend(entries);
    }

    *LAST_TIMESTAMPS.lock().unwrap() = Some(timestamps);

    debug!("Collected {} event log entries", all_entries.len());
    all_entries
}

async fn collect_channel(channel: &str, levels: &[u8], after: Option<&str>) -> Vec<LogEntry> {
    // Build PowerShell filter
    let level_csv = levels.iter().map(|l| l.to_string()).collect::<Vec<_>>().join(",");

    let time_filter = if let Some(ts) = after {
        format!(
            " -and $_.TimeCreated -gt [DateTime]::Parse('{}')",
            ts
        )
    } else {
        // First run: last 5 minutes
        " -and $_.TimeCreated -gt (Get-Date).AddMinutes(-5)".to_string()
    };

    let ps_script = format!(
        r#"
        try {{
            Get-WinEvent -FilterHashtable @{{LogName='{channel}'; Level={level_csv}}} -MaxEvents 100 -ErrorAction SilentlyContinue |
            Where-Object {{ $_ -ne $null{time_filter} }} |
            Select-Object TimeCreated, Level, ProviderName, Message |
            ForEach-Object {{
                $ts = $_.TimeCreated.ToUniversalTime().ToString('o')
                $msg = if ($_.Message) {{ $_.Message.Replace("`r`n"," ").Replace("`n"," ").Substring(0, [Math]::Min($_.Message.Length, 500)) }} else {{ "" }}
                "$ts|$($_.Level)|$($_.ProviderName)|$msg"
            }}
        }} catch {{}}
        "#
    );

    let output = Command::new("powershell")
        .args(["-NoProfile", "-NoLogo", "-Command", &ps_script])
        .output()
        .await;

    let Ok(output) = output else {
        warn!("PowerShell event log query failed for {channel}");
        return Vec::new();
    };

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut entries: Vec<LogEntry> = stdout
        .lines()
        .filter_map(|line| {
            let parts: Vec<&str> = line.splitn(4, '|').collect();
            if parts.len() < 4 {
                return None;
            }

            let timestamp = parts[0].to_string();
            let level: u8 = parts[1].parse().unwrap_or(4);
            let app_name = parts[2].to_string();
            let message = parts[3].to_string();

            if message.is_empty() {
                return None;
            }

            // Map Windows Event Level to syslog severity
            // Windows: 1=Critical, 2=Error, 3=Warning, 4=Info, 5=Verbose
            // Syslog:  2=Critical, 3=Error, 4=Warning, 6=Info, 7=Debug
            let severity = match level {
                1 => 2, // Critical
                2 => 3, // Error
                3 => 4, // Warning
                4 => 6, // Info
                5 => 7, // Debug
                _ => 6,
            };

            Some(LogEntry {
                timestamp,
                severity,
                app_name,
                message,
                facility: None,
            })
        })
        .collect();

    // Sort newest first
    entries.sort_by(|a, b| b.timestamp.cmp(&a.timestamp));

    entries
}
