"""Optional Cloud Run push endpoint for ambient Pub/Sub intake.

Receives a standard Cloud Pub/Sub push envelope, decodes the base64 payload, and
starts (or resumes) a workflow session via :func:`good_neighbour.services.build_runner`.
When a submission pauses at ``coordinator_approval``, the HTTP 202 response returns
the ``session_id`` and ``interrupt_id`` so a coordinator UI or webhook can resume it.
"""

from __future__ import annotations

import base64
from typing import Any

from fastapi import FastAPI, HTTPException
from google.genai import types
from pydantic import BaseModel

from good_neighbour import config, nodes, services
from good_neighbour.schemas import ApprovalDecision

api = FastAPI(title="The Good Neighbour — Push Endpoint", version="0.1.0")
_runner = services.build_runner()


class PubSubMessage(BaseModel):
    """Inner Pub/Sub push message."""

    data: str
    messageId: str | None = None  # noqa: N815
    attributes: dict[str, str] | None = None


class PubSubEnvelope(BaseModel):
    """Standard Cloud Pub/Sub push request body."""

    message: PubSubMessage
    subscription: str | None = None


class ResumeRequest(BaseModel):
    """Coordinator resume request body."""

    user_id: str = "pubsub"
    interrupt_id: str
    decision: ApprovalDecision


@api.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness check for Cloud Run."""
    return {"status": "ok", "app": config.APP_NAME}


@api.post("/pubsub/push")
async def pubsub_push(envelope: PubSubEnvelope) -> dict[str, Any]:
    """Ingest one Pub/Sub push event."""
    try:
        payload_text = base64.b64decode(envelope.message.data).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid base64 data: {exc}") from exc

    session = await _runner.session_service.create_session(
        app_name=config.APP_NAME, user_id="pubsub"
    )
    msg = types.Content(role="user", parts=[types.Part(text=payload_text)])
    pending_interrupt: str | None = None
    async for event in _runner.run_async(user_id="pubsub", session_id=session.id, new_message=msg):
        calls = event.get_function_calls()
        if calls and calls[0].name == "adk_request_input":
            pending_interrupt = calls[0].id

    await nodes.await_background_tasks()
    updated = await _runner.session_service.get_session(
        app_name=config.APP_NAME, user_id="pubsub", session_id=session.id
    )
    return {
        "session_id": session.id,
        "status": "awaiting_coordinator"
        if pending_interrupt
        else (updated.state.get("outcome") if updated else "done"),
        "interrupt_id": pending_interrupt,
    }


@api.post("/sessions/{session_id}/resume")
async def resume_session(session_id: str, body: ResumeRequest) -> dict[str, Any]:
    """Resume a paused coordinator approval gate."""
    msg = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=body.interrupt_id,
                    name="adk_request_input",
                    response=body.decision.model_dump(mode="json"),
                )
            )
        ],
    )
    async for _ in _runner.run_async(user_id=body.user_id, session_id=session_id, new_message=msg):
        pass
    await nodes.await_background_tasks()
    updated = await _runner.session_service.get_session(
        app_name=config.APP_NAME, user_id=body.user_id, session_id=session_id
    )
    return {
        "session_id": session_id,
        "outcome": updated.state.get("outcome") if updated else None,
        "dispatch_order": updated.state.get("dispatch_order") if updated else None,
    }
