//! Microphone capture for the voice STT bridge.
//!
//! Wraps `cpal`'s default input device, resamples to Whisper's
//! 16 kHz mono int16 format, and forwards PCM frames into the
//! [`super::VoiceManager`] via [`super::VoiceManager::feed_chunk`].
//! The composer mic is the only caller today; future continuous-
//! listen UI will reuse the same start/stop shape.
//!
//! Threading: `cpal::Stream` is `!Send + !Sync` on macOS (it holds
//! CoreAudio refs that live on a specific run loop), so the stream
//! itself stays on a dedicated `std::thread`. PCM frames cross
//! into Tokio via `tokio::sync::mpsc` and a per-session async task
//! drains the channel into the manager. A oneshot `stop` signal
//! ends the thread, which drops the stream as it returns.
//!
//! The resampler is a plain decimating downmix — sufficient for
//! conversational speech (Whisper is robust to mild aliasing
//! above 8 kHz). A proper anti-aliased resampler would be a
//! quality follow-up if voice accuracy on noisy inputs surfaces
//! a problem.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc as std_mpsc;
use std::sync::Arc;

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{Sample, SampleFormat, StreamConfig};
use thiserror::Error;

use super::manager::{SessionId, VoiceManager};

/// Whisper's input format. The resampler in [`downmix_to_16khz_mono`]
/// always produces this rate regardless of the device's native rate.
const TARGET_SAMPLE_RATE: u32 = 16_000;

/// Errors that surface from the mic-capture pipeline.
#[derive(Debug, Error)]
pub enum CaptureError {
    #[error("no default input device — check the OS audio settings")]
    NoInputDevice,
    #[error("default input config unavailable: {0}")]
    DefaultConfig(String),
    #[error("failed to build the cpal input stream: {0}")]
    BuildStream(String),
    #[error("unsupported sample format: {0:?}")]
    UnsupportedFormat(SampleFormat),
}

/// Handle for one in-flight mic-capture session. Drop the handle to
/// stop capture; for cooperative shutdown call [`MicCapture::stop`]
/// to wait for the worker to drain.
pub struct MicCapture {
    stop_tx: Option<std_mpsc::Sender<()>>,
    /// Set when the cpal callback signals an unrecoverable error so
    /// the renderer's "still listening?" tick can short-circuit.
    #[allow(dead_code)]
    failed: Arc<AtomicBool>,
}

impl MicCapture {
    /// Open the default input device and start streaming PCM into
    /// the manager under `session_id`. The session must already be
    /// open (i.e. [`VoiceManager::start`] returned its id) — the
    /// capture loop calls [`VoiceManager::feed_chunk`] from a
    /// spawned task.
    pub fn start(session_id: SessionId, manager: Arc<VoiceManager>) -> Result<Self, CaptureError> {
        let host = cpal::default_host();
        let device = host
            .default_input_device()
            .ok_or(CaptureError::NoInputDevice)?;
        let supported = device
            .default_input_config()
            .map_err(|err| CaptureError::DefaultConfig(err.to_string()))?;
        let device_rate = supported.sample_rate();
        let channels = supported.channels();
        let sample_format = supported.sample_format();
        let stream_config: StreamConfig = supported.into();

        let (pcm_tx, mut pcm_rx) = tokio::sync::mpsc::channel::<Vec<i16>>(64);
        let (stop_tx, stop_rx) = std_mpsc::channel::<()>();
        let failed = Arc::new(AtomicBool::new(false));
        let failed_for_stream = failed.clone();

        // Spawn the cpal-owning OS thread. It builds the stream,
        // starts it, and parks until the stop signal fires.
        let thread_pcm_tx = pcm_tx.clone();
        std::thread::spawn(move || {
            let stream_result = build_input_stream(
                &device,
                &stream_config,
                sample_format,
                channels,
                device_rate,
                thread_pcm_tx,
                failed_for_stream.clone(),
            );
            let stream = match stream_result {
                Ok(stream) => stream,
                Err(err) => {
                    tracing::warn!(?err, "failed to build mic stream");
                    failed_for_stream.store(true, Ordering::Relaxed);
                    return;
                }
            };
            if let Err(err) = stream.play() {
                tracing::warn!(?err, "failed to start mic stream");
                failed_for_stream.store(true, Ordering::Relaxed);
                return;
            }
            // Block this OS thread until the renderer-side stop
            // signal fires (or the sender is dropped). cpal's stream
            // stays alive as long as we hold it; dropping it here
            // ends capture.
            let _ = stop_rx.recv();
            drop(stream);
        });

        // Spawn the async drain that forwards PCM into the manager.
        // Lives in the Tokio runtime; ends when the channel closes
        // (sender drops on stop).
        tauri::async_runtime::spawn(async move {
            while let Some(frame) = pcm_rx.recv().await {
                if let Err(err) = manager.feed_chunk(&session_id, &frame).await {
                    tracing::warn!(?err, "voice manager rejected mic frame");
                    break;
                }
            }
        });

        Ok(Self {
            stop_tx: Some(stop_tx),
            failed,
        })
    }

    /// Signal the capture thread to drop the stream and end. Idempotent.
    pub fn stop(&mut self) {
        if let Some(tx) = self.stop_tx.take() {
            let _ = tx.send(());
        }
    }
}

