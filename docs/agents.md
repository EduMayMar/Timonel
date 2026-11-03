# Supported agents and what was verified

Timonel never guesses flags in code: every flag lives in
[`default_routes.yaml`](https://github.com/EduMayMar/Timonel/blob/main/src/timonel/default_routes.yaml) and you can override any of them in your
own `routes.yaml`. This page records where each default comes from.

**Verified** means the flag appears in the `--help` output of the version listed, read on
2026-11-03, or in the vendor's official documentation. **Unverified** means it comes from secondary
sources and must be confirmed with `timonel doctor --smoke` on your machine.

| Agent | Command | Version read | Billing default | Flags | How Timonel calls it |
|---|---|---|---|---|---|
| `claude` | `claude` | Claude Code 2.1.317 | subscription | verified | `claude -p --output-format json --permission-prompts none --no-session-persistence --permission-mode plan\|acceptEdits [--model M] [--effort E]`, prompt on stdin |
| `deepseek` | `claude` | Claude Code 2.1.317 | api | Claude flags verified, endpoint unverified | same as `claude`, with `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic` and `ANTHROPIC_AUTH_TOKEN` from `DEEPSEEK_API_KEY`, only in that subprocess |
| `codex` | `codex` | codex-cli 0.166.0 | subscription | verified except effort | `codex exec --json --color never --ephemeral --sandbox read-only\|workspace-write [--model M] [-c model_reasoning_effort="E"] -`, prompt on stdin |
| `copilot` | `copilot` | GitHub Copilot CLI 1.0.105 | subscription | verified | `copilot --silent --no-color --no-ask-user --no-auto-update --allow-all-tools --deny-tool=... [--model M] [--reasoning-effort E] -p PROMPT` |
| `agy` | `agy` | not installable from npm | subscription | **unverified** | `agy --print-timeout Ns --mode plan\|accept-edits [--model M] -p PROMPT` inside a pseudo-terminal |

## Claude Code (`claude`)

Verified in `claude --help` (2.1.317):

- `-p, --print` runs non-interactively. The official headless guide says a prompt can be piped on
  stdin, that the process exits with 0 on success and non-zero on failure, and that SIGTERM ends it
  with code 143.
- `--output-format json` returns one object with `result`, `is_error`, `subtype`,
  `total_cost_usd` and `usage`. Timonel reads `total_cost_usd` as the real cost; the vendor calls it
  a client-side estimate.
- `--permission-mode` accepts `acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`,
  `plan`. Timonel uses `plan` for read-only tasks and `acceptEdits` for edits.
- `--permission-prompts none` denies anything that would prompt instead of waiting. It requires
  Claude Code 2.1.259 or later; older versions reject the flag, which `doctor --smoke` reveals.
- `--model` takes aliases (`fable`, `opus`, `sonnet`) or full names; `--effort` takes `low`,
  `medium`, `high`, `xhigh`, `max`.
- `claude auth status` prints JSON with `"loggedIn": true` when authenticated.
- `--dangerously-skip-permissions` exists and is only ever passed through `dangerous_args`.

## DeepSeek through Claude Code (`deepseek`)

DeepSeek's official guides document an Anthropic-compatible endpoint at
`https://api.deepseek.com/anthropic`, the variables `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN`
for Claude Code, and the model names `deepseek-flash` and `deepseek-v4-pro` (names starting with
`claude-opus` map to `deepseek-v4-pro`, other `claude*` names to `deepseek-flash`). Timonel removes
`ANTHROPIC_API_KEY` from that subprocess so it cannot take precedence. No DeepSeek key was available,
so the end-to-end call is unverified.

## Codex CLI (`codex`)

Verified in `codex exec --help` (0.166.0):

- `codex exec` reads the prompt from stdin when the prompt is `-`.
- `--json` prints JSONL events. The official non-interactive guide lists `thread.started`,
  `turn.started`, `turn.completed` (with `usage`), `turn.failed`, `item.*` and `error`; the final
  answer is an `item.completed` event whose item type is `agent_message`.
- `--sandbox read-only|workspace-write|danger-full-access`; the guide says `exec` defaults to
  read-only.
- `--model`, `--color never`, `--ephemeral`, `--dangerously-bypass-approvals-and-sandbox`.
- `codex login status` prints `Not logged in` and exits with 1 when there is no login.

Unverified: the `model_reasoning_effort` configuration key used for effort, and numeric exit codes,
which the guide does not document.

## GitHub Copilot CLI (`copilot`)

Verified in `copilot --help` (1.0.105):

- `-p, --prompt <text>` runs non-interactively; `--allow-all-tools` is described as required for
  non-interactive mode.
- `-s, --silent` prints only the agent response, `--no-ask-user`, `--no-color`, `--no-auto-update`.
- `--deny-tool` and the tool names `write` and `shell` come from the examples in the help. Timonel
  always denies `shell`, and also `write` for read-only tasks.
- `--model` (with `auto`) and `--reasoning-effort none|minimal|low|medium|high|xhigh|max`.
- `copilot help environment` documents `COPILOT_GITHUB_TOKEN`, `GH_TOKEN` and `GITHUB_TOKEN`.

There is no `login status` command, so Timonel reports authentication as `unknown` unless one of those
variables is set. Usage-limit messages are matched with generic patterns (`quota`, `premium requests`,
`AI credits`) that are unverified.

## Google Antigravity CLI (`agy`)

`agy` is not published on npm and could not be installed in the build environment. Its flags come
from the public issue tracker (`google-antigravity/antigravity-cli#318`) and third-party cheat
sheets: `-p`, `--model`, `--output-format text|json|stream-json`, `--print-timeout` (default 5m),
`--mode plan|accept-edits`, `--sandbox`, `--dangerously-skip-permissions` and `agy models`. All of
them are **unverified**.

Issue #318 reports that `agy -p` hangs when stdout is not a terminal. Timonel therefore runs `agy`
inside a pseudo-terminal: the `pty` module on macOS and Linux and `pywinpty` (ConPTY) on Windows. The
reporters say the hang also happened in a real terminal on some versions, so treat `--print-timeout`
as the last line of defence and run `timonel doctor --smoke`.

## Correcting a default without touching code

Put only what changes in your `routes.yaml`:

```yaml
agents:
  agy:
    base_args: ["--print-timeout", "{timeout_s}s", "--output-format", "text"]
    verified: true
  codex:
    effort_args: []
```

Then run `timonel validate` and `timonel doctor --smoke`.
