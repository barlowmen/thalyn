//! Local Whisper.cpp STT engine.
//!
//! Uses the `whisper-cpp-plus` crate to run inference in-process
//! against a `.bin` GGML model file. The engine is feature-gated on
//! `voice-whisper`; the lean build (`--no-default-features`) drops
//! the dep entirely and falls back to the [`super::engine::NoopEngine`]
//! at startup.
//!
//! Per-session shape: [`Engine::begin`] spawns a streaming worker
//! thread that owns a [`WhisperStream`] and emits interim transcripts
//! as new audio arrives. [`Engine::feed`] pushes int16 frames into
//! the worker (and into a parallel full-buffer used by
//! [`Engine::finish`] for the gold-standard final transcribe over
//! the entire utterance). The streaming path keeps inference time
//! pipelined with the user's hold; the batch-on-stop path remains
//! the source of truth for the final transcript so accuracy
//! doesn't depend on the sliding-window step granularity.
//!
//! Apple Silicon Core ML acceleration is not yet exposed by
//! `whisper-cpp-plus` 0.1.4. The going-public-checklist tracks the
//! upstream-PR-vs-in-tree-patch decision; the engine works without
//! Core ML today (Metal still hits the latency budget on Apple
//! Silicon — see ADR-0025).

#![cfg(feature = "voice-whisper")]

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::mpsc as std_mpsc;
use std::sync::Arc;
use std::thread::{self, JoinHandle};

use async_trait::async_trait;
use thiserror::Error;
use tokio::sync::Mutex;
use whisper_cpp_plus::{
    FullParams, SamplingStrategy, TranscriptionParams, WhisperContext, WhisperStream,
    WhisperStreamConfig,
};

use super::engine::{Engine, EngineKind, InterimSink, StartConfig};
use super::manager::{SessionId, Transcript, VoiceError};

/// 16 kHz mono int16 is the format Whisper expects. The renderer
/// captures at this rate (cpal in a later commit); the manager
/// passes frames through unchanged.
const SAMPLE_RATE: u32 = 16_000;

/// Streaming step size in milliseconds. Each `process_step` call
/// consumes this much new audio and re-runs inference over a
/// sliding window that includes prior overlap, so a smaller step
/// yields more frequent interim transcripts at the cost of more
/// inference passes per second of speech. 1500 ms produces ~1
/// update per 1.5 s of speech, which keeps the composer feeling
/// live for typical push-to-talk holds (2–5 s) without burning
/// inference budget on too-frequent re-decodes.
const STREAM_STEP_MS: i32 = 1500;

/// Sliding-window length for streaming inference. Whisper.cpp's
/// `WhisperStream` uses this to decide how much context to feed each
/// step; the in-progress text re-renders over the latest
/// `length_ms` of audio.
const STREAM_LENGTH_MS: i32 = 10_000;

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

/// Per-session state. The streaming worker emits interim transcripts
/// as audio arrives; the parallel `pcm` buffer feeds the final
/// batch transcribe so the gold transcript covers the entire
/// utterance without depending on the streaming step granularity.
struct SessionBuffer {
    pcm: Vec<i16>,
    initial_prompt: Option<String>,
    streaming: Option<StreamingWorker>,
}

/// Handle for the streaming worker thread that owns a
/// [`WhisperStream`]. Audio frames go in via the channel; interim
/// transcripts come out via the [`InterimSink`] handed to
/// [`StreamingWorker::spawn`]. Dropping the sender closes the
/// channel and the worker exits on its next recv.
struct StreamingWorker {
    tx: std_mpsc::Sender<Vec<f32>>,
    handle: Option<JoinHandle<()>>,
}

impl StreamingWorker {
    fn spawn(
        context: Arc<WhisperContext>,
        initial_prompt: Option<String>,
        interim: InterimSink,
    ) -> Self {
        let (tx, rx) = std_mpsc::channel::<Vec<f32>>();
        let handle = thread::spawn(move || {
            run_streaming(context, initial_prompt, rx, interim);
        });
        Self {
            tx,
            handle: Some(handle),
        }
    }

    /// Push converted f32 PCM into the worker. Send errors are
    /// swallowed — they only happen if the worker has already
    /// exited (e.g. WhisperStream init failed), in which case the
    /// session simply degrades to a final-only transcript.
    fn push(&self, samples: Vec<f32>) {
        let _ = self.tx.send(samples);
    }

    /// Drop the sender to signal EOF, then join the worker thread.
    /// Idempotent.
    fn shutdown(mut self) {
        // Closing the sender lets the worker's recv() return Err so
        // its loop exits cleanly.
        // SAFETY: replace with a dummy that will be immediately dropped.
        let (dummy_tx, _) = std_mpsc::channel::<Vec<f32>>();
        let live_tx = std::mem::replace(&mut self.tx, dummy_tx);
        drop(live_tx);
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
        }
    }
}

