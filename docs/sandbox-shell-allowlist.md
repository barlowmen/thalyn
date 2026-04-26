# Sub-agent shell allowlist

Sub-agents reach the host shell only through the `restricted_shell`
tool. Two layers gate every call:

1. A **binary allowlist** the first token of the parsed command must
   match (basename comparison so `/usr/bin/git` and `git` both pass).
2. A **catastrophic-shape blocklist** the raw command string is
   regex-checked against, even when the binary itself is allowed.

The canonical lists live in
[`brain/thalyn_brain/sandbox/shell_allowlist.py`](../brain/thalyn_brain/sandbox/shell_allowlist.py)
as `DEFAULT_ALLOWLIST` and `DEFAULT_BLOCKLIST_PATTERNS`. This
document mirrors them for human reference; the Python file is the
single source of truth.

## Binary allowlist

Grouped by what the binary does. Anything not on this list is
rejected regardless of arguments.

### File reading and listing

`cat`, `head`, `tail`, `ls`, `find`, `tree`, `stat`, `file`, `wc`,
`du`, `df`

### Text processing (read-only by default)

`grep`, `rg`, `egrep`, `fgrep`, `awk`, `sed`, `sort`, `uniq`, `cut`,
`tr`, `diff`

### Path / process inspection

`pwd`, `basename`, `dirname`, `which`, `type`, `env`, `id`,
`whoami`, `ps`, `uname`, `hostname`

### Output

`echo`, `printf`, `true`, `false`

### Version control

`git` — write subcommands are bounded by the worktree confinement
(detached branch under `<repo>/.thalyn-worktrees/<run_id>`) and the
`--network=none` default that prevents `git push` from reaching a
remote.

### Language runtimes

`python`, `python3`, `node`, `deno`

### Build tools

`make`, `cargo`, `npm`, `pnpm`, `uv`, `pip`

## Why these are *not* on the list

- **Shell interpreters** (`sh`, `bash`, `zsh`, `fish`) — accepting
  `sh -c <anything>` would defeat the allowlist entirely. The tool
  intentionally refuses every shell-escape route.
- **Network clients** (`curl`, `wget`, `ssh`, `scp`, `ftp`) —
  network access flows through the egress allowlist when needed,
  not through ad-hoc invocations of these binaries inside the
  shell.
- **Privilege escalation** (`sudo`, `su`) — never appropriate inside
  a sub-agent; if a task needs root the user runs it themselves.
- **Process control** (`kill`, `killall`, `pkill`) — kill an agent
  through the runs-index UI, not from inside another agent.
- **Filesystem-destructive defaults** (`rm`, `mv`, `chmod`, `chown`,
  `dd`, `mkfs`) — git's worktree semantics give the user a safer
  way to discard sub-agent work; the catastrophic-shape blocklist
  catches these even if a future change accidentally allowed them.

## Catastrophic-shape blocklist

Patterns the raw command string is checked against. The regexes are
deliberately conservative — false positives are preferable to
silent acceptance. Adjust in
[`shell_allowlist.py`](../brain/thalyn_brain/sandbox/shell_allowlist.py)
if a real workflow trips one without cause.

| Pattern | What it catches |
|---|---|
| `\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*[fF]` | `rm -rf`, `rm -Rf`, `rm -fR`, etc. |
| `\brm\s+-[a-zA-Z]*[fF][a-zA-Z]*[rR]` | `rm -fr`, with `-f` before `-r` |
| `\bdd\s+.*\bof=/dev/` | `dd` writing to a device node |
| `:\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:` | classic fork bomb |
| `\bmkfs(\.[a-z0-9]+)?\b` | filesystem creation (`mkfs`, `mkfs.ext4`, …) |
| `\b(?:sudo\|su)\b` | privilege escalation |
| `\bchmod\s+(?:\d{3,4}\|[+-]?[ugo]?[+-=]?[rwxst]+)\s+/` | `chmod` on the filesystem root |
| `>\s*/dev/sd[a-z]` | redirect to a raw block device |

## Adding to the lists

The lists are data; loosening or tightening is a one-line edit to
the Python file. Pull requests should:

1. Update `DEFAULT_ALLOWLIST` or `DEFAULT_BLOCKLIST_PATTERNS` in
   `shell_allowlist.py`.
2. Add a row to this document.
3. Add a test in
   [`brain/tests/sandbox/test_shell_allowlist.py`](../brain/tests/sandbox/test_shell_allowlist.py)
   covering the new case.

The leakage-scan + lint + type-check + test gates pick up the rest.
