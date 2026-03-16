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

        if cfg.server.is_empty() {
            anyhow::bail!("Server URL is required (--server, NODEGLOW_SERVER, or config.json)");
        }

        Ok(cfg)
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
