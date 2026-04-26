mod brain;
mod provider;
mod secrets;

use std::sync::Arc;
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, State};
use tokio::sync::RwLock;

use crate::brain::{BrainSupervisor, SpawnConfig};
use crate::provider::{builtin_providers, ProviderMeta, ProviderRegistry};
use crate::secrets::SecretsManager;

const BRAIN_CALL_TIMEOUT: Duration = Duration::from_secs(15);
const CHAT_DEADLINE: Duration = Duration::from_secs(180);
const CHAT_CHUNK_EVENT: &str = "chat:chunk";

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

#[derive(Debug, Serialize, Clone)]
struct ChatChunkEvent {
    session_id: String,
    chunk: Value,
}

#[derive(Debug, Serialize)]
struct ChatSummary {
    session_id: String,
    chunks: u64,
    reason: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    total_cost_usd: Option<f64>,
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

#[tauri::command]
async fn send_chat(
    app: AppHandle,
    state: State<'_, AppState>,
    session_id: String,
    provider_id: String,
    prompt: String,
    system_prompt: Option<String>,
) -> Result<ChatSummary, String> {
    if !state.secrets.has_api_key(&provider_id) {
        return Err(format!("provider {provider_id} has no API key configured",));
    }

    let session_id_for_callback = session_id.clone();
    let app_for_callback = app.clone();

    let mut params = json!({
        "sessionId": session_id,
        "providerId": provider_id,
        "prompt": prompt,
    });
    if let Some(sp) = system_prompt {
        params["systemPrompt"] = Value::String(sp);
    }

    let result = state
        .brain
        .call_streaming("chat.send", params, CHAT_DEADLINE, move |method, params| {
            if method != "chat.chunk" {
                return;
            }
            if let Some(chunk) = params.get("chunk") {
                let event = ChatChunkEvent {
                    session_id: session_id_for_callback.clone(),
                    chunk: chunk.clone(),
                };
                if let Err(err) = app_for_callback.emit(CHAT_CHUNK_EVENT, event) {
                    tracing::error!(?err, "failed to forward chat chunk");
                }
            }
        })
        .await
        .map_err(|err| err.to_string())?;

    Ok(ChatSummary {
        session_id: result
            .get("sessionId")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        chunks: result.get("chunks").and_then(|v| v.as_u64()).unwrap_or(0),
        reason: result
            .get("reason")
            .and_then(|v| v.as_str())
            .unwrap_or("incomplete")
            .to_string(),
        total_cost_usd: result.get("totalCostUsd").and_then(|v| v.as_f64()),
    })
}

/// Spawn the brain sidecar during app setup. Failure here surfaces as a
/// startup error rather than crashing the app.
async fn init_app_state(app: &AppHandle) -> Result<(), String> {
    let secrets = Arc::new(SecretsManager::with_default_store());
    let registry = ProviderRegistry::new(builtin_providers(secrets.has_api_key("anthropic")));

    let mut config = SpawnConfig::dev_default();
    if let Ok(Some(api_key)) = secrets.read_api_key("anthropic") {
        // Forward the key into the spawn env so the Claude Agent SDK
        // can authenticate without anything touching disk.
        config = config.with_env("ANTHROPIC_API_KEY", api_key);
    }
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
            send_chat,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
