//! Local Whisper.cpp STT engine.
//!
//! Uses the `whisper-cpp-plus` crate to run inference in-process
//! against a `.bin` GGML model file. The engine is feature-gated on
//! `voice-whisper`; the lean build (`--no-default-features`) drops
//! the dep entirely and falls back to the [`super::engine::NoopEngine`]
//! at startup.
//!
//! Per-session shape: [`Engine::begin`] allocates a PCM buffer for
//! the session, [`Engine::feed`] appends int16 frames to it, and
//! [`Engine::finish`] runs `transcribe_with_params` over the
//! accumulated buffer and returns the joined transcript. This is
//! the batch-on-stop path; streaming interim transcripts (via
//! `WhisperStream` / `WhisperStreamPcm`) land alongside the
//! continuous-listen surface in a later commit.
//!
//! Apple Silicon Core ML acceleration is not yet exposed by
//! `whisper-cpp-plus` 0.1.4. The going-public-checklist tracks the
//! upstream-PR-vs-in-tree-patch decision; the engine works without
//! Core ML today (Metal still hits the latency budget on Apple
//! Silicon — see ADR-0025).

#![cfg(feature = "voice-whisper")]

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use async_trait::async_trait;
use thiserror::Error;
use tokio::sync::Mutex;
use whisper_cpp_plus::{TranscriptionParams, WhisperContext};

use super::engine::{Engine, EngineKind, StartConfig};
use super::manager::{SessionId, Transcript, VoiceError};

/// 16 kHz mono int16 is the format Whisper expects. The renderer
/// captures at this rate (cpal in a later commit); the manager
/// passes frames through unchanged.
const SAMPLE_RATE: u32 = 16_000;

/// Errors surfaced when the engine itself can't be constructed or
/// model inference fails.
#[derive(Debug, Error)]
pub enum LocalEngineError {
    #[error("whisper model not found at {0}")]
    ModelNotFound(PathBuf),
    #[error("failed to load whisper model: {0}")]
    ModelLoad(String),
    #[error("failed to run whisper inference: {0}")]
    Inference(String),
}

impl From<LocalEngineError> for VoiceError {
    fn from(err: LocalEngineError) -> Self {
        VoiceError::Engine(err.to_string())
    }
}

/// Per-session PCM accumulator. Whisper.cpp wants the full buffer
/// in float32 form at decode time; we keep int16 for memory
/// efficiency during recording and convert in [`Engine::finish`].
struct SessionBuffer {
    pcm: Vec<i16>,
    initial_prompt: Option<String>,
}

/// Local Whisper engine. One instance is constructed at startup
/// when a model file is present; the [`WhisperContext`] is shared
/// across sessions (whisper.cpp itself is thread-safe for the
/// `transcribe` call shape we use).
pub struct LocalWhisperEngine {
    context: Arc<WhisperContext>,
    model_path: PathBuf,
    sessions: Mutex<HashMap<SessionId, SessionBuffer>>,
}

impl std::fmt::Debug for LocalWhisperEngine {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LocalWhisperEngine")
            .field("model_path", &self.model_path)
            .finish_non_exhaustive()
    }
}

impl LocalWhisperEngine {
    /// Try to load a model from `model_path`. Returns
    /// [`LocalEngineError::ModelNotFound`] if the path doesn't
    /// resolve to a file — lib.rs treats that as a soft failure
    /// and falls back to the NoopEngine until a model is present.
    pub fn try_load(model_path: impl Into<PathBuf>) -> Result<Self, LocalEngineError> {
        let model_path = model_path.into();
        if !model_path.is_file() {
            return Err(LocalEngineError::ModelNotFound(model_path));
        }
        let context = WhisperContext::new(&model_path)
            .map_err(|err| LocalEngineError::ModelLoad(err.to_string()))?;
        Ok(Self {
            context: Arc::new(context),
            model_path,
            sessions: Mutex::new(HashMap::new()),
        })
    }

    /// Read-only view of the loaded model's path. Used by tests and
    /// by the startup log line that records which model the engine
    /// actually picked up.
    pub fn model_path(&self) -> &Path {
        &self.model_path
    }
}

#[async_trait]
impl Engine for LocalWhisperEngine {
    fn kind(&self) -> EngineKind {
        EngineKind::LocalWhisper
    }

    async fn begin(&self, session_id: &SessionId, config: &StartConfig) -> Result<(), VoiceError> {
        let initial_prompt = build_initial_prompt(&config.vocabulary.terms);
        self.sessions.lock().await.insert(
            session_id.clone(),
            SessionBuffer {
                pcm: Vec::new(),
                initial_prompt,
            },
        );
        Ok(())
    }

    async fn feed(&self, session_id: &SessionId, pcm: &[i16]) -> Result<(), VoiceError> {
        let mut sessions = self.sessions.lock().await;
        let buffer = sessions
            .get_mut(session_id)
            .ok_or_else(|| VoiceError::UnknownSession(session_id.to_string()))?;
        buffer.pcm.extend_from_slice(pcm);
        Ok(())
    }

