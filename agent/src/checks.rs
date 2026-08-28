//! Probe mode — run host checks on behalf of the backend.
//!
//! A probe is an ordinary agent that the backend has assigned hosts to. There is
//! no second connection and no second credential: assignments arrive on the
//! heartbeat response (`checks`), are executed between heartbeats, and the
//! results are handed back on the next heartbeat (`check_results`).
//!
//! An agent that is not a probe never receives assignments, so nothing in this
//! module runs for it — no client is built, no task is spawned, no timer is armed.
//!
//! Wire format, both directions:
//!
//! ```json
//! // response
//! "checks": [{"host_id": 42, "hostname": "nas.kunde.ch", "ip": "10.0.0.5",
//!             "check_type": "icmp", "port": null, "timeout_seconds": 5.0}]
//! // request
//! "check_results": [{"host_id": 42, "success": true, "latency_ms": 12.4,
//!                    "detail": {"icmp": true}, "ts": "2026-08-28T09:01:13Z"}]
//! ```

use std::collections::{BTreeMap, VecDeque};
use std::process::Stdio;
use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail};
use chrono::{SecondsFormat, Utc};
use serde::{Deserialize, Serialize};
use tokio::process::Command;
use tokio::sync::Semaphore;
use tracing::{debug, info, warn};

use crate::config::Config;

/// Maximum number of assigned hosts checked at the same time. ICMP forks a
/// process per check, so this bounds process and socket pressure on a probe
/// that has been given a large site. Everything above the limit waits its
/// turn — it is never dropped.
const MAX_CONCURRENT_CHECKS: usize = 64;

/// Upper bound on undelivered check results held in memory.
///
/// Results are retried on the next heartbeat, but a probe that cannot reach the
/// backend for hours must not grow without bound. Once the buffer is full the
/// OLDEST results are dropped: a stale result is worth less than a current one,
/// and the gap is meant to be visible as a gap — the backend evaluates
/// freshness per probe and renders hosts it has not heard about as `unknown`.
/// Hoarding old results (or inventing new ones) would paper over exactly the
/// failure the design calls out.
const MAX_PENDING_RESULTS: usize = 1000;

/// Extra wall-clock allowance on top of the check timeout before the `ping`
/// process is killed outright. `ping -W`/`-w` bounds the wait for a reply but
/// not name resolution or a wedged binary.
const PING_PROCESS_GRACE: Duration = Duration::from_secs(3);

/// Used when the backend sends no (or an unusable) timeout.
const DEFAULT_TIMEOUT_SECONDS: f64 = 5.0;
const MIN_TIMEOUT_SECONDS: f64 = 0.5;
const MAX_TIMEOUT_SECONDS: f64 = 60.0;

/// Port a TCP check falls back to when neither the assignment nor the check
/// type names one. Mirrors the backend's `host.port or 80`.
const DEFAULT_TCP_PORT: u16 = 80;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

// ── Wire types ───────────────────────────────────────────────────────────────

/// One host this probe is responsible for, as sent on the heartbeat response.
#[derive(Debug, Clone, Deserialize)]
pub struct CheckAssignment {
    pub host_id: i64,
    #[serde(default)]
    pub hostname: String,
    /// May be null — fall back to `hostname`.
    #[serde(default)]
    pub ip: Option<String>,
    /// `icmp`, `tcp`, `http` or `https`. A comma-separated list is also
    /// accepted, because that is how `ping_hosts.check_type` is stored in the
    /// backend and it costs nothing to tolerate it here.
    #[serde(default)]
    pub check_type: String,
    /// Set for `tcp`, optional for `http`/`https`.
    #[serde(default)]
    pub port: Option<u16>,
    /// Per-check timeout in seconds. `timeout` is accepted as an alias because
    /// the design document names the field that way.
    #[serde(default, alias = "timeout")]
    pub timeout_seconds: Option<f64>,
}

/// One completed check, as sent on the next heartbeat request.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CheckResult {
    pub host_id: i64,
    pub success: bool,
    pub latency_ms: Option<f64>,
    /// Per-check-type outcome, mirroring what the backend's own `check_host`
    /// produces: `{"icmp": true}`, `{"tcp:8080": false}`, `{"https": true}`.
    ///
    /// Values are booleans only. The reason a check failed is logged by the
    /// agent rather than put in here, because the backend treats every value in
    /// this map as a boolean and a string would read as "true" to it — which is
    /// the silent-success failure this module is written to avoid.
    pub detail: BTreeMap<String, bool>,
    /// RFC3339 UTC, e.g. `2026-08-28T09:01:13Z`.
    pub ts: String,
}

// ── Bounded result buffer ────────────────────────────────────────────────────

/// Check results waiting for a heartbeat that succeeds.
///
/// Bounded on purpose — see `MAX_PENDING_RESULTS`.
#[derive(Debug, Default)]
pub struct PendingResults {
    buf: VecDeque<CheckResult>,
    dropped: u64,
}

impl PendingResults {
    pub fn new() -> Self {
        Self {
            buf: VecDeque::new(),
            dropped: 0,
        }
    }

