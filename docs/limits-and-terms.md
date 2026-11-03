# Limits and terms of service

Timonel automates command-line tools that you already have access to. It does not grant new access,
share accounts or work around any limit. Before you automate a plan, **read its terms**.

## Things to check for each plan

- **Automated or programmatic use.** Some consumer subscriptions are meant for interactive use by a
  person. Running them from scripts, schedulers or other programs may not be allowed, or may be
  allowed only within fair-use limits.
- **Usage limits.** Subscriptions have message, token or credit limits per time window. Timonel
  detects common limit messages and moves on to another agent, which also means it can consume
  several plans' allowances quickly.
- **Billing.** `billing: api` agents spend money per request. Set `cost_per_attempt_usd` and review
  `timonel stats`. Claude Code's `--max-budget-usd` can be added to `base_args` if you want a hard
  cap per attempt.
- **Data handling.** Each agent sends your code and prompts to its provider under that provider's
  privacy terms. Routing a task to an agent means that provider sees it.
- **Third-party endpoints.** Using DeepSeek through Claude Code means Claude Code talks to DeepSeek's
  Anthropic-compatible API with your DeepSeek key. Check that both products' terms allow it for your
  use.
- **Organization policies.** Your employer may restrict which AI services can see company code.

## Where to read them

| Provider | Look for |
|---|---|
| Anthropic (Claude Code) | Consumer terms, commercial terms, usage policy, Claude Code documentation on authentication and costs |
| OpenAI (Codex CLI) | Terms of use, usage policies, Codex documentation on plans and limits |
| GitHub (Copilot CLI) | GitHub Copilot product terms, premium request and AI credit documentation |
| Google (Antigravity) | Antigravity terms and usage limits |
| DeepSeek | Open platform terms of service and pricing |

Links change often, so search the provider's site for the current version.

Timonel is distributed under the MIT license, without warranty. You are responsible for how you use
the services it calls.
