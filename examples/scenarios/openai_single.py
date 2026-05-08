"""Single-shot OpenAI chat scenario."""

from __future__ import annotations

from openai import OpenAI


def run(input: dict) -> dict:
    client = OpenAI()
    resp = client.chat.completions.create(
        model=input.get("model", "gpt-4o-mini"),
        messages=[{"role": "user", "content": input["query"]}],
        max_tokens=input.get("max_tokens", 256),
    )
    return {"answer": resp.choices[0].message.content}
