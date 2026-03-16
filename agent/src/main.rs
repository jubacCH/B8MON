mod config;
mod collector;
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
use tracing::{info, warn, error};

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

        // Report to server
        match api.report(&metrics, &logs).await {
            Ok(resp) => {
                if let Some(sc) = resp.config {
                    let mut guard = server_config.write().await;
                    *guard = sc;
                }
            }
            Err(e) => {
                warn!("Report failed: {e}");
            }
        }

        // Auto-update check (every 5 minutes)
        let count = update_counter.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let checks_per_update = (300 / cfg.interval).max(1);
        if count % checks_per_update == 0 {
            match updater::check_and_update(&api).await {
                Ok(true) => {
                    info!("Update applied, restarting...");
                    std::process::exit(0);
                }
                Ok(false) => {} // no update
                Err(e) => warn!("Update check failed: {e}"),
            }
        }

        tokio::time::sleep(std::time::Duration::from_secs(cfg.interval)).await;
    }
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
