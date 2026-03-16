use serde::Serialize;
use std::collections::HashMap;

/// Unified system metrics payload — matches the backend API schema.
#[derive(Debug, Clone, Serialize)]
pub struct SystemMetrics {
    pub hostname: String,
    pub platform: String,
    pub arch: String,
    pub agent_version: String,

    // CPU
    pub cpu_pct: f64,

    // Memory
    pub mem_total_mb: u64,
    pub mem_used_mb: u64,
    pub mem_pct: f64,

    // Swap
    pub swap_total_mb: u64,
    pub swap_used_mb: u64,
    pub swap_pct: f64,

    // Primary disk
    pub disk_pct: f64,

    // All disks
    pub disks: Vec<DiskInfo>,

    // Load averages (Linux)
    pub load_1: f64,
    pub load_5: f64,
    pub load_15: f64,

    // Uptime
    pub uptime_s: u64,

    // Network (cumulative bytes)
    pub rx_bytes: u64,
    pub tx_bytes: u64,
    pub network_interfaces: Vec<NetInterface>,

    // CPU temperature (Celsius, optional)
    pub cpu_temp: Option<f64>,

    // Top processes by CPU
    pub processes: Vec<ProcessInfo>,

    // OS info (static, cached)
    pub os_info: Option<OsInfo>,
    pub cpu_info: Option<CpuInfo>,

    // Docker containers (optional)
    pub docker_containers: Vec<DockerContainer>,

    // Extra data
    #[serde(flatten)]
    pub extra: HashMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize)]
pub struct DiskInfo {
    pub mount: String,
    pub device: String,
    pub fs_type: String,
    pub total_gb: f64,
    pub used_gb: f64,
    pub pct: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct NetInterface {
    pub name: String,
    pub rx_bytes: u64,
    pub tx_bytes: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct ProcessInfo {
    pub pid: u32,
    pub name: String,
    pub cpu_pct: f64,
    pub mem_mb: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct OsInfo {
    pub name: String,
    pub version: String,
    pub build: String,
    pub arch: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct CpuInfo {
    pub model: String,
    pub cores: u32,
    pub threads: u32,
}

#[derive(Debug, Clone, Serialize)]
pub struct DockerContainer {
    pub name: String,
    pub image: String,
    pub status: String,
}

/// Collect all system metrics (dispatches to platform-specific implementation).
pub async fn collect() -> anyhow::Result<SystemMetrics> {
    #[cfg(target_os = "linux")]
    {
        crate::collector_linux::collect_metrics().await
    }
    #[cfg(target_os = "windows")]
    {
        crate::collector_windows::collect_metrics().await
    }
    #[cfg(not(any(target_os = "windows", target_os = "linux")))]
    {
        anyhow::bail!("Unsupported platform")
    }
}
