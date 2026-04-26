# thalyn-brain

The Python sidecar that hosts the agent brain. Spawned by the Rust core as
a child process and addressed over NDJSON-framed JSON-RPC 2.0.

For now (walking-skeleton stage) the sidecar speaks JSON-RPC over stdio:
the Rust core writes requests to stdin and reads responses from stdout.
A switch to a Unix domain socket / Windows named pipe is queued for the
next iteration of the runtime, when concurrent inbound clients become
relevant.

## Local development

```sh
uv sync
uv run python -m thalyn_brain                   # serve on stdio
echo '{"jsonrpc":"2.0","id":1,"method":"ping"}' | uv run python -m thalyn_brain
uv run pytest
uv run ruff check
uv run mypy
```

The `thalyn-brain` console script also exists, but Python 3.13 silently
skips uv's editable-install `_*.pth` hook, so the script can fail to find
the package when launched outside `uv run`. Invoking the module via
`python -m thalyn_brain` works in every environment we care about and is
what the desktop runtime spawns.
