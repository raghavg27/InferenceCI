"""Fake multi-step agent: plans, then makes N follow-up LLM calls.

Demonstrates a scenario that issues multiple model calls per run, so the cost
diff captures both per-call and per-run aggregates.
"""

from __future__ import annotations

from openai import OpenAI


def run(input: dict) -> dict:
    client = OpenAI()
    model = input.get("model", "gpt-4o-mini")
    steps = int(input.get("steps", 3))
    topic = input.get("topic", "neural network training")

    plan = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": f"Outline {steps} short bullet steps for understanding {topic}.",
            }
        ],
        max_tokens=200,
    )
    outline = plan.choices[0].message.content or ""

    answers: list[str] = []
    for i in range(steps):
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Answer in <=2 sentences."},
                {"role": "user", "content": f"Step {i + 1} of: {outline}"},
            ],
            max_tokens=120,
        )
        answers.append(r.choices[0].message.content or "")

    return {"outline": outline, "answers": answers}
