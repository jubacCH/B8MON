use crate::collector::*;
use std::collections::HashMap;
use tokio::process::Command;
#[allow(unused_imports)]
use tracing::debug;

const VERSION: &str = env!("CARGO_PKG_VERSION");

pub async fn collect_metrics() -> anyhow::Result<SystemMetrics> {
    let hostname = hostname::get()
        .map(|h| h.to_string_lossy().to_string())
        .unwrap_or_else(|_| "unknown".into());

    // sysinfo needs a brief delay between refreshes for CPU measurement
    let mut sys = sysinfo::System::new_all();
    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    sys.refresh_all();

    let cpu_pct = {
        let usage: f64 = sys.cpus().iter().map(|c| c.cpu_usage() as f64).sum::<f64>()
            / sys.cpus().len().max(1) as f64;
        (usage * 10.0).round() / 10.0
    };

    let mem_total_mb = sys.total_memory() / (1024 * 1024);
    let mem_used_mb = sys.used_memory() / (1024 * 1024);
    let mem_pct = if mem_total_mb > 0 {
        (mem_used_mb as f64 / mem_total_mb as f64 * 100.0 * 10.0).round() / 10.0
    } else {
        0.0
    };

    let swap_total_mb = sys.total_swap() / (1024 * 1024);
    let swap_used_mb = sys.used_swap() / (1024 * 1024);
    let swap_pct = if swap_total_mb > 0 {
        (swap_used_mb as f64 / swap_total_mb as f64 * 100.0 * 10.0).round() / 10.0
    } else {
        0.0
    };

    // Disks
    let disk_data = sysinfo::Disks::new_with_refreshed_list();
    let mut disks = Vec::new();
    for d in disk_data.list() {
        let total = d.total_space();
        if total == 0 {
            continue;
        }
        let used = total - d.available_space();
        let pct = (used as f64 / total as f64 * 100.0 * 10.0).round() / 10.0;
        disks.push(DiskInfo {
            mount: d.mount_point().to_string_lossy().to_string(),
            device: d.name().to_string_lossy().to_string(),
            fs_type: String::from_utf8_lossy(d.file_system()).to_string(),
            total_gb: (total as f64 / 1073741824.0 * 10.0).round() / 10.0,
            used_gb: (used as f64 / 1073741824.0 * 10.0).round() / 10.0,
            pct,
        });
    }

    let primary_disk_pct = disks
        .iter()
        .find(|d| d.mount == "C:\\" || d.mount == "/")
        .map(|d| d.pct)
        .unwrap_or(disks.first().map(|d| d.pct).unwrap_or(0.0));

    // Network
    let networks = sysinfo::Networks::new_with_refreshed_list();
    let mut net_interfaces = Vec::new();
    let mut total_rx = 0u64;
    let mut total_tx = 0u64;
    for (name, data) in networks.list() {
        let rx = data.total_received();
        let tx = data.total_transmitted();
        total_rx += rx;
        total_tx += tx;
        net_interfaces.push(NetInterface {
            name: name.clone(),
            rx_bytes: rx,
            tx_bytes: tx,
        });
    }

    // Uptime
    let uptime_s = sysinfo::System::uptime();

    // Processes (top 10 by CPU) - use ps via PowerShell for compatibility
    let procs = read_processes().await.unwrap_or_default();

    // OS info
    let os_info = Some(OsInfo {
        name: sysinfo::System::long_os_version().unwrap_or_else(|| "Windows".into()),
        version: sysinfo::System::os_version().unwrap_or_default(),
        build: sysinfo::System::kernel_version().unwrap_or_default(),
        arch: std::env::consts::ARCH.into(),
    });

    // CPU info
    let cpu_info = if !sys.cpus().is_empty() {
        Some(CpuInfo {
            model: sys.cpus()[0].brand().to_string(),
            cores: sys.physical_core_count().unwrap_or(0) as u32,
            threads: sys.cpus().len() as u32,
        })
    } else {
        None
    };

    // Docker
    let docker = read_docker().await.unwrap_or_default();

    // CPU temperature via sysinfo components
    let cpu_temp = {
        let components = sysinfo::Components::new_with_refreshed_list();
        components
            .iter()
            .filter(|c| {
                let label = c.label().to_lowercase();
                label.contains("cpu") || label.contains("core") || label.contains("package")
            })
            .map(|c| c.temperature() as f64)
            .reduce(f64::max)
    };

    debug!(cpu_pct, mem_pct, disk_pct = primary_disk_pct, "Collected metrics");

    Ok(SystemMetrics {
        hostname,
        platform: "windows".into(),
        arch: std::env::consts::ARCH.into(),
        agent_version: VERSION.into(),
        cpu_pct,
        mem_total_mb: mem_total_mb as u64,
        mem_used_mb: mem_used_mb as u64,
        mem_pct,
        swap_total_mb: swap_total_mb as u64,
        swap_used_mb: swap_used_mb as u64,
        swap_pct,
        disk_pct: primary_disk_pct,
        disks,
        load_1: 0.0,
        load_5: 0.0,
        load_15: 0.0,
        uptime_s,
        rx_bytes: total_rx,
        tx_bytes: total_tx,
        network_interfaces: net_interfaces,
        cpu_temp,
        processes: procs,
        os_info,
        cpu_info,
        docker_containers: docker,
        extra: HashMap::new(),
    })
}

async fn read_processes() -> anyhow::Result<Vec<ProcessInfo>> {
    let ps_script = r#"
        Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 |
        ForEach-Object {
            "$($_.Id)|$($_.ProcessName)|$([math]::Round($_.CPU,1))|$([math]::Round($_.WorkingSet64/1MB,1))"
        }
    "#;

    let output = Command::new("powershell")
        .args(["-NoProfile", "-NoLogo", "-Command", ps_script])
        .output()
        .await?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let procs = stdout
        .lines()
        .filter_map(|line| {
            let parts: Vec<&str> = line.splitn(4, '|').collect();
            if parts.len() == 4 {
                Some(ProcessInfo {
                    pid: parts[0].parse().unwrap_or(0),
                    name: parts[1].to_string(),
                    cpu_pct: parts[2].parse().unwrap_or(0.0),
                    mem_mb: parts[3].parse().unwrap_or(0.0),
                })
            } else {
                None
            }
        })
        .collect();

    Ok(procs)
}

async fn read_docker() -> anyhow::Result<Vec<DockerContainer>> {
    let output = Command::new("docker")
        .args(["ps", "--format", "{{.Names}}|{{.Image}}|{{.Status}}"])
        .output()
        .await;

    let output = match output {
        Ok(o) if o.status.success() => o,
        _ => return Ok(Vec::new()),
    };

    let stdout = String::from_utf8_lossy(&output.stdout);
    let containers = stdout
        .lines()
        .filter_map(|line| {
            let parts: Vec<&str> = line.splitn(3, '|').collect();
            if parts.len() == 3 {
                Some(DockerContainer {
                    name: parts[0].into(),
                    image: parts[1].into(),
                    status: parts[2].into(),
                })
            } else {
                None
            }
        })
        .collect();

    Ok(containers)
}
