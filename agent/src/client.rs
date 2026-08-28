use reqwest::Client;
use serde::{Deserialize, Serialize};
use tracing::{debug, warn};

use crate::checks::{CheckAssignment, CheckResult};
use crate::collector::SystemMetrics;
use crate::config::{Config, ServerConfig};

/// Hard cap on update-binary download size to prevent OOM/DoS from a malicious
/// or compromised server returning an unbounded body. 200 MiB.
const MAX_UPDATE_BYTES: usize = 200 * 1024 * 1024;

pub struct ApiClient {
    client: Client,
    base_url: String,
    token: String,
}

/// Build the shared HTTP client. TLS certificate validation is enforced by
/// default; it is only disabled when `allow_insecure_tls` is explicitly set
/// (config field / NODEGLOW_ALLOW_INSECURE_TLS), which is intended for testing.
fn build_client(cfg: &Config) -> Client {
    let mut builder = Client::builder().timeout(std::time::Duration::from_secs(15));

    if cfg.allow_insecure_tls {
        warn!("TLS certificate validation is DISABLED (allow_insecure_tls=true) — insecure, testing only");
        builder = builder.danger_accept_invalid_certs(true);
    }

    builder.build().expect("Failed to create HTTP client")
}

/// Heartbeat request body: the metrics object exactly as before, with
/// `check_results` added next to it. The field is omitted entirely when there
/// is nothing to deliver, so a non-probe agent sends the same bytes it always
/// did.
#[derive(Serialize)]
struct ReportPayload<'a> {
    #[serde(flatten)]
    metrics: &'a SystemMetrics,
    #[serde(skip_serializing_if = "is_empty")]
    check_results: &'a [CheckResult],
}

fn is_empty(results: &&[CheckResult]) -> bool {
    results.is_empty()
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
    pub command: Option<String>,
    /// Hosts this agent is responsible for checking until the next heartbeat.
    /// Absent or empty for every agent that is not a probe.
    #[serde(default)]
    pub checks: Option<Vec<CheckAssignment>>,
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
    /// Optional hex-encoded ed25519 detached signature over the binary bytes.
    /// Present only if the server has signing enabled (defense-in-depth on top
    /// of the authenticated TLS channel + SHA-256 hash check).
    #[serde(default)]
    pub signature: Option<String>,
}

impl ApiClient {
    pub fn new(cfg: &Config) -> Self {
        Self {
            client: build_client(cfg),
            base_url: cfg.server.trim_end_matches('/').to_string(),
            token: String::new(),
        }
    }

