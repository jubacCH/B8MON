use clap::Parser;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use tracing::debug;

#[derive(Parser, Debug)]
#[command(name = "nodeglow-agent", about = "Nodeglow monitoring agent")]
struct Cli {
    /// Server URL (e.g. http://nodeglow.local:8000)
    #[arg(long, env = "NODEGLOW_SERVER")]
    server: Option<String>,

    /// Agent token (from enrollment)
    #[arg(long, env = "NODEGLOW_TOKEN")]
    token: Option<String>,

    /// Report interval in seconds
    #[arg(long, env = "NODEGLOW_INTERVAL")]
    interval: Option<u64>,

    /// Enrollment key (for first-time enrollment)
    #[arg(long, env = "NODEGLOW_ENROLLMENT_KEY")]
    enrollment_key: Option<String>,

    /// Allow connecting to servers with invalid/self-signed TLS certificates.
    /// INSECURE — only for testing. Defaults to false (certificates are verified).
    #[arg(long, env = "NODEGLOW_ALLOW_INSECURE_TLS")]
    allow_insecure_tls: Option<bool>,

    /// Config file path
    #[arg(long, short)]
    config: Option<PathBuf>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    pub server: String,
    #[serde(default)]
    pub token: String,
    #[serde(default = "default_interval")]
    pub interval: u64,
    #[serde(default)]
    pub enrollment_key: String,
    /// When true, TLS certificate validation is disabled (INSECURE). Default false.
    #[serde(default)]
    pub allow_insecure_tls: bool,
    /// Optional ed25519 public key (hex-encoded, 32 bytes) used to verify a
    /// detached signature over downloaded update binaries. Empty = signature
    /// verification disabled, fall back to the SHA-256 hash check only.
    #[serde(default)]
    pub update_public_key: String,
    #[serde(skip)]
    pub config_path: PathBuf,
}

fn default_interval() -> u64 {
    30
}

impl Config {
    /// Load config: CLI args > env vars > config file
    pub fn load() -> anyhow::Result<Self> {
        let cli = Cli::parse();

        let config_path = cli.config.unwrap_or_else(|| {
            let exe = std::env::current_exe().unwrap_or_default();
            exe.parent()
                .unwrap_or(std::path::Path::new("."))
                .join("config.json")
        });

        debug!("Config path: {}", config_path.display());

        // Load file config (if exists)
        let mut cfg = if config_path.exists() {
            let data = std::fs::read_to_string(&config_path)?;
            serde_json::from_str::<Config>(&data)?
        } else {
            Config {
                server: String::new(),
                token: String::new(),
                interval: default_interval(),
                enrollment_key: String::new(),
                allow_insecure_tls: false,
                update_public_key: String::new(),
                config_path: PathBuf::new(),
            }
        };

        cfg.config_path = config_path;

        // Override with CLI / env
        if let Some(s) = cli.server {
            cfg.server = s;
        }
        if let Some(t) = cli.token {
            cfg.token = t;
        }
        if let Some(i) = cli.interval {
            cfg.interval = i;
        }
        if let Some(k) = cli.enrollment_key {
            cfg.enrollment_key = k;
        }
        if let Some(insecure) = cli.allow_insecure_tls {
            cfg.allow_insecure_tls = insecure;
        }

        if cfg.server.is_empty() {
            anyhow::bail!("Server URL is required (--server, NODEGLOW_SERVER, or config.json)");
        }

        Ok(cfg)
    }

    /// Minimal in-memory config for unit tests. Never reads or writes a file.
    #[cfg(test)]
    pub fn for_test() -> Self {
        Config {
            server: "http://localhost:8000".to_string(),
            token: String::new(),
            interval: default_interval(),
            enrollment_key: String::new(),
            allow_insecure_tls: false,
            update_public_key: String::new(),
            config_path: PathBuf::new(),
        }
    }

    /// Save current config to file (preserves token after enrollment).
    pub fn save(&self) -> anyhow::Result<()> {
        #[derive(Serialize)]
        struct FileConfig<'a> {
            server: &'a str,
            token: &'a str,
            interval: u64,
        }

        let data = serde_json::to_string_pretty(&FileConfig {
            server: &self.server,
            token: &self.token,
            interval: self.interval,
        })?;

        std::fs::write(&self.config_path, data)?;

        // The config file holds the enrollment bearer token. Restrict its
        // permissions so other local users cannot read the credential.
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let perms = std::fs::Permissions::from_mode(0o600);
            std::fs::set_permissions(&self.config_path, perms)?;
        }
        // On Windows the file inherits the parent directory ACL. Tightening the
        // ACL (e.g. removing inherited entries, granting only the current user)
        // requires WinAPI calls and is non-trivial; the agent typically runs as
        // a service under a dedicated/system account, so the inherited ACL is
        // relied upon here.

        Ok(())
    }
}

/// Runtime config pushed from server on each report.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct ServerConfig {
    #[serde(default)]
    pub log_levels: String,
    #[serde(default)]
    pub log_channels: String,
    #[serde(default)]
    pub log_file_paths: String,
    #[serde(default = "default_log_level")]
    pub agent_log_level: String,
}

fn default_log_level() -> String {
    "errors".to_string()
}
