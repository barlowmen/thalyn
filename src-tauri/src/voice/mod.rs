//! Voice STT bridge.
//!
//! The renderer's composer mic captures intent; the heavy lifting
//! (mic capture, model decoding, transcript streaming) lives in the
//! Rust core per `02-architecture.md` §4.1 and the v0.32 spike's F4
//! / F8 findings — audio bytes never traverse the Tauri IPC, so the
//! renderer-facing surface is just session control: start, stop, and
//! a `stt:transcript` event channel.
//!
//! This module ships the seam first: the [`VoiceManager`] that owns
//! sessions plus the [`Engine`] trait every backend (whisper.cpp,
//! Deepgram, MLX) implements. The default engine is a
//! [`NoopEngine`] that yields an empty transcript on `finish` —
//! later commits swap in the real backends without touching the
//! shape that lib.rs and the renderer talk to.

pub mod engine;
#[cfg(feature = "voice-whisper")]
pub mod local;
pub mod manager;
#[cfg(feature = "voice-whisper")]
pub mod models;

pub use engine::{ProjectVocabulary, StartConfig};
#[cfg(feature = "voice-whisper")]
pub use local::LocalWhisperEngine;
pub use manager::{SessionId, Transcript, VoiceManager};
#[cfg(feature = "voice-whisper")]
pub use models::ModelStore;
