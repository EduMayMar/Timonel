# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
