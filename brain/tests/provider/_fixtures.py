"""Fixture loader for provider contract tests.

Each JSON fixture under `fixtures/` documents one canonical Anthropic
SDK message sequence in a wire-friendly shape. The loader translates
those entries back into the SDK's actual message classes so the
provider's translation layer can be exercised against them. Adding a
provider amounts to: ship a few fixtures, run them through this same
helper, and assert the contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load(name: str) -> list[Any]:
    """Read a JSON fixture and return SDK-shaped messages.

    The on-disk shape uses snake_case strings; this is the inverse of
    the AnthropicProvider's translation step, so the JSON we ship is
    human-readable and round-trippable.
    """
    path = FIXTURES_DIR / f"{name}.json"
    raw = json.loads(path.read_text())
    return [_decode(entry) for entry in raw["messages"]]


def _decode(entry: dict[str, Any]) -> Any:
    kind = entry["kind"]
    if kind == "text":
        return AssistantMessage(
            content=[TextBlock(text=cast(str, entry["text"]))],
            model="fixture-model",
        )
    if kind == "tool_call":
        return AssistantMessage(
            content=[
                ToolUseBlock(
                    id=cast(str, entry["call_id"]),
                    name=cast(str, entry["name"]),
                    input=cast(dict[str, Any], entry.get("input", {})),
                )
            ],
            model="fixture-model",
        )
    if kind == "tool_result":
        return UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id=cast(str, entry["call_id"]),
                    content=cast(str, entry.get("output", "")),
                    is_error=bool(entry.get("is_error", False)),
                )
            ],
        )
    if kind == "result":
        return ResultMessage(
            subtype="success",
            duration_ms=12,
            duration_api_ms=11,
            is_error=False,
            num_turns=1,
            session_id="fixture-session",
            total_cost_usd=entry.get("total_cost_usd"),
        )
    raise ValueError(f"unknown fixture entry kind: {kind!r}")
