use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use sha2::{Digest, Sha256};
use tracing::{info, warn, debug};

use crate::client::ApiClient;
use crate::config::Config;

/// Return the first 8 bytes of a server-provided string for logging without
/// panicking if it is shorter (the value is fully server-controlled).
fn short(s: &str) -> &str {
    s.get(..8).unwrap_or(s)
}

/// True if `s` is a non-empty, even-length lowercase/uppercase hex string.
fn is_hex(s: &str) -> bool {
    !s.is_empty() && s.len() % 2 == 0 && s.bytes().all(|b| b.is_ascii_hexdigit())
}

/// Verify an ed25519 detached signature over `data` using a hex-encoded public
/// key and hex-encoded signature. Returns Err on any decode/verify failure.
fn verify_signature(public_key_hex: &str, signature_hex: &str, data: &[u8]) -> anyhow::Result<()> {
    let key_bytes = hex::decode(public_key_hex)
        .map_err(|_| anyhow::anyhow!("update_public_key is not valid hex"))?;
    let key_arr: [u8; 32] = key_bytes
        .as_slice()
        .try_into()
        .map_err(|_| anyhow::anyhow!("update_public_key must be 32 bytes"))?;
    let verifying_key = VerifyingKey::from_bytes(&key_arr)
        .map_err(|e| anyhow::anyhow!("invalid ed25519 public key: {e}"))?;

    let sig_bytes = hex::decode(signature_hex)
        .map_err(|_| anyhow::anyhow!("signature is not valid hex"))?;
    let sig_arr: [u8; 64] = sig_bytes
        .as_slice()
        .try_into()
        .map_err(|_| anyhow::anyhow!("ed25519 signature must be 64 bytes"))?;
    let signature = Signature::from_bytes(&sig_arr);

    verifying_key
        .verify(data, &signature)
        .map_err(|e| anyhow::anyhow!("ed25519 signature verification failed: {e}"))
}

/// Check for updates and apply if available. Returns true if an update was applied.
///
/// Integrity model:
///   - TLS now authenticates the channel (see client.rs), closing the MITM vector.
///   - The downloaded binary is verified against the server-provided SHA-256 hash.
///   - Defense-in-depth: if `update_public_key` is configured AND the server
///     supplies a detached ed25519 `signature`, the signature is verified over
///     the downloaded bytes before applying. If no public key is configured the
///     hash check remains the active integrity path.
///
/// NOTE: signing must be enabled server-side for the signature path to engage —
/// the server has to return a `signature` field on /api/agent/version/<platform>
/// and the operator has to set `update_public_key` in the agent config.
pub async fn check_and_update(api: &ApiClient, cfg: &Config) -> anyhow::Result<bool> {
    let platform = if cfg!(target_os = "windows") {
        "windows"
    } else {
        "linux"
    };

    // Get expected hash (+ optional signature) from server
    let version = api.get_version_info(platform).await?;
    let server_hash = version.hash.trim().to_string();
    debug!("Server hash: {server_hash}");

    // Validate the server-provided hash is a non-empty hex digest before use.
    if !is_hex(&server_hash) {
        anyhow::bail!("Server returned an invalid version hash");
    }

    // Hash our own binary
    let exe_path = std::env::current_exe()?;
    let exe_data = tokio::fs::read(&exe_path).await?;
    let local_hash = hex::encode(Sha256::digest(&exe_data));
    debug!("Local hash: {local_hash}");

    if local_hash == server_hash {
        return Ok(false);
    }

    info!("Update available (local={} server={})", short(&local_hash), short(&server_hash));

    // Download new binary (size-capped in the client)
    let new_data = api.download_agent(platform).await?;

    // Verify hash
    let download_hash = hex::encode(Sha256::digest(&new_data));
    if download_hash != server_hash {
        warn!(
            "Download hash mismatch (expected={} got={}), aborting update",
            short(&server_hash),
            short(&download_hash)
        );
        anyhow::bail!("Hash verification failed");
    }

    // Defense-in-depth: verify ed25519 signature when a public key is configured.
    if !cfg.update_public_key.trim().is_empty() {
        match version.signature.as_deref() {
            Some(sig) if !sig.trim().is_empty() => {
                verify_signature(cfg.update_public_key.trim(), sig.trim(), &new_data)?;
                info!("Update ed25519 signature verified");
            }
            _ => {
                // A public key is configured but the server provided no signature.
                // Fail closed: the operator has opted into signing.
                anyhow::bail!(
                    "update_public_key is configured but server provided no signature, aborting update"
                );
            }
        }
    }

    // Apply update
    apply_update(&exe_path, &new_data).await?;

    Ok(true)
}

// The agent ships for Linux (musl) and Windows only. Any other platform — macOS
// in particular, where the agent is developed and its tests are run — has no
// self-update path. Refuse explicitly instead of failing to compile there.
#[cfg(not(any(target_os = "linux", target_os = "windows")))]
async fn apply_update(_exe_path: &std::path::Path, _data: &[u8]) -> anyhow::Result<()> {
    anyhow::bail!("Self-update is not supported on {}", std::env::consts::OS)
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
