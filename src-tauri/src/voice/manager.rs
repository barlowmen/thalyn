//! Voice session manager.
//!
//! Owns the in-flight [`Session`] map, dispatches audio frames to
//! the active [`super::Engine`], and broadcasts [`Transcript`]
//! updates so the renderer (via Tauri events) and the brain (via a
//! later JSON-RPC notification) can both observe one stream.
//!
//! Sessions are identified by a `stt_<uuid>` string assigned at
//! [`VoiceManager::start`]. Audio frames pushed via
//! [`VoiceManager::feed_chunk`] forward straight to the engine —
//! the manager carries no PCM buffer of its own. Finalisation goes
//! through [`VoiceManager::finish`], which removes the session
//! from the map and returns the engine's final transcript.

use std::collections::HashMap;
use std::sync::Arc;

use serde::Serialize;
use thiserror::Error;
use tokio::sync::{broadcast, Mutex};
use uuid::Uuid;

use super::engine::{Engine, EngineKind, NoopEngine, StartConfig};

const TRANSCRIPT_BUFFER: usize = 256;

/// Errors surfaced to Tauri commands.
#[derive(Debug, Error)]
pub enum VoiceError {
    #[error("unknown voice session: {0}")]
    UnknownSession(String),
    #[error("voice engine failure: {0}")]
    Engine(String),
}

/// Stable session identifier. Wraps a string so callers can pattern-
/// match on it without leaking the underlying uuid format.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize)]
pub struct SessionId(String);

