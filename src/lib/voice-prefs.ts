/**
 * Renderer-side voice input preferences.
 *
 * Three orthogonal settings live here:
 *
 * - ``voiceMode`` — whether the engine runs continuous-listen (VAD
 *   segments each utterance) or single-utterance push-to-talk for a
 *   given session.
 * - ``voiceMicGesture`` — how the mic button activates: press-and-
 *   hold (release ends the session) or tap-toggle (tap to start, tap
 *   again to stop). Independent of mode so any combination works.
 * - ``voiceContinuousSubmit`` — what happens to each utterance once
 *   continuous-listen finalises it: drop into the textarea for the
 *   user to review (default), or append + auto-submit immediately.
 *
 * All three persist in ``localStorage`` and broadcast custom events
 * so the composer + settings dialog stay in sync without prop
 * drilling. Mirrors the active-project / active-provider pattern.
 */

export type VoiceMode = "ptt" | "continuous";
export type VoiceMicGesture = "hold" | "tap-toggle";
export type VoiceContinuousSubmit = "review" | "auto";
export type VoiceEngine = "local" | "cloud" | "mlx";

const MODE_KEY = "thalyn:voice-mode";
const GESTURE_KEY = "thalyn:voice-mic-gesture";
const SUBMIT_KEY = "thalyn:voice-continuous-submit";
const ENGINE_KEY = "thalyn:voice-engine";

export const VOICE_MODE_DEFAULT: VoiceMode = "continuous";
export const VOICE_MIC_GESTURE_DEFAULT: VoiceMicGesture = "hold";
export const VOICE_CONTINUOUS_SUBMIT_DEFAULT: VoiceContinuousSubmit = "review";
export const VOICE_ENGINE_DEFAULT: VoiceEngine = "local";

const VOICE_MODE_VALUES: ReadonlySet<VoiceMode> = new Set(["ptt", "continuous"]);
const VOICE_GESTURE_VALUES: ReadonlySet<VoiceMicGesture> = new Set([
  "hold",
  "tap-toggle",
]);
const VOICE_SUBMIT_VALUES: ReadonlySet<VoiceContinuousSubmit> = new Set([
  "review",
  "auto",
]);
const VOICE_ENGINE_VALUES: ReadonlySet<VoiceEngine> = new Set([
  "local",
  "cloud",
  "mlx",
]);

export const VOICE_MODE_EVENT = "thalyn:voice-mode-changed";
export const VOICE_MIC_GESTURE_EVENT = "thalyn:voice-mic-gesture-changed";
export const VOICE_CONTINUOUS_SUBMIT_EVENT =
  "thalyn:voice-continuous-submit-changed";
export const VOICE_ENGINE_EVENT = "thalyn:voice-engine-changed";

function readEnum<T extends string>(
  key: string,
  fallback: T,
  allowed: ReadonlySet<T>,
): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw && (allowed as ReadonlySet<string>).has(raw)) {
      return raw as T;
    }
  } catch {
    // best-effort
  }
  return fallback;
}

function writeEnum<T extends string>(
  key: string,
  value: T,
  event: string,
): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
    window.dispatchEvent(new CustomEvent(event, { detail: value }));
  } catch {
    // best-effort
  }
}

function subscribeEnum<T extends string>(
  event: string,
  allowed: ReadonlySet<T>,
  handler: (value: T) => void,
): () => void {
  if (typeof window === "undefined") return () => undefined;
  const listener = (e: Event) => {
    const detail = (e as CustomEvent<unknown>).detail;
    if (
      typeof detail === "string" &&
      (allowed as ReadonlySet<string>).has(detail)
    ) {
      handler(detail as T);
    }
  };
  window.addEventListener(event, listener);
  return () => window.removeEventListener(event, listener);
}

// --- voiceMode -------------------------------------------------------------

export function readVoiceMode(): VoiceMode {
  return readEnum(MODE_KEY, VOICE_MODE_DEFAULT, VOICE_MODE_VALUES);
}

export function writeVoiceMode(value: VoiceMode): void {
  writeEnum(MODE_KEY, value, VOICE_MODE_EVENT);
}

export function subscribeVoiceMode(
  handler: (value: VoiceMode) => void,
): () => void {
  return subscribeEnum(VOICE_MODE_EVENT, VOICE_MODE_VALUES, handler);
}

// --- voiceMicGesture ------------------------------------------------------

export function readVoiceMicGesture(): VoiceMicGesture {
  return readEnum(GESTURE_KEY, VOICE_MIC_GESTURE_DEFAULT, VOICE_GESTURE_VALUES);
}

export function writeVoiceMicGesture(value: VoiceMicGesture): void {
  writeEnum(GESTURE_KEY, value, VOICE_MIC_GESTURE_EVENT);
}

export function subscribeVoiceMicGesture(
  handler: (value: VoiceMicGesture) => void,
): () => void {
  return subscribeEnum(VOICE_MIC_GESTURE_EVENT, VOICE_GESTURE_VALUES, handler);
}

// --- voiceContinuousSubmit ------------------------------------------------

export function readVoiceContinuousSubmit(): VoiceContinuousSubmit {
  return readEnum(
    SUBMIT_KEY,
    VOICE_CONTINUOUS_SUBMIT_DEFAULT,
    VOICE_SUBMIT_VALUES,
  );
}

export function writeVoiceContinuousSubmit(value: VoiceContinuousSubmit): void {
  writeEnum(SUBMIT_KEY, value, VOICE_CONTINUOUS_SUBMIT_EVENT);
}

export function subscribeVoiceContinuousSubmit(
  handler: (value: VoiceContinuousSubmit) => void,
): () => void {
  return subscribeEnum(
    VOICE_CONTINUOUS_SUBMIT_EVENT,
    VOICE_SUBMIT_VALUES,
    handler,
  );
}

// --- voiceEngine -----------------------------------------------------------

export function readVoiceEngine(): VoiceEngine {
  return readEnum(ENGINE_KEY, VOICE_ENGINE_DEFAULT, VOICE_ENGINE_VALUES);
}

export function writeVoiceEngine(value: VoiceEngine): void {
  writeEnum(ENGINE_KEY, value, VOICE_ENGINE_EVENT);
}

export function subscribeVoiceEngine(
  handler: (value: VoiceEngine) => void,
): () => void {
  return subscribeEnum(VOICE_ENGINE_EVENT, VOICE_ENGINE_VALUES, handler);
}
