import { Check, Mail, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  type EmailAccount,
  type EmailCredentialsStatus,
  type EmailProvider,
  addAccount,
  credentialsStatus,
  listAccounts,
  removeAccount,
  saveCredentials,
} from "@/lib/email";

const EMPTY: EmailCredentialsStatus = {
  refreshTokenConfigured: false,
  clientIdConfigured: false,
  clientSecretConfigured: false,
};

/**
 * Per-account credential management for Gmail and Microsoft Graph.
 * The user adds an account row, then pastes a refresh token + OAuth
 * client id (and secret if their app is confidential). Tokens land
 * in the OS keychain; nothing renders the secret values back.
 */
export function EmailSection() {
  const [accounts, setAccounts] = useState<EmailAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      const result = await listAccounts();
      setAccounts(result.accounts);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <section className="space-y-4">
      <header className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
            Email accounts
          </h3>
          <p className="text-sm text-muted-foreground">
            Gmail and Microsoft inboxes Thalyn can read and draft to.
            Bring your own OAuth client; refresh tokens stay on your
            machine.
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          onClick={() => setAdding(true)}
          aria-label="Add an email account"
        >
          <Plus aria-hidden className="size-4" />
          Add account
        </Button>
      </header>

      {error ? (
        <p className="text-sm text-danger" role="alert">
          {error}
        </p>
      ) : null}

      {adding ? (
        <AddAccountForm
          onCancel={() => setAdding(false)}
          onAdded={async () => {
            setAdding(false);
            await refresh();
          }}
        />
      ) : null}

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading accounts…</p>
      ) : accounts.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No accounts yet. Add one above.
        </p>
      ) : (
        <ul className="space-y-3">
          {accounts.map((account) => (
            <li key={account.accountId}>
              <AccountCard account={account} onChanged={() => void refresh()} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function AddAccountForm({
  onCancel,
  onAdded,
}: {
  onCancel: () => void;
  onAdded: () => Promise<void> | void;
}) {
  const [provider, setProvider] = useState<EmailProvider>("gmail");
  const [label, setLabel] = useState("");
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async () => {
    if (!label.trim() || !address.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await addAccount(provider, label.trim(), address.trim());
      await onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="email-add-provider">Provider</Label>
          <select
            id="email-add-provider"
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            value={provider}
            onChange={(e) => setProvider(e.target.value as EmailProvider)}
          >
            <option value="gmail">Gmail</option>
            <option value="microsoft">Microsoft Graph</option>
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="email-add-label">Label</Label>
          <Input
            id="email-add-label"
            placeholder="Personal"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </div>
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="email-add-address">Email address</Label>
          <Input
            id="email-add-address"
            type="email"
            placeholder="me@example.com"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            autoComplete="off"
          />
        </div>
      </div>
      {error ? (
        <p className="text-sm text-danger" role="alert">
          {error}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button
          type="button"
          onClick={() => void onSubmit()}
          disabled={busy || !label.trim() || !address.trim()}
        >
          Add account
        </Button>
      </div>
    </div>
  );
}

function AccountCard({
  account,
  onChanged,
}: {
  account: EmailAccount;
  onChanged: () => Promise<void> | void;
}) {
  const [status, setStatus] = useState<EmailCredentialsStatus>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");

  const refreshStatus = useCallback(async () => {
    try {
      const next = await credentialsStatus(account.accountId);
      setStatus(next);
    } catch {
      // surfaced via per-action errors
    }
  }, [account.accountId]);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  const onSave = async () => {
    setBusy(true);
    setError(null);
    try {
      await saveCredentials(account.accountId, {
        refreshToken: refresh.trim() || undefined,
        clientId: clientId.trim() || undefined,
        clientSecret: clientSecret.trim() || undefined,
      });
      setRefresh("");
      setClientId("");
      setClientSecret("");
      await refreshStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onRemove = async () => {
    setBusy(true);
    setError(null);
    try {
      await removeAccount(account.accountId);
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
        <div className="space-y-0.5">
          <h4 className="flex items-center gap-2 text-sm font-medium">
            <Mail aria-hidden className="size-4 text-muted-foreground" />
            {account.label}
            <span className="rounded bg-secondary px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-secondary-foreground">
              {account.provider === "gmail" ? "Gmail" : "Microsoft"}
            </span>
          </h4>
          <p className="text-xs text-muted-foreground">{account.address}</p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void onRemove()}
          disabled={busy}
        >
          <Trash2 aria-hidden className="size-4" />
          Remove
        </Button>
      </header>
      <CredentialFields
        status={status}
        refresh={refresh}
        clientId={clientId}
        clientSecret={clientSecret}
        provider={account.provider}
        onRefreshChange={setRefresh}
        onClientIdChange={setClientId}
        onClientSecretChange={setClientSecret}
      />
      {error ? (
        <p className="text-sm text-danger" role="alert">
          {error}
        </p>
      ) : null}
      <div className="flex justify-end">
        <Button
          type="button"
          onClick={() => void onSave()}
          disabled={busy || (!refresh.trim() && !clientId.trim() && !clientSecret.trim())}
        >
          Save credentials
        </Button>
      </div>
    </article>
  );
}

function CredentialFields({
  status,
  refresh,
  clientId,
  clientSecret,
  provider,
  onRefreshChange,
  onClientIdChange,
  onClientSecretChange,
}: {
  status: EmailCredentialsStatus;
  refresh: string;
  clientId: string;
  clientSecret: string;
  provider: EmailProvider;
  onRefreshChange: (value: string) => void;
  onClientIdChange: (value: string) => void;
  onClientSecretChange: (value: string) => void;
}) {
  return (
    <div className="space-y-3">
      <CredentialField
        id="email-refresh"
        label="Refresh token"
        configured={status.refreshTokenConfigured}
        value={refresh}
        onChange={onRefreshChange}
        description={
          provider === "gmail"
            ? "Long-lived Google OAuth refresh token. Mint via your OAuth client."
            : "Microsoft Entra refresh token with Mail.ReadWrite + Mail.Send + offline_access scopes."
        }
      />
      <CredentialField
        id="email-client-id"
        label="OAuth client ID"
        configured={status.clientIdConfigured}
        value={clientId}
        onChange={onClientIdChange}
        description={
          provider === "gmail"
            ? "Your Google Cloud OAuth client ID."
            : "Your Microsoft Entra app (client) ID."
        }
      />
      <CredentialField
        id="email-client-secret"
        label={
          provider === "microsoft"
            ? "OAuth client secret (optional for desktop apps)"
            : "OAuth client secret"
        }
        configured={status.clientSecretConfigured}
        value={clientSecret}
        onChange={onClientSecretChange}
        description={
          provider === "microsoft"
            ? "Leave empty if your Microsoft Entra app is registered as a public client."
            : "Paired client secret for the OAuth client above."
        }
      />
    </div>
  );
}

function CredentialField({
  id,
  label,
  description,
  configured,
  value,
  onChange,
}: {
  id: string;
  label: string;
  description: string;
  configured: boolean;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} className="flex items-center gap-1.5">
        {label}
        {configured ? <Check aria-hidden className="size-3.5 text-success" /> : null}
      </Label>
      <p className="text-xs text-muted-foreground">{description}</p>
      <Input
        id={id}
        type="password"
        placeholder={configured ? "(saved — paste a new value to replace)" : "Paste value"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
        autoComplete="off"
      />
    </div>
  );
}