impl Drop for StreamingWorker {
    fn drop(&mut self) {
        if let Some(handle) = self.handle.take() {
            let (dummy_tx, _) = std_mpsc::channel::<Vec<f32>>();
            let live_tx = std::mem::replace(&mut self.tx, dummy_tx);
            drop(live_tx);
            let _ = handle.join();
        }
    }
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

    async fn begin(
        &self,
        session_id: &SessionId,
        config: &StartConfig,
        interim: InterimSink,
    ) -> Result<(), VoiceError> {
        let initial_prompt = build_initial_prompt(&config.vocabulary.terms);
        let streaming =
            StreamingWorker::spawn(self.context.clone(), initial_prompt.clone(), interim);
        self.sessions.lock().await.insert(
            session_id.clone(),
            SessionBuffer {
                pcm: Vec::new(),
                initial_prompt,
                streaming: Some(streaming),
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
        if let Some(streaming) = &buffer.streaming {
            streaming.push(pcm_to_f32(pcm));
        }
        Ok(())
    }

    async fn finish(&self, session_id: &SessionId) -> Result<Transcript, VoiceError> {
        let mut buffer = self
            .sessions
            .lock()
            .await
            .remove(session_id)
            .ok_or_else(|| VoiceError::UnknownSession(session_id.to_string()))?;

        // Stop the streaming worker first so its inference doesn't
        // race the final batch for whisper.cpp's compute resources.
        // Joining the OS thread is blocking; offload to spawn_blocking
        // so the Tokio runtime stays unblocked.
        if let Some(streaming) = buffer.streaming.take() {
            let _ = tokio::task::spawn_blocking(move || streaming.shutdown()).await;
        }

        // Empty buffers (no feed calls between begin and finish) are
        // a normal path during tests and during a stray click on the
        // mic button. Return an empty final transcript instead of
        // running whisper on zero samples.
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

/// Streaming worker body. Owns a [`WhisperStream`], pulls PCM chunks
/// off `rx`, and emits interim transcripts via `interim`. Exits when
/// the channel closes (the engine drops its `Sender` in `finish`).
fn run_streaming(
    context: Arc<WhisperContext>,
    initial_prompt: Option<String>,
    rx: std_mpsc::Receiver<Vec<f32>>,
    interim: InterimSink,
) {
    let params = build_stream_params(initial_prompt.as_deref());
    let config = WhisperStreamConfig {
        step_ms: STREAM_STEP_MS,
        length_ms: STREAM_LENGTH_MS,
        ..WhisperStreamConfig::default()
    };
    let mut stream = match WhisperStream::with_config(&context, params, config) {
        Ok(s) => s,
        Err(err) => {
            tracing::warn!(
                ?err,
                "WhisperStream construction failed; interim transcripts disabled"
            );
            // Drain the channel so the producer doesn't block on a
            // bounded channel later. Sender is unbounded today; this
            // is a defensive no-op.
            while rx.recv().is_ok() {}
            return;
        }
    };

    while let Ok(chunk) = rx.recv() {
        if chunk.is_empty() {
            continue;
        }
        stream.feed_audio(&chunk);
        loop {
            match stream.process_step() {
                Ok(Some(segments)) if !segments.is_empty() => {
                    let text = join_segments(&segments);
                    if !text.is_empty() {
                        interim.send_interim(text);
                    }
                }
                Ok(_) => break,
                Err(err) => {
                    tracing::warn!(?err, "WhisperStream.process_step error");
                    return;
                }
            }
        }
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
/// `whisper-cpp-plus`'s decode entry points expect.
fn pcm_to_f32(pcm: &[i16]) -> Vec<f32> {
    pcm.iter()
        .map(|&s| f32::from(s) / f32::from(i16::MAX))
        .collect()
}

/// Concatenate the text bodies of a slice of streaming segments,
/// trimming and collapsing empty entries.
fn join_segments(segments: &[whisper_cpp_plus::Segment]) -> String {
    segments
        .iter()
        .map(|seg| seg.text.trim())
        .filter(|t| !t.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// Build the streaming-mode `FullParams`. Mirrors the batch-path
/// transcribe params (English-only, optional initial prompt) so the
/// streaming and final transcripts agree on language + vocabulary
/// hints.
fn build_stream_params(initial_prompt: Option<&str>) -> FullParams {
    let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 1 }).language("en");
    if let Some(prompt) = initial_prompt {
        params = params.initial_prompt(prompt);
    }
    params
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

    #[test]
    fn join_segments_trims_and_skips_empty() {
        use whisper_cpp_plus::Segment;
        let segs = vec![
            Segment {
                start_ms: 0,
                end_ms: 100,
                text: "  hello  ".to_string(),
                speaker_turn_next: false,
            },
            Segment {
                start_ms: 100,
                end_ms: 200,
                text: "".to_string(),
                speaker_turn_next: false,
            },
            Segment {
                start_ms: 200,
                end_ms: 300,
                text: " world ".to_string(),
                speaker_turn_next: false,
            },
        ];
        assert_eq!(join_segments(&segs), "hello world");
    }
}
