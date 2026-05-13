/**
 * Project switcher popover (F8.5 / F3.7).
 *
 * The top-bar's project pill opens this popover. It lists every
 * active project with a one-line lead-status hint, paused / archived
 * sections collapsed below, and a "+ New project" affordance that
 * inline-creates a fresh project. Picking a project flips the
 * foreground attention — the next turn the user types is biased
 * toward that project (the brain side honours ``projectId`` in
 * ``thread.send``).
 *
 * The popover state-machine mirrors the provider switcher in
 * ``top-bar.tsx``: open on click, dismiss on outside click /
 * Escape, listbox semantics for the active list.
 */

import { ChevronDown, Plus } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  emitActiveProjectChange,
  writeActiveProject,
} from "@/lib/active-project";
import { listLeads, type LeadAgent } from "@/lib/leads";
import {
  createProject,
  listProjects,
  type Project,
} from "@/lib/projects";
import { cn } from "@/lib/utils";

type Props = {
  /** The current foreground project id; rendered with the active dot. */
  activeProjectId: string;
  /** Optional preview override for tests / Storybook. When provided
   *  the popover renders the supplied projects + leads instead of
   *  hitting the Tauri bindings. */
  preview?: {
    projects: Project[];
    leads: LeadAgent[];
  };
};

type LoadedState =
  | { kind: "loading" }
  | { kind: "ready"; projects: Project[]; leads: LeadAgent[] }
  | { kind: "error"; message: string };

