import { Inbox, Mail, RefreshCw, Send, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  type EmailAccount,
  type EmailDraft,
  type EmailMessage,
  approveDraft,
  createDraft,
  discardDraft,
  getMessage,
  listAccounts,
  listMessages,
  sendDraft,
} from "@/lib/email";
import { cn } from "@/lib/utils";

type SurfaceState = {
  accounts: EmailAccount[];
  selectedAccountId: string | null;
  loadingAccounts: boolean;
  error: string | null;
};

/**
 * Inbox + thread + compose. Lists Gmail and Microsoft accounts the
 * user has wired up; switching accounts repaints the inbox. Send is
 * gated by a confirm modal that maps to the brain's approval RPC,
 * so an autonomous draft can never reach the wire without a user
 * click on this surface.
 */
export function EmailSurface() {
  const [state, setState] = useState<SurfaceState>({
    accounts: [],
    selectedAccountId: null,
    loadingAccounts: true,
    error: null,
  });

  const refreshAccounts = useCallback(async () => {
    try {
      const { accounts } = await listAccounts();
      setState((prev) => ({
        accounts,
        selectedAccountId: prev.selectedAccountId
          ? accounts.find((a) => a.accountId === prev.selectedAccountId)?.accountId ??
            (accounts[0]?.accountId ?? null)
          : accounts[0]?.accountId ?? null,
        loadingAccounts: false,
        error: null,
      }));
    } catch (err) {
      setState({
        accounts: [],
        selectedAccountId: null,
        loadingAccounts: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }, []);

  useEffect(() => {
    void refreshAccounts();
  }, [refreshAccounts]);

  const selectedAccount = state.accounts.find(
    (a) => a.accountId === state.selectedAccountId,
  );

  if (state.loadingAccounts) {
    return <SurfaceFrame heading="Email">Loading accounts…</SurfaceFrame>;
  }

  if (state.error) {
    return (
      <SurfaceFrame heading="Email">
        <p className="text-sm text-destructive" role="alert">
          {state.error}
        </p>
      </SurfaceFrame>
    );
  }

  if (state.accounts.length === 0) {
    return (
      <SurfaceFrame heading="Email">
        <EmptyState />
      </SurfaceFrame>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-border bg-surface px-4 py-2">
        <div className="flex items-center gap-2">
          <Inbox aria-hidden className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">Email</h2>
        </div>
        <select
          className="rounded-md border border-border bg-background px-2 py-1 text-sm"
          value={state.selectedAccountId ?? ""}
          onChange={(e) =>
            setState((prev) => ({ ...prev, selectedAccountId: e.target.value }))
          }
          aria-label="Active account"
        >
          {state.accounts.map((account) => (
            <option key={account.accountId} value={account.accountId}>
              {account.label} ({account.address})
            </option>
          ))}
        </select>
      </header>
      {selectedAccount ? <AccountInbox account={selectedAccount} /> : null}
    </div>
  );
}

function SurfaceFrame({
  heading,
  children,
}: {
  heading: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center gap-2 border-b border-border bg-surface px-4 py-2">
        <Inbox aria-hidden className="size-4 text-muted-foreground" />
        <h2 className="text-sm font-medium">{heading}</h2>
      </header>
      <div className="flex flex-1 items-start justify-center p-6">{children}</div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="max-w-md space-y-3 text-center">
      <Mail aria-hidden className="mx-auto size-10 text-muted-foreground" />
      <h3 className="text-base font-medium">No email accounts</h3>
      <p className="text-sm text-muted-foreground">
        Add a Gmail or Microsoft account from Settings → Email accounts. Refresh
        tokens stay in your OS keychain; nothing leaves your machine until the
        first inbox fetch.
      </p>
    </div>
  );
}

function AccountInbox({ account }: { account: EmailAccount }) {
  const [messages, setMessages] = useState<EmailMessage[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await listMessages(account.accountId, { maxResults: 25 });
      setMessages(result.messages);
      if (!selectedId && result.messages.length > 0) {
        setSelectedId(result.messages[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [account.accountId, selectedId]);

  useEffect(() => {
    setMessages([]);
    setSelectedId(null);
    void refresh();
    // refresh changes when account changes; intentional dependency on
    // account.accountId is captured via refresh's deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account.accountId]);

  const selectedMessage = useMemo(
    () => messages.find((m) => m.id === selectedId) ?? null,
    [messages, selectedId],
  );

  return (
    <div className="flex flex-1 min-h-0">
      <aside className="flex w-80 min-w-[280px] flex-col border-r border-border bg-surface">
        <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
          <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Inbox
          </span>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Refresh inbox"
              onClick={() => void refresh()}
              disabled={busy}
            >
              <RefreshCw aria-hidden className={cn("size-4", busy && "animate-spin")} />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Compose"
              onClick={() => setComposing(true)}
            >
              <Mail aria-hidden className="size-4" />
            </Button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto" role="listbox" aria-label="Messages">
          {error ? (
            <p className="p-3 text-xs text-destructive" role="alert">
              {error}
            </p>
          ) : null}
          {messages.length === 0 && !busy && !error ? (
            <p className="p-3 text-xs text-muted-foreground">No messages.</p>
          ) : null}
          {messages.map((message) => (
            <button
              key={message.id}
              type="button"
              role="option"
              aria-selected={selectedId === message.id}
              onClick={() => setSelectedId(message.id)}
              className={cn(
                "block w-full border-b border-border px-3 py-2 text-left text-sm hover:bg-card",
                selectedId === message.id && "bg-card",
              )}
            >
              <div className="truncate font-medium">{message.from || "(no sender)"}</div>
              <div className="truncate text-xs text-muted-foreground">
                {message.subject || "(no subject)"}
              </div>
              <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                {message.snippet}
              </div>
            </button>
          ))}
        </div>
      </aside>
      <div className="flex flex-1 min-h-0 flex-col">
        {composing ? (
          <ComposePane
            account={account}
            onClose={() => setComposing(false)}
            onSent={async () => {
              setComposing(false);
              await refresh();
            }}
            inReplyToMessage={null}
          />
        ) : selectedMessage ? (
          <MessageView account={account} message={selectedMessage} />
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Select a message to read it.
          </div>
        )}
      </div>
    </div>
  );
}

function MessageView({
  account,
  message,
}: {
  account: EmailAccount;
  message: EmailMessage;
}) {
  const [body, setBody] = useState<string | null>(message.body);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (message.body !== null) {
      setBody(message.body);
      return;
    }
    setBusy(true);
    setError(null);
    getMessage(account.accountId, message.id)
      .then((full) => setBody(full.body ?? ""))
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setBusy(false));
  }, [account.accountId, message.id, message.body]);

  return (
    <article className="flex h-full flex-col">
      <header className="space-y-1 border-b border-border bg-surface px-4 py-3">
        <h3 className="text-base font-medium">{message.subject || "(no subject)"}</h3>
        <p className="text-xs text-muted-foreground">
          From {message.from} · {message.date}
        </p>
      </header>
      <div className="flex-1 overflow-y-auto whitespace-pre-wrap p-4 text-sm">
        {error ? (
          <p className="text-destructive" role="alert">
            {error}
          </p>
        ) : busy ? (
          "Loading message…"
        ) : (
          body ?? "(empty body)"
        )}
      </div>
    </article>
  );
}

function ComposePane({
  account,
  onClose,
  onSent,
  inReplyToMessage,
}: {
  account: EmailAccount;
  onClose: () => void;
  onSent: () => Promise<void> | void;
  inReplyToMessage: EmailMessage | null;
}) {
  const [to, setTo] = useState(inReplyToMessage?.from ?? "");
  const [subject, setSubject] = useState(
    inReplyToMessage ? `Re: ${inReplyToMessage.subject}` : "",
  );
  const [body, setBody] = useState("");
  const [draft, setDraft] = useState<EmailDraft | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onPrepare = async () => {
    setBusy(true);
    setError(null);
    try {
      const newDraft = await createDraft(account.accountId, {
        to: to.split(",").map((s) => s.trim()).filter(Boolean),
        subject,
        body,
        inReplyTo: inReplyToMessage?.id ?? undefined,
      });
      setDraft(newDraft);
      setConfirming(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onConfirmSend = async () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      await approveDraft(draft.draftId);
      await sendDraft(draft.draftId);
      await onSent();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      // Discard so the user re-prepares on the next attempt.
      try {
        await discardDraft(draft.draftId);
      } catch {
        // Fine — best-effort cleanup.
      }
      setDraft(null);
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  };

  const onCancelConfirm = async () => {
    if (draft) {
      try {
        await discardDraft(draft.draftId);
      } catch {
        // Best-effort cleanup.
      }
    }
    setDraft(null);
    setConfirming(false);
  };

  return (
    <section className="flex h-full flex-col">
      <header className="flex items-center justify-between gap-2 border-b border-border bg-surface px-4 py-2">
        <h3 className="text-sm font-medium">New message</h3>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Close compose"
          onClick={onClose}
        >
          <X aria-hidden className="size-4" />
        </Button>
      </header>
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        <div className="space-y-1">
          <Label htmlFor="email-compose-to">To</Label>
          <Input
            id="email-compose-to"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder="alice@example.com, bob@example.com"
            spellCheck={false}
            autoComplete="off"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="email-compose-subject">Subject</Label>
          <Input
            id="email-compose-subject"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="email-compose-body">Body</Label>
          <textarea
            id="email-compose-body"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={12}
            className="w-full resize-y rounded-md border border-border bg-background p-2 text-sm font-mono"
          />
        </div>
        {error ? (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}
      </div>
      <footer className="flex items-center justify-end gap-2 border-t border-border bg-surface px-4 py-2">
        <Button type="button" variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button
          type="button"
          onClick={() => void onPrepare()}
          disabled={!to.trim() || busy}
        >
          <Send aria-hidden className="size-4" />
          Prepare to send
        </Button>
      </footer>
      {confirming && draft ? (
        <ConfirmSendModal
          draft={draft}
          busy={busy}
          onCancel={() => void onCancelConfirm()}
          onConfirm={() => void onConfirmSend()}
        />
      ) : null}
    </section>
  );
}

function ConfirmSendModal({
  draft,
  busy,
  onCancel,
  onConfirm,
}: {
  draft: EmailDraft;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/70"
      role="dialog"
      aria-modal="true"
      aria-labelledby="email-confirm-title"
    >
      <div className="w-full max-w-md space-y-4 rounded-lg border border-border bg-card p-6 shadow-lg">
        <div className="space-y-1">
          <h2 id="email-confirm-title" className="text-base font-medium">
            Send this message?
          </h2>
          <p className="text-sm text-muted-foreground">
            Sending requires explicit approval. The brain refuses every
            send that hasn't gone through this confirm.
          </p>
        </div>
        <dl className="space-y-1 text-sm">
          <div className="flex gap-2">
            <dt className="w-16 text-muted-foreground">To</dt>
            <dd className="flex-1">{draft.to.join(", ")}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-16 text-muted-foreground">Subject</dt>
            <dd className="flex-1">{draft.subject || "(no subject)"}</dd>
          </div>
        </dl>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button type="button" onClick={onConfirm} disabled={busy}>
            {busy ? "Sending…" : "Send"}
          </Button>
        </div>
      </div>
    </div>
  );
}
