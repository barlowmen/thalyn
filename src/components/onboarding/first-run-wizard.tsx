/**
 * First-run wizard.
 *
 * Per F9.1 the new-install flow is three screens then drop-in:
 *
 * 1. Brain selection — three primary options. Claude subscription is
 *    pre-selected when the bundled CLI's probe says ``loggedIn``;
 *    otherwise the user chooses Local LLM (Ollama) or Other API.
 * 2. Setup walkthrough for the chosen backend. Subscription needs
 *    nothing; API key shows the paste form; Ollama / OpenAI-compat
 *    show their own next steps. The walkthrough re-runs the probe
 *    after the user takes an action so they see "ready" before they
 *    hit "continue".
 * 3. Drop-in — set ``thalyn:first-run-completed=true`` so the wizard
 *    doesn't re-show, then dismiss; the chat surface picks up the
 *    user's selection and Thalyn introduces himself in his own first
 *    message of the eternal thread.
 *
 * The wizard is a controlled overlay rendered above the AppShell when
 * ``isFirstRun`` reads false from localStorage. It calls the brain
 * directly via the Tauri ``auth_*`` commands; the brain's
 * AuthBackendRegistry is the source of truth for which backend ends
 * up active.
 */

import { Loader2 } from "lucide-react";
import {
  type FormEvent,
  type ReactElement,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  type AuthBackendDescriptor,
  type AuthBackendKind,
  listAuthBackends,
  probeAuthBackend,
  setActiveAuthBackend,
} from "@/lib/auth";
import { saveApiKey } from "@/lib/providers";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "thalyn:first-run-completed";
const RECOMMENDED_KIND: AuthBackendKind = "claude_subscription";

export type FirstRunWizardProps = {
  /**
   * Forced override (used by Storybook / tests). When undefined, the
   * wizard reads localStorage to decide whether to render.
   */
  forceOpen?: boolean;
  /**
   * Called once the wizard finishes. Production consumers update the
   * "first run completed" flag inside the wizard; this callback is for
   * external state (e.g. focusing the chat composer).
   */
  onComplete?: (kind: AuthBackendKind) => void;
};