    /// Append a round of results, discarding the oldest entries if the buffer
    /// would exceed its cap. Returns how many entries this call discarded.
    pub fn push_round(&mut self, results: Vec<CheckResult>) -> u64 {
        let mut dropped_now = 0u64;
        for r in results {
            if self.buf.len() >= MAX_PENDING_RESULTS {
                self.buf.pop_front();
                dropped_now += 1;
            }
            self.buf.push_back(r);
        }
        if dropped_now > 0 {
            self.dropped += dropped_now;
            warn!(
                dropped = dropped_now,
                dropped_total = self.dropped,
                buffered = self.buf.len(),
                "Undelivered check results exceeded the buffer cap; oldest results discarded"
            );
        }
        dropped_now
    }

    /// The buffered results as a contiguous slice, ready to serialise.
    pub fn as_slice(&mut self) -> &[CheckResult] {
        self.buf.make_contiguous()
    }

    /// Called once the backend has accepted the results.
    pub fn clear(&mut self) {
        self.buf.clear();
    }

    pub fn len(&self) -> usize {
        self.buf.len()
    }

    pub fn is_empty(&self) -> bool {
        self.buf.is_empty()
    }

    pub fn dropped_total(&self) -> u64 {
        self.dropped
    }
}

// ── HTTP client for probe checks ─────────────────────────────────────────────

/// HTTP client used for `http`/`https` checks.
///
/// Built lazily, only once this agent has actually been given assignments, and
/// with the same TLS discipline as the client that talks to the backend:
/// certificate validation stays on unless `allow_insecure_tls` is set.
#[derive(Clone)]
pub struct ProbeClient {
    http: reqwest::Client,
}

impl ProbeClient {
    pub fn new(cfg: &Config) -> anyhow::Result<Self> {
        let mut builder = reqwest::Client::builder()
            // A 2xx/3xx is success, and redirects are followed the way the
            // backend's httpx client follows them.
            .redirect(reqwest::redirect::Policy::limited(10))
            .user_agent(concat!("nodeglow-agent/", env!("CARGO_PKG_VERSION")));

        if cfg.allow_insecure_tls {
            warn!(
                "Probe HTTP checks run with TLS certificate validation DISABLED \
                 (allow_insecure_tls=true) — insecure, testing only"
            );
            builder = builder.danger_accept_invalid_certs(true);
        }

        // No client-wide timeout: each check carries its own.
        let http = builder
            .build()
            .map_err(|e| anyhow!("failed to build probe HTTP client: {e}"))?;
        Ok(Self { http })
    }
}

// ── Runner ───────────────────────────────────────────────────────────────────

/// Run every assigned check concurrently and collect the results.
///
/// Always returns one result per assignment: a check that could not be
/// performed comes back as a failed check with the reason logged, never as a
/// success and never as a missing entry.
pub async fn run_checks(client: &ProbeClient, assignments: &[CheckAssignment]) -> Vec<CheckResult> {
    if assignments.is_empty() {
        return Vec::new();
    }

    let started = Instant::now();
    let sem = Arc::new(Semaphore::new(MAX_CONCURRENT_CHECKS));
    let mut tasks = Vec::with_capacity(assignments.len());

    for assignment in assignments {
        let assignment = assignment.clone();
        let client = client.clone();
        let sem = Arc::clone(&sem);
        let host_id = assignment.host_id;
        // One task per host, so a host that is down cannot delay any other.
        let handle = tokio::spawn(async move {
            // The semaphore is never closed, so acquire() cannot fail; if it
            // somehow did, running the check unthrottled still beats skipping it.
            let _permit = sem.acquire_owned().await.ok();
            run_one(&client, &assignment).await
        });
        tasks.push((host_id, handle));
    }

    let mut results = Vec::with_capacity(tasks.len());
    for (host_id, handle) in tasks {
        match handle.await {
            Ok(result) => results.push(result),
            Err(e) => {
                // The task was cancelled or panicked. Report the host as
                // failed rather than omitting it, and keep going.
                warn!(host_id, "Check task did not complete: {e}");
                results.push(CheckResult {
                    host_id,
                    success: false,
                    latency_ms: None,
                    detail: BTreeMap::new(),
                    ts: now_rfc3339(),
                });
            }
        }
    }

    debug!(
        hosts = results.len(),
        elapsed_ms = started.elapsed().as_millis() as u64,
        "Probe check round finished"
    );
    results
}

/// Outcome of a single check type for one host.
#[derive(Debug, Clone, PartialEq)]
struct SubResult {
    label: String,
    is_icmp: bool,
    ok: bool,
    latency_ms: Option<f64>,
}

async fn run_one(client: &ProbeClient, assignment: &CheckAssignment) -> CheckResult {
    let timeout = check_timeout(assignment.timeout_seconds);
    let types = parse_check_types(&assignment.check_type);
    let mut subs = Vec::with_capacity(types.len());

    for ct in &types {
        let (ok, latency_ms) = match execute(client, assignment, ct, timeout).await {
            Ok(v) => v,
            Err(e) => {
                // Could not be performed — a failed check with a reason, never
                // a silent success and never an omitted result.
                warn!(
                    host_id = assignment.host_id,
                    hostname = %assignment.hostname,
                    check = %ct,
                    "Check could not be performed: {e}"
                );
                (false, None)
            }
        };
        subs.push(SubResult {
            label: detail_label(ct, assignment.port),
            is_icmp: ct == "icmp",
            ok,
            latency_ms,
        });
    }

    summarize(assignment.host_id, subs)
}