    pub fn with_token(cfg: &Config, token: &str) -> Self {
        Self {
            client: build_client(cfg),
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

    /// Send metrics + logs to the server, plus any probe check results that are
    /// waiting for delivery.
    pub async fn report(
        &self,
        metrics: &SystemMetrics,
        logs: &[LogEntry],
        check_results: &[CheckResult],
    ) -> anyhow::Result<ReportResponse> {
        let resp = self
            .client
            .post(format!("{}/api/agent/report", self.base_url))
            .bearer_auth(&self.token)
            .json(&ReportPayload {
                metrics,
                check_results,
            })
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

    /// Check latest agent version info (hash + optional signature) from server.
    pub async fn get_version_info(&self, platform: &str) -> anyhow::Result<VersionResponse> {
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
        Ok(data)
    }

    /// Download agent binary from server, enforcing a maximum size to avoid
    /// an OOM/DoS from an unbounded response body.
    pub async fn download_agent(&self, platform: &str) -> anyhow::Result<Vec<u8>> {
        let url = format!("{}/agents/download/{}", self.base_url, platform);
        let mut resp = self
            .client
            .get(&url)
            .timeout(std::time::Duration::from_secs(120))
            .send()
            .await?;

        if !resp.status().is_success() {
            anyhow::bail!("Download failed: {}", resp.status());
        }

        // Reject early if the advertised length already exceeds the cap.
        if let Some(len) = resp.content_length() {
            if len > MAX_UPDATE_BYTES as u64 {
                anyhow::bail!(
                    "Update too large: Content-Length {len} exceeds limit {MAX_UPDATE_BYTES}"
                );
            }
        }

        // Stream the body with a running byte cap so a server that lies about
        // (or omits) Content-Length still cannot exhaust memory.
        let mut buf: Vec<u8> = Vec::new();
        while let Some(chunk) = resp.chunk().await? {
            if buf.len() + chunk.len() > MAX_UPDATE_BYTES {
                anyhow::bail!(
                    "Update exceeded maximum download size of {MAX_UPDATE_BYTES} bytes, aborting"
                );
            }
            buf.extend_from_slice(&chunk);
        }

        Ok(buf)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::collector::SystemMetrics;
    use std::collections::{BTreeMap, HashMap};

    fn sample_metrics() -> SystemMetrics {
        SystemMetrics {
            hostname: "probe01".into(),
            platform: "linux".into(),
            arch: "x86_64".into(),
            agent_version: "0.1.0".into(),
            cpu_pct: 1.0,
            mem_total_mb: 1024,
            mem_used_mb: 256,
            mem_pct: 25.0,
            swap_total_mb: 0,
            swap_used_mb: 0,
            swap_pct: 0.0,
            disk_pct: 10.0,
            disks: Vec::new(),
            load_1: 0.0,
            load_5: 0.0,
            load_15: 0.0,
            uptime_s: 60,
            rx_bytes: 0,
            tx_bytes: 0,
            network_interfaces: Vec::new(),
            cpu_temp: None,
            processes: Vec::new(),
            os_info: None,
            cpu_info: None,
            docker_containers: Vec::new(),
            extra: HashMap::new(),
        }
    }

    fn sample_result() -> CheckResult {
        let mut detail = BTreeMap::new();
        detail.insert("icmp".to_string(), true);
        CheckResult {
            host_id: 42,
            success: true,
            latency_ms: Some(12.4),
            detail,
            ts: "2026-08-28T09:01:13Z".to_string(),
        }
    }

    #[test]
    fn a_non_probe_payload_is_unchanged() {
        let metrics = sample_metrics();
        let payload = ReportPayload {
            metrics: &metrics,
            check_results: &[],
        };
        let with_probe_field = serde_json::to_value(&payload).unwrap();
        let plain = serde_json::to_value(&metrics).unwrap();
        assert_eq!(
            with_probe_field, plain,
            "an agent with no check results must send exactly what it always sent"
        );
    }

    #[test]
    fn check_results_ride_along_at_the_top_level() {
        let metrics = sample_metrics();
        let results = vec![sample_result()];
        let payload = ReportPayload {
            metrics: &metrics,
            check_results: &results,
        };
        let json = serde_json::to_value(&payload).unwrap();

        // The metrics stay where the backend already reads them.
        assert_eq!(json["hostname"], "probe01");
        assert_eq!(
            json["check_results"],
            serde_json::json!([{
                "host_id": 42,
                "success": true,
                "latency_ms": 12.4,
                "detail": {"icmp": true},
                "ts": "2026-08-28T09:01:13Z"
            }])
        );
    }

    #[test]
    fn a_response_without_checks_yields_no_assignments() {
        let resp: ReportResponse = serde_json::from_str(
            r#"{"ok": true, "config": {"log_levels": "1", "log_channels": "System",
                "log_file_paths": "", "agent_log_level": "errors"}}"#,
        )
        .unwrap();
        assert!(resp.checks.is_none());
        assert!(resp.checks.unwrap_or_default().is_empty());
    }

    #[test]
    fn a_response_with_checks_carries_the_assignments() {
        let resp: ReportResponse = serde_json::from_str(
            r#"{"ok": true, "config": null, "command": null, "checks": [
                {"host_id": 42, "hostname": "nas.kunde.ch", "ip": "10.0.0.5",
                 "check_type": "icmp", "port": null, "timeout_seconds": 5.0}]}"#,
        )
        .unwrap();
        let checks = resp.checks.unwrap();
        assert_eq!(checks.len(), 1);
        assert_eq!(checks[0].host_id, 42);
        assert_eq!(checks[0].check_type, "icmp");
    }
}
