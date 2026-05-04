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

/**
 * Begin a voice STT session. The optional ``projectId`` lets the
 * brain seed the engine's ``initial_prompt`` with the project
 * vocabulary — pass the foreground project so dictation biases
 * toward known identifiers.
 */
export async function startStt(projectId?: string): Promise<string> {
  return invoke<string>("stt_start", { projectId: projectId ?? null });
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
