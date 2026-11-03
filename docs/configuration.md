# Configuration

Everything Timonel does can be changed. The full list of options, generated from the schema, is in
[reference/configuration.md](reference/configuration.md) ([es](reference/configuration.es.md),
[pt](reference/configuration.pt.md)). Ready-made setups are in [recipes.md](recipes.md).

## Where values come from

Later layers win:

1. built-in defaults (the schema and `default_routes.yaml`);
2. your user file: `~/.config/timonel/routes.yaml` (Linux, macOS) or `%APPDATA%\timonel\routes.yaml`
   (Windows); `TIMONEL_HOME` moves it;
3. the repository's `.timonel.yaml`, only after `timonel trust`;
4. environment variables `TIMONEL_<SECTION>__<KEY>`, for example `TIMONEL_UI__THEME=dark`; values are
   read as YAML, so `TIMONEL_REVIEW__ENABLED=false` is a boolean;
5. `--set key=value` on any command, for example `timonel run --set review.enabled=false "…"`.

Mappings merge key by key; lists and the whole `tiers:` mapping replace the lower layer.

```bash
timonel config explain review.enabled
```

shows the meaning, the default, the current value and which layer set it.

## Commands

| Command | What it does |
|---|---|
| `timonel config list [--area A] [--all]` | Every option with its value and the layer that set it (`--all` adds advanced ones in simple mode) |
| `timonel config get KEY` / `set KEY VALUE` / `unset KEY` | Read or change one option in your user file |
| `timonel config edit` | Open the file in `$EDITOR`, validate it on save |
| `timonel config validate [FILE]` | Check a file: every problem names the line and suggests close keys |
| `timonel config schema` | Print the JSON Schema (also published as `schema/timonel.schema.json`) |
| `timonel config export` / `import FILE` / `reset` | Copy, replace or empty your user file (a `.bak` is kept) |
| `timonel profiles list/create/copy/use/delete` | Named routing profiles |
| `timonel mode simple\|expert` | Hide or show advanced options in help and in the web UI |

Add `--project` to `set`, `unset`, `edit` to change the repository's `.timonel.yaml` instead.

Editors that understand JSON Schema (VS Code with the YAML extension, JetBrains IDEs) can autocomplete
the file with this first line:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/EduMayMar/Timonel/main/schema/timonel.schema.json
```

## Project files and trust

A `.timonel.yaml` in a repository can add hooks and verification commands, which run on your machine.
So it is never used until you approve it:

```bash
timonel trust
```

lists every command the file would add and asks for confirmation. Timonel stores the file's SHA-256 in
`trusted-projects.json` in your configuration directory (the hash of exactly what it showed you); if the file changes, it is ignored again (with
a warning) until you run `timonel trust` once more. `timonel trust --revoke` forgets it.

A project file may not set an agent's `provider`, `env_from`, `env_pass` or install metadata; those
belong in your user file.

`timonel config set`, `timonel mode` and the Settings page rewrite your user file as plain YAML, so
comments in it are not kept (a `.bak` copy of the previous file is). Use `timonel config edit` if you
want to keep comments.

## The web UI

`timonel ui` has a *Settings* page built from the same schema: search, area filter, advanced options in
expert mode, a badge on values you changed and on values set by another layer, a live YAML preview with
validation, import, export and reset per option.
