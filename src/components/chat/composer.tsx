import { ArrowUp, Loader2, Mic } from "lucide-react";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  startStt,
  stopStt,
  subscribeSttLevels,
  subscribeSttTranscripts,
} from "@/lib/voice";
import {
  type VoiceContinuousSubmit,
  type VoiceMicGesture,
  type VoiceMode,
  readVoiceContinuousSubmit,
  readVoiceMicGesture,
  readVoiceMode,
  subscribeVoiceContinuousSubmit,
  subscribeVoiceMicGesture,
  subscribeVoiceMode,
} from "@/lib/voice-prefs";

type Props = {
  disabled?: boolean;
  placeholder?: string;
  onSubmit: (prompt: string) => void;
  /**
   * Visual size of the composer. ``compact`` is the legacy mosaic
   * shape — single-line presentation, smaller padding. ``roomy`` is
   * the chat-first shape — wider padding, generous typography, fits
   * the bottom of a full-width chat window. Defaults to ``compact``
   * so the legacy callers keep their layout.
   */
  size?: "compact" | "roomy";
  /**
   * Project context for voice transcription. The Rust core forwards
   * this to the brain's ``voice.project_vocabulary`` RPC so Whisper's
   * ``initial_prompt`` biases toward project-specific terminology.
   * ``null`` / undefined is fine — the engine falls back to its
   * default decoder behaviour (F7 / ADR-0025).
   */
  projectId?: string | null;
};

type VoiceState =
  | { kind: "idle" }
  | { kind: "recording"; sessionId: string }
  | { kind: "transcribing" }
  | { kind: "error"; message: string };

/**
 * Map raw peak amplitude (linear, [0, 1]) to a meter scale that
 * feels responsive. Speech rarely peaks near 1.0 even when loud —
 * raising the floor and clamping high values gives a livelier bar
 * for normal voice input without over-saturating on loud syllables.
 */
function meterScale(peak: number): number {
  const floor = 0.05;
  const ceiling = 0.6;
  const clamped = Math.max(0, Math.min(peak, 1));
  if (clamped <= floor) return 0;
  return Math.min(1, (clamped - floor) / (ceiling - floor));
}

/**
 * Concatenate the recording-time prefix and the transcript, inserting
 * a space when the prefix is non-empty and doesn't already end with
 * one. Mirrors the legacy ``stopRecording`` join behaviour so
 * dictating after typed text reads as one sentence.
 */
function joinPrefixSuffix(prefix: string, suffix: string): string {
  if (!prefix) return suffix;
  return prefix.endsWith(" ") ? prefix + suffix : `${prefix} ${suffix}`;
}

/**
 * Multi-line composer. Enter sends; Shift-Enter inserts a newline;
 * ⌘/Ctrl-Enter is an explicit send alias for users who prefer the
 * Cmd-Enter convention. Auto-grows to a sensible cap then scrolls.
 *
 * The mic button is **press-and-hold push-to-talk** (F7 / ADR-0025).
 * Mouse / touch down opens an STT session in the Rust core; release
 * stops the cpal stream, runs the engine's batch-on-stop transcribe,
 * and drops the editable transcript into the textarea. The user can
 * tweak before hitting Cmd/Ctrl-Enter — voice is a faster way to
 * dictate intent, not a one-shot voice command.
 */
