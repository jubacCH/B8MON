mod config;
mod collector;
mod checks;
mod client;
mod updater;

#[cfg(target_os = "linux")]
mod collector_linux;
#[cfg(target_os = "windows")]
mod collector_windows;
#[cfg(target_os = "linux")]
mod logs_linux;
#[cfg(target_os = "windows")]
mod logs_windows;

use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::{debug, info, warn, error};

use config::Config;
use client::ApiClient;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "nodeglow_agent=info".into()),
        )
        .compact()
        .init();

    let cfg = match Config::load() {
        Ok(c) => c,
        Err(e) => {
            error!("Failed to load config: {e}");
            std::process::exit(1);
        }
    };

    info!(
        server = %cfg.server,
        interval = cfg.interval,
        "Nodeglow agent starting"
    );

    let api = ApiClient::new(&cfg);

    // Enroll if no token
    let token = if cfg.token.is_empty() {
        info!("No token found, attempting enrollment...");
        match api.enroll(&cfg).await {
            Ok(t) => {
                info!("Enrolled successfully");
                // Save token to config
                let mut new_cfg = cfg.clone();
                new_cfg.token = t.clone();
                if let Err(e) = new_cfg.save() {
                    warn!("Failed to save config with token: {e}");
                }
                t
            }
            Err(e) => {
                error!("Enrollment failed: {e}");
                std::process::exit(1);
            }
        }
    } else {
        cfg.token.clone()
    };

    let api = ApiClient::with_token(&cfg, &token);
    let server_config: Arc<RwLock<config::ServerConfig>> =
        Arc::new(RwLock::new(config::ServerConfig::default()));
    let update_counter = Arc::new(std::sync::atomic::AtomicU64::new(0));

    // Probe mode state. All of it stays empty for an agent the backend never
    // assigns a host to: no client is built, no check task is spawned, and the
    // loop below behaves exactly as it did before.
    let mut assignments: Vec<checks::CheckAssignment> = Vec::new();
    let mut pending_results = checks::PendingResults::new();
    let mut probe_client: Option<checks::ProbeClient> = None;

    let interval = std::time::Duration::from_secs(cfg.interval);

    info!("Entering main loop (interval={}s)", cfg.interval);

    loop {
        // Collect metrics
        let metrics = match collector::collect().await {
            Ok(m) => m,
            Err(e) => {
                warn!("Metric collection failed: {e}");
                tokio::time::sleep(std::time::Duration::from_secs(cfg.interval)).await;
                continue;
            }
        };

        // Collect logs
        let logs = collect_logs(&server_config).await;

        // Report to server, handing over any check results collected since the
        // last successful heartbeat.
        let delivered = pending_results.len();
        match api.report(&metrics, &logs, pending_results.as_slice()).await {
            Ok(resp) => {
                // Only clear once the server has actually taken them.
                if delivered > 0 {
                    debug!("Delivered {delivered} check result(s)");
                }
                pending_results.clear();

                if let Some(sc) = resp.config {
                    let mut guard = server_config.write().await;
                    *guard = sc;
                }
                // Handle remote commands
                if let Some(cmd) = resp.command {
                    if cmd == "uninstall" {
                        info!("Received remote uninstall command");
                        run_uninstall();
                        // run_uninstall does not return on success
                    } else {
                        warn!("Unknown command: {cmd}");
                    }
                }

                // Probe assignments for the coming interval. An absent or empty
                // list means this agent is not a probe.
                let next = resp.checks.unwrap_or_default();
                checks::log_assignment_change(assignments.len(), next.len());
                assignments = next;
            }
            Err(e) => {
                // The results stay buffered (bounded) and go out with the next
                // successful heartbeat. The assignment list is kept as it is:
                // the probe carries on checking what it was last told to check.
                warn!("Report failed: {e}");
                if !pending_results.is_empty() {
                    warn!(
                        "{} check result(s) still undelivered ({} discarded since start)",
                        pending_results.len(),
                        pending_results.dropped_total()
                    );
                }
            }
        }

        // Auto-update check (every 5 minutes)
        let count = update_counter.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let checks_per_update = (300 / cfg.interval).max(1);
        if count % checks_per_update == 0 {
            match updater::check_and_update(&api, &cfg).await {
                Ok(true) => {
                    info!("Update applied, restarting...");
                    std::process::exit(0);
                }
                Ok(false) => {} // no update
                Err(e) => warn!("Update check failed: {e}"),
            }
        }

        if assignments.is_empty() {
            // Not a probe (or nothing assigned): plain sleep, as before.
            tokio::time::sleep(interval).await;
            continue;
        }

        // Probe mode: run the assigned checks *while* waiting out the interval,
        // so the checks never delay the next heartbeat and a host that is down
        // never delays another host.
        let client = match probe_client.as_ref() {
            Some(c) => c,
            None => match checks::ProbeClient::new(&cfg) {
                Ok(c) => probe_client.insert(c),
                Err(e) => {
                    // Without a client the checks cannot be performed. Report
                    // nothing rather than fabricating results: the backend
                    // tracks freshness per probe and will show these hosts as
                    // unknown, which is the truth.
                    error!("Probe mode unavailable: {e}");
                    tokio::time::sleep(interval).await;
                    continue;
                }
            },
        };

        let started = std::time::Instant::now();
        let (results, _) = tokio::join!(
            checks::run_checks(client, &assignments),
            tokio::time::sleep(interval)
        );
        let elapsed = started.elapsed();
        if elapsed > interval {
            warn!(
                "Check round took {}s for {} host(s), longer than the {}s report interval — \
                 this probe is assigned more hosts than it can check in one interval",
                elapsed.as_secs(),
                assignments.len(),
                interval.as_secs()
            );
        }
        pending_results.push_round(results);
    }
}

