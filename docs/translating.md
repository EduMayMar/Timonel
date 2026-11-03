# Translating Timonel

Every message lives in one JSON file per language in `src/timonel/locales/`: `en.json` (the source),
`es.json`, `pt.json`. The terminal and the web UI read the same files; keys that start with `ui.` are the
web page's.

## Fix or improve a translation

Edit the text in the language file and open a pull request. Keep the `{placeholders}` exactly as they
are in English: the tests fail if one is missing, extra or renamed.

## Add a language

1. Copy `en.json` to `<code>.json` (a two-letter ISO 639-1 code, for example `fr.json`).
2. Translate every value. Do not translate keys, placeholders, commands, flags or file names.
3. Add the code to `LANGUAGES` in `src/timonel/i18n.py` and to the `language` enum in
   `src/timonel/schema/config.schema.yaml`, then run `python scripts/gen_docs.py`.
4. Add `x-i18n.<code>` descriptions to the schema if you can; options without one fall back to English.
5. Run the tests:

```bash
uv run pytest tests/test_i18n.py -q
```

They fail when a key is missing or extra in any language, when placeholders differ from English, when a
text is empty, when a locale file is not listed in `LANGUAGES`, or when the web page uses a key that no
catalog has.

## Style

- Speak to the user directly and plainly ("Install Git?"); avoid jargon a beginner would not know, or
  explain it once.
- Keep product names (GitHub Copilot, Claude Code), commands and error codes (`TMN-2001`) unchanged.
- Choose the language with `--lang <code>`, the `TIMONEL_LANG` environment variable or
  `timonel config set language <code>`; the web UI also has a language button.
