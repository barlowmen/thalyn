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
uv run thalyn-brain                  # serve on stdio
echo '{"jsonrpc":"2.0","id":1,"method":"ping"}' | uv run thalyn-brain
uv run pytest
uv run ruff check
uv run mypy
```
