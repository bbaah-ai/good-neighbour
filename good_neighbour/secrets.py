"""Secret access with no literals in code.

Resolution order for :func:`get_secret`:

1. Process environment (``.env`` loaded by ADK, Cloud Run env, CI secrets).
2. Google Secret Manager ``projects/<project>/secrets/<name>/versions/latest``
   when ``GOOGLE_CLOUD_PROJECT`` is set and the client library is installed.
3. ``default`` if provided, otherwise :class:`MissingSecretError`.

Values are cached per process and never logged.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from . import config

logger = logging.getLogger("good_neighbour.secrets")


class MissingSecretError(RuntimeError):
    """Raised when a required secret cannot be resolved from any source."""


@lru_cache(maxsize=32)
def _from_secret_manager(name: str) -> str | None:
    project = config.GOOGLE_CLOUD_PROJECT
    if not project:
        return None
    try:
        from google.cloud import secretmanager
    except ImportError:
        logger.debug("secrets.secret_manager_unavailable", extra={"secret": name})
        return None
    try:
        client = secretmanager.SecretManagerServiceClient()
        resource = f"projects/{project}/secrets/{name}/versions/latest"
        response = client.access_secret_version(request={"name": resource})
        logger.info("secrets.resolved", extra={"secret": name, "source": "secret_manager"})
        return response.payload.data.decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - surface as "not found", never crash startup
        logger.warning(
            "secrets.secret_manager_error",
            extra={"secret": name, "error_type": type(exc).__name__},
        )
        return None


def get_secret(name: str, default: str | None = None) -> str:
    """Return the secret ``name`` from env or Secret Manager.

    Args:
      name: Environment variable / Secret Manager secret id, e.g. ``GOOGLE_API_KEY``.
      default: Fallback if nothing is configured. Omit for required secrets.

    Raises:
      MissingSecretError: when the secret is required and unresolvable.

    """
    value = os.environ.get(name)
    if value:
        return value
    value = _from_secret_manager(name)
    if value:
        os.environ.setdefault(name, value)  # make it visible to SDKs that read env
        return value
    if default is not None:
        return default
    raise MissingSecretError(
        f"Secret '{name}' not found in environment or Secret Manager. "
        "Set it in good_neighbour/.env or create it with `gcloud secrets create`."
    )


def hydrate_model_credentials() -> None:
    """Pull the model credential from Secret Manager into env if it is missing.

    In AI Studio mode the credential is ``GOOGLE_API_KEY``. In Vertex mode ADC
    is used, so nothing is required here.
    """
    if config.is_enterprise_mode():
        return
    get_secret("GOOGLE_API_KEY", default="")
