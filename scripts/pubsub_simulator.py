"""Ambient event simulator: replays sample donations locally or publishes to Pub/Sub.

Usage::

    # Replay sample submissions against the local runner (auto-approves HITL gates):
    python scripts/pubsub_simulator.py --local

    # Publish sample submissions to a real Cloud Pub/Sub topic:
    python scripts/pubsub_simulator.py --project my-gcp-project --topic good-neighbour-intake
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from google.genai import types

from good_neighbour import config, nodes, services


async def replay_locally(submissions_file: Path, *, auto_decision: str = "approve") -> None:
    items = json.loads(submissions_file.read_text(encoding="utf-8"))
    runner = services.build_runner()
    for idx, item in enumerate(items, start=1):
        print(f"\n=== [{idx}/{len(items)}] Submission {item.get('donation_id')} ===")
        session = await runner.session_service.create_session(
            app_name=config.APP_NAME, user_id="pubsub_simulator"
        )
        msg: types.Content | None = types.Content(
            role="user", parts=[types.Part(text=json.dumps(item))]
        )
        while msg is not None:
            pending_fc = None
            async for event in runner.run_async(
                user_id="pubsub_simulator", session_id=session.id, new_message=msg
            ):
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text)
                calls = event.get_function_calls()
                if calls and calls[0].name == "adk_request_input":
                    pending_fc = calls[0]
            if pending_fc is not None:
                print(f"🤖 Simulator auto-responding to HITL gate with '{auto_decision}'")
                msg = types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=pending_fc.id,
                                name=pending_fc.name,
                                response={"decision": auto_decision, "note": "simulator"},
                            )
                        )
                    ],
                )
            else:
                msg = None
    await nodes.await_background_tasks()


def publish_to_pubsub(project: str, topic: str, submissions_file: Path) -> None:
    from google.cloud import pubsub_v1

    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project, topic)
    items = json.loads(submissions_file.read_text(encoding="utf-8"))
    for item in items:
        data = json.dumps(item).encode("utf-8")
        future = publisher.publish(topic_path, data, donation_id=str(item.get("donation_id", "")))
        print(f"Published {item.get('donation_id')} -> message_id={future.result(timeout=15)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate ambient Pub/Sub food-rescue events.")
    parser.add_argument(
        "--file",
        type=Path,
        default=config.DATA_DIR / "sample_submissions.json",
        help="Path to JSON list of sample submissions.",
    )
    parser.add_argument(
        "--local", action="store_true", help="Replay in-process instead of Pub/Sub."
    )
    parser.add_argument("--project", default=config.GOOGLE_CLOUD_PROJECT)
    parser.add_argument("--topic", default="good-neighbour-intake")
    parser.add_argument(
        "--auto-decision",
        choices=["approve", "reject"],
        default="approve",
        help="Decision to supply when a simulated event hits the coordinator gate.",
    )
    args = parser.parse_args()

    if args.local or not args.project:
        asyncio.run(replay_locally(args.file, auto_decision=args.auto_decision))
    else:
        publish_to_pubsub(args.project, args.topic, args.file)


if __name__ == "__main__":
    main()
