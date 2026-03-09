#!/usr/bin/env python3
"""
Nodeglow Agent — lightweight host monitoring agent.

Collects system metrics (CPU, memory, disk, network, load, processes)
and reports them to your Nodeglow instance via HTTP API.

Zero dependencies — uses only Python stdlib.
Runs on Linux, macOS, and Windows.

Usage:
    python nodeglow-agent.py --server https://nodeglow.local:8000 --token YOUR_TOKEN

    # Or set environment variables:
    NODEGLOW_SERVER=https://nodeglow.local:8000
    NODEGLOW_TOKEN=YOUR_TOKEN
    python nodeglow-agent.py
"""
import argparse
import json
import os
import platform
import socket
import sys
import time
import urllib.error
import urllib.request

__version__ = "1.0.0"

USER_AGENT = f"NodeglowAgent/{__version__} ({platform.system()}; {platform.machine()})"


def _make_request(url, data=None, token=None, method=None):
    """Create a urllib Request with proper headers for Cloudflare compatibility."""
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
        if isinstance(data, dict):
            data = json.dumps(data).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, data=data, headers=headers, method=method)


# ── Metric collectors ────────────────────────────────────────────────────────


def get_cpu_percent():
    """Get CPU usage percentage (Linux/macOS via /proc or top)."""
    try:
        # Linux: read /proc/stat twice
        if os.path.exists("/proc/stat"):
            def read_cpu():
                with open("/proc/stat") as f:
                    parts = f.readline().split()
                vals = [int(x) for x in parts[1:]]
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
                total = sum(vals)
                return idle, total

            idle1, total1 = read_cpu()
            time.sleep(0.5)
            idle2, total2 = read_cpu()
            idle_d = idle2 - idle1
            total_d = total2 - total1
            if total_d == 0:
                return 0.0
            return round((1.0 - idle_d / total_d) * 100, 1)
    except Exception:
        pass

    try:
        # macOS/BSD fallback
        import subprocess
        out = subprocess.check_output(
            ["top", "-l", "1", "-n", "0"], stderr=subprocess.DEVNULL, text=True
        )
        for line in out.splitlines():
            if "CPU usage" in line:
                # "CPU usage: 5.26% user, 3.94% sys, 90.79% idle"
                parts = line.split(",")
                for p in parts:
                    if "idle" in p:
                        idle = float(p.strip().split("%")[0])
                        return round(100.0 - idle, 1)
    except Exception:
        pass

    try:
        # Windows fallback
        import subprocess
        out = subprocess.check_output(
            ["wmic", "cpu", "get", "loadpercentage"],
            stderr=subprocess.DEVNULL, text=True
        )
        for line in out.strip().splitlines()[1:]:
            line = line.strip()
            if line:
                return float(line)
    except Exception:
        pass

    return None


def get_memory():
    """Get memory usage. Returns dict with total_mb, used_mb, pct."""
    try:
        # Linux
        if os.path.exists("/proc/meminfo"):
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        info[parts[0].rstrip(":")] = int(parts[1])
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", info.get("MemFree", 0))
            used = total - avail
            total_mb = round(total / 1024, 1)
            used_mb = round(used / 1024, 1)
            pct = round(used / total * 100, 1) if total > 0 else 0
            return {"total_mb": total_mb, "used_mb": used_mb, "pct": pct}
    except Exception:
        pass

    try:
        # macOS
        import subprocess
        out = subprocess.check_output(["sysctl", "-n", "hw.memsize"],
                                       stderr=subprocess.DEVNULL, text=True)
        total = int(out.strip())
        out2 = subprocess.check_output(
            ["vm_stat"], stderr=subprocess.DEVNULL, text=True
        )
        pages = {}
        for line in out2.splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                val = val.strip().rstrip(".")
                try:
                    pages[key.strip()] = int(val)
                except ValueError:
                    pass
        page_size = 4096  # default
        free = pages.get("Pages free", 0) * page_size
        total_mb = round(total / 1048576, 1)
        used_mb = round((total - free) / 1048576, 1)
        pct = round((total - free) / total * 100, 1)
        return {"total_mb": total_mb, "used_mb": used_mb, "pct": pct}
    except Exception:
        pass

    return None