/// Dispatch one check type. `Err` means the check could not be performed at
/// all; `Ok((false, _))` means it was performed and the host did not answer.
async fn execute(
    client: &ProbeClient,
    assignment: &CheckAssignment,
    check_type: &str,
    timeout: Duration,
) -> anyhow::Result<(bool, Option<f64>)> {
    match check_type {
        "icmp" => {
            let target = network_target(assignment)?;
            run_icmp(&target, timeout).await
        }
        ct if ct == "tcp" || ct.starts_with("tcp:") => {
            let target = network_target(assignment)?;
            let port = tcp_port(ct, assignment.port);
            if ct == "tcp" && assignment.port.is_none() {
                warn!(
                    host_id = assignment.host_id,
                    "TCP check has no port; falling back to {DEFAULT_TCP_PORT}"
                );
            }
            run_tcp(&target, port, timeout).await
        }
        ct @ ("http" | "https") => {
            // HTTP uses the hostname, not the IP: certificates and virtual
            // hosts are keyed on the name. Same rule as the backend.
            let host = http_target(assignment)?;
            let url = build_http_url(ct, &host, assignment.port)?;
            run_http(client, &url, timeout).await
        }
        other => bail!("unsupported check type '{other}'"),
    }
}

// ── ICMP ─────────────────────────────────────────────────────────────────────

/// Which `ping` command line to build. The binaries disagree on both the flag
/// names and the unit of the timeout.
///
/// Every variant is handled by `ping_args` and exercised by the tests, but only
/// the one matching the build target is ever constructed at runtime — hence the
/// dead-code exemption.
#[allow(dead_code)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PingFlavor {
    /// iputils / busybox: `-c <count> -W <seconds>`.
    Linux,
    /// BSD / macOS: `-c <count> -t <seconds>` (`-W` there is milliseconds).
    MacOs,
    /// `ping.exe`: `-n <count> -w <milliseconds>`, and no `--` separator.
    Windows,
}

impl PingFlavor {
    pub fn current() -> Self {
        #[cfg(target_os = "windows")]
        {
            PingFlavor::Windows
        }
        #[cfg(target_os = "macos")]
        {
            PingFlavor::MacOs
        }
        #[cfg(not(any(target_os = "windows", target_os = "macos")))]
        {
            PingFlavor::Linux
        }
    }
}

/// Build the `ping` argument list.
///
/// Mirrors the backend's `ping -c 1 -W <timeout> <host>` and adds the `--`
/// separator the backend uses for its other subprocess calls, so a target that
/// begins with `-` can never be read as an option. `ping.exe` has no `--`, so
/// there the target is validated instead (see `validate_target`).
pub fn ping_args(flavor: PingFlavor, target: &str, timeout: Duration) -> Vec<String> {
    let secs = timeout.as_secs_f64().ceil().max(1.0) as u64;
    match flavor {
        PingFlavor::Linux => vec![
            "-c".into(),
            "1".into(),
            "-W".into(),
            secs.to_string(),
            "--".into(),
            target.into(),
        ],
        PingFlavor::MacOs => vec![
            "-c".into(),
            "1".into(),
            "-t".into(),
            secs.to_string(),
            "--".into(),
            target.into(),
        ],
        PingFlavor::Windows => vec![
            "-n".into(),
            "1".into(),
            "-w".into(),
            timeout.as_millis().max(1).to_string(),
            target.into(),
        ],
    }
}

