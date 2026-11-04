# Configuration reference

Generated from `src/timonel/schema/config.schema.yaml` by `scripts/gen_docs.py`; do not edit by hand. Values are merged in this order: built-in defaults, your user file, a trusted project `.timonel.yaml`, `TIMONEL_SECTION__KEY` environment variables and `--set key=value`. `timonel config explain <key>` shows where a value comes from.

## General

| Key | Type | Default | Meaning |
|---|---|---|---|
| `version` | `1` | `1` | Schema version. |
| `mode` | `simple` \| `expert` | `simple` | Simple mode hides advanced options in the CLI help and the web UI; expert shows everything. |
| `language` | `auto` \| `en` \| `es` \| `pt` | `auto` | Language of messages. auto follows TIMONEL_LANG and then the system locale. |

## Routing

| Key | Type | Default | Meaning |
|---|---|---|---|
| `default_profile` | string | `balanced` | Profile used when a command does not pass --profile. |
| `tiers` | object | — | Ordered ladder of rungs for each tier from 1 (trivial) to 4 (architecture). |
| `profiles` | object | — | Named routing profiles. |
| `routing.prefer_subscription` | boolean | `true` | Within a tier, try subscription agents before pay-per-use APIs. |
| `routing.deny_agents` | list of string | `[]` | Agents never used. |
| `routing.deny_models` | list of string | `[]` | Models never used, as model or agent:model. |
| `routing.rules` | list of object | `[]` | Ordered rules; every matching rule is applied to the plan. *(advanced)* |
| `planner.ladder` | list of object | — | Agents or providers tried in order to split tasks. *(advanced)* |
| `planner.prompt_template` | string | — | Prompt for the planner. Must contain {task}, {marker} and {max_subtasks}. *(advanced)* |
| `adaptive.enabled` | boolean | `true` | Use the ledger to reorder rungs. |
| `adaptive.min_samples` | integer | `5` | Attempts needed before a route counts. |
| `decompose.auto` | boolean | `false` | Decompose without --decompose at min_complexity. *(advanced)* |
| `decompose.min_complexity` | integer | `4` | Complexity that triggers automatic decomposition. *(advanced)* |
| `decompose.max_subtasks` | integer | `6` | Most subtasks accepted from the planner. *(advanced)* |
| `decompose.max_parallel` | integer | `3` | Subtasks running at the same time. *(advanced)* |

## Agents

