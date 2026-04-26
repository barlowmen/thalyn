mod brain;

use std::sync::Arc;
use std::time::Duration;

use serde::Serialize;
use serde_json::json;
use tauri::{AppHandle, Manager, State};

use crate::brain::{BrainSupervisor, SpawnConfig};

const BRAIN_CALL_TIMEOUT: Duration = Duration::from_secs(15);

/// Shared application state, registered with Tauri's `manage` so commands
/// can pull it via `State<...>`.
struct AppState {
    brain: Arc<BrainSupervisor>,
}

/// Trimmed pong payload sent back to the renderer.
#[derive(Debug, Serialize)]
struct PongPayload {
    pong: bool,
    version: String,
    epoch_ms: i64,
}

#[tauri::command]
async fn ping_brain(state: State<'_, AppState>) -> Result<PongPayload, String> {
    let result = state
        .brain
        .call("ping", json!({}), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())?;

    let pong = result
        .get("pong")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let version = result
        .get("version")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_owned();
    let epoch_ms = result.get("epoch_ms").and_then(|v| v.as_i64()).unwrap_or(0);

    Ok(PongPayload {
        pong,
        version,
        epoch_ms,
    })
}

/// Spawn the brain sidecar during app setup. Failure here surfaces as a
/// startup error rather than crashing the app — the renderer can show a
/// useful message and the user can re-launch after fixing their setup.
async fn init_brain(app: &AppHandle) -> Result<Arc<BrainSupervisor>, String> {
    let config = SpawnConfig::dev_default();
    tracing::info!(program = %config.program, args = ?config.args, "spawning brain sidecar");
    let supervisor = BrainSupervisor::spawn(config)
        .await
        .map_err(|err| format!("brain sidecar failed to start: {err}"))?;
    let arc = Arc::new(supervisor);
    app.manage(AppState {
        brain: Arc::clone(&arc),
    });
    Ok(arc)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info,thalyn=debug")),
        )
        .init();

    tauri::Builder::default()
        .setup(|app| {
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(err) = init_brain(&handle).await {
                    tracing::error!(?err, "failed to init brain sidecar");
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![ping_brain])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
