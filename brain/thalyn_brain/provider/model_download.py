"""Local-model availability checks + pull progress.

Local providers (Ollama, MLX) need a model file present before they
can stream. This module wraps the provider-specific download
mechanics in a uniform shape so the renderer doesn't need to know
how each provider stores models.

Ollama: query ``/api/tags`` for the local catalogue; trigger
``/api/pull`` (NDJSON progress stream) when a model is missing.

MLX: ``mlx-lm`` downloads weights from Hugging Face on first
``stream_generate`` call; we expose a ``check`` that returns
``unknown`` so the renderer can surface "the first turn will
download the weights" without blocking on a pre-fetch.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class ModelStatus:
    """Outcome of a local-availability check."""

    provider_id: str
    model: str
    state: str  # "available" | "missing" | "unknown"
    detail: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "providerId": self.provider_id,
            "model": self.model,
            "state": self.state,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PullProgress:
    """One line of pull-time progress, normalised across providers."""

    status: str
    completed: int | None = None
    total: int | None = None
    digest: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "completed": self.completed,
            "total": self.total,
            "digest": self.digest,
        }


ClientFactory = Callable[[], httpx.AsyncClient]


def _default_client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0))


async def check_ollama_model(
    *,
    base_url: str,
    model: str,
    client_factory: ClientFactory | None = None,
) -> ModelStatus:
    """Look the model up in Ollama's local tag catalogue."""
    factory = client_factory or _default_client_factory
    try:
        async with factory() as client:
            response = await client.get(f"{base_url.rstrip('/')}/api/tags")
            if response.status_code != 200:
                return ModelStatus(
                    provider_id="ollama",
                    model=model,
                    state="unknown",
                    detail=f"ollama returned {response.status_code}",
                )
            payload = response.json()
    except httpx.HTTPError as exc:
        return ModelStatus(
            provider_id="ollama",
            model=model,
            state="unknown",
            detail=f"ollama unreachable: {exc}",
        )
    except json.JSONDecodeError:
        return ModelStatus(
            provider_id="ollama",
            model=model,
            state="unknown",
            detail="ollama tag listing was not JSON",
        )

    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return ModelStatus(
            provider_id="ollama",
            model=model,
            state="missing",
            detail="ollama returned no models",
        )

    target_root = model.split(":")[0]
    for entry in models:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("model")
        if not isinstance(name, str):
            continue
        # Match either an exact tag (qwen3-coder:7b) or the model
        # root (qwen3-coder) when no tag was specified.
        if name == model:
            return ModelStatus(provider_id="ollama", model=model, state="available")
        if ":" in name and name.split(":")[0] == target_root and ":" not in model:
            return ModelStatus(
                provider_id="ollama",
                model=model,
                state="available",
                detail=f"resolved tag: {name}",
            )

    return ModelStatus(
        provider_id="ollama",
        model=model,
        state="missing",
        detail=f"{model} not in local catalogue",
    )


async def pull_ollama_model(
    *,
    base_url: str,
    model: str,
    client_factory: ClientFactory | None = None,
) -> AsyncIterator[PullProgress]:
    """Stream Ollama's pull progress.

    Yields one ``PullProgress`` per NDJSON line; the final entry
    typically carries ``status="success"``. On transport failure or
    a non-200 status we yield one terminal ``PullProgress`` with
    ``status`` describing the error and stop.
    """
    factory = client_factory or _default_client_factory
    payload = {"model": model, "stream": True}
    try:
        async with factory() as client:
            async with client.stream(
                "POST",
                f"{base_url.rstrip('/')}/api/pull",
                json=payload,
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    snippet = body.decode("utf-8", errors="replace")[:200]
                    yield PullProgress(
                        status=f"error: ollama returned {response.status_code}: {snippet}",
                    )
                    return
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    yield _to_progress(record)
                    if record.get("error"):
                        return
    except httpx.HTTPError as exc:
        yield PullProgress(status=f"error: ollama unreachable: {exc}")


def _to_progress(record: dict[str, Any]) -> PullProgress:
    status = record.get("status") if isinstance(record.get("status"), str) else "progress"
    completed = record.get("completed") if isinstance(record.get("completed"), int) else None
    total = record.get("total") if isinstance(record.get("total"), int) else None
    digest = record.get("digest") if isinstance(record.get("digest"), str) else None
    error = record.get("error")
    if isinstance(error, str) and error:
        status = f"error: {error}"
    return PullProgress(
        status=status or "progress",
        completed=completed,
        total=total,
        digest=digest,
    )


def check_mlx_model(*, model: str) -> ModelStatus:
    """MLX downloads on first stream — we report ``unknown`` so the
    UI can surface "first turn may take a while" without blocking on
    a pre-fetch we can't easily probe."""
    return ModelStatus(
        provider_id="mlx",
        model=model,
        state="unknown",
        detail="mlx-lm fetches weights on first stream; first turn may pause to download.",
    )


__all__ = [
    "ModelStatus",
    "PullProgress",
    "check_mlx_model",
    "check_ollama_model",
    "pull_ollama_model",
]
