use crate::collector::*;
use std::collections::HashMap;
use tokio::process::Command;
use tracing::debug;

const VERSION: &str = env!("CARGO_PKG_VERSION");

pub async fn collect_metrics() -> anyhow::Result<SystemMetrics> {
    let hostname = hostname::get()
        .map(|h| h.to_string_lossy().to_string())
        .unwrap_or_else(|_| "unknown".into());

    let (cpu_pct, mem, swap, disks, load, uptime, network, cpu_temp, processes, os_info, cpu_info, docker) = tokio::join!(
        read_cpu_pct(),
        read_memory(),
        read_swap(),
        read_disks(),
        read_load(),
        read_uptime(),
        read_network(),
        read_cpu_temp(),
        read_processes(),
        read_os_info(),
        read_cpu_info(),
        read_docker(),
    );

    let primary_disk_pct = disks
        .as_ref()
        .ok()
        .and_then(|d| d.iter().find(|d| d.mount == "/"))
        .map(|d| d.pct)
        .unwrap_or(0.0);

    let (total_rx, total_tx) = network
        .as_ref()
        .ok()
        .map(|ifaces| {
            ifaces
                .iter()
                .filter(|i| i.name != "lo")
                .fold((0u64, 0u64), |(rx, tx), i| (rx + i.rx_bytes, tx + i.tx_bytes))
        })
        .unwrap_or((0, 0));

    let (load_1, load_5, load_15) = load.unwrap_or((0.0, 0.0, 0.0));
    let (mem_total, mem_used, mem_pct) = mem.unwrap_or((0, 0, 0.0));
    let (swap_total, swap_used, swap_pct) = swap.unwrap_or((0, 0, 0.0));

    Ok(SystemMetrics {
        hostname,
        platform: "linux".into(),
        arch: std::env::consts::ARCH.into(),
        agent_version: VERSION.into(),
        cpu_pct: cpu_pct.unwrap_or(0.0),
        mem_total_mb: mem_total,
        mem_used_mb: mem_used,
        mem_pct,
        swap_total_mb: swap_total,
        swap_used_mb: swap_used,
        swap_pct,
        disk_pct: primary_disk_pct,
        disks: disks.unwrap_or_default(),
        load_1,
        load_5,
        load_15,
        uptime_s: uptime.unwrap_or(0),
        rx_bytes: total_rx,
        tx_bytes: total_tx,
        network_interfaces: network.unwrap_or_default(),
        cpu_temp: cpu_temp.ok().flatten(),
        processes: processes.unwrap_or_default(),
        os_info: os_info.ok(),
        cpu_info: cpu_info.ok(),
        docker_containers: docker.unwrap_or_default(),
        extra: HashMap::new(),
    })
}

// ── CPU ──────────────────────────────────────────────────────────────────────

async fn read_cpu_pct() -> anyhow::Result<f64> {
    let read_stat = || -> anyhow::Result<(u64, u64)> {
        let data = std::fs::read_to_string("/proc/stat")?;
        let line = data.lines().next().ok_or(anyhow::anyhow!("empty /proc/stat"))?;
        let vals: Vec<u64> = line
            .split_whitespace()
            .skip(1) // skip "cpu"
            .filter_map(|v| v.parse().ok())
            .collect();
        if vals.len() < 4 {
            anyhow::bail!("unexpected /proc/stat format");
        }
        let idle = vals[3] + vals.get(4).copied().unwrap_or(0); // idle + iowait
        let total: u64 = vals.iter().sum();
        Ok((idle, total))
    };

    let (idle1, total1) = read_stat()?;
    tokio::time::sleep(std::time::Duration::from_millis(500)).await;
    let (idle2, total2) = read_stat()?;

    let diff_idle = idle2.saturating_sub(idle1) as f64;
    let diff_total = total2.saturating_sub(total1) as f64;

    if diff_total == 0.0 {
        return Ok(0.0);
    }

    Ok(((1.0 - diff_idle / diff_total) * 100.0 * 10.0).round() / 10.0)
}

// ── Memory ───────────────────────────────────────────────────────────────────

fn read_meminfo_field(data: &str, field: &str) -> Option<u64> {
    data.lines()
        .find(|l| l.starts_with(field))
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|v| v.parse::<u64>().ok())
}

