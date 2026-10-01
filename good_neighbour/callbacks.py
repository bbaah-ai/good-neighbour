"""Agent callbacks: model-level guardrails and tool intent/outcome capture.

Attached to every ``LlmAgent`` in :mod:`good_neighbour.agents`. Each callback
returns ``None`` to let the normal thing happen and a value only to override.
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import BaseTool, ToolContext
from google.genai import types

from .guardrails import redact_pii, scan_injection
from .observability import log_intent, log_outcome, traced

logger = logging.getLogger("good_neighbour.callbacks")

_INTENT_KEY = "temp:_tool_intent_started"


def guard_model_request(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> LlmResponse | None:
    """Defence in depth before every model call.

    1. Re-scrubs PII from every user/tool text part (the intake screen already
       did this once; this catches anything introduced mid-conversation).
    2. Blocks the call outright if an injection heuristic fires, returning a
       fixed refusal so the agent cannot be steered off-policy.
    """
    agent_name = getattr(callback_context, "agent_name", "unknown")
    findings: list[str] = []
    for content in llm_request.contents or []:
        if content.role != "user":
            continue
        for part in content.parts or []:
            if not part.text:
                continue
            result = redact_pii(part.text)
            if result.redacted:
                part.text = result.text
                logger.info(
                    "guard.pii_redacted",
                    extra={"agent": agent_name, "categories": result.categories},
                )
            findings.extend(scan_injection(part.text))
    if findings:
        logger.warning(
            "guard.injection_blocked",
            extra={"agent": agent_name, "findings": sorted(set(findings))},
        )
        return LlmResponse(
            content=types.ModelContent(
                "Request blocked by policy: the input contains instructions that attempt to "
                "override the dispatcher. A coordinator has been notified."
            )
        )
    return None


def log_tool_intent(tool: BaseTool, args: dict[str, Any], tool_context: ToolContext) -> None:
    """Record the agent's *intended* action before the tool executes."""
    started = log_intent(
        f"tool:{tool.name}",
        agent=tool_context.agent_name,
        tool=tool.name,
        args=args,
        invocation_id=tool_context.invocation_id,
    )
    tool_context.state[_INTENT_KEY] = started
    return None


def log_tool_outcome(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, tool_response: dict[str, Any]
) -> None:
    """Record the *actual* outcome, paired with the intent, and span it."""
    started = tool_context.state.get(_INTENT_KEY) or 0.0
    ok = not (isinstance(tool_response, dict) and tool_response.get("status") == "error")
    with traced(f"tool.{tool.name}", agent=tool_context.agent_name, ok=ok) as span:
        if not ok:
            span.set_attribute("error_code", tool_response.get("error_code", "UNKNOWN"))
    log_outcome(
        f"tool:{tool.name}",
        started,
        ok=ok,
        agent=tool_context.agent_name,
        tool=tool.name,
        error_code=None if ok else tool_response.get("error_code"),
        invocation_id=tool_context.invocation_id,
    )
    return None


def tool_error_to_guidance(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, error: Exception
) -> dict[str, Any] | None:
    """Convert an unexpected tool exception into a guided error for the model.

    Tools are written to return ``ToolError`` themselves; this is the last-resort
    net so a bug never surfaces to the LLM as a raw traceback.
    """
    logger.exception(
        "tool.unhandled_error", extra={"tool": tool.name, "agent": tool_context.agent_name}
    )
    return {
        "status": "error",
        "error_code": "TOOL_EXCEPTION",
        "message": f"{tool.name} failed with {type(error).__name__}.",
        "recovery_instruction": "Do not retry the identical call. Check the arguments against the "
        "tool description, try once with corrected arguments, otherwise explain the blocker.",
    }
