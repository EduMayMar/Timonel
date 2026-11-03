# Contributing

Thanks for helping. This guide is short on purpose.

## Setup

```bash
git clone https://github.com/EduMayMar/Timonel
cd Timonel
uv sync --group dev
```

Run everything that CI runs:

```bash
uv run ruff format --check src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
```

The browser tests need Chromium: `uv run playwright install chromium`.

## House rules

- **No comments and no docstrings in `src/`.** Names explain intent, small functions explain
  structure, and tests document behaviour. If something needs a comment, rename or split it.
- Strict typing (`mypy --strict`) and `ruff` must stay green.
- Standard library first. A new runtime dependency needs a very good reason in the PR.
- Never use `shell=True`. Build argument lists.
- Never write secrets to disk or logs. Pass them through environment variables or the keyring.
- Every behaviour change comes with a test. Agent behaviour is tested against the fake CLIs in
  `tests/fakes/fake_agent.py`, never against real services.
- Do not claim performance or comparisons you have not measured.

## Adding or fixing an agent

Most fixes are a change in `src/timonel/default_routes.yaml`. Read
[docs/adding-an-agent.md](docs/adding-an-agent.md). If you could only verify a flag from third-party
sources, leave `verified: false`.

## Commits and pull requests

One logical change per commit, with a message that says why. Fill in the pull request template.
