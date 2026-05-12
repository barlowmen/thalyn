"""Unit tests for ``thalyn_brain.lead_delegation``.

These cover the pure helpers — addressing detection, the sanity-check
critic, the default-system-prompt fallback. Integration with
``thread.send`` lives in ``test_thread_send_delegation``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.agents import AgentRecord, AgentRecordsStore, new_agent_id
from thalyn_brain.lead_delegation import (
    DEFAULT_LEAD_SYSTEM_PROMPT_TEMPLATE,
    DEFAULT_SUB_LEAD_SYSTEM_PROMPT_TEMPLATE,
    ESCALATION_QUESTION_THRESHOLD,
    LOW_CONFIDENCE_NOTE,
    build_attribution_chain,
    effective_system_prompt,
    evaluate_lead_escalation,
    find_addressed_lead,
    sanity_check_lead_reply,
)


def _now() -> int:
    return int(time.time() * 1000)


def _lead(**overrides: Any) -> AgentRecord:
    base: dict[str, Any] = {
        "agent_id": new_agent_id(),
        "kind": "lead",
        "display_name": "Lead-Default",
        "parent_agent_id": None,
        "project_id": "proj_default",
        "scope_facet": None,
        "memory_namespace": "lead-default",
        "default_provider_id": "anthropic",
        "system_prompt": "",
        "status": "active",
        "created_at_ms": _now(),
        "last_active_at_ms": _now(),
    }
    base.update(overrides)
    return AgentRecord(**base)


@pytest.mark.parametrize(
    "message",
    [
        "Lead-Default, status on the auth refactor?",
        "Lead-Default: status on the auth refactor?",
        "Lead-Default - status on the auth refactor?",
        "Lead-Default status on the auth refactor?",
        "lead-default, what's up?",
    ],
)
def test_find_addressed_lead_matches_separator_variants(message: str) -> None:
    leads = [_lead()]
    addressed = find_addressed_lead(message, leads)
    assert addressed is not None
    assert addressed.lead.display_name == "Lead-Default"
    assert "Lead-Default" not in addressed.body


def test_find_addressed_lead_skips_inactive_leads() -> None:
    leads = [_lead(status="paused")]
    assert find_addressed_lead("Lead-Default, hi", leads) is None


def test_find_addressed_lead_returns_none_for_unaddressed_message() -> None:
    leads = [_lead()]
    assert find_addressed_lead("how is the build going?", leads) is None


def test_find_addressed_lead_resolves_unique_match_when_one_lead_matches() -> None:
    leads = [_lead(display_name="Sam"), _lead(display_name="Pat")]
    addressed = find_addressed_lead("Sam, ping", leads)
    assert addressed is not None
    assert addressed.lead.display_name == "Sam"
    assert addressed.body == "ping"


def test_find_addressed_lead_returns_none_when_no_lead_matches_uniquely() -> None:
    # Both leads share the same display_name — ambiguous, decline to
    # delegate rather than guessing.
    leads = [_lead(display_name="Sam"), _lead(display_name="Sam")]
    assert find_addressed_lead("Sam, ping", leads) is None


def test_find_addressed_lead_handles_whitespace_only_message() -> None:
    leads = [_lead()]
    assert find_addressed_lead("   ", leads) is None


@pytest.mark.parametrize(
    "message",
    [
        "hey @Lead-Default, what's the status?",
        "anyone know? @lead-default has the context.",
        "ask @Lead-Default about the auth refactor",
        "(@Lead-Default already covered this)",
    ],
)
def test_find_addressed_lead_recognises_mid_message_at_mention(message: str) -> None:
    leads = [_lead()]
    addressed = find_addressed_lead(message, leads)
    assert addressed is not None
    assert addressed.lead.display_name == "Lead-Default"
    # Mid-message mentions preserve the surrounding sentence —
    # the lead sees the full body, not a stripped suffix.
    assert addressed.body == message


def test_find_addressed_lead_at_mention_requires_word_boundary() -> None:
    leads = [_lead(display_name="Sam"), _lead(display_name="Samantha")]
    addressed = find_addressed_lead("ask @Sam about it", leads)
    assert addressed is not None
    assert addressed.lead.display_name == "Sam"


def test_find_addressed_lead_ignores_email_style_at_in_address() -> None:
    leads = [_lead(display_name="Sam")]
    # An email-like ``user@host`` shouldn't trigger a mention — the
    # ``@`` must follow whitespace / punctuation, not a word char.
    assert find_addressed_lead("ping carl@sam tomorrow", leads) is None


def test_find_addressed_lead_prefers_leading_address_over_at_mention() -> None:
    leads = [_lead(display_name="Sam"), _lead(display_name="Pat")]
    addressed = find_addressed_lead("Sam, can you ask @Pat about it?", leads)
    assert addressed is not None
    assert addressed.lead.display_name == "Sam"


def test_find_addressed_lead_ambiguous_at_mention_returns_none() -> None:
    leads = [_lead(display_name="Sam"), _lead(display_name="Pat")]
    assert find_addressed_lead("hey @Sam and @Pat, sync up please", leads) is None


def test_effective_system_prompt_uses_stored_when_present() -> None:
    lead = _lead(system_prompt="You are Sam, the harness lead.")
    assert effective_system_prompt(lead) == "You are Sam, the harness lead."


def test_effective_system_prompt_default_names_lead() -> None:
    lead = _lead(display_name="Sam", system_prompt="")
    expected = DEFAULT_LEAD_SYSTEM_PROMPT_TEMPLATE.format(name="Sam")
    assert effective_system_prompt(lead) == expected


def test_sanity_check_passes_typical_reply() -> None:
    verdict = sanity_check_lead_reply("Three commits shipped overnight.")
    assert verdict.ok is True
    assert verdict.note is None


def test_sanity_check_flags_empty_reply() -> None:
    verdict = sanity_check_lead_reply("   ")
    assert verdict.ok is False
    assert verdict.note is not None
    assert "empty" in verdict.note.lower()


@pytest.mark.parametrize(
    "reply",
    [
        "I don't know what's going on.",
        "I'm not sure about the status.",
        "Unclear at this point.",
    ],
)
def test_sanity_check_flags_hedge_phrases(reply: str) -> None:
    verdict = sanity_check_lead_reply(reply)
    assert verdict.ok is False
    assert verdict.note == LOW_CONFIDENCE_NOTE


def test_evaluate_escalation_returns_none_below_threshold() -> None:
    lead = _lead(display_name="Lead-Sam")
    reply = "I have one open question. Should we land the test fixtures?"
    assert evaluate_lead_escalation(lead, reply) is None


def test_evaluate_escalation_flags_high_density_reply() -> None:
    lead = _lead(display_name="Lead-Sam")
    reply = (
        "A few open questions before I can land the next slice:\n"
        "1. Do we keep the legacy helper around for one cycle?\n"
        "2. Should the test fixtures live with this slice or the next?\n"
        "3. Is the new session boundary documented yet?"
    )
    signal = evaluate_lead_escalation(lead, reply)
    assert signal is not None
    assert signal.lead_id == lead.agent_id
    assert signal.density == "high"
    assert signal.suggestion == "open_drawer"
    assert signal.question_count >= ESCALATION_QUESTION_THRESHOLD


def test_evaluate_escalation_to_wire_round_trip() -> None:
    lead = _lead(display_name="Lead-Sam")
    reply = "q1? q2? q3?"
    signal = evaluate_lead_escalation(lead, reply)
    assert signal is not None
    wire = signal.to_wire()
    assert wire == {
        "leadId": lead.agent_id,
        "questionCount": 3,
        "density": "high",
        "suggestion": "open_drawer",
    }


# -----------------------------------------------------------------
# Sub-lead addressing + attribution chain (Phase v0.36 / F2.3)
# -----------------------------------------------------------------


def _sub_lead(parent: AgentRecord, **overrides: Any) -> AgentRecord:
    base: dict[str, Any] = {
        "agent_id": new_agent_id(),
        "kind": "sub_lead",
        "display_name": "SubLead-UI",
        "parent_agent_id": parent.agent_id,
        "project_id": parent.project_id,
        "scope_facet": "ui",
        "memory_namespace": f"{parent.memory_namespace}/ui",
        "default_provider_id": parent.default_provider_id,
        "system_prompt": "",
        "status": "active",
        "created_at_ms": _now(),
        "last_active_at_ms": _now(),
    }
    base.update(overrides)
    return AgentRecord(**base)


def test_find_addressed_lead_includes_sub_leads_in_candidate_set() -> None:
    parent = _lead()
    sub = _sub_lead(parent)
    addressed = find_addressed_lead("SubLead-UI, status?", [parent, sub])
    assert addressed is not None
    assert addressed.lead.agent_id == sub.agent_id


def test_effective_system_prompt_for_sub_lead_names_parent_and_facet() -> None:
    parent = _lead(display_name="Lead-Alpha")
    sub = _sub_lead(parent, display_name="SubLead-UI", scope_facet="ui")
    expected = DEFAULT_SUB_LEAD_SYSTEM_PROMPT_TEMPLATE.format(
        name="SubLead-UI",
        parent_name="Lead-Alpha",
        scope_facet="ui",
    )
    assert effective_system_prompt(sub, parent_lead=parent) == expected


def test_effective_system_prompt_sub_lead_falls_back_when_parent_missing() -> None:
    parent = _lead()
    sub = _sub_lead(parent)
    rendered = effective_system_prompt(sub, parent_lead=None)
    assert "the project lead" in rendered
    assert "SubLead-UI" in rendered


async def test_build_attribution_chain_for_top_level_lead(tmp_path: Path) -> None:
    agents = AgentRecordsStore(data_dir=tmp_path)
    parent = _lead(display_name="Lead-Alpha", project_id="proj_alpha")
    await agents.insert(parent)

    chain = await build_attribution_chain(parent, agents=agents)
    assert chain.names == ("Thalyn", "Lead-Alpha")
    assert chain.agent_ids == ("agent_brain", parent.agent_id)


async def test_build_attribution_chain_for_sub_lead(tmp_path: Path) -> None:
    agents = AgentRecordsStore(data_dir=tmp_path)
    parent = _lead(display_name="Lead-Alpha", project_id="proj_alpha")
    sub = _sub_lead(parent, display_name="SubLead-UI")
    await agents.insert(parent)
    await agents.insert(sub)

    chain = await build_attribution_chain(sub, agents=agents)
    assert chain.names == ("Thalyn", "Lead-Alpha", "SubLead-UI")
    assert chain.agent_ids == ("agent_brain", parent.agent_id, sub.agent_id)


async def test_build_attribution_chain_to_wire_round_trip(tmp_path: Path) -> None:
    agents = AgentRecordsStore(data_dir=tmp_path)
    parent = _lead(display_name="Lead-Alpha", project_id="proj_alpha")
    sub = _sub_lead(parent, display_name="SubLead-UI")
    await agents.insert(parent)
    await agents.insert(sub)

    chain = await build_attribution_chain(sub, agents=agents)
    wire = chain.to_wire()
    assert wire == {
        "names": ["Thalyn", "Lead-Alpha", "SubLead-UI"],
        "agentIds": ["agent_brain", parent.agent_id, sub.agent_id],
    }
