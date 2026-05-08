# costdiff examples

Three runnable scenarios:

- `scenarios/openai_single.py` — one-shot chat completion.
- `scenarios/anthropic_single.py` — one-shot Messages call.
- `scenarios/multistep_agent.py` — plan + N follow-up calls (fake agent).

```bash
cd examples
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
costdiff run --output baseline_report.json
# edit a prompt or swap a model in a scenario, then:
costdiff run --output head_report.json
costdiff compare baseline_report.json head_report.json --format text
```
