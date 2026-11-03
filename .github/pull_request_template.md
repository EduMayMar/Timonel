## What changes

## Why

## How it was verified

- [ ] `uv run ruff format --check src tests && uv run ruff check src tests`
- [ ] `uv run mypy`
- [ ] `uv run pytest -q`
- [ ] Docs updated (README, README.es.md, docs/) if behaviour changed
- [ ] No comments or docstrings added to `src/` (see CONTRIBUTING)

## Adding or changing a connector (skip otherwise)

- [ ] One file in `src/timonel/agents/`, following docs/adding-an-agent.md
- [ ] `verification` block filled in: status, version, date and source
- [ ] Every flag appears in the help output pasted below (or the connector is marked `unverified`)
- [ ] New agents start with `enabled: false`; no keys or tokens in the file
- [ ] `python scripts/flag_drift.py` and `python scripts/gen_docs.py` run

<details><summary>Help output used</summary>

```text
paste `<command> --help` here
```

</details>
