import { useEffect, useState } from "react";

import { Label } from "@/components/ui/label";
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
  writeVoiceContinuousSubmit,
  writeVoiceMicGesture,
  writeVoiceMode,
} from "@/lib/voice-prefs";

/**
 * Three toggles controlling the composer's voice input behaviour:
 *
 * - **Mode** picks between push-to-talk (one utterance per session,
 *   batched on stop) and continuous-listen (the engine auto-segments
 *   utterances on silence and emits each one finalised).
 * - **Mic button gesture** picks between press-and-hold (release ends
 *   the session) and tap-to-toggle (tap to start, tap to stop). Hold
 *   feels like an intercom; tap-to-toggle is hands-free / accessible.
 * - **After utterance** decides what continuous-listen does with each
 *   finalised utterance: drop into the textarea for review (the
 *   default — gives you a chance to edit before sending) or auto-
 *   submit immediately (true hands-free dictation).
 *
 * All three persist in ``localStorage`` so the choice carries across
 * sessions; the composer subscribes via custom events so changes
 * here flow into a live composer without a remount.
 */
export function VoiceSection() {
  const [mode, setModeState] = useState<VoiceMode>(readVoiceMode);
  const [gesture, setGestureState] = useState<VoiceMicGesture>(
    readVoiceMicGesture,
  );
  const [submit, setSubmitState] = useState<VoiceContinuousSubmit>(
    readVoiceContinuousSubmit,
  );

  useEffect(() => {
    const unMode = subscribeVoiceMode(setModeState);
    const unGesture = subscribeVoiceMicGesture(setGestureState);
    const unSubmit = subscribeVoiceContinuousSubmit(setSubmitState);
    return () => {
      unMode();
      unGesture();
      unSubmit();
    };
  }, []);

  const onModeChange = (next: VoiceMode) => {
    setModeState(next);
    writeVoiceMode(next);
  };
  const onGestureChange = (next: VoiceMicGesture) => {
    setGestureState(next);
    writeVoiceMicGesture(next);
  };
  const onSubmitChange = (next: VoiceContinuousSubmit) => {
    setSubmitState(next);
    writeVoiceContinuousSubmit(next);
  };

  return (
    <section className="space-y-4">
      <header className="space-y-1">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
          Voice
        </h3>
        <p className="text-sm text-muted-foreground">
          How the composer mic behaves. Audio decoding is local by
          default — the cloud opt-in is off until you supply an API
          key elsewhere.
        </p>
      </header>

      <div className="space-y-3 rounded-lg border border-border bg-card p-4">
        <div>
          <Label htmlFor="voice-mode" className="text-sm font-medium">
            Mode
          </Label>
          <p className="mt-1 text-xs text-muted-foreground">
            Continuous-listen finalises each utterance on its own
            silence, so a long monologue arrives as a stream of
            finished phrases. Push-to-talk runs one batch decode over
            the whole session on release.
          </p>
        </div>
        <select
          id="voice-mode"
          value={mode}
          onChange={(e) => onModeChange(e.target.value as VoiceMode)}
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="continuous">Continuous-listen (VAD-segmented)</option>
          <option value="ptt">Push-to-talk (single utterance)</option>
        </select>
      </div>

      <div className="space-y-3 rounded-lg border border-border bg-card p-4">
        <div>
          <Label htmlFor="voice-gesture" className="text-sm font-medium">
            Mic button gesture
          </Label>
          <p className="mt-1 text-xs text-muted-foreground">
            Press-and-hold means recording ends when you release.
            Tap-to-toggle means a single click starts a session and
            another click ends it — useful for hands-free dictation
            or motor-accessibility.
          </p>
        </div>
        <select
          id="voice-gesture"
          value={gesture}
          onChange={(e) => onGestureChange(e.target.value as VoiceMicGesture)}
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="hold">Press-and-hold</option>
          <option value="tap-toggle">Tap-to-toggle</option>
        </select>
      </div>

      <div className="space-y-3 rounded-lg border border-border bg-card p-4">
        <div>
          <Label htmlFor="voice-submit" className="text-sm font-medium">
            After each utterance (continuous-listen)
          </Label>
          <p className="mt-1 text-xs text-muted-foreground">
            Review keeps each finalised utterance in the textarea so
            you can edit before sending. Auto-submit sends each
            utterance the moment it lands — true hands-free.
          </p>
        </div>
        <select
          id="voice-submit"
          value={submit}
          onChange={(e) =>
            onSubmitChange(e.target.value as VoiceContinuousSubmit)
          }
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="review">Drop into textarea (review before send)</option>
          <option value="auto">Auto-submit each utterance</option>
        </select>
      </div>
    </section>
  );
}