async fn read_memory() -> anyhow::Result<(u64, u64, f64)> {
    let data = tokio::fs::read_to_string("/proc/meminfo").await?;
    let total_kb = read_meminfo_field(&data, "MemTotal:").unwrap_or(0);
    let free_kb = read_meminfo_field(&data, "MemFree:").unwrap_or(0);
    let buffers_kb = read_meminfo_field(&data, "Buffers:").unwrap_or(0);
    let cached_kb = read_meminfo_field(&data, "Cached:").unwrap_or(0);
    let sreclaimable_kb = read_meminfo_field(&data, "SReclaimable:").unwrap_or(0);

    let total_mb = total_kb / 1024;
    let available_kb = free_kb + buffers_kb + cached_kb + sreclaimable_kb;
    let used_mb = (total_kb.saturating_sub(available_kb)) / 1024;
    let pct = if total_mb > 0 {
        (used_mb as f64 / total_mb as f64 * 100.0 * 10.0).round() / 10.0
    } else {
        0.0
    };

    Ok((total_mb, used_mb, pct))
}

async fn read_swap() -> anyhow::Result<(u64, u64, f64)> {
    let data = tokio::fs::read_to_string("/proc/meminfo").await?;
    let total_kb = read_meminfo_field(&data, "SwapTotal:").unwrap_or(0);
    let free_kb = read_meminfo_field(&data, "SwapFree:").unwrap_or(0);

    let total_mb = total_kb / 1024;
    let used_mb = (total_kb.saturating_sub(free_kb)) / 1024;
    let pct = if total_mb > 0 {
        (used_mb as f64 / total_mb as f64 * 100.0 * 10.0).round() / 10.0
    } else {
        0.0
    };

    Ok((total_mb, used_mb, pct))
}

// ── Disks ────────────────────────────────────────────────────────────────────

async fn read_disks() -> anyhow::Result<Vec<DiskInfo>> {
    let mounts = tokio::fs::read_to_string("/proc/mounts").await?;
    let mut disks = Vec::new();
    let seen_devices = std::collections::HashSet::<String>::new();

    for line in mounts.lines() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() < 3 {
            continue;
        }
        let device = parts[0];
        let mount = parts[1];
        let fs = parts[2];

        // Only real filesystems
        if !device.starts_with('/') || fs == "squashfs" || fs == "tmpfs" {
            continue;
        }
        if seen_devices.contains(device) {
            continue;
        }

        match nix_statvfs(mount) {
            Some((total, used, pct)) => {
                disks.push(DiskInfo {
                    mount: mount.into(),
                    device: device.into(),
                    fs_type: fs.into(),
                    total_gb: (total as f64 / 1073741824.0 * 10.0).round() / 10.0,
                    used_gb: (used as f64 / 1073741824.0 * 10.0).round() / 10.0,
                    pct,
                });
            }
            None => continue,
        }
    }

    Ok(disks)
}

fn nix_statvfs(path: &str) -> Option<(u64, u64, f64)> {
    use std::ffi::CString;
    let c_path = CString::new(path).ok()?;
    let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
    let ret = unsafe { libc::statvfs(c_path.as_ptr(), &mut stat) };
    if ret != 0 {
        return None;
    }
    let block_size = stat.f_frsize as u64;
    let total = stat.f_blocks as u64 * block_size;
    let free = stat.f_bfree as u64 * block_size;
    if total == 0 {
        return None;
    }
    let used = total - free;
    let pct = (used as f64 / total as f64 * 100.0 * 10.0).round() / 10.0;
    Some((total, used, pct))
}

// ── Load ─────────────────────────────────────────────────────────────────────

async fn read_load() -> anyhow::Result<(f64, f64, f64)> {
    let data = tokio::fs::read_to_string("/proc/loadavg").await?;
    let parts: Vec<f64> = data
        .split_whitespace()
        .take(3)
        .filter_map(|v| v.parse().ok())
        .collect();
    Ok((*parts.first().unwrap_or(&0.0), *parts.get(1).unwrap_or(&0.0), *parts.get(2).unwrap_or(&0.0)))
}

// ── Uptime ───────────────────────────────────────────────────────────────────

async fn read_uptime() -> anyhow::Result<u64> {
    let data = tokio::fs::read_to_string("/proc/uptime").await?;
    let secs: f64 = data
        .split_whitespace()
        .next()
        .and_then(|v| v.parse().ok())
        .unwrap_or(0.0);
    Ok(secs as u64)
}

// ── Network ──────────────────────────────────────────────────────────────────

