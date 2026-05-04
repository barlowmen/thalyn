//! STT engine trait.
//!
//! Three engine kinds are planned for v0.33: the local default
//! ([`EngineKind::LocalWhisper`], `whisper-cpp-plus`); the opt-in
//! cloud fallback ([`EngineKind::Cloud`], Deepgram Nova-3); and the
//! Apple-Silicon power-user opt-in ([`EngineKind::Mlx`],
//! MLX-Whisper). All three sit behind the same trait so the
//! [`super::VoiceManager`] can swap between them on a settings
//! flip without re-wiring the IPC surface.
//!
//! [`NoopEngine`] is the placeholder shipped alongside the seam —
//! it accepts every audio frame, returns an empty transcript on
//! finalise, and lets the rest of the wire (Tauri commands, brain
//! RPC, renderer events) land against a stable shape before the
//! real backends arrive.

use async_trait::async_trait;
use tokio::sync::broadcast;

use super::manager::{LevelEvent, SessionId, Transcript, VoiceError};

/// Which backend a session uses. Persisted with the session so
/// observers can disambiguate transcripts in mixed-mode tests.
///
/// Variants are listed up front so the routing surface lands once;
/// only [`Noop`] is constructed in this scaffolding commit. Real
/// backends fill the others in: `whisper-cpp-plus` for
/// [`LocalWhisper`], Deepgram Nova-3 for [`Cloud`], MLX-Whisper for
/// [`Mlx`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[allow(dead_code)]
pub enum EngineKind {
    /// Whisper.cpp via the `whisper-cpp-plus` Rust crate (ADR-0025).
    LocalWhisper,
    /// Deepgram Nova-3 streaming WebSocket (opt-in cloud fallback).
    Cloud,
    /// MLX-Whisper on Apple Silicon (opt-in power-user alternative).
    Mlx,
    /// Placeholder used by tests and by the seam-only build before the
    /// real backends land.
    Noop,
}

/// Per-session inputs the manager hands the engine when a recording
/// starts. The vocabulary slice biases Whisper's `initial_prompt` so
/// the model recognises project-specific terminology — the EM
/// metaphor cashing out in the voice path (spike F7). The
/// [`NoopEngine`] ignores both fields; later engines consume them.
///
/// `continuous` flips the engine to VAD-driven segmentation: each
/// utterance ends when an energy-based silence threshold is met,
/// the engine finalises just that utterance via
/// [`InterimSink::send_final`], and the session keeps listening
/// for the next one. Off by default — push-to-talk is the
/// composer's primary surface.
#[derive(Debug, Clone, Default)]
pub struct StartConfig {
    pub project_id: Option<String>,
    #[allow(dead_code)]
    pub vocabulary: ProjectVocabulary,
    #[allow(dead_code)]
    pub continuous: bool,
}

/// Project-derived terminology hints. Populated by the brain's
/// `voice.project_vocabulary` RPC (THALYN.md identifiers + memory
/// facts) and forwarded to the engine via [`StartConfig`].
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ProjectVocabulary {
    pub terms: Vec<String>,
}

/// Channel an engine pushes interim transcripts down. The manager
/// constructs one per session — the engine only sees `send_interim`
/// and never has to think about routing or session correlation.
///
/// Cheap to clone (it's a wrapper around a `tokio::sync::broadcast`
/// `Sender` + a `SessionId`), so engines that hand the sink to a
/// worker thread can clone freely.
///
/// `dead_code` is allowed because the lean (non-`voice-whisper`)
/// build only ever runs the [`NoopEngine`], which never emits
/// interim transcripts; the type still has to exist so the
/// `Engine::begin` signature compiles on every target.
#[allow(dead_code)]
#[derive(Clone)]
pub struct InterimSink {
    sender: broadcast::Sender<Transcript>,
    session_id: SessionId,
}

#[allow(dead_code)]
impl InterimSink {
    pub(super) fn new(sender: broadcast::Sender<Transcript>, session_id: SessionId) -> Self {
        Self { sender, session_id }
    }

    /// Emit an interim transcript. Send failures (no subscribers,
    /// channel lagged) are swallowed — interim transcripts are best-
    /// effort and a missing one isn't fatal to the session.
    pub fn send_interim(&self, text: String) {
        let _ = self.sender.send(Transcript {
            session_id: self.session_id.clone(),
            text,
            is_final: false,
        });
    }

    /// Emit a final transcript without ending the session. Used by
    /// the continuous-listen path to publish each completed
    /// utterance — the renderer's continuous-mode handler treats
    /// these as auto-submit triggers, while push-to-talk sessions
    /// only ever see one final via the `Engine::finish` return path.
    pub fn send_final(&self, text: String) {
        let _ = self.sender.send(Transcript {
            session_id: self.session_id.clone(),
            text,
            is_final: true,
        });
    }
}

/// Channel the mic-capture path pushes peak-amplitude samples down.
/// One sink per session; the cpal callback owns a clone and emits
/// per-chunk peaks straight into the manager's broadcast channel.
///
/// `dead_code` is allowed for the same reason as [`InterimSink`] —
/// the lean (non-`voice-whisper`) build still wires the type but
/// the noop path doesn't run mic capture.
#[allow(dead_code)]
#[derive(Clone)]
pub struct LevelSink {
    sender: broadcast::Sender<LevelEvent>,
    session_id: SessionId,
}

#[allow(dead_code)]
impl LevelSink {
    pub(super) fn new(sender: broadcast::Sender<LevelEvent>, session_id: SessionId) -> Self {
        Self { sender, session_id }
    }

    /// Emit one peak-amplitude sample. Send failures are swallowed —
    /// level events are best-effort animation fuel, not state, so a
    /// missing one is harmless.
    pub fn emit(&self, peak: f32) {
        let _ = self.sender.send(LevelEvent {
            session_id: self.session_id.clone(),
            peak,
        });
    }
}

/// What every backend must implement. Engines are stateless across
/// sessions — per-session state lives in the [`super::Session`] the
/// manager owns; the engine is purely the decoder.
#[async_trait]
pub trait Engine: Send + Sync {
    /// Identifier the manager records in [`Session`] for observability.
    fn kind(&self) -> EngineKind;

    /// Open a decoder context for a new session. The default impl
    /// returns no per-session state — engines that need to load a
    /// model context override this. The `interim` sink lets the
    /// engine push live transcripts back through the manager's
    /// broadcast channel as it decodes.
    async fn begin(
        &self,
        _session_id: &SessionId,
        _config: &StartConfig,
        _interim: InterimSink,
    ) -> Result<(), VoiceError> {
        Ok(())
    }

    /// Process an interim PCM frame. The seam-only [`NoopEngine`]
    /// drops the frame; real engines decode and emit interim
    /// transcripts via the manager's broadcast channel.
    async fn feed(&self, _session_id: &SessionId, _pcm: &[i16]) -> Result<(), VoiceError> {
        Ok(())
    }

    /// Finalise a session and return the final transcript. Engines
    /// release any per-session state here.
    async fn finish(&self, _session_id: &SessionId) -> Result<Transcript, VoiceError>;
}

/// Default engine used until the real backends land. Returns an
/// empty final transcript on every session — the wire shape
/// (Tauri commands + brain RPC) is what this commit ships.
pub struct NoopEngine;

#[async_trait]
impl Engine for NoopEngine {
    fn kind(&self) -> EngineKind {
        EngineKind::Noop
    }

    async fn finish(&self, _session_id: &SessionId) -> Result<Transcript, VoiceError> {
        Ok(Transcript::final_empty())
    }
}