export function Composer({
  disabled,
  placeholder,
  onSubmit,
  size = "compact",
  projectId,
}: Props) {
  const [value, setValue] = useState("");
  const [voice, setVoice] = useState<VoiceState>({ kind: "idle" });
  const [level, setLevel] = useState(0);
  const [mode, setMode] = useState<VoiceMode>(readVoiceMode);
  const [gesture, setGesture] = useState<VoiceMicGesture>(readVoiceMicGesture);
  const [continuousSubmit, setContinuousSubmit] =
    useState<VoiceContinuousSubmit>(readVoiceContinuousSubmit);
  const recordingRef = useRef<string | null>(null);
  // Snapshot of the textarea's content at the moment recording
  // started. Interim transcripts are written *after* this prefix so
  // the user's prior typed text isn't clobbered, and the final
  // transcript replaces the whole interim suffix on stop.
  const prefixRef = useRef<string>("");
  // Whether the *current* session is in continuous-listen mode.
  // Captured at session start so a settings flip mid-session can't
  // confuse the utterance handler.
  const sessionContinuousRef = useRef<boolean>(false);
  // Latest submit mode so the transcript subscription closure (which
  // runs across sessions) reads the current setting without re-
  // subscribing every change.
  const continuousSubmitRef = useRef<VoiceContinuousSubmit>(continuousSubmit);
  useEffect(() => {
    continuousSubmitRef.current = continuousSubmit;
  }, [continuousSubmit]);

  const submit = () => {
    if (disabled) return;
    const trimmed = value.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
    setValue("");
  };

  // Pending text accumulated by continuous-listen utterances waiting
  // to be pushed into the textarea. We can't close over `setValue`
  // safely from the long-lived transcript subscription, so we hop
  // through this ref and a `useEffect` flush.
  const submitNowRef = useRef<((text: string) => void) | null>(null);
  useEffect(() => {
    submitNowRef.current = (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || disabled) return;
      onSubmit(trimmed);
      setValue("");
    };
    return () => {
      submitNowRef.current = null;
    };
  }, [onSubmit, disabled]);

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // Cmd-Enter / Ctrl-Enter — explicit send alias.
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submit();
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  const startRecording = async () => {
    if (voice.kind !== "idle" || disabled) return;
    prefixRef.current = value;
    sessionContinuousRef.current = mode === "continuous";
    try {
      const sessionId = await startStt({
        projectId: projectId ?? null,
        continuous: mode === "continuous",
      });
      recordingRef.current = sessionId;
      setVoice({ kind: "recording", sessionId });
    } catch (err) {
      sessionContinuousRef.current = false;
      const message = err instanceof Error ? err.message : String(err);
      setVoice({ kind: "error", message });
    }
  };

  const stopRecording = async () => {
    if (voice.kind !== "recording") return;
    const sessionId = voice.sessionId;
    const prefix = prefixRef.current;
    const wasContinuous = sessionContinuousRef.current;
    recordingRef.current = null;
    sessionContinuousRef.current = false;
    setVoice({ kind: "transcribing" });
    try {
      const transcript = await stopStt(sessionId);
      if (wasContinuous) {
        // Continuous-listen sessions accumulate utterances into the
        // textarea inline; the final return from stopStt is empty
        // by design, so just leave whatever the user has so far.
        setVoice({ kind: "idle" });
        return;
      }
      const text = transcript.text.trim();
      // Replace the whole interim suffix (anything appended during
      // the hold) with the final, gold transcript — interim text
      // is rolling and may not match the final phrasing.
      setValue(text ? joinPrefixSuffix(prefix, text) : prefix);
      setVoice({ kind: "idle" });
    } catch (err) {
      // Restore the pre-recording prefix so the textarea doesn't
      // strand a partial interim if the engine errored out.
      setValue(prefix);
      const message = err instanceof Error ? err.message : String(err);
      setVoice({ kind: "error", message });
    }
  };

  // Defensive: if the component unmounts mid-recording, drop the
  // session in the core so the cpal stream doesn't leak.
  useEffect(() => {
    return () => {
      const sessionId = recordingRef.current;
      if (sessionId) {
        stopStt(sessionId).catch(() => undefined);
        recordingRef.current = null;
      }
    };
  }, []);

  // Subscribe to mic-level events so the meter reflects real
  // amplitude rather than a CSS animation. The subscription is
  // active across the component's lifetime; payloads with a
  // session id that doesn't match the current recording are
  // dropped so concurrent composers can't crosstalk.
  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    void subscribeSttLevels((sample) => {
      if (cancelled) return;
      const current = recordingRef.current;
      if (!current || sample.sessionId !== current) return;
      setLevel(sample.peak);
    }).then((cleanup) => {
      if (cancelled) {
        cleanup();
        return;
      }
      unlisten = cleanup;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  // Subscribe to transcripts: interim updates render live in the
  // textarea, and continuous-listen utterance-finals either fold
  // into the textarea or auto-submit per the user's preference.
  // Push-to-talk session-finals are handled by ``stopRecording``'s
  // ``stopStt`` return path so we don't double-write them here.
  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    void subscribeSttTranscripts((sample) => {
      if (cancelled) return;
      const current = recordingRef.current;
      if (!current || sample.sessionId !== current) return;
      const text = sample.text.trim();

      if (!sample.isFinal) {
        // Interim — replace the rolling suffix.
        const prefix = prefixRef.current;
        setValue(text ? joinPrefixSuffix(prefix, text) : prefix);
        return;
      }

      // Final transcripts only flow through the broadcast in
      // continuous mode. PTT sessions only emit one final via the
      // ``stopStt`` return path; ignoring it here is correct.
      if (!sessionContinuousRef.current) return;
      if (!text) return;

      const submitMode = continuousSubmitRef.current;
      if (submitMode === "auto") {
        const submitNow = submitNowRef.current;
        if (submitNow) {
          // Auto-submit drops the prior prefix + interim suffix and
          // sends only this utterance. The composer clears the
          // textarea after; the next utterance starts from empty.
          submitNow(joinPrefixSuffix(prefixRef.current, text));
          prefixRef.current = "";
        }
        return;
      }

      // Review mode: append the utterance to the prefix and let it
      // become the new prefix for the next utterance, so the user
      // can edit before sending.
      const next = joinPrefixSuffix(prefixRef.current, text);
      prefixRef.current = next;
      setValue(next);
    }).then((cleanup) => {
      if (cancelled) {
        cleanup();
        return;
      }
      unlisten = cleanup;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  // Reset the meter when a session ends so the bar collapses
  // cleanly (otherwise the last peak value would linger).
  useEffect(() => {
    if (voice.kind !== "recording") {
      setLevel(0);
    }
  }, [voice.kind]);

  // Subscribe to voice-pref changes so settings dialog edits flow
  // into the composer without a remount.
  useEffect(() => {
    const unMode = subscribeVoiceMode(setMode);
    const unGesture = subscribeVoiceMicGesture(setGesture);
    const unSubmit = subscribeVoiceContinuousSubmit(setContinuousSubmit);
    return () => {
      unMode();
      unGesture();
      unSubmit();
    };
  }, []);

  const roomy = size === "roomy";
  const recording = voice.kind === "recording";
  const transcribing = voice.kind === "transcribing";
  const errored = voice.kind === "error";
  const tapToggle = gesture === "tap-toggle";
  const continuousMode = mode === "continuous";

  const idleHint = tapToggle
    ? continuousMode
      ? "Voice input — tap to start continuous-listen"
      : "Voice input — tap to start"
    : continuousMode
      ? "Voice input — hold for continuous-listen"
      : "Voice input — hold to record";
  const recordingHint = tapToggle
    ? "Recording — tap to stop"
    : "Recording — release to transcribe";

  const micLabel = recording
    ? recordingHint
    : transcribing
      ? "Transcribing…"
      : errored
        ? `Voice input error — ${voice.message}`
        : idleHint;

  return (
    <form
      className={cn(
        "flex items-end gap-2 border-t border-border bg-background",
        roomy ? "px-6 py-4" : "px-6 py-3",
      )}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="relative shrink-0">
        <Button
          type="button"
          size="icon"
          variant={recording ? "destructive" : "ghost"}
          disabled={disabled || transcribing}
          aria-label={micLabel}
          aria-pressed={recording}
          title={micLabel}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            if (tapToggle) {
              // Tap-toggle: capture is unnecessary because we don't
              // care about pointer-up. Single click toggles.
              event.preventDefault();
              if (recording) {
                void stopRecording();
              } else {
                void startRecording();
              }
              return;
            }
            event.preventDefault();
            event.currentTarget.setPointerCapture(event.pointerId);
            void startRecording();
          }}
          onPointerUp={(event) => {
            if (tapToggle) return;
            if (event.currentTarget.hasPointerCapture(event.pointerId)) {
              event.currentTarget.releasePointerCapture(event.pointerId);
            }
            void stopRecording();
          }}
          onPointerCancel={() => {
            if (tapToggle) return;
            void stopRecording();
          }}
          onLostPointerCapture={() => {
            if (tapToggle) return;
            void stopRecording();
          }}
          className={cn(
            "relative",
            roomy ? "h-10 w-10" : "h-9 w-9",
            !recording && !transcribing && !errored && "text-muted-foreground",
          )}
        >
          {transcribing ? (
            <Loader2 className="animate-spin" aria-hidden />
          ) : (
            <Mic aria-hidden className="relative z-10" />
          )}
          {recording && (
            <span
              aria-hidden
              className="pointer-events-none absolute inset-x-1 bottom-1 origin-bottom rounded-sm bg-destructive-foreground/40 transition-transform duration-75 ease-out"
              style={{
                height: "60%",
                transform: `scaleY(${meterScale(level)})`,
              }}
            />
          )}
        </Button>
        {recording && (
          <span
            role="meter"
            aria-label="Microphone level"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(meterScale(level) * 100)}
            className="sr-only"
          >
            {Math.round(meterScale(level) * 100)}
          </span>
        )}
      </div>
      <label htmlFor="chat-composer" className="sr-only">
        Message Thalyn
      </label>
      <textarea
        id="chat-composer"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        disabled={disabled}
        rows={1}
        placeholder={placeholder ?? "Message Thalyn…"}
        className={cn(
          "flex-1 resize-y rounded-md border border-border bg-card placeholder:text-muted-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          "disabled:cursor-not-allowed disabled:opacity-50",
          roomy
            ? "min-h-[48px] max-h-60 px-4 py-3 text-base"
            : "min-h-[40px] max-h-48 px-3 py-2 text-sm",
        )}
      />
      <Button
        type="submit"
        size="icon"
        disabled={disabled || !value.trim()}
        aria-label="Send message"
        className={cn(roomy ? "h-10 w-10" : undefined)}
      >
        <ArrowUp aria-hidden />
      </Button>
    </form>
  );
}
