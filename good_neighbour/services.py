"""Session and memory back-ends, chosen from the environment.

The ``App`` is declarative; services are deployment wiring attached to the
``Runner``. Two profiles:

* **Local** — SQLite for sessions (``DatabaseSessionService``) and SQLite
  FTS5 for memory (``SqliteMemoryService``). State survives restarts, no
  cloud project required.
* **Enterprise** — Vertex AI Agent Engine Sessions + Memory Bank when
  ``GOOGLE_GENAI_USE_ENTERPRISE=TRUE`` and ``AGENT_ENGINE_ID`` is set.

``adk web`` reaches the same outcome via flags, e.g.
``adk web --session_service_uri=sqlite+aiosqlite:///./good_neighbour_sessions.db``.
"""

from __future__ import annotations

import logging

from google.adk.memory import BaseMemoryService
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService

from . import config

logger = logging.getLogger("good_neighbour.services")

_MEMORY_DB_PATH = "./good_neighbour_memory.db"


def _use_vertex_services() -> bool:
    return bool(config.is_enterprise_mode() and config.AGENT_ENGINE_ID)


def build_session_service() -> BaseSessionService:
    """Build the persistent session store (Vertex AI Sessions in enterprise mode, else SQLite)."""
    if _use_vertex_services():
        from google.adk.sessions import VertexAiSessionService

        logger.info(
            "services.sessions", extra={"backend": "vertex_ai", "engine": config.AGENT_ENGINE_ID}
        )
        return VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION,
            agent_engine_id=config.AGENT_ENGINE_ID,
        )

    from google.adk.sessions import DatabaseSessionService

    logger.info("services.sessions", extra={"backend": "database", "url": config.SESSION_DB_URL})
    return DatabaseSessionService(db_url=config.SESSION_DB_URL)


def build_memory_service() -> BaseMemoryService:
    """Build the long-term memory service (Vertex AI Memory Bank or local SQLite/in-memory)."""
    if _use_vertex_services():
        from google.adk.memory import VertexAiMemoryBankService

        logger.info(
            "services.memory", extra={"backend": "memory_bank", "engine": config.AGENT_ENGINE_ID}
        )
        return VertexAiMemoryBankService(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION,
            agent_engine_id=config.AGENT_ENGINE_ID,
        )

    try:
        from google.adk.memory import SqliteMemoryService  # type: ignore[attr-defined]

        logger.info("services.memory", extra={"backend": "sqlite", "path": _MEMORY_DB_PATH})
        return SqliteMemoryService(db_path=_MEMORY_DB_PATH)
    except ImportError:
        from google.adk.memory import InMemoryMemoryService

        logger.info("services.memory", extra={"backend": "in_memory"})
        return InMemoryMemoryService()


def build_runner() -> Runner:
    """Build a ``Runner`` for scripts and the push endpoint wired to env-selected services."""
    from .agent import app

    return Runner(
        app=app,
        session_service=build_session_service(),
        memory_service=build_memory_service(),
    )