    async fn finish(&self, session_id: &SessionId) -> Result<Transcript, VoiceError> {
        let buffer = self
            .sessions
            .lock()
            .await
            .remove(session_id)
            .ok_or_else(|| VoiceError::UnknownSession(session_id.to_string()))?;

        // Empty buffers (no feed calls between begin and finish) are
        // a normal path during the seam-only renderer surface — the
        // composer mic isn't capturing yet. Return an empty final
        // transcript instead of running whisper on zero samples.
        if buffer.pcm.is_empty() {
            return Ok(Transcript {
                session_id: session_id.clone(),
                text: String::new(),
                is_final: true,
            });
        }

        let audio = pcm_to_f32(&buffer.pcm);
        let context = self.context.clone();
        let initial_prompt = buffer.initial_prompt;
        let transcript_text = tokio::task::spawn_blocking(move || {
            run_inference(&context, &audio, initial_prompt.as_deref())
        })
        .await
        .map_err(|err| VoiceError::Engine(format!("whisper inference task panicked: {err}")))??;

        Ok(Transcript {
            session_id: session_id.clone(),
            text: transcript_text,
            is_final: true,
        })
    }
}

/// Construct the `initial_prompt` text from a vocabulary term list.
/// Whisper conditions on raw text, so we just join the terms with
/// commas in a sentence-like shape — `tinydiarize`'s cookbook
/// approach. Empty vocabulary yields `None` so we don't wedge an
/// empty prompt into the params.
fn build_initial_prompt(terms: &[String]) -> Option<String> {
    if terms.is_empty() {
        return None;
    }
    Some(format!(
        "Project glossary: {}.",
        terms
            .iter()
            .map(|t| t.trim())
            .filter(|t| !t.is_empty())
            .collect::<Vec<_>>()
            .join(", ")
    ))
}

/// Convert int16 PCM to float32 in [-1.0, 1.0] — the format
/// `whisper-cpp-plus`'s `transcribe_with_params` expects.
fn pcm_to_f32(pcm: &[i16]) -> Vec<f32> {
    pcm.iter()
        .map(|&s| f32::from(s) / f32::from(i16::MAX))
        .collect()
}

/// Run a single transcribe pass over the buffer and return the
/// concatenated text. Runs on a blocking thread because the
/// whisper.cpp call is CPU-bound and would otherwise block the
/// async runtime for the duration of inference.
fn run_inference(
    context: &WhisperContext,
    audio: &[f32],
    initial_prompt: Option<&str>,
) -> Result<String, LocalEngineError> {
    let mut params_builder = TranscriptionParams::builder();
    params_builder = params_builder.language("en");
    if let Some(prompt) = initial_prompt {
        params_builder = params_builder.initial_prompt(prompt);
    }
    let params = params_builder.build();

    let result = context
        .transcribe_with_params(audio, params)
        .map_err(|err| LocalEngineError::Inference(err.to_string()))?;

    let text = result
        .segments
        .iter()
        .map(|seg| seg.text.as_str())
        .collect::<Vec<_>>()
        .join(" ")
        .trim()
        .to_string();
    Ok(text)
}

/// Sample rate the engine expects on its feed path. Exposed for the
/// renderer-side cpal capture commit so the resampling target is in
/// one place.
#[allow(dead_code)]
pub const fn expected_sample_rate() -> u32 {
    SAMPLE_RATE
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn try_load_errors_when_model_missing() {
        let err = LocalWhisperEngine::try_load("/nonexistent/path/to/model.bin")
            .expect_err("loading a missing model must fail");
        assert!(matches!(err, LocalEngineError::ModelNotFound(_)));
    }

    #[test]
    fn build_initial_prompt_joins_terms() {
        let terms = vec!["Thalyn".into(), "MCP".into(), "LangGraph".into()];
        let prompt = build_initial_prompt(&terms).expect("non-empty terms yield a prompt");
        assert!(prompt.contains("Thalyn"));
        assert!(prompt.contains("MCP"));
        assert!(prompt.contains("LangGraph"));
    }

    #[test]
    fn build_initial_prompt_returns_none_for_empty_input() {
        assert!(build_initial_prompt(&[]).is_none());
    }

    #[test]
    fn build_initial_prompt_skips_blank_terms() {
        let terms = vec!["Thalyn".into(), "   ".into(), "MCP".into()];
        let prompt = build_initial_prompt(&terms).unwrap();
        assert!(prompt.contains("Thalyn, MCP"));
    }

    #[test]
    fn pcm_to_f32_normalises_into_signed_unit_range() {
        let pcm = [0_i16, i16::MAX, i16::MIN, i16::MAX / 2];
        let floats = pcm_to_f32(&pcm);
        assert!((floats[0] - 0.0).abs() < 1e-6);
        assert!((floats[1] - 1.0).abs() < 1e-6);
        assert!((floats[2] - (-1.0)).abs() < 1e-3);
        assert!((floats[3] - 0.5).abs() < 1e-3);
    }
}