/// Execute platform-specific self-uninstall, then exit.
fn run_uninstall() -> ! {
    let exe = std::env::current_exe().unwrap_or_default();
    let dir = exe
        .parent()
        .unwrap_or(std::path::Path::new("."))
        .to_string_lossy()
        .to_string();

    #[cfg(target_os = "windows")]
    {
        let script = [
            "Start-Sleep -Seconds 3",
            "Stop-ScheduledTask -TaskName 'NodeglowAgent' -ErrorAction SilentlyContinue",
            "Start-Sleep -Seconds 1",
            "Unregister-ScheduledTask -TaskName 'NodeglowAgent' -Confirm:$false -ErrorAction SilentlyContinue",
            "Get-Process | Where-Object { $_.Path -like '*nodeglow*' } | Stop-Process -Force -ErrorAction SilentlyContinue",
            "Start-Sleep -Seconds 2",
            "Remove-Item -Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\NodeglowAgent' -Force -ErrorAction SilentlyContinue",
        ].join("; ");
        let full_script = format!("{}; Remove-Item -Path '{}' -Recurse -Force -ErrorAction SilentlyContinue", script, dir);
        let _ = std::process::Command::new("powershell")
            .args(["-ExecutionPolicy", "Bypass", "-Command", &full_script])
            .spawn();
    }

    #[cfg(target_os = "linux")]
    {
        let script = format!(
            "sleep 3 && systemctl stop nodeglow-agent 2>/dev/null; \
             systemctl disable nodeglow-agent 2>/dev/null; \
             rm -f /etc/systemd/system/nodeglow-agent.service; \
             systemctl daemon-reload 2>/dev/null; \
             rm -rf '{}'", dir
        );
        let _ = std::process::Command::new("sh")
            .args(["-c", &script])
            .spawn();
    }

    info!("Uninstall spawned, exiting agent");
    std::process::exit(0);
}

async fn collect_logs(
    server_config: &Arc<RwLock<config::ServerConfig>>,
) -> Vec<client::LogEntry> {
    #[cfg(target_os = "windows")]
    {
        let sc = server_config.read().await;
        logs_windows::collect_event_logs(&sc.log_channels, &sc.log_levels).await
    }
    #[cfg(target_os = "linux")]
    {
        let _sc = server_config.read().await;
        logs_linux::collect_journal_logs().await
    }
    #[cfg(not(any(target_os = "windows", target_os = "linux")))]
    {
        Vec::new()
    }
}
