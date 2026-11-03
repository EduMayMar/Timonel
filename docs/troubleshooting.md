# Troubleshooting

Start with:

```bash
timonel doctor --fix
```

It checks Git, every enabled agent (installed, version, logged in) and your configuration, and offers
the fix for each problem. Every error message has a code; [errors.md](errors.md) lists them all.

## "timonel: command not found" after installing

Open a new terminal. If it still fails, run `pipx ensurepath` (or `uv tool update-shell`) and open a
new terminal again.

## An agent is installed but shows "not logged in"

Run the agent's own login (shown by `timonel doctor` and on the UI's *Setup* page), for example
`codex login` or `gh auth login`, then `timonel doctor` again.

## A task keeps escalating to the most expensive agent

`timonel plan "<task>"` shows the classification and why each tier was chosen. Use
`--profile cheap`, lower `max_escalations`, or set a budget (see [recipes](recipes.md)).

## The project's `.timonel.yaml` is ignored

That is on purpose until you trust it: run `timonel trust` in the repository. If the file changed,
trust it again.

## The web UI says "Open the URL printed by timonel ui"

The address contains a one-time token. Use the exact link printed in the terminal; reopening an old
link does not work.

## Behind a proxy or offline

Timonel honours `HTTPS_PROXY`/`HTTP_PROXY`. `timonel start` checks whether package registries are
reachable and tells you which host it could not reach instead of failing halfway.

## Reporting a problem

Run the failing command again with `--set privacy.log_level=debug`, check the output contains no
secrets, and open an [issue](https://github.com/EduMayMar/Timonel/issues/new/choose).
