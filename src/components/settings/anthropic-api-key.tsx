import { Eye, EyeOff } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { clearApiKey, isProviderConfigured, saveApiKey } from "@/lib/providers";

type Status =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "clearing" }
  | { kind: "cleared" }
  | { kind: "error"; message: string };

const PROVIDER_ID = "anthropic";

/**
 * Paste-the-key flow for the Anthropic provider. The key never lives
 * in localStorage or any rendered text — it's posted straight to the
 * keychain via the Tauri command and forgotten by the renderer once
 * saved.
 */
export function AnthropicApiKeyForm({
  onConfiguredChange,
}: {
  onConfiguredChange?: (configured: boolean) => void;
}) {
  const [value, setValue] = useState("");
  const [reveal, setReveal] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  useEffect(() => {
    isProviderConfigured(PROVIDER_ID)
      .then((flag) => {
        setConfigured(flag);
        onConfiguredChange?.(flag);
      })
      .catch(() => {
        // The Tauri command is missing in the storybook / playwright
        // environments; treat that as "not configured" silently.
        setConfigured(false);
        onConfiguredChange?.(false);
      });
  }, [onConfiguredChange]);

  const onSave = async () => {
    if (!value.trim()) return;
    setStatus({ kind: "saving" });
    try {
      await saveApiKey(PROVIDER_ID, value.trim());
      setStatus({ kind: "saved" });
      setConfigured(true);
      onConfiguredChange?.(true);
      setValue("");
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
      await clearApiKey(PROVIDER_ID);
      setStatus({ kind: "cleared" });
      setConfigured(false);
      onConfiguredChange?.(false);
    } catch (err) {
      setStatus({
        kind: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="anthropic-api-key">Anthropic API key</Label>
        <div className="flex items-center gap-2">
          <Input
            id="anthropic-api-key"
            type={reveal ? "text" : "password"}
            placeholder="sk-ant-…"
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
          Stored in the OS keychain. Never written to disk in plain text;
          never sent anywhere except to Anthropic from this device.
        </p>
      </div>

      <div className="flex items-center gap-2">
        <Button
          type="button"
          onClick={onSave}
          disabled={!value.trim() || status.kind === "saving"}
        >
          {status.kind === "saving" ? "Saving…" : "Save key"}
        </Button>
        {configured && (
          <Button
            type="button"
            variant="outline"
            onClick={onClear}
            disabled={status.kind === "clearing"}
          >
            {status.kind === "clearing" ? "Clearing…" : "Clear key"}
          </Button>
        )}
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
    return <p className="text-sm text-danger">{status.message}</p>;
  }
  if (status.kind === "saved") {
    return <p className="text-sm text-success">Key saved.</p>;
  }
  if (status.kind === "cleared") {
    return <p className="text-sm text-muted-foreground">Key cleared.</p>;
  }
  if (configured) {
    return (
      <p className="text-sm text-success">
        Connected — a key is on file in the OS keychain.
      </p>
    );
  }
  return (
    <p className="text-sm text-muted-foreground">
      No key on file yet — the Anthropic provider will be unavailable
      until one is saved.
    </p>
  );
}
