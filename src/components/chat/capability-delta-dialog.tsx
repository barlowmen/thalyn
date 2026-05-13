import { useEffect, useState } from "react";

import { AlertTriangle, Info } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  type CapabilityDelta,
  providerDelta,
  type ProviderMeta,
} from "@/lib/providers";

type Props = {
  pending: { from: ProviderMeta; to: ProviderMeta } | null;
  onCancel: () => void;
  onConfirm: (toId: string) => void;
};

const DIMENSION_LABEL: Record<string, string> = {
  maxContextTokens: "Context window",
  supportsToolUse: "Tool use",
  toolUseReliability: "Tool-use reliability",
  supportsVision: "Vision",
  supportsStreaming: "Streaming",
  local: "Runs locally",
  authBackend: "Auth backend",
};

// Human-readable names for AuthBackendKind values; mirrors the
// brain's ``_DISPLAY_NAMES`` mapping in ``auth_registry.py`` so the
// dialog speaks the same language as the wizard.
const AUTH_BACKEND_LABEL: Record<string, string> = {
  claude_subscription: "Claude subscription",
  anthropic_api: "Anthropic API key",
  openai_compat: "OpenAI-compatible endpoint",
  ollama: "Ollama (local)",
  llama_cpp: "llama.cpp (local)",
  mlx: "MLX (Apple Silicon)",
};

/**
 * Shown when the user picks a different provider in the chat
 * header. Fetches the capability delta between the current and
 * selected provider, lays out one row per changed dimension with a
 * before / after, and gates the swap behind an explicit confirm.
 *
 * If the delta turns out to be empty (capability-equivalent swap),
 * we commit the change immediately without showing a dialog —
 * confirming "yes, change to the same thing" is friction the user
 * doesn't need.
 */
export function CapabilityDeltaDialog({
  pending,
  onCancel,
  onConfirm,
}: Props) {
  const [delta, setDelta] = useState<CapabilityDelta | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pending) {
      setDelta(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    providerDelta(pending.from.id, pending.to.id)
      .then((result) => {
        if (cancelled) return;
        setDelta(result);
        if (result.changes.length === 0) {
          onConfirm(pending.to.id);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [pending, onConfirm]);

  const open = pending !== null && (delta?.changes.length ?? 0) > 0;
  const handleOpenChange = (value: boolean) => {
    if (!value) onCancel();
  };

  if (!pending) return null;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <header className="space-y-1">
          <DialogTitle>
            Switch to {pending.to.displayName}?
          </DialogTitle>
          <DialogDescription>
            The new provider differs from {pending.from.displayName} in
            the following ways. Any dimensions not listed are
            unchanged.
          </DialogDescription>
        </header>

        {loading && (
          <p className="mt-4 text-xs text-muted-foreground">
            Computing delta…
          </p>
        )}
        {error && <p className="mt-4 text-xs text-danger">{error}</p>}

        {delta && delta.changes.length > 0 && (
          <ul className="mt-4 space-y-2 text-sm">
            {delta.changes.map((change) => (
              <li
                key={change.dimension}
                className="flex items-start gap-2 rounded-md border border-border bg-bg px-3 py-2"
              >
                {change.severity === "warning" ? (
                  <AlertTriangle
                    className="mt-0.5 h-4 w-4 text-warning"
                    aria-hidden
                  />
                ) : (
                  <Info
                    className="mt-0.5 h-4 w-4 text-muted-foreground"
                    aria-hidden
                  />
                )}
                <div className="flex-1 overflow-hidden">
                  <p className="text-sm font-medium">
                    {DIMENSION_LABEL[change.dimension] ?? change.dimension}
                  </p>
                  <p className="text-[11px] text-muted-foreground">
                    {formatValue(change.before, change.dimension)} →{" "}
                    {formatValue(change.after, change.dimension)}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-5 flex items-center justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="default"
            onClick={() => onConfirm(pending.to.id)}
            disabled={loading || !delta}
          >
            Switch provider
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function formatValue(value: unknown, dimension?: string): string {
  if (dimension === "authBackend" && typeof value === "string") {
    return AUTH_BACKEND_LABEL[value] ?? value;
  }
  if (value === true) return "Yes";
  if (value === false) return "No";
  if (typeof value === "number") return value.toLocaleString();
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "—";
  return JSON.stringify(value);
}
