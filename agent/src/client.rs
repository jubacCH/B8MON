use reqwest::Client;
use serde::{Deserialize, Serialize};
use tracing::{debug, warn};

use crate::collector::SystemMetrics;
use crate::config::{Config, ServerConfig};

pub struct ApiClient {
    client: Client,
    base_url: String,
    token: String,
}

#[derive(Debug, Serialize)]
pub struct LogEntry {
    pub timestamp: String,
    pub severity: u8,
    pub app_name: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub facility: Option<u8>,
}

#[derive(Debug, Deserialize)]
pub struct ReportResponse {
    pub ok: bool,
    pub config: Option<ServerConfig>,
}

#[derive(Debug, Deserialize)]
pub struct EnrollResponse {
    pub ok: bool,
    pub token: String,
    pub agent_id: u64,
}

#[derive(Debug, Deserialize)]
pub struct VersionResponse {
    pub hash: String,
}

impl ApiClient {
    pub fn new(cfg: &Config) -> Self {
        let client = Client::builder()
            .timeout(std::time::Duration::from_secs(15))
            .danger_accept_invalid_certs(true)
            .build()
            .expect("Failed to create HTTP client");

        Self {
            client,
            base_url: cfg.server.trim_end_matches('/').to_string(),
            token: String::new(),
        }
    }

    pub fn with_token(cfg: &Config, token: &str) -> Self {
        let client = Client::builder()
            .timeout(std::time::Duration::from_secs(15))
            .danger_accept_invalid_certs(true)
            .build()
            .expect("Failed to create HTTP client");

        Self {
            client,
            base_url: cfg.server.trim_end_matches('/').to_string(),
            token: token.to_string(),
        }
    }

    /// Enroll with the server using hostname and enrollment key.
    pub async fn enroll(&self, cfg: &Config) -> anyhow::Result<String> {
        let hostname = hostname::get()
            .map(|h| h.to_string_lossy().to_string())
            .unwrap_or_else(|_| "unknown".into());

        #[derive(Serialize)]
        struct EnrollPayload {
            enrollment_key: String,
            hostname: String,
            platform: String,
            arch: String,
        }

        let resp = self
            .client
            .post(format!("{}/api/agent/enroll", self.base_url))
            .json(&EnrollPayload {
                enrollment_key: cfg.enrollment_key.clone(),
                hostname,
                platform: std::env::consts::OS.into(),
                arch: std::env::consts::ARCH.into(),
            })
            .send()
            .await?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            anyhow::bail!("Enrollment failed ({}): {}", status, body);
        }

        let data: EnrollResponse = resp.json().await?;
        if !data.ok {
            anyhow::bail!("Enrollment rejected by server");
        }

        Ok(data.token)
    }

    /// Send metrics + logs to the server.
    pub async fn report(
        &self,
        metrics: &SystemMetrics,
        logs: &[LogEntry],
    ) -> anyhow::Result<ReportResponse> {
        let resp = self
            .client
            .post(format!("{}/api/agent/report", self.base_url))
            .bearer_auth(&self.token)
            .json(metrics)
            .send()
            .await?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            anyhow::bail!("Report failed ({}): {}", status, body);
        }

        let report_resp: ReportResponse = resp.json().await?;
        debug!("Report OK");

        // Send logs separately if any
        if !logs.is_empty() {
            self.send_logs(&metrics.hostname, logs).await;
        }

        Ok(report_resp)
    }

    /// Send collected logs to the server.
    async fn send_logs(&self, hostname: &str, logs: &[LogEntry]) {
        #[derive(Serialize)]
        struct LogPayload<'a> {
            hostname: &'a str,
            logs: &'a [LogEntry],
        }

        // Send in batches of 500
        for chunk in logs.chunks(500) {
            let result = self
                .client
                .post(format!("{}/api/agent/logs", self.base_url))
                .bearer_auth(&self.token)
                .json(&LogPayload {
                    hostname,
                    logs: chunk,
                })
                .send()
                .await;

            match result {
                Ok(resp) if resp.status().is_success() => {
                    debug!("Sent {} log entries", chunk.len());
                }
                Ok(resp) => {
                    warn!("Log submission failed: {}", resp.status());
                }
                Err(e) => {
                    warn!("Log submission error: {e}");
                }
            }
        }
    }

    /// Check latest agent version hash from server.
    pub async fn get_version_hash(&self, platform: &str) -> anyhow::Result<String> {
        let resp = self
            .client
            .get(format!(
                "{}/api/agent/version/{}",
                self.base_url, platform
            ))
            .send()
            .await?;

        if !resp.status().is_success() {
            anyhow::bail!("Version check failed: {}", resp.status());
        }

        let data: VersionResponse = resp.json().await?;
        Ok(data.hash)
    }

    /// Download agent binary from server.
    pub async fn download_agent(&self, platform: &str) -> anyhow::Result<Vec<u8>> {
        let url = format!("{}/agents/download/{}", self.base_url, platform);
        let resp = self
            .client
            .get(&url)
            .timeout(std::time::Duration::from_secs(120))
            .send()
            .await?;

        if !resp.status().is_success() {
            anyhow::bail!("Download failed: {}", resp.status());
        }

        let bytes = resp.bytes().await?;
        Ok(bytes.to_vec())
    }
}
