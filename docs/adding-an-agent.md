# Adding an agent or a provider

A connector is data, not code: one YAML file. Timonel has two kinds.

- **agent**: a coding CLI that edits files in a worktree (Claude Code, Codex, Gemini CLI…).
- **provider**: an HTTP model API (OpenAI-compatible or Anthropic) that only answers text. Providers can
  classify, plan and review, but never edit files, so they cannot sit on a tier ladder.

## The quick way (just for you)

```bash
timonel agents add
```

The wizard asks for the command, how the prompt is passed and the flags, then writes
`<config dir>/agents/<name>.yaml`. Check it with:

```bash
timonel agents info <name>
timonel agents test <name>
```

`agents test` runs one tiny read-only prompt and shows the exit code, whether the output parsed and the
first line of the reply.

## 1. Read the real help

Install the CLI and read `--help` and its official docs. Write down:

- how to run one prompt and exit, and whether the prompt can come from stdin or a file;
- how to choose the model and the reasoning effort;
- how to restrict it to read-only and how to allow edits without prompting;
- the output format (plain text, one JSON object, or JSON lines) and where cost is reported;
- how to check the login and the version;
- what it prints when a usage limit is hit.

Never copy a flag from a blog post or a cheat sheet. If you could not confirm a flag in `--help` or the
official docs, set `verification.status: unverified` and say what is missing in `source`.

## 2. Write the file

Agent (`src/timonel/agents/mycli.yaml`):

```yaml
name: mycli
kind: agent
title: My CLI
vendor: Example Inc.
homepage: https://example.com/docs/cli
verification: {status: verified, version: 1.4.0, date: "2026-11-03", source: "mycli --help; mycli run --help"}
install:
  approx_size_mb: 120
  npm: ["npm", "install", "-g", "mycli"]
  uninstall: {npm: ["npm", "uninstall", "-g", "mycli"]}
  drift: {npm: "mycli", help: [["run", "--help"]]}
login: {command: ["mycli", "login"], flow: browser}
agent:
  enabled: false
  command: mycli
  billing: subscription
  parser: text
  prompt_via: stdin
  base_args: ["run", "--no-color"]
  read_args: ["--read-only"]
  write_args: ["--allow-edits"]
  model_args: ["--model", "{model}"]
  effort_args: []
  prompt_args: []
  dangerous_args: ["--yolo"]
  known_models: [small, large]
  rate_limit_patterns: ["out of credits"]
  detect: {version: ["--version"], auth: ["whoami"]}
```

Provider (`src/timonel/agents/myapi.yaml`):

```yaml
name: myapi
kind: provider
title: My API
vendor: Example Inc.
homepage: https://example.com/docs/api
verification: {status: docs, date: "2026-11-03", source: "API reference, chat completions page"}
api_key: {env: MYAPI_API_KEY, url: https://example.com/keys}
provider: {api: openai, base_url: "https://api.example.com/v1", key_env: MYAPI_API_KEY}
agent: {command: myapi, billing: api, parser: text}
```

Rules that reviewers check:

- `verification.status` is `verified` (you ran `--help`), `docs` (official docs only) or `unverified`.
- New agents start with `enabled: false`; users opt in with `timonel agents enable <name>`.
- `prompt_via`: prefer `stdin`; use `file` with `{prompt_file}` when the CLI reads a file; use `arg` only
  when nothing else exists (long prompts end up in the process list).
- If the CLI has no read-only mode, add `supports_read_only: false`; Timonel then never uses it for
  read-only work.
- Secrets go through `env_from` or `api_key.env`, read from the environment or the system keyring. Never
  put a key in the file.
- `install.drift` lists the package and the help commands for the nightly flag check.
- `approx_size_mb` is measured (install it in a clean folder and look at the size), or left out.

## 3. Add a test with a fake CLI

Tests never call the real CLI. `tests/fakes/fake_agent.py` imitates one: add your command name to the
fakes in `tests/conftest.py` if it needs a new output format, then add a test that builds the argv and
parses a sample reply, like the ones in `tests/test_adapters.py`. `tests/test_layers.py` already checks
that every registry file loads and matches the schema.

## 4. Check and open the pull request

```bash
uv run ruff format --check src tests && uv run ruff check src tests
uv run mypy
uv run pytest -q
python scripts/flag_drift.py
python scripts/gen_docs.py
```

`flag_drift.py` compares every flag in the registry with the CLI's `--help` (it skips CLIs you do not
have installed). `gen_docs.py` refreshes `docs/agents.md`. Fill in the connector section of the pull
request template, including the exact help output you used.

Every night the `flag-drift` workflow installs the public CLIs, runs the same check and opens an issue
if a flag disappeared.