def get_disks():
    """Get disk usage for mounted filesystems."""
    disks = []
    try:
        if platform.system() == "Windows":
            import subprocess
            out = subprocess.check_output(
                ["wmic", "logicaldisk", "get",
                 "DeviceID,Size,FreeSpace,FileSystem"],
                stderr=subprocess.DEVNULL, text=True
            )
            for line in out.strip().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        device = parts[0]
                        fs = parts[1] if len(parts) >= 4 else ""
                        free = int(parts[-2]) if len(parts) >= 4 else int(parts[-1])
                        size = int(parts[-1]) if len(parts) >= 4 else 0
                        if size > 0:
                            used = size - free
                            disks.append({
                                "mount": device,
                                "fs": fs,
                                "total_gb": round(size / 1073741824, 1),
                                "used_gb": round(used / 1073741824, 1),
                                "pct": round(used / size * 100, 1),
                            })
                    except (ValueError, IndexError):
                        pass
        else:
            # Linux/macOS: use os.statvfs
            mounts = set()
            # Read /proc/mounts or /etc/mtab for mount points
            mtab = "/proc/mounts" if os.path.exists("/proc/mounts") else "/etc/mtab"
            if os.path.exists(mtab):
                with open(mtab) as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 3:
                            device, mount, fs = parts[0], parts[1], parts[2]
                            # Skip virtual filesystems
                            if fs in ("proc", "sysfs", "devpts", "tmpfs", "cgroup",
                                      "cgroup2", "overlay", "squashfs", "devtmpfs",
                                      "securityfs", "pstore", "bpf", "tracefs",
                                      "debugfs", "hugetlbfs", "mqueue", "fusectl",
                                      "configfs", "autofs", "efivarfs", "ramfs"):
                                continue
                            if mount.startswith("/snap/") or mount.startswith("/sys/"):
                                continue
                            mounts.add(mount)
            else:
                mounts = {"/"}

            for mount in sorted(mounts):
                try:
                    st = os.statvfs(mount)
                    total = st.f_blocks * st.f_frsize
                    free = st.f_bavail * st.f_frsize
                    if total == 0:
                        continue
                    used = total - free
                    disks.append({
                        "mount": mount,
                        "total_gb": round(total / 1073741824, 1),
                        "used_gb": round(used / 1073741824, 1),
                        "pct": round(used / total * 100, 1),
                    })
                except OSError:
                    pass
    except Exception:
        pass

    return disks


def get_load():
    """Get load averages (Linux/macOS)."""
    try:
        load1, load5, load15 = os.getloadavg()
        return {
            "load_1": round(load1, 2),
            "load_5": round(load5, 2),
            "load_15": round(load15, 2),
        }
    except (OSError, AttributeError):
        return None


def get_uptime():
    """Get system uptime in seconds."""
    try:
        if os.path.exists("/proc/uptime"):
            with open("/proc/uptime") as f:
                return int(float(f.read().split()[0]))
    except Exception:
        pass
    try:
        import subprocess
        if platform.system() == "Darwin":
            out = subprocess.check_output(
                ["sysctl", "-n", "kern.boottime"],
                stderr=subprocess.DEVNULL, text=True
            )
            # "{ sec = 1709000000, usec = 0 } ..."
            import re
            m = re.search(r"sec\s*=\s*(\d+)", out)
            if m:
                return int(time.time()) - int(m.group(1))
    except Exception:
        pass
    return None


