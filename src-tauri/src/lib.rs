mod brain;
mod browser;
pub mod cef;
mod data_dir;
mod power;
mod provider;
mod sandbox;
mod secrets;
mod terminal;

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, State};
use tokio::sync::{Mutex, RwLock};

use crate::brain::{BrainSupervisor, SpawnConfig};
use crate::browser::BrowserManager;
use crate::cef::CefHost;
use crate::power::{AssertionToken, PowerManager};
use crate::provider::{builtin_providers, ProviderMeta, ProviderRegistry};
use crate::sandbox::SandboxManager;
use crate::secrets::SecretsManager;
use crate::terminal::TerminalManager;

const BRAIN_CALL_TIMEOUT: Duration = Duration::from_secs(15);
const CHAT_DEADLINE: Duration = Duration::from_secs(180);
const CHAT_CHUNK_EVENT: &str = "chat:chunk";
const RUN_STATUS_EVENT: &str = "run:status";
const RUN_PLAN_UPDATE_EVENT: &str = "run:plan_update";
const RUN_ACTION_LOG_EVENT: &str = "run:action_log";
const RUN_APPROVAL_REQUIRED_EVENT: &str = "run:approval_required";
const LEAD_ESCALATION_EVENT: &str = "lead:escalation";
const LSP_MESSAGE_EVENT: &str = "lsp:message";
const LSP_ERROR_EVENT: &str = "lsp:error";
const TERMINAL_DATA_EVENT: &str = "terminal:data";

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
    /// v1 system-Chromium sidecar manager. Retained for the
    /// engine-swap transition; the in-app `browser_*` commands now
    /// drive [`CefHost`] instead. v1 retires when the bundled-CEF
    /// path is the only shipped engine.
    #[allow(dead_code)]
    browser: Arc<BrowserManager>,
    /// Bundled CEF child-binary host. The renderer drives lifecycle
    /// via the `browser_*` Tauri commands; the brain attaches via
    /// JSON-RPC `browser.attach` once the DevTools endpoint comes up.
    cef: Arc<CefHost>,
    power: Arc<PowerManager>,
    /// run id → outstanding power-assertion token. Lets the
    /// notification forwarder release a previously-acquired
    /// assertion when the same run hits a terminal status.
    assertions: Arc<Mutex<HashMap<String, AssertionToken>>>,
    terminals: Arc<TerminalManager>,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    lead_id: Option<String>,
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
async fn save_observability_secret(
    state: State<'_, AppState>,
    name: String,
    value: String,
) -> Result<(), String> {
    if !is_observability_secret(&name) {
        return Err(format!("unknown observability secret: {name}"));
    }
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("value is empty".into());
    }
    state
        .secrets
        .save_secret(&name, trimmed)
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn clear_observability_secret(
    state: State<'_, AppState>,
    name: String,
) -> Result<(), String> {
    if !is_observability_secret(&name) {
        return Err(format!("unknown observability secret: {name}"));
    }
    state
        .secrets
        .delete_secret(&name)
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn observability_status(state: State<'_, AppState>) -> Result<Value, String> {
    Ok(json!({
        "sentryDsnConfigured": state.secrets.has_secret("sentry_dsn"),
        "otelOtlpEndpointConfigured": state.secrets.has_secret("otel_otlp_endpoint"),
    }))
}

fn is_observability_secret(name: &str) -> bool {
    matches!(name, "sentry_dsn" | "otel_otlp_endpoint")
}

#[tauri::command]
async fn list_runs(
    state: State<'_, AppState>,
    statuses: Option<Vec<String>>,
    limit: Option<u32>,
    parent_lead_id: Option<String>,
) -> Result<Value, String> {
    let mut params = serde_json::Map::new();
    if let Some(statuses) = statuses {
        params.insert("statuses".into(), Value::from(statuses));
    }
    if let Some(limit) = limit {
        params.insert("limit".into(), Value::from(limit));
    }
    if let Some(p) = parent_lead_id {
        params.insert("parentLeadId".into(), Value::from(p));
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
async fn provider_delta(
    state: State<'_, AppState>,
    from_id: String,
    to_id: String,
) -> Result<Value, String> {
    state
        .brain
        .call(
            "providers.delta",
            json!({ "fromId": from_id, "toId": to_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn auth_list(state: State<'_, AppState>) -> Result<Value, String> {
    state
        .brain
        .call("auth.list", json!({}), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn auth_probe(state: State<'_, AppState>, kind: String) -> Result<Value, String> {
    state
        .brain
        .call("auth.probe", json!({ "kind": kind }), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn auth_set(state: State<'_, AppState>, kind: String) -> Result<Value, String> {
    state
        .brain
        .call("auth.set", json!({ "kind": kind }), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn thread_recent(
    state: State<'_, AppState>,
    thread_id: String,
    limit: Option<u32>,
) -> Result<Value, String> {
    let mut params = serde_json::Map::new();
    params.insert("threadId".into(), Value::from(thread_id));
    if let Some(l) = limit {
        params.insert("limit".into(), Value::from(l));
    }
    state
        .brain
        .call("thread.recent", Value::Object(params), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn digest_latest(state: State<'_, AppState>, thread_id: String) -> Result<Value, String> {
    state
        .brain
        .call(
            "digest.latest",
            json!({ "threadId": thread_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn lead_list(
    state: State<'_, AppState>,
    project_id: Option<String>,
    status: Option<String>,
    kind: Option<String>,
) -> Result<Value, String> {
    let mut params = serde_json::Map::new();
    if let Some(p) = project_id {
        params.insert("projectId".into(), Value::from(p));
    }
    if let Some(s) = status {
        params.insert("status".into(), Value::from(s));
    }
    if let Some(k) = kind {
        params.insert("kind".into(), Value::from(k));
    }
    state
        .brain
        .call("lead.list", Value::Object(params), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn lead_spawn(
    state: State<'_, AppState>,
    project_id: String,
    display_name: Option<String>,
    default_provider_id: Option<String>,
    system_prompt: Option<String>,
) -> Result<Value, String> {
    let mut params = serde_json::Map::new();
    params.insert("projectId".into(), Value::from(project_id));
    if let Some(name) = display_name {
        params.insert("displayName".into(), Value::from(name));
    }
    if let Some(provider) = default_provider_id {
        params.insert("defaultProviderId".into(), Value::from(provider));
    }
    if let Some(prompt) = system_prompt {
        params.insert("systemPrompt".into(), Value::from(prompt));
    }
    state
        .brain
        .call("lead.spawn", Value::Object(params), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn lead_pause(state: State<'_, AppState>, agent_id: String) -> Result<Value, String> {
    state
        .brain
        .call(
            "lead.pause",
            json!({ "agentId": agent_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn lead_resume(state: State<'_, AppState>, agent_id: String) -> Result<Value, String> {
    state
        .brain
        .call(
            "lead.resume",
            json!({ "agentId": agent_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn lead_archive(state: State<'_, AppState>, agent_id: String) -> Result<Value, String> {
    state
        .brain
        .call(
            "lead.archive",
            json!({ "agentId": agent_id }),
            BRAIN_CALL_TIMEOUT,
        )
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

#[tauri::command]
async fn list_memory(
    state: State<'_, AppState>,
    project_id: Option<String>,
    scopes: Option<Vec<String>>,
    limit: Option<u32>,
) -> Result<Value, String> {
    let mut params = serde_json::Map::new();
    if let Some(project) = project_id {
        params.insert("projectId".into(), Value::from(project));
    }
    if let Some(s) = scopes {
        params.insert("scopes".into(), Value::from(s));
    }
    if let Some(l) = limit {
        params.insert("limit".into(), Value::from(l));
    }
    state
        .brain
        .call("memory.list", Value::Object(params), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn add_memory(
    state: State<'_, AppState>,
    body: String,
    scope: String,
    kind: String,
    author: String,
    project_id: Option<String>,
) -> Result<Value, String> {
    let mut params = serde_json::Map::new();
    params.insert("body".into(), Value::from(body));
    params.insert("scope".into(), Value::from(scope));
    params.insert("kind".into(), Value::from(kind));
    params.insert("author".into(), Value::from(author));
    if let Some(project) = project_id {
        params.insert("projectId".into(), Value::from(project));
    }
    state
        .brain
        .call("memory.add", Value::Object(params), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn update_memory(
    state: State<'_, AppState>,
    memory_id: String,
    body: Option<String>,
    kind: Option<String>,
    scope: Option<String>,
) -> Result<Value, String> {
    let mut params = serde_json::Map::new();
    params.insert("memoryId".into(), Value::from(memory_id));
    if let Some(b) = body {
        params.insert("body".into(), Value::from(b));
    }
    if let Some(k) = kind {
        params.insert("kind".into(), Value::from(k));
    }
    if let Some(s) = scope {
        params.insert("scope".into(), Value::from(s));
    }
    state
        .brain
        .call("memory.update", Value::Object(params), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn delete_memory(state: State<'_, AppState>, memory_id: String) -> Result<Value, String> {
    state
        .brain
        .call(
            "memory.delete",
            json!({ "memoryId": memory_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn lsp_start(state: State<'_, AppState>, language: String) -> Result<Value, String> {
    state
        .brain
        .call(
            "lsp.start",
            json!({ "language": language }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn lsp_send(
    state: State<'_, AppState>,
    session_id: String,
    message: Value,
) -> Result<Value, String> {
    state
        .brain
        .call(
            "lsp.send",
            json!({ "sessionId": session_id, "message": message }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn lsp_stop(state: State<'_, AppState>, session_id: String) -> Result<Value, String> {
    state
        .brain
        .call(
            "lsp.stop",
            json!({ "sessionId": session_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn lsp_list(state: State<'_, AppState>) -> Result<Value, String> {
    state
        .brain
        .call(
            "lsp.list",
            Value::Object(Default::default()),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn browser_start(state: State<'_, AppState>) -> Result<Value, String> {
    let new_state = state.cef.start().await.map_err(|err| err.to_string())?;
    if let crate::cef::HostState::Running { ws_url, .. } = &new_state {
        // Tell the brain to attach to this WS URL. If the attach
        // fails the renderer still gets back the running state — the
        // user can retry, or the next attempt to drive an agent tool
        // will surface the missing attachment.
        if let Err(err) = state
            .brain
            .call(
                "browser.attach",
                json!({ "wsUrl": ws_url }),
                BRAIN_CALL_TIMEOUT,
            )
            .await
        {
            tracing::warn!(?err, "brain failed to attach to browser session");
        }
    }
    serde_json::to_value(new_state).map_err(|err| err.to_string())
}

#[tauri::command]
async fn browser_stop(state: State<'_, AppState>) -> Result<(), String> {
    // Detach the brain first so its CDP socket closes cleanly before
    // we tear the child binary down. We swallow any detach error — if
    // the brain is in a weird state it shouldn't block the stop path.
    let _ = state
        .brain
        .call(
            "browser.detach",
            Value::Object(Default::default()),
            BRAIN_CALL_TIMEOUT,
        )
        .await;
    state.cef.stop().await.map_err(|err| err.to_string())
}

#[tauri::command]
async fn browser_status(state: State<'_, AppState>) -> Result<Value, String> {
    serde_json::to_value(state.cef.state()).map_err(|err| err.to_string())
}

/// Forward the latest drawer-host rect from the renderer to the
/// bundled-CEF host. The OS-specific parenting layer reads the
/// stored rect when applying its native parent/child relationship
/// (`NSWindow.addChildWindow:` on macOS, `SetParent` on Windows,
/// `XReparentWindow` on X11). Idempotent and safe to call before,
/// during, or after a session is running — the rect is held for
/// the next session if no session is live.
#[tauri::command]
async fn cef_set_window_rect(
    state: State<'_, AppState>,
    rect: crate::cef::HostWindowRect,
) -> Result<(), String> {
    state.cef.set_window_rect(rect).await;
    Ok(())
}

#[tauri::command]
async fn terminal_open(
    app: AppHandle,
    state: State<'_, AppState>,
    cwd: Option<String>,
    cols: Option<u16>,
    rows: Option<u16>,
    program: Option<String>,
) -> Result<Value, String> {
    let cwd_path = cwd.map(std::path::PathBuf::from);
    let session_id = state
        .terminals
        .open(program, cwd_path, cols.unwrap_or(80), rows.unwrap_or(24))
        .await
        .map_err(|err| err.to_string())?;

    // Subscribe and forward output to the renderer. The replay
    // snapshot is empty for a fresh session — there is no recent
    // buffer yet — but we still emit an initial event so the
    // renderer knows about the session id from the worker.
    let (mut rx, snapshot) = state
        .terminals
        .subscribe(&session_id)
        .await
        .map_err(|err| err.to_string())?;

    if !snapshot.is_empty() {
        let _ = app.emit(
            TERMINAL_DATA_EVENT,
            json!({ "sessionId": session_id, "seq": 0u64, "data": snapshot }),
        );
    }

    let app_for_task = app.clone();
    let session_id_clone = session_id.clone();
    let brain_for_task = state.brain.clone();
    tauri::async_runtime::spawn(async move {
        loop {
            match rx.recv().await {
                Ok(chunk) => {
                    if let Err(err) = app_for_task.emit(TERMINAL_DATA_EVENT, &chunk) {
                        tracing::warn!(?err, "failed to forward terminal chunk");
                    }
                    // Also push the observation into the brain so
                    // agents can attach to the session.
                    let push = brain_for_task
                        .call(
                            "terminal.observe",
                            json!({
                                "sessionId": chunk.session_id,
                                "seq": chunk.seq,
                                "data": chunk.data,
                            }),
                            BRAIN_CALL_TIMEOUT,
                        )
                        .await;
                    if let Err(err) = push {
                        tracing::debug!(?err, "failed to forward terminal chunk to brain");
                    }
                }
                Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                    tracing::warn!(skipped = n, session_id = %session_id_clone, "terminal subscriber lagged");
                }
                Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
            }
        }
    });

    Ok(json!({ "sessionId": session_id, "snapshot": snapshot }))
}

#[tauri::command]
async fn terminal_input(
    state: State<'_, AppState>,
    session_id: String,
    data: String,
) -> Result<(), String> {
    state
        .terminals
        .write(&session_id, data.as_bytes())
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn terminal_resize(
    state: State<'_, AppState>,
    session_id: String,
    cols: u16,
    rows: u16,
) -> Result<(), String> {
    state
        .terminals
        .resize(&session_id, cols, rows)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn terminal_close(state: State<'_, AppState>, session_id: String) -> Result<Value, String> {
    let closed = state
        .terminals
        .close(&session_id)
        .await
        .map_err(|err| err.to_string())?;
    // Best-effort: tell the brain to drop its observer state.
    let _ = state
        .brain
        .call(
            "terminal.forget",
            json!({ "sessionId": session_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await;
    Ok(json!({ "closed": closed, "sessionId": session_id }))
}

#[tauri::command]
async fn terminal_list(state: State<'_, AppState>) -> Result<Value, String> {
    Ok(json!({ "sessions": state.terminals.list().await }))
}

// ---------------------------------------------------------------------------
// Email accounts
// ---------------------------------------------------------------------------

fn email_secret_key(account_id: &str, slot: &str) -> String {
    format!("email:{account_id}:{slot}")
}

async fn forward_email_credentials(state: &AppState, account_id: &str) -> Result<(), String> {
    let refresh = state
        .secrets
        .read_secret(&email_secret_key(account_id, "refresh_token"))
        .map_err(|err| err.to_string())?
        .unwrap_or_default();
    let client_id = state
        .secrets
        .read_secret(&email_secret_key(account_id, "client_id"))
        .map_err(|err| err.to_string())?
        .unwrap_or_default();
    let client_secret = state
        .secrets
        .read_secret(&email_secret_key(account_id, "client_secret"))
        .map_err(|err| err.to_string())?
        .unwrap_or_default();
    state
        .brain
        .call(
            "email.set_credentials",
            json!({
                "accountId": account_id,
                "refreshToken": refresh,
                "clientId": client_id,
                "clientSecret": client_secret,
            }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())?;
    Ok(())
}

#[tauri::command]
async fn email_list_accounts(state: State<'_, AppState>) -> Result<Value, String> {
    state
        .brain
        .call("email.list_accounts", json!({}), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn email_add_account(
    state: State<'_, AppState>,
    provider: String,
    label: String,
    address: String,
) -> Result<Value, String> {
    if !matches!(provider.as_str(), "gmail" | "microsoft") {
        return Err(format!("unsupported provider: {provider}"));
    }
    state
        .brain
        .call(
            "email.add_account",
            json!({
                "provider": provider,
                "label": label,
                "address": address,
            }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn email_remove_account(
    state: State<'_, AppState>,
    account_id: String,
) -> Result<Value, String> {
    let _ = state
        .secrets
        .delete_secret(&email_secret_key(&account_id, "refresh_token"));
    let _ = state
        .secrets
        .delete_secret(&email_secret_key(&account_id, "client_id"));
    let _ = state
        .secrets
        .delete_secret(&email_secret_key(&account_id, "client_secret"));
    let _ = state
        .brain
        .call(
            "email.clear_credentials",
            json!({ "accountId": account_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await;
    state
        .brain
        .call(
            "email.remove_account",
            json!({ "accountId": account_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
async fn email_save_credentials(
    state: State<'_, AppState>,
    account_id: String,
    refresh_token: Option<String>,
    client_id: Option<String>,
    client_secret: Option<String>,
) -> Result<(), String> {
    if let Some(value) = refresh_token.as_ref().filter(|v| !v.trim().is_empty()) {
        state
            .secrets
            .save_secret(
                &email_secret_key(&account_id, "refresh_token"),
                value.trim(),
            )
            .map_err(|err| err.to_string())?;
    }
    if let Some(value) = client_id.as_ref().filter(|v| !v.trim().is_empty()) {
        state
            .secrets
            .save_secret(&email_secret_key(&account_id, "client_id"), value.trim())
            .map_err(|err| err.to_string())?;
    }
    if let Some(value) = client_secret.as_ref() {
        // Allow empty client_secret (public OAuth clients) but clear
        // it when the user passes "" to avoid stale values lingering.
        if value.trim().is_empty() {
            let _ = state
                .secrets
                .delete_secret(&email_secret_key(&account_id, "client_secret"));
        } else {
            state
                .secrets
                .save_secret(
                    &email_secret_key(&account_id, "client_secret"),
                    value.trim(),
                )
                .map_err(|err| err.to_string())?;
        }
    }
    forward_email_credentials(&state, &account_id).await?;
    Ok(())
}

#[tauri::command]
async fn email_credentials_status(
    state: State<'_, AppState>,
    account_id: String,
) -> Result<Value, String> {
    Ok(json!({
        "refreshTokenConfigured": state
            .secrets
            .has_secret(&email_secret_key(&account_id, "refresh_token")),
        "clientIdConfigured": state
            .secrets
            .has_secret(&email_secret_key(&account_id, "client_id")),
        "clientSecretConfigured": state
            .secrets
            .has_secret(&email_secret_key(&account_id, "client_secret")),
    }))
}

#[tauri::command]
async fn email_list_messages(
    state: State<'_, AppState>,
    account_id: String,
    query: Option<String>,
    page_token: Option<String>,
    max_results: Option<u32>,
) -> Result<Value, String> {
    forward_email_credentials(&state, &account_id).await?;
    let mut params = serde_json::Map::new();
    params.insert("accountId".into(), Value::from(account_id));
    if let Some(q) = query {
        params.insert("query".into(), Value::from(q));
    }
    if let Some(pt) = page_token {
        params.insert("pageToken".into(), Value::from(pt));
    }
    if let Some(mr) = max_results {
        params.insert("maxResults".into(), Value::from(mr));
    }
    state
        .brain
        .call(
            "email.list_messages",
            Value::Object(params),
            Duration::from_secs(60),
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn email_get_message(
    state: State<'_, AppState>,
    account_id: String,
    message_id: String,
) -> Result<Value, String> {
    forward_email_credentials(&state, &account_id).await?;
    state
        .brain
        .call(
            "email.get_message",
            json!({ "accountId": account_id, "messageId": message_id }),
            Duration::from_secs(60),
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
async fn email_create_draft(
    state: State<'_, AppState>,
    account_id: String,
    to: Vec<String>,
    cc: Option<Vec<String>>,
    bcc: Option<Vec<String>>,
    subject: String,
    body: String,
    in_reply_to: Option<String>,
) -> Result<Value, String> {
    let mut params = serde_json::Map::new();
    params.insert("accountId".into(), Value::from(account_id));
    params.insert("to".into(), Value::from(to));
    params.insert("cc".into(), Value::from(cc.unwrap_or_default()));
    params.insert("bcc".into(), Value::from(bcc.unwrap_or_default()));
    params.insert("subject".into(), Value::from(subject));
    params.insert("body".into(), Value::from(body));
    if let Some(reply) = in_reply_to {
        params.insert("inReplyTo".into(), Value::from(reply));
    }
    state
        .brain
        .call(
            "email.create_draft",
            Value::Object(params),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn email_list_drafts(state: State<'_, AppState>) -> Result<Value, String> {
    state
        .brain
        .call("email.list_drafts", json!({}), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn email_discard_draft(
    state: State<'_, AppState>,
    draft_id: String,
) -> Result<Value, String> {
    state
        .brain
        .call(
            "email.discard_draft",
            json!({ "draftId": draft_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn email_approve_draft(
    state: State<'_, AppState>,
    draft_id: String,
) -> Result<Value, String> {
    state
        .brain
        .call(
            "email.approve_draft",
            json!({ "draftId": draft_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn email_send_draft(state: State<'_, AppState>, draft_id: String) -> Result<Value, String> {
    // The brain enforces the hard gate, but the renderer must
    // surface it as a confirm modal first; this command is the
    // only path to send and is documented as user-driven only.
    state
        .brain
        .call(
            "email.send_draft",
            json!({ "draftId": draft_id }),
            Duration::from_secs(60),
        )
        .await
        .map_err(|err| err.to_string())
}

// ---------------------------------------------------------------------------
// MCP connectors
// ---------------------------------------------------------------------------

fn mcp_secret_key(connector_id: &str, secret_key: &str) -> String {
    format!("mcp:{connector_id}:{secret_key}")
}

fn ensure_safe_id(value: &str, label: &str) -> Result<(), String> {
    if value.is_empty() {
        return Err(format!("{label} is empty"));
    }
    if !value
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return Err(format!("{label} must be alphanumeric, '_' or '-'"));
    }
    Ok(())
}

#[tauri::command]
async fn mcp_catalog(state: State<'_, AppState>) -> Result<Value, String> {
    state
        .brain
        .call("mcp.catalog", json!({}), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn mcp_list(state: State<'_, AppState>) -> Result<Value, String> {
    state
        .brain
        .call("mcp.list", json!({}), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn mcp_install(
    state: State<'_, AppState>,
    connector_id: String,
    granted_tools: Option<Vec<String>>,
) -> Result<Value, String> {
    ensure_safe_id(&connector_id, "connectorId")?;
    let mut params = serde_json::Map::new();
    params.insert("connectorId".into(), Value::from(connector_id));
    if let Some(grants) = granted_tools {
        params.insert("grantedTools".into(), Value::from(grants));
    }
    state
        .brain
        .call("mcp.install", Value::Object(params), BRAIN_CALL_TIMEOUT)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn mcp_uninstall(state: State<'_, AppState>, connector_id: String) -> Result<Value, String> {
    ensure_safe_id(&connector_id, "connectorId")?;
    state
        .brain
        .call(
            "mcp.uninstall",
            json!({ "connectorId": connector_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn mcp_set_grants(
    state: State<'_, AppState>,
    connector_id: String,
    granted_tools: Vec<String>,
) -> Result<Value, String> {
    ensure_safe_id(&connector_id, "connectorId")?;
    state
        .brain
        .call(
            "mcp.set_grants",
            json!({ "connectorId": connector_id, "grantedTools": granted_tools }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn mcp_set_enabled(
    state: State<'_, AppState>,
    connector_id: String,
    enabled: bool,
) -> Result<Value, String> {
    ensure_safe_id(&connector_id, "connectorId")?;
    state
        .brain
        .call(
            "mcp.set_enabled",
            json!({ "connectorId": connector_id, "enabled": enabled }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn mcp_save_secret(
    state: State<'_, AppState>,
    connector_id: String,
    secret_key: String,
    value: String,
) -> Result<(), String> {
    ensure_safe_id(&connector_id, "connectorId")?;
    ensure_safe_id(&secret_key, "secretKey")?;
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("secret value is empty".into());
    }
    state
        .secrets
        .save_secret(&mcp_secret_key(&connector_id, &secret_key), trimmed)
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn mcp_clear_secret(
    state: State<'_, AppState>,
    connector_id: String,
    secret_key: String,
) -> Result<(), String> {
    ensure_safe_id(&connector_id, "connectorId")?;
    ensure_safe_id(&secret_key, "secretKey")?;
    state
        .secrets
        .delete_secret(&mcp_secret_key(&connector_id, &secret_key))
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn mcp_secret_status(
    state: State<'_, AppState>,
    connector_id: String,
    secret_keys: Vec<String>,
) -> Result<Value, String> {
    ensure_safe_id(&connector_id, "connectorId")?;
    let mut entries = serde_json::Map::new();
    for key in secret_keys {
        ensure_safe_id(&key, "secretKey")?;
        let configured = state
            .secrets
            .has_secret(&mcp_secret_key(&connector_id, &key));
        entries.insert(key, Value::from(configured));
    }
    Ok(Value::Object(entries))
}

#[tauri::command]
async fn mcp_start(
    state: State<'_, AppState>,
    connector_id: String,
    secret_keys: Vec<String>,
) -> Result<Value, String> {
    ensure_safe_id(&connector_id, "connectorId")?;
    let mut secrets = serde_json::Map::new();
    for key in &secret_keys {
        ensure_safe_id(key, "secretKey")?;
        match state
            .secrets
            .read_secret(&mcp_secret_key(&connector_id, key))
        {
            Ok(Some(value)) => {
                secrets.insert(key.clone(), Value::from(value));
            }
            Ok(None) => {
                return Err(format!(
                    "secret '{key}' is not configured for connector '{connector_id}'"
                ));
            }
            Err(err) => return Err(err.to_string()),
        }
    }
    state
        .brain
        .call(
            "mcp.start",
            json!({ "connectorId": connector_id, "secrets": Value::Object(secrets) }),
            // Spawning an MCP server (npx download, etc.) can be
            // slow on first run. Give it a generous deadline.
            Duration::from_secs(120),
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn mcp_stop(state: State<'_, AppState>, connector_id: String) -> Result<Value, String> {
    ensure_safe_id(&connector_id, "connectorId")?;
    state
        .brain
        .call(
            "mcp.stop",
            json!({ "connectorId": connector_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn mcp_list_tools(state: State<'_, AppState>, connector_id: String) -> Result<Value, String> {
    ensure_safe_id(&connector_id, "connectorId")?;
    state
        .brain
        .call(
            "mcp.list_tools",
            json!({ "connectorId": connector_id }),
            BRAIN_CALL_TIMEOUT,
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn mcp_call_tool(
    state: State<'_, AppState>,
    connector_id: String,
    tool_name: String,
    arguments: Option<Value>,
) -> Result<Value, String> {
    ensure_safe_id(&connector_id, "connectorId")?;
    if tool_name.trim().is_empty() {
        return Err("toolName is empty".into());
    }
    state
        .brain
        .call(
            "mcp.call_tool",
            json!({
                "connectorId": connector_id,
                "toolName": tool_name,
                "arguments": arguments.unwrap_or_else(|| json!({})),
            }),
            // Tool calls can be slow (network calls to upstream APIs).
            Duration::from_secs(120),
        )
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
async fn inline_suggest(
    state: State<'_, AppState>,
    provider_id: String,
    prefix: String,
    suffix: Option<String>,
    language: Option<String>,
    request_id: Option<String>,
) -> Result<Value, String> {
    let mut params = serde_json::Map::new();
    params.insert("providerId".into(), Value::from(provider_id));
    params.insert("prefix".into(), Value::from(prefix));
    if let Some(s) = suffix {
        params.insert("suffix".into(), Value::from(s));
    }
    if let Some(lang) = language {
        params.insert("language".into(), Value::from(lang));
    }
    if let Some(rid) = request_id {
        params.insert("requestId".into(), Value::from(rid));
    }
    state
        .brain
        .call("inline.suggest", Value::Object(params), CHAT_DEADLINE)
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
        "lead.escalation" => LEAD_ESCALATION_EVENT,
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
    lead_id: Option<String>,
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
    if let Some(id) = lead_id {
        params["leadId"] = Value::String(id);
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
        lead_id: result
            .get("leadId")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string()),
    })
}

/// Spawn the brain sidecar during app setup. Failure here surfaces as a
/// startup error rather than crashing the app.
async fn init_app_state(app: &AppHandle) -> Result<(), String> {
    let secrets = Arc::new(SecretsManager::with_default_store());
    let registry = ProviderRegistry::new(builtin_providers(secrets.has_api_key("anthropic")));

    let mut config = SpawnConfig::dev_default();
    // Forward the canonical data directory so the brain and the Rust
    // core agree on where state lives. Per ADR-0028, the brain owns
    // every SQLite store; this env var is the single knob both
    // processes consult.
    let data_dir = data_dir::resolve();
    config = config.with_env("THALYN_DATA_DIR", data_dir.to_string_lossy().to_string());
    if let Ok(Some(api_key)) = secrets.read_api_key("anthropic") {
        // Forward the key into the spawn env so the Claude Agent SDK
        // can authenticate without anything touching disk.
        config = config.with_env("ANTHROPIC_API_KEY", api_key);
    }
    // User-supplied Sentry DSN (opt-in crash reporting per F10.3).
    // Forwarded only when configured; without this env var the
    // brain's init_sentry is a no-op and nothing leaves the machine.
    if let Ok(Some(dsn)) = secrets.read_secret("sentry_dsn") {
        config = config.with_env("THALYN_SENTRY_DSN", dsn);
    }
    // OTLP endpoint for the OpenTelemetry exporter — same shape;
    // unset means "no traces leave the machine."
    if let Ok(Some(endpoint)) = secrets.read_secret("otel_otlp_endpoint") {
        config = config.with_env("THALYN_OTEL_OTLP_ENDPOINT", endpoint);
    }
    tracing::info!(program = %config.program, args = ?config.args, "spawning brain sidecar");
    let supervisor = BrainSupervisor::spawn(config)
        .await
        .map_err(|err| format!("brain sidecar failed to start: {err}"))?;

    let supervisor = Arc::new(supervisor);
    spawn_global_notification_forwarder(app.clone(), supervisor.clone());

    // CEF profile lives under the same canonical root as the brain's
    // SQLite stores so all on-disk state shares one directory (per
    // ADR-0028). `data_dir` was resolved above and forwarded to the
    // brain via `THALYN_DATA_DIR`; before this change the profile
    // sat under Tauri's bundle-id'd `app_data_dir()`, one level up.
    let profile_root = data_dir.join("cef-profile");

    app.manage(AppState {
        brain: supervisor,
        providers: RwLock::new(registry),
        secrets,
        sandboxes: Arc::new(SandboxManager::new()),
        browser: Arc::new(BrowserManager::new(profile_root.clone())),
        cef: Arc::new(CefHost::new(profile_root)),
        power: Arc::new(PowerManager::new()),
        assertions: Arc::new(Mutex::new(HashMap::new())),
        terminals: Arc::new(TerminalManager::new()),
    });
    Ok(())
}

/// Subscribe to brain notifications and forward the long-lived
/// streams (LSP today, terminal next) to renderer events. Per-call
/// streams (chat, approve_plan) keep their existing routing through
/// `forward_brain_notification` — that path remains the way to bind
/// notifications to a specific session id.
fn spawn_global_notification_forwarder(app: AppHandle, brain: Arc<BrainSupervisor>) {
    let mut subscriber = brain.subscribe_notifications();
    tauri::async_runtime::spawn(async move {
        loop {
            match subscriber.recv().await {
                Ok(notification) => {
                    let event = match notification.method.as_ref() {
                        "lsp.message" => LSP_MESSAGE_EVENT,
                        "lsp.error" => LSP_ERROR_EVENT,
                        _ => continue,
                    };
                    if let Err(err) = app.emit(event, notification.params.as_ref().clone()) {
                        tracing::warn!(?err, %event, "failed to forward brain notification");
                    }
                }
                Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                    tracing::warn!(skipped = n, "brain notification subscriber lagged");
                }
                Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
            }
        }
    });
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
            // Tauri has built its EventLoop at this point — tao has
            // registered `TaoApp` as an `NSApplication` subclass and
            // `[NSApp sharedApplication]` has locked it in as the
            // principal class — but the run loop has not yet spun.
            // This is the only safe window to graft CEF's NSApp
            // protocol contracts onto `TaoApp` (ADR-0029) before the
            // engine starts driving events.
            #[cfg(feature = "cef")]
            crate::cef::embed::runtime::install_swizzle_inside_setup_hook();

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
            provider_delta,
            auth_list,
            auth_probe,
            auth_set,
            thread_recent,
            digest_latest,
            lead_list,
            lead_spawn,
            lead_pause,
            lead_resume,
            lead_archive,
            list_memory,
            add_memory,
            update_memory,
            delete_memory,
            lsp_start,
            lsp_send,
            lsp_stop,
            lsp_list,
            inline_suggest,
            browser_start,
            browser_stop,
            browser_status,
            cef_set_window_rect,
            save_observability_secret,
            clear_observability_secret,
            observability_status,
            terminal_open,
            terminal_input,
            terminal_resize,
            terminal_close,
            terminal_list,
            mcp_catalog,
            mcp_list,
            mcp_install,
            mcp_uninstall,
            mcp_set_grants,
            mcp_set_enabled,
            mcp_save_secret,
            mcp_clear_secret,
            mcp_secret_status,
            mcp_start,
            mcp_stop,
            mcp_list_tools,
            mcp_call_tool,
            email_list_accounts,
            email_add_account,
            email_remove_account,
            email_save_credentials,
            email_credentials_status,
            email_list_messages,
            email_get_message,
            email_create_draft,
            email_list_drafts,
            email_discard_draft,
            email_approve_draft,
            email_send_draft,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
