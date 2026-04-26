mod brain;
mod power;
mod provider;
mod sandbox;
mod secrets;

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, State};
use tokio::sync::{Mutex, RwLock};

use crate::brain::{BrainSupervisor, SpawnConfig};
use crate::power::{AssertionToken, PowerManager};
use crate::provider::{builtin_providers, ProviderMeta, ProviderRegistry};
use crate::sandbox::SandboxManager;
use crate::secrets::SecretsManager;

const BRAIN_CALL_TIMEOUT: Duration = Duration::from_secs(15);
const CHAT_DEADLINE: Duration = Duration::from_secs(180);
const CHAT_CHUNK_EVENT: &str = "chat:chunk";
const RUN_STATUS_EVENT: &str = "run:status";
const RUN_PLAN_UPDATE_EVENT: &str = "run:plan_update";
const RUN_ACTION_LOG_EVENT: &str = "run:action_log";
const RUN_APPROVAL_REQUIRED_EVENT: &str = "run:approval_required";

/// Shared application state, registered with Tauri's `manage` so commands
/// can pull it via `State<...>`.
struct AppState {
    brain: Arc<BrainSupervisor>,
    providers: RwLock<ProviderRegistry>,
    secrets: Arc<SecretsManager>,
    /// Sandbox manager is plumbed through AppState now so the
    /// restricted-shell + tier-1 commits land into a stable surface.
    /// Consumed by Tauri commands in subsequent commits.
    #[allow(dead_code)]
    sandboxes: Arc<SandboxManager>,
    power: Arc<PowerManager>,
    /// run id → outstanding power-assertion token. Lets the
    /// notification forwarder release a previously-acquired
    /// assertion when the same run hits a terminal status.
    assertions: Arc<Mutex<HashMap<String, AssertionToken>>>,
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
async fn list_runs(
    state: State<'_, AppState>,
    statuses: Option<Vec<String>>,
    limit: Option<u32>,
) -> Result<Value, String> {
    let mut params = serde_json::Map::new();
    if let Some(statuses) = statuses {
        params.insert("statuses".into(), Value::from(statuses));
    }
    if let Some(limit) = limit {
        params.insert("limit".into(), Value::from(limit));
    }
    state
        .brain
        .call("runs.list", Value::Object(params), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn get_run(state: State<'_, AppState>, run_id: String) -> Result<Value, String> {
    state
        .brain
        .call("runs.get", json!({ "runId": run_id }), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn get_run_tree(state: State<'_, AppState>, run_id: String) -> Result<Value, String> {
    state
        .brain
        .call("runs.tree", json!({ "runId": run_id }), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn kill_run(
    app: AppHandle,
    state: State<'_, AppState>,
    run_id: String,
) -> Result<Value, String> {
    let app_for_callback = app.clone();
    state
        .brain
        .call_streaming(
            "runs.kill",
            json!({ "runId": run_id }),
            BRAIN_CALL_TIMEOUT,
            move |method, params| {
                forward_brain_notification(method, params, &app_for_callback, "");
            },
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn list_schedules(state: State<'_, AppState>) -> Result<Value, String> {
    state
        .brain
        .call("schedules.list", json!({}), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn create_schedule(
    state: State<'_, AppState>,
    title: String,
    nl_input: Option<String>,
    cron: Option<String>,
    run_template: Value,
) -> Result<Value, String> {
    let mut params = serde_json::Map::new();
    params.insert("title".into(), Value::from(title));
    if let Some(nl) = nl_input {
        params.insert("nlInput".into(), Value::from(nl));
    }
    if let Some(c) = cron {
        params.insert("cron".into(), Value::from(c));
    }
    params.insert("runTemplate".into(), run_template);
    state
        .brain
        .call(
            "schedules.create",
            Value::Object(params),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn delete_schedule(state: State<'_, AppState>, schedule_id: String) -> Result<Value, String> {
    state
        .brain
        .call(
            "schedules.delete",
            json!({ "scheduleId": schedule_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn translate_cron(
    state: State<'_, AppState>,
    nl_input: String,
    provider_id: Option<String>,
) -> Result<Value, String> {
    let mut params = serde_json::Map::new();
    params.insert("nlInput".into(), Value::from(nl_input));
    if let Some(provider) = provider_id {
        params.insert("providerId".into(), Value::from(provider));
    }
    state
        .brain
        .call("cron.translate", Value::Object(params), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

/// Translate a brain JSON-RPC notification into a Tauri event for the
/// renderer. `chat.chunk` is wrapped with the session id; the run.*
/// notifications already carry their runId in the params.
fn forward_brain_notification(method: &str, params: &Value, app: &AppHandle, session_id: &str) {
    let event_name = match method {
        "chat.chunk" => CHAT_CHUNK_EVENT,
        "run.status" => RUN_STATUS_EVENT,
        "run.plan_update" => RUN_PLAN_UPDATE_EVENT,
        "run.action_log" => RUN_ACTION_LOG_EVENT,
        "run.approval_required" => RUN_APPROVAL_REQUIRED_EVENT,
        _ => return,
    };
    if event_name == CHAT_CHUNK_EVENT {
        if let Some(chunk) = params.get("chunk") {
            let event = ChatChunkEvent {
                session_id: session_id.to_owned(),
                chunk: chunk.clone(),
            };
            if let Err(err) = app.emit(event_name, event) {
                tracing::error!(?err, "failed to forward chat chunk");
            }
        }
        return;
    }
    if event_name == RUN_STATUS_EVENT {
        update_power_assertion(params, app);
    }
    if let Err(err) = app.emit(event_name, params.clone()) {
        tracing::error!(?err, %event_name, "failed to forward brain notification");
    }
}

/// Acquire / release the per-run power assertion so a long run keeps
/// the system awake. Display sleep is intentionally not blocked —
/// the spec wants the screen to power down even mid-run.
fn update_power_assertion(params: &Value, app: &AppHandle) {
    let Some(run_id) = params.get("runId").and_then(|v| v.as_str()) else {
        return;
    };
    let Some(status) = params.get("status").and_then(|v| v.as_str()) else {
        return;
    };
    let Some(state) = app.try_state::<AppState>() else {
        return;
    };
    let power = state.power.clone();
    let assertions = state.assertions.clone();
    let run_id = run_id.to_owned();
    let status = status.to_owned();

    tauri::async_runtime::spawn(async move {
        match status.as_str() {
            "running" | "planning" | "pending" => {
                let map = assertions.lock().await;
                if map.contains_key(&run_id) {
                    return;
                }
                drop(map);
                match power.acquire(format!("thalyn run {run_id}")).await {
                    Ok(token) => {
                        assertions.lock().await.insert(run_id, token);
                    }
                    Err(err) => {
                        tracing::warn!(?err, "could not acquire power assertion");
                    }
                }
            }
            "completed" | "errored" | "killed" | "awaiting_approval" => {
                let token = assertions.lock().await.remove(&run_id);
                if let Some(token) = token {
                    power.release(token).await;
                }
            }
            _ => {}
        }
    });
}

#[tauri::command]
async fn approve_plan(
    app: AppHandle,
    state: State<'_, AppState>,
    run_id: String,
    provider_id: String,
    decision: String,
    edited_plan: Option<Value>,
    session_id: Option<String>,
) -> Result<Value, String> {
    let app_for_callback = app.clone();
    let session_id_for_callback = session_id.unwrap_or_default();

    let mut params = json!({
        "runId": run_id,
        "providerId": provider_id,
        "decision": decision,
    });
    if let Some(plan) = edited_plan {
        params["editedPlan"] = plan;
    }

    state
        .brain
        .call_streaming(
            "run.approve_plan",
            params,
            CHAT_DEADLINE,
            move |method, params| {
                forward_brain_notification(
                    method,
                    params,
                    &app_for_callback,
                    &session_id_for_callback,
                );
            },
        )
        .await
        .map_err(|err| err.to_string())
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
            forward_brain_notification(method, params, &app_for_callback, &session_id_for_callback);
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
        sandboxes: Arc::new(SandboxManager::new()),
        power: Arc::new(PowerManager::new()),
        assertions: Arc::new(Mutex::new(HashMap::new())),
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
            approve_plan,
            list_runs,
            get_run,
            get_run_tree,
            kill_run,
            list_schedules,
            create_schedule,
            delete_schedule,
            translate_cron,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
