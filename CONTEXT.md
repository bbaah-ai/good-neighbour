# Project Context — The Good Neighbour (`good_neighbour`)

## Overview
*The Good Neighbour* is an open-source surplus-food dispatch agent built on **Google Agent Development Kit (ADK 2.0)** (`google.adk.Workflow` + `google.adk.apps.App`).

## Key Architectural Invariants
1. **Pre-LLM Guardrails First:** Every input must pass through `screen_submission` (`good_neighbour/nodes.py`) and `guard_model_request` (`good_neighbour/callbacks.py`) so PII is redacted and prompt-injection attempts are rejected before any model call.
2. **Zero-LLM Fast Path:** Small (`<= 20` meals), ambient, non-perishable donations with a known location must take the `low_risk` route (`route_by_risk` -> `auto_match` -> `dispatch_donation`) without invoking `matcher_agent` or `risk_reviewer_agent`.
3. **Strategic Model Routing:**
   - `triage_agent` and `matcher_agent` use `config.FAST_MODEL` (`gemini-2.5-flash`).
   - `risk_reviewer_agent` uses `config.REASONING_MODEL` (`gemini-2.5-pro`).
4. **Hard Human-in-the-Loop Gate:** Any `needs_review` lot must pause at `coordinator_approval` (`@node(rerun_on_resume=True)` yielding `RequestInput(response_schema=ApprovalDecision)`). Lots rated `risk_level == "unsafe"` cannot be approved even if a coordinator replies `approve`.
5. **Guided Tool Errors:** Tools in `good_neighbour/tools.py` never raise unhandled exceptions for expected domain failures; they return `ToolError.model_dump()` with a concrete `recovery_instruction`.
6. **Observability & Privacy:** Every tool and dispatch action logs `phase="INTENT"` before and `phase="OUTCOME"` after execution via `good_neighbour/observability.py`, filtered by `PiiRedactingFilter`.
