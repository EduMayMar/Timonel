# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `timonel` with no arguments opens a home screen in the terminal: original wheel logo (color,
  monochrome, ASCII), profile, mode, folder and git state, agent states checked in the background
  with a time limit and cache, spend and budget, actionable notices, an input box with history,
  `/` commands with completion, plan-confirm-run with a live timeline and clean Ctrl+C.
  Without a terminal it prints the header and the command list and exits 0; `timonel home --once`
  prints only the header. New `tui.*` settings, also on the web Settings page.
- `--no-color` on every command; plain-text header for screen readers.
- Assisted `git init` with a first commit, a suggested `.gitignore` and a stop before committing files
  that look like secrets.

### Changed

- `timonel plan` works in folders that are not git repositories. `timonel run` explains why it needs
  one and offers to create it.
- New dependency: prompt_toolkit (pure Python), see docs/terminal.md.

### Added

- `timonel start`: guided, resumable installer for Git, GitHub CLI, Node.js, pipx and agent CLIs with
  official package managers, `--dry-run`, `--yes --allow`, an audit log with undo commands and no
  automatic privilege elevation. Logins run the vendor's own command; API keys go to the keyring
  (`timonel keys`).
- `timonel demo` (simulated agents), `timonel learn`, `--explain` on every command, stable error codes
  `TMN-xxxx` with fixes (`docs/errors.md`), `timonel doctor --fix`, simple and expert modes.
- English, Spanish and Portuguese catalogs shared by the CLI and the web UI, with completeness tests.
- Connector registry (`src/timonel/agents/*.yaml`): 16 agents and 11 HTTP model providers, each with
  its verification status, version and date; `timonel agents`; custom connector wizard; nightly
  flag-drift workflow.
- JSON Schema as the single source of truth for configuration, with layers (defaults, user, trusted
  project, environment, `--set`), `timonel config`, `timonel profiles`, `timonel trust`, published
  schema and generated reference in three languages.
- New options: routing rules and deny lists, budgets, escalation policies, verification autodetect,
  hooks, notifications, privacy and retention (`timonel purge`), UI preferences.
- Web UI: guided tour, setup checklist, settings page generated from the schema, Portuguese,
  configurable shortcuts and density, automated accessibility checks.
- Release binaries for Windows, macOS and Linux with `SHA256SUMS`, container image, winget, Scoop and
  Homebrew templates, one-line installers that show their plan first, documentation site.

### Changed

- Configuration keys moved into areas (for example `execution.verify` is now
  `verification.commands`); old keys are migrated automatically.
- A repository's `.timonel.yaml` is ignored until `timonel trust`, and again whenever it changes.

### Security

- Provider API key variables are hidden from CLI agents unless the agent lists them in `env_pass`.
- `timonel trust` shows the whole file and stores the hash of what it showed; project files cannot set
  agent providers, secret mappings or install metadata; the loaded file is the one that was hashed.
- The installer only runs plain package-manager commands by itself, strips control characters from
  script previews and says when a preview is truncated; declined scripts are deleted.
- `yolo` approval modes are treated as dangerous wherever they appear.

### Fixed

- Skipped installer steps are no longer remembered as done.
- The Settings page validates every layer before writing and never leaves a half-written file.
- Cross review now reviews the fixed change again instead of accepting it unseen.
- `timonel start` adds new agents to your own or the default ladders instead of copying merged ones.
- In Spanish and Portuguese a bare `s` (skip) is no longer read as "yes".
- The Windows PATH refresh keeps your current PATH order first.
- The web UI no longer lets a slow earlier page overwrite the page you navigated to.
- `timonel demo --path` can run again on the same folder.

## [0.1.0] - 2026-11-03

### Added

- Declarative adapters in `routes.yaml` for Claude Code, Codex CLI, GitHub Copilot CLI,
  Google Antigravity CLI (through a pseudo-terminal) and DeepSeek through Claude Code.
- Schema validation of `routes.yaml` with file, line and field in every error.
- `timonel doctor` (with `--smoke`), `models`, `plan`, `run`, `status`, `diff`, `apply`, `discard`,
  `resume`, `stats`, `init`, `validate`, `ui` and `mcp`.
- Model classifier with JSON schema validation and deterministic heuristic fallback.
- Tiered ladders with subscription-before-API ordering, profiles `cheap`, `balanced` and `max`.
- Isolated git worktrees, verification commands, escalation with error context, cross review at
  tier 3 and above, usage-limit cooldowns, total and idle timeouts, process-tree cancellation and
  resumable runs.
- Adaptive ordering from the ledger with explanations, race mode, task decomposition and a savings
  metric.
- SQLite ledger of every run and attempt.
- Local web UI with token, Host, Origin and CSRF checks, live SSE timeline, diff viewer and a
  drag-and-drop routes editor, in English and Spanish.
- MCP server over stdio exposing plan, run and status.