async fn run_icmp(target: &str, timeout: Duration) -> anyhow::Result<(bool, Option<f64>)> {
    let flavor = PingFlavor::current();
    let args = ping_args(flavor, target, timeout);

    let mut cmd = Command::new("ping");
    cmd.args(&args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // If the deadline below fires, the future is dropped and the child dies
        // with it instead of lingering for the life of the agent.
        .kill_on_drop(true);
    #[cfg(target_os = "windows")]
    cmd.creation_flags(CREATE_NO_WINDOW);

    let deadline = timeout + PING_PROCESS_GRACE;
    let output = match tokio::time::timeout(deadline, async {
        let child = cmd.spawn()?;
        child.wait_with_output().await
    })
    .await
    {
        Ok(Ok(out)) => out,
        // Spawn or I/O failure: the check could not be performed (no `ping`
        // binary, no permission to fork, …).
        Ok(Err(e)) => return Err(anyhow!("could not run ping: {e}")),
        Err(_) => {
            // The check ran, the host did not answer in time. That is a failed
            // check, not an unperformable one.
            debug!(target, "ICMP check timed out after {deadline:?}");
            return Ok((false, None));
        }
    };

    let stdout = String::from_utf8_lossy(&output.stdout);
    let ok = icmp_succeeded(flavor, output.status.success(), &stdout);
    let latency = if ok { parse_ping_latency(&stdout) } else { None };
    Ok((ok, latency))
}

/// Decide whether a finished `ping` run means the host answered.
///
/// On Windows `ping.exe` exits 0 when it received *any* reply, including a
/// router's "Destination host unreachable" — trusting the exit code alone would
/// report a dead host as up. A genuine echo reply always carries `TTL=`, which
/// Windows does not localise, so it is required there.
pub fn icmp_succeeded(flavor: PingFlavor, exit_ok: bool, stdout: &str) -> bool {
    if !exit_ok {
        return false;
    }
    match flavor {
        PingFlavor::Windows => stdout.to_ascii_uppercase().contains("TTL="),
        _ => true,
    }
}

/// Extract the round-trip time in milliseconds from `ping` output.
///
/// Mirrors the backend's `time=` token scan, and additionally tolerates
/// Windows' `time<1ms` form and the localised labels Windows emits (German
/// `Zeit=`, French `temps=`), which the backend never sees because it only ever
/// runs on Linux.
pub fn parse_ping_latency(output: &str) -> Option<f64> {
    for token in output.split_whitespace() {
        let lower = token.to_ascii_lowercase();
        let value = lower
            .strip_prefix("time=")
            .or_else(|| lower.strip_prefix("time<"));
        if let Some(v) = value.and_then(parse_ms) {
            return Some(v);
        }
    }
    // Localised output: take the first token that carries a millisecond value
    // after '=' or '<'. With `-n 1`/`-c 1` the only such numbers are this one
    // reply's round-trip time (min/max/avg all equal it).
    for token in output.split_whitespace() {
        let lower = token.to_ascii_lowercase();
        if !lower.ends_with("ms") && !lower.ends_with("ms,") {
            continue;
        }
        if let Some(idx) = lower.rfind(['=', '<']) {
            if let Some(v) = parse_ms(&lower[idx + 1..]) {
                return Some(v);
            }
        }
    }
    None
}

fn parse_ms(raw: &str) -> Option<f64> {
    let s = raw.trim().trim_end_matches([',', ';', ')']);
    let s = s.strip_suffix("ms").unwrap_or(s).trim();
    let v: f64 = s.parse().ok()?;
    if v.is_finite() && v >= 0.0 {
        Some(v)
    } else {
        None
    }
}

// ── TCP ──────────────────────────────────────────────────────────────────────

async fn run_tcp(target: &str, port: u16, timeout: Duration) -> anyhow::Result<(bool, Option<f64>)> {
    let addr = format!("{target}:{port}");
    let started = Instant::now();
    match tokio::time::timeout(timeout, tokio::net::TcpStream::connect(&addr)).await {
        Ok(Ok(stream)) => {
            let latency = elapsed_ms(started);
            drop(stream);
            Ok((true, Some(latency)))
        }
        Ok(Err(e)) => {
            debug!(target, port, "TCP check failed: {e}");
            Ok((false, None))
        }
        Err(_) => {
            debug!(target, port, "TCP check timed out after {timeout:?}");
            Ok((false, None))
        }
    }
}

/// Port for a TCP check: `tcp:8080` wins, then the assignment's `port`, then 80.
pub fn tcp_port(check_type: &str, assigned: Option<u16>) -> u16 {
    if let Some(suffix) = check_type.strip_prefix("tcp:") {
        if let Ok(p) = suffix.trim().parse::<u16>() {
            if p > 0 {
                return p;
            }
        }
    }
    assigned.filter(|p| *p > 0).unwrap_or(DEFAULT_TCP_PORT)
}

// ── HTTP / HTTPS ─────────────────────────────────────────────────────────────

async fn run_http(
    client: &ProbeClient,
    url: &str,
    timeout: Duration,
) -> anyhow::Result<(bool, Option<f64>)> {
    let started = Instant::now();
    match client.http.get(url).timeout(timeout).send().await {
        Ok(resp) => {
            let latency = elapsed_ms(started);
            let status = resp.status();
            // Redirects are followed, so a 3xx only surfaces when the redirect
            // limit was reached; it still counts as reachable.
            let ok = status.is_success() || status.is_redirection();
            if !ok {
                debug!(
                    url,
                    status = status.as_u16(),
                    "HTTP check returned a non-2xx/3xx status"
                );
            }
            Ok((ok, Some(latency)))
        }
        Err(e) => {
            debug!(url, "HTTP check failed: {e}");
            Ok((false, None))
        }
    }
}

/// Build the URL for an `http`/`https` check. A hostname that is already a URL
/// is used as-is, mirroring the backend.
pub fn build_http_url(scheme: &str, host: &str, port: Option<u16>) -> anyhow::Result<String> {
    let host = host.trim();
    if host.is_empty() {
        bail!("no hostname for an {scheme} check");
    }
    if host.starts_with("http://") || host.starts_with("https://") {
        return Ok(host.to_string());
    }
    match port.filter(|p| *p > 0) {
        Some(p) => Ok(format!("{scheme}://{host}:{p}")),
        None => Ok(format!("{scheme}://{host}")),
    }
}

// ── Shared helpers ───────────────────────────────────────────────────────────

/// Target for ICMP/TCP: the IP if the backend sent one, otherwise the hostname.
pub fn network_target(assignment: &CheckAssignment) -> anyhow::Result<String> {
    let ip = assignment.ip.as_deref().unwrap_or("").trim();
    let candidate = if ip.is_empty() {
        assignment.hostname.trim()
    } else {
        ip
    };
    validate_target(candidate)?;
    Ok(candidate.to_string())
}

/// Target for HTTP/HTTPS: the hostname, falling back to the IP only when there
/// is no hostname at all.
pub fn http_target(assignment: &CheckAssignment) -> anyhow::Result<String> {
    let hostname = assignment.hostname.trim();
    let candidate = if hostname.is_empty() {
        assignment.ip.as_deref().unwrap_or("").trim()
    } else {
        hostname
    };
    validate_target(candidate)?;
    Ok(candidate.to_string())
}

/// Reject targets that cannot be used safely as a command-line argument.
/// `ping.exe` has no `--` separator, so a leading `-` or `/` would turn the
/// target into an option.
pub fn validate_target(target: &str) -> anyhow::Result<()> {
    if target.is_empty() {
        bail!("assignment has neither an ip nor a hostname");
    }
    if target.starts_with('-') || target.starts_with('/') {
        bail!("target '{target}' starts with an option-like character");
    }
    if target.chars().any(char::is_whitespace) {
        bail!("target '{target}' contains whitespace");
    }
    Ok(())
}

/// Clamp the backend-supplied timeout into a usable range.
pub fn check_timeout(seconds: Option<f64>) -> Duration {
    let s = match seconds {
        Some(v) if v.is_finite() && v > 0.0 => v.clamp(MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS),
        _ => DEFAULT_TIMEOUT_SECONDS,
    };
    Duration::from_secs_f64(s)
}

/// Split `check_type` into individual check types, defaulting to `icmp` the way
/// the backend's `check_host` does.
pub fn parse_check_types(raw: &str) -> Vec<String> {
    let types: Vec<String> = raw
        .split(',')
        .map(|t| t.trim().to_ascii_lowercase())
        .filter(|t| !t.is_empty())
        .collect();
    if types.is_empty() {
        vec!["icmp".to_string()]
    } else {
        types
    }
}

/// Key used for this check type in `detail`. Mirrors `check_host`: a plain
/// `tcp` with a known port is labelled `tcp:<port>`.
pub fn detail_label(check_type: &str, port: Option<u16>) -> String {
    match port {
        Some(p) if check_type == "tcp" => format!("tcp:{p}"),
        _ => check_type.to_string(),
    }
}

/// Fold the per-type outcomes into the result sent to the backend.
///
/// Mirrors `check_host`: ICMP alone decides `success` when it is configured,
/// otherwise any successful check counts; latency prefers ICMP and falls back
/// to the first check that measured one.
fn summarize(host_id: i64, subs: Vec<SubResult>) -> CheckResult {
    let has_icmp = subs.iter().any(|s| s.is_icmp);
    let success = if has_icmp {
        subs.iter().filter(|s| s.is_icmp).all(|s| s.ok)
    } else {
        subs.iter().any(|s| s.ok)
    };

    let latency_ms = subs
        .iter()
        .find(|s| s.is_icmp)
        .and_then(|s| s.latency_ms)
        .or_else(|| subs.iter().find_map(|s| s.latency_ms));

    let mut detail = BTreeMap::new();
    for s in &subs {
        detail.insert(s.label.clone(), s.ok);
    }

    CheckResult {
        host_id,
        success,
        latency_ms,
        detail,
        ts: now_rfc3339(),
    }
}

fn elapsed_ms(started: Instant) -> f64 {
    (started.elapsed().as_secs_f64() * 1000.0 * 100.0).round() / 100.0
}

fn now_rfc3339() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true)
}