impl SessionId {
    fn new() -> Self {
        Self(format!("stt_{}", Uuid::new_v4().simple()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for SessionId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl From<&str> for SessionId {
    fn from(value: &str) -> Self {
        Self(value.to_owned())
    }
}

/// One transcript update — interim or final. Real engines emit a
/// stream of these (interim updates as the user speaks, then one
/// `is_final = true` at the end); the seam-only [`NoopEngine`]
/// emits only the final shape.
#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Transcript {
    pub session_id: SessionId,
    pub text: String,
    pub is_final: bool,
}

impl Transcript {
    /// Used by the seam-only NoopEngine where the session ID isn't
    /// known until the manager wraps the result. Streaming engines
    /// will land their own constructors for interim and final
    /// transcripts in commit 2.
    pub fn final_empty() -> Self {
        Self {
            session_id: SessionId(String::new()),
            text: String::new(),
            is_final: true,
        }
    }
}

/// Per-session bookkeeping. Engines are stateless across sessions;
/// anything session-scoped lives here. The fields are read by tests
/// today; commit 2 surfaces them through observability spans and
/// the cloud / MLX routing decisions.
#[allow(dead_code)]
pub struct Session {
    pub id: SessionId,
    pub engine: EngineKind,
    pub project_id: Option<String>,
}

/// Public manager — Tauri commands talk to this.
pub struct VoiceManager {
    sessions: Mutex<HashMap<SessionId, Arc<Session>>>,
    transcripts: broadcast::Sender<Transcript>,
    engine: Arc<dyn Engine>,
}

impl VoiceManager {
    /// Build a manager with the given engine. Lib.rs constructs one
    /// `VoiceManager` at startup and stores it on `AppState`.
    pub fn new(engine: Arc<dyn Engine>) -> Self {
        let (transcripts, _) = broadcast::channel(TRANSCRIPT_BUFFER);
        Self {
            sessions: Mutex::new(HashMap::new()),
            transcripts,
            engine,
        }
    }

    /// Default seam-only manager — the [`NoopEngine`] returns empty
    /// transcripts. Real engines replace this in later commits.
    pub fn with_noop_engine() -> Self {
        Self::new(Arc::new(NoopEngine))
    }

    /// Subscribe to the transcript stream. Lib.rs spawns one
    /// forwarder task at startup that re-emits every transcript as
    /// a `stt:transcript` Tauri event.
    pub fn subscribe(&self) -> broadcast::Receiver<Transcript> {
        self.transcripts.subscribe()
    }

    /// Start a new session and return its id.
    pub async fn start(&self, config: StartConfig) -> Result<SessionId, VoiceError> {
        let id = SessionId::new();
        self.engine
            .begin(&id, &config)
            .await
            .map_err(|err| VoiceError::Engine(err.to_string()))?;

        let session = Arc::new(Session {
            id: id.clone(),
            engine: self.engine.kind(),
            project_id: config.project_id,
        });
        self.sessions.lock().await.insert(id.clone(), session);
        Ok(id)
    }

    /// Push a PCM frame into an existing session. Sample rate
    /// expectation matches Whisper.cpp: 16 kHz mono int16. Real
    /// engines may emit interim transcripts via the broadcast
    /// channel as a side effect.
    pub async fn feed_chunk(&self, session_id: &SessionId, pcm: &[i16]) -> Result<(), VoiceError> {
        if !self.sessions.lock().await.contains_key(session_id) {
            return Err(VoiceError::UnknownSession(session_id.to_string()));
        }
        self.engine
            .feed(session_id, pcm)
            .await
            .map_err(|err| VoiceError::Engine(err.to_string()))
    }

    /// Finalise a session and return the final transcript. Removes
    /// the session from the map; subsequent feed/finish calls
    /// surface [`VoiceError::UnknownSession`].
    pub async fn finish(&self, session_id: &SessionId) -> Result<Transcript, VoiceError> {
        let removed = self.sessions.lock().await.remove(session_id);
        if removed.is_none() {
            return Err(VoiceError::UnknownSession(session_id.to_string()));
        }
        let mut transcript = self
            .engine
            .finish(session_id)
            .await
            .map_err(|err| VoiceError::Engine(err.to_string()))?;
        // Engines may return Transcript::final_empty() with a placeholder
        // session id; the manager stamps the real one before broadcast
        // so subscribers can correlate.
        transcript.session_id = session_id.clone();
        let _ = self.transcripts.send(transcript.clone());
        Ok(transcript)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn start_assigns_a_unique_session_id() {
        let manager = VoiceManager::with_noop_engine();
        let a = manager.start(StartConfig::default()).await.unwrap();
        let b = manager.start(StartConfig::default()).await.unwrap();
        assert_ne!(a, b);
        assert!(a.as_str().starts_with("stt_"));
    }

    #[tokio::test]
    async fn feed_chunk_succeeds_for_an_open_session() {
        let manager = VoiceManager::with_noop_engine();
        let id = manager.start(StartConfig::default()).await.unwrap();
        let pcm = vec![0_i16; 1600];
        manager.feed_chunk(&id, &pcm).await.unwrap();
    }

    #[tokio::test]
    async fn feed_chunk_errors_after_finish() {
        let manager = VoiceManager::with_noop_engine();
        let id = manager.start(StartConfig::default()).await.unwrap();
        manager.finish(&id).await.unwrap();
        let err = manager.feed_chunk(&id, &[]).await.unwrap_err();
        assert!(matches!(err, VoiceError::UnknownSession(_)));
    }

    #[tokio::test]
    async fn finish_returns_a_final_transcript_and_broadcasts_it() {
        let manager = VoiceManager::with_noop_engine();
        let mut rx = manager.subscribe();
        let id = manager.start(StartConfig::default()).await.unwrap();
        let transcript = manager.finish(&id).await.unwrap();
        assert!(transcript.is_final);
        assert_eq!(transcript.session_id, id);

        let broadcast = rx.recv().await.unwrap();
        assert_eq!(broadcast.session_id, id);
        assert!(broadcast.is_final);
    }

    #[tokio::test]
    async fn finish_errors_when_session_unknown() {
        let manager = VoiceManager::with_noop_engine();
        let err = manager
            .finish(&SessionId::from("stt_missing"))
            .await
            .unwrap_err();
        assert!(matches!(err, VoiceError::UnknownSession(_)));
    }

    #[tokio::test]
    async fn start_records_the_engine_kind_and_project_id() {
        let manager = VoiceManager::with_noop_engine();
        let id = manager
            .start(StartConfig {
                project_id: Some("proj_alpha".into()),
                ..Default::default()
            })
            .await
            .unwrap();
        let sessions = manager.sessions.lock().await;
        let session = sessions.get(&id).unwrap();
        assert_eq!(session.engine, EngineKind::Noop);
        assert_eq!(session.project_id.as_deref(), Some("proj_alpha"));
    }
}
