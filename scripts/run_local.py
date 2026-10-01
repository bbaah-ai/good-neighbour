"""Interactive local CLI for The Good Neighbour, including the HITL approval gate.

Usage::

    # Single submission passed on the command line:
    python scripts/run_local.py "40 hot trays of chicken & rice at MTCC South Building"

    # Interactive loop (persists session state in SQLite across turns):
    python scripts/run_local.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from google.genai import types

from good_neighbour import config, nodes, services


def _print_event(event: Any) -> None:
    if not event.content or not event.content.parts:
        return
    for part in event.content.parts:
        if part.text:
            print(part.text)


def _prompt_coordinator(fc: Any) -> types.Content:
    raw = input("\n[Coordinator decision — approve | revise: <note> | reject: <reason>]> ").strip()
    head, _, note = (raw or "approve").partition(":")
    decision = head.strip().lower()
    if decision not in {"approve", "revise", "reject"}:
        decision = "approve" if decision.startswith("y") else "reject"
    payload = {"decision": decision, "note": note.strip() or None}
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(id=fc.id, name=fc.name, response=payload)
            )
        ],
    )


async def run_submission(text: str, *, user_id: str = "local_coordinator") -> None:
    runner = services.build_runner()
    session = await runner.session_service.create_session(app_name=config.APP_NAME, user_id=user_id)
    message: types.Content | None = types.Content(role="user", parts=[types.Part(text=text)])
    while message is not None:
        pending_fc = None
        async for event in runner.run_async(
            user_id=user_id, session_id=session.id, new_message=message
        ):
            _print_event(event)
            calls = event.get_function_calls()
            if calls and calls[0].name == "adk_request_input":
                pending_fc = calls[0]
        message = _prompt_coordinator(pending_fc) if pending_fc else None

    await nodes.await_background_tasks()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run The Good Neighbour locally.")
    parser.add_argument("submission", nargs="?", help="Surplus food submission text or JSON.")
    parser.add_argument("--user-id", default="local_coordinator")
    args = parser.parse_args()

    if args.submission:
        await run_submission(args.submission, user_id=args.user_id)
        return

    print("The Good Neighbour — local interactive shell (Ctrl-D or 'quit' to exit)")
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        if text.lower() in {"quit", "exit"}:
            break
        await run_submission(text, user_id=args.user_id)


if __name__ == "__main__":
    asyncio.run(main())
