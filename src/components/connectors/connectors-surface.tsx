import { Check, Plug, Power, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  type ConnectorAuth,
  type ConnectorDescriptor,
  type ConnectorTool,
  type InstalledConnector,
  clearSecret,
  getCatalog,
  getInstalled,
  getSecretStatus,
  installConnector,
  saveSecret,
  setEnabled,
  setGrants,
  startConnector,
  stopConnector,
  uninstallConnector,
} from "@/lib/mcp";

type ConnectorsState = {
  catalog: ConnectorDescriptor[];
  installed: InstalledConnector[];
  loading: boolean;
  error: string | null;
};

const EMPTY: ConnectorsState = {
  catalog: [],
  installed: [],
  loading: true,
  error: null,
};

/**
 * Connector marketplace + per-connector grants — main-panel surface.
 * Lists every descriptor from the brain catalog, marks the installed
 * ones, and lets the user paste secrets, grant or revoke individual
 * tools, and start or stop the underlying MCP session.
 *
 * Grants gate every tool call before it reaches the wire so a
 * sensitive tool ("post a Slack message") stays revoked until the
 * user explicitly toggles it on.
 *
 * The surface is split into a connected wrapper (this) and a
 * presentational [`ConnectorsView`] so Storybook can render every
 * outer state — loading, empty catalog, populated, error — without
 * touching the Tauri invoke surface. The per-card sub-components
 * (install / configure / grant / start) own their own per-card
 * state and call Tauri directly; that's fine because each card
 * scopes its busy/error to itself.
 */
