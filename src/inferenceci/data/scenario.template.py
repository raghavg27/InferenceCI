"""Example scenario.

A scenario is a function `run(input: dict) -> dict`. The runner imports this
module, calls `run()`, and captures OTel spans for any LLM SDK calls made
during the call. The return value is recorded but not interpreted.
"""

from openai import OpenAI


def run(input: dict) -> dict:
    client = OpenAI()
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": input["query"]}],
    )
    return {"answer": resp.choices[0].message.content}
