# Recipes

Ready-to-paste configurations. Put one in your user file (`timonel config edit`) or apply it with
`timonel config import recipe.yaml`. Every block on this page is validated by the test suite, so it
matches the current schema. `docs/reference/configuration.md` explains each key.

## Spend as little as possible

Start one tier lower, stop at tier 3, escalate at most once, and refuse to spend more than two dollars
a day on pay-per-use APIs.

```yaml
default_profile: cheap
routing:
  prefer_subscription: true
review:
  enabled: false
budgets:
  - name: daily-api
    agent: "*"
    period: day
    max_usd: 2
    action: skip
```

## Maximum quality

Always start at the strongest tier and have every write reviewed by another agent.

```yaml
default_profile: max
review:
  enabled: true
  min_tier: 1
  strictness: strict
  max_rounds: 2
```

## Only GitHub Copilot

Useful with the GitHub Student Developer Pack. Every tier, the classifier, the planner and the
reviewer use Copilot; nothing else is called.

```yaml
tiers:
  1:
    - {agent: copilot, model: auto, effort: low}
  2:
    - {agent: copilot, model: auto, effort: medium}
  3:
    - {agent: copilot, model: auto, effort: high}
  4:
    - {agent: copilot, model: auto, effort: high}
classifier:
  ladder:
    - {agent: copilot, model: auto, effort: low}
planner:
  ladder:
    - {agent: copilot, model: auto, effort: low}
review:
  enabled: false
```

The model names are the ones the Copilot CLI accepts; `auto` lets Copilot choose. Change them with
`timonel config set` if your plan offers others.

## Never touch some agents

```yaml
routing:
  deny_agents: [deepseek]
```

## A quiet weekly budget for one agent

```yaml
budgets:
  - name: codex-week
    agent: codex
    period: week
    max_requests: 200
    action: ask
```

`skip` moves on to the next rung, `ask` asks before going over (in the terminal), `stop` ends the run.
