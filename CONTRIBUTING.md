# Contributing to Thalyn

Thalyn is open source under MIT. The project is small and opinionated —
patches are welcome, and the conventions below exist so a stranger can
get a useful change landed without DM-ing anyone.

## Setting up

Install:

- Rust stable (`curl https://sh.rustup.rs | sh`).
- Node.js ≥ 22 with pnpm ≥ 10 (`corepack enable pnpm`).
- uv for the Python sidecar.
- Tauri's [platform prerequisites](https://v2.tauri.app/start/prerequisites/).
- **cmake** and **ninja** (for the bundled CEF build).

Then:

```sh
# Where cef-dll-sys caches the downloaded CEF SDK across builds.
# `~/.cache/thalyn-cef` is the conventional location; pick anywhere
# writable. First `cargo build` populates it (~130 MB compressed).
export CEF_PATH="$HOME/.cache/thalyn-cef"

pnpm install
( cd brain && uv sync )
pnpm tauri dev
```

If `pnpm tauri dev` brings up a window and `Ping brain` returns a pong,
you're set up.

### Brain sidecar packaging

`pnpm tauri dev` runs the brain via `uv run python -m thalyn_brain`
from the in-tree `brain/` directory — no packaging step. `pnpm tauri
build` runs an extra `beforeBundleCommand` step that PyInstallers
the brain into a one-folder bundle at
`<target>/brain-sidecar/thalyn-brain/` (per ADR-0018) and copies it
into `<App>.app/Contents/Resources/thalyn-brain/`. The bundle takes
~30s on a warm cache; set `THALYN_SKIP_BRAIN_BUNDLE=1` to reuse the
existing staged bundle if you're iterating on Rust-only changes.

PyInstaller installs from the brain's `bundle` dependency group
(`uv sync --group bundle --frozen`); contributors who only run the
dev path don't pay the install cost.

### CEF (bundled Chromium)

The bundled-Chromium engine (ADR-0019, ADR-0029) is on by default.
The first `cargo build` downloads the pinned CEF SDK to `$CEF_PATH`
and runs cmake/ninja against `libcef_dll_wrapper`; subsequent builds
hit the cache. The pinned version lives in
[`src-tauri/cef-version.txt`](src-tauri/cef-version.txt) and CI keeps a
matching cache keyed on the same file.

If you specifically need a CEF-free build (e.g., bisecting a non-CEF
regression), pass `--no-default-features` to cargo:

```sh
cargo check --manifest-path src-tauri/Cargo.toml --no-default-features
```

The renderer's browser drawer falls back to an "engine not available"
state in that build.

## Where things live

- `src/` — React renderer.
- `src-tauri/` — Rust core that owns the window, supervises sidecars,
  brokers IPC, and exposes commands to the renderer.
- `brain/` — Python sidecar, the agent reasoning layer.
- `docs/adr/` — Architecture Decision Records. Read these before
  proposing a structural change.
- `01-requirements.md`, `02-architecture.md`, `` —
  the canonical product spec, architecture, and build plan.
- `docs/going-public-checklist.md` — items that gate any public
  release.

## How to propose a change

1. **Read the relevant ADR.** If a load-bearing technology choice is
   involved (Tauri, the brain process model, the IPC protocol, the
   sandbox tier, the design system, observability), there is an ADR
   that defends the existing choice. Match it or supersede it with a
   new ADR. Don't quietly drift.
2. **Open an issue first** for non-trivial changes. A short
   description of the problem and the proposed approach saves time.
   Trivial fixes (typos, small cleanups, dependency bumps in the
   accepted range) can go straight to a pull request.
3. **Branch from `main`.** Push to your fork; open the PR against
   `main`.
4. **Write atomic commits.** One logical change per commit. The PR
   description should be readable in under 30 minutes — if it grows
   beyond that, split it.

## Commit hygiene

- **Conventional Commits.** `type(scope): subject`, imperative mood.
  Body explains *why*, not *what* — the diff is the *what*. Wrap at
  100. The `feat`, `fix`, `perf`, `refactor`, `docs`, `chore`, `test`,
  and `style` types are all in use; see existing history for the
  style.
- **No agent-attribution trailers.** Commits are authored by the human
  pushing them. The `attribution` setting in `.claude/settings.json`
  is empty for this reason; do not re-enable it.
- **No internal-workflow language.** No phase numbers, no version
  sequences, no references to a planning document, no "as
  instructed", no working names. The full forbidden-token list lives
  in `scripts/scan-leakage.sh` and is authoritative; the scanner runs
  as a pre-commit hook and in CI. Commits read as if a human engineer
  wrote them in the natural course of work.

## Pre-commit gates

Before any commit lands, the following must pass:

| Gate | Command |
|---|---|
| Frontend type-check + build | `pnpm exec tsc --noEmit && pnpm build` |
| Rust fmt + clippy + tests | `cd src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test --lib` |
| Python lint + types + tests | `cd brain && uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest` |
| Leakage scan | `scripts/scan-leakage.sh` |

To wire these in locally, install [pre-commit](https://pre-commit.com):

```sh
uv tool install pre-commit
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

`.pre-commit-config.yaml` runs the leakage scan on every commit and
validates Conventional Commits on the message.

CI runs the same gates on every push and pull request.

## When you get stuck

The escalation protocol in `` §8 applies to humans
too: don't retry blindly, don't lower thresholds to make a check pass,
don't bypass hooks. If a gate is wrong, fix the gate (in a separate
commit) — don't bypass it. If you're truly stuck, file an issue with
what you tried, what failed, and your best hypothesis.

## Code of conduct

By participating in any project space — issues, pull requests,
discussions — you agree to abide by the
[Contributor Covenant 2.1](CODE_OF_CONDUCT.md).

## License

By contributing, you agree your contribution is licensed under the
project's [MIT license](LICENSE).