export function FirstRunWizard({
  forceOpen,
  onComplete,
}: FirstRunWizardProps): ReactElement | null {
  const [open, setOpen] = useState<boolean>(() => {
    if (typeof forceOpen === "boolean") return forceOpen;
    if (typeof window === "undefined") return false;
    // Visual-regression / e2e harnesses set ``navigator.webdriver`` and
    // can't drive the wizard's brain calls; skip the overlay so they
    // exercise the steady-state UI directly.
    if (typeof navigator !== "undefined" && navigator.webdriver) return false;
    return window.localStorage.getItem(STORAGE_KEY) !== "true";
  });
  const [step, setStep] = useState<"select" | "configure">("select");
  const [backends, setBackends] = useState<AuthBackendDescriptor[] | null>(null);
  const [activeKind, setActiveKind] = useState<AuthBackendKind | null>(null);
  const [chosenKind, setChosenKind] = useState<AuthBackendKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Pre-load the auth list as soon as the wizard mounts. The probe
  // results decide which option the wizard recommends.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setError(null);
    setBackends(null);
    listAuthBackends()
      .then((result) => {
        if (cancelled) return;
        setBackends(result.backends);
        setActiveKind(result.activeKind);
        setChosenKind((prev) => prev ?? recommendKind(result.backends));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const recommended = useMemo(() => {
    if (!backends) return null;
    return recommendKind(backends);
  }, [backends]);

  const finish = useCallback(
    async (kind: AuthBackendKind) => {
      setBusy(true);
      setError(null);
      try {
        const result = await setActiveAuthBackend(kind);
        if (typeof window !== "undefined") {
          window.localStorage.setItem(STORAGE_KEY, "true");
        }
        setActiveKind(result.activeKind);
        setOpen(false);
        onComplete?.(kind);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [onComplete],
  );

  const refreshProbe = useCallback(
    async (kind: AuthBackendKind) => {
      setBusy(true);
      setError(null);
      try {
        const probed = await probeAuthBackend(kind);
        setBackends((prev) =>
          prev
            ? prev.map((b) => (b.kind === kind ? { ...b, probe: probed.probe } : b))
            : prev,
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="first-run-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/95 backdrop-blur-sm"
    >
      <div className="mx-4 w-full max-w-2xl rounded-lg border border-border bg-card p-6 shadow-lg">
        <header className="mb-4 space-y-1">
          <h1 id="first-run-title" className="text-lg font-semibold">
            Welcome to Thalyn
          </h1>
          <p className="text-sm text-muted-foreground">
            {step === "select"
              ? "Pick how Thalyn will reach a model. You can change this later."
              : chosenKind && describeStep(chosenKind)}
          </p>
        </header>

        {!backends && !error && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 aria-hidden className="size-4 animate-spin" />
            Loading available backends…
          </div>
        )}

        {error && (
          <p
            role="alert"
            className="mb-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-foreground"
          >
            {error}
          </p>
        )}

        {backends && step === "select" && (
          <BackendSelectStep
            backends={backends}
            chosen={chosenKind}
            recommended={recommended}
            activeKind={activeKind}
            onChoose={setChosenKind}
            onContinue={() => {
              if (!chosenKind) return;
              const descriptor = backends.find((b) => b.kind === chosenKind);
              if (descriptor && stepNeedsConfigure(descriptor)) {
                setStep("configure");
              } else {
                void finish(chosenKind);
              }
            }}
            busy={busy}
          />
        )}

        {backends && step === "configure" && chosenKind && (
          <BackendConfigureStep
            kind={chosenKind}
            backend={backends.find((b) => b.kind === chosenKind)}
            onBack={() => setStep("select")}
            onRefresh={() => refreshProbe(chosenKind)}
            onSavedApiKey={async (key) => {
              if (chosenKind !== "anthropic_api") return;
              setBusy(true);
              setError(null);
              try {
                await saveApiKey("anthropic", key);
                await refreshProbe(chosenKind);
              } catch (err) {
                setError(err instanceof Error ? err.message : String(err));
              } finally {
                setBusy(false);
              }
            }}
            onContinue={() => void finish(chosenKind)}
            busy={busy}
          />
        )}
      </div>
    </div>
  );
}

function recommendKind(backends: AuthBackendDescriptor[]): AuthBackendKind {
  // The Claude subscription path is the F9.1 default when the probe
  // says authenticated. Otherwise prefer the API-key path because it's
  // the only Anthropic option that's set up by pasting a key.
  const subscription = backends.find((b) => b.kind === RECOMMENDED_KIND);
  if (subscription && subscription.probe.authenticated) {
    return RECOMMENDED_KIND;
  }
  return "anthropic_api";
}

function stepNeedsConfigure(backend: AuthBackendDescriptor): boolean {
  if (backend.probe.authenticated) return false;
  // Subscription auth without a logged-in CLI also needs the configure
  // step (which surfaces the "log in via terminal" instruction).
  return true;
}

function describeStep(kind: AuthBackendKind): string {
  switch (kind) {
    case "claude_subscription":
      return "Sign in to your Claude subscription via the bundled CLI.";
    case "anthropic_api":
      return "Paste your Anthropic API key. It stays in the OS keychain.";
    case "openai_compat":
      return "Configure your OpenAI-compatible endpoint.";
    case "ollama":
      return "Make sure Ollama is running locally on port 11434.";
    case "llama_cpp":
      return "Configure your llama.cpp HTTP server.";
    case "mlx":
      return "MLX runs on-device on Apple Silicon — no key needed.";
  }
}

type BackendSelectStepProps = {
  backends: AuthBackendDescriptor[];
  chosen: AuthBackendKind | null;
  recommended: AuthBackendKind | null;
  activeKind: AuthBackendKind | null;
  onChoose: (kind: AuthBackendKind) => void;
  onContinue: () => void;
  busy: boolean;
};

function BackendSelectStep({
  backends,
  chosen,
  recommended,
  activeKind,
  onChoose,
  onContinue,
  busy,
}: BackendSelectStepProps) {
  return (
    <div className="space-y-4">
      <ul className="space-y-2" role="radiogroup" aria-label="Auth backend">
        {backends.map((backend) => (
          <BackendOption
            key={backend.kind}
            backend={backend}
            chosen={chosen === backend.kind}
            recommended={recommended === backend.kind}
            isActive={activeKind === backend.kind}
            onChoose={onChoose}
          />
        ))}
      </ul>
      <div className="flex justify-end">
        <Button onClick={onContinue} disabled={!chosen || busy}>
          Continue
        </Button>
      </div>
    </div>
  );
}

type BackendOptionProps = {
  backend: AuthBackendDescriptor;
  chosen: boolean;
  recommended: boolean;
  isActive: boolean;
  onChoose: (kind: AuthBackendKind) => void;
};

function BackendOption({
  backend,
  chosen,
  recommended,
  isActive,
  onChoose,
}: BackendOptionProps) {
  return (
    <li>
      <button
        type="button"
        role="radio"
        aria-checked={chosen}
        onClick={() => onChoose(backend.kind)}
        className={cn(
          "flex w-full flex-col gap-1 rounded-md border px-4 py-3 text-left transition-colors",
          "hover:bg-accent hover:text-accent-foreground",
          chosen
            ? "border-primary bg-primary/5"
            : "border-border bg-card",
        )}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium">{backend.displayName}</span>
          <span className="flex items-center gap-2 text-xs">
            {recommended && (
              <span className="rounded bg-primary px-2 py-0.5 text-primary-foreground">
                Recommended
              </span>
            )}
            {isActive && !recommended && (
              <span className="rounded bg-secondary px-2 py-0.5 text-secondary-foreground">
                Active
              </span>
            )}
            <ProbeBadge backend={backend} />
          </span>
        </div>
        <p className="text-xs text-muted-foreground">{backend.description}</p>
        {backend.probe.detail && (
          <p className="text-xs text-muted-foreground">{backend.probe.detail}</p>
        )}
        {backend.probe.error && (
          <p className="text-xs text-danger">{backend.probe.error}</p>
        )}
      </button>
    </li>
  );
}

function ProbeBadge({ backend }: { backend: AuthBackendDescriptor }) {
  const { detected, authenticated } = backend.probe;
  if (authenticated) {
    return (
      <span className="rounded bg-green-500/15 px-2 py-0.5 text-green-700 dark:text-green-400">
        Ready
      </span>
    );
  }
  if (detected) {
    return (
      <span className="rounded bg-amber-500/15 px-2 py-0.5 text-amber-700 dark:text-amber-400">
        Needs setup
      </span>
    );
  }
  return (
    <span className="rounded bg-muted px-2 py-0.5 text-muted-foreground">
      Not detected
    </span>
  );
}

type BackendConfigureStepProps = {
  kind: AuthBackendKind;
  backend: AuthBackendDescriptor | undefined;
  onBack: () => void;
  onRefresh: () => void;
  onSavedApiKey: (key: string) => Promise<void>;
  onContinue: () => void;
  busy: boolean;
};

function BackendConfigureStep({
  kind,
  backend,
  onBack,
  onRefresh,
  onSavedApiKey,
  onContinue,
  busy,
}: BackendConfigureStepProps) {
  const [apiKey, setApiKey] = useState("");
  const ready = backend?.probe.authenticated ?? false;

  const handleSave = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const trimmed = apiKey.trim();
      if (!trimmed) return;
      await onSavedApiKey(trimmed);
      setApiKey("");
    },
    [apiKey, onSavedApiKey],
  );

  return (
    <div className="space-y-4">
      {kind === "anthropic_api" && !ready && (
        <form onSubmit={handleSave} className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="first-run-api-key">Anthropic API key</Label>
            <Input
              id="first-run-api-key"
              type="password"
              autoComplete="off"
              autoFocus
              placeholder="sk-ant-…"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              disabled={busy}
            />
          </div>
          <Button type="submit" disabled={!apiKey.trim() || busy}>
            Save key
          </Button>
        </form>
      )}

      {kind === "claude_subscription" && !ready && (
        <p className="text-sm text-muted-foreground">
          Run <code className="font-mono">claude /login</code> in a terminal to
          sign in, then click <em>Re-check</em>.
        </p>
      )}

      {kind === "ollama" && !ready && (
        <p className="text-sm text-muted-foreground">
          Start Ollama (default: <code>http://localhost:11434</code>), then
          click <em>Re-check</em>.
        </p>
      )}

      {kind === "llama_cpp" && !ready && (
        <p className="text-sm text-muted-foreground">
          Start a llama.cpp HTTP server on{" "}
          <code>http://localhost:8080</code>, then click <em>Re-check</em>.
        </p>
      )}

      {kind === "mlx" && !ready && (
        <p className="text-sm text-muted-foreground">
          MLX needs Apple Silicon. If you're on a different platform, go back
          and pick another option.
        </p>
      )}

      {kind === "openai_compat" && !ready && (
        <p className="text-sm text-muted-foreground">
          Set your <code>OPENAI_API_KEY</code> env var (or use the settings
          form after first-run completes), then click <em>Re-check</em>.
        </p>
      )}

      {ready && backend?.probe.detail && (
        <p className="text-sm text-muted-foreground">{backend.probe.detail}</p>
      )}

      <div className="flex items-center justify-between">
        <Button type="button" variant="ghost" onClick={onBack} disabled={busy}>
          Back
        </Button>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={onRefresh}
            disabled={busy}
          >
            Re-check
          </Button>
          <Button type="button" onClick={onContinue} disabled={!ready || busy}>
            Continue
          </Button>
        </div>
      </div>
    </div>
  );
}
