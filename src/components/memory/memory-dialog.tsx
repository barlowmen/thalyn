import { useCallback, useEffect, useState } from "react";

import { Pencil, Trash2 } from "lucide-react";

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
  addMemory,
  deleteMemory,
  listMemory,
  type MemoryEntry,
  type MemoryKind,
  type MemoryScope,
  updateMemory,
} from "@/lib/memory";

const SCOPES: MemoryScope[] = ["personal", "project", "episodic", "agent"];
const KINDS: MemoryKind[] = ["fact", "preference", "reference", "feedback"];

type ScopeFilter = MemoryScope | "all";

const SCOPE_FILTERS: ScopeFilter[] = ["all", ...SCOPES];

export function MemoryDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [filter, setFilter] = useState<ScopeFilter>("all");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listMemory(
        filter === "all" ? undefined : { scopes: [filter] },
      );
      setEntries(result.entries);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    if (!open) return;
    void refresh();
  }, [open, refresh]);

  const handleDelete = useCallback(
    async (memoryId: string) => {
      await deleteMemory(memoryId).catch(() => undefined);
      await refresh();
    },
    [refresh],
  );

  const handleSaveEdit = useCallback(
    async (memoryId: string, body: string) => {
      await updateMemory({ memoryId, body }).catch(() => undefined);
      setEditingId(null);
      await refresh();
    },
    [refresh],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[640px]">
        <header className="space-y-1">
          <DialogTitle>Memory</DialogTitle>
          <DialogDescription>
            Persistent context Thalyn and you share. Edits and
            deletions take effect on the next turn.
          </DialogDescription>
        </header>

        <div className="mt-4 max-h-[60vh] space-y-6 overflow-y-auto pr-1">
          <MemoryForm onCreated={refresh} />

          <section>
            <div className="mb-2 flex items-center justify-between gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Entries
              </h3>
              <div
                role="radiogroup"
                aria-label="Filter memory by scope"
                className="flex flex-wrap gap-1"
              >
                {SCOPE_FILTERS.map((value) => {
                  const active = value === filter;
                  return (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      onClick={() => setFilter(value)}
                      className={`rounded-md px-2 py-0.5 text-[11px] uppercase tracking-wider transition-colors ${
                        active
                          ? "bg-primary text-primary-foreground"
                          : "border border-border text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {value}
                    </button>
                  );
                })}
              </div>
            </div>
            {loading && (
              <p className="text-xs text-muted-foreground">Loading…</p>
            )}
            {error && <p className="text-xs text-danger">{error}</p>}
            {!loading && entries.length === 0 && (
              <p className="text-xs text-muted-foreground">
                None yet — add one above, or let Thalyn remember
                something for you.
              </p>
            )}
            <ul className="space-y-2">
              {entries.map((entry) => (
                <li
                  key={entry.memoryId}
                  className="rounded-md border border-border bg-bg px-3 py-2"
                >
                  {editingId === entry.memoryId ? (
                    <EditRow
                      entry={entry}
                      onSave={(body) => handleSaveEdit(entry.memoryId, body)}
                      onCancel={() => setEditingId(null)}
                    />
                  ) : (
                    <ReadRow
                      entry={entry}
                      onEdit={() => setEditingId(entry.memoryId)}
                      onDelete={() => {
                        void handleDelete(entry.memoryId);
                      }}
                    />
                  )}
                </li>
              ))}
            </ul>
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function MemoryForm({ onCreated }: { onCreated: () => void | Promise<void> }) {
  const [body, setBody] = useState("");
  const [scope, setScope] = useState<MemoryScope>("personal");
  const [kind, setKind] = useState<MemoryKind>("preference");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!body.trim()) {
      setError("Body is required.");
      return;
    }
    setSubmitting(true);
    try {
      await addMemory({
        body: body.trim(),
        scope,
        kind,
        author: "user",
      });
      setBody("");
      await onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Add a memory
      </h3>
      <div className="space-y-1">
        <Label htmlFor="memory-body">Body</Label>
        <Input
          id="memory-body"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="The user prefers tabs over spaces."
          disabled={submitting}
        />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <ChipPicker
          label="Scope"
          choices={SCOPES}
          value={scope}
          onChange={setScope}
          disabled={submitting}
        />
        <ChipPicker
          label="Kind"
          choices={KINDS}
          value={kind}
          onChange={setKind}
          disabled={submitting}
        />
      </div>
      {error && <p className="text-xs text-danger">{error}</p>}
      <Button type="submit" size="sm" disabled={submitting}>
        {submitting ? "Saving…" : "Add memory"}
      </Button>
    </form>
  );
}

function ChipPicker<T extends string>({
  label,
  choices,
  value,
  onChange,
  disabled,
}: {
  label: string;
  choices: readonly T[];
  value: T;
  onChange: (value: T) => void;
  disabled?: boolean;
}) {
  return (
    <div>
      <p className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <div className="flex flex-wrap gap-1">
        {choices.map((choice) => {
          const active = choice === value;
          return (
            <button
              key={choice}
              type="button"
              onClick={() => onChange(choice)}
              disabled={disabled}
              className={`rounded-md px-2 py-0.5 text-[11px] uppercase tracking-wider transition-colors ${
                active
                  ? "bg-primary text-primary-foreground"
                  : "border border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              {choice}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ReadRow({
  entry,
  onEdit,
  onDelete,
}: {
  entry: MemoryEntry;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="flex-1 overflow-hidden">
        <p className="text-sm">{entry.body}</p>
        <p className="text-[11px] text-muted-foreground">
          {entry.scope} · {entry.kind} · by {entry.author} ·{" "}
          {new Date(entry.createdAtMs).toLocaleString()}
        </p>
      </div>
      <Button
        size="sm"
        variant="ghost"
        aria-label="Edit memory"
        onClick={onEdit}
      >
        <Pencil className="h-3.5 w-3.5" aria-hidden />
      </Button>
      <Button
        size="sm"
        variant="ghost"
        aria-label="Delete memory"
        onClick={onDelete}
      >
        <Trash2 className="h-3.5 w-3.5" aria-hidden />
      </Button>
    </div>
  );
}

function EditRow({
  entry,
  onSave,
  onCancel,
}: {
  entry: MemoryEntry;
  onSave: (body: string) => void | Promise<void>;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(entry.body);
  return (
    <div className="space-y-2">
      <Input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        autoFocus
      />
      <div className="flex items-center justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          size="sm"
          variant="default"
          onClick={() => {
            void onSave(draft.trim());
          }}
          disabled={!draft.trim() || draft.trim() === entry.body}
        >
          Save
        </Button>
      </div>
    </div>
  );
}
