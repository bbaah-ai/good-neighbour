"""The three LLM specialists and their model routing.

* ``triage_agent``        — Gemini **Flash**: structured extraction (cheap, fast).
* ``matcher_agent``       — Gemini **Flash**: tool-heavy lookup, little reasoning.
* ``risk_reviewer_agent`` — Gemini **Pro**: multi-factor food-safety judgement.

All three share the same guardrail and observability callbacks and emit
schema-validated output, so downstream function nodes receive dicts that
Pydantic can rebuild, never prose.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools import load_memory
from google.genai import types

from . import config
from .callbacks import (
    guard_model_request,
    log_tool_intent,
    log_tool_outcome,
    tool_error_to_guidance,
)
from .schemas import DonationTriage, MatchProposal, RiskAssessment
from .tools import (
    check_org_capacity,
    estimate_food_safety_window,
    find_recipient_orgs,
    lookup_donor_site,
)


def load_prompt(name: str) -> str:
    """Read a system-instruction "constitution" from ``prompts/<name>.md``."""
    return (config.PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


_COMMON = dict(
    before_model_callback=guard_model_request,
    before_tool_callback=log_tool_intent,
    after_tool_callback=log_tool_outcome,
    on_tool_error_callback=tool_error_to_guidance,
    include_contents="none",  # each specialist judges only its own input
)


def build_triage_agent(model: str = config.FAST_MODEL) -> LlmAgent:
    """Normalize a screened submission into a ``DonationTriage``."""
    return LlmAgent(
        name="triage_agent",
        model=model,
        description="Turns a donor's message into a structured donation record.",
        instruction=load_prompt("triage"),
        tools=[lookup_donor_site],
        output_schema=DonationTriage,
        output_key="triage",
        generate_content_config=types.GenerateContentConfig(temperature=0.1),
        **_COMMON,
    )


def build_matcher_agent(model: str = config.FAST_MODEL) -> LlmAgent:
    """Pick the best recipient org for a donation that needs human review."""
    return LlmAgent(
        name="matcher_agent",
        model=model,
        description="Selects the best recipient organization for a donation.",
        instruction=load_prompt("matcher"),
        tools=[find_recipient_orgs, check_org_capacity, load_memory],
        output_schema=MatchProposal,
        output_key="match_proposal",
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        **_COMMON,
    )


def build_risk_reviewer_agent(model: str = config.REASONING_MODEL) -> LlmAgent:
    """Assess food-safety risk of a proposed match for the coordinator."""
    return LlmAgent(
        name="risk_reviewer_agent",
        model=model,
        description="Reviews food-safety risk and handling conditions for a proposed dispatch.",
        instruction=load_prompt("risk_reviewer"),
        tools=[estimate_food_safety_window],
        output_schema=RiskAssessment,
        output_key="risk_assessment",
        generate_content_config=types.GenerateContentConfig(temperature=0.0),
        **_COMMON,
    )