def get_network():
    """Get network I/O counters (Linux)."""
    try:
        if os.path.exists("/proc/net/dev"):
            with open("/proc/net/dev") as f:
                lines = f.readlines()[2:]  # skip headers
            rx_total = tx_total = 0
            for line in lines:
                parts = line.split()
                iface = parts[0].rstrip(":")
                if iface == "lo":
                    continue
                rx_total += int(parts[1])
                tx_total += int(parts[9])
            return {
                "rx_bytes": rx_total,
                "tx_bytes": tx_total,
                "rx_mb": round(rx_total / 1048576, 1),
                "tx_mb": round(tx_total / 1048576, 1),
            }
    except Exception:
        pass
    return None


def get_top_processes(n=10):
    """Get top N processes by CPU usage (Linux)."""
    procs = []
    try:
        if os.path.exists("/proc"):
            import subprocess
            out = subprocess.check_output(
                ["ps", "aux", "--sort=-pcpu"],
                stderr=subprocess.DEVNULL, text=True
            )
            for line in out.strip().splitlines()[1:n + 1]:
                parts = line.split(None, 10)
                if len(parts) >= 11:
                    procs.append({
                        "user": parts[0],
                        "pid": int(parts[1]),
                        "cpu": float(parts[2]),
                        "mem": float(parts[3]),
                        "cmd": parts[10][:80],
                    })
    except Exception:
        pass
    return procs


def collect_all():
    """Collect all metrics and return as a dict."""
    data = {
        "hostname": socket.gethostname(),
        "platform": platform.system(),
        "platform_release": platform.release(),
        "arch": platform.machine(),
        "agent_version": __version__,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    cpu = get_cpu_percent()
    if cpu is not None:
        data["cpu_pct"] = cpu

    mem = get_memory()
    if mem:
        data["memory"] = mem

    disks = get_disks()
    if disks:
        data["disks"] = disks

    load = get_load()
    if load:
        data["load"] = load

    uptime = get_uptime()
    if uptime is not None:
        data["uptime_s"] = uptime

    network = get_network()
    if network:
        data["network"] = network

    procs = get_top_processes(8)
    if procs:
        data["processes"] = procs

    return data


# ── Reporter ─────────────────────────────────────────────────────────────────


def send_metrics(server: str, token: str, data: dict) -> bool:
    """POST metrics to Nodeglow server. Returns True on success."""
    url = f"{server.rstrip('/')}/api/agent/report"
    req = _make_request(url, data=data, token=token, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        print(f"[nodeglow-agent] HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[nodeglow-agent] Error: {e}", file=sys.stderr)
        return False


# ── Main loop ────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Nodeglow Agent — system monitoring agent"
    )
    parser.add_argument(
        "--server", "-s",
        default=os.environ.get("NODEGLOW_SERVER", ""),
        help="Nodeglow server URL (or set NODEGLOW_SERVER env var)",
    )
    parser.add_argument(
        "--token", "-t",
        default=os.environ.get("NODEGLOW_TOKEN", ""),
        help="Agent token (or set NODEGLOW_TOKEN env var)",
    )
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=int(os.environ.get("NODEGLOW_INTERVAL", "30")),
        help="Reporting interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Collect and report once, then exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect metrics and print to stdout (don't send)",
    )
    args = parser.parse_args()

    if args.dry_run:
        data = collect_all()
        print(json.dumps(data, indent=2))
        return

    if not args.server:
        print("Error: --server or NODEGLOW_SERVER required", file=sys.stderr)
        sys.exit(1)
    if not args.token:
        print("Error: --token or NODEGLOW_TOKEN required", file=sys.stderr)
        sys.exit(1)

    print(f"[nodeglow-agent] v{__version__} reporting to {args.server} every {args.interval}s")
    print(f"[nodeglow-agent] hostname={socket.gethostname()}, platform={platform.system()}")

    while True:
        try:
            data = collect_all()
            ok = send_metrics(args.server, args.token, data)
            if ok:
                print(f"[nodeglow-agent] reported: cpu={data.get('cpu_pct', '?')}% "
                      f"mem={data.get('memory', {}).get('pct', '?')}%")
            else:
                print("[nodeglow-agent] report failed", file=sys.stderr)
        except Exception as e:
            print(f"[nodeglow-agent] error: {e}", file=sys.stderr)

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
