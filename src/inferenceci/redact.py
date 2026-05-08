"""Prompt-redaction helpers.

We don't write prompts to reports today, but if a future feature surfaces
prompts/messages from spans, route them through `redact()` first when
`redact_prompts: true` (the default per PRD).
"""

from __future__ import annotations


def redact(text: str | None) -> str | None:
    if text is None:
        return None
    return "[REDACTED]"
