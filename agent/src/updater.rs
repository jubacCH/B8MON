use sha2::{Digest, Sha256};
use tracing::{info, warn, debug};

use crate::client::ApiClient;

/// Check for updates and apply if available. Returns true if an update was applied.
pub async fn check_and_update(api: &ApiClient) -> anyhow::Result<bool> {
    let platform = if cfg!(target_os = "windows") {
        "windows"
    } else {
        "linux"
    };

    // Get expected hash from server
    let server_hash = api.get_version_hash(platform).await?;
    debug!("Server hash: {server_hash}");

    // Hash our own binary
    let exe_path = std::env::current_exe()?;
    let exe_data = tokio::fs::read(&exe_path).await?;
    let local_hash = hex::encode(Sha256::digest(&exe_data));
    debug!("Local hash: {local_hash}");

    if local_hash == server_hash {
        return Ok(false);
    }

    info!("Update available (local={} server={})", &local_hash[..8], &server_hash[..8]);

    // Download new binary
    let new_data = api.download_agent(platform).await?;

    // Verify hash
    let download_hash = hex::encode(Sha256::digest(&new_data));
    if download_hash != server_hash {
        warn!(
            "Download hash mismatch (expected={} got={}), aborting update",
            &server_hash[..8],
            &download_hash[..8]
        );
        anyhow::bail!("Hash verification failed");
    }

    // Apply update
    apply_update(&exe_path, &new_data).await?;

    Ok(true)
}

#[cfg(target_os = "linux")]
async fn apply_update(exe_path: &std::path::Path, data: &[u8]) -> anyhow::Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let tmp_path = exe_path.with_extension("new");

    // Write to temp file
    tokio::fs::write(&tmp_path, data).await?;

    // Make executable
    let mut perms = tokio::fs::metadata(&tmp_path).await?.permissions();
    perms.set_mode(0o755);
    tokio::fs::set_permissions(&tmp_path, perms).await?;

    // Atomic replace
    tokio::fs::rename(&tmp_path, exe_path).await?;

    info!("Update applied to {}", exe_path.display());
    Ok(())
}

#[cfg(target_os = "windows")]
async fn apply_update(exe_path: &std::path::Path, data: &[u8]) -> anyhow::Result<()> {
    let dir = exe_path.parent().unwrap_or(std::path::Path::new("."));
    let new_path = dir.join("nodeglow-agent.exe.new");
    let old_path = dir.join("nodeglow-agent.exe.old");

    // Write new binary
    tokio::fs::write(&new_path, data).await?;

    // Try direct rename (may fail if running)
    let _ = tokio::fs::remove_file(&old_path).await;
    match tokio::fs::rename(exe_path, &old_path).await {
        Ok(()) => {
            tokio::fs::rename(&new_path, exe_path).await?;
            info!("Update applied via direct rename");
        }
        Err(_) => {
            // Deferred swap: the wrapper batch file will handle it on restart
            info!("Deferred update: .exe.new written, will be swapped on restart");
        }
    }

    Ok(())
}
