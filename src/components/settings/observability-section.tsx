import { Eye, EyeOff } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  type ObservabilitySecretName,
  type ObservabilityStatus,
  clearObservabilitySecret,
  getObservabilityStatus,
  saveObservabilitySecret,
} from "@/lib/observability";

const EMPTY_STATUS: ObservabilityStatus = {
  sentryDsnConfigured: false,
  otelOtlpEndpointConfigured: false,
};

/**
 * Two paste-and-go fields: the user's Sentry DSN (opt-in crash
 * reporting per F10.3) and the OTLP endpoint the brain ships
 * OpenTelemetry GenAI spans to (opt-in tracing). Both go straight to
 * the OS keychain via Tauri commands; neither is rendered after save.
 *
 * The empty state for both is intentional — the panel exists to be
 * disabled by default. With nothing pasted, no network traffic
 * leaves the machine for telemetry.
 */
export function ObservabilitySection() {
  const [status, setStatus] = useState<ObservabilityStatus>(EMPTY_STATUS);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      setStatus(await getObservabilityStatus());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus(EMPTY_STATUS);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  return (
    <section className="space-y-4">
      <header className="space-y-1">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
          Observability
        </h3>
        <p className="text-sm text-muted-foreground">
          Both fields are optional and default to off. Nothing leaves
          the machine until the user pastes a value.
        </p>
      </header>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <div className="space-y-3 rounded-lg border border-border bg-card p-4">
        <h4 className="text-sm font-medium">Trace destination (OTLP)</h4>
        <p className="text-xs text-muted-foreground">
          Where Thalyn ships OpenTelemetry GenAI spans. Point at a
          local Langfuse via{" "}
          <code className="font-mono">http://localhost:3000/api/public/otel</code>
          {" "}— the bundled <code className="font-mono">observability/docker-compose.yml</code>
          {" "}brings one up.
        </p>
        <SecretField
          name="otel_otlp_endpoint"
          label="OTLP endpoint"
          placeholder="http://localhost:3000/api/public/otel"
          inputType="text"
          configured={status.otelOtlpEndpointConfigured}
          onSaved={refresh}
        />
      </div>

      <div className="space-y-3 rounded-lg border border-border bg-card p-4">
        <h4 className="text-sm font-medium">Crash reporting (Sentry)</h4>
        <p className="text-xs text-muted-foreground">
          Paste your own Sentry DSN to receive crash reports in your
          Sentry project. Thalyn never sees them. Leave empty to
          disable.
        </p>
        <SecretField
          name="sentry_dsn"
          label="Sentry DSN"
          placeholder="https://public@example.ingest.sentry.io/1"
          inputType="password"
          configured={status.sentryDsnConfigured}
          onSaved={refresh}
        />
      </div>

      <p className="text-xs text-muted-foreground">
        Restart Thalyn after changing either field — these are read
        from environment variables at startup.
      </p>
    </section>
  );
}

type Status =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "clearing" }
  | { kind: "cleared" }
  | { kind: "error"; message: string };

function SecretField({
  name,
  label,
  placeholder,
  inputType,
  configured,
  onSaved,
}: {
  name: ObservabilitySecretName;
  label: string;
  placeholder: string;
  inputType: "text" | "password";
  configured: boolean;
  onSaved: () => Promise<void> | void;
}) {
  const [value, setValue] = useState("");
  const [reveal, setReveal] = useState(false);
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  const onSave = async () => {
    if (!value.trim()) return;
    setStatus({ kind: "saving" });
    try {
      await saveObservabilitySecret(name, value.trim());
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
      await clearObservabilitySecret(name);
      setStatus({ kind: "cleared" });
      await onSaved();
    } catch (err) {
      setStatus({
        kind: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const fieldId = `observability-${name}`;
  const isPassword = inputType === "password";
  const effectiveType = isPassword && !reveal ? "password" : "text";

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor={fieldId}>{label}</Label>
        <div className="flex items-center gap-2">
          <Input
            id={fieldId}
            type={effectiveType}
            placeholder={placeholder}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            spellCheck={false}
            autoComplete="off"
            autoCapitalize="off"
            autoCorrect="off"
          />
          {isPassword ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={reveal ? "Hide value" : "Show value"}
              aria-pressed={reveal}
              onClick={() => setReveal((current) => !current)}
            >
              {reveal ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
            </Button>
          ) : null}
        </div>
        <p className="text-xs text-muted-foreground">
          Stored in the OS keychain. Forwarded to Thalyn as an
          environment variable on next launch.
        </p>
      </div>

      <div className="flex items-center gap-2">
        <Button
          type="button"
          onClick={onSave}
          disabled={!value.trim() || status.kind === "saving"}
        >
          {status.kind === "saving" ? "Saving…" : "Save"}
        </Button>
        {configured ? (
          <Button
            type="button"
            variant="outline"
            onClick={onClear}
            disabled={status.kind === "clearing"}
          >
            {status.kind === "clearing" ? "Clearing…" : "Clear"}
          </Button>
        ) : null}
      </div>

      <StatusLine status={status} configured={configured} />
    </div>
  );
}

function StatusLine({
  status,
  configured,
}: {
  status: Status;
  configured: boolean;
}) {
  if (status.kind === "error") {
    return <p className="text-sm text-destructive">{status.message}</p>;
  }
  if (status.kind === "saved") {
    return <p className="text-sm text-success">Saved.</p>;
  }
  if (status.kind === "cleared") {
    return <p className="text-sm text-muted-foreground">Cleared.</p>;
  }
  if (configured) {
    return (
      <p className="text-sm text-success">A value is on file in the OS keychain.</p>
    );
  }
  return (
    <p className="text-sm text-muted-foreground">
      No value on file. This destination is disabled until one is saved.
    </p>
  );
}
