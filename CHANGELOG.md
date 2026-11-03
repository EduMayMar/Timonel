# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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

### Fixed

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
