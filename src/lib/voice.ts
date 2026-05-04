/**
 * Voice STT bridge — TypeScript wrappers for the Rust core's
 * ``stt_*`` Tauri commands and the ``stt:transcript`` event.
 *
 * The renderer never handles audio bytes itself — capture happens
 * in the Rust core via ``cpal`` (lands in a later commit). The
 * composer only triggers session lifecycle (start / stop) and
 * subscribes to the transcript event channel for live updates.
 */

import { invoke } from "@tauri-apps/api/core";

export type SttTranscript = {
  sessionId: string;
  text: string;
  isFinal: boolean;
};

export type SttLevel = {
  sessionId: string;
  /** Peak absolute amplitude over the last cpal chunk, in [0, 1]. */
  peak: number;
};

export type StartSttOptions = {
  /**
   * Project context for vocabulary biasing. The brain returns the
   * matching ``project_vocabulary`` slice and the engine wraps it
   * into Whisper's ``initial_prompt``.
   */
  projectId?: string | null;
  /**
   * Run the engine in continuous-listen mode (VAD segments each
   * utterance). When ``true``, the engine emits one
   * ``stt:transcript`` event with ``isFinal: true`` per detected
   * utterance and keeps listening until ``stopStt`` is called.
   * Default ``false`` (single-utterance push-to-talk).
   */
  continuous?: boolean;
  /**
   * Route the session to the opt-in cloud STT engine (Deepgram
   * Nova-3 per ADR-0025) instead of the local Whisper.cpp default.
   * The Rust core fails fast when this is true but no API key is
   * configured, or when the network wire-up is still pending.
   */
  preferCloud?: boolean;
  /**
   * Route the session to the opt-in MLX-Whisper engine (Apple
   * Silicon power users, ADR-0025). Mutually exclusive with
   * ``preferCloud``; the engine errors out until the model-download
   * + brain-venv MLX dep go live.
   */
  preferMlx?: boolean;
};

/**
 * Begin a voice STT session.
 */
export async function startStt(
  options: StartSttOptions = {},
): Promise<string> {
  return invoke<string>("stt_start", {
    projectId: options.projectId ?? null,
    continuous: options.continuous ?? false,
    preferCloud: options.preferCloud ?? false,
    preferMlx: options.preferMlx ?? false,
  });
}

/**
 * Push a PCM frame into an open session. Bytes are little-endian
 * 16-bit signed mono at 16 kHz (the Whisper format). The current
 * renderer doesn't call this — audio capture moves into the Rust
 * core via cpal — but the wrapper exists so test stories and
 * future automation can drive a session deterministically.
 */
export async function pushSttChunk(
  sessionId: string,
  pcm: Uint8Array,
): Promise<void> {
  await invoke("stt_chunk", { sessionId, pcm: Array.from(pcm) });
}

/**
 * Finalise a session and return the final transcript. The
 * ``stt:transcript`` event also fires for the same payload so
 * subscribers receive the closure even if the caller doesn't
 * await this promise.
 */
export async function stopStt(sessionId: string): Promise<SttTranscript> {
  return invoke<SttTranscript>("stt_stop", { sessionId });
}

/**
 * Subscribe to live transcript updates. Returns an unsubscribe
 * function. The handler is called for both interim and final
 * transcripts; ``isFinal`` distinguishes the two.
 *
 * Tauri's event API is loaded best-effort so storybook + playwright
 * tolerate its absence — same shape as the existing terminal /
 * lead-escalation listeners.
 */
export async function subscribeSttTranscripts(
  handler: (transcript: SttTranscript) => void,
): Promise<() => void> {
  const eventModule = await import("@tauri-apps/api/event").catch(
    () => undefined,
  );
  if (!eventModule) return () => undefined;
  const unlisten = await eventModule.listen<SttTranscript>(
    "stt:transcript",
    (event) => handler(event.payload),
  );
  return () => unlisten();
}

/**
 * Subscribe to live mic-level samples. Peak amplitude per cpal
 * chunk; the handler runs at audio-callback cadence (~20–50 ms)
 * so callers should keep work cheap. Returns an unsubscribe.
 */
export async function subscribeSttLevels(
  handler: (level: SttLevel) => void,
): Promise<() => void> {
  const eventModule = await import("@tauri-apps/api/event").catch(
    () => undefined,
  );
  if (!eventModule) return () => undefined;
  const unlisten = await eventModule.listen<SttLevel>(
    "stt:level",
    (event) => handler(event.payload),
  );
  return () => unlisten();
}
