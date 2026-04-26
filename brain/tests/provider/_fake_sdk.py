"""Fake Claude Agent SDK client for use in provider tests.

Records the prompt that was queried and replays a scripted sequence of
SDK messages so the AnthropicProvider's translation logic can be
exercised without an Anthropic API key.

Multi-turn shape: each call to ``receive_response`` consumes messages
from the front of the buffer up to and including the next
``ResultMessage`` (which terminates a turn). Subsequent calls continue
from where the previous one stopped, mirroring the real SDK's
turn-by-turn drain.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Iterable
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


def text_message(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model="fake-model")


def tool_call_message(*, call_id: str, name: str, input_: dict[str, Any]) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolUseBlock(id=call_id, name=name, input=input_)],
        model="fake-model",
    )


def tool_result_message(
    *,
    call_id: str,
    output: str,
    is_error: bool = False,
) -> UserMessage:
    return UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id=call_id,
                content=output,
                is_error=is_error,
            )
        ],
    )


def result_message(*, total_cost_usd: float | None = None) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=12,
        duration_api_ms=11,
        is_error=False,
        num_turns=1,
        session_id="fake-session",
        total_cost_usd=total_cost_usd,
    )


class FakeClient:
    def __init__(self, messages: Iterable[Any]) -> None:
        self._messages: deque[Any] = deque(messages)
        self.queries: list[str] = []
        self.options: ClaudeAgentOptions | None = None

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    async def receive_response(self) -> AsyncIterator[Any]:
        # Yield from the front of the buffer until (and including) the
        # next ResultMessage; subsequent calls pick up where this one
        # stops.
        while self._messages:
            message = self._messages.popleft()
            yield message
            if isinstance(message, ResultMessage):
                return


def factory_for(messages: Iterable[Any]) -> tuple[FakeClient, Any]:
    """Returns the fake client and a factory closure that always returns it."""
    fake = FakeClient(messages)

    def factory(options: ClaudeAgentOptions) -> FakeClient:
        fake.options = options
        return fake

    return fake, factory