| Key | Type | Default | Meaning |
|---|---|---|---|
| `agents` | object | `{}` | Agent and provider connectors, merged over the built-in registry (agents/*.yaml). |
| `rate_limit_patterns` | list of string | — | Regular expressions that mark a failed attempt as a usage limit, for every agent. *(advanced)* |

## Classifier

| Key | Type | Default | Meaning |
|---|---|---|---|
| `classifier.use_model` | boolean | `true` | Ask a model to classify. When false, only the heuristics are used. |
| `classifier.ladder` | list of object | — | Agents or providers tried in order to classify. |
| `classifier.retries` | integer | `0` | Extra attempts per rung when the answer does not match the schema. *(advanced)* |
| `classifier.on_failure` | `heuristic` \| `fail` | `heuristic` | What to do when no model gives a valid classification. |
| `classifier.prompt_template` | string | — | Prompt sent to the classifier. Must contain {task} and {marker}. *(advanced)* |
| `classifier.heuristics.base_complexity.explain` | integer | `1` | Starting complexity for 'explain' tasks. *(advanced)* |
| `classifier.heuristics.base_complexity.review` | integer | `2` | Starting complexity for 'review' tasks. *(advanced)* |
| `classifier.heuristics.base_complexity.edit` | integer | `2` | Starting complexity for 'edit' tasks. *(advanced)* |
| `classifier.heuristics.base_complexity.debug` | integer | `2` | Starting complexity for 'debug' tasks. *(advanced)* |
| `classifier.heuristics.base_complexity.refactor` | integer | `3` | Starting complexity for 'refactor' tasks. *(advanced)* |
| `classifier.heuristics.base_complexity.architecture` | integer | `4` | Starting complexity for 'architecture' tasks. *(advanced)* |
| `classifier.heuristics.long_task_chars` | integer | `400` | Descriptions longer than this count as long. *(advanced)* |
| `classifier.heuristics.long_task_bonus` | integer | `1` | Complexity added for long descriptions. *(advanced)* |
| `classifier.heuristics.many_files` | integer | `3` | Number of mentioned files that counts as many. *(advanced)* |
| `classifier.heuristics.many_files_bonus` | integer | `1` | Complexity added when many files are mentioned. *(advanced)* |
| `classifier.heuristics.broad_bonus` | integer | `1` | Complexity added for broad-scope words. *(advanced)* |
| `classifier.heuristics.trivial_penalty` | integer | `1` | Complexity removed for trivial-change words. *(advanced)* |
| `classifier.heuristics.trivial_max_chars` | integer | `200` | Trivial words only count in descriptions shorter than this. *(advanced)* |
| `classifier.heuristics.high_risk_raises_tier` | boolean | `true` | High-risk tasks below tier 3 start one tier higher. *(advanced)* |
| `classifier.heuristics.keywords.review` | list of string | — | Words that mark a 'review' task. *(advanced)* |
| `classifier.heuristics.keywords.architecture` | list of string | — | Words that mark a 'architecture' task. *(advanced)* |
| `classifier.heuristics.keywords.debug` | list of string | — | Words that mark a 'debug' task. *(advanced)* |
| `classifier.heuristics.keywords.refactor` | list of string | — | Words that mark a 'refactor' task. *(advanced)* |
| `classifier.heuristics.keywords.explain` | list of string | — | Words that mark a 'explain' task. *(advanced)* |
| `classifier.heuristics.broad_words` | list of string | — | Words that mark a broad scope. *(advanced)* |
| `classifier.heuristics.trivial_words` | list of string | — | Words that mark a trivial change. *(advanced)* |
| `classifier.heuristics.chat_words` | list of string | — | Words of a plain greeting or thanks; a message made only of these is answered read-only instead of editing files. *(advanced)* |
| `classifier.heuristics.risky_words` | list of string | — | Words that mark a high-risk task. *(advanced)* |

## Cross review

| Key | Type | Default | Meaning |
|---|---|---|---|
| `review.enabled` | boolean | `true` | Review changes at or above min_tier. |
| `review.min_tier` | integer | `3` | Lowest tier whose changes are reviewed. |
| `review.reviewers` | list of object | — | Reviewers in order; empty uses every tier from the top. The executor's agent is skipped. |
| `review.max_reviewers_tried` | integer | `3` | How many reviewers are tried before giving up. *(advanced)* |
| `review.strictness` | `lenient` \| `normal` \| `strict` | `normal` | How strict the reviewer is asked to be. |
| `review.max_rounds` | integer | `1` | Fix rounds after a rejection. |
| `review.on_no_verdict` | `approve` \| `reject` | `approve` | What to do when no reviewer gives a valid verdict. |
| `review.diff_chars` | integer | `60000` | Maximum diff characters sent to the reviewer. *(advanced)* |
| `review.prompt_template` | string | — | Prompt for reviewers. Must contain {marker}, {task}, {diff} and {strictness}. *(advanced)* |

## Escalation

| Key | Type | Default | Meaning |
|---|---|---|---|
| `escalation.max_escalations` | integer | `2` | Extra tiers tried after the starting one. |
| `escalation.on_verify_failure` | `next_tier` \| `next_rung` | `next_tier` | Where to go when verification fails. |
| `escalation.on_agent_failure` | `next_rung` \| `next_tier` | `next_rung` | Where to go when an agent crashes, times out or prints garbage. |
| `escalation.context_chars` | integer | `4000` | Characters of the previous error passed to the next attempt. |
| `escalation.context_attempts` | integer | `2` | How many previous failures are included as context. *(advanced)* |
| `escalation.cooldown_minutes` | integer | `30` | Rest time for an agent after a usage limit. |
| `escalation.answer_without_changes_max_complexity` | integer | `2` | Up to this complexity, an agent that answers without changing any file counts as done instead of escalating (0 always escalates). |
| `escalation.reset_between_attempts` | boolean | `true` | Reset the worktree to the base commit before each new attempt. *(advanced)* |

## Conversation context

| Key | Type | Default | Meaning |
|---|---|---|---|
| `context.scope` | `off` \| `session` \| `repo` | `session` | off starts every task fresh, session shares context within one home-screen session (or with --continue), repo shares it across all tasks of the repository. |
| `context.max_runs` | integer | `5` | Maximum number of earlier tasks passed to the agent. |
| `context.stack` | boolean | `true` | Start a follow-up from the branch of the previous task when its changes are not merged yet, so the agent sees them. |
| `context.statuses` | list of object | `["succeeded", "applied", "failed"]` | Which earlier tasks count as context, by final status. *(advanced)* |
| `context.include` | list of object | `["task", "outcome", "changes", "summary"]` | What to tell the agent about each earlier task. *(advanced)* |
| `context.max_chars` | integer | `6000` | Maximum size of the whole context block; the oldest tasks are dropped first. *(advanced)* |
| `context.summary_chars` | integer | `800` | Maximum size of the result text kept for each earlier task. *(advanced)* |

## Verification

| Key | Type | Default | Meaning |
|---|---|---|---|
| `verification.commands` | list of object | `[]` | Commands that must succeed, as argument lists. |
| `verification.autodetect` | boolean | `false` | When no commands are set, detect the test command from project files. |
| `verification.detectors` | list of object | — | File markers and the command they imply, checked in order. *(advanced)* |
| `verification.timeout_s` | integer | `900` | Time limit for each command. |
| `verification.success_exit_codes` | list of integer | `[0]` | Exit codes that count as success. *(advanced)* |
| `verification.env_allow` | list of string | `[]` | Extra environment variables passed to verification commands. *(advanced)* |

## Execution

| Key | Type | Default | Meaning |
|---|---|---|---|
| `execution.total_timeout_s` | integer | `1800` | Wall-clock limit per attempt. |
| `execution.idle_timeout_s` | integer | `300` | Limit without any output. |
| `execution.orphan_grace_s` | number | `2.0` | Seconds to wait for output after the agent exits before stopping leftover processes. *(advanced)* |
| `execution.worktree_dir` | string | `` | Folder for worktrees; empty uses the data directory. *(advanced)* |
| `execution.branch_pattern` | string | `timonel/{id}` | Name of run branches; must contain {id}. |
| `execution.base_ref` | string | `HEAD` | Commit or branch new worktrees start from. |
| `execution.cleanup_on_apply` | boolean | `true` | Remove the worktree and branch after apply. |
| `execution.write_kinds` | list of object | `["edit", "debug", "refactor", "architecture"]` | Task kinds that get write access in a worktree; others run read-only. |
| `execution.agent_env_deny` | list of string | `[]` | Environment variable name patterns never passed to agents. *(advanced)* |
| `execution.max_output_chars` | integer | `4000000` | Output kept in memory per attempt (the tail is kept). *(advanced)* |
| `execution.stored_output_chars` | integer | `200000` | Output stored in the ledger per attempt. *(advanced)* |
| `execution.max_parallel_runs` | integer | `2` | Runs started from the web UI or MCP at the same time. |
| `execution.race_contenders` | integer | `2` | Agents that compete in --race. |

## Budgets

| Key | Type | Default | Meaning |
|---|---|---|---|
| `budgets` | list of object | `[]` | Spending limits per agent and period, and what to do when one is reached. |

## Logs and privacy

| Key | Type | Default | Meaning |
|---|---|---|---|
| `privacy.log_level` | `debug` \| `info` \| `warning` \| `error` | `warning` | Level of the local log file. |
| `privacy.log_file` | string | `` | Log file path; empty disables file logging. |
| `privacy.redact_patterns` | list of string | `[]` | Extra regular expressions masked in logs, the ledger and the UI. |
| `privacy.secret_env_names` | string | `(KEY\|TOKEN\|SECRET\|PASSWORD\|PASSWD\|CREDENTIAL\|AUTH)` | Environment variable names whose values are always masked. *(advanced)* |
| `privacy.store_outputs` | boolean | `true` | Keep agent outputs in the ledger. |
| `privacy.retention_days` | integer | `0` | Delete runs older than this many days on purge; 0 keeps everything. |
| `privacy.update_check` | boolean | `false` | Let doctor ask PyPI for a newer version. Off by default; nothing else uses the network. |

## Interface

| Key | Type | Default | Meaning |
|---|---|---|---|
| `ui.host` | `127.0.0.1` \| `localhost` \| `::1` | `127.0.0.1` | Loopback address to listen on. |
| `ui.port` | integer | `0` | Port; 0 picks a free one. |
| `ui.open_browser` | boolean | `true` | Open the browser on start. |
| `ui.theme` | `auto` \| `light` \| `dark` | `auto` | Color theme. |
| `ui.density` | `comfortable` \| `compact` | `comfortable` | Spacing of the interface. |
| `ui.tour` | boolean | `true` | Show the guided tour on first visit. |
| `ui.shortcuts.panel` | string | `p` | Key that opens the panel. *(advanced)* |
| `ui.shortcuts.new` | string | `n` | Key that opens new task. *(advanced)* |
| `ui.shortcuts.history` | string | `h` | Key that opens history. *(advanced)* |
| `ui.shortcuts.stats` | string | `s` | Key that opens statistics. *(advanced)* |
| `ui.shortcuts.routes` | string | `r` | Key that opens routes. *(advanced)* |
| `ui.shortcuts.setup` | string | `i` | Key that opens setup. *(advanced)* |
| `ui.shortcuts.settings` | string | `c` | Key that opens settings. *(advanced)* |
| `ui.shortcuts.theme` | string | `t` | Key that opens theme switch. *(advanced)* |
| `ui.shortcuts.language` | string | `l` | Key that opens language switch. *(advanced)* |

## Terminal home screen

| Key | Type | Default | Meaning |
|---|---|---|---|
| `tui.enabled` | boolean | `true` | Open the interactive home screen when timonel runs with no arguments in a terminal. |
| `tui.logo` | `auto` \| `color` \| `mono` \| `ascii` \| `none` | `auto` | Logo style: auto picks color, monochrome or ASCII from the terminal. |
| `tui.compact` | boolean | `false` | One-line header instead of the logo. |
| `tui.plain` | boolean | `false` | Plain text without boxes or symbols, for screen readers. |
| `tui.theme` | `auto` \| `dark` \| `light` | `auto` | Colors for dark or light terminals. |
| `tui.show_spend` | boolean | `true` | Show today's spend and remaining budget. |
| `tui.check_on_start` | boolean | `true` | Check agents in the background when the home screen opens. |
| `tui.check_timeout_s` | integer | `20` | Seconds to wait for each agent check before showing 'no answer'. |
| `tui.status_cache_s` | integer | `300` | Seconds an agent check is reused before checking again; 0 disables the cache. |
| `tui.refresh_s` | number | `1.0` | How often the status bar is redrawn, in seconds. *(advanced)* |
| `tui.notices` | list of string | `["first_run", "untrusted", "login", "outdated", "not_git"]` | Which actionable notices to show. |
| `tui.placeholder` | string | `` | Hint inside the input box; empty uses the translated default. |
| `tui.history` | boolean | `true` | Remember what you type between sessions (lines that look like secrets are never saved). |
| `tui.history_size` | integer | `1000` | Maximum number of remembered lines. *(advanced)* |
| `tui.confirm_run` | boolean | `true` | Ask for confirmation after showing the plan of a task. |
| `tui.classify_with_model` | boolean | `true` | Classify each task with the cheap classifier model before planning, instead of keyword rules only. |
| `tui.clear_screen` | boolean | `true` | Clear the terminal when the home screen opens, like a fresh application. |
| `tui.keys.profile` | string | `s-tab` | Cycle the profile (cheap, balanced, max). *(advanced)* |
| `tui.keys.mode` | string | `f2` | Switch between simple and expert mode for this session. *(advanced)* |
| `tui.keys.help` | string | `f1` | Show the help. *(advanced)* |
| `tui.keys.exit` | string | `c-d` | Leave the home screen. *(advanced)* |

## Notifications

| Key | Type | Default | Meaning |
|---|---|---|---|
| `notifications.events` | list of object | `["succeeded", "failed"]` | Which outcomes notify. |
| `notifications.desktop` | boolean | `false` | Desktop notification. |
| `notifications.sound` | boolean | `false` | Terminal bell. |
| `notifications.webhook_url` | string | `` | HTTPS URL that receives a JSON POST (no task text or output, only id, status and diff size). |

## Hooks

| Key | Type | Default | Meaning |
|---|---|---|---|
| `hooks.pre_run` | list of object | `[]` | Commands run before each run; a failure stops the run. *(advanced)* |
| `hooks.post_run` | list of object | `[]` | Commands run after a successful run. *(advanced)* |
| `hooks.on_failure` | list of object | `[]` | Commands run after a failed run. *(advanced)* |
| `hooks.timeout_s` | integer | `120` | Time limit for each hook. *(advanced)* |

## Paths

| Key | Type | Default | Meaning |
|---|---|---|---|
| `paths.data_dir` | string | `` | Data directory; empty uses the platform default. *(advanced)* |
| `paths.ledger` | string | `` | Ledger file; empty uses data_dir/ledger.sqlite. *(advanced)* |
| `paths.cache_dir` | string | `` | Cache directory for downloaded installer scripts; empty uses data_dir/cache. *(advanced)* |
