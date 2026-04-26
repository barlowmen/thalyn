"""NL → cron translator tests."""

from __future__ import annotations

from thalyn_brain.orchestration.cron import (
    CronTranslation,
    parse_cron_response,
    translate_nl_to_cron,
    validate_cron,
)
from thalyn_brain.provider import AnthropicProvider

from tests.provider._fake_sdk import factory_for, result_message, text_message

# ---------------------------------------------------------------------------
# validate_cron — pure function
# ---------------------------------------------------------------------------


def test_validate_cron_accepts_standard_5_field() -> None:
    decision = validate_cron("0 6 * * 1-5")
    assert decision.valid
    assert decision.cron == "0 6 * * 1-5"
    assert decision.error is None


def test_validate_cron_accepts_6_field_with_seconds() -> None:
    decision = validate_cron("0 0 6 * * 1-5")
    assert decision.valid


def test_validate_cron_rejects_garbage() -> None:
    decision = validate_cron("every weekday")
    assert not decision.valid
    assert decision.error is not None


def test_validate_cron_rejects_3_field() -> None:
    decision = validate_cron("* * *")
    assert not decision.valid


# ---------------------------------------------------------------------------
# parse_cron_response — JSON extraction
# ---------------------------------------------------------------------------


def test_parse_returns_translation_for_well_formed_response() -> None:
    raw = '{"cron": "0 6 * * 1-5", "explanation": "weekdays at 6 a.m."}'
    decision = parse_cron_response(raw, nl_input="every weekday at 6 a.m.")
    assert decision.valid
    assert decision.cron == "0 6 * * 1-5"
    assert "weekdays" in decision.explanation


def test_parse_rejects_non_json_response() -> None:
    decision = parse_cron_response("This is prose.", nl_input="x")
    assert not decision.valid
    assert "not JSON" in (decision.error or "")


def test_parse_rejects_invalid_cron_in_response() -> None:
    raw = '{"cron": "every weekday", "explanation": ""}'
    decision = parse_cron_response(raw, nl_input="x")
    assert not decision.valid


def test_parse_rejects_missing_cron_field() -> None:
    raw = '{"explanation": "weekdays"}'
    decision = parse_cron_response(raw, nl_input="x")
    assert not decision.valid
    assert "missing" in (decision.error or "")


# ---------------------------------------------------------------------------
# translate_nl_to_cron — provider round-trip
# ---------------------------------------------------------------------------


async def test_empty_input_short_circuits_without_calling_provider() -> None:
    _fake, factory = factory_for([])
    provider = AnthropicProvider(client_factory=factory)
    decision = await translate_nl_to_cron(provider, "   ")
    assert not decision.valid
    assert decision.error == "empty input"


async def test_translate_round_trips_against_provider() -> None:
    _fake, factory = factory_for(
        [
            text_message('{"cron": "0 6 * * 1-5", "explanation": "Every weekday at 6 a.m."}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    decision = await translate_nl_to_cron(provider, "every weekday at 6 a.m.")
    assert isinstance(decision, CronTranslation)
    assert decision.valid
    assert decision.cron == "0 6 * * 1-5"
    assert decision.nl_input == "every weekday at 6 a.m."


async def test_translate_falls_through_to_invalid_on_garbage_response() -> None:
    _fake, factory = factory_for(
        [
            text_message("I think it should run sometimes."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    decision = await translate_nl_to_cron(provider, "every other tuesday")
    assert not decision.valid
    assert decision.cron == ""


def test_translation_to_wire_carries_metadata() -> None:
    decision = CronTranslation(
        cron="0 6 * * 1-5",
        explanation="weekdays at 6 a.m.",
        nl_input="weekdays at 6 a.m.",
        valid=True,
    )
    wire = decision.to_wire()
    assert wire["cron"] == "0 6 * * 1-5"
    assert wire["valid"] is True
    assert wire["nlInput"] == "weekdays at 6 a.m."
