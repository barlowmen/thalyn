import { ArrowUp, Loader2, Mic } from "lucide-react";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { startStt, stopStt } from "@/lib/voice";

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
  const recordingRef = useRef<string | null>(null);

  const submit = () => {
    if (disabled) return;
    const trimmed = value.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
    setValue("");
  };

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
    try {
      const sessionId = await startStt(projectId ?? undefined);
      recordingRef.current = sessionId;
      setVoice({ kind: "recording", sessionId });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setVoice({ kind: "error", message });
    }
  };

  const stopRecording = async () => {
    if (voice.kind !== "recording") return;
    const sessionId = voice.sessionId;
    recordingRef.current = null;
    setVoice({ kind: "transcribing" });
    try {
      const transcript = await stopStt(sessionId);
      const text = transcript.text.trim();
      if (text) {
        setValue((prev) => (prev ? `${prev}${prev.endsWith(" ") ? "" : " "}${text}` : text));
      }
      setVoice({ kind: "idle" });
    } catch (err) {
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

  const roomy = size === "roomy";
  const recording = voice.kind === "recording";
  const transcribing = voice.kind === "transcribing";
  const errored = voice.kind === "error";

  const micLabel = recording
    ? "Recording — release to transcribe"
    : transcribing
      ? "Transcribing…"
      : errored
        ? `Voice input error — ${voice.message}`
        : "Voice input — hold to record";

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
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          void startRecording();
        }}
        onPointerUp={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId);
          }
          void stopRecording();
        }}
        onPointerCancel={() => {
          void stopRecording();
        }}
        onLostPointerCapture={() => {
          void stopRecording();
        }}
        className={cn(
          "shrink-0",
          roomy ? "h-10 w-10" : "h-9 w-9",
          !recording && !transcribing && !errored && "text-muted-foreground",
          recording && "animate-pulse",
        )}
      >
        {transcribing ? (
          <Loader2 className="animate-spin" aria-hidden />
        ) : (
          <Mic aria-hidden />
        )}
      </Button>
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