export function ProjectSwitcher({ activeProjectId, preview }: Props) {
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [state, setState] = useState<LoadedState>(() =>
    preview
      ? { kind: "ready", projects: preview.projects, leads: preview.leads }
      : { kind: "loading" },
  );
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const inputId = useId();

  const refresh = useRefresh(setState, preview);

  useEffect(() => {
    if (!open || preview) return;
    refresh();
  }, [open, preview, refresh]);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target;
      if (
        target instanceof Node &&
        (popoverRef.current?.contains(target) ||
          triggerRef.current?.contains(target))
      ) {
        return;
      }
      setOpen(false);
      setCreating(false);
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        setCreating(false);
      }
    };
    window.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("keydown", handleEscape);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  const projectName = useProjectLabel(state, activeProjectId);

  const handleSelect = (project: Project) => {
    setOpen(false);
    setCreating(false);
    if (project.projectId === activeProjectId) return;
    writeActiveProject(project.projectId);
    emitActiveProjectChange(project.projectId);
  };

  const handleCreate = async () => {
    const trimmed = draftName.trim();
    if (!trimmed) return;
    try {
      const result = await createProject({ name: trimmed });
      setDraftName("");
      setCreating(false);
      writeActiveProject(result.project.projectId);
      emitActiveProjectChange(result.project.projectId);
      setOpen(false);
    } catch (err) {
      setState({
        kind: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  return (
    <div className="relative flex items-center">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Project: ${projectName}. Open project switcher.`}
        className="group flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        <span className="truncate">{projectName}</span>
        <ChevronDown
          aria-hidden
          className="h-3 w-3 text-muted-foreground transition-transform group-aria-expanded:rotate-180"
        />
      </button>

      {open && (
        <div
          ref={popoverRef}
          aria-label="Project switcher"
          className="absolute left-1/2 top-full z-30 mt-2 w-[320px] -translate-x-1/2 rounded-md border border-border bg-popover p-1 shadow-lg"
        >
          <ProjectSection
            title="Active"
            projects={projectsByStatus(state, "active")}
            leadsByProject={leadsByProject(state)}
            activeProjectId={activeProjectId}
            onSelect={handleSelect}
          />
          <PausedSection
            projects={projectsByStatus(state, "paused")}
          />
          <div className="mt-1 border-t border-border pt-1">
            {creating ? (
              <div className="flex items-center gap-1 px-1 py-1">
                <input
                  id={inputId}
                  autoFocus
                  value={draftName}
                  onChange={(event) => setDraftName(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      void handleCreate();
                    }
                  }}
                  placeholder="New project name"
                  aria-label="New project name"
                  className="flex-1 rounded border border-border bg-background px-2 py-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => void handleCreate()}
                  aria-label="Create project"
                  className="h-7 px-2 text-xs"
                >
                  Add
                </Button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setCreating(true)}
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <Plus aria-hidden className="h-3 w-3" />
                <span>New project</span>
              </button>
            )}
          </div>
          {state.kind === "error" && (
            <p
              role="alert"
              className="mt-1 px-2 py-1 text-[11px] text-danger"
            >
              {state.message}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

type SectionProps = {
  title: string;
  projects: Project[];
  leadsByProject: Map<string, LeadAgent>;
  activeProjectId: string;
  onSelect: (project: Project) => void;
};

function ProjectSection({
  title,
  projects,
  leadsByProject,
  activeProjectId,
  onSelect,
}: SectionProps) {
  if (projects.length === 0) return null;
  return (
    <div className="mb-1">
      <p
        id="project-switcher-active-label"
        className="px-2 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground"
      >
        {title}
      </p>
      <ul role="listbox" aria-labelledby="project-switcher-active-label">
        {projects.map((project) => {
          const isActive = project.projectId === activeProjectId;
          const lead = leadsByProject.get(project.projectId);
          return (
            <li
              key={project.projectId}
              role="option"
              aria-selected={isActive}
              className="m-0 list-none"
            >
              <button
                type="button"
                onClick={() => onSelect(project)}
                className={cn(
                  "flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent",
                )}
              >
                <span className="flex flex-col overflow-hidden">
                  <span className="truncate font-medium">{project.name}</span>
                  <span className="truncate text-[11px] text-muted-foreground">
                    {leadStatusLine(lead)}
                  </span>
                </span>
                {isActive && (
                  <span aria-hidden className="text-success">
                    ●
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function PausedSection({ projects }: { projects: Project[] }) {
  if (projects.length === 0) return null;
  return (
    <div className="mb-1">
      <p className="px-2 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Paused
      </p>
      <ul>
        {projects.map((project) => (
          <li
            key={project.projectId}
            className="flex items-center justify-between px-2 py-1 text-sm text-muted-foreground"
          >
            <span className="truncate">{project.name}</span>
            <span className="text-[11px]">paused</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function leadStatusLine(lead: LeadAgent | undefined): string {
  if (!lead) return "no lead spawned";
  if (lead.status === "paused") return `${lead.displayName} · paused`;
  if (lead.status === "archived") return `${lead.displayName} · archived`;
  return lead.displayName;
}

function projectsByStatus(
  state: LoadedState,
  status: "active" | "paused" | "archived",
): Project[] {
  if (state.kind !== "ready") return [];
  return state.projects.filter((project) => project.status === status);
}

function leadsByProject(state: LoadedState): Map<string, LeadAgent> {
  const map = new Map<string, LeadAgent>();
  if (state.kind !== "ready") return map;
  for (const lead of state.leads) {
    if (lead.projectId && !map.has(lead.projectId)) {
      map.set(lead.projectId, lead);
    }
  }
  return map;
}

function useProjectLabel(state: LoadedState, activeProjectId: string): string {
  if (state.kind !== "ready") return "Thalyn";
  const found = state.projects.find((p) => p.projectId === activeProjectId);
  return found?.name ?? "Thalyn";
}

function useRefresh(
  setState: React.Dispatch<React.SetStateAction<LoadedState>>,
  preview: Props["preview"] | undefined,
): () => void {
  const ref = useRef<() => void>(() => undefined);
  useEffect(() => {
    let cancelled = false;
    ref.current = () => {
      if (preview) return;
      setState({ kind: "loading" });
      Promise.all([listProjects(), listLeads({ status: "active" })])
        .then(([projectsResult, leadsResult]) => {
          if (cancelled) return;
          setState({
            kind: "ready",
            projects: projectsResult.projects,
            leads: leadsResult.agents,
          });
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setState({
            kind: "error",
            message: err instanceof Error ? err.message : String(err),
          });
        });
    };
    return () => {
      cancelled = true;
    };
  }, [preview, setState]);
  return () => ref.current();
}
