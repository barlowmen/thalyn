import { useCallback, useEffect, useState } from "react";

import { Trash2 } from "lucide-react";

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
  type Schedule,
  createSchedule,
  deleteSchedule,
  listSchedules,
} from "@/lib/schedules";

type ComposeMode = "natural" | "cron";

export function SchedulesDialog({
  open,
  onOpenChange,
  defaultProviderId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  defaultProviderId: string;
}) {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listSchedules();
      setSchedules(result.schedules);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    void refresh();
  }, [open, refresh]);

  const handleDelete = useCallback(
    async (scheduleId: string) => {
      await deleteSchedule(scheduleId).catch(() => undefined);
      await refresh();
    },
    [refresh],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[640px]">
        <header className="space-y-1">
          <DialogTitle>Schedules</DialogTitle>
          <DialogDescription>
            Run an agent on a recurring cron expression. While Thalyn
            is open, schedules fire on time.
          </DialogDescription>
        </header>

        <div className="mt-4 max-h-[60vh] space-y-6 overflow-y-auto pr-1">
          <ScheduleForm
            defaultProviderId={defaultProviderId}
            onCreated={() => {
              void refresh();
            }}
          />

          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Active schedules
            </h3>
            {loading && (
              <p className="text-xs text-muted-foreground">Loading…</p>
            )}
            {error && <p className="text-xs text-danger">{error}</p>}
            {!loading && schedules.length === 0 && (
              <p className="text-xs text-muted-foreground">
                None yet — create one above.
              </p>
            )}
            <ul className="space-y-2">
              {schedules.map((schedule) => (
                <li
                  key={schedule.scheduleId}
                  className="flex items-start gap-3 rounded-md border border-border bg-bg px-3 py-2"
                >
                  <div className="flex-1 overflow-hidden">
                    <p className="truncate text-sm font-medium">
                      {schedule.title}
                    </p>
                    <p className="font-mono text-[11px] text-muted-foreground">
                      {schedule.cron}
                    </p>
                    {schedule.nlInput && (
                      <p className="text-[11px] text-muted-foreground">
                        {schedule.nlInput}
                      </p>
                    )}
                    {schedule.nextRunAtMs !== null && (
                      <p className="text-[11px] text-muted-foreground">
                        next: {new Date(schedule.nextRunAtMs).toLocaleString()}
                      </p>
                    )}
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Delete schedule ${schedule.title}`}
                    onClick={() => {
                      void handleDelete(schedule.scheduleId);
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ScheduleForm({
  defaultProviderId,
  onCreated,
}: {
  defaultProviderId: string;
  onCreated: () => void;
}) {
  const [mode, setMode] = useState<ComposeMode>("natural");
  const [title, setTitle] = useState("");
  const [nlInput, setNlInput] = useState("");
  const [cron, setCron] = useState("");
  const [prompt, setPrompt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setTitle("");
    setNlInput("");
    setCron("");
    setPrompt("");
    setError(null);
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!title.trim() || !prompt.trim()) {
      setError("Title and prompt are required.");
      return;
    }
    setSubmitting(true);
    try {
      await createSchedule({
        title: title.trim(),
        nlInput: mode === "natural" ? nlInput.trim() : undefined,
        cron: mode === "cron" ? cron.trim() : undefined,
        runTemplate: {
          prompt: prompt.trim(),
          providerId: defaultProviderId,
        },
      });
      reset();
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        New schedule
      </h3>

      <div className="space-y-1">
        <Label htmlFor="schedule-title">Title</Label>
        <Input
          id="schedule-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Daily summary"
          disabled={submitting}
        />
      </div>

      <div className="flex items-center gap-2 text-xs">
        <ModeToggle mode={mode} onChange={setMode} disabled={submitting} />
      </div>

      {mode === "natural" ? (
        <div className="space-y-1">
          <Label htmlFor="schedule-nl">When (natural language)</Label>
          <Input
            id="schedule-nl"
            value={nlInput}
            onChange={(e) => setNlInput(e.target.value)}
            placeholder="every weekday at 6 a.m."
            disabled={submitting}
          />
        </div>
      ) : (
        <div className="space-y-1">
          <Label htmlFor="schedule-cron">Cron expression</Label>
          <Input
            id="schedule-cron"
            value={cron}
            onChange={(e) => setCron(e.target.value)}
            placeholder="0 6 * * 1-5"
            className="font-mono"
            disabled={submitting}
          />
        </div>
      )}

      <div className="space-y-1">
        <Label htmlFor="schedule-prompt">Prompt</Label>
        <Input
          id="schedule-prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Summarize today's PRs"
          disabled={submitting}
        />
      </div>

      {error && <p className="text-xs text-danger">{error}</p>}

      <Button type="submit" disabled={submitting}>
        {submitting ? "Creating…" : "Create schedule"}
      </Button>
    </form>
  );
}

function ModeToggle({
  mode,
  onChange,
  disabled,
}: {
  mode: ComposeMode;
  onChange: (mode: ComposeMode) => void;
  disabled?: boolean;
}) {
  return (
    <div className="inline-flex items-center gap-1 rounded-md border border-border bg-bg p-0.5">
      <button
        type="button"
        onClick={() => onChange("natural")}
        disabled={disabled}
        className={`rounded px-2 py-0.5 text-[11px] uppercase tracking-wider transition-colors ${
          mode === "natural"
            ? "bg-primary text-primary-foreground"
            : "text-muted-foreground hover:text-foreground"
        }`}
      >
        Natural
      </button>
      <button
        type="button"
        onClick={() => onChange("cron")}
        disabled={disabled}
        className={`rounded px-2 py-0.5 text-[11px] uppercase tracking-wider transition-colors ${
          mode === "cron"
            ? "bg-primary text-primary-foreground"
            : "text-muted-foreground hover:text-foreground"
        }`}
      >
        Cron
      </button>
    </div>
  );
}
