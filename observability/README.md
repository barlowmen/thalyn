# Observability — local Langfuse for Thalyn agent traces

Thalyn emits OpenTelemetry GenAI spans for every agent run, every LLM call, every tool invocation, and every orchestration node transition. By default those spans go to a no-op exporter — nothing leaves your machine. When you want a UI to inspect them, bring up the local Langfuse stack defined here.

## Bring it up

```bash
docker compose -f observability/docker-compose.yml up -d
```

Six containers come up: PostgreSQL (Langfuse metadata), ClickHouse (trace store), Redis (queue), MinIO (event upload bucket), the Langfuse worker, and the Langfuse web UI. The web UI binds to **127.0.0.1:3000** only — there is no exposure outside your machine.

First-run setup:

1. Open <http://localhost:3000> and sign up. (Local accounts; no email verification.)
2. Create an organisation and a project.
3. Copy the project's **public key** and **secret key** from the project's Settings → API Keys page.

## Wire Thalyn to Langfuse

Set the environment variable before launching Thalyn:

```bash
export THALYN_OTEL_OTLP_ENDPOINT=http://localhost:3000/api/public/otel
export THALYN_OTEL_OTLP_HEADERS='Authorization=Basic $(echo -n "<public-key>:<secret-key>" | base64)'
```

Restart Thalyn. New runs land in Langfuse within a few seconds; the worker may take 10–20 seconds to backfill ClickHouse on first ingest.

The Settings → Observability panel gives you the same controls in the UI; the env-var path is for headless / scripted use.

## Tear it down

```bash
docker compose -f observability/docker-compose.yml down
```

Add `-v` if you also want to wipe the trace history. The compose file's volumes are the only place trace data persists; nothing else on your machine is touched.

## Why these particular images

- **Langfuse 3.x** — supports OTLP/HTTP ingestion natively (since v3.0). Earlier versions required a Langfuse-specific SDK.
- **PostgreSQL 16** — Langfuse's metadata store. v16 is its supported floor in 2026.
- **ClickHouse 24.8** — the trace store after the 2025 acquisition. The compose runs single-node since this is local-only.
- **MinIO** — Langfuse's S3-compatible event upload backend. Local in-cluster, no AWS account needed.
- **Redis 7** — queue for the worker.

## What's deliberately omitted

- **No HTTPS / no reverse proxy.** Local-only; the bind address is `127.0.0.1`.
- **No backups.** Trace data is debugging telemetry, not production state.
- **No SSO.** Local accounts only.
- **No metrics / no Grafana.** The Langfuse UI covers the trace use case; we don't ship a metrics stack.

If you want any of these, the compose file is a starting point — fork it.