async fn read_network() -> anyhow::Result<Vec<NetInterface>> {
    let data = tokio::fs::read_to_string("/proc/net/dev").await?;
    let mut interfaces = Vec::new();

    for line in data.lines().skip(2) {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() < 10 {
            continue;
        }
        let name = parts[0].trim_end_matches(':');
        let rx: u64 = parts[1].parse().unwrap_or(0);
        let tx: u64 = parts[9].parse().unwrap_or(0);

        interfaces.push(NetInterface {
            name: name.into(),
            rx_bytes: rx,
            tx_bytes: tx,
        });
    }

    Ok(interfaces)
}

// ── CPU Temperature ──────────────────────────────────────────────────────────

async fn read_cpu_temp() -> anyhow::Result<Option<f64>> {
    let zones = std::path::Path::new("/sys/class/thermal");
    if !zones.exists() {
        return Ok(None);
    }

    let mut temps = Vec::new();
    if let Ok(entries) = std::fs::read_dir(zones) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if !name.starts_with("thermal_zone") {
                continue;
            }
            let temp_path = entry.path().join("temp");
            if let Ok(data) = std::fs::read_to_string(&temp_path) {
                if let Ok(millideg) = data.trim().parse::<f64>() {
                    temps.push(millideg / 1000.0);
                }
            }
        }
    }

    Ok(temps.into_iter().reduce(f64::max))
}

// ── Processes ────────────────────────────────────────────────────────────────

async fn read_processes() -> anyhow::Result<Vec<ProcessInfo>> {
    let output = Command::new("ps")
        .args(["aux", "--sort=-pcpu"])
        .output()
        .await?;
    let stdout = String::from_utf8_lossy(&output.stdout);

    let mut procs = Vec::new();
    for line in stdout.lines().skip(1).take(10) {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() < 11 {
            continue;
        }
        let pid: u32 = parts[1].parse().unwrap_or(0);
        let cpu: f64 = parts[2].parse().unwrap_or(0.0);
        let mem_pct: f64 = parts[3].parse().unwrap_or(0.0);
        let name = parts[10..].join(" ");
        // Approximate mem_mb from percentage
        let total_mem = read_meminfo_total_mb().unwrap_or(0) as f64;
        let mem_mb = (mem_pct / 100.0 * total_mem * 10.0).round() / 10.0;

        procs.push(ProcessInfo {
            pid,
            name: name.chars().take(80).collect(),
            cpu_pct: cpu,
            mem_mb,
        });
    }

    Ok(procs)
}

fn read_meminfo_total_mb() -> Option<u64> {
    let data = std::fs::read_to_string("/proc/meminfo").ok()?;
    read_meminfo_field(&data, "MemTotal:").map(|kb| kb / 1024)
}

// ── OS Info ──────────────────────────────────────────────────────────────────

async fn read_os_info() -> anyhow::Result<OsInfo> {
    let data = tokio::fs::read_to_string("/etc/os-release").await.unwrap_or_default();
    let mut name = String::new();
    let mut version = String::new();

    for line in data.lines() {
        if let Some(val) = line.strip_prefix("PRETTY_NAME=") {
            name = val.trim_matches('"').into();
        }
        if let Some(val) = line.strip_prefix("VERSION_ID=") {
            version = val.trim_matches('"').into();
        }
    }

    let uname = Command::new("uname").arg("-r").output().await;
    let build = uname
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default();

    Ok(OsInfo {
        name,
        version,
        build,
        arch: std::env::consts::ARCH.into(),
    })
}

// ── CPU Info ─────────────────────────────────────────────────────────────────

async fn read_cpu_info() -> anyhow::Result<CpuInfo> {
    let data = tokio::fs::read_to_string("/proc/cpuinfo").await?;

    let model = data
        .lines()
        .find(|l| l.starts_with("model name"))
        .and_then(|l| l.split(':').nth(1))
        .map(|v| v.trim().to_string())
        .unwrap_or_default();

    let threads = data
        .lines()
        .filter(|l| l.starts_with("processor"))
        .count() as u32;

    // Core count from unique "core id" values
    let cores: u32 = {
        let ids: std::collections::HashSet<_> = data
            .lines()
            .filter(|l| l.starts_with("core id"))
            .filter_map(|l| l.split(':').nth(1).map(|v| v.trim().to_string()))
            .collect();
        if ids.is_empty() { threads } else { ids.len() as u32 }
    };

    Ok(CpuInfo { model, cores, threads })
}

// ── Docker ───────────────────────────────────────────────────────────────────

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
