mod brain;
mod provider;
mod secrets;

use std::sync::Arc;
use std::time::Duration;

use serde::Serialize;
use serde_json::json;
use tauri::{AppHandle, Manager, State};
use tokio::sync::RwLock;

use crate::brain::{BrainSupervisor, SpawnConfig};
use crate::provider::{builtin_providers, ProviderMeta, ProviderRegistry};
use crate::secrets::SecretsManager;

const BRAIN_CALL_TIMEOUT: Duration = Duration::from_secs(15);

/// Shared application state, registered with Tauri's `manage` so commands
/// can pull it via `State<...>`.
struct AppState {
    brain: Arc<BrainSupervisor>,
    providers: RwLock<ProviderRegistry>,
    secrets: Arc<SecretsManager>,
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

#[tauri::command]
async fn list_providers(state: State<'_, AppState>) -> Result<Vec<ProviderMeta>, String> {
    let mut metas = state.providers.read().await.list_meta();
    // Reflect the current keychain state in the configured flag so the
    // UI shows whether a provider is selectable without us having to
    // refresh the registry on every key change.
    for meta in metas.iter_mut() {
        meta.configured = state.secrets.has_api_key(&meta.id);
    }
    Ok(metas)
}

#[tauri::command]
async fn save_api_key(
    state: State<'_, AppState>,
    provider_id: String,
    api_key: String,
) -> Result<(), String> {
    if api_key.trim().is_empty() {
        return Err("api key is empty".into());
    }
    state
        .secrets
        .save_api_key(&provider_id, api_key.trim())
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn clear_api_key(state: State<'_, AppState>, provider_id: String) -> Result<(), String> {
    state
        .secrets
        .delete_api_key(&provider_id)
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn provider_configured(
    state: State<'_, AppState>,
    provider_id: String,
) -> Result<bool, String> {
    Ok(state.secrets.has_api_key(&provider_id))
}

/// Spawn the brain sidecar during app setup. Failure here surfaces as a
/// startup error rather than crashing the app — the renderer can show a
/// useful message and the user can re-launch after fixing their setup.
async fn init_app_state(app: &AppHandle) -> Result<(), String> {
    let secrets = Arc::new(SecretsManager::with_default_store());
    let registry = ProviderRegistry::new(builtin_providers(secrets.has_api_key("anthropic")));

    let config = SpawnConfig::dev_default();
    tracing::info!(program = %config.program, args = ?config.args, "spawning brain sidecar");
    let supervisor = BrainSupervisor::spawn(config)
        .await
        .map_err(|err| format!("brain sidecar failed to start: {err}"))?;

    app.manage(AppState {
        brain: Arc::new(supervisor),
        providers: RwLock::new(registry),
        secrets,
    });
    Ok(())
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
                if let Err(err) = init_app_state(&handle).await {
                    tracing::error!(?err, "failed to init app state");
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            ping_brain,
            list_providers,
            save_api_key,
            clear_api_key,
            provider_configured,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