/// Log an assignment change so an operator can see what a probe took on.
pub fn log_assignment_change(previous: usize, current: usize) {
    if previous != current {
        info!(
            previous_hosts = previous,
            assigned_hosts = current,
            "Probe assignment changed"
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn assignment(json: &str) -> CheckAssignment {
        serde_json::from_str(json).expect("assignment should deserialise")
    }

    // ── Wire format ──────────────────────────────────────────────────────────

    #[test]
    fn deserializes_the_documented_assignment_shape() {
        let a = assignment(
            r#"{"host_id": 42, "hostname": "nas.kunde.ch", "ip": "10.0.0.5",
                "check_type": "icmp", "port": null, "timeout_seconds": 5.0}"#,
        );
        assert_eq!(a.host_id, 42);
        assert_eq!(a.hostname, "nas.kunde.ch");
        assert_eq!(a.ip.as_deref(), Some("10.0.0.5"));
        assert_eq!(a.check_type, "icmp");
        assert_eq!(a.port, None);
        assert_eq!(a.timeout_seconds, Some(5.0));
    }

    #[test]
    fn tolerates_missing_optional_assignment_fields() {
        let a = assignment(r#"{"host_id": 7}"#);
        assert_eq!(a.host_id, 7);
        assert!(a.hostname.is_empty());
        assert_eq!(a.ip, None);
        assert_eq!(a.timeout_seconds, None);
        // An empty check_type falls back to icmp, like the backend does.
        assert_eq!(parse_check_types(&a.check_type), vec!["icmp".to_string()]);
    }

    #[test]
    fn accepts_timeout_as_an_alias_for_timeout_seconds() {
        let a = assignment(r#"{"host_id": 1, "timeout": 2.5}"#);
        assert_eq!(a.timeout_seconds, Some(2.5));
    }

    #[test]
    fn serializes_the_documented_result_shape() {
        let mut detail = BTreeMap::new();
        detail.insert("icmp".to_string(), true);
        let result = CheckResult {
            host_id: 42,
            success: true,
            latency_ms: Some(12.4),
            detail,
            ts: "2026-08-28T09:01:13Z".to_string(),
        };
        let json = serde_json::to_value(&result).unwrap();
        assert_eq!(
            json,
            serde_json::json!({
                "host_id": 42,
                "success": true,
                "latency_ms": 12.4,
                "detail": {"icmp": true},
                "ts": "2026-08-28T09:01:13Z"
            })
        );
    }

    #[test]
    fn a_result_without_latency_serializes_null_not_omitted() {
        let result = CheckResult {
            host_id: 1,
            success: false,
            latency_ms: None,
            detail: BTreeMap::new(),
            ts: "2026-08-28T09:01:13Z".to_string(),
        };
        let json = serde_json::to_value(&result).unwrap();
        assert!(json.get("latency_ms").unwrap().is_null());
    }

    #[test]
    fn timestamps_are_rfc3339_utc() {
        let ts = now_rfc3339();
        assert!(ts.ends_with('Z'), "{ts} should be UTC with a Z suffix");
        assert!(chrono::DateTime::parse_from_rfc3339(&ts).is_ok());
    }

    // ── ping argument construction ───────────────────────────────────────────

    #[test]
    fn linux_ping_args_match_the_backend_plus_a_separator() {
        let args = ping_args(PingFlavor::Linux, "10.0.0.5", Duration::from_secs(5));
        assert_eq!(args, vec!["-c", "1", "-W", "5", "--", "10.0.0.5"]);
    }

    #[test]
    fn macos_ping_args_use_the_total_deadline_flag() {
        let args = ping_args(PingFlavor::MacOs, "10.0.0.5", Duration::from_secs(3));
        assert_eq!(args, vec!["-c", "1", "-t", "3", "--", "10.0.0.5"]);
    }

    #[test]
    fn windows_ping_args_use_count_and_milliseconds_without_a_separator() {
        let args = ping_args(PingFlavor::Windows, "10.0.0.5", Duration::from_secs(5));
        assert_eq!(args, vec!["-n", "1", "-w", "5000", "10.0.0.5"]);
        assert!(!args.contains(&"--".to_string()));
    }

    #[test]
    fn sub_second_timeouts_never_round_down_to_zero() {
        let unix = ping_args(PingFlavor::Linux, "h", Duration::from_millis(500));
        assert_eq!(unix[3], "1");
        let win = ping_args(PingFlavor::Windows, "h", Duration::from_millis(500));
        assert_eq!(win[3], "500");
    }

    // ── ping output parsing ──────────────────────────────────────────────────

    #[test]
    fn parses_latency_from_linux_ping_output() {
        let out = "64 bytes from 10.0.0.5: icmp_seq=1 ttl=64 time=12.4 ms";
        assert_eq!(parse_ping_latency(out), Some(12.4));
    }

    #[test]
    fn parses_latency_from_english_windows_output() {
        let out = "Reply from 10.0.0.5: bytes=32 time=3ms TTL=64";
        assert_eq!(parse_ping_latency(out), Some(3.0));
    }

    #[test]
    fn parses_latency_from_the_sub_millisecond_windows_form() {
        let out = "Reply from 10.0.0.5: bytes=32 time<1ms TTL=128";
        assert_eq!(parse_ping_latency(out), Some(1.0));
    }

    #[test]
    fn parses_latency_from_localised_windows_output() {
        let out = "Antwort von 10.0.0.5: Bytes=32 Zeit=7ms TTL=64";
        assert_eq!(parse_ping_latency(out), Some(7.0));
    }

    #[test]
    fn returns_no_latency_when_the_output_carries_none() {
        assert_eq!(parse_ping_latency("Request timeout for icmp_seq 0"), None);
        assert_eq!(parse_ping_latency(""), None);
    }

    #[test]
    fn a_nonzero_exit_is_a_failed_icmp_check_everywhere() {
        assert!(!icmp_succeeded(PingFlavor::Linux, false, "time=1ms"));
        assert!(!icmp_succeeded(PingFlavor::Windows, false, "TTL=64"));
    }

    #[test]
    fn windows_unreachable_reply_is_not_counted_as_success() {
        // ping.exe exits 0 for a router's unreachable notice; without the TTL
        // requirement this would render a dead host as up.
        let out = "Reply from 10.0.0.1: Destination host unreachable.";
        assert!(!icmp_succeeded(PingFlavor::Windows, true, out));
        let good = "Reply from 10.0.0.5: bytes=32 time=1ms TTL=64";
        assert!(icmp_succeeded(PingFlavor::Windows, true, good));
    }

    // ── target selection and validation ──────────────────────────────────────

    #[test]
    fn network_checks_prefer_the_ip_and_fall_back_to_the_hostname() {
        let with_ip = assignment(
            r#"{"host_id": 1, "hostname": "nas.kunde.ch", "ip": "10.0.0.5", "check_type": "icmp"}"#,
        );
        assert_eq!(network_target(&with_ip).unwrap(), "10.0.0.5");

        let without_ip =
            assignment(r#"{"host_id": 1, "hostname": "nas.kunde.ch", "ip": null, "check_type": "icmp"}"#);
        assert_eq!(network_target(&without_ip).unwrap(), "nas.kunde.ch");
    }

    #[test]
    fn http_checks_prefer_the_hostname_so_certificates_match() {
        let a = assignment(
            r#"{"host_id": 1, "hostname": "nas.kunde.ch", "ip": "10.0.0.5", "check_type": "https"}"#,
        );
        assert_eq!(http_target(&a).unwrap(), "nas.kunde.ch");

        let ip_only = assignment(r#"{"host_id": 1, "hostname": "", "ip": "10.0.0.5"}"#);
        assert_eq!(http_target(&ip_only).unwrap(), "10.0.0.5");
    }

    #[test]
    fn an_assignment_without_a_target_is_an_error_not_a_guess() {
        let a = assignment(r#"{"host_id": 1, "hostname": "", "ip": null}"#);
        assert!(network_target(&a).is_err());
        assert!(http_target(&a).is_err());
    }

    #[test]
    fn option_like_targets_are_rejected() {
        assert!(validate_target("-oremote").is_err());
        assert!(validate_target("/t").is_err());
        assert!(validate_target("nas.kunde.ch extra").is_err());
        assert!(validate_target("").is_err());
        assert!(validate_target("10.0.0.5").is_ok());
    }

    // ── timeout handling ─────────────────────────────────────────────────────

    #[test]
    fn timeouts_are_clamped_into_a_usable_range() {
        assert_eq!(check_timeout(Some(5.0)), Duration::from_secs(5));
        assert_eq!(check_timeout(Some(0.01)), Duration::from_secs_f64(0.5));
        assert_eq!(check_timeout(Some(9999.0)), Duration::from_secs(60));
    }

    #[test]
    fn a_missing_or_nonsensical_timeout_falls_back_to_the_default() {
        let default = Duration::from_secs_f64(DEFAULT_TIMEOUT_SECONDS);
        assert_eq!(check_timeout(None), default);
        assert_eq!(check_timeout(Some(0.0)), default);
        assert_eq!(check_timeout(Some(-3.0)), default);
        assert_eq!(check_timeout(Some(f64::NAN)), default);
        assert_eq!(check_timeout(Some(f64::INFINITY)), default);
    }

    #[tokio::test]
    async fn a_check_that_cannot_be_performed_is_reported_as_failed() {
        // No hostname and no IP: the check is impossible, and the result must
        // still be a failed check for this host rather than a missing entry.
        let cfg = Config::for_test();
        let client = ProbeClient::new(&cfg).unwrap();
        let a = assignment(r#"{"host_id": 99, "hostname": "", "ip": null, "check_type": "icmp"}"#);
        let results = run_checks(&client, std::slice::from_ref(&a)).await;
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].host_id, 99);
        assert!(!results[0].success);
        assert_eq!(results[0].detail.get("icmp"), Some(&false));
    }

    #[tokio::test]
    async fn an_unknown_check_type_fails_loudly_instead_of_silently_passing() {
        let cfg = Config::for_test();
        let client = ProbeClient::new(&cfg).unwrap();
        let a = assignment(r#"{"host_id": 5, "hostname": "example.invalid", "check_type": "smtp"}"#);
        let results = run_checks(&client, std::slice::from_ref(&a)).await;
        assert_eq!(results.len(), 1);
        assert!(!results[0].success);
        assert_eq!(results[0].detail.get("smtp"), Some(&false));
    }

    #[tokio::test]
    async fn no_assignments_means_no_work_and_no_results() {
        let cfg = Config::for_test();
        let client = ProbeClient::new(&cfg).unwrap();
        assert!(run_checks(&client, &[]).await.is_empty());
    }

    // ── check type parsing and result mapping ────────────────────────────────

    #[test]
    fn check_types_are_normalised_and_default_to_icmp() {
        assert_eq!(parse_check_types("ICMP"), vec!["icmp"]);
        assert_eq!(parse_check_types(" icmp , https "), vec!["icmp", "https"]);
        assert_eq!(parse_check_types(""), vec!["icmp"]);
        assert_eq!(parse_check_types(" , "), vec!["icmp"]);
    }

    #[test]
    fn detail_labels_mirror_the_backend() {
        assert_eq!(detail_label("icmp", None), "icmp");
        assert_eq!(detail_label("icmp", Some(443)), "icmp");
        assert_eq!(detail_label("tcp", Some(8080)), "tcp:8080");
        assert_eq!(detail_label("tcp", None), "tcp");
        assert_eq!(detail_label("tcp:22", Some(8080)), "tcp:22");
        assert_eq!(detail_label("https", Some(8443)), "https");
    }

    #[test]
    fn tcp_port_prefers_the_suffix_then_the_assignment_then_80() {
        assert_eq!(tcp_port("tcp:8080", Some(443)), 8080);
        assert_eq!(tcp_port("tcp", Some(443)), 443);
        assert_eq!(tcp_port("tcp", None), DEFAULT_TCP_PORT);
        assert_eq!(tcp_port("tcp:0", Some(443)), 443);
        assert_eq!(tcp_port("tcp:notaport", None), DEFAULT_TCP_PORT);
    }

    #[test]
    fn http_urls_are_built_from_scheme_host_and_optional_port() {
        assert_eq!(
            build_http_url("https", "nas.kunde.ch", None).unwrap(),
            "https://nas.kunde.ch"
        );
        assert_eq!(
            build_http_url("http", "nas.kunde.ch", Some(8080)).unwrap(),
            "http://nas.kunde.ch:8080"
        );
        // A hostname that is already a URL is used verbatim, like the backend.
        assert_eq!(
            build_http_url("https", "http://nas.kunde.ch/health", Some(8080)).unwrap(),
            "http://nas.kunde.ch/health"
        );
        assert!(build_http_url("https", "  ", None).is_err());
    }

    fn sub(label: &str, is_icmp: bool, ok: bool, latency: Option<f64>) -> SubResult {
        SubResult {
            label: label.to_string(),
            is_icmp,
            ok,
            latency_ms: latency,
        }
    }

    #[test]
    fn icmp_alone_decides_success_when_it_is_configured() {
        let r = summarize(
            1,
            vec![
                sub("icmp", true, true, Some(12.4)),
                sub("https", false, false, None),
            ],
        );
        assert!(r.success, "a failing service check must not mark the host down");
        assert_eq!(r.latency_ms, Some(12.4));
        assert_eq!(r.detail.get("icmp"), Some(&true));
        assert_eq!(r.detail.get("https"), Some(&false));
    }

    #[test]
    fn without_icmp_any_successful_check_counts_as_up() {
        let r = summarize(
            2,
            vec![
                sub("tcp:22", false, false, None),
                sub("https", false, true, Some(80.0)),
            ],
        );
        assert!(r.success);
        assert_eq!(r.latency_ms, Some(80.0));
    }

    #[test]
    fn a_failed_icmp_check_marks_the_host_down_and_carries_no_latency() {
        let r = summarize(3, vec![sub("icmp", true, false, None)]);
        assert!(!r.success);
        assert_eq!(r.latency_ms, None);
        assert_eq!(r.detail.get("icmp"), Some(&false));
    }

    #[test]
    fn latency_prefers_icmp_over_a_service_check() {
        let r = summarize(
            4,
            vec![
                sub("https", false, true, Some(90.0)),
                sub("icmp", true, true, Some(4.0)),
            ],
        );
        assert_eq!(r.latency_ms, Some(4.0));
    }

    // ── bounded pending buffer ───────────────────────────────────────────────

    fn result(host_id: i64) -> CheckResult {
        CheckResult {
            host_id,
            success: true,
            latency_ms: None,
            detail: BTreeMap::new(),
            ts: "2026-08-28T09:01:13Z".to_string(),
        }
    }

    #[test]
    fn pending_results_keep_everything_below_the_cap() {
        let mut p = PendingResults::new();
        assert!(p.is_empty());
        assert_eq!(p.push_round((0..10).map(result).collect()), 0);
        assert_eq!(p.len(), 10);
        assert_eq!(p.dropped_total(), 0);
        assert_eq!(p.as_slice().len(), 10);
    }

    #[test]
    fn pending_results_are_bounded_and_drop_the_oldest_first() {
        let mut p = PendingResults::new();
        p.push_round((0..MAX_PENDING_RESULTS as i64).map(result).collect());
        assert_eq!(p.len(), MAX_PENDING_RESULTS);

        // One more full round on top of a full buffer.
        let dropped = p.push_round((10_000..10_005).map(result).collect());
        assert_eq!(dropped, 5);
        assert_eq!(p.len(), MAX_PENDING_RESULTS, "the buffer never grows past its cap");
        assert_eq!(p.dropped_total(), 5);

        let slice = p.as_slice();
        // The five oldest are gone, the five newest are at the end.
        assert_eq!(slice.first().unwrap().host_id, 5);
        assert_eq!(slice.last().unwrap().host_id, 10_004);
    }

    #[test]
    fn an_endless_outage_cannot_grow_the_buffer() {
        let mut p = PendingResults::new();
        // 200 rounds of 50 results with no successful heartbeat in between.
        for _ in 0..200 {
            p.push_round((0..50).map(result).collect());
        }
        assert_eq!(p.len(), MAX_PENDING_RESULTS);
        assert!(p.dropped_total() > 0);
    }

    #[test]
    fn a_delivered_round_clears_the_buffer() {
        let mut p = PendingResults::new();
        p.push_round((0..3).map(result).collect());
        p.clear();
        assert!(p.is_empty());
        assert_eq!(p.as_slice().len(), 0);
    }
}
