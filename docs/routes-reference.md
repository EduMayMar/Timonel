# `routes.yaml` reference

Timonel loads the built-in [`default_routes.yaml`](https://github.com/EduMayMar/Timonel/blob/main/src/timonel/default_routes.yaml) and merges your
file on top of it:

- mappings merge key by key (you can change one field of one agent);
- lists and the whole `tiers:` mapping replace the default.

Your file lives in the user configuration directory, never in the repository being worked on:

| OS | Path |
|---|---|
| Linux, macOS | `$XDG_CONFIG_HOME/timonel/routes.yaml` or `~/.config/timonel/routes.yaml` |
| Windows | `%APPDATA%\timonel\routes.yaml` |

`TIMONEL_HOME` overrides both the config and the data directory. `--config PATH` uses another file.
`timonel init` writes a starting file; `timonel validate [PATH]` checks one.

Every validation error names the file, the line and the field path:

```text
/home/me/.config/timonel/routes.yaml:3: agents.claude.billing: expected one of subscription, api, got 'free'
```

## Top level

| Key | Type | Default | Meaning |
|---|---|---|---|
| `version` | int | `1` | Schema version. Only `1` exists. |
| `agents` | map | five agents | Adapter definitions, see below. |
| `rate_limit_patterns` | list of regex | see defaults | Usage-limit patterns shared by every agent (case-insensitive). |
| `tiers` | map 1–4 → ladder | see defaults | Ordered rungs per tier. |
| `classifier` | ladder | deepseek, claude, copilot | Who classifies tasks. Empty list means heuristics only. |
| `planner` | ladder | claude, codex | Who splits tasks for `--decompose`. |
| `reviewer` | ladder | all tiers, top first | Who reviews diffs. The executor's own agent is always skipped. |
| `profiles` | map | `cheap`, `balanced`, `max` | Policy profiles. |
| `execution` | map | see below | Escalation, timeouts and verification. |
| `adaptive` | map | enabled, 5 samples | Ledger-driven reordering. |
| `decompose` | map | off | Task decomposition. |

A **ladder** is a list of rungs: `{agent: NAME, model: STRING, effort: STRING}`. Empty `model` or
`effort` means "let the CLI decide" and the corresponding arguments are omitted.

## `agents.<name>`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `command` | string | required | Executable looked up on `PATH` (`.cmd` shims on Windows are handled). |
| `billing` | `subscription` \| `api` | `api` | Subscriptions are tried first within a tier. |
| `verified` | bool | `false` | Shown in `doctor` and the UI. Informational. |
| `parser` | `text` \| `json` \| `stream-json` \| `jsonl` | `text` | How to read the output. |
| `prompt_via` | `arg` \| `stdin` | `arg` | `stdin` writes the prompt to standard input. |
| `pty` | bool | `false` | Run inside a pseudo-terminal (needs `prompt_via: arg`). |
| `base_args` | template | `[]` | Always passed. |
| `read_args` | template | `[]` | Passed for read-only tasks. |
| `write_args` | template | `[]` | Passed for edit tasks. |
| `model_args` | template | `[]` | Passed when the rung has a model. |
| `effort_args` | template | `[]` | Passed when the rung has an effort. |
| `prompt_args` | template | `[]` | Passed last. Must contain `{prompt}` when `prompt_via: arg`, and must not when `stdin`. |
| `dangerous_args` | list | `[]` | Permission-bypass flags. Only used with `allow_dangerous: true` **and** `timonel run --dangerous` confirmed interactively. |
| `allow_dangerous` | bool | `false` | Opt-in for `dangerous_args`. |
| `env` | map | `{}` | Extra environment variables for this agent's process only. |
| `env_from` | map | `{}` | `TARGET: SOURCE`: copy the secret `SOURCE` (environment variable, or keyring service `timonel`) into `TARGET` for this agent only. Every `SOURCE` is removed from all other agents' environments. |
| `env_remove` | list | `[]` | Variables removed from this agent's environment. |
| `models_command` | list | `[]` | Arguments that list models (`agy models`). |
| `known_models` | list | `[]` | Static model list when there is no command. |
| `rate_limit_patterns` | list of regex | `[]` | Agent-specific usage-limit patterns. |
| `cost_per_attempt_usd` | number | none | Estimate used when the CLI does not report a cost. |
| `detect.version` | list | `["--version"]` | Arguments that print the version. |
| `detect.auth` | list | `[]` | Arguments that check the login. Exit code 0 means logged in unless `auth_ok_pattern` is set. |
| `detect.auth_ok_pattern` | regex | `""` | Logged in when the output matches. |
| `detect.auth_env` | list | `[]` | Logged in when any of these variables is set. |

### Templates

Templates are lists of strings. Each token may contain these placeholders, replaced once and never
re-scanned, so a prompt that contains `{model}` stays literal:

| Placeholder | Value |
|---|---|
| `{prompt}` | The full prompt. |
| `{model}` | The rung's model. |
| `{effort}` | The rung's effort. |
| `{timeout_s}` | The total timeout in seconds. |

Any other placeholder is a validation error. Tokens that bypass safety (`--dangerously…`, `--yolo`,
`--allow-all`, `--allow-all-paths`, `--allow-all-urls`, `danger-full-access`, `bypassPermissions`) are
rejected everywhere except in `dangerous_args`.

The final command is:

```text
command + base_args + (write_args | read_args) + [model_args] + [effort_args] + [dangerous_args] + prompt_args
```

Processes are always started with an argument list, never through a shell.

## `profiles.<name>`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `start_offset` | int −3…3 | `0` | Added to the complexity to choose the starting tier. |
| `min_tier` | int 1–4 | `1` | Lowest starting tier. |
| `max_tier` | int 1–4 | `4` | Highest tier ever used. |
| `skip_tiers` | list | `[]` | Tiers never used. |
| `max_escalations` | int 0–10 | `execution.max_escalations` | Overrides the global limit. |

## `execution`

| Field | Default | Meaning |
|---|---|---|
| `max_escalations` | `2` | Extra tiers after the starting one. |
| `review_min_tier` | `3` | Cross review from this tier. Use `5` to disable. |
| `cooldown_minutes` | `30` | How long an agent rests after a usage limit. |
| `total_timeout_s` | `1800` | Wall-clock limit per attempt. |
| `idle_timeout_s` | `300` | Limit without any output. |
| `verify` | `[]` | Commands as argument lists, e.g. `[["python", "-m", "pytest", "-q"]]`. |
| `verify_timeout_s` | `900` | Limit per verification command. |
| `verify_env` | `[]` | Extra environment variable names passed to verification commands. By default they only get `PATH`, home, temp, locale, toolchain locations and similar non-secret variables. |

## `adaptive`

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Reorder rungs by observed success. |
| `min_samples` | `5` | Attempts needed before a route's statistics count. |

## `decompose`

| Field | Default | Meaning |
|---|---|---|
| `auto` | `false` | Decompose complexity-4 edit tasks without `--decompose`. |
| `max_subtasks` | `6` | Upper bound accepted from the planner. |
| `max_parallel` | `3` | Subtasks running at the same time. |

## Example

```yaml
version: 1
agents:
  claude:
    cost_per_attempt_usd: 0.05
  codex:
    billing: api
tiers:
  1:
    - {agent: copilot, model: auto, effort: low}
  2:
    - {agent: codex, model: "", effort: medium}
  3:
    - {agent: claude, model: opus, effort: high}
  4:
    - {agent: claude, model: opus, effort: max}
execution:
  verify:
    - ["python", "-m", "pytest", "-q"]
    - ["python", "-m", "ruff", "check", "."]
profiles:
  night: {start_offset: 1, max_escalations: 3}
```