impl Drop for MicCapture {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Build a cpal input stream that downmixes + resamples to 16 kHz
/// mono int16 and pushes through `pcm_tx`. The callback runs on
/// the audio real-time thread; it must not block, so we use a
/// bounded mpsc channel and drop frames if the consumer falls
/// behind (Whisper inference is the only consumer; if it can't
/// keep up the user's hold is too long anyway).
#[allow(clippy::too_many_arguments)]
fn build_input_stream(
    device: &cpal::Device,
    config: &StreamConfig,
    sample_format: SampleFormat,
    channels: u16,
    device_rate: u32,
    pcm_tx: tokio::sync::mpsc::Sender<Vec<i16>>,
    failed: Arc<AtomicBool>,
) -> Result<cpal::Stream, CaptureError> {
    let err_failed = failed.clone();
    let err_handler = move |err| {
        tracing::warn!(?err, "cpal input stream error");
        err_failed.store(true, Ordering::Relaxed);
    };

    match sample_format {
        SampleFormat::F32 => device
            .build_input_stream(
                config,
                move |data: &[f32], _| {
                    let frame = downmix_to_16khz_mono(data, channels, device_rate);
                    let _ = pcm_tx.try_send(frame);
                },
                err_handler,
                None,
            )
            .map_err(|err| CaptureError::BuildStream(err.to_string())),
        SampleFormat::I16 => device
            .build_input_stream(
                config,
                move |data: &[i16], _| {
                    let as_f32: Vec<f32> = data.iter().map(|&s| s.to_sample()).collect();
                    let frame = downmix_to_16khz_mono(&as_f32, channels, device_rate);
                    let _ = pcm_tx.try_send(frame);
                },
                err_handler,
                None,
            )
            .map_err(|err| CaptureError::BuildStream(err.to_string())),
        SampleFormat::U16 => device
            .build_input_stream(
                config,
                move |data: &[u16], _| {
                    let as_f32: Vec<f32> = data.iter().map(|&s| s.to_sample()).collect();
                    let frame = downmix_to_16khz_mono(&as_f32, channels, device_rate);
                    let _ = pcm_tx.try_send(frame);
                },
                err_handler,
                None,
            )
            .map_err(|err| CaptureError::BuildStream(err.to_string())),
        other => Err(CaptureError::UnsupportedFormat(other)),
    }
}

/// Downmix multi-channel f32 frames to mono and resample (decimate)
/// to 16 kHz int16. The input layout is interleaved: channels are
/// packed sample-by-sample. The resampler picks one out of every
/// `device_rate / 16000` mono samples — sufficient for speech given
/// Whisper's robustness to mild aliasing in the 5–8 kHz band.
fn downmix_to_16khz_mono(data: &[f32], channels: u16, device_rate: u32) -> Vec<i16> {
    if data.is_empty() || channels == 0 {
        return Vec::new();
    }
    let channels = channels as usize;
    let frame_count = data.len() / channels;
    let inv_channels = 1.0 / channels as f32;

    // Mono mix at the device rate.
    let mut mono = Vec::with_capacity(frame_count);
    for i in 0..frame_count {
        let offset = i * channels;
        let sum: f32 = data[offset..offset + channels].iter().copied().sum();
        mono.push(sum * inv_channels);
    }

    // Decimate from device_rate to TARGET_SAMPLE_RATE. When the rate
    // is already 16 kHz we copy through directly; otherwise we step
    // by an integer or float ratio, picking nearest neighbours.
    if device_rate == TARGET_SAMPLE_RATE {
        return mono.iter().map(f32_to_i16).collect();
    }
    let step = device_rate as f64 / TARGET_SAMPLE_RATE as f64;
    let target_len = (mono.len() as f64 / step).floor() as usize;
    let mut out = Vec::with_capacity(target_len);
    for i in 0..target_len {
        let src_idx = (i as f64 * step) as usize;
        if src_idx >= mono.len() {
            break;
        }
        out.push(f32_to_i16(&mono[src_idx]));
    }
    out
}

fn f32_to_i16(sample: &f32) -> i16 {
    let clamped = sample.clamp(-1.0, 1.0);
    (clamped * f32::from(i16::MAX)) as i16
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn downmix_passes_mono_16khz_through_unchanged() {
        // Half-amplitude sine sample at i16 maximum/2.
        let input = vec![0.5_f32, -0.25, 0.0, 0.5];
        let out = downmix_to_16khz_mono(&input, 1, 16_000);
        assert_eq!(out.len(), 4);
        assert!((out[0] - (i16::MAX / 2)).abs() <= 1);
        assert!((out[1] - (-i16::MAX / 4)).abs() <= 1);
        assert_eq!(out[2], 0);
        assert!((out[3] - (i16::MAX / 2)).abs() <= 1);
    }

    #[test]
    fn downmix_averages_stereo_to_mono() {
        // Stereo interleaved: L=1.0, R=-1.0 should average to 0.
        let input = vec![1.0_f32, -1.0, 0.5, -0.5];
        let out = downmix_to_16khz_mono(&input, 2, 16_000);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0], 0);
        assert_eq!(out[1], 0);
    }

    #[test]
    fn downmix_decimates_48khz_to_16khz_at_three_to_one() {
        // 6 samples at 48 kHz → 2 samples at 16 kHz (every third).
        let input: Vec<f32> = (0..6).map(|i| i as f32 / 5.0).collect();
        let out = downmix_to_16khz_mono(&input, 1, 48_000);
        // 2 samples expected (floor of 6 * 16/48 = 2).
        assert_eq!(out.len(), 2);
    }

    #[test]
    fn downmix_clamps_out_of_range_floats() {
        let input = vec![2.0_f32, -2.0];
        let out = downmix_to_16khz_mono(&input, 1, 16_000);
        assert_eq!(out, vec![i16::MAX, -i16::MAX]);
    }

    #[test]
    fn downmix_handles_empty_input() {
        assert!(downmix_to_16khz_mono(&[], 1, 16_000).is_empty());
    }

    #[test]
    fn downmix_handles_zero_channels() {
        // Defensive: channels = 0 must not divide-by-zero. Returns empty.
        assert!(downmix_to_16khz_mono(&[1.0, 2.0], 0, 16_000).is_empty());
    }
}
