import { AlertTriangle, Eye, EyeOff } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  type VoiceContinuousSubmit,
  type VoiceEngine,
  type VoiceMicGesture,
  type VoiceMode,
  readVoiceContinuousSubmit,
  readVoiceEngine,
  readVoiceMicGesture,
  readVoiceMode,
  subscribeVoiceContinuousSubmit,
  subscribeVoiceEngine,
  subscribeVoiceMicGesture,
  subscribeVoiceMode,
  writeVoiceContinuousSubmit,
  writeVoiceEngine,
  writeVoiceMicGesture,
  writeVoiceMode,
} from "@/lib/voice-prefs";
import {
  clearVoiceSecret,
  getVoiceSecretStatus,
  saveVoiceSecret,
  type VoiceSecretStatus,
} from "@/lib/voice-secrets";

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
const EMPTY_SECRET_STATUS: VoiceSecretStatus = { deepgramConfigured: false };

export function VoiceSection() {
  const [mode, setModeState] = useState<VoiceMode>(readVoiceMode);
  const [gesture, setGestureState] = useState<VoiceMicGesture>(
    readVoiceMicGesture,
  );
  const [submit, setSubmitState] = useState<VoiceContinuousSubmit>(
    readVoiceContinuousSubmit,
  );
  const [engine, setEngineState] = useState<VoiceEngine>(readVoiceEngine);
  const [secretStatus, setSecretStatus] =
    useState<VoiceSecretStatus>(EMPTY_SECRET_STATUS);
  const [pendingCloud, setPendingCloud] = useState(false);

  const refreshSecretStatus = async () => {
    try {
      setSecretStatus(await getVoiceSecretStatus());
    } catch {
      setSecretStatus(EMPTY_SECRET_STATUS);
    }
  };

  useEffect(() => {
    const unMode = subscribeVoiceMode(setModeState);
    const unGesture = subscribeVoiceMicGesture(setGestureState);
    const unSubmit = subscribeVoiceContinuousSubmit(setSubmitState);
    const unEngine = subscribeVoiceEngine(setEngineState);
    void refreshSecretStatus();
    return () => {
      unMode();
      unGesture();
      unSubmit();
      unEngine();
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
  const onEngineChange = (next: VoiceEngine) => {
    if (next === "cloud" && engine !== "cloud") {
      // Surface the privacy disclosure before flipping the toggle.
      // The Dialog confirm path actually persists the change.
      setPendingCloud(true);
      return;
    }
    setEngineState(next);
    writeVoiceEngine(next);
  };
  const confirmCloud = () => {
    setEngineState("cloud");
    writeVoiceEngine("cloud");
    setPendingCloud(false);
  };
  const cancelCloud = () => setPendingCloud(false);

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

      <div className="space-y-3 rounded-lg border border-border bg-card p-4">
        <div>
          <Label htmlFor="voice-engine" className="text-sm font-medium">
            Engine
          </Label>
          <p className="mt-1 text-xs text-muted-foreground">
            Local Whisper.cpp runs on-device by default — audio never
            leaves your machine. Deepgram Nova-3 is an opt-in cloud
            fallback for weak hardware; it sends audio over the
            network and needs an API key. MLX-Whisper is an opt-in
            Apple-Silicon-only fast path that runs locally but pulls
            in a separate model. The cloud + MLX wire-ups are
            scheduled post-v1; the toggles are here so the settings
            paths exist when the smokes go live.
          </p>
        </div>
        <select
          id="voice-engine"
          value={engine}
          onChange={(e) => onEngineChange(e.target.value as VoiceEngine)}
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="local">Local Whisper.cpp (default)</option>
          <option value="cloud">Deepgram Nova-3 (cloud, opt-in)</option>
          <option value="mlx">MLX-Whisper (Apple Silicon, opt-in)</option>
        </select>
        {engine === "cloud" && (
          <DeepgramApiKeyField
            configured={secretStatus.deepgramConfigured}
            onSaved={refreshSecretStatus}
          />
        )}
        {engine === "mlx" && (
          <p className="rounded-md border border-border bg-bg p-3 text-xs text-muted-foreground">
            MLX-Whisper runs ~3× faster than whisper.cpp on M-series
            hardware but needs a separate model download (~600 MB) and
            adds an MLX dependency to Thalyn&apos;s bundled Python
            runtime. Both ship in a v1.x follow-up; selecting MLX
            today surfaces a clear &quot;wire-up pending&quot; error
            when you press the mic.
          </p>
        )}
      </div>

      <Dialog
        open={pendingCloud}
        onOpenChange={(open) => {
          if (!open) cancelCloud();
        }}
      >
        <DialogContent className="sm:max-w-[480px]">
          <header className="space-y-1">
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle
                aria-hidden
                className="h-5 w-5 text-warning"
              />
              Switch voice engine to Deepgram cloud?
            </DialogTitle>
            <DialogDescription>
              The local Whisper.cpp engine processes audio entirely on
              your machine. Switching to Deepgram Nova-3 sends each
              utterance over the network to Deepgram and requires
              their API key. Lower latency on weak hardware; less
              private than local-only.
            </DialogDescription>
          </header>
          <div className="mt-5 flex items-center justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={cancelCloud}>
              Keep local
            </Button>
            <Button size="sm" variant="default" onClick={confirmCloud}>
              Switch to cloud
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </section>
  );
}

type ApiKeyStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "clearing" }
  | { kind: "cleared" }
  | { kind: "error"; message: string };

function DeepgramApiKeyField({
  configured,
  onSaved,
}: {
  configured: boolean;
  onSaved: () => Promise<void> | void;
}) {
  const [value, setValue] = useState("");
  const [reveal, setReveal] = useState(false);
  const [status, setStatus] = useState<ApiKeyStatus>({ kind: "idle" });

  const onSave = async () => {
    if (!value.trim()) return;
    setStatus({ kind: "saving" });
    try {
      await saveVoiceSecret("deepgram_api_key", value.trim());
      setStatus({ kind: "saved" });
      setValue("");
      await onSaved();
    } catch (err) {
      setStatus({
        kind: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const onClear = async () => {
    setStatus({ kind: "clearing" });
    try {
      await clearVoiceSecret("deepgram_api_key");
      setStatus({ kind: "cleared" });
      await onSaved();
    } catch (err) {
      setStatus({
        kind: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  return (
    <div className="space-y-3 rounded-md border border-border bg-bg p-3">
      <Label htmlFor="deepgram-api-key" className="text-sm font-medium">
        Deepgram API key
      </Label>
      <div className="flex items-center gap-2">
        <Input
          id="deepgram-api-key"
          type={reveal ? "text" : "password"}
          placeholder="sk_…"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
        />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={reveal ? "Hide key" : "Show key"}
          aria-pressed={reveal}
          onClick={() => setReveal((current) => !current)}
        >
          {reveal ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Stored in the OS keychain. Never rendered after save.
      </p>

      <div className="flex items-center gap-2">
        <Button
          type="button"
          onClick={onSave}
          disabled={!value.trim() || status.kind === "saving"}
          size="sm"
        >
          {status.kind === "saving" ? "Saving…" : "Save"}
        </Button>
        {configured && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onClear}
            disabled={status.kind === "clearing"}
          >
            {status.kind === "clearing" ? "Clearing…" : "Clear"}
          </Button>
        )}
      </div>

      {status.kind === "error" && (
        <p className="text-sm text-destructive">{status.message}</p>
      )}
      {status.kind === "saved" && (
        <p className="text-sm text-success">Saved.</p>
      )}
      {status.kind === "cleared" && (
        <p className="text-sm text-muted-foreground">Cleared.</p>
      )}
      {status.kind === "idle" &&
        (configured ? (
          <p className="text-sm text-success">
            A Deepgram API key is on file in the OS keychain.
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            No key on file. Cloud STT is disabled until one is saved.
          </p>
        ))}
    </div>
  );
}
