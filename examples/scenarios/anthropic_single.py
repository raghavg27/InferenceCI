"""Single-shot Anthropic Messages scenario."""

from __future__ import annotations

from anthropic import Anthropic


def run(input: dict) -> dict:
    client = Anthropic()
    resp = client.messages.create(
        model=input.get("model", "claude-haiku-4-5"),
        max_tokens=input.get("max_tokens", 256),
        messages=[{"role": "user", "content": input["query"]}],
    )
    return {"answer": resp.content[0].text if resp.content else ""}
