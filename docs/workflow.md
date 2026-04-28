# Build workflow — session-by-session

Quick reference for building Thalyn one phase at a time. The full
plan is in ``; this doc is the operating manual.

---

## How a phase session starts

Open a fresh harness session at the repo root and paste this prompt
(replace `vN.NN` with the phase you're starting):

> Read `.claude/CLAUDE.md`, then `` §1–§9 (the
> preamble), then the entry for **Phase vN.NN**. Then load the
> "Read first" pointers from that phase entry.
>
> Begin Phase vN.NN. Use `/commit` for every commit, `/adr <slug>`
> for any new ADR, follow the §8 stuck protocol if you can't make
> progress, and watch CI after every push (the snippet in
> `.claude/CLAUDE.md` is authoritative).
>
> Before you start: read `PROGRESS.md` for any notes from the prior
> session. When you stop (phase done, blocked, or stepping away),
> update `PROGRESS.md` so the next session can pick up cold.

That prompt is the whole opener. Don't preload more — the §6
cross-session-continuity discipline is "everything load-bearing
lives in git history, ADRs, and the plan; the conversation is the
least durable thing."

---

## How a phase session ends

A phase ends when:

1. Every exit criterion in the phase's §10–§26 entry holds, verified
   against the phase's "How to verify" recipe.
2. The pre-commit gate sequence passes on the final commit
   (`/commit` enforces).
3. `gh run watch` reports CI green on the pushed sha.
4. The phase is tagged: `git tag vN.NN && git push --tags`.
5. `PROGRESS.md` is updated to mark the phase complete and note the
   next phase.

A session ends mid-phase when:

1. You've hit a clean stopping point (a commit landed, gates passed,
   CI green) — even if more work remains in the phase.
2. `PROGRESS.md` records the current state (what's done, what's
   next, anything to remember).
3. Conversation is closed. Don't try to keep context across breaks
   — design as if the next session starts cold.

If the harness gets stuck (3 retries on the same problem, no
progress for 30 minutes, gates failing in ways it can't diagnose):
write `STUCK.md` per `` §8 and pause for human
review. Never `--no-verify`, never lower thresholds.

---

## Slash commands

All defined under `.claude/commands/`. Each runs the right gate
sequence; never invoke `git commit` directly.

| Command | When to use |
|---|---|
| `/commit` | Every commit. Runs lint + types + tests + leakage scan + sanity smoke + ADR/doc check. Only commits if every gate passes. |
| `/adr <slug>` | When the harness chooses between meaningful alternatives, replaces a load-bearing library, changes a public interface, or revises a §10 posture from `01-requirements.md`. Scaffolds `docs/adr/NNNN-<slug>.md` from MADR. |
| `/architecture-review` | Triggered automatically at the end of every third phase (see `` §7). Re-searches state of the field, compares to active ADRs, files updates. |
| `/dependency-review` | Quarterly. Independent of phase cadence. Dep upgrades, deprecation calendar, CVE scan, model deprecations. |
| `/spike <slug>` | Time-boxed investigation on a named risk. Produces `docs/spikes/YYYY-MM-DD-<slug>.md`. The outcome supersedes or refines an ADR. |

---

## Where state lives

Per `` §6 — the only durable state across sessions:

- **Git history** — every change ever shipped. Commit messages
  capture *why*. The narrative memory of the project.
- **`.claude/CLAUDE.md`** — always-loaded project conventions. Lean
  on purpose; don't bloat.
- **`docs/adr/`** — every load-bearing decision, immutable, MADR.
- **`docs/architecture-reviews/`** — per-cycle re-evaluation
  summaries.
- **`docs/spikes/`** — risk investigations and outcomes.
- **``** — the phase catalogue and rules.
- **`docs/going-public-checklist.md`** — deferred public-release
  hardening.
- **`PROGRESS.md`** — your local notebook (gitignored). See below.

State that *does not* survive: conversation history, in-context
notes, "I just figured out X" insights. If something matters across
sessions, it lives in one of the rows above.

---

## `PROGRESS.md`

A personal notebook for "where did I leave off?" It is gitignored —
your local working file, not project state.

What to put in it:

- Current phase + status (in-progress / blocked / complete).
- What landed in the last session (or a sha if you'd rather link).
- What the next session should pick up.
- Any rough edges you noticed but didn't act on (file as issues
  later).

What *not* to put in it:

- Architecture decisions (those go in ADRs).
- Anything another contributor would need to know (git history is
  authoritative).
- The plan itself (`` is authoritative).

Treat it like the sticky note on a real engineer's monitor: useful
for the human, ignored by everything else.

---

## Watching CI after a push

Local Mac gates are not equivalent to Linux CI; Storybook a11y in
particular catches WCAG violations that local runs miss. After every
`git push origin main`:

```bash
SHA=$(git rev-parse --short=8 HEAD)
until ID=$(gh run list -R barlowmen/thalyn --limit 5 \
            --json databaseId,headSha \
            --jq ".[] | select(.headSha | startswith(\"$SHA\")) | .databaseId") \
      && [ -n "$ID" ]; do sleep 2; done
gh run watch "$ID" -R barlowmen/thalyn --exit-status
gh run view  "$ID" -R barlowmen/thalyn --json conclusion --jq '.conclusion'
```

The trailing `gh run view` is the actual gate — `gh run watch` can
exit 0 even on a failed run. Confirm `success`. On red, surface the
failure with `gh run view <id> --log-failed` and push a fix as a
follow-up commit before moving on.

The phase isn't complete until CI is green.
