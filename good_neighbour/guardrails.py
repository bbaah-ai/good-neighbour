"""Security guardrails: PII redaction and prompt-injection screening.

These run *before* any model call. The same ``redact_pii`` function is used by
the intake screen, the ``before_model_callback`` on every LLM agent, and the
logging filter, so one definition of "sensitive" governs the whole pipeline.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

# Order matters: longer, more specific patterns first so a credit-card number
# is not partially consumed by the phone regex.
_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    (
        "PHONE",
        re.compile(r"(?:(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b)"),
    ),
    # Canadian SIN / US SSN: 3-3-3 or 3-2-4 groups.
    ("GOV_ID", re.compile(r"\b\d{3}[ -]\d{3}[ -]\d{3}\b|\b\d{3}-\d{2}-\d{4}\b")),
    ("POSTAL_CODE", re.compile(r"\b[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d\b")),
]

_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "ignore_instructions",
        re.compile(r"ignore (?:all |any )?(?:previous|prior|above) instructions", re.I),
    ),
    ("disregard_system", re.compile(r"disregard (?:your|the) (?:system|developer) prompt", re.I)),
    (
        "role_override",
        re.compile(r"\byou are now\b|\bact as (?:an? )?(?:unrestricted|dan)\b", re.I),
    ),
    (
        "prompt_exfil",
        re.compile(
            r"(?:reveal|print|show|repeat) (?:your|the) (?:system )?(?:prompt|instructions)", re.I
        ),
    ),
    ("fake_system_tag", re.compile(r"^\s*(?:system|assistant)\s*:", re.I | re.M)),
    (
        "tool_hijack",
        re.compile(
            r"call (?:the )?notify_recipient_org (?:tool )?(?:now|immediately|directly)", re.I
        ),
    ),
]


@dataclass
class RedactionResult:
    """Outcome of a redaction pass."""

    text: str
    categories: list[str] = field(default_factory=list)

    @property
    def redacted(self) -> bool:  # noqa: D102
        return bool(self.categories)


def redact_pii(text: str) -> RedactionResult:
    """Replace personal identifiers with ``[REDACTED_<CATEGORY>]`` placeholders.

    Args:
      text: Arbitrary text that may contain emails, phone numbers, card numbers,
        government ids, or postal codes.

    Returns:
      The scrubbed text plus the list of categories that were removed, in order
      of first occurrence.

    """
    if not text:
        return RedactionResult(text=text or "")
    categories: list[str] = []
    out = text
    for category, pattern in _PII_PATTERNS:
        if pattern.search(out):
            out = pattern.sub(f"[REDACTED_{category}]", out)
            categories.append(category)
    return RedactionResult(text=out, categories=categories)


def scan_injection(text: str) -> list[str]:
    """Return the names of prompt-injection heuristics that fire on ``text``.

    This is a cheap, deterministic first line of defence; it is complemented by
    the model-level guard in :mod:`good_neighbour.callbacks`.
    """
    if not text:
        return []
    return [name for name, pattern in _INJECTION_PATTERNS if pattern.search(text)]


class PiiRedactingFilter(logging.Filter):
    """Logging filter that scrubs PII from messages, args, and extra fields.

    Attach to the root logger so redaction applies to every handler/sink,
    including the JSON formatter and any cloud exporter.
    """

    _SKIP = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D102
        if isinstance(record.msg, str):
            record.msg = redact_pii(record.msg).text
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._scrub(v) for k, v in record.args.items()}
            else:
                record.args = tuple(self._scrub(a) for a in record.args)
        for key, value in list(record.__dict__.items()):
            if key in self._SKIP:
                continue
            record.__dict__[key] = self._scrub(value)
        return True

    @classmethod
    def _scrub(cls, value):  # noqa: ANN001, ANN206
        if isinstance(value, str):
            return redact_pii(value).text
        if isinstance(value, dict):
            return {k: cls._scrub(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(cls._scrub(v) for v in value)
        return value