export function ConnectorsSurface() {
  const [state, setState] = useState<ConnectorsState>(EMPTY);

  const refresh = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const [catalog, installed] = await Promise.all([
        getCatalog(),
        getInstalled(),
      ]);
      setState({
        catalog: catalog.connectors,
        installed: installed.installed,
        loading: false,
        error: null,
      });
    } catch (err) {
      setState({
        catalog: [],
        installed: [],
        loading: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <ConnectorsView
      catalog={state.catalog}
      installed={state.installed}
      loading={state.loading}
      error={state.error}
      onRefresh={() => void refresh()}
      onChanged={() => void refresh()}
    />
  );
}

export function ConnectorsView({
  catalog,
  installed,
  loading,
  error,
  onRefresh,
  onChanged,
}: {
  catalog: ConnectorDescriptor[];
  installed: InstalledConnector[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onChanged: () => Promise<void> | void;
}) {
  const installedById = useMemo(() => {
    const map = new Map<string, InstalledConnector>();
    for (const item of installed) {
      map.set(item.connectorId, item);
    }
    return map;
  }, [installed]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-border bg-surface px-4 py-2">
        <div className="flex items-center gap-2">
          <Plug aria-hidden className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">Connectors</h2>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Refresh connectors"
          onClick={onRefresh}
        >
          <RefreshCw aria-hidden className="size-4" />
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        <p className="mb-4 max-w-2xl text-xs text-muted-foreground">
          MCP connectors the brain can call. Install one, paste its
          credentials, and grant individual tools. Sensitive tools
          (post a message, create an event, send an action) stay
          revoked by default.
        </p>

        {error ? (
          <p
            role="alert"
            className="mb-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-foreground"
          >
            {error}
          </p>
        ) : null}

        {loading ? (
          <p className="text-sm text-muted-foreground">Loading connectors…</p>
        ) : catalog.length === 0 ? (
          <EmptyState />
        ) : (
          <ul className="space-y-3">
            {catalog.map((descriptor) => (
              <li key={descriptor.connectorId}>
                <ConnectorCard
                  descriptor={descriptor}
                  installed={installedById.get(descriptor.connectorId) ?? null}
                  onChanged={onChanged}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-3 rounded-md border border-dashed border-border px-6 py-12 text-center">
      <Plug aria-hidden className="size-8 text-muted-foreground" />
      <h3 className="text-sm font-medium">No connectors available</h3>
      <p className="text-xs text-muted-foreground">
        The brain reported an empty catalog. Confirm the brain sidecar
        is running, then refresh.
      </p>
    </div>
  );
}

function ConnectorCard({
  descriptor,
  installed,
  onChanged,
}: {
  descriptor: ConnectorDescriptor;
  installed: InstalledConnector | null;
  onChanged: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onInstall = async () => {
    setBusy(true);
    setError(null);
    try {
      await installConnector(descriptor.connectorId);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onUninstall = async () => {
    setBusy(true);
    setError(null);
    try {
      await uninstallConnector(descriptor.connectorId);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="rounded-lg border border-border bg-card p-4 space-y-4">
      <header className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h3 className="flex items-center gap-2 text-sm font-medium">
            <Plug aria-hidden className="size-4 text-muted-foreground" />
            {descriptor.displayName}
            {descriptor.firstParty ? (
              <span className="rounded bg-secondary px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-secondary-foreground">
                First-party
              </span>
            ) : null}
          </h3>
          <p className="text-sm text-muted-foreground">{descriptor.summary}</p>
          {descriptor.homepage ? (
            <p className="text-xs text-muted-foreground">
              Vendor:{" "}
              <a
                href={descriptor.homepage}
                target="_blank"
                rel="noreferrer"
                className="underline underline-offset-2"
              >
                {descriptor.vendor}
              </a>
            </p>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          {installed ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void onUninstall()}
              disabled={busy}
              aria-label={`Uninstall ${descriptor.displayName}`}
            >
              <Trash2 aria-hidden className="size-4" />
              Uninstall
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              onClick={() => void onInstall()}
              disabled={busy}
            >
              {busy ? "Installing…" : "Install"}
            </Button>
          )}
        </div>
      </header>

      {error ? <InlineError message={error} /> : null}

      {installed ? (
        <InstalledDetails
          descriptor={descriptor}
          installed={installed}
          onChanged={onChanged}
        />
      ) : (
        <CatalogPreview descriptor={descriptor} />
      )}
    </article>
  );
}

function CatalogPreview({ descriptor }: { descriptor: ConnectorDescriptor }) {
  return (
    <div className="space-y-2 text-xs">
      <p className="font-medium uppercase tracking-wider text-muted-foreground">
        Tools
      </p>
      <ul className="grid grid-cols-1 gap-1 text-muted-foreground sm:grid-cols-2">
        {descriptor.advertisedTools.map((tool) => (
          <li key={tool.name} className="flex items-baseline gap-1.5">
            <span className="font-mono text-[11px]">{tool.name}</span>
            {tool.sensitive ? (
              <span className="rounded bg-warning/15 px-1 py-0.5 text-[10px] font-medium uppercase tracking-wider text-foreground">
                sensitive
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function InstalledDetails({
  descriptor,
  installed,
  onChanged,
}: {
  descriptor: ConnectorDescriptor;
  installed: InstalledConnector;
  onChanged: () => Promise<void> | void;
}) {
  return (
    <div className="space-y-4">
      <SecretsBlock
        descriptor={descriptor}
        running={installed.running}
        onChanged={onChanged}
      />
      <GrantsBlock
        descriptor={descriptor}
        installed={installed}
        onChanged={onChanged}
      />
      <SessionBlock installed={installed} onChanged={onChanged} />
      {installed.lastError ? (
        <InlineError message={`Last error: ${installed.lastError}`} />
      ) : null}
    </div>
  );
}

function SecretsBlock({
  descriptor,
  running,
  onChanged,
}: {
  descriptor: ConnectorDescriptor;
  running: boolean;
  onChanged: () => Promise<void> | void;
}) {
  const [statuses, setStatuses] = useState<Record<string, boolean>>({});

  const refresh = useCallback(async () => {
    if (descriptor.requiredSecrets.length === 0) return;
    try {
      const next = await getSecretStatus(
        descriptor.connectorId,
        descriptor.requiredSecrets.map((s) => s.key),
      );
      setStatuses(next);
    } catch {
      // Surface failures via the per-field status; nothing fatal.
    }
  }, [descriptor.connectorId, descriptor.requiredSecrets]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (descriptor.requiredSecrets.length === 0) return null;

  return (
    <div className="space-y-3">
      <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
        Credentials
      </p>
      {descriptor.requiredSecrets.map((slot) => (
        <SecretField
          key={slot.key}
          connectorId={descriptor.connectorId}
          slot={slot}
          configured={!!statuses[slot.key]}
          disabled={running}
          onChanged={async () => {
            await refresh();
            await onChanged();
          }}
        />
      ))}
      {running ? (
        <p className="text-xs text-muted-foreground">
          Stop the connector to change credentials.
        </p>
      ) : null}
    </div>
  );
}

function SecretField({
  connectorId,
  slot,
  configured,
  disabled,
  onChanged,
}: {
  connectorId: string;
  slot: ConnectorAuth;
  configured: boolean;
  disabled: boolean;
  onChanged: () => Promise<void> | void;
}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fieldId = `mcp-secret-${connectorId}-${slot.key}`;

  const onSave = async () => {
    if (!value.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await saveSecret(connectorId, slot.key, value.trim());
      setValue("");
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onClear = async () => {
    setBusy(true);
    setError(null);
    try {
      await clearSecret(connectorId, slot.key);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1.5">
      <Label htmlFor={fieldId} className="flex items-center gap-1.5">
        {slot.label}
        {configured ? (
          <Check aria-hidden className="size-3.5 text-success" />
        ) : null}
      </Label>
      <p className="text-xs text-muted-foreground">{slot.description}</p>
      <div className="flex items-center gap-2">
        <Input
          id={fieldId}
          type="password"
          placeholder={slot.placeholder || "Paste value"}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          spellCheck={false}
          autoComplete="off"
          disabled={disabled || busy}
        />
        <Button
          type="button"
          size="sm"
          onClick={() => void onSave()}
          disabled={!value.trim() || disabled || busy}
        >
          Save
        </Button>
        {configured ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void onClear()}
            disabled={disabled || busy}
          >
            Clear
          </Button>
        ) : null}
      </div>
      {error ? <InlineError message={error} /> : null}
    </div>
  );
}

function GrantsBlock({
  descriptor,
  installed,
  onChanged,
}: {
  descriptor: ConnectorDescriptor;
  installed: InstalledConnector;
  onChanged: () => Promise<void> | void;
}) {
  // Live tool list (after the connector has been started) is the
  // source of truth for what's actually callable. Falling back to
  // the static catalog lets the user grant tools before the first
  // start — the brain will reject calls to anything that the live
  // server doesn't expose.
  const tools: ConnectorTool[] = useMemo(() => {
    if (installed.liveTools && installed.liveTools.length > 0) {
      const advertised = new Map(
        descriptor.advertisedTools.map((t) => [t.name, t]),
      );
      return installed.liveTools.map((tool) => ({
        name: tool.name,
        description:
          tool.description || advertised.get(tool.name)?.description || "",
        sensitive: advertised.get(tool.name)?.sensitive ?? false,
      }));
    }
    return descriptor.advertisedTools;
  }, [descriptor.advertisedTools, installed.liveTools]);

  const granted = new Set(installed.grantedTools);
  const [busy, setBusy] = useState(false);

  const toggle = async (toolName: string) => {
    const next = new Set(granted);
    if (next.has(toolName)) {
      next.delete(toolName);
    } else {
      next.add(toolName);
    }
    setBusy(true);
    try {
      await setGrants(installed.connectorId, Array.from(next));
      await onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
        Tools
      </p>
      <ul className="space-y-1.5">
        {tools.map((tool) => {
          const isGranted = granted.has(tool.name);
          return (
            <li
              key={tool.name}
              className="flex items-center justify-between gap-3 rounded-md border border-border/50 px-3 py-2"
            >
              <div className="min-w-0 space-y-0.5">
                <div className="flex items-center gap-1.5 text-sm">
                  <span className="font-mono text-xs">{tool.name}</span>
                  {tool.sensitive ? (
                    <span className="rounded bg-warning/15 px-1 py-0.5 text-[10px] font-medium uppercase tracking-wider text-foreground">
                      sensitive
                    </span>
                  ) : null}
                </div>
                {tool.description ? (
                  <p className="truncate text-xs text-muted-foreground">
                    {tool.description}
                  </p>
                ) : null}
              </div>
              <Button
                type="button"
                size="sm"
                variant={isGranted ? "default" : "outline"}
                aria-pressed={isGranted}
                onClick={() => void toggle(tool.name)}
                disabled={busy}
              >
                {isGranted ? "Granted" : "Grant"}
              </Button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function SessionBlock({
  installed,
  onChanged,
}: {
  installed: InstalledConnector;
  onChanged: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onStart = async () => {
    setBusy(true);
    setError(null);
    try {
      const keys = installed.descriptor.requiredSecrets.map((s) => s.key);
      await startConnector(installed.connectorId, keys);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onStop = async () => {
    setBusy(true);
    setError(null);
    try {
      await stopConnector(installed.connectorId);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onToggleEnabled = async () => {
    setBusy(true);
    setError(null);
    try {
      await setEnabled(installed.connectorId, !installed.enabled);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      {installed.running ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => void onStop()}
          disabled={busy}
        >
          <Power aria-hidden className="size-4" />
          Stop
        </Button>
      ) : (
        <Button
          type="button"
          size="sm"
          onClick={() => void onStart()}
          disabled={busy || !installed.enabled}
        >
          <Power aria-hidden className="size-4" />
          {busy ? "Starting…" : "Start"}
        </Button>
      )}
      <Button
        type="button"
        size="sm"
        variant="outline"
        aria-pressed={installed.enabled}
        onClick={() => void onToggleEnabled()}
        disabled={busy}
      >
        {installed.enabled ? "Disable" : "Enable"}
      </Button>
      <Badge
        tone={installed.running ? "success" : "muted"}
        className="ml-auto"
      >
        {installed.running
          ? "Running"
          : installed.enabled
            ? "Stopped"
            : "Disabled"}
      </Badge>
      {error ? (
        <div className="basis-full">
          <InlineError message={error} />
        </div>
      ) : null}
    </div>
  );
}

/**
 * Tinted-container error banner used for inline + block alerts so
 * the destructive colour stays as the visual cue (border + bg) but
 * the text uses the high-contrast foreground token. Matches the
 * pattern already used in the browser surface and the agents/logs
 * surfaces.
 */
function InlineError({ message }: { message: string }) {
  return (
    <p
      role="alert"
      className="rounded-md border border-destructive/40 bg-destructive/10 px-2.5 py-1.5 text-xs text-foreground"
    >
      {message}
    </p>
  );
}
