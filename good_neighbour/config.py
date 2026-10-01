"""Runtime configuration for The Good Neighbour.

Every tunable lives here so that policy thresholds, model routing, and
environment detection are in one auditable place. Nothing in this module
reads secrets — see :mod:`good_neighbour.secrets` for that.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "good_neighbour"
PROJECT_TAGLINE = "Open-source AI dispatch for surplus food."

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
DATA_DIR = REPO_ROOT / "data"
PROMPTS_DIR = PACKAGE_DIR / "prompts"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_enterprise_mode() -> bool:
    """Return True when ADK should talk to Vertex AI rather than Google AI Studio.

    ``GOOGLE_GENAI_USE_ENTERPRISE`` is the current switch; the legacy
    ``GOOGLE_GENAI_USE_VERTEXAI`` is honoured for compatibility.
    """
    return _env_bool("GOOGLE_GENAI_USE_ENTERPRISE") or _env_bool("GOOGLE_GENAI_USE_VERTEXAI")


# --- Model routing ----------------------------------------------------------
# Fast model: extraction, tool-heavy matching. Reasoning model: safety review.
FAST_MODEL = os.environ.get("GN_FAST_MODEL", "gemini-2.5-flash")
REASONING_MODEL = os.environ.get("GN_REASONING_MODEL", "gemini-2.5-pro")

# --- Dispatch policy --------------------------------------------------------
# Donations at or below this size that are also non-perishable take the
# deterministic, zero-LLM fast path. Everything else needs human approval.
AUTO_DISPATCH_MAX_MEALS = int(os.environ.get("GN_AUTO_DISPATCH_MAX_MEALS", "20"))
DEFAULT_RADIUS_KM = float(os.environ.get("GN_DEFAULT_RADIUS_KM", "5"))
MAX_RADIUS_KM = 25.0

# Toronto Financial District (King & Bay). Used only when a submission has no
# resolvable pickup location; the triage output flags ``location_assumed``.
DEFAULT_PICKUP_LAT = 43.6487
DEFAULT_PICKUP_LNG = -79.3817

# --- Observability ----------------------------------------------------------
LOG_LEVEL = os.environ.get("GN_LOG_LEVEL", "INFO").upper()
TRACE_EXPORTER = os.environ.get("GN_TRACE_EXPORTER", "console").lower()

# --- Persistence -------------------------------------------------------------
SESSION_DB_URL = os.environ.get(
    "GN_SESSION_DB_URL", "sqlite+aiosqlite:///./good_neighbour_sessions.db"
)
AGENT_ENGINE_ID = os.environ.get("AGENT_ENGINE_ID")
GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
GOOGLE_CLOUD_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
