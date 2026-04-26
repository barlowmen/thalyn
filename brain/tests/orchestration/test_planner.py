"""Tests for the LLM-driven plan generator."""

from __future__ import annotations

from thalyn_brain.orchestration.planner import plan_for
from thalyn_brain.provider import AnthropicProvider

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _provider_returning(text: str) -> AnthropicProvider:
    _fake, factory = factory_for([text_message(text), result_message()])
    return AnthropicProvider(client_factory=factory)


async def test_plan_for_parses_well_formed_json() -> None:
    provider = _provider_returning(
        """
        {
          "goal": "Refactor the auth middleware",
          "steps": [
            {
              "description": "Map current call sites.",
              "rationale": "Need an audit before changes.",
              "estimated_tokens": 800
            },
            {
              "description": "Replace the middleware adapter.",
              "rationale": "Apply the new shape.",
              "estimated_tokens": 1500
            }
          ]
        }
        """
    )
    result = await plan_for(provider, "Refactor the auth middleware")

    assert result.plan.goal == "Refactor the auth middleware"
    assert len(result.plan.nodes) == 2
    assert result.plan.nodes[0].description == "Map current call sites."
    assert result.plan.nodes[0].rationale == "Need an audit before changes."
    assert result.plan.nodes[0].estimated_cost == {"tokens": 800}
    assert result.plan.nodes[1].order == 1


async def test_plan_for_falls_back_when_text_is_not_json() -> None:
    provider = _provider_returning("Sure, I will help with that.")
    result = await plan_for(provider, "What is 2+2?")

    assert result.plan.goal == "What is 2+2?"
    assert len(result.plan.nodes) == 1
    assert result.plan.nodes[0].description == "Answer the user's question."
    assert "not JSON" in result.plan.nodes[0].rationale


async def test_plan_for_tolerates_prose_around_the_json() -> None:
    provider = _provider_returning(
        'Here is the plan: { "goal": "Hi", "steps": '
        '[{"description": "Wave back.", "rationale": "Friendly."}] } okay?'
    )
    result = await plan_for(provider, "Hi")

    assert result.plan.goal == "Hi"
    assert len(result.plan.nodes) == 1
    assert result.plan.nodes[0].description == "Wave back."


async def test_plan_for_drops_step_entries_missing_a_description() -> None:
    provider = _provider_returning(
        '{"goal": "g", "steps": [{"description": ""}, {"description": "ok"}]}'
    )
    result = await plan_for(provider, "g")
    assert [node.description for node in result.plan.nodes] == ["ok"]


async def test_plan_for_falls_back_when_steps_is_empty() -> None:
    provider = _provider_returning('{"goal": "x", "steps": []}')
    result = await plan_for(provider, "x")
    assert len(result.plan.nodes) == 1
    assert "no steps" in result.plan.nodes[0].rationale
